"""Bounded, evidence-backed efficiency observations for fixed draw."""

from dataclasses import dataclass, replace
import re

from mana_architect.collection_playability.contracts import (
    EfficiencyFinding, EvaluationStatus, EvidenceReference, ExecutionUnit, MechanicalUse, SemanticCoverageState,
    TargetedMillAssessment,
)
from mana_architect.collection_playability.semantic_coverage import MANA_TAP_DRAW_VERIFIER

MODEL_VERSION = "cpa-efficiency-v1"
ACTIVATION_MODEL_VERSION = "cpa-activation-draw-efficiency-v1"
MAX_PEERS = 64
_ACTIVATION_PREFIX = re.compile(r"(?P<mana>(?:\{(?:[0-9]+|[WUBRGC])\})+),[ \t]*\{T\}:[ \t]*Draw ")
_DRAW = re.compile(r"^Draw (a|one|two|three|four|five|\d+) cards?\.(?:\s+Target (player|opponent) mills? (a|one|two|three|four|five|\d+) cards?\.)?$", re.I)
_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def _bundle_link(card, profile):
    """Require the sole target to be the exact mill clause in the shared spell."""
    match = _DRAW.fullmatch((card.oracle_text or "").strip()) if card else None
    if not match or match.group(2) is None:
        return None
    if (card.type_line != "Sorcery" or profile.card_id != card.card_id or len(profile.execution_units) != 1 or len(profile.boundary_coverage) != 1
            or any(dep.status != "independent" for dep in profile.card_dependencies)):
        return None
    [unit] = profile.execution_units
    [boundary] = profile.boundary_coverage
    text = boundary.source_text
    if (boundary.state != SemanticCoverageState.VERIFIED or text != card.oracle_text.strip()
            or boundary.face_index != 0 or boundary.card_id != card.card_id
            or unit.card_id != card.card_id or unit.face_index != 0 or unit.kind != "spell"
            or unit.ability_id != boundary.boundary_id or unit.status != EvaluationStatus.EVALUATED
            or unit.mode_ids or "at_least_one_target_legal" not in unit.requirements):
        return None
    references = {ref.evidence_id: ref for ref in profile.evidence}
    linked = []
    for role, clause in (("draw", text.partition(".")[0]), ("mill_opponent", text.partition(".")[2].strip().rstrip("."))):
        uses = [use for use in profile.mechanical_uses if use.role_id == role]
        if len(uses) != 1:
            return None
        [use] = uses
        if (use.safety == "unsupported" or use.execution_unit_id != unit.unit_id
                or "legal_target_at_resolution" not in use.requirements or len(use.evidence_ids) != 1):
            return None
        ref = references.get(use.evidence_ids[0])
        offset = boundary.source_start + text.index(clause)
        if (ref is None or (ref.card_id, ref.source, ref.record_id, ref.source_field, ref.start, ref.end, ref.text)
                != (card.card_id, "mechanical", role, "oracle_text", offset, offset + len(clause), clause)
                or ref.evidence_id not in unit.evidence_ids or ref.evidence_id not in boundary.recognized_evidence_ids
                or card.oracle_text[ref.start:ref.end] != ref.text):
            return None
        if role == "draw" and use.target_coverage != "not_targeted":
            return None
        if role == "mill_opponent" and (use.target_coverage not in {"typed", "restricted"}
                or (match.group(2).lower() == "opponent" and "target_opponent" not in use.restrictions)):
            return None
        linked.append(ref.evidence_id)
    if unit.target_evidence_ids != (linked[1],):
        return None
    return unit, linked[1]


def assess_targeted_mill(inputs, strategies=None):
    """Annotate a verified player-mill bundle before owned-peer retrieval."""
    card, profile = inputs.card, inputs.profile
    match = _DRAW.fullmatch((card.oracle_text or "").strip()) if card else None
    if not match or match.group(2) is None:
        return profile
    link = _bundle_link(card, profile)
    if link is None:
        return replace(profile, reasons=(*profile.reasons, "bundle_target_evidence_unresolved"),
                       efficiency_findings=(), targeted_mill_assessment=None)
    if match.group(2).lower() != "player":
        return profile
    unit, mill_id = link
    benefit = bool(strategies and "mill" in profile.context.strategy_ids and any(
        strategy.id == "mill" and any(
            preference.id == "mill_theme_mill_opponent" and preference.capability == "mill_opponent"
            and preference.weight > 0 and not preference.types and preference.subtype is None and preference.reference is None
            for preference in strategy.preferences
        ) for strategy in strategies.strategies
    ))
    assessment = TargetedMillAssessment(unit.unit_id, (mill_id,), (mill_id,))
    if benefit:
        assessment = replace(assessment, mill_contribution="conditional_benefit", strategy_id="mill",
                             preference_id="mill_theme_mill_opponent", catalog_fingerprint=profile.provenance.catalog_fingerprint,
                             explanation=(*assessment.explanation,
                                 "Choosing a legal opponent and resolving the spell conditionally advances the explicitly selected mill objective."))
    return replace(profile, targeted_mill_assessment=assessment)


