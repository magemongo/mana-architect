"""Evaluate mechanical strategy fit before deck construction."""

from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import math
from pathlib import Path

from mana_architect.catalog import StrategyCatalog, load_strategy_catalog
from mana_architect.collection import VALID_COLORS
from mana_architect.models import Card
from mana_architect.oracle_text import (
    PARSER_VERSION, LexiconEntry, MechanicalPattern, OracleParse, parse_oracle_text,
)
from mana_architect.synergy_capabilities import (
    CAPABILITY_IDS,
    CAPABILITY_VERSION,
    CapabilityEvidence,
    MechanicalCapability,
    MechanicalProfile,
    extract_mechanical_capabilities,
)
from mana_architect.synergy_catalog import (
    CapabilityPreference,
    SynergyStrategyCatalog,
    load_synergy_strategies,
)
from mana_architect.synergy_interactions import (
    INTERACTION_RULES,
    INTERACTION_RULE_VERSION,
    MatchStatus,
    MechanicalRule,
    rule_applies_to_capability,
    validate_interaction,
)

ENGINE_VERSION = "1.1.0"
ELIGIBILITY_VERSION = "single-commander-snapshot-v1"


@dataclass(frozen=True)
class SelectedStrategy:
    id: str
    priority: float = 1.0


@dataclass(frozen=True)
class SynergyScoringPolicy:
    version: str = "1.0.0"
    direct: float = 0.40
    affinity: float = 0.35
    bridge: float = 0.15
    reliability: float = 0.10
    specialist_bias: float = 0.65
    conditional_discount: float = 0.70
    potential_discount: float = 0.25
    bridge_cap: float = 0.5

    def __post_init__(self):
        weights = (self.direct, self.affinity, self.bridge, self.reliability)
        factors = (self.specialist_bias, self.conditional_discount, self.potential_discount, self.bridge_cap)
        if any(not 0 <= value <= 1 for value in (*weights, *factors)) or not math.isclose(sum(weights), 1):
            raise ValueError("Synergy weights must sum to one and all scoring factors must be in [0, 1].")
        if self.potential_discount > self.conditional_discount:
            raise ValueError("Potential evidence cannot be weighted above conditional evidence.")


@dataclass(frozen=True)
class SynergyCatalogContext:
    oracle: StrategyCatalog
    strategies: SynergyStrategyCatalog
    rules: tuple[MechanicalRule, ...] = INTERACTION_RULES
    rule_version: str = INTERACTION_RULE_VERSION
    scoring: SynergyScoringPolicy = SynergyScoringPolicy()

    def __post_init__(self):
        rule_ids = {rule.id for rule in self.rules}
        if len(rule_ids) != len(self.rules):
            raise ValueError("Duplicate mechanical interaction rule IDs.")
        if any({rule.source, rule.target} - CAPABILITY_IDS or not 0 <= rule.weight <= 1 for rule in self.rules):
            raise ValueError("Mechanical rules require known capabilities and a weight in [0, 1].")
        missing = {rule for strategy in self.strategies.strategies for rule in strategy.interaction_rule_ids} - rule_ids
        if missing:
            raise ValueError(f"Unknown strategy interaction rules: {', '.join(sorted(missing))}.")


def load_synergy_context(config_dir: Path | None = None) -> SynergyCatalogContext:
    oracle = load_strategy_catalog(config_dir)
    active = frozenset(strategy.id for strategy in oracle.strategies if strategy.active)
    return SynergyCatalogContext(oracle, load_synergy_strategies(config_dir, active_strategy_ids=active))


@dataclass(frozen=True)
class SynergyEvidence:
    id: str
    status: MatchStatus
    rule_id: str
    relation: str
    event: str
    explanation: str
    source_card_id: str
    source: MechanicalCapability
    target_card_id: str | None = None
    target: MechanicalCapability | None = None
    strategy_ids: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    weight: float = 0.0


@dataclass(frozen=True)
class StrategyAffinity:
    strategy_id: str
    priority: float
    score: float
    roles: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommanderSynergy:
    score: float = 0.0
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyBridge:
    strategies: tuple[str, ...]
    event_key: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class SynergyScores:
    direct_commander_synergy: float = 0.0
    weighted_strategy_affinity: float = 0.0
    cross_strategy_bridge: float = 0.0
    interaction_reliability: float = 0.0
    coverage_confidence: float = 0.0
    overall: float = 0.0


