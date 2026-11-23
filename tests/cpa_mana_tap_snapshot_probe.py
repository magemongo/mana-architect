"""Read-only coverage probe. Run directly with the pinned Scryfall cache path."""

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
import gzip
import hashlib
import json
from pathlib import Path
import re
from unittest.mock import patch

from mana_architect.ability_boundaries import resolve_ability_boundaries
from mana_architect.collection_playability import mechanics as cpa_mechanics
from mana_architect.collection_playability.adapters import CollectionInputs
from mana_architect.collection_playability.contracts import PlayabilityContext
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.strategy_synergy import load_synergy_context


FIXTURE = Path(__file__).parent / 'fixtures' / 'cpa_repeatable_draw.json'
STAGES = ('source', 'boundary', 'native', 'verified', 'conditional')
WORDS = dict(zip(('a', 'one', 'two', 'three', 'four', 'five'), (1, 1, 2, 3, 4, 5)))
DRAW = re.compile(r'Draw (\w+) (card|cards)\.\Z')
NEXT_ABILITY = re.compile(r'(?<=\.)[ \t]+(?=\{|When\b|Whenever\b|At\b)')
CONTINUATION = re.compile(r'(?:Activate|Then|If|Otherwise)\b', re.I)

# Transcribed from docs/collection-playability/phase-2-mana-tap-snapshot-probe.md.
# Each row fixes the Oracle ID, cost, quantity, stage and any reported dependency category.
HISTORICAL_CANDIDATES = (
    ('08bb1f33-b232-4382-a268-4804d4e72d29', '{3}{U}, {T}', 1, 'verified', 'unmodeled_static_or_keyword', 'Copy target instant'),
    ('09ee1332-741f-4c55-abc3-8bcff9031cd9', '{3}{U}, {T}', 1, 'conditional', None, None),
    ('39ee576a-0803-4063-9c84-f2b537e4d44c', '{4}, {T}', 1, 'conditional', None, None),
    ('4243baf6-9ac2-4f87-988d-3ebf5e177f3b', '{U}, {T}', 2, 'verified', 'unmodeled_static_or_keyword', 'Discard a card'),
    ('6b78e417-44f8-4ab6-9d3c-e71704fc648e', '{6}, {T}', 1, 'verified', 'casting_restriction', 'Create a tapped Vibranium token'),
    ('d7888719-647d-4022-a211-822fa09f0791', '{5}, {T}', 1, 'verified', 'unmodeled_static_or_keyword', 'Untap this artifact.'),
    ('d8956775-fda8-475d-9e8a-b61196a958ea', '{2}, {T}', 1, 'source', None, None),
    ('d91ea728-2d69-42cb-bb5d-e2b058f0d7b1', '{3}, {T}', 1, 'conditional', None, None),
    ('db1ea6e0-4076-49dd-92f7-7e6d7eb65a27', '{C}{C}, {T}', 1, 'verified', 'timing_or_frequency', 'during each other player'),
    ('dfb23df9-f912-4daa-9842-01bd12b4a72a', '{8}, {T}', 4, 'conditional', None, None),
    ('e2725411-a600-4ff3-94fb-5d52f5eda414', '{3}{U}, {T}', 1, 'conditional', None, None),
    ('e495033b-f727-41e3-b7fc-7f5fa8abdd87', '{3}{C}, {T}', 1, 'verified', 'unmodeled_static_or_keyword', 'Devoid'),
)
GUILDMAGE_ID = HISTORICAL_CANDIDATES[0][0]
ACTIVATION_EFFICIENCY_MODEL = 'cpa-activation-draw-efficiency-v1'


@dataclass(frozen=True)
class Activation:
    start: int
    end: int
    cost: str
    effect: str
    quantity: int | None


