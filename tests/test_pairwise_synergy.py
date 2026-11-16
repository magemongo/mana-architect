from collections import Counter
from dataclasses import replace
import json

import pytest

from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS, parse_card_snapshot
from mana_architect.deck_pipeline.candidate_pool import CandidatePoolBuilder, PoolConstraints
from mana_architect.deck_pipeline.contracts import CandidateStatus, DeckBuildingContext, StrategyPreference
from mana_architect.deck_pipeline.eligibility import EligibilityGate, RULESET_VERSION
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.deck_pipeline.pairwise_synergy import (
    PairwiseAnalysisError,
    PairwiseConfig,
    PairwiseSynergyAnalyzer,
    _endpoint,
)
from mana_architect.strategy_synergy import load_synergy_context
from mana_architect.synergy_capabilities import extract_mechanical_capabilities


def _card(card_id, text="", type_line="Sorcery", *, color=("B",)):
    return CardSnapshot(
        card_id, f"Fixture {card_id}", type_line, text, "{2}{B}", 3, color, (), (), "legal",
    )


_COMMANDER = _card(
    "commander", "Whenever an artifact you control enters, draw a card.", "Legendary Creature — Human",
)
_TREASURES = _card("maker", "Create two Treasure tokens.")
_DOUBLER = _card(
    "doubler", "If you would create one or more tokens, create twice that many of those tokens instead.",
    "Enchantment",
)
_OUTLET = _card(
    "outlet", "{T}, Sacrifice a creature: Add {B}. Activate only once each turn.", "Creature — Human",
)
_DEATH_PAYOFF = _card("payoff", "Whenever a creature dies, draw a card.", "Creature — Vampire")
_BLINK = _card(
    "blink", "Exile target creature you control. Return it to the battlefield under its owner's control.",
    "Instant",
)
_ETB = _card("etb", "When this creature enters, draw a card.", "Creature — Human")


@pytest.fixture
def catalog():
    return load_synergy_context()


def _build(catalog, *cards, commander=_COMMANDER, lock=True):
    snapshots = (commander, *cards)
    inventory = tuple(
        OwnedPrinting(i, f"printing-{card.card_id}", card.card_id, card.name,
                      "fixture", str(i), "en", "nonfoil", "near_mint", 1)
        for i, card in enumerate(snapshots, start=1)
    )
    snapshot = CollectionSnapshot(snapshots, inventory, "2026-11-12")
    builder = CandidatePoolBuilder(synergy_context=catalog)
    context = DeckBuildingContext(
        "fixture-run", (commander.card_id,), snapshot.snapshot_id, RULESET_VERSION,
        ENGINE_VERSIONS, builder.config.version, (StrategyPreference("tokens"), StrategyPreference("artifacts")),
    )
    gate = EligibilityGate(context, snapshot)
    constraints = PoolConstraints(locked_card_ids=tuple(card.card_id for card in cards) if lock else ())
    return gate, builder.build(gate, constraints=constraints)


def _edges(analysis, source, target):
    return tuple(edge for edge in analysis.edges if (edge.source_card_id, edge.target_card_id) == (source, target))


def _counts(analysis):
    counts = Counter()
    for row in analysis.diagnostics.matches_by_rule:
        counts[row.rule_id, row.reason_code] += row.count
    return counts


def _json(artifact):
    return json.dumps(artifact.to_json(), sort_keys=True, separators=(",", ":"), allow_nan=False)


def test_endpoint_checks_exact_oracle_spans_and_does_not_guess_modes(catalog):
    parsed = parse_card_snapshot(_TREASURES, catalog)
    profile = extract_mechanical_capabilities(parsed, type_line=_TREASURES.type_line)
    native = next(capability for capability in profile.capabilities if capability.id == "create_token")
    endpoint = _endpoint(_TREASURES, parsed, native)
    assert endpoint.evidence("rule").source_text == "Create two Treasure tokens"
    corrupt = replace(native, evidence=replace(native.evidence, oracle_span="Something else"))
    assert _endpoint(_TREASURES, parsed, corrupt) == "invalid_ability_evidence"

    modal = replace(_TREASURES, oracle_text="Choose one —\nCreate two Treasure tokens.")
    prefix = len("Choose one —\n")
    inferred = replace(native, evidence=replace(
        native.evidence, start=native.evidence.start + prefix, end=native.evidence.end + prefix,
    ))
    assert _endpoint(modal, parse_card_snapshot(modal, catalog), inferred) == "unresolved_mode"


