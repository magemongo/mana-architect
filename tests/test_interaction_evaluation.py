import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from mana_architect.interaction_evaluation_engine import build_interaction_profile, classify_card_interaction
from mana_architect.interaction_profile import (
    InteractionEvidence,
    InteractionProfile,
    InteractionScore,
    Quantity,
)
from mana_architect.interaction_rules_catalog import INTERACTION_RULES, mask_reminder_text
from mana_architect.interaction_scoring import InteractionScoringConfig
from mana_architect.models import Card
from mana_architect.oracle_text import parse_oracle_text


@pytest.fixture(scope="module")
def profile_schema():
    path = Path(__file__).resolve().parents[1] / "config" / "interaction_profile.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_catalog_has_unique_stable_rule_ids():
    ids = [rule.id for rule in INTERACTION_RULES]
    assert len(ids) == len(set(ids))
    assert {"EXILE_001", "GY_EXILE_001", "SPELL_COUNTER_001", "PREVENT_001"} <= set(ids)


def test_reminder_and_granted_rules_are_masked_without_moving_offsets():
    text = 'Create a token (Exile an artifact.) with “Destroy all creatures.” Then draw a card.'
    masked = mask_reminder_text(text)
    assert "Exile" not in masked and "Destroy" not in masked
    assert masked.index("Then") == text.index("Then")
    assert len(masked) == len(text)


def test_profile_default_json_and_invariants(profile_schema):
    profile = InteractionProfile("basic-land", "oracle-lexical-v3", (), ())
    serialized = json.loads(json.dumps(profile.to_json()))
    profile_schema.validate(serialized)
    assert serialized["card_id"] == "basic-land"
    assert not serialized["interaction_profile"]["is_interaction"]
    assert serialized["interaction_profile"]["strategy_tags"] == []
    assert serialized["interaction_profile"]["scores"] == {"spot": None, "mass": None}
    with pytest.raises(ValueError):
        Quantity("fixed", 2, 1)
    with pytest.raises(ValueError):
        InteractionEvidence("Destroy it.", 0, 1, ("DESTROY_001",), "a1")
    with pytest.raises(ValueError):
        InteractionScore(11)


@pytest.mark.parametrize(("name", "oracle", "tags", "mechanics"), [
    ("Swords to Plowshares", "Exile target creature. Its controller gains life equal to its power.",
     ("spot_interaction",), {"exile_removal"}),
    ("Counterspell", "Counter target spell.", ("spot_interaction",), {"counterspell"}),
    ("Stifle", "Counter target activated or triggered ability. (Mana abilities can't be targeted.)",
     ("spot_interaction",), {"ability_counter"}),
    ("Tamiyo's Safekeeping", "Target permanent you control gains hexproof and indestructible until end of turn. You gain 2 life.",
     ("spot_interaction",), {"protection"}),
    ("Wrath of God", "Destroy all creatures. They can't be regenerated.",
     ("mass_interaction",), {"destroy_removal", "board_wipe"}),
    ("Tormod's Crypt", "{T}, Sacrifice this artifact: Exile target player's graveyard.",
     ("mass_interaction",), {"graveyard_hate"}),
    ("Fog", "Prevent all combat damage that would be dealt this turn.",
     ("mass_interaction",), {"damage_prevention", "fog"}),
    ("Heroic Intervention", "Permanents you control gain hexproof and indestructible until end of turn.",
     ("mass_interaction",), {"protection"}),
    ("Cyclonic Rift", "Return target nonland permanent you don't control to its owner's hand.\nOverload {6}{U}",
     ("spot_interaction", "mass_interaction"), {"bounce", "board_wipe"}),
    ("Duress", "Target opponent reveals their hand. You choose a noncreature, nonland card from it. That player discards that card.",
     ("spot_interaction",), {"discard_disruption"}),
    ("Cremate", "Exile target card from a graveyard.\nDraw a card.", ("spot_interaction",), {"graveyard_hate"}),
    ("Distributed edict", "Each opponent sacrifices a creature.", ("mass_interaction",), {"forced_sacrifice"}),
    ("Lightning Bolt", "Lightning Bolt deals 3 damage to any target.", ("spot_interaction",), {"damage_removal"}),
    ("Blasphemous Act", "Blasphemous Act deals 13 damage to each creature.",
     ("mass_interaction",), {"damage_removal", "board_wipe"}),
    ("Languish", "All creatures get -4/-4 until end of turn.", ("mass_interaction",), {"stat_reduction", "board_wipe"}),
    ("Ground Seal", "Cards in graveyards can't be the targets of spells or abilities.",
     ("mass_interaction",), {"graveyard_hate"}),
    ("Divination", "Draw two cards.", (), set()),
    ("Cultivate", "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.", (), set()),
    ("Ephemerate", "Exile target creature you control, then return it to the battlefield under its owner's control.", (), set()),
    ("Basic land", "{T}: Add {W}.", (), set()),
    ("Token reminder", 'Create a token with “Sacrifice this token: Destroy all creatures.”', (), set()),
    ("Counters", "Put a +1/+1 counter on target creature.", (), set()),
    ("Impulse draw", "Exile the top card of your library. You may play it this turn.", (), set()),
    ("Own bounce", "Return target creature you control to its owner's hand.", (), set()),
    ("Trigger guard", "Whenever you counter a spell, draw a card.", (), set()),
])
def test_regression_matrix(name, oracle, tags, mechanics, profile_schema):
    parsed = parse_oracle_text(oracle)
    profile = build_interaction_profile(name, parsed, type_line="Instant", mana_cost="{1}{U}")
    assert profile.strategy_tags == tags
    assert set(profile.mechanical_tags) == mechanics
    profile_schema.validate(profile.to_json())
    for variant in profile.variants:
        for effect in variant.effects:
            evidence = effect.evidence
            assert evidence.oracle_span == oracle[evidence.start:evidence.end]
            assert evidence.rule_ids


