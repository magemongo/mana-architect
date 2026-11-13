import pytest

from mana_architect.mana_resource_extractor import ResourceQuantity, extract_mana_resources
from mana_architect.oracle_text import parse_oracle_text


def extract(text):
    return extract_mana_resources(parse_oracle_text(text))


def test_extracts_mana_symbols_costs_and_exact_evidence_across_lines():
    text = "Flying\n{2/W}{T}: Add {C}{C}. Spend this mana only to cast creature spells."
    event, = extract(text)

    assert event.action == "add_mana"
    assert event.activation_symbols == ("2/W", "T")
    assert event.activation_cost == "{2/W}{T}"
    assert event.mana_symbols == ("C", "C")
    assert event.quantity == ResourceQuantity("fixed", 2)
    assert event.restrictions == ("Spend this mana only to cast creature spells.",)
    assert event.evidence.start == len("Flying\n")
    assert event.evidence.ability_id == "a1"
    assert text[event.evidence.start:event.evidence.end] == event.evidence.oracle_span


@pytest.mark.parametrize(
    ("text", "count", "land_filter", "tapped"),
    [
        ("Search your library for a Forest card, put it onto the battlefield, then shuffle.", 1, "Forest card", False),
        ("Search your library for a Forest or Plains card, put it onto the battlefield, then shuffle.", 1, "Forest or Plains card", False),
        ("Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.", 1, "basic land cards", True),
        ("Search your library for two basic land cards, put them onto the battlefield tapped, then shuffle.", 2, "basic land cards", True),
    ],
)
def test_extracts_only_the_lands_put_onto_the_battlefield(text, count, land_filter, tapped):
    event, = extract(text)
    assert event.action == "put_land"
    assert event.source_zone == "library"
    assert event.object == land_filter
    assert event.quantity.value == count
    assert event.enters_tapped_by_effect is tapped


def test_distinguishes_extra_land_permission_from_a_land_put_from_hand():
    permission, land = extract(
        "You may play an additional land on each of your turns.\n"
        "{T}: You may put a land card from your hand onto the battlefield."
    )
    assert permission.action == "permit_land_play"
    assert permission.requirements == ("land_available_to_play",)
    assert permission.duration == "on each of your turns"
    assert land.action == "put_land"
    assert land.source_zone == "hand"
    assert land.activation_cost == "{T}"
    assert land.optional


def test_treasure_reminder_is_not_a_second_mana_event():
    event, = extract(
        "Landfall — Whenever a land you control enters, you may create a Treasure token. "
        '(It’s an artifact with "{T}, Sacrifice this token: Add one mana of any color.")'
    )
    assert event.action == "create_token"
    assert event.object == "treasure"
    assert event.quantity == ResourceQuantity("fixed", 1)
    assert event.mana_per_token == 1
    assert event.color_options == ("any",)
    assert event.timing == "triggered"
    assert event.requirements == ("Landfall — Whenever a land you control enters",)
    assert event.optional


def test_powerstone_retains_its_spending_restriction():
    event, = extract("Create a tapped Powerstone token.")
    assert event.object == "powerstone"
    assert event.color_options == ("C",)
    assert event.enters_tapped_by_effect
    assert event.restrictions == ("Cannot be spent to cast a nonartifact spell.",)


@pytest.mark.parametrize(
    "text",
    [
        "Add X mana in any combination of colors.",
        "{T}: Add {G} for each Elf on the battlefield.",
        "{T}: Add {G} or {C}{C}.",
    ],
)
def test_variable_production_does_not_claim_a_fixed_mana_quantity(text):
    event, = extract(text)
    assert event.quantity.kind == "variable"
    assert event.quantity.value is None
    assert event.quantity.expression


def test_alternative_mana_colors_do_not_increase_production():
    event, = extract("{T}: Add {G} or {U}.")
    assert event.quantity == ResourceQuantity("fixed", 1)
    assert event.color_options == ("G", "U")
    assert event.mana_symbols == ()


@pytest.mark.parametrize(
    "text",
    [
        "Search your library for a basic land card, reveal it, and put it into your hand.",
        "Create two Clue tokens.",
        "You can't add {G}.",
        "Target player may add {G}.",
        'Enchanted creature has "{T}: Add {G}."',
    ],
)
def test_ignores_unrelated_negated_and_out_of_scope_effects(text):
    assert extract(text) == ()
