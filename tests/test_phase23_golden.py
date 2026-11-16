"""Independent real-card release gates for the Phase 2 to Phase 3 handoff."""

from collections import Counter
from dataclasses import replace
from functools import lru_cache
from itertools import product
import json
from pathlib import Path
from random import Random
from uuid import UUID

import pytest

from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS, parse_card_snapshot
from mana_architect.deck_pipeline.candidate_pool import CandidatePoolBuilder, PoolConstraints
from mana_architect.deck_pipeline.contracts import (
    DeckBuildingContext, PairwiseDiagnostics, PairwiseProvenance, PairwiseSynergyAnalysis, StrategyPreference,
    artifact_digest,
)
from mana_architect.deck_pipeline.eligibility import EligibilityGate, RULESET_VERSION
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.deck_pipeline.pairwise_synergy import (
    PairwiseConfig, PairwiseSynergyAnalyzer, _endpoint, canonical_rule_relation, pairwise_catalog_fingerprint,
)
from mana_architect.deck_pipeline.synergy_packages import PackageBuildError, SynergyPackageBuilder
from mana_architect.oracle_text import PARSER_VERSION
from mana_architect.strategy_synergy import load_synergy_context
from mana_architect.synergy_capabilities import CAPABILITY_VERSION, extract_mechanical_capabilities
from mana_architect.synergy_interactions import MatchStatus, validate_interaction

FIXTURES = Path(__file__).parent / "fixtures" / "phase23"
ORACLE = json.loads((FIXTURES / "cards.json").read_text(encoding="utf-8"))
SCENARIOS = json.loads((FIXTURES / "scenarios.json").read_text(encoding="utf-8"))
BY_ID = {entry["id"]: entry for entry in SCENARIOS}
CARDS = ORACLE["cards"]
ALIASES = {record["oracle_id"]: alias for alias, record in CARDS.items()}
CATALOG = load_synergy_context()


def _verified_scenarios():
    return [scenario for scenario in SCENARIOS if scenario.get("verification") != "pending"]


def _prepare(scenario, *, shuffle_seed=None, lock_cards=True):
    commander = scenario.get("commander", "kenrith")
    aliases = sorted(set(scenario["cards"]) | {commander})
    snapshots, rows = [], []
    for row_id, alias in enumerate(aliases, start=1):
        record = CARDS[alias]
        card = CardSnapshot(
            record["oracle_id"], record["name"], record["type_line"], record["oracle_text"],
            record["mana_cost"], record["mana_value"], tuple(record["color_identity"]),
            tuple(record["produced_mana"]), tuple(record["keywords"]), record["commander_legality"],
            None if record["colors"] is None else tuple(record["colors"]),
        )
        snapshots.append(card)
        rows.append(OwnedPrinting(
            row_id, record["printing_id"], card.card_id, card.name, record["set_code"],
            record["collector_number"], "en", "nonfoil", "near_mint", 1,
        ))
    locks = [CARDS[alias]["oracle_id"] for alias in aliases
             if alias != commander and alias not in scenario.get("exclude", {})]
    if shuffle_seed is not None:
        rng = Random(shuffle_seed)
        for values in (snapshots, rows, locks):
            rng.shuffle(values)
    snapshot = CollectionSnapshot(tuple(snapshots), tuple(rows), ORACLE["legality_data_as_of"])
    builder = CandidatePoolBuilder(synergy_context=CATALOG)
    context = DeckBuildingContext(
        scenario["id"], (CARDS[commander]["oracle_id"],), snapshot.snapshot_id, RULESET_VERSION,
        ENGINE_VERSIONS, builder.config.version,
        (StrategyPreference("aristocrats"), StrategyPreference("artifacts"), StrategyPreference("tokens")),
    )
    gate = EligibilityGate(context, snapshot)
    constraints = PoolConstraints(locked_card_ids=tuple(locks)) if lock_cards else PoolConstraints()
    pool = builder.build(gate, constraints=constraints)
    return gate, pool


def _run(scenario, *, shuffle_seed=None, lock_cards=True):
    gate, pool = _prepare(scenario, shuffle_seed=shuffle_seed, lock_cards=lock_cards)
    pairs = PairwiseSynergyAnalyzer(synergy_context=CATALOG).analyze(gate, pool)
    packages = SynergyPackageBuilder(synergy_context=CATALOG).build(gate, pool, pairs)
    return gate, pool, pairs, packages


