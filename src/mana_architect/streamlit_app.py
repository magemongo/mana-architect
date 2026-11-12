from __future__ import annotations

import html
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Literal

import pandas as pd
import streamlit as st
from sqlalchemy import Engine, delete, or_, select
from sqlalchemy.orm import Session

from mana_architect.catalog import (
    DEFAULT_CATALOG_DIR,
    StrategyCatalog,
    load_strategy_catalog,
)
from mana_architect.commanders import CommanderOption, list_available_commanders
from mana_architect.classification import CardClassificationInput, classify_card
from mana_architect.classification_repository import (
    load_current_classifications,
    load_commander_affinity_profile,
    reclassify_cards,
    remove_card_classification_override,
    save_card_classification_override,
    save_commander_affinity_profile,
)
from mana_architect.commander_affinity import (
    CommanderAffinityProfile,
    CommanderStrategyAffinity,
    CommanderTagAffinity,
)
from mana_architect.database import create_database_engine, initialize_database
from mana_architect.deckbuilding import (
    DeckAnalysis,
    DeckCardDetails,
    analyze_deck,
    build_deck,
)
from mana_architect.models import (
    Card,
    CardClassificationOverrideRecord,
    CollectionPrinting,
    CommanderAffinityRecord,
    ManualOverrideRecord,
    SyncMetadata,
)
from mana_architect.oracle_text import PARSER_VERSION
from mana_architect.overrides import (
    ClassificationTargetType,
    ManualOverride,
    OverrideAction,
    resolve_classification_overrides,
)
from mana_architect.suggestions import (
    CollectionSuggestion,
    collection_oracle_ids,
    suggest_collection_cards,
)
from mana_architect.strategy import load_strategy_profile
from mana_architect.validation import DeckCard


COLOR_NAMES = {
    "W": "White",
    "U": "Blue",
    "B": "Black",
    "R": "Red",
    "G": "Green",
}
MANA_SYMBOLS = frozenset(
    {
        "T", "Q", "E", "P", "PW", "CHAOS", "A", "TK", "X", "Y", "Z",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
        "11", "12", "13", "14", "15", "16", "17", "18", "19", "20",
        "100", "1000000", "½", "∞", "W/U", "W/B", "B/R", "B/G",
        "U/B", "U/R", "R/G", "R/W", "G/W", "G/U", "B/G/P", "B/R/P",
        "G/U/P", "G/W/P", "R/G/P", "R/W/P", "U/B/P", "U/R/P",
        "W/B/P", "W/U/P", "C/W", "C/U", "C/B", "C/R", "C/G",
        "2/W", "2/U", "2/B", "2/R", "2/G", "H", "W/P", "U/P",
        "B/P", "R/P", "G/P", "C/P", "HW", "HR", "W", "U", "B", "R",
        "G", "C", "S", "L", "D",
    }
)


@st.cache_resource
def get_engine() -> Engine:
    engine = create_database_engine()
    initialize_database(engine)
    return engine


def _load_commander_options(session: Session) -> list[CommanderOption]:
    return list_available_commanders(session, limit=5000)


def _format_identity(identity: tuple[str, ...]) -> str:
    return " ".join(COLOR_NAMES.get(color, color) for color in identity) or "Colorless"


def _mana_cost_markdown(mana_cost: str | None) -> str:
    if not mana_cost:
        return ""

    parts = re.split(r"(\{[^{}]+\})", mana_cost)
    rendered: list[str] = []
    for part in parts:
        if not (part.startswith("{") and part.endswith("}")):
            rendered.append(html.escape(part))
            continue

        symbol = part[1:-1].upper()
        if symbol not in MANA_SYMBOLS:
            rendered.append(html.escape(part))
            continue
        filename = {"½": "HALF", "∞": "INFINITY"}.get(
            symbol, symbol.replace("/", "")
        )
        safe_symbol = html.escape(symbol, quote=True)
        rendered.append(
            f'![{{{safe_symbol}}}]'
            f'(https://svgs.scryfall.io/card-symbols/{filename}.svg)'
        )
    return "".join(rendered)


def _mana_curve_markup(mana_curve: dict[float, int]) -> str:
    if not mana_curve:
        return ""

    max_quantity = max(mana_curve.values())
    bars: list[str] = []
    for mana_value, quantity in sorted(mana_curve.items()):
        height = max(4, round(quantity / max_quantity * 120))
        bars.append(
            '<div class="mana-curve-column">'
            f'<span class="mana-curve-count">{quantity}</span>'
            '<div class="mana-curve-track">'
            f'<div class="mana-curve-bar" style="height:{height}px"></div>'
            "</div>"
            f'<span class="mana-curve-label">{mana_value:g}</span>'
            "</div>"
        )
    return (
        '<div class="mana-curve" role="img" aria-label="Mana curve histogram">'
        + "".join(bars)
        + "</div>"
    )