def scan(text):
    """Enumerate symbol-led draw clauses independently of CPA source parsing."""
    lines = text.splitlines(keepends=True)
    offset = 0
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip('\r\n')
        cuts = [(0, 0), *(m.span() for m in NEXT_ABILITY.finditer(line)), (len(line), len(line))]
        for (_, left), (right, _) in zip(cuts, cuts[1:]):
            clause = line[left:right].strip()
            if not clause:
                continue
            start = offset + left + len(line[left:right]) - len(line[left:right].lstrip())
            cost, colon, body = clause.partition(':')
            effect = body.lstrip(' \t')
            if not (cost.startswith('{') and colon and re.match(r'Draw\b', effect, re.I)):
                continue
            mana, comma, tap = cost.partition(',')
            tokens = re.findall(r'\{([^{}]+)\}', mana)
            fixed = (bool(tokens) and ''.join('{' + t + '}' for t in tokens) == mana
                     and all(t in {'W', 'U', 'B', 'R', 'G', 'C'} or re.fullmatch('[0-9]+', t) for t in tokens))
            match = DRAW.fullmatch(effect)
            next_line = lines[index + 1].lstrip() if index + 1 < len(lines) and right == len(line) else ''
            quantity = None
            if fixed and comma and tap.lstrip(' \t') == '{T}' and match and not CONTINUATION.match(next_line):
                count, noun = match.groups()
                value = int(count) if re.fullmatch('[0-9]+', count) else WORDS.get(count)
                if value and (noun == 'card') == (value == 1):
                    quantity = value
            # Nearby controls include tap, extra costs, variable symbols and plain mana-only draw.
            yield Activation(start, start + len(clause), cost, effect, quantity)
        offset += len(raw_line)


def card_snapshot(row):
    return CardSnapshot(
        row['oracle_id'], row['name'], row.get('type_line'), row.get('oracle_text'), row.get('mana_cost'),
        row.get('cmc'), tuple(sorted(row['color_identity'])) if 'color_identity' in row else None,
        tuple(sorted(row.get('produced_mana') or ())), tuple(sorted(row.get('keywords') or ())),
        row.get('legalities', {}).get('commander'),
        tuple(sorted(row['colors'])) if 'colors' in row else None, row.get('layout'),
    )


def single_face(row, card):
    return (len(row.get('card_faces') or ()) <= 1 and '//' not in (card.type_line or '')
            and ' // ' not in (card.oracle_text or ''))


def evaluate(result, activation):
    profile, card = result.profile, result.card
    if result.parsed is None or result.mechanics is None:
        return 'source', [profile.status, *profile.reasons]
    boundaries = resolve_ability_boundaries(result.parsed, card_id=card.card_id, type_line=card.type_line)
    overlapping = [b for b in boundaries if b.start < activation.end and b.end > activation.start]
    exact = [b for b in overlapping if (b.start, b.end, b.kind, b.status, b.activation_cost) ==
             (activation.start, activation.end, 'activated', 'resolved', activation.cost)]
    if len(exact) != 1:
        return 'source', [f'{b.kind}/{b.status} [{b.start}:{b.end}] {b.source_text!r}' for b in overlapping]
    [boundary] = exact
    effect_start = activation.end - len(activation.effect)
    fact_ids = set()
    available = []
    for cap in result.mechanics.capabilities:
        span = cap.evidence
        if cap.id != 'draw' or span.source_field != 'oracle_text' or span.end <= activation.start or span.start >= activation.end:
            continue
        available.append(f'{cap.costs!r} {cap.quantity.kind}:{cap.quantity.value} {cap.timing} [{span.start}:{span.end}] {span.oracle_span!r}')
        if (cap.costs == (activation.cost,) and cap.quantity.kind == 'fixed'
                and cap.quantity.value == activation.quantity and cap.timing == 'activated'
                and cap.targeting == 'none' and not cap.resolution_targets
                and span.start == effect_start and span.end in {activation.end - 1, activation.end}
                and card.oracle_text[span.start:span.end] == span.oracle_span):
            fact_ids.update(ref.evidence_id for ref in profile.evidence if
                            (ref.card_id, ref.source, ref.record_id, ref.source_field, ref.start, ref.end, ref.text) ==
                            (card.card_id, 'mechanical', 'draw', 'oracle_text', span.start, span.end, span.oracle_span))
    if not fact_ids:
        return 'boundary', available or ['no overlapping native draw capability']
    coverage = next((b for b in profile.boundary_coverage if b.boundary_id == boundary.ability_id), None)
    if coverage is None or coverage.state != 'verified' or not fact_ids.intersection(coverage.recognized_evidence_ids):
        return 'native', ([*coverage.reasons, f'unknown spans {coverage.unresolved_spans}'] if coverage
                          else ['no boundary coverage record'])
    uses = [use for use in profile.mechanical_uses if use.role_id == 'draw' and fact_ids.intersection(use.evidence_ids)]
    units = {unit.unit_id: unit for unit in profile.execution_units}
    for use in uses:
        unit = units.get(use.execution_unit_id)
        if (use.safety == use.applicability == 'conditional' and {'pay_ability_cost', 'tap_source'} <= set(use.requirements)
                and unit and (unit.card_id, unit.face_index, unit.ability_id, unit.activation_cost) ==
                (card.card_id, boundary.face_index, boundary.ability_id, activation.cost)
                and fact_ids.intersection(use.evidence_ids, unit.evidence_ids)):
            return 'conditional', list(use.requirements)
    return 'verified', [*(f'{u.safety}: {", ".join(u.requirements)}' for u in uses),
                        *(f'{d.category}: {d.source_text}' for d in profile.card_dependencies
                          if boundary.ability_id in d.affected_boundary_ids and d.status != 'independent')] or ['no associated draw use']


