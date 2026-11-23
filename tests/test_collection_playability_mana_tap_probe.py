from dataclasses import replace
from unittest.mock import patch

import pytest

from cpa_mana_tap_snapshot_probe import (
    activation_decision, activation_efficiency_reference, assert_annotation_isolation, check_activation_finding,
    cpa_mechanics, evaluate, reference_peers, scan, validate_shared_tap,
)
from test_collection_playability_mechanics import _collection, _plummet, catalog


@pytest.mark.parametrize('text,quantity', [
    ('{4}, {T}: Draw a card.', 1),
    ('{2}{U},\t{T}:Draw two cards.', 2),
    ('{4}, {T}: Draw 4 cards.', 4),
    ('{4}, {T}: Draw two card.', None),
    ('{X}, {T}: Draw a card.', None),
    ('{4}, {T}, Discard a card: Draw a card.', None),
    ('{4}, {T}: Draw a card, then discard a card.', None),
    ('{4}, {T}: Draw a card. Activate only as a sorcery.', None),
    ('{4}, {T}: Draw a card.\nActivate only as a sorcery.', None),
])
def test_scan_selects_source_grammar_without_cpa(text, quantity):
    [activation] = scan(text)
    assert activation.quantity == quantity
    assert text[activation.start:activation.end].partition(':')[0] == activation.cost


def test_scan_preserves_distinct_ability_offsets_and_keeps_unknown_ability_labels():
    first = '{4}, {T}: Draw a card.'
    second = '{C}, {T}: Draw two cards.'
    text = f'  {first} {second}\nMax speed — Whenever you draw a card, each opponent mills two cards.'
    rows = list(scan(text))
    assert [row.quantity for row in rows] == [1, 2]
    assert [text[row.start:row.end] for row in rows] == [first, second]


def test_probe_checks_original_cost_and_isolation_with_emission_disabled(catalog):
    card = replace(_plummet(), card_id='synthetic-probe-shared-tap', type_line='Artifact',
                   oracle_text='{4}, {T}: Draw a card.\n{2}, {T}: Draw two cards.')
    adapter = _collection(catalog, card)
    result = adapter.analyze(card.card_id)
    [group] = validate_shared_tap(result)
    assert [cost['text'] for cost in group['costs']] == ['{4}, {T}', '{2}, {T}']
    corrupted = replace(result, card=replace(card, oracle_text=card.oracle_text.replace('{4}', '{5}')))
    with pytest.raises(AssertionError):
        validate_shared_tap(corrupted)

    with patch.object(cpa_mechanics, 'find_shared_tap_prerequisites', return_value=()) as bypass:
        disabled = adapter.analyze(card.card_id)
    bypass.assert_called_once()
    assert_annotation_isolation({card.card_id: result}, {card.card_id: disabled})

    altered = replace(disabled, profile=replace(disabled.profile, confidence='low'))
    with pytest.raises(AssertionError, match=card.card_id):
        assert_annotation_isolation({card.card_id: result}, {card.card_id: altered})


def test_probe_reference_checks_selection_with_independent_cost_and_mechanical_facts(catalog):
    template = replace(_plummet(), type_line='Artifact', color_identity=(), commander_legality='legal')
    subject = replace(template, card_id='synthetic-subject', oracle_text='{4}, {T}: Draw a card.', mana_cost='{1}')
    cheaper = replace(template, card_id='synthetic-cheaper', oracle_text='{3}, {T}: Draw a card.', mana_cost='{9}')
    other_quantity = replace(template, card_id='synthetic-two', oracle_text='{1}, {T}: Draw two cards.')
    outside = replace(template, card_id='synthetic-creature', oracle_text='{1}, {T}: Draw a card.', type_line='Creature')
    cards = (subject, cheaper, other_quantity, outside)
    adapter = _collection(catalog, *cards)
    results = {card.card_id: adapter.analyze(card.card_id) for card in cards}
    references = {}
    for card in cards:
        result = results[card.card_id]
        [activation] = scan(card.oracle_text)
        stage, _ = evaluate(result, activation)
        reference, reason = activation_efficiency_reference(
            result, activation, {'reached': stage, 'decision': activation_decision(result.profile, activation)},
        )
        if card == outside:
            assert stage == 'conditional' and reference is None and reason == 'printed_type_outside_artifact_scope'
        else:
            assert reference and reason is None
            references[card.card_id] = reference

    original = references[subject.card_id]
    compatible, cheaper_peers = reference_peers(original, reversed(tuple(references.values())))
    assert compatible == [cheaper.card_id] and cheaper_peers == [references[cheaper.card_id]]
    assert check_activation_finding(results, original, cheaper_peers[0])['mana_delta'] == 1
    with pytest.raises(AssertionError, match=subject.card_id):
        check_activation_finding(results, original, None)

    # A candidate that differs only in a modeled prerequisite is also incompatible.
    different_timing = dict(cheaper_peers[0], timing=('sorcery_speed', 'sorcery_speed'))
    assert reference_peers(original, (different_timing,)) == ([], [])
