import json

from sqlalchemy.orm import Session

from mana_architect.candidate_evaluation import rank_collection_candidates
from mana_architect.cli import main
from mana_architect.database import create_database_engine, initialize_database
from mana_architect.models import Card, CollectionPrinting, SyncMetadata
from mana_architect.strategy_synergy import SelectedStrategy


def add_card(session, card_id, name, text, *, colors=("B",), legality="legal",
             type_line="Sorcery", mana_cost="{B}", quantity=1):
    session.add(Card(
        oracle_id=card_id, name=name, oracle_text=text, type_line=type_line,
        mana_cost=mana_cost, color_identity=list(colors),
        legalities={"commander": legality},
    ))
    session.flush()
    if quantity:
        session.add(CollectionPrinting(
            scryfall_id=f"printing-{card_id}", oracle_id=card_id, name=name,
            set_code="tst", collector_number="1", language="en", finish="nonfoil",
            quantity=quantity, condition="near_mint",
        ))


def seed(session):
    add_card(session, "commander", "Test Commander",
             "Whenever an artifact you control enters, draw a card.",
             type_line="Legendary Creature — Human")
    add_card(session, "treasure", "Treasure Generator", "Create two Treasure tokens.", quantity=3)
    add_card(session, "draw", "Draw Spell", "Draw two cards.")
    add_card(session, "removal", "Removal Spell", "Destroy target creature.")
    add_card(session, "unowned", "Unowned Treasure", "Create five Treasure tokens.", quantity=0)
    add_card(session, "off-color", "Off-Color Treasure", "Create two Treasure tokens.", colors=("R",))
    add_card(session, "banned", "Banned Treasure", "Create two Treasure tokens.", legality="banned")
    session.add(SyncMetadata(key="bulk_updated_at", value="2026-11-12"))


def test_collection_ranks_eligible_cards_and_returns_independent_profiles(tmp_path):
    engine = create_database_engine(tmp_path / "collection.db")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        seed(session)
    with Session(engine) as session:
        batch = rank_collection_candidates(
            session, "Test Commander", (SelectedStrategy("tokens"), SelectedStrategy("artifacts")), limit=3,
        )
    engine.dispose()
    assert batch.legality_data_as_of == "2026-11-12"
    assert batch.total_eligible == 3
    treasure, draw, removal = batch.ranked
    assert treasure.card_id == "treasure" and treasure.owned_quantity == 3
    assert treasure.ramp.best_line is not None
    assert treasure.strategy_synergy.commander_synergy.score == 1
    assert draw.to_json()["evaluations"]["card_advantage"]["best_strict_net_cards"] == 1
    assert removal.interaction.scores["spot"] is not None
    assert [(item.card_id, item.reasons) for item in batch.rejected] == [
        ("banned", ("candidate_banned",)), ("off-color", ("incompatible_color_identity",)),
    ]


def test_cli_accepts_weighted_strategies_and_outputs_json(tmp_path, monkeypatch, capsys):
    db = tmp_path / "collection.db"
    engine = create_database_engine(db)
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        seed(session)
    engine.dispose()
    monkeypatch.setenv("MANA_ARCHITECT_DB_PATH", str(db))
    assert main([
        "evaluate-candidates", "commander", "--strategy", "artifacts=3",
        "--strategy", "tokens", "--limit", "1",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["total_eligible"] == 3
    assert [item["id"] for item in result["selected_strategies"]] == ["artifacts", "tokens"]
    assert result["selected_strategies"][0]["priority"] == 0.75
    [top] = result["ranked"]
    assert top["card_id"] == "treasure"
    assert set(top["evaluations"]) == {"strategy_synergy", "ramp", "interaction", "card_advantage"}
    assert top["evaluations"]["strategy_synergy"]["evidence"]
