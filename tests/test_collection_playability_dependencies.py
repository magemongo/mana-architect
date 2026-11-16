from dataclasses import replace

import pytest

from test_collection_playability_mechanics import _collection, _plummet, catalog
from mana_architect.collection_playability.mechanics import evaluate_mechanics


@pytest.mark.parametrize("modifier", [
    "Players can't draw cards.",
    "Mana abilities can't be activated.",
    "If you would add mana, add that much instead.",
    "Prevent all damage that would be dealt to any target.",
    "Activated abilities cost {1} more to activate.",
    "Activated abilities can be activated only once each turn.",
    "Cascade",
    "Strange rules alter the game.",
])
def test_modifier_keeps_evidence_and_downgrades_affected_mana(catalog, modifier):
    card = replace(_plummet(), card_id="modifier", type_line="Artifact",
                   oracle_text="{T}: Add {C}.\n\n" + modifier)
    profile = _collection(catalog, card).analyze(card.card_id).profile
    [use] = [item for item in profile.mechanical_uses if item.role_id == "mana_acceleration"]
    if modifier == "Players can't draw cards.":
        assert use.applicability != "unknown"
    else:
        assert use.applicability == "unknown"
        assert profile.confidence == "low"
        assert not any(set(use.evidence_ids) & set(item.evidence_ids) for item in profile.viable_contexts)
        [dependency] = [item for item in profile.card_dependencies if item.status != "independent"]
        assert card.oracle_text[dependency.source_start:dependency.source_end] == dependency.source_text
    assert profile.profile_id == _collection(catalog, card).analyze(card.card_id).profile.profile_id


def test_simple_mana_is_independent_within_detection_scope(catalog):
    card = replace(_plummet(), card_id="simple", type_line="Artifact", oracle_text="{T}: Add {C}.")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    assert any(item.status == "independent" for item in profile.card_dependencies)


def test_draw_restriction_preserves_independent_mana(catalog):
    card = replace(_plummet(), card_id="mixed", type_line="Artifact",
                   oracle_text="{T}: Draw a card.\n\n{1}: Add {C}.\n\nPlayers can't draw cards.")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    draw = next(item for item in profile.mechanical_uses if item.role_id == "draw")
    mana = next(item for item in profile.mechanical_uses if item.role_id == "add_mana")
    assert draw.applicability == "unknown"
    assert "card_dependency_unresolved" in draw.requirements
    assert mana.applicability != "unknown"
    assert all(not set(draw.evidence_ids) & set(item.evidence_ids) for item in profile.viable_contexts)
    assert any(item.category == "draw_restriction" and item.source_text == "Players can't draw cards."
               for item in profile.card_dependencies)


@pytest.mark.parametrize("modifier", [
    "Whenever a creature dies, players can't draw cards this turn.",
    "{1}: Players can't draw cards this turn.",
])
def test_delayed_modifiers_are_uncertain_not_permanent_rules(catalog, modifier):
    card = replace(_plummet(), card_id="delayed", type_line="Artifact",
                   oracle_text="{T}: Draw a card.\n\n" + modifier)
    profile = _collection(catalog, card).analyze(card.card_id).profile
    [draw] = [item for item in profile.mechanical_uses if item.role_id == "draw"]
    assert draw.applicability == "unknown"
    [dependency] = [item for item in profile.card_dependencies if item.status != "independent"]
    assert dependency.timing == "conditional"
    assert dependency.source_text == modifier


def test_interaction_and_cross_face_modifier_are_conservative(catalog):
    card = replace(_plummet(), card_id="faces", type_line="Instant // Artifact",
                   oracle_text="Destroy target creature. // Players can't cast spells.",
                   layout="modal_dfc")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    interaction = [item for item in profile.mechanical_uses if item.role_id == "spot_interaction"]
    assert interaction and all(item.applicability == "unknown" for item in interaction)
    assert any(item.face_relationship == "cross_face" for item in profile.card_dependencies)


def test_independent_keyword_keeps_verified_mana(catalog):
    card = replace(_plummet(), card_id="flying", type_line="Artifact Creature",
                   oracle_text="Flying\n\n{T}: Add {C}.")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    [ramp] = [item for item in profile.mechanical_uses if item.role_id == "mana_acceleration"]
    assert ramp.applicability == "conditional"


def test_fallback_draw_keeps_dependency_reason(catalog, monkeypatch):
    card = replace(_plummet(), card_id="fallback", type_line="Sorcery",
                   oracle_text="Draw two cards.")
    raw = _collection(catalog, card).card(card.card_id)
    # Exercise the fallback when the general extractor cannot provide a draw.
    raw = replace(raw, mechanics=replace(raw.mechanics, capabilities=()))
    monkeypatch.setattr("mana_architect.collection_playability.mechanics.assess_dependencies",
                        lambda boundaries, assessments, groups: ((), frozenset({boundaries[0].ability_id})))
    profile = evaluate_mechanics(raw, catalog.strategies)
    [draw] = [item for item in profile.mechanical_uses if item.role_id == "draw"]
    assert draw.applicability == "unknown"
    assert "execution_unit_unresolved" in draw.requirements
    assert "card_dependency_unresolved" in draw.requirements


def test_spell_modifier_on_other_face_is_conditional_and_abstains(catalog):
    card = replace(
        _plummet(), card_id="spell-modifier", type_line="Sorcery // Artifact",
        oracle_text="Until end of turn, players can't activate mana abilities. // {T}: Add {C}.",
        layout="modal_dfc",
    )
    profile = _collection(catalog, card).analyze(card.card_id).profile
    [use] = [item for item in profile.mechanical_uses if item.role_id == "add_mana"]
    assert use.applicability == "unknown" and use.safety == "unsupported"
    assert "card_dependency_unresolved" in use.requirements
    [dependency] = [item for item in profile.card_dependencies if item.status != "independent"]
    assert dependency.timing == "conditional"
    assert dependency.face_relationship == "cross_face"
    assert dependency.source_text == "Until end of turn, players can't activate mana abilities."
    assert profile.confidence == "low"
    assert not any(set(use.evidence_ids) & set(item.evidence_ids) for item in profile.viable_contexts)


def test_unrecognized_nonstatic_text_cannot_establish_independence(catalog):
    card = replace(
        _plummet(), card_id="opaque-trigger", type_line="Artifact",
        oracle_text="{T}: Add {C}.\n\nWhenever a creature dies, exchange all resources.",
    )
    profile = _collection(catalog, card).analyze(card.card_id).profile
    [use] = [item for item in profile.mechanical_uses if item.role_id == "mana_acceleration"]
    assert use.applicability == "unknown" and use.safety == "unsupported"
    assert any(item.status == "unknown" and "exchange all resources" in item.source_text
               for item in profile.card_dependencies)
