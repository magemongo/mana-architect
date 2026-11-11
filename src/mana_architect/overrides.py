from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Literal

from mana_architect.catalog import StrategyCatalog
from mana_architect.classification import ClassificationEvidence, ClassificationResult


ClassificationTargetType = Literal["strategy", "tag", "role"]
OverrideAction = Literal["force_include", "force_exclude", "set_level"]
ClassificationSource = Literal["automatic", "manual", "mixed"]
_TARGET_TYPES = {"strategy", "tag", "role"}
_OVERRIDE_ACTIONS = {"force_include", "force_exclude", "set_level"}
_STRATEGY_LEVELS = {"primary", "secondary", "complementary"}


@dataclass(frozen=True)
class ManualOverride:
    target_type: ClassificationTargetType
    target_id: str
    action: OverrideAction
    level: str | None = None
    confidence_score: float | None = None
    reason: str | None = None
    author: str | None = None
    recorded_at: datetime | None = None


@dataclass(frozen=True)
class ClassificationDecision:
    target_type: ClassificationTargetType
    target_id: str
    included: bool
    level: str | None
    confidence_score: float | None
    source: ClassificationSource
    automatic_evidence: tuple[ClassificationEvidence, ...]
    override: ManualOverride | None
    conflict: bool


def resolve_classification_overrides(
    automatic_result: ClassificationResult,
    overrides: tuple[ManualOverride, ...],
    catalog: StrategyCatalog,
) -> tuple[ClassificationDecision, ...]:
    """Apply in-memory manual decisions while retaining all automatic evidence."""
    catalog_ids = {
        "strategy": {item.id for item in catalog.strategies},
        "tag": {item.id for item in catalog.tags},
        "role": {item.id for item in catalog.roles},
    }
    automatic: dict[tuple[str, str], list[ClassificationEvidence]] = {}
    for evidence in automatic_result.evidence:
        target_type = _validated_target_type(evidence.target_type)
        if evidence.target_id not in catalog_ids[target_type]:
            raise ValueError(
                f"ID inexistente no catálogo: {target_type} '{evidence.target_id}'."
            )
        key = (target_type, evidence.target_id)
        automatic.setdefault(key, []).append(evidence)

    manual_candidates: dict[tuple[str, str], list[ManualOverride]] = {}
    for override in overrides:
        _validate_override(override, catalog_ids)
        key = (override.target_type, override.target_id)
        manual_candidates.setdefault(key, []).append(override)
    manual = {
        key: _select_override(key, candidates)
        for key, candidates in manual_candidates.items()
    }

    keys = set(automatic) | set(manual)
    decisions: list[ClassificationDecision] = []
    for target_type, target_id in sorted(keys):
        key = (target_type, target_id)
        evidence = tuple(automatic.get(key, ()))
        override = manual.get(key)
        automatic_confidence = max(
            (item.confidence for item in evidence),
            default=None,
        )
        if override is None:
            decisions.append(
                ClassificationDecision(
                    target_type=_validated_target_type(target_type),
                    target_id=target_id,
                    included=True,
                    level=None,
                    confidence_score=automatic_confidence,
                    source="automatic",
                    automatic_evidence=evidence,
                    override=None,
                    conflict=False,
                )
            )
            continue

        included = override.action != "force_exclude"
        decisions.append(
            ClassificationDecision(
                target_type=_validated_target_type(target_type),
                target_id=target_id,
                included=included,
                level=override.level,
                confidence_score=(
                    override.confidence_score
                    if override.confidence_score is not None
                    else automatic_confidence
                ),
                source="mixed" if included and evidence else "manual",
                automatic_evidence=evidence,
                override=override,
                conflict=bool(evidence) and not included,
            )
        )
    return tuple(decisions)


def _validate_override(
    override: ManualOverride,
    catalog_ids: dict[str, set[str]],
) -> None:
    if not isinstance(override.target_type, str) or override.target_type not in _TARGET_TYPES:
        raise ValueError(f"Tipo de alvo inválido: {override.target_type!r}.")
    if not isinstance(override.target_id, str) or not override.target_id:
        raise ValueError("target_id deve ser um texto não vazio.")
    if override.target_id not in catalog_ids[override.target_type]:
        raise ValueError(
            f"ID inexistente no catálogo: {override.target_type} '{override.target_id}'."
        )
    if not isinstance(override.action, str) or override.action not in _OVERRIDE_ACTIONS:
        raise ValueError(f"Ação de override inválida: {override.action!r}.")

    if override.action == "set_level":
        if override.target_type != "strategy":
            raise ValueError("set_level só é válido para alvos strategy.")
        if not isinstance(override.level, str) or override.level not in _STRATEGY_LEVELS:
            raise ValueError(
                "set_level exige level primary, secondary ou complementary."
            )
    elif override.level is not None and (
        override.action != "force_include"
        or override.target_type != "strategy"
        or not isinstance(override.level, str)
        or override.level not in _STRATEGY_LEVELS
    ):
        raise ValueError(
            "level em force_include exige alvo strategy e um nível de afinidade válido."
        )

    if override.confidence_score is not None and (
        isinstance(override.confidence_score, bool)
        or not isinstance(override.confidence_score, (int, float))
        or not math.isfinite(override.confidence_score)
        or not 0 <= override.confidence_score <= 1
    ):
        raise ValueError("confidence_score deve ser um número finito entre 0 e 1.")
    if override.action == "force_exclude" and override.confidence_score is not None:
        raise ValueError("force_exclude não aceita confidence_score.")
    if override.reason is not None and not isinstance(override.reason, str):
        raise ValueError("reason deve ser texto ou None.")
    if override.author is not None and not isinstance(override.author, str):
        raise ValueError("author deve ser texto ou None.")
    if override.recorded_at is not None and (
        not isinstance(override.recorded_at, datetime)
        or override.recorded_at.tzinfo is None
        or override.recorded_at.utcoffset() is None
    ):
        raise ValueError("recorded_at deve conter timezone ou ser None.")


def _validated_target_type(value: str) -> ClassificationTargetType:
    if value == "strategy":
        return "strategy"
    if value == "tag":
        return "tag"
    if value == "role":
        return "role"
    raise ValueError(f"Tipo de alvo inválido: {value!r}.")


def _select_override(
    key: tuple[str, str],
    candidates: list[ManualOverride],
) -> ManualOverride:
    exclusions = [item for item in candidates if item.action == "force_exclude"]
    if exclusions:
        if len(exclusions) > 1:
            raise ValueError(
                f"Overrides de exclusão duplicados para {key[0]} '{key[1]}'."
            )
        return exclusions[0]

    if len(candidates) > 1:
        raise ValueError(
            f"Overrides manuais conflitantes para {key[0]} '{key[1]}'."
        )
    return candidates[0]
