import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, URL, create_engine, event, inspect, insert, select, text
from sqlalchemy.orm import Session, sessionmaker

from mana_architect.models import Base, SchemaMigration


CLASSIFICATION_SCHEMA_VERSION = "classification-storage-v1"
CARD_CLASSIFICATION_OVERRIDES_SCHEMA_VERSION = "card-classification-overrides-v1"


def create_database_engine(database_path: Path | None = None) -> Engine:
    load_dotenv()
    path = database_path or Path(
        os.environ.get("MANA_ARCHITECT_DB_PATH", "data/mana_architect.db")
    )
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        URL.create("sqlite", database=str(path)),
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(connection, _) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("cards")}
    if "produced_mana" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE cards ADD COLUMN produced_mana JSON"))
    with engine.begin() as connection:
        versions = (
            CLASSIFICATION_SCHEMA_VERSION,
            CARD_CLASSIFICATION_OVERRIDES_SCHEMA_VERSION,
        )
        for version in versions:
            existing_version = connection.scalar(
                select(SchemaMigration.version).where(
                    SchemaMigration.version == version
                )
            )
            if existing_version is None:
                connection.execute(
                    insert(SchemaMigration).values(
                        version=version,
                        applied_at=datetime.now(timezone.utc),
                    )
                )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
