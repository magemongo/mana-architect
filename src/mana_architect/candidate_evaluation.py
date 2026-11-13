"""Rank cards from the collection and return their independent evaluations."""

from dataclasses import asdict, dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mana_architect.card_advantage import Profile as CardAdvantageProfile
from mana_architect.card_advantage import compile_profile, evaluate_cast
from mana_architect.commanders import resolve_commander
from mana_architect.interaction_evaluation_engine import classify_card_interaction
from mana_architect.interaction_profile import InteractionProfile
from mana_architect.models import Card, CollectionPrinting, SyncMetadata
from mana_architect.ramp_evaluator import RampProfile, evaluate_ramp
from mana_architect.strategy_synergy import (
    SelectedStrategy,
    StrategySynergyProfile,
    SynergyCatalogContext,
    evaluate_synergy,
    load_synergy_context,
    normalize_priorities,
    parse_synergy_oracle,
)


@dataclass(frozen=True)
class RankedCandidate:
    card_id: str
    name: str
    owned_quantity: int
    strategy_synergy: StrategySynergyProfile
    ramp: RampProfile
    card_advantage: CardAdvantageProfile
    interaction: InteractionProfile

    def to_json(self) -> dict:
        return {
            "card_id": self.card_id,
            "name": self.name,
            "owned_quantity": self.owned_quantity,
            "evaluations": {
                "strategy_synergy": self.strategy_synergy.to_json(),
                "ramp": {
                    **asdict(self.ramp),
                    "best_score": self.ramp.best_line.score if self.ramp.best_line else None,
                },
                "card_advantage": {
                    **asdict(self.card_advantage),
                    "best_strict_net_cards": max(
                        (evaluate_cast(line).self_delta for line in self.card_advantage.lines), default=None,
                    ),
                },
                "interaction": self.interaction.to_json(),
            },
        }


@dataclass(frozen=True)
class CandidateRejection:
    card_id: str
    name: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CandidateEvaluationBatch:
    commander_id: str
    commander_name: str
    legality_data_as_of: str | None
    selected_strategies: tuple[SelectedStrategy, ...]
    total_eligible: int
    ranked: tuple[RankedCandidate, ...]
    rejected: tuple[CandidateRejection, ...]

    def to_json(self) -> dict:
        return {
            "commander_id": self.commander_id,
            "commander_name": self.commander_name,
            "legality_data_as_of": self.legality_data_as_of,
            "selected_strategies": [asdict(item) for item in self.selected_strategies],
            "total_eligible": self.total_eligible,
            "ranked": [item.to_json() for item in self.ranked],
            "rejected": [asdict(item) for item in self.rejected],
        }


def rank_collection_candidates(
    session: Session,
    commander_identifier: str,
    selected_strategies: tuple[SelectedStrategy, ...],
    *,
    context: SynergyCatalogContext | None = None,
    limit: int = 20,
) -> CandidateEvaluationBatch:
    """Use the synchronized collection to evaluate candidates without a decklist."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer.")
    context = context or load_synergy_context()
    priorities = normalize_priorities(selected_strategies, context)
    commander_option = resolve_commander(session, commander_identifier)
    commander = session.get_one(Card, commander_option.oracle_id)
    commander_parse = parse_synergy_oracle(commander, context)
    bulk_sync = session.get(SyncMetadata, "bulk_updated_at")
    cards = session.execute(
        select(Card, func.sum(CollectionPrinting.quantity))
        .join(CollectionPrinting, CollectionPrinting.oracle_id == Card.oracle_id)
        .where(Card.oracle_id != commander.oracle_id)
        .group_by(Card.oracle_id)
        .order_by(Card.oracle_id)
    )
    eligible = []
    rejected = []
    for card, quantity in cards:
        profile = evaluate_synergy(
            commander, card, selected_strategies, context, owned_quantity=quantity,
            commander_parse=commander_parse,
        )
        if profile.eligible:
            eligible.append((card, quantity, profile))
        else:
            rejected.append(CandidateRejection(card.oracle_id, card.name, profile.rejection_reasons))
    eligible.sort(key=lambda row: (-row[2].scores.overall, row[0].name.casefold(), row[0].oracle_id))
    ranked = []
    for card, quantity, profile in eligible[:limit]:
        parsed = parse_synergy_oracle(card, context)
        ranked.append(RankedCandidate(
            card.oracle_id, card.name, quantity, profile,
            evaluate_ramp(
                card.oracle_id, card.type_line, card.mana_cost, parsed,
                colorless_identity=not commander.color_identity,
            ),
            compile_profile(card.oracle_id, card.type_line, card.mana_cost, parsed),
            classify_card_interaction(card, parsed),
        ))
    return CandidateEvaluationBatch(
        commander.oracle_id, commander.name, bulk_sync.value if bulk_sync else None,
        tuple(SelectedStrategy(key, priority) for key, priority in priorities.items()),
        len(eligible), tuple(ranked), tuple(rejected),
    )
