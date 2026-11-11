import pytest

pytest.importorskip("streamlit")

from mana_architect.streamlit_app import (
    _deck_preview_markup,
    _balance_category_names,
    _mana_cost_markdown,
    _mana_curve_markup,
)
from mana_architect.deckbuilding import DeckCardDetails


def test_mana_cost_uses_scryfall_pips_for_standard_and_hybrid_symbols() -> None:
    markup = _mana_cost_markdown("{2}{W}{W/U}{W/U/P}{T}")

    assert "![{2}](https://svgs.scryfall.io/card-symbols/2.svg)" in markup
    assert "![{W}](https://svgs.scryfall.io/card-symbols/W.svg)" in markup
    assert "![{W/U}](https://svgs.scryfall.io/card-symbols/WU.svg)" in markup
    assert "![{W/U/P}](https://svgs.scryfall.io/card-symbols/WUP.svg)" in markup
    assert "![{T}](https://svgs.scryfall.io/card-symbols/T.svg)" in markup


def test_mana_cost_keeps_unknown_symbols_as_escaped_text() -> None:
    markup = _mana_cost_markdown("{<unexpected>}")

    assert "&lt;unexpected&gt;" in markup
    assert "svgs.scryfall.io" not in markup


def test_empty_mana_cost_is_omitted() -> None:
    assert _mana_cost_markdown(None) == ""


def test_mana_curve_markup_renders_sorted_histogram_bars() -> None:
    markup = _mana_curve_markup({4: 2, 1: 5, 2.5: 1})

    assert 'aria-label="Histograma da curva de mana"' in markup
    assert markup.index('class="mana-curve-label">1<') < markup.index(
        'class="mana-curve-label">2.5<'
    ) < markup.index('class="mana-curve-label">4<')
    assert 'class="mana-curve-count">5<' in markup
    assert 'class="mana-curve-bar" style="height:120px"' in markup


def test_empty_mana_curve_markup_is_empty() -> None:
    assert _mana_curve_markup({}) == ""


def test_deck_preview_markup_changes_card_on_row_hover() -> None:
    card = DeckCardDetails(
        oracle_id="card-1",
        name="<Card>",
        quantity=1,
        mana_cost=None,
        cmc=1,
        type_line="Creature",
        image_uri="https://example.test/card.jpg",
        oracle_text="First line\nSecond line",
        roles=(),
        strategy_signals=(),
        is_commander=False,
    )

    markup = _deck_preview_markup([card], {"card-1": "deck-card-row-0"}, "card-1")

    assert "body:has(.st-key-deck-card-row-0 p:hover)" in markup
    assert 'data-card-id="card-1"' in markup
    assert 'alt="&lt;Card&gt;"' in markup
    assert "loading=\"lazy\"" in markup
    assert (
        'div[data-testid="stMarkdownContainer"]:has(.deck-preview-panel)'
        "{width:320px!important;max-width:100%!important;height:auto!important}"
    ) in markup
    assert (
        '<div class="deck-preview-panel"><div class="deck-preview-stack">'
        in markup
    )
    assert (
        '<div class="deck-preview-details-stack">'
        '<div class="deck-preview-details" data-card-id="card-1">'
        in markup
    )
    assert "First line\nSecond line" not in markup
    assert ".deck-preview-panel{width:320px;max-width:100%}" in markup
    assert ".deck-preview-details{width:100%}" in markup


def test_category_columns_are_balanced_without_splitting_groups() -> None:
    categories = [
        ("Comandante", 2),
        ("Criaturas", 28),
        ("Artefatos", 15),
        ("Encantamentos", 4),
        ("Mágicas instantâneas", 10),
        ("Feitiços", 10),
        ("Terrenos", 38),
    ]

    columns = _balance_category_names(categories)

    assert len(columns) == 3
    assert [category for column in columns for category in column] == [
        category for category, _ in categories
    ]
    column_totals = [
        sum(weight for category, weight in categories if category in column)
        for column in columns
    ]
    assert max(column_totals) - min(column_totals) <= 15
    category_order = [category for category, _ in categories]
    for column in columns:
        assert column == sorted(column, key=category_order.index)


def test_category_balance_preserves_order_when_there_are_fewer_groups() -> None:
    columns = _balance_category_names(
        [("Comandante", 1), ("Criaturas", 20)],
        column_count=3,
    )

    assert columns == (["Comandante"], ["Criaturas"], [])


def test_category_balance_rejects_zero_columns() -> None:
    with pytest.raises(ValueError, match="column_count must be at least 1"):
        _balance_category_names([], column_count=0)
