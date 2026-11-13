"""Card-local interaction facts and their independent heuristic evaluations."""

from dataclasses import asdict, dataclass
from enum import StrEnum

from mana_architect.interaction_rules_catalog import INTERACTION_RULES_VERSION


class Scope(StrEnum):
    SINGLE = "single"
    MULTI_TARGET = "multi_target"
    MASS = "mass"
    VARIABLE = "variable"
    MODAL = "modal"
    UNKNOWN = "unknown"


class Selection(StrEnum):
    TARGET = "target"
    CHOSEN = "chosen"
    ALL = "all"
    EACH = "each"
    NON_TARGETED = "non_targeted"
    UNKNOWN = "unknown"


class Timing(StrEnum):
    INSTANT = "instant"
    SORCERY = "sorcery"
    TRIGGERED = "triggered"
    STATIC = "static"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Quantity:
    kind: str = "unknown"
    min: int | None = None
    max: int | None = None

    def __post_init__(self) -> None:
        if (self.min is not None and self.min < 0) or (self.max is not None and self.max < 0):
            raise ValueError("Interaction quantity bounds must be nonnegative.")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("Interaction quantity minimum exceeds its maximum.")


@dataclass(frozen=True)
class InteractionEvidence:
    oracle_span: str
    start: int
    end: int
    rule_ids: tuple[str, ...]
    ability_id: str

    def __post_init__(self) -> None:
        if not self.rule_ids or self.start < 0 or self.end <= self.start:
            raise ValueError("Interaction evidence requires a rule and a nonempty source span.")
        if self.end - self.start != len(self.oracle_span):
            raise ValueError("Interaction evidence offsets must match the span length.")


@dataclass(frozen=True)
class InteractionEffect:
    effect_id: str
    action: str
    object_types: tuple[str, ...]
    selection: Selection
    target_count: Quantity
    affected_object_count: Quantity
    affected_zone: str
    controller_filter: str
    duration: str
    symmetry: str
    scope_type: Scope
    mechanical_tags: tuple[str, ...]
    roles: tuple[str, ...]
    evidence: InteractionEvidence
    conditions: tuple[str, ...] = ()
    additional_costs: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    wipe_potential: str | None = None
    timing: Timing = Timing.UNKNOWN

    @property
    def interaction_type(self) -> str | None:
        if self.action == "tax" and any(item.startswith("cast from ") for item in self.restrictions):
            return None
        if self.scope_type in {Scope.SINGLE, Scope.MULTI_TARGET}:
            return "spot"
        if self.scope_type is Scope.MASS:
            return "mass"
        return None


@dataclass(frozen=True)
class InteractionScore:
    total: float | None = None
    components: tuple[tuple[str, float | None], ...] = ()

    def __post_init__(self) -> None:
        for score in (self.total, *(value for _, value in self.components)):
            if score is not None and not 0 <= score <= 10:
                raise ValueError("Interaction scores must be between 0 and 10.")


@dataclass(frozen=True)
class InteractionVariant:
    variant_id: str
    ability_id: str
    cost: str | None
    timing: Timing
    scope_type: Scope
    effects: tuple[InteractionEffect, ...]
    requirements: tuple[str, ...] = ()
    additional_costs: tuple[str, ...] = ()
    mode_ids: tuple[str, ...] = ()
    face_index: int | None = None
    base_mana_value: float | None = None
    activated: bool = False
    spot_score: InteractionScore = InteractionScore()
    mass_score: InteractionScore = InteractionScore()

    @property
    def interaction_types(self) -> tuple[str, ...]:
        return tuple(kind for kind in ("spot", "mass") if any(
            effect.interaction_type == kind for effect in self.effects
        ))


@dataclass(frozen=True)
class ModeChoice:
    ability_id: str
    minimum: int
    maximum: int
    modes: tuple[tuple[str, str], ...]
    face_index: int | None = None


@dataclass(frozen=True)
class InteractionProfile:
    card_id: str
    oracle_parser_version: str
    oracle_keywords: tuple[str, ...]
    variants: tuple[InteractionVariant, ...]
    mode_choices: tuple[ModeChoice, ...] = ()
    classification_confidence: Confidence = Confidence.HIGH
    warnings: tuple[str, ...] = ()
    rules_version: str = INTERACTION_RULES_VERSION
    scoring_version: str | None = None

    @property
    def spot_interaction(self) -> bool:
        return any("spot" in variant.interaction_types for variant in self.variants)

    @property
    def mass_interaction(self) -> bool:
        return any("mass" in variant.interaction_types for variant in self.variants)

    @property
    def is_interaction(self) -> bool:
        return any(variant.effects for variant in self.variants)

    @property
    def strategy_tags(self) -> tuple[str, ...]:
        return tuple(tag for tag in ("spot_interaction", "mass_interaction") if getattr(self, tag))

    @property
    def mechanical_tags(self) -> tuple[str, ...]:
        return tuple(sorted({tag for variant in self.variants for effect in variant.effects
                             for tag in effect.mechanical_tags}))

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(sorted({role for variant in self.variants for effect in variant.effects
                             for role in effect.roles}))

    def best_variant(self, kind: str) -> InteractionVariant | None:
        """Find the highest-scoring variant for a scope, including unscored matches."""
        if kind not in {"spot", "mass"}:
            raise ValueError("Interaction kind must be spot or mass.")

        def rank(variant: InteractionVariant) -> float:
            score = getattr(variant, f"{kind}_score").total
            return score if score is not None else -1.0

        return max((variant for variant in self.variants if kind in variant.interaction_types),
                   key=rank, default=None)

    @property
    def scores(self) -> dict[str, float | None]:
        return {
            kind: getattr(variant, f"{kind}_score").total if (variant := self.best_variant(kind)) else None
            for kind in ("spot", "mass")
        }

    def to_json(self) -> dict[str, object]:
        profile = asdict(self)
        del profile["card_id"]
        for data, variant in zip(profile["variants"], self.variants, strict=True):
            data["interaction_types"] = variant.interaction_types
            for kind in ("spot", "mass"):
                score = data[f"{kind}_score"]
                score["components"] = dict(score["components"])
        for name in ("is_interaction", "spot_interaction", "mass_interaction", "strategy_tags",
                     "mechanical_tags", "roles", "scores"):
            profile[name] = getattr(self, name)
        return {"card_id": self.card_id, "interaction_profile": _json_arrays(profile)}


def _json_arrays(value: object) -> object:
    if isinstance(value, (tuple, list)):
        return [_json_arrays(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_arrays(item) for key, item in value.items()}
    return value
