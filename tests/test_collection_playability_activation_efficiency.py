"""Pinned and synthetic tests for one printed draw activation."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from mana_architect.collection_playability.contracts import EvaluationStatus
from mana_architect.deck_pipeline.inventory import CardSnapshot
from test_collection_playability_repeatable_draw import analyze, catalog, real


FIXTURES = Path(__file__).with_name('fixtures')
PINNED = json.loads((FIXTURES / 'cpa_activation_draw_efficiency.json').read_text(encoding='utf-8'))
MODEL = 'cpa-activation-draw-efficiency-v1'


def pinned(name):
    row = PINNED['cards'][name]
    return CardSnapshot(row['oracle_id'], row['name'], row['type_line'], row['oracle_text'], row['mana_cost'],
                        row['cmc'], tuple(row['color_identity']), tuple(row['produced_mana']), tuple(row['keywords']),
                        row['commander_legality'], tuple(row['colors']), row['layout'])


def coverage(profile):
    return next(item for item in profile.coverage if item.source == 'effect_efficiency')


def test_pinned_books_compare_one_activation_with_exact_endpoint_evidence(catalog):
    tome, encyclopedia = real('Jayemdae Tome'), pinned('Arcane Encyclopedia')
    profile = analyze(catalog, tome, (encyclopedia,))
    [finding] = profile.efficiency_findings
    assert (finding.model_version, finding.assessment, finding.peer_card_id) == (
        MODEL, 'higher_mana_for_same_draw', encyclopedia.card_id,
    )
    [unit] = profile.execution_units
    assert finding.execution_unit_id == unit.unit_id and unit.kind == 'activated'
    assert {'tap_source', 'pay_ability_cost', 'owned peer scope'} <= set(finding.conditions)
    assert '1 more generic mana for the same draw of 1 card.' in ' '.join(finding.explanation)
    assert any('later activation' in reason and 'tap again' in reason for reason in finding.explanation)
    refs = [ref for ref in profile.evidence if ref.evidence_id in finding.evidence_ids]
    assert len(refs) == 4
    peer_profile = analyze(catalog, encyclopedia)
    for card, endpoint, cost in ((tome, profile, '{4}, {T}'), (encyclopedia, peer_profile, '{3}, {T}')):
        [boundary] = endpoint.boundary_coverage
        [native] = [ref for ref in refs if ref.card_id == card.card_id and ref.source == 'mechanical']
        [citation] = [ref for ref in refs if ref.card_id == card.card_id and ref.source == 'activation_cost']
        assert native.evidence_id in boundary.recognized_evidence_ids
        assert native.text == card.oracle_text[native.start:native.end] == 'Draw a card'
        assert (citation.start, citation.end, citation.record_id, citation.source_version) == (0, len(cost), boundary.boundary_id, MODEL)
        assert citation.source_field == 'oracle_text'
        assert citation.text == card.oracle_text[citation.start:citation.end] == cost
        assert citation.extraction_boundary_id is citation.extraction_parser_version is None
    assert coverage(profile).status == EvaluationStatus.EVALUATED
    assert profile.to_json() == analyze(catalog, tome, (encyclopedia,), reverse=True).to_json()
    assert profile.profile_id == analyze(catalog, tome, (encyclopedia,)).profile_id

    # Synthetic mutation of casting metadata. Activation costs and evidence remain fixed.
    expensive_cast = replace(tome, mana_cost='{9}{U}', mana_value=10)
    assert analyze(catalog, expensive_cast, (encyclopedia,)).efficiency_findings == (finding,)


@pytest.mark.parametrize('oracle_text,identity', [
    ('{3}, {T}: Draw two cards.', ()),
    ('{2}{C}, {T}: Draw a card.', ()),
    ('{3}, {T}: Draw a card.', ('U',)),
    ('{1}, {T}, Pay 2 life: Draw a card.', ()),
    ('{1}, {T}: Draw a card. Activate only as a sorcery.', ()),
    ("{1}, {T}: Draw a card.\n\nPlayers can't draw cards.", ()),
    ('{X}, {T}: Draw a card.', ()),
])
def test_incompatible_or_unmodeled_peers_leave_an_unknown(catalog, oracle_text, identity):
    tome = real('Jayemdae Tome')
    peer = replace(pinned('Arcane Encyclopedia'), card_id='synthetic-peer', oracle_text=oracle_text, color_identity=identity)
    profile = analyze(catalog, tome, (peer,))
    [finding] = profile.efficiency_findings
    assert finding.assessment == 'unknown' and finding.peer_card_id is None
    assert coverage(profile).status == EvaluationStatus.INSUFFICIENT_EVIDENCE


def test_owned_sorcery_and_artifact_evaluations_do_not_mix(catalog):
    tome = real('Jayemdae Tome')
    peer = replace(tome, card_id='synthetic-sorcery', type_line='Sorcery', oracle_text='Draw a card.', mana_cost='{1}')
    [activation] = analyze(catalog, tome, (peer,)).efficiency_findings
    [spell] = analyze(catalog, peer, (tome,)).efficiency_findings
    assert (activation.model_version, activation.assessment) == (MODEL, 'unknown')
    assert (spell.model_version, spell.assessment) == ('cpa-efficiency-v1', 'unknown')


def test_pinned_unsupported_draws_abstain_with_an_owned_peer(catalog):
    row = json.loads((FIXTURES / 'cpa_league_guildmage.json').read_text(encoding='utf-8'))['card']
    for key in ('color_identity', 'colors', 'keywords', 'produced_mana'):
        row[key] = tuple(row[key])
    for subject in (CardSnapshot(**row), pinned('Staff of Domination'), pinned('Endbringer')):
        profile = analyze(catalog, subject, (pinned('Arcane Encyclopedia'),))
        [draw] = [use for use in profile.mechanical_uses if use.role_id == 'draw']
        assert draw.safety == 'unsupported' and 'card_dependency_unresolved' in draw.requirements
        assert not profile.efficiency_findings


def test_activation_prefilter_bounds_peer_search_and_breaks_ties_deterministically(catalog):
    tome, encyclopedia = real('Jayemdae Tome'), pinned('Arcane Encyclopedia')
    incompatible = tuple(replace(encyclopedia, card_id=f'aaa-{i:02}', oracle_text='{2}, {T}: Draw two cards.')
                         for i in range(64))
    at_limit = analyze(catalog, tome, (*incompatible[:-1], encyclopedia))
    assert at_limit.efficiency_findings[0].peer_card_id == encyclopedia.card_id
    assert 'peer_search_truncated' not in coverage(at_limit).limitations
    omitted = analyze(catalog, tome, (*incompatible, encyclopedia))
    assert omitted.efficiency_findings[0].assessment == 'unknown'
    assert 'peer_search_truncated' in coverage(omitted).limitations

    earlier = replace(encyclopedia, card_id='000-earlier')
    tied = replace(encyclopedia, card_id='001-tie')
    peers = (*incompatible, encyclopedia, tied, earlier)
    selected = analyze(catalog, tome, peers)
    assert selected.efficiency_findings[0].peer_card_id == earlier.card_id
    assert 'peer_search_truncated' in coverage(selected).limitations
    replay = analyze(catalog, tome, peers, reverse=True)
    assert (selected.to_json(), selected.profile_id) == (replay.to_json(), replay.profile_id)
