"""Analyze owned candidate interactions through the existing mechanical rules."""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import re

from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS, parse_card_snapshot
from mana_architect.deck_pipeline.contracts import (
    Artifact,
    CandidatePool,
    CardCandidate,
    Diagnostic,
    PairwiseDiagnostics,
    PairwiseProvenance,
    PairwiseSynergyAnalysis,
    PairwiseSynergyEdge,
    RuleEvidence,
    RuleMatchCount,
    ValidationIssue,
    artifact_digest,
)
from mana_architect.deck_pipeline.eligibility import EligibilityGate
from mana_architect.deck_pipeline.inventory import CardSnapshot
from mana_architect.interaction_rules_catalog import mask_reminder_text
from mana_architect.oracle_text import PARSER_VERSION, OracleParse
from mana_architect.strategy_synergy import (
    SelectedStrategy,
    SynergyCatalogContext,
    load_synergy_context,
    normalize_priorities,
)
from mana_architect.synergy_capabilities import (
    CAPABILITY_VERSION,
    MechanicalCapability,
    extract_mechanical_capabilities,
)
from mana_architect.synergy_interactions import (
    MatchStatus,
    MechanicalRule,
    RuleMatch,
    validate_interaction,
)

_ANALYZER_VERSION = "1.1.0"
_CANONICAL_RELATIONS = {
    "directly_triggers": "enables", "recurs": "enables", "enables": "enables",
    "rewards": "rewards", "amplifies": "amplifies", "converts": "converts",
}
_MODAL_HEADER = re.compile(r"^\s*choose\s+[^\r\n]+[—:]", re.IGNORECASE | re.MULTILINE)
_DEATH_EXILE = re.compile(
    r"^if (?:a creature|another creature|a creature you control|a creature an opponent controls|this creature)"
    r" would die, (?:instead exile it(?: (?:and|then) [^.]+)?"
    r"|exile it(?: (?:and|then) [^.]+)? instead)\.$",
    re.IGNORECASE | re.MULTILINE,
)
_GRAVEYARD_EXILE = re.compile(
    r"^if (?:a card or token|a card|a token|a creature card) would be put into a graveyard"
    r"(?: from anywhere| from the battlefield)?, exile it instead\.$",
    re.IGNORECASE | re.MULTILINE,
)
_ONE_PER_TURN = re.compile(
    r"\b(?:only once each turn|activate only once (?:each|per) turn)\b", re.IGNORECASE,
)


@dataclass(frozen=True)
class PairwiseConfig(Artifact):
    version: str = "pairwise-v1"
    conditional_strength_factor: float = 0.7
    max_rule_matches: int = 250_000
    include_reserve: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.version or not 0 < self.conditional_strength_factor <= 1:
            raise ValueError("Pairwise configuration requires a version and a conditional factor in (0, 1].")
        if type(self.max_rule_matches) is not int or self.max_rule_matches < 1:
            raise ValueError("The pairwise work limit must be a positive integer.")


class PairwiseAnalysisError(ValueError):
    def __init__(self, violations: tuple[ValidationIssue, ...]):
        self.violations = violations
        super().__init__(" ".join(issue.message for issue in violations))


@dataclass(frozen=True)
class _Endpoint:
    card: CardSnapshot
    capability: MechanicalCapability
    line: str

    @property
    def card_id(self) -> str:
        return self.card.card_id

    @property
    def event_id(self) -> str:
        span = self.capability.evidence
        return f"{self.card_id}:{span.source_field}:{self.capability.event_key}"

    def evidence(self, rule_id: str) -> RuleEvidence:
        span = self.capability.evidence
        return RuleEvidence(
            self.card_id, span.ability_id, rule_id, span.source_field,
            span.start, span.end, span.oracle_span,
        )


