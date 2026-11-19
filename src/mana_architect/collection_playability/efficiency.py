"""Bounded, evidence-backed efficiency observations for fixed draw spells."""

from dataclasses import replace
import re

from mana_architect.collection_playability.contracts import EfficiencyFinding, SemanticCoverageState

MODEL_VERSION = "cpa-efficiency-v1"
MAX_PEERS = 64
_DRAW = re.compile(r"^Draw (a|one|two|three|four|five|\d+) cards?\.(?:\s+Target (player|opponent) mills? (a|one|two|three|four|five|\d+) cards?\.)?$", re.I)
_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


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
    if profile.boundary_coverage[0].state != SemanticCoverageState.VERIFIED:
        return None
    draws = [use for use in profile.mechanical_uses if use.role_id == "draw" and use.safety != "unsupported"]
    if len(draws) != 1:
        return None
    # A multi-effect spell is one resolving unit: all effects must be understood.
    if any(use.safety == "unsupported" for use in profile.mechanical_uses):
        return None
    return _quantity(match.group(1)), bool(match.group(2)), draws[0]


def supports_efficiency_search(inputs):
    return _shape(inputs.card, inputs.profile) is not None


def evaluate_efficiency(inputs, peers):
    """Compare only a verified fixed-draw component against compatible owned peers.

    A more expensive draw component is not an assessment of the whole spell.
    Peer selection is capped; peers are sorted before this function is called.
    """
    profile, card = inputs.profile, inputs.card
    shape = _shape(card, profile)
    if not shape:
        return profile
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
