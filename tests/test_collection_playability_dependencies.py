from dataclasses import replace
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_collection_playability_mechanics import _collection, _plummet, catalog
from mana_architect.ability_boundaries import resolve_ability_boundaries
from mana_architect.collection_playability.mechanics import evaluate_mechanics
from mana_architect.collection_playability.shared_tap import find_shared_tap_prerequisites
from mana_architect.deck_pipeline.contracts import artifact_digest
from mana_architect.deck_pipeline.inventory import CardSnapshot


def test_league_guildmage_copy_boundary_blocks_the_verified_draw_use(catalog):
    record = json.loads(Path(__file__).with_name('fixtures').joinpath('cpa_league_guildmage.json').read_text(encoding='utf-8'))
    metadata = record['card']
    for field in ('color_identity', 'produced_mana', 'keywords', 'colors'):
        metadata[field] = tuple(metadata[field])
    card = CardSnapshot(**metadata)
    result = _collection(catalog, card).analyze(card.card_id)
    profile = result.profile

    draw_source, copy_source = card.oracle_text.split('\n')
    draw_boundary, copy_boundary = resolve_ability_boundaries(result.parsed, card_id=card.card_id, type_line=card.type_line)
    assert [(b.kind, b.status, b.face_index, b.source_text) for b in (draw_boundary, copy_boundary)] == [
        ('activated', 'resolved', 0, draw_source), ('activated', 'resolved', 0, copy_source),
    ]
    assert (draw_boundary.activation_cost, copy_boundary.activation_cost) == ('{3}{U}, {T}', '{X}{R}, {T}')
    assert (draw_boundary.start, draw_boundary.end) == (0, len(draw_source))
    coverage = {item.boundary_id: item for item in profile.boundary_coverage}
    assert coverage[draw_boundary.ability_id].state == 'verified'
    assert coverage[copy_boundary.ability_id].state != 'verified'

    [native] = [cap for cap in result.mechanics.capabilities if cap.id == 'draw']
    assert (native.costs, native.quantity.kind, native.quantity.value, native.timing) == (('{3}{U}, {T}',), 'fixed', 1, 'activated')
    span = native.evidence
    assert (span.start, span.end, span.oracle_span) == (draw_source.index('Draw'), len(draw_source) - 1, 'Draw a card')
    [draw] = [use for use in profile.mechanical_uses if use.role_id == 'draw']
    [ref] = [ref for ref in profile.evidence if ref.evidence_id in draw.evidence_ids]
    assert (ref.card_id, ref.source, ref.record_id, ref.source_field, ref.start, ref.end, ref.text) == (
        card.card_id, 'mechanical', 'draw', 'oracle_text', span.start, span.end, span.oracle_span,
    )
    assert card.oracle_text[ref.start:ref.end] == ref.text
    assert ref.evidence_id in coverage[draw_boundary.ability_id].recognized_evidence_ids
    unit = next(u for u in profile.execution_units if u.unit_id == draw.execution_unit_id)
    assert (unit.card_id, unit.face_index, unit.ability_id, unit.kind, unit.activation_cost) == (
        card.card_id, 0, draw_boundary.ability_id, 'activated', '{3}{U}, {T}',
    )
    assert ref.evidence_id in unit.evidence_ids

    [dependency] = [d for d in profile.card_dependencies if d.status != 'independent']
    assert dependency.source_boundary_id == copy_boundary.ability_id
    assert (dependency.source_start, dependency.source_end, dependency.source_text) == (
        len(draw_source) + 1, len(card.oracle_text), copy_source,
    )
    assert dependency.affected_boundary_ids == (draw_boundary.ability_id,)
    assert (dependency.category, dependency.status, dependency.timing, dependency.face_relationship) == (
        'unmodeled_static_or_keyword', 'unknown', 'conditional', 'same_face',
    )
    assert dependency.reasons == ('card_dependency_unresolved',)
    assert (draw.applicability, draw.safety) == ('unknown', 'unsupported')
    assert {'card_dependency_unresolved', 'pay_ability_cost', 'tap_source'} <= set(draw.requirements)
    assert all(ref.evidence_id not in context.evidence_ids for context in profile.viable_contexts)
    assert not profile.efficiency_findings
    [shared_tap] = profile.shared_tap_prerequisites
    assert (shared_tap.card_id, shared_tap.face_index) == (card.card_id, 0)
    assert [(cost.boundary_id, cost.start, cost.end, cost.text) for cost in shared_tap.costs] == [
        (draw_boundary.ability_id, 0, 11, '{3}{U}, {T}'),
        (copy_boundary.ability_id, 26, 37, '{X}{R}, {T}'),
    ]

    # Synthetic control. The only Oracle edit is removal of the second activation.
    control = replace(card, card_id='synthetic-league-guildmage-draw-only', oracle_text=draw_source)
    control_profile = _collection(catalog, control).analyze(control.card_id).profile
    [control_draw] = [use for use in control_profile.mechanical_uses if use.role_id == 'draw']
    [control_boundary] = control_profile.boundary_coverage
    assert (control_boundary.source_text, control_boundary.state) == (draw_source, 'verified')
    assert (control_draw.applicability, control_draw.safety) == ('conditional', 'conditional')
    assert {'pay_ability_cost', 'tap_source'} <= set(control_draw.requirements)
    assert 'card_dependency_unresolved' not in control_draw.requirements
    assert not control_profile.shared_tap_prerequisites