def _deck_json(
    analysis: DeckAnalysis,
    catalog_strategy_id: str | None = None,
    catalog_strategy_name: str | None = None,
) -> str:
    draft = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "validation_status": analysis.validation.status,
        "data_as_of": analysis.validation.data_as_of,
        "land_count": analysis.land_count,
        "rules_source": analysis.validation.rules_source,
        "legality_source": analysis.validation.legality_source,
        "role_counts_heuristic": analysis.role_counts,
        "strategy_profile": analysis.strategy_name,
        "strategy_themes_heuristic": analysis.strategy_themes,
    }
    if catalog_strategy_id is not None:
        draft.pop("strategy_profile")
        draft.pop("strategy_themes_heuristic")
        draft["catalog_strategy"] = {
            "id": catalog_strategy_id,
            "name": catalog_strategy_name,
        }
    return json.dumps(
        {
            "commander": next(
                card.oracle_id for card in analysis.cards if card.is_commander
            ),
            "cards": [
                {
                    "oracle_id": card.oracle_id,
                    "quantity": card.quantity,
                    "roles": card.roles,
                }
                for card in analysis.cards
            ],
            "draft": draft,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def _deck_text(
    analysis: DeckAnalysis,
    catalog_strategy_name: str | None = None,
) -> str:
    lines = [f"# Commander: {analysis.commander_name}"]
    if catalog_strategy_name:
        lines.append(f"# Catalog strategy: {catalog_strategy_name}")
    lines.extend(
        f"{card.quantity} {card.name}"
        for card in analysis.cards
        if card.is_commander
    )
    lines.extend(["", "# Main deck"])
    lines.extend(
        f"{card.quantity} {card.name}"
        for card in analysis.cards
        if not card.is_commander
    )
    return "\n".join(lines) + "\n"


def _generate_deck_from_form() -> None:
    commander_id = st.session_state["builder_commander_id"]
    land_count = int(st.session_state["builder_land_count"])
    generation_mode = st.session_state["builder_generation_mode"]
    strategy_id = (
        st.session_state.get("builder_strategy_id")
        if generation_mode == "catalog"
        else None
    )
    if generation_mode not in {"legacy", "catalog"}:
        st.session_state["deck_build_error"] = (
            "Unknown generation mode. Choose Legacy or Catalog."
        )
        return
    if generation_mode == "catalog" and not strategy_id:
        st.session_state["deck_build_error"] = (
            "There is no active strategy in the catalog."
        )
        return
    try:
        with Session(get_engine()) as session:
            result = build_deck(
                session,
                commander_id,
                land_count,
                strategy_id=strategy_id,
            )
    except (ValueError, RuntimeError) as error:
        st.session_state["deck_build_error"] = str(error)
        return

    st.session_state.pop("deck_build_error", None)
    st.session_state["deck_commander_id"] = commander_id
    if result.catalog_strategy_id is not None:
        st.session_state["deck_catalog_strategy_id"] = result.catalog_strategy_id
        st.session_state["deck_catalog_strategy_name"] = result.catalog_strategy_name
    else:
        st.session_state.pop("deck_catalog_strategy_id", None)
        st.session_state.pop("deck_catalog_strategy_name", None)
    st.session_state["deck_unclassified_count"] = result.unclassified_count
    st.session_state["deck_role_counts"] = result.role_counts
    st.session_state["deck_card_roles"] = result.card_roles
    st.session_state["deck_cards"] = [
        {"oracle_id": card.oracle_id, "quantity": card.quantity}
        for card in result.cards
    ]
    st.session_state["page_navigation"] = "Deck"


def _card_category(card: DeckCardDetails) -> str:
    if card.is_commander:
        return "Commander"
    card_types = (card.type_line or "").split("—", maxsplit=1)[0].casefold().split()
    categories = (
        ("Creatures", "creature"),
        ("Planeswalkers", "planeswalker"),
        ("Battles", "battle"),
        ("Artifacts", "artifact"),
        ("Enchantments", "enchantment"),
        ("Instants", "instant"),
        ("Sorceries", "sorcery"),
        ("Lands", "land"),
    )
    for label, card_type in categories:
        if card_type in card_types:
            return label
    return "Other Cards"


def _balance_category_names(
    category_weights: list[tuple[str, int]],
    column_count: int = 3,
) -> tuple[list[str], ...]:
    if column_count < 1:
        raise ValueError("column_count must be at least 1")

    categories = [category for category, _ in category_weights]
    if len(categories) < column_count:
        return tuple(
            [[category] for category in categories]
            + [[] for _ in range(column_count - len(categories))]
        )

    prefix_weights = [0]
    for _, weight in category_weights:
        prefix_weights.append(prefix_weights[-1] + weight)

    def partition_score(cuts: tuple[int, ...]) -> tuple[int, int, tuple[int, ...]]:
        boundaries = (0, *cuts, len(categories))
        totals = [
            prefix_weights[end] - prefix_weights[start]
            for start, end in zip(boundaries, boundaries[1:])
        ]
        return max(totals) - min(totals), max(totals), cuts

    cuts = min(
        combinations(range(1, len(categories)), column_count - 1),
        key=partition_score,
    )
    boundaries = (0, *cuts, len(categories))
    return tuple(
        categories[start:end]
        for start, end in zip(boundaries, boundaries[1:])
    )


def _render_deck_card_row(card: DeckCardDetails, row_key: str) -> None:
    label = f"{card.quantity}x  {card.name}"
    mana_cost = _mana_cost_markdown(card.mana_cost)
    if mana_cost:
        label = f"{label}  {mana_cost}"
    with st.container(key=row_key):
        st.markdown(label)


def _deck_preview_markup(
    cards: list[DeckCardDetails],
    row_keys_by_id: dict[str, str],
    default_card_id: str,
) -> str:
    preview_cards: list[str] = []
    preview_details: list[str] = []
    hover_rules: list[str] = []
    for card in cards:
        safe_id = html.escape(card.oracle_id, quote=True)
        preview_cards.append(
            f'<div class="deck-preview-card" data-card-id="{safe_id}">'
            + (
                f'<img src="{html.escape(card.image_uri, quote=True)}" '
                f'alt="{html.escape(card.name, quote=True)}" width="320" '
                'loading="lazy">'
                if card.image_uri
                else '<span>Card image unavailable.</span>'
            )
            + "</div>"
        )
        preview_details.append(
            f'<div class="deck-preview-details" data-card-id="{safe_id}">'
            + (
                f'<p class="deck-preview-signals">'
                f'Heuristic synergies: '
                f'{html.escape(", ".join(card.strategy_signals))}</p>'
                if card.strategy_signals
                else ""
            )
            + "</div>"
        )

        row_class = row_keys_by_id[card.oracle_id]
        hover_selector = f".st-key-{row_class} p:hover"
        hover_rules.extend(
            (
                f"body:has({hover_selector}) .deck-preview-card"
                "{display:none}",
                f'body:has({hover_selector}) '
                f'.deck-preview-card[data-card-id="{safe_id}"]'
                "{display:block}",
                f"body:has({hover_selector}) .deck-preview-details"
                "{display:none}",
                f'body:has({hover_selector}) '
                f'.deck-preview-details[data-card-id="{safe_id}"]'
                "{display:block}",
            )
        )

    safe_default_id = html.escape(default_card_id, quote=True)
    styles = (
        "<style>"
        ".deck-preview-card{display:none}"
        f'.deck-preview-card[data-card-id="{safe_default_id}"]'
        "{display:block}"
        ".deck-preview-details{display:none}"
        f'.deck-preview-details[data-card-id="{safe_default_id}"]'
        "{display:block}"
        + "".join(hover_rules)
        + 'div[data-testid="stMarkdownContainer"]:has(.deck-preview-panel)'
        "{width:320px!important;max-width:100%!important;height:auto!important}"
        + 'div[data-testid="stMarkdownContainer"]:has(.deck-preview-panel)>p:empty'
        "{display:none}"
        + ".deck-preview-panel{width:320px;max-width:100%}"
        + ".deck-preview-stack{position:relative;width:100%;aspect-ratio:488/680}"
        + ".deck-preview-card img{display:block;width:100%;height:auto}"
        + ".deck-preview-details{width:100%}"
        + "</style>"
    )
    preview = (
        '<div class="deck-preview-panel">'
        '<div class="deck-preview-stack">'
        + "".join(preview_cards)
        + "</div>"
        + '<div class="deck-preview-details-stack">'
        + "".join(preview_details)
        + "</div></div>"
    )
    return styles + preview


def _render_deck_page(session: Session) -> None:
    commander_id = st.session_state.get("deck_commander_id")
    current_cards = st.session_state.get("deck_cards")
    if not commander_id or not current_cards:
        st.info("Build a deck in Deck Builder to view it here.")
        return

    deck_cards = [
        DeckCard(oracle_id=card["oracle_id"], quantity=card["quantity"])
        for card in current_cards
    ]
    try:
        analysis = analyze_deck(session, commander_id, deck_cards)
    except (ValueError, RuntimeError) as error:
        st.error(str(error))
        return

    generated_role_counts = st.session_state.get("deck_role_counts")
    generated_card_roles = st.session_state.get("deck_card_roles")
    if isinstance(generated_role_counts, dict) and isinstance(generated_card_roles, dict):
        analysis = replace(
            analysis,
            role_counts=generated_role_counts,
            cards=tuple(
                replace(
                    card,
                    roles=tuple(generated_card_roles.get(card.oracle_id, card.roles)),
                )
                for card in analysis.cards
            ),
        )

    st.title(analysis.commander_name)
    catalog_strategy_name = st.session_state.get("deck_catalog_strategy_name")
    if catalog_strategy_name:
        st.caption(f"Catalog strategy: {catalog_strategy_name}")
    elif analysis.strategy_name:
        st.caption(
            f"{analysis.strategy_name} · "
            f"{' · '.join(analysis.strategy_themes)}"
        )
    unclassified_count = st.session_state.get("deck_unclassified_count", 0)
    if unclassified_count:
        st.warning(
            f"{unclassified_count} cards without current classifications were "
            "excluded by the catalog-guided builder."
        )

    metric_columns = st.columns(4)
    metric_columns[0].metric("Cards", f"{analysis.total_cards}/100")
    metric_columns[1].metric("Lands", analysis.land_count)
    metric_columns[2].metric("Nonlands", analysis.nonland_count)
    metric_columns[3].metric(
        "Validation",
        {
            "valid": "Valid",
            "incomplete": "Incomplete",
            "invalid": "Invalid",
        }[analysis.validation.status],
    )

    if analysis.validation.status == "valid":
        st.success("Deck passes validation against the synchronized data.")
    elif analysis.validation.status == "incomplete":
        st.warning("Validation is incomplete because some data is missing.")
    else:
        st.error("The deck has issues. Review the details before exporting.")
    if analysis.validation.data_as_of:
        st.caption(
            f"Legality snapshot: {analysis.validation.data_as_of}. "
            "This does not guarantee that the ban list is current."
        )
    for issue in analysis.validation.errors:
        st.error(f"{issue.message} [{issue.code}]")
    for issue in analysis.validation.unverified:
        st.warning(f"Unverified: {issue.message}")
    for issue in analysis.validation.warnings:
        st.caption(issue.message)

    actions = st.columns(2)
    if analysis.validation.status == "valid":
        actions[0].download_button(
            "Download JSON",
            data=_deck_json(
                analysis,
                st.session_state.get("deck_catalog_strategy_id"),
                catalog_strategy_name,
            ),
            file_name="mana-architect-deck.json",
            mime="application/json",
            key="download_json",
            use_container_width=True,
        )
        actions[1].download_button(
            "Download decklist TXT",
            data=_deck_text(analysis, catalog_strategy_name),
            file_name="mana-architect-deck.txt",
            mime="text/plain",
            key="download_text",
            use_container_width=True,
        )

    row_keys_by_id = {
        card.oracle_id: f"deck-card-row-{index}"
        for index, card in enumerate(analysis.cards)
    }

    list_column, preview_column = st.columns([2.75, 1])
    with list_column:
        with st.container(key="deck-list"):
            st.subheader("Decklist")
            grouped_cards: dict[str, list[DeckCardDetails]] = {}
            for card in analysis.cards:
                grouped_cards.setdefault(_card_category(card), []).append(card)
            category_order = (
                "Commander",
                "Creatures",
                "Artifacts",
                "Enchantments",
                "Instants",
                "Sorceries",
                "Lands",
                "Planeswalkers",
                "Battles",
                "Other Cards",
            )
            category_groups: list[tuple[str, list[DeckCardDetails]]] = []
            for category in category_order:
                category_cards = grouped_cards.get(category)
                if not category_cards:
                    continue
                category_groups.append(
                    (
                        category,
                        sorted(
                            category_cards,
                            key=lambda entry: (
                                entry.cmc is None,
                                entry.cmc or 0,
                                entry.name.casefold(),
                            ),
                        ),
                    )
                )

            category_columns_by_name = _balance_category_names(
                [
                    (category, sum(card.quantity for card in cards) + 1)
                    for category, cards in category_groups
                ]
            )
            groups_by_name = dict(category_groups)

            category_columns = st.columns(3, gap="small")
            for column, category_names in zip(
                category_columns, category_columns_by_name
            ):
                with column:
                    for category in category_names:
                        sorted_cards = groups_by_name[category]
                        category_count = sum(card.quantity for card in sorted_cards)
                        with st.expander(
                            f"{category} ({category_count})",
                            expanded=True,
                        ):
                            for card in sorted_cards:
                                _render_deck_card_row(
                                    card,
                                    row_keys_by_id[card.oracle_id],
                                )

    with preview_column:
        with st.container(key="selected-card-preview"):
            st.subheader("Card Preview")
            st.markdown(
                _deck_preview_markup(
                    list(analysis.cards),
                    row_keys_by_id,
                    commander_id,
                ),
                unsafe_allow_html=True,
            )

    with st.expander("Deck Analysis"):
        st.markdown("**Mana Curve**")
        if analysis.mana_curve:
            st.markdown(
                _mana_curve_markup(analysis.mana_curve),
                unsafe_allow_html=True,
            )
        else:
            st.caption("No mana costs available to display.")
        st.markdown("**Deckbuilding Roles**")
        for role, quantity in analysis.role_counts.items():
            st.text(f"{role}: {quantity}")


def _render_commander_affinity_editor(
    session: Session,
    commander_id: str,
    catalog: StrategyCatalog,
) -> None:
    profile = load_commander_affinity_profile(session, commander_id, catalog)
    existing = profile.strategy_affinities if profile else ()
    names = {item.id: item.name for item in catalog.strategies}
    labels = {
        "primary": "Primary",
        "secondary": "Secondary",
        "complementary": "Complementary",
    }
    rows = [
        {
            "strategy_id": item.strategy_id,
            "level": labels[item.level],
            "strength": item.strength_score,
            "rationale": item.rationale or "",
        }
        for item in existing
    ]
    with st.expander("Commander Affinities", expanded=True):
        st.caption(
            "Add multiple strategies and edit their affinity levels in one "
            "table. Strength and levels are heuristic, not probabilities."
        )
        with st.form(f"commander_affinities_{commander_id}"):
            affinity_rows = pd.DataFrame(
                {
                    "strategy_id": pd.Series(
                        [row["strategy_id"] for row in rows], dtype="string"
                    ),
                    "level": pd.Series(
                        [row["level"] for row in rows], dtype="string"
                    ),
                    "strength": pd.Series(
                        [row["strength"] for row in rows], dtype="float64"
                    ),
                    "rationale": pd.Series(
                        [row["rationale"] for row in rows], dtype="string"
                    ),
                }
            )
            edited = st.data_editor(
                affinity_rows,
                key=f"affinity_editor_{commander_id}",
                num_rows="dynamic",
                hide_index=True,
                width="stretch",
                column_config={
                    "strategy_id": st.column_config.SelectboxColumn(
                        "Strategy",
                        options=[item.id for item in catalog.strategies],
                        required=True,
                        format_func=lambda value: names.get(value, value),
                    ),
                    "level": st.column_config.SelectboxColumn(
                        "Level",
                        options=list(labels.values()),
                        required=True,
                    ),
                    "strength": st.column_config.NumberColumn(
                        "Strength",
                        min_value=0.0,
                        max_value=1.0,
                        step=0.05,
                        format="%.2f",
                    ),
                    "rationale": st.column_config.TextColumn("Rationale"),
                },
            )
            tag_names = {item.id: item.name for item in catalog.tags}
            selected_tags = st.multiselect(
                "Commander Tags",
                options=[item.id for item in catalog.tags],
                default=[item.tag_id for item in profile.tag_affinities] if profile else [],
                format_func=lambda tag_id: tag_names[tag_id],
                key=f"affinity_tags_{commander_id}",
            )
            submitted = st.form_submit_button(
                "Save Affinities",
                type="primary",
                key=f"save_affinities_{commander_id}",
            )
        if submitted:
            level_by_label = {label: level for level, label in labels.items()}
            seen: set[str] = set()
            strategy_affinities: list[CommanderStrategyAffinity] = []
            previous = {item.strategy_id: item for item in existing}
            try:
                edited_rows = (
                    edited.to_dict("records")
                    if hasattr(edited, "to_dict")
                    else edited
                )
                for row in edited_rows:
                    strategy_id = row.get("strategy_id")
                    if not strategy_id:
                        continue
                    if strategy_id in seen:
                        raise ValueError("Each strategy can only appear once.")
                    seen.add(strategy_id)
                    strength = row.get("strength")
                    if strength is None:
                        strength = 0.5
                    strategy_affinities.append(
                        CommanderStrategyAffinity(
                            strategy_id=strategy_id,
                            level=level_by_label[row["level"]],
                            strength_score=float(strength),
                            evidence_ids=previous[strategy_id].evidence_ids
                            if strategy_id in previous else (),
                            rationale=row.get("rationale", "").strip() or None,
                            source="manual",
                        )
                    )
                previous_tags = {
                    item.tag_id: item for item in profile.tag_affinities
                } if profile else {}
                tag_affinities = tuple(
                    CommanderTagAffinity(
                        tag_id=tag_id,
                        evidence_ids=previous_tags[tag_id].evidence_ids
                        if tag_id in previous_tags else (),
                        rationale=previous_tags[tag_id].rationale
                        if tag_id in previous_tags else None,
                        source="manual",
                    )
                    for tag_id in selected_tags
                )
                session.rollback()
                with session.begin():
                    save_commander_affinity_profile(
                        session,
                        CommanderAffinityProfile(
                            commander_oracle_id=commander_id,
                            strategy_affinities=tuple(strategy_affinities),
                            tag_affinities=tag_affinities,
                        ),
                        catalog,
                    )
            except (KeyError, TypeError, ValueError) as error:
                st.error(f"Could not save affinities: {error}")
            else:
                st.session_state["suggestion_notice"] = "Commander affinities saved."
                st.rerun()


def _bump_catalog_version(data: dict[str, object], field: str) -> None:
    version = data.get(field)
    if not isinstance(version, str):
        raise ValueError(f"Missing version in field {field}.")
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"Invalid version in field {field}: {version}.")
    parts[2] = str(int(parts[2]) + 1)
    data[field] = ".".join(parts)


def _write_catalog_files(updates: dict[str, dict[str, object]]) -> StrategyCatalog:
    originals: dict[str, bytes] = {}
    paths: dict[str, Path] = {}
    try:
        for filename, data in updates.items():
            path = DEFAULT_CATALOG_DIR / filename
            paths[filename] = path
            originals[filename] = path.read_bytes()
            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(path)
        return load_strategy_catalog(DEFAULT_CATALOG_DIR)
    except (OSError, ValueError) as error:
        for filename, original in originals.items():
            path = paths[filename]
            temp_path = path.with_suffix(path.suffix + ".restore.tmp")
            temp_path.write_bytes(original)
            temp_path.replace(path)
        raise ValueError(f"Change was not saved: {error}") from error


def _render_catalog_admin(session: Session) -> None:
    st.header("Strategy and Role Catalog")
    st.caption(
        "The catalog is versioned in local files. Changes make previous "
        "classifications stale and require an explicit reclassification."
    )
    strategies_data = json.loads(
        (DEFAULT_CATALOG_DIR / "strategy_catalog.json").read_text(encoding="utf-8")
    )
    roles_data = json.loads(
        (DEFAULT_CATALOG_DIR / "card_roles.json").read_text(encoding="utf-8")
    )
    rules_data = json.loads(
        (DEFAULT_CATALOG_DIR / "classification_rules.json").read_text(encoding="utf-8")
    )
    catalog = load_strategy_catalog(DEFAULT_CATALOG_DIR)
    strategy_ids = [item["id"] for item in strategies_data["strategies"]]
    role_ids = [item["id"] for item in roles_data["roles"]]
    catalog_view = st.segmented_control(
        "Catalog view",
        options=["Strategies", "Roles", "Oracle Parser"],
        default="Strategies",
        key="catalog_admin_view",
    )

    if catalog_view == "Strategies":
        st.dataframe(
            [
                {
                    "Strategy": item["name"],
                    "ID": item["id"],
                    "Status": "Active" if item["active"] else "Inactive",
                    "Description": item["description"],
                }
                for item in strategies_data["strategies"]
            ],
            hide_index=True,
            width="stretch",
        )
        action = st.segmented_control(
            "Strategy action",
            options=["Edit", "Add"],
            default="Edit",
            key="catalog_strategy_action",
        )
        selected = None
        if action == "Edit" and strategy_ids:
            selected = st.selectbox(
                "Strategy",
                options=strategy_ids,
                format_func=lambda item_id: next(
                    item["name"] for item in strategies_data["strategies"]
                    if item["id"] == item_id
                ),
                key="catalog_strategy_selected",
            )
        current = next(
            (item for item in strategies_data["strategies"] if item["id"] == selected),
            None,
        )
        with st.form("catalog_strategy_form"):
            strategy_id = st.text_input(
                "ID",
                value=current["id"] if current else "",
                disabled=current is not None,
                help="Unique snake_case ID. Existing IDs cannot be renamed.",
            )
            name = st.text_input("Nome", value=current["name"] if current else "")
            description = st.text_area(
                "Description", value=current["description"] if current else ""
            )
            active = st.checkbox(
                "Active strategy", value=current["active"] if current else True
            )
            nature = st.text_input(
                "Nature (comma-separated values)",
                value=", ".join(current["nature"]) if current else "resource_engine",
            )
            tag_weights = st.text_area(
                "Tags and weights (one per line: tag_id=0.8)",
                value="\n".join(
                    f"{item['tag_id']}={item['weight']}"
                    for item in current["tag_affinities"]
                ) if current else "",
            )
            notes = st.text_area(
                "Notes (one per line)",
                value="\n".join(current["notes"]) if current else "",
            )
            save_strategy = st.form_submit_button("Save Strategy", type="primary")
        if save_strategy:
            try:
                affinities = []
                for line in tag_weights.splitlines():
                    if not line.strip():
                        continue
                    tag_id, raw_weight = line.split("=", maxsplit=1)
                    affinities.append({"tag_id": tag_id.strip(), "weight": float(raw_weight)})
                record = {
                    "id": strategy_id.strip(),
                    "name": name.strip(),
                    "description": description.strip(),
                    "nature": [item.strip() for item in nature.split(",") if item.strip()],
                    "active": active,
                    "tag_affinities": affinities,
                    "notes": [item.strip() for item in notes.splitlines() if item.strip()],
                }
                strategies_data["strategies"] = [
                    item for item in strategies_data["strategies"]
                    if item["id"] != record["id"]
                ]
                strategies_data["strategies"].append(record)
                _bump_catalog_version(strategies_data, "catalog_version")
                _write_catalog_files({"strategy_catalog.json": strategies_data})
            except (ValueError, KeyError) as error:
                st.error(str(error))
            else:
                st.success("Strategy saved. Reclassify the collection to apply the new version.")
                st.rerun()
        if current is not None:
            strategy_delete_confirmed = st.checkbox(
                "I confirm deletion and removal of linked affinities and overrides.",
                key=f"confirm_delete_strategy_{current['id']}",
            )
            if st.button(
                "Delete Strategy", key=f"delete_strategy_{current['id']}"
            ):
                if not strategy_delete_confirmed:
                    st.warning("Confirm the deletion before continuing.")
                else:
                    strategy_id_to_delete = current["id"]
                    tag_data = json.loads(
                        (DEFAULT_CATALOG_DIR / "synergy_tags.json").read_text(encoding="utf-8")
                    )
                    for tag in tag_data["tags"]:
                        tag["related_strategy_ids"] = [
                            item for item in tag["related_strategy_ids"]
                            if item != strategy_id_to_delete
                        ]
                    rules_data["rules"] = [
                        item for item in rules_data["rules"]
                        if not (
                            item["target_type"] == "strategy"
                            and item["target_id"] == strategy_id_to_delete
                        )
                    ]
                    strategies_data["strategies"] = [
                        item for item in strategies_data["strategies"]
                        if item["id"] != strategy_id_to_delete
                    ]
                    _bump_catalog_version(strategies_data, "catalog_version")
                    _bump_catalog_version(rules_data, "ruleset_version")
                    session.rollback()
                    try:
                        with session.begin():
                            session.execute(
                                delete(CommanderAffinityRecord).where(
                                    CommanderAffinityRecord.target_type == "strategy",
                                    CommanderAffinityRecord.target_id == strategy_id_to_delete,
                                )
                            )
                            session.execute(
                                delete(ManualOverrideRecord).where(
                                    ManualOverrideRecord.target_type == "strategy",
                                    ManualOverrideRecord.target_id == strategy_id_to_delete,
                                )
                            )
                            session.execute(
                                delete(CardClassificationOverrideRecord).where(
                                    CardClassificationOverrideRecord.target_type == "strategy",
                                    CardClassificationOverrideRecord.target_id == strategy_id_to_delete,
                                )
                            )
                            _write_catalog_files(
                                {
                                    "strategy_catalog.json": strategies_data,
                                    "synergy_tags.json": tag_data,
                                    "classification_rules.json": rules_data,
                                }
                            )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.success("Strategy deleted. Classification history was preserved.")
                        st.rerun()

    elif catalog_view == "Roles":
        st.dataframe(
            [
                {
                    "Role": item["name"],
                    "Grupo": (
                        "Deck Service"
                        if item["group"] == "deck_service"
                        else "Synergy Role"
                    ),
                    "Oracle Phrases": ", ".join(item.get("oracle_keywords", [])),
                    "Description": item["description"],
                }
                for item in roles_data["roles"]
            ],
            hide_index=True,
            width="stretch",
        )
        action = st.segmented_control(
            "Role action",
            options=["Edit", "Add"],
            default="Edit",
            key="catalog_role_action",
        )
        selected = None
        if action == "Edit" and role_ids:
            selected = st.selectbox(
                "Role",
                options=role_ids,
                format_func=lambda item_id: next(
                    item["name"] for item in roles_data["roles"]
                    if item["id"] == item_id
                ),
                key="catalog_role_selected",
            )
        current = next(
            (item for item in roles_data["roles"] if item["id"] == selected), None
        )
        with st.form("catalog_role_form"):
            role_id = st.text_input(
                "ID",
                value=current["id"] if current else "",
                disabled=current is not None,
                help="Unique snake_case ID. Existing IDs cannot be renamed.",
            )
            name = st.text_input("Nome", value=current["name"] if current else "")
            st.caption(
                "Catalog roles describe general deck needs. Main Strategy is "
                "calculated dynamically from the selected strategy."
            )
            group = "deck_service"
            description = st.text_area(
                "Description", value=current["description"] if current else ""
            )
            oracle_keywords = st.text_area(
                "Oracle text terms (one per line)",
                value="\n".join(current.get("oracle_keywords", [])) if current else "",
                help="Terms are extracted as source-linked phrase features before they can suggest this role. Classifications remain heuristic.",
            )
            save_role = st.form_submit_button("Save Role", type="primary")
        if save_role:
            record = {
                "id": role_id.strip(),
                "name": name.strip(),
                "group": group,
                "description": description.strip(),
                "oracle_keywords": [
                    item.strip() for item in oracle_keywords.splitlines() if item.strip()
                ],
            }
            roles_data["roles"] = [
                item for item in roles_data["roles"] if item["id"] != record["id"]
            ]
            roles_data["roles"].append(record)
            _bump_catalog_version(strategies_data, "catalog_version")
            _bump_catalog_version(rules_data, "ruleset_version")
            try:
                _write_catalog_files(
                    {
                        "card_roles.json": roles_data,
                        "strategy_catalog.json": strategies_data,
                        "classification_rules.json": rules_data,
                    }
                )
            except ValueError as error:
                st.error(str(error))
            else:
                st.success("Role saved. Reclassify the collection to apply the new version.")
                st.rerun()
        if current is not None:
            role_delete_confirmed = st.checkbox(
                "I confirm deletion and removal of linked rules and overrides.",
                key=f"confirm_delete_role_{current['id']}",
            )
            if st.button("Delete Role", key=f"delete_role_{current['id']}"):
                if not role_delete_confirmed:
                    st.warning("Confirm the deletion before continuing.")
                else:
                    role_id_to_delete = current["id"]
                    rules_data["rules"] = [
                        item for item in rules_data["rules"]
                        if not (
                            item["target_type"] == "role"
                            and item["target_id"] == role_id_to_delete
                        )
                    ]
                    roles_data["roles"] = [
                        item for item in roles_data["roles"]
                        if item["id"] != role_id_to_delete
                    ]
                    _bump_catalog_version(strategies_data, "catalog_version")
                    _bump_catalog_version(rules_data, "ruleset_version")
                    session.rollback()
                    try:
                        with session.begin():
                            session.execute(
                                delete(ManualOverrideRecord).where(
                                    ManualOverrideRecord.target_type == "role",
                                    ManualOverrideRecord.target_id == role_id_to_delete,
                                )
                            )
                            session.execute(
                                delete(CardClassificationOverrideRecord).where(
                                    CardClassificationOverrideRecord.target_type == "role",
                                    CardClassificationOverrideRecord.target_id == role_id_to_delete,
                                )
                            )
                            _write_catalog_files(
                                {
                                    "card_roles.json": roles_data,
                                    "strategy_catalog.json": strategies_data,
                                    "classification_rules.json": rules_data,
                                }
                            )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.success("Role deleted. Classification history was preserved.")
                        st.rerun()

    else:
        _render_oracle_parser_inspector(session, catalog)


def _render_oracle_parser_inspector(
    session: Session,
    catalog: StrategyCatalog,
) -> None:
    st.subheader("Oracle parser inspector")
    st.caption(
        "Inspect how a card's Oracle text becomes ability segments, lexical "
        "features, mechanical facts, and classification evidence."
    )
    st.info(
        "Features show terms found in the text. Mechanical facts interpret "
        "those terms in context. The rules below map mechanics to tags, roles, "
        "or strategies; these are separate steps."
    )
    parser_left, parser_middle, parser_right = st.columns(3)
    parser_left.metric("Parser version", PARSER_VERSION)
    parser_middle.metric("Lexical entries", len(catalog.oracle_lexicon))
    parser_right.metric("Mechanical patterns", len(catalog.oracle_mechanical_patterns))

    parser_rules = [
        rule for rule in catalog.rules
        if rule.match.type in {"oracle_feature_any", "oracle_mechanic_any"}
    ]
    target_names = {
        **{("strategy", item.id): item.name for item in catalog.strategies},
        **{("tag", item.id): item.name for item in catalog.tags},
        **{("role", item.id): item.name for item in catalog.roles},
    }
    st.markdown("**Active parser-backed classification rules**")
    if parser_rules:
        st.dataframe(
            [
                {
                    "Rule": rule.id,
                    "Classifies as": target_names.get(
                        (rule.target_type, rule.target_id),
                        f"{rule.target_type}: {rule.target_id}",
                    ),
                    "Matches": ", ".join(rule.match.values),
                    "Conditions": json.dumps(dict(rule.match.where), ensure_ascii=False),
                    "Weight": rule.weight,
                    "Confidence": rule.confidence,
                    "Review": "Required" if rule.requires_review else "No",
                }
                for rule in parser_rules
            ],
            hide_index=True,
            width="stretch",
            alt="Parser-backed classification rules",
        )
    else:
        st.info("No parser-backed classification rules are configured.")

    with st.form("oracle_parser_card_search_form"):
        search_text = st.text_input(
            "Find a card in your collection",
            key="oracle_parser_search_input",
            help="Search by card name or paste an exact Oracle ID.",
        )
        search_submitted = st.form_submit_button(
            "Search cards", icon=":material/search:"
        )
    if search_submitted:
        st.session_state["oracle_parser_search_query"] = search_text.strip()

    query = st.session_state.get("oracle_parser_search_query", "")
    if not query:
        st.info("Search for a card to inspect its current Oracle parse.")
        return
    if len(query) < 2:
        st.info("Enter at least two characters of a card name, or an Oracle ID.")
        return

    matching_cards = list(
        session.scalars(
            select(Card)
            .join(CollectionPrinting, CollectionPrinting.oracle_id == Card.oracle_id)
            .where(
                or_(
                    Card.name.ilike(f"%{query}%"),
                    Card.oracle_id == query,
                )
            )
            .distinct()
            .order_by(Card.name, Card.oracle_id)
            .limit(100)
        )
    )
    if not matching_cards:
        st.warning("No matching card was found in your collection.")
        return
    cards_by_id = {card.oracle_id: card for card in matching_cards}
    card_ids = tuple(cards_by_id)
    if st.session_state.get("oracle_parser_selected_card") not in cards_by_id:
        st.session_state.pop("oracle_parser_selected_card", None)
    selected_card_id = st.selectbox(
        "Matching card",
        options=card_ids,
        format_func=lambda oracle_id: (
            f"{cards_by_id[oracle_id].name} · {oracle_id}"
        ),
        key="oracle_parser_selected_card",
    )
    card = cards_by_id[selected_card_id]
    current_result = load_current_classifications(
        session,
        (selected_card_id,),
        catalog,
        DEFAULT_CATALOG_DIR,
    ).get(selected_card_id)
    persisted_parse = (
        current_result.oracle_parse if current_result is not None else None
    )
    classification_result = current_result
    if persisted_parse is not None:
        oracle_parse = persisted_parse
        st.success(
            f"Showing the saved parse for {persisted_parse.parser_version} "
            "and the current catalog."
        )
    else:
        preview_result = classify_card(
            CardClassificationInput(
                type_line=card.type_line,
                oracle_text=card.oracle_text,
                mana_cost=card.mana_cost,
                cmc=card.cmc,
                colors=tuple(card.colors) if card.colors is not None else None,
                color_identity=(
                    tuple(card.color_identity)
                    if card.color_identity is not None else None
                ),
                produced_mana=(
                    tuple(card.produced_mana)
                    if card.produced_mana is not None else None
                ),
                keywords=tuple(card.keywords) if card.keywords is not None else None,
            ),
            catalog,
        )
        classification_result = preview_result
        oracle_parse = preview_result.oracle_parse
        st.warning(
            "This is a live preview. The saved classification is missing or "
            "out of date, so this result has not been written to the database."
        )
        if st.button(
            "Save current parser result for this card",
            key=f"save_oracle_parse_{selected_card_id}",
            type="primary",
        ):
            try:
                session.rollback()
                with session.begin():
                    summary = reclassify_cards(
                        session,
                        catalog,
                        DEFAULT_CATALOG_DIR,
                        (selected_card_id,),
                    )
            except ValueError as error:
                st.error(str(error))
            else:
                st.success(
                    f"Card updated: {summary.classified} new classification, "
                    f"{summary.skipped} already current."
                )
                st.rerun()

    if oracle_parse is None:
        st.error("The parser did not return a result for this card.")
        return
    with st.expander("Oracle text", expanded=False):
        st.code(oracle_parse.oracle_text or "No Oracle text is available.")

    st.markdown("**Ability segments**")
    ability_rows = [
        {
            "Ability": item.id,
            "Structure": item.kind.replace("_", " ").title(),
            "Cost": " ".join(f"{{{symbol}}}" for symbol in item.mana_cost.symbols)
                if item.mana_cost else "—",
            "Source span": f"{item.start}–{item.end}",
            "Oracle text": item.text,
        }
        for item in oracle_parse.abilities
    ]
    if ability_rows:
        st.dataframe(
            ability_rows,
            hide_index=True,
            width="stretch",
            alt="Parsed Oracle ability segments",
        )
    else:
        st.caption("No Oracle ability text was found.")

    st.markdown("**Extracted Oracle features**")
    feature_rows = [
        {
            "Category": item.category.replace("_", " ").title(),
            "Term in text": item.surface,
            "Canonical term": item.canonical,
            "Ability": item.ability_id,
            "Source span": f"{item.start}–{item.end}",
        }
        for item in oracle_parse.features
    ]
    if feature_rows:
        st.dataframe(
            feature_rows,
            hide_index=True,
            width="stretch",
            alt="Oracle terms extracted by the parser",
        )
    else:
        st.caption("No catalog terms were found in this card's Oracle text.")
    st.markdown("**Mechanical facts**")
    mechanic_rows = [
        {
            "Mechanic": item.id,
            "Attributes": json.dumps(dict(item.attributes), ensure_ascii=False),
            "Ability": item.ability_id,
            "Source span": f"{item.evidence_start}–{item.evidence_end}",
            "Matched text": oracle_parse.oracle_text[
                item.evidence_start:item.evidence_end
            ],
        }
        for item in oracle_parse.mechanics
    ]
    if mechanic_rows:
        st.dataframe(
            mechanic_rows,
            hide_index=True,
            width="stretch",
            alt="Mechanical facts recognized in Oracle text",
        )
    else:
        st.caption("No configured mechanical patterns matched this card.")
    if classification_result is not None:
        st.markdown("**Classification evidence**")
        classification_rows = [
                {
                    "Rule": item.rule_id,
                    "Target": target_names.get(
                        (item.target_type, item.target_id),
                        f"{item.target_type}: {item.target_id}",
                    ),
                    "Evidence": item.matched_value,
                    "Source span": (
                        f"{item.span_start}–{item.span_end}"
                        if item.span_start is not None and item.span_end is not None
                        else "—"
                    ),
                    "Review": "Required" if item.requires_review else "No",
                }
                for item in classification_result.evidence
            ]
        if classification_rows:
            st.dataframe(
                classification_rows,
                hide_index=True,
                width="stretch",
                alt="Classification evidence for this card",
            )
        else:
            st.caption("No classification rule matched this card.")


def _render_suggestion_correction(
    session: Session,
    suggestion: CollectionSuggestion,
    catalog: StrategyCatalog,
) -> None:
    score = suggestion.score
    target_labels = {
        **{
            f"strategy:{item.id}": f"Strategy — {item.name}"
            for item in catalog.strategies
        },
        **{
            f"tag:{item.id}": f"Tag — {item.name}"
            for item in catalog.tags
        },
        **{
            f"role:{item.id}": f"Role — {item.name}"
            for item in catalog.roles
        },
    }
    target_options = list(target_labels)
    target_by_ref: dict[str, tuple[ClassificationTargetType, str]] = {
        f"strategy:{item.id}": ("strategy", item.id)
        for item in catalog.strategies
    }
    target_by_ref.update(
        {
            f"tag:{item.id}": ("tag", item.id)
            for item in catalog.tags
        }
    )
    target_by_ref.update(
        {
            f"role:{item.id}": ("role", item.id)
            for item in catalog.roles
        }
    )
    overrides_by_target = {
        f"{item.target_type}:{item.target_id}": item
        for item in suggestion.overrides
    }
    default_target = next(iter(overrides_by_target), None)
    if default_target not in target_labels:
        default_target = next(
            (
                f"{item.target_type}:{item.target_id}"
                for item in suggestion.classification.evidence
                if f"{item.target_type}:{item.target_id}" in target_labels
            ),
            target_options[0],
        )
    target_widget_key = f"correction_target_{score.card_oracle_id}"
    selected_target = st.session_state.get(target_widget_key, default_target)
    if selected_target not in target_labels:
        selected_target = default_target
    existing_override = overrides_by_target.get(selected_target)
    action_labels = (
        "Force Include",
        "Force Exclude",
        "Reset to Automatic",
    )
    action_values: dict[str, OverrideAction | Literal["reset"]] = {
        "Force Include": "force_include",
        "Force Exclude": "force_exclude",
        "Reset to Automatic": "reset",
    }

    with st.expander("Correct This Card's Classification"):
        st.caption(
            "This correction applies only to this card. Original automatic "
            "evidence is preserved."
        )
        with st.form(f"card_correction_{score.card_oracle_id}"):
            target_ref = st.selectbox(
                "Classification",
                options=target_options,
                index=target_options.index(selected_target),
                format_func=lambda value: target_labels[value],
                key=target_widget_key,
            )
            action_label = st.selectbox(
                "Decision",
                options=action_labels,
                index=(
                    action_labels.index("Force Exclude")
                    if existing_override
                    and existing_override.action == "force_exclude"
                    else action_labels.index("Force Include")
                ),
                key=f"correction_action_{score.card_oracle_id}",
            )
            reason = st.text_area(
                "Rationale",
                value=existing_override.reason or "" if existing_override else "",
                key=f"correction_reason_{score.card_oracle_id}",
            )
            submitted = st.form_submit_button(
                "Apply Correction",
                key=f"apply_correction_{score.card_oracle_id}",
            )
        if not submitted:
            return

        target_type, target_id = target_by_ref[target_ref]
        action = action_values[action_label]
        try:
            session.rollback()
            with session.begin():
                if action == "reset":
                    remove_card_classification_override(
                        session,
                        score.card_oracle_id,
                        target_type,
                        target_id,
                        catalog,
                    )
                else:
                    save_card_classification_override(
                        session,
                        score.card_oracle_id,
                        ManualOverride(
                            target_type=target_type,
                            target_id=target_id,
                            action=action,
                            reason=reason,
                        ),
                        catalog,
                    )
        except ValueError as error:
            st.error(str(error))
            return
        st.session_state["suggestion_notice"] = (
            "Manual override saved; automatic evidence was preserved."
            if action != "reset"
            else "Override removed; classification reverted to automatic."
        )
        st.rerun()


def _render_collection_suggestion(
    session: Session,
    suggestion: CollectionSuggestion,
    catalog: StrategyCatalog,
) -> None:
    score = suggestion.score
    strategy_names = {item.id: item.name for item in catalog.strategies}
    tag_names = {item.id: item.name for item in catalog.tags}
    role_names = {item.id: item.name for item in catalog.roles}
    decisions = resolve_classification_overrides(
        suggestion.classification,
        suggestion.overrides,
        catalog,
    )
    included = [item for item in decisions if item.included]
    matches = [
        item
        for item in included
        if item.target_type in {"strategy", "tag", "role"}
    ]
    with st.container(border=True):
        st.subheader(f"{score.card_name} — {score.score:.1f} ({score.tier})")
        details = [
            item
            for item in (suggestion.mana_cost, suggestion.type_line)
            if item
        ]
        if details:
            st.caption(" · ".join(details))
        st.caption(
            f"Available in collection after accounting for this deck: "
            f"{score.available_quantity}"
        )
        if matches:
            st.markdown(
                "**Matches:** "
                + ", ".join(
                    (
                        strategy_names[item.target_id]
                        if item.target_type == "strategy"
                        else tag_names[item.target_id]
                        if item.target_type == "tag"
                        else role_names[item.target_id]
                    )
                    for item in matches
                )
            )
        detected_roles = [
            role_names[item.target_id]
            for item in included
            if item.target_type == "role"
        ]
        selected_strategy = next(
            (item for item in catalog.strategies if item.id == score.strategy_id),
            None,
        )
        strategy_tag_ids = (
            {item.tag_id for item in selected_strategy.tag_affinities}
            if selected_strategy is not None
            else set()
        )
        if any(
            item.target_type == "strategy" and item.target_id == score.strategy_id
            or item.target_type == "tag" and item.target_id in strategy_tag_ids
            for item in included
        ):
            detected_roles.append("Main Strategy (dynamic)")
        st.caption(
            "Detected roles: "
            + (", ".join(detected_roles) if detected_roles else "none")
        )
        with st.expander("Evidence and Score Breakdown"):
            for decision in decisions:
                label = (
                    strategy_names[decision.target_id]
                    if decision.target_type == "strategy"
                    else tag_names[decision.target_id]
                    if decision.target_type == "tag"
                    else role_names[decision.target_id]
                )
                evidence = "; ".join(
                    item.evidence_label for item in decision.automatic_evidence
                )
                state = "included" if decision.included else "excluded"
                st.markdown(
                    f"- **{label}** — {state}; origem: {decision.source}."
                )
                if evidence:
                    st.caption(evidence)
                if decision.override and decision.override.reason:
                    st.caption(f"Justificativa manual: {decision.override.reason}")
                if decision.conflict:
                    st.warning(
                        f"Manual override contradicts automatic evidence for {label}."
                    )
            st.markdown("**Components**")
            for component, value in score.components.items():
                st.text(f"{component}: {value:.3f}")
            st.markdown("**Score Reasons**")
            for reason in score.reasons:
                detail = f" — {reason.detail}" if reason.detail else ""
                st.text(f"{reason.code}: {reason.contribution:+.2f}{detail}")
        _render_suggestion_correction(session, suggestion, catalog)


def _render_collection_suggestions(session: Session) -> None:
    st.header("Card Suggestions")
    st.caption(
        "Deterministic suggestions from your collection. Legality and color "
        "identity are filters, not score bonuses. No external service is called."
    )
    catalog = load_strategy_catalog()
    commanders = _load_commander_options(session)
    if not commanders:
        st.warning(
            "No commanders are available. Import your collection and run "
            "`sync-scryfall` primeiro."
        )
        return
    commander_by_id = {item.oracle_id: item for item in commanders}
    commander_ids = list(commander_by_id)
    previous_commander = st.session_state.get(
        "suggestion_commander_id",
        st.session_state.get("deck_commander_id"),
    )
    commander_index = (
        commander_ids.index(previous_commander)
        if previous_commander in commander_by_id
        else 0
    )
    commander_id = st.selectbox(
        "Commander",
        options=commander_ids,
        index=commander_index,
        key="suggestion_commander_id",
        format_func=lambda oracle_id: (
            f"{commander_by_id[oracle_id].name} — "
            f"{_format_identity(commander_by_id[oracle_id].color_identity)}"
        ),
    )
    profile = load_commander_affinity_profile(session, commander_id, catalog)
    strategies = tuple(item for item in catalog.strategies if item.active)
    strategy_ids = [item.id for item in strategies]
    current_affinity_id = next(
        (
            item.strategy_id
            for item in profile.strategy_affinities
            if item.strategy_id in strategy_ids
        ),
        strategy_ids[0],
    ) if profile else strategy_ids[0]
    current_strategy = st.session_state.get(
        "suggestion_strategy_id",
        current_affinity_id,
    )
    strategy_index = (
        strategy_ids.index(current_strategy)
        if current_strategy in strategy_ids
        else strategy_ids.index(current_affinity_id)
    )
    strategy_id = st.selectbox(
        "Target Strategy",
        options=strategy_ids,
        index=strategy_index,
        key="suggestion_strategy_id",
        format_func=lambda value: next(
            item.name for item in strategies if item.id == value
        ),
    )
    role_names = {item.id: item.name for item in catalog.roles}
    service_role_ids = [item.id for item in catalog.roles if item.group == "deck_service"]
    with st.expander("Prioritize Roles in Suggestions", expanded=False):
        service_roles = st.multiselect(
            "Core Deck Roles",
            options=service_role_ids,
            format_func=lambda value: role_names[value],
            key="suggestion_service_roles",
            help="Prioritize ramp, card advantage, and spot or mass interaction.",
        )
        st.caption(
            "Main Strategy contribution is calculated from the selected strategy "
            "and card classification; it is not a universal role filter."
        )
    required_roles = service_roles
    search_clicked = st.button(
        "Find Suggestions",
        type="primary",
        key="search_collection_suggestions",
    )
    if search_clicked:
        st.session_state["suggestion_query"] = (
            commander_id,
            strategy_id,
            tuple(required_roles),
        )

    notice = st.session_state.pop("suggestion_notice", None)
    if notice:
        st.success(notice)
    _render_commander_affinity_editor(session, commander_id, catalog)

    if st.button(
        "Reclassify Collection Cards",
        key="reclassify_collection_for_suggestions",
    ):
        oracle_ids = collection_oracle_ids(session)
        try:
            session.rollback()
            with session.begin():
                summary = reclassify_cards(
                    session,
                    catalog,
                    DEFAULT_CATALOG_DIR,
                    oracle_ids,
                )
        except ValueError as error:
            st.error(str(error))
            return
        st.session_state["suggestion_notice"] = (
            f"Classification complete: {summary.classified} updated, "
            f"{summary.skipped} unchanged."
        )
        st.rerun()

    saved_query = st.session_state.get("suggestion_query")
    active_query = (commander_id, strategy_id, tuple(required_roles))
    if saved_query != active_query:
        st.info("Choose filters and click **Find Suggestions**.")
        return

    used_quantities: dict[str, int] = {}
    if st.session_state.get("deck_commander_id") == commander_id:
        for item in st.session_state.get("deck_cards", []):
            oracle_id = item.get("oracle_id")
            quantity = item.get("quantity")
            if isinstance(oracle_id, str) and isinstance(quantity, int):
                used_quantities[oracle_id] = quantity
    try:
        batch = suggest_collection_cards(
            session,
            commander_id,
            strategy_id,
            catalog,
            config_dir=DEFAULT_CATALOG_DIR,
            required_roles=tuple(required_roles) or None,
            already_used=used_quantities,
        )
    except ValueError as error:
        st.warning(str(error))
        return

    metrics = st.columns(3)
    metrics[0].metric("Collection Cards", batch.total_candidates)
    metrics[1].metric("Not Currently Classified", batch.unclassified_count)
    metrics[2].metric("Ineligible", batch.ineligible_count)
    if batch.unclassified_count:
        st.info(
            "Some cards do not have current classifications. Use **Reclassify "
            "Collection Cards**. This is local and does not download data."
        )
    if not batch.suggestions:
        st.info("No eligible cards were found for these filters.")
        return
    st.subheader(
        f"Best matches for {batch.strategy_name} — "
        f"{batch.commander.name}"
    )
    for suggestion in batch.suggestions:
        _render_collection_suggestion(session, suggestion, catalog)


def _render_deck_builder(session: Session) -> None:
    st.header("Deck Builder")
    st.caption(
        "Choose a commander. The builder uses only your collection and "
        "prioritizes strategy signals when a profile is configured."
    )
    commanders = _load_commander_options(session)
    if not commanders:
        st.warning(
            "No commanders are available. Import your collection and run "
            "`sync-scryfall` primeiro."
        )
        return

    catalog = load_strategy_catalog()
    strategies = tuple(item for item in catalog.strategies if item.active)
    strategy_names = {item.id: item.name for item in strategies}
    commander_by_id = {commander.oracle_id: commander for commander in commanders}
    commander_ids = list(commander_by_id)
    with st.form("build_deck"):
        commander_id = st.selectbox(
            "Commander",
            options=commander_ids,
            key="builder_commander_id",
            format_func=lambda oracle_id: (
                f"{commander_by_id[oracle_id].name} — "
                f"{_format_identity(commander_by_id[oracle_id].color_identity)}"
            ),
        )
        st.selectbox(
            "Strategy Mode",
            options=("legacy", "catalog"),
            format_func=lambda value: (
                "Legacy" if value == "legacy" else "Catalog Strategy"
            ),
            key="builder_generation_mode",
        )
        if strategies:
            st.selectbox(
                "Catalog Strategy",
                options=tuple(strategy_names),
                format_func=lambda value: strategy_names[value],
                key="builder_strategy_id",
            )
        else:
            st.warning("There are no active strategies in the catalog.")
        st.number_input(
            "Land Count",
            min_value=0,
            max_value=99,
            value=38,
            step=1,
            key="builder_land_count",
        )
        st.form_submit_button(
            "Build Deck",
            type="primary",
            on_click=_generate_deck_from_form,
            key="build_deck_submit",
        )

    selected_profile = load_strategy_profile(commander_id)
    if st.session_state.get("builder_generation_mode") == "catalog":
        selected_strategy_id = st.session_state.get("builder_strategy_id")
        strategy_name = strategy_names.get(selected_strategy_id)
        if strategy_name:
            st.caption(
                f"Catalog mode: {strategy_name}. The score complements role, "
                "curve, and land balancing."
            )
    elif selected_profile:
        st.caption(
            f"Profile: {selected_profile.name} — "
            f"{', '.join(selected_profile.themes)}. "
            "Strategy signals are heuristic."
        )
    else:
        st.caption(
            "No specific strategy profile; general role and mana-curve scores "
            "will be used."
        )

    if "deck_build_error" in st.session_state:
        st.error(st.session_state["deck_build_error"])
    if st.session_state.get("deck_cards"):
        st.info("Deck built. Open **Deck** in the navigation to view it.")


def main() -> None:
    st.set_page_config(
        page_title="Mana Architect",
        page_icon="🧙",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        div.st-key-selected-card-preview {
            width: 320px;
            max-width: 100%;
        }
        div[class*="st-key-deck-card-row-"] {
            margin-bottom: 0;
        }
        div[class*="st-key-deck-card-row-"] p {
            display: flex;
            align-items: center;
            gap: 0.25rem;
            width: fit-content;
            max-width: 100%;
            min-height: 1.75rem;
            margin: 0;
            padding: 0.1rem 0.4rem;
            border-radius: 0.25rem;
            font-size: 0.9rem;
        }
        div[class*="st-key-deck-card-row-"] p:hover {
            background: rgba(190, 188, 182, 0.1);
            cursor: default;
        }
        div[class*="st-key-deck-card-row-"] img {
            width: 0.85rem;
            height: 0.85rem;
            vertical-align: middle;
        }
        div[class*="st-key-deck-card-row-"] p img {
            flex: 0 0 auto;
        }
        div.st-key-deck-list div[class*="st-key-deck-card-row-"] p {
            text-align: left;
        }
        div.st-key-deck-list div[class*="st-key-deck-card-row-"] {
            min-height: 1.75rem;
        }
        div.st-key-deck-list div[data-testid="stExpander"] {
            margin-bottom: -0.5rem;
        }
        div.st-key-deck-list div[data-testid="stExpander"] summary {
            min-height: 2rem;
            padding: 0.15rem 0.5rem;
        }
        div.st-key-deck-list div[data-testid="stExpanderDetails"] {
            padding: 0.25rem 0.5rem 0.5rem;
        }
        div.st-key-deck-list div[data-testid="stExpanderDetails"]
        div[data-testid="stVerticalBlock"] {
            gap: 0.15rem;
        }
        div.st-key-deck-list div[data-testid="stExpanderDetails"]
        div[data-testid="stHorizontalBlock"] {
            gap: 0.4rem;
        }
        .mana-curve {
            display: flex;
            align-items: flex-end;
            gap: 0.4rem;
            min-height: 10rem;
            overflow-x: auto;
            padding: 0.5rem 0.25rem 0;
        }
        .mana-curve-column {
            display: flex;
            flex: 1 0 2rem;
            flex-direction: column;
            align-items: center;
            gap: 0.25rem;
            min-width: 2rem;
        }
        .mana-curve-count,
        .mana-curve-label {
            color: inherit;
            font-size: 0.75rem;
        }
        .mana-curve-track {
            display: flex;
            align-items: flex-end;
            width: 100%;
            height: 7.5rem;
            border-bottom: 1px solid currentColor;
        }
        .mana-curve-bar {
            width: 100%;
            min-height: 4px;
            border-radius: 0.2rem 0.2rem 0 0;
            background: var(--primary-color, #ff4b4b);
        }
        @media (min-width: 768px) {
            div[data-testid="stLayoutWrapper"]:has(
                > div.stVerticalBlock.st-key-selected-card-preview
            ) {
                position: sticky;
                top: 1rem;
                align-self: flex-start;
                z-index: 5;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    page = st.sidebar.radio(
        "Navigation",
        ["Deck Builder", "Deck", "Suggestions", "Catalog"],
        key="page_navigation",
    )
    if page == "Deck Builder":
        st.title("Mana Architect")
        st.caption(
            "Your collection is the source of truth. Decks and rules are checked locally."
        )
    else:
        st.caption("Mana Architect")

    engine = get_engine()
    with Session(engine) as session:
        metadata = session.get(SyncMetadata, "bulk_updated_at")
        if metadata is None:
            st.info(
                "Card data has not been synchronized. Run "
                "`python -m mana_architect sync-scryfall` before using Commander "
                "filters and deckbuilding."
            )
        else:
            st.caption(f"Scryfall snapshot: {metadata.value}")

        if page == "Deck Builder":
            _render_deck_builder(session)
        elif page == "Suggestions":
            _render_collection_suggestions(session)
        elif page == "Catalog":
            _render_catalog_admin(session)
        else:
            _render_deck_page(session)


if __name__ == "__main__":
    main()
