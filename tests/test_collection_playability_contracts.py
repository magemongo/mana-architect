from dataclasses import replace

import pytest

from mana_architect.collection_playability.contracts import (
    BoundaryCoverage, Classification, ComparisonEvidence, ComparisonOutcome, Coverage, EvaluationStatus,
    EvidenceReference, PlayabilityContext, PlayabilityProfile, PlayabilityProvenance,
    SemanticCoverageState, SharedTapPrerequisite, TapCostCitation, UniquenessProfile,
)
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS


def test_context_identity_is_independent_of_strategy_order():
    first = PlayabilityContext("snapshot", "v1", ENGINE_VERSIONS, ("tokens", "aristocrats"))
    second = PlayabilityContext("snapshot", "v1", ENGINE_VERSIONS, ("aristocrats", "tokens"))
    assert first.context_id == second.context_id


def test_targeted_mill_assessment_rejects_unresolved_or_unselected_basis():
    from test_collection_playability_efficiency_golden import real, run
    from mana_architect.strategy_synergy import load_synergy_context
    profile = run(load_synergy_context(), real("Thassa's Bounty"), strategy_ids=('mill',))
    assessment = profile.targeted_mill_assessment
    with pytest.raises(ValueError, match='shared execution'):
        replace(profile, targeted_mill_assessment=replace(assessment, execution_unit_id='missing'))
    with pytest.raises(ValueError, match='shared execution'):
        replace(profile, targeted_mill_assessment=replace(assessment, mill_evidence_ids=('missing',)))
    with pytest.raises(ValueError, match='shared execution'):
        replace(profile, execution_units=tuple(replace(unit, target_evidence_ids=()) for unit in profile.execution_units))
    with pytest.raises(ValueError, match='explicit context'):
        replace(profile, context=replace(profile.context, strategy_ids=()))
    with pytest.raises(ValueError, match='provenance'):
        replace(profile, targeted_mill_assessment=replace(assessment, catalog_fingerprint='wrong'))
    with pytest.raises(ValueError, match='Invalid targeted'):
        replace(assessment, target_legality='legal')


def test_shared_tap_contract_binds_both_costs_and_preserves_canonical_order():
    context = PlayabilityContext('snapshot', 'v1', ENGINE_VERSIONS)
    cost = '{1}, {T}'
    source = f'{cost}: Draw a card.'
    first = TapCostCitation('first', 0, len(cost), cost)
    offset = len(source) + 1
    second = TapCostCitation('second', offset, offset + len(cost), cost)
    ref = EvidenceReference('card', 'mechanical', 'v1', 'draw', 'oracle_text', 10, 21, 'Draw a card')
    boundaries = (
        BoundaryCoverage('card', 0, 'first', 0, len(source), source, SemanticCoverageState.VERIFIED,
                         (ref.evidence_id,), activation_cost=cost),
        BoundaryCoverage('card', 0, 'second', offset, offset + len(source), source, SemanticCoverageState.PARTIAL,
                         activation_cost=cost),
    )
    group = SharedTapPrerequisite('card', 0, (second, first, second))
    assert group.costs == (first, second)
    profile = PlayabilityProfile('card', context, evidence=(ref,), boundary_coverage=boundaries,
                                 shared_tap_prerequisites=(group, group))
    assert profile.shared_tap_prerequisites == (group,)
    reordered = replace(profile, boundary_coverage=boundaries[::-1],
                        shared_tap_prerequisites=(replace(group, costs=(first, second)),))
    assert (profile.to_json(), profile.profile_id) == (reordered.to_json(), reordered.profile_id)

    with pytest.raises(ValueError, match='card and single face'):
        replace(profile, shared_tap_prerequisites=(replace(group, card_id='wrong-card'),))
    with pytest.raises(ValueError, match='single face'):
        replace(profile, boundary_coverage=(boundaries[0], replace(boundaries[1], face_index=1)))
    with pytest.raises(ValueError, match='single face'):
        replace(group, face_index=1)
    with pytest.raises(ValueError, match='exact pre-colon'):
        replace(profile, shared_tap_prerequisites=(replace(group, costs=(first, replace(second, boundary_id='missing'))),))
    shifted = replace(second, start=second.start + 1, end=second.end + 1)
    with pytest.raises(ValueError, match='exact pre-colon'):
        replace(profile, shared_tap_prerequisites=(replace(group, costs=(first, shifted)),))
    wrong_text = replace(second, text='{2}, {T}')
    with pytest.raises(ValueError, match='exact pre-colon'):
        replace(profile, shared_tap_prerequisites=(replace(group, costs=(first, wrong_text)),))
    with pytest.raises(ValueError, match='two or more nonoverlapping'):
        replace(group, costs=(first, replace(second, start=first.start, end=first.end)))
    with pytest.raises(ValueError, match='Conflicting tap cost citations'):
        replace(group, costs=(first, second, shifted))
    with pytest.raises(ValueError, match='Conflicting shared tap groups'):
        replace(profile, shared_tap_prerequisites=(group, replace(group, costs=(first, shifted))))
    with pytest.raises(ValueError, match='locally verified draw'):
        replace(profile, boundary_coverage=(replace(boundaries[0], state=SemanticCoverageState.PARTIAL), boundaries[1]))


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
