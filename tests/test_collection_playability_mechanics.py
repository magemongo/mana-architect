from dataclasses import replace
import json
from pathlib import Path

import pytest

from mana_architect.collection_playability.adapters import CollectionInputs
from mana_architect.collection_playability.contracts import Classification, EvaluationStatus, PlayabilityContext
from mana_architect.collection_playability.contracts import EvidenceReference
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.strategy_synergy import load_synergy_context
from mana_architect.collection_playability.mechanics import evaluate_mechanics


@pytest.fixture(scope="module")
def catalog():
    return load_synergy_context()


def _collection(catalog, *cards, strategy_ids=()):
    printings = tuple(
        OwnedPrinting(i, f"fixture-{i}", card.card_id, card.name, "fixture", str(i), "en", "nonfoil", "nm", 1)
        for i, card in enumerate(cards, start=1)
    )
    snapshot = CollectionSnapshot(cards, printings, "2026-11-11")
    context = PlayabilityContext(snapshot.snapshot_id, "phase-1", ENGINE_VERSIONS, strategy_ids)
    return CollectionInputs(context, snapshot, catalog)


def _skeleton():
    data = json.loads((Path(__file__).parent / "fixtures/phase23/cards.json").read_text(encoding="utf-8"))
    row = next(row for row in data["cards"].values() if row["name"] == "Reassembling Skeleton")
    return CardSnapshot(
        row["oracle_id"], row["name"], row["type_line"], row["oracle_text"], row["mana_cost"],
        row["mana_value"], tuple(row["color_identity"]), tuple(row["produced_mana"]),
        tuple(row["keywords"]), row["commander_legality"],
    )


def _plummet():
    # Oracle record from the pinned 2026-11-11 local Scryfall snapshot.
    return CardSnapshot(
        "85bde6ac-3dd4-4946-8b57-24f57e3eae2b", "Plummet", "Instant",
        "Destroy target creature with flying.", "{1}{G}", 2, ("G",), (), (), "legal",
    )


def test_reassembling_skeleton_has_a_conditional_fodder_context(catalog):
    card = _skeleton()
    inputs = _collection(catalog, card, strategy_ids=("aristocrats",)).analyze(card.card_id)
    profile = inputs.profile
    assert profile.status == EvaluationStatus.EVALUATED
    [fodder] = [use for use in profile.mechanical_uses if use.role_id == "repeatable_sacrifice_fodder"]
    assert fodder.applicability == "conditional"
    assert fodder.target_coverage == "not_targeted"
    assert set(fodder.requirements) >= {"card_in_graveyard", "pay_ability_cost", "compatible_sacrifice_outlet"}
    [context] = [item for item in profile.viable_contexts if item.role_id == fodder.role_id]
    assert (context.strategy_id, context.viability, context.evidence_ids) == (
        "aristocrats", "conditional", fodder.evidence_ids,
    )
    [reference] = [ref for ref in profile.evidence if ref.evidence_id in fodder.evidence_ids]
    assert reference.text == card.oracle_text[reference.start:reference.end]
    [recursion] = [cap for cap in inputs.mechanics.capabilities if cap.id == "return_from_graveyard"]
    assert (recursion.source_zone, recursion.destination_zone, recursion.costs, recursion.returns_tapped) == (
        "graveyard", "battlefield", ("{1}{B}",), True,
    )
    assert profile.classification == Classification.UNCERTAIN
    assert not profile.bulk_review_eligibility


def test_return_to_hand_and_keyword_only_text_do_not_imply_fodder(catalog):
    source = _skeleton()
    hand = replace(source, oracle_text="{1}{B}: Return this card from your graveyard to your hand.")
    keyword = replace(source, card_id="keyword", oracle_text="Exploit")
    adapter = _collection(catalog, hand, keyword, strategy_ids=("aristocrats",))
    for card in (hand, keyword):
        profile = adapter.analyze(card.card_id).profile
        assert "repeatable_sacrifice_fodder" not in {use.role_id for use in profile.mechanical_uses}
        assert not profile.viable_contexts


def test_plummet_restriction_is_independent_of_mana_cost(catalog):
    card = _plummet()
    profiles = (
        _collection(catalog, card).analyze(card.card_id).profile,
        _collection(catalog, replace(card, mana_cost="{5}{G}", mana_value=6)).analyze(card.card_id).profile,
    )
    for profile in profiles:
        [use] = [use for use in profile.mechanical_uses if use.role_id == "spot_interaction"]
        assert (use.timing, use.target_coverage, use.applicability) == ("instant", "restricted", "narrow")
        assert use.restrictions == ("with flying",)
        assert "legal_target_at_resolution" in use.requirements
        assert "effect_efficiency" not in dict(profile.dimensions)
        [ref] = [ref for ref in profile.evidence if ref.evidence_id in use.evidence_ids]
        assert ref.source == "interaction"
        assert "target creature with flying" in ref.text
    assert profiles[0].mechanical_uses == profiles[1].mechanical_uses


