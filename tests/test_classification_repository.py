import json
from pathlib import Path
import shutil

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mana_architect.catalog import DEFAULT_CATALOG_DIR, load_strategy_catalog
from mana_architect.classification import CardClassificationInput, classify_card
from mana_architect.classification_repository import (
    catalog_configuration_hash,
    load_commander_affinity_profile,
    load_latest_classification,
    load_manual_overrides,
    reclassify_cards,
    remove_manual_override,
    save_commander_affinity_profile,
    save_manual_override,
)
from mana_architect.commander_affinity import (
    CommanderAffinityProfile,
    CommanderStrategyAffinity,
    CommanderTagAffinity,
)
from mana_architect.database import (
    CLASSIFICATION_SCHEMA_VERSION,
    create_database_engine,
    initialize_database,
)
from mana_architect.models import (
    Card,
    CardClassification,
    ClassificationCatalogVersion,
    ClassificationEvidenceRecord,
    ClassificationRun,
    CommanderAffinityRecord,
    ManualOverrideRecord,
    SchemaMigration,
)
from mana_architect.overrides import ManualOverride


@pytest.fixture
def catalog():
    return load_strategy_catalog()


@pytest.fixture
def database(tmp_path: Path):
    engine = create_database_engine(tmp_path / "classification.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                Card(
                    oracle_id="commander-id",
                    name="Commander",
                    mana_cost="{2}{G}",
                    cmc=3,
                    colors=["G"],
                    color_identity=["G"],
                    produced_mana=None,
                    type_line="Legendary Creature — Elf",
                    oracle_text="Create a token.",
                    keywords=[],
                    legalities={"commander": "legal"},
                    image_uri=None,
                    scryfall_updated_at="2026-11-11T12:00:00Z",
                ),
                Card(
                    oracle_id="spell-id",
                    name="Token Spell",
                    mana_cost="{2}{G}",
                    cmc=3,
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
                Card(
                    oracle_id="plain-id",
                    name="Plain Spell",
                    mana_cost="{U}",
                    cmc=1,
                    colors=["U"],
                    color_identity=["U"],
                    produced_mana=None,
                    type_line="Instant",
                    oracle_text="Scry 1.",
                    keywords=[],
                    legalities={"commander": "legal"},
                    image_uri=None,
                    scryfall_updated_at="2026-11-11T12:00:00Z",
                ),
            ]
        )
    yield engine
    engine.dispose()


def _copy_catalog(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    shutil.copytree(DEFAULT_CATALOG_DIR, config_dir)
    return config_dir


def test_initialization_adds_new_tables_and_records_schema_version(database) -> None:
    inspector = inspect(database)
    table_names = set(inspector.get_table_names())

    assert {
        "schema_migrations",
        "classification_catalog_versions",
        "classification_runs",
        "card_classifications",
        "classification_evidence",
        "manual_overrides",
        "commander_affinities",
    } <= table_names
    with Session(database) as session:
        versions = session.scalars(select(SchemaMigration.version)).all()
    assert versions == [CLASSIFICATION_SCHEMA_VERSION]


def test_initialization_migrates_existing_database_without_losing_collection_data(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "existing.db")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE cards (oracle_id VARCHAR(36) PRIMARY KEY, "
                "name VARCHAR(255) NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO cards (oracle_id, name) VALUES ('old-card', 'Old Card')")
        )

    initialize_database(engine)
    initialize_database(engine)

    with engine.connect() as connection:
        card = connection.execute(
            text("SELECT oracle_id, name FROM cards WHERE oracle_id = 'old-card'")
        ).one()
        columns = {column["name"] for column in inspect(engine).get_columns("cards")}
    assert card == ("old-card", "Old Card")
    assert "produced_mana" in columns
    engine.dispose()


