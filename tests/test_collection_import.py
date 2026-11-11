import csv
from pathlib import Path

import pytest

from mana_architect.collection import search_collection
from mana_architect.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from mana_architect.importer import import_manabox_csv
from mana_architect.manabox import ImportFormatError, parse_manabox_csv


HEADERS = [
    "Name",
    "Set code",
    "Collector number",
    "Foil",
    "Quantity",
    "Scryfall ID",
    "Condition",
    "Language",
]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(HEADERS)
        writer.writerows(rows)


def test_parser_combines_duplicate_rows_and_maps_finishes(tmp_path: Path) -> None:
    csv_path = tmp_path / "collection.csv"
    write_csv(
        csv_path,
        [
            ["Aetherize", "znc", "23", "normal", "1", "abc-123", "near_mint", "PT"],
            ["Aetherize", "ZNC", "23", "normal", "2", "ABC-123", "near_mint", "pt"],
            ["Aetherize", "ZNC", "23", "foil", "1", "abc-123", "near_mint", "pt"],
        ],
    )

    parsed = parse_manabox_csv(csv_path)

    assert parsed.rows_read == 3
    assert parsed.duplicate_rows_combined == 1
    assert [(entry.finish, entry.quantity) for entry in parsed.entries] == [
        ("nonfoil", 3),
        ("foil", 1),
    ]


def test_import_is_idempotent_and_search_aggregates_quantity(tmp_path: Path) -> None:
    csv_path = tmp_path / "collection.csv"
    database_path = tmp_path / "mana_architect.db"
    write_csv(
        csv_path,
        [
            ["Aetherize", "ZNC", "23", "normal", "1", "abc-123", "near_mint", "pt"],
            ["Aetherize", "ZNC", "23", "normal", "2", "abc-123", "near_mint", "pt"],
            ["Aetherize", "ZNC", "23", "foil", "1", "abc-123", "near_mint", "pt"],
        ],
    )
    engine = create_database_engine(database_path)
    initialize_database(engine)

    first = import_manabox_csv(csv_path, engine)
    second = import_manabox_csv(csv_path, engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        results = search_collection(session, "aether")

    assert first.inserted == 2
    assert first.updated == 0
    assert second.inserted == 0
    assert second.updated == 2
    assert [(result.name, result.quantity) for result in results] == [("Aetherize", 4)]
    engine.dispose()


def test_invalid_row_is_reported_with_line_number(tmp_path: Path) -> None:
    csv_path = tmp_path / "collection.csv"
    write_csv(
        csv_path,
        [["Aetherize", "ZNC", "23", "normal", "not-a-number", "abc-123", "near_mint", "pt"]],
    )

    with pytest.raises(ImportFormatError, match="Linha 2"):
        parse_manabox_csv(csv_path)

