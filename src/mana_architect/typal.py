"""Creature-type evidence for collection suggestions."""

from dataclasses import dataclass
import re
from collections.abc import Iterable

from mana_architect.synergy_capabilities import MechanicalCapability


_ENTERING_CREATURE = re.compile(
    r"\bwhen(?:ever)?\s+(?:[^,:]+ or )?(?:one or more |another |other |a |an |each )?"
    r"(?P<group>[a-z][a-z' -]+?) (?:enters|enter)\b[^,]*,",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TypalMatch:
    creature_type: str
    affinity: float
    reasons: tuple[str, ...]
    is_creature_body: bool


def creature_subtypes(type_line: str) -> tuple[str, ...]:
    main_types, _, subtypes = type_line.split(" // ", 1)[0].partition(" — ")
    if "creature" not in main_types.casefold().split():
        return ()
    return tuple(subtypes.split())


def match_typal(capabilities: Iterable[MechanicalCapability], creature_type: str) -> TypalMatch:
    reasons: dict[str, float] = {}
    body = False
    names = {creature_type, f"{creature_type}s"}
    for capability in capabilities:
        if capability.quantity.value == 0:
            continue
        if capability.id == "creature_presence":
            if creature_type in capability.object.subtypes:
                reasons["Creature type"] = 1.0
                body = True
            elif capability.resolution == "changeling":
                reasons["Changeling"] = 1.0
                body = True
        if capability.id == "enter_payoff" and capability.controller != "opponent":
            if match := _ENTERING_CREATURE.search(capability.evidence.oracle_span):
                group = match["group"].casefold().removesuffix(" creature").removesuffix(" creatures")
                if group in names and "an opponent" not in match[0].casefold():
                    reasons["Creature-type payoff"] = 0.85
        if creature_type not in capability.object.subtypes:
            continue
        if capability.id == "create_token" and "creature" in capability.object.types:
            reasons["Creates creature tokens"] = 0.75
        elif capability.id in {"tribal_buff", "combat_damage_payoff", "typal_entry_counters"}:
            reasons["Creature-type payoff"] = 0.85
    return TypalMatch(creature_type, max(reasons.values(), default=0.0), tuple(reasons), body)
