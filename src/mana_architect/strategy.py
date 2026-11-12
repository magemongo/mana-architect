from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
import tomllib


STRATEGY_PROFILE_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "strategy_profiles.toml"
)


@dataclass(frozen=True)
class StrategySignal:
    name: str
    weight: float
    patterns: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class CommanderStrategyProfile:
    name: str
    themes: tuple[str, ...]
    signals: tuple[StrategySignal, ...]
    utility_land_limit: int


def load_strategy_profile(
    commander_oracle_id: str,
) -> CommanderStrategyProfile | None:
    if not STRATEGY_PROFILE_PATH.is_file():
        return None

    with STRATEGY_PROFILE_PATH.open("rb") as profile_file:
        profiles = tomllib.load(profile_file).get("profiles", {})
    profile = profiles.get(commander_oracle_id)
    if profile is None:
        return None
    if not isinstance(profile, dict):
        raise ValueError(
            f"Invalid strategy profile for commander {commander_oracle_id}."
        )

    name = profile.get("name")
    themes = profile.get("themes")
    utility_land_limit = profile.get("utility_land_limit", 0)
    raw_signals = profile.get("signals", [])
    if (
        not isinstance(name, str)
        or not isinstance(themes, list)
        or not all(isinstance(theme, str) for theme in themes)
        or isinstance(utility_land_limit, bool)
        or not isinstance(utility_land_limit, int)
        or utility_land_limit < 0
        or not isinstance(raw_signals, list)
    ):
        raise ValueError(
            f"Invalid strategy profile for commander {commander_oracle_id}."
        )

    signals: list[StrategySignal] = []
    for raw_signal in raw_signals:
        if not isinstance(raw_signal, dict):
            raise ValueError(f"Invalid strategy signal in profile {name}.")
        signal_name = raw_signal.get("name")
        weight = raw_signal.get("weight")
        patterns = raw_signal.get("patterns")
        if (
            not isinstance(signal_name, str)
            or isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight < 0
            or not isinstance(patterns, list)
            or not patterns
            or not all(isinstance(pattern, str) for pattern in patterns)
        ):
            raise ValueError(f"Invalid strategy signal in profile {name}.")
        try:
            compiled_patterns = tuple(re.compile(pattern) for pattern in patterns)
        except re.error as error:
            raise ValueError(
                f"Invalid expression in signal '{signal_name}' in profile {name}: {error}"
            ) from error
        signals.append(
            StrategySignal(
                name=signal_name,
                weight=float(weight),
                patterns=compiled_patterns,
            )
        )

    return CommanderStrategyProfile(
        name=name,
        themes=tuple(themes),
        signals=tuple(signals),
        utility_land_limit=utility_land_limit,
    )


def match_strategy_signals(
    profile: CommanderStrategyProfile | None,
    type_line: str | None,
    oracle_text: str | None,
) -> tuple[str, ...]:
    if profile is None:
        return ()
    text = f"{type_line or ''}\n{oracle_text or ''}"
    return tuple(
        signal.name
        for signal in profile.signals
        if any(pattern.search(text) for pattern in signal.patterns)
    )


def strategy_signal_score(
    profile: CommanderStrategyProfile | None,
    matched_signals: tuple[str, ...],
) -> float:
    if profile is None:
        return 0.0
    matched = set(matched_signals)
    return sum(
        signal.weight
        for signal in profile.signals
        if signal.name in matched
    )
