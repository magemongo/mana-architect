"""Bounded, reversible one-card refinement of a validated Phase 4 proposal."""

from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from itertools import islice
from math import isfinite
from typing import Literal, TypeVar

from mana_architect.deck_pipeline.candidate_pool import PoolConstraints
from mana_architect.deck_pipeline.composition_constraints import pool_constraints
from mana_architect.deck_pipeline.composition_utility import CompositionConfig, DeckUtility
from mana_architect.deck_pipeline.contracts import (
    Artifact, CandidatePool, DeckProposal, PackageAnalysis, PairwiseSynergyAnalysis, Role, ValidationIssue,
    artifact_digest,
)
from mana_architect.deck_pipeline.eligibility import EligibilityGate
from mana_architect.deck_pipeline.preliminary_composer import (
    DeckSelection, PreliminaryDeckComposer, PreparedComposition, proposal_identity,
)
from mana_architect.deck_pipeline.refinement_diagnostics import RefinementDiagnostics, assess_deck
from mana_architect.strategy_synergy import SynergyCatalogContext


@dataclass(frozen=True)
class RefinementConfig:
    max_swaps: int = 8
    attempts_per_iteration: int = 2048
    max_attempts: int = 8192
    min_improvement: float = 0.000001

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in (
            self.max_swaps, self.attempts_per_iteration, self.max_attempts,
        )):
            raise ValueError("Refinement limits must be positive integers.")
        if isinstance(self.min_improvement, bool) or not isfinite(self.min_improvement) or self.min_improvement < 0:
            raise ValueError("Minimum improvement must be nonnegative and finite.")


@dataclass(frozen=True)
class SwapAttempt:
    iteration: int
    before_proposal_id: str
    outgoing_card_id: str
    incoming_card_id: str
    outcome: Literal["constraint_rejected", "visited_deck", "below_threshold", "passed_over", "accepted"]
    before_utility: float
    after_utility: float | None = None
    utility_delta: float | None = None
    component_deltas: tuple[tuple[str, float], ...] = ()
    violations: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True)
class AcceptedSwap:
    attempt_number: int
    before_proposal_id: str
    after_proposal_id: str
    changed_package_ids: tuple[str, ...]
    changed_edge_ids: tuple[str, ...]
    changed_mana_symbols: tuple[str, ...]
    changed_roles: tuple[Role, ...]


@dataclass(frozen=True)
class RefinementSnapshot:
    proposal: DeckProposal
    utility: float
    utility_components: tuple[tuple[str, float], ...]
    diagnostics: RefinementDiagnostics


