"""Deterministic lexical parsing for Magic: The Gathering Oracle text.

This module deliberately stops before strategy and card-role assignment. It
produces source-linked lexical features and a small set of mechanical facts;
downstream catalog rules can consume those facts independently.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

PARSER_VERSION = "oracle-lexical-v2"


@dataclass(frozen=True)
class LexiconEntry:
    id: str
    category: str
    phrase: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class MechanicalPattern:
    id: str
    matcher: str
    mechanic_id: str
    attributes: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class OracleFeature:
    id: str
    category: str
    surface: str
    canonical: str
    start: int
    end: int
    ability_id: str


@dataclass(frozen=True)
class ManaCost:
    symbols: tuple[str, ...]
    start: int
    end: int


@dataclass(frozen=True)
class OracleAbility:
    id: str
    kind: str
    text: str
    start: int
    end: int
    mana_cost: ManaCost | None
    features: tuple[OracleFeature, ...]


@dataclass(frozen=True)
class MechanicalFact:
    id: str
    ability_id: str
    evidence_start: int
    evidence_end: int
    attributes: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class OracleParse:
    parser_version: str
    oracle_text: str
    abilities: tuple[OracleAbility, ...]
    features: tuple[OracleFeature, ...]
    mechanics: tuple[MechanicalFact, ...]

    def to_json(self) -> dict[str, object]:
        def feature_json(feature: OracleFeature) -> dict[str, object]:
            return {
                "id": feature.id, "category": feature.category,
                "surface": feature.surface, "canonical": feature.canonical,
                "start": feature.start, "end": feature.end,
                "ability_id": feature.ability_id,
            }

        return {
            "parser_version": self.parser_version,
            "oracle_text": self.oracle_text,
            "abilities": [
                {
                    "id": ability.id, "kind": ability.kind,
                    "text": ability.text, "start": ability.start,
                    "end": ability.end,
                    "mana_cost": (
                        {"symbols": list(ability.mana_cost.symbols),
                         "start": ability.mana_cost.start,
                         "end": ability.mana_cost.end}
                        if ability.mana_cost else None
                    ),
                    "features": [feature_json(item) for item in ability.features],
                }
                for ability in self.abilities
            ],
            "features": [feature_json(item) for item in self.features],
            "mechanics": [
                {
                    "id": item.id, "ability_id": item.ability_id,
                    "evidence_start": item.evidence_start,
                    "evidence_end": item.evidence_end,
                    "attributes": dict(item.attributes),
                }
                for item in self.mechanics
            ],
        }

    @classmethod
    def from_json(cls, value: object) -> "OracleParse | None":
        if not isinstance(value, dict):
            return None

        def parse_feature(item: dict[str, object]) -> OracleFeature:
            return OracleFeature(
                str(item["id"]), str(item["category"]), str(item["surface"]),
                str(item["canonical"]), int(item["start"]), int(item["end"]),
                str(item["ability_id"]),
            )

        abilities: list[OracleAbility] = []
        for item in value.get("abilities", []):
            if not isinstance(item, dict):
                continue
            raw_cost = item.get("mana_cost")
            cost = None
            if isinstance(raw_cost, dict):
                cost = ManaCost(
                    tuple(str(symbol) for symbol in raw_cost.get("symbols", [])),
                    int(raw_cost["start"]), int(raw_cost["end"]),
                )
            ability_features = tuple(
                parse_feature(feature)
                for feature in item.get("features", [])
                if isinstance(feature, dict)
            )
            abilities.append(OracleAbility(
                str(item["id"]), str(item["kind"]), str(item["text"]),
                int(item["start"]), int(item["end"]), cost, ability_features,
            ))
        features = tuple(
            parse_feature(item)
            for item in value.get("features", [])
            if isinstance(item, dict)
        )
        mechanics = tuple(
            MechanicalFact(
                str(item["id"]), str(item["ability_id"]),
                int(item["evidence_start"]), int(item["evidence_end"]),
                tuple(sorted(
                    (str(key), attribute)
                    for key, attribute in item.get("attributes", {}).items()
                )),
            )
            for item in value.get("mechanics", [])
            if isinstance(item, dict)
        )
        return cls(
            str(value["parser_version"]), str(value["oracle_text"]),
            tuple(abilities), features, mechanics,
        )


# Starter coverage. The lexicon is intentionally data-like and can be replaced
# by a versioned catalog without changing the parser's matching algorithm.
DEFAULT_LEXICON = (
    LexiconEntry("ability_word.landfall", "ability_word", "landfall"),
    LexiconEntry("ability_word.constellation", "ability_word", "constellation"),
    LexiconEntry("keyword.flying", "ability_keyword", "flying"),
    LexiconEntry("keyword.trample", "ability_keyword", "trample"),
    LexiconEntry("action.create", "keyword_action", "create", ("creates", "created")),
    LexiconEntry("action.sacrifice", "keyword_action", "sacrifice", ("sacrifices", "sacrificed")),
    LexiconEntry("action.exile", "keyword_action", "exile", ("exiles", "exiled")),
    LexiconEntry("trigger.whenever", "trigger_timing", "whenever"),
    LexiconEntry("trigger.when", "trigger_timing", "when"),
    LexiconEntry("trigger.at_beginning", "trigger_timing", "at the beginning of"),
    LexiconEntry("event.enters", "trigger_timing", "enters", ("enter", "entered")),
    LexiconEntry("event.dies", "trigger_timing", "dies", ("die", "died")),
    LexiconEntry("object.permanent", "game_object", "permanent", ("permanents",)),
    LexiconEntry("object.creature", "game_object", "creature", ("creatures",)),
    LexiconEntry("object.artifact", "game_object", "artifact", ("artifacts",)),
    LexiconEntry("object.enchantment", "game_object", "enchantment", ("enchantments",)),
    LexiconEntry("object.land", "game_object", "land", ("lands",)),
    LexiconEntry("object.token", "game_object", "token", ("tokens",)),
    LexiconEntry("counter.plus_one_plus_one", "counter", "+1/+1 counter", ("+1/+1 counters",)),
    LexiconEntry("counter.minus_one_minus_one", "counter", "-1/-1 counter", ("-1/-1 counters",)),
    LexiconEntry("counter.generic", "counter", "counter", ("counters",)),
    LexiconEntry("target.creature", "targeting", "target creature"),
    LexiconEntry("target.opponent", "targeting", "target opponent"),
    LexiconEntry("scope.each", "scope", "each"),
    LexiconEntry("scope.one_or_more", "scope", "one or more"),
    LexiconEntry("scope.you_control", "scope", "you control"),
    LexiconEntry("operator.instead", "rules_operator", "instead"),
    LexiconEntry("operator.would", "rules_operator", "would"),
    LexiconEntry("operator.twice", "rules_operator", "twice"),
    LexiconEntry("operator.if", "rules_operator", "if"),
    LexiconEntry("operator.may", "rules_operator", "may"),
    LexiconEntry("zone.battlefield", "zone", "battlefield"),
    LexiconEntry("zone.graveyard", "zone", "graveyard", ("graveyards",)),
    LexiconEntry("zone.exile", "zone", "exile"),
)

_MANA = re.compile(r"\{([^{}]+)\}")
_WORD = re.compile(r"\w", re.UNICODE)
_ABILITY_WORD_PREFIX = re.compile(r"^\s*([\w +/'-]+?)\s+—\s*", re.UNICODE)
_DOUBLE_PATTERN = re.compile(
    r"\bdouble\b[^.\n]{0,100}\b\+1/\+1 counters?\b|"
    r"\bdouble the number of \+1/\+1 counters?\b",
    re.IGNORECASE,
)
_COUNTER_PUT_PATTERN = re.compile(
    r"\bput\b[^.\n]{0,100}\b(?:\+1/\+1 counters?|counters?)\b",
    re.IGNORECASE,
)
_PLUS_ONE_COUNTER_PUT_PATTERN = re.compile(
    r"\bput\b[^.\n]{0,100}\b\+1/\+1 counters?\b",
    re.IGNORECASE,
)
_PLUS_ONE_COUNTER_MATTERS_PATTERN = re.compile(
    r"\b(?:with|has|have|gets?)\b[^.;\n]{0,100}\b\+1/\+1 counters?\b|"
    r"\bwhenever\b[^.;\n]{0,100}\b\+1/\+1 counter\b[^.;\n]{0,100}\bput\b",
    re.IGNORECASE,
)
_TOKEN_CREATE_PATTERN = re.compile(
    r"\bcreate(?:s|d)?\b[^.\n]{0,100}\btokens?\b", re.IGNORECASE
)
_TOKEN_REPLACEMENT_PATTERN = re.compile(
    r"\bwould create\b[^.\n]{0,140}\btokens?\b[^.\n]{0,100}\b(?:twice|double)\b[^.\n]{0,40}\binstead\b",
    re.IGNORECASE,
)
_COUNTER_REPLACEMENT_PATTERN = re.compile(
    r"\bwould put\b[^.\n]{0,220}\bcounters?\b[^.\n]{0,220}\b(?:twice|double)\b[^.\n]{0,60}\binstead\b",
    re.IGNORECASE,
)
_CARD_DRAW_PATTERN = re.compile(
    r"\bdraw\s+(?P<quantity>a|one|two|three|four|five|\d+|x)\s+cards?\b",
    re.IGNORECASE,
)
_LIFE_GAIN_TRIGGER_PATTERN = re.compile(
    r"\b(?:whenever|when)\s+(?P<subject>you|a player|that player|an opponent)\s+gains?\s+life\b",
    re.IGNORECASE,
)
_LIFE_GAIN_PATTERN = re.compile(
    r"\b(?P<subject>you|a player|that player|target player|each player|each opponent)\s+"
    r"gain(?:s)?\s+(?P<quantity>\d+|one|two|three|four|five|that much)?\s*life\b",
    re.IGNORECASE,
)
_NONCOMBAT_DAMAGE_PATTERN = re.compile(
    r"\bdeals?\s+(?P<quantity>\d+|x|that much)\s+damage\s+to\s+each opponent\b",
    re.IGNORECASE,
)
_COMBAT_DAMAGE_PATTERN = re.compile(r"\bdeals?\s+combat damage\b", re.IGNORECASE)
_LAND_SEARCH_PATTERN = re.compile(
    r"\bsearch your library for\s+(?:a |an |up to \w+ |any number of )?"
    r"[\w' -]{0,80}?land card\b",
    re.IGNORECASE,
)
_LAND_RAMP_PATTERN = re.compile(
    r"\bsearch your library for\s+(?:a |an |up to \w+ |any number of )?"
    r"[\w' -]{0,80}?land card\b[^.;\n]{0,160}?put\s+(?:it|that card|them)\s+"
    r"onto the battlefield\b",
    re.IGNORECASE,
)
_REANIMATE_PATTERN = re.compile(
    r"\breturn\s+(?:target\s+|a\s+|one\s+)?creature card\s+"
    r"from your graveyard to the battlefield\b",
    re.IGNORECASE,
)
_CREATURE_BUFF_PATTERN = re.compile(
    r"\b(?:target|equipped|enchanted) creature gets?\s+\+1/\+1\b",
    re.IGNORECASE,
)
_MECHANIC_MATCHERS = {
    "token_create": _TOKEN_CREATE_PATTERN,
    "counter_put": _COUNTER_PUT_PATTERN,
    "plus_one_counter_put": _PLUS_ONE_COUNTER_PUT_PATTERN,
    "plus_one_counter_matters": _PLUS_ONE_COUNTER_MATTERS_PATTERN,
    "counter_double": _DOUBLE_PATTERN,
    "token_create_replacement": _TOKEN_REPLACEMENT_PATTERN,
    "counter_placement_replacement": _COUNTER_REPLACEMENT_PATTERN,
    "card_draw": _CARD_DRAW_PATTERN,
    "life_gain": _LIFE_GAIN_PATTERN,
    "life_gain_trigger": _LIFE_GAIN_TRIGGER_PATTERN,
    "noncombat_damage_opponents": _NONCOMBAT_DAMAGE_PATTERN,
    "combat_damage": _COMBAT_DAMAGE_PATTERN,
    "land_search": _LAND_SEARCH_PATTERN,
    "land_ramp": _LAND_RAMP_PATTERN,
    "creature_return_from_graveyard": _REANIMATE_PATTERN,
    "creature_plus_one_plus_one_modifier": _CREATURE_BUFF_PATTERN,
}
DEFAULT_MECHANIC_PATTERNS = (
    MechanicalPattern("token_create_replacement", "token_create_replacement", "token.create_replacement", (("multiplier", 2),)),
    MechanicalPattern("counter_placement_replacement", "counter_placement_replacement", "counter.placement_replacement", (("multiplier", 2),)),
    MechanicalPattern("counter_double", "counter_double", "counter.double", (("counter_type", "+1/+1"),)),
    MechanicalPattern("counter_put", "counter_put", "counter.put"),
    MechanicalPattern("plus_one_counter_put", "plus_one_counter_put", "counter.put_plus_one_plus_one", (("counter_type", "+1/+1"),)),
    MechanicalPattern("plus_one_counter_matters", "plus_one_counter_matters", "counter.matters_plus_one_plus_one", (("counter_type", "+1/+1"),)),
    MechanicalPattern("token_create", "token_create", "token.create"),
    MechanicalPattern("card_draw", "card_draw", "card.draw"),
    MechanicalPattern("life_gain", "life_gain", "life.gain"),
    MechanicalPattern("life_gain_trigger", "life_gain_trigger", "life.gain_trigger"),
    MechanicalPattern("noncombat_damage_opponents", "noncombat_damage_opponents", "damage.noncombat_opponents"),
    MechanicalPattern("combat_damage", "combat_damage", "damage.combat"),
    MechanicalPattern("land_search", "land_search", "land.search_library"),
    MechanicalPattern("land_ramp", "land_ramp", "land.search_to_battlefield"),
    MechanicalPattern("creature_return_from_graveyard", "creature_return_from_graveyard", "creature.return_from_graveyard"),
    MechanicalPattern("creature_plus_one_plus_one_modifier", "creature_plus_one_plus_one_modifier", "creature.plus_one_plus_one_modifier"),
)


def _normalize_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("’", "'")).strip()


def parse_oracle_text(
    oracle_text: str | None,
    lexicon: Iterable[LexiconEntry] | None = None,
    mechanical_patterns: Iterable[MechanicalPattern] | None = None,
) -> OracleParse:
    """Segment Oracle text and extract longest-match lexical features.

    Offsets are Python Unicode code-point offsets into the original string.
    The parser is conservative: ambiguous text remains lexical evidence and is
    not promoted to a mechanic unless one of the explicit patterns matches.
    """
    source = oracle_text or ""
    abilities: list[OracleAbility] = []
    all_features: list[OracleFeature] = []
    mechanics: list[MechanicalFact] = []
    entries = tuple(lexicon or DEFAULT_LEXICON)
    patterns = tuple(mechanical_patterns or DEFAULT_MECHANICAL_PATTERNS)

    for ability_index, (start, end) in enumerate(_segments(source), start=1):
        text = source[start:end]
        if not text.strip():
            continue
        ability_id = f"a{len(abilities) + 1}"
        kind, cost = _ability_kind_and_cost(text, start)
        features = _extract_features(text, start, ability_id, entries)
        ability = OracleAbility(
            ability_id, kind, text, start, end, cost, features
        )
        abilities.append(ability)
        all_features.extend(features)
        replacement_spans: list[tuple[int, int]] = []
        for definition in patterns:
            matcher = _MECHANIC_MATCHERS.get(definition.matcher)
            if matcher is None:
                continue
            for match in matcher.finditer(text):
                if _is_negated(text, match.start()):
                    continue
                if definition.matcher.endswith("replacement"):
                    replacement_spans.append((match.start(), match.end()))
                if any(match.start() >= left and match.end() <= right for left, right in replacement_spans):
                    if not definition.matcher.endswith("replacement"):
                        continue
                attributes = dict(definition.attributes)
                attributes.update(
                    (key, value.casefold() if key == "subject" else value)
                    for key, value in match.groupdict().items()
                    if value is not None
                )
                if "quantity" in attributes:
                    attributes["quantity"] = _normalize_quantity(
                        str(attributes["quantity"])
                    )
                mechanics.append(
                    MechanicalFact(
                        definition.mechanic_id,
                        ability_id,
                        start + match.start(),
                        start + match.end(),
                        tuple(sorted(attributes.items())),
                    )
                )
    return OracleParse(
        PARSER_VERSION, source, tuple(abilities), tuple(all_features), tuple(mechanics)
    )


def _is_negated(text: str, action_start: int) -> bool:
    clause_start = max(
        text.rfind(mark, 0, action_start) for mark in (".", "!", "?", ";", "\n")
    ) + 1
    prefix = text[clause_start:action_start]
    negation = re.search(
        r"\b(?:can't|cannot|can not|doesn't|does not|don't|do not|never)\b",
        prefix,
        re.IGNORECASE,
    )
    if negation is None:
        return False
    continuation = prefix[negation.end():]
    return re.search(r"\b(?:but|however|instead)\b", continuation, re.IGNORECASE) is None


def _normalize_quantity(value: str) -> int | str:
    quantities: dict[str, int | str] = {
        "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "x": "variable", "that much": "variable",
    }
    normalized = value.casefold()
    if normalized in quantities:
        return quantities[normalized]
    if normalized.isdigit():
        return int(normalized)
    return normalized


def _segments(source: str) -> tuple[tuple[int, int], ...]:
    """Split at paragraph breaks, preserving absolute offsets."""
    result = []
    cursor = 0
    for match in re.finditer(r"\n\s*\n", source):
        if source[cursor:match.start()].strip():
            result.append((cursor, match.start()))
        cursor = match.end()
    if source[cursor:].strip():
        result.append((cursor, len(source)))
    return tuple(result)


def _ability_kind_and_cost(text: str, offset: int) -> tuple[str, ManaCost | None]:
    colon = text.find(":")
    prefix = text[:colon] if colon >= 0 else ""
    symbols = tuple(match.group(1) for match in _MANA.finditer(prefix))
    if colon >= 0 and symbols:
        first = _MANA.search(prefix)
        assert first is not None
        return (
            "activated",
            ManaCost(symbols, offset + first.start(), offset + colon),
        )
    if re.match(r"\s*(?:if|as)\b", text, re.IGNORECASE) and re.search(
        r"\binstead\b", text, re.IGNORECASE
    ):
        return "replacement", None
    if re.match(r"\s*(?:when|whenever|at)\b", text, re.IGNORECASE) or _ABILITY_WORD_PREFIX.match(text):
        return "triggered", None
    return "static", None


def _extract_features(
    text: str,
    offset: int,
    ability_id: str,
    lexicon: tuple[LexiconEntry, ...],
) -> tuple[OracleFeature, ...]:
    candidates: list[tuple[int, int, LexiconEntry, str]] = []
    for entry in lexicon:
        for phrase in (entry.phrase, *entry.aliases):
            normalized = _normalize_phrase(phrase)
            if not normalized:
                continue
            pattern = re.compile(
                rf"(?<!\w){re.escape(normalized)}(?!\w)", re.IGNORECASE
            )
            for match in pattern.finditer(text):
                candidates.append((match.start(), match.end(), entry, match.group()))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2].id))
    chosen: list[tuple[int, int, LexiconEntry, str]] = []
    for candidate in candidates:
        start, end, _, _ = candidate
        if any(
            start < kept_end and end > kept_start
            and (start, end) != (kept_start, kept_end)
            for kept_start, kept_end, *_ in chosen
        ):
            continue
        chosen.append(candidate)
    return tuple(
        OracleFeature(
            entry.id,
            entry.category,
            surface,
            _normalize_phrase(entry.phrase),
            offset + start,
            offset + end,
            ability_id,
        )
        for start, end, entry, surface in sorted(chosen, key=lambda item: item[0])
    )
