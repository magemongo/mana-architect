from dataclasses import FrozenInstanceError, replace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mana_architect.candidate_evaluation import rank_collection_candidates
from mana_architect.card_advantage import evaluate_cast
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS, EvaluationAdapters, EvaluationError
from mana_architect.deck_pipeline.contracts import (
    CandidatePool,
    CandidateProvenance,
    CandidateStatus,
    CardCandidate,
    DeckBuildingContext,
    DeckEntry,
    DeckProposal,
    DecisionRecord,
    LoopVerification,
    PairwiseSynergyEdge,
    PrintingAllocation,
    Role,
    RuleEvidence,
    SelectionReason,
    StrategyPreference,
    SynergyPackage,
)
from mana_architect.deck_pipeline.eligibility import RULESET_VERSION, EligibilityGate
from mana_architect.deck_pipeline.inventory import load_collection_snapshot
from mana_architect.models import Base, Card, CollectionPrinting, SyncMetadata
from mana_architect.strategy_synergy import SelectedStrategy, load_synergy_context


def _card(card_id, name, text="", *, identity=("B",), legality="legal", type_line="Sorcery"):
    return Card(
        oracle_id=card_id, name=name, oracle_text=text, mana_cost="{1}{B}",
        cmc=2, type_line=type_line, color_identity=list(identity) if identity is not None else None,
        legalities={"commander": legality},
    )


def _printing(card_id, quantity, *, finish="nonfoil"):
    return CollectionPrinting(
        scryfall_id=f"printing-{card_id}", oracle_id=card_id, name=card_id or "Unresolved",
        set_code="test", collector_number="1", language="en", condition="near_mint",
        finish=finish, quantity=quantity,
    )


@pytest.fixture
def collection():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all((
            _card(
                "leader", "Test Commander", "Whenever an artifact you control enters, draw a card.",
                type_line="Legendary Creature — Human",
            ),
            _card("treasure", "Treasure Maker", "Create two Treasure tokens."),
            _card("draw", "Fixed Draw", "Draw two cards."),
            _card("other-draw", "Fixed Draw", "Draw two cards."),
            _card("spot", "Spot Removal", "Destroy target creature."),
            _card("mass", "Mass Removal", "Destroy all creatures."),
            _card("off-color", "Off-Color Spell", identity=("R",)),
            _card("banned", "Banned Spell", legality="banned"),
            _card("unknown", "Unknown Spell", identity=None, legality=None),
            _card("unowned", "Unowned Spell"),
            _card("basic", "Swamp", "({T}: Add {B}.)", type_line="Basic Land — Swamp"),
            _card("typed", "Typed Land", identity=(), type_line="Land — Forest"),
            _card("unlimited", "Persistent Petitioners",
                  "A deck can have any number of cards named Persistent Petitioners."),
            _card("limited", "Seven Dwarves",
                  "A deck can have up to seven cards named Seven Dwarves."),
        ))
        session.flush()
        session.add_all((
            _printing("leader", 1), _printing("treasure", 2), _printing("treasure", 1, finish="foil"),
            _printing("draw", 1), _printing("other-draw", 1), _printing("spot", 1),
            _printing("mass", 1), _printing("off-color", 1), _printing("banned", 1),
            _printing("unknown", 1), _printing("basic", 99), _printing("typed", 1),
            _printing("unlimited", 12), _printing("limited", 8), _printing(None, 5),
            SyncMetadata(key="bulk_updated_at", value="2026-11-12"),
        ))
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def gate(collection):
    snapshot = load_collection_snapshot(collection)
    context = DeckBuildingContext(
        run_id="fixture-run",
        commander_card_ids=("leader",),
        collection_snapshot_id=snapshot.snapshot_id,
        ruleset_version=RULESET_VERSION,
        engine_versions=ENGINE_VERSIONS,
        config_version="fixture-v1",
        strategy_preferences=(StrategyPreference("tokens"), StrategyPreference("artifacts", 2)),
    )
    return EligibilityGate(context, snapshot)


@pytest.fixture
def synergy_context():
    return load_synergy_context()


def test_snapshot_keeps_distinct_rows_and_replays_in_canonical_order(collection, gate):
    snapshot = gate.snapshot
    printings = tuple(row for row in snapshot.printings if row.card_id == "treasure")
    assert printings[0].scryfall_id == printings[1].scryfall_id
    assert printings[0].finish != printings[1].finish
    result = gate.check_card("treasure")
    assert result.eligible
    assert result.owned_printing_ids == tuple(row.printing_id for row in printings)
    assert result.available_quantity == 3
    assert result.copy_limit == 1

    reversed_snapshot = replace(
        snapshot, cards=snapshot.cards[::-1], printings=snapshot.printings[::-1],
    )
    assert reversed_snapshot == snapshot
    with pytest.raises(FrozenInstanceError):
        snapshot.legality_data_as_of = None

    row = collection.scalar(select(CollectionPrinting).where(CollectionPrinting.id == printings[0].printing_id))
    row.quantity = 1
    collection.flush()
    changed_snapshot = load_collection_snapshot(collection)
    assert changed_snapshot.snapshot_id != snapshot.snapshot_id
    assert gate.check_card("treasure").available_quantity == 3
    assert gate.unlinked_printings[0].code == "unlinked_printing"
    assert gate.unlinked_printings[0].printing_id is not None


