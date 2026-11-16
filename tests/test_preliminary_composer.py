from collections import Counter
from dataclasses import replace
import json

import pytest
from sqlalchemy.orm import Session

from mana_architect.cli import main
from mana_architect.database import create_database_engine, initialize_database
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS
from mana_architect.deck_pipeline.candidate_pool import CandidatePoolBuilder, CandidatePoolConfig, PoolConstraints
from mana_architect.deck_pipeline.composition_utility import CompositionConfig
from mana_architect.deck_pipeline.contracts import DeckBuildingContext, Role, StrategyPreference
from mana_architect.deck_pipeline.eligibility import PARTNER_RULESET_VERSION, EligibilityGate
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.deck_pipeline.mana_base import ManaBase, fixed_mana_cost
from mana_architect.deck_pipeline.pairwise_synergy import PairwiseConfig, PairwiseSynergyAnalyzer
from mana_architect.deck_pipeline.preliminary_composer import PreliminaryDeckComposer, run_preliminary_pipeline
from mana_architect.deck_pipeline.synergy_packages import PackageConfig, SynergyPackageBuilder
from mana_architect.models import Card, CollectionPrinting, SyncMetadata


def card(
    card_id, text="", *, name=None, types="Sorcery", colors=("W",), cost="{1}{W}", value=2,
    legality="legal", produced=(), layout=None, keywords=(),
):
    return CardSnapshot(
        card_id, name or card_id, types, text, cost, value, colors, produced, keywords, legality, colors, layout,
    )


def commander(card_id="leader", colors=("W",), text=""):
    return card(card_id, text, types="Legendary Creature — Human", colors=colors, cost=f"{{2}}{{{colors[0]}}}", value=3)


def basic(card_id="plains", name="Plains", color="W"):
    return card(card_id, name=name, types=f"Basic Land — {name}", colors=(color,), cost="", value=0)


def gate_for(holdings, *, leaders=("leader",), strategies=()):
    cards = tuple(item for item, _ in holdings)
    rows = []
    for item, quantities in holdings:
        for quantity in quantities:
            row_id = len(rows) + 1
            rows.append(OwnedPrinting(row_id, f"scryfall-{item.card_id}", item.card_id, item.name, "tst",
                                     "42", "en", "nonfoil", "near_mint", quantity))
    snapshot = CollectionSnapshot(cards, tuple(rows), "2026-11-11")
    return EligibilityGate(DeckBuildingContext(
        "composer-test", leaders, snapshot.snapshot_id, PARTNER_RULESET_VERSION,
        ENGINE_VERSIONS, CandidatePoolConfig().version, tuple(StrategyPreference(item) for item in strategies),
    ), snapshot)


def stages(gate, constraints=PoolConstraints(), *, include_reserve=False, draw_budget=None):
    config = CandidatePoolConfig()
    policies = {
        Role.STRATEGY_SYNERGY: {"quality_floor": 0},
        Role.CARD_ADVANTAGE: {"max_candidates": draw_budget},
    }
    if draw_budget is None:
        del policies[Role.CARD_ADVANTAGE]
    config = replace(config, policies=tuple(replace(policy, **policies.get(policy.role, {})) for policy in config.policies))
    pool = CandidatePoolBuilder(config).build(gate, constraints=constraints)
    pairwise = PairwiseSynergyAnalyzer(PairwiseConfig(include_reserve=include_reserve)).analyze(gate, pool)
    packages = SynergyPackageBuilder(PackageConfig(include_reserve=include_reserve)).build(gate, pool, pairwise)
    return pool, pairwise, packages


def decisions(proposal):
    return {item.card_id: item for item in proposal.card_decisions}