def test_targeted_graveyard_and_bounded_targets_count_differently():
    oracle = "Exile all cards from target player’s graveyard. Counter up to two target spells."
    profile = build_interaction_profile("test", parse_oracle_text(oracle))
    graveyard, spells = profile.variants[0].effects
    assert graveyard.target_count == Quantity("fixed", 1, 1)
    assert graveyard.affected_object_count == Quantity("all", 0)
    assert graveyard.object_types == ("card",)
    assert graveyard.symmetry == "selective"
    assert spells.target_count == Quantity("up_to", 0, 2)
    assert spells.affected_object_count == spells.target_count
    assert spells.scope_type == "multi_target"


def test_modal_combinations_and_shared_evidence():
    options = (
        "\n• Destroy all artifacts.\n• Destroy all enchantments."
        "\n• Destroy all creatures with mana value 3 or less."
        "\n• Destroy all creatures with mana value 4 or greater."
    )
    choose_two = build_interaction_profile("command", parse_oracle_text("Choose two —" + options))
    assert len(choose_two.variants) == 6
    assert all(len(item.mode_ids) == 2 for item in choose_two.variants)
    assert len(choose_two.mode_choices[0].modes) == 4
    assert len({e.effect_id for v in choose_two.variants for e in v.effects}) == 4
    farewell = "Choose one or more —\n• Exile all artifacts.\n• Exile all enchantments.\n• Exile all creatures.\n• Exile all graveyards."
    profile = build_interaction_profile("farewell", parse_oracle_text(farewell))
    assert len(profile.variants) == 15
    assert {"graveyard_hate", "exile_removal"} <= set(profile.mechanical_tags)


def test_activation_cost_condition_restriction_and_duration():
    oracle = "Sacrifice a creature: Destroy target nonblack creature. Activate only as a sorcery.\nCounter target spell unless its controller pays {3}."
    profile = build_interaction_profile("test", parse_oracle_text(oracle), type_line="Artifact")
    destroy, counter = profile.variants
    assert destroy.cost == "Sacrifice a creature"
    assert destroy.additional_costs == ("Sacrifice a creature",)
    assert destroy.timing == "sorcery"
    assert destroy.effects[0].restrictions == ("nonblack",)
    assert counter.effects[0].conditions == ("unless its controller pays {3}",)


def test_variable_scope_stays_unresolved_and_warns_on_unrecognized_clause():
    oracle = "Exile up to X target creatures. Destroy it all somehow."
    profile = build_interaction_profile("test", parse_oracle_text(oracle))
    effect, = profile.variants[0].effects
    assert effect.scope_type == "variable"
    assert effect.target_count == Quantity("variable", 0)
    assert not profile.spot_interaction and not profile.mass_interaction
    assert profile.is_interaction
    assert profile.warnings


def test_collection_card_uses_existing_parse_and_joined_face_metadata():
    oracle = "Counter target spell. // Destroy all creatures."
    card = Card(oracle_id="test", name="Test", type_line="Instant // Sorcery", mana_cost="{U} // {4}",
                cmc=5, oracle_text=oracle, keywords=["test_keyword"])
    parsed = parse_oracle_text(oracle)
    profile = classify_card_interaction(card, parsed)
    assert profile == classify_card_interaction(card, parsed)
    assert profile.oracle_keywords == ("test_keyword",)
    spot, mass = profile.variants
    assert (spot.cost, spot.face_index, spot.timing) == ("{U}", 0, "instant")
    assert (mass.cost, mass.face_index, mass.timing) == ("{4}", 1, "sorcery")
    assert mass.effects[0].evidence.start == oracle.index("Destroy")