def test_empty_inventory_retains_legality_sync_date(collection):
    collection.query(CollectionPrinting).delete()
    snapshot = load_collection_snapshot(collection)
    assert snapshot.cards == snapshot.printings == ()
    assert snapshot.legality_data_as_of == "2026-11-12"


@pytest.mark.parametrize(("card_id", "codes"), [
    ("leader", ("card_is_commander",)),
    ("off-color", ("outside_color_identity",)),
    ("typed", ("outside_color_identity",)),
    ("banned", ("banned_card",)),
    ("unknown", ("unknown_card_legality", "unknown_color_identity")),
    ("unowned", ("card_not_owned",)),
])
def test_ineligible_cards_have_structured_reasons(gate, synergy_context, card_id, codes):
    result = gate.check_card(card_id)
    assert not result.eligible
    assert tuple(issue.code for issue in result.violations) == codes
    assert all(issue.card_id == card_id for issue in result.violations)
    with pytest.raises(EvaluationError) as caught:
        EvaluationAdapters(gate, synergy_context).evaluate(card_id)
    assert caught.value.violations == result.violations


@pytest.mark.parametrize(("ids", "code"), [
    ((), "invalid_commander_configuration"),
    (("leader", "leader"), "invalid_commander_configuration"),
    (("leader", "draw"), "unsupported_commander_configuration"),
    (("draw",), "ineligible_commander"),
    (("unowned",), "commander_not_owned"),
])
def test_gate_validates_commander_configuration(gate, ids, code):
    context = replace(gate.context, commander_card_ids=ids)
    assert context.main_deck_size == 100 - len(ids)
    rejected = EligibilityGate(context, gate.snapshot)
    assert code in {issue.code for issue in rejected.violations}
    assert not rejected.check_card("treasure").eligible


def test_gate_checks_snapshot_and_engine_versions(gate, synergy_context):
    with pytest.raises(ValueError, match="Unsupported deck pipeline schema"):
        replace(gate.context, schema_version="2.0")

    undated = replace(gate.snapshot, legality_data_as_of=None)
    different_gate = EligibilityGate(gate.context, undated)
    assert {issue.code for issue in different_gate.violations} == {"snapshot_mismatch", "missing_sync_date"}
    wrong_versions = replace(ENGINE_VERSIONS, ramp="unknown")
    bad_gate = EligibilityGate(replace(gate.context, engine_versions=wrong_versions), gate.snapshot)
    with pytest.raises(EvaluationError) as caught:
        EvaluationAdapters(bad_gate, synergy_context)
    assert caught.value.violations[0].code == "engine_version_mismatch"


def test_commander_permission_must_apply_to_the_front_face(gate):
    leader = next(card for card in gate.snapshot.cards if card.card_id == "leader")
    explicit_permission = "Test Commander can be your commander."
    permitted = replace(leader, type_line="Legendary Planeswalker", oracle_text=explicit_permission)
    back_face_only = replace(
        leader, type_line="Artifact // Legendary Creature — Human",
        oracle_text=f" // {explicit_permission}",
    )
    for card, issues in ((permitted, ()), (back_face_only, ("ineligible_commander",))):
        snapshot = replace(gate.snapshot, cards=tuple(
            card if item.card_id == "leader" else item for item in gate.snapshot.cards
        ))
        context = replace(gate.context, collection_snapshot_id=snapshot.snapshot_id)
        assert tuple(issue.code for issue in EligibilityGate(context, snapshot).violations) == issues


def test_adapters_match_all_existing_engine_profiles(collection, gate, synergy_context):
    settings = (SelectedStrategy("tokens"), SelectedStrategy("artifacts", 2))
    existing = rank_collection_candidates(collection, "leader", settings, context=synergy_context)
    existing_by_id = {item.card_id: item for item in existing.ranked}
    adapters = EvaluationAdapters(gate, synergy_context)
    for card_id in ("draw", "treasure", "spot", "mass"):
        native = existing_by_id[card_id]
        profiles = adapters.evaluate(card_id)
        assert profiles.ramp == native.ramp
        assert profiles.card_advantage == native.card_advantage
        assert profiles.interaction == native.interaction
        assert profiles.strategy_synergy == native.strategy_synergy
        assert profiles == adapters.evaluate(card_id)

    assert adapters.evaluate("treasure").ramp.best_line.score > 0
    assert adapters.evaluate("treasure").strategy_synergy.commander_synergy.evidence_ids
    [draw_line] = adapters.evaluate("draw").card_advantage.lines
    assert evaluate_cast(draw_line).self_delta == 1
    assert adapters.evaluate("spot").interaction.spot_interaction
    assert adapters.evaluate("mass").interaction.mass_interaction
    assert adapters.evaluate("spot").card_advantage.unsupported_reason


