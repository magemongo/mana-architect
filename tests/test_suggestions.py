from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from mana_architect.candidate_evaluation import rank_collection_candidates
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
from mana_architect.suggestions import (
    suggest_collection_cards,
    suggest_collection_cards_by_identity,
)
from mana_architect.strategy_synergy import SelectedStrategy


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


def test_identity_suggestions_need_no_commander_and_filter_by_color_and_strategy(
    database,
    catalog,
) -> None:
    with Session(database) as session, session.begin():
        reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR)

    with Session(database) as session:
        result = suggest_collection_cards_by_identity(
            session,
            ("G",),
            ("tokens", "spellslinger"),
            catalog,
        )

    assert result.color_identity == ("G",)
    assert result.strategy_ids == ("tokens", "spellslinger")
    assert result.total_candidates == 3
    assert result.unclassified_count == 0
    assert [item.oracle_id for item in result.suggestions] == [
        "token-card-id",
        "plain-card-id",
    ]
    assert result.suggestions[0].strategy_scores["tokens"] > 0
    assert [item.score for item in result.suggestions] == sorted(
        (item.score for item in result.suggestions), reverse=True
    )


def test_commander_suggestions_use_same_synergy_as_candidate_ranking_without_saved_classifications(database, catalog):
    with Session(database) as session, session.begin():
        session.get(Card, "commander-id").oracle_text = "Whenever a creature token you control enters, draw a card."
        session.get(Card, "token-card-id").oracle_text = "Create a 1/1 green Elf creature token."

    with Session(database) as session:
        weighted = (SelectedStrategy("tokens"), SelectedStrategy("aristocrats", 2))
        cli = rank_collection_candidates(session, "commander-id", weighted)
        suggestions = suggest_collection_cards_by_identity(
            session, (), ("tokens", "aristocrats"), catalog, commander_identifier="commander-id",
            strategy_priorities={"aristocrats": 2},
        )
        direct_only = suggest_collection_cards_by_identity(
            session, (), (), catalog, commander_identifier="commander-id",
        )

    assert suggestions.color_identity == ("G",)
    assert suggestions.unclassified_count == 2
    assert [item.oracle_id for item in suggestions.suggestions] == ["token-card-id"]
    item = suggestions.suggestions[0]
    assert item.mechanical_synergy == cli.ranked[0].strategy_synergy
    assert item.score == cli.ranked[0].strategy_synergy.scores.overall
    assert item.mechanical_synergy.commander_synergy.score == 1
    assert direct_only.suggestions[0].oracle_id == item.oracle_id
    assert not direct_only.suggestions[0].strategy_scores


def test_identity_suggestions_allow_colorless_and_optional_role_priorities(
    database,
    catalog,
) -> None:
    with Session(database) as session, session.begin():
        card = Card(
            oracle_id="colorless-card-id",
            name="Colorless Token Source",
            mana_cost="{2}",
            cmc=2,
            colors=[],
            color_identity=[],
            produced_mana=None,
            type_line="Artifact",
            oracle_text=(
                "Whenever an artifact enters the battlefield under your control, "
                "create a Treasure token."
            ),
            keywords=[],
            legalities={"commander": "legal"},
            image_uri=None,
            scryfall_updated_at="2026-11-11T12:00:00Z",
        )
        session.add(card)
        session.add(_printing("colorless-card-id", card.name, 1))
        reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR)

    with Session(database) as session:
        result = suggest_collection_cards_by_identity(
            session,
            (),
            ("tokens", "artifacts"),
            catalog,
            prioritized_roles=("ramp",),
        )

    assert result.color_identity == ()
    assert result.total_candidates == 1
    assert [item.oracle_id for item in result.suggestions] == ["colorless-card-id"]
    assert set(result.suggestions[0].matching_strategy_ids) == {"tokens", "artifacts"}
    assert "ramp" in result.suggestions[0].role_ids


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
            required_roles=("card_advantage",),
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
    with pytest.raises(ValueError, match="Add a commander affinity"):
        with Session(database) as session:
            suggest_collection_cards(
                session,
                "commander-id",
                "tokens",
                catalog,
            )


