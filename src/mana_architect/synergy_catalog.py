"""Load the versioned mechanical overlay for the project's strategy catalog."""

from dataclasses import dataclass
from pathlib import Path

from mana_architect.catalog import (
    DEFAULT_CATALOG_DIR,
    _error,
    _identifier,
    _number,
    _object,
    _read_json,
    _records,
    _schema_version,
    _string,
    _string_tuple,
    _unique_ids,
    load_strategy_catalog,
)
from mana_architect.synergy_capabilities import CAPABILITY_IDS

_FILENAME = "strategy_synergy.json"
_ROLES = {"generator", "enabler", "payoff", "amplifier", "converter", "finisher"}
_TYPES = {
    "artifact", "battle", "creature", "enchantment", "instant", "land",
    "planeswalker", "sorcery", "token",
}


@dataclass(frozen=True)
class CapabilityPreference:
    id: str
    capability: str
    role: str
    weight: float
    types: tuple[str, ...] = ()
    subtype: str | None = None
    reference: str | None = None


@dataclass(frozen=True)
class SynergyStrategy:
    id: str
    objective: str
    preferences: tuple[CapabilityPreference, ...]
    interaction_rule_ids: tuple[str, ...]
    exclusions: tuple[str, ...]


@dataclass(frozen=True)
class SynergyStrategyCatalog:
    version: str
    strategies: tuple[SynergyStrategy, ...]
    aliases: tuple[tuple[str, str], ...]


def load_synergy_strategies(
    config_dir: Path | None = None, *, active_strategy_ids: frozenset[str] | None = None,
) -> SynergyStrategyCatalog:
    directory = config_dir or DEFAULT_CATALOG_DIR
    if active_strategy_ids is None:
        active_strategy_ids = frozenset(
            strategy.id for strategy in load_strategy_catalog(directory).strategies if strategy.active
        )
    data = _read_json(directory, _FILENAME)
    _schema_version(data, _FILENAME)
    strategies = tuple(
        _read_strategy(record, f"strategies[{index}]")
        for index, record in enumerate(_records(data, _FILENAME, "strategies"))
    )
    _unique_ids((strategy.id for strategy in strategies), _FILENAME, "strategies")
    _unique_ids(
        (preference.id for strategy in strategies for preference in strategy.preferences),
        _FILENAME, "preferences",
    )
    canonical_ids = {strategy.id for strategy in strategies}
    aliases = _object(data, _FILENAME, "aliases")
    for alias in aliases:
        target = _identifier(aliases, _FILENAME, f"aliases.{alias}")
        if _identifier({alias: alias}, _FILENAME, alias) in canonical_ids or target not in canonical_ids:
            raise _error(_FILENAME, f"aliases.{alias}", "expected an alias of an existing strategy")
    enabled = canonical_ids & active_strategy_ids
    return SynergyStrategyCatalog(
        _string(data, _FILENAME, "version", "version"),
        tuple(sorted((strategy for strategy in strategies if strategy.id in enabled), key=lambda s: s.id)),
        tuple(sorted((alias, target) for alias, target in aliases.items() if target in enabled)),
    )


def _read_strategy(record: dict[str, object], path: str) -> SynergyStrategy:
    preferences = tuple(
        _read_preference(preference, f"{path}.preferences[{index}]")
        for index, preference in enumerate(_records(record, _FILENAME, f"{path}.preferences"))
    )
    if not preferences:
        raise _error(_FILENAME, path, "expected at least one capability preference")
    rules = _string_tuple(record, _FILENAME, f"{path}.interaction_rule_ids")
    exclusions = _string_tuple(record, _FILENAME, f"{path}.exclusions")
    _unique_ids(rules, _FILENAME, f"{path}.interaction_rule_ids")
    _unique_ids(exclusions, _FILENAME, f"{path}.exclusions")
    return SynergyStrategy(
        _identifier(record, _FILENAME, f"{path}.id"),
        _string(record, _FILENAME, f"{path}.objective", path),
        preferences, rules, exclusions,
    )


def _read_preference(record: dict[str, object], path: str) -> CapabilityPreference:
    unknown = record.keys() - {"id", "capability", "role", "weight", "types", "subtype", "reference"}
    if unknown:
        raise _error(_FILENAME, path, f"unknown fields: {', '.join(sorted(unknown))}")
    capability = _identifier(record, _FILENAME, f"{path}.capability")
    role = _identifier(record, _FILENAME, f"{path}.role")
    if capability not in CAPABILITY_IDS or role not in _ROLES:
        raise _error(_FILENAME, path, f"unknown capability or role: {capability}, {role}")
    types = _string_tuple(record, _FILENAME, f"{path}.types") if "types" in record else ()
    if set(types) - _TYPES:
        raise _error(_FILENAME, f"{path}.types", "unknown object type")
    _unique_ids(types, _FILENAME, f"{path}.types")
    subtype = _string(record, _FILENAME, f"{path}.subtype", path) if "subtype" in record else None
    reference = _identifier(record, _FILENAME, f"{path}.reference") if "reference" in record else None
    if reference not in {None, "self", "other", "any"}:
        raise _error(_FILENAME, f"{path}.reference", "unknown object reference")
    return CapabilityPreference(
        _identifier(record, _FILENAME, f"{path}.id"), capability, role,
        _number(record, _FILENAME, f"{path}.weight", 0, 1), types, subtype, reference,
    )
