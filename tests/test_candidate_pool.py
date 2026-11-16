from dataclasses import replace
import json

import pytest

from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS, EvaluationAdapters
from mana_architect.deck_pipeline.candidate_pool import (
    CandidatePoolBuilder, CandidatePoolConfig, EvaluationSnapshot, PoolBuildError,
    PoolConstraints, RolePolicy,
)
from mana_architect.deck_pipeline.contracts import (
    CandidateStatus, DeckBuildingContext, EvaluationState, Role, StrategyPreference,
)
from mana_architect.deck_pipeline.eligibility import RULESET_VERSION, EligibilityGate
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.strategy_synergy import load_synergy_context


def _card(card_id, text="", *, card_type="Sorcery", colors=("B",), legality="legal", name=None):
    return CardSnapshot(
        card_id, name or card_id, card_type, text, "{1}{B}", 2.0,
        colors, (), (), legality,
    )


def _gate(cards, *, config=CandidatePoolConfig(), quantities=None):
    quantities = quantities or {}
    printings = tuple(
        OwnedPrinting(
            index, f"test-{card.card_id}", card.card_id, card.name,
            "test", str(index), "en", "nonfoil", "near_mint",
            quantities.get(card.card_id, 1),
        )
        for index, card in enumerate(cards, 1)
    )
    snapshot = CollectionSnapshot(cards, printings, "2026-11-12")
    context = DeckBuildingContext(
        "pool-test", ("leader",), snapshot.snapshot_id, RULESET_VERSION,
        ENGINE_VERSIONS, config.version,
        (StrategyPreference("tokens", 2), StrategyPreference("artifacts", 1)),
    )
    return EligibilityGate(context, snapshot)


_LEADER = _card(
    "leader", "Whenever an artifact you control enters, draw a card.",
    card_type="Legendary Creature — Human",
)


@pytest.fixture
def catalog():
    return load_synergy_context()


def _coverage(pool, role):
    return next(item for item in pool.role_coverage if item.role is role)


def _profile(candidate, role):
    return next(item for item in candidate.role_profiles if item.role is role)


def _config(**policies):
    config = CandidatePoolConfig(version="pool-fixture-v1")
    return replace(config, policies=tuple(policies.get(policy.role.value, policy) for policy in config.policies))


def test_native_engines_preserve_multirole_evidence_and_land_inventory(catalog):
    gate = _gate((
        _LEADER,
        _card("draw", "Draw two cards."),
        _card("single-draw", "Draw a card."),
        _card("treasure", "Create two Treasure tokens.", name="Treasure Maker"),
        _card("spot", "Destroy target creature."),
        _card("mass", "Exile all cards from target player's graveyard."),
        _card("off-color", "Create three Treasure tokens.", colors=("G",)),
        _card("banned", "Create three Treasure tokens.", legality="banned"),
        _card("basic", card_type="Basic Land — Swamp"),
        _card("back-land", "Draw a card. // {T}: Add {B}.", card_type="Enchantment // Land"),
    ), quantities={"basic": 68, "treasure": 3})
    pool = CandidatePoolBuilder(synergy_context=catalog).build(gate)
    by_id = {card.card_id: card for card in pool.candidates}
    assert set(by_id) >= {"draw", "treasure", "spot", "mass", "basic"}
    assert set(by_id).isdisjoint({"leader", "off-color", "banned", "back-land", "single-draw"})
    assert {issue.code for issue in pool.excluded} >= {
        "card_is_commander", "outside_color_identity", "banned_card",
    }
    treasure = by_id["treasure"]
    assert set(treasure.roles) >= {Role.RAMP, Role.STRATEGY_SYNERGY}
    assert treasure.available_quantity == 3 and treasure.copy_limit == 1
    assert _profile(treasure, Role.RAMP).subtypes == ("treasure_generation",)
    assert _profile(treasure, Role.RAMP).evidence_ids
    assert _profile(treasure, Role.STRATEGY_SYNERGY).evidence_ids
    assert _profile(by_id["draw"], Role.CARD_ADVANTAGE).score == 1
    assert _profile(by_id["spot"], Role.SPOT_INTERACTION).score_scale == "interaction_0_10"
    assert Role.MASS_INTERACTION in by_id["mass"].roles and Role.SPOT_INTERACTION not in by_id["mass"].roles
    lands = _coverage(pool, Role.LANDS)
    assert (lands.selected_count, lands.selected_copies, lands.shortfall, lands.unsupported) == (1, 68, 0, 1)
    assert _coverage(pool, Role.MASS_INTERACTION).shortfall == 11
    payload = pool.to_json()
    assert payload["audit"]["unique_candidate_count"] == len(by_id)
    assert payload["audit"]["membership_count"] == sum(len(ids) for ids in pool.role_memberships.values())
    assert all(row.selected_count == len(pool.role_memberships[row.role]) for row in pool.role_coverage)


