from collections import Counter
from dataclasses import replace
import json

from sqlalchemy.orm import Session

from mana_architect.cli import main
from mana_architect.database import create_database_engine, initialize_database
from mana_architect.deck_pipeline.candidate_pool import PoolConstraints
from mana_architect.deck_pipeline.composition_utility import CompositionConfig
from mana_architect.deck_pipeline.eligibility import EligibilityGate
from mana_architect.deck_pipeline.preliminary_composer import PreliminaryDeckComposer, proposal_identity
from mana_architect.deck_pipeline.refinement import DeckRefinementEngine, RefinementConfig
from mana_architect.models import Card, CollectionPrinting, SyncMetadata
from test_preliminary_composer import basic, card, commander, gate_for, stages


def _compose(gate, config, constraints=PoolConstraints()):
    artifacts = stages(gate, constraints)
    proposal = PreliminaryDeckComposer(config).compose(gate, *artifacts)
    assert proposal.validation.is_legal
    return proposal, artifacts


def _starting_deck(gate, proposal, quantities):
    # A legal proposal from the same run with a deliberately suboptimal selection.
    return replace(
        proposal, proposal_id=proposal_identity(gate, proposal.provenance, Counter(quantities)),
        main_deck=tuple(gate.allocate_entry(card_id, quantity) for card_id, quantity in sorted(quantities.items())),
    )


def test_refinement_audits_every_pair_and_uses_preference_only_to_break_a_tie():
    gate = gate_for((
        (commander(), (1,)), (basic(), (99,)),
        (card("first", "Draw three cards."), (1,)),
        (card("preferred", "Draw three cards."), (1,)),
        (card("worse", "Draw two cards.", cost="{30}{W}", value=31), (1,)),
        (card("excluded", "Draw three cards."), (1,)),
    ))
    config = CompositionConfig(land_target=99)
    start, artifacts = _compose(gate, config)
    limits = RefinementConfig(max_swaps=1)
    engine = DeckRefinementEngine(
        limits, composition_config=config, preferred_card_ids=("preferred",),
        constraints=PoolConstraints(exclude_card_ids=("excluded",)),
    )
    result = engine.refine(gate, start, *artifacts)
    attempts = {attempt.incoming_card_id: attempt for attempt in result.attempts}
    assert result.status == "improved" and result.stop_reason == "iteration_limit"
    assert {card_id: item.outcome for card_id, item in attempts.items()} == {
        "first": "passed_over", "preferred": "accepted",
        "worse": "below_threshold", "excluded": "constraint_rejected",
    }
    assert attempts["first"].utility_delta == attempts["preferred"].utility_delta > limits.min_improvement
    assert attempts["excluded"].violations[0].code == "user_excluded"
    assert attempts["excluded"].after_utility is None and not attempts["excluded"].component_deltas
    assert result.proposal.command_zone == start.command_zone
    assert result.snapshots[0].proposal.main_deck == start.main_deck
    assert result.snapshots[0].proposal.proposal_id == start.proposal_id
    assert all(
        gate.validate_proposal(item.proposal, excluded_card_ids=("excluded",), require_printings=True).is_legal
        for item in result.snapshots
    )
    assert result.snapshots[1].utility > result.snapshots[0].utility
    assert result.proposal.capacity.attainable_land_count == start.capacity.attainable_land_count == 99
    assert dict(result.proposal.role_coverage)["lands"] == 98
    assert "excluded" not in result.proposal.reserve_card_ids

    default_tie = DeckRefinementEngine(
        limits, composition_config=config, constraints=PoolConstraints(exclude_card_ids=("excluded",)),
    ).refine(gate, start, *artifacts)
    assert default_tie.attempts[default_tie.accepted_swaps[0].attempt_number - 1].incoming_card_id == "first"


