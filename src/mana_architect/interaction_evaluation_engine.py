"""Build an explainable interaction profile from a card and its Oracle parse."""

from dataclasses import dataclass, replace
from itertools import combinations
import re

from mana_architect.interaction_effects import extract_interaction_effects
from mana_architect.interaction_profile import (
    Confidence, InteractionEffect, InteractionProfile, InteractionVariant, ModeChoice, Scope, Timing,
)
from mana_architect.interaction_rules_catalog import mask_reminder_text
from mana_architect.interaction_scoring import (
    DEFAULT_INTERACTION_SCORING, InteractionScoringConfig, score_interaction_variant,
)
from mana_architect.models import Card
from mana_architect.oracle_text import OracleParse, parse_oracle_text

_FACE_SEPARATOR = " // "
_TRIGGER = re.compile(r"^(?:[^,.]+ — )?(?:when|whenever|at)\b", re.IGNORECASE)
_ABILITY_WORD = re.compile(r"^[^,.;:]+ — (?=(?:when|whenever|at|if)\b)", re.IGNORECASE)
_MODE_HEADER = re.compile(
    r"^(?P<prefix>.*?)\bchoose (?P<count>one or more|up to one|up to two|one|two|three|four)\s*—\s*$",
    re.IGNORECASE,
)
_MULTIPLE_MODE_RULE = re.compile(r"\b(?:choose the same mode|mode more than once|entwine|escalate|spree)\b", re.IGNORECASE)
_ALTERNATE = re.compile(r"(?im)^\s*(?P<kind>overload|kicker)\s+(?P<cost>(?:\{[^{}]+\})+(?:,[^\n]+)?)")
_UNSUPPORTED_COST = re.compile(r"(?im)^\s*(?:multikicker|cleave|bargain|pitch|evoke|entwine|escalate|spree)\b")
_VARIABLE_CAST_COST = re.compile(r"(?im)^this spell costs?\s+(?:\{[^{}]+\})+\s+(?:less|more) to cast\b")
_ADDITIONAL = re.compile(r"as an additional cost to cast [^,\n]+, (?P<cost>[^.\n]+)", re.IGNORECASE)
_CAST_METADATA = re.compile(r"^(?:kicker|overload|as an additional cost|this spell costs?)\b", re.IGNORECASE)
_KICKED = re.compile(r"\bif (?:this spell|it) (?:was|is) kicked\b", re.IGNORECASE)
_NOT_KICKED = re.compile(r"\bif (?:this spell|it) (?:was|is)n't kicked\b", re.IGNORECASE)
_SORCERY = re.compile(r"\b(?:only as a sorcery|only during your main phase)\b", re.IGNORECASE)
_NONMANA = re.compile(r"\b(?:sacrifice|discard|pay|exile|return|remove|tap|reveal)\b", re.IGNORECASE)
_PARTS = re.compile(r"[^\r\n•]+|•(?P<bullet>[^\r\n•]*)")
_NAME = re.compile(r"\b(?:one|two|three|four)\b")
_MODE_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4}


@dataclass(frozen=True)
class InteractionFace:
    type_line: str | None
    mana_cost: str | None
    mana_value: float | None = None
    index: int | None = None


@dataclass(frozen=True)
class _Source:
    ability_id: str
    text: str
    offset: int
    activation_cost: str | None = None
    trigger: bool = False
    face: InteractionFace = InteractionFace(None, None)


