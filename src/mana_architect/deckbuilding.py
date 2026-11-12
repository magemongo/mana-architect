from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mana_architect.catalog import (
    DEFAULT_CATALOG_DIR,
    StrategyCatalog,
    load_strategy_catalog,
)
from mana_architect.classification_repository import (
    load_card_classification_overrides,
    load_commander_affinity_profile,
    load_current_classifications,
)
from mana_architect.commanders import CommanderOption, resolve_commander
from mana_architect.classification import ClassificationResult
from mana_architect.commander_affinity import CommanderAffinityProfile
from mana_architect.models import Card, CollectionPrinting
from mana_architect.overrides import ManualOverride
from mana_architect.roles import classify_card_roles
from mana_architect.strategy import (
    CommanderStrategyProfile,
    load_strategy_profile,
    match_strategy_signals,
    strategy_signal_score,
)
from mana_architect.synergy import CardScoreInput, score_card_for_strategy
from mana_architect.validation import (
    DeckCard,
    DeckValidationResult,
    deck_name_copy_limit,
    is_basic_land,
    validate_deck,
)


BASIC_LAND_COLORS = {
    "plains": "W",
    "island": "U",
    "swamp": "B",
    "mountain": "R",
    "forest": "G",
}
ROLE_NAMES = (
    "Ramp (heuristic)",
    "Card Draw (heuristic)",
    "Removal (heuristic)",
    "Recursion (heuristic)",
)
_CATALOG_SCORE_WEIGHT = 0.25


class DeckBuildError(ValueError):
    """Raised when the collection cannot satisfy a requested draft."""


@dataclass(frozen=True)
class DeckBuildResult:
    commander: CommanderOption
    cards: tuple[DeckCard, ...]
    land_count: int
    role_counts: dict[str, int]
    validation: DeckValidationResult
    catalog_strategy_id: str | None = None
    catalog_strategy_name: str | None = None
    unclassified_count: int = 0


@dataclass(frozen=True)
class DeckCardDetails:
    oracle_id: str
    name: str
    quantity: int
    mana_cost: str | None
    cmc: float | None
    type_line: str | None
    image_uri: str | None
    oracle_text: str | None
    roles: tuple[str, ...]
    strategy_signals: tuple[str, ...]
    is_commander: bool


@dataclass(frozen=True)
class DeckAnalysis:
    commander_name: str
    strategy_name: str | None
    strategy_themes: tuple[str, ...]
    total_cards: int
    land_count: int
    nonland_count: int
    mana_curve: dict[float, int]
    role_counts: dict[str, int]
    cards: tuple[DeckCardDetails, ...]
    validation: DeckValidationResult


@dataclass(frozen=True)
class _Candidate:
    card: Card
    quantity: int
    roles: tuple[str, ...]
    strategy_signals: tuple[str, ...] = ()
    catalog_synergy_score: float | None = None


def _basic_land_color(card: Card) -> str | None:
    type_line = card.type_line or ""
    _, separator, subtypes = type_line.partition("—")
    if not separator:
        return None
    for subtype in subtypes.casefold().split():
        if subtype in BASIC_LAND_COLORS:
            return BASIC_LAND_COLORS[subtype]
    return None


def _candidate_score(
    candidate: _Candidate,
    role_counts: Counter[str],
    curve_counts: Counter[float],
    profile: CommanderStrategyProfile | None = None,
) -> tuple[float, float, str, str]:
    role_score = (
        sum(1 / (1 + role_counts[role]) for role in candidate.roles) / len(candidate.roles)
        if candidate.roles
        else 0.0
    )
    curve_score = (
        0.25 / (1 + curve_counts[candidate.card.cmc])
        if candidate.card.cmc is not None
        else 0.0
    )
    synergy_score = (
        candidate.catalog_synergy_score
        if candidate.catalog_synergy_score is not None
        else strategy_signal_score(profile, candidate.strategy_signals)
    )
    return (
        -(role_score + curve_score + synergy_score),
        candidate.card.cmc if candidate.card.cmc is not None else float("inf"),
        candidate.card.name.casefold(),
        candidate.card.oracle_id,
    )