def test_overload_changes_selection_and_cost_without_duplicate_effect():
    oracle = "Return target nonland permanent you don’t control to its owner’s hand.\nOverload {6}{U}"
    profile = build_interaction_profile("rift", parse_oracle_text(oracle), type_line="Instant", mana_cost="{1}{U}")
    normal, overload = profile.variants
    assert normal.cost == "{1}{U}"
    assert overload.cost == "{6}{U}"
    assert normal.interaction_types == ("spot",)
    assert overload.interaction_types == ("mass",)
    assert normal.effects[0].effect_id == overload.effects[0].effect_id
    assert normal.effects[0].target_count.max == 1
    assert overload.effects[0].target_count.max == 0


def _instant(oracle, cost="{U}", **kwargs):
    return build_interaction_profile("test", parse_oracle_text(oracle), type_line="Instant", mana_cost=cost, **kwargs)


def test_independent_scores_and_best_available_variant():
    oracle = "Return target nonland permanent you don't control to its owner's hand.\nOverload {6}{U}"
    profile = _instant(oracle, "{1}{U}")
    normal, overloaded = profile.variants
    assert normal.spot_score.total == pytest.approx(8.0)
    assert normal.mass_score.total is None
    assert overloaded.spot_score.total is None
    assert overloaded.mass_score.total is not None
    assert dict(normal.spot_score.components)["efficiency"] == 7
    assert dict(overloaded.mass_score.components)["efficiency"] == 3
    assert profile.scores == {"spot": normal.spot_score.total, "mass": overloaded.mass_score.total}


def test_unresolved_or_disabled_scoring_preserves_classification():
    oracle = "Counter target spell unless its controller pays {X}."
    unknown = _instant(oracle, "{X}{U}", mana_value=1)
    assert unknown.spot_interaction
    assert unknown.scores == {"spot": None, "mass": None}
    assert dict(unknown.variants[0].spot_score.components)["efficiency"] is None
    assert dict(unknown.variants[0].spot_score.components)["reliability"] < 10
    assert _instant(oracle, None, mana_value=2).scores["spot"] is not None
    disabled = _instant(oracle, scoring=None)
    assert disabled.spot_interaction and disabled.scoring_version is None
    assert disabled.variants[0].spot_score.components == ()


def test_scoring_weights_are_validated_and_independently_configurable():
    defaults = InteractionScoringConfig()
    weights = tuple((name, float(name == "speed")) for name, _ in defaults.spot_weights)
    config = InteractionScoringConfig(spot_weights=weights, version="speed-only")
    profile = _instant("Counter target spell.", scoring=config)
    assert profile.scores["spot"] == 10
    assert profile.scoring_version == "speed-only"
    with pytest.raises(ValueError):
        InteractionScoringConfig(spot_weights=(("speed", 1),))


def test_kicker_preserves_mutually_exclusive_effects_and_additional_payment():
    oracle = (
        "Kicker {4}\nExile target creature if this spell wasn't kicked.\n"
        "If this spell was kicked, exile all creatures instead."
    )
    profile = _instant(oracle, "{1}{W}")
    normal, kicked = profile.variants
    assert normal.interaction_types == ("spot",)
    assert kicked.interaction_types == ("mass",)
    assert kicked.additional_costs == ("{4}",)
    assert kicked.effects[0].conditions == ("if this spell was kicked",)
    assert dict(kicked.mass_score.components)["efficiency"] == 4


def test_modal_scores_do_not_sum_overlapping_effects():
    oracle = "Choose one or more —\n• Destroy all creatures.\n• Destroy all artifacts."
    profile = _instant(oracle, "{3}{W}")
    first, second, both = profile.variants
    assert len(both.effects) == 2
    assert both.mass_score.total == max(first.mass_score.total, second.mass_score.total)


def test_restriction_and_durations_remain_auditable():
    profile = _instant("Counter target creature spell unless its controller pays {3}.")
    effect, = profile.variants[0].effects
    assert effect.object_types == ("spell",)
    assert effect.restrictions == ("creature spell",)
    assert effect.conditions == ("unless its controller pays {3}",)
    wrath = _instant("Destroy all creatures. They can't be regenerated.")
    wipe, = wrath.variants[0].effects
    assert wipe.symmetry == "symmetric" and wipe.wipe_potential == "direct"
    assert wipe.exceptions == ("they can't be regenerated",)
    assert wipe.evidence.oracle_span.endswith("They can't be regenerated.")
    shrink = _instant("All creatures get -X/-X until end of turn.", "{X}{B}")
    wipe, = shrink.variants[0].effects
    assert wipe.wipe_potential == "potential" and wipe.duration == "until end of turn"
    assert shrink.mass_interaction and shrink.scores["mass"] is None


