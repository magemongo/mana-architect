"""Conservative estimates of available mana and printed casting demand."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from math import ceil, isfinite
import re

from mana_architect.deck_pipeline.contracts import ColorSources, Diagnostic
from mana_architect.deck_pipeline.inventory import CardSnapshot
from mana_architect.interaction_rules_catalog import mask_reminder_text
from mana_architect.mana_resource_extractor import extract_mana_resources
from mana_architect.oracle_text import parse_oracle_text

_BASIC_TYPES = dict(zip(("plains", "island", "swamp", "mountain", "forest"), "WUBRG", strict=True))
_SYMBOLS = "WUBRGC"
_TAPPED = re.compile(r"\benters(?: the battlefield)? tapped\b", re.IGNORECASE)
_FETCH = re.compile(
    r"\bsearch your library for (?:a|an|one) (?P<filter>[^.\n]+?) cards?, "
    r"(?:(?:reveal (?:it|that card),? )?and )?put (?:it|that card) onto the battlefield\b",
    re.IGNORECASE,
)
_FETCH_TYPE = re.compile(r"(?:(?P<basic>basic) )?(?:(?P<snow>snow) )?(?P<type>land|plains|island|swamp|mountain|forest)")
_ALTERNATE_COST = re.compile(r"\b(?:rather than pay|without paying|alternate cost|alternative cost)\b")
_FIXED_COST = re.compile(r"(?:\{(?:[0-9]+|[WUBRGC])\})+")


def _types(type_line: str) -> tuple[set[str], set[str]]:
    types, _, subtypes = type_line.casefold().partition("—")
    return set(types.split()), set(subtypes.split())


def is_front_land(card: CardSnapshot) -> bool:
    return "land" in _types((card.type_line or "").split(" // ", 1)[0])[0]


@dataclass(frozen=True)
class FixedManaCost:
    generic: int
    pips: tuple[str, ...]


def fixed_mana_cost(cost: str) -> FixedManaCost | None:
    if not _FIXED_COST.fullmatch(cost):
        return None
    symbols = re.findall(r"\{([^{}]+)\}", cost)
    return FixedManaCost(
        sum(int(symbol) for symbol in symbols if symbol.isdecimal()),
        tuple(symbol for symbol in symbols if not symbol.isdecimal()),
    )


@dataclass(frozen=True)
class _LandFace:
    types: frozenset[str]
    subtypes: frozenset[str]
    direct: frozenset[str]
    conditional: frozenset[str]
    enters_tapped: bool
    fetch: tuple[tuple[str, bool, bool], ...]


def _land_face(type_line: str, oracle: str, identity: frozenset[str]) -> _LandFace:
    types, subtypes = _types(type_line)
    lines = mask_reminder_text(oracle).casefold()
    reliable = {_BASIC_TYPES[subtype] for subtype in subtypes if subtype in _BASIC_TYPES}
    conditional = set()
    for event in extract_mana_resources(parse_oracle_text(oracle)):
        if event.action != "add_mana":
            continue
        colors = set(event.color_options)
        rule = (event.color_rule or "").casefold().replace("’", "'")
        if "commander" in rule and "color identity" in rule:
            colors = set(identity)
        elif "any" in colors:
            colors = set("WUBRG")
        elif not colors:
            continue
        if (
            (event.activation_cost or "").strip().casefold() == "{t}"
            and not (event.requirements or event.restrictions or event.optional)
            and (
                not event.color_rule or bool(event.color_options and "any" not in event.color_options)
                or rule in {"of any color", "of any one color"}
                or rule in {"of any color in your commander's color identity", "of any one color in your commander's color identity"}
            )
        ):
            reliable.update(colors)
        else:
            conditional.update(colors)

    fetch = ()
    if match := _FETCH.search(lines):
        filters = re.split(r",\s*(?:or )?|\s+or\s+", match["filter"])
        parsed = tuple(_FETCH_TYPE.fullmatch(term.strip()) for term in filters)
        if all(parsed):
            fetch = tuple((item["type"], bool(item["basic"]), bool(item["snow"])) for item in parsed if item)
    return _LandFace(
        frozenset(types), frozenset(subtypes), frozenset(reliable),
        frozenset(conditional - reliable), bool(_TAPPED.search(lines)), fetch,
    )


@dataclass(frozen=True)
class _ManaCard:
    front_land: bool
    land_faces: tuple[_LandFace, ...]
    modal: bool
    uncertain_layout: bool
    mana_value: float | None
    pips: tuple[str, ...]
    uncertain_cost: bool
    unmodeled_sources: frozenset[str]


def _mana_card(card: CardSnapshot, identity: frozenset[str]) -> _ManaCard:
    types = (card.type_line or "").split(" // ")
    oracle = (card.oracle_text or "").split(" // ")
    front_land = is_front_land(card)
    modal = card.layout == "modal_dfc"
    faces = tuple(
        _land_face(face, oracle[index] if index < len(oracle) else "", identity)
        for index, face in enumerate(types)
        if (index == 0 or modal) and "land" in _types(face)[0]
    )
    cost = (card.mana_cost or "").split(" // ", 1)[0]
    symbols = tuple(re.findall(r"\{([^{}]+)\}", cost))
    mana_value = card.mana_value
    if mana_value is not None and (not isfinite(mana_value) or mana_value < 0):
        mana_value = None
    pips = tuple(symbol for symbol in symbols if symbol in _SYMBOLS and len(symbol) == 1)
    uncertain_cost = not front_land and (
        not symbols or any(symbol not in _SYMBOLS and not symbol.isdecimal() for symbol in symbols)
        or len(types) > 1 or bool(_ALTERNATE_COST.search(oracle[0].casefold()))
    )
    known = set().union(*(face.direct | face.conditional for face in faces))
    return _ManaCard(
        front_land, faces, modal, card.layout is None and any("land" in _types(face)[0] for face in types[1:]),
        mana_value, pips, uncertain_cost, frozenset((set(card.produced_mana) & set(_SYMBOLS)) - known),
    )


def _fetch_matches(fetch: _LandFace, target: _LandFace) -> bool:
    return any(
        (land_type == "land" or land_type in target.subtypes)
        and (not basic or "basic" in target.types)
        and (not snow or "snow" in target.types)
        for land_type, basic, snow in fetch.fetch
    )


@dataclass(frozen=True)
class ManaAssessment:
    land_count: int
    tapped_count: int
    conditional_source_count: int
    utility_land_count: int
    modal_land_count: int
    sources: tuple[ColorSources, ...]
    curve: tuple[tuple[float, int], ...]
    source_utility: float
    curve_penalty: float


class ManaBase:
    def __init__(
        self, cards: Mapping[str, CardSnapshot], commanders: tuple[str, ...], identity: frozenset[str],
    ) -> None:
        self.cards = {card_id: _mana_card(card, identity) for card_id, card in cards.items()}
        self.commanders = commanders
        self._fetch_targets = {
            (card_id, symbol): tuple(
                target_id for target_id, target in self.cards.items()
                if target_id != card_id and target.front_land and target.land_faces
                and symbol in target.land_faces[0].direct
                and any(_fetch_matches(face, target.land_faces[0]) for face in card.land_faces)
            )
            for card_id, card in self.cards.items() if any(face.fetch for face in card.land_faces)
            for symbol in _SYMBOLS
            if all(symbol not in face.direct for face in card.land_faces)
        }

    def _fetch_count(self, deck: Mapping[str, int], symbol: str) -> int:
        # Match distinct fetch copies to distinct target copies for each color estimate.
        assignments: dict[tuple[str, int], str] = {}

        def assign(fetch_id: str, visited: set[tuple[str, int]]) -> bool:
            for target_id in self._fetch_targets.get((fetch_id, symbol), ()):
                for copy in range(deck.get(target_id, 0)):
                    target = target_id, copy
                    if target in visited:
                        continue
                    visited.add(target)
                    if target not in assignments or assign(assignments[target], visited):
                        assignments[target] = fetch_id
                        return True
            return False

        for card_id, quantity in deck.items():
            if self.cards[card_id].front_land and self._fetch_targets.get((card_id, symbol)):
                for _ in range(quantity):
                    assign(card_id, set())
        return len(assignments)

    def supports(self, deck: Mapping[str, int], cost: FixedManaCost) -> bool:
        """Check whether distinct direct land copies can pay one printed cost."""
        sources = []
        for card_id, quantity in deck.items():
            card = self.cards[card_id]
            if card.front_land:
                colors = frozenset().union(*(face.direct for face in card.land_faces))
                if colors:
                    sources.extend([colors] * quantity)
        if len(sources) < cost.generic + len(cost.pips):
            return False
        assignments: dict[int, str] = {}

        def assign(symbol: str, visited: set[int]) -> bool:
            for index, source in enumerate(sources):
                if symbol not in source or index in visited:
                    continue
                visited.add(index)
                if index not in assignments or assign(assignments[index], visited):
                    assignments[index] = symbol
                    return True
            return False

        return all(assign(symbol, set()) for symbol in cost.pips)

    def assess(self, deck: Mapping[str, int], land_target: int) -> ManaAssessment:
        direct, untapped, conditional, modal, pips, demand, peak, curve = (Counter() for _ in range(8))
        lands = tapped = conditional_count = modal_count = utility_lands = 0
        for card_id, quantity in (*deck.items(), *((card_id, 1) for card_id in self.commanders)):
            card = self.cards[card_id]
            is_commander = card_id in self.commanders
            if card.front_land and not is_commander:
                lands += quantity
            elif card.mana_value is not None:
                curve[card.mana_value] += quantity
            early_weight = 2 if is_commander or (card.mana_value is not None and card.mana_value <= 3) else 1
            for symbol, count in Counter(card.pips).items():
                pips[symbol] += count * quantity
                demand[symbol] += count * quantity * early_weight
                peak[symbol] = max(peak[symbol], count)
            if is_commander or not card.land_faces:
                continue

            possible = set().union(*(face.direct | face.conditional for face in card.land_faces))
            if card.modal:
                modal_count += quantity
            if not card.front_land:
                modal.update({symbol: quantity for symbol in possible})
                continue
            front, *backs = card.land_faces
            if front.enters_tapped and all(face.enters_tapped for face in backs):
                tapped += quantity
            reliable = set().union(*(face.direct for face in card.land_faces))
            early = set().union(*(face.direct for face in card.land_faces if not face.enters_tapped))
            uncertain = possible - reliable
            if uncertain or card.unmodeled_sources:
                conditional_count += quantity
            if not possible and not any(face.fetch for face in card.land_faces):
                utility_lands += quantity
            direct.update({symbol: quantity for symbol in reliable})
            untapped.update({symbol: quantity for symbol in early})
            conditional.update({symbol: quantity for symbol in uncertain})

        total_demand = sum(demand.values())
        sources = tuple(
            ColorSources(
                symbol, pips[symbol], max(peak[symbol], ceil(0.7 * land_target * demand[symbol] / total_demand))
                if demand[symbol] else 0,
                direct[symbol], untapped[symbol], conditional[symbol], modal[symbol], self._fetch_count(deck, symbol),
            )
            for symbol in _SYMBOLS
        )
        source_utility = sum(
            min(row.target, 0.65 * row.direct + 0.35 * row.potentially_untapped + 0.35 * row.fetch + 0.2 * row.modal)
            - 0.5 * row.target
            for row in sources
        )
        curve_penalty = sum(max(0, value - 4) * quantity for value, quantity in sorted(curve.items()))
        return ManaAssessment(
            lands, tapped, conditional_count, utility_lands, modal_count, sources,
            tuple(sorted(curve.items())), source_utility, curve_penalty,
        )

    def diagnostics(self, deck: Mapping[str, int], mana: ManaAssessment) -> tuple[Diagnostic, ...]:
        diagnostics = [
            Diagnostic("land_count", "Selected front-face land copies.", value=mana.land_count),
            Diagnostic("enters_tapped", "Land copies modeled as entering tapped.", value=mana.tapped_count),
            Diagnostic("conditional_land_sources", "Land copies with restricted or unresolved mana.", value=mana.conditional_source_count),
            Diagnostic("utility_lands", "Land copies with no modeled mana production or fetch ability.", value=mana.utility_land_count),
            Diagnostic("modal_land_options", "Copies with a verified modal land face.", value=mana.modal_land_count),
        ]
        for card_id in sorted(deck.keys() | set(self.commanders)):
            card = self.cards[card_id]
            if card.uncertain_layout:
                diagnostics.append(Diagnostic("unknown_land_face_layout", "Back-face land access is unresolved.", card_id=card_id))
            if card.uncertain_cost:
                diagnostics.append(Diagnostic("uncertain_cast_cost", "Printed pips omit variable, alternate, or unresolved casting options.", card_id=card_id))
        return tuple(diagnostics)