def assert_valid(gate, proposal):
    assert proposal.proposal_status == "valid", proposal.validation
    assert proposal.validation.is_legal and gate.validate_proposal(proposal, require_printings=True).is_legal
    assert len(proposal.main_deck) == len({item.card_id for item in proposal.main_deck})
    assert sum(item.quantity for item in (*proposal.main_deck, *proposal.command_zone)) == 100
    assert all(sum(row.quantity for row in item.printings) == item.quantity
               for item in (*proposal.main_deck, *proposal.command_zone))


def test_exact_deck_uses_owned_printings_and_english_name_copy_limits():
    gate = gate_for((
        (commander(colors=("B", "R")), (2,)),
        (basic("swamp", "Swamp", "B"), (40, 49)),
        (basic("mountain", "Mountain", "R"), (2,)),
        (card("rats", "Draw three cards.\nA deck can have any number of cards named Relentless Rats.",
              name="Relentless Rats", colors=("B",), cost="{2}{B}"), (4,)),
        (card("dwarves", "Draw three cards.\nA deck can have up to seven cards named Seven Dwarves.",
              name="Seven Dwarves", colors=("R",), cost="{2}{R}"), (10,)),
        (card("alias-a", "Draw three cards.", name="Same English Name", colors=("B",), cost="{2}{B}"), (2,)),
        (card("alias-b", "Draw three cards.", name="Same English Name", colors=("B",), cost="{2}{B}"), (3,)),
        (card("off-color", "Draw three cards."), (100,)),
        (card("banned", "Draw three cards.", colors=("B",), legality="banned"), (100,)),
        (card("excluded", "Draw three cards.", colors=("B",)), (1,)),
    ))
    constraints = PoolConstraints(("excluded",), ("rats", "dwarves"))
    proposal = run_preliminary_pipeline(gate, constraints=constraints, config=CompositionConfig(land_target=87))
    assert_valid(gate, proposal)
    selected = {item.card_id: item for item in proposal.main_deck}
    assert selected["rats"].quantity == 4
    assert selected["dwarves"].quantity == 7
    assert len(selected["swamp"].printings) == 2
    assert selected.keys() & {"alias-a", "alias-b"} == {"alias-a"}
    assert not selected.keys() & {"banned", "off-color", "excluded", "leader"}
    assert sum(item.quantity for item in proposal.command_zone) == 1
    assert decisions(proposal)["excluded"].reason_codes == ("user_excluded",)
    assert "locked_card" in decisions(proposal)["rats"].reason_codes

    fraudulent = replace(proposal, command_zone=())
    assert "invalid_command_zone" in {
        issue.code for issue in gate.validate_proposal(fraudulent, require_printings=True).violations
    }


def test_partner_pipeline_binds_both_anchors_and_replays_permuted_inputs():
    partner = "\nPartner (You can have two commanders if both have partner.)"
    multiplier = "If one or more tokens would be created under your control, twice that many of those tokens are created instead."
    gate = gate_for((
        (commander("alpha", ("W",), "When Alpha enters, create a 1/1 white Soldier creature token." + partner), (1,)),
        (commander("beta", ("G",), multiplier + partner), (1,)),
        (basic(), (50,)),
        (basic("forest", "Forest", "G"), (50,)),
        (card("both-colors", "Create two 1/1 white Soldier creature tokens.", colors=("W", "G"), cost="{W}{G}"), (1,)),
        (card("duplicate-effect", multiplier, types="Enchantment", cost="{3}{W}", value=4), (1,)),
    ), leaders=("beta", "alpha"), strategies=("tokens",))
    locks = PoolConstraints(locked_card_ids=("both-colors", "duplicate-effect"))
    pool, pairwise, packages = stages(gate, locks)
    profiles = next(item for item in pool.candidates if item.card_id == "both-colors").evaluations.commander_profiles
    assert [item.commander_id for item in profiles] == ["alpha", "beta"]
    assert all(item.eligible for item in profiles)
    assert {edge.commander_anchor_ids for edge in pairwise.edges} >= {("alpha",), ("beta",), ("alpha", "beta")}
    assert packages.packages and all(package.member_card_ids for package in packages.packages)
    composer = PreliminaryDeckComposer(CompositionConfig(land_target=96))
    proposal = composer.compose(gate, pool, pairwise, packages)
    assert_valid(gate, proposal)
    assert tuple(entry.card_id for entry in proposal.command_zone) == ("alpha", "beta")
    assert sum(item.quantity for item in proposal.main_deck) == 98
    assert any(item.modeled_requirements_supported and item.status == "conditional" for item in proposal.package_assessments)
    assert proposal.supporting_edges and decisions(proposal)["both-colors"].package_ids

    replay_gate = EligibilityGate(
        replace(gate.context, commander_card_ids=("beta", "alpha")),
        replace(gate.snapshot, cards=gate.snapshot.cards[::-1], printings=gate.snapshot.printings[::-1]),
    )
    replay = composer.compose(
        replay_gate, replace(pool, candidates=pool.candidates[::-1], reserve=pool.reserve[::-1]),
        replace(pairwise, edges=pairwise.edges[::-1]),
        replace(packages, packages=packages.packages[::-1], partial=packages.partial[::-1]),
    )
    assert json.dumps(replay.to_json(), sort_keys=True) == json.dumps(proposal.to_json(), sort_keys=True)