def _sources(parsed: OracleParse, faces: tuple[InteractionFace, ...]) -> tuple[_Source, ...]:
    text = parsed.oracle_text
    separators = tuple(match.start() for match in re.finditer(re.escape(_FACE_SEPARATOR), text))
    starts = (0, *(position + len(_FACE_SEPARATOR) for position in separators))
    ends = (*separators, len(text))
    sources = []
    for face, start, end in zip(faces, starts, ends, strict=True):
        for ability in parsed.abilities:
            left, right = max(ability.start, start), min(ability.end, end)
            if left >= right:
                continue
            masked = mask_reminder_text(text[left:right])
            for part in _PARTS.finditer(masked):
                raw = part.group()
                offset = left + part.start() + len(raw) - len(raw.lstrip())
                line = raw.strip()
                if not line:
                    continue
                trigger = bool(_TRIGGER.match(line))
                colon = line.find(":")
                activation = None
                if colon >= 0 and not trigger:
                    activation = line[:colon].strip()
                    remainder = line[colon + 1:]
                    offset += colon + 1 + len(remainder) - len(remainder.lstrip())
                    line = remainder.lstrip()
                ability_word = _ABILITY_WORD.match(line)
                if ability_word:
                    offset += ability_word.end()
                    line = line[ability_word.end():]
                sources.append(_Source(ability.id, line, offset, activation, trigger, face))
    return tuple(sources)


def _metadata(
    type_line: str | None, mana_cost: str | None, mana_value: float | None,
    parsed: OracleParse, faces: tuple[InteractionFace, ...],
) -> tuple[InteractionFace, ...]:
    face_count = parsed.oracle_text.count(_FACE_SEPARATOR) + 1
    if faces:
        if len(faces) != face_count:
            raise ValueError("Face metadata must match the number of Oracle text faces.")
        return tuple(replace(face, index=index if face_count > 1 else None) for index, face in enumerate(faces))
    if face_count == 1:
        return (InteractionFace(type_line, mana_cost, mana_value),)
    types = (type_line or "").split(_FACE_SEPARATOR)
    costs = (mana_cost or "").split(_FACE_SEPARATOR)
    return tuple(InteractionFace(
        types[index] or None if len(types) == face_count else None,
        costs[index] or None if len(costs) == face_count else None,
        index=index,
    ) for index in range(face_count))


def _timing(source: _Source, face_text: str, oracle_keywords: tuple[str, ...]) -> Timing:
    types = set((source.face.type_line or "").split("—", 1)[0].split())
    if source.activation_cost is not None:
        return Timing.SORCERY if _SORCERY.search(source.text) else Timing.INSTANT
    if source.trigger:
        return Timing.TRIGGERED
    if "Instant" in types or "flash" in {keyword.casefold() for keyword in oracle_keywords} or re.search(r"(?im)^flash\b", face_text):
        return Timing.INSTANT
    if "Sorcery" in types:
        return Timing.SORCERY
    return Timing.STATIC if types else Timing.UNKNOWN


def _spell_instruction(source: _Source) -> bool:
    types = set((source.face.type_line or "").split("—", 1)[0].split())
    return bool(types.intersection({"Instant", "Sorcery"}) and not source.trigger
                and source.activation_cost is None and not source.text.startswith("•")
                and not _MODE_HEADER.match(source.text))


def _variant(
    variant_id: str, source: _Source, effects: tuple[InteractionEffect, ...],
    cost: str | None, timing: Timing, requirements: tuple[str, ...],
    additional: tuple[str, ...], modes: tuple[str, ...] = (),
) -> InteractionVariant:
    scopes = {effect.scope_type for effect in effects}
    scope = next(iter(scopes)) if len(scopes) == 1 else Scope.MODAL if scopes else Scope.UNKNOWN
    effects = tuple(replace(effect, additional_costs=additional, timing=timing) for effect in effects)
    return InteractionVariant(variant_id, source.ability_id, cost, timing, scope, effects,
                              requirements, additional, modes, source.face.index,
                              source.face.mana_value, source.activation_cost is not None)


def _variant_id(source: _Source, execution: str) -> str:
    return f"{source.ability_id}:{source.offset}:{execution}"


