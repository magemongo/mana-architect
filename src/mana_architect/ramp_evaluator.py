"""Evaluate ramp for candidate selection using card-local information."""

from dataclasses import dataclass
import re
from typing import Literal

from mana_architect.mana_resource_extractor import ManaResourceEvent, extract_mana_resources
from mana_architect.oracle_text import OracleParse
from mana_architect.ramp_rules_catalog import MANA_SYMBOL, RAMP_RULES_VERSION

RampMechanism = Literal[
    "land development", "reusable mana", "mana tokens", "mana burst", "triggered mana",
]

_CAST_SYMBOLS = re.compile(r"(?:\{[^{}]+\})+")
_ADDITIONAL_PAYMENT = re.compile(r"\b(?:additional cost|kicker|entwine|escalate)\b", re.IGNORECASE)
_SIMPLE_BURST = re.compile(r"add (?:\{[WUBRGC]\})+\.", re.IGNORECASE)
_SACRIFICE = re.compile(r"\bsacrifice\b", re.IGNORECASE)
_CONSUMES_RESOURCE = re.compile(r"\b(?:sacrifice|discard|exile|return|remove)\b", re.IGNORECASE)
_ENTERS_TAPPED = re.compile(r"\benters(?: the battlefield)? tapped\b", re.IGNORECASE)
_SCORE_WEIGHTS = {
    "acceleration": 0.40,
    "persistence": 0.25,
    "speed": 0.20,
    "reliability": 0.15,
}


@dataclass(frozen=True)
class RampLine:
    mechanism: RampMechanism
    event: ManaResourceEvent
    score: float
    conditional: bool
    net_immediate_mana: int | None = None


@dataclass(frozen=True)
class RampProfile:
    oracle_id: str
    oracle_parser_version: str
    lines: tuple[RampLine, ...]
    ramp_rules_version: str = RAMP_RULES_VERSION

    @property
    def best_line(self) -> RampLine | None:
        return max(self.lines, key=lambda line: line.score, default=None)


def _fixed_mana_payment(symbols: tuple[str, ...]) -> int | None:
    total = 0
    for symbol in symbols:
        if symbol in {"T", "Q"}:
            continue
        if symbol.isascii() and symbol.isdecimal():
            total += int(symbol)
        elif all(part in {"W", "U", "B", "R", "G", "C", "S"} for part in symbol.split("/")):
            total += 1
        else:
            return None
    return total


def evaluate_ramp(
    oracle_id: str,
    type_line: str | None,
    mana_cost: str | None,
    parsed: OracleParse,
    *,
    colorless_identity: bool = False,
) -> RampProfile:
    """Score the strongest supported line without summing alternative abilities."""
    if not type_line or "//" in type_line:
        return RampProfile(oracle_id, parsed.parser_version, ())
    types = set(type_line.split("—", 1)[0].split())
    land = "Land" in types
    spell = bool(types & {"Instant", "Sorcery"})
    investment = None
    if land:
        investment = 0
    elif mana_cost and _CAST_SYMBOLS.fullmatch(mana_cost):
        investment = _fixed_mana_payment(tuple(re.findall(MANA_SYMBOL, mana_cost)))

    lines = []
    for event in extract_mana_resources(parsed):
        line = _evaluate_event(
            event, types, spell, land, investment, parsed.oracle_text, colorless_identity,
        )
        if line is not None:
            lines.append(line)
    return RampProfile(oracle_id, parsed.parser_version, tuple(lines))


def _evaluate_event(
    event: ManaResourceEvent,
    types: set[str],
    spell: bool,
    land: bool,
    investment: int | None,
    oracle_text: str,
    colorless_identity: bool,
) -> RampLine | None:
    amount = event.quantity.value
    activation_cost = event.activation_cost or ""
    activation_mana = _fixed_mana_payment(event.activation_symbols)
    resource_cost = bool(_CONSUMES_RESOURCE.search(activation_cost))
    requirements = set(event.requirements) - {"matching_land_in_library"}
    commander_color = "commander’s color identity" in (event.color_rule or "").replace("'", "’").casefold()
    conditional = (
        bool(requirements) or event.optional or event.quantity.kind != "fixed"
        or event.timing in {"triggered", "replacement"} or resource_cost
        or activation_mana is None or investment is None
        or commander_color
        or bool(_ADDITIONAL_PAYMENT.search(oracle_text))
        or "•" in event.evidence.oracle_span
    )
    delayed = event.enters_tapped_by_effect or (
        event.timing == "activated"
        and ("T" in event.activation_symbols and "Creature" in types
             or bool(_ENTERS_TAPPED.search(oracle_text)))
    )
    mechanism: RampMechanism
    net = None
    if amount == 0:
        return None
    if event.action == "add_mana":
        if colorless_identity and commander_color:
            return None
        if land and (amount == 1 or _SACRIFICE.search(activation_cost)):
            return None
        if amount is not None and activation_mana is not None:
            amount -= activation_mana
            if amount <= 0:
                return None
        if spell:
            if amount is not None and investment is not None and amount <= investment:
                return None
            mechanism, persistence = "mana burst", 0.0
            if amount is not None and investment is not None:
                amount -= investment
                if not conditional and _SIMPLE_BURST.fullmatch(oracle_text.strip()):
                    net = amount
        elif event.timing == "activated" and not resource_cost:
            mechanism, persistence = "reusable mana", 1.0
            if land and amount is not None:
                amount -= 1  # A normal land is already a mana source.
                if amount <= 0:
                    return None
        else:
            mechanism, persistence = "triggered mana", 0.4
    elif event.action == "create_token":
        if amount is not None and activation_mana is not None and amount <= activation_mana:
            return None
        mechanism = "mana tokens"
        persistence = 1.0 if event.object == "powerstone" else 0.6
    else:
        if land and _SACRIFICE.search(activation_cost):
            return None
        mechanism, persistence = "land development", 1.0

    acceleration = min(amount / 3, 1.0) if amount is not None else 0.0
    speed = 1 / (1 + (investment if investment is not None else 3) + bool(delayed))
    reliability = 0.5 if conditional else 1.0
    if event.restrictions:
        reliability *= 0.8
    dimensions = {
        "acceleration": acceleration,
        "persistence": persistence,
        "speed": speed,
        "reliability": reliability,
    }
    score = round(100 * sum(_SCORE_WEIGHTS[key] * value for key, value in dimensions.items()), 3)
    return RampLine(mechanism, event, score, conditional, net)
