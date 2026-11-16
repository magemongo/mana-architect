from dataclasses import replace
import json

import pytest

from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS
from mana_architect.deck_pipeline.candidate_pool import CandidatePoolBuilder, PoolConstraints
from mana_architect.deck_pipeline.contracts import DeckBuildingContext, StrategyPreference
from mana_architect.deck_pipeline.eligibility import EligibilityGate, RULESET_VERSION
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.deck_pipeline.pairwise_synergy import PairwiseSynergyAnalyzer
from mana_architect.deck_pipeline.synergy_packages import (
    PackageBuildError, PackageConfig, SynergyPackageBuilder, _fits_creature,
)
from mana_architect.strategy_synergy import load_synergy_context
from mana_architect.synergy_capabilities import MechanicalCapability, MechanicalObject, CapabilityEvidence


def _card(card_id, name, text, type_line, *, cost="{2}{B}"):
    return CardSnapshot(card_id, name, type_line, text, cost, 3, ("B",), (), (), "legal")


_LEADER = _card("leader", "Fixture Commander", "", "Legendary Creature — Human")
_ARTIFACT_LEADER = replace(_LEADER, oracle_text="Whenever an artifact you control enters, draw a card.")
_TREASURE = _card("treasure", "Fixture Maker", "Create a Treasure token.", "Sorcery")
_DOUBLER = _card(
    "multiplier", "Parallel Lives",
    "If you would create one or more tokens, create twice that many of those tokens instead.", "Enchantment",
)
_SEER = _card("seer", "Viscera Seer", "Sacrifice a creature: Scry 1.", "Creature — Vampire Wizard")
_ARTIST = _card(
    "artist", "Blood Artist",
    "Whenever Blood Artist or another creature dies, target player loses 1 life and you gain 1 life.",
    "Creature — Vampire",
)
_SKELETON = _card(
    "skeleton", "Reassembling Skeleton", "{1}{B}: Return this card from your graveyard to the battlefield tapped.",
    "Creature — Skeleton Warrior",
)
_TOKEN = _card("token", "Fixture Generator", "Create a 1/1 creature token.", "Sorcery")


@pytest.fixture
def catalog():
    return load_synergy_context()


def _pipeline(catalog, *cards, commander=_LEADER):
    owned = (commander, *cards)
    rows = tuple(
        OwnedPrinting(index, f"printing-{card.card_id}", card.card_id, card.name,
                      "test", str(index), "en", "nonfoil", "near_mint", 1)
        for index, card in enumerate(owned, 1)
    )
    snapshot = CollectionSnapshot(owned, rows, "2026-11-12")
    builder = CandidatePoolBuilder(synergy_context=catalog)
    gate = EligibilityGate(DeckBuildingContext(
        "test-packages", (commander.card_id,), snapshot.snapshot_id, RULESET_VERSION,
        ENGINE_VERSIONS, builder.config.version,
        (StrategyPreference("tokens"), StrategyPreference("artifacts"), StrategyPreference("aristocrats")),
    ), snapshot)
    pool = builder.build(gate, constraints=PoolConstraints(locked_card_ids=tuple(item.card_id for item in cards)))
    pairwise = PairwiseSynergyAnalyzer(synergy_context=catalog).analyze(gate, pool)
    return gate, pool, pairwise