def test_native_pool_to_pairwise_analysis_with_commander_and_canonical_evidence(catalog):
    gate, pool = _build(catalog, _TREASURES, _DOUBLER, _OUTLET, _DEATH_PAYOFF, _BLINK, _ETB)
    pool_before = _json(pool)
    analyzer = PairwiseSynergyAnalyzer(synergy_context=catalog)
    result = analyzer.analyze(gate, pool)
    [amp] = _edges(result, "doubler", "maker")
    [death] = _edges(result, "outlet", "payoff")
    [blink] = _edges(result, "blink", "etb")
    [leader] = _edges(result, "maker", "commander")
    assert amp.relation == "amplifies" and amp.confidence == "rule_verified"
    assert death.relation == "enables" and death.confidence == "conditional"
    assert "creature_moves_from_battlefield_to_graveyard" in death.prerequisites
    assert "source:can_pay_creature_tap_or_untap_cost" in death.prerequisites
    assert "source:once_each_turn" in death.timing_constraints
    assert "source:{T}, Sacrifice a creature" in death.resource_requirements
    assert death.strength < death.base_strength
    assert blink.event_type == "enter" and "legal_target_at_resolution" in blink.prerequisites
    assert leader.commander_anchor_ids == ("commander",)
    assert leader.strategy_ids == ("artifacts",)
    assert all(candidate.card_id != "commander" for candidate in pool.candidates)
    assert not _edges(result, "maker", "doubler")
    assert not _edges(result, "payoff", "outlet")
    assert result.provenance.pool_id == pool.pool_id
    assert result.provenance.evaluation_snapshot_id == pool.provenance.evaluation_snapshot_id
    assert result.run_id == pool.run_id
    assert len({edge.edge_id for edge in result.edges}) == len(result.edges)
    snapshots = {card.card_id: card for card in gate.snapshot.cards}
    for edge in result.edges:
        assert edge.directional and len(edge.evidence) == 2 and len(edge.event_ids) == 2
        assert edge.explanation and edge.rule_version == result.rule_catalog_version
        for span in edge.evidence:
            text = getattr(snapshots[span.card_id], span.source_field)
            assert text[span.span_start:span.span_end] == span.source_text
    assert _json(pool) == pool_before
    assert analyzer.analyze(gate, pool) is result
    fresh_result = PairwiseSynergyAnalyzer(synergy_context=catalog).analyze(
        gate, replace(pool, candidates=pool.candidates[::-1]),
    )
    assert _json(fresh_result) == _json(result)


def test_sacrifice_replacement_and_controller_mismatch_reject_death_edges(catalog):
    replacement = _card(
        "replacement",
        "Whenever a creature dies, draw a card.\nIf a creature would die, exile it instead.",
        "Enchantment",
    )
    opposing = _card("opposing", "Whenever a creature an opponent controls dies, draw a card.", "Creature")
    discard = _card("discard", "Whenever you discard a card, draw a card.", "Creature")
    exile = _card("exile", "Exile target creature.", "Instant")
    gate, pool = _build(catalog, _OUTLET, _DEATH_PAYOFF, replacement, opposing, discard, exile)
    result = PairwiseSynergyAnalyzer(synergy_context=catalog).analyze(gate, pool)
    assert _edges(result, "outlet", "payoff")
    assert all(not _edges(result, "outlet", target) for target in ("replacement", "opposing", "discard"))
    assert not _edges(result, "exile", "payoff")
    counts = _counts(result)
    assert counts["sacrifice_triggers_death_payoff", "death_replaced"] == 1
    assert counts["sacrifice_triggers_death_payoff", "invalid"] == 1


def test_unrelated_zones_effects_and_opponent_events_do_not_join(catalog):
    cards = (
        _card("land-search", "Search your library for a basic land card, reveal it, put it into your hand, then shuffle."),
        _card("landfall", "Landfall — Whenever a land you control enters, draw a card.", "Enchantment"),
        _card("wipe", "Destroy all creatures."),
        _card("hexproof", "Hexproof", "Creature"),
        _card("token-move", "Put a token onto the battlefield."),
        _card("opponent-life", "Each opponent gains 3 life."),
        _card("lifegain-payoff", "Whenever you gain life, draw a card.", "Creature"),
        _card("opponent-blink",
              "Exile target creature an opponent controls. Return it to the battlefield under its owner's control.",
              "Instant"),
        _DOUBLER, _ETB,
    )
    gate, pool = _build(catalog, *cards)
    result = PairwiseSynergyAnalyzer(synergy_context=catalog).analyze(gate, pool)
    for source, target in (
        ("land-search", "landfall"), ("hexproof", "wipe"), ("wipe", "hexproof"),
        ("doubler", "token-move"), ("opponent-life", "lifegain-payoff"), ("opponent-blink", "etb"),
    ):
        assert not _edges(result, source, target)
    assert _counts(result)["lifegain_triggers_payoff", "invalid"] == 1
    assert any(
        row.rule_id == "blink_retriggers_etb" and "opponent" in row.detail
        for row in result.diagnostics.matches_by_rule
    )


