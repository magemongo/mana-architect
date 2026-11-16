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
_SHAPES = (
    ("draw", re.compile(rf"^(?:{_DRAW})\.$", re.I)),
    ("draw_mill", re.compile(rf"^(?:{_DRAW})\.\s+(?:{_MILL})\.$", re.I)),
    ("mill", re.compile(rf"^(?:{_MILL})\.$", re.I)),
    ("mana", re.compile(rf"^(?:{_MANA})\.$", re.I)),
    ("return", re.compile(rf"^(?:{_RETURN})\.$", re.I)),
    ("remove", re.compile(rf"^(?:{_REMOVE})\.$", re.I)),
    ("damage_trigger", re.compile(rf"^(?:{_DAMAGE_TRIGGER})\.$", re.I)),
)


def assess_boundary(boundary, capabilities, references, unknowns):
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
    return BoundaryCoverage(
        boundary.card_id, boundary.face_index, boundary.ability_id, boundary.start, boundary.end,
        boundary.source_text, state, tuple(ref.evidence_id for ref in refs), unknown, cost,
        targeting, conditions, () if verified else ("semantic_completeness_unverified",),
    )
