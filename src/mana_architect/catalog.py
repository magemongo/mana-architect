from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Iterable, Mapping


DEFAULT_CATALOG_DIR = Path(__file__).resolve().parents[2] / "config"
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_RULE_TARGET_TYPES = {"strategy", "tag", "role"}
_RULE_SOURCE_FIELDS = {
    "cmc",
    "color_identity",
    "colors",
    "keywords",
    "mana_cost",
    "oracle_text_normalized",
    "produced_mana",
    "type_line",
    "type_line_normalized",
}
_PREDICATE_TYPES = {
    "phrase_any",
    "phrase_all",
    "type_line_contains",
    "keyword_contains",
    "numeric_threshold",
    "field_equals",
}
_SCORING_COMPONENTS = {
    "strategy_evidence",
    "shared_synergy_tags",
    "role_coverage",
    "commander_text_support",
}
_COMMANDER_LEVELS = {"primary", "secondary", "complementary"}
_THRESHOLD_LEVELS = {"strong", "moderate", "weak"}


@dataclass(frozen=True)
class StrategyTagAffinity:
    tag_id: str
    weight: float


@dataclass(frozen=True)
class StrategyDefinition:
    id: str
    name: str
    description: str
    nature: tuple[str, ...]
    active: bool
    tag_affinities: tuple[StrategyTagAffinity, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class SynergyTag:
    id: str
    name: str
    description: str
    family: str
    related_strategy_ids: tuple[str, ...]
    aliases: tuple[str, ...]
    manual_review_recommended: bool


@dataclass(frozen=True)
class CardRole:
    id: str
    name: str
    group: str
    description: str
    oracle_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchPredicate:
    type: str
    values: tuple[str, ...] = ()
    field: str | None = None
    operator: str | None = None
    value: str | int | float | bool | None = None


@dataclass(frozen=True)
class ClassificationRule:
    id: str
    enabled: bool
    target_type: str
    target_id: str
    source_fields: tuple[str, ...]
    match: MatchPredicate
    weight: float
    confidence: float
    evidence_label: str
    requires_review: bool


@dataclass(frozen=True)
class ScoreNormalization:
    minimum: float
    maximum: float
    round_digits: int


@dataclass(frozen=True)
class ScoringConfig:
    normalization: ScoreNormalization
    commander_levels: Mapping[str, float]
    components: Mapping[str, float]
    thresholds: Mapping[str, float]
    caps: Mapping[str, float]
    user_priority_adjustment: Mapping[str, float]


@dataclass(frozen=True)
class StrategyCatalog:
    catalog_version: str
    strategies: tuple[StrategyDefinition, ...]
    tags: tuple[SynergyTag, ...]
    roles: tuple[CardRole, ...]
    ruleset_version: str
    rules: tuple[ClassificationRule, ...]
    scoring_version: str
    scoring: ScoringConfig


def load_strategy_catalog(config_dir: Path | None = None) -> StrategyCatalog:
    """Load and cross-validate the versioned domain configuration."""
    directory = config_dir or DEFAULT_CATALOG_DIR
    strategy_data = _read_json(directory, "strategy_catalog.json")
    tag_data = _read_json(directory, "synergy_tags.json")
    role_data = _read_json(directory, "card_roles.json")
    rule_data = _read_json(directory, "classification_rules.json")
    scoring_data = _read_json(directory, "scoring.json")

    catalog_version = _version(strategy_data, "strategy_catalog.json", "catalog_version")
    strategies = _parse_strategies(strategy_data)
    tags = _parse_tags(tag_data)
    roles = _parse_roles(role_data)
    ruleset_version = _version(rule_data, "classification_rules.json", "ruleset_version")
    rules = _parse_rules(rule_data)
    scoring_version = _version(scoring_data, "scoring.json", "scoring_version")
    scoring = _parse_scoring(scoring_data)
    _validate_references(strategies, tags, roles, rules)
    _validate_scoring(scoring, "scoring.json")

    return StrategyCatalog(
        catalog_version=catalog_version,
        strategies=strategies,
        tags=tags,
        roles=roles,
        ruleset_version=ruleset_version,
        rules=rules,
        scoring_version=scoring_version,
        scoring=scoring,
    )


def _read_json(directory: Path, filename: str) -> dict[str, object]:
    path = directory / filename
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
    except OSError as error:
        raise ValueError(f"{filename}: could not read file: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{filename}: invalid JSON at line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error
    if not isinstance(data, dict):
        raise _error(filename, "$", "esperado um objeto JSON")
    _schema_version(data, filename)
    return data


def _schema_version(data: dict[str, object], filename: str) -> None:
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise _error(filename, "schema_version", "esperado o inteiro 1")


def _version(data: dict[str, object], filename: str, field: str) -> str:
    return _string(data, filename, field, field)


def _parse_strategies(data: dict[str, object]) -> tuple[StrategyDefinition, ...]:
    filename = "strategy_catalog.json"
    records = _records(data, filename, "strategies")
    strategies: list[StrategyDefinition] = []
    for index, record in enumerate(records):
        path = f"strategies[{index}]"
        affinities: list[StrategyTagAffinity] = []
        for affinity_index, raw_affinity in enumerate(
            _records(record, filename, f"{path}.tag_affinities")
        ):
            affinity_path = f"{path}.tag_affinities[{affinity_index}]"
            affinities.append(
                StrategyTagAffinity(
                    tag_id=_identifier(raw_affinity, filename, f"{affinity_path}.tag_id"),
                    weight=_number(raw_affinity, filename, f"{affinity_path}.weight", 0.0),
                )
            )
        affinity_ids = [item.tag_id for item in affinities]
        if len(affinity_ids) != len(set(affinity_ids)):
            raise _error(filename, f"{path}.tag_affinities", "tag_ids duplicados")
        nature = _string_tuple(record, filename, f"{path}.nature")
        if not nature:
            raise _error(filename, f"{path}.nature", "the list cannot be empty")
        strategies.append(
            StrategyDefinition(
                id=_identifier(record, filename, f"{path}.id"),
                name=_string(record, filename, f"{path}.name", path),
                description=_string(record, filename, f"{path}.description", path),
                nature=nature,
                active=_boolean(record, filename, f"{path}.active"),
                tag_affinities=tuple(affinities),
                notes=_string_tuple(record, filename, f"{path}.notes"),
            )
        )
    _unique_ids((item.id for item in strategies), filename, "strategies")
    if not strategies:
        raise _error(filename, "strategies", "the catalog must contain at least one strategy")
    return tuple(strategies)


def _parse_tags(data: dict[str, object]) -> tuple[SynergyTag, ...]:
    filename = "synergy_tags.json"
    records = _records(data, filename, "tags")
    tags: list[SynergyTag] = []
    for index, record in enumerate(records):
        path = f"tags[{index}]"
        tags.append(
            SynergyTag(
                id=_identifier(record, filename, f"{path}.id"),
                name=_string(record, filename, f"{path}.name", path),
                description=_string(record, filename, f"{path}.description", path),
                family=_identifier(record, filename, f"{path}.family"),
                related_strategy_ids=_identifier_tuple(
                    record, filename, f"{path}.related_strategy_ids"
                ),
                aliases=_string_tuple(record, filename, f"{path}.aliases"),
                manual_review_recommended=_boolean(
                    record, filename, f"{path}.manual_review_recommended"
                ),
            )
        )
    _unique_ids((item.id for item in tags), filename, "tags")
    return tuple(tags)


def _parse_roles(data: dict[str, object]) -> tuple[CardRole, ...]:
    filename = "card_roles.json"
    records = _records(data, filename, "roles")
    roles = tuple(
        CardRole(
            id=_identifier(record, filename, f"roles[{index}].id"),
            name=_string(record, filename, f"roles[{index}].name", f"roles[{index}]"),
            group=_identifier(record, filename, f"roles[{index}].group"),
            description=_string(
                record, filename, f"roles[{index}].description", f"roles[{index}]"
            ),
            oracle_keywords=_optional_string_tuple(
                record, filename, f"roles[{index}].oracle_keywords"
            ),
        )
        for index, record in enumerate(records)
    )
    _unique_ids((item.id for item in roles), filename, "roles")
    if not roles:
        raise _error(filename, "roles", "the catalog must contain at least one role")
    return roles


def _parse_rules(data: dict[str, object]) -> tuple[ClassificationRule, ...]:
    filename = "classification_rules.json"
    records = _records(data, filename, "rules")
    rules: list[ClassificationRule] = []
    for index, record in enumerate(records):
        path = f"rules[{index}]"
        target_type = _string(record, filename, f"{path}.target_type", path)
        if target_type not in _RULE_TARGET_TYPES:
            raise _error(
                filename,
                f"{path}.target_type",
                f"esperado um de {', '.join(sorted(_RULE_TARGET_TYPES))}",
            )
        source_fields = _string_tuple(record, filename, f"{path}.source_fields")
        if not source_fields:
            raise _error(filename, f"{path}.source_fields", "the list cannot be empty")
        if len(set(source_fields)) != len(source_fields):
            raise _error(filename, f"{path}.source_fields", "campos duplicados")
        unknown_fields = set(source_fields) - _RULE_SOURCE_FIELDS
        if unknown_fields:
            raise _error(
                filename,
                f"{path}.source_fields",
                f"unsupported fields: {', '.join(sorted(unknown_fields))}",
            )
        rules.append(
            ClassificationRule(
                id=_identifier(record, filename, f"{path}.id"),
                enabled=_boolean(record, filename, f"{path}.enabled"),
                target_type=target_type,
                target_id=_identifier(record, filename, f"{path}.target_id"),
                source_fields=source_fields,
                match=_parse_predicate(record, filename, f"{path}.match", source_fields),
                weight=_number(record, filename, f"{path}.weight", 0.0),
                confidence=_number(record, filename, f"{path}.confidence", 0.0, 1.0),
                evidence_label=_string(record, filename, f"{path}.evidence_label", path),
                requires_review=_boolean(record, filename, f"{path}.requires_review"),
            )
        )
    _unique_ids((item.id for item in rules), filename, "rules")
    return tuple(rules)


def _parse_predicate(
    record: dict[str, object],
    filename: str,
    path: str,
    source_fields: tuple[str, ...],
) -> MatchPredicate:
    raw = _object(record, filename, path)
    predicate_type = _string(raw, filename, f"{path}.type", path)
    if predicate_type not in _PREDICATE_TYPES:
        raise _error(
            filename,
            f"{path}.type",
            f"unsupported predicate: {predicate_type}",
        )
    if predicate_type in {
        "phrase_any",
        "phrase_all",
        "type_line_contains",
        "keyword_contains",
    }:
        values = _string_tuple(raw, filename, f"{path}.values")
        if not values:
            raise _error(filename, f"{path}.values", "the list cannot be empty")
        required_field = {
            "type_line_contains": {"type_line", "type_line_normalized"},
            "keyword_contains": {"keywords"},
        }.get(predicate_type)
        if required_field is not None and not required_field.intersection(source_fields):
            raise _error(
                filename,
                path,
                f"o predicado {predicate_type} exige source_fields com "
                f"{' ou '.join(sorted(required_field))}",
            )
        return MatchPredicate(type=predicate_type, values=values)

    field = _identifier(raw, filename, f"{path}.field")
    if field not in source_fields:
        raise _error(filename, f"{path}.field", "o campo deve constar em source_fields")
    value = raw.get("value")
    if predicate_type == "numeric_threshold":
        if field != "cmc":
            raise _error(filename, f"{path}.field", "numeric_threshold aceita apenas cmc")
        operator = _string(raw, filename, f"{path}.operator", path)
        if operator not in {"gt", "gte", "lt", "lte", "eq"}:
            raise _error(filename, f"{path}.operator", "invalid numeric operator")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _error(filename, f"{path}.value", "expected a finite number")
        if not math.isfinite(value):
            raise _error(filename, f"{path}.value", "expected a finite number")
        return MatchPredicate(
            type=predicate_type,
            field=field,
            operator=operator,
            value=value,
        )
    if value is None or not isinstance(value, (str, int, float, bool)):
        raise _error(filename, f"{path}.value", "expected a non-null scalar value")
    if isinstance(value, float) and not math.isfinite(value):
        raise _error(filename, f"{path}.value", "expected a finite number")
    return MatchPredicate(type=predicate_type, field=field, value=value)


def _parse_scoring(data: dict[str, object]) -> ScoringConfig:
    filename = "scoring.json"
    normalization = _object(data, filename, "normalization")
    minimum = _number(normalization, filename, "normalization.minimum")
    maximum = _number(normalization, filename, "normalization.maximum")
    digits = normalization.get("round_digits")
    if isinstance(digits, bool) or not isinstance(digits, int) or digits < 0:
        raise _error(filename, "normalization.round_digits", "expected a non-negative integer")
    commander_levels = _number_map(data, filename, "commander_levels")
    components = _number_map(data, filename, "components")
    thresholds = _number_map(data, filename, "thresholds")
    caps = _number_map(data, filename, "caps")
    user_priority_adjustment = _number_map(
        data,
        filename,
        "user_priority_adjustment",
    )
    if set(commander_levels) != _COMMANDER_LEVELS:
        raise _error(
            filename,
            "commander_levels",
            f"chaves esperadas: {', '.join(sorted(_COMMANDER_LEVELS))}",
        )
    if set(components) != _SCORING_COMPONENTS:
        raise _error(
            filename,
            "components",
            f"chaves esperadas: {', '.join(sorted(_SCORING_COMPONENTS))}",
        )
    if set(thresholds) != _THRESHOLD_LEVELS:
        raise _error(
            filename,
            "thresholds",
            f"chaves esperadas: {', '.join(sorted(_THRESHOLD_LEVELS))}",
        )
    return ScoringConfig(
        normalization=ScoreNormalization(minimum, maximum, digits),
        commander_levels=MappingProxyType(commander_levels),
        components=MappingProxyType(components),
        thresholds=MappingProxyType(thresholds),
        caps=MappingProxyType(caps),
        user_priority_adjustment=MappingProxyType(user_priority_adjustment),
    )


def _validate_references(
    strategies: tuple[StrategyDefinition, ...],
    tags: tuple[SynergyTag, ...],
    roles: tuple[CardRole, ...],
    rules: tuple[ClassificationRule, ...],
) -> None:
    strategy_ids = {item.id for item in strategies}
    tag_ids = {item.id for item in tags}
    role_ids = {item.id for item in roles}
    for strategy in strategies:
        for affinity in strategy.tag_affinities:
            if affinity.tag_id not in tag_ids:
                raise _error(
                    "strategy_catalog.json",
                    f"strategies[{strategy.id}].tag_affinities",
                    f"tag_id inexistente: {affinity.tag_id}",
                )
    for tag in tags:
        unknown = set(tag.related_strategy_ids) - strategy_ids
        if unknown:
            raise _error(
                "synergy_tags.json",
                f"tags[{tag.id}].related_strategy_ids",
                f"strategy_ids inexistentes: {', '.join(sorted(unknown))}",
            )
    targets = {"strategy": strategy_ids, "tag": tag_ids, "role": role_ids}
    for rule in rules:
        if rule.target_id not in targets[rule.target_type]:
            raise _error(
                "classification_rules.json",
                f"rules[{rule.id}].target_id",
                f"{rule.target_type} inexistente: {rule.target_id}",
            )


def _validate_scoring(scoring: ScoringConfig, filename: str) -> None:
    normalization = scoring.normalization
    if normalization.maximum <= normalization.minimum:
        raise _error(filename, "normalization", "maximum must be greater than minimum")
    if any(
        not normalization.minimum <= value <= normalization.maximum
        for value in scoring.thresholds.values()
    ):
        raise _error(
            filename,
            "thresholds",
            "limiares devem estar dentro da faixa normalizada",
        )
    if not (
        scoring.thresholds["strong"]
        > scoring.thresholds["moderate"]
        > scoring.thresholds["weak"]
    ):
        raise _error(filename, "thresholds", "exija strong > moderate > weak")
    for name, value in scoring.commander_levels.items():
        if not 0 <= value <= 1:
            raise _error(filename, f"commander_levels.{name}", "valor deve estar entre 0 e 1")
    if not (
        scoring.commander_levels["primary"]
        >= scoring.commander_levels["secondary"]
        >= scoring.commander_levels["complementary"]
    ):
        raise _error(filename, "commander_levels", "levels must be non-increasing")
    if any(value < 0 for value in scoring.components.values()):
        raise _error(filename, "components", "weights cannot be negative")
    if not math.isclose(sum(scoring.components.values()), 1.0, abs_tol=1e-9):
        raise _error(filename, "components", "os pesos ativos devem somar 1.0")
    if any(
        not normalization.minimum <= value <= normalization.maximum
        for value in scoring.caps.values()
    ):
        raise _error(filename, "caps", "limites devem estar dentro da faixa normalizada")
    if set(scoring.user_priority_adjustment) != {"minimum", "maximum"}:
        raise _error(
            filename,
            "user_priority_adjustment",
            "chaves esperadas: maximum, minimum",
        )
    if (
        scoring.user_priority_adjustment["minimum"] > 0
        or scoring.user_priority_adjustment["maximum"] < 0
        or scoring.user_priority_adjustment["minimum"]
        > scoring.user_priority_adjustment["maximum"]
    ):
        raise _error(
            filename,
            "user_priority_adjustment",
            "range must be ordered and include zero",
        )


def _number_map(
    data: dict[str, object],
    filename: str,
    path: str,
) -> dict[str, float]:
    raw = _object(data, filename, path)
    if not raw:
        raise _error(filename, path, "the object cannot be empty")
    result: dict[str, float] = {}
    for key, value in raw.items():
        if (
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise _error(filename, f"{path}.{key}", "expected a finite number")
        result[key] = float(value)
    return result


def _records(data: dict[str, object], filename: str, path: str) -> list[dict[str, object]]:
    field = path.rpartition(".")[2]
    raw = data.get(field)
    if not isinstance(raw, list):
        raise _error(filename, path, "esperada uma lista")
    if not all(isinstance(item, dict) for item in raw):
        raise _error(filename, path, "todos os itens devem ser objetos")
    return raw


def _object(
    data: dict[str, object],
    filename: str,
    path: str,
) -> dict[str, object]:
    raw = data.get(path.rpartition(".")[2])
    if not isinstance(raw, dict):
        raise _error(filename, path, "esperado um objeto")
    return raw


def _string(
    data: dict[str, object],
    filename: str,
    field: str,
    path: str,
) -> str:
    value = data.get(field.rpartition(".")[2])
    if not isinstance(value, str) or not value.strip():
        raise _error(filename, field, f"{path}: expected non-empty text")
    return value


def _identifier(data: dict[str, object], filename: str, field: str) -> str:
    value = _string(data, filename, field, field.rpartition(".")[0] or field)
    if not _ID_PATTERN.fullmatch(value):
        raise _error(filename, field, "ID deve usar snake_case ASCII")
    return value


def _string_tuple(data: dict[str, object], filename: str, field: str) -> tuple[str, ...]:
    raw = data.get(field.rpartition(".")[2])
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and item.strip() for item in raw
    ):
        raise _error(filename, field, "expected a list of non-empty strings")
    return tuple(raw)


def _optional_string_tuple(
    data: dict[str, object], filename: str, field: str
) -> tuple[str, ...]:
    value = data.get(field.rpartition(".")[2], [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise _error(filename, field, "expected a list of non-empty strings")
    if len(value) != len(set(value)):
        raise _error(filename, field, "termos duplicados")
    return tuple(value)


def _identifier_tuple(
    data: dict[str, object],
    filename: str,
    field: str,
) -> tuple[str, ...]:
    values = _string_tuple(data, filename, field)
    invalid = [item for item in values if not _ID_PATTERN.fullmatch(item)]
    if invalid:
        raise _error(filename, field, f"IDs devem usar snake_case ASCII: {', '.join(invalid)}")
    if len(set(values)) != len(values):
        raise _error(filename, field, "IDs duplicados")
    return values


def _boolean(data: dict[str, object], filename: str, field: str) -> bool:
    value = data.get(field.rpartition(".")[2])
    if not isinstance(value, bool):
        raise _error(filename, field, "esperado booleano")
    return value


def _number(
    data: dict[str, object],
    filename: str,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = data.get(field.rpartition(".")[2])
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise _error(filename, field, "expected a finite number")
    if minimum is not None and value < minimum:
        raise _error(filename, field, f"valor deve ser >= {minimum}")
    if maximum is not None and value > maximum:
        raise _error(filename, field, f"valor deve ser <= {maximum}")
    return float(value)


def _unique_ids(values: Iterable[str], filename: str, field: str) -> None:
    ids = tuple(values)
    if len(ids) != len(set(ids)):
        raise _error(filename, field, "IDs duplicados")


def _error(filename: str, field: str, message: str) -> ValueError:
    return ValueError(f"{filename}: {field}: {message}")
