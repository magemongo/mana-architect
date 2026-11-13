"""Extract source-linked mana resource events from a parsed Oracle document."""

from dataclasses import dataclass
import re
from typing import Literal

from mana_architect import ramp_rules_catalog as rules
from mana_architect.oracle_text import OracleParse

Timing = Literal["effect", "activated", "triggered", "replacement"]


@dataclass(frozen=True)
class ResourceQuantity:
    kind: Literal["fixed", "up_to", "variable"]
    value: int | None
    expression: str | None = None


@dataclass(frozen=True)
class ResourceEvidence:
    ability_id: str
    start: int
    end: int
    oracle_span: str


@dataclass(frozen=True)
class ManaResourceEvent:
    action: Literal["add_mana", "put_land", "permit_land_play", "create_token"]
    object: str
    quantity: ResourceQuantity
    evidence: ResourceEvidence
    timing: Timing
    activation_cost: str | None = None
    activation_symbols: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()
    optional: bool = False
    source_zone: str | None = None
    enters_tapped_by_effect: bool | None = None
    color_options: tuple[str, ...] = ()
    mana_symbols: tuple[str, ...] = ()
    color_rule: str | None = None
    token_activation_cost: str | None = None
    mana_per_token: int | None = None
    duration: str | None = None


_REMINDER_OR_QUOTE = re.compile(r'\([^()]*\)|"[^"]*"|“[^”]*”')
_TRIGGER = re.compile(r"^(?:[^,.;:]+ — )?(?:when|whenever|at)\b", re.IGNORECASE)
_REPLACEMENT = re.compile(r"^(?:if|as)\b.*\binstead\b", re.IGNORECASE)
_PREREQUISITE = re.compile(
    r"\b(?:if|unless|as long as|only if|for each|depending on|opponent chooses)\b", re.IGNORECASE
)
_VARIABLE_MANA = re.compile(r"\b(?:for each|equal to|that much|that many)\b", re.IGNORECASE)
_NON_ACTION = re.compile(
    r"\b(?:can't|cannot|can not|doesn't|does not|don't|do not|never|would|"
    r"opponents?|that player|target player|each player|they)\b[^.;,]*$",
    re.IGNORECASE,
)
_SPENDING_RESTRICTION = re.compile(r"\b(?:spend|this mana)\b", re.IGNORECASE)


def _quantity(count: str, up_to: str | None = None) -> ResourceQuantity:
    count = count.casefold()
    value = int(count) if count.isdecimal() else rules.NUMBER_WORDS.get(count)
    if value is None:
        return ResourceQuantity("variable", None, count)
    return ResourceQuantity("up_to" if up_to else "fixed", value)


def _mask_reminder_and_quotes(text: str) -> str:
    # Repeated passes remove nested parentheticals without disturbing evidence.
    while _REMINDER_OR_QUOTE.search(text):
        text = _REMINDER_OR_QUOTE.sub(lambda match: " " * len(match[0]), text)
    return text


def extract_mana_resources(parsed: OracleParse) -> tuple[ManaResourceEvent, ...]:
    """Compile supported effects in source order, retaining Oracle offsets."""
    events = []
    for ability in parsed.abilities:
        # The parser groups paragraphs. Scryfall also separates abilities with
        # single newlines, so refine the span without changing the parser ID.
        for line in re.finditer(r"[^\r\n]+", ability.text):
            start = ability.start + line.start()
            end = ability.start + line.end()
            evidence = ResourceEvidence(
                ability.id, start, end, parsed.oracle_text[start:end],
            )
            events.extend(_extract_line(evidence))
    return tuple(events)