def _read_modes(
    parsed: OracleParse, header: _Source, options: list[_Source], mode: re.Match[str],
    timing: Timing, cost: str | None, requirements: tuple[str, ...], additional: tuple[str, ...],
) -> tuple[ModeChoice | None, tuple[InteractionVariant, ...], tuple[str, ...]]:
    count = mode["count"].casefold()
    fixed = _NAME.search(count)
    assert fixed is not None
    minimum = 0 if count.startswith("up to ") else _MODE_COUNTS[fixed.group()]
    maximum = len(options) if count == "one or more" else _MODE_COUNTS[fixed.group()]
    if not options or maximum > len(options) or maximum > 10 or len(options) > 10:
        return None, (), (f"Unresolved modal selection at {header.offset}.",)
    modes = []
    effects_by_mode = []
    warnings = []
    for index, option in enumerate(options, start=1):
        raw = option.text[1:]
        offset = option.offset + 1 + len(raw) - len(raw.lstrip())
        effects, notes = extract_interaction_effects(parsed, header.ability_id, raw.lstrip(), offset)
        mode_id = f"{_variant_id(header, 'mode')}{index}"
        modes.append((mode_id, parsed.oracle_text[offset:option.offset + len(option.text)]))
        effects_by_mode.append((mode_id, effects))
        warnings.extend(notes)
    variants = []
    for size in range(minimum, maximum + 1):
        for selection in combinations(effects_by_mode, size):
            mode_ids = tuple(mode_id for mode_id, _ in selection)
            effects = tuple(effect for _, items in selection for effect in items)
            label = "+".join(mode_id.rsplit(":", 1)[-1] for mode_id in mode_ids) or "no_modes"
            variants.append(_variant(_variant_id(header, label), header, effects, cost, timing,
                                     requirements, additional, mode_ids))
    choice = ModeChoice(header.ability_id, minimum, maximum, tuple(modes), header.face.index)
    return choice, tuple(variants), tuple(warnings)


def classify_card_interaction(card: Card, parsed: OracleParse | None = None) -> InteractionProfile:
    """Classify a collection card using its stored metadata and optional existing parse."""
    return build_interaction_profile(
        card.oracle_id, parsed if parsed is not None else parse_oracle_text(card.oracle_text),
        type_line=card.type_line, mana_cost=card.mana_cost, mana_value=card.cmc,
        oracle_keywords=tuple(card.keywords or ()),
    )