def test_single_and_distributed_player_actions_keep_distinct_target_counts():
    targeted = _instant("Target opponent sacrifices a creature.").variants[0].effects[0]
    distributed = _instant("Each opponent sacrifices a creature.").variants[0].effects[0]
    assert targeted.target_count == Quantity("fixed", 1, 1)
    assert targeted.affected_object_count == Quantity("fixed", 1, 1)
    assert targeted.choices == distributed.choices == ("affected_player_chooses",)
    assert targeted.interaction_type == "spot" and distributed.interaction_type == "mass"
    assert distributed.target_count == Quantity("fixed", 0, 0)


def test_one_counter_can_have_two_mechanisms_without_gaining_a_target():
    oracle = "Counter target spell or activated ability."
    profile = _instant(oracle)
    effect, = profile.variants[0].effects
    assert set(effect.mechanical_tags) == {"counterspell", "ability_counter"}
    assert len(effect.evidence.rule_ids) == 2
    assert effect.target_count == Quantity("fixed", 1, 1)
    assert effect.scope_type == "single"


def test_one_clause_can_have_several_explicit_targets():
    oracle = "Destroy target artifact, target enchantment, and target creature."
    effect, = _instant(oracle).variants[0].effects
    assert effect.scope_type == "multi_target"
    assert effect.target_count == Quantity("fixed", 3, 3)


def test_replacement_restriction_and_one_time_graveyard_hate_are_distinct():
    replacement = _instant("If a card would be put into a graveyard from anywhere, exile it instead.")
    effect, = replacement.variants[0].effects
    assert effect.roles == ("suppression",)
    assert effect.affected_zone == "graveyard" and effect.duration == "while_source_remains"
    assert replacement.mass_interaction
    purge = _instant("Exile all cards from all graveyards.")
    effect, = purge.variants[0].effects
    assert effect.roles == ("disruption",) and effect.duration == "indefinite"


def test_spell_paragraphs_share_a_variant_and_independent_activations_do_not():
    spell = _instant("Counter target spell.\n\nDestroy all creatures.")
    variant, = spell.variants
    assert variant.interaction_types == ("spot", "mass")
    assert variant.spot_score.total is not None and variant.mass_score.total is not None
    assert [effect.evidence.ability_id for effect in variant.effects] == ["a1", "a2"]
    parsed = parse_oracle_text("{U}: Counter target spell.\n{W}: Destroy all creatures.")
    activations = build_interaction_profile("test", parsed, type_line="Artifact")
    assert [variant.interaction_types for variant in activations.variants] == [("spot",), ("mass",)]


def test_attachment_removal_is_scoped_to_its_anchor():
    profile = _instant("Destroy all Auras and Equipment attached to target creature.")
    effect, = profile.best_variant("spot").effects
    assert effect.object_types == ("artifact", "enchantment")
    assert effect.target_count == Quantity("fixed", 1, 1)
    assert effect.affected_object_count.kind == "all"
    assert not profile.mass_interaction
    assert "board_wipe" not in effect.mechanical_tags


def test_narrow_casting_tax_does_not_turn_spell_delay_into_mass_interaction():
    profile = build_interaction_profile("aven", parse_oracle_text(
        "Flash\nWhen this creature enters, exile target spell. It becomes plotted.\n"
        "Spells your opponents cast from graveyards or from exile cost {2} more to cast."
    ), type_line="Creature", mana_cost="{1}{W}{W}")
    delay, tax = (effect for variant in profile.variants for effect in variant.effects)
    assert delay.action == "delay_spell"
    assert tax.action == "tax"
    assert profile.strategy_tags == ("spot_interaction",)
    assert _instant("Noncreature spells cost {1} more to cast.").mass_interaction


def test_linked_damage_prevention_stays_spot_and_global_fog_stays_mass():
    single = _instant("Put a +1/+1 counter on target creature. "
                      "Prevent all combat damage that would be dealt to it this turn.")
    assert single.strategy_tags == ("spot_interaction",)
    assert "fog" not in single.mechanical_tags
    assert _instant("Prevent all combat damage that would be dealt this turn.").mass_interaction


def test_ping_and_conditional_destruction_rank_below_reliable_removal():
    ping = build_interaction_profile("triskelion", parse_oracle_text(
        "Remove a +1/+1 counter from this creature: It deals 1 damage to any target."
    ), type_line="Artifact Creature", mana_cost="{6}", mana_value=6)
    conditional = _instant("Target creature gets -1/-1 until end of turn. "
                           "Then if it's a Cyclops, destroy it.", "{B}")
    direct = _instant("Destroy target creature or planeswalker.", "{1}{B}")
    assert ping.spot_interaction and conditional.spot_interaction
    assert conditional.best_variant("spot").effects[-1].conditions == ("if it's a cyclops",)
    assert ping.scores["spot"] < conditional.scores["spot"] < direct.scores["spot"]
