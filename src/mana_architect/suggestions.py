from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mana_architect.card_advantage import compile_profile, evaluate_cast
from mana_architect.catalog import DEFAULT_CATALOG_DIR, StrategyCatalog, StrategyDefinition
from mana_architect.classification import (
    ClassificationEvidence,
    ClassificationResult,
    classify_card,
    with_deck_role_heuristics,
)
from mana_architect.classification_repository import (
    card_classification_input,
    load_card_classification_overrides,
    load_current_classifications,
)
from mana_architect.commanders import resolve_commander
from mana_architect.interaction_evaluation_engine import classify_card_interaction
from mana_architect.interaction_profile import InteractionProfile
from mana_architect.models import Card, CollectionPrinting
from mana_architect.oracle_text import parse_oracle_text
from mana_architect.overrides import ManualOverride, resolve_classification_overrides
from mana_architect.ramp_evaluator import RampProfile, evaluate_ramp
from mana_architect.strategy_synergy import (
    SelectedStrategy,
    StrategySynergyProfile,
    SynergyCatalogContext,
    evaluate_synergy,
    mechanical_profile,
    normalize_priorities,
    parse_synergy_oracle,
)
from mana_architect.synergy_catalog import load_synergy_strategies
from mana_architect.typal import TypalMatch, creature_subtypes, match_typal

INTERACTION_SUGGESTION_ROLES = frozenset({"spot_interaction", "mass_interaction"})
STANDALONE_SUGGESTION_ROLES = INTERACTION_SUGGESTION_ROLES | {"card_advantage", "ramp"}
_MAX_INTERACTION_STRATEGY_BONUS = 5.0
_TYPAL_WEIGHT = 0.20


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
    strict_card_advantage: int | None = None
    ramp_profile: RampProfile | None = None
    interaction_profile: InteractionProfile | None = None
    mechanical_synergy: StrategySynergyProfile | None = None
    typal_match: TypalMatch | None = None


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


def collection_creature_types(session: Session) -> tuple[str, ...]:
    type_lines = session.scalars(
        select(Card.type_line).join(CollectionPrinting).where(Card.type_line.is_not(None)).distinct()
    )
    return tuple(sorted({subtype for line in type_lines for subtype in creature_subtypes(line)}))