def _endpoint(
    card: CardSnapshot, parsed: OracleParse, capability: MechanicalCapability,
) -> _Endpoint | str:
    span = capability.evidence
    if span.source_field == "type_line":
        text = card.type_line or ""
        if span.ability_id != "type_line":
            return "invalid_ability_evidence"
    elif span.source_field == "oracle_text":
        text = parsed.oracle_text
        if not any(
            ability.id == span.ability_id and ability.start <= span.start < span.end <= ability.end
            for ability in parsed.abilities
        ):
            return "invalid_ability_evidence"
    else:
        return "invalid_ability_evidence"
    if not (0 <= span.start < span.end <= len(text) and text[span.start:span.end] == span.oracle_span):
        return "invalid_ability_evidence"
    if " // " in text[:span.start] or " // " in span.oracle_span:
        return "unresolved_face"
    if span.source_field == "type_line":
        return _Endpoint(card, capability, span.oracle_span)
    line_start = text.rfind("\n", 0, span.start) + 1
    line = text[line_start:].split("\n", 1)[0].split(" // ", 1)[0]
    paragraph_break = text.rfind("\n\n", 0, span.start)
    paragraph_start = paragraph_break + 2 if paragraph_break >= 0 else 0
    if line.lstrip().startswith("•") or _MODAL_HEADER.search(text[paragraph_start:span.start]):
        return "unresolved_mode"
    return _Endpoint(card, capability, line)


def prevents_creature_death(
    card: CardSnapshot, *, controller: str, sacrificed_kind: str, sacrificed_card_id: str | None,
) -> bool:
    """Recognize mandatory exile replacements for a specific creature death."""
    front_type = (card.type_line or "").split(" // ", 1)[0].partition("—")[0]
    if {"instant", "sorcery"} & set(front_type.casefold().split()):
        return False
    text = mask_reminder_text((card.oracle_text or "").split(" // ", 1)[0])
    for replacement in _DEATH_EXILE.finditer(text):
        precondition = replacement[0].casefold().split(" would die", 1)[0]
        if "an opponent controls" in precondition and controller != "opponent":
            continue
        if "you control" in precondition and controller != "you":
            continue
        if "another creature" in precondition and sacrificed_kind != "token" and (
            sacrificed_card_id is None or sacrificed_card_id == card.card_id
        ):
            continue
        if "this creature" in precondition and sacrificed_card_id != card.card_id:
            continue
        return True
    for replacement in _GRAVEYARD_EXILE.finditer(text):
        subject = replacement[0].casefold().split(" would be put", 1)[0]
        if subject == "if a card or token" or (
            sacrificed_kind == "token" and subject == "if a token"
        ) or (sacrificed_kind == "card" and subject in {"if a card", "if a creature card"}):
            return True
    return False


def _known_death_replacement(source: _Endpoint, target: _Endpoint) -> bool:
    obj = source.capability.object
    return any(prevents_creature_death(
        endpoint.card, controller=source.capability.controller,
        sacrificed_kind="card" if obj.reference == "self" else obj.kind,
        sacrificed_card_id=source.card_id if obj.reference == "self" else None,
    ) for endpoint in (source, target))


def canonical_rule_relation(rule: MechanicalRule) -> str:
    return _CANONICAL_RELATIONS[rule.relation]


def pairwise_catalog_fingerprint(catalog: SynergyCatalogContext) -> str:
    return artifact_digest({
        "rules": [asdict(rule) for rule in sorted(catalog.rules, key=lambda item: item.id)],
        "strategies": asdict(catalog.strategies),
        "lexicon": [asdict(entry) for entry in catalog.oracle.oracle_lexicon],
        "mechanical_patterns": [asdict(entry) for entry in catalog.oracle.oracle_mechanical_patterns],
        "scoring_version": catalog.scoring.version,
    })


