import pytest

from mana_architect.oracle_text import parse_oracle_text
from mana_architect.synergy_capabilities import extract_mechanical_capabilities
from mana_architect.synergy_interactions import (
    INTERACTION_RULES,
    MatchStatus,
    validate_interaction,
)


def check(rule_id, source, target, *, source_type="", target_type=""):
    rule = next(rule for rule in INTERACTION_RULES if rule.id == rule_id)
    source_caps = extract_mechanical_capabilities(parse_oracle_text(source), type_line=source_type)
    target_caps = extract_mechanical_capabilities(parse_oracle_text(target), type_line=target_type)
    matches = [
        validate_interaction(rule, left, right, source_type=source_type, target_type=target_type)
        for left in source_caps.capabilities for right in target_caps.capabilities
    ]
    return next((match for match in matches if match is not None), None)


@pytest.mark.parametrize("rule,source,target", [
    ("token_etb_triggers_payoff", "Create a 1/1 creature token.",
     "Whenever a creature token you control enters, draw a card."),
    ("artifact_etb_triggers_payoff", "Create a Treasure token.",
     "Whenever an artifact you control enters, draw a card."),
    ("token_multiplier_amplifies_creation",
     "If you would create one or more tokens, create twice that many of those tokens instead.",
     "Create a Treasure token."),
    ("counter_multiplier_amplifies_placement",
     "If one or more +1/+1 counters would be put on a creature you control, twice that many +1/+1 counters are put on it instead.",
     "Put two +1/+1 counters on this creature."),
    ("lifegain_triggers_payoff", "You gain 3 life.", "Whenever you gain life, draw a card."),
])
def test_rule_validates_compatible_event(rule, source, target):
    result = check(rule, source, target, target_type="Creature — Human")
    assert result.status == MatchStatus.VALIDATED
    assert result.dependencies == ()


@pytest.mark.parametrize("rule,source,target,status", [
    ("token_etb_triggers_payoff", "Create a Treasure token.",
     "Whenever a creature you control enters, draw a card.", MatchStatus.INVALID),
    ("sacrifice_triggers_death_payoff", "Sacrifice an artifact.",
     "Whenever a creature dies, draw a card.", MatchStatus.INVALID),
    ("sacrifice_triggers_death_payoff", "Sacrifice a creature.",
     "Whenever a creature an opponent controls dies, draw a card.", MatchStatus.INVALID),
    ("lifegain_triggers_payoff", "Each opponent gains 3 life.",
     "Whenever you gain life, draw a card.", MatchStatus.INVALID),
    ("sacrifice_triggers_death_payoff", "Sacrifice a creature.",
     "Whenever you discard a card, draw a card.", None),
    ("sacrifice_triggers_death_payoff", "Exile target creature.",
     "Whenever a creature dies, draw a card.", None),
    ("casting_triggers_payoff", "Copy target instant or sorcery spell.",
     "Whenever you cast an instant or sorcery spell, draw a card.", None),
    ("token_multiplier_amplifies_creation",
     "If you would create one or more tokens, create twice that many of those tokens instead.",
     "Put a token onto the battlefield.", None),
    ("blink_retriggers_etb", "Exile target creature token. Return it to the battlefield under your control.",
     "Whenever a creature enters, draw a card.", MatchStatus.INVALID),
    ("counter_multiplier_amplifies_placement",
     "If one or more +1/+1 counters would be put on a creature you control, twice that many +1/+1 counters are put on it instead.",
     "Put two -1/-1 counters on this creature.", MatchStatus.INVALID),
])
def test_rule_rejects_incompatible_events(rule, source, target, status):
    result = check(rule, source, target, target_type="Creature")
    assert (result.status if result else None) == status


@pytest.mark.parametrize("rule,source,target,dependency", [
    ("sacrifice_triggers_death_payoff", "Sacrifice a creature: Add {B}.",
     "Whenever another creature you control dies, you may draw a card.", "choose_optional_effect"),
    ("treasure_feeds_artifact_sacrifice", "Create a Treasure token.",
     "Whenever you sacrifice a permanent, draw a card.", "activate_treasure_sacrifice_ability"),
    ("blink_retriggers_etb", "Exile target creature you control. Return it to the battlefield under its owner's control.",
     "When this creature enters, draw a card.", "legal_target_at_resolution"),
    ("recursion_reuses_etb", "Return target creature card from your graveyard to the battlefield.",
     "When this creature enters, draw a card.", "eligible_card_in_graveyard"),
    ("extra_land_play_enables_landfall", "You may play an additional land on each of your turns.",
     "Landfall — Whenever a land you control enters, draw a card.", "land_available_to_play"),
    ("spell_copy_amplifies_spell_effect", "Copy target instant or sorcery spell.",
     "Draw a card.", "legal_copy_target"),
    ("equipment_enables_voltron", "Equip {2}",
     "Whenever this creature attacks alone, draw a card.", "sorcery_timing"),
])
def test_rule_records_remaining_dependencies(rule, source, target, dependency):
    target_type = "Instant" if rule == "spell_copy_amplifies_spell_effect" else "Creature"
    result = check(rule, source, target, target_type=target_type)
    assert result.status == MatchStatus.CONDITIONAL
    assert dependency in result.dependencies


def test_unmodeled_object_restriction_is_explicit():
    result = check(
        "token_etb_triggers_payoff", "Create a 1/1 creature token.",
        "Whenever a Goblin creature you control enters, draw a card.",
    )
    assert result.status == MatchStatus.UNKNOWN
    assert result.dependencies == ("goblin",)
