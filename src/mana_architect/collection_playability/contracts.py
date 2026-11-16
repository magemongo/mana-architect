"""Versioned inputs and evidence for collection playability analysis."""

from dataclasses import asdict, dataclass, field
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
    extraction_boundary_id: str | None = None
    extraction_parser_version: str | None = None

    def __post_init__(self) -> None:
        if not all((self.card_id, self.source, self.source_version, self.record_id)):
            raise ValueError("Evidence requires a card, source, version, and record ID.")
        if self.source_field not in {"oracle_text", "type_line"}:
            raise ValueError("Unknown evidence source field.")
        if not (0 <= self.start < self.end and self.end - self.start == len(self.text)):
            raise ValueError("Evidence requires a nonempty source span.")
        if bool(self.extraction_boundary_id) != bool(self.extraction_parser_version):
            raise ValueError("Scoped evidence requires a boundary ID and parser version.")
        if self.extraction_boundary_id and self.source_field != "oracle_text":
            raise ValueError("Scoped extraction requires Oracle text evidence.")

    @property
    def evidence_id(self) -> str:
        payload = asdict(self)
        if self.extraction_boundary_id is None:
            del payload["extraction_boundary_id"]
            del payload["extraction_parser_version"]
        return artifact_digest(payload)


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


class SemanticCoverageState(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CardDependency:
    source_boundary_id: str
    affected_boundary_ids: tuple[str, ...]
    category: str
    source_start: int
    source_end: int
    source_text: str
    status: Literal["independent", "potentially_affected", "unknown"]
    reasons: tuple[str, ...]
    confidence: Literal["unchanged", "low"]
    timing: Literal["static", "conditional", "unknown"] = "unknown"
    face_relationship: Literal["same_face", "cross_face", "mixed"] = "same_face"

    def __post_init__(self) -> None:
        if self.status not in {"independent", "potentially_affected", "unknown"}:
            raise ValueError("Unknown dependency status.")
        if self.timing not in {"static", "conditional", "unknown"} or self.face_relationship not in {"same_face", "cross_face", "mixed"}:
            raise ValueError("Unknown dependency timing or face relationship.")
        if self.source_end <= self.source_start or len(self.source_text) != self.source_end - self.source_start:
            raise ValueError("Dependency requires an exact source span.")
        object.__setattr__(self, "affected_boundary_ids", tuple(sorted(set(self.affected_boundary_ids))))


@dataclass(frozen=True)
class BoundaryCoverage:
    card_id: str
    face_index: int
    boundary_id: str
    source_start: int
    source_end: int
    source_text: str
    state: SemanticCoverageState
    recognized_evidence_ids: tuple[str, ...] = ()
    unresolved_spans: tuple[tuple[int, int], ...] = ()
    activation_cost: str | None = None
    targeting: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    verifier_version: str = "cpa-boundary-coverage-v1"

    def __post_init__(self) -> None:
        if not self.card_id or not self.boundary_id or self.face_index < 0:
            raise ValueError("Boundary coverage requires card and boundary identity.")
        if self.source_start < 0 or self.source_end <= self.source_start or len(self.source_text) != self.source_end - self.source_start:
            raise ValueError("Boundary coverage requires an exact source span.")
        if self.state not in tuple(SemanticCoverageState):
            raise ValueError("Unknown semantic coverage state.")
        if self.state == SemanticCoverageState.VERIFIED and (not self.recognized_evidence_ids or self.reasons):
            raise ValueError("Verified semantic coverage requires evidence and no unresolved reasons.")
        for start, end in self.unresolved_spans:
            if start < self.source_start or end > self.source_end or start >= end:
                raise ValueError("Unresolved spans must lie inside the boundary.")
        for name in ("recognized_evidence_ids", "unresolved_spans", "targeting", "conditions", "reasons"):
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
class MechanicalUse:
    role_id: str
    evidence_ids: tuple[str, ...]
    applicability: Literal["general", "conditional", "narrow", "unknown"]
    target_coverage: Literal["not_targeted", "typed", "restricted", "unknown"]
    requirements: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()
    timing: str = "unknown"
    execution_unit_id: str | None = None
    safety: Literal["supported", "conditional", "unsupported"] = field(init=False)

    def __post_init__(self) -> None:
        if not self.role_id or not self.evidence_ids:
            raise ValueError("A mechanical use requires a role and evidence.")
        if self.applicability not in {"general", "conditional", "narrow", "unknown"}:
            raise ValueError("Unknown mechanical applicability.")
        if self.target_coverage not in {"not_targeted", "typed", "restricted", "unknown"}:
            raise ValueError("Unknown mechanical target coverage.")
        if not all(self.evidence_ids) or not self.timing:
            raise ValueError("A mechanical use requires nonempty evidence IDs and timing.")
        for name in ("evidence_ids", "requirements", "restrictions"):
            object.__setattr__(self, name, tuple(sorted(set(getattr(self, name)))))
        safety = "unsupported"
        if self.execution_unit_id and self.applicability != "unknown":
            safety = "conditional" if self.requirements or self.restrictions else "supported"
        object.__setattr__(self, "safety", safety)


@dataclass(frozen=True)
class ExecutionUnit:
    unit_id: str
    card_id: str
    face_index: int
    kind: str
    ability_id: str
    evidence_ids: tuple[str, ...]
    mode_ids: tuple[str, ...] = ()
    minimum_modes: int | None = None
    maximum_modes: int | None = None
    requirements: tuple[str, ...] = ()
    target_evidence_ids: tuple[str, ...] = ()
    activation_cost: str | None = None
    timing: str = "unknown"
    status: EvaluationStatus = EvaluationStatus.EVALUATED

    def __post_init__(self) -> None:
        if not all((self.unit_id, self.card_id, self.kind, self.ability_id)):
            raise ValueError("Execution units require identity and kind.")
        if self.face_index < 0 or self.kind not in {"spell", "activated", "triggered", "unknown"}:
            raise ValueError("Execution units require a valid face and kind.")
        if self.status not in tuple(EvaluationStatus):
            raise ValueError("Execution units require a valid evaluation status.")
        if (self.minimum_modes is None) != (self.maximum_modes is None):
            raise ValueError("Mode selection requires both bounds.")
        if self.minimum_modes is not None and (
            self.minimum_modes < 0 or self.maximum_modes < self.minimum_modes
            or not self.minimum_modes <= len(self.mode_ids) <= self.maximum_modes
        ):
            raise ValueError("Selected modes must satisfy their bounds.")
        for name in ("evidence_ids", "mode_ids", "requirements", "target_evidence_ids"):
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
    mechanical_uses: tuple[MechanicalUse, ...] = ()
    execution_units: tuple[ExecutionUnit, ...] = ()
    boundary_coverage: tuple[BoundaryCoverage, ...] = ()
    card_dependencies: tuple[CardDependency, ...] = ()

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
        boundaries = {item.boundary_id: item for item in self.boundary_coverage}
        if boundaries:
            for ref in evidence.values():
                if ref.extraction_boundary_id is None:
                    continue
                boundary = boundaries.get(ref.extraction_boundary_id)
                if (boundary is None or ref.card_id != boundary.card_id
                    or not boundary.source_start <= ref.start < ref.end <= boundary.source_end
                    or self.provenance is None or ref.extraction_parser_version != self.provenance.parser_version):
                    raise ValueError("Scoped evidence must match its Oracle boundary and parser version.")
        for item in self.boundary_coverage:
            if item.card_id != self.card_id or set(item.recognized_evidence_ids) - evidence.keys():
                raise ValueError("Boundary coverage evidence must resolve for the profile card.")
        for item in (*self.coverage, *self.viable_contexts, *self.comparisons, *self.mechanical_uses):
            if set(item.evidence_ids) - evidence.keys():
                raise ValueError("Profile contains an unresolved evidence reference.")
        for use in self.mechanical_uses:
            if any(evidence[key].card_id != self.card_id for key in use.evidence_ids):
                raise ValueError("Mechanical use evidence must belong to the profile card.")
        unit_ids = {unit.unit_id for unit in self.execution_units}
        if len(unit_ids) != len(self.execution_units):
            raise ValueError("Execution unit IDs must be unique.")
        for unit in self.execution_units:
            if unit.card_id != self.card_id or set(unit.evidence_ids) - evidence.keys():
                raise ValueError("Execution unit evidence must resolve for the profile card.")
            if any(evidence[key].card_id != self.card_id for key in unit.evidence_ids):
                raise ValueError("Execution unit evidence must belong to the profile card.")
            if set(unit.target_evidence_ids) - set(unit.evidence_ids):
                raise ValueError("Execution unit target evidence must belong to the unit.")
        if any(use.execution_unit_id and use.execution_unit_id not in unit_ids for use in self.mechanical_uses):
            raise ValueError("Mechanical use references an unknown execution unit.")
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
        for name in ("coverage", "viable_contexts", "comparisons", "mechanical_uses", "execution_units", "boundary_coverage", "card_dependencies"):
            object.__setattr__(self, name, tuple(sorted(getattr(self, name), key=lambda item: artifact_digest(asdict(item)))))

    def to_json(self) -> dict[str, object]:
        return asdict(self)

    @property
    def profile_id(self) -> str:
        return artifact_digest(self.to_json())
