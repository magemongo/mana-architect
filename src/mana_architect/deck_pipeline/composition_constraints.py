"""Check composer inputs and calculate legal capacity before ranking cards."""

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from math import isfinite

from mana_architect.deck_pipeline.candidate_pool import PoolConstraints
from mana_architect.deck_pipeline.contracts import (
    CandidatePool, PackageAnalysis, PairwiseSynergyAnalysis, PairwiseSynergyEdge, SynergyPackage, ValidationIssue,
    artifact_digest, edge_fingerprint,
)
from mana_architect.deck_pipeline.eligibility import CardEligibility, EligibilityGate
from mana_architect.deck_pipeline.inventory import CardSnapshot
from mana_architect.deck_pipeline.mana_base import is_front_land
from mana_architect.deck_pipeline.pairwise_synergy import pairwise_catalog_fingerprint
from mana_architect.strategy_synergy import SelectedStrategy, SynergyCatalogContext, normalize_priorities


def pool_constraints(pool: CandidatePool) -> PoolConstraints:
    excluded = {issue.card_id for issue in pool.excluded if issue.card_id and issue.code == "user_excluded"}
    locked = {card.card_id for card in (*pool.candidates, *pool.reserve) if card.locked}
    if pool.provenance:
        excluded.update(pool.provenance.excluded_card_ids)
        locked.update(pool.provenance.locked_card_ids)
    return PoolConstraints(tuple(excluded), tuple(locked))


def check_artifacts(
    gate: EligibilityGate, pool: CandidatePool, pairwise: PairwiseSynergyAnalysis, packages: PackageAnalysis,
    catalog: SynergyCatalogContext,
) -> tuple[tuple[PairwiseSynergyEdge, ...], tuple[ValidationIssue, ...]]:
    context = gate.context
    pool_fingerprint = artifact_digest(pool.to_json())
    issues = []
    if pool.provenance:
        original = pool.provenance.context
        try:
            priorities = normalize_priorities(
                tuple(SelectedStrategy(item.strategy_id, item.weight) for item in context.strategy_preferences),
                catalog,
            )
        except ValueError as error:
            return (), (ValidationIssue("invalid_strategy_preferences", str(error)),)
        if (
            replace(original, strategy_preferences=context.strategy_preferences) != context
            or {item.strategy_id: item.weight for item in original.strategy_preferences} != priorities
        ):
            issues.append(ValidationIssue("pool_context_mismatch", "The pool belongs to different run inputs."))
    pair_origin = pairwise.provenance
    package_origin = packages.provenance
    if (
        any(run_id != context.run_id for run_id in (pool.run_id, pairwise.run_id, packages.run_id))
        or any(origin.pool_id != pool.pool_id or origin.pool_fingerprint != pool_fingerprint
               or origin.collection_snapshot_id != gate.snapshot.snapshot_id for origin in (pair_origin, package_origin))
        or pair_origin.commander_card_ids != context.commander_card_ids
        or pair_origin.ruleset_version != context.ruleset_version
        or pair_origin.engine_versions != context.engine_versions
        or pair_origin.evaluation_snapshot_id != (pool.provenance.evaluation_snapshot_id if pool.provenance else None)
        or pair_origin.strategy_catalog_version != catalog.strategies.version
        or pairwise.rule_catalog_version != catalog.rule_version
        or pair_origin.rule_catalog_fingerprint != pairwise_catalog_fingerprint(catalog)
        or package_origin.rule_catalog_version != pairwise.rule_catalog_version
        or package_origin.pairwise_analysis_id != pairwise.analysis_id
    ):
        issues.append(ValidationIssue("analysis_context_mismatch", "The analyses belong to different pipeline inputs."))

    anchors = set(context.commander_card_ids)
    all_members = {card.card_id for card in (*pool.candidates, *pool.reserve)}
    active = all_members if package_origin.include_reserve else {card.card_id for card in pool.candidates}
    constraints = pool_constraints(pool)
    for card in (*pool.candidates, *pool.reserve):
        eligible = gate.check_card(card.card_id)
        if (
            not eligible.eligible or card.provenance.collection_snapshot_id != gate.snapshot.snapshot_id
            or card.provenance.engine_versions != context.engine_versions
            or card.owned_printing_ids != eligible.owned_printing_ids
            or card.available_quantity != eligible.available_quantity
            or card.card_id in constraints.exclude_card_ids
        ):
            issues.append(ValidationIssue("pool_inventory_mismatch", "The pool card is inconsistent with eligibility.", card.card_id))

    unique = {}
    for edge in pairwise.edges:
        endpoints = {edge.source_card_id, edge.target_card_id}
        if (
            (edge.edge_id in unique and unique[edge.edge_id] != edge)
            or not endpoints <= all_members | anchors or set(edge.commander_anchor_ids) != endpoints & anchors
        ):
            issues.append(ValidationIssue("invalid_edge_reference", f"Inconsistent edge {edge.edge_id}."))
        unique[edge.edge_id] = edge
    admitted = {
        edge_id: edge for edge_id, edge in unique.items()
        if {edge.source_card_id, edge.target_card_id} <= active | anchors
    }
    edges = tuple(admitted[edge_id] for edge_id in sorted(admitted))
    if edge_fingerprint(edges) != package_origin.edge_fingerprint:
        issues.append(ValidationIssue("edge_fingerprint_mismatch", "The edges differ from the Phase 3 handoff."))

    for edge in edges:
        if len(edge.event_ids) != 2:
            issues.append(ValidationIssue("invalid_edge_reference", f"Missing directional events for {edge.edge_id}."))
    package_ids = set()
    for package in (*packages.packages, *packages.partial):
        members = set(package.member_card_ids)
        package_anchors = set(package.commander_anchor_ids)
        path = [admitted[edge_id] for edge_id in package.edge_ids if edge_id in admitted]
        events = {event for edge in path for event in edge.event_ids}
        invalid = (
            not members or not members <= active or not package_anchors <= anchors
            or not path or len(path) != len(package.edge_ids)
            or any(not {edge.source_card_id, edge.target_card_id} <= members | package_anchors for edge in path)
        )
        if isinstance(package, SynergyPackage):
            invalid |= (
                package.package_id in package_ids or not isfinite(package.score) or not 0 <= package.score <= 1
                or not set(package.benefit_event_ids) <= events or not events <= set(package.effect_ids)
                or not {term for edge in path for term in edge.prerequisites} <= set(package.requirements)
                or not {f"{edge.edge_id}:{cost}" for edge in path for cost in edge.resource_requirements}
                <= set(package.resource_requirements)
                or not {f"{edge.edge_id}:{term}" for edge in path for term in edge.timing_constraints}
                <= set(package.timing_constraints)
                or not {term for edge in path for term in edge.limitations} <= set(package.limitations)
                or (package.dependency_status == "complete") != (not package.requirements)
                or (bool(package.resource_requirements) and "pay_recorded_resource_costs" not in package.requirements)
            )
            package_ids.add(package.package_id)
        if invalid:
            issues.append(ValidationIssue(
                "invalid_package_reference", f"Inconsistent {package.package_archetype} package for {', '.join(sorted(members))}.",
            ))
    return edges, tuple(issues)