def test_partner_refinement_preserves_owned_allocations_and_protects_locked_singletons():
    gate = gate_for((
        (commander("alpha", ("W",), "Partner"), (1, 1)),
        (commander("beta", ("U",), "Partner"), (1,)),
        (basic(), (98,)),
        (card("locked", "Draw two cards.", name="Shared Name"), (1, 1)),
        (card("same-name", "Draw four cards.", name="Shared Name"), (1,)),
        (card("new", "Draw three cards."), (1,)),
    ), leaders=("beta", "alpha"))
    config = CompositionConfig(land_target=97)
    proposal, artifacts = _compose(gate, config, PoolConstraints(locked_card_ids=("locked",)))
    start = _starting_deck(gate, proposal, {"locked": 1, "plains": 97})
    def last_printing(entry):
        return replace(entry, printings=(replace(entry.printings[0], printing_id=entry.printings[0].printing_id + 1),))

    start = replace(
        start, command_zone=(last_printing(start.command_zone[0]), start.command_zone[1]),
        main_deck=(last_printing(start.main_deck[0]), start.main_deck[1]),
    )
    engine = DeckRefinementEngine(RefinementConfig(max_swaps=1), composition_config=config)
    result = engine.refine(gate, start, *artifacts)
    assert result.status == "improved"
    assert result.proposal.command_zone == start.command_zone
    assert result.proposal.main_deck[0] == start.main_deck[0]
    assert sum(entry.quantity for entry in result.proposal.main_deck) == 98
    assert {issue.code for attempt in result.attempts for issue in attempt.violations} >= {
        "copy_limit", "missing_locked_card",
    }
    assert gate.validate_proposal(result.proposal, locked_card_ids=("locked",), require_printings=True).is_legal

    pool, pairwise, packages = artifacts
    replay = engine.refine(
        EligibilityGate(replace(gate.context, commander_card_ids=("beta", "alpha")), gate.snapshot),
        replace(start, main_deck=start.main_deck[::-1], command_zone=start.command_zone[::-1]),
        replace(pool, reserve=pool.reserve[::-1], candidates=pool.candidates[::-1]),
        replace(pairwise, edges=pairwise.edges[::-1]), packages,
    )
    assert replay.to_json() == result.to_json()


def test_package_completion_and_breakage_use_shared_utility_and_evidence():
    multiplier = (
        "If one or more tokens would be created under your control, twice that many of those tokens are created instead."
    )
    gate = gate_for((
        (commander(), (1,)), (basic(), (97,)),
        (card("draw", "Draw three cards."), (1,)),
        (card("procession", multiplier, types="Enchantment", value=4, cost="{3}{W}"), (1,)),
        (card("soldiers", "Create two 1/1 white Soldier creature tokens."), (1,)),
    ), strategies=("tokens",))
    config = CompositionConfig(land_target=97, role_weight=0, curve_weight=0)
    proposal, artifacts = _compose(gate, config)
    start = _starting_deck(gate, proposal, {"plains": 97, "procession": 1, "draw": 1})
    result = DeckRefinementEngine(composition_config=config).refine(gate, start, *artifacts)
    assert result.status == "improved"
    assert len(result.accepted_swaps) == 1
    assert result.stop_reason == "no_improving_legal_swap"
    accepted = result.accepted_swaps[0]
    assert accepted.changed_package_ids and accepted.changed_edge_ids
    assert result.snapshots[-1].diagnostics.package_dependencies
    assert all(
        package.modeled_requirements_supported for package in result.proposal.package_assessments
        if package.package_id in accepted.changed_package_ids
    )
    assert "draw" in result.proposal.reserve_card_ids

    # Starting from the completed deck evaluates the reverse as a scored loss.
    recheck = DeckRefinementEngine(composition_config=config).refine(gate, result.proposal, *artifacts)
    broken = next(a for a in recheck.attempts if (a.outgoing_card_id, a.incoming_card_id) == ("soldiers", "draw"))
    assert broken.outcome == "below_threshold"
    assert dict(broken.component_deltas)["joint_synergy"] < 0