def test_conditional_quantity_and_unresolved_modes_faces_and_profiles(catalog):
    variable = replace(_TREASURES, oracle_text="Create X Treasure tokens.", mana_cost="{X}{B}")
    modal = _card("modal", "Choose one —\n• Create a Treasure token.\n• Draw a card.")
    back_face = _card("back", "Draw a card. // Create a Treasure token.", "Sorcery // Sorcery")
    stale = _card("stale", "Create a Treasure token.")
    missing = _card("missing", "Create a Treasure token.")
    broken_evidence = _card("broken", "Create a Treasure token.")
    gate, pool = _build(catalog, variable, modal, back_face, stale, missing, broken_evidence, _DOUBLER)

    altered = []
    for card in pool.candidates:
        if card.card_id == "missing":
            card = replace(card, evaluations=None)
        elif card.card_id in {"stale", "broken"}:
            native = card.evaluations.strategy_synergy
            if card.card_id == "stale":
                native = replace(native, catalog_versions=())
            else:
                native = replace(native, mechanical_capabilities=tuple(
                    replace(capability, evidence=replace(capability.evidence, ability_id="invalid"))
                    for capability in native.mechanical_capabilities
                ))
            card = replace(card, evaluations=replace(card.evaluations, strategy_synergy=native))
        altered.append(card)

    result = PairwiseSynergyAnalyzer(synergy_context=catalog).analyze(gate, replace(pool, candidates=tuple(altered)))
    [edge] = _edges(result, "doubler", "maker")
    assert edge.confidence == "conditional" and "positive_event_quantity" in edge.prerequisites
    assert "target:printed_spell_cost:{X}{B}" in edge.resource_requirements
    assert all(not _edges(result, "doubler", card.card_id) for card in (modal, back_face, stale, missing, broken_evidence))
    coverage = {(issue.card_id, issue.code) for issue in result.diagnostics.coverage}
    assert {
        ("missing", "missing_mechanical_profile"), ("stale", "incompatible_mechanical_profile"),
        ("broken", "invalid_ability_evidence"), ("modal", "unresolved_abilities"), ("back", "unresolved_face"),
    } <= coverage


def test_duplicate_detection_and_deterministic_work_budget(catalog):
    gate, pool = _build(catalog, _TREASURES, _DOUBLER, _OUTLET, _DEATH_PAYOFF)
    analyzer = PairwiseSynergyAnalyzer(synergy_context=catalog)
    normal = analyzer.analyze(gate, pool)
    candidates = []
    for card in pool.candidates:
        if card.card_id == "maker":
            native = card.evaluations.strategy_synergy
            native = replace(native, mechanical_capabilities=native.mechanical_capabilities * 2)
            card = replace(card, evaluations=replace(card.evaluations, strategy_synergy=native))
        candidates.append(card)
    duplicates = analyzer.analyze(gate, replace(pool, candidates=tuple(candidates)))
    assert duplicates.edges == normal.edges
    assert _counts(duplicates)["token_multiplier_amplifies_creation", "duplicate_evidence"] == 1
    assert not normal.diagnostics.work_limit_reached
    bounded = PairwiseSynergyAnalyzer(PairwiseConfig(max_rule_matches=1), synergy_context=catalog)
    result = bounded.analyze(gate, pool)
    assert result.diagnostics.work_limit_reached
    assert result.diagnostics.candidate_rule_matches == 1
    assert result.analysis_id != normal.analysis_id
    assert bounded.analyze(gate, pool) == result


def test_index_skips_unrelated_pairs_and_can_explicitly_include_reserve(catalog):
    fillers = tuple(_card(f"draw-{index}", "Draw two cards.") for index in range(25))
    gate, pool = _build(catalog, _TREASURES, _DOUBLER, *fillers)
    [doubler] = [card for card in pool.candidates if card.card_id == _DOUBLER.card_id]
    pool = replace(
        pool, candidates=tuple(card for card in pool.candidates if card is not doubler),
        reserve=(replace(doubler, status=CandidateStatus.RESERVE),),
    )
    active = PairwiseSynergyAnalyzer(synergy_context=catalog).analyze(gate, pool)
    assert not _edges(active, "doubler", "maker")
    combined = PairwiseSynergyAnalyzer(
        PairwiseConfig(include_reserve=True), synergy_context=catalog,
    ).analyze(gate, pool)
    assert _edges(combined, "doubler", "maker")
    assert combined.diagnostics.candidate_rule_matches == active.diagnostics.candidate_rule_matches + 1
    assert combined.diagnostics.considered_pairs < len(pool.candidates)


def test_pool_provenance_and_ownership_are_enforced(catalog):
    gate, pool = _build(catalog, _TREASURES)
    analyzer = PairwiseSynergyAnalyzer(synergy_context=catalog)
    with pytest.raises(PairwiseAnalysisError) as mismatched:
        analyzer.analyze(gate, replace(pool, run_id="other"))
    assert {issue.code for issue in mismatched.value.violations} == {"pool_run_mismatch"}

    [original] = pool.candidates
    fake = replace(original, card_id="unowned", evaluations=None)
    with pytest.raises(PairwiseAnalysisError) as unowned:
        analyzer.analyze(gate, replace(pool, candidates=(fake,)))
    assert {issue.code for issue in unowned.value.violations} >= {"card_not_owned", "pool_inventory_mismatch"}

    changed = replace(gate.context, strategy_preferences=(StrategyPreference("mill"),))
    with pytest.raises(PairwiseAnalysisError) as different_strategy:
        analyzer.analyze(EligibilityGate(changed, gate.snapshot), pool)
    assert different_strategy.value.violations[0].code == "pool_context_mismatch"