def inventory(path):
    counts = Counter()
    variants = defaultdict(set)
    with gzip.open(path, 'rt', encoding='utf-8') as source:
        for line in source:
            row = json.loads(line)
            counts['printing_rows'] += 1
            if not row.get('oracle_id'):
                counts['rows_without_oracle_id'] += 1
                continue
            card = card_snapshot(row)
            key = (card, single_face(row, card))
            # Keep all materially different records per Oracle ID. Printing metadata is ignored.
            variants[card.card_id].add(key)
    return counts, variants


def validate_shared_tap(result):
    profile, card = result.profile, result.card
    groups = profile.shared_tap_prerequisites
    if not groups:
        return []
    assert single_face({}, card) and len(groups) == 1, card.card_id
    boundaries = {b.ability_id: b for b in resolve_ability_boundaries(
        result.parsed, card_id=card.card_id, type_line=card.type_line,
    )}
    coverage = {b.boundary_id: b for b in profile.boundary_coverage}
    refs = {ref.evidence_id: ref for ref in profile.evidence}
    [group] = groups
    assert (group.card_id, group.face_index) == (card.card_id, 0)
    assert len({cost.boundary_id for cost in group.costs}) == len(group.costs) >= 2
    verified_draw = False
    previous_end = 0
    for cost in group.costs:
        boundary = boundaries[cost.boundary_id]
        record = coverage[cost.boundary_id]
        assert (boundary.kind, boundary.status, boundary.card_id, boundary.face_index) == (
            'activated', 'resolved', group.card_id, group.face_index,
        )
        assert previous_end <= cost.start == boundary.start < cost.end < boundary.end
        assert card.oracle_text[cost.start:cost.end + 1] == cost.text + ':'
        assert cost.text == boundary.activation_cost == record.activation_cost
        assert (record.source_start, record.source_end, record.source_text) == (
            boundary.start, boundary.end, card.oracle_text[boundary.start:boundary.end],
        )
        mana, separator, tap = cost.text.partition(',')
        tokens = re.findall(r'\{([0-9]+|[WUBRGCX])\}', mana)
        assert tokens and ''.join('{' + token + '}' for token in tokens) == mana
        assert separator and tap.lstrip(' \t') == '{T}'
        verified_draw |= record.state == 'verified' and any(
            refs[key].source == 'mechanical' and refs[key].record_id == 'draw'
            for key in record.recognized_evidence_ids
        )
        previous_end = cost.end
    assert verified_draw, card.card_id
    return [asdict(group)]


