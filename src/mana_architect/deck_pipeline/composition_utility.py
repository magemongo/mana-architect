"""Versioned deck-level utility shared by composition moves."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from math import fsum, isfinite

from mana_architect.deck_pipeline.contracts import (
    Artifact, CardCandidate, PairwiseSynergyEdge, Role, SynergyPackage,
)
from mana_architect.deck_pipeline.mana_base import FixedManaCost, ManaAssessment, ManaBase, fixed_mana_cost
from mana_architect.synergy_interactions import MatchStatus

_ROLE_TARGETS = (
    (Role.RAMP, 10),
    (Role.CARD_ADVANTAGE, 10),
    (Role.SPOT_INTERACTION, 8),
    (Role.MASS_INTERACTION, 3),
    (Role.STRATEGY_SYNERGY, 12),
)


@dataclass(frozen=True)
class CompositionConfig(Artifact):
    version: str = "preliminary-composition-v1"
    land_target: int = 38
    role_targets: tuple[tuple[Role, int], ...] = _ROLE_TARGETS
    role_weight: float = 1.0
    joint_weight: float = 1.0
    mana_weight: float = 0.6
    curve_weight: float = 0.04
    package_seed_limit: int = 64
    repair_budget: int = 256

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.version or type(self.land_target) is not int or not 0 <= self.land_target <= 99:
            raise ValueError("Composition requires a version and a land target in [0, 99].")
        if len(self.role_targets) != len(_ROLE_TARGETS) or (
            {role for role, _ in self.role_targets} != {role for role, _ in _ROLE_TARGETS}
        ) or any(type(target) is not int or target < 0 for _, target in self.role_targets):
            raise ValueError("Composition requires a nonnegative target for each functional role.")
        if any(
            isinstance(weight, bool) or not isfinite(weight) or weight < 0
            for weight in (self.role_weight, self.joint_weight, self.mana_weight, self.curve_weight)
        ):
            raise ValueError("Composition weights must be finite and nonnegative.")
        if any(type(limit) is not int or limit < 0 for limit in (self.package_seed_limit, self.repair_budget)):
            raise ValueError("Composition work limits must be nonnegative integers.")
        object.__setattr__(self, "role_targets", tuple(sorted(self.role_targets)))


def _intrinsic_strategy(card: CardCandidate) -> float:
    if card.evaluations is None:
        return 0
    best = 0.0
    for profile in card.evaluations.commander_profiles:
        priorities = {item.strategy_id: item.priority for item in profile.strategy_affinities}
        for item in profile.evidence:
            if item.relation != "supports" or item.missing_capabilities or item.status not in {
                MatchStatus.VALIDATED, MatchStatus.CONDITIONAL,
            }:
                continue
            priority = fsum(priorities.get(strategy, 0) for strategy in sorted(set(item.strategy_ids)))
            reliability = 1.0 if item.status is MatchStatus.VALIDATED else 0.5
            best = max(best, min(1.0, item.weight * priority * reliability))
    return best


def _role_credit(card: CardCandidate) -> tuple[tuple[Role, float], ...]:
    qualities = {
        profile.role: profile.quality for profile in card.role_profiles
        if profile.qualifies and profile.quality and profile.role not in {Role.LANDS, Role.STRATEGY_SYNERGY}
    }
    if strategy := _intrinsic_strategy(card):
        qualities[Role.STRATEGY_SYNERGY] = strategy
    if not qualities:
        return ()
    scale = max(qualities.values()) / fsum(qualities.values())
    return tuple((role, quality * scale) for role, quality in sorted(qualities.items()))


def is_complete_package(package: SynergyPackage) -> bool:
    return (
        package.dependency_status == "complete" and not package.requirements and not package.resource_requirements
        and bool(package.benefit_event_ids)
    )


def _printed_costs(package: SynergyPackage) -> tuple[FixedManaCost, ...] | None:
    if set(package.requirements) != {"pay_recorded_resource_costs"} or not package.resource_requirements:
        return None
    costs = []
    for requirement in package.resource_requirements:
        if ":printed_spell_cost:" not in requirement:
            return None
        cost = fixed_mana_cost(requirement.rsplit(":printed_spell_cost:", 1)[1])
        if cost is None:
            return None
        costs.append(cost)
    return tuple(costs)


@dataclass(frozen=True)
class DeckUtility:
    components: tuple[tuple[str, float], ...]
    mana: ManaAssessment

    @property
    def total(self) -> float:
        return fsum(value for _, value in self.components)

    def delta(self, before: "DeckUtility") -> tuple[tuple[str, float], ...]:
        previous = dict(before.components)
        return tuple((key, value - previous[key]) for key, value in self.components)


class CompositionUtility:
    def __init__(
        self, config: CompositionConfig, cards: Mapping[str, CardCandidate], mana: ManaBase,
        edges: tuple[PairwiseSynergyEdge, ...], packages: tuple[SynergyPackage, ...],
    ) -> None:
        self.config = config
        self.mana = mana
        self.role_credit = {card_id: _role_credit(card) for card_id, card in cards.items()}
        self.anchors = frozenset(mana.commanders)
        self.verified_edges = tuple(
            edge for edge in edges
            if edge.confidence == "rule_verified" and not edge.prerequisites and len(edge.event_ids) == 2
            and {edge.source_card_id, edge.target_card_id} - self.anchors
        )
        self.package_costs = {}
        for package in packages:
            if is_complete_package(package):
                self.package_costs[package.package_id] = ()
            elif costs := _printed_costs(package):
                self.package_costs[package.package_id] = costs
        self.scorable_packages = tuple(package for package in packages if package.package_id in self.package_costs)

    def package_supported(self, package: SynergyPackage, deck: Mapping[str, int]) -> bool:
        costs = self.package_costs.get(package.package_id)
        return costs is not None and all(self.mana.supports(deck, cost) for cost in costs)

    def _joint_value(self, deck: Mapping[str, int]) -> float:
        present = deck.keys() | self.anchors
        best: dict[str, float] = {}
        feeder_edges = set()
        for package in self.scorable_packages:
            if (
                not present.issuperset(package.member_card_ids)
                or not self.anchors.issuperset(package.commander_anchor_ids)
                or not self.package_supported(package, deck)
            ):
                continue
            weight = 1.0 if is_complete_package(package) else 0.8
            for event_id in package.benefit_event_ids:
                best[event_id] = max(best.get(event_id, 0.0), weight * package.score)
            feeder_edges.update(package.edge_ids)
        for edge in self.verified_edges:
            event_id = edge.event_ids[1]
            if edge.edge_id in feeder_edges and event_id not in best:
                continue
            if edge.source_card_id in present and edge.target_card_id in present:
                best[event_id] = max(best.get(event_id, 0.0), edge.strength)
        return fsum(best[event_id] for event_id in sorted(best))

    def evaluate(self, deck: Mapping[str, int], land_target: int) -> DeckUtility:
        roles: Counter[Role] = Counter()
        for card_id in sorted(deck):
            for role, credit in self.role_credit.get(card_id, ()):
                roles[role] += credit * deck[card_id]
        mana = self.mana.assess(deck, land_target)
        components = tuple(
            (role.value, self.config.role_weight * min(roles[role], target))
            for role, target in self.config.role_targets
        )
        return DeckUtility(
            (*components,
             ("joint_synergy", self.config.joint_weight * self._joint_value(deck)),
             ("mana_sources", self.config.mana_weight * mana.source_utility),
             ("land_tempo", -0.025 * self.config.mana_weight * mana.tapped_count),
             ("mana_curve", -self.config.curve_weight * mana.curve_penalty)),
            mana,
        )
