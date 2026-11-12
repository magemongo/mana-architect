import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

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
from mana_architect.deckbuilding import (
    DeckBuildError,
    analyze_deck,
    build_deck,
    export_deck,
    export_decklist,
)
from mana_architect.database import create_database_engine, initialize_database
from mana_architect.models import (
    Card,
    ClassificationRun,
    CollectionPrinting,
    SyncMetadata,
)
from mana_architect.validation import DeckCard, load_deck_file


def add_card(
    session: Session,
    oracle_id: str,
    name: str,
    quantity: int,
    type_line: str,
    oracle_text: str | None = None,
    cmc: float | None = None,
    produced_mana: list[str] | None = None,
    color_identity: list[str] | None = None,
    image_uri: str | None = None,
) -> None:
    session.add(
        Card(
            oracle_id=oracle_id,
            name=name,
            mana_cost=None,
            cmc=cmc,
            colors=[],
            color_identity=(
                color_identity
                if color_identity is not None
                else ["U"] if oracle_id == "commander" else []
            ),
            produced_mana=produced_mana,
            type_line=type_line,
            oracle_text=oracle_text,
            keywords=[],
            legalities={"commander": "legal"},
            image_uri=image_uri,
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
    with_spell: bool = False,
    island_quantity: int = 99,
):
    engine = create_database_engine(path)
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        session.add(
            SyncMetadata(
                key="bulk_updated_at",
                value="2026-11-11T10:06:45.392+00:00",
            )
        )
        add_card(
            session,
            "commander",
            "Blue Captain",
            1,
            "Legendary Creature — Human",
        )
        add_card(session, "island", "Island", island_quantity, "Basic Land — Island")
        if with_spell:
            add_card(
                session,
                "draw-spell",
                "Helpful Insight",
                1,
                "Sorcery",
                "Draw two cards.",
                cmc=2,
            )
    return engine


def make_catalog_deck_database(
    path: Path,
    *,
    classified_ids: tuple[str, ...] = ("island", "token-card", "plain-card"),
    with_affinity: bool = True,
):
    engine = make_deck_database(path)
    with Session(engine) as session, session.begin():
        add_card(
            session,
            "token-card",
            "Z Token Plan",
            1,
            "Sorcery",
            "Create a token.",
            cmc=1,
        )
        add_card(
            session,
            "plain-card",
            "A Plain Plan",
            1,
            "Sorcery",
            "Scry 1.",
            cmc=1,
        )

    catalog = load_strategy_catalog()
    with Session(engine) as session, session.begin():
        if with_affinity:
            save_commander_affinity_profile(
                session,
                CommanderAffinityProfile(
                    commander_oracle_id="commander",
                    strategy_affinities=(
                        CommanderStrategyAffinity("tokens", "primary", 0.8),
                    ),
                    tag_affinities=(CommanderTagAffinity("create_tokens"),),
                ),
                catalog,
            )
        if classified_ids:
            reclassify_cards(
                session,
                catalog,
                DEFAULT_CATALOG_DIR,
                classified_ids,
            )
    return engine, catalog


def test_build_deck_uses_only_owned_cards_and_validates_draft(tmp_path: Path) -> None:
    engine = make_deck_database(tmp_path / "build.db", with_spell=True)
    with Session(engine) as session:
        result = build_deck(session, commander_identifier="commander", land_count=98)
    engine.dispose()

    card_quantities = {card.oracle_id: card.quantity for card in result.cards}
    assert sum(card_quantities.values()) == 100
    assert card_quantities == {
        "commander": 1,
        "draw-spell": 1,
        "island": 98,
    }
    assert result.land_count == 98
    assert result.validation.status == "valid"
    assert result.role_counts["Compra de cartas (heurística)"] == 1


def test_catalog_strategy_prioritizes_cards_without_changing_legacy_mode(
    tmp_path: Path,
) -> None:
    engine, _ = make_catalog_deck_database(tmp_path / "catalog-guided.db")
    with Session(engine) as session:
        legacy_result = build_deck(
            session,
            commander_identifier="commander",
            land_count=98,
        )
        catalog_result = build_deck(
            session,
            commander_identifier="commander",
            land_count=98,
            strategy_id="tokens",
        )
        repeated_result = build_deck(
            session,
            commander_identifier="commander",
            land_count=98,
            strategy_id="tokens",
        )
    engine.dispose()

    legacy_ids = {card.oracle_id for card in legacy_result.cards}
    catalog_ids = {card.oracle_id for card in catalog_result.cards}
    assert "plain-card" in legacy_ids
    assert "token-card" not in legacy_ids
    assert "token-card" in catalog_ids
    assert "plain-card" not in catalog_ids
    assert catalog_result.catalog_strategy_id == "tokens"
    assert catalog_result.catalog_strategy_name == "Tokens"
    assert catalog_result.validation.status == "valid"
    assert repeated_result.cards == catalog_result.cards

    output = tmp_path / "catalog-guided.json"
    export_deck(catalog_result, output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["draft"]["catalog_strategy"] == {
        "id": "tokens",
        "name": "Tokens",
    }


def test_catalog_strategy_requires_explicit_commander_affinity(
    tmp_path: Path,
) -> None:
    engine, _ = make_catalog_deck_database(
        tmp_path / "missing-affinity.db",
        with_affinity=False,
        classified_ids=(),
    )
    with Session(engine) as session:
        with pytest.raises(DeckBuildError, match="Cadastre a afinidade"):
            build_deck(
                session,
                commander_identifier="commander",
                land_count=98,
                strategy_id="tokens",
            )
    engine.dispose()


def test_catalog_generation_reports_unclassified_cards_without_persisting_them(
    tmp_path: Path,
) -> None:
    engine, _ = make_catalog_deck_database(
        tmp_path / "unclassified.db",
        classified_ids=("island", "token-card"),
    )
    with Session(engine) as session:
        result = build_deck(
            session,
            commander_identifier="commander",
            land_count=98,
            strategy_id="tokens",
        )
        unclassified_runs = session.scalars(
            select(ClassificationRun).where(
                ClassificationRun.oracle_id == "plain-card"
            )
        ).all()
    engine.dispose()

    assert "token-card" in {card.oracle_id for card in result.cards}
    assert result.unclassified_count == 1
    assert unclassified_runs == []


def test_build_deck_requires_sufficient_physical_land_copies(tmp_path: Path) -> None:
    engine = make_deck_database(
        tmp_path / "not-enough-lands.db",
        with_spell=True,
        island_quantity=1,
    )
    with Session(engine) as session:
        with pytest.raises(DeckBuildError, match="apenas 1"):
            build_deck(session, commander_identifier="commander", land_count=39)
    engine.dispose()


def test_build_deck_fails_instead_of_exporting_an_incomplete_list(
    tmp_path: Path,
) -> None:
    engine = make_deck_database(tmp_path / "not-enough-spells.db")
    with Session(engine) as session:
        with pytest.raises(DeckBuildError, match="cartas não-terreno"):
            build_deck(session, commander_identifier="commander", land_count=98)
    engine.dispose()


def test_build_deck_respects_textual_copy_exceptions_and_inventory(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "copy-exceptions.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        session.add(
            SyncMetadata(
                key="bulk_updated_at",
                value="2026-11-11T10:06:45.392+00:00",
            )
        )
        add_card(
            session,
            "commander",
            "Blue Captain",
            1,
            "Legendary Creature — Human",
        )
        add_card(session, "island", "Island", 99, "Basic Land — Island")
        add_card(
            session,
            "rats",
            "Relentless Rats",
            4,
            "Creature — Rat",
            "A deck can have any number of cards named Relentless Rats.",
        )
        add_card(
            session,
            "dwarves",
            "Seven Dwarves",
            7,
            "Creature — Dwarf",
            "A deck can have up to seven cards named Seven Dwarves.",
        )

    with Session(engine) as session:
        result = build_deck(session, commander_identifier="commander", land_count=88)
    engine.dispose()

    card_quantities = {card.oracle_id: card.quantity for card in result.cards}
    assert card_quantities["rats"] == 4
    assert card_quantities["dwarves"] == 7
    assert sum(card_quantities.values()) == 100
    assert result.validation.status == "valid"


def test_exported_deck_is_readable_by_validator_and_not_overwritten_silently(
    tmp_path: Path,
) -> None:
    engine = make_deck_database(tmp_path / "export.db", with_spell=True)
    with Session(engine) as session:
        result = build_deck(session, commander_identifier="commander", land_count=98)
    engine.dispose()

    output = tmp_path / "decks" / "draft.json"
    export_deck(result, output)
    commander, cards = load_deck_file(output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert commander == "commander"
    assert sum(card.quantity for card in cards) == 100
    assert payload["draft"]["validation_status"] == "valid"
    with pytest.raises(FileExistsError, match="já existe"):
        export_deck(result, output)
    export_deck(result, output, overwrite=True)


def test_analyze_deck_reports_names_curve_roles_and_exports_text(
    tmp_path: Path,
) -> None:
    engine = make_deck_database(tmp_path / "analysis.db", with_spell=True)
    cards = [DeckCard("commander", 1), DeckCard("island", 98), DeckCard("draw-spell", 1)]
    with Session(engine) as session:
        analysis = analyze_deck(session, "commander", cards)
    engine.dispose()

    output = tmp_path / "deck.txt"
    export_decklist(analysis, output)
    contents = output.read_text(encoding="utf-8")

    assert analysis.total_cards == 100
    assert analysis.land_count == 98
    assert analysis.nonland_count == 2
    assert analysis.mana_curve == {2: 1}
    assert analysis.role_counts["Compra de cartas (heurística)"] == 1
    assert analysis.validation.status == "valid"
    assert "# Commander: Blue Captain" in contents
    assert "1 Blue Captain" in contents
    assert "# Main deck" in contents
    assert "98 Island" in contents
    assert "1 Helpful Insight" in contents
    with pytest.raises(FileExistsError, match="já existe"):
        export_decklist(analysis, output)
    export_decklist(analysis, output, overwrite=True)


def test_anowon_profile_prioritizes_rogue_and_exposes_strategy_signals(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "anowon.db")
    initialize_database(engine)
    anowon_id = "e7e69583-8326-417e-8a9a-6b917c8ba4b6"
    with Session(engine) as session, session.begin():
        session.add(
            SyncMetadata(
                key="bulk_updated_at",
                value="2026-11-11T10:06:45.392+00:00",
            )
        )
        add_card(
            session,
            anowon_id,
            "Anowon, the Ruin Thief",
            1,
            "Legendary Creature — Vampire Rogue",
            "Other Rogues you control get +1/+1.",
            color_identity=["U", "B"],
        )
        add_card(
            session,
            "island",
            "Island",
            100,
            "Basic Land — Island",
            produced_mana=["U"],
            image_uri="https://example.test/island.jpg",
        )
        add_card(
            session,
            "swamp",
            "Swamp",
            100,
            "Basic Land — Swamp",
            produced_mana=["B"],
        )
        add_card(
            session,
            "rogue-card",
            "Rogue Striker",
            1,
            "Creature — Rogue",
            "Whenever this deals combat damage to a player, that player mills a card.",
            cmc=2,
            image_uri="https://example.test/rogue.jpg",
        )
        add_card(
            session,
            "generic-card",
            "Generic Insight",
            1,
            "Sorcery",
            "Draw two cards.",
            cmc=2,
        )

    with Session(engine) as session:
        result = build_deck(session, anowon_id, land_count=98)
        analysis = analyze_deck(session, anowon_id, list(result.cards))
    engine.dispose()

    card_ids = {card.oracle_id for card in result.cards}
    assert "rogue-card" in card_ids
    assert "generic-card" not in card_ids
    rogue_details = next(card for card in analysis.cards if card.oracle_id == "rogue-card")
    assert rogue_details.strategy_signals == (
        "Tribal Rogue",
        "Mill",
        "Dano de combate",
    )
    assert rogue_details.image_uri == "https://example.test/rogue.jpg"
    assert analysis.strategy_name == "Anowon, the Ruin Thief"
    assert analysis.strategy_themes == ("Rogues tribal", "mill por dano de combate")


def test_land_selection_uses_dual_sources_and_colorless_utility_land(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "mana-base.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        session.add(
            SyncMetadata(
                key="bulk_updated_at",
                value="2026-11-11T10:06:45.392+00:00",
            )
        )
        add_card(
            session,
            "commander",
            "Dimir Captain",
            1,
            "Legendary Creature — Human",
            color_identity=["U", "B"],
        )
        add_card(
            session,
            "island",
            "Island",
            99,
            "Basic Land — Island",
            produced_mana=["U"],
        )
        add_card(
            session,
            "swamp",
            "Swamp",
            99,
            "Basic Land — Swamp",
            produced_mana=["B"],
        )
        add_card(
            session,
            "dual",
            "Dimir Guildgate",
            1,
            "Land — Gate",
            "This land enters tapped.\n{T}: Add {U} or {B}.",
            produced_mana=["U", "B"],
        )
        add_card(
            session,
            "utility",
            "Rogue's Passage",
            1,
            "Land",
            "{T}: Add {C}.\n{4}, {T}: Target creature can't be blocked this turn.",
            produced_mana=["C"],
        )
        add_card(
            session,
            "spell",
            "Test Spell",
            1,
            "Sorcery",
            "Draw a card.",
        )

    with Session(engine) as session:
        result = build_deck(session, "commander", land_count=98)
    engine.dispose()

    card_quantities = {card.oracle_id: card.quantity for card in result.cards}
    assert card_quantities["dual"] == 1
    assert card_quantities["utility"] == 1
    assert card_quantities["island"] + card_quantities["swamp"] == 96
    assert sum(card_quantities.values()) == 100