def _quantity(value):
    return _WORDS[value.lower()] if value.lower() in _WORDS else int(value)


def _cost(value):
    if not value or not re.fullmatch(r"(?:\{(?:\d+|[WUBRGC])\})+", value):
        return None
    symbols = re.findall(r"\{([^}]+)\}", value)
    return sum(int(s) for s in symbols if s.isdecimal()), tuple(sorted(s for s in symbols if not s.isdecimal()))


def _shape(card, profile):
    if (card is None or card.type_line != "Sorcery" or card.commander_legality != "legal"
            or card.color_identity is None or not card.oracle_text or _cost(card.mana_cost) is None):
        return None
    match = _DRAW.fullmatch(card.oracle_text.strip())
    if not match or len(profile.boundary_coverage) != 1:
        return None
    if match.group(2) and _bundle_link(card, profile) is None:
        return None
    if profile.boundary_coverage[0].state != SemanticCoverageState.VERIFIED:
        return None
    draws = [use for use in profile.mechanical_uses if use.role_id == "draw" and use.safety != "unsupported"]
    if len(draws) != 1:
        return None
    # A multi-effect spell is one resolving unit: all effects must be understood.
    if any(use.safety == "unsupported" for use in profile.mechanical_uses):
        return None
    return _quantity(match.group(1)), bool(match.group(2)), draws[0]


def _artifact_draw_prefix(card):
    if (card is None or (card.type_line or "").partition(" — ")[0] != "Artifact"
            or card.layout not in {None, "normal"} or not card.oracle_text or " // " in card.oracle_text):
        return None
    return _ACTIVATION_PREFIX.match(card.oracle_text.lstrip())


def peer_prefilter(card, model):
    if model == ACTIVATION_MODEL_VERSION:
        return bool(_artifact_draw_prefix(card))
    return card.type_line == "Sorcery" and card.oracle_text and card.oracle_text.strip().casefold().startswith("draw ")


@dataclass(frozen=True)
class _ActivationDraw:
    quantity: int
    generic_mana: int
    mana_symbols: tuple[str, ...]
    color_identity: tuple[str, ...]
    use: MechanicalUse
    unit: ExecutionUnit
    native_evidence: EvidenceReference
    cost_evidence: EvidenceReference

    @property
    def peer_key(self):
        return (self.quantity, self.mana_symbols, self.color_identity, self.use.timing, self.unit.timing,
                self.use.requirements, self.unit.requirements, self.use.target_coverage, self.use.restrictions)


def _activation_shape(inputs):
    card, profile = inputs.card, inputs.profile
    prefix = _artifact_draw_prefix(card)
    if (not prefix or card.commander_legality != "legal" or card.color_identity is None
            or profile.context.scope != "collection" or profile.card_id != card.card_id
            or not any(row.card_id == card.card_id and row.quantity > 0 for row in inputs.printings)
            or inputs.mechanics is None or len(profile.boundary_coverage) != 1
            or len(profile.execution_units) != 1 or len(profile.mechanical_uses) != 1
            or any(dep.status != "independent" for dep in profile.card_dependencies)):
        return None
    [boundary] = profile.boundary_coverage
    [unit] = profile.execution_units
    [use] = profile.mechanical_uses
    oracle = card.oracle_text
    start, end = boundary.source_start, boundary.source_end
    cost = boundary.activation_cost
    if (not cost or boundary.verifier_version != MANA_TAP_DRAW_VERIFIER
            or boundary.state != SemanticCoverageState.VERIFIED or (boundary.card_id, boundary.face_index) != (card.card_id, 0)
            or oracle[start:end] != boundary.source_text or boundary.source_text != oracle.strip()
            or not boundary.source_text.startswith(cost + ":") or boundary.targeting
            or (unit.card_id, unit.face_index, unit.ability_id, unit.kind, unit.activation_cost)
            != (card.card_id, 0, boundary.boundary_id, "activated", cost)
            or unit.status != EvaluationStatus.EVALUATED or unit.mode_ids or unit.target_evidence_ids
            or use.execution_unit_id != unit.unit_id or use.role_id != "draw"
            or use.safety != "conditional" or use.applicability != "conditional" or use.target_coverage != "not_targeted"
            or not {"pay_ability_cost", "tap_source"} <= set(use.requirements)):
        return None

    draw_refs = set(use.evidence_ids) & set(unit.evidence_ids) & set(boundary.recognized_evidence_ids)
    native = [cap for cap in inputs.mechanics.capabilities if cap.id == "draw" and cap.costs == (cost,)
              and cap.quantity.kind == "fixed" and cap.quantity.value and cap.quantity.value > 0
              and cap.timing == unit.timing == use.timing == "activated"
              and cap.targeting == "none" and not cap.resolution_targets]
    if len(native) != 1:
        return None
    [draw] = native
    span = draw.evidence
    refs = [ref for ref in profile.evidence if ref.evidence_id in draw_refs
            and (ref.card_id, ref.source, ref.record_id, ref.source_field, ref.start, ref.end, ref.text)
            == (card.card_id, "mechanical", "draw", "oracle_text", span.start, span.end, span.oracle_span)
            and ref.extraction_boundary_id is None and start + len(cost) < ref.start < ref.end <= end
            and oracle[ref.start:ref.end] == ref.text]
    if len(refs) != 1:
        return None
    generic, symbols = _cost(prefix["mana"])
    cost_ref = EvidenceReference(card.card_id, "activation_cost", ACTIVATION_MODEL_VERSION,
                                 boundary.boundary_id, "oracle_text", start, start + len(cost), cost)
    return _ActivationDraw(draw.quantity.value, generic, symbols, tuple(sorted(card.color_identity)),
                           use, unit, refs[0], cost_ref)