def _json(artifact):
    return json.dumps(artifact.to_json(), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _package_type(analysis, kind):
    return tuple(package for package in analysis.packages if package.package_archetype == kind)


def test_fodder_type_check_is_sensitive_to_token_status_and_required_types():
    cap = MechanicalCapability(
        "death_payoff", "observe", "death",
        MechanicalObject("permanent", ("artifact", "creature"), restrictions=("all_listed_types", "nontoken")),
        CapabilityEvidence("a1", 0, 1, "a"),
    )
    assert _fits_creature(cap, {"artifact", "creature"}, set(), token=False)
    assert not _fits_creature(cap, {"artifact", "creature"}, set(), token=True)
    assert not _fits_creature(cap, {"creature"}, set(), token=False)


def test_recurring_sacrifice_package_pays_for_each_cycle_and_tracks_all_roles(catalog):
    gate, pool, pairs = _pipeline(catalog, _SEER, _ARTIST, _SKELETON)
    builder = SynergyPackageBuilder(synergy_context=catalog)
    pool_before, pairs_before = _json(pool), _json(pairs)
    result = builder.build(gate, pool, pairs)
    [package] = _package_type(result, "sacrifice_value")
    assert package.member_card_ids == ("artist", "seer", "skeleton")
    assert dict(package.functional_roles) == {"fodder": ("skeleton",), "outlet": ("seer",), "payoff": ("artist",)}
    assert package.dependency_status == "conditional"
    assert {"fodder_reaches_your_graveyard", "pay_recursion_cost"} <= set(package.requirements)
    assert package.repeatable and not package.self_sustaining and not package.infinite
    assert "{1}{B}" in package.renewal_path
    assert "skeleton:return_from_graveyard:{1}{B}" in package.resource_requirements
    assert {item.card_id for item in package.evidence} == set(package.member_card_ids)
    assert set(package.edge_ids) <= {edge.edge_id for edge in pairs.edges}
    assert _json(pool) == pool_before and _json(pairs) == pairs_before
    assert builder.build(gate, pool, pairs) is result
    assert result.to_json()["packages"][0]["infinite"] is False

    extra_cost = replace(_SEER, oracle_text="Discard a card, Sacrifice a creature: Scry 1.")
    gate, pool, pairs = _pipeline(catalog, extra_cost, _ARTIST, _SKELETON)
    [package] = _package_type(builder.build(gate, pool, pairs), "sacrifice_value")
    assert not package.repeatable and package.renewal_path is None


def test_sacrifice_without_fodder_is_partial_and_self_exile_prevents_renewal(catalog):
    builder = SynergyPackageBuilder(synergy_context=catalog)
    gate, pool, pairs = _pipeline(catalog, _SEER, _ARTIST)
    result = builder.build(gate, pool, pairs)
    assert not _package_type(result, "sacrifice_value")
    [partial] = result.partial
    assert partial.missing_roles == ("fodder",) and partial.reason_code == "missing_fodder"

    exiled = replace(_SKELETON, oracle_text=_SKELETON.oracle_text + "\nIf this creature would die, exile it instead.")
    gate, pool, pairs = _pipeline(catalog, _SEER, _ARTIST, exiled)
    result = builder.build(gate, pool, pairs)
    assert not _package_type(result, "sacrifice_value")
    assert dict(result.diagnostics.rejected_by_reason)["death_replaced"] == 1


def test_token_fodder_requires_the_same_outlet_and_a_compatible_payoff(catalog):
    commander = replace(
        _ARTIST, card_id="leader", type_line="Legendary Creature — Vampire",
        oracle_text="Whenever a creature dies, draw a card.",
    )
    gate, pool, pairs = _pipeline(catalog, _SEER, _TOKEN, commander=commander)
    builder = SynergyPackageBuilder(synergy_context=catalog)
    [package] = _package_type(builder.build(gate, pool, pairs), "sacrifice_value")
    assert package.member_card_ids == ("seer", "token")
    assert package.commander_anchor_ids == ("leader",)
    assert dict(package.functional_roles)["payoff"] == ("leader",)
    assert len(package.edge_ids) == 2
    assert not package.repeatable

    nontoken = replace(commander, oracle_text="Whenever a nontoken creature dies, draw a card.")
    gate, pool, pairs = _pipeline(catalog, _SEER, _TOKEN, commander=nontoken)
    result = builder.build(gate, pool, pairs)
    assert not _package_type(result, "sacrifice_value")
    assert dict(result.diagnostics.rejected_by_reason)["incompatible_fodder"] == 1

    token_link = next(edge for edge in pairs.edges if edge.source_card_id == "token" and edge.target_card_id == "seer")
    assert token_link.event_ids[1] == next(
        edge.event_ids[0] for edge in pairs.edges if edge.source_card_id == "seer" and edge.target_card_id == "leader"
    )


def test_edges_to_different_abilities_on_one_card_do_not_close_a_package(catalog):
    outlet = replace(_SEER, oracle_text=_SEER.oracle_text + "\nSacrifice an artifact: Draw a card.")
    payoff = replace(_ARTIST, oracle_text="Whenever you sacrifice an artifact, draw a card.")
    gate, pool, pairs = _pipeline(catalog, outlet, payoff, _TOKEN)
    feeder = next(edge for edge in pairs.edges if edge.source_card_id == "token" and edge.target_card_id == "seer")
    seed = next(edge for edge in pairs.edges if edge.source_card_id == "seer" and edge.target_card_id == "artist")
    assert feeder.event_ids[1] != seed.event_ids[0]
    result = SynergyPackageBuilder(synergy_context=catalog).build(gate, pool, pairs)
    assert not _package_type(result, "sacrifice_value")
    assert result.partial[0].reason_code == "missing_fodder"


def test_blink_and_token_amplification_have_evidence_without_assuming_a_loop(catalog):
    single = _card(
        "single", "Fixture One-Shot",
        "Exile target creature you control. Return it to the battlefield under its owner's control.", "Instant",
    )
    repeat = replace(single, card_id="repeat", name="Fixture Engine", type_line="Artifact",
                     oracle_text="{2}: " + single.oracle_text)
    payoff = _card("payoff", "Fixture Adept", "When this creature enters, draw a card.", "Creature")
    gate, pool, pairs = _pipeline(catalog, single, repeat, payoff, _TREASURE, _DOUBLER, commander=_ARTIFACT_LEADER)
    result = SynergyPackageBuilder(synergy_context=catalog).build(gate, pool, pairs)
    blinks = {dict(item.functional_roles)["enabler"][0]: item for item in _package_type(result, "blink_etb")
              if dict(item.functional_roles)["payoff"] == ("payoff",)}
    assert not blinks["single"].repeatable and blinks["repeat"].repeatable
    assert all(not item.infinite and item.benefit_event_ids for item in result.packages)
    [amplification] = _package_type(result, "token_amplification")
    assert dict(amplification.functional_roles) == {"amplifier": ("multiplier",), "generator": ("treasure",)}
    [artifact] = _package_type(result, "artifact_value")
    assert artifact.member_card_ids == ("treasure",)
    assert artifact.commander_anchor_ids == ("leader",)
    assert blinks["repeat"].benefit_event_ids == blinks["single"].benefit_event_ids


def test_duplicate_edges_preserve_canonical_output_and_foreign_provenance_fails(catalog):
    gate, pool, pairs = _pipeline(catalog, _TREASURE, _DOUBLER)
    builder = SynergyPackageBuilder(synergy_context=catalog)
    base = builder.build(gate, pool, pairs)
    duplicated = replace(pairs, edges=(pairs.edges + pairs.edges)[::-1])
    assert _json(SynergyPackageBuilder(synergy_context=catalog).build(gate, pool, duplicated)) == _json(base)
    with pytest.raises(PackageBuildError) as error:
        builder.build(gate, pool, replace(pairs, provenance=replace(pairs.provenance, pool_fingerprint="wrong")))
    assert error.value.violations[0].code == "analysis_context_mismatch"
    changed_strategies = replace(gate.context, strategy_preferences=(StrategyPreference("mill"),))
    with pytest.raises(PackageBuildError) as error:
        builder.build(EligibilityGate(changed_strategies, gate.snapshot), pool, pairs)
    assert error.value.violations[0].code == "package_context_mismatch"
    [edge] = pairs.edges
    foreign = replace(
        edge, source_card_id="unowned", evidence=tuple(
            replace(item, card_id="unowned") if item.card_id == edge.source_card_id else item for item in edge.evidence
        ),
    )
    with pytest.raises(PackageBuildError) as error:
        builder.build(gate, pool, replace(pairs, edges=(foreign,)))
    assert error.value.violations[0].code == "unknown_edge_endpoint"


def test_packages_keep_two_independently_verified_directions(catalog):
    oracle = (
        "When this creature enters, create a 1/1 creature token.\n"
        "Whenever a creature token you control enters, draw a card."
    )
    left = _card("left", "Left Witness", oracle, "Creature")
    right = _card("right", "Right Witness", oracle, "Creature")
    gate, pool, pairs = _pipeline(catalog, left, right)
    result = SynergyPackageBuilder(synergy_context=catalog).build(gate, pool, pairs)
    package_edges = {edge_id for package in _package_type(result, "tokens_wide") for edge_id in package.edge_ids}
    assert {(edge.source_card_id, edge.target_card_id) for edge in pairs.edges if edge.edge_id in package_edges} == {
        ("left", "right"), ("right", "left"),
    }


def test_two_life_feedback_edges_do_not_prove_renewal_when_a_trigger_is_limited(catalog):
    loss = _card("loss", "Loss Witness", "Whenever you gain life, target opponent loses that much life.", "Enchantment")
    gain = _card(
        "gain", "Gain Witness", "Whenever an opponent loses life, you gain that much life. "
        "This ability triggers only once each turn.", "Enchantment",
    )
    gate, pool, pairs = _pipeline(catalog, loss, gain)
    assert {"life_gain", "life_loss"} <= {edge.event_type for edge in pairs.edges}
    result = SynergyPackageBuilder(synergy_context=catalog).build(gate, pool, pairs)
    assert not _package_type(result, "life_drain_loop")
    assert all(not package.self_sustaining and not package.infinite for package in result.packages)


def test_bounded_search_and_member_limit_are_explicit(catalog):
    fodder = tuple(replace(_SKELETON, card_id=f"skeleton-{index}", name=f"Fixture Skeleton {index}")
                   for index in range(15))
    gate, pool, pairs = _pipeline(catalog, _SEER, _ARTIST, *fodder)
    limited = SynergyPackageBuilder(
        PackageConfig(max_expansions_per_template=2, beam_width=1), synergy_context=catalog,
    )
    result = limited.build(gate, pool, pairs)
    assert len(_package_type(result, "sacrifice_value")) == 1
    assert result.diagnostics.attempted_paths == 2
    assert result.diagnostics.limited_templates == ("sacrifice_value",)
    assert _json(result) == _json(limited.build(gate, pool, replace(pairs, edges=pairs.edges[::-1])))

    builder = SynergyPackageBuilder(PackageConfig(max_members=2), synergy_context=catalog)
    truncated = replace(pairs, diagnostics=replace(pairs.diagnostics, work_limit_reached=True))
    result = builder.build(gate, pool, truncated)
    assert result.diagnostics.pairwise_work_limit_reached
    assert result.partial[0].reason_code == "member_limit"
    assert not result.packages