def assert_annotation_isolation(current, disabled):
    """Compare fresh analyses after the harness bypasses emission, including all linked decisions."""
    assert current.keys() == disabled.keys()
    for card_id, result in current.items():
        before = disabled[card_id].profile
        assert not before.shared_tap_prerequisites, card_id
        assert replace(result.profile, shared_tap_prerequisites=()) == before, card_id
        if not result.profile.shared_tap_prerequisites:
            assert result.profile.profile_id == before.profile_id, card_id


def activation_decision(profile, activation):
    boundaries = [b for b in profile.boundary_coverage
                  if b.source_start == activation.start and b.source_end == activation.end]
    boundary_ids = {b.boundary_id for b in boundaries}
    units = {u.unit_id: u for u in profile.execution_units if u.ability_id in boundary_ids}
    return dict(
        coverage=[(b.boundary_id, b.state) for b in boundaries],
        uses=[dict(safety=u.safety, applicability=u.applicability, requirements=u.requirements,
                   unit=asdict(units[u.execution_unit_id]), evidence_ids=u.evidence_ids)
              for u in profile.mechanical_uses if u.role_id == 'draw' and u.execution_unit_id in units],
        blockers=[asdict(d) for d in profile.card_dependencies
                  if d.status != 'independent' and boundary_ids.intersection(d.affected_boundary_ids)],
    )


def compare_historical_candidates(rows, cards):
    """Compare only facts recoverable from the previous report and the same Oracle snapshot."""
    by_span = {(row['oracle_id'], row['start'], row['end']): row for row in rows}
    assert len(by_span) == len(rows) == len(HISTORICAL_CANDIDATES)
    for oracle_id, cost, quantity, stage, category, source in HISTORICAL_CANDIDATES:
        # The report identifies one unique clause per ID by cost and draw quantity.
        [clause] = [a for a in scan(cards[oracle_id].oracle_text) if (a.cost, a.quantity) == (cost, quantity)]
        row = by_span[oracle_id, clause.start, clause.end]
        assert (row['cost'], row['draw'], row['reached']) == (cost, quantity, stage), row
        decision = row['decision']
        if stage == 'source':
            assert (clause.start, clause.end) == (145, 167)
            assert any('[145:314]' in item and 'Max speed' in item for item in row['evidence']), row
            continue
        [(boundary_id, state)] = decision['coverage']
        assert state == 'verified', row
        [use] = decision['uses']
        assert {'pay_ability_cost', 'tap_source'} <= set(use['requirements']), row
        assert use['unit']['ability_id'] == boundary_id and use['unit']['activation_cost'] == cost, row
        if stage == 'conditional':
            assert use['safety'] == use['applicability'] == 'conditional' and not decision['blockers'], row
        else:
            assert (use['safety'], use['applicability']) == ('unsupported', 'unknown'), row
            assert 'card_dependency_unresolved' in use['requirements'], row
            assert any(d['category'] == category and source in d['source_text'] for d in decision['blockers']), row
            if oracle_id == 'd7888719-647d-4022-a211-822fa09f0791':
                assert len(decision['blockers']) == 4, row