def test_unknown_effect_abstains_and_strategy_order_does_not_change_findings(catalog):
    skeleton = _skeleton()
    plummet = _plummet()
    unknown = replace(
        plummet, card_id="draw-game", oracle_text="The game is a draw.", type_line="Enchantment",
    )
    adapter = _collection(catalog, skeleton, plummet, unknown, strategy_ids=("tokens", "aristocrats"))
    profile = adapter.analyze(unknown.card_id).profile
    assert profile.status == EvaluationStatus.UNSUPPORTED
    assert profile.confidence == "unknown"
    assert profile.mechanical_uses == ()
    assert adapter.analyze("missing").profile.status == EvaluationStatus.INSUFFICIENT_EVIDENCE

    first = _collection(catalog, skeleton, strategy_ids=("tokens", "aristocrats"))
    second = _collection(catalog, skeleton, strategy_ids=("aristocrats", "tokens"))
    assert first.analyze(skeleton.card_id).profile.profile_id == second.analyze(skeleton.card_id).profile.profile_id


def test_targeted_compound_spell_conditions_draw_but_ordinary_draw_does_not(catalog):
    base = _plummet()
    bounty = replace(base, card_id="bounty", type_line="Sorcery", mana_cost="{5}{U}",
                     oracle_text="Draw three cards. Target player mills three cards.")
    simple = replace(bounty, card_id="simple", oracle_text="Draw three cards.")
    adapter = _collection(catalog, bounty, simple)
    [draw] = [use for use in adapter.analyze("bounty").profile.mechanical_uses if use.role_id == "draw"]
    [unconditional] = [use for use in adapter.analyze("simple").profile.mechanical_uses if use.role_id == "draw"]
    assert draw.applicability == "conditional"
    assert "legal_target_at_resolution" in draw.requirements
    assert "legal_target_at_resolution" not in unconditional.requirements


@pytest.mark.parametrize(("text", "target", "restricted"), [
    ("Target player mills three cards.", "typed", False),
    ("Target opponent mills three cards.", "restricted", True),
    ("Mill three cards.", "not_targeted", False),
])
def test_mill_target_recipient_is_distinct_from_moved_cards(catalog, text, target, restricted):
    card = replace(_plummet(), oracle_text=text, type_line="Sorcery")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    [mill] = [use for use in profile.mechanical_uses if "mill" in use.role_id]
    assert mill.target_coverage == target
    assert ("target_opponent" in mill.restrictions) == restricted


def test_self_damage_does_not_count_as_burn(catalog):
    card = replace(_skeleton(), card_id="pup", type_line="Creature — Dog",
                   oracle_text="Whenever this creature is dealt damage, it deals that much damage to you.")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    assert "burn" not in {item.strategy_id for item in profile.viable_contexts}
    assert "self_damage" in {item.strategy_id for item in profile.viable_contexts}


def test_activated_mana_cost_is_present_in_acceleration_use(catalog):
    card = replace(_plummet(), card_id="ring", type_line="Artifact", oracle_text="{T}: Add {C}{C}.")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    [ramp] = [use for use in profile.mechanical_uses if use.role_id == "mana_acceleration"]
    assert ramp.applicability == "conditional"
    assert {"tap_source", "pay_ability_cost"} <= set(ramp.requirements)


def test_use_contract_rejects_invalid_values_and_foreign_evidence(catalog):
    card = _plummet()
    profile = _collection(catalog, card).analyze(card.card_id).profile
    [use] = [item for item in profile.mechanical_uses if item.role_id == "spot_interaction"]
    with pytest.raises(ValueError):
        replace(use, applicability="invalid")
    with pytest.raises(ValueError):
        replace(use, target_coverage="invalid")
    foreign = EvidenceReference("other", "mechanical", "v1", "event", "oracle_text", 0, 4, "Draw")
    with pytest.raises(ValueError, match="profile card"):
        replace(profile, evidence=(*profile.evidence, foreign),
                mechanical_uses=(replace(use, evidence_ids=(foreign.evidence_id,)),))


