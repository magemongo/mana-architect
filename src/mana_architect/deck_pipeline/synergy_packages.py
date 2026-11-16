"""Discover owned packages from verified directional paths and typed capabilities."""

from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from math import fsum, isclose
import re

from mana_architect.deck_pipeline.adapters import parse_card_snapshot
from mana_architect.deck_pipeline.contracts import (
    Artifact,
    CandidatePool,
    PackageAnalysis,
    PackageDiagnostics,
    PackageProvenance,
    PairwiseSynergyAnalysis,
    PairwiseSynergyEdge,
    PartialPackage,
    RuleEvidence,
    SynergyPackage,
    ValidationIssue,
    artifact_digest,
)
from mana_architect.deck_pipeline.eligibility import EligibilityGate
from mana_architect.deck_pipeline.inventory import CardSnapshot
from mana_architect.deck_pipeline.package_templates import (
    PACKAGE_TEMPLATES,
    TEMPLATE_CATALOG_VERSION,
    PackageTemplate,
)
from mana_architect.deck_pipeline.pairwise_synergy import (
    _Endpoint as _Ability,
    _endpoint,
    canonical_rule_relation,
    edge_is_supported,
    pairwise_catalog_fingerprint,
    prevents_creature_death,
)
from mana_architect.oracle_text import PARSER_VERSION
from mana_architect.strategy_synergy import (
    SelectedStrategy,
    SynergyCatalogContext,
    load_synergy_context,
    normalize_priorities,
)
from mana_architect.synergy_capabilities import CAPABILITY_VERSION, MechanicalCapability, extract_mechanical_capabilities
from mana_architect.synergy_interactions import MatchStatus, validate_interaction

_RECURSION_RULE = "self_return_renews_sacrifice_fodder_v1"
_FIXED_MANA = re.compile(r"(?:\{(?:[0-9]+|[WUBRGC])\})+", re.IGNORECASE)


@dataclass(frozen=True)
class PackageConfig(Artifact):
    version: str = "package-builder-v2"
    max_members: int = 4
    max_expansions_per_template: int = 10_000
    beam_width: int = 32
    include_reserve: bool = False
    edge_support_weight: float = 0.75
    efficiency_weight: float = 0.15
    alignment_weight: float = 0.10

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.version or type(self.max_members) is not int or self.max_members < 2:
            raise ValueError("A versioned package configuration requires room for two participants.")
        if any(type(limit) is not int or limit < 1 for limit in (self.max_expansions_per_template, self.beam_width)):
            raise ValueError("Package work limits must be positive integers.")
        weights = self.edge_support_weight, self.efficiency_weight, self.alignment_weight
        if any(not 0 <= weight <= 1 for weight in weights) or not isclose(fsum(weights), 1):
            raise ValueError("Package score weights must be in [0, 1] and sum to one.")


class PackageBuildError(ValueError):
    def __init__(self, violations: tuple[ValidationIssue, ...]):
        self.violations = violations
        super().__init__(" ".join(issue.message for issue in violations))


@dataclass(frozen=True)
class _Path:
    roles: tuple[tuple[str, str], ...]
    edges: tuple[PairwiseSynergyEdge, ...]
    recursion: _Ability | None = None
    renewal: str | None = None
    renewal_requirements: tuple[str, ...] = ()
    witnesses: tuple[tuple[_Ability, str], ...] = ()
    self_sustaining: bool = False

    @property
    def card_ids(self) -> set[str]:
        return {card_id for _, card_id in self.roles}


def _rule_id(edge: PairwiseSynergyEdge) -> str:
    return edge.evidence[0].rule_id


def _span_matches(card: CardSnapshot, evidence: RuleEvidence) -> bool:
    text = {"oracle_text": card.oracle_text, "type_line": card.type_line}.get(evidence.source_field)
    return bool(
        text is not None and evidence.span_start is not None and evidence.span_end is not None
        and evidence.span_end <= len(text) and text[evidence.span_start:evidence.span_end] == evidence.source_text
    )


