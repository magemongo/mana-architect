import gzip
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from mana_architect.models import Card, CollectionPrinting, SyncMetadata


API_BASE = "https://api.scryfall.com"
BULK_DATA_URL = f"{API_BASE}/bulk-data"
BULK_TYPE = "default_cards"
BULK_FILENAME = "default-cards.jsonl.gz"
USER_AGENT = "ManaArchitect/0.1.0"
DOWNLOAD_TIMEOUT_SECONDS = 120
BULK_BATCH_SIZE = 200


class ScryfallError(RuntimeError):
    """Raised when Scryfall data cannot be retrieved or interpreted."""


@dataclass(frozen=True)
class BulkDataFile:
    updated_at: str
    download_uri: str


@dataclass(frozen=True)
class SyncSummary:
    bulk_updated_at: str
    bulk_downloaded: bool
    cards_updated: int
    printings_linked: int
    unresolved_printings: int


def _card_text(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    if isinstance(value, str):
        return value

    faces = record.get("card_faces")
    if isinstance(faces, list):
        face_values = [
            face[key]
            for face in faces
            if isinstance(face, dict) and isinstance(face.get(key), str)
        ]
        if face_values:
            return " // ".join(face_values)
    return None


def _image_uri(record: dict[str, Any]) -> str | None:
    images = record.get("image_uris")
    if isinstance(images, dict):
        normal = images.get("normal") or images.get("small")
        if isinstance(normal, str):
            return normal

    faces = record.get("card_faces")
    if isinstance(faces, list):
        for face in faces:
            if not isinstance(face, dict):
                continue
            images = face.get("image_uris")
            if isinstance(images, dict):
                normal = images.get("normal") or images.get("small")
                if isinstance(normal, str):
                    return normal
    return None


def _card_values(record: dict[str, Any], bulk_updated_at: str) -> dict[str, Any]:
    oracle_id = record.get("oracle_id")
    name = record.get("name")
    if not isinstance(oracle_id, str) or not isinstance(name, str) or not name:
        raise ScryfallError("Registro do Bulk Data sem oracle_id ou nome válido.")

    colors = record.get("colors", [])
    color_identity = record.get("color_identity", [])
    produced_mana = record.get("produced_mana", [])
    keywords = record.get("keywords", [])
    legalities = record.get("legalities", {})
    cmc = record.get("cmc")
    if not isinstance(colors, list) or not all(isinstance(item, str) for item in colors):
        raise ScryfallError(f"Campo colors inválido para {name}.")
    if not isinstance(color_identity, list) or not all(
        isinstance(item, str) for item in color_identity
    ):
        raise ScryfallError(f"Campo color_identity inválido para {name}.")
    if not isinstance(produced_mana, list) or not all(
        isinstance(item, str) for item in produced_mana
    ):
        raise ScryfallError(f"Campo produced_mana inválido para {name}.")
    if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
        raise ScryfallError(f"Campo keywords inválido para {name}.")
    if not isinstance(legalities, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in legalities.items()
    ):
        raise ScryfallError(f"Campo legalities inválido para {name}.")
    if cmc is not None and (isinstance(cmc, bool) or not isinstance(cmc, (int, float))):
        raise ScryfallError(f"Campo cmc inválido para {name}.")

    type_line = _card_text(record, "type_line")
    mana_cost = _card_text(record, "mana_cost")
    oracle_text = _card_text(record, "oracle_text")
    if type_line is None:
        raise ScryfallError(f"Campo type_line ausente para {name}.")

    return {
        "oracle_id": oracle_id,
        "name": name,
        "mana_cost": mana_cost,
        "cmc": cmc,
        "colors": colors,
        "color_identity": color_identity,
        "produced_mana": produced_mana,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "keywords": keywords,
        "legalities": legalities,
        "image_uri": _image_uri(record),
        "scryfall_updated_at": bulk_updated_at,
    }


class ScryfallClient:
    def __init__(self, cache_directory: Path) -> None:
        self.cache_directory = cache_directory

    def _request_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise ScryfallError(f"Falha ao consultar Scryfall ({url}): {error}") from error
        if not isinstance(payload, dict):
            raise ScryfallError(f"Resposta inválida do Scryfall ({url}).")
        return payload

    def get_bulk_data(self) -> BulkDataFile:
        payload = self._request_json(BULK_DATA_URL)
        bulk_records = payload.get("data")
        if not isinstance(bulk_records, list):
            raise ScryfallError("Resposta de Bulk Data sem a lista data.")

        for record in bulk_records:
            if not isinstance(record, dict) or record.get("type") != BULK_TYPE:
                continue
            updated_at = record.get("updated_at")
            download_uri = record.get("jsonl_download_uri") or record.get("download_uri")
            if not isinstance(updated_at, str) or not isinstance(download_uri, str):
                raise ScryfallError("Metadados de default_cards incompletos.")
            parsed_uri = urlparse(download_uri)
            if parsed_uri.scheme != "https" or parsed_uri.hostname != "data.scryfall.io":
                raise ScryfallError("URL do Bulk Data fora do domínio oficial do Scryfall.")
            return BulkDataFile(updated_at=updated_at, download_uri=download_uri)
        raise ScryfallError("O Bulk Data default_cards não foi encontrado.")

    def download_bulk(self, download_uri: str, destination: Path) -> None:
        parsed_uri = urlparse(download_uri)
        if parsed_uri.scheme != "https" or parsed_uri.hostname != "data.scryfall.io":
            raise ScryfallError("URL do Bulk Data fora do domínio oficial do Scryfall.")
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_suffix(destination.suffix + ".tmp")
        request = Request(
            download_uri,
            headers={
                "Accept": "application/gzip",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                with temporary_path.open("wb") as target:
                    shutil.copyfileobj(response, target)
            with gzip.open(temporary_path, "rb") as archive:
                archive.read(1)
            os.replace(temporary_path, destination)
        except (HTTPError, URLError, TimeoutError, OSError, EOFError) as error:
            temporary_path.unlink(missing_ok=True)
            raise ScryfallError(f"Falha ao baixar Bulk Data do Scryfall: {error}") from error


def _read_relevant_cards(
    bulk_path: Path,
    owned_scryfall_ids: set[str],
    bulk_updated_at: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    cards: dict[str, dict[str, Any]] = {}
    printing_oracle_ids: dict[str, str] = {}
    try:
        with gzip.open(bulk_path, "rt", encoding="utf-8", newline="") as bulk_file:
            for line_number, line in enumerate(bulk_file, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ScryfallError(
                        f"JSON inválido no Bulk Data, linha {line_number}: {error}"
                    ) from error
                if not isinstance(record, dict):
                    raise ScryfallError(
                        f"Registro inválido no Bulk Data, linha {line_number}."
                    )

                scryfall_id = record.get("id")
                oracle_id = record.get("oracle_id")
                if not isinstance(scryfall_id, str) or scryfall_id not in owned_scryfall_ids:
                    continue
                if not isinstance(oracle_id, str):
                    continue

                printing_oracle_ids[scryfall_id] = oracle_id
                if oracle_id not in cards:
                    cards[oracle_id] = _card_values(record, bulk_updated_at)
    except (OSError, EOFError, UnicodeDecodeError) as error:
        raise ScryfallError(f"Não foi possível ler o cache de Bulk Data: {error}") from error
    return cards, printing_oracle_ids


def sync_collection(
    engine: Engine,
    cache_directory: Path = Path("data/cache/scryfall"),
    client: ScryfallClient | None = None,
) -> SyncSummary:
    scryfall = client or ScryfallClient(cache_directory)
    bulk = scryfall.get_bulk_data()
    bulk_path = scryfall.cache_directory / BULK_FILENAME

    with Session(engine) as session:
        previous_bulk_updated_at = session.get(SyncMetadata, "bulk_updated_at")
        previous_produced_mana_sync = session.get(
            SyncMetadata,
            "produced_mana_updated_at",
        )
        bulk_changed = (
            previous_bulk_updated_at is None
            or previous_bulk_updated_at.value != bulk.updated_at
        )
        refresh_all = bulk_changed or (
            previous_produced_mana_sync is None
            or previous_produced_mana_sync.value != bulk.updated_at
        )
        if refresh_all:
            owned_scryfall_ids = set(
                session.scalars(select(CollectionPrinting.scryfall_id))
            )
        else:
            owned_scryfall_ids = set(
                session.scalars(
                    select(CollectionPrinting.scryfall_id).where(
                        CollectionPrinting.oracle_id.is_(None)
                    )
                )
            )

    bulk_downloaded = bulk_changed or not bulk_path.is_file()
    if bulk_downloaded:
        scryfall.download_bulk(bulk.download_uri, bulk_path)

    if not owned_scryfall_ids:
        cards: dict[str, dict[str, Any]] = {}
        printing_oracle_ids: dict[str, str] = {}
    else:
        cards, printing_oracle_ids = _read_relevant_cards(
            bulk_path,
            owned_scryfall_ids,
            bulk.updated_at,
        )

    with Session(engine) as session, session.begin():
        for start in range(0, len(cards), BULK_BATCH_SIZE):
            batch = list(cards.values())[start : start + BULK_BATCH_SIZE]
            statement = insert(Card).values(batch)
            statement = statement.on_conflict_do_update(
                index_elements=[Card.oracle_id],
                set_={
                    column: getattr(statement.excluded, column)
                    for column in (
                        "name",
                        "mana_cost",
                        "cmc",
                        "colors",
                        "color_identity",
                        "produced_mana",
                        "type_line",
                        "oracle_text",
                        "keywords",
                        "legalities",
                        "image_uri",
                        "scryfall_updated_at",
                    )
                },
            )
            session.execute(statement)

        linked_printings = 0
        for start in range(0, len(printing_oracle_ids), BULK_BATCH_SIZE):
            batch = list(printing_oracle_ids.items())[start : start + BULK_BATCH_SIZE]
            for scryfall_id, oracle_id in batch:
                result = session.execute(
                    update(CollectionPrinting)
                    .where(
                        CollectionPrinting.scryfall_id == scryfall_id,
                        CollectionPrinting.oracle_id.is_(None),
                    )
                    .values(oracle_id=oracle_id)
                )
                linked_printings += result.rowcount or 0

        metadata = {
            "bulk_updated_at": bulk.updated_at,
            "produced_mana_updated_at": bulk.updated_at,
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
        }
        for key, value in metadata.items():
            statement = insert(SyncMetadata).values(key=key, value=value)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[SyncMetadata.key],
                    set_={"value": statement.excluded.value},
                )
            )
        unresolved_count = session.scalar(
            select(func.count(CollectionPrinting.id)).where(
                CollectionPrinting.oracle_id.is_(None)
            )
        )

    return SyncSummary(
        bulk_updated_at=bulk.updated_at,
        bulk_downloaded=bulk_downloaded,
        cards_updated=len(cards),
        printings_linked=linked_printings,
        unresolved_printings=unresolved_count or 0,
    )
