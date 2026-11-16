from dataclasses import replace

import pytest

from mana_architect.collection_playability.contracts import (
    Classification, ComparisonEvidence, ComparisonOutcome, Coverage, EvaluationStatus,
    EvidenceReference, PlayabilityContext, PlayabilityProfile, PlayabilityProvenance,
    UniquenessProfile,
)
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS


def test_context_identity_is_independent_of_strategy_order():
    first = PlayabilityContext("snapshot", "v1", ENGINE_VERSIONS, ("tokens", "aristocrats"))
    second = PlayabilityContext("snapshot", "v1", ENGINE_VERSIONS, ("aristocrats", "tokens"))
    assert first.context_id == second.context_id


@pytest.mark.parametrize("status", [state for state in EvaluationStatus if state != EvaluationStatus.EVALUATED])
def test_incomplete_evaluations_cannot_be_bulk_eligible(status):
    context = PlayabilityContext("snapshot", "v1", ENGINE_VERSIONS)
    with pytest.raises(ValueError):
        PlayabilityProfile("card", context, status=status, bulk_review_eligibility=True)


def test_context_requires_compatible_schema_and_scope_inputs():
    context = PlayabilityContext("snapshot", "v1", ENGINE_VERSIONS)
    with pytest.raises(ValueError, match="schema"):
        replace(context, schema_version="2.0")
    with pytest.raises(ValueError, match="reference universe"):
        replace(context, scope="global")
    with pytest.raises(ValueError, match="commander"):
        replace(context, scope="deck")
    assert replace(context, evaluator_config_version="v2").context_id != context.context_id


def test_comparisons_require_traceable_evidence_and_material_tradeoffs():
    context = PlayabilityContext("snapshot", "v1", ENGINE_VERSIONS)
    evidence = EvidenceReference("card", "mechanical", "v1", "ability", "oracle_text", 0, 4, "Draw")
    alternative = replace(evidence, card_id="peer")
    comparison = ComparisonEvidence(
        "card", "peer", "collection", "draw", ("draw",), ("higher mana cost",), (),
        ComparisonOutcome.UPGRADE, "low", (evidence.evidence_id, alternative.evidence_id),
    )
    coverage = Coverage("mechanical", EvaluationStatus.EVALUATED, (evidence.evidence_id,))
    profile = PlayabilityProfile(
        "card", context, evidence=(evidence, alternative), comparisons=(comparison,), coverage=(coverage,),
    )
    assert profile.profile_id == replace(profile, evidence=(alternative, evidence, evidence)).profile_id
    with pytest.raises(ValueError, match="both endpoints"):
        replace(profile, comparisons=(replace(comparison, evidence_ids=(evidence.evidence_id,)),))
    abstention = replace(comparison, outcome=ComparisonOutcome.NOT_COMPARABLE, evidence_ids=())
    assert replace(profile, comparisons=(abstention,)).comparisons == (abstention,)
    with pytest.raises(ValueError, match="unresolved evidence"):
        replace(profile, evidence=())
    with pytest.raises(ValueError, match="subject and scope"):
        replace(profile, card_id="other")
    with pytest.raises(ValueError, match="material differences"):
        replace(comparison, material_differences=())
    with pytest.raises(ValueError, match="benchmark version"):
        replace(comparison, scope="global")


def test_empty_coverage_cannot_make_a_card_bulk_eligible():
    context = PlayabilityContext("snapshot", "v1", ENGINE_VERSIONS)
    provenance = PlayabilityProvenance("snapshot", None, "oracle", "parser", "capabilities", "catalog")
    with pytest.raises(ValueError, match="require a playability classifier"):
        PlayabilityProfile(
            "card", context, status=EvaluationStatus.EVALUATED,
            classification=Classification.BULK_CANDIDATE, confidence="high",
            coverage=(Coverage("arbitrary", EvaluationStatus.EVALUATED),),
            provenance=provenance, bulk_review_eligibility=True,
        )


@pytest.mark.parametrize(
    ("source", "evidence_card", "error"),
    [("ramp", "card", "recorded source"), ("mechanical", "peer", "profile card")],
)
def test_card_local_coverage_rejects_unrelated_evidence(source, evidence_card, error):
    context = PlayabilityContext("snapshot", "v1", ENGINE_VERSIONS)
    evidence = EvidenceReference(evidence_card, "mechanical", "v1", "ability", "oracle_text", 0, 4, "Draw")
    coverage = Coverage(source, EvaluationStatus.EVALUATED, (evidence.evidence_id,))
    with pytest.raises(ValueError, match=error):
        PlayabilityProfile("card", context, evidence=(evidence,), coverage=(coverage,))


def test_global_uniqueness_requires_matching_reference_provenance():
    context = PlayabilityContext("snapshot", "v1", ENGINE_VERSIONS)
    provenance = PlayabilityProvenance("snapshot", None, "oracle", "parser", "capabilities", "catalog")
    uniqueness = UniquenessProfile(global_uniqueness="unique")
    for invalid_context, invalid_provenance in (
        (context, provenance),
        (replace(context, reference_universe_version="reference"), provenance),
        (replace(context, reference_universe_version="reference"),
         replace(provenance, reference_universe_version="older")),
    ):
        with pytest.raises(ValueError, match="matching reference universe"):
            PlayabilityProfile("card", invalid_context, provenance=invalid_provenance, uniqueness_profile=uniqueness)
    profile = PlayabilityProfile(
        "card", replace(context, reference_universe_version="reference"),
        provenance=replace(provenance, reference_universe_version="reference"), uniqueness_profile=uniqueness,
    )
    assert profile.uniqueness_profile.global_uniqueness == "unique"
