from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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
from mana_architect.overrides import ManualOverride
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
