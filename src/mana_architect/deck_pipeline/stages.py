"""Signatures for the later stages of the deck-building pipeline."""

from typing import Protocol

from mana_architect.deck_pipeline.adapters import EvaluationAdapters
from mana_architect.deck_pipeline.candidate_pool import EvaluationSnapshot, PoolConstraints
from mana_architect.deck_pipeline.contracts import (
    CandidatePool,
    DeckProposal,
    PackageAnalysis,
    PairwiseSynergyAnalysis,
    PairwiseSynergyEdge,
    SynergyPackage,
)
from mana_architect.deck_pipeline.eligibility import EligibilityGate


class CandidatePoolBuilder(Protocol):
    def build(
        self, gate: EligibilityGate, evaluators: EvaluationAdapters | None = None,
        *, evaluation_snapshot: EvaluationSnapshot | None = None,
        constraints: PoolConstraints = PoolConstraints(),
    ) -> CandidatePool: ...


class PairwiseSynergyAnalyzer(Protocol):
    def analyze(
        self, gate: EligibilityGate, pool: CandidatePool,
    ) -> PairwiseSynergyAnalysis: ...


class SynergyPackageBuilder(Protocol):
    def build(
        self, gate: EligibilityGate, pool: CandidatePool,
        analysis: PairwiseSynergyAnalysis,
    ) -> PackageAnalysis: ...


class PreliminaryDeckComposer(Protocol):
    def compose(
        self, gate: EligibilityGate, pool: CandidatePool,
        packages: tuple[SynergyPackage, ...],
    ) -> DeckProposal: ...


class DeckRefinementEngine(Protocol):
    def refine(
        self, gate: EligibilityGate, pool: CandidatePool, edges: tuple[PairwiseSynergyEdge, ...],
        packages: tuple[SynergyPackage, ...], proposal: DeckProposal,
    ) -> DeckProposal: ...