def _is_self_return(ability: _Ability) -> bool:
    cap = ability.capability
    front_type = (ability.card.type_line or "").split(" // ", 1)[0].partition("—")[0].casefold()
    return (
        cap.id == "return_from_graveyard" and cap.object.reference == "self"
        and "creature" in front_type.split() and cap.source_zone == "graveyard"
        and cap.destination_zone == "battlefield" and cap.owner == cap.controller == "you"
        and cap.timing == "activated" and cap.repeatability == "repeatable"
        and len(cap.costs) == 1 and bool(_FIXED_MANA.fullmatch(cap.costs[0]))
        and not cap.conditions and not cap.object.restrictions
        and cap.evidence.source_field == "oracle_text"
        and " // " not in (ability.card.oracle_text or "")[:cap.evidence.end]
        and "•" not in (ability.card.oracle_text or "")[:cap.evidence.start]
    )


def _fits_creature(
    cap: MechanicalCapability, types: set[str], subtypes: set[str], *,
    token: bool, colors: set[str] | None = None,
) -> bool:
    obj = cap.object
    restrictions = set(obj.restrictions)
    required_types = set(obj.types)
    return (
        obj.reference != "self" and obj.kind not in {"player", "spell"}
        and (obj.kind != "token" or token)
        and (not required_types or bool(types & required_types))
        and ("all_listed_types" not in restrictions or required_types <= types)
        and (not obj.subtypes or bool(subtypes & set(obj.subtypes)))
        and (not obj.colors or bool(colors and colors.intersection(obj.colors)))
        and ("nontoken" not in restrictions or not token)
        and "noncreature" not in restrictions
        and ("nonland" not in restrictions or "land" not in types)
        and restrictions <= {"all_listed_types", "nontoken", "nonland"}
    )


def _compatible_fodder(
    seed: PairwiseSynergyEdge, outlet: _Ability, payoff: _Ability, fodder: _Ability, *, token: bool,
) -> str | None:
    # This template closes one sacrifice at a time, through the same outlet ability.
    if outlet.capability.quantity.kind != "fixed" or outlet.capability.quantity.value != 1:
        return "outlet_quantity_unresolved"
    if fodder.card_id == payoff.card_id or (not token and fodder.card_id == outlet.card_id):
        return "fodder_must_be_distinct"
    if token:
        types = set(fodder.capability.object.types)
        subtypes = set(fodder.capability.object.subtypes)
        colors = set(fodder.capability.object.colors)
    else:
        type_line = (fodder.card.type_line or "").split(" // ", 1)[0].casefold()
        prefix, _, suffix = type_line.partition("—")
        types, subtypes = set(prefix.split()), set(suffix.split())
        colors = set(fodder.card.colors or ())
    if "creature" not in types or any(
        not _fits_creature(item.capability, types, subtypes, token=token, colors=colors) for item in (outlet, payoff)
    ):
        return "incompatible_fodder"
    # Recurring cards must reach their graveyard even if the payoff observes
    # the sacrifice itself. Token paths only need a death for death payoffs.
    if (seed.event_type == "death" or not token) and any(
        prevents_creature_death(
            item.card, controller=outlet.capability.controller,
            sacrificed_kind="token" if token else "card",
            sacrificed_card_id=None if token else fodder.card_id,
        )
        for item in (outlet, payoff, fodder)
    ):
        return "death_replaced"
    return None


def _outlet_can_repeat(outlet: _Ability) -> bool:
    cap = outlet.capability
    if cap.timing not in {"activated", "sorcery_speed"} or cap.repeatability != "repeatable" or cap.conditions:
        return False
    costs = [part.strip() for cost in cap.costs for part in cost.split(",")]
    fodder_cost = cap.evidence.oracle_span
    if costs.count(fodder_cost) != 1:
        return False
    costs.remove(fodder_cost)
    return all(cost == "{T}" or _FIXED_MANA.fullmatch(cost) for cost in costs)


def _printed_creature(card: CardSnapshot) -> tuple[set[str], set[str]]:
    type_line = (card.type_line or "").split(" // ", 1)[0].casefold()
    prefix, _, suffix = type_line.partition("—")
    return set(prefix.split()), set(suffix.split())