@pytest.mark.parametrize(("header", "expected"), [("Choose one —", 2), ("Choose two —", 1)])
def test_modal_interaction_units_keep_selected_modes(catalog, header, expected):
    card = replace(
        _plummet(), oracle_text=header + "\n• Destroy target creature.\n• Destroy target artifact.",
    )
    profile = _collection(catalog, card).analyze(card.card_id).profile
    units = [unit for unit in profile.execution_units if unit.mode_ids]
    assert len(units) == expected
    assert all(len(unit.mode_ids) == (1 if expected == 2 else 2) for unit in units)
    assert all("at_least_one_target_legal" in unit.requirements for unit in units)
    assert all(len(unit.target_evidence_ids) == len(unit.mode_ids) for unit in units)
    assert all(use.execution_unit_id in {unit.unit_id for unit in units}
               for use in profile.mechanical_uses if use.role_id == "spot_interaction")


def test_separate_activations_retain_distinct_execution_units(catalog):
    card = replace(
        _plummet(), type_line="Artifact",
        oracle_text="{U}: Counter target spell.\n{W}: Destroy all creatures.",
    )
    profile = _collection(catalog, card).analyze(card.card_id).profile
    units = profile.execution_units
    assert len(units) == 2
    targeted = next(unit for unit in units if unit.target_evidence_ids)
    untargeted = next(unit for unit in units if not unit.target_evidence_ids)
    assert targeted.activation_cost == "{U}"
    assert untargeted.activation_cost == "{W}"
    assert "at_least_one_target_legal" not in untargeted.requirements


def test_compound_spell_and_mana_share_resolved_native_boundaries(catalog):
    bounty = replace(
        _plummet(), card_id="bounty", type_line="Sorcery",
        oracle_text="Draw three cards. Target player mills three cards.",
    )
    ring = replace(_plummet(), card_id="ring", type_line="Artifact", oracle_text="{T}: Add {C}{C}.")
    adapter = _collection(catalog, bounty, ring)
    spell = adapter.analyze("bounty").profile
    draw = next(item for item in spell.mechanical_uses if item.role_id == "draw")
    mill = next(item for item in spell.mechanical_uses if item.role_id == "mill_opponent")
    assert draw.execution_unit_id == mill.execution_unit_id
    assert "legal_target_at_resolution" in draw.requirements
    [unit] = [item for item in spell.execution_units if item.unit_id == draw.execution_unit_id]
    assert unit.kind == "spell" and "at_least_one_target_legal" in unit.requirements
    assert set(draw.evidence_ids + mill.evidence_ids) <= set(unit.evidence_ids)
    profile = adapter.analyze("ring").profile
    mana = next(item for item in profile.mechanical_uses if item.role_id == "add_mana")
    ramp = next(item for item in profile.mechanical_uses if item.role_id == "mana_acceleration")
    assert mana.execution_unit_id == ramp.execution_unit_id
    [unit] = [item for item in profile.execution_units if item.unit_id == mana.execution_unit_id]
    assert unit.activation_cost == "{T}"


def test_capability_crossing_ability_boundaries_remains_uncertain(catalog):
    card = replace(
        _skeleton(), card_id="triggers",
        oracle_text="Whenever a creature dies, draw a card. Whenever an artifact enters, draw a card.",
    )
    profile = _collection(catalog, card).analyze(card.card_id).profile
    assert profile.confidence == "low"
    assert any(use.applicability == "unknown" and "execution_unit_unresolved" in use.requirements
               for use in profile.mechanical_uses)


@pytest.mark.parametrize(("text", "burn"), [
    ("Whenever a creature dies, this creature deals 2 damage to you.", False),
    ("Whenever a creature dies, this creature deals 2 damage to each opponent.", True),
])
def test_typed_damage_recipient_controls_burn_matching(catalog, text, burn):
    card = replace(_skeleton(), card_id="damage", oracle_text=text)
    profile = _collection(catalog, card, strategy_ids=("burn",)).analyze(card.card_id).profile
    assert ("burn" in {item.strategy_id for item in profile.viable_contexts}) is burn


def test_mixed_abilities_do_not_share_target_prerequisites(catalog):
    card = replace(
        _plummet(), type_line="Artifact",
        oracle_text="{T}: Add {C}.\nWhenever a creature dies, destroy target creature.",
    )
    profile = _collection(catalog, card).analyze(card.card_id).profile
    ramp = next(item for item in profile.mechanical_uses if item.role_id == "mana_acceleration")
    assert "legal_target_at_resolution" not in ramp.requirements


