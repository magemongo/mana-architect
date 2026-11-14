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
    creation, = [cap for cap in result.mechanical_capabilities if cap.id == "create_token"]
    assert creation.quantity.value == 2
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


def test_anowon_rewards_actual_rogues_rogue_tokens_and_changelings(context):
    commander = card(
        "anowon",
        "Other Rogues you control get +1/+1.\n"
        "Whenever one or more Rogues you control deal combat damage to a player, "
        "that player mills a card for each 1 damage dealt to them. "
        "If the player mills at least one creature card this way, you draw a card.",
        type_line="Legendary Creature — Vampire Rogue",
    )
    candidates = (
        card("vanilla-rogue", "", type_line="Creature — Human Rogue"),
        card("evasive-rogue", "This creature can't be blocked.", type_line="Creature — Human Rogue"),
        card("rogue-tokens", "Create two 1/1 black Goblin Rogue creature tokens.", type_line="Sorcery"),
        card("changeling", "Changeling\nThis creature can't be blocked.", type_line="Creature — Shapeshifter"),
        card("zombie-tokens", "Create two 1/1 black Zombie creature tokens.", type_line="Sorcery"),
        card("kindred-spell", "Draw a card.", type_line="Kindred Sorcery — Rogue"),
    )
    results = [run(context, commander, candidate, "tokens", "aristocrats") for candidate in candidates]
    plain, evasive, tokens, changeling, zombies, spell = results
    assert evasive.commander_synergy.score > plain.commander_synergy.score > 0
    assert changeling.commander_synergy.score == evasive.commander_synergy.score
    assert zombies.commander_synergy.score == spell.commander_synergy.score == 0
    matches = [item for item in tokens.evidence if item.id in tokens.commander_synergy.evidence_ids]
    assert {item.rule_id for item in matches} == {
        "token_receives_tribal_buff", "token_enables_tribal_combat_payoff",
    }
    assert all(item.status == MatchStatus.CONDITIONAL for item in matches)
    combat = next(item for item in matches if item.target.id == "combat_damage_payoff")
    assert "created_token_deals_combat_damage_to_player" in combat.dependencies
    assert "If the player mills at least one creature card" in combat.target.resolution
    assert not tokens.commander_unknowns


def test_kalitas_style_creation_is_discounted_and_earns_no_validated_bridge(context):
    commander = card("commander", "")
    candidate = card(
        "kalitas",
        "If a nontoken creature an opponent controls would die, instead exile that card "
        "and create a 2/2 black Zombie creature token.",
        type_line="Creature — Vampire Warrior",
    )
    result = run(context, commander, candidate, "tokens", "aristocrats")
    affinity = next(affinity for affinity in result.strategy_affinities if affinity.strategy_id == "tokens")
    assert affinity.score == pytest.approx(0.75 * context.scoring.conditional_discount)
    assert not result.cross_strategy_bridges
    intrinsic = next(item for item in result.evidence if item.rule_id == "tokens_create_token")
    assert intrinsic.status == MatchStatus.CONDITIONAL
    assert "a nontoken creature an opponent controls would die" in intrinsic.dependencies