def test_conditional_package_retains_runtime_requirement_without_extra_credit():
    gate = gate_for((
        (commander(colors=("B",)), (1,)), (basic("swamp", "Swamp", "B"), (96,)),
        (card("outlet", "Sacrifice a creature: Add {C}{C}.", types="Artifact", colors=(), cost="{3}"), (1,)),
        (card("payoff", "Whenever this creature or another creature dies, target player loses 1 life and you gain 1 life.",
              types="Creature — Vampire", colors=("B",), cost="{1}{B}"), (1,)),
        (card("fodder", "Create two 1/1 black Zombie creature tokens.", colors=("B",), cost="{1}{B}"), (1,)),
    ), strategies=("aristocrats",))
    config = CompositionConfig(land_target=96)
    locks = PoolConstraints(locked_card_ids=("outlet", "payoff", "fodder"))
    start, artifacts = _compose(gate, config, locks)
    result = DeckRefinementEngine(composition_config=config).refine(gate, start, *artifacts)
    assert result.proposal.selected_package_ids
    assert all(
        item.status == "conditional" and not item.modeled_requirements_supported
        for item in result.proposal.package_assessments if item.package_id in result.proposal.selected_package_ids
    )
    assert any("legal_resolution_target" in requirement for item in result.proposal.package_assessments
               for requirement in item.requirements)
    assert dict(result.proposal.utility_breakdown)["joint_synergy"] == dict(start.utility_breakdown)["joint_synergy"]


def test_used_identity_can_gain_an_owned_copy_under_a_copy_exception():
    repeat = card("repeat", "Create two Treasure tokens.\nA deck can have up to two cards named repeat.")
    gate = gate_for(((commander(), (1,)), (basic(), (98,)), (repeat, (1, 1))), strategies=("tokens",))
    config = CompositionConfig(land_target=98)
    start, artifacts = _compose(gate, config, PoolConstraints(locked_card_ids=("repeat",)))
    result = DeckRefinementEngine(composition_config=config).refine(gate, start, *artifacts)
    assert result.status == "improved"
    entry = next(item for item in result.proposal.main_deck if item.card_id == "repeat")
    assert entry.quantity == 2 and len(entry.printings) == 2
    assert any(item.card_id == "repeat" for item in result.snapshots[-1].diagnostics.early_ramp)
    assert any(item.cards[0].card_id == "repeat" for item in result.snapshots[-1].diagnostics.repeated_role_subtypes)


def test_mana_swap_improves_missing_color_without_changing_printed_demand():
    leader = replace(commander(colors=("W", "U")), mana_cost="{W}{U}", mana_value=2)
    gate = gate_for(((leader, (1,)), (basic(), (99,)), (basic("island", "Island", "U"), (1,))))
    config = CompositionConfig(land_target=99)
    proposal, artifacts = _compose(gate, config)
    start = _starting_deck(gate, proposal, {"plains": 99})
    result = DeckRefinementEngine(composition_config=config).refine(gate, start, *artifacts)
    assert result.status == "improved"
    assert result.accepted_swaps[0].changed_mana_symbols == ("U", "W")
    assert dict(result.proposal.utility_breakdown)["mana_sources"] > dict(
        result.snapshots[0].proposal.utility_breakdown,
    )["mana_sources"]
    assert dict(result.proposal.role_coverage)["lands"] == 99


def test_limits_distinguish_truncated_search_from_complete_scan():
    gate = gate_for((
        (commander(), (1,)), (basic(), (99,)),
        (card("one", "Draw two cards."), (1,)),
        (card("two", "Draw two cards."), (1,)),
    ))
    config = CompositionConfig(land_target=99)
    start, artifacts = _compose(gate, config)
    truncated = DeckRefinementEngine(
        RefinementConfig(attempts_per_iteration=1, min_improvement=100), composition_config=config,
    ).refine(gate, start, *artifacts)
    assert truncated.stop_reason == "swap_budget_exhausted" and len(truncated.attempts) == 1
    assert truncated.status == "unchanged"
    exhausted = DeckRefinementEngine(
        RefinementConfig(max_attempts=1), composition_config=config,
    ).refine(gate, start, *artifacts)
    assert exhausted.status == "improved" and exhausted.stop_reason == "swap_budget_exhausted"
    full = DeckRefinementEngine(
        RefinementConfig(min_improvement=100), composition_config=config,
    ).refine(gate, start, *artifacts)
    assert full.stop_reason == "no_improving_legal_swap" and len(full.attempts) == 2

    small = gate_for(((commander(), (1,)), (basic(), (99,))))
    start, artifacts = _compose(small, config)
    empty = DeckRefinementEngine(composition_config=config).refine(small, start, *artifacts)
    assert empty.stop_reason == "no_legal_swaps" and not empty.attempts
    assert "ramp" in empty.rediscovery_roles


