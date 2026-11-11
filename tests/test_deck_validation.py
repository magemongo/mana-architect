import json
from pathlib import Path

from sqlalchemy.orm import Session

from mana_architect.database import create_database_engine, initialize_database
from mana_architect.models import Card, CollectionPrinting, SyncMetadata
from mana_architect.validation import (
    DeckCard,
    load_deck_file,
    validate_deck,
)


def add_card(
    session: Session,
    *,
    oracle_id: str,
    name: str,
    quantity: int,
    type_line: str,
    color_identity: list[str] | None = None,
    oracle_text: str | None = None,
    legality: str = "legal",
) -> None:
    session.add(
        Card(
            oracle_id=oracle_id,
            name=name,
            mana_cost=None,
            cmc=None,
            colors=[],
            color_identity=color_identity or [],
            type_line=type_line,
            oracle_text=oracle_text,
            keywords=[],
            legalities={"commander": legality},
            image_uri=None,
            scryfall_updated_at="2026-11-11T10:06:45.392+00:00",
        )
    )
    session.flush()
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


def make_deck_database(
    path: Path,
    *,
    add_sync_metadata: bool = True,
    additional_cards: list[dict[str, object]] | None = None,
):
    engine = create_database_engine(path)
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        if add_sync_metadata:
            session.add(
                SyncMetadata(
                    key="bulk_updated_at",
                    value="2026-11-11T10:06:45.392+00:00",
                )
            )
        add_card(
            session,
            oracle_id="commander",
            name="Blue Captain",
            quantity=1,
            type_line="Legendary Creature — Human",
            color_identity=["U"],
        )
        add_card(
            session,
            oracle_id="island",
            name="Island",
            quantity=99,
            type_line="Basic Land — Island",
        )
        for card in additional_cards or []:
            add_card(session, **card)
    return engine


def test_valid_deck_includes_commander_and_basic_lands_ignore_singleton(
    tmp_path: Path,
) -> None:
    engine = make_deck_database(tmp_path / "valid.db")
    with Session(engine) as session:
        result = validate_deck(
            session,
            "commander",
            [DeckCard("commander", 1), DeckCard("island", 99)],
        )
    engine.dispose()

    assert result.status == "valid"
    assert result.total_cards == 100
    assert result.data_as_of == "2026-11-11T10:06:45.392+00:00"
    assert not result.errors
    assert result.warnings[0].code == "snapshot_legality"


def test_deck_flags_singleton_color_and_physical_quantity_violations(
    tmp_path: Path,
) -> None:
    engine = make_deck_database(
        tmp_path / "invalid.db",
        additional_cards=[
            {
                "oracle_id": "red-spell",
                "name": "Red Spell",
                "quantity": 1,
                "type_line": "Instant",
                "color_identity": ["R"],
            },
            {
                "oracle_id": "blue-spell",
                "name": "Blue Spell",
                "quantity": 1,
                "type_line": "Instant",
                "color_identity": ["U"],
            },
        ],
    )
    with Session(engine) as session:
        result = validate_deck(
            session,
            "commander",
            [
                DeckCard("commander", 1),
                DeckCard("island", 96),
                DeckCard("blue-spell", 2),
                DeckCard("red-spell", 1),
            ],
        )
    engine.dispose()

    error_codes = {issue.code for issue in result.errors}
    assert result.status == "invalid"
    assert result.total_cards == 100
    assert {"singleton", "color_identity", "insufficient_quantity"} <= error_codes


def test_name_text_exceptions_allow_any_number_and_up_to_seven(tmp_path: Path) -> None:
    engine = make_deck_database(
        tmp_path / "exceptions.db",
        additional_cards=[
            {
                "oracle_id": "rats",
                "name": "Relentless Rats",
                "quantity": 4,
                "type_line": "Creature — Rat",
                "oracle_text": (
                    "A deck can have any number of cards named Relentless Rats."
                ),
            },
            {
                "oracle_id": "dwarves",
                "name": "Seven Dwarves",
                "quantity": 7,
                "type_line": "Creature — Dwarf",
                "oracle_text": (
                    "A deck can have up to seven cards named Seven Dwarves."
                ),
            },
        ],
    )
    with Session(engine) as session:
        result = validate_deck(
            session,
            "commander",
            [
                DeckCard("commander", 1),
                DeckCard("island", 88),
                DeckCard("rats", 4),
                DeckCard("dwarves", 7),
            ],
        )
    engine.dispose()

    assert result.total_cards == 100
    assert result.status == "valid"
    assert not any(issue.code == "singleton" for issue in result.errors)


def test_validation_is_incomplete_when_no_legality_snapshot_is_recorded(
    tmp_path: Path,
) -> None:
    engine = make_deck_database(
        tmp_path / "incomplete.db",
        add_sync_metadata=False,
    )
    with Session(engine) as session:
        result = validate_deck(
            session,
            "commander",
            [DeckCard("commander", 1), DeckCard("island", 99)],
        )
    engine.dispose()

    assert result.status == "incomplete"
    assert result.data_as_of is None
    assert any(issue.code == "missing_sync_date" for issue in result.unverified)


def test_nonlegal_card_from_scryfall_is_a_definite_legality_error(
    tmp_path: Path,
) -> None:
    engine = make_deck_database(
        tmp_path / "not-legal.db",
        additional_cards=[
            {
                "oracle_id": "not-legal-card",
                "name": "Not Legal Card",
                "quantity": 1,
                "type_line": "Sorcery",
                "legality": "not_legal",
            },
        ],
    )
    with Session(engine) as session:
        result = validate_deck(
            session,
            "commander",
            [
                DeckCard("commander", 1),
                DeckCard("island", 98),
                DeckCard("not-legal-card", 1),
            ],
        )
    engine.dispose()

    assert result.status == "invalid"
    assert any(issue.code == "illegal_card" for issue in result.errors)


def test_load_deck_file_reads_expected_json_shape(tmp_path: Path) -> None:
    path = tmp_path / "deck.json"
    path.write_text(
        json.dumps(
            {
                "commander": "commander",
                "cards": [
                    {"oracle_id": "commander", "quantity": 1},
                    {"oracle_id": "island", "quantity": 99},
                ],
            }
        ),
        encoding="utf-8",
    )

    commander, cards = load_deck_file(path)

    assert commander == "commander"
    assert cards == [DeckCard("commander", 1), DeckCard("island", 99)]
