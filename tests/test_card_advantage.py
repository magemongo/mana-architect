import pytest

from mana_architect.card_advantage import compile_profile, evaluate_cast
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
