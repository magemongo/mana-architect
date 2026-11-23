"""Conservative semantic coverage for explicitly supported ability shapes."""

import re

from mana_architect.collection_playability.contracts import BoundaryCoverage, SemanticCoverageState


_DRAW = r"draw (?:a|one|two|three|four|five|\d+) cards?"
_MILL = r"target (?:player|opponent) mills? (?:a|one|two|three|four|five|\d+) cards?"
_MANA = r"add (?:\{[WUBRGC]\})+"
_RETURN = r"return this card from your graveyard to the battlefield(?: tapped)?"
_REMOVE = r"destroy target (?:creature|artifact|enchantment|land|planeswalker|permanent)(?: with flying)?"
_DAMAGE_TRIGGER = (
    r"whenever (?:this creature is dealt damage|a creature dies), "
    r"(?:this creature|it) deals (?:that much|\d+) damage to (?:you|each opponent)"
)
_COST = r"(?:\{(?:\d+|[WUBRGCSTQ])\})+"
MANA_TAP_DRAW_VERIFIER = "cpa-boundary-coverage-mana-tap-draw-v1"
_MANA_TAP_DRAW = re.compile(
    r"(?P<cost>(?:\{(?:[0-9]+|[WUBRGC])\})+,[ \t]*\{T\}):[ \t]*"
    r"(?P<effect>Draw (?P<count>a|one|two|three|four|five|[0-9]+) (?P<noun>cards?))\."
)
_DRAW_COUNTS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_SHAPES = (
    ("draw", re.compile(rf"^(?:{_DRAW})\.$", re.I)),
    ("draw_mill", re.compile(rf"^(?:{_DRAW})\.\s+(?:{_MILL})\.$", re.I)),
    ("mill", re.compile(rf"^(?:{_MILL})\.$", re.I)),
    ("mana", re.compile(rf"^(?:{_MANA})\.$", re.I)),
    ("return", re.compile(rf"^(?:{_RETURN})\.$", re.I)),
    ("remove", re.compile(rf"^(?:{_REMOVE})\.$", re.I)),
    ("damage_trigger", re.compile(rf"^(?:{_DAMAGE_TRIGGER})\.$", re.I)),
)


def _matches_native_mana_tap_draw(boundary, match, capabilities, refs):
    count_text = match["count"]
    count = (count_text.lstrip("0") or "0") if count_text.isdecimal() else str(_DRAW_COUNTS[count_text])
    if count == "0" or (match["noun"] == "card") != (count == "1"):
        return False
    start = boundary.start + match.start("effect")
    end = boundary.start + match.end("effect")
    return any(
        cap.id == "draw" and cap.timing == "activated" and cap.targeting == "none"
        and not cap.resolution_targets and cap.costs == (match["cost"],)
        and cap.quantity.kind == "fixed" and str(cap.quantity.value) == count
        and cap.evidence.start == start and cap.evidence.end in {end, end + 1}
        and any(ref.source == "mechanical" and ref.record_id == cap.id
                and (ref.start, ref.end, ref.text) == (start, cap.evidence.end, cap.evidence.oracle_span)
                and ref.text == boundary.source_text[start - boundary.start:ref.end - boundary.start]
                for ref in refs)
        for cap in capabilities
    )


def assess_boundary(boundary, capabilities, references, unknowns, *, single_face=True):
    """Verify known complete shapes and preserve all other boundaries as uncertain."""
    refs = tuple(ref for ref in references if ref.card_id == boundary.card_id
                 and ref.source_field == "oracle_text" and boundary.start <= ref.start < ref.end <= boundary.end)
    overlapping_unknowns = tuple(span for span in unknowns if span.source_field == "oracle_text"
                                 and span.start < boundary.end and span.end > boundary.start)
    unknown = tuple((max(span.start, boundary.start), min(span.end, boundary.end))
                    for span in overlapping_unknowns)
    text = boundary.source_text.strip()
    cost = boundary.activation_cost
    body = text
    cost_supported = True
    if boundary.kind == "activated":
        cost_supported = bool(cost and re.fullmatch(_COST, cost, re.I))
        body = text.partition(":")[2].strip()
    shape = next((name for name, pattern in _SHAPES if pattern.fullmatch(body)), None)
    ids = {cap.id for cap in capabilities}
    native_supported = {
        "draw": "draw" in ids,
        "draw_mill": "draw" in ids and "mill_opponent" in ids,
        "mill": "mill_opponent" in ids,
        "mana": "add_mana" in ids,
        "return": "return_from_graveyard" in ids,
        "remove": any(ref.source == "interaction" for ref in refs),
        "damage_trigger": "deal_noncombat_damage" in ids,
    }.get(shape, False)
    mana_tap_draw = _MANA_TAP_DRAW.fullmatch(boundary.source_text) if boundary.kind == "activated" else None
    if mana_tap_draw:
        cost_supported = single_face and cost == mana_tap_draw["cost"]
        native_supported = _matches_native_mana_tap_draw(boundary, mana_tap_draw, capabilities, refs)
    # A matching Interaction rule supplies the complete supported removal
    # semantics even when the general capability extractor leaves it unknown.
    independently_supported = (
        shape == "remove" and native_supported
        or shape == "mana" and any(ref.source == "ramp" for ref in refs)
    )
    # A warning on the original unsplit line remains diagnostic. A positive
    # scoped extraction for the exact supported shape may supersede it, but
    # a warning from within the boundary still applies to that ability.
    scoped = any(ref.extraction_boundary_id == boundary.ability_id and ref.record_id != "unresolved"
                 for ref in refs)
    local_unknown = any(boundary.start <= span.start < span.end <= boundary.end for span in overlapping_unknowns)
    unsupported = bool(unknown) and not independently_supported and (not scoped or local_unknown)
    verified = (
        boundary.status == "resolved" and boundary.kind in {"spell", "activated", "triggered"}
        and cost_supported and shape is not None and native_supported and not unsupported
    )
    state = (SemanticCoverageState.VERIFIED if verified else
             SemanticCoverageState.PARTIAL if refs else SemanticCoverageState.UNKNOWN)
    targeting = ("mandatory_target",) if verified and re.search(r"\btarget\b", body, re.I) else ()
    conditions = ("pay_activation_cost",) if verified and cost else ()
    version = {"verifier_version": MANA_TAP_DRAW_VERIFIER} if mana_tap_draw else {}
    return BoundaryCoverage(
        boundary.card_id, boundary.face_index, boundary.ability_id, boundary.start, boundary.end,
        boundary.source_text, state, tuple(ref.evidence_id for ref in refs), unknown, cost,
        targeting, conditions, () if verified else ("semantic_completeness_unverified",),
        **version,
    )
