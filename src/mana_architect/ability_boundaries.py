"""Resolve conservative Oracle ability spans without changing parser identities."""

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Literal

from mana_architect.oracle_text import OracleParse


RESOLVER_VERSION = "ability-boundaries-v1"
AbilityKind = Literal["spell", "activated", "triggered", "static", "modal", "unknown"]
_FACE = " // "
_START = re.compile(
    r"(?im)(?:^|(?<=\n)|(?<=\. ))[ \t]*"
    r"(?=(?:when|whenever|at)\b|(?:\{[^{}]+\}|[^\n.:]+,\s*)+[^\n.:]*:)"
)
_TRIGGER = re.compile(r"^(?:[^,.\n]+ — )?(?:when|whenever|at)\b", re.IGNORECASE)
_MODE = re.compile(r"\bchoose (?:one|two|three|four|one or more|up to one|up to two)\s*—", re.IGNORECASE)
_NESTED = re.compile(r'["“”]|\b(?:gains?|has|have)\s+["“]|\b(?:saga|class|level up)\b', re.IGNORECASE)


@dataclass(frozen=True)
class AbilityBoundary:
    ability_id: str
    card_id: str
    face_index: int
    kind: AbilityKind
    start: int
    end: int
    source_text: str
    status: Literal["resolved", "ambiguous"]
    parser_ability_ids: tuple[str, ...]
    activation_cost: str | None = None
    limitations: tuple[str, ...] = ()
    modal_choices: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class AbilityAssociation:
    status: Literal["resolved", "ambiguous", "unmatched"]
    ability_id: str | None = None
    reason: str | None = None


def resolve_ability_boundaries(
    parsed: OracleParse, *, card_id: str, type_line: str | None,
) -> tuple[AbilityBoundary, ...]:
    """Return deterministic, source-linked spans with conservative ambiguity."""
    if not card_id:
        raise ValueError("A canonical card identity is required.")
    text = parsed.oracle_text
    faces = text.split(_FACE)
    types = (type_line or "").split(_FACE)
    offset = 0
    result = []
    for face_index, face in enumerate(faces):
        face_type = types[face_index] if len(types) == len(faces) else ""
        is_spell = bool({"Instant", "Sorcery"} & set(face_type.split(" — ", 1)[0].split()))
        starts = {match.start() for match in _START.finditer(face)}
        starts.update(match.end() for match in re.finditer(r"\n\s*\n", face))
        starts.add(0)
        points = sorted(starts)
        spans = []
        for index, start in enumerate(points):
            end = points[index + 1] if index + 1 < len(points) else len(face)
            raw = face[start:end]
            left = len(raw) - len(raw.lstrip())
            right = len(raw.rstrip())
            if left >= right:
                continue
            spans.append((start + left, start + right))
        # Plain spell paragraphs form one resolution. Ability starts and mode
        # structures invalidate this inference and stay separate or ambiguous.
        if is_spell and len(spans) > 1 and all(
            not _TRIGGER.match(face[start:end]) and ":" not in face[start:end]
            for start, end in spans
        ):
            spans = [(spans[0][0], spans[-1][1])]
        for start, end in spans:
            source = face[start:end]
            absolute_start, absolute_end = offset + start, offset + end
            ambiguous = bool(_NESTED.search(source))
            if _MODE.search(source):
                kind: AbilityKind = "modal"
                ambiguous = True
            elif _TRIGGER.match(source):
                kind = "triggered"
            elif ":" in source and not source.lstrip().startswith("•"):
                kind = "activated"
            elif is_spell:
                kind = "spell"
            elif face_type:
                kind = "static"
            else:
                kind = "unknown"
                ambiguous = True
            # An embedded ability start without a recognized separator is not
            # assigned a confident boundary.
            if re.search(r"(?i)\.\s+(?:when|whenever|at)\b", source):
                ambiguous = True
            cost = source.partition(":")[0].strip() if kind == "activated" else None
            identity = sha256(
                f"{RESOLVER_VERSION}\0{card_id}\0{face_index}\0{absolute_start}\0{absolute_end}\0{source}".encode("utf-8")
            ).hexdigest()
            paragraph_ids = tuple(
                ability.id for ability in parsed.abilities
                if ability.start < absolute_end and ability.end > absolute_start
            )
            result.append(AbilityBoundary(
                identity, card_id, face_index, kind, absolute_start, absolute_end, source,
                "ambiguous" if ambiguous else "resolved", paragraph_ids, cost,
                ("unsupported_boundary_structure",) if ambiguous else (),
                tuple((absolute_start + match.start(), absolute_start + match.end())
                      for match in re.finditer(r"•[^\n•]+", source)) if kind == "modal" else (),
            ))
        offset += len(face) + len(_FACE)
    return tuple(result)


def associate_evidence(
    boundaries: tuple[AbilityBoundary, ...], *, start: int, end: int,
) -> AbilityAssociation:
    """Associate an Oracle span only when one resolved boundary contains it."""
    if start < 0 or end <= start:
        raise ValueError("Evidence requires a nonempty source span.")
    overlapping = tuple(item for item in boundaries if item.start < end and item.end > start)
    if not overlapping:
        return AbilityAssociation("unmatched", reason="outside_resolved_text")
    if len(overlapping) != 1 or overlapping[0].status != "resolved":
        return AbilityAssociation("ambiguous", reason="ambiguous_boundary")
    boundary = overlapping[0]
    if start < boundary.start or end > boundary.end:
        return AbilityAssociation("ambiguous", reason="evidence_crosses_boundary")
    return AbilityAssociation("resolved", boundary.ability_id)
