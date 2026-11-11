from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from mana_architect.commanders import (
    CommanderError,
    list_available_commanders,
    resolve_commander,
    search_for_commander,
)
from mana_architect.database import create_database_engine, initialize_database
from mana_architect.models import Card, CollectionPrinting
from mana_architect.roles import classify_card_roles


def add_card(
    session: Session,
    *,
    oracle_id: str,
    name: str,
    type_line: str,
    color_identity: list[str],
    oracle_text: str | None = None,
    commander_legality: str = "legal",
    quantity: int = 1,
    linked: bool = True,
) -> None:
    session.add(
        Card(
            oracle_id=oracle_id,
            name=name,
            mana_cost=None,
            cmc=None,
            colors=[],
            color_identity=color_identity,
            type_line=type_line,
            oracle_text=oracle_text,
            keywords=[],
            legalities={"commander": commander_legality},
            image_uri=None,
            scryfall_updated_at="2026-11-11T10:06:45.392+00:00",
        )
    )
    session.flush()
    session.add(
        CollectionPrinting(
            scryfall_id=f"printing-{oracle_id}",
            oracle_id=oracle_id if linked else None,
            name=name,
            set_code="tst",
            collector_number="1",
            language="en",
            finish="nonfoil",
            quantity=quantity,
            condition="near_mint",
        )
    )


@pytest.fixture
def commander_database(tmp_path: Path):
    engine = create_database_engine(tmp_path / "commander.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        add_card(
            session,
            oracle_id="legendary-id",
            name="Legendary Commander",
            type_line="Legendary Creature — Human Wizard",
            color_identity=["U", "B"],
            quantity=2,
        )
        add_card(
            session,
            oracle_id="special-id",
            name="Special Planeswalker",
            type_line="Legendary Planeswalker — Special",
            color_identity=["U"],
            oracle_text="Special Planeswalker can be your commander.",
        )
        add_card(
            session,
            oracle_id="banned-id",
            name="Banned Commander",
            type_line="Legendary Creature — Human",
            color_identity=["U"],
            commander_legality="banned",
        )
        add_card(
            session,
            oracle_id="ordinary-id",
            name="Ordinary Creature",
            type_line="Creature — Human",
            color_identity=["U"],
        )
        add_card(
            session,
            oracle_id="red-card-id",
            name="Red Card",
            type_line="Instant",
            color_identity=["R"],
        )
        add_card(
            session,
            oracle_id="identityless-id",
            name="Unlinked Card",
            type_line="Creature",
            color_identity=["U"],
            linked=False,
        )
        add_card(
            session,
            oracle_id="unknown-legality-id",
            name="Unknown Legality",
            type_line="Legendary Creature — Human",
            color_identity=["U"],
            commander_legality="not_legal",
        )
    yield engine
    engine.dispose()


def test_list_available_commanders_includes_only_owned_eligible_legal_cards(
    commander_database,
) -> None:
    with Session(commander_database) as session:
        options = list_available_commanders(session)

    assert [(option.name, option.quantity) for option in options] == [
        ("Legendary Commander", 2),
        ("Special Planeswalker", 1),
    ]


def test_user_resolves_commander_by_exact_name_or_oracle_id(commander_database) -> None:
    with Session(commander_database) as session:
        by_name = resolve_commander(session, "legendary commander")
        by_id = resolve_commander(session, "special-id")

    assert by_name.oracle_id == "legendary-id"
    assert by_id.name == "Special Planeswalker"


def test_collection_filter_uses_chosen_commander_identity_and_legality(
    commander_database,
) -> None:
    with Session(commander_database) as session:
        commander, cards = search_for_commander(
            session,
            commander_identifier="Legendary Commander",
        )

    assert commander.color_identity == ("U", "B")
    assert [card.name for card in cards] == [
        "Legendary Commander",
        "Ordinary Creature",
        "Special Planeswalker",
    ]


def test_unknown_or_ineligible_commander_is_reported(commander_database) -> None:
    with Session(commander_database) as session:
        with pytest.raises(CommanderError, match="não corresponde"):
            resolve_commander(session, "Ordinary Creature")


def test_initial_card_roles_are_explicitly_heuristic() -> None:
    roles = classify_card_roles(
        "Sorcery",
        "Search your library for a basic land card and put it onto the battlefield. "
        "Draw a card. Destroy target artifact.",
    )

    assert roles == (
        "Ramp (heurística)",
        "Compra de cartas (heurística)",
        "Remoção (heurística)",
    )
    assert classify_card_roles("Basic Land — Island", None) == ("Terreno",)
