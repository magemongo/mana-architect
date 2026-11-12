from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Literal

import pandas as pd
import streamlit as st
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from mana_architect.catalog import (
    DEFAULT_CATALOG_DIR,
    StrategyCatalog,
    load_strategy_catalog,
)
from mana_architect.commanders import CommanderOption, list_available_commanders
from mana_architect.classification_repository import (
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
    CardClassificationOverrideRecord,
    CommanderAffinityRecord,
    ManualOverrideRecord,
    SyncMetadata,
)
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
    "W": "Branco",
    "U": "Azul",
    "B": "Preto",
    "R": "Vermelho",
    "G": "Verde",
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
    return " ".join(COLOR_NAMES.get(color, color) for color in identity) or "Sem cor"


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
        '<div class="mana-curve" role="img" aria-label="Histograma da curva de mana">'
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
                {"oracle_id": card.oracle_id, "quantity": card.quantity}
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
        lines.append(f"# Estratégia do catálogo: {catalog_strategy_name}")
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
            "Modo de geração desconhecido; escolha Legado ou Catálogo."
        )
        return
    if generation_mode == "catalog" and not strategy_id:
        st.session_state["deck_build_error"] = (
            "Não há uma estratégia ativa disponível no catálogo."
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
    st.session_state["deck_cards"] = [
        {"oracle_id": card.oracle_id, "quantity": card.quantity}
        for card in result.cards
    ]
    st.session_state["page_navigation"] = "Deck"


def _card_category(card: DeckCardDetails) -> str:
    if card.is_commander:
        return "Comandante"
    card_types = (card.type_line or "").split("—", maxsplit=1)[0].casefold().split()
    categories = (
        ("Criaturas", "creature"),
        ("Planeswalkers", "planeswalker"),
        ("Batalhas", "battle"),
        ("Artefatos", "artifact"),
        ("Encantamentos", "enchantment"),
        ("Mágicas instantâneas", "instant"),
        ("Feitiços", "sorcery"),
        ("Terrenos", "land"),
    )
    for label, card_type in categories:
        if card_type in card_types:
            return label
    return "Outras cartas"


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
                else '<span>Imagem indisponível para esta carta.</span>'
            )
            + "</div>"
        )
        preview_details.append(
            f'<div class="deck-preview-details" data-card-id="{safe_id}">'
            + (
                f'<p class="deck-preview-signals">'
                f'Sinergias heurísticas: '
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
        st.info("Gere um deck no Construtor para visualizá-lo aqui.")
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

    st.title(analysis.commander_name)
    catalog_strategy_name = st.session_state.get("deck_catalog_strategy_name")
    if catalog_strategy_name:
        st.caption(f"Estratégia do catálogo: {catalog_strategy_name}")
    elif analysis.strategy_name:
        st.caption(
            f"{analysis.strategy_name} · "
            f"{' · '.join(analysis.strategy_themes)}"
        )
    unclassified_count = st.session_state.get("deck_unclassified_count", 0)
    if unclassified_count:
        st.warning(
            f"{unclassified_count} cartas sem classificação atual foram "
            "desconsideradas pelo gerador do catálogo."
        )

    metric_columns = st.columns(4)
    metric_columns[0].metric("Cartas", f"{analysis.total_cards}/100")
    metric_columns[1].metric("Terrenos", analysis.land_count)
    metric_columns[2].metric("Não-terrenos", analysis.nonland_count)
    metric_columns[3].metric(
        "Validação",
        {
            "valid": "Válido",
            "incomplete": "Incompleta",
            "invalid": "Inválido",
        }[analysis.validation.status],
    )

    if analysis.validation.status == "valid":
        st.success("Deck aprovado pelo validador para os dados sincronizados.")
    elif analysis.validation.status == "incomplete":
        st.warning("A validação está incompleta devido a dados ausentes.")
    else:
        st.error("O deck tem problemas; consulte os detalhes antes de exportar.")
    if analysis.validation.data_as_of:
        st.caption(
            f"Snapshot de legalidade: {analysis.validation.data_as_of}. "
            "Isso não garante que a banlist permaneça atual."
        )
    for issue in analysis.validation.errors:
        st.error(f"{issue.message} [{issue.code}]")
    for issue in analysis.validation.unverified:
        st.warning(f"Não verificado: {issue.message}")
    for issue in analysis.validation.warnings:
        st.caption(issue.message)

    actions = st.columns(2)
    if analysis.validation.status == "valid":
        actions[0].download_button(
            "Baixar JSON",
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
            "Baixar decklist TXT",
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
            st.subheader("Lista do deck")
            grouped_cards: dict[str, list[DeckCardDetails]] = {}
            for card in analysis.cards:
                grouped_cards.setdefault(_card_category(card), []).append(card)
            category_order = (
                "Comandante",
                "Criaturas",
                "Artefatos",
                "Encantamentos",
                "Mágicas instantâneas",
                "Feitiços",
                "Terrenos",
                "Planeswalkers",
                "Batalhas",
                "Outras cartas",
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
            st.subheader("Prévia da carta")
            st.markdown(
                _deck_preview_markup(
                    list(analysis.cards),
                    row_keys_by_id,
                    commander_id,
                ),
                unsafe_allow_html=True,
            )

    with st.expander("Análise do deck"):
        st.markdown("**Curva de mana**")
        if analysis.mana_curve:
            st.markdown(
                _mana_curve_markup(analysis.mana_curve),
                unsafe_allow_html=True,
            )
        else:
            st.caption("Nenhum custo de mana disponível para exibir.")
        st.markdown("**Funções detectadas (heurísticas)**")
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
        "primary": "Primária",
        "secondary": "Secundária",
        "complementary": "Complementar",
    }
    rows = [
        {
            "strategy_id": item.strategy_id,
            "nível": labels[item.level],
            "força": item.strength_score,
            "justificativa": item.rationale or "",
        }
        for item in existing
    ]
    with st.expander("Afinidades do comandante", expanded=True):
        st.caption(
            "Adicione várias estratégias e ajuste suas afinidades na mesma "
            "tela. Força e níveis são heurísticos, não probabilidades."
        )
        with st.form(f"commander_affinities_{commander_id}"):
            affinity_rows = pd.DataFrame(
                {
                    "strategy_id": pd.Series(
                        [row["strategy_id"] for row in rows], dtype="string"
                    ),
                    "nível": pd.Series(
                        [row["nível"] for row in rows], dtype="string"
                    ),
                    "força": pd.Series(
                        [row["força"] for row in rows], dtype="float64"
                    ),
                    "justificativa": pd.Series(
                        [row["justificativa"] for row in rows], dtype="string"
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
                        "Estratégia",
                        options=[item.id for item in catalog.strategies],
                        required=True,
                        format_func=lambda value: names.get(value, value),
                    ),
                    "nível": st.column_config.SelectboxColumn(
                        "Nível",
                        options=list(labels.values()),
                        required=True,
                    ),
                    "força": st.column_config.NumberColumn(
                        "Força",
                        min_value=0.0,
                        max_value=1.0,
                        step=0.05,
                        format="%.2f",
                    ),
                    "justificativa": st.column_config.TextColumn("Justificativa"),
                },
            )
            tag_names = {item.id: item.name for item in catalog.tags}
            selected_tags = st.multiselect(
                "Tags associadas ao comandante",
                options=[item.id for item in catalog.tags],
                default=[item.tag_id for item in profile.tag_affinities] if profile else [],
                format_func=lambda tag_id: tag_names[tag_id],
                key=f"affinity_tags_{commander_id}",
            )
            submitted = st.form_submit_button(
                "Salvar afinidades",
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
                        raise ValueError("Cada estratégia pode aparecer uma vez.")
                    seen.add(strategy_id)
                    strength = row.get("força")
                    if strength is None:
                        strength = 0.5
                    strategy_affinities.append(
                        CommanderStrategyAffinity(
                            strategy_id=strategy_id,
                            level=level_by_label[row["nível"]],
                            strength_score=float(strength),
                            evidence_ids=previous[strategy_id].evidence_ids
                            if strategy_id in previous else (),
                            rationale=row.get("justificativa", "").strip() or None,
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
                st.error(f"Afinidades não salvas: {error}")
            else:
                st.session_state["suggestion_notice"] = "Afinidades do comandante salvas."
                st.rerun()


def _bump_catalog_version(data: dict[str, object], field: str) -> None:
    version = data.get(field)
    if not isinstance(version, str):
        raise ValueError(f"Versão ausente no campo {field}.")
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"Versão inválida no campo {field}: {version}.")
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
        raise ValueError(f"Alteração não salva: {error}") from error


def _render_catalog_admin(session: Session) -> None:
    st.header("Catálogo de estratégias e funções")
    st.caption(
        "O catálogo é versionado em arquivos locais. Mudanças invalidam a "
        "classificação anterior e pedem uma reclassificação explícita."
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
    strategy_ids = [item["id"] for item in strategies_data["strategies"]]
    role_ids = [item["id"] for item in roles_data["roles"]]
    strategy_tab, role_tab = st.tabs(["Estratégias", "Funções"])

    with strategy_tab:
        st.dataframe(
            [
                {
                    "Estratégia": item["name"],
                    "ID": item["id"],
                    "Estado": "Ativa" if item["active"] else "Inativa",
                    "Descrição": item["description"],
                }
                for item in strategies_data["strategies"]
            ],
            hide_index=True,
            width="stretch",
        )
        action = st.segmented_control(
            "Ação para estratégias",
            options=["Editar", "Adicionar"],
            default="Editar",
            key="catalog_strategy_action",
        )
        selected = None
        if action == "Editar" and strategy_ids:
            selected = st.selectbox(
                "Estratégia",
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
                help="ID único em snake_case; IDs existentes não podem ser renomeados.",
            )
            name = st.text_input("Nome", value=current["name"] if current else "")
            description = st.text_area(
                "Descrição", value=current["description"] if current else ""
            )
            active = st.checkbox(
                "Estratégia ativa", value=current["active"] if current else True
            )
            nature = st.text_input(
                "Natureza (separe valores por vírgula)",
                value=", ".join(current["nature"]) if current else "resource_engine",
            )
            tag_weights = st.text_area(
                "Tags e pesos (uma por linha: id_da_tag=0.8)",
                value="\n".join(
                    f"{item['tag_id']}={item['weight']}"
                    for item in current["tag_affinities"]
                ) if current else "",
            )
            notes = st.text_area(
                "Notas (uma por linha)",
                value="\n".join(current["notes"]) if current else "",
            )
            save_strategy = st.form_submit_button("Salvar estratégia", type="primary")
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
                st.success("Estratégia salva. Reclassifique a coleção para aplicar a versão nova.")
                st.rerun()
        if current is not None:
            strategy_delete_confirmed = st.checkbox(
                "Confirmo a exclusão e a remoção das afinidades e correções vinculadas.",
                key=f"confirm_delete_strategy_{current['id']}",
            )
            if st.button(
                "Excluir estratégia", key=f"delete_strategy_{current['id']}"
            ):
                if not strategy_delete_confirmed:
                    st.warning("Confirme a exclusão antes de continuar.")
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
                        st.success("Estratégia excluída; o histórico de classificações foi preservado.")
                        st.rerun()

    with role_tab:
        st.dataframe(
            [
                {
                    "Função": item["name"],
                    "Grupo": (
                        "Serviço do deck"
                        if item["group"] == "deck_service"
                        else "Sinergia da estratégia"
                    ),
                    "Termos Oracle": ", ".join(item.get("oracle_keywords", [])),
                    "Descrição": item["description"],
                }
                for item in roles_data["roles"]
            ],
            hide_index=True,
            width="stretch",
        )
        action = st.segmented_control(
            "Ação para funções",
            options=["Editar", "Adicionar"],
            default="Editar",
            key="catalog_role_action",
        )
        selected = None
        if action == "Editar" and role_ids:
            selected = st.selectbox(
                "Função",
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
                help="ID único em snake_case; IDs existentes não podem ser renomeados.",
            )
            name = st.text_input("Nome", value=current["name"] if current else "")
            group = st.selectbox(
                "Grupo",
                options=["deck_service", "synergy_role"],
                index=(
                    ["deck_service", "synergy_role"].index(current["group"])
                    if current else 0
                ),
                format_func=lambda value: {
                    "deck_service": "Serviço do deck",
                    "synergy_role": "Sinergia da estratégia",
                }[value],
            )
            description = st.text_area(
                "Descrição", value=current["description"] if current else ""
            )
            oracle_keywords = st.text_area(
                "Termos Oracle (um por linha)",
                value="\n".join(current.get("oracle_keywords", [])) if current else "",
                help="Correspondências literais, sem distinção entre maiúsculas e minúsculas. Cada termo pode sugerir a função; a classificação permanece heurística.",
            )
            save_role = st.form_submit_button("Salvar função", type="primary")
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
                st.success("Função salva. Reclassifique a coleção para aplicar a versão nova.")
                st.rerun()
        if current is not None:
            role_delete_confirmed = st.checkbox(
                "Confirmo a exclusão e a remoção das regras e correções vinculadas.",
                key=f"confirm_delete_role_{current['id']}",
            )
            if st.button("Excluir função", key=f"delete_role_{current['id']}"):
                if not role_delete_confirmed:
                    st.warning("Confirme a exclusão antes de continuar.")
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
                        st.success("Função excluída; o histórico de classificações foi preservado.")
                        st.rerun()


def _render_suggestion_correction(
    session: Session,
    suggestion: CollectionSuggestion,
    catalog: StrategyCatalog,
) -> None:
    score = suggestion.score
    target_labels = {
        **{
            f"strategy:{item.id}": f"Estratégia — {item.name}"
            for item in catalog.strategies
        },
        **{
            f"tag:{item.id}": f"Tag — {item.name}"
            for item in catalog.tags
        },
        **{
            f"role:{item.id}": f"Função — {item.name}"
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
        "Forçar inclusão",
        "Forçar exclusão",
        "Voltar ao automático",
    )
    action_values: dict[str, OverrideAction | Literal["reset"]] = {
        "Forçar inclusão": "force_include",
        "Forçar exclusão": "force_exclude",
        "Voltar ao automático": "reset",
    }

    with st.expander("Corrigir classificação desta carta"):
        st.caption(
            "A correção fica vinculada a esta carta; a evidência automática "
            "original permanece armazenada."
        )
        with st.form(f"card_correction_{score.card_oracle_id}"):
            target_ref = st.selectbox(
                "Classificação",
                options=target_options,
                index=target_options.index(selected_target),
                format_func=lambda value: target_labels[value],
                key=target_widget_key,
            )
            action_label = st.selectbox(
                "Decisão",
                options=action_labels,
                index=(
                    action_labels.index("Forçar exclusão")
                    if existing_override
                    and existing_override.action == "force_exclude"
                    else action_labels.index("Forçar inclusão")
                ),
                key=f"correction_action_{score.card_oracle_id}",
            )
            reason = st.text_area(
                "Justificativa",
                value=existing_override.reason or "" if existing_override else "",
                key=f"correction_reason_{score.card_oracle_id}",
            )
            submitted = st.form_submit_button(
                "Aplicar correção",
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
            "Correção manual salva; a evidência automática foi preservada."
            if action != "reset"
            else "Correção removida; a classificação voltou ao automático."
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
            f"Disponíveis na coleção após considerar o deck: "
            f"{score.available_quantity}"
        )
        if matches:
            st.markdown(
                "**Correspondências:** "
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
        st.caption(
            "Funções detectadas: "
            + (", ".join(detected_roles) if detected_roles else "nenhuma")
        )
        with st.expander("Evidências e explicação do score"):
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
                state = "incluída" if decision.included else "excluída"
                st.markdown(
                    f"- **{label}** — {state}; origem: {decision.source}."
                )
                if evidence:
                    st.caption(evidence)
                if decision.override and decision.override.reason:
                    st.caption(f"Justificativa manual: {decision.override.reason}")
                if decision.conflict:
                    st.warning(
                        f"A correção manual contradiz evidência automática para {label}."
                    )
            st.markdown("**Componentes**")
            for component, value in score.components.items():
                st.text(f"{component}: {value:.3f}")
            st.markdown("**Razões do score**")
            for reason in score.reasons:
                detail = f" — {reason.detail}" if reason.detail else ""
                st.text(f"{reason.code}: {reason.contribution:+.2f}{detail}")
        _render_suggestion_correction(session, suggestion, catalog)


def _render_collection_suggestions(session: Session) -> None:
    st.header("Sugestões de cartas")
    st.caption(
        "Sugestões determinísticas da sua coleção; legalidade e identidade de "
        "cor são filtros, não pontos. Nenhuma chamada externa é feita."
    )
    catalog = load_strategy_catalog()
    commanders = _load_commander_options(session)
    if not commanders:
        st.warning(
            "Nenhum comandante disponível. Importe sua coleção e execute "
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
        "Comandante",
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
        "Estratégia alvo",
        options=strategy_ids,
        index=strategy_index,
        key="suggestion_strategy_id",
        format_func=lambda value: next(
            item.name for item in strategies if item.id == value
        ),
    )
    role_names = {item.id: item.name for item in catalog.roles}
    service_role_ids = [item.id for item in catalog.roles if item.group == "deck_service"]
    synergy_role_ids = [item.id for item in catalog.roles if item.group == "synergy_role"]
    with st.expander("Priorizar funções nas sugestões", expanded=False):
        service_roles = st.multiselect(
            "Serviços do deck",
            options=service_role_ids,
            format_func=lambda value: role_names[value],
            key="suggestion_service_roles",
            help="Ramp, compra, interação, proteção e outras funções de suporte.",
        )
        synergy_roles = st.multiselect(
            "Sinergias da estratégia",
            options=synergy_role_ids,
            format_func=lambda value: role_names[value],
            key="suggestion_synergy_roles",
            help="Funções temáticas como gerar fichas, habilitar sacrifícios ou converter uma mecânica em valor.",
        )
    required_roles = [*service_roles, *synergy_roles]
    search_clicked = st.button(
        "Buscar sugestões",
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
        "Reclassificar cartas da coleção",
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
            f"Classificação concluída: {summary.classified} atualizadas, "
            f"{summary.skipped} sem alterações."
        )
        st.rerun()

    saved_query = st.session_state.get("suggestion_query")
    active_query = (commander_id, strategy_id, tuple(required_roles))
    if saved_query != active_query:
        st.info("Escolha os filtros e clique em **Buscar sugestões**.")
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
    metrics[0].metric("Cartas da coleção", batch.total_candidates)
    metrics[1].metric("Sem classificação atual", batch.unclassified_count)
    metrics[2].metric("Inelegíveis", batch.ineligible_count)
    if batch.unclassified_count:
        st.info(
            "Há cartas sem classificação atual. Use **Reclassificar cartas da "
            "coleção**; essa ação é local e não baixa dados."
        )
    if not batch.suggestions:
        st.info("Nenhuma carta elegível encontrada para esses filtros.")
        return
    st.subheader(
        f"Melhores correspondências para {batch.strategy_name} — "
        f"{batch.commander.name}"
    )
    for suggestion in batch.suggestions:
        _render_collection_suggestion(session, suggestion, catalog)


def _render_deck_builder(session: Session) -> None:
    st.header("Construtor de deck")
    st.caption(
        "Você escolhe o comandante. O gerador usa somente sua coleção e "
        "prioriza sinais de estratégia quando houver um perfil configurado."
    )
    commanders = _load_commander_options(session)
    if not commanders:
        st.warning(
            "Nenhum comandante disponível. Importe sua coleção e execute "
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
            "Comandante",
            options=commander_ids,
            key="builder_commander_id",
            format_func=lambda oracle_id: (
                f"{commander_by_id[oracle_id].name} — "
                f"{_format_identity(commander_by_id[oracle_id].color_identity)}"
            ),
        )
        st.selectbox(
            "Modo de estratégia",
            options=("legacy", "catalog"),
            format_func=lambda value: (
                "Legado" if value == "legacy" else "Estratégia do catálogo"
            ),
            key="builder_generation_mode",
        )
        if strategies:
            st.selectbox(
                "Estratégia do catálogo",
                options=tuple(strategy_names),
                format_func=lambda value: strategy_names[value],
                key="builder_strategy_id",
            )
        else:
            st.warning("Não há estratégias ativas no catálogo.")
        st.number_input(
            "Quantidade de terrenos",
            min_value=0,
            max_value=99,
            value=38,
            step=1,
            key="builder_land_count",
        )
        st.form_submit_button(
            "Gerar deck",
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
                f"Modo catálogo: {strategy_name}. O score complementa o "
                "balanceamento de funções, curva e terrenos."
            )
    elif selected_profile:
        st.caption(
            f"Perfil: {selected_profile.name} — "
            f"{', '.join(selected_profile.themes)}. "
            "Os sinais estratégicos são heurísticos."
        )
    else:
        st.caption(
            "Sem perfil estratégico específico; será usada a pontuação geral "
            "de funções e curva de mana."
        )

    if "deck_build_error" in st.session_state:
        st.error(st.session_state["deck_build_error"])
    if st.session_state.get("deck_cards"):
        st.info("Deck gerado. Abra **Deck** na navegação para visualizá-lo.")


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
        "Navegação",
        ["Construtor", "Deck", "Sugestões", "Catálogo"],
        key="page_navigation",
    )
    if page == "Construtor":
        st.title("Mana Architect")
        st.caption(
            "Sua coleção é a fonte de verdade. Decks e regras são verificados localmente."
        )
    else:
        st.caption("Mana Architect")

    engine = get_engine()
    with Session(engine) as session:
        metadata = session.get(SyncMetadata, "bulk_updated_at")
        if metadata is None:
            st.info(
                "Dados de cartas ainda não sincronizados. Execute "
                "`python -m mana_architect sync-scryfall` antes de usar filtros "
                "de Commander e deckbuilding."
            )
        else:
            st.caption(f"Snapshot Scryfall: {metadata.value}")

        if page == "Construtor":
            _render_deck_builder(session)
        elif page == "Sugestões":
            _render_collection_suggestions(session)
        elif page == "Catálogo":
            _render_catalog_admin(session)
        else:
            _render_deck_page(session)


if __name__ == "__main__":
    main()
