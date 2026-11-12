from mana_architect.catalog import load_strategy_catalog
from mana_architect.oracle_text import OracleParse, parse_oracle_text


def test_doubling_season_emits_replacement_facts_not_unconditional_actions() -> None:
    catalog = load_strategy_catalog()
    parsed = parse_oracle_text(
        "If an effect would create one or more tokens under your control, "
        "it creates twice that many of those tokens instead.\n\n"
        "If an effect would put one or more counters on a permanent you control, "
        "it puts twice that many of those counters on that permanent instead.",
        catalog.oracle_lexicon,
        catalog.oracle_mechanical_patterns,
    )

    mechanic_ids = {item.id for item in parsed.mechanics}
    assert "token.create_replacement" in mechanic_ids
    assert "counter.placement_replacement" in mechanic_ids
    assert "token.create" not in mechanic_ids
    assert "counter.put" not in mechanic_ids
    assert all(dict(item.attributes).get("multiplier") == 2 for item in parsed.mechanics)


def test_bristly_bill_segments_trigger_and_activated_counter_abilities() -> None:
    catalog = load_strategy_catalog()
    parsed = parse_oracle_text(
        "Landfall — Whenever a land you control enters, put a +1/+1 counter "
        "on target creature.\n\n"
        "{3}{G}{G}: Double the number of +1/+1 counters on each creature you control.",
        catalog.oracle_lexicon,
        catalog.oracle_mechanical_patterns,
    )

    assert [ability.kind for ability in parsed.abilities] == ["triggered", "activated"]
    assert parsed.abilities[1].mana_cost is not None
    assert parsed.abilities[1].mana_cost.symbols == ("3", "G", "G")
    assert any(item.id == "ability_word_landfall" for item in parsed.features)
    assert any(item.id == "counter_plus_one_plus_one" for item in parsed.features)
    assert {item.id for item in parsed.mechanics} == {"counter.put", "counter.double"}


def test_parser_matches_legacy_rule_patterns_with_structured_attributes() -> None:
    catalog = load_strategy_catalog()
    parsed = parse_oracle_text(
        "Draw two cards. You gain 3 life. Flame deals 6 damage to each opponent.\n\n"
        "Whenever this creature deals combat damage to a player, draw a card.\n\n"
        "Search your library for a basic land card, put it onto the battlefield, then shuffle.\n\n"
        "Return target creature card from your graveyard to the battlefield.\n\n"
        "Target creature gets +1/+1 until end of turn.",
        catalog.oracle_lexicon,
        catalog.oracle_mechanical_patterns,
    )
    facts_by_id = {}
    for fact in parsed.mechanics:
        facts_by_id.setdefault(fact.id, []).append(fact)

    assert dict(facts_by_id["card.draw"][0].attributes)["quantity"] == 2
    life_gain = next(item for item in facts_by_id["life.gain"])
    assert dict(life_gain.attributes)["subject"] == "you"
    assert "damage.noncombat_opponents" in facts_by_id
    assert "damage.combat" in facts_by_id
    assert "land.search_to_battlefield" in facts_by_id
    assert "creature.return_from_graveyard" in facts_by_id
    assert "creature.plus_one_plus_one_modifier" in facts_by_id


def test_negated_action_is_not_classified_as_positive_mechanic() -> None:
    catalog = load_strategy_catalog()
    parsed = parse_oracle_text(
        "You can't create a token or draw a card.",
        catalog.oracle_lexicon,
        catalog.oracle_mechanical_patterns,
    )

    assert parsed.mechanics == ()


def test_parser_json_round_trip_preserves_spans_and_facts() -> None:
    catalog = load_strategy_catalog()
    parsed = parse_oracle_text(
        "Create a token.", catalog.oracle_lexicon, catalog.oracle_mechanical_patterns
    )

    assert OracleParse.from_json(parsed.to_json()) == parsed