@dataclass(frozen=True)
class RefinementResult(Artifact):
    status: Literal["improved", "unchanged", "invalid_input"]
    stop_reason: Literal[
        "invalid_input", "no_improving_legal_swap", "no_legal_swaps", "iteration_limit", "swap_budget_exhausted",
    ]
    refinement_version: str = "deck-refinement-v1"
    refinement_run_id: str | None = None
    config: RefinementConfig | None = None
    constraints: PoolConstraints | None = None
    preferred_card_ids: tuple[str, ...] = ()
    snapshots: tuple[RefinementSnapshot, ...] = ()
    attempts: tuple[SwapAttempt, ...] = ()
    accepted_swaps: tuple[AcceptedSwap, ...] = ()
    rediscovery_roles: tuple[Role, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def proposal(self) -> DeckProposal | None:
        return self.snapshots[-1].proposal if self.snapshots else None

    @classmethod
    def failure(cls, *issues: ValidationIssue) -> "RefinementResult":
        return cls("invalid_input", "invalid_input", issues=tuple(sorted(set(issues), key=lambda issue: (
            issue.code, issue.card_id or "", issue.printing_id or 0, issue.message,
        ))))

    def to_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["attempt_count"] = len(self.attempts)
        payload["scored_attempt_count"] = sum(attempt.after_utility is not None for attempt in self.attempts)
        for serialized, snapshot in zip(payload["snapshots"], self.snapshots, strict=True):
            serialized["proposal"]["validation"]["is_legal"] = snapshot.proposal.validation.is_legal
        payload["proposal"] = payload["snapshots"][-1]["proposal"] if self.snapshots else None
        return payload


def _remove(deck: Counter[str], card_id: str) -> Counter[str]:
    trial = deck.copy()
    trial[card_id] -= 1
    if trial[card_id] == 0:
        del trial[card_id]
    return trial


def _signature(deck: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(deck.items()))


def _ranked_pairs(outgoing: list[str], incoming: list[str]) -> Iterator[tuple[str, str]]:
    for rank in range(len(outgoing) + len(incoming) - 1):
        first = max(0, rank + 1 - len(incoming))
        last = min(rank + 1, len(outgoing))
        yield from sorted(
            (outgoing[index], incoming[rank - index])
            for index in range(first, last) if outgoing[index] != incoming[rank - index]
        )


_Item = TypeVar("_Item")
_Key = TypeVar("_Key", bound=str)


def _changed_keys(before: tuple[_Item, ...], after: tuple[_Item, ...], key: Callable[[_Item], _Key]) -> tuple[_Key, ...]:
    previous = {key(item): item for item in before}
    current = {key(item): item for item in after}
    return tuple(sorted(item for item in previous.keys() | current.keys() if previous.get(item) != current.get(item)))


def _accepted_swap(number: int, before: DeckProposal, after: DeckProposal) -> AcceptedSwap:
    previous_packages = tuple(
        (item.package_id, item.status, item.modeled_requirements_supported, item.missing_card_ids)
        for item in before.package_assessments if item.package_id is not None
    )
    current_packages = tuple(
        (item.package_id, item.status, item.modeled_requirements_supported, item.missing_card_ids)
        for item in after.package_assessments if item.package_id is not None
    )
    return AcceptedSwap(
        number, before.proposal_id, after.proposal_id,
        _changed_keys(previous_packages, current_packages, lambda item: item[0]),
        _changed_keys(before.supporting_edges, after.supporting_edges, lambda item: item.edge_id),
        _changed_keys(before.mana_sources, after.mana_sources, lambda item: item.symbol),
        _changed_keys(before.role_coverage, after.role_coverage, lambda item: item[0]),
    )


class DeckRefinementEngine:
    def __init__(
        self, config: RefinementConfig = RefinementConfig(), *, composition_config: CompositionConfig = CompositionConfig(),
        constraints: PoolConstraints = PoolConstraints(), preferred_card_ids: tuple[str, ...] = (),
        synergy_context: SynergyCatalogContext | None = None,
    ) -> None:
        if any(not isinstance(card_id, str) or not card_id.strip() for card_id in preferred_card_ids):
            raise ValueError("Preferences require nonempty Oracle IDs.")
        self.config = config
        self.composer = PreliminaryDeckComposer(composition_config, synergy_context=synergy_context)
        self.constraints = constraints
        self.preferred = frozenset(preferred_card_ids)

    def refine(
        self, gate: EligibilityGate, proposal: DeckProposal, pool: CandidatePool,
        pairwise: PairwiseSynergyAnalysis, packages: PackageAnalysis,
    ) -> RefinementResult:
        original = pool_constraints(pool)
        constraints = PoolConstraints(
            (*original.exclude_card_ids, *self.constraints.exclude_card_ids),
            (*original.locked_card_ids, *self.constraints.locked_card_ids),
        )
        report = gate.validate_proposal(
            proposal, locked_card_ids=constraints.locked_card_ids, excluded_card_ids=constraints.exclude_card_ids,
            require_printings=True,
        )
        if report.violations:
            return RefinementResult.failure(*report.violations)
        if proposal.proposal_status != "valid" or len({entry.card_id for entry in proposal.main_deck}) != len(proposal.main_deck):
            return RefinementResult.failure(ValidationIssue("invalid_starting_proposal", "Expected a valid, grouped Phase 4 deck."))

        prepared = self.composer.prepare(gate, pool, pairwise, packages)
        if isinstance(prepared, DeckProposal):
            return RefinementResult.failure(*prepared.validation.violations)
        if proposal.provenance != prepared.provenance or proposal.capacity != prepared.diagnostics:
            return RefinementResult.failure(ValidationIssue(
                "proposal_provenance_mismatch", "The proposal and supplied composition inputs differ.",
            ))
        deck = Counter({entry.card_id: entry.quantity for entry in proposal.main_deck})
        if not deck.keys() <= prepared.cards.keys() or proposal.proposal_id != proposal_identity(gate, prepared.provenance, deck):
            return RefinementResult.failure(ValidationIssue("invalid_starting_proposal", "The starting deck differs from its proposal identity or pool."))
        return self._search(prepared, proposal, deck, constraints)

    def _search(
        self, prepared: PreparedComposition, proposal: DeckProposal, deck: Counter[str], constraints: PoolConstraints,
    ) -> RefinementResult:
        selection = DeckSelection(
            prepared.utility, prepared.land_count, prepared.cards, starting_deck=deck, source="deck_refinement",
        )
        entries = {
            entry.card_id: replace(entry, printings=tuple(sorted(entry.printings, key=lambda row: row.printing_id)))
            for entry in proposal.main_deck
        }
        command_zone = tuple(
            replace(entry, printings=tuple(sorted(entry.printings, key=lambda row: row.printing_id)))
            for entry in sorted(proposal.command_zone, key=lambda entry: entry.card_id)
        )

        def describe() -> RefinementSnapshot:
            described = self.composer.describe(
                prepared, selection, entries=tuple(entries[key] for key in sorted(entries)),
                command_zone=command_zone, constraints=constraints,
            )
            return RefinementSnapshot(
                described, selection.result.total, selection.result.components, assess_deck(described, prepared),
            )

        snapshots = [describe()]
        visited = {_signature(deck)}
        attempts = []
        accepted = []
        locked, excluded = frozenset(constraints.locked_card_ids), frozenset(constraints.exclude_card_ids)
        names = {card.card_id: card.name.casefold() for card in prepared.gate.snapshot.cards}
        capacity = {card_id: card.available_quantity for card_id, card in prepared.cards.items()}
        limits = {card_id: prepared.gate.copy_limit(card_id) for card_id in prepared.cards}
        commander_card_ids = prepared.gate.context.commander_card_ids
        stop_reason = "iteration_limit"

        for iteration in range(1, self.config.max_swaps + 1):
            remaining = self.config.max_attempts - len(attempts)
            if remaining == 0:
                stop_reason = "swap_budget_exhausted"
                break
            current = snapshots[-1].proposal
            deck = selection.deck
            before = selection.result
            name_counts = Counter(names[card_id] for card_id in commander_card_ids)
            for card_id, quantity in deck.items():
                name_counts[names[card_id]] += quantity

            outgoing_decks = {card_id: _remove(deck, card_id) for card_id in deck}
            incoming = [
                card_id for card_id, owned in capacity.items()
                if deck[card_id] < min(owned, limits[card_id] if limits[card_id] is not None else owned)
            ]

            def utility(trial: Counter[str]) -> DeckUtility:
                return prepared.utility.evaluate(trial, prepared.land_count)

            outgoing = sorted(outgoing_decks, key=lambda card_id: (
                -round(utility(outgoing_decks[card_id]).total, 9), card_id,
            ))
            incoming.sort(key=lambda card_id: (-round(utility(deck + Counter({card_id: 1})).total, 9), card_id))
            work_limit = min(remaining, self.config.attempts_per_iteration)
            pairs = _ranked_pairs(outgoing, incoming)
            first_attempt = len(attempts)
            improvements = []

            for out_id, in_id in islice(pairs, work_limit):
                issue = self._swap_issue(out_id, in_id, deck, name_counts, names, limits, locked, excluded)
                attempt = SwapAttempt(iteration, current.proposal_id, out_id, in_id, "constraint_rejected", before.total)
                if issue is not None:
                    attempts.append(replace(attempt, violations=(issue,)))
                    continue
                trial = outgoing_decks[out_id].copy()
                trial[in_id] += 1
                if _signature(trial) in visited:
                    attempts.append(replace(attempt, outcome="visited_deck"))
                    continue
                after = utility(trial)
                delta = after.total - before.total
                improves = delta > self.config.min_improvement
                attempts.append(replace(
                    attempt, outcome="passed_over" if improves else "below_threshold",
                    after_utility=after.total, utility_delta=delta,
                    component_deltas=tuple((term, value) for term, value in after.delta(before) if value != 0),
                ))
                if improves:
                    preference_delta = int(in_id in self.preferred) - int(out_id in self.preferred)
                    improvements.append((-round(delta, 9), -preference_delta, out_id, in_id, len(attempts) - 1))

            truncated = next(pairs, None) is not None
            for _, _, out_id, in_id, attempt_index in sorted(improvements):
                trial = outgoing_decks[out_id].copy()
                trial[in_id] += 1
                trial_entries = dict(entries)
                for card_id in (out_id, in_id):
                    if trial[card_id]:
                        trial_entries[card_id] = prepared.gate.allocate_entry(card_id, trial[card_id])
                    else:
                        del trial_entries[card_id]
                trial_proposal = replace(current, main_deck=tuple(trial_entries.values()))
                report = prepared.gate.validate_proposal(
                    trial_proposal, locked_card_ids=constraints.locked_card_ids,
                    excluded_card_ids=constraints.exclude_card_ids, require_printings=True,
                )
                attempt = attempts[attempt_index]
                if not report.is_legal:
                    attempts[attempt_index] = replace(
                        attempt, outcome="constraint_rejected", after_utility=None, utility_delta=None,
                        component_deltas=(), violations=report.violations,
                    )
                    continue
                entries = trial_entries
                selection.commit(out_id, -1, "refinement_swap_out")
                selection.commit(in_id, 1, "refinement_swap_in")
                visited.add(_signature(selection.deck))
                attempts[attempt_index] = replace(attempt, outcome="accepted")
                snapshot = describe()
                snapshots.append(snapshot)
                accepted.append(_accepted_swap(attempt_index + 1, current, snapshot.proposal))
                break
            else:
                if truncated:
                    stop_reason = "swap_budget_exhausted"
                elif any(attempt.outcome != "constraint_rejected" for attempt in attempts[first_attempt:]):
                    stop_reason = "no_improving_legal_swap"
                else:
                    stop_reason = "no_legal_swaps"
                break

        preferred = tuple(sorted(self.preferred))
        starting = snapshots[0].proposal
        rediscovery = self._rediscovery(
            snapshots[-1].proposal, prepared, selection.deck, capacity, names, limits, locked, excluded,
        )
        return RefinementResult(
            "improved" if accepted else "unchanged", stop_reason,
            refinement_run_id=artifact_digest((
                "deck-refinement-v1", starting.proposal_id,
                [asdict(entry) for entry in (*starting.main_deck, *starting.command_zone)],
                asdict(self.config), asdict(constraints), preferred,
            )),
            config=self.config, constraints=constraints, preferred_card_ids=preferred, snapshots=tuple(snapshots),
            attempts=tuple(attempts), accepted_swaps=tuple(accepted), rediscovery_roles=rediscovery,
        )

    @staticmethod
    def _swap_issue(
        out_id: str, in_id: str, deck: Counter[str], name_counts: Counter[str], names: dict[str, str],
        limits: dict[str, int | None], locked: frozenset[str], excluded: frozenset[str],
    ) -> ValidationIssue | None:
        if out_id in locked and deck[out_id] <= 1:
            return ValidationIssue("missing_locked_card", "The swap would remove the last copy of a locked card.", out_id)
        if in_id in excluded:
            return ValidationIssue("user_excluded", "The incoming card is excluded.", in_id)
        limit = limits[in_id]
        if limit is not None and name_counts[names[in_id]] + int(names[out_id] != names[in_id]) > limit:
            return ValidationIssue("copy_limit", "The swap would exceed the English-name copy limit.", in_id)
        return None

    def _rediscovery(
        self, proposal: DeckProposal, prepared: PreparedComposition, deck: Counter[str], capacity: dict[str, int],
        names: dict[str, str], limits: dict[str, int | None], locked: frozenset[str], excluded: frozenset[str],
    ) -> tuple[Role, ...]:
        gaps = {gap.role for gap in proposal.unmet_objectives if gap.code == "role_target_shortfall" and gap.role is not None}
        names_in_deck = Counter(names[card_id] for card_id in proposal.commander_card_ids)
        for card_id, quantity in deck.items():
            names_in_deck[names[card_id]] += quantity
        for in_id in sorted(prepared.cards):
            roles = {profile.role for profile in prepared.cards[in_id].role_profiles if profile.qualifies}
            if roles & gaps and deck[in_id] < capacity[in_id] and any(
                out_id != in_id and self._swap_issue(
                    out_id, in_id, deck, names_in_deck, names, limits, locked, excluded,
                ) is None for out_id in deck
            ):
                gaps.difference_update(roles)
        return tuple(sorted(gaps))