@dataclass(frozen=True)
class StrategySynergyProfile:
    card_id: str
    commander_id: str
    catalog_versions: tuple[tuple[str, str], ...]
    eligible: bool
    rejection_reasons: tuple[str, ...] = ()
    mechanical_capabilities: tuple[MechanicalCapability, ...] = ()
    strategy_affinities: tuple[StrategyAffinity, ...] = ()
    commander_synergy: CommanderSynergy = CommanderSynergy()
    cross_strategy_bridges: tuple[StrategyBridge, ...] = ()
    evidence: tuple[SynergyEvidence, ...] = ()
    scores: SynergyScores = SynergyScores()
    unknowns: tuple[CapabilityEvidence, ...] = ()
    commander_unknowns: tuple[CapabilityEvidence, ...] = ()
    engine_version: str = ENGINE_VERSION

    def to_json(self) -> dict:
        result = asdict(self)
        result["catalog_versions"] = dict(self.catalog_versions)
        return result


@lru_cache(maxsize=4096)
def _parse_cached(
    text: str, parser_version: str, lexicon: tuple[LexiconEntry, ...],
    patterns: tuple[MechanicalPattern, ...],
) -> OracleParse:
    return parse_oracle_text(text, lexicon, patterns)


def parse_synergy_oracle(card: Card, context: SynergyCatalogContext) -> OracleParse:
    return _parse_cached(
        card.oracle_text or "", PARSER_VERSION, context.oracle.oracle_lexicon,
        context.oracle.oracle_mechanical_patterns,
    )


@lru_cache(maxsize=4096)
def _compile_parsed_capabilities(
    parsed: OracleParse, name: str, type_line: str, capability_version: str,
) -> MechanicalProfile:
    return extract_mechanical_capabilities(parsed, name=name, type_line=type_line)


def _capabilities(card: Card, parsed: OracleParse) -> MechanicalProfile:
    if parsed.oracle_text != (card.oracle_text or ""):
        raise ValueError("The supplied Oracle parse does not match the card's text.")
    return _compile_parsed_capabilities(parsed, card.name, card.type_line or "", CAPABILITY_VERSION)


def normalize_priorities(selected: tuple[SelectedStrategy, ...], context: SynergyCatalogContext) -> dict[str, float]:
    known = {strategy.id for strategy in context.strategies.strategies}
    aliases = dict(context.strategies.aliases)
    weights: dict[str, float] = {}
    for strategy in selected:
        strategy_id = aliases.get(strategy.id, strategy.id)
        if strategy_id not in known:
            raise ValueError(f"Unknown mechanical strategy: {strategy.id}.")
        if strategy_id in weights:
            raise ValueError(f"Duplicate selected strategy: {strategy.id}.")
        if isinstance(strategy.priority, bool) or not math.isfinite(strategy.priority) or strategy.priority < 0:
            raise ValueError("Strategy priorities must be finite and nonnegative.")
        weights[strategy_id] = strategy.priority
    scale = max(weights.values(), default=0)
    if weights and scale == 0:
        raise ValueError("Select at least one strategy with a positive priority.")
    scaled = {key: weight / scale for key, weight in sorted(weights.items()) if weight > 0}
    total = sum(scaled.values())
    return {key: value / total for key, value in scaled.items()}


def candidate_rejections(commander: Card, candidate: Card, owned_quantity: int) -> tuple[str, ...]:
    reasons = []
    if owned_quantity <= 0:
        reasons.append("candidate_not_owned")
    if candidate.oracle_id == commander.oracle_id:
        reasons.append("candidate_is_commander")
    for card, label in ((commander, "commander"), (candidate, "candidate")):
        legality = (card.legalities or {}).get("commander")
        if legality != "legal":
            reasons.append(f"{label}_{legality if legality in {'banned', 'restricted', 'not_legal'} else 'legality_unknown'}")
        if card.color_identity is None or not set(card.color_identity) <= VALID_COLORS:
            reasons.append(f"{label}_color_identity_unknown")
    if commander.color_identity is not None and candidate.color_identity is not None:
        if set(candidate.color_identity) - set(commander.color_identity):
            reasons.append("incompatible_color_identity")
    return tuple(reasons)


def _evidence_id(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]


def _preference_matches(preference: CapabilityPreference, cap: MechanicalCapability, card: Card) -> bool:
    if preference.capability != cap.id:
        return False
    types = set(cap.object.types) | {cap.object.kind}
    if cap.object.reference == "self" and not cap.object.types:
        types.update((cap.face_type_line or card.type_line or "").casefold().split(" — ")[0].split())
    if preference.types and not types.intersection(preference.types):
        return False
    if preference.subtype and preference.subtype not in cap.object.subtypes:
        return False
    if preference.reference and preference.reference != cap.object.reference:
        return False
    if cap.controller == "opponent" and cap.id in {"gain_life", "lifegain_payoff", "enter_payoff"}:
        return False
    if cap.id == "add_mana" and "land" in (cap.face_type_line or card.type_line or "").casefold().split(" — ")[0].split():
        return False
    return cap.quantity.value != 0


