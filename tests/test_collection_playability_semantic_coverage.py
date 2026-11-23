from dataclasses import replace

import pytest

from mana_architect.collection_playability.contracts import SemanticCoverageState
from mana_architect.collection_playability.semantic_coverage import assess_boundary
from mana_architect.ability_boundaries import resolve_ability_boundaries
from test_collection_playability_mechanics import _collection, _plummet, catalog


@pytest.mark.parametrize("text", [
    "{T}, Pay 2 life: Add {C}.",
    "{T}, Sacrifice this artifact: Add {C}.",
    "{T}, Discard a card: Add {C}.",
    "Destroy target creature if its controller has more life than you.",
    "As an additional cost to cast this spell, reveal your hand. Draw two cards.",
    "Draw two cards if you control an artifact.",
    "Draw two cards. Then return a card from your hand to its owner's library.",
    "{T}: Add {C}. Activate only if you control an artifact.",
    "Draw two cards unless an opponent pays {2}.",
])
def test_unverified_costs_and_dependencies_never_create_confident_use(catalog, text):
    card = replace(_plummet(), card_id="test", type_line="Artifact" if ":" in text else "Instant", oracle_text=text)
    result = _collection(catalog, card).analyze(card.card_id)
    assert all(item.state != SemanticCoverageState.VERIFIED for item in result.profile.boundary_coverage)
    for use in result.profile.mechanical_uses:
        if any(ref.evidence_id in use.evidence_ids and ref.source_field == "oracle_text" for ref in result.profile.evidence):
            assert use.applicability == "unknown"
            assert all(not set(use.evidence_ids) & set(context.evidence_ids)
                       for context in result.profile.viable_contexts)


def test_simple_verified_and_independent_unresolved_ability(catalog):
    card = replace(_plummet(), card_id="test", type_line="Artifact",
                   oracle_text="{T}: Add {C}.\n{1}, Sacrifice this artifact: Add {C}{C}.")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    coverage = {item.source_text: item for item in profile.boundary_coverage}
    assert coverage["{T}: Add {C}."].state == SemanticCoverageState.VERIFIED
    assert coverage["{1}, Sacrifice this artifact: Add {C}{C}."].state != SemanticCoverageState.VERIFIED
    assert all(card.oracle_text[item.source_start:item.source_end] == item.source_text for item in coverage.values())
    assert profile.profile_id == _collection(catalog, card).analyze(card.card_id).profile.profile_id


@pytest.mark.parametrize('text', [
    '{4}, {T}, Pay 2 life: Draw a card.',
    '{4}, {T}, Sacrifice this artifact: Draw a card.',
    '{4}, {T}, Discard a card: Draw a card.',
    '{4}, {T}, Remove a page counter from this artifact: Draw a card.',
    '{4}, {T}: Draw a card. Activate only as a sorcery.',
    '{4}, {T}: Draw a card, then discard a card.',
    '{4}, {T}: Draw a card. Then exile a card linked to this ability.',
    'This artifact has "{4}, {T}: Draw a card."',
    '{4}, {T}: Draw a card. Choose one —\n• Draw a card.\n• Add {C}.',
    '{4}, {T}: Draw a card. {4}, {T}: Unmodeled effect.',
    '{X}, {T}: Draw a card.',
    '{S}, {T}: Draw a card.',
    '{U/P}, {T}: Draw a card.',
    '{4}, {Q}: Draw a card.',
    '{4}, {T}: Draw two card.',
])
def test_mana_tap_draw_exclusions_keep_uncertainty(catalog, text):
    card = replace(_plummet(), card_id='test', type_line='Artifact', oracle_text=text)
    profile = _collection(catalog, card).analyze(card.card_id).profile
    assert profile.boundary_coverage
    assert not any(boundary.state == SemanticCoverageState.VERIFIED for boundary in profile.boundary_coverage)
    assert all(use.safety == 'unsupported' for use in profile.mechanical_uses if use.role_id == 'draw')
    assert not profile.viable_contexts
    assert not profile.efficiency_findings


def test_mana_tap_draw_requires_matching_native_fact_and_evidence(catalog):
    card = replace(_plummet(), card_id='native-draw-mismatch', type_line='Artifact',
                   oracle_text='{4}, {T}: Draw a card.')
    inputs = _collection(catalog, card).card(card.card_id)
    [boundary] = resolve_ability_boundaries(inputs.parsed, card_id=card.card_id, type_line=card.type_line)
    [draw] = [cap for cap in inputs.mechanics.capabilities if cap.id == 'draw']
    refs = inputs.profile.evidence
    assert assess_boundary(boundary, (draw,), refs, ()).state == SemanticCoverageState.VERIFIED
    for fact, evidence, unknowns in (
        (replace(draw, costs=('{2}, {T}',)), refs, ()),
        (replace(draw, quantity=replace(draw.quantity, value=2)), refs, ()),
        (draw, (), ()),
        (draw, refs, (draw.evidence,)),
    ):
        assert assess_boundary(boundary, (fact,), evidence, unknowns).state != SemanticCoverageState.VERIFIED