def _extract_line(evidence: ResourceEvidence) -> list[ManaResourceEvent]:
    text = _mask_reminder_and_quotes(evidence.oracle_span).strip()
    activation_cost = None
    trigger = None
    timing: Timing
    if _TRIGGER.match(text):
        timing = "triggered"
        trigger, _, effect = text.partition(",")
    elif _REPLACEMENT.match(text):
        timing = "replacement"
        trigger, _, effect = text.partition(",")
    elif ":" in text:
        timing = "activated"
        activation_cost, _, effect = text.partition(":")
    else:
        timing = "effect"
        effect = text

    requirements = [trigger] if trigger else []
    if _PREREQUISITE.search(effect):
        requirements.append(effect.strip())
    restrictions = tuple(
        sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", effect.strip())
        if _SPENDING_RESTRICTION.search(sentence)
    )
    events = []
    for pattern in (
        rules.MANA_PRODUCTION, rules.LAND_SEARCH, rules.LAND_FROM_HAND,
        rules.EXTRA_LAND_PLAY, rules.MANA_TOKEN_CREATION,
    ):
        for match in pattern.finditer(effect):
            prefix = effect[:match.start()]
            if _NON_ACTION.search(prefix):
                continue
            event = _make_event(
                pattern, match, effect, evidence, timing, activation_cost,
                tuple(requirements), restrictions,
            )
            events.append((match.start(), event))
    return [event for _, event in sorted(events, key=lambda pair: pair[0])]


def _make_event(
    pattern: re.Pattern[str],
    match: re.Match[str],
    effect: str,
    evidence: ResourceEvidence,
    timing: Timing,
    activation_cost: str | None,
    requirements: tuple[str, ...],
    restrictions: tuple[str, ...],
) -> ManaResourceEvent:
    groups = match.groupdict()
    optional = bool(groups.get("up_to") or re.search(r"\bmay\b", effect, re.IGNORECASE))
    details = {
        "evidence": evidence,
        "timing": timing,
        "activation_cost": activation_cost,
        "activation_symbols": tuple(re.findall(rules.MANA_SYMBOL, activation_cost or "")),
        "requirements": requirements,
        "restrictions": restrictions,
        "optional": optional,
    }
    if pattern is rules.MANA_PRODUCTION:
        symbols = tuple(re.findall(rules.MANA_SYMBOL, groups["symbols"] or ""))
        quantity = _quantity(groups["count"] or str(len(symbols)))
        clause_end = effect.find(".", match.end())
        clause = effect[match.start():clause_end if clause_end != -1 else len(effect)]
        if _VARIABLE_MANA.search(clause):
            quantity = ResourceQuantity("variable", None, clause.strip())
        color_rule = groups["color_rule"]
        colors = tuple(dict.fromkeys(symbols))
        if groups["alternative"]:
            alternatives = re.findall(r"\bor ((?:\{[WUBRGC]\})+)", groups["alternative"], re.IGNORECASE)
            choices = [tuple(re.findall(rules.MANA_SYMBOL, option)) for option in alternatives]
            if any(len(choice) != len(symbols) for choice in choices):
                quantity = ResourceQuantity("variable", None, clause.strip())
            colors = tuple(dict.fromkeys(symbol for choice in [symbols, *choices] for symbol in choice))
            color_rule = (groups["symbols"] or "") + groups["alternative"]
            symbols = ()
        elif color_rule and re.search(r"\bany (?:combination of )?colors?\b", color_rule, re.IGNORECASE):
            colors = ("any",)
        return ManaResourceEvent(
            "add_mana", "mana", quantity, **details, color_options=colors,
            mana_symbols=symbols, color_rule=color_rule,
        )

    quantity = _quantity(groups["count"], groups.get("up_to"))
    if pattern is rules.EXTRA_LAND_PLAY:
        details["requirements"] += ("land_available_to_play",)
        return ManaResourceEvent(
            "permit_land_play", "land", quantity, **details,
            duration=groups["duration"].strip() if groups["duration"] else None,
        )
    tapped = (groups["tapped"] or "").strip().casefold() == "tapped"
    if pattern is rules.MANA_TOKEN_CREATION:
        token = groups["token"].casefold()
        colors, cost, token_restrictions = rules.MANA_TOKENS[token]
        details["restrictions"] += token_restrictions
        return ManaResourceEvent(
            "create_token", token, quantity, **details, enters_tapped_by_effect=tapped,
            color_options=colors, token_activation_cost=cost, mana_per_token=1,
        )

    source = "library" if pattern is rules.LAND_SEARCH else "hand"
    if source == "hand":
        details["requirements"] += ("land_available_in_hand",)
    else:
        details["requirements"] += ("matching_land_in_library",)
        if groups["moved"].casefold() in {"one of them", "one"}:
            quantity = ResourceQuantity("up_to" if optional else "fixed", 1)
    return ManaResourceEvent(
        "put_land", groups["filter"], quantity, **details,
        source_zone=source, enters_tapped_by_effect=tapped,
    )
