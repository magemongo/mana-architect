from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mana_architect.catalog import DEFAULT_CATALOG_DIR, StrategyCatalog
from mana_architect.classification import (
    ClassificationResult,
    with_deck_role_heuristics,
)
from mana_architect.classification_repository import (
    load_card_classification_overrides,
    load_commander_affinity_profile,
    load_current_classifications,
)
from mana_architect.commanders import CommanderOption, resolve_commander
from mana_architect.models import Card, CollectionPrinting
from mana_architect.overrides import ManualOverride, resolve_classification_overrides
from mana_architect.synergy import (
    CardScoreInput,
    CardSynergyScore,
    rank_scored_cards,
    score_card_for_strategy,
)
from mana_architect.validation import deck_name_copy_limit


@dataclass(frozen=True)
class CollectionSuggestion:
    score: CardSynergyScore
    type_line: str | None
    mana_cost: str | None
    oracle_text: str | None
    image_uri: str | None
    classification: ClassificationResult
    overrides: tuple[ManualOverride, ...]


@dataclass(frozen=True)
class CollectionSuggestionBatch:
    commander: CommanderOption
    strategy_name: str
    total_candidates: int
    unclassified_count: int
    unclassified_oracle_ids: tuple[str, ...]
    ineligible_count: int
    suggestions: tuple[CollectionSuggestion, ...]


@dataclass(frozen=True)
class IdentityCollectionSuggestion:
    oracle_id: str
    name: str
    type_line: str | None
    mana_cost: str | None
    oracle_text: str | None
    image_uri: str | None
    available_quantity: int
    score: float
    strategy_scores: Mapping[str, float]
    matching_strategy_ids: tuple[str, ...]
    role_ids: tuple[str, ...]
    classification: ClassificationResult
    overrides: tuple[ManualOverride, ...]


@dataclass(frozen=True)
class IdentityCollectionSuggestionBatch:
    color_identity: tuple[str, ...]
    strategy_ids: tuple[str, ...]
    total_candidates: int
    unclassified_count: int
    ineligible_count: int
    suggestions: tuple[IdentityCollectionSuggestion, ...]


def collection_oracle_ids(session: Session) -> tuple[str, ...]:
    return tuple(
        oracle_id
        for oracle_id in session.scalars(
            select(CollectionPrinting.oracle_id)
            .where(CollectionPrinting.oracle_id.is_not(None))
            .distinct()
            .order_by(CollectionPrinting.oracle_id)
        )
        if oracle_id is not None
    )


