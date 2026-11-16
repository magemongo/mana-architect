"""Compose the first legal, owned Commander deck from the staged pipeline."""

from collections import Counter, defaultdict
from collections.abc import Set
from dataclasses import asdict, dataclass, replace
from itertools import islice
from math import fsum

from mana_architect.deck_pipeline.adapters import EvaluationError
from mana_architect.deck_pipeline.candidate_pool import (
    CandidatePoolBuilder, CandidatePoolConfig, PoolBuildError, PoolConstraints, make_card_candidate,
)
from mana_architect.deck_pipeline.composition_constraints import OwnedCapacity, check_artifacts, pool_constraints
from mana_architect.deck_pipeline.composition_utility import (
    CompositionConfig, CompositionUtility, DeckUtility, is_complete_package,
)
from mana_architect.deck_pipeline.contracts import (
    CandidatePool, CandidateStatus, CardCandidate, CompositionCapacity, CompositionCardDecision, CompositionProvenance,
    DecisionRecord, DeckEntry, DeckProposal, Diagnostic, PackageAnalysis, PackageAssessment, PairwiseSynergyAnalysis,
    PairwiseSynergyEdge, Role, SelectionReason, ValidationIssue, ValidationReport, artifact_digest,
)
from mana_architect.deck_pipeline.eligibility import CardEligibility, EligibilityGate
from mana_architect.deck_pipeline.inventory import CardSnapshot
from mana_architect.deck_pipeline.mana_base import ManaBase, is_front_land
from mana_architect.deck_pipeline.pairwise_synergy import PairwiseAnalysisError, PairwiseConfig, PairwiseSynergyAnalyzer
from mana_architect.deck_pipeline.synergy_packages import PackageBuildError, PackageConfig, SynergyPackageBuilder
from mana_architect.strategy_synergy import SynergyCatalogContext, load_synergy_context


def _issue_key(issue: ValidationIssue) -> tuple:
    return issue.code, issue.card_id or "", issue.printing_id or 0, issue.message


def _failure(
    gate: EligibilityGate, issues: tuple[ValidationIssue, ...], *,
    provenance: CompositionProvenance | None = None, capacity: CompositionCapacity | None = None,
    reserve: tuple[str, ...] = (), card_decisions: tuple[CompositionCardDecision, ...] = (),
) -> DeckProposal:
    report = ValidationReport(tuple(sorted(set(issues), key=_issue_key)), gate.snapshot.legality_data_as_of)
    return DeckProposal(
        artifact_digest((asdict(gate.context), asdict(report), asdict(provenance) if provenance else None)),
        gate.context.run_id, gate.context.commander_card_ids, (), reserve,
        validation=report, proposal_status="infeasible", provenance=provenance,
        capacity=capacity, card_decisions=card_decisions,
    )


def _roles(card: CardCandidate) -> tuple[Role, ...]:
    return tuple(sorted(set(card.roles) | {profile.role for profile in card.role_profiles if profile.qualifies}))


def _reserve_decisions(cards: dict[str, CardCandidate], pool: CandidatePool) -> tuple[CompositionCardDecision, ...]:
    decisions = [
        CompositionCardDecision(card_id, CandidateStatus.RESERVE, 0, _roles(card), ("composition_infeasible",))
        for card_id, card in sorted(cards.items())
    ]
    decisions.extend(_exclusions(pool, cards.keys()))
    return tuple(sorted(decisions, key=lambda item: item.card_id))


def _exclusions(pool: CandidatePool, present: Set[str]) -> list[CompositionCardDecision]:
    exclusions = defaultdict(set)
    for issue in pool.excluded:
        if issue.card_id and issue.card_id not in present:
            exclusions[issue.card_id].add(issue.code)
    return [
        CompositionCardDecision(card_id, CandidateStatus.EXCLUDED, 0, (), tuple(sorted(codes)))
        for card_id, codes in sorted(exclusions.items())
    ]


