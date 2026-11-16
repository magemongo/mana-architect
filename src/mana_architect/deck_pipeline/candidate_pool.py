"""Build a broad, deterministic pool from eligible owned card identities."""

from collections import Counter
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from math import ceil

from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS, EvaluationAdapters
from mana_architect.deck_pipeline.contracts import (
    Artifact,
    CandidatePool,
    CandidateProvenance,
    CandidateStatus,
    CardCandidate,
    CardEvaluations,
    DeckBuildingContext,
    DecisionRecord,
    Diagnostic,
    EvaluationState,
    PoolProvenance,
    Role,
    RoleCoverage,
    RoleMembership,
    SelectionReason,
    StrategyPreference,
    ValidationIssue,
)
from mana_architect.deck_pipeline.eligibility import CardEligibility, EligibilityGate
from mana_architect.deck_pipeline.inventory import CardSnapshot
from mana_architect.deck_pipeline.role_mapping import map_role_memberships
from mana_architect.oracle_text import PARSER_VERSION
from mana_architect.strategy_synergy import (
    SelectedStrategy, SynergyCatalogContext, load_synergy_context, normalize_priorities,
)
from mana_architect.synergy_capabilities import CAPABILITY_VERSION
from mana_architect.synergy_interactions import MatchStatus


def _fingerprint(payload: object) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(serialized.encode("utf-8")).hexdigest()


class PoolBuildError(ValueError):
    def __init__(self, violations: tuple[ValidationIssue, ...]):
        self.violations = violations
        super().__init__(" ".join(issue.message for issue in violations))


@dataclass(frozen=True)
class PoolConstraints:
    exclude_card_ids: tuple[str, ...] = ()
    locked_card_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in ("exclude_card_ids", "locked_card_ids"):
            ids = getattr(self, field)
            if any(not isinstance(card_id, str) or not card_id.strip() for card_id in ids):
                raise ValueError("Pool constraints require nonempty Oracle IDs.")
            object.__setattr__(self, field, tuple(sorted(set(ids))))


@dataclass(frozen=True)
class RolePolicy:
    role: Role
    soft_target: int
    quality_floor: float | None = None
    max_candidates: int | None = None
    allow_unscored: bool = False

    def __post_init__(self) -> None:
        if type(self.soft_target) is not int or self.soft_target < 0:
            raise ValueError("Role soft targets must be nonnegative integers.")
        if self.max_candidates is not None and (
            type(self.max_candidates) is not int or self.max_candidates < 0
        ):
            raise ValueError("Role caps must be nonnegative integers.")
        if self.quality_floor is not None and not 0 <= self.quality_floor <= 1:
            raise ValueError("Role quality floors must be in [0, 1].")
        if self.role is Role.LANDS and self.quality_floor is not None:
            raise ValueError("Land inventory has no quality score.")


DEFAULT_POLICIES = (
    RolePolicy(Role.RAMP, 24, 0.45, 40),
    RolePolicy(Role.CARD_ADVANTAGE, 28, 0.45, 45),
    RolePolicy(Role.SPOT_INTERACTION, 18, 0.45, 35, allow_unscored=True),
    RolePolicy(Role.MASS_INTERACTION, 12, 0.45, 25, allow_unscored=True),
    RolePolicy(Role.STRATEGY_SYNERGY, 55, 0.40, 100),
    RolePolicy(Role.LANDS, 48),
)


@dataclass(frozen=True)
class CandidatePoolConfig(Artifact):
    version: str = "candidate-pool-v1"
    policies: tuple[RolePolicy, ...] = DEFAULT_POLICIES
    minimum_subtype_coverage: float = 0.6
    allow_diversity_overflow: bool = True
    commander_synergy_override: float | None = 0.8
    retain_unevaluated: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.version or len(self.policies) != len(Role) or {p.role for p in self.policies} != set(Role):
            raise ValueError("A versioned pool configuration requires exactly one policy per role.")
        if not 0 <= self.minimum_subtype_coverage <= 1:
            raise ValueError("Minimum subtype coverage must be in [0, 1].")
        if self.commander_synergy_override is not None and not 0 < self.commander_synergy_override <= 1:
            raise ValueError("The commander synergy override must be in (0, 1].")
        object.__setattr__(self, "policies", tuple(sorted(self.policies, key=lambda item: item.role)))


