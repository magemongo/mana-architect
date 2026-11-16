"""Versioned, immutable in-memory artifacts shared by deck-building stages."""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
from typing import Literal

from mana_architect.card_advantage import Profile as CardAdvantageProfile
from mana_architect.interaction_profile import InteractionProfile
from mana_architect.ramp_evaluator import RampProfile
from mana_architect.strategy_synergy import StrategySynergyProfile

SCHEMA_VERSION = "1.0"
COMMANDER_DECK_SIZE = 100


def artifact_digest(data: object) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, kw_only=True)
class Artifact:
    schema_version: Literal["1.0"] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported deck pipeline schema: {self.schema_version!r}.")


@dataclass(frozen=True)
class EngineVersions:
    ramp: str
    card_advantage: str
    interaction: str
    strategy_synergy: str


@dataclass(frozen=True)
class StrategyPreference:
    strategy_id: str
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.strategy_id or isinstance(self.weight, bool) or not (
            isfinite(self.weight) and self.weight >= 0
        ):
            raise ValueError("A strategy preference requires an ID and a finite, nonnegative weight.")


@dataclass(frozen=True)
class DeckBuildingContext(Artifact):
    run_id: str
    commander_card_ids: tuple[str, ...]
    collection_snapshot_id: str
    ruleset_version: str
    engine_versions: EngineVersions
    config_version: str
    strategy_preferences: tuple[StrategyPreference, ...] = ()
    format: Literal["commander"] = "commander"
    random_seed: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not all((self.run_id, self.collection_snapshot_id, self.ruleset_version, self.config_version)):
            raise ValueError("A deck-building context requires run, snapshot, ruleset and config IDs.")
        preferences = tuple(sorted(self.strategy_preferences, key=lambda item: item.strategy_id))
        if len({item.strategy_id for item in preferences}) != len(preferences):
            raise ValueError("Strategy preference IDs must be unique.")
        object.__setattr__(self, "strategy_preferences", preferences)

    @property
    def main_deck_size(self) -> int:
        return COMMANDER_DECK_SIZE - len(self.commander_card_ids)


class Role(StrEnum):
    RAMP = "ramp"
    CARD_ADVANTAGE = "card_advantage"
    SPOT_INTERACTION = "spot_interaction"
    MASS_INTERACTION = "mass_interaction"
    STRATEGY_SYNERGY = "strategy_synergy"
    LANDS = "lands"


class CandidateStatus(StrEnum):
    ELIGIBLE = "eligible"
    CANDIDATE = "candidate"
    SHORTLISTED = "shortlisted"
    SELECTED = "selected"
    RESERVE = "reserve"
    EXCLUDED = "excluded"


class EvaluationState(StrEnum):
    EVALUATED = "evaluated"
    NOT_EVALUATED = "not_evaluated"
    UNSUPPORTED = "unsupported"
    UNSCORED = "unscored"


@dataclass(frozen=True)
class RoleMembership:
    role: Role
    qualifies: bool
    evaluation_state: EvaluationState
    score: float | None = None
    score_scale: str | None = None
    quality: float | None = None
    subtypes: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    confidence: str | None = None

    def __post_init__(self) -> None:
        if self.qualifies and not self.evidence_ids:
            raise ValueError("A qualifying role membership requires evidence.")
        if self.score is not None and (
            not isfinite(self.score) or self.score_scale is None or self.quality is None
        ):
            raise ValueError("A raw role score requires a known scale and calibrated quality.")
        if self.quality is not None and (self.score is None or not 0 <= self.quality <= 1):
            raise ValueError("Calibrated role quality requires a raw score and must be in [0, 1].")


@dataclass(frozen=True)
class SelectionReason:
    code: str
    role: Role | None = None
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionRecord(Artifact):
    sequence: int
    card_id: str
    source_module: str
    previous_status: CandidateStatus | None
    new_status: CandidateStatus
    reason: SelectionReason
    reversible: bool


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    card_id: str | None = None
    printing_id: int | None = None


