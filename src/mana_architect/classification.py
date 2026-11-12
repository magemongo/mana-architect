from __future__ import annotations

from dataclasses import dataclass
import math
import re
from types import MappingProxyType
from typing import Mapping

from mana_architect.catalog import (
    ClassificationRule,
    MatchPredicate,
    StrategyCatalog,
)


_WHITESPACE = re.compile(r"\s+")
_MANA_SYMBOL = re.compile(r"\{([^{}]+)\}")
_APOSTROPHES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u02bc": "'",
        "\uff07": "'",
    }
)
_ORACLE_FIELDS = {"oracle_text", "oracle_text_normalized"}


@dataclass(frozen=True)
class CardClassificationInput:
    type_line: str | None = None
    oracle_text: str | None = None
    mana_cost: str | None = None
    cmc: float | None = None
    colors: tuple[str, ...] | None = None
    color_identity: tuple[str, ...] | None = None
    produced_mana: tuple[str, ...] | None = None
    keywords: tuple[str, ...] | None = None


@dataclass(frozen=True)
class NormalizedCard:
    type_line: str | None
    oracle_text: str | None
    mana_cost: str | None
    cmc: float | None
    colors: tuple[str, ...] | None
    color_identity: tuple[str, ...] | None
    produced_mana: tuple[str, ...] | None
    keywords: tuple[str, ...] | None
    type_line_normalized: str | None
    oracle_text_normalized: str | None
    oracle_text_faces: tuple[str, ...]
    mana_cost_normalized: str | None
    normalized_fields: Mapping[str, object]


@dataclass(frozen=True)
class ClassificationEvidence:
    rule_id: str
    target_type: str
    target_id: str
    source_field: str
    matched_value: str
    weight: float
    confidence: float
    requires_review: bool
    evidence_label: str
    face_index: int | None = None


@dataclass(frozen=True)
class ClassificationResult:
    evidence: tuple[ClassificationEvidence, ...]


def normalize_text(value: str) -> str:
    """Normalize text for matching without changing the source card fields."""
    apostrophe_normalized = value.translate(_APOSTROPHES).casefold()
    mana_normalized = _MANA_SYMBOL.sub(
        lambda match: "{" + _WHITESPACE.sub("", match.group(1)) + "}",
        apostrophe_normalized,
    )
    return _WHITESPACE.sub(" ", mana_normalized).strip()


def normalize_card(card: CardClassificationInput) -> NormalizedCard:
    """Create immutable match fields while retaining every supplied source value."""
    if card.cmc is not None and (
        isinstance(card.cmc, bool)
        or not isinstance(card.cmc, (int, float))
        or not math.isfinite(card.cmc)
    ):
        raise ValueError("cmc deve ser um número finito ou None.")
    if card.cmc is not None and card.cmc < 0:
        raise ValueError("cmc não pode ser negativo.")

    type_line_normalized = (
        normalize_text(card.type_line) if card.type_line is not None else None
    )
    oracle_text_faces = _normalize_oracle_faces(card.oracle_text)
    oracle_text_normalized = (
        "\n\n".join(oracle_text_faces) if card.oracle_text is not None else None
    )
    mana_cost_normalized = (
        normalize_text(card.mana_cost) if card.mana_cost is not None else None
    )
    normalized_fields: dict[str, object] = {
        "type_line": type_line_normalized,
        "type_line_normalized": type_line_normalized,
        "oracle_text": oracle_text_normalized,
        "oracle_text_normalized": oracle_text_normalized,
        "mana_cost": mana_cost_normalized,
        "cmc": card.cmc,
        "colors": _normalize_values(card.colors),
        "color_identity": _normalize_values(card.color_identity),
        "produced_mana": _normalize_values(card.produced_mana),
        "keywords": _normalize_values(card.keywords),
    }
    return NormalizedCard(
        type_line=card.type_line,
        oracle_text=card.oracle_text,
        mana_cost=card.mana_cost,
        cmc=card.cmc,
        colors=card.colors,
        color_identity=card.color_identity,
        produced_mana=card.produced_mana,
        keywords=card.keywords,
        type_line_normalized=type_line_normalized,
        oracle_text_normalized=oracle_text_normalized,
        oracle_text_faces=oracle_text_faces,
        mana_cost_normalized=mana_cost_normalized,
        normalized_fields=MappingProxyType(normalized_fields),
    )