def test_diversity_preserves_subtypes_and_keeps_alternatives(catalog):
    config = _config(
        ramp=RolePolicy(Role.RAMP, 2, 0.2, 2),
        strategy_synergy=RolePolicy(Role.STRATEGY_SYNERGY, 0, 0.99, 0),
    )
    config = replace(config, commander_synergy_override=None, minimum_subtype_coverage=1)
    gate = _gate((
        _LEADER,
        _card("rock-a", "{T}: Add {B}{B}.", card_type="Artifact"),
        _card("rock-b", "{T}: Add {B}{B}.", card_type="Artifact"),
        _card("rock-c", "{T}: Add {B}{B}.", card_type="Artifact"),
        _card("treasure", "Create a Treasure token."),
    ), config=config)
    builder = CandidatePoolBuilder(config, synergy_context=catalog)
    pool = builder.build(gate)
    assert pool.role_memberships[Role.RAMP] == ("rock-a", "treasure")
    assert _coverage(pool, Role.RAMP).subtype_counts == (("mana_rock", 1), ("treasure_generation", 1))
    assert {card.card_id for card in pool.reserve} >= {"rock-b", "rock-c"}
    assert any(reason.code == "role_budget" for item in pool.reserve for reason in item.selection_reasons)
    rerun = builder.build(gate, constraints=PoolConstraints(exclude_card_ids=("rock-a",)))
    assert rerun.role_memberships[Role.RAMP] == ("rock-b", "treasure")
    assert rerun.pool_id != pool.pool_id


def test_role_cap_controls_admission_and_other_roles_keep_coverage(catalog):
    config = _config(
        ramp=RolePolicy(Role.RAMP, 1, 0, 1),
        strategy_synergy=RolePolicy(Role.STRATEGY_SYNERGY, 1, 0, 1),
    )
    gate = _gate((
        _LEADER,
        _card("a-faster-ramp", "Create three Gold tokens."),
        _card("slow-land", "{T}: Add {B}{B}.", card_type="Land"),
    ), config=config)
    pool = CandidatePoolBuilder(config, synergy_context=catalog).build(gate)
    assert pool.role_memberships[Role.RAMP] == ("a-faster-ramp", "slow-land")
    assert _coverage(pool, Role.RAMP).selected_count == 2
    assert len({decision.card_id for decision in pool.decision_log}) == len(pool.decision_log)


def test_locks_are_legal_overrides_and_exclusions_skip_evaluation(catalog):
    config = _config(ramp=RolePolicy(Role.RAMP, 0, 1, 0))
    config = replace(config, commander_synergy_override=None)
    gate = _gate((
        _LEADER,
        _card("treasure", "Create two Treasure tokens."),
        _card("exclude", "Draw two cards."),
        _card("off-color", "Draw two cards.", colors=("G",)),
    ), config=config)
    builder = CandidatePoolBuilder(config, synergy_context=catalog)
    adapters = EvaluationAdapters(gate, catalog)
    before = adapters.evaluate
    evaluated = []

    def record(card_id):
        evaluated.append(card_id)
        return before(card_id)

    adapters.evaluate = record
    pool = builder.build(gate, adapters, constraints=PoolConstraints(("exclude",), ("treasure",)))
    assert evaluated == ["treasure"]
    assert pool.candidates[0].locked
    treasure_action = next(item for item in pool.decision_log if item.card_id == "treasure")
    assert treasure_action.new_status is CandidateStatus.CANDIDATE
    assert treasure_action.reason.code == "user_locked_override"
    assert {issue.code for issue in pool.excluded} >= {"user_excluded", "outside_color_identity"}

    for card_id, code in (("off-color", "outside_color_identity"), ("missing", "card_not_owned")):
        with pytest.raises(PoolBuildError) as caught:
            builder.build(gate, constraints=PoolConstraints(locked_card_ids=(card_id,)))
        assert {issue.code for issue in caught.value.violations} == {"invalid_locked_card", code}
    with pytest.raises(PoolBuildError) as conflicting:
        builder.build(gate, constraints=PoolConstraints(("exclude",), ("exclude",)))
    assert conflicting.value.violations[0].code == "conflicting_constraints"


