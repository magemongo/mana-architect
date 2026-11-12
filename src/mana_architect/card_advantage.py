"""Compile a supported subset of Oracle text into strict card-stock lines."""

from dataclasses import dataclass, replace
import re
from typing import Literal

from mana_architect.oracle_text import OracleAbility, OracleParse

EVALUATOR_VERSION = "card-stock-v2"
Zone = Literal["hand", "stack", "battlefield", "graveyard", "exile", "library", "command"]
_COUNTED_ZONES = frozenset({"hand", "battlefield"})
_DRAW = r"[Dd]raw (?:a|one|two|three|four|five|\d+) cards?\."
_ENTERS = r"When [^,.]+ enters(?: the battlefield)?, [^\n]+"
_SCRY = re.compile(r"Scry \d+, then ")
_EVOKE = re.compile(r"Evoke (?P<cost>(?:\{[^{}]+\})+)(?: \([^()]*\))?")
_PERMANENT_TYPES = {"Artifact", "Battle", "Creature", "Enchantment", "Land", "Planeswalker"}


@dataclass(frozen=True)
class Draw:
    ability_id: str
    source_span: tuple[int, int]
    quantity: int


@dataclass(frozen=True)
class CastLine:
    mode: Literal["normal", "evoke"]
    retained: bool
    draw: Draw
    mana_cost: str
    selects: bool = False


@dataclass(frozen=True)
class Profile:
    oracle_id: str
    parser_version: str
    lines: tuple[CastLine, ...] = ()
    unsupported_reason: str | None = None
    evaluator_version: str = EVALUATOR_VERSION


@dataclass(frozen=True)
class Transition:
    event_id: str
    card_id: str
    from_zone: Zone
    to_zone: Zone
    player_id: str = 'self'

    @property
    def strict_delta(self) -> int:
        return int(self.to_zone in _COUNTED_ZONES) - int(self.from_zone in _COUNTED_ZONES)


@dataclass(frozen=True)
class StrictResult:
    ledger: tuple[Transition, ...]
    token_delta: int = 0
    self_id: str = 'self'

    @property
    def self_delta(self) -> int:
        return sum(item.strict_delta for item in self.ledger if item.player_id == self.self_id)

    @property
    def opponent_deltas(self) -> dict[str, int]:
        deltas: dict[str, int] = {}
        for item in self.ledger:
            if item.player_id != self.self_id:
                deltas[item.player_id] = deltas.get(item.player_id, 0) + item.strict_delta
        return deltas


def compile_profile(
    oracle_id: str, type_line: str | None, mana_cost: str | None, parsed: OracleParse,
) -> Profile:
    """Compile unconditional fixed draw on a single-faced card.

    Check the complete ability shape because a draw fact alone does not prove
    who draws, whether the draw is optional, or what other costs apply.
    """
    prefix = (oracle_id, parsed.parser_version)
    types = set((type_line or "").split(" — ", 1)[0].split())
    if not type_line or "//" in type_line or mana_cost is None or not parsed.abilities:
        return Profile(*prefix, unsupported_reason="missing data or multiple faces")

    lines = _ability_lines(parsed)
    if types & {"Instant", "Sorcery"} and len(lines) == 1:
        first = lines[0]
        scry = _SCRY.match(first.text)
        offset = scry.end() if scry else 0
        draw = _fixed_draw(parsed, first, offset)
        if draw:
            return Profile(*prefix, (CastLine("normal", False, draw, mana_cost, bool(scry)),))

    permanent_types = types & (_PERMANENT_TYPES - {"Land"})
    if permanent_types:
        abilities = [ability for ability in lines if ability.text != "Flying"]
        triggers = [ability for ability in abilities if re.fullmatch(_ENTERS, ability.text)]
        if len(triggers) == 1:
            trigger = triggers[0]
            subject, _, _ = trigger.text.partition(", ")
            expected_subjects = {"When this permanent enters"}
            expected_subjects.update(f"When this {kind.lower()} enters" for kind in permanent_types)
            if subject.removesuffix(" the battlefield") not in expected_subjects:
                return Profile(*prefix, unsupported_reason="enters ability requires another object")
            draw = _fixed_draw(parsed, trigger, len(subject) + 2)
            extra = [ability for ability in abilities if ability is not trigger]
            evoke = _EVOKE.fullmatch(extra[0].text) if len(extra) == 1 and "Creature" in types else None
            if draw and (not extra or evoke):
                normal = CastLine("normal", True, draw, mana_cost)
                lines = (normal, CastLine("evoke", False, draw, evoke["cost"])) if evoke else (normal,)
                return Profile(*prefix, lines)

    return Profile(*prefix, unsupported_reason="ability shape outside fixed-draw coverage")


def _ability_lines(parsed: OracleParse) -> list[OracleAbility]:
    """Refine paragraph segments while preserving parent IDs and offsets."""
    lines = []
    for ability in parsed.abilities:
        for match in re.finditer(r"[^\r\n]+", ability.text):
            text = match[0].strip()
            if text:
                start = ability.start + match.start() + len(match[0]) - len(match[0].lstrip())
                lines.append(replace(ability, text=text, start=start, end=start + len(text)))
    return lines


def _fixed_draw(parsed: OracleParse, ability: OracleAbility, offset: int) -> Draw | None:
    if not re.fullmatch(_DRAW, ability.text[offset:]):
        return None
    start = ability.start + offset
    for fact in parsed.mechanics:
        if fact.id == "card.draw" and fact.ability_id == ability.id:
            quantity = dict(fact.attributes).get("quantity")
            if (fact.evidence_start, fact.evidence_end) == (start, ability.end - 1):
                if type(quantity) is int and quantity >= 0:
                    return Draw(ability.id, (start, ability.end - 1), quantity)
    return None