@dataclass(frozen=True)
class _Slots:
    total: int
    lands: int
    spells: int


class OwnedCapacity:
    """Limit each English name and each printing-backed Oracle identity independently."""

    def __init__(
        self, cards: Mapping[str, CardSnapshot], eligibility: Mapping[str, CardEligibility],
        commanders: tuple[CardSnapshot, ...],
    ) -> None:
        self.owned = {card_id: eligibility[card_id].available_quantity for card_id in cards}
        self.keys = {card_id: card.name.casefold() for card_id, card in cards.items()}
        self.lands = frozenset(card_id for card_id, card in cards.items() if is_front_land(card))
        self.limits = {
            self.keys[card_id]: eligibility[card_id].copy_limit for card_id in cards
            if eligibility[card_id].copy_limit is not None
        }
        self.commander_names = Counter(card.name.casefold() for card in commanders)
        self.grouped = defaultdict(Counter)
        for card_id, quantity in self.owned.items():
            self.grouped[self.keys[card_id]][card_id in self.lands] += quantity

    def remaining(self, deck: Mapping[str, int]) -> _Slots | None:
        used = defaultdict(Counter)
        for card_id, quantity in deck.items():
            if quantity > self.owned.get(card_id, 0) or quantity < 1:
                return None
            used[self.keys[card_id]][card_id in self.lands] += quantity
        total = lands = spells = 0
        for name, owned in self.grouped.items():
            consumed = used.get(name)
            land = owned[True] - (consumed[True] if consumed else 0)
            spell = owned[False] - (consumed[False] if consumed else 0)
            copies = self.limits.get(name)
            if copies is None:
                copies = land + spell
            else:
                copies -= self.commander_names[name] + (sum(consumed.values()) if consumed else 0)
                if copies < 0:
                    return None
            lands += min(copies, land)
            spells += min(copies, spell)
            total += min(copies, land + spell)
        return _Slots(total, lands, spells)

    def land_interval(self, deck: Mapping[str, int], size: int) -> tuple[int, int] | None:
        remaining = self.remaining(deck)
        slots = size - sum(deck.values())
        if remaining is None or not 0 <= slots <= remaining.total:
            return None
        selected = sum(quantity for card_id, quantity in deck.items() if card_id in self.lands)
        return selected + max(0, slots - remaining.spells), selected + min(slots, remaining.lands)

    def addition_check(
        self, deck: Mapping[str, int], size: int, land_target: int,
    ) -> Callable[[tuple[str, ...]], bool]:
        selected = Counter()
        for card_id, quantity in deck.items():
            selected[self.keys[card_id], card_id in self.lands] += quantity
        land_slots = land_target - sum(quantity for (_, land), quantity in selected.items() if land)
        spell_slots = size - sum(deck.values()) - land_slots
        remaining = self.remaining(deck)
        assert remaining is not None

        def can_add(card_ids: tuple[str, ...]) -> bool:
            local = defaultdict(Counter)
            for card_id, quantity in Counter(card_ids).items():
                if quantity + deck.get(card_id, 0) > self.owned.get(card_id, 0):
                    return False
                local[self.keys[card_id]][card_id in self.lands] += quantity
            lands_added = sum(counts[True] for counts in local.values())
            spells_added = len(card_ids) - lands_added
            if lands_added > land_slots or spells_added > spell_slots:
                return False
            land_capacity, spell_capacity = remaining.lands, remaining.spells
            for name, counts in local.items():
                owned = self.grouped[name]
                lands = owned[True] - selected[name, True]
                spells = owned[False] - selected[name, False]
                limit = self.limits.get(name)
                copies = lands + spells if limit is None else (
                    limit - self.commander_names[name] - selected[name, True] - selected[name, False]
                )
                used = sum(counts.values())
                if used > copies:
                    return False
                land_capacity += min(lands - counts[True], copies - used) - min(lands, copies)
                spell_capacity += min(spells - counts[False], copies - used) - min(spells, copies)
            # Each valid addition removes exactly one legal slot per copy from total capacity.
            return land_slots - lands_added <= land_capacity and spell_slots - spells_added <= spell_capacity

        return can_add
