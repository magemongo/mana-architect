from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from mana_architect.catalog import DEFAULT_CATALOG_DIR, load_strategy_catalog
from mana_architect.classification_repository import (
    reclassify_cards,
    save_card_classification_override,
    save_commander_affinity_profile,
)
from mana_architect.commander_affinity import (
    CommanderAffinityProfile,
    CommanderStrategyAffinity,
    CommanderTagAffinity,
)
from mana_architect.database import create_database_engine, initialize_database
from mana_architect.overrides import ManualOverride
from mana_architect.models import Card, CollectionPrinting
from mana_architect.suggestions import suggest_collection_cards


@pytest.fixture
def catalog():
    return load_strategy_catalog()


@pytest.fixture
def database(tmp_path: Path):
    engine = create_database_engine(tmp_path / "suggestions.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                Card(
                    oracle_id="commander-id",
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
                    oracle_id="token-card-id",
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
                Card(
                    oracle_id="plain-card-id",
                    name="Plain Spell",
                    mana_cost="{G}",
                    cmc=1,
                    colors=["G"],
                    color_identity=["G"],
                    produced_mana=None,
                    type_line="Instant",
                    oracle_text="Draw a card.",
                    keywords=[],
                    legalities={"commander": "legal"},
                    image_uri=None,
                    scryfall_updated_at="2026-11-11T12:00:00Z",
                ),
                Card(
                    oracle_id="off-color-id",
                    name="Off Color Spell",
                    mana_cost="{R}",
                    cmc=1,
                    colors=["R"],
                    color_identity=["R"],
                    produced_mana=None,
                    type_line="Instant",
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
                _printing("commander-id", "Token Commander", 1),
                _printing("token-card-id", "Token Spell", 2),
                _printing("plain-card-id", "Plain Spell", 1),
                _printing("off-color-id", "Off Color Spell", 1),
            ]
        )
    yield engine
    engine.dispose()


def _printing(oracle_id: str, name: str, quantity: int) -> CollectionPrinting:
    return CollectionPrinting(
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


def _configure_catalog_and_affinity(database, catalog) -> None:
    profile = CommanderAffinityProfile(
        commander_oracle_id="commander-id",
        strategy_affinities=(
            CommanderStrategyAffinity("tokens", "primary", 0.8),
        ),
        tag_affinities=(CommanderTagAffinity("create_tokens"),),
    )
    with Session(database) as session, session.begin():
        save_commander_affinity_profile(session, profile, catalog)
        reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR)


def test_suggestions_rank_only_classified_owned_color_legal_candidates(
    database,
    catalog,
) -> None:
    _configure_catalog_and_affinity(database, catalog)

    with Session(database) as session:
        result = suggest_collection_cards(
            session,
            "commander-id",
            "tokens",
            catalog,
        )

    assert result.total_candidates == 3
    assert result.unclassified_count == 0
    assert result.ineligible_count == 1
    assert [item.score.card_oracle_id for item in result.suggestions] == [
        "token-card-id",
        "plain-card-id",
    ]
    assert result.suggestions[0].score.available_quantity == 2
    assert result.suggestions[0].classification.evidence


def test_suggestions_respect_current_deck_copies_and_required_roles(
    database,
    catalog,
) -> None:
    _configure_catalog_and_affinity(database, catalog)

    with Session(database) as session:
        result = suggest_collection_cards(
            session,
            "commander-id",
            "tokens",
            catalog,
            required_roles=("token_generator",),
            already_used={"token-card-id": 1},
        )

    assert "token-card-id" not in {
        item.score.card_oracle_id for item in result.suggestions
    }
    assert result.ineligible_count == 2


def test_suggestions_apply_manual_correction_without_erasing_auto_evidence(
    database,
    catalog,
) -> None:
    _configure_catalog_and_affinity(database, catalog)
    with Session(database) as session, session.begin():
        save_card_classification_override(
            session,
            "token-card-id",
            ManualOverride(
                "tag",
                "create_tokens",
                "force_exclude",
                reason="A ficha não é o plano principal.",
            ),
            catalog,
        )

    with Session(database) as session:
        result = suggest_collection_cards(
            session,
            "commander-id",
            "tokens",
            catalog,
        )
    token_suggestion = next(
        item
        for item in result.suggestions
        if item.score.card_oracle_id == "token-card-id"
    )

    assert token_suggestion.score.components["shared_synergy_tags"] == 0
    assert any(
        item.target_id == "create_tokens"
        for item in token_suggestion.classification.evidence
    )
    assert token_suggestion.overrides[0].reason == (
        "A ficha não é o plano principal."
    )


def test_suggestions_report_cards_without_current_classification(
    database,
    catalog,
) -> None:
    with Session(database) as session, session.begin():
        save_commander_affinity_profile(
            session,
            CommanderAffinityProfile(
                commander_oracle_id="commander-id",
                strategy_affinities=(
                    CommanderStrategyAffinity("tokens", "primary", 0.8),
                ),
                tag_affinities=(CommanderTagAffinity("create_tokens"),),
            ),
            catalog,
        )
    with Session(database) as session:
        result = suggest_collection_cards(
            session,
            "commander-id",
            "tokens",
            catalog,
        )

    assert result.unclassified_count == 3
    assert result.suggestions == ()


def test_suggestions_require_explicit_commander_affinity(database, catalog) -> None:
    with pytest.raises(ValueError, match="Cadastre a afinidade"):
        with Session(database) as session:
            suggest_collection_cards(
                session,
                "commander-id",
                "tokens",
                catalog,
            )
