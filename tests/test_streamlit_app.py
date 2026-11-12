from pathlib import Path

import pytest

pytest.importorskip("streamlit")
import streamlit as st
from streamlit.testing.v1 import AppTest

from mana_architect.catalog import DEFAULT_CATALOG_DIR, load_strategy_catalog
from mana_architect.classification_repository import (
    reclassify_cards,
    save_commander_affinity_profile,
)
from mana_architect.commander_affinity import (
    CommanderAffinityProfile,
    CommanderStrategyAffinity,
    CommanderTagAffinity,
)
from mana_architect.database import create_database_engine, initialize_database
from mana_architect.streamlit_app import (
    _deck_preview_markup,
    _balance_category_names,
    _color_identity_options,
    get_engine,
    _mana_cost_markdown,
    _mana_curve_markup,
    _next_suggestion_count,
    _suggestion_preview_markup,
)
from mana_architect.deckbuilding import DeckCardDetails
from mana_architect.models import (
    Card,
    CollectionPrinting,
)
from sqlalchemy.orm import Session


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


def test_suggestions_offer_all_32_color_identities_including_colorless() -> None:
    identities = _color_identity_options()

    assert len(identities) == 32
    assert identities[0] == ()
    assert identities[-1] == ("W", "U", "B", "R", "G")


def test_suggestions_scroll_loading_increments_and_caps_at_50() -> None:
    assert _next_suggestion_count(20) == 30
    assert _next_suggestion_count(45) == 50
    assert _next_suggestion_count(50) == 50


def test_suggestion_preview_markup_switches_image_on_card_name_hover() -> None:
    from types import SimpleNamespace

    card = SimpleNamespace(
        oracle_id="card-1",
        name="Preview Card",
        image_uri="https://example.test/card.jpg",
    )
    markup = _suggestion_preview_markup(
        (card,),
        {"card-1": "suggestion-row-0"},
        "card-1",
    )

    assert "body:has(.st-key-suggestion-row-0 p:hover)" in markup
    assert "https://example.test/card.jpg" in markup


def test_mana_curve_markup_renders_sorted_histogram_bars() -> None:
    markup = _mana_curve_markup({4: 2, 1: 5, 2.5: 1})

    assert 'aria-label="Mana curve histogram"' in markup
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


def test_suggestions_page_filters_by_identity_and_strategy_without_corrections(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "streamlit-suggestions.db"
    monkeypatch.setenv("MANA_ARCHITECT_DB_PATH", str(database_path))
    st.cache_resource.clear()
    get_engine.clear()
    engine = create_database_engine(database_path)
    initialize_database(engine)
    catalog = load_strategy_catalog()
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                Card(
                    oracle_id="ui-commander",
                    name="Token Commander",
                    mana_cost="{2}{G}",
                    cmc=3,
                    colors=["G"],
                    color_identity=["G"],
                    produced_mana=None,
                    type_line="Legendary Creature — Elf",
                    oracle_text="",
                    keywords=[],
                    legalities={"commander": "legal"},
                    image_uri=None,
                    scryfall_updated_at="2026-11-11T12:00:00Z",
                ),
                Card(
                    oracle_id="ui-token-card",
                    name="Token Spell",
                    mana_cost="{G}",
                    cmc=1,
                    colors=["G"],
                    color_identity=["G"],
                    produced_mana=None,
                    type_line="Sorcery",
                    oracle_text="Create a token.",
                    keywords=[],
                    legalities={"commander": "legal"},
                    image_uri=None,
                    scryfall_updated_at="2026-11-11T12:00:00Z",
                ),
            ]
        )
        session.add_all(
            [
                CollectionPrinting(
                    scryfall_id="ui-printing-commander",
                    oracle_id="ui-commander",
                    name="Token Commander",
                    set_code="tst",
                    collector_number="1",
                    language="en",
                    finish="nonfoil",
                    quantity=1,
                    condition="near_mint",
                ),
                CollectionPrinting(
                    scryfall_id="ui-printing-token-card",
                    oracle_id="ui-token-card",
                    name="Token Spell",
                    set_code="tst",
                    collector_number="2",
                    language="en",
                    finish="nonfoil",
                    quantity=2,
                    condition="near_mint",
                ),
            ]
        )
        reclassify_cards(
            session,
            catalog,
            DEFAULT_CATALOG_DIR,
            ("ui-commander", "ui-token-card"),
        )
    engine.dispose()

    try:
        app = AppTest.from_file(
            str(Path(__file__).parents[1] / "src" / "mana_architect" / "streamlit_app.py")
        ).run()
        app.radio(key="page_navigation").set_value("Suggestions").run()
        assert not app.exception

        app.selectbox(key="suggestion_color_identity").select(("G",))
        app.multiselect(key="suggestion_strategy_ids").select("tokens")
        app.form_submit_button(key="search_collection_suggestions").click().run()
        assert not app.exception
        assert any("Token Spell" in item.value for item in app.markdown)
        assert not any("Correct This Card" in item.value for item in app.markdown)
        assert app.metric[0].label == "Cards in color identity"
    finally:
        app_engine = get_engine()
        app_engine.dispose()
        get_engine.clear()
        st.cache_resource.clear()

