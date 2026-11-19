"""Pinned real cards and explicitly synthetic controls for repeatable draw."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from mana_architect.collection_playability.adapters import CollectionInputs
from mana_architect.collection_playability.contracts import EvaluationStatus, PlayabilityContext
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.strategy_synergy import load_synergy_context

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/cpa_repeatable_draw.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def catalog():
    return load_synergy_context()


def real(name):
    row = FIXTURE['cards'][name]
    return CardSnapshot(row['oracle_id'], row['name'], row['type_line'], row['oracle_text'], row['mana_cost'],
                        row['cmc'], tuple(row['color_identity']), tuple(row['produced_mana'] or ()),
                        tuple(row['keywords'] or ()), row['commander_legality'],
                        tuple(row['colors']) if row['colors'] is not None else None, row['layout'])


def analyze(catalog, subject, others=(), *, reverse=False):
    cards = (subject, *others)
    sorted_cards = sorted(cards, key=lambda card: card.card_id)
    printings = tuple(OwnedPrinting(i, str(i), card.card_id, card.name, 'test', str(i), 'en', 'nonfoil', 'nm', 1)
                      for i, card in enumerate(sorted_cards))
    snapshot = CollectionSnapshot(tuple(reversed(cards)) if reverse else cards,
                                  tuple(reversed(printings)) if reverse else printings, 'pinned-local-snapshot')
    adapter = CollectionInputs(PlayabilityContext(snapshot.snapshot_id, 'repeatable-draw-review-v1', ENGINE_VERSIONS),
                               snapshot, catalog)
    return adapter.analyze(subject.card_id).profile


@pytest.mark.parametrize('name', sorted(FIXTURE['cards']))
def test_real_draw_cost_and_uncertainty_are_preserved(catalog, name):
    card = real(name)
    expected = FIXTURE['cards'][name]['expected']
    profile = analyze(catalog, card)
    [draw] = [use for use in profile.mechanical_uses if use.role_id == 'draw']
    unit = next(unit for unit in profile.execution_units if unit.unit_id == draw.execution_unit_id)
    assert unit.kind == 'activated'
    assert unit.activation_cost == expected['activation_cost']
    assert draw.safety == 'unsupported'
    assert 'pay_ability_cost' in draw.requirements
    assert not profile.efficiency_findings
    [coverage] = [item for item in profile.coverage if item.source == 'effect_efficiency']
    assert coverage.status == EvaluationStatus.UNSUPPORTED
    assert not profile.bulk_review_eligibility
    evidence = {ref.evidence_id: ref for ref in profile.evidence}
    assert draw.evidence_ids and set(draw.evidence_ids) <= evidence.keys()
    for key in draw.evidence_ids:
        ref = evidence[key]
        assert ref.card_id == card.card_id and ref.source_version and ref.record_id
        assert card.oracle_text[ref.start:ref.end] == ref.text
    matching = [boundary for boundary in profile.boundary_coverage
                if boundary.activation_cost == expected['activation_cost']]
    assert len(matching) == 1
    assert card.oracle_text[matching[0].source_start:matching[0].source_end] == matching[0].source_text
    assert expected['effect'] in matching[0].source_text
    assert matching[0].state != 'verified'


def test_real_distinctions_are_not_erased(catalog):
    greed, tome, stone = (real(name) for name in ('Greed', 'Jalum Tome', 'Mind Stone'))
    p_greed, p_tome, p_stone = (analyze(catalog, card) for card in (greed, tome, stone))
    costs = [next(unit.activation_cost for unit in p.execution_units if unit.activation_cost)
             for p in (p_greed, p_tome)]
    assert 'Pay 2 life' in costs[0]
    assert 'discard' not in costs[1].lower()
    assert 'then discard a card' in tome.oracle_text
    assert any('Sacrifice this artifact' in (unit.activation_cost or '') for unit in p_stone.execution_units)
    assert not p_greed.comparisons and not p_tome.comparisons and not p_stone.comparisons


def test_real_profiles_are_repeatable_and_order_independent(catalog):
    cards = [real(name) for name in sorted(FIXTURE['cards'])]
    for card in cards:
        peers = [other for other in cards if other != card]
        assert analyze(catalog, card, peers).profile_id == analyze(catalog, card, peers, reverse=True).profile_id
        assert analyze(catalog, card, peers).profile_id == analyze(catalog, card, peers).profile_id


def test_synthetic_cost_condition_and_peer_mutations_remain_conservative(catalog):
    # These variants are synthetic and are never presented as Oracle records.
    base = real('Greed')
    variants = [
        replace(base, card_id='synthetic-life-4', oracle_text='{B}, Pay 4 life: Draw a card.'),
        replace(base, card_id='synthetic-discard', oracle_text='{B}, Discard a card: Draw a card.'),
        replace(base, card_id='synthetic-condition', oracle_text='{B}, Pay 2 life: Draw a card. Activate only as a sorcery.'),
    ]
    for card in variants:
        for peers in ((), (real('Jayemdae Tome'), real('Mind Stone'))):
            profile = analyze(catalog, card, peers)
            assert not profile.efficiency_findings
            assert next(item for item in profile.coverage if item.source == 'effect_efficiency').status == EvaluationStatus.UNSUPPORTED
            assert not profile.bulk_review_eligibility