def _proposal(gate, *entries):
    return DeckProposal("fixture-deck", gate.context.run_id, gate.context.commander_card_ids, entries)


def test_proposal_counts_commander_basics_and_copy_exceptions(gate):
    proposal = _proposal(
        gate, DeckEntry("basic", 80), DeckEntry("unlimited", 12), DeckEntry("limited", 7),
    )
    assert not proposal.validation.is_legal
    assert gate.validate_proposal(proposal).is_legal
    too_many = replace(proposal, main_deck=(
        DeckEntry("basic", 79), DeckEntry("unlimited", 12), DeckEntry("limited", 8),
    ))
    [issue] = gate.validate_proposal(too_many).violations
    assert (issue.code, issue.card_id) == ("copy_limit", "limited")
    assert {"deck_size", "card_is_commander"} <= {
        issue.code for issue in gate.validate_proposal(_proposal(gate, DeckEntry("leader", 1))).violations
    }


def test_proposal_aggregates_across_rows_names_and_printing_allocations(gate):
    first, second = gate.check_card("treasure").owned_printing_ids
    proposal = _proposal(
        gate,
        DeckEntry("treasure", 2, (PrintingAllocation(first, 2),)),
        DeckEntry("treasure", 2, (PrintingAllocation(first, 1), PrintingAllocation(second, 1))),
        DeckEntry("draw", 1, (PrintingAllocation(first, 1),)),
        DeckEntry("other-draw", 1),
        DeckEntry("unowned", 1),
        DeckEntry("basic", True),
    )
    report = gate.validate_proposal(proposal)
    assert not report.is_legal
    assert report == gate.validate_proposal(replace(proposal, main_deck=proposal.main_deck[::-1]))
    assert {(issue.code, issue.card_id) for issue in report.violations} >= {
        ("copy_limit", "treasure"),
        ("copy_limit", "draw"),
        ("copy_limit", "other-draw"),
        ("insufficient_quantity", "treasure"),
        ("insufficient_printing_quantity", "treasure"),
        ("invalid_printing", "draw"),
        ("printing_allocation_mismatch", "draw"),
        ("card_not_owned", "unowned"),
        ("invalid_quantity", "basic"),
    }


def test_pool_contract_indexes_a_multirole_card_once(gate, synergy_context):
    eligibility = gate.check_card("treasure")
    profiles = EvaluationAdapters(gate, synergy_context).evaluate("treasure")
    reason = SelectionReason("role_coverage", Role.STRATEGY_SYNERGY,
                             profiles.strategy_synergy.commander_synergy.evidence_ids)
    candidate = CardCandidate(
        "treasure", eligibility.owned_printing_ids, eligibility.available_quantity, profiles,
        CandidateProvenance(gate.snapshot.snapshot_id, ENGINE_VERSIONS),
        roles=(Role.STRATEGY_SYNERGY, Role.RAMP), selection_reasons=(reason,),
    )
    pool = CandidatePool(gate.context.run_id, (candidate,))
    assert len(pool.candidates) == 1
    assert pool.role_memberships[Role.RAMP] == pool.role_memberships[Role.STRATEGY_SYNERGY] == ("treasure",)
    assert pool.role_memberships[Role.LANDS] == ()
    with pytest.raises(ValueError, match="identities must be unique"):
        replace(pool, reserve=(replace(candidate, status=CandidateStatus.RESERVE),))
    with pytest.raises(ValueError, match="IDs must match"):
        replace(candidate, card_id="draw")
    decision = DecisionRecord(
        1, candidate.card_id, "test", CandidateStatus.CANDIDATE, CandidateStatus.RESERVE, reason, True,
    )
    assert decision.reversible and decision.reason.evidence_ids


def test_edges_require_evidence_and_packages_require_loop_verification():
    evidence = RuleEvidence("generator", "ability-1", "test-rule")
    edge = PairwiseSynergyEdge(
        "edge", "generator", "payoff", "enables", "create_token", "death_payoff",
        (evidence,), 0.8, "conditional", prerequisites=("sacrifice_token",),
    )
    with pytest.raises(ValueError, match="rule evidence"):
        replace(edge, evidence=())
    with pytest.raises(ValueError, match="explicit prerequisites"):
        replace(edge, prerequisites=())

    package = SynergyPackage(
        "package", ("payoff", "generator"), (("generator", ("generator",)),),
        ("edge", "edge"), ("pay_mana",), True, False, "Requires additional mana.",
    )
    assert package.member_card_ids == ("generator", "payoff")
    assert package.edge_ids == ("edge",)
    assert not package.infinite
    verification = LoopVerification(True, True, True, (evidence,))
    verified = replace(package, self_sustaining=True, requirements=(), loop_verification=verification)
    assert verified.infinite
    assert not replace(verified, loop_verification=replace(verification, timing_and_rules_satisfied=False)).infinite
