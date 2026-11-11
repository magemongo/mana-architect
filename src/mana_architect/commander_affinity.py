from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Literal

from mana_architect.catalog import StrategyCatalog


CommanderAffinityLevel = Literal["primary", "secondary", "complementary"]
AffinitySource = Literal["manual", "automatic"]
_LEVELS = {"primary", "secondary", "complementary"}
_SOURCES = {"manual", "automatic"}
_EVIDENCE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class CommanderStrategyAffinity:
    strategy_id: str
    level: CommanderAffinityLevel
    strength_score: float
    evidence_ids: tuple[str, ...] = ()
    rationale: str | None = None
    source: AffinitySource = "manual"


@dataclass(frozen=True)
class CommanderTagAffinity:
    tag_id: str
    evidence_ids: tuple[str, ...] = ()
    rationale: str | None = None
    source: AffinitySource = "manual"


@dataclass(frozen=True)
class CommanderAffinityProfile:
    commander_oracle_id: str
    strategy_affinities: tuple[CommanderStrategyAffinity, ...] = ()
    tag_affinities: tuple[CommanderTagAffinity, ...] = ()


def validate_commander_affinity_profile(
    profile: CommanderAffinityProfile,
    catalog: StrategyCatalog,
) -> CommanderAffinityProfile:
    """Validate a curated commander profile against the loaded catalog."""
    if (
        not isinstance(profile.commander_oracle_id, str)
        or not profile.commander_oracle_id.strip()
    ):
        raise ValueError("commander_oracle_id deve ser um texto não vazio.")

    strategy_ids = {item.id for item in catalog.strategies}
    tag_ids = {item.id for item in catalog.tags}
    seen_strategies: set[str] = set()
    for affinity in profile.strategy_affinities:
        if not isinstance(affinity.strategy_id, str) or affinity.strategy_id not in strategy_ids:
            raise ValueError(
                f"Strategy inexistente no catálogo: '{affinity.strategy_id}'."
            )
        if affinity.strategy_id in seen_strategies:
            raise ValueError(
                f"Affinity duplicada para strategy '{affinity.strategy_id}'."
            )
        seen_strategies.add(affinity.strategy_id)
        if not isinstance(affinity.level, str) or affinity.level not in _LEVELS:
            raise ValueError(
                f"Nível inválido para strategy '{affinity.strategy_id}': "
                f"{affinity.level!r}."
            )
        if (
            isinstance(affinity.strength_score, bool)
            or not isinstance(affinity.strength_score, (int, float))
            or not math.isfinite(affinity.strength_score)
            or not 0 <= affinity.strength_score <= 1
        ):
            raise ValueError(
                f"strength_score de '{affinity.strategy_id}' deve estar entre 0 e 1."
            )
        _validate_evidence_ids(
            affinity.evidence_ids,
            f"strategy '{affinity.strategy_id}'",
        )
        _validate_text(affinity.rationale, "rationale")
        _validate_source(affinity.source)

    seen_tags: set[str] = set()
    for affinity in profile.tag_affinities:
        if not isinstance(affinity.tag_id, str) or affinity.tag_id not in tag_ids:
            raise ValueError(f"Tag inexistente no catálogo: '{affinity.tag_id}'.")
        if affinity.tag_id in seen_tags:
            raise ValueError(f"Affinity duplicada para tag '{affinity.tag_id}'.")
        seen_tags.add(affinity.tag_id)
        _validate_evidence_ids(
            affinity.evidence_ids,
            f"tag '{affinity.tag_id}'",
        )
        _validate_text(affinity.rationale, "rationale")
        _validate_source(affinity.source)

    return profile


def _validate_evidence_ids(
    values: tuple[str, ...],
    target: str,
) -> None:
    if not all(isinstance(value, str) for value in values):
        raise ValueError(f"IDs de evidência devem ser texto para {target}.")
    if len(set(values)) != len(values):
        raise ValueError(f"IDs de evidência duplicados para {target}.")
    invalid = [value for value in values if not _EVIDENCE_ID_PATTERN.fullmatch(value)]
    if invalid:
        raise ValueError(
            f"IDs de evidência inválidos para {target}: {', '.join(sorted(invalid))}."
        )


def _validate_text(value: str | None, field: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} deve ser texto ou None.")


def _validate_source(source: str) -> None:
    if not isinstance(source, str) or source not in _SOURCES:
        raise ValueError(f"source deve ser manual ou automatic, recebido {source!r}.")
