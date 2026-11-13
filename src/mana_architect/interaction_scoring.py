"""Independent card-local scoring heuristics for spot and mass interaction."""

from dataclasses import dataclass
import math
import re

from mana_architect.interaction_profile import InteractionEffect, InteractionScore, InteractionVariant, Timing

INTERACTION_SCORING_VERSION = "interaction-scoring-v1"

_SPOT_WEIGHTS = (
    ("versatility", .20), ("efficiency", .20), ("speed", .20),
    ("reliability", .20), ("answer_quality", .15), ("duration", .05),
)
_MASS_WEIGHTS = (
    ("coverage", .20), ("asymmetry", .20), ("scalability", .15), ("efficiency", .15),
    ("speed", .10), ("reliability", .10), ("answer_quality", .10),
)


@dataclass(frozen=True)
class InteractionScoringConfig:
    spot_weights: tuple[tuple[str, float], ...] = _SPOT_WEIGHTS
    mass_weights: tuple[tuple[str, float], ...] = _MASS_WEIGHTS
    version: str = INTERACTION_SCORING_VERSION

    def __post_init__(self) -> None:
        for weights, defaults in ((self.spot_weights, _SPOT_WEIGHTS), (self.mass_weights, _MASS_WEIGHTS)):
            if len(weights) != len(defaults) or dict(weights).keys() != dict(defaults).keys():
                raise ValueError("Interaction scoring weights must name each dimension once.")
            if any(not 0 <= weight <= 1 for _, weight in weights) or not math.isclose(sum(dict(weights).values()), 1):
                raise ValueError("Interaction scoring weights must be nonnegative and add to one.")


DEFAULT_INTERACTION_SCORING = InteractionScoringConfig()

_SPEED = {Timing.INSTANT: 10.0, Timing.SORCERY: 5.0, Timing.STATIC: 5.0, Timing.TRIGGERED: 4.0}
_ASYMMETRY = {"opponent_only": 10.0, "self_only": 9.0, "selective": 8.0, "symmetric": 5.0}
_QUALITY = {
    "destroy_removal": 8.0, "exile_removal": 9.0, "bounce": 5.0, "tuck": 8.0,
    "counterspell": 9.0, "ability_counter": 8.0, "graveyard_hate": 8.0,
    "protection": 7.0, "damage_prevention": 6.0, "forced_sacrifice": 7.0,
    "discard_disruption": 7.0, "tap_disable": 5.0, "combat_interaction": 5.0,
    "control_change": 9.0, "stat_reduction": 6.0, "rule_restriction": 7.0,
    "damage_removal": 7.0,
}
_MANA_SYMBOL = re.compile(r"\{([^{}]+)\}")
_NONMANA_COST = re.compile(r"^(?:sacrifice|discard|exile|return|remove|tap|reveal|pay \d+ life)\b", re.IGNORECASE)
_TARGET_RESTRICTION = re.compile(
    r"^(?:with|without|nonland|nonartifact|noncreature|tapped|untapped|attacking|blocking|"
    r"nonblack|monocolored|legendary|nontoken|other)\b|^\w+ spell$",
)
_COMPENSATION = re.compile(r"^(?:its controller|that player|each player|that permanent's controller)\b")


def _payment(cost: str) -> tuple[float, int] | None:
    """Return fixed mana and the number of separately paid resource costs."""
    mana = 0.0
    resources = 0
    parts = cost.split(",")
    for part in parts:
        part = part.strip()
        if _NONMANA_COST.match(part):
            resources += 1
            continue
        if not part or _MANA_SYMBOL.sub("", part).strip():
            return None
        for symbol in _MANA_SYMBOL.findall(part.upper()):
            if symbol in {"T", "Q"}:
                resources += 1
            elif symbol.isascii() and symbol.isdecimal():
                mana += int(symbol)
            elif all(color in {"W", "U", "B", "R", "G", "C", "S"} for color in symbol.split("/")):
                mana += 1
            else:
                # X, Phyrexian, and two-brid do not specify one fixed payment.
                return None
    return mana, resources