@pytest.mark.parametrize(("ids", "text", "expected"), [
    (("leader", "second"), "Partner—Character select", "unsupported_commander_configuration"),
    (("leader", "second"), "Partner with Leader", "unsupported_commander_configuration"),
    (("leader", "leader"), "Partner", "invalid_commander_configuration"),
    (("leader", "missing"), "Partner", "commander_not_owned"),
])
def test_unsupported_or_invalid_partner_is_structured(ids, text, expected):
    gate = gate_for(((commander(text="Partner"), (1,)), (commander("second", text=text), (1,))), leaders=ids)
    proposal = run_preliminary_pipeline(gate)
    assert proposal.proposal_status == "infeasible"
    assert expected in {issue.code for issue in proposal.validation.violations}


def test_rulebreaker_and_card_constraints_report_explicit_failures():
    gate = gate_for(((commander(text="Rulebreaker — Your deck must contain only lands."), (1,)),))
    assert run_preliminary_pipeline(gate).validation.violations[0].code == "unsupported_deckbuilding_rule"
    ordinary = gate_for(((commander(), (1,)), (basic(), (120,))))
    for constraints in (PoolConstraints(("plains",), ("plains",)), PoolConstraints(("leader",))):
        proposal = run_preliminary_pipeline(ordinary, constraints=constraints)
        assert proposal.proposal_status == "infeasible"
        assert "conflicting_constraints" in {issue.code for issue in proposal.validation.violations}


def test_locked_cards_with_one_shared_singleton_slot_are_infeasible():
    gate = gate_for((
        (commander(), (1,)), (basic(), (99,)),
        (card("same-a", name="Same Name"), (1,)),
        (card("same-b", name="Same Name"), (1,)),
    ))
    proposal = run_preliminary_pipeline(gate, constraints=PoolConstraints(locked_card_ids=("same-a", "same-b")))
    assert proposal.proposal_status == "infeasible"
    assert proposal.validation.violations[0].code == "conflicting_constraints"
    assert proposal.main_deck == ()


def test_insufficient_collection_capacity_excludes_extra_singleton_copies():
    gate = gate_for(((commander(), (1,)), (basic(), (35,)), (card("draw", "Draw three cards."), (100,))))
    proposal = run_preliminary_pipeline(gate)
    assert proposal.proposal_status == "infeasible"
    assert proposal.capacity.available_in_collection == 36
    assert proposal.validation.violations[0].code == "insufficient_owned_legal_cards"