def _affinity_evidence(card, caps, preferences, strategy_id):
    for cap in caps:
        for preference in preferences:
            if not _preference_matches(preference, cap, card):
                continue
            dependencies = set(cap.conditions)
            if cap.modality == "optional":
                dependencies.add("choose_optional_effect")
            if cap.timing == "triggered":
                dependencies.add("qualifying_trigger_event")
            if cap.targeting == "target":
                dependencies.add("legal_target_at_resolution")
            if cap.costs:
                dependencies.add("pay_ability_cost")
            if cap.quantity.kind in {"up_to", "variable"}:
                dependencies.add("positive_event_quantity")
            if cap.action == "multiply":
                dependencies.add(f"qualifying_{cap.event}")
            evidence = SynergyEvidence(
                _evidence_id(card.oracle_id, preference.id, cap.event_key),
                MatchStatus.CONDITIONAL if dependencies else MatchStatus.VALIDATED,
                preference.id, "supports", cap.event,
                f"{cap.id.replace('_', ' ').capitalize()} contributes as {preference.role} for {strategy_id.replace('_', ' ')}.",
                card.oracle_id, cap, strategy_ids=(strategy_id,),
                dependencies=tuple(sorted(dependencies)), weight=preference.weight,
            )
            yield evidence, preference.role


def _direct_evidence(left_card, right_card, left_caps, right_caps, rule, strategy_ids):
    for source in left_caps:
        if source.id != rule.source:
            continue
        for target in right_caps:
            if target.id != rule.target:
                continue
            match = validate_interaction(
                rule, source, target, source_type=left_card.type_line or "", target_type=right_card.type_line or "",
            )
            if match is None:
                continue
            yield SynergyEvidence(
                _evidence_id(rule.id, left_card.oracle_id, source.event_key, right_card.oracle_id, target.event_key),
                match.status, rule.id, rule.relation, rule.event, match.explanation,
                left_card.oracle_id, source, right_card.oracle_id, target, strategy_ids,
                match.dependencies, weight=rule.weight,
            )


def _discount(evidence: SynergyEvidence, policy: SynergyScoringPolicy) -> float:
    if evidence.status == MatchStatus.VALIDATED:
        return 1.0
    if evidence.status == MatchStatus.CONDITIONAL:
        return policy.conditional_discount
    if evidence.status == MatchStatus.POTENTIAL:
        return policy.potential_discount
    return 0.0


def _score(evidence: SynergyEvidence, policy: SynergyScoringPolicy) -> float:
    return evidence.weight * _discount(evidence, policy)


def _candidate_event(evidence: SynergyEvidence, candidate: Card) -> str:
    if evidence.source_card_id == candidate.oracle_id:
        return evidence.source.event_key
    assert evidence.target is not None
    return evidence.target.event_key


