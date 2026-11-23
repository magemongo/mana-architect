"""Derive card roles and qualitative applicability from native engine evidence."""

from dataclasses import replace
import re
from typing import TYPE_CHECKING

from mana_architect.ability_boundaries import associate_evidence, resolve_ability_boundaries
from mana_architect.collection_playability.contracts import (
    EvaluationStatus, ExecutionUnit, MechanicalUse, PlayabilityProfile, ViableContext,
)
from mana_architect.interaction_profile import Selection
from mana_architect.collection_playability.semantic_coverage import MANA_TAP_DRAW_VERIFIER, assess_boundary
from mana_architect.collection_playability.shared_tap import find_shared_tap_prerequisites
from mana_architect.collection_playability.dependencies import assess_dependencies
from mana_architect.collection_playability.contracts import SemanticCoverageState
from mana_architect.strategy_synergy import capability_requirements, matches_strategy_preference
from mana_architect.synergy_capabilities import MechanicalCapability
from mana_architect.synergy_catalog import SynergyStrategyCatalog

if TYPE_CHECKING:
    from mana_architect.collection_playability.adapters import CollectionCardInputs


_METADATA_CAPABILITIES = frozenset({
    "keyword_ability", "printed_type", "printed_supertype", "printed_subtype", "resolve_spell",
})


def _use(role, evidence_id, timing, requirements=(), restrictions=(), *, targeted=False, typed=False):
    if targeted is None:
        coverage = "unknown"
    elif not targeted:
        coverage = "not_targeted"
    elif restrictions:
        coverage = "restricted"
    else:
        coverage = "typed" if typed else "unknown"
    if coverage == "restricted":
        applicability = "narrow"
    elif coverage == "unknown":
        applicability = "unknown"
    elif requirements or restrictions:
        applicability = "conditional"
    else:
        applicability = "general"
    return MechanicalUse(role, (evidence_id,), applicability, coverage, requirements, restrictions, timing)


def _capability_use(capability: MechanicalCapability, evidence_id: str, shared_target: bool = False) -> MechanicalUse:
    requirements = set(capability_requirements(capability))
    if capability.source_zone == "graveyard":
        requirements.add("graveyard_access")
        if capability.object.reference == "self":
            requirements.add("card_in_graveyard")
    restrictions = (*capability.object.restrictions, *capability.object.subtypes, *capability.object.colors)
    targeted = capability.targeting == "target" or bool(capability.resolution_targets)
    if capability.resolution_targets:
        requirements.add("legal_target_at_resolution")
    if shared_target:
        requirements.add("legal_target_at_resolution")
    if capability.timing == "sorcery_speed" and capability.costs:
        requirements.add("sorcery_timing")
    player_mill = capability.action == "mill" and capability.targeting == "target"
    if player_mill and capability.controller == "opponent":
        restrictions += ("target_opponent",)
    return _use(
        capability.id, evidence_id, capability.timing, tuple(requirements), restrictions,
        targeted=targeted if targeted or capability.targeting in {"none", "each", "all"} else None,
        typed=player_mill or bool(capability.object.types) or capability.object.kind in {"player", "spell", "permanent"},
    )


def _repeatable_fodder(capability: MechanicalCapability) -> bool:
    return (
        capability.id == "return_from_graveyard"
        and capability.object.reference == "self"
        and "creature" in capability.object_types
        and capability.source_zone == "graveyard"
        and capability.destination_zone == "battlefield"
        and capability.owner == capability.controller == "you"
        and capability.timing in {"activated", "sorcery_speed"}
        and bool(capability.costs)
        and capability.repeatability == "repeatable"
    )