@pytest.mark.parametrize("text", [
    "Destroy target creature. Destroy target artifact.",
    "Destroy up to one target creature.",
])
def test_unsupported_target_sets_abstain(catalog, text):
    card = replace(_plummet(), oracle_text=text)
    profile = _collection(catalog, card).analyze(card.card_id).profile
    interaction = [item for item in profile.mechanical_uses if item.role_id == "spot_interaction"]
    assert interaction
    assert all(item.applicability == "unknown" for item in interaction)
    assert profile.confidence == "low"


@pytest.mark.parametrize("text", [
    "{T}, Sacrifice this artifact: Add {C}{C}.",
    "Add {R}{R}{R}.",
])
def test_mana_use_does_not_lose_activation_requirements(catalog, text):
    card = replace(_plummet(), card_id="mana", oracle_text=text,
                   type_line="Artifact" if ":" in text else "Instant")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    uses = [use for use in profile.mechanical_uses if use.role_id == "mana_acceleration"]
    assert uses
    if ":" in text:
        assert all({"pay_ability_cost", "tap_source"} <= set(use.requirements) for use in uses)


def test_flexible_damage_target_requires_evidence_or_abstention(catalog):
    card = replace(_plummet(), oracle_text="This spell deals 3 damage to any target.")
    profile = _collection(catalog, card, strategy_ids=("burn",)).analyze(card.card_id).profile
    contexts = [item for item in profile.viable_contexts if item.strategy_id == "burn"]
    assert not contexts or all(item.evidence_ids and item.requirements for item in contexts)
    if not contexts:
        assert profile.confidence in {"low", "unknown"}


def _assert_uncertain_use(profile, use, reason):
    assert use.applicability == "unknown"
    assert reason in use.requirements
    assert profile.confidence in {"low", "unknown"}
    assert all(not set(use.evidence_ids).intersection(context.evidence_ids)
               for context in profile.viable_contexts)
    assert set(use.evidence_ids) <= {item.evidence_id for item in profile.evidence}


def test_card_advantage_fallback_abstains_when_native_capability_is_missing(catalog):
    card = replace(_plummet(), card_id="draw-only", type_line="Sorcery", oracle_text="Draw two cards.")
    raw = _collection(catalog, card).card(card.card_id)
    assert raw.card_advantage.lines
    raw = replace(raw, mechanics=replace(raw.mechanics, capabilities=()))
    profile = evaluate_mechanics(raw, catalog.strategies)
    [use] = [item for item in profile.mechanical_uses if item.role_id == "draw"]
    _assert_uncertain_use(profile, use, "execution_unit_unresolved")


def test_interaction_boundary_ambiguity_preserves_evidence_and_abstains(catalog, monkeypatch):
    card = _plummet()
    raw = _collection(catalog, card).card(card.card_id)
    from mana_architect import ability_boundaries

    boundaries = ability_boundaries.resolve_ability_boundaries(
        raw.parsed, card_id=card.card_id, type_line=card.type_line,
    )
    monkeypatch.setattr(
        "mana_architect.collection_playability.mechanics.resolve_ability_boundaries",
        lambda *args, **kwargs: tuple(replace(item, status="ambiguous") for item in boundaries),
    )
    profile = evaluate_mechanics(raw, catalog.strategies)
    [use] = [item for item in profile.mechanical_uses if item.role_id == "spot_interaction"]
    _assert_uncertain_use(profile, use, "interaction_semantics_unresolved")


@pytest.mark.parametrize("text", [
    "Destroy target creature. Destroy target artifact.",
    "Destroy up to one target creature.",
])
def test_unsupported_target_resolution_keeps_evidence_without_strategy_claims(catalog, text):
    card = replace(_plummet(), oracle_text=text)
    profile = _collection(catalog, card).analyze(card.card_id).profile
    uses = [item for item in profile.mechanical_uses if item.role_id == "spot_interaction"]
    assert uses
    for use in uses:
        _assert_uncertain_use(profile, use, "interaction_semantics_unresolved")


def test_unresolved_clause_in_same_ability_prevents_positive_capability_claim(catalog):
    card = replace(_plummet(), card_id="compound", type_line="Sorcery",
                   oracle_text="Draw two cards. The game is a draw.")
    inputs = _collection(catalog, card).analyze(card.card_id)
    assert inputs.mechanics.unknowns
    [draw] = [use for use in inputs.profile.mechanical_uses if use.role_id == "draw"]
    _assert_uncertain_use(inputs.profile, draw, "native_effect_coverage_unresolved")