def suggest_collection_cards_by_identity(
    session: Session,
    color_identity: tuple[str, ...],
    strategy_ids: tuple[str, ...],
    catalog: StrategyCatalog,
    *,
    config_dir: Path = DEFAULT_CATALOG_DIR,
    prioritized_roles: tuple[str, ...] = (),
    limit: int = 50,
) -> IdentityCollectionSuggestionBatch:
    """Rank collection cards by selected strategy evidence and optional roles."""
    valid_colors = {"W", "U", "B", "R", "G"}
    if len(set(color_identity)) != len(color_identity) or not set(color_identity) <= valid_colors:
        raise ValueError("color_identity must contain unique Magic colors.")
    if not strategy_ids or len(set(strategy_ids)) != len(strategy_ids):
        raise ValueError("Select one or more unique strategies.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50.")
    strategies = {item.id: item for item in catalog.strategies if item.active}
    unknown_strategies = set(strategy_ids) - set(strategies)
    if unknown_strategies:
        raise ValueError(f"Unknown strategies: {', '.join(sorted(unknown_strategies))}.")
    roles = {item.id for item in catalog.roles}
    unknown_roles = set(prioritized_roles) - roles
    if unknown_roles:
        raise ValueError(f"Unknown roles: {', '.join(sorted(unknown_roles))}.")

    rows = session.execute(
        select(Card, func.sum(CollectionPrinting.quantity))
        .join(CollectionPrinting, CollectionPrinting.oracle_id == Card.oracle_id)
        .group_by(Card.oracle_id)
        .order_by(Card.name, Card.oracle_id)
    ).all()
    allowed_identity = set(color_identity)
    candidate_rows = [
        (card, int(quantity))
        for card, quantity in rows
        if card.color_identity is not None
        and set(card.color_identity) <= allowed_identity
    ]
    oracle_ids = tuple(card.oracle_id for card, _ in candidate_rows)
    classifications = load_current_classifications(session, oracle_ids, catalog, config_dir)
    card_overrides = load_card_classification_overrides(session, oracle_ids, catalog)
    unclassified = 0
    ineligible = 0
    suggestions: list[IdentityCollectionSuggestion] = []

    for card, quantity in candidate_rows:
        classification = classifications.get(card.oracle_id)
        if classification is None:
            unclassified += 1
            continue
        if _commander_legality(card) is not True:
            ineligible += 1
            continue
        overrides = card_overrides.get(card.oracle_id, ())
        enriched = with_deck_role_heuristics(
            card.type_line, card.oracle_text, classification, catalog
        )
        decisions = resolve_classification_overrides(enriched, overrides, catalog)
        evidence = tuple(
            item
            for decision in decisions
            if decision.included
            for item in decision.automatic_evidence
        )
        manual_inclusions = {
            (item.target_type, item.target_id)
            for item in decisions
            if item.included and item.override is not None
        }
        found_roles = {
            item.target_id for item in evidence if item.target_type == "role"
        } | {
            target_id
            for target_type, target_id in manual_inclusions
            if target_type == "role"
        }
        strategy_scores: dict[str, float] = {}
        for strategy_id in strategy_ids:
            weights = {
                item.tag_id: item.weight
                for item in strategies[strategy_id].tag_affinities
            }
            signals: dict[str, float] = {}
            direct_strategy_signal = 0.0
            for item in evidence:
                if item.target_type == "tag" and item.target_id in weights:
                    signals[item.target_id] = max(
                        signals.get(item.target_id, 0.0),
                        min(1.0, item.weight * item.confidence),
                    )
                elif item.target_type == "strategy" and item.target_id == strategy_id:
                    direct_strategy_signal = max(
                        direct_strategy_signal,
                        min(1.0, item.weight * item.confidence),
                    )
            for target_type, target_id in manual_inclusions:
                if target_type == "tag" and target_id in weights:
                    signals[target_id] = 1.0
                elif target_type == "strategy" and target_id == strategy_id:
                    direct_strategy_signal = 1.0
            denominator = sum(weights.values()) + (1.0 if direct_strategy_signal else 0.0)
            numerator = sum(weights[tag_id] * strength for tag_id, strength in signals.items())
            if direct_strategy_signal:
                numerator += direct_strategy_signal
            strategy_scores[strategy_id] = (
                round(100 * numerator / denominator, 1) if denominator else 0.0
            )

        best_strategy_score = max(strategy_scores.values(), default=0.0)
        matched_strategy_ids = tuple(
            strategy_id
            for strategy_id in strategy_ids
            if strategy_scores[strategy_id] > 0
        )
        matched_priorities = len(found_roles & set(prioritized_roles))
        if best_strategy_score <= 0 and matched_priorities == 0:
            continue
        role_bonus = (
            15 * matched_priorities / len(prioritized_roles)
            if prioritized_roles
            else 0.0
        )
        combined_score = min(100.0, best_strategy_score + role_bonus)
        suggestions.append(
            IdentityCollectionSuggestion(
                oracle_id=card.oracle_id,
                name=card.name,
                type_line=card.type_line,
                mana_cost=card.mana_cost,
                oracle_text=card.oracle_text,
                image_uri=card.image_uri,
                available_quantity=quantity,
                score=round(combined_score, 1),
                strategy_scores=MappingProxyType(strategy_scores),
                matching_strategy_ids=matched_strategy_ids,
                role_ids=tuple(sorted(found_roles)),
                classification=enriched,
                overrides=overrides,
            )
        )
    suggestions.sort(
        key=lambda item: (
            -item.score,
            -max(item.strategy_scores.values(), default=0.0),
            item.name.casefold(),
            item.oracle_id,
        )
    )
    return IdentityCollectionSuggestionBatch(
        color_identity=tuple(color for color in ("W", "U", "B", "R", "G") if color in allowed_identity),
        strategy_ids=strategy_ids,
        total_candidates=len(candidate_rows),
        unclassified_count=unclassified,
        ineligible_count=ineligible,
        suggestions=tuple(suggestions[:limit]),
    )