def test_refinement_rejects_illegal_start_altered_provenance_and_missing_lock():
    gate = gate_for(((commander(), (1,)), (basic(), (99,)), (card("draw", "Draw two cards."), (1,))))
    config = CompositionConfig(land_target=99)
    start, artifacts = _compose(gate, config)
    engine = DeckRefinementEngine(composition_config=config)
    invalid = engine.refine(gate, replace(start, command_zone=()), *artifacts)
    assert invalid.status == "invalid_input" and not invalid.snapshots
    assert "invalid_command_zone" in {issue.code for issue in invalid.issues}
    altered = replace(start, provenance=replace(start.provenance, package_fingerprint="foreign"))
    assert engine.refine(gate, altered, *artifacts).issues[0].code == "proposal_provenance_mismatch"
    new_lock = DeckRefinementEngine(composition_config=config, constraints=PoolConstraints(locked_card_ids=("draw",)))
    assert new_lock.refine(gate, start, *artifacts).issues[0].code == "missing_locked_card"


def test_refine_cli_fresh_and_saved_match_and_reject_invalid_replay(tmp_path, monkeypatch):
    database = tmp_path / "test.db"
    monkeypatch.setenv("MANA_ARCHITECT_DB_PATH", str(database))
    engine = create_database_engine(database)
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        for item, quantity in ((commander(), 1), (basic(), 99), (card("draw", "Draw two cards."), 1)):
            session.add(Card(
                oracle_id=item.card_id, name=item.name, oracle_text=item.oracle_text, cmc=item.mana_value,
                mana_cost=item.mana_cost, type_line=item.type_line, color_identity=list(item.color_identity),
                legalities={"commander": "legal"},
            ))
            session.flush()
            session.add(CollectionPrinting(
                scryfall_id=item.card_id, oracle_id=item.card_id, name=item.name, quantity=quantity,
                set_code="tst", collector_number="1", language="en", finish="nonfoil", condition="near_mint",
            ))
        session.add(SyncMetadata(key="bulk_updated_at", value="2026-11-11"))

    proposal, fresh, saved = (tmp_path / filename for filename in ("proposal.json", "fresh.json", "saved.json"))
    assert main(["compose", "leader", "--land-target", "99", "--output", str(proposal)]) == 0
    assert main(["refine", "leader", "--land-target", "99", "--output", str(fresh)]) == 0
    args = ["refine", "--proposal", str(proposal), "--output", str(saved)]
    assert main(args) == 0
    assert fresh.read_bytes() == saved.read_bytes()
    result = json.loads(saved.read_bytes())
    assert result["status"] == "improved" and result["attempt_count"] == len(result["attempts"])
    assert result["snapshots"][0]["proposal"]["main_deck"] == json.loads(proposal.read_bytes())["main_deck"]
    restored = tmp_path / "restored.json"
    restored.write_text(json.dumps(result["snapshots"][0]["proposal"]), encoding="utf-8")
    assert main(["refine", "--proposal", str(restored), "--output", str(saved), "--overwrite"]) == 0
    assert fresh.read_bytes() == saved.read_bytes()

    payload = json.loads(proposal.read_bytes())
    older = {key: value for key, value in payload.items() if key != "replay"}
    args.append("--overwrite")
    original = proposal.read_bytes()
    proposal.write_text(json.dumps(older), encoding="utf-8")
    assert main(args) == 1
    assert json.loads(saved.read_bytes())["issues"][0]["code"] == "missing_replay_manifest"
    payload["main_deck"][0]["quantity"] -= 1
    proposal.write_text(json.dumps(payload), encoding="utf-8")
    assert main(args) == 1
    assert json.loads(saved.read_bytes())["issues"][0]["code"] == "altered_proposal"

    proposal.write_bytes(original)
    with Session(engine) as session, session.begin():
        session.get(CollectionPrinting, 2).quantity += 1
    engine.dispose()
    assert main(args) == 1
    assert json.loads(saved.read_bytes())["issues"][0]["code"] == "stale_collection_snapshot"