def activation_efficiency_reference(result, activation, row):
    """Derive eligibility from pinned text and already established mechanical facts."""
    card, profile = result.card, result.profile
    stage = row['reached']
    if stage != 'conditional':
        if stage == 'verified' and row['decision']['blockers']:
            return None, 'unresolved_card_dependency'
        return None, row['first_block']
    if card.type_line.partition(' — ')[0] != 'Artifact':
        return None, 'printed_type_outside_artifact_scope'
    if (card.commander_legality != 'legal' or not single_face({}, card)
            or card.layout not in {None, 'normal'} or card.color_identity is None
            or not any(p.card_id == card.card_id and p.quantity > 0 for p in result.printings)):
        return None, 'ownership_legality_face_or_identity_not_established'
    if card.oracle_text.strip() != card.oracle_text[activation.start:activation.end]:
        return None, 'oracle_contains_more_than_one_activation'

    decision = row['decision']
    [(boundary_id, state)] = decision['coverage']
    [use] = decision['uses']
    unit = use['unit']
    draw = next(item for item in profile.mechanical_uses if item.execution_unit_id == unit['unit_id'])
    if (state != 'verified' or decision['blockers'] or any(d.status != 'independent' for d in profile.card_dependencies)
            or len(profile.boundary_coverage) != 1 or len(profile.execution_units) != 1 or len(profile.mechanical_uses) != 1
            or (unit['card_id'], unit['face_index'], unit['ability_id'], unit['kind'], unit['status'])
            != (card.card_id, 0, boundary_id, 'activated', 'evaluated')
            or unit['target_evidence_ids'] or unit['mode_ids'] or draw.target_coverage != 'not_targeted'):
        return None, 'execution_or_dependency_gate'

    # scan() has already proved the fixed mana/tap grammar and independently parsed the printed quantity.
    symbols = re.findall(r'\{([^{}]+)\}', activation.cost.partition(',')[0])
    assert card.oracle_text[activation.start:activation.start + len(activation.cost) + 1] == activation.cost + ':'
    assert unit['activation_cost'] == activation.cost and activation.quantity is not None
    assert unit['timing'] == draw.timing == 'activated'
    return dict(oracle_id=card.card_id, printed_type=card.type_line, draw=activation.quantity,
                cost=activation.cost, cost_span=(activation.start, activation.start + len(activation.cost)),
                generic=sum(int(s) for s in symbols if s.isdecimal()),
                fixed_symbols=tuple(sorted(s for s in symbols if not s.isdecimal())),
                color_identity=tuple(sorted(card.color_identity)), timing=(draw.timing, unit['timing']),
                requirements=draw.requirements, unit_requirements=unit['requirements'],
                target_semantics=(draw.target_coverage, draw.restrictions), boundary_id=boundary_id,
                unit_id=unit['unit_id'], draw_evidence_ids=use['evidence_ids']), None


def reference_peers(subject, universe):
    fields = ('draw', 'fixed_symbols', 'color_identity', 'timing', 'requirements', 'unit_requirements', 'target_semantics')
    compatible = [peer for peer in universe if peer['oracle_id'] != subject['oracle_id']
                  and all(peer[key] == subject[key] for key in fields)]
    cheaper = sorted((peer for peer in compatible if peer['generic'] < subject['generic']),
                     key=lambda peer: (peer['generic'], peer['oracle_id']))
    return sorted(peer['oracle_id'] for peer in compatible), cheaper


def check_activation_finding(results, subject, expected_peer):
    subject_id = subject['oracle_id']
    profile = results[subject_id].profile
    findings = profile.efficiency_findings
    peer_id = expected_peer['oracle_id'] if expected_peer else None
    expected = (ACTIVATION_EFFICIENCY_MODEL, 'higher_mana_for_same_draw' if peer_id else 'unknown', peer_id, subject['unit_id'])
    observed = [(f.model_version, f.assessment, f.peer_card_id, f.execution_unit_id) for f in findings]
    assert observed == [expected], dict(subject=subject_id, cost_span=subject['cost_span'], expected=expected, actual=observed)
    [finding] = findings
    delta = subject['generic'] - expected_peer['generic'] if expected_peer else None
    if delta is not None:
        assert re.findall(r'\b([0-9]+) more generic mana\b', ' '.join(finding.explanation)) == [str(delta)], finding
    refs = [ref for ref in profile.evidence if ref.evidence_id in finding.evidence_ids]
    endpoints = (subject, expected_peer) if expected_peer else (subject,)
    assert len(refs) == 2 * len(endpoints), finding
    for endpoint in endpoints:
        card = results[endpoint['oracle_id']].card
        cost_refs = [ref for ref in refs if ref.card_id == card.card_id and ref.source == 'activation_cost']
        native_refs = [ref for ref in refs if ref.card_id == card.card_id and ref.source == 'mechanical']
        [cost] = cost_refs
        [native] = native_refs
        assert (cost.start, cost.end, cost.text, cost.record_id, cost.source_version) == (
            *endpoint['cost_span'], endpoint['cost'], endpoint['boundary_id'], ACTIVATION_EFFICIENCY_MODEL,
        )
        assert cost.extraction_boundary_id is None and native.evidence_id in endpoint['draw_evidence_ids']
        assert all(ref.source_field == 'oracle_text' and card.oracle_text[ref.start:ref.end] == ref.text
                   for ref in (cost, native))
    return dict(assessment=finding.assessment, selected_peer=peer_id, mana_delta=delta,
                evidence=[asdict(ref) for ref in refs])