def _package_assessments(
    analysis: PackageAnalysis, deck: Counter[str], utility: CompositionUtility,
) -> tuple[PackageAssessment, ...]:
    selected = deck.keys()
    packages = []
    for package in sorted(analysis.packages, key=lambda item: item.package_id):
        missing = tuple(sorted(set(package.member_card_ids) - selected))
        packages.append(PackageAssessment(
            package.package_id, package.package_archetype,
            "incomplete" if missing else "complete" if is_complete_package(package) else "conditional",
            package.member_card_ids, package.commander_anchor_ids, package.edge_ids,
            missing_card_ids=missing, requirements=package.requirements,
            resource_requirements=package.resource_requirements, timing_constraints=package.timing_constraints,
            limitations=package.limitations, benefit_event_ids=package.benefit_event_ids, explanation=package.explanation,
            modeled_requirements_supported=not missing and utility.package_supported(package, deck),
            evidence=package.evidence,
            repeatable=package.repeatable, self_sustaining=package.self_sustaining, infinite=package.infinite,
        ))
    for partial in sorted(analysis.partial, key=lambda item: (item.package_archetype, item.member_card_ids, item.edge_ids)):
        packages.append(PackageAssessment(
            None, partial.package_archetype, "incomplete", partial.member_card_ids, partial.commander_anchor_ids,
            partial.edge_ids, tuple(sorted(set(partial.member_card_ids) - selected)), partial.missing_roles,
            explanation=partial.reason_code,
        ))
    return tuple(packages)


def _shortfalls(
    config: CompositionConfig, roles: Counter[Role], utility: DeckUtility,
) -> tuple[Diagnostic, ...]:
    mana = utility.mana
    gaps = [
        Diagnostic("role_target_shortfall", "Selected role coverage is below target.", role, target - roles[role])
        for role, target in config.role_targets if roles[role] < target
    ]
    if mana.land_count != config.land_target:
        gaps.append(Diagnostic("land_target_adjusted", "Selected land count differs from the configured target.",
                               Role.LANDS, mana.land_count - config.land_target))
    for row in mana.sources:
        dependable = 0.65 * row.direct + 0.35 * row.potentially_untapped
        if dependable < row.target:
            gaps.append(Diagnostic("mana_source_shortfall", f"Direct {row.symbol} source estimate is below target.",
                                   value=round(row.target - dependable, 6)))
    return tuple(gaps)


class DeckSelection:
    """An in-memory decision trace and the current deck utility."""

    def __init__(
        self, utility: CompositionUtility, land_count: int, cards: dict[str, CardCandidate], *,
        starting_deck: Counter[str] | None = None, source: str = "preliminary_composer",
    ) -> None:
        self.utility = utility
        self.land_count = land_count
        self.deck = Counter(starting_deck)
        self.source = source
        self.result = utility.evaluate(self.deck, land_count)
        self.log: list[DecisionRecord] = []
        self.reasons: dict[str, set[str]] = defaultdict(set)
        self.deltas: dict[str, Counter[str]] = defaultdict(Counter)
        self.statuses = {card_id: card.status for card_id, card in cards.items()}
        for card_id in self.deck:
            self.statuses[card_id] = CandidateStatus.SELECTED
            self.reasons[card_id].add("retained_from_preliminary_deck")

    def commit(self, card_id: str, quantity: int, reason: str, *, evidence: tuple[str, ...] = ()) -> None:
        previous = self.statuses[card_id]
        self.deck[card_id] += quantity
        if not self.deck[card_id]:
            del self.deck[card_id]
        status = CandidateStatus.SELECTED if card_id in self.deck else CandidateStatus.RESERVE
        self.statuses[card_id] = status
        after = self.utility.evaluate(self.deck, self.land_count)
        self.deltas[card_id].update(dict(after.delta(self.result)))
        self.result = after
        self.reasons[card_id].add(reason)
        self.log.append(DecisionRecord(
            len(self.log) + 1, card_id, self.source, previous, status,
            SelectionReason(reason, evidence_ids=evidence), reason != "locked_card",
        ))

    def fill(self, capacity: OwnedCapacity, size: int, package_seeds: tuple[tuple[str, ...], ...]) -> bool:
        card_ids = tuple(sorted(capacity.owned))
        while sum(self.deck.values()) < size:
            can_add = capacity.addition_check(self.deck, size, self.land_count)
            groups = {
                missing for group in package_seeds
                if (missing := tuple(card_id for card_id in group if card_id not in self.deck))
                and len(missing) > 1
            }
            options = [(card_id,) for card_id in card_ids if self.deck[card_id] < capacity.owned[card_id]]
            options.extend(sorted(groups))
            best = None
            tied = False
            for group in options:
                if not can_add(group):
                    continue
                trial = self.deck.copy()
                trial.update(group)
                comparison = self.utility.evaluate(trial, self.land_count)
                delta_per_slot = (comparison.total - self.result.total) / len(group)
                key = (-round(delta_per_slot, 9), group)
                if best is None or key < best:
                    tied = best is not None and key[0] == best[0]
                    best = key
                elif key[0] == best[0]:
                    tied = True
            if best is None:
                return False
            for card_id in best[1]:
                reason = (
                    "package_group" if len(best[1]) > 1 else
                    "land_allocation" if card_id in capacity.lands else "marginal_utility"
                )
                self.commit(card_id, 1, reason)
                if tied:
                    self.reasons[card_id].add("oracle_id_tiebreak")
        return True

    def repair(self, capacity: OwnedCapacity, budget: int, locked: set[str]) -> None:
        size = sum(self.deck.values())
        removable = sorted(
            (card_id for card_id, quantity in self.deck.items() if quantity > int(card_id in locked)),
            key=lambda card_id: (fsum(score for _, score in self.utility.role_credit.get(card_id, ())), card_id),
        )
        available = sorted(
            (card_id for card_id in capacity.owned if self.deck[card_id] < capacity.owned[card_id]),
            key=lambda card_id: (-fsum(score for _, score in self.utility.role_credit.get(card_id, ())), card_id),
        )
        pairs = (
            (incoming, outgoing) for incoming in available for outgoing in removable
            if incoming != outgoing and (incoming in capacity.lands) == (outgoing in capacity.lands)
        )
        best = None
        for incoming, outgoing in islice(pairs, budget):
            trial = self.deck.copy()
            trial[outgoing] -= 1
            if not trial[outgoing]:
                del trial[outgoing]
            trial[incoming] += 1
            if capacity.land_interval(trial, size) is None:
                continue
            delta = round(self.utility.evaluate(trial, self.land_count).total - self.result.total, 9)
            if delta > 0:
                key = (-delta, incoming, outgoing)
                if best is None or key < best:
                    best = key
        if best is not None:
            _, incoming, outgoing = best
            self.commit(outgoing, -1, "composition_repair_removed")
            self.commit(incoming, 1, "composition_repair_added")


