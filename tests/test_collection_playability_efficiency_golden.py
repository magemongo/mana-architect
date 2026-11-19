"""Independent expectations for the pinned real-card draw-efficiency subset."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mana_architect.collection_playability.adapters import CollectionInputs
from mana_architect.collection_playability.contracts import PlayabilityContext
from mana_architect.collection_playability.efficiency import evaluate_efficiency
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.strategy_synergy import load_synergy_context

DATA = json.loads((Path(__file__).parent / 'fixtures/cpa_draw_efficiency.json').read_text(encoding='utf8'))


def real(name):
    c = DATA['cards'][name]
    return CardSnapshot(c['oracle_id'], c['name'], c['type_line'], c['oracle_text'], c['mana_cost'], c['cmc'], tuple(c['color_identity']), (), (), c['commander_legality'])


@pytest.fixture(scope='module')
def catalog():
    return load_synergy_context()


def run(catalog, subject, others=(), reverse=False):
    cards = [subject, *others]
    # Printing identity remains tied to card identity when inputs are reordered.
    ordered = sorted(cards, key=lambda c: c.card_id)
    rows = [OwnedPrinting(i, str(i), c.card_id, c.name, 'test', str(i), 'en', 'nonfoil', 'nm', 1) for i, c in enumerate(ordered)]
    snapshot = CollectionSnapshot(tuple(reversed(cards)) if reverse else tuple(cards), tuple(reversed(rows)) if reverse else tuple(rows), 'pinned-local-snapshot')
    context = PlayabilityContext(snapshot.snapshot_id, 'golden-fixed-draw-v1', ENGINE_VERSIONS)
    return CollectionInputs(context, snapshot, catalog).analyze(subject.card_id).profile


def coverage(profile):
    return next(c for c in profile.coverage if c.source == 'effect_efficiency')


def test_real_bounty_peer_and_evidence(catalog):
    bounty, peer = real("Thassa's Bounty"), real('Brilliant Plan')
    p = run(catalog, bounty, [peer])
    [f] = p.efficiency_findings
    assert (f.assessment, f.peer_card_id) == ('higher_mana_for_same_draw', peer.card_id)
    assert 'legal_target_at_resolution' in f.conditions
    unit = next(u for u in p.execution_units if u.unit_id == f.execution_unit_id)
    assert 'at_least_one_target_legal' in unit.requirements
    assert unit.target_evidence_ids
    assert any('mill' in x and 'conditional' in x for x in f.explanation)
    assert any('not a global benchmark' in x for x in f.conditions)
    evidence = [e for e in p.evidence if e.evidence_id in f.evidence_ids]
    assert {e.card_id for e in evidence} == {bounty.card_id, peer.card_id}
    for e in evidence:
        source = bounty if e.card_id == bounty.card_id else peer
        assert source.oracle_text[e.start:e.end] == e.text
        assert e.source_version and e.record_id
    assert not p.comparisons and not p.bulk_review_eligibility
    assert p.profile_id == run(catalog, bounty, [peer], reverse=True).profile_id
    assert p.profile_id == run(catalog, bounty, [peer]).profile_id


def test_no_peer_and_different_blue_intensity(catalog):
    bounty = real("Thassa's Bounty")
    for peers in ([], [real('Concentrate')], [real('Divination')]):
        p = run(catalog, bounty, peers)
        assert p.efficiency_findings[0].assessment == 'unknown'
        assert 'legal_target_at_resolution' in p.efficiency_findings[0].conditions
        assert 'peer_search_truncated' not in coverage(p).limitations


@pytest.mark.parametrize('name', ['Inspiration', 'Opportunity', 'Painful Truths', 'Time Stretch'])
def test_noncomparable_or_high_impact_abstains(catalog, name):
    p = run(catalog, real(name), [real('Brilliant Plan')])
    assert not any(f.assessment != 'unknown' for f in p.efficiency_findings)


def test_metamorphic_cost_target_and_secondary_effect(catalog):
    bounty, peer = real("Thassa's Bounty"), real('Brilliant Plan')
    equal_cost = replace(bounty, mana_cost=peer.mana_cost)
    no_target = replace(bounty, oracle_text='Draw three cards.')
    unknown_extra = replace(bounty, oracle_text='Draw three cards. You win the game.')
    assert run(catalog, equal_cost, [peer]).efficiency_findings[0].assessment == 'unknown'
    finding = run(catalog, no_target, [peer]).efficiency_findings[0]
    assert finding.assessment == 'higher_mana_for_same_draw'
    assert 'legal_target_at_resolution' not in finding.conditions
    assert not any(f.assessment != 'unknown' for f in run(catalog, unknown_extra, [peer]).efficiency_findings)
    assert run(catalog, bounty, [replace(peer, color_identity=('U', 'R'))]).efficiency_findings[0].assessment == 'unknown'
    assert not any(f.assessment != 'unknown' for f in run(
        catalog, replace(bounty, oracle_text='Draw three cards. Target creature mills three cards.'), [peer]
    ).efficiency_findings)


def test_truncation_is_reported_and_does_not_claim_absence(catalog):
    bounty, peer = real("Thassa's Bounty"), real('Brilliant Plan')
    blockers = [replace(real('Concentrate'), card_id=f'000-{i:03}', name=f'blocker-{i}') for i in range(64)]
    late_peer = replace(peer, card_id='zzz-peer')
    p = run(catalog, bounty, [*blockers, late_peer])
    assert p.efficiency_findings[0].assessment == 'unknown'
    assert 'peer_search_truncated' in coverage(p).limitations
    assert p.profile_id == run(catalog, bounty, [*blockers, late_peer], reverse=True).profile_id


def test_truncation_is_reported_even_when_a_peer_is_found(catalog):
    bounty, peer = real("Thassa's Bounty"), real('Brilliant Plan')
    early_peer = replace(peer, card_id='000-peer')
    blockers = [replace(real('Concentrate'), card_id=f'111-{i:03}', name=f'blocker-{i}') for i in range(64)]
    p = run(catalog, bounty, [early_peer, *blockers])
    assert p.efficiency_findings[0].assessment == 'higher_mana_for_same_draw'
    assert 'peer_search_truncated' in coverage(p).limitations


def test_unsupported_phase_one_draw_never_generates_a_finding(catalog):
    bounty, peer = real("Thassa's Bounty"), real('Brilliant Plan')
    profile = run(catalog, bounty, [peer])
    draw = next(u for u in profile.mechanical_uses if u.role_id == 'draw')
    unsupported = replace(draw, applicability='unknown')
    altered = replace(profile, mechanical_uses=tuple(unsupported if u == draw else u for u in profile.mechanical_uses),
                      efficiency_findings=())
    result = evaluate_efficiency(SimpleNamespace(card=bounty, profile=altered), ())
    assert not result.efficiency_findings