def audit_activation_efficiency(results, candidate_rows, candidates, peer_visits):
    references, excluded = {}, []
    for row in candidate_rows:
        card_id = row['oracle_id']
        [activation] = [a for a in candidates[card_id] if (a.start, a.end) == (row['start'], row['end'])]
        reference, reason = activation_efficiency_reference(results[card_id], activation, row)
        if reference:
            references[card_id] = reference
        else:
            excluded.append(dict(oracle_id=card_id, name=row['name'], span=(activation.start, activation.end),
                                 first_exclusion=reason, evidence=row['evidence']))
    # The production findings are first read here, after reference eligibility is established.
    observed_ids = {card_id for card_id, result in results.items() if any(
        f.model_version == ACTIVATION_EFFICIENCY_MODEL for f in result.profile.efficiency_findings
    )}
    assert observed_ids == references.keys(), dict(expected=list(references), observed=sorted(observed_ids), excluded=excluded)
    checks = []
    for card_id, reference in references.items():
        compatible, cheaper = reference_peers(reference, references.values())
        visited = peer_visits[card_id]
        assert visited == sorted(set(visited)) and card_id not in visited and len(visited) <= 64, visited
        [coverage] = [c for c in results[card_id].profile.coverage if c.source == 'effect_efficiency']
        truncated = 'peer_search_truncated' in coverage.limitations
        # With no truncation, an omitted compatible ID can only have been lost before native peer evaluation.
        in_budget = [peer for peer in cheaper if peer['oracle_id'] in visited]
        unbounded_peer = cheaper[0] if cheaper else None
        bounded_peer = in_budget[0] if in_budget else None
        observed = check_activation_finding(results, reference, bounded_peer)
        assert coverage.status == ('evaluated' if bounded_peer else 'insufficient_evidence'), coverage
        explanation = None
        if not bounded_peer:
            if cheaper:
                explanation = 'cheaper_compatible_peers_not_visited'
            else:
                explanation = 'compatible_peers_are_equal_or_more_expensive' if compatible else 'no_compatible_peer'
        checks.append(dict(reference=reference, compatible_peers=compatible,
                           cheaper_peers=[p['oracle_id'] for p in cheaper], examined_peers=visited,
                           compatible_not_examined=sorted(set(compatible) - set(visited)), truncated=truncated,
                           selection_changed_by_search=unbounded_peer != bounded_peer, unknown_reason=explanation, **observed))
    return dict(eligible_subjects=len(checks), cheaper_peer_findings=sum(row['selected_peer'] is not None for row in checks),
                excluded=excluded, eligible=checks)