@dataclass(frozen=True)
class ValidationReport(Artifact):
    violations: tuple[ValidationIssue, ...]
    legality_data_as_of: str | None

    @property
    def is_legal(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class CandidateProvenance:
    collection_snapshot_id: str
    engine_versions: EngineVersions


@dataclass(frozen=True)
class CardEvaluations(Artifact):
    """Native profiles keep their individual score scales, variants and evidence."""

    ramp: RampProfile | None
    card_advantage: CardAdvantageProfile | None
    interaction: InteractionProfile | None
    strategy_synergy: StrategySynergyProfile | None

    def __post_init__(self) -> None:
        super().__post_init__()
        ids = {profile.oracle_id for profile in (self.ramp, self.card_advantage) if profile is not None}
        ids.update(profile.card_id for profile in (self.interaction, self.strategy_synergy) if profile is not None)
        if len(ids) != 1:
            raise ValueError("Evaluation bundles require at least one profile for a single card.")

    @property
    def card_id(self) -> str:
        if self.ramp is not None:
            return self.ramp.oracle_id
        if self.card_advantage is not None:
            return self.card_advantage.oracle_id
        if self.interaction is not None:
            return self.interaction.card_id
        assert self.strategy_synergy is not None
        return self.strategy_synergy.card_id


@dataclass(frozen=True)
class CardCandidate(Artifact):
    card_id: str
    owned_printing_ids: tuple[int, ...]
    available_quantity: int
    evaluations: CardEvaluations | None
    provenance: CandidateProvenance
    roles: tuple[Role, ...] = ()
    mechanical_subtypes: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    selection_reasons: tuple[SelectionReason, ...] = ()
    status: CandidateStatus = CandidateStatus.CANDIDATE
    role_profiles: tuple[RoleMembership, ...] = ()
    locked: bool = False
    copy_limit: int | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.evaluations is not None and self.evaluations.card_id != self.card_id:
            raise ValueError("Candidate and evaluation card IDs must match.")
        if (
            not self.owned_printing_ids
            or len(set(self.owned_printing_ids)) != len(self.owned_printing_ids)
            or type(self.available_quantity) is not int
            or self.available_quantity < len(self.owned_printing_ids)
        ):
            raise ValueError("A candidate requires unique owned printings and positive inventory.")
        object.__setattr__(self, "roles", tuple(sorted(set(self.roles))))
        object.__setattr__(self, "owned_printing_ids", tuple(sorted(self.owned_printing_ids)))
        if len({item.role for item in self.role_profiles}) != len(self.role_profiles):
            raise ValueError("A candidate may record each role profile once.")
        object.__setattr__(self, "role_profiles", tuple(sorted(self.role_profiles, key=lambda item: item.role)))

    @property
    def evaluation_completeness(self) -> Literal["complete", "partial", "not_evaluated"]:
        if self.evaluations is None:
            return "not_evaluated"
        if any(item.evaluation_state is not EvaluationState.EVALUATED for item in self.role_profiles):
            return "partial"
        return "complete"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    role: Role | None = None
    value: int | float | None = None
    card_id: str | None = None


@dataclass(frozen=True)
class RoleCoverage:
    role: Role
    soft_target: int
    qualified_available: int
    selected_count: int
    shortfall: int
    subtype_counts: tuple[tuple[str, int], ...] = ()
    available_subtypes: tuple[str, ...] = ()
    not_evaluated: int = 0
    unsupported: int = 0
    unscored: int = 0
    # Land targets can be measured in playable copies. Membership counts always count identities.
    qualified_copies: int | None = None
    selected_copies: int | None = None


@dataclass(frozen=True)
class PoolProvenance:
    context: DeckBuildingContext
    evaluation_snapshot_id: str
    config_fingerprint: str
    excluded_card_ids: tuple[str, ...]
    locked_card_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidatePool(Artifact):
    run_id: str
    candidates: tuple[CardCandidate, ...]
    reserve: tuple[CardCandidate, ...] = ()
    excluded: tuple[ValidationIssue, ...] = ()
    coverage_diagnostics: tuple[Diagnostic, ...] = ()
    diversity_diagnostics: tuple[Diagnostic, ...] = ()
    pool_id: str | None = None
    provenance: PoolProvenance | None = None
    role_coverage: tuple[RoleCoverage, ...] = ()
    eligible_count: int = 0
    warnings: tuple[Diagnostic, ...] = ()
    decision_log: tuple[DecisionRecord, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        ids = [card.card_id for card in (*self.candidates, *self.reserve)]
        if len(set(ids)) != len(ids):
            raise ValueError("Card identities must be unique across candidates and reserve.")
        for section in ("candidates", "reserve"):
            object.__setattr__(self, section, tuple(
                sorted(getattr(self, section), key=lambda card: card.card_id)
            ))

    @property
    def role_memberships(self) -> dict[Role, tuple[str, ...]]:
        return {
            role: tuple(card.card_id for card in self.candidates if role in card.roles)
            for role in Role
        }

    def to_json(self) -> dict[str, object]:
        """Return a stable payload for JSON serialization."""
        payload = asdict(self)
        for section in ("candidates", "reserve"):
            for card, data in zip(getattr(self, section), payload[section], strict=True):
                data["evaluation_completeness"] = card.evaluation_completeness
        payload["candidate_ids"] = tuple(card.card_id for card in self.candidates)
        payload["role_memberships"] = self.role_memberships
        payload["audit"] = {
            "eligible_count": self.eligible_count,
            "unique_candidate_count": len(self.candidates),
            "membership_count": sum(len(card.roles) for card in self.candidates),
            "reserve_count": len(self.reserve),
        }
        return payload


@dataclass(frozen=True)
class RuleEvidence:
    card_id: str
    ability_id: str
    rule_id: str
    source_field: Literal["oracle_text", "type_line"] = "oracle_text"
    span_start: int | None = None
    span_end: int | None = None
    source_text: str = ""

    def __post_init__(self) -> None:
        if not all((self.card_id, self.ability_id, self.rule_id)):
            raise ValueError("Rule evidence requires a card, ability and rule ID.")
        if self.span_start is not None or self.span_end is not None:
            if (
                self.span_start is None or self.span_end is None
                or self.span_start < 0 or self.span_end <= self.span_start
                or self.span_end - self.span_start != len(self.source_text)
            ):
                raise ValueError("Rule evidence offsets must locate a nonempty source span.")


@dataclass(frozen=True)
class PairwiseSynergyEdge(Artifact):
    edge_id: str
    source_card_id: str
    target_card_id: str
    relation: Literal["enables", "rewards", "amplifies", "converts"]
    source_capability: str
    target_capability: str
    evidence: tuple[RuleEvidence, ...]
    strength: float
    confidence: Literal["rule_verified", "conditional"]
    prerequisites: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    directional: bool = True
    event_ids: tuple[str, ...] = ()
    commander_anchor_ids: tuple[str, ...] = ()
    strategy_ids: tuple[str, ...] = ()
    resource_requirements: tuple[str, ...] = ()
    timing_constraints: tuple[str, ...] = ()
    rule_version: str = ""
    explanation: str = ""
    base_strength: float | None = None
    event_type: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        endpoints = {self.source_card_id, self.target_card_id}
        if len(endpoints) != 2 or not self.evidence or any(
            item.card_id not in endpoints for item in self.evidence
        ):
            raise ValueError("An edge requires two distinct cards and rule evidence from its endpoints.")
        if not 0 <= self.strength <= 1:
            raise ValueError("Edge strength must be between zero and one.")
        if self.confidence == "conditional" and not self.prerequisites:
            raise ValueError("Conditional edges require explicit prerequisites.")
        if set(self.commander_anchor_ids) - endpoints:
            raise ValueError("Commander anchors must be edge endpoints.")


@dataclass(frozen=True)
class RuleMatchCount:
    rule_id: str
    reason_code: str
    count: int
    detail: str = ""


@dataclass(frozen=True)
class PairwiseDiagnostics:
    considered_pairs: int
    candidate_rule_matches: int
    verified_edges: int
    conditional_edges: int
    matches_by_rule: tuple[RuleMatchCount, ...] = ()
    coverage: tuple[Diagnostic, ...] = ()
    work_limit_reached: bool = False


@dataclass(frozen=True)
class PairwiseProvenance:
    collection_snapshot_id: str
    pool_id: str | None
    pool_fingerprint: str
    evaluation_snapshot_id: str | None
    commander_card_ids: tuple[str, ...]
    oracle_parser_version: str
    capability_extractor_version: str
    strategy_catalog_version: str
    config_version: str
    config_fingerprint: str
    rule_catalog_fingerprint: str
    analyzer_version: str
    ruleset_version: str
    engine_versions: EngineVersions


@dataclass(frozen=True)
class PairwiseSynergyAnalysis(Artifact):
    analysis_id: str
    run_id: str
    rule_catalog_version: str
    edges: tuple[PairwiseSynergyEdge, ...]
    diagnostics: PairwiseDiagnostics
    provenance: PairwiseProvenance

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LoopVerification:
    """Evidence for a closed loop and its resource and timing checks."""

    closed_loop: bool
    resource_neutral_or_positive: bool
    timing_and_rules_satisfied: bool
    evidence: tuple[RuleEvidence, ...]


@dataclass(frozen=True)
class SynergyPackage(Artifact):
    package_id: str
    member_card_ids: tuple[str, ...]
    functional_roles: tuple[tuple[str, tuple[str, ...]], ...]
    edge_ids: tuple[str, ...]
    requirements: tuple[str, ...]
    repeatable: bool
    self_sustaining: bool
    explanation: str
    loop_verification: LoopVerification | None = None
    commander_anchor_ids: tuple[str, ...] = ()
    package_archetype: str = ""
    dependency_status: Literal["complete", "conditional"] = "complete"
    effect_ids: tuple[str, ...] = ()
    benefit_event_ids: tuple[str, ...] = ()
    evidence: tuple[RuleEvidence, ...] = ()
    resource_requirements: tuple[str, ...] = ()
    timing_constraints: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    strategy_ids: tuple[str, ...] = ()
    renewal_path: str | None = None
    score: float = 0.0
    score_components: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        members = set(self.member_card_ids)
        anchors = set(self.commander_anchor_ids)
        if (
            not members or len(members | anchors) < 2 or members & anchors
            or len(members) != len(self.member_card_ids) or len(anchors) != len(self.commander_anchor_ids)
        ):
            raise ValueError("A package requires distinct member cards.")
        if any(set(ids) - (members | anchors) for _, ids in self.functional_roles):
            raise ValueError("Package roles must reference members or commander anchors.")
        object.__setattr__(self, "member_card_ids", tuple(sorted(members)))
        object.__setattr__(self, "commander_anchor_ids", tuple(sorted(anchors)))
        object.__setattr__(self, "edge_ids", tuple(sorted(set(self.edge_ids))))

    @property
    def infinite(self) -> bool:
        proof = self.loop_verification
        return bool(
            self.repeatable and self.self_sustaining and proof
            and proof.closed_loop and proof.resource_neutral_or_positive
            and proof.timing_and_rules_satisfied and proof.evidence
        )


@dataclass(frozen=True)
class PartialPackage:
    package_archetype: str
    member_card_ids: tuple[str, ...]
    commander_anchor_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    missing_roles: tuple[str, ...]
    reason_code: str


@dataclass(frozen=True)
class PackageDiagnostics:
    eligible_edges: int
    attempted_paths: int
    complete_packages: int
    conditional_packages: int
    partial_packages: int
    rejected_by_reason: tuple[tuple[str, int], ...] = ()
    limited_templates: tuple[str, ...] = ()
    pairwise_work_limit_reached: bool = False


@dataclass(frozen=True)
class PackageProvenance:
    collection_snapshot_id: str
    pool_id: str | None
    pool_fingerprint: str
    pairwise_analysis_id: str
    edge_fingerprint: str
    rule_catalog_version: str
    template_catalog_version: str
    template_fingerprint: str
    config_version: str
    config_fingerprint: str


@dataclass(frozen=True)
class PackageAnalysis(Artifact):
    analysis_id: str
    run_id: str
    packages: tuple[SynergyPackage, ...]
    partial: tuple[PartialPackage, ...]
    diagnostics: PackageDiagnostics
    provenance: PackageProvenance

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        for package, serialized in zip(self.packages, data["packages"], strict=True):
            serialized["infinite"] = package.infinite
        return data


@dataclass(frozen=True)
class PrintingAllocation:
    printing_id: int
    quantity: int


@dataclass(frozen=True)
class DeckEntry:
    card_id: str
    quantity: int
    printings: tuple[PrintingAllocation, ...] = ()


@dataclass(frozen=True)
class DeckProposal(Artifact):
    proposal_id: str
    run_id: str
    commander_card_ids: tuple[str, ...]
    main_deck: tuple[DeckEntry, ...]
    reserve_card_ids: tuple[str, ...] = ()
    selected_package_ids: tuple[str, ...] = ()
    role_coverage: tuple[tuple[Role, int], ...] = ()
    mana_curve: tuple[tuple[float, int], ...] = ()
    mana_base_diagnostics: tuple[Diagnostic, ...] = ()
    validation: ValidationReport = field(default_factory=lambda: ValidationReport(
        (ValidationIssue("unvalidated_proposal", "Run proposal validation."),), None,
    ))
    decision_log: tuple[DecisionRecord, ...] = ()