def _select_lands(
    candidates: list[_Candidate],
    count: int,
    commander_identity: set[str],
    utility_land_limit: int,
    unclassified_count: int = 0,
) -> list[_Candidate]:
    selected: list[_Candidate] = []
    selected_quantities: Counter[str] = Counter()
    selected_names: Counter[str] = Counter()
    color_sources: Counter[str] = Counter()
    utility_count = 0
    colors = set(commander_identity)

    def produced_colors(candidate: _Candidate) -> set[str]:
        if candidate.card.produced_mana is not None:
            return set(candidate.card.produced_mana) & colors
        basic_color = _basic_land_color(candidate.card)
        if basic_color in colors:
            return {basic_color}
        oracle_text = candidate.card.oracle_text or ""
        produced: set[str] = set()
        for line in oracle_text.splitlines():
            match = re.search(r"\badd\b([^.\n]*)", line, re.IGNORECASE)
            if match is None:
                continue
            output = match.group(1)
            if re.search(r"\bany color\b", output, re.IGNORECASE):
                return colors
            produced.update(
                color
                for color in re.findall(r"\{([WUBRG])\}", output)
                if color in colors
            )
        return produced

    def is_utility(candidate: _Candidate) -> bool:
        if is_basic_land(candidate.card.type_line):
            return False
        for line in (candidate.card.oracle_text or "").splitlines():
            if ":" in line and not re.search(
                r":\s*add\b",
                line,
                re.IGNORECASE,
            ):
                return True
        return False

    def can_add(candidate: _Candidate) -> bool:
        name = candidate.card.name.casefold()
        copy_limit = (
            100
            if is_basic_land(candidate.card.type_line)
            else deck_name_copy_limit(name, candidate.card.oracle_text)
        )
        return (
            selected_quantities[candidate.card.oracle_id] < candidate.quantity
            and selected_names[name] < copy_limit
        )

    def add(candidate: _Candidate) -> None:
        nonlocal utility_count
        selected.append(candidate)
        selected_quantities[candidate.card.oracle_id] += 1
        selected_names[candidate.card.name.casefold()] += 1
        color_sources.update(produced_colors(candidate))
        if not produced_colors(candidate) and is_utility(candidate):
            utility_count += 1

    def best_candidate(
        pool: list[_Candidate],
        allow_utility_overflow: bool = False,
    ) -> _Candidate | None:
        available = [
            candidate
            for candidate in pool
            if can_add(candidate)
            and (
                produced_colors(candidate)
                or not is_utility(candidate)
                or allow_utility_overflow
                or utility_count < utility_land_limit
            )
        ]
        if not available:
            return None

        def rank(
            candidate: _Candidate,
        ) -> tuple[float, float, float, str, str]:
            supported_colors = produced_colors(candidate)
            coverage = sum(
                1 / (1 + color_sources[color]) for color in supported_colors
            )
            multicolor_bonus = max(0, len(supported_colors) - 1) * 0.1
            enters_tapped_penalty = 0.2 if re.search(
                r"\benters (?:the battlefield )?tapped\b",
                candidate.card.oracle_text or "",
                re.IGNORECASE,
            ) else 0.0
            utility_bonus = (
                0.1
                if not supported_colors and is_utility(candidate)
                else 0.0
            )
            return (
                -(coverage + multicolor_bonus + utility_bonus - enters_tapped_penalty),
                -(
                    candidate.catalog_synergy_score
                    if candidate.catalog_synergy_score is not None
                    else 0.0
                ),
                candidate.card.cmc if candidate.card.cmc is not None else float("inf"),
                candidate.card.name.casefold(),
                candidate.card.oracle_id,
            )

        return min(available, key=rank)

    utility_candidates = [
        candidate
        for candidate in candidates
        if is_utility(candidate) and not produced_colors(candidate)
    ]
    available_utility_names = {
        candidate.card.name.casefold()
        for candidate in utility_candidates
        if can_add(candidate)
    }
    utility_target = min(
        count,
        utility_land_limit,
        len(available_utility_names),
    )
    color_candidates = [
        candidate for candidate in candidates if produced_colors(candidate)
    ]

    while len(selected) < count - utility_target:
        candidate = best_candidate(color_candidates)
        if candidate is None:
            break
        add(candidate)

    while len(selected) < count and utility_count < utility_target:
        candidate = best_candidate(utility_candidates)
        if candidate is None:
            break
        add(candidate)

    while len(selected) < count:
        candidate = best_candidate(candidates)
        if candidate is None:
            candidate = best_candidate(candidates, allow_utility_overflow=True)
        if candidate is None:
            break
        add(candidate)

    if len(selected) != count:
        message = (
            f"Requested {count} lands, but only {len(selected)} legal land "
            "identities are available in the collection."
        )
        if unclassified_count:
            message += (
                f" {unclassified_count} cards were skipped because they lack "
                "current classifications. Reclassify the collection and try again."
            )
        raise DeckBuildError(message)
    return selected