@dataclass(frozen=True)
class PreparedComposition:
    gate: EligibilityGate
    pool: CandidatePool
    packages: PackageAnalysis
    edges: tuple[PairwiseSynergyEdge, ...]
    constraints: PoolConstraints
    cards: dict[str, CardCandidate]
    capacity: OwnedCapacity
    diagnostics: CompositionCapacity
    active: set[str]
    land_fallback: set[str]
    provenance: CompositionProvenance
    utility: CompositionUtility
    land_count: int


def proposal_identity(gate: EligibilityGate, provenance: CompositionProvenance, deck: Counter[str]) -> str:
    return artifact_digest((gate.context.run_id, asdict(provenance), sorted(deck.items())))


class PreliminaryDeckComposer:
    def __init__(
        self, config: CompositionConfig = CompositionConfig(), *, synergy_context: SynergyCatalogContext | None = None,
    ) -> None:
        self.config = config
        self.catalog = synergy_context or load_synergy_context()

    def compose(
        self, gate: EligibilityGate, pool: CandidatePool, pairwise: PairwiseSynergyAnalysis, packages: PackageAnalysis,
    ) -> DeckProposal:
        prepared = self.prepare(gate, pool, pairwise, packages)
        if isinstance(prepared, DeckProposal):
            return prepared
        selection = DeckSelection(prepared.utility, prepared.land_count, prepared.cards)
        for card_id in prepared.constraints.locked_card_ids:
            selection.commit(card_id, 1, "locked_card")
        members = (
            package for package in prepared.utility.scorable_packages
            if prepared.active.issuperset(package.member_card_ids)
        )
        seeds = tuple(package.member_card_ids for package in islice(
            sorted(members, key=lambda item: (-item.score, item.package_id)), self.config.package_seed_limit,
        ))
        if not selection.fill(prepared.capacity, gate.context.main_deck_size, seeds):
            return _failure(gate, (ValidationIssue("selection_stalled", "Unable to fill the reserved deck slots."),),
                            provenance=prepared.provenance, capacity=prepared.diagnostics)
        roles = self._coverage(selection.deck, prepared.cards, selection.result)
        if _shortfalls(self.config, roles, selection.result):
            selection.repair(prepared.capacity, self.config.repair_budget, set(prepared.constraints.locked_card_ids))
        return self.describe(prepared, selection)

    def prepare(
        self, gate: EligibilityGate, pool: CandidatePool, pairwise: PairwiseSynergyAnalysis, packages: PackageAnalysis,
    ) -> PreparedComposition | DeckProposal:
        if gate.violations:
            return _failure(gate, gate.violations)
        edges, issues = check_artifacts(gate, pool, pairwise, packages, self.catalog)
        if issues:
            return _failure(gate, issues)
        provenance = CompositionProvenance(
            gate.snapshot.snapshot_id, gate.context.ruleset_version, pairwise.provenance.pool_fingerprint,
            pairwise.analysis_id, packages.analysis_id, packages.provenance.edge_fingerprint,
            artifact_digest((
                [asdict(package) for package in sorted(packages.packages, key=lambda item: item.package_id)],
                [asdict(partial) for partial in sorted(
                    packages.partial, key=lambda item: (item.package_archetype, item.member_card_ids, item.edge_ids),
                )],
            )),
            self.config.version, artifact_digest(asdict(self.config)),
        )
        constraints = pool_constraints(pool)
        locked = set(constraints.locked_card_ids)
        excluded = set(constraints.exclude_card_ids)
        cards = {card.card_id: card for card in (*pool.candidates, *pool.reserve)}
        conflicts = [
            ValidationIssue("conflicting_constraints", "A locked card is also excluded.", card_id)
            for card_id in sorted(locked & excluded)
        ] + [
            ValidationIssue("invalid_locked_card", "The locked card is absent from the legal pool.", card_id)
            for card_id in sorted(locked - cards.keys() - excluded)
        ] + [
            ValidationIssue("conflicting_constraints", "An excluded card is in the command zone.", card_id)
            for card_id in sorted(excluded & set(gate.context.commander_card_ids))
        ]
        if conflicts:
            return _failure(gate, tuple(conflicts), provenance=provenance)

        owned = {card.card_id: card for card in gate.snapshot.cards}
        eligibility = {card_id: gate.check_card(card_id) for card_id in owned if card_id not in excluded}
        eligible_ids = {card_id for card_id, result in eligibility.items() if result.eligible}
        size = gate.context.main_deck_size
        lock_deck = Counter(constraints.locked_card_ids)
        initial_ids = {card.card_id for card in pool.candidates}
        capacity = self._capacity(initial_ids | locked, owned, eligibility, gate)
        candidate_slots = capacity.remaining({})
        all_slots = self._capacity(eligible_ids, owned, eligibility, gate).remaining({})
        assert candidate_slots is not None and all_slots is not None

        active = initial_ids | locked
        interval = capacity.land_interval(lock_deck, size)
        if interval is None or interval[1] < self.config.land_target:
            active = active | {card_id for card_id in eligible_ids if is_front_land(owned[card_id])}
            capacity = self._capacity(active, owned, eligibility, gate)
            interval = capacity.land_interval(lock_deck, size)
        land_fallback = active - cards.keys()
        for card_id in sorted(land_fallback):
            cards[card_id] = make_card_candidate(gate, owned[card_id], eligibility[card_id], None, False)
        expanded = self._capacity(set(cards), owned, eligibility, gate)
        reserve_slots = expanded.remaining({})
        assert reserve_slots is not None
        if interval is None or not interval[0] <= self.config.land_target <= interval[1]:
            expanded_interval = expanded.land_interval(lock_deck, size)
            if expanded_interval is not None and (
                interval is None or self._land_distance(expanded_interval) < self._land_distance(interval)
            ):
                active, capacity, interval = set(cards), expanded, expanded_interval
        land_count = self._land_count(interval) if interval is not None else None
        diagnostics = CompositionCapacity(
            size, candidate_slots.total, reserve_slots.total, all_slots.total, self.config.land_target, land_count,
        )
        if land_count is None:
            if sum(lock_deck.values()) > size or expanded.remaining(lock_deck) is None:
                issue = ValidationIssue("conflicting_constraints", "The locked cards exceed deck slots or shared copy limits.")
            elif all_slots.total < size:
                issue = ValidationIssue("insufficient_owned_legal_cards", f"Need {size} main-deck copies; own {all_slots.total} legal copies.")
            else:
                issue = ValidationIssue("insufficient_legal_pool_capacity", f"Need {size} main-deck copies; the pool and reserve allow {reserve_slots.total} before locks.")
            return _failure(
                gate, (issue,), provenance=provenance, capacity=diagnostics,
                reserve=tuple(sorted(cards)), card_decisions=_reserve_decisions(cards, pool),
            )

        mana = ManaBase(
            {card_id: owned[card_id] for card_id in sorted(cards.keys() | set(gate.context.commander_card_ids))},
            gate.context.commander_card_ids, gate.color_identity,
        )
        utility = CompositionUtility(self.config, cards, mana, edges, packages.packages)
        return PreparedComposition(
            gate, pool, packages, edges, constraints, cards, capacity, diagnostics, active, land_fallback,
            provenance, utility, land_count,
        )

    @staticmethod
    def _capacity(
        card_ids: set[str], cards: dict[str, CardSnapshot],
        eligibility: dict[str, CardEligibility], gate: EligibilityGate,
    ) -> OwnedCapacity:
        return OwnedCapacity({card_id: cards[card_id] for card_id in sorted(card_ids)}, eligibility, gate.commanders)

    def _land_count(self, interval: tuple[int, int]) -> int:
        low, high = interval
        return min(max(self.config.land_target, low), high)

    def _land_distance(self, interval: tuple[int, int]) -> int:
        return abs(self.config.land_target - self._land_count(interval))

    @staticmethod
    def _coverage(deck: Counter[str], cards: dict[str, CardCandidate], result: DeckUtility) -> Counter[Role]:
        coverage = Counter({role: 0 for role in Role})
        for card_id in deck:
            coverage.update(role for role in _roles(cards[card_id]) if role is not Role.LANDS)
        coverage[Role.LANDS] = result.mana.land_count
        return coverage

    def describe(
        self, prepared: PreparedComposition, selection: DeckSelection, *,
        entries: tuple[DeckEntry, ...] | None = None, command_zone: tuple[DeckEntry, ...] | None = None,
        constraints: PoolConstraints | None = None,
    ) -> DeckProposal:
        gate, pool, packages = prepared.gate, prepared.pool, prepared.packages
        constraints = constraints if constraints is not None else prepared.constraints
        cards = prepared.cards
        excluded = set(constraints.exclude_card_ids)
        deck = selection.deck
        selected = set(deck)
        reserves = tuple(sorted(cards.keys() - selected - excluded))
        assessments = _package_assessments(packages, deck, selection.utility)
        ready_packages = tuple(item for item in assessments if item.package_id is not None and not item.missing_card_ids)
        present = selected | set(gate.context.commander_card_ids)
        supported_edges = tuple(
            edge for edge in prepared.edges if {edge.source_card_id, edge.target_card_id} <= present
        )
        decisions = []
        decision_log = list(selection.log)
        for card_id, card in sorted(cards.items()):
            if card_id in excluded:
                decisions.append(CompositionCardDecision(
                    card_id, CandidateStatus.EXCLUDED, 0, (), ("user_excluded",), name=card.name,
                ))
                continue
            status = CandidateStatus.SELECTED if card_id in deck else CandidateStatus.RESERVE
            reasons = set(selection.reasons[card_id])
            if status is CandidateStatus.RESERVE:
                reserve_reason = (
                    "not_selected" if card_id in prepared.active or selection.source == "deck_refinement"
                    else "reserve_fallback_not_needed"
                )
                reasons.add(reserve_reason)
                reasons.update(reason.code for reason in card.selection_reasons)
            elif card_id in prepared.land_fallback:
                reasons.add("owned_land_fallback")
            elif card.status is CandidateStatus.RESERVE:
                reasons.add("owned_reserve_fallback")
            if card_id in deck and card.evaluation_completeness != "complete":
                reasons.add("limited_evaluation")
            if card_id in deck:
                deltas = selection.deltas[card_id]
                if any(deltas[role.value] > 0 for role in _roles(card)):
                    reasons.add("role_coverage")
                if deltas["mana_sources"] > 0:
                    reasons.add("mana_sources")
                if deltas["joint_synergy"] > 0:
                    reasons.add("joint_synergy")
            if status is CandidateStatus.RESERVE:
                decision_log.append(DecisionRecord(
                    len(decision_log) + 1, card_id, selection.source, selection.statuses[card_id],
                    status, SelectionReason(reserve_reason), True,
                ))
            evidence = {event for profile in card.role_profiles if profile.qualifies for event in profile.evidence_ids}
            evidence.update(event for reason in card.selection_reasons for event in reason.evidence_ids)
            card_edges = tuple(edge.edge_id for edge in supported_edges if card_id in (edge.source_card_id, edge.target_card_id))
            card_packages = tuple(item.package_id for item in ready_packages if card_id in item.member_card_ids)
            decisions.append(CompositionCardDecision(
                card_id, status, deck[card_id], _roles(card), tuple(sorted(reasons)), tuple(sorted(evidence)),
                card_edges, card_packages,
                tuple((term, round(delta, 6)) for term, delta in sorted(selection.deltas[card_id].items()) if abs(delta) >= 0.0000005),
                tuple((role, round(credit * deck[card_id], 6)) for role, credit in selection.utility.role_credit[card_id])
                if card_id in deck else (),
                card.name, card.role_profiles if card_id in deck else (),
            ))
        decisions.extend(_exclusions(pool, cards.keys() | set(gate.context.commander_card_ids)))
        decisions.extend(
            CompositionCardDecision(
                commander.card_id, CandidateStatus.SELECTED, 1, (), ("commander",),
                edge_ids=tuple(edge.edge_id for edge in supported_edges if commander.card_id in edge.commander_anchor_ids),
                package_ids=tuple(item.package_id for item in ready_packages if commander.card_id in item.commander_anchor_ids),
                name=commander.name,
            )
            for commander in gate.commanders
        )
        result = selection.result
        roles = self._coverage(deck, cards, result)
        gaps = list(_shortfalls(self.config, roles, result))
        if packages.diagnostics.pairwise_work_limit_reached or packages.diagnostics.limited_templates:
            gaps.append(Diagnostic("upstream_search_limited", "The upstream synergy search reached a configured work limit."))
        if any(item.status == "conditional" and not item.modeled_requirements_supported for item in ready_packages):
            gaps.append(Diagnostic("unresolved_package_prerequisites", "Conditional packages retain their timing and resource requirements."))
        commanders = command_zone if command_zone is not None else tuple(
            gate.allocate_entry(card_id, 1) for card_id in gate.context.commander_card_ids
        )
        main_deck = entries if entries is not None else tuple(
            gate.allocate_entry(card_id, quantity) for card_id, quantity in sorted(deck.items())
        )
        proposal = DeckProposal(
            proposal_identity(gate, prepared.provenance, deck),
            gate.context.run_id, gate.context.commander_card_ids, main_deck, reserves,
            tuple(item.package_id for item in ready_packages), tuple(sorted(roles.items())), result.mana.curve,
            selection.utility.mana.diagnostics(deck, result.mana), decision_log=tuple(decision_log),
            command_zone=commanders, provenance=prepared.provenance, capacity=prepared.diagnostics,
            utility_breakdown=tuple((name, round(value, 6)) for name, value in result.components),
            card_decisions=tuple(sorted(decisions, key=lambda item: item.card_id)), package_assessments=assessments,
            mana_sources=result.mana.sources, unmet_objectives=tuple(gaps),
            supporting_edges=supported_edges,
        )
        validation = gate.validate_proposal(
            proposal, locked_card_ids=constraints.locked_card_ids,
            excluded_card_ids=constraints.exclude_card_ids, require_printings=True,
        )
        return replace(proposal, validation=validation, proposal_status="valid" if validation.is_legal else "infeasible")


