from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from mana_architect.manabox import parse_manabox_csv
from mana_architect.models import CollectionPrinting


UPSERT_BATCH_SIZE = 200


@dataclass(frozen=True)
class ImportSummary:
    rows_read: int
    unique_printings: int
    duplicate_rows_combined: int
    quantities_in_file: int
    inserted: int
    updated: int


def import_manabox_csv(path: Path, engine: Engine) -> ImportSummary:
    parsed = parse_manabox_csv(path)
    inserted = 0
    updated = 0
    key_columns = (
        CollectionPrinting.scryfall_id,
        CollectionPrinting.language,
        CollectionPrinting.finish,
        CollectionPrinting.condition,
    )

    with Session(engine) as session, session.begin():
        for start in range(0, len(parsed.entries), UPSERT_BATCH_SIZE):
            batch = parsed.entries[start : start + UPSERT_BATCH_SIZE]
            keys = [entry.identity for entry in batch]
            existing = session.execute(
                select(*key_columns).where(tuple_(*key_columns).in_(keys))
            ).all()
            existing_keys = {tuple(row) for row in existing}
            inserted += sum(entry.identity not in existing_keys for entry in batch)
            updated += len(batch) - sum(
                entry.identity not in existing_keys for entry in batch
            )

            values = [
                {
                    "scryfall_id": entry.scryfall_id,
                    "name": entry.name,
                    "set_code": entry.set_code,
                    "collector_number": entry.collector_number,
                    "language": entry.language,
                    "finish": entry.finish,
                    "quantity": entry.quantity,
                    "condition": entry.condition,
                }
                for entry in batch
            ]
            statement = insert(CollectionPrinting).values(values)
            statement = statement.on_conflict_do_update(
                index_elements=[*key_columns],
                set_={
                    "name": statement.excluded.name,
                    "set_code": statement.excluded.set_code,
                    "collector_number": statement.excluded.collector_number,
                    "quantity": statement.excluded.quantity,
                },
            )
            session.execute(statement)

    return ImportSummary(
        rows_read=parsed.rows_read,
        unique_printings=len(parsed.entries),
        duplicate_rows_combined=parsed.duplicate_rows_combined,
        quantities_in_file=sum(entry.quantity for entry in parsed.entries),
        inserted=inserted,
        updated=updated,
    )