def test_strict_card_advantage_suggestions_rank_without_strategy(database, catalog):
    cards = (
        ("divination", "Divination", "Sorcery", "Draw two cards.", "G", "legal"),
        ("harmonize", "Harmonize", "Sorcery", "Draw three cards.", "G", "legal"),
        ("engine", "Conditional Engine", "Enchantment",
         "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.",
         "G", "legal"),
        ("token-engine", "Token Engine", "Artifact",
         "{T}: Draw a card. Activate only if you created a token this turn.\n"
         "{8}, {T}: Create a token.", "G", "legal"),
        ("exile-filter", "Exile Filter", "Artifact",
         "{T}: Draw a card, then exile a card from your hand.", "G", "legal"),
        ("reminder-draw", "Reminder Draw", "Instant",
         "Target creature gains flying. (Whenever a creature attacks, draw a card.)", "G", "legal"),
        ("banned-draw", "Banned Draw", "Sorcery", "Draw five cards.", "G", "banned"),
        ("red-draw", "Red Draw", "Sorcery", "Draw five cards.", "R", "legal"),
        ("unowned-draw", "Unowned Draw", "Sorcery", "Draw five cards.", "G", "legal"),
    )
    with Session(database) as session, session.begin():
        for oracle_id, name, type_line, text, color, legality in cards:
            session.add(Card(
                oracle_id=oracle_id, name=name, type_line=type_line, oracle_text=text,
                mana_cost="{3}", color_identity=[color], legalities={"commander": legality},
            ))
            if oracle_id != "unowned-draw":
                session.add(_printing(oracle_id, name, 1))
        reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR)
    with Session(database) as session:
        for strategies in ((), ("tokens",)):
            result = suggest_collection_cards_by_identity(
                session, ("G",), strategies, catalog, prioritized_roles=("card_advantage",)
            )
            assert result.strategy_ids == strategies
            ranked = {item.name: item for item in result.suggestions}
            assert set(ranked) == {"Harmonize", "Divination", "Conditional Engine", "Token Engine"}
            assert ranked["Harmonize"].strict_card_advantage == 2
            assert ranked["Divination"].score == 1
            assert ranked["Conditional Engine"].strict_card_advantage is None
            assert ranked["Conditional Engine"].score == 1
            token_engine = ranked["Token Engine"]
            assert token_engine.strict_card_advantage is None
            if strategies:
                assert token_engine.score > ranked["Divination"].score
                assert token_engine.strategy_scores["tokens"] > 0
                assert result.suggestions.index(token_engine) < result.suggestions.index(ranked["Divination"])
            else:
                assert token_engine.score == 1
                assert not token_engine.strategy_scores


def test_counter_payoffs_rank_as_conditional_card_advantage(database, catalog):
    cards = {
        "Armorcraft Judge": "When this creature enters, draw a card for each creature you control with a +1/+1 counter on it.",
        "Cloaked Cadet": "Whenever one or more +1/+1 counters are put on one or more Humans you control, draw a card.",
        "Terrasymbiosis": "Whenever you put one or more +1/+1 counters on a creature you control, you may draw that many cards.",
        "Agonasaur Rex": "Cycling {2}{G} ({2}{G}, Discard this card: Draw a card.)\nWhen you cycle this card, put two +1/+1 counters on target creature.",
    }
    with Session(database) as session, session.begin():
        for name, oracle_text in cards.items():
            session.add(Card(
                oracle_id=name, name=name, type_line="Creature", oracle_text=oracle_text,
                mana_cost="{3}{G}", color_identity=["G"], legalities={"commander": "legal"},
            ))
            session.add(_printing(name, name, 1))
        reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR)
    with Session(database) as session:
        result = suggest_collection_cards_by_identity(
            session, ("G",), ("plus_one_counters",), catalog,
            prioritized_roles=("card_advantage",),
        )
    assert {item.name for item in result.suggestions} == set(cards) - {"Agonasaur Rex"}
    for item in result.suggestions:
        assert item.strict_card_advantage is None
        assert item.strategy_scores["plus_one_counters"] > 0


