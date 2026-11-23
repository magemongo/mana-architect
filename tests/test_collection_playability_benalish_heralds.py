"""Offline diagnostic for a verified creature activation outside efficiency scope."""

from dataclasses import replace
import json
from pathlib import Path

from mana_architect.collection_playability.contracts import EvaluationStatus
from mana_architect.deck_pipeline.inventory import CardSnapshot
from test_collection_playability_mechanics import _collection, catalog


def test_benalish_heralds_has_verified_draw_but_creature_type_excludes_efficiency(catalog):
    path = Path(__file__).parent / 'fixtures' / 'cpa_benalish_heralds.json'
    fixture = json.loads(path.read_text(encoding='utf-8'))
    row = fixture['card']
    for field in ('color_identity', 'produced_mana', 'keywords', 'colors'):
        row[field] = tuple(row[field])
    heralds = CardSnapshot(**row)
    expected = fixture['expected']
    assert heralds.card_id == '09ee1332-741f-4c55-abc3-8bcff9031cd9'
    assert heralds.type_line == 'Creature — Human Soldier'
    assert heralds.mana_cost == '{3}{W}' and heralds.color_identity == ('U', 'W')

    result = _collection(catalog, heralds).analyze(heralds.card_id)
    profile = result.profile
    [boundary] = profile.boundary_coverage
    assert (boundary.card_id, boundary.face_index, boundary.state) == (heralds.card_id, 0, 'verified')
    assert (boundary.source_start, boundary.source_end, boundary.source_text) == (0, 25, heralds.oracle_text)
    start, end = expected['cost_span']
    assert heralds.oracle_text[start:end + 1] == expected['activation_cost'] + ':'
    assert boundary.activation_cost == expected['activation_cost']
    start, end = expected['draw_instruction_span']
    assert heralds.oracle_text[start:end] == 'Draw a card.'

    [native] = [cap for cap in result.mechanics.capabilities if cap.id == 'draw']
    assert (native.costs, native.quantity.kind, native.quantity.value, native.timing) == (
        (expected['activation_cost'],), 'fixed', expected['draw_quantity'], 'activated',
    )
    assert native.targeting == 'none' and not native.resolution_targets
    assert (native.evidence.start, native.evidence.end) == tuple(expected['native_draw_span'])
    [unit] = profile.execution_units
    assert (unit.card_id, unit.face_index, unit.ability_id, unit.kind, unit.activation_cost, unit.timing) == (
        heralds.card_id, 0, boundary.boundary_id, 'activated', expected['activation_cost'], 'activated',
    )
    [draw] = [use for use in profile.mechanical_uses if use.role_id == 'draw']
    assert (draw.role_id, draw.execution_unit_id, draw.applicability, draw.safety, draw.target_coverage) == (
        'draw', unit.unit_id, 'conditional', 'conditional', 'not_targeted',
    )
    assert set(draw.requirements) == {
        'legal_activation_timing', 'meet_creature_tap_restriction_if_applicable', 'pay_ability_cost', 'tap_source',
    }
    [ref] = [ref for ref in profile.evidence if ref.evidence_id in draw.evidence_ids]
    assert (ref.card_id, ref.source, ref.source_field, ref.start, ref.end, ref.text) == (
        heralds.card_id, 'mechanical', 'oracle_text', *expected['native_draw_span'], 'Draw a card',
    )
    assert ref.text == heralds.oracle_text[ref.start:ref.end] == native.evidence.oracle_span
    assert ref.evidence_id in unit.evidence_ids and ref.evidence_id in boundary.recognized_evidence_ids
    [presence] = [use for use in profile.mechanical_uses if use.role_id == 'creature_presence']
    assert presence.execution_unit_id is None
    assert all(dependency.status == 'independent' for dependency in profile.card_dependencies)
    assert not profile.efficiency_findings
    [coverage] = [c for c in profile.coverage if c.source == 'effect_efficiency']
    assert coverage.status == EvaluationStatus.UNSUPPORTED

    # Synthetic type-only control isolates the artifact gate without asserting a real peer.
    artifact = replace(heralds, card_id='synthetic-benalish-artifact', type_line='Artifact')
    control = _collection(catalog, artifact).analyze(artifact.card_id).profile
    [finding] = control.efficiency_findings
    assert (finding.model_version, finding.assessment) == ('cpa-activation-draw-efficiency-v1', 'unknown')
    assert finding.peer_card_id is None