def test_pool_shortfall_and_owned_reserve_fallback_are_distinct():
    gate = gate_for(((commander(), (1,)), (basic(), (98,)), (card("draw", "Draw three cards."), (1,))))
    pool, pairwise, packages = stages(gate, include_reserve=True, draw_budget=0)
    assert "draw" in {item.card_id for item in pool.reserve}
    composer = PreliminaryDeckComposer(CompositionConfig(land_target=98))
    proposal = composer.compose(gate, pool, pairwise, packages)
    assert_valid(gate, proposal)
    assert "owned_reserve_fallback" in decisions(proposal)["draw"].reason_codes

    trimmed = replace(pool, reserve=())
    pairwise = PairwiseSynergyAnalyzer().analyze(gate, trimmed)
    packages = SynergyPackageBuilder().build(gate, trimmed, pairwise)
    proposal = composer.compose(gate, trimmed, pairwise, packages)
    assert proposal.validation.violations[0].code == "insufficient_legal_pool_capacity"
    assert proposal.capacity.available_in_collection == 99


def test_sparse_land_admission_recovers_only_owned_legal_lands():
    gate = gate_for(((commander(), (1,)), (basic(), (98,)), (card("draw", "Draw three cards."), (1,))))
    pool, _, _ = stages(gate)
    trimmed = replace(pool, candidates=tuple(item for item in pool.candidates if item.card_id != "plains"))
    pairs = PairwiseSynergyAnalyzer().analyze(gate, trimmed)
    packages = SynergyPackageBuilder().build(gate, trimmed, pairs)
    proposal = PreliminaryDeckComposer().compose(gate, trimmed, pairs, packages)
    assert_valid(gate, proposal)
    assert "owned_land_fallback" in decisions(proposal)["plains"].reason_codes


def test_legal_deck_with_no_lands_reports_soft_mana_shortfalls():
    spell = card("repeat", "Draw three cards.\nA deck can have any number of cards named repeat.")
    gate = gate_for(((commander(), (1,)), (spell, (99,))))
    proposal = run_preliminary_pipeline(gate, constraints=PoolConstraints(locked_card_ids=("repeat",)))
    assert_valid(gate, proposal)
    assert {item.code for item in proposal.unmet_objectives} >= {"land_target_adjusted", "mana_source_shortfall"}


def test_mana_counts_fetch_targets_once_and_distinguishes_modal_and_transform_faces():
    records = (
        commander(),
        basic("island", "Island", "U"),
        card("dual", "This land enters tapped.", types="Land — Plains Island", cost="", value=0),
        card("fetch", "{T}, Sacrifice this land: Search your library for an Island card, put it onto the battlefield, then shuffle.",
             types="Land", cost="", value=0),
        card("fetch-two", "{T}, Sacrifice this land: Search your library for an Island card, put it onto the battlefield, then shuffle.",
             types="Land", cost="", value=0),
        card("restricted", "{T}: Add {U}. Spend this mana only to cast an artifact spell.",
             types="Land", cost="", value=0),
        card("colorless", "{T}: Add {C}.", types="Land", cost="", value=0),
        card("modal", "Draw a card. // {T}: Add {U}.", types="Sorcery // Land", layout="modal_dfc"),
        card("transform", "Draw a card. // {T}: Add {U}.", types="Artifact // Land", layout="transform"),
        card("unknown", "Draw a card. // {T}: Add {U}.", types="Artifact // Land"),
    )
    mana = ManaBase({item.card_id: item for item in records}, ("leader",), frozenset("WU"))
    deck = Counter({item.card_id: 1 for item in records if item.card_id not in {"leader", "dual"}})
    result = mana.assess(deck, 6)
    rows = {item.symbol: item for item in result.sources}
    assert rows["U"].direct == 1 and rows["U"].fetch == 1
    assert rows["U"].conditional == 1 and rows["U"].modal == 1
    assert rows["C"].direct == 1
    assert result.modal_land_count == 1 and result.tapped_count == 0
    assert mana.assess(deck - Counter(island=1), 6).sources[1].fetch == 0
    assert mana.assess(deck + Counter(dual=1), 6).tapped_count == 1
    assert {item.card_id for item in mana.diagnostics(deck, result) if item.code == "unknown_land_face_layout"} == {"unknown"}
    cost = fixed_mana_cost("{W}{U}")
    assert cost is not None and not mana.supports({"dual": 1, "colorless": 1}, cost)
    assert mana.supports({"dual": 1, "island": 1}, cost)