def test_ramp_suggestions_use_the_profile_and_optional_strategy_bonus(database, catalog):
    cards = (
        ("sol-ring", "Artifact", "{1}", "{T}: Add {C}{C}.", (), "legal"),
        ("dark-ritual", "Instant", "{B}", "Add {B}{B}{B}.", ("B",), "legal"),
        ("neutral-ritual", "Instant", "{B}", "Add {B}.", ("B",), "legal"),
        ("fixing", "Artifact", "{1}", "{1}, {T}: Add one mana of any color.", (), "legal"),
        ("forest", "Basic Land — Forest", "", "{T}: Add {G}.", ("G",), "legal"),
        ("exploration", "Enchantment", "{G}", "You may play an additional land on each of your turns.", ("G",), "legal"),
        ("nature-lore", "Sorcery", "{1}{G}", "Search your library for a Forest card, put it onto the battlefield, then shuffle.", ("G",), "legal"),
        ("treasure", "Instant", "{2}", "Create two Treasure tokens.", (), "legal"),
        ("conditional", "Creature — Elf", "{1}{G}", "{T}: Add {G} for each Elf on the battlefield.", ("G",), "legal"),
        ("banned", "Artifact", "{0}", "{T}: Add {C}{C}.", (), "banned"),
        ("off-color", "Creature", "{R}", "{T}: Add {R}.", ("R",), "legal"),
        ("unowned", "Artifact", "{0}", "{T}: Add {C}{C}.", (), "legal"),
        ("unclassified", "Artifact", "{0}", "{T}: Add {C}{C}.", (), "legal"),
        ("manual", "Enchantment", "{1}", "Spells you cast cost {1} less to cast.", (), "legal"),
        ("excluded", "Artifact", "{0}", "{T}: Add {C}{C}.", (), "legal"),
    )
    with Session(database) as session, session.begin():
        for oracle_id, type_line, cost, text, colors, legality in cards:
            session.add(Card(
                oracle_id=oracle_id, name=oracle_id, type_line=type_line, oracle_text=text,
                mana_cost=cost, color_identity=list(colors), legalities={"commander": legality},
            ))
            if oracle_id != "unowned":
                session.add(_printing(oracle_id, oracle_id, 1))
        reclassify_cards(
            session, catalog, DEFAULT_CATALOG_DIR,
            tuple(card[0] for card in cards if card[0] != "unclassified"),
        )
        for oracle_id, action in (("excluded", "force_exclude"), ("manual", "force_include")):
            save_card_classification_override(
                session, oracle_id, ManualOverride("role", "ramp", action, reason="Reviewed"), catalog,
            )

    with Session(database) as session:
        standalone = suggest_collection_cards_by_identity(
            session, ("G", "B"), (), catalog, prioritized_roles=("ramp",),
        )
        ranked = suggest_collection_cards_by_identity(
            session, ("G", "B"), ("tokens",), catalog, prioritized_roles=("ramp",),
        )

    by_id = {item.oracle_id: item for item in standalone.suggestions}
    assert set(by_id) == {
        "sol-ring", "dark-ritual", "exploration", "nature-lore", "treasure", "conditional", "manual",
    }
    assert standalone.unclassified_count >= 1
    assert standalone.suggestions[0].oracle_id == "sol-ring"
    assert by_id["dark-ritual"].ramp_profile.best_line.net_immediate_mana == 2
    assert by_id["conditional"].ramp_profile.best_line.event.quantity.value is None
    assert by_id["conditional"].ramp_profile.best_line.conditional
    assert by_id["manual"].ramp_profile.best_line is None
    assert by_id["manual"].score == 1
    assert all("ramp" in item.role_ids for item in standalone.suggestions)
    with_strategy = {item.oracle_id: item for item in ranked.suggestions}
    assert set(with_strategy) == set(by_id)
    treasure = with_strategy["treasure"]
    assert treasure.strategy_scores["tokens"] > 0
    assert treasure.score == round(by_id["treasure"].score + treasure.strategy_scores["tokens"] / 100, 3)