def suggest_collection_cards_by_identity(
    session: Session,
    color_identity: tuple[str, ...],
    strategy_ids: tuple[str, ...],
    catalog: StrategyCatalog,
    *,
    config_dir: Path = DEFAULT_CATALOG_DIR,
    prioritized_roles: tuple[str, ...] = (),
    limit: int = 50,
    commander_identifier: str | None = None,
    strategy_priorities: Mapping[str, float] | None = None,
    creature_type: str | None = None,
    require_creature_body: bool = False,
) -> IdentityCollectionSuggestionBatch:
    """Rank by strategy or core role, optionally using a commander's actual abilities."""
    role_id = prioritized_roles[0] if len(prioritized_roles) == 1 else None
    card_advantage_mode = role_id == "card_advantage"
    ramp_mode = role_id == "ramp"
    interaction_mode = role_id in INTERACTION_SUGGESTION_ROLES
    valid_colors = {"W", "U", "B", "R", "G"}
    if len(set(color_identity)) != len(color_identity) or not set(color_identity) <= valid_colors:
        raise ValueError("color_identity must contain unique Magic colors.")
    if len(set(strategy_ids)) != len(strategy_ids):
        raise ValueError("Select unique strategies.")
    selected_type = " ".join(creature_type.split()).casefold() if creature_type else None
    if require_creature_body and not selected_type:
        raise ValueError("Choose a creature type to filter for creature bodies.")
    if not commander_identifier and not strategy_ids and not selected_type and role_id not in STANDALONE_SUGGESTION_ROLES:
        raise ValueError("Choose a theme, a creature type, or a core role with its own ranking.")
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
    commander = None
    commander_parse = None
    synergy_context = None
    selected_strategies = ()
    if commander_identifier:
        option = resolve_commander(session, commander_identifier)
        commander = session.get_one(Card, option.oracle_id)
        color_identity = option.color_identity
        mechanical_catalog = load_synergy_strategies(config_dir, active_strategy_ids=frozenset(strategies))
        synergy_context = SynergyCatalogContext(catalog, mechanical_catalog)
        priorities = strategy_priorities or {}
        if priorities.keys() - set(strategy_ids):
            raise ValueError("Set priorities only for selected strategies.")
        selected_strategies = tuple(
            SelectedStrategy(strategy_id, priorities.get(strategy_id, 1.0)) for strategy_id in strategy_ids
        )
        normalize_priorities(selected_strategies, synergy_context)
        commander_parse = parse_synergy_oracle(commander, synergy_context)

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
        if (commander is None or card.oracle_id != commander.oracle_id)
        and card.color_identity is not None
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
            if commander is None and not selected_type:
                continue
        if _commander_legality(card) is not True:
            ineligible += 1
            continue
        typal_match = (
            match_typal(mechanical_profile(card, catalog).capabilities, selected_type) if selected_type else None
        )
        if require_creature_body and typal_match and not typal_match.is_creature_body:
            continue
        if classification is None:
            classification = (
                classify_card(card_classification_input(card), catalog) if role_id else ClassificationResult(())
            )
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
        net_cards = None
        ramp_profile = None
        ramp_line = None
        interaction_profile = None
        interaction_score = None
        if role_id is not None and role_id in STANDALONE_SUGGESTION_ROLES:
            excluded_roles = {
                item.target_id for item in decisions if item.target_type == "role" and not item.included
            }
            if role_id in excluded_roles:
                continue
            parsed = classification.oracle_parse or parse_oracle_text(
                card.oracle_text, catalog.oracle_lexicon, catalog.oracle_mechanical_patterns
            )
            if card_advantage_mode:
                profile = compile_profile(card.oracle_id, card.type_line, card.mana_cost, parsed)
                net_cards = max((evaluate_cast(line).self_delta for line in profile.lines), default=None)
                if net_cards is None and role_id not in found_roles:
                    continue
                if net_cards is not None and net_cards <= 0:
                    continue
            elif ramp_mode:
                ramp_profile = evaluate_ramp(
                    card.oracle_id, card.type_line, card.mana_cost, parsed,
                    colorless_identity=not allowed_identity,
                )
                ramp_line = ramp_profile.best_line
                if ramp_line is None and ("role", role_id) not in manual_inclusions and not any(
                    item.target_type == "role" and item.target_id == role_id
                    for item in classification.evidence
                ):
                    continue
            elif interaction_mode:
                interaction_profile = classify_card_interaction(card, parsed)
                supported_roles = set(interaction_profile.strategy_tags)
                # A catalog phrase such as "exile target" can match blink or a
                # whole graveyard. Use affected scope for interaction roles.
                found_roles.difference_update(INTERACTION_SUGGESTION_ROLES)
                found_roles.update(
                    candidate_role for candidate_role in INTERACTION_SUGGESTION_ROLES & roles
                    if candidate_role not in excluded_roles and (
                        candidate_role in supported_roles
                        or ("role", candidate_role) in manual_inclusions
                    )
                )
                if role_id not in found_roles:
                    continue
                interaction_score = interaction_profile.scores[role_id.split("_", 1)[0]]
            found_roles.add(role_id)

        mechanical_synergy = None
        if commander is not None and synergy_context is not None:
            mechanical_synergy = evaluate_synergy(
                commander, card, selected_strategies, synergy_context,
                owned_quantity=quantity, commander_parse=commander_parse,
            )
            if not mechanical_synergy.eligible:
                ineligible += 1
                continue
            strategy_scores = {
                affinity.strategy_id: 100 * affinity.score for affinity in mechanical_synergy.strategy_affinities
            }
            synergy_score = mechanical_synergy.scores.overall
        else:
            strategy_scores = _classified_strategy_scores(strategies, strategy_ids, evidence, manual_inclusions)
            synergy_score = max(strategy_scores.values(), default=0.0)
        if typal_match:
            typal_score = 100 * typal_match.affinity
            if commander is not None or strategy_ids:
                synergy_score = (1 - _TYPAL_WEIGHT) * synergy_score + _TYPAL_WEIGHT * typal_score
            else:
                synergy_score = typal_score
        matched_strategy_ids = tuple(
            strategy_id
            for strategy_id in strategy_ids
            if strategy_scores[strategy_id] > 0
        )
        matched_priorities = len(found_roles & set(prioritized_roles))
        if prioritized_roles and matched_priorities == 0:
            continue
        if not prioritized_roles and synergy_score <= 0:
            continue
        combined_score = synergy_score
        if card_advantage_mode:
            # One point for classification evidence when no exact line is available.
            combined_score = (net_cards or 1) + synergy_score / 100
        elif ramp_mode:
            combined_score = (ramp_line.score if ramp_line else 1.0) + synergy_score / 100
        elif interaction_mode:
            base_score = interaction_score if interaction_score is not None else 1.0
            combined_score = base_score + _MAX_INTERACTION_STRATEGY_BONUS * synergy_score / 100
        suggestions.append(
            IdentityCollectionSuggestion(
                oracle_id=card.oracle_id,
                name=card.name,
                type_line=card.type_line,
                mana_cost=card.mana_cost,
                oracle_text=card.oracle_text,
                image_uri=card.image_uri,
                available_quantity=quantity,
                score=combined_score if mechanical_synergy and not prioritized_roles else round(combined_score, 3),
                strict_card_advantage=net_cards,
                ramp_profile=ramp_profile,
                interaction_profile=interaction_profile,
                strategy_scores=MappingProxyType(strategy_scores),
                matching_strategy_ids=matched_strategy_ids,
                role_ids=tuple(sorted(found_roles)),
                classification=enriched,
                overrides=overrides,
                mechanical_synergy=mechanical_synergy,
                typal_match=typal_match,
            )
        )
    suggestions.sort(
        key=lambda item: (
            -item.score,
            0.0 if commander is not None else -max(item.strategy_scores.values(), default=0.0),
            item.strict_card_advantage is None,
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


def _classified_strategy_scores(
    strategies: Mapping[str, StrategyDefinition],
    strategy_ids: tuple[str, ...],
    evidence: tuple[ClassificationEvidence, ...],
    manual_inclusions: set[tuple[str, str]],
) -> dict[str, float]:
    scores = {}
    for strategy_id in strategy_ids:
        weights = {item.tag_id: item.weight for item in strategies[strategy_id].tag_affinities}
        signals = {}
        direct_strategy_signal = 0.0
        for item in evidence:
            if item.target_type == "tag" and item.target_id in weights:
                signals[item.target_id] = max(
                    signals.get(item.target_id, 0.0), min(1.0, item.weight * item.confidence),
                )
            elif item.target_type == "strategy" and item.target_id == strategy_id:
                direct_strategy_signal = max(direct_strategy_signal, min(1.0, item.weight * item.confidence))
        for target_type, target_id in manual_inclusions:
            if target_type == "tag" and target_id in weights:
                signals[target_id] = 1.0
            elif target_type == "strategy" and target_id == strategy_id:
                direct_strategy_signal = 1.0
        denominator = sum(weights.values()) + (1.0 if direct_strategy_signal else 0.0)
        numerator = sum(weights[tag_id] * strength for tag_id, strength in signals.items())
        if direct_strategy_signal:
            numerator += direct_strategy_signal
        scores[strategy_id] = round(100 * numerator / denominator, 1) if denominator else 0.0
    return scores


def _commander_legality(card: Card) -> bool | None:
    if card.legalities is None:
        return None
    legality = card.legalities.get("commander")
    if legality == "legal":
        return True
    if legality in {"not_legal", "banned"}:
        return False
    return None