def test_any_color_production_pays_generic_in_a_colorless_deck():
    rainbow = card("rainbow", "{T}: Add one mana of any color.", types="Land", cost="", value=0, colors=())
    tower = card("tower", "{T}: Add one mana of any color in your commander's color identity.",
                 types="Land", cost="", value=0, colors=())
    mana = ManaBase({item.card_id: item for item in (rainbow, tower)}, (), frozenset())
    assert mana.supports({"rainbow": 1}, fixed_mana_cost("{1}"))
    assert not mana.supports({"rainbow": 1}, fixed_mana_cost("{C}"))
    assert not mana.supports({"tower": 1}, fixed_mana_cost("{1}"))


def test_modal_back_face_shares_one_legal_owned_deck_identity():
    modal = card(
        "modal", "Draw a card. // {T}: Add {W}.", types="Sorcery // Land", layout="modal_dfc", cost="{1}{W} // ",
    )
    gate = gate_for(((commander(), (1,)), (basic(), (98,)), (modal, (2,))))
    proposal = run_preliminary_pipeline(gate)
    assert_valid(gate, proposal)
    assert decisions(proposal)["modal"].quantity == 1
    assert dict(proposal.role_coverage)[Role.LANDS] == 98
    assert next(row for row in proposal.mana_sources if row.symbol == "W").modal == 1


def test_package_and_pairwise_credit_share_a_benefit_and_group_beats_standalone_rank():
    gate = gate_for((
        (commander(), (1,)), (basic(), (98,)),
        (card("a-draw", "Draw three cards."), (1,)),
        (card("procession", "If one or more tokens would be created under your control, twice that many of those tokens are created instead.",
              types="Enchantment", value=4, cost="{3}{W}"), (1,)),
        (card("soldiers", "Create two 1/1 white Soldier creature tokens."), (1,)),
    ), strategies=("tokens",))
    pool, pairwise, packages = stages(gate)
    assert pairwise.edges and packages.packages
    duplicated = replace(packages.packages[0], package_id="equivalent-path")
    package_analysis = replace(packages, packages=(*packages.packages, duplicated))
    composer = PreliminaryDeckComposer(CompositionConfig(land_target=97, role_weight=0.2))
    proposal = composer.compose(gate, pool, pairwise, package_analysis)
    assert_valid(gate, proposal)
    assert {item.card_id for item in proposal.main_deck} == {"plains", "procession", "soldiers"}
    assert len(proposal.selected_package_ids) == 2
    package = packages.packages[0]
    assert dict(proposal.utility_breakdown)["joint_synergy"] == pytest.approx(
        max(pairwise.edges[0].strength, 0.8 * package.score),
    )
    assert all("package_group" in decisions(proposal)[item].reason_codes for item in ("procession", "soldiers"))


def test_incomplete_or_runtime_packages_preserve_requirements_without_credit():
    gate = gate_for((
        (commander(colors=("B",)), (1,)), (basic("swamp", "Swamp", "B"), (98,)),
        (card("outlet", "Sacrifice a creature: Add {C}{C}.", types="Artifact", colors=(), cost="{3}"), (1,)),
        (card("payoff", "Whenever this creature or another creature dies, target player loses 1 life and you gain 1 life.",
              types="Creature — Vampire", colors=("B",), cost="{1}{B}"), (1,)),
    ), strategies=("aristocrats",))
    locks = PoolConstraints(locked_card_ids=("outlet", "payoff"))
    pool, pairwise, packages = stages(gate, locks)
    assert packages.partial
    proposal = PreliminaryDeckComposer().compose(gate, pool, pairwise, packages)
    assert_valid(gate, proposal)
    assert not proposal.selected_package_ids
    assert dict(proposal.utility_breakdown)["joint_synergy"] == 0
    assert "fodder" in proposal.package_assessments[0].missing_roles
    assert any(edge.prerequisites for edge in proposal.supporting_edges)