def _requirements(
    rule: MechanicalRule, source: _Endpoint, target: _Endpoint, match: RuleMatch,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    prerequisites = set(match.dependencies)
    limitations = set()
    resources = set()
    timing = set()
    for label, endpoint in (("source", source), ("target", target)):
        card, capability = endpoint.card, endpoint.capability
        types = (capability.face_type_line or card.type_line or "").casefold().partition("—")[0].split()
        copied_spell = label == "target" and rule.id == "spell_copy_amplifies_spell_effect"
        if not copied_spell and card.mana_cost and {"instant", "sorcery"} & set(types):
            resources.add(f"{label}:printed_spell_cost:{card.mana_cost.split(' // ', 1)[0]}")
        if not copied_spell and capability.additional_cast_costs:
            resources.update(f"{label}:additional_cast_cost:{cost}" for cost in capability.additional_cast_costs)
            prerequisites.add(f"{label}:pay_additional_cast_cost")
        if capability.costs:
            resources.update(f"{label}:{cost}" for cost in capability.costs)
            if any(re.search(r"\{[TQ]\}", cost, re.IGNORECASE) for cost in capability.costs):
                timing.add(f"{label}:tap_or_untap_cost")
                if "creature" in types:
                    prerequisites.add(f"{label}:can_pay_creature_tap_or_untap_cost")
                    limitations.add("creature_tap_cost_subject_to_summoning_sickness")
        if capability.timing in {"activated", "sorcery_speed", "instant_speed", "triggered", "replacement"}:
            timing.add(f"{label}:{capability.timing}")
        if capability.returns_tapped:
            limitations.add(f"{label}:returns_tapped")
        if capability.repeatability == "single_use":
            limitations.add(f"{label}:one_execution_per_cast")
        if _ONE_PER_TURN.search(endpoint.line):
            timing.add(f"{label}:once_each_turn")
            limitations.add(f"{label}:once_each_turn")
        if re.search(r"\b(?:next end step|at end of combat)\b", endpoint.line, re.IGNORECASE):
            timing.add(f"{label}:delayed_effect")
        if capability.timing == "triggered" and capability.action == "observe":
            limitations.add(f"{label}:payoff_must_be_able_to_observe_event")
            if rule.id in {"blink_retriggers_etb", "recursion_reuses_etb"} and capability.object.reference != "self":
                prerequisites.add(f"{label}:payoff_in_applicable_zone_when_event_occurs")
    if rule.event == "death":
        prerequisites.add("creature_moves_from_battlefield_to_graveyard")
        limitations.add("death_edge_fails_if_replaced_by_exile_or_other_zone")
    if rule.relation == "amplifies" and source.capability.timing == "replacement":
        limitations.add("replacement_effect_must_already_apply")
    return tuple(sorted(prerequisites)), tuple(sorted(limitations)), tuple(sorted(resources)), tuple(sorted(timing))


def edge_is_supported(
    rule: MechanicalRule, source: _Endpoint, target: _Endpoint, edge: PairwiseSynergyEdge,
) -> bool:
    """Recheck a persisted edge against its ordered, bound Oracle capabilities."""
    if any(cap.quantity.kind in {"fixed", "up_to"} and cap.quantity.value == 0
           for cap in (source.capability, target.capability)):
        return False
    match = validate_interaction(
        rule, source.capability, target.capability,
        source_type=source.card.type_line or "", target_type=target.card.type_line or "",
    )
    if (
        match is None or match.status not in {MatchStatus.VALIDATED, MatchStatus.CONDITIONAL}
        or (rule.event == "death" and _known_death_replacement(source, target))
    ):
        return False
    constraints = _requirements(rule, source, target, match)
    if (match.status == MatchStatus.CONDITIONAL or constraints[0]) and edge.confidence != "conditional":
        return False
    return all(set(expected) <= set(actual) for expected, actual in zip(
        constraints, (edge.prerequisites, edge.limitations, edge.resource_requirements, edge.timing_constraints),
        strict=True,
    ))


class PairwiseSynergyAnalyzer:
    """Local Phase 2 service. Its result is immutable and safe to reuse."""

    def __init__(
        self, config: PairwiseConfig = PairwiseConfig(), *, synergy_context: SynergyCatalogContext | None = None,
    ):
        self._config = config
        self._catalog = synergy_context or load_synergy_context()
        self._rules = tuple(sorted(self._catalog.rules, key=lambda rule: rule.id))
        if any(rule.relation not in _CANONICAL_RELATIONS for rule in self._rules):
            raise ValueError("The rule catalog declares an unsupported pairwise relation.")
        self._rules_fingerprint = pairwise_catalog_fingerprint(self._catalog)
        self._cache: PairwiseSynergyAnalysis | None = None

    def analyze(self, gate: EligibilityGate, pool: CandidatePool) -> PairwiseSynergyAnalysis:
        cards = (*pool.candidates, *pool.reserve) if self._config.include_reserve else pool.candidates
        priorities = self._validate_inputs(gate, pool, cards)
        provenance = PairwiseProvenance(
            collection_snapshot_id=gate.snapshot.snapshot_id,
            pool_id=pool.pool_id,
            pool_fingerprint=artifact_digest(pool.to_json()),
            evaluation_snapshot_id=pool.provenance.evaluation_snapshot_id if pool.provenance else None,
            commander_card_ids=gate.context.commander_card_ids,
            oracle_parser_version=PARSER_VERSION,
            capability_extractor_version=CAPABILITY_VERSION,
            strategy_catalog_version=self._catalog.strategies.version,
            config_version=self._config.version,
            config_fingerprint=artifact_digest(asdict(self._config)),
            rule_catalog_fingerprint=self._rules_fingerprint,
            analyzer_version=_ANALYZER_VERSION,
            ruleset_version=gate.context.ruleset_version,
            engine_versions=gate.context.engine_versions,
        )
        analysis_id = artifact_digest({
            "run_id": pool.run_id, "input": asdict(provenance),
            "rule_version": self._catalog.rule_version, "strategy_preferences": priorities,
        })
        if self._cache is not None and self._cache.analysis_id == analysis_id:
            return self._cache

        index, coverage = self._index(gate, cards, priorities)
        result = self._join(pool.run_id, analysis_id, index, priorities, coverage, provenance)
        self._cache = result
        return result

    def _validate_inputs(
        self, gate: EligibilityGate, pool: CandidatePool, cards: tuple[CardCandidate, ...],
    ) -> dict[str, float]:
        context = gate.context
        issues = list(gate.violations)
        if context.engine_versions != ENGINE_VERSIONS:
            issues.append(ValidationIssue("engine_version_mismatch", "The context uses incompatible engine versions."))
        if pool.run_id != context.run_id:
            issues.append(ValidationIssue("pool_run_mismatch", "The pool belongs to a different run."))
        try:
            priorities = normalize_priorities(
                tuple(SelectedStrategy(item.strategy_id, item.weight) for item in context.strategy_preferences),
                self._catalog,
            )
        except ValueError as error:
            raise PairwiseAnalysisError((ValidationIssue("invalid_strategy_preferences", str(error)),)) from error
        excluded = {issue.card_id for issue in pool.excluded if issue.code == "user_excluded"}
        if pool.provenance is not None:
            original = pool.provenance.context
            excluded.update(pool.provenance.excluded_card_ids)
            if (
                original.run_id != context.run_id
                or original.collection_snapshot_id != context.collection_snapshot_id
                or original.commander_card_ids != context.commander_card_ids
                or original.engine_versions != context.engine_versions
                or original.ruleset_version != context.ruleset_version
                or original.config_version != context.config_version
                or original.format != context.format
                or {item.strategy_id: item.weight for item in original.strategy_preferences} != priorities
            ):
                issues.append(ValidationIssue("pool_context_mismatch", "The pool uses different run inputs."))
        for card in cards:
            if card.card_id in excluded:
                issues.append(ValidationIssue("user_excluded", "A pool entry was excluded by the user.", card.card_id))
            eligibility = gate.check_card(card.card_id)
            issues.extend(eligibility.violations)
            if (
                card.provenance.collection_snapshot_id != context.collection_snapshot_id
                or card.provenance.engine_versions != context.engine_versions
                or card.available_quantity != eligibility.available_quantity
                or card.owned_printing_ids != eligibility.owned_printing_ids
            ):
                issues.append(ValidationIssue("pool_inventory_mismatch", "Pool inventory or provenance differs.", card.card_id))
        if issues:
            raise PairwiseAnalysisError(tuple(dict.fromkeys(issues)))
        return priorities

    def _index(
        self, gate: EligibilityGate, candidates: tuple[CardCandidate, ...], priorities: dict[str, float],
    ) -> tuple[dict[str, list[_Endpoint]], tuple[Diagnostic, ...]]:
        assert gate.commander is not None
        snapshots = {card.card_id: card for card in gate.snapshot.cards}
        relevant_ids = {name for rule in self._rules for name in (rule.source, rule.target)}
        index: dict[str, list[_Endpoint]] = defaultdict(list)
        coverage = []
        expected = {
            "oracle_parser": PARSER_VERSION, "capabilities": CAPABILITY_VERSION,
            "interaction_rules": self._catalog.rule_version, "strategies": self._catalog.strategies.version,
            "scoring": self._catalog.scoring.version,
        }
        profiles = {}
        commander = gate.commander
        commander_parse = parse_card_snapshot(commander, self._catalog)
        leader = extract_mechanical_capabilities(
            commander_parse, name=commander.name, type_line=commander.type_line or "",
        )
        profiles[commander.card_id] = (commander_parse, leader.capabilities, leader.unknowns)
        for candidate in candidates:
            profile = candidate.evaluations.strategy_synergy if candidate.evaluations else None
            if profile is None:
                coverage.append(Diagnostic(
                    "missing_mechanical_profile", "No Strategy Synergy capability profile was evaluated.",
                    card_id=candidate.card_id,
                ))
                continue
            if (
                profile.commander_id != commander.card_id or not profile.eligible
                or profile.engine_version != gate.context.engine_versions.strategy_synergy
                or not expected.items() <= dict(profile.catalog_versions).items()
                or {item.strategy_id: item.priority for item in profile.strategy_affinities} != priorities
            ):
                coverage.append(Diagnostic(
                    "incompatible_mechanical_profile", "The capability profile has incompatible run inputs or versions.",
                    card_id=candidate.card_id,
                ))
                continue
            parsed = parse_card_snapshot(snapshots[candidate.card_id], self._catalog)
            profiles[candidate.card_id] = (parsed, profile.mechanical_capabilities, profile.unknowns)

        for card_id, (parsed, capabilities, unknowns) in sorted(profiles.items()):
            card = snapshots[card_id]
            skipped: Counter[str] = Counter()
            for capability in capabilities:
                if capability.id not in relevant_ids:
                    continue
                endpoint = _endpoint(card, parsed, capability)
                if isinstance(endpoint, str):
                    skipped[endpoint] += 1
                else:
                    index[capability.id].append(endpoint)
            if unknowns:
                coverage.append(Diagnostic(
                    "unresolved_abilities", "The extractor left some abilities unresolved.",
                    value=len(unknowns), card_id=card_id,
                ))
            if card.oracle_text is None:
                coverage.append(Diagnostic(
                    "missing_oracle_text", "The snapshot has no Oracle text for this card.", card_id=card_id,
                ))
            for reason, count in sorted(skipped.items()):
                coverage.append(Diagnostic(
                    reason, "Capability evidence could not be verified for this snapshot.",
                    value=count, card_id=card_id,
                ))
        for endpoints in index.values():
            endpoints.sort(key=lambda item: (item.card_id, item.capability.evidence.start,
                                             item.capability.evidence.end, item.capability.event))
        return index, tuple(sorted(coverage, key=lambda item: (item.card_id or "", item.code)))

    def _join(
        self, run_id: str, analysis_id: str, index: dict[str, list[_Endpoint]],
        priorities: dict[str, float], coverage: tuple[Diagnostic, ...], provenance: PairwiseProvenance,
    ) -> PairwiseSynergyAnalysis:
        counts: Counter[tuple[str, str, str]] = Counter()
        pairs = set()
        edges = {}
        worked = 0
        limit_reached = False
        for rule in self._rules:
            for source in index.get(rule.source, ()):
                for target in index.get(rule.target, ()):
                    if worked == self._config.max_rule_matches:
                        limit_reached = True
                        break
                    worked += 1
                    if source.card_id == target.card_id:
                        counts[rule.id, "same_card", ""] += 1
                        continue
                    pairs.add((source.card_id, target.card_id))
                    if any(cap.quantity.kind in {"fixed", "up_to"} and cap.quantity.value == 0
                           for cap in (source.capability, target.capability)):
                        counts[rule.id, "zero_event_quantity", ""] += 1
                        continue
                    match = validate_interaction(
                        rule, source.capability, target.capability,
                        source_type=source.card.type_line or "", target_type=target.card.type_line or "",
                    )
                    if match is None:
                        counts[rule.id, "no_rule_match", ""] += 1
                        continue
                    if match.status not in {MatchStatus.VALIDATED, MatchStatus.CONDITIONAL}:
                        counts[rule.id, match.status.value, match.explanation] += 1
                        continue
                    if rule.event == "death" and _known_death_replacement(source, target):
                        counts[rule.id, "death_replaced", ""] += 1
                        continue
                    edge = self._edge(rule, source, target, match, priorities, provenance.commander_card_ids)
                    if edge.edge_id in edges:
                        counts[rule.id, "duplicate_evidence", ""] += 1
                        continue
                    edges[edge.edge_id] = edge
                    counts[rule.id, edge.confidence, ""] += 1
                if limit_reached:
                    break
            if limit_reached:
                break

        ordered = tuple(sorted(edges.values(), key=lambda edge: (edge.source_card_id, edge.target_card_id, edge.edge_id)))
        verified = sum(edge.confidence == "rule_verified" for edge in ordered)
        diagnostics = PairwiseDiagnostics(
            len(pairs), worked, verified, len(ordered) - verified,
            tuple(RuleMatchCount(rule, reason, count, detail)
                  for (rule, reason, detail), count in sorted(counts.items())),
            coverage, limit_reached,
        )
        return PairwiseSynergyAnalysis(
            analysis_id, run_id, self._catalog.rule_version, ordered, diagnostics, provenance,
        )

    def _edge(
        self, rule: MechanicalRule, source: _Endpoint, target: _Endpoint, match: RuleMatch,
        priorities: dict[str, float], commander_ids: tuple[str, ...],
    ) -> PairwiseSynergyEdge:
        relation = canonical_rule_relation(rule)
        event_ids = source.event_id, target.event_id
        prerequisites, limitations, resources, timing = _requirements(rule, source, target, match)
        evidence = source.evidence(rule.id), target.evidence(rule.id)
        conditional = match.status is MatchStatus.CONDITIONAL or bool(prerequisites)
        anchors = tuple(card_id for card_id in commander_ids if card_id in {source.card_id, target.card_id})
        strategies = tuple(sorted(
            strategy.id for strategy in self._catalog.strategies.strategies
            if strategy.id in priorities and rule.id in strategy.interaction_rule_ids
        ))
        event_signature = tuple(
            (cap.action, cap.event, asdict(cap.object), cap.controller, cap.owner, cap.source_zone, cap.destination_zone)
            for cap in (source.capability, target.capability)
        )
        return PairwiseSynergyEdge(
            edge_id=artifact_digest((event_ids, relation, rule.id, rule.event, event_signature)),
            source_card_id=source.card_id, target_card_id=target.card_id, relation=relation,
            source_capability=rule.source, target_capability=rule.target,
            evidence=evidence, strength=round(rule.weight * self._config.conditional_strength_factor, 6)
            if conditional else rule.weight,
            confidence="conditional" if conditional else "rule_verified", prerequisites=prerequisites,
            limitations=limitations, event_ids=event_ids, commander_anchor_ids=anchors, strategy_ids=strategies,
            resource_requirements=resources, timing_constraints=timing, rule_version=self._catalog.rule_version,
            explanation=f"{source.card.name} → {target.card.name}. {match.explanation}",
            base_strength=rule.weight, event_type=rule.event,
        )
