import pytest

from mana_architect.card_advantage import (
    BlinkTarget, CreatureInPlay, EquippedDeath, OpponentCast, compile_engine, compile_profile,
    evaluate_cast, evaluate_engine, evaluate_ephemerate, evaluate_wrath_of_god,
)
from mana_architect.catalog import load_strategy_catalog
from mana_architect.oracle_text import parse_oracle_text


@pytest.fixture(scope="module")
def parse():
    catalog = load_strategy_catalog()

    def parse_with_catalog(text):
        return parse_oracle_text(text, catalog.oracle_lexicon, catalog.oracle_mechanical_patterns)

    return parse_with_catalog


@pytest.mark.parametrize(
    ("name", "type_line", "mana", "text", "deltas"),
    [
        ("Divination", "Sorcery", "{2}{U}", "Draw two cards.", [1]),
        ("Opt", "Instant", "{U}", "Scry 1, then draw a card.", [0]),
        (
            "Mulldrifter", "Creature — Elemental", "{4}{U}",
            "Flying\nWhen this creature enters, draw two cards.\n"
            "Evoke {2}{U} (You may cast this spell for its evoke cost.)",
            [2, 1],
        ),
    ],
)
def test_fixed_draw_lines(name, type_line, mana, text, deltas, parse):
    parsed = parse(text)
    profile = compile_profile(name, type_line, mana, parsed)
    assert profile.unsupported_reason is None
    assert [evaluate_cast(line).self_delta for line in profile.lines] == deltas
    for line in profile.lines:
        assert evaluate_cast(line, resolves=False).self_delta == -1
        start, end = line.draw.source_span
        assert text[start:end].lower().startswith("draw ")
    if name == "Mulldrifter":
        normal, evoke = profile.lines
        assert normal.retained and normal.mana_cost == "{4}{U}"
        assert evoke.mode == "evoke" and evoke.mana_cost == "{2}{U}"
        assert evaluate_cast(evoke).ledger[2].from_zone == "battlefield"
    if name == "Opt":
        assert profile.lines[0].selects


@pytest.mark.parametrize("text", [
    "You may draw two cards.",
    "Target opponent may draw two cards.",
    "Draw a card for each creature you control.",
    "Draw X cards.",
    "As an additional cost to cast this spell, discard a card.\nDraw two cards.",
    "Draw two cards. Then discard a card.",
])
def test_unmodeled_draw_has_no_exact_line(text, parse):
    profile = compile_profile("fixture", "Instant", "{U}", parse(text))
    assert not profile.lines
    assert profile.unsupported_reason


def test_rhystic_study_branches(parse):
    engine = compile_engine(parse(
        "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}."
    ))
    assert engine is not None and engine.payment == 1
    assert evaluate_engine(engine, ()).self_delta == 0
    events = (
        OpponentCast(paid=True),
        OpponentCast(paid=False, choose_draw=False),
        OpponentCast(paid=False, trigger_resolves=False),
        OpponentCast(paid=False),
    )
    assert evaluate_engine(engine, events).self_delta == 1


def test_skullclamp_counts_fodder_and_replacement(parse):
    engine = compile_engine(parse(
        "Equipped creature gets +1/-1.\nWhenever equipped creature dies, draw two cards.\nEquip {1}"
    ))
    assert engine is not None
    assert evaluate_engine(engine, ()).self_delta == 0
    token = evaluate_engine(engine, (EquippedDeath("token", token=True),))
    assert (token.self_delta, token.token_delta) == (2, -1)
    assert evaluate_engine(engine, (EquippedDeath("creature"),)).self_delta == 1
    assert evaluate_engine(engine, (EquippedDeath("creature", reaches_graveyard=False),)).self_delta == -1
    with pytest.raises(ValueError, match="only once"):
        evaluate_engine(engine, (EquippedDeath("same"), EquippedDeath("same")))


def test_wrath_of_god_depends_on_board_and_controller():
    board = (
        CreatureInPlay("own", "caster"),
        CreatureInPlay("a", "opponent"),
        CreatureInPlay("b", "opponent"),
        CreatureInPlay("c", "opponent"),
        CreatureInPlay("survives", "other", indestructible=True),
        CreatureInPlay("token", "caster", token=True),
    )
    result = evaluate_wrath_of_god(board, caster_id="caster")
    assert result.self_delta == -2
    assert result.opponent_deltas == {"opponent": -3}
    assert result.token_delta == -1
    reversed_result = evaluate_wrath_of_god(board, caster_id="opponent")
    assert reversed_result.self_delta == -4
    assert reversed_result.opponent_deltas == {"caster": -1}
    countered = evaluate_wrath_of_god(board, caster_id="caster", resolves=False)
    assert countered.self_delta == -1 and countered.opponent_deltas == {}


def test_ephemerate_reuses_only_eligible_enters_draw(parse):
    profile = compile_profile(
        "mulldrifter", "Creature — Elemental", "{4}{U}",
        parse("When this creature enters, draw two cards."),
    )
    target = BlinkTarget("mulldrifter", profile.lines[0].draw)
    assert evaluate_ephemerate(target).self_delta == 1
    both = evaluate_ephemerate(target, target)
    assert both.self_delta == 3
    assert len({entry.event_id for entry in both.ledger}) == len(both.ledger)
    assert evaluate_ephemerate(None).self_delta == -1
    plain = BlinkTarget("plain")
    assert evaluate_ephemerate(plain, plain).self_delta == -1
    token = BlinkTarget("token", target.enters_draw, token=True)
    result = evaluate_ephemerate(token)
    assert (result.self_delta, result.token_delta) == (-1, -1)
    with pytest.raises(ValueError, match="token cannot return"):
        evaluate_ephemerate(token, token)
    with pytest.raises(ValueError, match="successful cast from hand"):
        evaluate_ephemerate(None, target)
    with pytest.raises(ValueError, match="successful cast from hand"):
        evaluate_ephemerate(target, target, cast_from_hand=False)
    borrowed = BlinkTarget("borrowed", target.enters_draw, owned_by_caster=False)
    result = evaluate_ephemerate(borrowed)
    assert result.self_delta == -2 and result.opponent_deltas == {"owner": 1}
