"""Map native evaluations into role-local quality and evidence records."""

from mana_architect.card_advantage import evaluate_cast
from mana_architect.deck_pipeline.contracts import (
    CardEvaluations, EvaluationState, Role, RoleMembership,
)
from mana_architect.deck_pipeline.inventory import CardSnapshot
from mana_architect.synergy_interactions import MatchStatus


def _ramp_membership(card: CardSnapshot, evaluations: CardEvaluations) -> RoleMembership:
    role = Role.RAMP
    profile = evaluations.ramp
    if profile is None:
        return RoleMembership(role, False, EvaluationState.NOT_EVALUATED)
    best = profile.best_line
    if best is None:
        state = EvaluationState.UNSUPPORTED if "//" in (card.type_line or "") else EvaluationState.EVALUATED
        return RoleMembership(role, False, state)

    subtypes = set()
    evidence_ids = set()
    conditions = set()
    types = set((card.type_line or "").partition("—")[0].split())
    for line in profile.lines:
        event = line.event
        subtype = line.mechanism.replace(" ", "_")
        if event.action == "put_land":
            subtype = "land_to_battlefield"
        elif event.action == "permit_land_play":
            subtype = "extra_land_play"
        elif event.action == "create_token":
            subtype = f"{event.object}_generation"
        elif line.mechanism == "reusable mana":
            if "Creature" in types:
                subtype = "mana_dork"
            elif "Artifact" in types:
                subtype = "mana_rock"
        subtypes.add(subtype)
        span = event.evidence
        evidence_ids.add(f"ramp:{span.ability_id}:{span.start}:{span.end}")
        conditions.update((*event.requirements, *event.restrictions))
        if line.conditional:
            conditions.add("conditional_ramp")

    return RoleMembership(
        role, True, EvaluationState.EVALUATED, best.score, "ramp_0_100", best.score / 100,
        tuple(sorted(subtypes)), tuple(sorted(evidence_ids)), tuple(sorted(conditions)),
        "conditional" if best.conditional else "supported",
    )


def _advantage_membership(evaluations: CardEvaluations) -> RoleMembership:
    role = Role.CARD_ADVANTAGE
    profile = evaluations.card_advantage
    if profile is None:
        return RoleMembership(role, False, EvaluationState.NOT_EVALUATED)
    if not profile.lines:
        return RoleMembership(role, False, EvaluationState.UNSUPPORTED)

    positive = []
    for line in profile.lines:
        net = evaluate_cast(line).self_delta
        if net > 0:
            positive.append((net, line))
    if not positive:
        return RoleMembership(role, False, EvaluationState.EVALUATED)
    best = max(net for net, _ in positive)
    return RoleMembership(
        role, True, EvaluationState.EVALUATED, best, "strict_net_cards", best / (1 + best),
        tuple(sorted({"permanent_enters_draw" if line.retained else "direct_draw" for _, line in positive})),
        tuple(sorted({
            f"card_advantage:{line.draw.ability_id}:{line.draw.source_span[0]}:{line.draw.source_span[1]}:{line.mode}"
            for _, line in positive
        })),
        ("spell_resolves",), "supported",
    )


def _interaction_membership(
    evaluations: CardEvaluations, kind: str, role: Role,
) -> RoleMembership:
    profile = evaluations.interaction
    if profile is None:
        return RoleMembership(role, False, EvaluationState.NOT_EVALUATED)
    best = profile.best_variant(kind)
    if best is None:
        return RoleMembership(role, False, EvaluationState.EVALUATED)
    variants = tuple(variant for variant in profile.variants if kind in variant.interaction_types)
    effects = tuple(
        effect for variant in variants for effect in variant.effects if effect.interaction_type == kind
    )
    score = best.spot_score.total if kind == "spot" else best.mass_score.total
    return RoleMembership(
        role, True, EvaluationState.EVALUATED if score is not None else EvaluationState.UNSCORED,
        score, "interaction_0_10", None if score is None else score / 10,
        tuple(sorted({tag for effect in effects for tag in (effect.action, *effect.mechanical_tags)})),
        tuple(sorted({effect.effect_id for effect in effects})),
        tuple(sorted({
            term for variant in variants for term in (*variant.requirements, *variant.additional_costs)
        } | {
            term for effect in effects for term in (*effect.conditions, *effect.restrictions)
        })),
        profile.classification_confidence.value,
    )


def _synergy_membership(evaluations: CardEvaluations) -> RoleMembership:
    role = Role.STRATEGY_SYNERGY
    profile = evaluations.strategy_synergy
    if profile is None:
        return RoleMembership(role, False, EvaluationState.NOT_EVALUATED)
    supported_ids = {
        evidence_id for affinity in profile.strategy_affinities if affinity.score > 0
        for evidence_id in affinity.evidence_ids
    } | set(profile.commander_synergy.evidence_ids)
    evidence = tuple(
        item for item in profile.evidence if item.id in supported_ids and item.weight > 0
        and item.status in (MatchStatus.VALIDATED, MatchStatus.CONDITIONAL, MatchStatus.POTENTIAL)
    )
    score = profile.scores.overall
    if not evidence or score <= 0:
        return RoleMembership(role, False, EvaluationState.EVALUATED)
    subtypes = {role_id for affinity in profile.strategy_affinities for role_id in affinity.roles}
    subtypes.update(
        item.source.id if item.source_card_id == profile.card_id else item.target.id
        for item in evidence if item.source_card_id == profile.card_id or item.target is not None
    )
    return RoleMembership(
        role, True, EvaluationState.EVALUATED, score, "strategy_synergy_0_100", score / 100,
        tuple(sorted(subtypes)), tuple(sorted(item.id for item in evidence)),
        tuple(sorted({
            term for item in evidence for term in (*item.dependencies, *item.missing_capabilities)
        })),
        ",".join(sorted({item.status.value for item in evidence})),
    )


def _land_membership(card: CardSnapshot) -> RoleMembership:
    role = Role.LANDS
    faces = tuple(set(face.partition("—")[0].split()) for face in (card.type_line or "").split(" // "))
    if "Land" not in faces[0]:
        # Flattened metadata cannot distinguish a playable modal face from a transform face.
        state = (
            EvaluationState.UNSUPPORTED if any("Land" in types for types in faces[1:])
            else EvaluationState.EVALUATED
        )
        return RoleMembership(role, False, state)
    return RoleMembership(
        role, True, EvaluationState.EVALUATED,
        subtypes=("basic_land" if "Basic" in faces[0] else "nonbasic_land",),
        evidence_ids=("type_line:0",), confidence="printed_type",
    )


def map_role_memberships(
    card: CardSnapshot, evaluations: CardEvaluations | None,
) -> tuple[RoleMembership, ...]:
    land = _land_membership(card)
    if evaluations is None:
        return tuple(
            land if role is Role.LANDS else RoleMembership(role, False, EvaluationState.NOT_EVALUATED)
            for role in Role
        )
    return (
        _ramp_membership(card, evaluations), _advantage_membership(evaluations),
        _interaction_membership(evaluations, "spot", Role.SPOT_INTERACTION),
        _interaction_membership(evaluations, "mass", Role.MASS_INTERACTION),
        _synergy_membership(evaluations), land,
    )