def _food_recursion(
    base: _Path, producer: _Ability, consumer: _Ability, outlets: list[_Ability],
) -> _Path | None:
    types, subtypes = _printed_creature(consumer.card)
    if (
        "creature" not in types or producer.capability.timing not in {"activated", "sorcery_speed"}
        or producer.capability.controller != "you" or producer.capability.conditions
        or producer.capability.quantity.kind != "fixed" or not producer.capability.quantity.value
        or consumer.capability.conditions
        or not any("{T}" in cost for cost in producer.capability.costs)
        or any(prevents_creature_death(
            item.card, controller="you", sacrificed_kind="card", sacrificed_card_id=consumer.card_id,
        ) for item in (producer, consumer))
    ):
        return None
    for outlet in outlets:
        cap = outlet.capability
        text = outlet.card.oracle_text or ""
        if (
            cap.costs == producer.capability.costs and cap.evidence.ability_id == producer.capability.evidence.ability_id
            and text.rfind("\n", 0, cap.evidence.start) == text.rfind("\n", 0, producer.capability.evidence.start)
            and cap.quantity.kind == "fixed" and cap.quantity.value == 1 and _outlet_can_repeat(outlet)
            and _fits_creature(cap, types, subtypes, token=False, colors=set(consumer.card.colors or ()))
        ):
            return replace(
                base, renewal="Sacrifice the creature to make Food, then spend that Food to return the creature. "
                "The Food generator must be ready to tap again before another cycle.",
                renewal_requirements=("food_and_creature_are_distinct_cost_objects", "renew_generator_tap_between_cycles"),
                witnesses=((outlet, "returned_creature_pays_food_outlet_v1"),),
            )
    return None


def _life_feedback(
    base: _Path, reverse: PairwiseSynergyEdge, abilities: dict[tuple[str, str], _Ability],
) -> _Path | None:
    edges = (base.edges[0], reverse)
    for emitted, observed in (edges, edges[::-1]):
        producer = abilities[(emitted.event_ids[0], emitted.source_capability)].capability
        observer = abilities[(observed.event_ids[1], observed.target_capability)].capability
        if (
            producer.evidence.ability_id != observer.evidence.ability_id
            or not observer.evidence.start <= producer.evidence.start < producer.evidence.end <= observer.evidence.end
            or producer.timing != "triggered" or producer.modality != "mandatory" or observer.modality != "mandatory"
            or producer.costs or producer.additional_cast_costs or observer.conditions
            or producer.quantity.expression != "that much"
            or producer.evidence.oracle_span.casefold().rstrip(".") != observer.resolution.casefold().rstrip(".")
        ):
            return None
    return replace(
        base, edges=edges, self_sustaining=True,
        renewal="Each equal life change triggers the opposite effect without another payment until a terminal game state or interruption.",
        renewal_requirements=(
            "positive_life_change_to_start", "qualifying_life_changes_not_prevented",
            "legal_opponent_target_with_remaining_life",
        ),
    )


