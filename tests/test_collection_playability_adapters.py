from dataclasses import asdict, replace
import json

import pytest

from mana_architect.collection_playability.adapters import CollectionInputs, IncompatibleInputError
from mana_architect.collection_playability.contracts import EvaluationStatus, PlayabilityContext
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS
from mana_architect.deck_pipeline.contracts import artifact_digest
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.deck_pipeline.pairwise_synergy import PairwiseSynergyAnalyzer
from mana_architect.strategy_synergy import load_synergy_context
from test_pairwise_synergy import _build, _DOUBLER, _TREASURES


@pytest.fixture
def collection_inputs():
    card = CardSnapshot("oracle", "Sol Ring", "Artifact", "{T}: Add {C}{C}.", "{1}", 1,
                        (), ("C",), (), "legal")
    rows = (OwnedPrinting(1, "p1", "oracle", "Sol Ring", "a", "1", "en", "foil", "nm", 2),
            OwnedPrinting(2, "p1", "oracle", "Sol Ring", "a", "1", "en", "nonfoil", "nm", 3),
            OwnedPrinting(3, "p2", None, "Unknown", "a", "2", "en", "nonfoil", "nm", 1))
    snapshot = CollectionSnapshot((card,), rows, "2026-11-11")
    context = PlayabilityContext(snapshot.snapshot_id, "v1", ENGINE_VERSIONS)
    return context, snapshot, load_synergy_context()


def test_owned_printings_and_engine_evidence_are_preserved(collection_inputs):
    context, snapshot, catalog = collection_inputs
    adapter = CollectionInputs(context, snapshot, catalog)
    result = adapter.card("oracle")
    assert result.printings == snapshot.printings[:2]
    assert sum(row.quantity for row in result.printings) == 5
    assert result.ramp.lines
    assert result.mechanics.capabilities
    assert adapter.unlinked_printings == snapshot.printings[2:]
    profile = result.profile
    references = {item.evidence_id: item for item in profile.evidence}
    coverage = {item.source: item for item in profile.coverage}
    for key in coverage["ramp"].evidence_ids:
        span = references[key]
        assert result.card.oracle_text[span.start:span.end] == span.text
    assert coverage["ramp"].evidence_ids
    assert coverage["card_advantage"].status == EvaluationStatus.UNSUPPORTED
    assert coverage["strategy_synergy"].status == EvaluationStatus.NOT_EVALUATED
    assert profile.provenance.legality_data_as_of == snapshot.legality_data_as_of
    replay = CollectionInputs(context, replace(snapshot, printings=snapshot.printings[::-1]), catalog)
    assert profile.profile_id == replay.card("oracle").profile.profile_id
    assert json.dumps(profile.to_json(), allow_nan=False) == json.dumps(asdict(profile), allow_nan=False)
    assert adapter.card("missing").profile.status == EvaluationStatus.INSUFFICIENT_EVIDENCE


def test_missing_oracle_and_engine_failure_are_distinct(collection_inputs, monkeypatch):
    context, snapshot, catalog = collection_inputs
    stale = replace(snapshot, cards=(replace(snapshot.cards[0], oracle_text=None),))
    missing = CollectionInputs(replace(context, collection_snapshot_id=stale.snapshot_id), stale, catalog)
    assert missing.card("oracle").profile.status == EvaluationStatus.INSUFFICIENT_EVIDENCE

    def fail(*args, **kwargs):
        raise RuntimeError("fixture exception")

    monkeypatch.setattr("mana_architect.collection_playability.adapters.evaluate_ramp", fail)
    profile = CollectionInputs(context, snapshot, catalog).card("oracle").profile
    assert profile.status == EvaluationStatus.EVALUATION_FAILED
    assert not profile.bulk_review_eligibility


def test_reject_incompatible_collection_inputs(collection_inputs):
    context, snapshot, catalog = collection_inputs
    for incompatible in (
        replace(context, strategy_ids=("missing",)),
        replace(context, collection_snapshot_id="older"),
        replace(context, engine_versions=replace(ENGINE_VERSIONS, ramp="older")),
    ):
        with pytest.raises(IncompatibleInputError) as error:
            CollectionInputs(incompatible, snapshot, catalog)
        assert error.value.status == EvaluationStatus.INCOMPATIBLE_CONTRACT


def test_published_edges_resolve_and_incompatible_artifacts_are_rejected():
    catalog = load_synergy_context()
    gate, pool = _build(catalog, _TREASURES, _DOUBLER)
    analysis = PairwiseSynergyAnalyzer(synergy_context=catalog).analyze(gate, pool)
    context = PlayabilityContext(
        gate.snapshot.snapshot_id, "v1", ENGINE_VERSIONS, commander_id=gate.commander.card_id,
        synergy_artifact_version=analysis.analysis_id,
        synergy_artifact_fingerprint=artifact_digest(analysis.to_json()),
    )
    adapter = CollectionInputs(context, gate.snapshot, catalog, pairwise=analysis)
    profile = adapter.card(_TREASURES.card_id).profile
    references = {item.evidence_id: item for item in profile.evidence}
    coverage = next(item for item in profile.coverage if item.source == "pairwise")
    assert coverage.evidence_ids
    for key in coverage.evidence_ids:
        assert references[key].record_id in {edge.edge_id for edge in analysis.edges}
        assert references[key].source_version == profile.provenance.synergy_artifact_fingerprint
    invalid_catalog = replace(analysis.provenance, rule_catalog_fingerprint="different")
    with pytest.raises(IncompatibleInputError):
        CollectionInputs(context, gate.snapshot, catalog, pairwise=replace(analysis, provenance=invalid_catalog))
    with pytest.raises(IncompatibleInputError, match="expected fingerprint"):
        CollectionInputs(replace(context, synergy_artifact_fingerprint=None), gate.snapshot, catalog, pairwise=analysis)

    edge, *other_edges = analysis.edges
    altered_edges = (
        replace(edge, relation="converts" if edge.relation != "converts" else "enables"),
        replace(edge, prerequisites=(*edge.prerequisites, "changed_condition")),
    )
    for altered in altered_edges:
        modified = replace(analysis, edges=(altered, *other_edges))
        assert modified.analysis_id == context.synergy_artifact_version
        with pytest.raises(IncompatibleInputError, match="expected fingerprint"):
            CollectionInputs(context, gate.snapshot, catalog, pairwise=modified)