def test_reclassification_persists_reproducible_evidence_and_skips_unchanged_cards(
    database,
    catalog,
) -> None:
    with Session(database) as session, session.begin():
        first = reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR)
    with Session(database) as session, session.begin():
        second = reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR)

    assert (first.examined, first.classified, first.skipped) == (3, 3, 0)
    assert (second.examined, second.classified, second.skipped) == (3, 0, 3)
    assert len(first.catalog_hash) == 64
    with Session(database) as session:
        result = load_latest_classification(session, "spell-id")
        runs = session.scalars(select(ClassificationRun)).all()
        candidates = session.scalars(select(CardClassification)).all()
        evidence = session.scalars(select(ClassificationEvidenceRecord)).all()
        versions = session.scalars(select(ClassificationCatalogVersion)).all()
    assert result is not None
    assert result == classify_card(
        CardClassificationInput(
            type_line="Sorcery",
            oracle_text="Create a token.",
            mana_cost="{2}{G}",
            cmc=3,
            colors=("G",),
            color_identity=("G",),
            keywords=(),
        ),
        catalog,
    )
    assert len(runs) == 3
    assert len(candidates) == 2
    assert len(evidence) == 2
    assert len(versions) == 1
    assert set(versions[0].configuration_snapshot) == {
        "strategy_catalog.json",
        "synergy_tags.json",
        "card_roles.json",
        "classification_rules.json",
        "scoring.json",
    }
    assert all(candidate.source == "automatic" for candidate in candidates)


def test_incremental_reclassification_only_reprocesses_changed_card(
    database,
    catalog,
) -> None:
    with Session(database) as session, session.begin():
        reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR)
    with Session(database) as session, session.begin():
        card = session.get(Card, "spell-id")
        assert card is not None
        card.oracle_text = "Draw two cards."
    with Session(database) as session, session.begin():
        summary = reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR)

    assert (summary.examined, summary.classified, summary.skipped) == (3, 1, 2)
    with Session(database) as session:
        runs = session.scalars(
            select(ClassificationRun).where(ClassificationRun.oracle_id == "spell-id")
        ).all()
        latest = load_latest_classification(session, "spell-id")
    assert len(runs) == 2
    assert latest is not None
    assert {item.target_id for item in latest.evidence} == {"card_draw"}


def test_changes_outside_rule_inputs_do_not_reclassify_and_score_changes_are_separate(
    database,
    tmp_path: Path,
) -> None:
    config_dir = _copy_catalog(tmp_path)
    copied_catalog = load_strategy_catalog(config_dir)
    with Session(database) as session, session.begin():
        reclassify_cards(session, copied_catalog, config_dir)

    with Session(database) as session, session.begin():
        card = session.get(Card, "spell-id")
        assert card is not None
        card.cmc = 4
    with Session(database) as session, session.begin():
        unchanged = reclassify_cards(session, copied_catalog, config_dir)

    assert (unchanged.classified, unchanged.skipped) == (0, 3)

    scoring_path = config_dir / "scoring.json"
    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    scoring["scoring_version"] = "1.0.1"
    scoring_path.write_text(json.dumps(scoring), encoding="utf-8")
    scoring_catalog = load_strategy_catalog(config_dir)
    with Session(database) as session, session.begin():
        scoring_update = reclassify_cards(session, scoring_catalog, config_dir)
    assert (scoring_update.classified, scoring_update.skipped) == (0, 3)
    with Session(database) as session:
        assert len(session.scalars(select(ClassificationCatalogVersion)).all()) == 2
        assert len(session.scalars(select(ClassificationRun)).all()) == 3


def test_display_name_changes_preserve_ids_and_do_not_reclassify(
    database,
    tmp_path: Path,
) -> None:
    config_dir = _copy_catalog(tmp_path)
    original_catalog = load_strategy_catalog(config_dir)
    with Session(database) as session, session.begin():
        reclassify_cards(session, original_catalog, config_dir)

    strategy_path = config_dir / "strategy_catalog.json"
    strategies = json.loads(strategy_path.read_text(encoding="utf-8"))
    strategies["strategies"][0]["name"] = "Fichas"
    strategy_path.write_text(json.dumps(strategies), encoding="utf-8")
    renamed_catalog = load_strategy_catalog(config_dir)

    assert renamed_catalog.strategies[0].id == original_catalog.strategies[0].id
    with Session(database) as session, session.begin():
        summary = reclassify_cards(session, renamed_catalog, config_dir)
    assert (summary.classified, summary.skipped) == (0, 3)