def evaluate_cast(line: CastLine, *, resolves: bool = True) -> StrictResult:
    """Evaluate a cast from hand through its immediate consequences.

    Assume paid mana, sufficient library, no external modifiers, and a
    successful draw effect when the spell resolves. The ledger omits mana.
    """
    ledger = [Transition("cast", "source", "hand", "stack")]
    if not resolves:
        ledger.append(Transition("countered", "source", "stack", "graveyard"))
        return StrictResult(tuple(ledger))

    permanent = line.retained or line.mode == "evoke"
    destination = "battlefield" if permanent else "graveyard"
    ledger.append(Transition("resolve", "source", "stack", destination))
    if line.mode == "evoke":
        ledger.append(Transition("evoke", "source", "battlefield", "graveyard"))
    ledger.extend(_draw_transitions(line.draw.quantity, "resolve"))
    return StrictResult(tuple(ledger))


@dataclass(frozen=True)
class Engine:
    kind: Literal["opponent_cast", "equipped_creature_dies"]
    draw: Draw
    payment: int = 0


@dataclass(frozen=True)
class OpponentCast:
    paid: bool
    choose_draw: bool = True
    trigger_resolves: bool = True


@dataclass(frozen=True)
class EquippedDeath:
    card_id: str
    token: bool = False
    reaches_graveyard: bool = True
    trigger_resolves: bool = True


def compile_engine(parsed: OracleParse) -> Engine | None:
    """Recognize supported trigger signatures without inferring event frequency."""
    lines = _ability_lines(parsed)
    texts = tuple(line.text for line in lines)
    rhystic_prefix = "Whenever an opponent casts a spell, you may "
    rhystic_suffix = " unless that player pays {1}."
    if len(lines) == 1 and texts[0].startswith(rhystic_prefix) and texts[0].endswith(rhystic_suffix):
        end = lines[0].end - len(rhystic_suffix) + 1
        clause = replace(lines[0], text=texts[0][:-len(rhystic_suffix)] + ".", end=end)
        draw = _fixed_draw(parsed, clause, len(rhystic_prefix))
        if draw:
            return Engine("opponent_cast", draw, payment=1)

    death_prefix = "Whenever equipped creature dies, "
    equip = r"Equip \{1\}(?: \([^\n]*\))?"
    if (
        len(lines) == 3
        and texts[0] == "Equipped creature gets +1/-1."
        and texts[1].startswith(death_prefix)
        and re.fullmatch(equip, texts[2])
    ):
        draw = _fixed_draw(parsed, lines[1], len(death_prefix))
        if draw:
            return Engine("equipped_creature_dies", draw)
    return None


def evaluate_engine(
    engine: Engine, events: tuple[OpponentCast | EquippedDeath, ...],
) -> StrictResult:
    """Evaluate supplied events with the engine already in play.

    Each event is a confirmed opportunity within the caller's horizon. An
    equipped death describes a controlled creature leaving the battlefield;
    it includes exile replacements so the loss is charged even without a draw.
    """
    ledger: list[Transition] = []
    token_delta = 0
    consumed: set[str] = set()
    for index, event in enumerate(events):
        event_id = f"engine:{index}"
        if isinstance(event, OpponentCast) and engine.kind == "opponent_cast":
            draws = event.trigger_resolves and not event.paid and event.choose_draw
        elif isinstance(event, EquippedDeath) and engine.kind == "equipped_creature_dies":
            if event.card_id in consumed:
                raise ValueError("A creature instance can leave only once in an engine line")
            consumed.add(event.card_id)
            if event.token:
                token_delta -= 1
            else:
                destination = "graveyard" if event.reaches_graveyard else "exile"
                ledger.append(Transition(event_id, event.card_id, "battlefield", destination))
            draws = event.reaches_graveyard and event.trigger_resolves
        else:
            raise ValueError("Event is incompatible with the engine trigger")
        if draws:
            ledger.extend(_draw_transitions(engine.draw.quantity, event_id))
    return StrictResult(tuple(ledger), token_delta=token_delta)


def _draw_transitions(quantity: int, source_event: str) -> tuple[Transition, ...]:
    return tuple(
        Transition(f"{source_event}:draw:{index}", f"{source_event}:drawn:{index}", "library", "hand")
        for index in range(quantity)
    )


@dataclass(frozen=True)
class CreatureInPlay:
    card_id: str
    controller_id: str
    token: bool = False
    indestructible: bool = False
    exile_instead: bool = False


def evaluate_wrath_of_god(
    creatures: tuple[CreatureInPlay, ...], *, caster_id: str, resolves: bool = True,
) -> StrictResult:
    """Evaluate Wrath's immediate exchange from an explicit board snapshot."""
    if len({creature.card_id for creature in creatures}) != len(creatures):
        raise ValueError("A creature instance can occur only once in the board snapshot")
    ledger = [
        Transition("cast", "source", "hand", "stack", caster_id),
        Transition("resolve" if resolves else "countered", "source", "stack", "graveyard", caster_id),
    ]
    token_delta = 0
    if resolves:
        for index, creature in enumerate(creatures):
            if creature.indestructible:
                continue
            if creature.token:
                token_delta -= int(creature.controller_id == caster_id)
                continue
            destination = "exile" if creature.exile_instead else "graveyard"
            ledger.append(Transition(
                f"destroy:{index}", creature.card_id, "battlefield",
                destination, creature.controller_id,
            ))
    return StrictResult(tuple(ledger), token_delta=token_delta, self_id=caster_id)
