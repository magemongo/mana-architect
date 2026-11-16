"""Versioned inputs and evidence for collection playability analysis."""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal

from mana_architect.deck_pipeline.contracts import Artifact, EngineVersions, artifact_digest

Scope = Literal["collection", "global", "deck"]


class EvaluationStatus(StrEnum):
    EVALUATED = "evaluated"
    NOT_EVALUATED = "not_evaluated"
    UNSUPPORTED = "unsupported"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INCOMPATIBLE_CONTRACT = "incompatible_contract"
    EVALUATION_FAILED = "evaluation_failed"


class Classification(StrEnum):
    BROADLY_PLAYABLE = "broadly_playable"
    STRATEGY_PLAYABLE = "strategy_playable"
    NICHE_PLAYABLE = "niche_playable"
    LOW_PRIORITY = "low_priority"
    BULK_CANDIDATE = "bulk_candidate"
    UNCERTAIN = "uncertain"


class ComparisonOutcome(StrEnum):
    EXACT_EQUIVALENT = "exact_mechanical_equivalent"
    SUBSTITUTE = "functional_substitute"
    UPGRADE = "contextual_upgrade"
    DOMINANCE = "strict_or_pareto_dominance"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True)
class EvidenceReference:
    card_id: str
    source: str
    source_version: str
    record_id: str
    source_field: str
    start: int
    end: int
    text: str

    def __post_init__(self) -> None:
        if not all((self.card_id, self.source, self.source_version, self.record_id)):
            raise ValueError("Evidence requires a card, source, version, and record ID.")
        if self.source_field not in {"oracle_text", "type_line"}:
            raise ValueError("Unknown evidence source field.")
        if not (0 <= self.start < self.end and self.end - self.start == len(self.text)):
            raise ValueError("Evidence requires a nonempty source span.")

    @property
    def evidence_id(self) -> str:
        return artifact_digest(asdict(self))


@dataclass(frozen=True)
class Coverage:
    source: str
    status: EvaluationStatus
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source or self.status not in tuple(EvaluationStatus):
            raise ValueError("Coverage requires a source and a valid evaluation status.")
        for name in ("evidence_ids", "limitations"):
            object.__setattr__(self, name, tuple(sorted(set(getattr(self, name)))))


@dataclass(frozen=True)
class PlayabilityProvenance:
    collection_snapshot_id: str
    legality_data_as_of: str | None
    oracle_fingerprint: str | None
    parser_version: str
    capability_version: str
    catalog_fingerprint: str
    synergy_artifact_fingerprint: str | None = None
    reference_universe_version: str | None = None


@dataclass(frozen=True)
class ViableContext:
    strategy_id: str
    role_id: str
    viability: str
    evidence_ids: tuple[str, ...]
    requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.strategy_id, self.role_id, self.viability, self.evidence_ids)):
            raise ValueError("A viable context requires a strategy, role, viability, and evidence.")
        for name in ("evidence_ids", "requirements"):
            object.__setattr__(self, name, tuple(sorted(set(getattr(self, name)))))


@dataclass(frozen=True)
class UniquenessProfile:
    mechanical_rarity: str = "unknown"
    functional_replaceability: str = "unknown"
    strategic_irreplaceability: str = "unknown"
    collection_uniqueness: str = "unknown"
    global_uniqueness: str = "unknown"


@dataclass(frozen=True)
class PlayabilityContext(Artifact):
    collection_snapshot_id: str
    evaluator_config_version: str
    engine_versions: EngineVersions
    strategy_ids: tuple[str, ...] = ()
    scope: Scope = "collection"
    format: Literal["commander"] = "commander"
    commander_id: str | None = None
    reference_universe_version: str | None = None
    synergy_artifact_version: str | None = None
    synergy_artifact_fingerprint: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.collection_snapshot_id or not self.evaluator_config_version:
            raise ValueError("Snapshot and evaluator configuration versions are required.")
        if self.scope not in {"collection", "global", "deck"} or self.format != "commander":
            raise ValueError("Unsupported playability scope or format.")
        if self.scope == "global" and not self.reference_universe_version:
            raise ValueError("Global scope requires a reference universe version.")
        if self.scope == "deck" and not self.commander_id:
            raise ValueError("Deck scope requires a commander.")
        if any(not item for item in self.strategy_ids) or len(set(self.strategy_ids)) != len(self.strategy_ids):
            raise ValueError("Strategy IDs must be unique and nonempty.")
        object.__setattr__(self, "strategy_ids", tuple(sorted(self.strategy_ids)))

    @property
    def context_id(self) -> str:
        return artifact_digest(asdict(self))