def test_catalog_update_reclassifies_without_erasing_manual_override(
    database,
    tmp_path: Path,
) -> None:
    config_dir = _copy_catalog(tmp_path)
    old_catalog = load_strategy_catalog(config_dir)
    with Session(database) as session, session.begin():
        reclassify_cards(session, old_catalog, config_dir)
        save_manual_override(
            session,
            ManualOverride("tag", "create_tokens", "force_exclude", reason="Curadoria"),
            old_catalog,
        )

    rules_path = config_dir / "classification_rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    rules["ruleset_version"] = "1.0.1"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    new_catalog = load_strategy_catalog(config_dir)
    with Session(database) as session, session.begin():
        summary = reclassify_cards(session, new_catalog, config_dir)
        overrides = load_manual_overrides(session, new_catalog)

    assert summary.classified == 3
    assert len(overrides) == 1
    assert overrides[0].action == "force_exclude"
    with Session(database) as session:
        assert len(session.scalars(select(ClassificationCatalogVersion)).all()) == 2


def test_selected_oracle_ids_are_validated(database, catalog) -> None:
    with Session(database) as session, session.begin():
        summary = reclassify_cards(
            session,
            catalog,
            DEFAULT_CATALOG_DIR,
            oracle_ids=("spell-id",),
        )
    assert (summary.examined, summary.classified, summary.skipped) == (1, 1, 0)

    with Session(database) as session, session.begin():
        with pytest.raises(ValueError, match="oracle_id inexistente"):
            reclassify_cards(
                session,
                catalog,
                DEFAULT_CATALOG_DIR,
                oracle_ids=("unknown-id",),
            )


def test_reclassification_transaction_rollback_leaves_no_partial_records(
    database,
    catalog,
) -> None:
    with pytest.raises(RuntimeError, match="force rollback"):
        with Session(database) as session, session.begin():
            reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR)
            raise RuntimeError("force rollback")

    with Session(database) as session:
        assert session.scalars(select(ClassificationRun)).all() == []
        assert session.scalars(select(ClassificationCatalogVersion)).all() == []


def test_sqlite_enforces_evidence_foreign_keys_and_unique_targets(database) -> None:
    with Session(database) as session, session.begin():
        reclassify_cards(
            session,
            load_strategy_catalog(),
            DEFAULT_CATALOG_DIR,
            oracle_ids=("spell-id",),
        )
    with Session(database) as session:
        run = session.scalar(
            select(ClassificationRun).where(ClassificationRun.oracle_id == "spell-id")
        )
        assert run is not None
        classification = session.scalar(
            select(CardClassification).where(
                CardClassification.run_id == run.run_id,
                CardClassification.target_type == "tag",
                CardClassification.target_id == "create_tokens",
            )
        )
        assert classification is not None

    with Session(database) as session:
        with pytest.raises(IntegrityError):
            with session.begin():
                session.add(
                    ClassificationEvidenceRecord(
                        run_id=run.run_id,
                        target_type="tag",
                        target_id="missing-target",
                        rule_id="missing-rule",
                        source_field="oracle_text_normalized",
                        matched_value="none",
                        weight=0.5,
                        confidence=0.5,
                        requires_review=False,
                        evidence_label="invalid foreign key",
                    )
                )
                session.flush()
    with Session(database) as session:
        with pytest.raises(IntegrityError):
            with session.begin():
                session.add(
                    CardClassification(
                        run_id=run.run_id,
                        target_type=classification.target_type,
                        target_id=classification.target_id,
                        confidence_score=0.75,
                        requires_review=False,
                        source="automatic",
                    )
                )
                session.flush()