def build_interaction_profile(
    card_id: str, parsed: OracleParse, *, type_line: str | None = None, mana_cost: str | None = None,
    mana_value: float | None = None, oracle_keywords: tuple[str, ...] = (),
    faces: tuple[InteractionFace, ...] = (), layout: str | None = None,
    scoring: InteractionScoringConfig | None = DEFAULT_INTERACTION_SCORING,
) -> InteractionProfile:
    """Classify possible execution variants before deck construction."""
    metadata = _metadata(type_line, mana_cost, mana_value, parsed, faces)
    sources = _sources(parsed, metadata)
    choices = []
    variants = []
    warnings = []
    face_texts = tuple(mask_reminder_text(text) for text in parsed.oracle_text.split(_FACE_SEPARATOR))
    if len(metadata) > 1 and layout not in {"modal_dfc", "split", "adventure", "transform", "flip"}:
        warnings.append("Face-specific execution requirements could not be resolved.")
    for face_index, face in enumerate(metadata):
        face_text = face_texts[face_index]
        alternate = {match["kind"].casefold(): match["cost"].strip() for match in _ALTERNATE.finditer(face_text)}
        additional = tuple(match["cost"].strip() for match in _ADDITIONAL.finditer(face_text))
        if _UNSUPPORTED_COST.search(face_text) or len(alternate) > 1:
            warnings.append(f"Unsupported alternate costing on face {face_index + 1}.")
        face_sources = [source for source in sources if source.face == face]
        index = 0
        while index < len(face_sources):
            source = face_sources[index]
            index += 1
            timing = _timing(source, face_text, oracle_keywords)
            cost = face.mana_cost
            requirements = ()
            if source.trigger:
                requirements = ("trigger_condition",)
            if _VARIABLE_CAST_COST.search(face_text) and source.activation_cost is None:
                requirements += ("variable_cast_cost",)
            if layout == "transform" and face_index > 0:
                requirements += ("transformed_face",)
            costs = additional
            if source.activation_cost is not None:
                cost = source.activation_cost
                requirements += ("source_available",)
                if _NONMANA.search(cost):
                    costs = (cost,)
                else:
                    costs = ()
            mode = _MODE_HEADER.match(source.text)
            if mode:
                options = []
                while index < len(face_sources) and face_sources[index].text.startswith("•"):
                    options.append(face_sources[index])
                    index += 1
                if _MULTIPLE_MODE_RULE.search(face_text):
                    warnings.append(f"Unsupported modal selection rule at {source.offset}.")
                    continue
                choice, legal, notes = _read_modes(parsed, source, options, mode, timing, cost, requirements, costs)
                if choice:
                    choices.append(choice)
                variants.extend(legal)
                warnings.extend(notes)
                continue
            if source.text.startswith("•"):
                warnings.append(f"Unresolved mode header at {source.offset}.")
                continue
            if _CAST_METADATA.match(source.text):
                continue
            instructions = [source]
            if _spell_instruction(source):
                while index < len(face_sources) and _spell_instruction(face_sources[index]):
                    continuation = face_sources[index]
                    if not _CAST_METADATA.match(continuation.text):
                        instructions.append(continuation)
                    index += 1
            effects, notes = _read_effects(parsed, instructions)
            warnings.extend(notes)
            if not effects:
                continue
            default_effects = tuple(effect for effect in effects if not any(_KICKED.search(item) for item in effect.conditions))
            if default_effects:
                variants.append(_variant(_variant_id(source, "default"), source, default_effects,
                                         cost, timing, requirements, costs))
            if "overload" in alternate and source.activation_cost is None and not source.trigger:
                overloaded, notes = _read_effects(parsed, instructions, overload=True)
                if overloaded != effects:
                    variants.append(_variant(_variant_id(source, "overload"), source, overloaded,
                                             alternate["overload"], timing, requirements + ("overload",), costs))
                warnings.extend(notes)
            if "kicker" in alternate and source.activation_cost is None:
                kicked = tuple(effect for effect in effects if not any(_NOT_KICKED.search(item) for item in effect.conditions))
                if kicked:
                    variants.append(_variant(_variant_id(source, "kicker"), source, kicked, cost,
                                             timing, requirements + ("kicked",), costs + (alternate["kicker"],)))
            elif len(default_effects) != len(effects):
                warnings.append(f"Unresolved kicker requirement at {source.offset}.")
    unresolved_scope = any(effect.scope_type is Scope.UNKNOWN for variant in variants for effect in variant.effects)
    if unresolved_scope:
        warnings.append("One or more affected scopes could not be resolved.")
    confidence = (Confidence.HIGH if not warnings else
                  Confidence.MEDIUM if any(variant.effects for variant in variants) else Confidence.LOW)
    if scoring is not None:
        for index, variant in enumerate(variants):
            spot, mass = score_interaction_variant(variant, scoring)
            variants[index] = replace(variant, spot_score=spot, mass_score=mass)
    return InteractionProfile(card_id, parsed.parser_version, oracle_keywords, tuple(variants),
                              tuple(choices), confidence, tuple(dict.fromkeys(warnings)),
                              scoring_version=scoring.version if scoring is not None else None)


def _read_effects(
    parsed: OracleParse, instructions: list[_Source], *, overload: bool = False,
) -> tuple[tuple[InteractionEffect, ...], tuple[str, ...]]:
    effects = []
    warnings = []
    for instruction in instructions:
        found, notes = extract_interaction_effects(
            parsed, instruction.ability_id, instruction.text, instruction.offset, overload=overload,
        )
        effects.extend(found)
        warnings.extend(notes)
    return tuple(effects), tuple(warnings)