def build_deck(
    session: Session,
    commander_identifier: str,
    land_count: int = 38,
    *,
    strategy_id: str | None = None,
    config_dir: Path = DEFAULT_CATALOG_DIR,
) -> DeckBuildResult:
    if isinstance(land_count, bool) or not isinstance(land_count, int):
        raise DeckBuildError("A quantidade de terrenos deve ser um inteiro.")
    if not 0 <= land_count <= 99:
        raise DeckBuildError("A quantidade de terrenos deve estar entre 0 e 99.")
    if strategy_id is not None and (
        not isinstance(strategy_id, str) or not strategy_id.strip()
    ):
        raise DeckBuildError("strategy_id must be a non-empty string or None.")

    commander = resolve_commander(session, commander_identifier)
    commander_card = session.get(Card, commander.oracle_id)
    if commander_card is None:
        raise DeckBuildError("Commander data is unavailable.")

    catalog: StrategyCatalog | None = None
    catalog_strategy_name: str | None = None
    commander_affinities: CommanderAffinityProfile | None = None
    unclassified_count = 0
    catalog_scores: dict[str, float] = {}
    if strategy_id is not None:
        catalog = load_strategy_catalog(config_dir)
        strategy = next(
            (
                item
                for item in catalog.strategies
                if item.id == strategy_id and item.active
            ),
            None,
        )
        if strategy is None:
            raise DeckBuildError(
                f"Active strategy does not exist in the catalog: '{strategy_id}'."
            )
        catalog_strategy_name = strategy.name
        commander_affinities = load_commander_affinity_profile(
            session,
            commander.oracle_id,
            catalog,
        )
        if commander_affinities is None or not any(
            affinity.strategy_id == strategy_id
            for affinity in commander_affinities.strategy_affinities
        ):
            raise DeckBuildError(
                "Add a commander affinity for this strategy on the Suggestions "
                "page before building the deck."
            )

    candidate_rows = session.execute(
        select(Card, func.sum(CollectionPrinting.quantity))
        .join(CollectionPrinting, CollectionPrinting.oracle_id == Card.oracle_id)
        .where(
            Card.legalities["commander"].as_string() == "legal",
            Card.color_identity.is_not(None),
        )
        .group_by(Card.oracle_id)
        .order_by(Card.name, Card.oracle_id)
    ).all()
    commander_identity = set(commander.color_identity)
    candidate_oracle_ids = tuple(
        card.oracle_id
        for card, _ in candidate_rows
        if card.oracle_id != commander.oracle_id
        and card.color_identity is not None
        and set(card.color_identity).issubset(commander_identity)
    )
    candidate_oracle_id_set = set(candidate_oracle_ids)
    classifications: dict[str, ClassificationResult] = {}
    overrides: dict[str, tuple[ManualOverride, ...]] = {}
    if catalog is not None:
        classifications = load_current_classifications(
            session,
            candidate_oracle_ids,
            catalog,
            config_dir,
        )
        overrides = load_card_classification_overrides(
            session,
            candidate_oracle_ids,
            catalog,
        )
        unclassified_count = len(
            set(candidate_oracle_ids) - set(classifications)
        )
        if commander_affinities is None:
            raise DeckBuildError(
                "The commander's affinity profile is unavailable."
            )
        for card, quantity in candidate_rows:
            if (
                card.oracle_id == commander.oracle_id
                or card.oracle_id not in candidate_oracle_id_set
            ):
                continue
            classification = classifications.get(card.oracle_id)
            if classification is None:
                continue
            score = score_card_for_strategy(
                card=CardScoreInput(
                    oracle_id=card.oracle_id,
                    name=card.name,
                    color_identity=tuple(card.color_identity),
                    format_legal=True,
                    classifications=classification,
                    available_quantity=int(quantity),
                ),
                commander_oracle_id=commander.oracle_id,
                commander_color_identity=tuple(commander.color_identity),
                commander_affinities=commander_affinities,
                strategy_id=strategy_id,
                catalog=catalog,
                only_collection=True,
                overrides=overrides.get(card.oracle_id, ()),
            )
            if not score.eligible:
                continue
            if not score.rankable or score.score is None:
                raise DeckBuildError(
                    f"Could not calculate the score for '{card.name}' "
                    f"for strategy '{strategy_id}'."
                )
            score_minimum = catalog.scoring.normalization.minimum
            score_range = (
                catalog.scoring.normalization.maximum - score_minimum
            )
            if score_range <= 0:
                raise DeckBuildError(
                    "The catalog score normalization range is invalid."
                )
            catalog_scores[card.oracle_id] = (
                _CATALOG_SCORE_WEIGHT
                * (score.score - score_minimum)
                / score_range
            )

    profile = load_strategy_profile(commander.oracle_id)
    candidates = [
        _Candidate(
            card=card,
            quantity=int(quantity),
            roles=classify_card_roles(card.type_line, card.oracle_text),
            strategy_signals=match_strategy_signals(
                profile,
                card.type_line,
                card.oracle_text,
            ),
            catalog_synergy_score=catalog_scores.get(card.oracle_id),
        )
        for card, quantity in candidate_rows
        if card.oracle_id != commander.oracle_id
        and card.color_identity is not None
        and set(card.color_identity).issubset(commander_identity)
        and (
            strategy_id is None
            or card.oracle_id in catalog_scores
        )
    ]

    land_candidates = [
        candidate
        for candidate in candidates
        if candidate.card.type_line
        and "land" in candidate.card.type_line.casefold().split("—", maxsplit=1)[0].split()
    ]
    selected_lands = _select_lands(
        land_candidates,
        land_count,
        commander_identity,
        profile.utility_land_limit
        if profile is not None
        else max(1, land_count // 10),
        unclassified_count=unclassified_count,
    )

    selected: dict[str, int] = {commander.oracle_id: 1}
    selected_names: Counter[str] = Counter({commander_card.name.casefold(): 1})
    for candidate in selected_lands:
        selected[candidate.card.oracle_id] = (
            selected.get(candidate.card.oracle_id, 0) + 1
        )
        selected_names[candidate.card.name.casefold()] += 1

    commander_limit = deck_name_copy_limit(
        commander_card.name,
        commander_card.oracle_text,
    )
    if selected_names[commander_card.name.casefold()] > commander_limit:
        raise DeckBuildError(
            "A quantidade de terrenos configurada conflita com o nome do comandante."
        )

    nonland_candidates = [
        candidate
        for candidate in candidates
        if candidate.card.type_line
        and "land" not in candidate.card.type_line.casefold().split("—", maxsplit=1)[0].split()
    ]
    nonland_candidates = [
        candidate
        for candidate in nonland_candidates
        if selected_names[candidate.card.name.casefold()]
        < deck_name_copy_limit(candidate.card.name, candidate.card.oracle_text)
    ]
    target_nonlands = 99 - land_count
    role_counts: Counter[str] = Counter()
    curve_counts: Counter[float] = Counter()
    selected_nonlands = 0
    while selected_nonlands < target_nonlands:
        eligible = [
            candidate
            for candidate in nonland_candidates
            if selected.get(candidate.card.oracle_id, 0) < candidate.quantity
            and selected_names[candidate.card.name.casefold()]
            < deck_name_copy_limit(candidate.card.name, candidate.card.oracle_text)
        ]
        if not eligible:
            break
        candidate = min(
            eligible,
            key=lambda item: _candidate_score(
                item,
                role_counts,
                curve_counts,
                profile,
            ),
        )
        copies = min(
            candidate.quantity - selected.get(candidate.card.oracle_id, 0),
            deck_name_copy_limit(candidate.card.name, candidate.card.oracle_text)
            - selected_names[candidate.card.name.casefold()],
            target_nonlands - selected_nonlands,
        )
        selected[candidate.card.oracle_id] = selected.get(candidate.card.oracle_id, 0) + copies
        selected_names[candidate.card.name.casefold()] += copies
        for _ in range(copies):
            role_counts.update(candidate.roles)
            if candidate.card.cmc is not None:
                curve_counts[candidate.card.cmc] += 1
        selected_nonlands += copies

    if selected_nonlands != target_nonlands:
        classification_note = (
            f" {unclassified_count} collection cards were skipped because they "
            "lack current classifications. Reclassify the collection and try again."
            if unclassified_count
            else ""
        )
        raise DeckBuildError(
            "There are not enough distinct nonland cards: "
            f"{target_nonlands} were needed, but only {selected_nonlands} could be "
            "selected while respecting collection, legality, and singleton rules."
            f"{classification_note}"
        )

    deck_cards = tuple(
        DeckCard(oracle_id=oracle_id, quantity=quantity)
        for oracle_id, quantity in sorted(selected.items())
    )
    validation = validate_deck(session, commander.oracle_id, list(deck_cards))
    if validation.errors:
        details = "; ".join(issue.message for issue in validation.errors[:5])
        raise DeckBuildError(
            "The generated draft did not pass validation and will not be exported. "
            f"Issues: {details}"
        )

    actual_role_counts: Counter[str] = Counter()
    cards_by_id = {candidate.card.oracle_id: candidate for candidate in candidates}
    for deck_card in deck_cards:
        candidate = cards_by_id.get(deck_card.oracle_id)
        if candidate is not None:
            for _ in range(deck_card.quantity):
                actual_role_counts.update(candidate.roles)

    return DeckBuildResult(
        commander=commander,
        cards=deck_cards,
        land_count=land_count,
        role_counts={role: actual_role_counts[role] for role in ROLE_NAMES},
        validation=validation,
        catalog_strategy_id=strategy_id,
        catalog_strategy_name=catalog_strategy_name,
        unclassified_count=unclassified_count,
    )


def export_deck(result: DeckBuildResult, path: Path, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"File '{path}' already exists. Use --overwrite to replace it."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    draft = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "land_count": result.land_count,
        "validation_status": result.validation.status,
        "data_as_of": result.validation.data_as_of,
        "rules_source": result.validation.rules_source,
        "legality_source": result.validation.legality_source,
        "role_counts_heuristic": result.role_counts,
    }
    if result.catalog_strategy_id is not None:
        draft["catalog_strategy"] = {
            "id": result.catalog_strategy_id,
            "name": result.catalog_strategy_name,
        }
        draft["unclassified_count"] = result.unclassified_count
    payload = {
        "commander": result.commander.oracle_id,
        "cards": [
            {"oracle_id": entry.oracle_id, "quantity": entry.quantity}
            for entry in result.cards
        ],
        "draft": draft,
    }
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def analyze_deck(
    session: Session,
    commander_identifier: str,
    deck_cards: list[DeckCard],
) -> DeckAnalysis:
    commander = resolve_commander(session, commander_identifier)
    profile = load_strategy_profile(commander.oracle_id)
    combined_quantities: Counter[str] = Counter()
    for entry in deck_cards:
        combined_quantities[entry.oracle_id.strip()] += entry.quantity

    cards_by_id = {
        card.oracle_id: card
        for card in session.scalars(
            select(Card).where(Card.oracle_id.in_(combined_quantities))
        )
    } if combined_quantities else {}
    details: list[DeckCardDetails] = []
    mana_curve: Counter[float] = Counter()
    role_counts: Counter[str] = Counter()
    land_count = 0

    for oracle_id, quantity in combined_quantities.items():
        card = cards_by_id.get(oracle_id)
        if card is None:
            details.append(
                DeckCardDetails(
                    oracle_id=oracle_id,
                    name=f"[Carta sem dados: {oracle_id}]",
                    quantity=quantity,
                    mana_cost=None,
                    cmc=None,
                    type_line=None,
                    image_uri=None,
                    oracle_text=None,
                    roles=(),
                    strategy_signals=(),
                    is_commander=oracle_id == commander.oracle_id,
                )
            )
            continue

        roles = classify_card_roles(card.type_line, card.oracle_text)
        is_land = bool(
            card.type_line
            and "land"
            in card.type_line.casefold().split("—", maxsplit=1)[0].split()
        )
        if is_land:
            land_count += quantity
        else:
            if card.cmc is not None:
                mana_curve[card.cmc] += quantity
            role_counts.update(role for role in roles if role in ROLE_NAMES for _ in range(quantity))
        details.append(
            DeckCardDetails(
                oracle_id=oracle_id,
                name=card.name,
                quantity=quantity,
                mana_cost=card.mana_cost,
                cmc=card.cmc,
                type_line=card.type_line,
                image_uri=card.image_uri,
                oracle_text=card.oracle_text,
                roles=roles,
                strategy_signals=match_strategy_signals(
                    profile,
                    card.type_line,
                    card.oracle_text,
                ),
                is_commander=oracle_id == commander.oracle_id,
            )
        )

    details.sort(
        key=lambda card: (
            not card.is_commander,
            card.name.casefold(),
            card.oracle_id,
        )
    )
    validation = validate_deck(session, commander.oracle_id, deck_cards)
    total_cards = sum(combined_quantities.values())
    return DeckAnalysis(
        commander_name=commander.name,
        strategy_name=profile.name if profile else None,
        strategy_themes=profile.themes if profile else (),
        total_cards=total_cards,
        land_count=land_count,
        nonland_count=total_cards - land_count,
        mana_curve=dict(sorted(mana_curve.items())),
        role_counts={role: role_counts[role] for role in ROLE_NAMES},
        cards=tuple(details),
        validation=validation,
    )


def export_decklist(
    analysis: DeckAnalysis,
    path: Path,
    overwrite: bool = False,
) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"File '{path}' already exists. Use --overwrite to replace it."
        )

    lines = [f"# Commander: {analysis.commander_name}"]
    lines.extend(
        f"{card.quantity} {card.name}"
        for card in analysis.cards
        if card.is_commander
    )
    lines.append("")
    lines.append("# Main deck")
    lines.extend(
        f"{card.quantity} {card.name}"
        for card in analysis.cards
        if not card.is_commander
    )
    payload = "\n".join(lines) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary_path.write_text(payload, encoding="utf-8")
        temporary_path.replace(path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
