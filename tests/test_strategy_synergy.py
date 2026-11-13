from dataclasses import replace
import json

import pytest

from mana_architect.models import Card
from mana_architect.strategy_synergy import SelectedStrategy, evaluate_synergy, load_synergy_context
from mana_architect.synergy_interactions import MatchStatus


def card(oracle_id, text, *, type_line="Creature", colors=("B",), legality="legal"):
    return Card(
        oracle_id=oracle_id, name=oracle_id, type_line=type_line, oracle_text=text,
        color_identity=list(colors) if colors is not None else None,
        legalities={"commander": legality} if legality else None,
    )


@pytest.fixture(scope="module")
def context():
    return load_synergy_context()


def run(context, commander, candidate, *strategies, quantity=1):
    return evaluate_synergy(
        commander, candidate, tuple(SelectedStrategy(s) for s in strategies),
        context, owned_quantity=quantity,
    )


def test_treasure_is_a_bridge_and_only_conditionally_triggers_sacrifice(context):
    commander = card("korvold", "Whenever you sacrifice a permanent, draw a card.")
    candidate = card("treasure-maker", "Create two Treasure tokens.")
    result = run(context, commander, candidate, "tokens", "artifacts", "aristocrats")
    assert result.eligible
    assert len(result.mechanical_capabilities) == 1
    assert result.mechanical_capabilities[0].quantity.value == 2
    direct = [item for item in result.evidence if item.id in result.commander_synergy.evidence_ids]
    assert [(item.rule_id, item.status) for item in direct] == [
        ("treasure_feeds_artifact_sacrifice", MatchStatus.CONDITIONAL),
    ]
    assert direct[0].dependencies == ("activate_treasure_sacrifice_ability",)
    assert result.scores.direct_commander_synergy == context.scoring.conditional_discount
    assert len(result.cross_strategy_bridges) == 1
    assert result.cross_strategy_bridges[0].strategies == ("aristocrats", "artifacts", "tokens")
    assert sum(item.priority for item in result.strategy_affinities) == 1


def test_commander_can_produce_the_event_and_missing_payoff_is_potential(context):
    commander = card("token-commander", "Create a 1/1 creature token.")
    candidate = card("payoff", "Whenever a token you control enters, draw a card.")
    result = run(context, commander, candidate, "tokens")
    direct = next(item for item in result.evidence if item.id in result.commander_synergy.evidence_ids)
    assert direct.source_card_id == commander.oracle_id
    assert direct.target_card_id == candidate.oracle_id
    assert direct.status == MatchStatus.VALIDATED
    result = run(context, card("quiet", ""), commander, "tokens")
    assert result.commander_synergy.score == 0
    missing = next(item for item in result.evidence if item.rule_id == "token_etb_triggers_payoff")
    assert missing.status == MatchStatus.POTENTIAL
    assert missing.missing_capabilities == ("enter_payoff",)


def test_selected_strategies_keep_specialists_and_ignore_unrelated_removal(context):
    commander = card("quiet", "")
    specialist = card("token-maker", "Create a 1/1 creature token.")
    single = run(context, commander, specialist, "tokens")
    multi = run(context, commander, specialist, "tokens", "spellslinger", "voltron")
    assert multi.scores.weighted_strategy_affinity > 0.65 * single.scores.weighted_strategy_affinity
    assert {item.strategy_id: item.score for item in multi.strategy_affinities} == {
        "tokens": 0.75, "spellslinger": 0, "voltron": 0,
    }
    removal = run(context, commander, card("kill-spell", "Destroy target creature."), "voltron")
    assert removal.scores.overall == 0


def test_output_is_stable_with_rule_and_strategy_order_changes(context):
    commander = card("commander", "Whenever an artifact you control enters, draw a card.")
    candidate = card("candidate", "Create a Treasure token. You gain 3 life.")
    first = run(context, commander, candidate, "tokens", "lifegain", "artifacts")
    second = run(
        replace(context, rules=tuple(reversed(context.rules))), commander, candidate,
        "artifacts", "lifegain", "tokens",
    )
    assert json.dumps(first.to_json()) == json.dumps(second.to_json())
    assert json.loads(json.dumps(first.to_json()))["catalog_versions"]["capabilities"]


def test_eligibility_precedes_scoring_and_unknown_remains_visible(context):
    commander = card("commander", "")
    candidate = card("banned", "Create a creature token.", colors=("W",), legality="banned")
    rejected = run(context, commander, candidate, "tokens", quantity=0)
    assert rejected.rejection_reasons == (
        "candidate_not_owned", "candidate_banned", "incompatible_color_identity",
    )
    assert rejected.evidence == ()
    unknown = run(context, commander, card("unknown", "Phase out target creature."), "tokens")
    assert unknown.unknowns[0].oracle_span == "Phase out target creature."
    assert unknown.scores.overall == unknown.scores.coverage_confidence == 0


def test_priority_validation_and_normalization(context):
    commander, candidate = card("commander", ""), card("candidate", "")
    strategies = (SelectedStrategy("tokens", 2), SelectedStrategy("artifacts", 6))
    result = evaluate_synergy(commander, candidate, strategies, context, owned_quantity=1)
    assert [affinity.priority for affinity in result.strategy_affinities] == [0.75, 0.25]
    with pytest.raises(ValueError, match="finite and nonnegative"):
        evaluate_synergy(
            commander, candidate, (SelectedStrategy("tokens", float("nan")),),
            context, owned_quantity=1,
        )
