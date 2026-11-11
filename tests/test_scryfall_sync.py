import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from mana_architect.collection import search_collection
from mana_architect.database import create_database_engine, initialize_database
from mana_architect.models import Card, CollectionPrinting, SyncMetadata
from mana_architect.scryfall import (
    BULK_DATA_URL,
    BulkDataFile,
    ScryfallClient,
    ScryfallError,
    sync_collection,
)


def scryfall_record(
    *,
    scryfall_id: str = "owned-id",
    oracle_id: str = "oracle-id",
    name: str = "Aetherize",
) -> dict[str, object]:
    return {
        "id": scryfall_id,
        "oracle_id": oracle_id,
        "name": name,
        "mana_cost": "{2}{U}",
        "cmc": 3.0,
        "colors": ["U"],
        "color_identity": ["U"],
        "produced_mana": ["U"],
        "type_line": "Instant",
        "oracle_text": "Return all attacking creatures to their owners' hands.",
        "keywords": [],
        "legalities": {"commander": "legal"},
        "image_uris": {"normal": "https://example.test/card.jpg"},
    }


def add_printing(session: Session, scryfall_id: str, name: str) -> None:
    session.add(
        CollectionPrinting(
            scryfall_id=scryfall_id,
            name=name,
            set_code="znc",
            collector_number="23",
            language="en",
            finish="nonfoil",
            quantity=1,
            condition="near_mint",
        )
    )


class FakeScryfallClient:
    def __init__(
        self,
        cache_directory: Path,
        records: list[dict[str, object]],
        updated_at: str = "2026-11-11T10:00:00Z",
    ) -> None:
        self.cache_directory = cache_directory
        self.records = records
        self.updated_at = updated_at
        self.download_count = 0
        self.download_uris: list[str] = []

    def get_bulk_data(self) -> BulkDataFile:
        return BulkDataFile(
            updated_at=self.updated_at,
            download_uri="https://data.scryfall.io/default-cards-test.jsonl.gz",
        )

    def download_bulk(self, _download_uri: str, destination: Path) -> None:
        self.download_count += 1
        self.download_uris.append(_download_uri)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(destination, "wt", encoding="utf-8") as bulk_file:
            for record in self.records:
                bulk_file.write(json.dumps(record) + "\n")


def test_bulk_metadata_uses_current_jsonl_download_uri_field(tmp_path: Path) -> None:
    client = ScryfallClient(tmp_path)

    def fake_request(url: str) -> dict[str, object]:
        assert url == BULK_DATA_URL
        return {
            "data": [
                {
                    "type": "default_cards",
                    "updated_at": "2026-11-11T10:06:45.392+00:00",
                    "jsonl_download_uri": (
                        "https://data.scryfall.io/default-cards/"
                        "default-cards-test.jsonl.gz"
                    ),
                }
            ]
        }

    client._request_json = fake_request

    bulk = client.get_bulk_data()

    assert bulk.updated_at == "2026-11-11T10:06:45.392+00:00"
    assert bulk.download_uri.startswith("https://data.scryfall.io/")


def test_sync_enriches_cards_and_links_owned_printings(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "collection.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        add_printing(session, "owned-id", "Aetherize")
        add_printing(session, "not-in-bulk", "Unknown Card")

    client = FakeScryfallClient(
        tmp_path / "cache",
        [scryfall_record()],
    )
    summary = sync_collection(engine, client=client)

    with Session(engine) as session:
        printing = session.scalar(
            select(CollectionPrinting).where(CollectionPrinting.scryfall_id == "owned-id")
        )
        card = session.get(Card, "oracle-id")
        metadata = session.get(SyncMetadata, "bulk_updated_at")

    assert summary.bulk_downloaded is True
    assert summary.cards_updated == 1
    assert summary.printings_linked == 1
    assert summary.unresolved_printings == 1
    assert client.download_uris == [
        "https://data.scryfall.io/default-cards-test.jsonl.gz"
    ]
    assert printing is not None and printing.oracle_id == "oracle-id"
    assert card is not None
    assert card.colors == ["U"]
    assert card.produced_mana == ["U"]
    assert card.legalities == {"commander": "legal"}
    assert card.image_uri == "https://example.test/card.jpg"
    assert metadata is not None and metadata.value == client.updated_at


def test_sync_reuses_cache_and_does_not_repeat_download(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "collection.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        add_printing(session, "owned-id", "Aetherize")

    client = FakeScryfallClient(tmp_path / "cache", [scryfall_record()])
    first = sync_collection(engine, client=client)
    second = sync_collection(engine, client=client)

    assert first.bulk_downloaded is True
    assert second.bulk_downloaded is False
    assert client.download_count == 1
    assert second.printings_linked == 0
    assert second.unresolved_printings == 0


def test_sync_refreshes_produced_mana_from_existing_bulk_cache(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "collection.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        add_printing(session, "owned-id", "Aetherize")

    client = FakeScryfallClient(tmp_path / "cache", [scryfall_record()])
    sync_collection(engine, client=client)
    with Session(engine) as session, session.begin():
        session.delete(session.get(SyncMetadata, "produced_mana_updated_at"))

    summary = sync_collection(engine, client=client)

    with Session(engine) as session:
        metadata = session.get(SyncMetadata, "produced_mana_updated_at")
        card = session.get(Card, "oracle-id")
    assert summary.bulk_downloaded is False
    assert summary.cards_updated == 1
    assert client.download_count == 1
    assert metadata is not None and metadata.value == client.updated_at
    assert card is not None and card.produced_mana == ["U"]


def test_initialize_database_adds_produced_mana_to_existing_cards_table(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "legacy.db")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE cards (oracle_id VARCHAR(36) PRIMARY KEY, "
                "name VARCHAR(255) NOT NULL)"
            )
        )

    initialize_database(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("cards")}
    assert "produced_mana" in columns


def test_sync_rejects_malformed_bulk_without_partial_database_updates(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "collection.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        add_printing(session, "owned-id", "Aetherize")

    client = FakeScryfallClient(tmp_path / "cache", [scryfall_record()])
    client.records = [{"id": "owned-id", "oracle_id": "oracle-id", "name": "Aetherize"}]
    with pytest.raises(ScryfallError, match="type_line"):
        sync_collection(engine, client=client)

    with Session(engine) as session:
        assert session.get(Card, "oracle-id") is None
        assert session.get(SyncMetadata, "bulk_updated_at") is None
        printing = session.scalar(select(CollectionPrinting))
        assert printing is not None and printing.oracle_id is None


def test_collection_search_filters_by_enriched_card_fields(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "collection.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        session.add(
            Card(
                oracle_id="oracle-id",
                name="Aetherize",
                mana_cost="{2}{U}",
                cmc=3,
                colors=["U"],
                color_identity=["U"],
                type_line="Instant",
                oracle_text="Return all attacking creatures to their owners' hands.",
                keywords=[],
                legalities={"commander": "legal"},
                image_uri=None,
                scryfall_updated_at="2026-11-11T10:00:00Z",
            )
        )
        add_printing(session, "owned-id", "Aetherize")
        printing = session.scalar(select(CollectionPrinting))
        assert printing is not None
        printing.oracle_id = "oracle-id"

    with Session(engine) as session:
        results = search_collection(
            session,
            colors="U",
            color_identity="U",
            type_line="Instant",
            mana_cost="{2}{U}",
        )
        assert search_collection(session, colors="B") == []
        assert search_collection(session, type_line="Creature") == []

    assert [(result.name, result.quantity) for result in results] == [("Aetherize", 1)]
    assert results[0].mana_cost == "{2}{U}"
    assert results[0].type_line == "Instant"