def evaluate_synergy(
    commander: Card, candidate: Card, selected_strategies: tuple[SelectedStrategy, ...],
    context: SynergyCatalogContext, *, owned_quantity: int,
    candidate_parse: OracleParse | None = None, commander_parse: OracleParse | None = None,
) -> StrategySynergyProfile:
    """Return separate direct, strategy, and bridge scores for an owned candidate."""
    priorities = normalize_priorities(selected_strategies, context)
    reasons = candidate_rejections(commander, candidate, owned_quantity)
    versions = (
        ("capabilities", CAPABILITY_VERSION), ("strategies", context.strategies.version),
        ("interaction_rules", context.rule_version), ("scoring", context.scoring.version),
        ("eligibility", ELIGIBILITY_VERSION), ("oracle_parser", PARSER_VERSION),
    )
    if reasons:
        return StrategySynergyProfile(candidate.oracle_id, commander.oracle_id, versions, False, reasons)

    own = _capabilities(candidate, candidate_parse or parse_synergy_oracle(candidate, context))
    leader = _capabilities(commander, commander_parse or parse_synergy_oracle(commander, context))
    if own.parser_version != PARSER_VERSION or leader.parser_version != PARSER_VERSION:
        raise ValueError("Use current Oracle parses to evaluate strategy synergy.")
    policy = context.scoring
    strategies = tuple(strategy for strategy in context.strategies.strategies if strategy.id in priorities)
    evidence: dict[str, SynergyEvidence] = {}
    direct: list[SynergyEvidence] = []
    potential: dict[str, list[SynergyEvidence]] = {key: [] for key in priorities}
    present = {cap.id for cap in (*own.capabilities, *leader.capabilities)}
    for rule in context.rules:
        rule_strategies = tuple(sorted(s.id for s in strategies if rule.id in s.interaction_rule_ids))
        pair_matches = (
            *_direct_evidence(candidate, commander, own.capabilities, leader.capabilities, rule, rule_strategies),
            *_direct_evidence(commander, candidate, leader.capabilities, own.capabilities, rule, rule_strategies),
        )
        direct.extend(pair_matches)
        if not rule_strategies:
            continue
        for cap in own.capabilities:
            if not rule_applies_to_capability(rule, cap) or cap.quantity.value == 0:
                continue
            missing = rule.target if cap.id == rule.source else rule.source
            if missing in present:
                continue
            if rule.object_type not in {"", "treasure"} and cap.object.types and rule.object_type not in cap.object.types:
                continue
            item = SynergyEvidence(
                _evidence_id(rule.id, candidate.oracle_id, cap.event_key, "potential"),
                MatchStatus.POTENTIAL, rule.id, rule.relation, rule.event,
                f"{rule.explanation} Requires a compatible {missing.replace('_', ' ')} capability.",
                candidate.oracle_id, cap, strategy_ids=rule_strategies,
                dependencies=(rule.dependency,) if rule.dependency else (),
                missing_capabilities=(missing,), weight=rule.weight,
            )
            for strategy_id in rule_strategies:
                potential[strategy_id].append(item)

    affinities = []
    bridge_events: dict[str, dict[str, set[str]]] = {}
    for strategy in strategies:
        intrinsic = tuple(_affinity_evidence(candidate, own.capabilities, strategy.preferences, strategy.id))
        related = [
            *(item for item, _ in intrinsic), *potential[strategy.id],
            *(item for item in direct if strategy.id in item.strategy_ids and _score(item, policy) > 0),
        ]
        evidence.update((item.id, item) for item in related)
        for item in related:
            if item.status == MatchStatus.VALIDATED and item.weight > 0:
                event = _candidate_event(item, candidate)
                bridge_events.setdefault(event, {}).setdefault(strategy.id, set()).add(item.id)
        affinities.append(StrategyAffinity(
            strategy.id, priorities[strategy.id],
            max((_score(item, policy) for item in related), default=0.0),
            tuple(sorted({role for item, role in intrinsic if item.weight > 0})),
            tuple(sorted({item.id for item in related})),
        ))
    affinities.sort(key=lambda affinity: affinity.strategy_id)
    evidence.update((item.id, item) for item in direct)
    bridges = tuple(
        StrategyBridge(tuple(sorted(matches)), event, tuple(sorted(set().union(*matches.values()))))
        for event, matches in sorted(bridge_events.items()) if len(matches) > 1
        and len({strategy.objective for strategy in strategies if strategy.id in matches}) > 1
    )

    direct_score = max((_score(item, policy) for item in direct), default=0.0)
    top = max((item.score for item in affinities), default=0.0)
    affinity_score = policy.specialist_bias * top + (1 - policy.specialist_bias) * sum(
        item.priority * item.score for item in affinities
    )
    bridge_score = min(policy.bridge_cap, 0.25 * len(bridges))
    strongest = {}
    for item in evidence.values():
        if _score(item, policy) > 0:
            event = _candidate_event(item, candidate)
            strongest[event] = max(strongest.get(event, 0.0), _discount(item, policy))
    reliability = math.fsum(strongest.values()) / len(strongest) if strongest else 0.0
    supported = {
        (label, cap.evidence.ability_id, cap.evidence.start)
        for label, profile in (("candidate", own), ("commander", leader))
        for cap in profile.capabilities
        if cap.evidence.source_field == "oracle_text"
    }
    unresolved = len(own.unknowns) + len(leader.unknowns) + sum(
        item.status == MatchStatus.UNKNOWN for item in evidence.values()
    )
    coverage = len(supported) / (len(supported) + unresolved) if supported else 0.0
    overall = 100 * (
        policy.direct * direct_score + policy.affinity * affinity_score
        + policy.bridge * bridge_score + policy.reliability * reliability
    )
    scores = SynergyScores(direct_score, affinity_score, bridge_score, reliability, coverage, round(overall, 6))
    return StrategySynergyProfile(
        candidate.oracle_id, commander.oracle_id, versions, True,
        mechanical_capabilities=own.capabilities, strategy_affinities=tuple(affinities),
        commander_synergy=CommanderSynergy(direct_score, tuple(sorted(item.id for item in direct))),
        cross_strategy_bridges=bridges, evidence=tuple(evidence[key] for key in sorted(evidence)),
        scores=scores, unknowns=own.unknowns, commander_unknowns=leader.unknowns,
    )