def test_synthetic_shared_tap_draws_keep_local_evidence_under_draw_restriction(catalog):
    activations = (('{3}{U}, {T}', 'Draw a card', 1), ('{2}, {T}', 'Draw two cards', 2))
    text = '\n'.join(f'{cost}: {effect}.' for cost, effect, _ in activations)
    card = replace(_plummet(), card_id='synthetic-shared-tap-draw-pair', name='Synthetic Shared Tap Draw Pair',
                   type_line='Artifact', oracle_text=text)
    restriction = "Players can't draw cards."
    restricted = replace(card, oracle_text=f'{text}\n\n{restriction}')
    baseline_ids = set()
    baseline_group = None

    for subject, blocked in ((card, False), (restricted, True)):
        result = _collection(catalog, subject).analyze(subject.card_id)
        profile = result.profile
        boundaries = resolve_ability_boundaries(result.parsed, card_id=subject.card_id, type_line=subject.type_line)
        assert len(boundaries) == (3 if blocked else 2)
        draw_boundaries = boundaries[:2]
        draw_ids = {boundary.ability_id for boundary in draw_boundaries}
        assert len(draw_ids) == 2
        coverage = {item.boundary_id: item for item in profile.boundary_coverage}
        native_draws = [cap for cap in result.mechanics.capabilities if cap.id == 'draw']
        draw_uses = [use for use in profile.mechanical_uses if use.role_id == 'draw']
        units = {unit.unit_id: unit for unit in profile.execution_units if unit.kind == 'activated'}
        assert len(draw_uses) == len(native_draws) == len(units) == 2
        assert {use.execution_unit_id for use in draw_uses} == set(units)
        [group] = profile.shared_tap_prerequisites
        assert (group.card_id, group.face_index) == (subject.card_id, 0)
        assert find_shared_tap_prerequisites(subject, boundaries[::-1], draw_ids) == (group,)

        for boundary, (cost, effect, quantity) in zip(draw_boundaries, activations, strict=True):
            assert (boundary.card_id, boundary.face_index, boundary.kind, boundary.status) == (
                subject.card_id, 0, 'activated', 'resolved',
            )
            assert subject.oracle_text[boundary.start:boundary.end] == f'{cost}: {effect}.'
            assert boundary.activation_cost == subject.oracle_text[boundary.start:boundary.start + len(cost)] == cost
            [citation] = [item for item in group.costs if item.boundary_id == boundary.ability_id]
            assert (citation.start, citation.end, citation.text) == (boundary.start, boundary.start + len(cost), cost)
            assessment = coverage[boundary.ability_id]
            assert (assessment.state, assessment.activation_cost) == ('verified', cost)
            assert (assessment.source_start, assessment.source_end) == (boundary.start, boundary.end)

            [unit] = [unit for unit in units.values() if unit.ability_id == boundary.ability_id]
            assert (unit.card_id, unit.face_index, unit.activation_cost) == (subject.card_id, 0, cost)
            [native] = [cap for cap in native_draws if cap.costs == (cost,)]
            assert (native.timing, native.quantity.kind, native.quantity.value) == ('activated', 'fixed', quantity)
            span = native.evidence
            assert (span.start, span.end, span.oracle_span) == (boundary.start + len(cost) + 2, boundary.end - 1, effect)
            [use] = [use for use in draw_uses if use.execution_unit_id == unit.unit_id]
            [ref] = [ref for ref in profile.evidence if ref.evidence_id in use.evidence_ids]
            assert (ref.card_id, ref.source, ref.record_id, ref.source_field, ref.start, ref.end, ref.text) == (
                subject.card_id, 'mechanical', 'draw', 'oracle_text', span.start, span.end, effect,
            )
            assert ref.evidence_id in unit.evidence_ids and ref.evidence_id in assessment.recognized_evidence_ids
            assert {'pay_ability_cost', 'tap_source'} <= set(use.requirements)
            if blocked:
                assert (use.applicability, use.safety) == ('unknown', 'unsupported')
                assert 'card_dependency_unresolved' in use.requirements
                assert all(ref.evidence_id not in context.evidence_ids for context in profile.viable_contexts)
            else:
                assert (use.applicability, use.safety) == ('conditional', 'conditional')
                assert 'card_dependency_unresolved' not in use.requirements

        if blocked:
            assert draw_ids == baseline_ids
            assert group == baseline_group
            modifier = boundaries[2]
            assert (modifier.kind, modifier.status, modifier.source_text) == ('static', 'resolved', restriction)
            [dependency] = [item for item in profile.card_dependencies if item.category == 'draw_restriction']
            assert (dependency.source_boundary_id, dependency.source_start, dependency.source_end, dependency.source_text) == (
                modifier.ability_id, len(text) + 2, len(subject.oracle_text), restriction,
            )
            assert set(dependency.affected_boundary_ids) == draw_ids
            assert (dependency.status, dependency.timing, dependency.reasons) == (
                'potentially_affected', 'static', ('card_dependency_unresolved',),
            )
            assert not profile.efficiency_findings
        else:
            baseline_ids = draw_ids
            baseline_group = group

        legacy = profile.to_json()
        assert legacy.pop('shared_tap_prerequisites')
        expected_id = ('df70adf9eae1d91e8dd384c644c3cc385e8e255d8839b6725a20eba5583c6975' if blocked
                       else 'ca864ccd897ddab0fd93f52d05b92fbfa182174316a027803233ab9bf713353a')
        canonical = json.dumps(legacy, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        assert hashlib.sha256(canonical).hexdigest() == expected_id
        assert profile.profile_id != expected_id
        replay = _collection(catalog, subject).analyze(subject.card_id).profile
        assert (profile.to_json(), profile.profile_id) == (replay.to_json(), replay.profile_id)

    only_draw = replace(card, oracle_text=text.splitlines()[0])
    profile = _collection(catalog, only_draw).analyze(only_draw.card_id).profile
    assert 'shared_tap_prerequisites' not in profile.to_json()
    assert profile.efficiency_findings[0].assessment == 'unknown'
    # Compare with the original shared-tap baseline under its sorcery-only efficiency model.
    with patch('mana_architect.collection_playability.efficiency._activation_shape', return_value=None):
        legacy_profile = _collection(catalog, only_draw).analyze(only_draw.card_id).profile
    assert artifact_digest(legacy_profile.to_json()) == legacy_profile.profile_id == (
        '3ad18070b3aada97f6a3199c8cb682cce055c43b7ca5d5cf42d84e6b65524f46'
    )


@pytest.mark.parametrize('second', [
    '{2}: Draw two cards.',
    'This artifact has "{2}, {T}: Draw two cards."',
    'This artifact has "\n{2}, {T}: Draw two cards.\n"',
    '{2}: Tap this artifact. Draw two cards.',
    '{2}, Tap an untapped creature you control: Draw two cards.',
    '{2}, {T}: Draw two cards. Activate this ability by paying {2} rather than paying its activation cost.',
])
def test_shared_tap_requires_a_mandatory_top_level_source_cost(catalog, second):
    card = replace(_plummet(), card_id='synthetic-no-shared-tap', type_line='Artifact',
                   oracle_text=f'{{3}}{{U}}, {{T}}: Draw a card.\n\n{second}')
    profile = _collection(catalog, card).analyze(card.card_id).profile
    assert any(boundary.state == 'verified' and boundary.activation_cost == '{3}{U}, {T}'
               for boundary in profile.boundary_coverage)
    assert not profile.shared_tap_prerequisites
    assert 'shared_tap_prerequisites' not in profile.to_json()


@pytest.mark.parametrize('separator,type_line', [('\n\n', 'Artifact'), (' // ', 'Artifact // Artifact')])
def test_mana_tap_draw_still_abstains_with_draw_modifier(catalog, separator, type_line):
    cost = '{4}, {T}'
    card = replace(_plummet(), card_id='mana-tap-modifier', type_line=type_line,
                   oracle_text=f"{cost}: Draw a card.{separator}Players can't draw cards.")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    [draw] = [use for use in profile.mechanical_uses if use.role_id == 'draw']
    unit = next(unit for unit in profile.execution_units if unit.unit_id == draw.execution_unit_id)
    assert unit.activation_cost == cost
    assert draw.safety == 'unsupported'
    assert 'card_dependency_unresolved' in draw.requirements
    assert any(dep.category == 'draw_restriction' and unit.ability_id in dep.affected_boundary_ids
               for dep in profile.card_dependencies)
    assert not profile.viable_contexts
    assert not profile.efficiency_findings


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