@lru_cache(maxsize=None)
def _baseline(case_id):
    return _run(BY_ID[case_id])


def _json(artifact):
    return json.dumps(artifact.to_json(), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _edge_key(edge):
    return ALIASES[edge.source_card_id], ALIASES[edge.target_card_id], edge.relation, edge.event_type


def _expected_edge_key(edge):
    return edge["source"], edge["target"], edge["relation"], edge["event"]


def _package_key(package):
    return (
        package.package_archetype, tuple(sorted(ALIASES[item] for item in package.member_card_ids)),
        tuple(sorted(ALIASES[item] for item in package.commander_anchor_ids)),
    )


def _expected_package_key(package):
    return package["archetype"], tuple(sorted(package["members"])), tuple(sorted(package.get("anchors", ())))


def _in_scope(scenario, package):
    return package[0] in scenario["archetypes"] and set(package[1] + package[2]) <= set(scenario["cards"])


def _covers(requirement, values):
    return any(requirement.casefold() in item.casefold() for item in values)


def _constraint_text(item):
    return (*item.prerequisites, *item.resource_requirements, *item.timing_constraints, *item.limitations)


def _findings(scenario, pairs, packages):
    scoped_pairs = {frozenset(item) for item in scenario["focus"]}
    predicted_edges = {}
    for edge in pairs.edges if pairs is not None else ():
        key = _edge_key(edge)
        if frozenset(key[:2]) in scoped_pairs:
            predicted_edges.setdefault(key, []).append(edge)
    expected_edges = {_expected_edge_key(edge): edge for edge in scenario["edges"]}
    expected_packages = {
        _expected_package_key(item): item for item in scenario["packages"] if item["classification"] != "partial"
    }
    expected_partial = {
        _expected_package_key(item): item for item in scenario["packages"] if item["classification"] == "partial"
    }
    predicted_packages = {}
    for item in packages.packages if packages is not None else ():
        key = _package_key(item)
        if _in_scope(scenario, key):
            predicted_packages.setdefault(key, []).append(item)
    predicted_partial = {
        _package_key(item): item
        for item in (packages.partial if packages is not None else ())
        if _in_scope(scenario, _package_key(item))
    }

    critical = []
    differences = []
    for key, expected in expected_edges.items():
        matching_edges = predicted_edges.get(key, ())
        for observed in matching_edges:
            for requirement in expected["requires"]:
                if not _covers(requirement, _constraint_text(observed)):
                    critical.append(f"edge {key} omits {requirement!r}")
        if matching_edges:
            target_spans = tuple(edge.evidence[1].source_text for edge in matching_edges)
            for effect in expected.get("target_effects", ()):
                if not _covers(effect, target_spans):
                    differences.append(f"edge {key} has no separate evidence for {effect!r}")
            target_events = {edge.event_ids[1] for edge in matching_edges}
            if len(target_events) < expected.get("minimum_target_events", 1):
                differences.append(f"edge {key} conflates distinct target events")
    for key, expected in expected_packages.items():
        for observed in predicted_packages.get(key, ()):
            flags = expected["repeatable"], expected["self_sustaining"], expected["infinite"]
            actual_flags = observed.repeatable, observed.self_sustaining, observed.infinite
            if actual_flags != flags or observed.dependency_status != expected["classification"]:
                differences.append(f"{key} classified as {observed.dependency_status}, flags {actual_flags}, expected {flags}")
            if (observed.infinite and not expected["infinite"]) or (
                observed.self_sustaining and not expected["self_sustaining"]
            ):
                critical.append(f"{key} overclaims loop closure")
            requirements = list(expected.get("requires", ()))
            if observed.repeatable:
                requirements.extend(expected.get("repeat_requires", ()))
            for requirement in requirements:
                if not _covers(requirement, (*observed.requirements, *observed.resource_requirements,
                                             *observed.timing_constraints, *observed.limitations)):
                    critical.append(f"package {key} omits {requirement!r}")
            if "roles" in expected:
                actual_roles = {role: sorted(ALIASES[item] for item in ids) for role, ids in observed.functional_roles}
                if actual_roles != expected["roles"]:
                    differences.append(f"{key} roles differ: {actual_roles}")
    for key, expected in expected_partial.items():
        observed = predicted_partial.get(key)
        if observed is None or not set(expected["missing"]) <= set(observed.missing_roles):
            differences.append(f"{key} missing partial opportunity for {expected['missing']}")

    return {
        "pairwise": _compare(expected_edges, predicted_edges),
        "packages": _compare(expected_packages, predicted_packages),
        "critical": sorted(set(critical)),
        "classification": sorted(set(differences)),
    }


def _compare(expected, predicted):
    wanted, generated = set(expected), set(predicted)
    return {"tp": sorted(wanted & generated), "fp": sorted(generated - wanted), "fn": sorted(wanted - generated)}


# Dataset and mechanical oracle checks. These do not call the engines.
def test_dataset_records_are_explicit_and_refer_to_pinned_real_identities():
    assert len(_verified_scenarios()) >= 20
    assert len(BY_ID) == len(SCENARIOS) and len(ALIASES) == len(CARDS)
    for card in CARDS.values():
        assert str(UUID(card["oracle_id"])) == card["oracle_id"]
        assert str(UUID(card["printing_id"])) == card["printing_id"]
        assert card["source"].startswith("https://scryfall.com/card/")
        assert card["name"] and card["mechanics"] and card["oracle_text"] is not None
    for case in SCENARIOS:
        assert case["conditions"] and case["explanation"] and case["rules"]
        assert set(case["cards"]) <= CARDS.keys()
        assert all(len(set(pair)) == 2 and set(pair) <= set(case["cards"]) for pair in case["focus"])
        for package in case["packages"]:
            assert package["archetype"] in case["archetypes"]
            assert set(package["members"] + package.get("anchors", [])) <= set(case["cards"])
            assert all(type(package[flag]) is bool for flag in ("repeatable", "self_sustaining", "infinite"))
    assert any(gold["infinite"] for case in SCENARIOS for gold in case["packages"])
    assert any(gold["self_sustaining"] and not gold["infinite"] for case in SCENARIOS for gold in case["packages"])


@pytest.mark.parametrize("scenario", [item for item in _verified_scenarios() if "ledger" in item],
                         ids=lambda item: item["id"])
def test_independent_step_order_and_resource_balances(scenario):
    ledger = scenario["ledger"]
    initial = Counter(ledger["initial"])
    actual = initial.copy()
    formal = initial.copy()
    executable = True
    for step in ledger["steps"]:
        consume, produce = Counter(step["consume"]), Counter(step["produce"])
        if executable and consume <= actual:
            actual.subtract(consume)
            actual.update(produce)
        else:
            executable = False
        formal.subtract(consume)
        formal.update(produce)
    formal.subtract(initial)
    assert {key: count for key, count in formal.items() if count} == ledger["expected_delta"]
    assert executable == ledger["completes_without_external"]
    if executable:
        assert initial <= actual


# Contract and real Phase 1 → Phase 2 → Phase 3 integration checks.
@pytest.mark.parametrize("scenario", _verified_scenarios(), ids=lambda item: item["id"])
def test_golden_artifact_contracts_preserve_inventory_evidence_and_constraints(scenario):
    gate, pool, pairs, packages = _baseline(scenario["id"])
    active = {card.card_id for card in pool.candidates}
    anchors = set(gate.context.commander_card_ids)
    endpoints = active | anchors
    by_id = {edge.edge_id: edge for edge in pairs.edges}
    cards = {row.card_id: row for row in gate.snapshot.cards}
    assert len(by_id) == len(pairs.edges)
    assert len(active) == len(pool.candidates) and not active.intersection(anchors)
    assert all(gate.check_card(card_id).eligible for card_id in active)
    assert packages.provenance.pairwise_analysis_id == pairs.analysis_id
    assert packages.provenance.pool_id == pool.pool_id
    assert packages.run_id == pairs.run_id == pool.run_id
    assert not pairs.diagnostics.work_limit_reached and not packages.diagnostics.limited_templates

    for alias, reason in scenario.get("exclude", {}).items():
        card_id = CARDS[alias]["oracle_id"]
        assert card_id not in endpoints
        assert any(item.card_id == card_id and item.code == reason for item in pool.excluded)
    for edge in pairs.edges:
        ordered = edge.source_card_id, edge.target_card_id
        assert edge.directional and set(ordered) <= endpoints
        assert tuple(item.card_id for item in edge.evidence) == ordered
        assert edge.commander_anchor_ids == tuple(sorted(set(ordered) & anchors))
        for item, event in zip(edge.evidence, edge.event_ids, strict=True):
            text = getattr(cards[item.card_id], item.source_field)
            assert text[item.span_start:item.span_end] == item.source_text
            assert event == f"{item.card_id}:{item.source_field}:{item.ability_id}:{item.span_start}:{item.span_end}"
    assert len({item.package_id for item in packages.packages}) == len(packages.packages)
    signatures = set()
    for package in packages.packages:
        members = set(package.member_card_ids)
        commander_ids = set(package.commander_anchor_ids)
        participants = members | commander_ids
        assert len(members) == len(package.member_card_ids) and members <= active
        assert commander_ids <= anchors
        assert all(set(ids) <= participants for _, ids in package.functional_roles)
        assert package.edge_ids and set(package.edge_ids) <= by_id.keys()
        path = tuple(sorted((by_id[key].evidence[0].rule_id, by_id[key].event_ids) for key in package.edge_ids))
        signature = (_package_key(package), package.functional_roles, path, package.effect_ids)
        assert signature not in signatures
        signatures.add(signature)
        assert len(set(package.benefit_event_ids)) == len(package.benefit_event_ids)
        assert set(package.benefit_event_ids) <= set(package.effect_ids)
        for edge_id in package.edge_ids:
            edge = by_id[edge_id]
            assert {edge.source_card_id, edge.target_card_id} <= participants
            assert set(edge.event_ids) <= set(package.effect_ids)
            assert set(edge.evidence) <= set(package.evidence)
            assert set(edge.prerequisites) <= set(package.requirements)
            assert set(edge.limitations) <= set(package.limitations)
            assert {f"{edge.edge_id}:{item}" for item in edge.resource_requirements} <= set(package.resource_requirements)
            assert {f"{edge.edge_id}:{item}" for item in edge.timing_constraints} <= set(package.timing_constraints)
        for item in package.evidence:
            assert item.card_id in participants
            assert getattr(cards[item.card_id], item.source_field)[item.span_start:item.span_end] == item.source_text
    for partial in packages.partial:
        assert partial.missing_roles and set(partial.edge_ids) <= by_id.keys()
        assert set(partial.member_card_ids) <= active and set(partial.commander_anchor_ids) <= anchors
    if scenario.get("invalid_infinite"):
        assert all(not item.infinite and not item.self_sustaining for item in packages.packages)


def test_handoff_rejects_a_reversed_relation_even_with_valid_oracle_spans():
    gate, pool, pairs, _ = _baseline("parallel-lives-goblins")
    [edge] = [item for item in pairs.edges if _edge_key(item)[:2] == ("lives", "dragon")]
    reversed_edge = replace(
        edge, source_card_id=edge.target_card_id, target_card_id=edge.source_card_id,
        evidence=edge.evidence[::-1], event_ids=edge.event_ids[::-1],
    )
    with pytest.raises(PackageBuildError):
        SynergyPackageBuilder(synergy_context=CATALOG).build(gate, pool, replace(pairs, edges=(reversed_edge,)))


@pytest.mark.parametrize("case_id", (
    "sacrifice-payoff-missing-fodder", "paid-skeleton-loop", "gutterbones-returns-to-hand",
    "soldiers-outlet-payoff", "rest-in-peace-blocks-active-death-loop", "woe-strider-two-functional-roles",
))
def test_blood_artist_target_survives_the_handoff(case_id):
    _, _, pairs, packages = _baseline(case_id)
    matching = tuple(edge for edge in pairs.edges if _edge_key(edge)[:2] in {
        ("seer", "artist"), ("woe", "artist"),
    } and edge.event_type == "death")
    assert matching
    for edge in matching:
        assert "legal_target_at_resolution" in edge.prerequisites
        assert _covers("target:legal_resolution_target:target player", edge.prerequisites)
        for package in packages.packages:
            if edge.edge_id in package.edge_ids:
                assert set(edge.prerequisites) <= set(package.requirements)


def test_additional_cast_cost_and_role_evidence_survive_both_routes():
    _, pool, pairs, packages = _baseline("big-score-discard-and-two-strategies")
    card = next(card for card in pool.candidates if ALIASES[card.card_id] == "score")
    assert {"ramp", "strategy_synergy"} <= {
        profile.role for profile in card.role_profiles if profile.qualifies and profile.evidence_ids
    }
    routes = tuple(edge for edge in pairs.edges if _edge_key(edge)[:2] in {("score", "fireweaver"), ("lives", "score")})
    assert len(routes) == 2
    assert {"artifacts", "tokens"} <= {strategy for edge in routes for strategy in edge.strategy_ids}
    assert all(_covers("additional_cast_cost:discard a card", edge.resource_requirements) for edge in routes)
    for edge in routes:
        [package] = [item for item in packages.packages if edge.edge_id in item.edge_ids]
        assert _covers("additional_cast_cost:discard a card", package.resource_requirements)


@pytest.mark.parametrize("case_id", ("paid-colorless-blink", "priest-needs-two-distinct-creatures", "ayara-tap-and-black-fodder"))
def test_typed_costs_close_only_supported_paths(case_id):
    _, _, pairs, packages = _baseline(case_id)
    result = _findings(BY_ID[case_id], pairs, packages)
    assert not result["pairwise"]["fn"] and not result["packages"]["fn"]
    assert not result["critical"] and not result["classification"]
    if case_id == "paid-colorless-blink":
        assert any("source:returns_tapped" in package.limitations for package in packages.packages)


def test_handoff_rejects_an_edge_to_a_printing_id_instead_of_a_card_id():
    gate, pool, pairs, _ = _baseline("parallel-lives-goblins")
    [edge] = [item for item in pairs.edges if _edge_key(item)[:2] == ("lives", "dragon")]
    foreign = CARDS["dragon"]["printing_id"]
    forged = replace(edge, target_card_id=foreign, evidence=(edge.evidence[0], replace(edge.evidence[1], card_id=foreign)))
    with pytest.raises(PackageBuildError) as error:
        SynergyPackageBuilder(synergy_context=CATALOG).build(gate, pool, replace(pairs, edges=(forged,)))
    assert error.value.violations[0].code == "unknown_edge_endpoint"


def test_handoff_rejects_a_bound_edge_with_its_additional_cost_removed():
    gate, pool, pairs, _ = _baseline("big-score-discard-and-two-strategies")
    edge = next(item for item in pairs.edges if _edge_key(item)[:2] == ("score", "fireweaver"))
    stripped = replace(edge, resource_requirements=tuple(
        cost for cost in edge.resource_requirements if "additional_cast_cost" not in cost
    ))
    with pytest.raises(PackageBuildError) as error:
        SynergyPackageBuilder(synergy_context=CATALOG).build(gate, pool, replace(pairs, edges=(stripped,)))
    assert error.value.violations[0].code == "invalid_edge_semantics"


def test_copying_a_spell_does_not_charge_its_additional_cast_cost_again():
    case = {"id": "copy-additional-cost", "cards": ["reverberate", "score"]}
    _, _, pairs, _ = _run(case)
    [copied] = [edge for edge in pairs.edges if _edge_key(edge) == (
        "reverberate", "score", "amplifies", "spell_copy",
    )]
    assert "legal_copy_target" in copied.prerequisites
    assert "source:printed_spell_cost:{R}{R}" in copied.resource_requirements
    assert not _covers("discard a card", copied.resource_requirements)


# Property checks with generated permutations and a fixed seed.
def _permutation_cases():
    cases = _verified_scenarios().copy()
    Random(1502).shuffle(cases)
    seeds = (11, 23, 47, 71)
    return [pytest.param(case, seeds[index % len(seeds)], id=case["id"]) for index, case in enumerate(cases)]


@pytest.mark.parametrize("case,seed", _permutation_cases())
def test_generated_input_permutations_remain_byte_stable(case, seed):
    _, original_pool, original_pairs, original_packages = _baseline(case["id"])
    gate, pool, pairs, packages = _run(case, shuffle_seed=seed)
    assert [_json(item) for item in (pool, pairs, packages)] == [
        _json(item) for item in (original_pool, original_pairs, original_packages)
    ]
    shuffled_edges = list(pairs.edges)
    Random(seed).shuffle(shuffled_edges)
    replay = SynergyPackageBuilder(synergy_context=CATALOG).build(
        gate, pool, replace(pairs, edges=tuple(shuffled_edges) + tuple(shuffled_edges[:1])),
    )
    assert _json(replay) == _json(packages)


def test_shared_benefit_keeps_one_event_identity_across_different_packages():
    _, _, _, result = _baseline("shared-blink-payoff")
    packages = [item for item in result.packages
                if item.package_archetype == "blink_etb" and "wall" in _package_key(item)[1]]
    assert len(packages) == 2
    assert len({item.package_id for item in packages}) == 2
    assert len({event for item in packages for event in item.benefit_event_ids}) == 1


def test_unlocked_owned_pool_produces_the_rampant_growth_commander_package():
    scenario = BY_ID["rampant-growth-landfall-anchor"]
    _, pool, pairs, packages = _run(scenario, lock_cards=False)
    [candidate] = pool.candidates
    assert ALIASES[candidate.card_id] == "growth" and not candidate.locked
    found = _findings(scenario, pairs, packages)
    assert found["pairwise"]["tp"] == [_expected_edge_key(scenario["edges"][0])]
    assert found["packages"]["tp"] == [_expected_package_key(scenario["packages"][0])]


@pytest.mark.parametrize("case_id", (
    "scales-adds-counter", "doubling-is-placement", "season-ballista-placement", "shared-blink-payoff",
    "gisa-needs-ready-tap-and-positive-power", "cat-oven-awaits-untap",
    "blood-bond-self-sustaining-until-terminal", "woe-strider-two-functional-roles",
))
def test_resource_and_event_remediation_regressions(case_id):
    _, _, pairs, packages = _baseline(case_id)
    result = _findings(BY_ID[case_id], pairs, packages)
    assert not result["pairwise"]["fn"] and not result["packages"]["fn"]
    assert not result["critical"] and not result["classification"]


def _verified_seed_artifact(gate, pool, authored_edges):
    """Bind only hand-adjudicated directed fixture relations to the production contract."""
    maker = PairwiseSynergyAnalyzer(synergy_context=CATALOG)
    cards = {ALIASES[card.card_id]: card for card in gate.snapshot.cards}
    profiles = {
        ALIASES[card.card_id]: card.evaluations.strategy_synergy.mechanical_capabilities
        for card in pool.candidates
    }
    leader = gate.commander
    profiles[ALIASES[leader.card_id]] = extract_mechanical_capabilities(
        parse_card_snapshot(leader, CATALOG), name=leader.name, type_line=leader.type_line,
    ).capabilities
    parsed = {alias: parse_card_snapshot(cards[alias], CATALOG) for alias in profiles}
    priorities = {item.strategy_id: item.weight for item in pool.provenance.context.strategy_preferences}
    unavailable = []
    seeded = {}
    for reference in authored_edges:
        source, target, relation, event = _expected_edge_key(reference)
        declared = [
            rule for rule in CATALOG.rules if rule.event == event and canonical_rule_relation(rule) == relation
        ]
        existing_ids = set(seeded)
        for rule in declared:
            for left, right in product(profiles[source], profiles[target]):
                if (left.id, right.id) != (rule.source, rule.target):
                    continue
                src = _endpoint(cards[source], parsed[source], left)
                dst = _endpoint(cards[target], parsed[target], right)
                if isinstance(src, str) or isinstance(dst, str):
                    continue
                match = validate_interaction(
                    rule, left, right,
                    source_type=cards[source].type_line, target_type=cards[target].type_line,
                )
                if match is None or match.status not in {MatchStatus.VALIDATED, MatchStatus.CONDITIONAL}:
                    continue
                edge = maker._edge(rule, src, dst, match, priorities, gate.context.commander_card_ids)
                if all(_covers(requirement, _constraint_text(edge)) for requirement in reference["requires"]):
                    seeded[edge.edge_id] = edge
        if set(seeded) == existing_ids:
            unavailable.append((source, target, relation, event))
    provenance = PairwiseProvenance(
        gate.snapshot.snapshot_id, pool.pool_id, artifact_digest(pool.to_json()), pool.provenance.evaluation_snapshot_id,
        gate.context.commander_card_ids, PARSER_VERSION, CAPABILITY_VERSION, CATALOG.strategies.version,
        "golden-seeds-v1", artifact_digest(PairwiseConfig().version), pairwise_catalog_fingerprint(CATALOG),
        "golden-seeds-v1", gate.context.ruleset_version, gate.context.engine_versions,
    )
    edges = tuple(seeded[key] for key in sorted(seeded))
    verified = sum(edge.confidence == "rule_verified" for edge in edges)
    artifact = PairwiseSynergyAnalysis(
        artifact_digest((pool.pool_id, tuple(sorted(seeded)))), pool.run_id, CATALOG.rule_version,
        edges, PairwiseDiagnostics(len({(edge.source_card_id, edge.target_card_id) for edge in edges}),
                                    len(edges), verified, len(edges) - verified),
        provenance,
    )
    return artifact, unavailable


def test_isolated_phase3_recall_with_independently_verified_golden_seeds():
    total, recovered, unencodable = 0, 0, 0
    misses = {}
    for scenario in _verified_scenarios():
        expected = {
            _expected_package_key(package) for package in scenario["packages"] if package["classification"] != "partial"
        }
        if not expected:
            continue
        total += len(expected)
        gate, pool = _prepare(scenario)
        seeds, absent = _verified_seed_artifact(gate, pool, scenario["edges"])
        report = SynergyPackageBuilder(synergy_context=CATALOG).build(gate, pool, seeds)
        found = {_package_key(package) for package in report.packages}
        recovered += len(found & expected)
        if missing := expected - found:
            misses[scenario["id"]] = {"missing_packages": sorted(missing), "unsupported_golden_edges": absent}
            if absent:
                unencodable += len(missing)
    print(json.dumps({
        "recovered": recovered, "all_expected": total, "encodable_expected": total - unencodable,
        "all_expected_recall": recovered / total, "encodable_recall": recovered / (total - unencodable),
        "misses": misses,
    }, indent=2))
    assert total and recovered / total >= 0.90


# End-to-end Golden release gate. Verified unsupported positives count as misses.
def test_golden_release_gate():
    totals = {"pairwise": Counter(), "packages": Counter()}
    outcomes = {}
    unavailable = {}
    rejected_infinite = 0
    invalid_infinite = 0
    for case in _verified_scenarios():
        case_id = case["id"]
        try:
            _, pool, pairs, packages = _baseline(case_id)
        except Exception as error:
            # Preserve recall denominators and report blocked scenarios separately.
            unavailable[case_id] = f"{type(error).__name__}: {error}"
            pool = pairs = packages = None
        finding = _findings(case, pairs, packages)
        if case.get("invalid_infinite"):
            invalid_infinite += 1
            if packages is not None and all(not item.infinite and not item.self_sustaining for item in packages.packages):
                rejected_infinite += 1
        if pool is not None and (alias := case.get("must_be_multi_role")):
            card = next(card for card in pool.candidates if ALIASES[card.card_id] == alias)
            qualified = {profile.role for profile in card.role_profiles if profile.qualifies and profile.evidence_ids}
            if not {"ramp", "strategy_synergy"} <= qualified:
                finding["classification"].append(f"{alias} lacks evidence of Ramp and Strategy Synergy qualification")
        for layer, counts in totals.items():
            counts.update({key: len(items) for key, items in finding[layer].items()})
        if any(finding[key] for key in ("critical", "classification")) or any(
            finding[layer][kind] for layer in totals for kind in ("fp", "fn")
        ):
            outcomes[case_id] = finding
    metrics = {}
    for layer, count in totals.items():
        tp, fp, fn = count["tp"], count["fp"], count["fn"]
        metrics[layer] = {
            **count, "precision": tp / (tp + fp) if tp + fp else 0, "recall": tp / (tp + fn) if tp + fn else 0,
        }
    report = json.dumps({
        "metrics": metrics,
        "invalid_infinite_rejections": {"passed": rejected_infinite, "total": invalid_infinite},
        "blocked": unavailable,
        "findings": outcomes,
    }, indent=2, sort_keys=True)
    print(report)
    blockers = [
        case_id for case_id, finding in outcomes.items()
        if finding["critical"] or finding["classification"] or finding["pairwise"]["fp"] or finding["packages"]["fp"]
    ]
    meets_thresholds = all(result["precision"] >= 0.95 and result["recall"] >= 0.90 for result in metrics.values())
    assert meets_thresholds and not blockers and not unavailable and rejected_infinite == invalid_infinite, (
        f"Golden release gate failed. Metrics {metrics}. Blocking cases {blockers + list(unavailable)}. "
        "See captured findings."
    )