def test_builder_generates_with_selected_catalog_strategy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "streamlit-catalog-builder.db"
    monkeypatch.setenv("MANA_ARCHITECT_DB_PATH", str(database_path))
    st.cache_resource.clear()
    get_engine.clear()
    engine = create_database_engine(database_path)
    initialize_database(engine)
    catalog = load_strategy_catalog()
    cards = (
        ("ui-commander", "Blue Captain", "Legendary Creature — Human", "", 1, ["U"]),
        ("ui-island", "Island", "Basic Land — Island", "", 99, ["U"]),
        ("ui-token", "Z Token Plan", "Sorcery", "Create a token.", 1, []),
        ("ui-plain", "A Plain Plan", "Sorcery", "Scry 1.", 1, []),
    )
    with Session(engine) as session, session.begin():
        for oracle_id, name, type_line, oracle_text, quantity, identity in cards:
            session.add(
                Card(
                    oracle_id=oracle_id,
                    name=name,
                    mana_cost=None,
                    cmc=1 if type_line == "Sorcery" else None,
                    colors=identity,
                    color_identity=identity,
                    produced_mana=["U"] if "Basic Land" in type_line else None,
                    type_line=type_line,
                    oracle_text=oracle_text,
                    keywords=[],
                    legalities={"commander": "legal"},
                    image_uri=None,
                    scryfall_updated_at="2026-11-11T12:00:00Z",
                )
            )
            session.add(
                CollectionPrinting(
                    scryfall_id=f"printing-{oracle_id}",
                    oracle_id=oracle_id,
                    name=name,
                    set_code="tst",
                    collector_number="1",
                    language="en",
                    finish="nonfoil",
                    quantity=quantity,
                    condition="near_mint",
                )
            )
        save_commander_affinity_profile(
            session,
            CommanderAffinityProfile(
                commander_oracle_id="ui-commander",
                strategy_affinities=(
                    CommanderStrategyAffinity("tokens", "primary", 0.8),
                ),
                tag_affinities=(CommanderTagAffinity("create_tokens"),),
            ),
            catalog,
        )
        reclassify_cards(
            session,
            catalog,
            DEFAULT_CATALOG_DIR,
            ("ui-island", "ui-token", "ui-plain"),
        )
    engine.dispose()

    try:
        app = AppTest.from_file(
            str(Path(__file__).parents[1] / "src" / "mana_architect" / "streamlit_app.py")
        ).run()
        app.selectbox(key="builder_commander_id").select("ui-commander")
        app.selectbox(key="builder_generation_mode").select("catalog")
        app.selectbox(key="builder_strategy_id").select("tokens")
        app.number_input(key="builder_land_count").set_value(98)
        app.form_submit_button(key="build_deck_submit").click().run()

        assert not app.exception
        assert app.session_state["deck_catalog_strategy_id"] == "tokens"
        assert "ui-token" in {
            card["oracle_id"] for card in app.session_state["deck_cards"]
        }
        assert any(
            "Catalog strategy: Tokens" in item.value
            for item in app.caption
        )
    finally:
        app_engine = get_engine()
        app_engine.dispose()
        get_engine.clear()
        st.cache_resource.clear()