def _efficiency(variant: InteractionVariant, kind: str) -> float | None:
    if "variable_cast_cost" in variant.requirements:
        return None
    payment = _payment(variant.cost) if variant.cost else None
    if (payment is None and variant.cost is None and variant.base_mana_value is not None
            and not variant.activated and "overload" not in variant.requirements):
        payment = (variant.base_mana_value, 0)
    if payment is None:
        return None
    mana, resources = payment
    for cost in variant.additional_costs:
        if variant.activated and cost == variant.cost:
            continue
        extra = _payment(cost)
        if extra is None:
            return None
        mana += extra[0]
        resources += extra[1]
    steepness = 1.5 if kind == "spot" else 1.0
    return max(0, 10 - steepness * mana - min(4, resources * 1.25))


def _duration(duration: str) -> float | None:
    if duration == "indefinite":
        return 10.0
    if duration == "while_source_remains" or duration.startswith("for as long as ") or "leaves the battlefield" in duration:
        return 6.0
    if duration == "unknown":
        return None
    return 4.0


def _dimensions(effect: InteractionEffect, variant: InteractionVariant, kind: str) -> dict[str, float | None]:
    object_types = set(effect.object_types) - {"unknown", "damage_event", "player"}
    width = None if effect.object_types == ("unknown",) else min(10, 5.5 + 1.5 * max(0, len(object_types) - 1))
    if "permanent" in object_types or "spell" in object_types:
        width = 10.0
    filters = sum(bool(_TARGET_RESTRICTION.match(item)) for item in effect.restrictions)
    if width is not None:
        excluded = sum(item.startswith(("except ", "other than ")) for item in effect.exceptions)
        width = max(0, width - filters - .75 * excluded)
    # A number or duration by itself is an Oracle fact. These deductions only
    # describe intrinsic hurdles, without estimating which targets exist.
    reliability = max(0, 10 - 1.5 * len(effect.conditions) - .5 * filters
                      - float("affected_player_chooses" in effect.choices)
                      - float(effect.wipe_potential == "potential")
                      - float(variant.timing is Timing.TRIGGERED))
    quality = _QUALITY.get(effect.mechanical_tags[0])
    if quality is not None and any(_COMPENSATION.match(item) for item in effect.restrictions):
        quality = max(0, quality - 1)
    dimensions = {
        "efficiency": _efficiency(variant, kind),
        "speed": _SPEED.get(variant.timing),
        "reliability": reliability,
        "answer_quality": quality,
    }
    if kind == "spot":
        dimensions.update(versatility=width, duration=_duration(effect.duration))
    else:
        dimensions.update(
            coverage=min(10, width + 1) if width is not None else None,
            asymmetry=_ASYMMETRY.get(effect.symmetry),
            scalability=8.0 if effect.affected_object_count.kind == "each" else 10.0,
        )
    return dimensions


def _weighted(dimensions: dict[str, float | None], weights: tuple[tuple[str, float], ...]) -> InteractionScore:
    components = tuple((name, dimensions[name]) for name, _ in weights)
    if any(dimensions[name] is None for name, weight in weights if weight):
        return InteractionScore(components=components)
    total = sum(weight * value for name, weight in weights if (value := dimensions[name]) is not None)
    return InteractionScore(round(total, 3), components)


def score_interaction_variant(
    variant: InteractionVariant, config: InteractionScoringConfig = DEFAULT_INTERACTION_SCORING,
) -> tuple[InteractionScore, InteractionScore]:
    """Use the strongest eligible effect per scope without summing overlapping coverage."""
    results = []
    for kind, weights in (("spot", config.spot_weights), ("mass", config.mass_weights)):
        scores = (_weighted(_dimensions(effect, variant, kind), weights) for effect in variant.effects
                  if effect.interaction_type == kind)
        results.append(max(scores, key=lambda score: score.total if score.total is not None else -1,
                           default=InteractionScore()))
    return results[0], results[1]