def suggest_collection_cards(
    session: Session,
    commander_identifier: str,
    strategy_id: str,
    catalog: StrategyCatalog,
    *,
    config_dir: Path = DEFAULT_CATALOG_DIR,
    required_roles: tuple[str, ...] | None = None,
    already_used: Mapping[str, int] | None = None,
    limit: int = 20,
) -> CollectionSuggestionBatch:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit deve ser um inteiro positivo.")
    used_quantities = dict(already_used or {})
    for oracle_id, quantity in used_quantities.items():
        if (
            not isinstance(oracle_id, str)
            or not oracle_id
            or isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity < 0
        ):
            raise ValueError("already_used must map Oracle IDs to non-negative integers.")

    commander = resolve_commander(session, commander_identifier)
    profile = load_commander_affinity_profile(
        session,
        commander.oracle_id,
        catalog,
    )
    if profile is None or not any(
        affinity.strategy_id == strategy_id
        for affinity in profile.strategy_affinities
    ):
        raise ValueError(
            "Add a commander affinity for this strategy before requesting "
            "suggestions."
        )
    strategy = next(
        (
            item
            for item in catalog.strategies
            if item.id == strategy_id and item.active
        ),
        None,
    )
    if strategy is None:
        raise ValueError(f"Active strategy does not exist in the catalog: '{strategy_id}'.")

    rows = session.execute(
        select(Card, func.sum(CollectionPrinting.quantity))
        .join(CollectionPrinting, CollectionPrinting.oracle_id == Card.oracle_id)
        .group_by(Card.oracle_id)
        .order_by(Card.name, Card.oracle_id)
    ).all()
    commander_identity = tuple(commander.color_identity)
    candidate_rows = [
        (card, int(quantity))
        for card, quantity in rows
        if card.oracle_id != commander.oracle_id
    ]
    oracle_ids = tuple(card.oracle_id for card, _ in candidate_rows)
    classifications = load_current_classifications(
        session,
        oracle_ids,
        catalog,
        config_dir,
    )
    card_overrides = load_card_classification_overrides(
        session,
        oracle_ids,
        catalog,
    )

    suggestions: list[CollectionSuggestion] = []
    ineligible_count = 0
    unclassified_ids: list[str] = []
    for card, quantity in candidate_rows:
        classification = classifications.get(card.oracle_id)
        if classification is None:
            unclassified_ids.append(card.oracle_id)
            continue
        already_used_quantity = used_quantities.get(card.oracle_id, 0)
        if (
            already_used_quantity
            >= deck_name_copy_limit(card.name, card.oracle_text)
        ):
            ineligible_count += 1
            continue
        overrides = card_overrides.get(card.oracle_id, ())
        scoring_classification = with_deck_role_heuristics(
            card.type_line,
            card.oracle_text,
            classification,
            catalog,
        )
        score = score_card_for_strategy(
            card=CardScoreInput(
                oracle_id=card.oracle_id,
                name=card.name,
                color_identity=(
                    tuple(card.color_identity)
                    if card.color_identity is not None
                    else None
                ),
                format_legal=_commander_legality(card),
                classifications=scoring_classification,
                available_quantity=quantity,
                already_used_quantity=already_used_quantity,
            ),
            commander_oracle_id=commander.oracle_id,
            commander_color_identity=commander_identity,
            commander_affinities=profile,
            strategy_id=strategy_id,
            catalog=catalog,
            required_roles=required_roles,
            only_collection=True,
            overrides=overrides,
        )
        if not score.eligible or not score.rankable:
            ineligible_count += 1
            continue
        suggestions.append(
            CollectionSuggestion(
                score=score,
                type_line=card.type_line,
                mana_cost=card.mana_cost,
                oracle_text=card.oracle_text,
                image_uri=card.image_uri,
                classification=scoring_classification,
                overrides=overrides,
            )
        )

    ranked = rank_scored_cards(tuple(item.score for item in suggestions))[:limit]
    suggestions_by_id = {item.score.card_oracle_id: item for item in suggestions}
    return CollectionSuggestionBatch(
        commander=commander,
        strategy_name=strategy.name,
        total_candidates=len(candidate_rows),
        unclassified_count=len(unclassified_ids),
        unclassified_oracle_ids=tuple(unclassified_ids),
        ineligible_count=ineligible_count,
        suggestions=tuple(
            suggestions_by_id[item.card_oracle_id] for item in ranked
        ),
    )


def _commander_legality(card: Card) -> bool | None:
    if card.legalities is None:
        return None
    legality = card.legalities.get("commander")
    if legality == "legal":
        return True
    if legality in {"not_legal", "banned"}:
        return False
    return None