def classify_card(
    card: CardClassificationInput | NormalizedCard,
    catalog: StrategyCatalog,
) -> ClassificationResult:
    """Return deterministic, rule-level evidence without persistence or scoring."""
    normalized = card if isinstance(card, NormalizedCard) else normalize_card(card)
    evidence: list[ClassificationEvidence] = []
    for rule in catalog.rules:
        if not rule.enabled:
            continue
        match = _match_rule(normalized, rule)
        if match is None:
            continue
        source_field, matched_value, face_index = match
        evidence.append(
            ClassificationEvidence(
                rule_id=rule.id,
                target_type=rule.target_type,
                target_id=rule.target_id,
                source_field=source_field,
                matched_value=matched_value,
                weight=rule.weight,
                confidence=rule.confidence,
                requires_review=rule.requires_review,
                evidence_label=rule.evidence_label,
                face_index=face_index,
            )
        )
    classified_roles = {
        item.target_id for item in evidence if item.target_type == "role"
    }
    for role in catalog.roles:
        if not role.oracle_keywords or role.id in classified_roles:
            continue
        for term_index, term in enumerate(role.oracle_keywords):
            normalized_term = normalize_text(term)
            matched_field = next(
                (
                    "oracle_text_normalized"
                    for segment in normalized.oracle_text_faces
                    if normalized_term in segment
                ),
                None,
            )
            matched_value = term if matched_field else None
            if matched_field is None:
                keyword_values = normalized.normalized_fields.get("keywords") or ()
                matched_keyword = next(
                    (value for value in keyword_values if normalized_term == value),
                    None,
                )
                if matched_keyword is not None:
                    matched_field = "keywords"
                    matched_value = str(matched_keyword)
            if matched_field is not None and matched_value is not None:
                evidence.append(
                    ClassificationEvidence(
                        rule_id=f"role_keyword_{role.id}_{term_index}",
                        target_type="role",
                        target_id=role.id,
                        source_field=matched_field,
                        matched_value=matched_value,
                        weight=0.5,
                        confidence=0.6,
                        requires_review=True,
                        evidence_label=(
                            f"O termo configurado para a função {role.name} "
                            "aparece nos dados Oracle da carta."
                        ),
                    )
                )
                break
    return ClassificationResult(evidence=tuple(evidence))


def _normalize_oracle_faces(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    faces = tuple(
        normalized
        for face in re.split(r"\n\s*\n", value.strip())
        if (normalized := normalize_text(face))
    )
    return faces


def _normalize_values(values: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    return tuple(normalize_text(value) for value in values)


def _match_rule(
    card: NormalizedCard,
    rule: ClassificationRule,
) -> tuple[str, str, int | None] | None:
    predicate = rule.match
    for source_field in rule.source_fields:
        segments = _field_segments(card, source_field)
        if predicate.type in {"phrase_any", "phrase_all"}:
            matched = _match_phrases(segments, predicate)
        elif predicate.type in {"type_line_contains", "keyword_contains"}:
            matched = _match_contains(segments, predicate)
        else:
            matched = _match_field_value(card, source_field, predicate)
        if matched is not None:
            matched_value, segment_index = matched
            face_index = (
                segment_index
                if source_field in _ORACLE_FIELDS
                else None
            )
            return source_field, matched_value, face_index
    return None


def _field_segments(card: NormalizedCard, field: str) -> tuple[str, ...]:
    if field in _ORACLE_FIELDS:
        return card.oracle_text_faces
    value = card.normalized_fields.get(field)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, tuple):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _match_phrases(
    segments: tuple[str, ...],
    predicate: MatchPredicate,
) -> tuple[str, int] | None:
    normalized_values = tuple(normalize_text(value) for value in predicate.values)
    if predicate.type == "phrase_any":
        for value, normalized_value in zip(predicate.values, normalized_values):
            for index, segment in enumerate(segments):
                if normalized_value in segment:
                    return value, index
        return None

    for index, segment in enumerate(segments):
        if all(value in segment for value in normalized_values):
            return "; ".join(predicate.values), index
    return None


def _match_contains(
    segments: tuple[str, ...],
    predicate: MatchPredicate,
) -> tuple[str, int] | None:
    normalized_values = tuple(normalize_text(value) for value in predicate.values)
    if predicate.type == "keyword_contains":
        for index, segment in enumerate(segments):
            normalized_segment = normalize_text(segment)
            if normalized_segment in normalized_values:
                return segment, index
        return None

    for value, normalized_value in zip(predicate.values, normalized_values):
        for index, segment in enumerate(segments):
            if re.search(
                rf"(?<!\w){re.escape(normalized_value)}(?!\w)",
                segment,
            ):
                return value, index
    return None


def _match_field_value(
    card: NormalizedCard,
    field: str,
    predicate: MatchPredicate,
) -> tuple[str, int] | None:
    actual = card.normalized_fields.get(field)
    if predicate.type == "numeric_threshold":
        if (
            not isinstance(actual, (int, float))
            or isinstance(actual, bool)
            or not isinstance(predicate.value, (int, float))
            or isinstance(predicate.value, bool)
            or predicate.operator is None
        ):
            return None
        if _compare_numbers(actual, predicate.operator, predicate.value):
            return f"{predicate.operator} {predicate.value}", 0
        return None

    expected = predicate.value
    if expected is None:
        return None
    if isinstance(actual, tuple):
        expected_normalized = (
            normalize_text(expected) if isinstance(expected, str) else expected
        )
        if expected_normalized in actual:
            return str(expected), actual.index(expected_normalized)
        return None
    if isinstance(actual, str) and isinstance(expected, str):
        if actual == normalize_text(expected):
            return expected, 0
        return None
    if isinstance(actual, bool) or isinstance(expected, bool):
        return (str(expected), 0) if type(actual) is type(expected) and actual == expected else None
    if actual == expected and isinstance(actual, (int, float)) and isinstance(
        expected, (int, float)
    ):
        return str(expected), 0
    return None


def _compare_numbers(actual: float, operator: str, expected: float) -> bool:
    if operator == "gt":
        return actual > expected
    if operator == "gte":
        return actual >= expected
    if operator == "lt":
        return actual < expected
    if operator == "lte":
        return actual <= expected
    return actual == expected
