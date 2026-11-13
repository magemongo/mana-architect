from mana_architect.oracle_text import parse_oracle_text
from mana_architect.synergy_capabilities import CAPABILITY_IDS, extract_mechanical_capabilities


def compile_card(text, **kwargs):
    result = extract_mechanical_capabilities(parse_oracle_text(text), **kwargs)
    for capability in result.capabilities:
        span = capability.evidence
        assert text[span.start:span.end] == span.oracle_span
        assert capability.id in CAPABILITY_IDS
    return result


def test_treasure_is_one_typed_creation_event_and_reminder_text_is_ignored():
    profile = compile_card(
        'Draw a card and create two Treasure tokens. (They have "{T}, Sacrifice this token: Add {G}.")',
        type_line="Sorcery",
    )
    draw, creation, spell = profile.capabilities
    assert draw.id == "draw"
    assert spell.id == "resolve_spell"
    assert creation.id == "create_token"
    assert creation.object.types == ("artifact",)
    assert creation.object.subtypes == ("treasure",)
    assert creation.quantity.value == 2
    assert creation.controller == "you"
    assert creation.destination_zone == "battlefield"
    assert creation.timing == "sorcery_speed"
    assert profile.unknowns == ()


def test_cost_observation_and_effect_are_separate_with_original_offsets():
    text = (
        "Whenever another creature you control dies, you may draw a card.\n"
        "{1}, Sacrifice another creature: Scry 1. Activate only as a sorcery."
    )
    profile = compile_card(text)
    trigger, draw, sacrifice = profile.capabilities
    assert trigger.id == "death_payoff"
    assert trigger.object.reference == "other"
    assert trigger.object.types == ("creature",)
    assert trigger.controller == "you"
    assert trigger.modality == draw.modality == "optional"
    assert sacrifice.id == "sacrifice"
    assert sacrifice.object.reference == "other"
    assert sacrifice.costs == ("{1}, Sacrifice another creature",)
    assert sacrifice.timing == "sorcery_speed"
    assert sacrifice.source_zone == "battlefield"
    assert sacrifice.destination_zone == "graveyard"
    assert profile.unknowns[0].oracle_span.startswith("{1}")


def test_replacement_and_blink_do_not_emit_their_hypothetical_actions():
    profile = compile_card(
        "If you would create one or more tokens, create twice that many of those tokens instead.\n"
        "Exile target creature you control. Return it to the battlefield under its owner's control."
    )
    multiply, blink = profile.capabilities
    assert multiply.id == "multiply_token_creation"
    assert multiply.quantity.value == 2
    assert blink.id == "blink"
    assert blink.object.types == ("creature",)
    assert blink.targeting == "target"
    assert blink.resolution == "its owner's"


def test_unknown_and_negative_constructions_are_not_positive_capabilities():
    profile = compile_card(
        "Players can't sacrifice creatures.\n"
        "Choose one —\n"
        "• Create a 1/1 green creature token.\n"
        "An opponent creates a Treasure token.\n"
        "If you would create a token, draw a card instead.\n"
        "If you would create a token, gain twice that much life instead."
    )
    assert profile.capabilities == ()
    assert len(profile.unknowns) == 6


def test_self_reference_is_linked_without_shifting_unicode_offsets():
    profile = compile_card(
        "Whenever Café Hero enters the battlefield, you gain 2 life.",
        name="Café Hero", type_line="Legendary Creature — Human",
    )
    trigger, gain = profile.capabilities
    assert trigger.object.reference == "self"
    assert gain.quantity.value == 2
    assert gain.evidence.oracle_span == "you gain 2 life"