@dataclass(frozen=True)
class ComparisonEvidence(Artifact):
    subject_card_id: str
    alternative_card_id: str
    scope: Scope
    functional_role: str
    shared_capabilities: tuple[str, ...]
    material_differences: tuple[str, ...]
    conditions: tuple[str, ...]
    outcome: ComparisonOutcome
    confidence: str
    evidence_ids: tuple[str, ...]
    benchmark_version: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.subject_card_id or not self.alternative_card_id or self.subject_card_id == self.alternative_card_id:
            raise ValueError("A comparison requires distinct card identities.")
        if self.scope not in {"global", "collection", "deck"} or not self.functional_role:
            raise ValueError("A comparison requires a valid scope and functional role.")
        if self.scope == "global" and not self.benchmark_version:
            raise ValueError("Global comparisons require a benchmark version.")
        if self.outcome not in tuple(ComparisonOutcome):
            raise ValueError("Unknown comparison outcome.")
        if self.outcome != ComparisonOutcome.NOT_COMPARABLE and not self.evidence_ids:
            raise ValueError("A comparison claim requires evidence.")
        if self.outcome in {ComparisonOutcome.UPGRADE, ComparisonOutcome.DOMINANCE} and not self.material_differences:
            raise ValueError("An upgrade or dominance claim requires material differences.")
        for name in ("shared_capabilities", "material_differences", "conditions", "evidence_ids"):
            object.__setattr__(self, name, tuple(sorted(set(getattr(self, name)))))


@dataclass(frozen=True)
class PlayabilityProfile(Artifact):
    card_id: str
    context: PlayabilityContext
    status: EvaluationStatus = EvaluationStatus.NOT_EVALUATED
    classification: Classification = Classification.UNCERTAIN
    dimensions: tuple[tuple[str, str], ...] = ()
    viable_contexts: tuple[ViableContext, ...] = ()
    uniqueness_profile: UniquenessProfile = UniquenessProfile()
    comparisons: tuple[ComparisonEvidence, ...] = ()
    preservation_flags: tuple[str, ...] = ()
    bulk_review_eligibility: bool = False
    confidence: str = "unknown"
    coverage: tuple[Coverage, ...] = ()
    provenance: PlayabilityProvenance | None = None
    evidence: tuple[EvidenceReference, ...] = ()
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.card_id:
            raise ValueError("A profile requires a card identity.")
        if self.status not in tuple(EvaluationStatus) or self.classification not in tuple(Classification):
            raise ValueError("Unknown profile status or classification.")
        if self.provenance and self.provenance.collection_snapshot_id != self.context.collection_snapshot_id:
            raise ValueError("Profile provenance must match its collection snapshot.")
        if self.status == EvaluationStatus.EVALUATED and self.provenance is None:
            raise ValueError("Evaluated profiles require provenance.")
        if self.status != EvaluationStatus.EVALUATED and self.classification != Classification.UNCERTAIN:
            raise ValueError("Incomplete evaluations require an uncertain classification.")
        if self.classification == Classification.BULK_CANDIDATE or self.bulk_review_eligibility:
            raise ValueError("Bulk classification and review require a playability classifier.")
        if self.uniqueness_profile.global_uniqueness != "unknown" and (
            not self.context.reference_universe_version
            or self.provenance is None
            or self.provenance.reference_universe_version != self.context.reference_universe_version
        ):
            raise ValueError("Global uniqueness requires matching reference universe provenance.")
        if any(item.subject_card_id != self.card_id or item.scope != self.context.scope
               for item in self.comparisons):
            raise ValueError("Comparisons must match the profile subject and scope.")
        evidence = {item.evidence_id: item for item in self.evidence}
        for item in (*self.coverage, *self.viable_contexts, *self.comparisons):
            if set(item.evidence_ids) - evidence.keys():
                raise ValueError("Profile contains an unresolved evidence reference.")
        for item in self.coverage:
            references = [evidence[key] for key in item.evidence_ids]
            if any(ref.source != item.source for ref in references):
                raise ValueError("Coverage evidence must come from its recorded source.")
            if item.source == "pairwise":
                subject_records = {
                    (ref.source_version, ref.record_id) for ref in references if ref.card_id == self.card_id
                }
                if any((ref.source_version, ref.record_id) not in subject_records for ref in references):
                    raise ValueError("Pairwise coverage evidence must include the profile card on each edge.")
            elif any(ref.card_id != self.card_id for ref in references):
                raise ValueError("Card-local coverage evidence must come from the profile card.")
        for comparison in self.comparisons:
            if comparison.scope == "global" and comparison.benchmark_version != self.context.reference_universe_version:
                raise ValueError("Global comparison must match the profile reference universe.")
            endpoints = {comparison.subject_card_id, comparison.alternative_card_id}
            cited_cards = {evidence[key].card_id for key in comparison.evidence_ids}
            if cited_cards - endpoints:
                raise ValueError("Comparison evidence must come from its endpoints.")
            if comparison.outcome != ComparisonOutcome.NOT_COMPARABLE and cited_cards != endpoints:
                raise ValueError("A comparison claim requires evidence from both endpoints.")
        object.__setattr__(self, "evidence", tuple(evidence[key] for key in sorted(evidence)))
        for name in ("dimensions", "preservation_flags", "reasons"):
            object.__setattr__(self, name, tuple(sorted(set(getattr(self, name)))))
        for name in ("coverage", "viable_contexts", "comparisons"):
            object.__setattr__(self, name, tuple(sorted(getattr(self, name), key=lambda item: artifact_digest(asdict(item)))))

    def to_json(self) -> dict[str, object]:
        return asdict(self)

    @property
    def profile_id(self) -> str:
        return artifact_digest(self.to_json())