def test_manual_override_round_trips_updates_and_can_be_removed(database, catalog) -> None:
    override = ManualOverride(
        "strategy",
        "tokens",
        "force_include",
        level="primary",
        confidence_score=0.9,
        reason="Aprovado em revisão.",
        author="user",
    )
    with Session(database) as session, session.begin():
        saved = save_manual_override(session, override, catalog)
    with Session(database) as session:
        loaded = load_manual_overrides(session, catalog)
    assert len(loaded) == 1
    assert loaded[0] == saved

    updated = ManualOverride("strategy", "tokens", "force_exclude", reason="Revisado")
    with Session(database) as session, session.begin():
        save_manual_override(session, updated, catalog)
    with Session(database) as session:
        loaded_updated = load_manual_overrides(session, catalog)
    assert len(loaded_updated) == 1
    assert loaded_updated[0].action == "force_exclude"

    with Session(database) as session, session.begin():
        assert remove_manual_override(session, "strategy", "tokens", catalog)
    with Session(database) as session:
        assert load_manual_overrides(session, catalog) == ()
        assert session.scalars(select(ManualOverrideRecord)).all() == []


def test_commander_affinity_profile_round_trips_replaces_and_clears(database, catalog) -> None:
    profile = CommanderAffinityProfile(
        commander_oracle_id="commander-id",
        strategy_affinities=(
            CommanderStrategyAffinity(
                "tokens",
                "primary",
                0.9,
                evidence_ids=("commander_rule_tokens",),
                rationale="Gera fichas repetidamente.",
            ),
        ),
        tag_affinities=(
            CommanderTagAffinity("create_tokens", rationale="Cria fichas."),
        ),
    )
    with Session(database) as session, session.begin():
        saved = save_commander_affinity_profile(session, profile, catalog)
    with Session(database) as session:
        loaded = load_commander_affinity_profile(session, "commander-id", catalog)
    assert saved == profile
    assert loaded == profile

    changed = CommanderAffinityProfile(
        commander_oracle_id="commander-id",
        strategy_affinities=(
            CommanderStrategyAffinity("aristocrats", "secondary", 0.6),
        ),
    )
    with Session(database) as session, session.begin():
        save_commander_affinity_profile(session, changed, catalog)
    with Session(database) as session:
        replaced = load_commander_affinity_profile(session, "commander-id", catalog)
        assert len(session.scalars(select(CommanderAffinityRecord)).all()) == 1
    assert replaced == changed

    with Session(database) as session, session.begin():
        save_commander_affinity_profile(
            session,
            CommanderAffinityProfile(commander_oracle_id="commander-id"),
            catalog,
        )
    with Session(database) as session:
        assert load_commander_affinity_profile(session, "commander-id", catalog) is None


def test_saving_commander_affinity_requires_an_existing_oracle_id(database, catalog) -> None:
    with Session(database) as session, session.begin():
        with pytest.raises(ValueError, match="Comandante desconhecido"):
            save_commander_affinity_profile(
                session,
                CommanderAffinityProfile(commander_oracle_id="missing-commander"),
                catalog,
            )


def test_configuration_hash_is_stable_and_changes_with_configuration(tmp_path: Path) -> None:
    config_dir = _copy_catalog(tmp_path)
    initial = catalog_configuration_hash(config_dir)
    assert catalog_configuration_hash(config_dir) == initial

    rules_path = config_dir / "classification_rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    rules["ruleset_version"] = "1.0.1"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")

    assert catalog_configuration_hash(config_dir) != initial


def test_reclassification_rejects_catalog_from_different_config_before_writing(
    database,
    catalog,
    tmp_path: Path,
) -> None:
    config_dir = _copy_catalog(tmp_path)
    strategy_path = config_dir / "strategy_catalog.json"
    strategies = json.loads(strategy_path.read_text(encoding="utf-8"))
    strategies["strategies"][0]["name"] = "Different display name"
    strategy_path.write_text(json.dumps(strategies), encoding="utf-8")
    with Session(database) as session, session.begin():
        with pytest.raises(ValueError, match="não corresponde aos arquivos"):
            reclassify_cards(session, catalog, config_dir)

    with Session(database) as session:
        assert session.scalars(select(ClassificationCatalogVersion)).all() == []
        assert session.scalars(select(ClassificationRun)).all() == []