def test_partial_snapshot_exposes_missing_unscored_and_stale_profiles(catalog):
    gate = _gate((
        _LEADER,
        _card("draw", "Draw two cards."),
        _card("missing"),
        _card("spot", "Destroy target creature."),
    ))
    adapters = EvaluationAdapters(gate, catalog)
    draw = replace(adapters.evaluate("draw"), interaction=None)
    removal = adapters.evaluate("spot")
    interaction = replace(
        removal.interaction,
        scoring_version=None,
        variants=tuple(replace(variant, spot_score=replace(variant.spot_score, total=None))
                       for variant in removal.interaction.variants),
    )
    removal = replace(removal, interaction=interaction)
    snapshot = EvaluationSnapshot(gate.context, (removal, draw), adapters.strategy_preferences)
    builder = CandidatePoolBuilder(synergy_context=catalog)
    pool = builder.build(gate, evaluation_snapshot=snapshot)
    draw_candidate, spot_candidate = pool.candidates
    assert draw_candidate.card_id == "draw" and spot_candidate.card_id == "spot"
    assert _profile(draw_candidate, Role.SPOT_INTERACTION).evaluation_state is EvaluationState.NOT_EVALUATED
    assert _profile(draw_candidate, Role.SPOT_INTERACTION).score is None
    assert _profile(spot_candidate, Role.SPOT_INTERACTION).evaluation_state is EvaluationState.UNSCORED
    assert any(reason.code == "unscored_fallback" for reason in spot_candidate.selection_reasons)
    [reserve] = pool.reserve
    assert reserve.card_id == "missing" and reserve.evaluation_completeness == "not_evaluated"
    assert _coverage(pool, Role.SPOT_INTERACTION).not_evaluated == 2
    assert _coverage(pool, Role.SPOT_INTERACTION).unscored == 1
    reordered = builder.build(gate, evaluation_snapshot=replace(snapshot, profiles=(draw, removal)))
    assert json.dumps(pool.to_json(), sort_keys=True) == json.dumps(reordered.to_json(), sort_keys=True)
    assert pool.provenance.evaluation_snapshot_id == snapshot.snapshot_id

    stale = replace(snapshot, profiles=(replace(draw, ramp=replace(draw.ramp, oracle_parser_version="stale")),))
    with pytest.raises(PoolBuildError) as caught:
        builder.build(gate, evaluation_snapshot=stale)
    assert caught.value.violations[0].code == "incompatible_evaluation"
    different = replace(snapshot, context=replace(gate.context, collection_snapshot_id="different"))
    with pytest.raises(PoolBuildError) as caught:
        builder.build(gate, evaluation_snapshot=different)
    assert caught.value.violations[0].code == "evaluation_snapshot_mismatch"


def test_strategic_override_records_role_and_commander_evidence(catalog):
    config = _config(
        ramp=RolePolicy(Role.RAMP, 0, 1.0, 1),
        strategy_synergy=RolePolicy(Role.STRATEGY_SYNERGY, 0, 1.0, 0),
    )
    gate = _gate((_LEADER, _card("treasure", "Create two Treasure tokens.", name="Treasure Maker")), config=config)
    pool = CandidatePoolBuilder(config, synergy_context=catalog).build(gate)
    [card] = pool.candidates
    reason = next(
        reason for reason in card.selection_reasons
        if reason.code == "commander_synergy_override" and reason.role is Role.RAMP
    )
    assert set(_profile(card, Role.RAMP).evidence_ids) < set(reason.evidence_ids)
    assert set(card.evaluations.strategy_synergy.commander_synergy.evidence_ids) <= set(reason.evidence_ids)


def test_strategy_weights_change_admission_without_changing_eligibility(catalog):
    config = _config(strategy_synergy=RolePolicy(Role.STRATEGY_SYNERGY, 1, 0.26, 1))
    cards = (
        replace(_LEADER, oracle_text=""),
        _card(
            "death-payoff", "Whenever a creature you control dies, target opponent loses 1 life and you gain 1 life.",
            card_type="Creature",
        ),
    )
    base = _gate(cards, config=config)
    builder = CandidatePoolBuilder(config, synergy_context=catalog)

    def with_weights(aristocrats, tokens):
        context = replace(base.context, strategy_preferences=(
            StrategyPreference("aristocrats", aristocrats), StrategyPreference("tokens", tokens),
        ))
        return builder.build(EligibilityGate(context, base.snapshot))

    primary = with_weights(9, 1)
    secondary = with_weights(1, 9)
    assert primary.eligible_count == secondary.eligible_count == 1
    assert primary.role_memberships[Role.STRATEGY_SYNERGY] == ("death-payoff",)
    assert secondary.role_memberships[Role.STRATEGY_SYNERGY] == ()
    assert secondary.reserve[0].card_id == "death-payoff"
    assert any(item.code == "dependency_gap" and item.card_id == "death-payoff" for item in primary.warnings)


def test_pool_can_exceed_99_unique_cards_and_replay(catalog):
    cards = (_LEADER, *(
        _card(f"land-{index:03}", card_type="Land") for index in range(104)
    ))
    gate = _gate(cards)
    builder = CandidatePoolBuilder(synergy_context=catalog)
    pool = builder.build(gate)
    rebuilt = builder.build(EligibilityGate(gate.context, replace(
        gate.snapshot, cards=gate.snapshot.cards[::-1], printings=gate.snapshot.printings[::-1],
    )))
    assert len(pool.candidates) == 104
    assert pool.role_memberships[Role.LANDS] == tuple(f"land-{index:03}" for index in range(104))
    assert json.dumps(pool.to_json(), sort_keys=True) == json.dumps(rebuilt.to_json(), sort_keys=True)