def run(path):
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    with path.open('rb') as source:
        checksum = hashlib.file_digest(source, 'sha256').hexdigest()
    if checksum != fixture['snapshot_sha256']:
        raise SystemExit(f'Snapshot mismatch: got {checksum}, expected {fixture["snapshot_sha256"]}')
    counts, variants = inventory(path)
    counts['oracle_ids'] = len(variants)
    conflicts = []
    cards, candidates, controls = [], {}, {}
    for card_id, versions in sorted(variants.items()):
        relevant = {}
        for card, face_ok in versions:
            if card.commander_legality == 'legal' and face_ok and card.type_line and card.oracle_text:
                clauses = tuple(scan(card.oracle_text))
                if clauses:
                    relevant[card] = clauses
        if len(versions) > 1:
            conflicts.append({'oracle_id': card_id, 'names': sorted({card.name for card, _ in versions}),
                              'variants': len(versions), 'probe_relevant': bool(relevant)})
            if relevant:
                raise SystemExit(f'Conflicting probe-relevant records: {list(versions)!r}')
        if len(versions) == 1:
            [(card, face_ok)] = versions
            if card.commander_legality == 'legal':
                counts['legal_oracle_ids_without_conflicts'] += 1
                if face_ok and card.type_line and card.oracle_text:
                    counts['eligible_single_face_oracle_ids'] += 1
        for card, clauses in relevant.items():
            cards.append(card)
            candidates[card_id] = tuple(a for a in clauses if a.quantity is not None)
            controls[card_id] = tuple(a for a in clauses if a.quantity is None)
    pinned = fixture['cards']
    for name, row in pinned.items():
        card_id = row['oracle_id']
        [card] = [card for card in cards if card.card_id == card_id]
        assert card.oracle_text == row['oracle_text'], name
        assert bool(candidates[card_id]) == (name == 'Jayemdae Tome'), name

    printings = tuple(OwnedPrinting(i, card.card_id, card.card_id, card.name, 'probe', str(i), 'en', 'nonfoil', 'nm', 1)
                      for i, card in enumerate(cards))
    catalog = load_synergy_context()

    peer_visits = {}

    def analyze(order, rows, *, trace=False):
        order = tuple(order)
        snapshot = CollectionSnapshot(order, tuple(rows), fixture['file_last_modified_utc'])
        context = PlayabilityContext(snapshot.snapshot_id, 'snapshot-mana-tap-probe-v1', ENGINE_VERSIONS)
        adapter = CollectionInputs(context, snapshot, catalog)
        results = {}
        for card in order:
            if trace and candidates[card.card_id]:
                with patch.object(adapter, 'card', wraps=adapter.card) as calls:
                    results[card.card_id] = adapter.analyze(card.card_id)
                subject, *visits = [call.args[0] for call in calls.call_args_list]
                assert subject == card.card_id
                peer_visits[card.card_id] = visits
            else:
                results[card.card_id] = adapter.analyze(card.card_id)
        return results

    results = analyze(cards, printings, trace=True)
    for other in (analyze(cards, printings), analyze(reversed(cards), reversed(printings))):
        assert {k: (v.profile.profile_id, v.profile.shared_tap_prerequisites) for k, v in results.items()} == {
            k: (v.profile.profile_id, v.profile.shared_tap_prerequisites) for k, v in other.items()
        }
    with patch.object(cpa_mechanics, 'find_shared_tap_prerequisites', return_value=()):
        disabled = analyze(cards, printings)
    assert_annotation_isolation(results, disabled)

    rows, control_rows, annotations = [], [], []
    for card in cards:
        result = results[card.card_id]
        groups = validate_shared_tap(result)
        if groups:
            annotations.append(dict(name=card.name, oracle_id=card.card_id, groups=groups,
                                    in_candidates=bool(candidates[card.card_id]), in_controls=bool(controls[card.card_id])))
        for a in candidates[card.card_id]:
            reached, reasons = evaluate(result, a)
            index = STAGES.index(reached)
            rows.append(dict(name=card.name, oracle_id=card.card_id, cost=a.cost, draw=a.quantity,
                             start=a.start, end=a.end, reached=reached,
                             first_block=STAGES[index + 1] if index + 1 < len(STAGES) else None, evidence=reasons,
                             decision=activation_decision(result.profile, a)))
        for a in controls[card.card_id]:
            boundaries = [b for b in result.profile.boundary_coverage
                          if b.source_start < a.end and b.source_end > a.start]
            units = {u.unit_id for u in result.profile.execution_units if u.ability_id in {b.boundary_id for b in boundaries}}
            control_rows.append(dict(name=card.name, oracle_id=card.card_id, cost=a.cost, effect=a.effect,
                                     start=a.start, end=a.end,
                                     coverage=[(b.state, b.verifier_version) for b in boundaries],
                                     uses=[u.safety for u in result.profile.mechanical_uses
                                           if u.role_id == 'draw' and u.execution_unit_id in units]))
    compare_historical_candidates(rows, {card.card_id: card for card in cards})
    jayemdae_id = pinned['Jayemdae Tome']['oracle_id']
    assert not results[jayemdae_id].profile.shared_tap_prerequisites
    assert any(row['oracle_id'] == jayemdae_id and row['reached'] == 'conditional' for row in rows)
    guildmage = json.loads(FIXTURE.with_name('cpa_league_guildmage.json').read_text(encoding='utf-8'))
    assert guildmage['snapshot_sha256'] == checksum
    assert guildmage['card']['card_id'] == GUILDMAGE_ID
    assert results[GUILDMAGE_ID].card.oracle_text == guildmage['card']['oracle_text']
    [guildmage_group] = results[GUILDMAGE_ID].profile.shared_tap_prerequisites
    assert [(cost.start, cost.end, cost.text) for cost in guildmage_group.costs] == [
        (0, 11, '{3}{U}, {T}'), (26, 37, '{X}{R}, {T}'),
    ]
    for name, record in pinned.items():
        if name == 'Jayemdae Tome':
            continue
        matched = [row for row in control_rows if row['oracle_id'] == record['oracle_id']
                   and row['cost'] == record['expected']['activation_cost']]
        assert matched and all('unsupported' in row['uses'] and set(row['uses']) == {'unsupported'} for row in matched), name
    for row in control_rows:
        assert not any(state == 'verified' and version == 'cpa-boundary-coverage-mana-tap-draw-v1'
                       for state, version in row['coverage']), row
    assert (len(control_rows), len({row['oracle_id'] for row in control_rows})) == (421, 415)
    assert sum('conditional' in row['uses'] for row in control_rows) == 6
    efficiency_audit = audit_activation_efficiency(results, rows, candidates, peer_visits)
    encyclopedia_id = 'd91ea728-2d69-42cb-bb5d-e2b058f0d7b1'
    [tome] = [row for row in efficiency_audit['eligible'] if row['reference']['oracle_id'] == jayemdae_id]
    assert (tome['selected_peer'], tome['mana_delta']) == (encyclopedia_id, 1), tome
    without_peer = analyze((card for card in cards if card.card_id != encyclopedia_id),
                           (row for row in printings if row.card_id != encyclopedia_id))
    without_tome_peer = check_activation_finding(without_peer, tome['reference'], None)
    assert without_peer[jayemdae_id].profile.mechanical_uses == results[jayemdae_id].profile.mechanical_uses
    efficiency_audit['tome_without_encyclopedia'] = without_tome_peer
    annotation_counts = {}
    for label in ('all', 'candidates', 'controls'):
        selected = [row for row in annotations if label == 'all' or row[f'in_{label}']]
        groups = [group for row in selected for group in row['groups']]
        annotation_counts[label] = dict(oracle_ids=len(selected), groups=len(groups),
                                        citations=sum(len(group['costs']) for group in groups))
    return dict(sha256=checksum, inventory=counts, conflicts=conflicts,
                stage_counts={stage: sum(STAGES.index(row['reached']) >= index for row in rows)
                              for index, stage in enumerate(STAGES)},
                candidate_oracle_ids=len({row['oracle_id'] for row in rows}), stable_profile_ids=len(results),
                historical_candidate_matches=len(rows), annotation_isolation_profile_matches=len(results),
                annotation_counts=annotation_counts, annotations=annotations,
                activation_efficiency=efficiency_audit,
                candidates=rows, controls=control_rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot), indent=2, ensure_ascii=True))