def _evaluation_inputs(context: DeckBuildingContext) -> DeckBuildingContext:
    return replace(context, run_id="evaluation", config_version="evaluation", random_seed=0)


@dataclass(frozen=True)
class EvaluationSnapshot(Artifact):
    """Bind precomputed native profiles to the inputs on which they depend."""

    context: DeckBuildingContext
    profiles: tuple[CardEvaluations, ...]
    strategy_preferences: tuple[StrategyPreference, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        ordered = tuple(sorted(self.profiles, key=lambda item: item.card_id))
        if len({profile.card_id for profile in ordered}) != len(ordered):
            raise ValueError("Evaluation snapshot card IDs must be unique.")
        preferences = tuple(sorted(self.strategy_preferences, key=lambda item: item.strategy_id))
        if len({item.strategy_id for item in preferences}) != len(preferences) or (
            preferences and abs(sum(item.weight for item in preferences) - 1) > 1e-9
        ):
            raise ValueError("Evaluation snapshots require normalized strategy preferences.")
        object.__setattr__(self, "profiles", ordered)
        object.__setattr__(self, "strategy_preferences", preferences)

    @property
    def snapshot_id(self) -> str:
        return _fingerprint({
            "schema_version": self.schema_version,
            "context": asdict(_evaluation_inputs(self.context)),
            "profiles": [asdict(item) for item in self.profiles],
            "strategy_preferences": [asdict(item) for item in self.strategy_preferences],
        })


@dataclass(frozen=True)
class _RoleChoice:
    card: CardCandidate
    membership: RoleMembership
    override: bool = False

    @property
    def commander_score(self) -> float:
        profiles = self.card.evaluations
        return profiles.strategy_synergy.commander_synergy.score if profiles and profiles.strategy_synergy else 0

    @property
    def quality_key(self) -> float:
        return self.membership.quality if self.membership.quality is not None else -1

    def evidence_ids(self, *, override: bool) -> tuple[str, ...]:
        if not override:
            return self.membership.evidence_ids
        assert self.card.evaluations is not None and self.card.evaluations.strategy_synergy is not None
        profile = self.card.evaluations.strategy_synergy
        commander_ids = set(profile.commander_synergy.evidence_ids)
        return tuple(sorted(set(self.membership.evidence_ids) | {
            item.id for item in profile.evidence if item.id in commander_ids and item.weight > 0
            and item.status in (MatchStatus.VALIDATED, MatchStatus.CONDITIONAL)
        }))


def _qualification(
    choice: _RoleChoice, policy: RolePolicy, config: CandidatePoolConfig,
) -> str:
    profile = choice.membership
    if not profile.qualifies:
        return "not_qualified"
    if profile.evaluation_state is EvaluationState.UNSCORED:
        return "unscored_fallback" if policy.allow_unscored else "unscored_role"
    if policy.quality_floor is None or (profile.quality is not None and profile.quality >= policy.quality_floor):
        return "role_qualified"
    if (
        config.commander_synergy_override is not None
        and choice.commander_score >= config.commander_synergy_override
    ):
        return "commander_synergy_override"
    return "low_role_score"


def _rank_key(choice: _RoleChoice) -> tuple[float, float, bool, str]:
    return (
        -choice.quality_key,
        -choice.commander_score,
        bool(choice.membership.conditions),
        choice.card.card_id,
    )


def _select_role(
    choices: list[_RoleChoice], policy: RolePolicy, config: CandidatePoolConfig,
) -> dict[str, str]:
    if policy.role is Role.LANDS and policy.max_candidates is None:
        return {choice.card.card_id: "land_inventory" for choice in choices}

    locked_choices = [choice for choice in choices if choice.card.locked]
    selected = {choice.card.card_id: "user_locked_override" for choice in locked_choices}
    selected_subtypes = Counter(subtype for choice in locked_choices for subtype in choice.membership.subtypes)
    maximum = policy.max_candidates if policy.max_candidates is not None else len(choices)
    target = maximum if policy.role is Role.LANDS else min(policy.soft_target, maximum)
    remaining = [choice for choice in choices if not choice.card.locked]
    diversity_target = ceil(config.minimum_subtype_coverage * len({
        subtype for choice in choices for subtype in choice.membership.subtypes
    }))

    def new_subtypes(choice: _RoleChoice) -> int:
        return sum(subtype not in selected_subtypes for subtype in choice.membership.subtypes)

    def admit(choice: _RoleChoice, reason: str) -> None:
        selected[choice.card.card_id] = reason
        selected_subtypes.update(choice.membership.subtypes)

    diversity_limit = maximum if config.allow_diversity_overflow else target
    while remaining and len(selected) < diversity_limit and len(selected_subtypes) < diversity_target:
        choice = min(remaining, key=lambda item: (-new_subtypes(item), *_rank_key(item)))
        if not new_subtypes(choice):
            break
        admit(choice, "subtype_diversity")
        remaining.remove(choice)

    # Once the coverage target is met, prefer quality and use novelty to break equal scores.
    def fill_rank(choice: _RoleChoice) -> tuple[float, float, float, bool, str]:
        novelty = sum(1 / (1 + selected_subtypes[item]) for item in choice.membership.subtypes)
        novelty /= max(1, len(choice.membership.subtypes))
        quality, commander, conditional, card_id = _rank_key(choice)
        return quality, -novelty, commander, conditional, card_id

    while remaining and len(selected) < target:
        choice = min(remaining, key=fill_rank)
        admit(choice, "role_quality")
        remaining.remove(choice)

    if config.commander_synergy_override is not None:
        for choice in sorted(remaining, key=lambda item: (-item.commander_score, *_rank_key(item))):
            if len(selected) >= maximum:
                break
            if choice.override:
                admit(choice, "commander_synergy_override")
    return selected


def _snapshot_issues(
    snapshot: EvaluationSnapshot, context: DeckBuildingContext,
    strategy_preferences: tuple[StrategyPreference, ...], catalog: SynergyCatalogContext,
) -> tuple[ValidationIssue, ...]:
    if _evaluation_inputs(snapshot.context) != _evaluation_inputs(context):
        return (ValidationIssue(
            "evaluation_snapshot_mismatch", "Evaluation snapshot has different collection, commander, rules, engines, or strategies.",
        ),)
    if snapshot.strategy_preferences != strategy_preferences:
        return (ValidationIssue(
            "evaluation_strategies_mismatch", "Evaluation snapshot has different normalized strategies.",
        ),)
    issues = []
    versions = context.engine_versions
    synergy_versions = {
        "oracle_parser": PARSER_VERSION,
        "capabilities": CAPABILITY_VERSION,
        "strategies": catalog.strategies.version,
        "interaction_rules": catalog.rule_version,
        "scoring": catalog.scoring.version,
    }
    for profile in snapshot.profiles:
        ramp, advantage, interaction, synergy = (
            profile.ramp, profile.card_advantage, profile.interaction, profile.strategy_synergy,
        )
        if (
            (ramp is not None and (ramp.ramp_rules_version != versions.ramp
                                  or ramp.oracle_parser_version != PARSER_VERSION))
            or (advantage is not None and (advantage.evaluator_version != versions.card_advantage
                                           or advantage.parser_version != PARSER_VERSION))
            or (
                interaction is not None
                and (interaction.oracle_parser_version != PARSER_VERSION
                     or interaction.rules_version != versions.interaction.partition("/")[0]
                     or (interaction.scoring_version is not None
                         and interaction.scoring_version != versions.interaction.partition("/")[2]))
            )
            or (
                synergy is not None
                and (synergy.engine_version != versions.strategy_synergy
                     or not synergy_versions.items() <= dict(synergy.catalog_versions).items()
                     or (synergy.commander_id,) != context.commander_card_ids or not synergy.eligible
                     or tuple(StrategyPreference(item.strategy_id, item.priority)
                              for item in synergy.strategy_affinities) != strategy_preferences)
            )
        ):
            issues.append(ValidationIssue(
                "incompatible_evaluation", "Evaluation profile uses incompatible rules, versions, or commander.",
                profile.card_id,
            ))
    return tuple(issues)


def _role_coverage(
    role: Role, policy: RolePolicy, cards: list[CardCandidate], qualified: list[_RoleChoice],
    selected: set[str],
) -> RoleCoverage:
    used = [choice for choice in qualified if choice.card.card_id in selected]
    subtype_counts = Counter(subtype for choice in used for subtype in choice.membership.subtypes)
    states = Counter(
        membership.evaluation_state
        for card in cards for membership in card.role_profiles if membership.role is role
    )
    qualified_copies = selected_copies = None
    if role is Role.LANDS:
        def copies(choices: list[_RoleChoice]) -> int:
            owned: Counter[str] = Counter()
            limits: dict[str, int] = {}
            for item in choices:
                card = item.card
                name = (card.name or card.card_id).casefold()
                owned[name] += card.available_quantity
                if card.copy_limit is not None:
                    limits[name] = min(limits.get(name, card.copy_limit), card.copy_limit)
            return sum(
                min(quantity, limits.get(name, quantity)) for name, quantity in owned.items()
            )

        qualified_copies, selected_copies = copies(qualified), copies(used)
    coverage = len(used) if selected_copies is None else selected_copies
    return RoleCoverage(
        role, policy.soft_target, len(qualified), len(used), max(0, policy.soft_target - coverage),
        tuple(sorted(subtype_counts.items())),
        tuple(sorted({subtype for choice in qualified for subtype in choice.membership.subtypes})),
        states[EvaluationState.NOT_EVALUATED], states[EvaluationState.UNSUPPORTED],
        states[EvaluationState.UNSCORED], qualified_copies, selected_copies,
    )


def _new_candidate(
    gate: EligibilityGate, card: CardSnapshot, inventory: CardEligibility,
    profiles: CardEvaluations | None, locked: bool,
) -> CardCandidate:
    memberships = map_role_memberships(card, profiles)
    synergy = profiles.strategy_synergy if profiles else None
    return CardCandidate(
        card.card_id, inventory.owned_printing_ids, inventory.available_quantity, profiles,
        CandidateProvenance(gate.snapshot.snapshot_id, gate.context.engine_versions),
        mechanical_subtypes=tuple(sorted({subtype for item in memberships for subtype in item.subtypes})),
        capabilities=tuple(sorted({item.id for item in synergy.mechanical_capabilities})) if synergy else (),
        dependencies=tuple(sorted({term for item in memberships for term in item.conditions})),
        role_profiles=memberships, locked=locked, copy_limit=inventory.copy_limit, name=card.name,
    )


def _eligible_inventory(
    gate: EligibilityGate, constraints: PoolConstraints,
) -> tuple[list[tuple[CardSnapshot, CardEligibility]], list[ValidationIssue]]:
    issues = list(gate.violations)
    excluded = set(constraints.exclude_card_ids)
    for card_id in constraints.locked_card_ids:
        if card_id in excluded:
            issues.append(ValidationIssue(
                "conflicting_constraints", "A locked card is also excluded.", card_id,
            ))
        elif invalid := gate.check_card(card_id).violations:
            issues.extend((
                ValidationIssue("invalid_locked_card", "A locked card must be eligible.", card_id),
                *invalid,
            ))
    if issues:
        raise PoolBuildError(tuple(dict.fromkeys(issues)))

    eligible = []
    omissions = list(gate.unlinked_printings)
    for card_id in constraints.exclude_card_ids:
        omissions.append(ValidationIssue("user_excluded", "Excluded by user constraint.", card_id))
    for card in gate.snapshot.cards:
        result = gate.check_card(card.card_id)
        omissions.extend(result.violations)
        if result.eligible and card.card_id not in excluded:
            eligible.append((card, result))
    return eligible, omissions


def _pool_audit(
    coverage: tuple[RoleCoverage, ...], selected: list[CardCandidate], config: CandidatePoolConfig,
) -> tuple[tuple[Diagnostic, ...], tuple[Diagnostic, ...], tuple[Diagnostic, ...]]:
    gaps = []
    diversity = []
    warnings = []
    for row in coverage:
        if row.shortfall:
            gaps.append(Diagnostic(
                "below_soft_target", "Qualified coverage is below the role target.", row.role, row.shortfall,
            ))
        subtype_gap = ceil(len(row.available_subtypes) * config.minimum_subtype_coverage) - len(row.subtype_counts)
        if subtype_gap > 0:
            diversity.append(Diagnostic(
                "subtype_coverage_gap", "Coverage is below the configured subtype target.", row.role, subtype_gap,
            ))
        for code, value in (
            ("not_evaluated", row.not_evaluated), ("unsupported_evaluation", row.unsupported),
            ("unscored_evaluation", row.unscored),
        ):
            if value:
                warnings.append(Diagnostic(code, "Eligible cards have partial role coverage.", row.role, value))

    capability_counts = Counter(capability for card in selected for capability in card.capabilities)
    for card in selected:
        synergy = card.evaluations.strategy_synergy if card.evaluations else None
        if synergy is None:
            continue
        missing = {
            capability for evidence in synergy.evidence for capability in evidence.missing_capabilities
            if capability_counts[capability] <= int(capability in card.capabilities)
        }
        if missing:
            warnings.append(Diagnostic(
                "dependency_gap", f"Pool lacks a distinct card with: {', '.join(sorted(missing))}.",
                Role.STRATEGY_SYNERGY, len(missing), card.card_id,
            ))
    return tuple(gaps), tuple(diversity), tuple(warnings)


class CandidatePoolBuilder:
    def __init__(
        self, config: CandidatePoolConfig = CandidatePoolConfig(),
        *, synergy_context: SynergyCatalogContext | None = None,
    ):
        self.config = config
        self._synergy_context = synergy_context

    def build(
        self, gate: EligibilityGate, evaluators: EvaluationAdapters | None = None,
        *, evaluation_snapshot: EvaluationSnapshot | None = None,
        constraints: PoolConstraints = PoolConstraints(),
    ) -> CandidatePool:
        """Use adapters by default or consume an explicitly supplied evaluation snapshot."""
        cards, excluded = _eligible_inventory(gate, constraints)
        if gate.context.config_version != self.config.version:
            raise PoolBuildError((ValidationIssue(
                "pool_config_mismatch", "The run context references a different candidate pool configuration.",
            ),))
        if gate.context.engine_versions != ENGINE_VERSIONS:
            raise PoolBuildError((ValidationIssue(
                "engine_version_mismatch", "The run context references different engine versions.",
            ),))
        if evaluators is not None and evaluation_snapshot is not None:
            raise PoolBuildError((ValidationIssue(
                "conflicting_evaluation_sources", "Provide adapters or an evaluation snapshot.",
            ),))

        catalog = self._synergy_context
        if evaluation_snapshot is None:
            evaluators = evaluators or EvaluationAdapters(gate, catalog or load_synergy_context())
            if _evaluation_inputs(evaluators.context) != _evaluation_inputs(gate.context):
                raise PoolBuildError((ValidationIssue(
                    "evaluation_context_mismatch", "The adapters are bound to different evaluation inputs.",
                ),))
            preferences = evaluators.strategy_preferences
            catalog = evaluators.synergy_context
            evaluation_snapshot = EvaluationSnapshot(
                gate.context, tuple(evaluators.evaluate(card.card_id) for card, _ in cards), preferences,
            )
        else:
            catalog = catalog or load_synergy_context()
            try:
                priorities = normalize_priorities(
                    tuple(SelectedStrategy(item.strategy_id, item.weight) for item in gate.context.strategy_preferences),
                    catalog,
                )
            except ValueError as error:
                raise PoolBuildError((ValidationIssue("invalid_strategy_preferences", str(error)),)) from error
            preferences = tuple(StrategyPreference(strategy_id, weight) for strategy_id, weight in priorities.items())
        if issues := _snapshot_issues(evaluation_snapshot, gate.context, preferences, catalog):
            raise PoolBuildError(issues)

        profiles = {item.card_id: item for item in evaluation_snapshot.profiles}
        locked_ids = set(constraints.locked_card_ids)
        candidates = [
            _new_candidate(gate, card, inventory, profiles.get(card.card_id), card.card_id in locked_ids)
            for card, inventory in cards
        ]
        return self._select(gate, candidates, excluded, constraints, evaluation_snapshot)

    def _select(
        self, gate: EligibilityGate, candidates: list[CardCandidate], excluded: list[ValidationIssue],
        constraints: PoolConstraints, snapshot: EvaluationSnapshot,
    ) -> CandidatePool:
        qualified: dict[Role, list[_RoleChoice]] = {}
        reasons: dict[str, list[SelectionReason]] = {card.card_id: [] for card in candidates}
        admissions: dict[str, SelectionReason] = {}
        selected = set(constraints.locked_card_ids)
        roles_by_card: dict[str, list[Role]] = {card.card_id: [] for card in candidates}
        for policy in self.config.policies:
            role = policy.role
            choices = []
            for card in candidates:
                membership = next(item for item in card.role_profiles if item.role is role)
                choice = _RoleChoice(card, membership)
                qualification = _qualification(choice, policy, self.config)
                override = qualification == "commander_synergy_override"
                if qualification != "not_qualified":
                    reasons[card.card_id].append(SelectionReason(
                        qualification, role, choice.evidence_ids(override=override),
                    ))
                if qualification in {"role_qualified", "commander_synergy_override", "unscored_fallback"}:
                    choices.append(replace(choice, override=override))
                    roles_by_card[card.card_id].append(role)
            qualified[role] = choices
            admitted = _select_role(choices, policy, self.config)
            selected.update(admitted)
            for choice in choices:
                card_id = choice.card.card_id
                reason = SelectionReason(
                    admitted.get(card_id, "role_budget"), role, choice.evidence_ids(override=choice.override),
                )
                reasons[card_id].append(reason)
                if card_id in admitted:
                    admissions.setdefault(card_id, reason)

        included, reserve = [], []
        decisions = []
        for card in candidates:
            card_id = card.card_id
            card_reasons = reasons[card_id]
            if card.locked:
                card_reasons.append(SelectionReason("user_locked_override"))
            incomplete = any(item.evaluation_state is EvaluationState.NOT_EVALUATED for item in card.role_profiles)
            if incomplete:
                card_reasons.append(SelectionReason("missing_evaluation"))
            if card_id in selected:
                status = CandidateStatus.CANDIDATE
                primary_reason = SelectionReason("user_locked_override") if card.locked else admissions[card_id]
                included.append(replace(
                    card, roles=tuple(roles_by_card[card_id]),
                    selection_reasons=tuple(dict.fromkeys(card_reasons)), status=status,
                ))
            elif any(reason.role is not None for reason in card_reasons) or (incomplete and self.config.retain_unevaluated):
                status = CandidateStatus.RESERVE
                primary_reason = next(
                    (reason for reason in card_reasons if reason.code in {"role_budget", "low_role_score", "unscored_role"}),
                    SelectionReason("missing_evaluation"),
                )
                reserve.append(replace(
                    card, roles=tuple(roles_by_card[card_id]),
                    selection_reasons=tuple(dict.fromkeys(card_reasons)), status=status,
                ))
            else:
                status = CandidateStatus.EXCLUDED
                code = "missing_evaluation" if incomplete else "no_qualifying_role"
                primary_reason = SelectionReason(code)
                excluded.append(ValidationIssue(code, "No eligible role was established.", card_id))
            decisions.append(DecisionRecord(
                len(decisions) + 1, card_id, "candidate_pool",
                CandidateStatus.ELIGIBLE, status, primary_reason, True,
            ))
        hard_issues = tuple(sorted(excluded, key=lambda item: (item.card_id or "", item.printing_id or 0, item.code)))
        decided = {item.card_id for item in decisions}
        for issue in hard_issues:
            if issue.card_id is None or issue.card_id in decided:
                continue
            decisions.append(DecisionRecord(
                len(decisions) + 1, issue.card_id, "candidate_pool", None, CandidateStatus.EXCLUDED,
                SelectionReason(issue.code), issue.code == "user_excluded",
            ))
            decided.add(issue.card_id)

        coverage = tuple(
            _role_coverage(policy.role, policy, candidates, qualified[policy.role], selected)
            for policy in self.config.policies
        )
        gaps, diversity, warnings = _pool_audit(coverage, included, self.config)
        provenance = PoolProvenance(
            replace(gate.context, strategy_preferences=snapshot.strategy_preferences), snapshot.snapshot_id,
            _fingerprint(asdict(self.config)), constraints.exclude_card_ids, constraints.locked_card_ids,
        )
        return CandidatePool(
            gate.context.run_id, tuple(included), tuple(reserve), hard_issues, gaps, diversity,
            pool_id=_fingerprint(asdict(provenance)), provenance=provenance, role_coverage=coverage,
            eligible_count=len(candidates), warnings=warnings, decision_log=tuple(decisions),
        )