def efficiency_search_model(inputs):
    if _shape(inputs.card, inputs.profile) is not None:
        return MODEL_VERSION
    if _activation_shape(inputs) is not None:
        return ACTIVATION_MODEL_VERSION
    return None


def _evaluate_activation_efficiency(inputs, subject, peers):
    candidates = []
    for peer in peers:
        other = _activation_shape(peer)
        if (other is not None and other.cost_evidence.card_id != subject.cost_evidence.card_id
                and other.peer_key == subject.peer_key and other.generic_mana < subject.generic_mana):
            candidates.append(other)
    refs = (subject.native_evidence, subject.cost_evidence)
    conditions = (*subject.use.requirements, *subject.unit.requirements, "owned peer scope")
    peer_id = None
    assessment = "unknown"
    explanation = ("No compatible lower-cost owned activation was established within the bounded search.",)
    if candidates:
        peer = min(candidates, key=lambda item: (item.generic_mana, item.cost_evidence.card_id))
        refs += (peer.native_evidence, peer.cost_evidence)
        peer_id = peer.cost_evidence.card_id
        assessment = "higher_mana_for_same_draw"
        delta = subject.generic_mana - peer.generic_mana
        cards = "card" if subject.quantity == 1 else "cards"
        explanation = (
            f"One activation costs {subject.cost_evidence.text} versus {peer.cost_evidence.text}. "
            f"The subject pays {delta} more generic mana for the same draw of {subject.quantity} {cards}.",
            "This compares one activation under matching printed prerequisites and excludes casting costs.",
            "A later activation requires the source to be able to tap again and its payment to be available again.",
        )
    finding = EfficiencyFinding("draw", subject.unit.unit_id, assessment, tuple(ref.evidence_id for ref in refs),
                                peer_id, conditions, explanation, ACTIVATION_MODEL_VERSION)
    return replace(inputs.profile, evidence=(*inputs.profile.evidence, *refs), efficiency_findings=(finding,))


def evaluate_efficiency(inputs, peers, strategies=None):
    """Compare only a verified fixed-draw component against compatible owned peers.

    A more expensive draw component is not an assessment of the whole spell.
    Peer selection is capped; peers are sorted before this function is called.
    """
    profile, card = assess_targeted_mill(inputs, strategies), inputs.card
    shape = _shape(card, profile)
    if not shape:
        activation = _activation_shape(inputs)
        return _evaluate_activation_efficiency(inputs, activation, peers) if activation else profile
    amount, has_mill, use = shape
    base_cost = _cost(card.mana_cost)
    candidates = []
    for peer in peers:
        other = _shape(peer.card, peer.profile)
        if not other or other[0] != amount or other[1]:
            continue
        if tuple(sorted(peer.card.color_identity)) != tuple(sorted(card.color_identity)):
            continue
        generic, colors = _cost(peer.card.mana_cost)
        if colors != base_cost[1] or generic >= base_cost[0]:
            continue
        candidates.append((generic, peer.card.card_id, peer, other[2]))
    if not candidates:
        finding = EfficiencyFinding(
            "draw", use.execution_unit_id, "unknown", use.evidence_ids,
            conditions=use.requirements,
            explanation=("No compatible lower-cost owned peer was established within the bounded search.",
                         "No general efficiency or global comparison is implied."),
        )
        return replace(profile, efficiency_findings=(finding,))
    generic, _, peer, peer_use = min(candidates, key=lambda x: (x[0], x[1]))
    peer_refs = tuple(ref for ref in peer.profile.evidence if ref.evidence_id in peer_use.evidence_ids)
    conditions = (*use.requirements, *use.restrictions,
                  "draw-focused context; other effects may matter", "owned peer; not a global benchmark")
    finding = EfficiencyFinding(
        "draw", use.execution_unit_id, "higher_mana_for_same_draw", (*use.evidence_ids, *peer_use.evidence_ids),
        peer.card.card_id, conditions,
        (f"Both sorceries draw {amount}; subject costs {card.mana_cost}, peer costs {peer.card.mana_cost}.",
         "Colored mana symbols match; the subject requires more generic mana.",
         "Secondary mill has conditional value and is not priced or ignored as a possible strategic benefit."
         if has_mill else "This compares the draw component, not overall card value.",
         "Both spells are sorcery speed and one-shot; no repeatable resources are assumed."),
    )
    return replace(profile, evidence=(*profile.evidence, *peer_refs), efficiency_findings=(finding,))