def test_role_credit_caps_a_multirole_card_and_reports_both_roles():
    gate = gate_for((
        (commander(), (1,)), (basic(), (98,)),
        (card("treasure", "Create two Treasure tokens.", types="Instant", cost="{2}{W}"), (1,)),
    ), strategies=("tokens",))
    proposal = run_preliminary_pipeline(gate, constraints=PoolConstraints(locked_card_ids=("treasure",)))
    assert_valid(gate, proposal)
    decision = decisions(proposal)["treasure"]
    assert set(decision.roles) >= {Role.RAMP, Role.STRATEGY_SYNERGY}
    credits = dict(decision.assigned_role_credit)
    assert credits[Role.RAMP] > 0 and credits[Role.STRATEGY_SYNERGY] > 0
    ramp = next(item.quality for item in decision.role_profiles if item.role is Role.RAMP)
    assert sum(credits.values()) == pytest.approx(max(ramp, 0.75), abs=1e-6)


def test_foreign_or_altered_artifacts_are_rejected():
    gate = gate_for((
        (commander(), (1,)), (basic(), (98,)),
        (card("a", "Create two 1/1 white Soldier creature tokens."), (1,)),
        (card("b", "If one or more tokens would be created under your control, twice that many of those tokens are created instead.",
              types="Enchantment"), (1,)),
    ), strategies=("tokens",))
    pool, pairwise, packages = stages(gate)
    composer = PreliminaryDeckComposer()
    altered = replace(pairwise.edges[0], resource_requirements=())
    proposal = composer.compose(gate, pool, replace(pairwise, edges=(altered, *pairwise.edges[1:])), packages)
    assert "edge_fingerprint_mismatch" in {issue.code for issue in proposal.validation.violations}
    proposal = composer.compose(gate, pool, pairwise, replace(packages, run_id="foreign"))
    assert proposal.validation.violations[0].code == "analysis_context_mismatch"


def test_compose_cli_writes_replayable_json_and_structured_failure(tmp_path, monkeypatch):
    database = tmp_path / "test.db"
    monkeypatch.setenv("MANA_ARCHITECT_DB_PATH", str(database))
    engine = create_database_engine(database)
    initialize_database(engine)
    with Session(engine) as session:
        for fake, quantity in ((commander(), 1), (basic(), 99)):
            session.add(Card(
                oracle_id=fake.card_id, name=fake.name, oracle_text=fake.oracle_text, cmc=fake.mana_value,
                mana_cost=fake.mana_cost, type_line=fake.type_line, color_identity=list(fake.color_identity),
                legalities={"commander": "legal"},
            ))
            session.flush()
            session.add(CollectionPrinting(
                scryfall_id=fake.card_id, oracle_id=fake.card_id, name=fake.name, quantity=quantity,
                set_code="tst", collector_number="1", language="en", finish="nonfoil", condition="near_mint",
            ))
        session.add(SyncMetadata(key="bulk_updated_at", value="2026-11-11"))
        session.commit()
    engine.dispose()

    output = tmp_path / "proposal.json"
    args = ["compose", "LEADER", "--output", str(output)]
    assert main(args) == 0
    contents = output.read_bytes()
    result = json.loads(contents)
    assert result["proposal_status"] == "valid"
    assert result["command_zone"][0]["name"] == "leader"
    assert main(args) == 1
    assert main([*args, "--overwrite"]) == 0 and contents == output.read_bytes()
    assert main(["compose", "invalid", "--output", str(output), "--overwrite"]) == 1
    assert json.loads(output.read_bytes())["validation"]["violations"][0]["code"] == "commander_not_owned"