class SynergyPackageBuilder:
    def __init__(
        self, config: PackageConfig = PackageConfig(), *,
        templates: tuple[PackageTemplate, ...] = PACKAGE_TEMPLATES,
        synergy_context: SynergyCatalogContext | None = None,
    ):
        self._config = config
        self._catalog = synergy_context or load_synergy_context()
        self._catalog_fingerprint = pairwise_catalog_fingerprint(self._catalog)
        self._rules = {rule.id: rule for rule in self._catalog.rules}
        self._templates = tuple(sorted(templates, key=lambda item: (item.archetype, item.seed_rules)))
        if any(
            not all((item.archetype, item.source_role, item.target_role, item.seed_rules))
            or item.extension not in {"none", "sacrifice_fodder", "food_recursion", "life_feedback"}
            for item in self._templates
        ) or len(set(self._templates)) != len(self._templates):
            raise ValueError("Package templates must have distinct, explicit archetypes, rules, and roles.")
        self._cache: PackageAnalysis | None = None

    def build(self, gate: EligibilityGate, pool: CandidatePool, analysis: PairwiseSynergyAnalysis) -> PackageAnalysis:
        abilities, edges = self._validate_inputs(gate, pool, analysis)
        provenance = PackageProvenance(
            gate.snapshot.snapshot_id, pool.pool_id, analysis.provenance.pool_fingerprint,
            analysis.analysis_id, artifact_digest([asdict(edge) for edge in edges]),
            analysis.rule_catalog_version, TEMPLATE_CATALOG_VERSION,
            artifact_digest([asdict(item) for item in self._templates]),
            self._config.version, artifact_digest(asdict(self._config)),
        )
        analysis_id = artifact_digest({
            "run_id": pool.run_id, "provenance": asdict(provenance),
            "truncated_pairwise": analysis.diagnostics.work_limit_reached,
        })
        if self._cache is not None and self._cache.analysis_id == analysis_id:
            return self._cache
        anchors = frozenset(gate.context.commander_card_ids)
        result = self._search(pool.run_id, analysis_id, provenance, edges, abilities, anchors,
                              analysis.diagnostics.work_limit_reached)
        self._cache = result
        return result

    def _validate_inputs(
        self, gate: EligibilityGate, pool: CandidatePool, analysis: PairwiseSynergyAnalysis,
    ) -> tuple[dict[tuple[str, str], _Ability], tuple[PairwiseSynergyEdge, ...]]:
        context, provenance = gate.context, analysis.provenance
        issues = list(gate.violations)
        original = pool.provenance
        if original is not None:
            try:
                selected = normalize_priorities(
                    tuple(SelectedStrategy(item.strategy_id, item.weight) for item in context.strategy_preferences),
                    self._catalog,
                )
            except ValueError as error:
                raise PackageBuildError((ValidationIssue("invalid_strategy_preferences", str(error)),)) from error
            if (
                selected != {item.strategy_id: item.weight for item in original.context.strategy_preferences}
                or original.context.config_version != context.config_version
                or original.evaluation_snapshot_id != provenance.evaluation_snapshot_id
            ):
                issues.append(ValidationIssue("package_context_mismatch", "The pool uses different strategy or evaluation inputs."))
        if (
            (context.run_id, pool.pool_id, artifact_digest(pool.to_json()), gate.snapshot.snapshot_id)
            != (analysis.run_id, provenance.pool_id, provenance.pool_fingerprint, provenance.collection_snapshot_id)
            or pool.run_id != context.run_id
            or provenance.commander_card_ids != context.commander_card_ids
            or provenance.engine_versions != context.engine_versions
            or provenance.ruleset_version != context.ruleset_version
            or provenance.oracle_parser_version != PARSER_VERSION
            or provenance.capability_extractor_version != CAPABILITY_VERSION
            or provenance.strategy_catalog_version != self._catalog.strategies.version
            or analysis.rule_catalog_version != self._catalog.rule_version
            or provenance.rule_catalog_fingerprint != self._catalog_fingerprint
        ):
            issues.append(ValidationIssue("analysis_context_mismatch", "The pairwise analysis belongs to different pipeline inputs."))
        if issues:
            raise PackageBuildError(tuple(issues))

        anchors = set(context.commander_card_ids)
        all_pool = {card.card_id: card for card in (*pool.candidates, *pool.reserve)}
        active = anchors | {card.card_id for card in pool.candidates}
        if self._config.include_reserve:
            active.update(all_pool)
        snapshots = {card.card_id: card for card in gate.snapshot.cards}
        excluded = {item.card_id for item in pool.excluded if item.code == "user_excluded"}
        if pool.provenance:
            excluded.update(pool.provenance.excluded_card_ids)
        for card_id in sorted(active - anchors):
            eligible = gate.check_card(card_id)
            card = all_pool[card_id]
            issues.extend(eligible.violations)
            if (
                card_id in excluded or card.provenance.collection_snapshot_id != gate.snapshot.snapshot_id
                or card.provenance.engine_versions != context.engine_versions
                or card.available_quantity != eligible.available_quantity
                or card.owned_printing_ids != eligible.owned_printing_ids
            ):
                issues.append(ValidationIssue("package_inventory_mismatch", "Package inventory is inconsistent.", card_id))
        if issues:
            raise PackageBuildError(tuple(dict.fromkeys(issues)))
        abilities = self._abilities(pool, {card_id: snapshots[card_id] for card_id in active}, frozenset(anchors))
        unique_edges: dict[str, PairwiseSynergyEdge] = {}
        for edge in analysis.edges:
            if edge.edge_id in unique_edges and unique_edges[edge.edge_id] != edge:
                issues.append(ValidationIssue("conflicting_edge_id", f"Conflicting data for edge {edge.edge_id}."))
            unique_edges[edge.edge_id] = edge
        edges = []
        for edge in unique_edges.values():
            endpoints = (edge.source_card_id, edge.target_card_id)
            if not set(endpoints) <= anchors | all_pool.keys():
                issues.append(ValidationIssue("unknown_edge_endpoint", f"Missing pool endpoint for {edge.edge_id}."))
                continue
            if not set(endpoints) <= active:
                continue
            rule = self._rules.get(_rule_id(edge))
            if (
                rule is None or (rule.source, rule.target, rule.event) != (
                    edge.source_capability, edge.target_capability, edge.event_type,
                )
                or edge.relation != canonical_rule_relation(rule)
                or edge.rule_version != analysis.rule_catalog_version or not edge.directional
                or edge.confidence not in {"conditional", "rule_verified"}
                or set(edge.commander_anchor_ids) != anchors.intersection(endpoints)
                or tuple(span.card_id for span in edge.evidence) != endpoints
                or edge.event_ids != tuple(
                    f"{span.card_id}:{span.source_field}:{span.ability_id}:{span.span_start}:{span.span_end}"
                    for span in edge.evidence
                )
                or any(span.rule_id != rule.id or span.card_id not in snapshots
                       or not _span_matches(snapshots[span.card_id], span) for span in edge.evidence)
            ):
                issues.append(ValidationIssue("unverified_package_edge", f"Unverified package edge {edge.edge_id}."))
                continue
            source = abilities.get((edge.event_ids[0], edge.source_capability))
            target = abilities.get((edge.event_ids[1], edge.target_capability))
            if source is None or target is None:
                issues.append(ValidationIssue(
                    "edge_capability_mismatch", f"Edge {edge.edge_id} has no matching directional capability evidence.",
                ))
                continue
            if not edge_is_supported(rule, source, target, edge):
                issues.append(ValidationIssue(
                    "invalid_edge_semantics", f"Edge {edge.edge_id} has incompatible events or missing prerequisites.",
                ))
                continue
            edges.append(edge)
        if issues:
            raise PackageBuildError(tuple(dict.fromkeys(issues)))
        return abilities, tuple(sorted(edges, key=lambda edge: edge.edge_id))

    def _abilities(
        self, pool: CandidatePool, cards: dict[str, CardSnapshot], anchors: frozenset[str],
    ) -> dict[tuple[str, str], _Ability]:
        index = {}
        for candidate in (*pool.candidates, *pool.reserve):
            if candidate.card_id not in cards or candidate.evaluations is None:
                continue
            profile = candidate.evaluations.strategy_synergy
            if (
                profile is None or not profile.eligible or profile.commander_id not in anchors
                or profile.engine_version != candidate.provenance.engine_versions.strategy_synergy
            ):
                continue
            if not {
                "oracle_parser": PARSER_VERSION, "capabilities": CAPABILITY_VERSION,
                "interaction_rules": self._catalog.rule_version, "strategies": self._catalog.strategies.version,
            }.items() <= dict(profile.catalog_versions).items():
                continue
            card = cards[candidate.card_id]
            parsed = parse_card_snapshot(card, self._catalog)
            for capability in profile.mechanical_capabilities:
                item = _endpoint(card, parsed, capability)
                if not isinstance(item, str):
                    index[item.event_id, capability.id] = item
        for card_id in sorted(anchors):
            card = cards[card_id]
            parsed = parse_card_snapshot(card, self._catalog)
            profile = extract_mechanical_capabilities(parsed, name=card.name, type_line=card.type_line or "")
            for capability in profile.capabilities:
                item = _endpoint(card, parsed, capability)
                if not isinstance(item, str):
                    index[item.event_id, capability.id] = item
        return index

    def _search(
        self, run_id: str, analysis_id: str, provenance: PackageProvenance,
        edges: tuple[PairwiseSynergyEdge, ...], abilities: dict[tuple[str, str], _Ability],
        anchors: frozenset[str], truncated_pairs: bool,
    ) -> PackageAnalysis:
        by_rule = defaultdict(list)
        by_outlet = defaultdict(list)
        life_reverse = defaultdict(list)
        for edge in sorted(edges, key=lambda item: (-item.strength, item.edge_id)):
            rule = _rule_id(edge)
            by_rule[rule].append(edge)
            if rule == "fodder_feeds_sacrifice_outlet":
                by_outlet[edge.event_ids[1]].append(edge)
            if rule == "lifegain_triggers_payoff":
                life_reverse[edge.source_card_id, edge.target_card_id].append(edge)
        local_tokens = defaultdict(list)
        outlets_by_card = defaultdict(list)
        for item in sorted(abilities.values(), key=lambda ability: ability.event_id):
            if (
                item.capability.id == "create_token" and item.capability.timing == "triggered"
                and item.capability.conditions in {("this creature enters",), ("@self enters",)}
                and item.capability.controller == "you"
            ):
                local_tokens[item.card_id].append(item)
            if item.capability.id == "sacrifice":
                outlets_by_card[item.card_id].append(item)
        recurring = tuple(sorted((item for item in abilities.values() if _is_self_return(item)),
                                 key=lambda item: item.event_id))
        packages: dict[str, SynergyPackage] = {}
        partial: dict[tuple, PartialPackage] = {}
        rejections: Counter[str] = Counter()
        attempts: Counter[str] = Counter()
        limited: set[str] = set()
        for template in self._templates:
            archetype = template.archetype
            seeds = sorted((edge for rule in template.seed_rules for edge in by_rule[rule]),
                           key=lambda item: (-item.strength, item.edge_id))
            for seed in seeds:
                if attempts[archetype] >= self._config.max_expansions_per_template:
                    limited.add(archetype)
                    break
                attempts[archetype] += 1
                renewal, renewal_requirements = self._renewal(seed, abilities)
                base = _Path(((template.source_role, seed.source_card_id), (template.target_role, seed.target_card_id)),
                             (seed,), renewal=renewal, renewal_requirements=renewal_requirements)
                if template.extension == "none":
                    package = self._materialize(template, base, anchors)
                    packages.setdefault(package.package_id, package)
                    continue
                if template.extension == "food_recursion":
                    producer = abilities[(seed.event_ids[0], seed.source_capability)]
                    consumer = abilities[(seed.event_ids[1], seed.target_capability)]
                    if path := _food_recursion(base, producer, consumer, outlets_by_card[seed.source_card_id]):
                        package = self._materialize(template, path, anchors)
                        packages.setdefault(package.package_id, package)
                    else:
                        rejections["food_feedback_unresolved"] += 1
                    continue
                if template.extension == "life_feedback":
                    for index, reverse in enumerate(life_reverse[seed.target_card_id, seed.source_card_id]):
                        if index >= self._config.beam_width or attempts[archetype] >= self._config.max_expansions_per_template:
                            limited.add(archetype)
                            break
                        attempts[archetype] += 1
                        if path := _life_feedback(base, reverse, abilities):
                            package = self._materialize(template, path, anchors)
                            packages.setdefault(package.package_id, package)
                        else:
                            rejections["life_feedback_unresolved"] += 1
                    continue
                complete = False
                examined = 0
                outlet = abilities.get((seed.event_ids[0], seed.source_capability))
                payoff = abilities.get((seed.event_ids[1], seed.target_capability))
                reason = "missing_fodder"
                if outlet is None or payoff is None:
                    reason = "missing_ability_profile"
                else:
                    for token_edge, fodder in self._fodder(
                        by_outlet[seed.event_ids[0]], local_tokens[seed.source_card_id], recurring, abilities,
                    ):
                        if examined >= self._config.beam_width or attempts[archetype] >= self._config.max_expansions_per_template:
                            limited.add(archetype)
                            reason = "search_limit"
                            break
                        attempts[archetype] += 1
                        examined += 1
                        if len(base.card_ids | {fodder.card_id}) > self._config.max_members:
                            limited.add(archetype)
                            reason = "member_limit"
                            continue
                        token = fodder.capability.id == "create_token"
                        if rejected := _compatible_fodder(seed, outlet, payoff, fodder, token=token):
                            rejections[rejected] += 1
                            continue
                        if token and token_edge is None:
                            rule = self._rules.get("fodder_feeds_sacrifice_outlet")
                            match = None if rule is None else validate_interaction(
                                rule, fodder.capability, outlet.capability,
                                source_type=fodder.card.type_line or "", target_type=outlet.card.type_line or "",
                                same_card=True,
                            )
                            if match is None or match.status not in {MatchStatus.VALIDATED, MatchStatus.CONDITIONAL}:
                                rejections["unverified_local_fodder"] += 1
                                continue
                            path = replace(
                                base, roles=(*base.roles, ("fodder", fodder.card_id)),
                                renewal_requirements=match.dependencies,
                                witnesses=((fodder, "fodder_feeds_sacrifice_outlet"),),
                            )
                        else:
                            path = self._extend(base, token_edge, fodder, outlet)
                        package = self._materialize(template, path, anchors)
                        packages.setdefault(package.package_id, package)
                        complete = True
                if not complete:
                    if examined and reason == "missing_fodder":
                        reason = "no_compatible_fodder"
                    members = tuple(sorted(base.card_ids - anchors))
                    commanders = tuple(sorted(base.card_ids & anchors))
                    key = archetype, members, commanders, seed.event_ids, _rule_id(seed)
                    partial.setdefault(key, PartialPackage(
                        archetype, members, commanders, (seed.edge_id,), ("fodder",), reason,
                    ))

        ordered = tuple(sorted(packages.values(), key=lambda item: (-item.score, item.package_id)))
        incomplete = tuple(partial[key] for key in sorted(partial))
        conditional = sum(item.dependency_status == "conditional" for item in ordered)
        diagnostics = PackageDiagnostics(
            len(edges), sum(attempts.values()), len(ordered) - conditional, conditional, len(incomplete),
            tuple(sorted(rejections.items())), tuple(sorted(limited)), truncated_pairs,
        )
        return PackageAnalysis(analysis_id, run_id, ordered, incomplete, diagnostics, provenance)

    @staticmethod
    def _fodder(
        token_edges: list[PairwiseSynergyEdge], local_tokens: list[_Ability], recurring: tuple[_Ability, ...],
        abilities: dict[tuple[str, str], _Ability],
    ) -> Iterator[tuple[PairwiseSynergyEdge | None, _Ability]]:
        for edge in token_edges:
            if ability := abilities.get((edge.event_ids[0], edge.source_capability)):
                yield edge, ability
        for ability in local_tokens:
            yield None, ability
        for ability in recurring:
            yield None, ability

    @staticmethod
    def _renewal(
        seed: PairwiseSynergyEdge, abilities: dict[tuple[str, str], _Ability],
    ) -> tuple[str | None, tuple[str, ...]]:
        enabler = abilities.get((seed.event_ids[0], seed.source_capability))
        payoff = abilities.get((seed.event_ids[1], seed.target_capability))
        if enabler is None or payoff is None:
            return None, ()
        producer = payoff if seed.relation == "amplifies" else enabler
        cap = producer.capability
        costs = tuple(part.strip() for cost in cap.costs for part in cost.split(","))
        activate = cap.timing in {"activated", "sorcery_speed"} and cap.repeatability == "repeatable" and bool(costs)
        pays_mana_or_tap = all(_FIXED_MANA.fullmatch(cost) or cost == "{T}" for cost in costs)
        if _rule_id(seed) == "blink_retriggers_etb" and payoff.capability.object.reference == "self":
            if activate and pays_mana_or_tap:
                return "Activate the blink again when its cost and timing allow, returning the same payoff creature.", ()
            if any(keyword.casefold() == "rebound" for keyword in enabler.card.keywords) and not activate:
                return (
                    "Rebound permits one optional second cast at your next upkeep after resolving the spell from hand.",
                    ("rebound_first_cast_from_hand_and_resolves", "legal_target_on_rebound_cast"),
                )
        if seed.relation == "amplifies" and seed.event_type == "counter_placement" and activate and pays_mana_or_tap:
            return "Pay the counter placement activation cost again and keep the replacement effect active.", ()
        if seed.event_type == "enter" and cap.id == "create_token" and activate and all(
            _FIXED_MANA.fullmatch(cost) or cost == "{T}" or cost.casefold() == "sacrifice another creature"
            for cost in costs
        ):
            return (
                "Supply new mana, an untapped generator when required, and fresh fodder for each activation.",
                ("renew_activated_token_creation_costs",),
            )
        return None, ()

    @staticmethod
    def _extend(base: _Path, token_edge: PairwiseSynergyEdge | None, fodder: _Ability, outlet: _Ability) -> _Path:
        roles = (*base.roles, ("fodder", fodder.card_id))
        if token_edge is not None:
            return _Path(roles, (token_edge, *base.edges))
        renewal = None
        if _outlet_can_repeat(outlet):
            renewal = (
                f"Sacrifice {fodder.card.name} and pay {fodder.capability.costs[0]} to return it from the graveyard "
                "before repeating. Pay the outlet's costs and satisfy its timing on every cycle."
            )
        return _Path(roles, base.edges, fodder, renewal)

    def _materialize(self, template: PackageTemplate, path: _Path, anchors: frozenset[str]) -> SynergyPackage:
        nodes = path.card_ids
        members = tuple(sorted(nodes - anchors))
        commanders = tuple(sorted(nodes & anchors))
        roles = tuple((role, tuple(sorted({card_id for key, card_id in path.roles if key == role})))
                      for role in sorted({key for key, _ in path.roles}))
        effects = {event for edge in path.edges for event in edge.event_ids}
        # The final event is the package's joint benefit; feeder edges enable it.
        payoff = path.edges[-1]
        benefit_event = payoff.event_ids[1]
        resources = {f"{edge.edge_id}:{cost}" for edge in path.edges for cost in edge.resource_requirements}
        timing = {f"{edge.edge_id}:{item}" for edge in path.edges for item in edge.timing_constraints}
        requirements = {item for edge in path.edges for item in edge.prerequisites}
        requirements.update(path.renewal_requirements)
        limitations = {item for edge in path.edges for item in edge.limitations}
        evidence = {item for edge in path.edges for item in edge.evidence}
        strategies = {item for edge in path.edges for item in edge.strategy_ids}
        explanations = [edge.explanation for edge in path.edges]
        for witness, rule in path.witnesses:
            effects.add(witness.event_id)
            evidence.add(witness.evidence(rule))
            explanations.insert(0, f"{witness.card.name} also supplies a separate ability: {witness.capability.evidence.oracle_span}.")
        if path.recursion:
            recursion = path.recursion
            effects.add(recursion.event_id)
            resources.add(f"{recursion.card_id}:return_from_graveyard:{recursion.capability.costs[0]}")
            timing.add(f"{recursion.card_id}:activate_from_graveyard_after_sacrifice")
            requirements.update(("fodder_reaches_your_graveyard", "pay_recursion_cost"))
            evidence.add(recursion.evidence(_RECURSION_RULE))
            explanations.insert(0, f"{recursion.card.name} can return itself and supply the same outlet again.")
        if path.renewal:
            explanations.append(path.renewal)
        if resources:
            requirements.add("pay_recorded_resource_costs")
        # One support value per effect, followed by a bottleneck score for the path.
        effect_support: dict[str, float] = {}
        for edge in path.edges:
            event = edge.event_ids[1]
            effect_support[event] = max(effect_support.get(event, 0), edge.strength)
        support = min(effect_support.values())
        efficiency = 1 / (1 + len({cost.split(":", 1)[1] for cost in resources}))
        alignment = float(bool(strategies or commanders))
        score = self._config.edge_support_weight * support + self._config.efficiency_weight * efficiency
        score += self._config.alignment_weight * alignment
        signature = (template.archetype, roles, commanders,
                     tuple((_rule_id(edge), edge.event_ids) for edge in path.edges),
                     path.recursion.event_id if path.recursion else None,
                     tuple((witness.event_id, rule) for witness, rule in path.witnesses))
        return SynergyPackage(
            artifact_digest(signature), members, roles, tuple(edge.edge_id for edge in path.edges),
            tuple(sorted(requirements)), path.renewal is not None, path.self_sustaining, " ".join(explanations),
            commander_anchor_ids=commanders, package_archetype=template.archetype,
            dependency_status="conditional" if requirements else "complete",
            effect_ids=tuple(sorted(effects)), benefit_event_ids=(benefit_event,),
            evidence=tuple(sorted(evidence, key=lambda item: (item.card_id, item.source_field, item.span_start,
                                                               item.span_end, item.ability_id, item.rule_id))),
            resource_requirements=tuple(sorted(resources)), timing_constraints=tuple(sorted(timing)),
            limitations=tuple(sorted(limitations)), strategy_ids=tuple(sorted(strategies)),
            renewal_path=path.renewal, score=round(score, 6),
            score_components=(("edge_support", support), ("resource_efficiency", efficiency),
                              ("context_alignment", alignment)),
        )