def evaluate_mechanics(inputs: "CollectionCardInputs", catalog: SynergyStrategyCatalog) -> PlayabilityProfile:
    """Evaluate Phase 1 for a card already bound to collection and engine inputs."""
    profile = inputs.profile
    if profile.status != EvaluationStatus.NOT_EVALUATED:
        return profile
    card, mechanics = inputs.card, inputs.mechanics
    assert card is not None and mechanics is not None and inputs.interaction is not None
    assert inputs.ramp is not None and inputs.card_advantage is not None
    evidence = {
        (ref.source, ref.record_id, ref.source_field, ref.start, ref.end): ref.evidence_id
        for ref in profile.evidence
    }
    selected = set(profile.context.strategy_ids)
    strategies = tuple(strategy for strategy in catalog.strategies if not selected or strategy.id in selected)
    uses = set()
    contexts = set()
    assert inputs.parsed is not None
    boundaries = resolve_ability_boundaries(inputs.parsed, card_id=card.card_id, type_line=card.type_line)
    boundary_map = {boundary.ability_id: boundary for boundary in boundaries}
    capability_groups = {}
    for capability in mechanics.capabilities:
        span = capability.evidence
        if span.source_field != "oracle_text":
            continue
        association = associate_evidence(boundaries, start=span.start, end=span.end)
        if association.status == "resolved":
            capability_groups.setdefault(association.ability_id, []).append(capability)
    for variant in inputs.interaction.variants:
        for effect in variant.effects:
            association = associate_evidence(
                boundaries, start=effect.evidence.start, end=effect.evidence.end,
            )
            if association.status == "resolved":
                capability_groups.setdefault(association.ability_id, [])

    units = []
    uncertain_variants = set()
    for variant in inputs.interaction.variants:
        refs = tuple(
            evidence["interaction", effect.effect_id, "oracle_text", effect.evidence.start, effect.evidence.end]
            for effect in variant.effects
        )
        targets = tuple(
            evidence["interaction", effect.effect_id, "oracle_text", effect.evidence.start, effect.evidence.end]
            for effect in variant.effects if effect.selection == Selection.TARGET
        )
        choice = next((choice for choice in inputs.interaction.mode_choices
                       if choice.ability_id == variant.ability_id and choice.face_index == variant.face_index), None)
        associations = {
            associate_evidence(boundaries, start=effect.evidence.start, end=effect.evidence.end).ability_id
            for effect in variant.effects
        }
        mode_supported = bool(choice and variant.mode_ids)
        if (None in associations and not mode_supported) or len(associations) > 1:
            uncertain_variants.add(variant.variant_id)
        if len(targets) > 1 or any(
            effect.target_count.kind in {"up_to", "variable", "unknown"}
            or effect.target_count.min == 0
            for effect in variant.effects if effect.selection == Selection.TARGET
        ):
            uncertain_variants.add(variant.variant_id)
        units.append(ExecutionUnit(
            variant.variant_id, card.card_id, variant.face_index or 0,
            "activated" if variant.activated else ("triggered" if variant.timing.value == "triggered" else "spell"),
            variant.ability_id, refs, variant.mode_ids,
            choice.minimum if choice else None, choice.maximum if choice else None,
            ("at_least_one_target_legal",) if targets else (), targets,
            variant.cost if variant.activated else None, variant.timing.value,
        ))

    boundary_units = {}
    incomplete_boundaries = set()
    single_face = " // " not in inputs.parsed.oracle_text and "//" not in card.type_line
    assessments = {
        boundary.ability_id: assess_boundary(
            boundary, capability_groups.get(boundary.ability_id, ()), profile.evidence, mechanics.unknowns,
            single_face=single_face,
        )
        for boundary in boundaries
    }
    dependencies, affected_boundaries = assess_dependencies(boundaries, assessments, capability_groups)
    verified_draw_ids = {
        boundary_id for boundary_id, capabilities in capability_groups.items()
        if assessments[boundary_id].state == SemanticCoverageState.VERIFIED
        and any(cap.id == "draw" for cap in capabilities)
    }
    shared_tap = find_shared_tap_prerequisites(card, boundaries, verified_draw_ids)
    for boundary_id, capabilities in capability_groups.items():
        boundary = boundary_map[boundary_id]
        if boundary.kind not in {"spell", "activated", "triggered"} or not capabilities:
            continue
        refs = tuple(
            evidence["mechanical", cap.id, cap.evidence.source_field, cap.evidence.start, cap.evidence.end]
            for cap in capabilities
        )
        targets = tuple(
            evidence["mechanical", cap.id, cap.evidence.source_field, cap.evidence.start, cap.evidence.end]
            for cap in capabilities if cap.targeting == "target" or cap.resolution_targets
        )
        target_uncertain = bool(re.search(
            r"\b(?:up to|any number of|another target|targets?)\b", boundary.source_text, re.IGNORECASE
        ) and (
            re.search(r"\b(?:up to|any number of)\b", boundary.source_text, re.IGNORECASE)
            or len(re.findall(r"\btarget\b", boundary.source_text, re.IGNORECASE)) > 1
        ))
        compatible = [unit for unit in units if not unit.mode_ids and unit.face_index == boundary.face_index
                      and unit.kind == boundary.kind and unit.evidence_ids
                      and all(boundary.start <= ref.start and ref.end <= boundary.end
                              for ref in profile.evidence if ref.evidence_id in unit.evidence_ids)]
        if len(compatible) == 1:
            prior = compatible[0]
            units[units.index(prior)] = replace(
                prior, evidence_ids=(*prior.evidence_ids, *refs),
                target_evidence_ids=(*prior.target_evidence_ids, *targets),
                requirements=tuple(set(prior.requirements) | ({"at_least_one_target_legal"} if targets else set())),
            )
            boundary_units[boundary_id] = prior.unit_id
        else:
            units.append(ExecutionUnit(
                boundary_id, card.card_id, boundary.face_index, boundary.kind, boundary_id, refs,
                requirements=("at_least_one_target_legal",) if targets else (),
                target_evidence_ids=targets, activation_cost=boundary.activation_cost,
                timing=capabilities[0].timing,
            ))
            boundary_units[boundary_id] = boundary_id
        if target_uncertain:
            uncertain_variants.add(boundary_units[boundary_id])
        if assessments[boundary_id].state != SemanticCoverageState.VERIFIED:
            incomplete_boundaries.add(boundary_id)
            uncertain_variants.add(boundary_units[boundary_id])
        if boundary_id in affected_boundaries:
            incomplete_boundaries.add(boundary_id)
            uncertain_variants.add(boundary_units[boundary_id])
        if assessments[boundary_id].state != SemanticCoverageState.VERIFIED and any(
            unknown.source_field == "oracle_text"
            and unknown.start < boundary.end and unknown.end > boundary.start
            for unknown in mechanics.unknowns
        ):
            incomplete_boundaries.add(boundary_id)
            uncertain_variants.add(boundary_units[boundary_id])

    unresolved = False
    for capability in mechanics.capabilities:
        if capability.id in _METADATA_CAPABILITIES or capability.quantity.value == 0:
            continue
        span = capability.evidence
        key = evidence["mechanical", capability.id, span.source_field, span.start, span.end]
        association = (associate_evidence(boundaries, start=span.start, end=span.end)
                       if span.source_field == "oracle_text" else None)
        boundary_id = association.ability_id if association and association.status == "resolved" else None
        group = capability_groups.get(boundary_id, ())
        target_count = sum(bool(cap.targeting == "target" or cap.resolution_targets) for cap in group)
        use = _capability_use(capability, key, target_count == 1)
        if boundary_id in boundary_units:
            assessment = assessments[boundary_id]
            requirements = set(use.requirements)
            if assessment.state == SemanticCoverageState.VERIFIED and assessment.activation_cost:
                requirements.add("pay_ability_cost")
                if assessment.verifier_version == MANA_TAP_DRAW_VERIFIER:
                    requirements.update(("legal_activation_timing", "meet_creature_tap_restriction_if_applicable"))
                for symbol, requirement in (("{T}", "tap_source"), ("{Q}", "untap_source")):
                    if symbol in assessment.activation_cost.upper():
                        requirements.add(requirement)
            applicability = "conditional" if use.applicability == "general" and requirements else use.applicability
            use = replace(use, execution_unit_id=boundary_units[boundary_id], requirements=tuple(requirements),
                          applicability=applicability)
        elif span.source_field == "oracle_text":
            use = replace(use, applicability="unknown", requirements=(*use.requirements, "execution_unit_unresolved"))
            unresolved = True
        if target_count > 1:
            use = replace(use, applicability="unknown", requirements=(*use.requirements, "multiple_target_resolution_unresolved"))
            unresolved = True
        if boundary_id in boundary_units and boundary_units[boundary_id] in uncertain_variants:
            use = replace(use, applicability="unknown", requirements=(*use.requirements, "target_semantics_unresolved"))
            unresolved = True
        if boundary_id in incomplete_boundaries:
            use = replace(use, applicability="unknown", requirements=(*use.requirements, "native_effect_coverage_unresolved"))
            unresolved = True
        if boundary_id in affected_boundaries:
            use = replace(use, applicability="unknown", requirements=(*use.requirements, "card_dependency_unresolved"))
            unresolved = True
        if use.safety == "unsupported" and use.applicability != "unknown":
            use = replace(use, applicability="unknown", requirements=(*use.requirements, "execution_unit_unresolved"))
            unresolved = True
        uses.add(use)
        fodder = _repeatable_fodder(capability)
        if fodder and use.safety != "unsupported":
            fodder_use = replace(
                use, role_id="repeatable_sacrifice_fodder", applicability="conditional",
                requirements=(*use.requirements, "compatible_sacrifice_outlet"),
            )
            uses.add(fodder_use)
        # This rule establishes printed activation mechanics only.
        if use.safety == "unsupported" or (
            boundary_id in assessments and assessments[boundary_id].verifier_version == MANA_TAP_DRAW_VERIFIER
        ):
            continue
        for strategy in strategies:
            for preference in strategy.preferences:
                if not matches_strategy_preference(preference, capability, card.type_line):
                    continue
                contexts.add(ViableContext(
                    strategy.id, preference.role, "conditional" if use.requirements else "plausible",
                    use.evidence_ids, use.requirements,
                ))
                if fodder and "fodder_feeds_sacrifice_outlet" in strategy.interaction_rule_ids:
                    contexts.add(ViableContext(
                        strategy.id, fodder_use.role_id, "conditional",
                        fodder_use.evidence_ids, fodder_use.requirements,
                    ))

    for variant in inputs.interaction.variants:
        for effect in variant.effects:
            if effect.interaction_type is None:
                continue
            span = effect.evidence
            key = evidence["interaction", effect.effect_id, "oracle_text", span.start, span.end]
            requirements = set((*effect.conditions, *variant.requirements))
            if effect.selection == Selection.TARGET:
                requirements.add("legal_target_at_resolution")
            if variant.additional_costs or effect.additional_costs or variant.activated:
                requirements.add("pay_ability_cost")
            if variant.mode_ids:
                requirements.add("select_supported_mode")
                requirements.update(f"selected_mode:{mode_id}" for mode_id in variant.mode_ids)
            if effect.affected_zone == "battlefield":
                requirements.add("relevant_permanent_on_battlefield")
            restrictions = (*effect.restrictions, *effect.exceptions)
            if effect.controller_filter in {"you", "opponent", "opponents"}:
                restrictions += (f"controller:{effect.controller_filter}",)
            use = _use(
                f"{effect.interaction_type}_interaction", key, variant.timing.value,
                tuple(requirements), restrictions,
                targeted=None if effect.selection == Selection.UNKNOWN else effect.selection == Selection.TARGET,
                typed=bool(effect.object_types),
            )
            association = associate_evidence(boundaries, start=span.start, end=span.end)
            assessment = assessments.get(association.ability_id)
            if (variant.variant_id in uncertain_variants or inputs.interaction.warnings
                or association.ability_id in affected_boundaries
                or assessment is None or assessment.state != SemanticCoverageState.VERIFIED):
                use = replace(use, applicability="unknown", requirements=(*use.requirements, "interaction_semantics_unresolved"))
                unresolved = True
            if association.ability_id in affected_boundaries:
                use = replace(use, requirements=(*use.requirements, "card_dependency_unresolved"))
            uses.add(replace(use, execution_unit_id=variant.variant_id))

    for line in inputs.ramp.lines:
        span = line.event.evidence
        key = evidence["ramp", span.ability_id, "oracle_text", span.start, span.end]
        requirements = set(line.event.requirements)
        if line.event.activation_cost:
            requirements.add("pay_ability_cost")
        if "T" in line.event.activation_symbols:
            requirements.add("tap_source")
        if line.conditional:
            requirements.add("meet_native_ramp_conditions")
        use = _use(
            "mana_acceleration", key, line.event.timing, tuple(requirements), line.event.restrictions,
        )
        association = associate_evidence(boundaries, start=span.start, end=span.end)
        unit_id = boundary_units.get(association.ability_id)
        if unit_id:
            unit = next(item for item in units if item.unit_id == unit_id)
            units[units.index(unit)] = replace(unit, evidence_ids=(*unit.evidence_ids, key))
            use = replace(use, execution_unit_id=unit_id)
            if association.ability_id in incomplete_boundaries or unit_id in uncertain_variants:
                use = replace(use, applicability="unknown", requirements=(*use.requirements, "native_effect_coverage_unresolved"))
                unresolved = True
            if association.ability_id in affected_boundaries:
                use = replace(use, applicability="unknown", requirements=(*use.requirements, "card_dependency_unresolved"))
        else:
            use = replace(use, applicability="unknown", requirements=(*use.requirements, "execution_unit_unresolved"))
            unresolved = True
        uses.add(use)

    if not any(use.role_id == "draw" for use in uses):
        for line in inputs.card_advantage.lines:
            start, end = line.draw.source_span
            key = evidence["card_advantage", line.draw.ability_id, "oracle_text", start, end]
            requirements = ("resolve_cast_line",) if line.mode == "normal" else ("resolve_evoke_cast_line",)
            association = associate_evidence(boundaries, start=start, end=end)
            if association.ability_id in affected_boundaries:
                requirements += ("card_dependency_unresolved",)
            uses.add(replace(_use("draw", key, "unknown", requirements), applicability="unknown",
                             requirements=(*requirements, "execution_unit_unresolved")))
            unresolved = True

    incomplete = unresolved or any(
        item.source in {"mechanical", "interaction", "ramp"} and item.status == EvaluationStatus.UNSUPPORTED
        for item in profile.coverage
    )
    if not uses:
        return replace(
            profile,
            status=EvaluationStatus.UNSUPPORTED if incomplete else EvaluationStatus.INSUFFICIENT_EVIDENCE,
            reasons=("no_supported_mechanical_use",),
            execution_units=tuple(units),
            boundary_coverage=tuple(assessments.values()),
            card_dependencies=dependencies,
            shared_tap_prerequisites=shared_tap,
        )
    applicability = {use.applicability for use in uses}
    return replace(
        profile,
        status=EvaluationStatus.EVALUATED,
        dimensions=(
            ("mechanical_utility", "identified"),
            ("applicability", next(iter(applicability)) if len(applicability) == 1 else "varies_by_effect"),
        ),
        mechanical_uses=tuple(uses),
        execution_units=tuple(units),
        boundary_coverage=tuple(assessments.values()),
        card_dependencies=dependencies,
        shared_tap_prerequisites=shared_tap,
        viable_contexts=tuple(contexts),
        confidence="low" if incomplete or "unknown" in applicability else "medium",
        reasons=("partial_native_coverage",) if incomplete else (),
    )