def test_interaction_suggestions_use_independent_scopes_and_optional_strategy_bonus(database, catalog):
    cards = (
        ("rift", "{1}{U}", "Return target nonland permanent you don't control to its owner's hand.\nOverload {6}{U}", "U", "legal"),
        ("crypt", "{0}", "{T}, Sacrifice this artifact: Exile target player's graveyard.", "", "legal"),
        ("counter", "{U}", "Counter target spell.", "U", "legal"),
        ("token-counter", "{U}", "Counter target spell. Create a token.", "U", "legal"),
        ("variable-cost", "{X}{U}", "Counter target spell unless its controller pays {X}.", "U", "legal"),
        ("blink", "{G}", "Exile target creature you control, then return it to the battlefield under its owner's control.", "G", "legal"),
        ("unresolved", "{X}{U}", "Exile up to X target creatures.", "U", "legal"),
        ("banned", "{U}", "Counter target spell.", "U", "banned"),
        ("off-color", "{R}", "Counter target spell.", "R", "legal"),
        ("unowned", "{U}", "Counter target spell.", "U", "legal"),
        ("unclassified", "{U}", "Counter target spell.", "U", "legal"),
        ("manual", "{G}", "Scry 1.", "G", "legal"),
    )
    with Session(database) as session, session.begin():
        for oracle_id, cost, oracle_text, color, legality in cards:
            session.add(Card(
                oracle_id=oracle_id, name=oracle_id, mana_cost=cost, oracle_text=oracle_text,
                type_line="Artifact" if oracle_id == "crypt" else "Instant",
                color_identity=list(color), legalities={"commander": legality},
            ))
            if oracle_id != "unowned":
                session.add(_printing(oracle_id, oracle_id, 1))
        reclassify_cards(session, catalog, DEFAULT_CATALOG_DIR,
                         tuple(card[0] for card in cards if card[0] != "unclassified"))
        for oracle_id, action in (("manual", "force_include"), ("rift", "force_exclude")):
            save_card_classification_override(
                session, oracle_id, ManualOverride("role", "spot_interaction", action, reason="Reviewed"), catalog,
            )

    with Session(database) as session:
        spot = suggest_collection_cards_by_identity(
            session, ("U", "G"), (), catalog, prioritized_roles=("spot_interaction",),
        )
        with_strategy = suggest_collection_cards_by_identity(
            session, ("U", "G"), ("tokens",), catalog, prioritized_roles=("spot_interaction",),
        )
        mass = suggest_collection_cards_by_identity(
            session, ("U", "G"), (), catalog, prioritized_roles=("mass_interaction",),
        )

    assert spot.unclassified_count >= 1
    assert spot.ineligible_count == 1
    assert [item.oracle_id for item in spot.suggestions] == ["counter", "token-counter", "manual", "variable-cost"]
    assert [item.oracle_id for item in with_strategy.suggestions[:2]] == ["token-counter", "counter"]
    token_counter = with_strategy.suggestions[0]
    assert token_counter.score == round(spot.suggestions[1].score + token_counter.strategy_scores["tokens"] / 20, 3)
    assert spot.suggestions[2].score == spot.suggestions[3].score == 1.0
    assert spot.suggestions[2].interaction_profile.best_variant("spot") is None
    assert spot.suggestions[3].interaction_profile.best_variant("spot").spot_score.total is None
    assert {item.oracle_id for item in mass.suggestions} == {"crypt", "rift"}
    for item in mass.suggestions:
        assert item.score == item.interaction_profile.scores["mass"]
        assert "spot_interaction" not in item.role_ids
    rift = next(item for item in mass.suggestions if item.oracle_id == "rift")
    assert rift.interaction_profile.best_variant("mass").cost == "{6}{U}"
    assert rift.interaction_profile.best_variant("spot").cost == "{1}{U}"
