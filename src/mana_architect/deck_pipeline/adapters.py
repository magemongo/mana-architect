"""Evaluate a single eligible card through the existing four engines."""

from mana_architect.card_advantage import EVALUATOR_VERSION, compile_profile
from mana_architect.deck_pipeline.contracts import (
    CardEvaluations, DeckBuildingContext, EngineVersions, StrategyPreference, ValidationIssue,
)
from mana_architect.deck_pipeline.eligibility import EligibilityGate
from mana_architect.deck_pipeline.inventory import CardSnapshot
from mana_architect.interaction_evaluation_engine import classify_card_interaction
from mana_architect.interaction_rules_catalog import INTERACTION_RULES_VERSION
from mana_architect.interaction_scoring import INTERACTION_SCORING_VERSION
from mana_architect.models import Card
from mana_architect.oracle_text import OracleParse
from mana_architect.ramp_evaluator import evaluate_ramp
from mana_architect.ramp_rules_catalog import RAMP_RULES_VERSION
from mana_architect.strategy_synergy import (
    ENGINE_VERSION as SYNERGY_ENGINE_VERSION,
    SelectedStrategy,
    SynergyCatalogContext,
    evaluate_synergy,
    normalize_priorities,
    parse_synergy_oracle,
)

ENGINE_VERSIONS = EngineVersions(
    ramp=RAMP_RULES_VERSION,
    card_advantage=EVALUATOR_VERSION,
    interaction=f"{INTERACTION_RULES_VERSION}/{INTERACTION_SCORING_VERSION}",
    strategy_synergy=SYNERGY_ENGINE_VERSION,
)


class EvaluationError(ValueError):
    def __init__(self, violations: tuple[ValidationIssue, ...]):
        self.violations = violations
        super().__init__(" ".join(issue.message for issue in violations))


def _engine_card(card: CardSnapshot) -> Card:
    """Build a transient input from the snapshot without a live ORM dependency."""
    return Card(
        oracle_id=card.card_id,
        name=card.name,
        type_line=card.type_line,
        oracle_text=card.oracle_text,
        mana_cost=card.mana_cost,
        cmc=card.mana_value,
        color_identity=list(card.color_identity) if card.color_identity is not None else None,
        produced_mana=list(card.produced_mana),
        keywords=list(card.keywords),
        legalities={"commander": card.commander_legality},
    )


def parse_card_snapshot(card: CardSnapshot, context: SynergyCatalogContext) -> OracleParse:
    return parse_synergy_oracle(_engine_card(card), context)


class EvaluationAdapters:
    """Bind engine configuration and the validated commander once per run."""

    def __init__(self, gate: EligibilityGate, synergy_context: SynergyCatalogContext):
        if gate.violations:
            raise EvaluationError(gate.violations)
        if gate.context.engine_versions != ENGINE_VERSIONS:
            raise EvaluationError((ValidationIssue(
                "engine_version_mismatch", "The context references different engine versions.",
            ),))
        preferences = tuple(
            SelectedStrategy(item.strategy_id, item.weight)
            for item in gate.context.strategy_preferences
        )
        try:
            priorities = normalize_priorities(preferences, synergy_context)
        except ValueError as error:
            raise EvaluationError((ValidationIssue("invalid_strategy_preferences", str(error)),)) from error

        assert gate.commander is not None
        self._gate = gate
        self._cards = {card.card_id: card for card in gate.snapshot.cards}
        self._commander = _engine_card(gate.commander)
        self._synergy_context = synergy_context
        self._commander_parse = parse_synergy_oracle(self._commander, synergy_context)
        self._preferences = preferences
        self.strategy_preferences = tuple(
            StrategyPreference(strategy_id, weight) for strategy_id, weight in priorities.items()
        )

    @property
    def context(self) -> DeckBuildingContext:
        return self._gate.context

    @property
    def synergy_context(self) -> SynergyCatalogContext:
        return self._synergy_context

    def evaluate(self, card_id: str) -> CardEvaluations:
        eligibility = self._gate.check_card(card_id)
        if eligibility.violations:
            raise EvaluationError(eligibility.violations)
        card = _engine_card(self._cards[card_id])
        parsed = parse_synergy_oracle(card, self._synergy_context)
        synergy = evaluate_synergy(
            self._commander, card, self._preferences, self._synergy_context,
            owned_quantity=eligibility.available_quantity,
            candidate_parse=parsed, commander_parse=self._commander_parse,
        )
        if not synergy.eligible:
            raise EvaluationError(tuple(
                ValidationIssue(reason, f"Strategy Synergy rejected {card.name} ({reason}).", card_id)
                for reason in synergy.rejection_reasons
            ))
        return CardEvaluations(
            ramp=evaluate_ramp(
                card_id, card.type_line, card.mana_cost, parsed,
                colorless_identity=self._commander.color_identity == [],
            ),
            card_advantage=compile_profile(card_id, card.type_line, card.mana_cost, parsed),
            interaction=classify_card_interaction(card, parsed),
            strategy_synergy=synergy,
        )