def build_preliminary_artifacts(
    gate: EligibilityGate, catalog: SynergyCatalogContext, *, constraints: PoolConstraints = PoolConstraints(),
    pool_config: CandidatePoolConfig = CandidatePoolConfig(), pairwise_config: PairwiseConfig = PairwiseConfig(),
    package_config: PackageConfig = PackageConfig(),
) -> tuple[CandidatePool, PairwiseSynergyAnalysis, PackageAnalysis] | DeckProposal:
    if gate.violations:
        return _failure(gate, gate.violations)
    try:
        pool = CandidatePoolBuilder(pool_config, synergy_context=catalog).build(gate, constraints=constraints)
        pairwise = PairwiseSynergyAnalyzer(pairwise_config, synergy_context=catalog).analyze(gate, pool)
        packages = SynergyPackageBuilder(package_config, synergy_context=catalog).build(gate, pool, pairwise)
    except (PoolBuildError, EvaluationError, PairwiseAnalysisError, PackageBuildError) as error:
        return _failure(gate, error.violations)
    return pool, pairwise, packages


def run_preliminary_pipeline(
    gate: EligibilityGate, *, constraints: PoolConstraints = PoolConstraints(),
    config: CompositionConfig = CompositionConfig(), synergy_context: SynergyCatalogContext | None = None,
) -> DeckProposal:
    catalog = synergy_context or load_synergy_context()
    artifacts = build_preliminary_artifacts(gate, catalog, constraints=constraints)
    if isinstance(artifacts, DeckProposal):
        return artifacts
    return PreliminaryDeckComposer(config, synergy_context=catalog).compose(gate, *artifacts)
