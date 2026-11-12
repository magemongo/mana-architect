from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

from mana_architect.catalog import StrategyCatalog
from mana_architect.classification import ClassificationEvidence, ClassificationResult
from mana_architect.commander_affinity import (
    CommanderAffinityProfile,
    validate_commander_affinity_profile,
)
from mana_architect.overrides import (
    ClassificationDecision,
    ManualOverride,
    resolve_classification_overrides,
)


@dataclass(frozen=True)
class CardScoreInput:
    oracle_id: str
    name: str
    color_identity: tuple[str, ...] | None
    format_legal: bool | None
    classifications: ClassificationResult
    available_quantity: int | None = None
    already_used_quantity: int = 0


@dataclass(frozen=True)
class ScoreReason:
    code: str
    contribution: float
    rule_id: str | None = None
    tag_id: str | None = None
    role_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class CardSynergyScore:
    card_oracle_id: str
    card_name: str
    commander_oracle_id: str
    strategy_id: str
    score: float | None
    tier: str | None
    eligible: bool
    rankable: bool
    eligibility_reasons: tuple[str, ...]
    rankability_reasons: tuple[str, ...]
    commander_level: str | None
    commander_factor: float | None
    components: Mapping[str, float]
    reasons: tuple[ScoreReason, ...]
    available_quantity: int | None
    catalog_version: str


def score_card_for_strategy(
    card: CardScoreInput,
    commander_oracle_id: str,
    commander_color_identity: tuple[str, ...],
    commander_affinities: CommanderAffinityProfile,
    strategy_id: str,
    catalog: StrategyCatalog,
    *,
    required_roles: tuple[str, ...] | None = None,
    only_collection: bool = False,
    overrides: tuple[ManualOverride, ...] = (),
    user_priority_adjustment: float = 0.0,
) -> CardSynergyScore:
    """Score one card for an explicitly selected strategy and commander."""
    strategy = next(
        (item for item in catalog.strategies if item.id == strategy_id and item.active),
        None,
    )
    if strategy is None:
        raise ValueError(f"Estratégia ativa inexistente no catálogo: '{strategy_id}'.")
    if not isinstance(commander_oracle_id, str) or not commander_oracle_id.strip():
        raise ValueError("commander_oracle_id deve ser um texto não vazio.")
    if commander_affinities.commander_oracle_id != commander_oracle_id:
        raise ValueError("O perfil de afinidades não pertence ao comandante informado.")
    validate_commander_affinity_profile(commander_affinities, catalog)
    if not isinstance(card.oracle_id, str) or not card.oracle_id.strip():
        raise ValueError("card.oracle_id deve ser um texto não vazio.")
    if not isinstance(card.name, str) or not card.name.strip():
        raise ValueError("card.name deve ser um texto não vazio.")
    if card.available_quantity is not None and (
        isinstance(card.available_quantity, bool)
        or not isinstance(card.available_quantity, int)
        or card.available_quantity < 0
    ):
        raise ValueError("available_quantity deve ser inteiro não negativo ou None.")
    if (
        isinstance(card.already_used_quantity, bool)
        or not isinstance(card.already_used_quantity, int)
        or card.already_used_quantity < 0
    ):
        raise ValueError("already_used_quantity deve ser inteiro não negativo.")
    if not isinstance(only_collection, bool):
        raise ValueError("only_collection deve ser booleano.")
    if (
        isinstance(user_priority_adjustment, bool)
        or not isinstance(user_priority_adjustment, (int, float))
        or not math.isfinite(user_priority_adjustment)
    ):
        raise ValueError("user_priority_adjustment deve ser um número finito.")
    if not (
        catalog.scoring.user_priority_adjustment["minimum"]
        <= user_priority_adjustment
        <= catalog.scoring.user_priority_adjustment["maximum"]
    ):
        raise ValueError(
            "user_priority_adjustment está fora da faixa configurada."
        )

    identities = set(commander_color_identity)
    if any(color not in {"W", "U", "B", "R", "G"} for color in identities):
        raise ValueError("Identidade de cor do comandante contém símbolo inválido.")
    card_identity = card.color_identity
    if card_identity is not None and any(
        color not in {"W", "U", "B", "R", "G"} for color in card_identity
    ):
        raise ValueError("Identidade de cor da carta contém símbolo inválido.")

    reasons: list[str] = []
    if card.format_legal is not True:
        reasons.append(
            "FORMAT_ILLEGAL"
            if card.format_legal is False
            else "FORMAT_LEGALITY_UNKNOWN"
        )
    if card_identity is None:
        reasons.append("COLOR_IDENTITY_UNKNOWN")
    elif not set(card_identity).issubset(identities):
        reasons.append("COLOR_IDENTITY_INCOMPATIBLE")

    available = _available_quantity(card)
    if only_collection and available is None:
        reasons.append("COLLECTION_AVAILABILITY_UNKNOWN")
    elif only_collection and available == 0:
        reasons.append("NOT_AVAILABLE_IN_COLLECTION")

    eligible = not reasons
    affinity = next(
        (
            item
            for item in commander_affinities.strategy_affinities
            if item.strategy_id == strategy_id
        ),
        None,
    )
    if affinity is None:
        return CardSynergyScore(
            card_oracle_id=card.oracle_id,
            card_name=card.name,
            commander_oracle_id=commander_oracle_id,
            strategy_id=strategy_id,
            score=None,
            tier=None,
            eligible=eligible,
            rankable=False,
            eligibility_reasons=tuple(reasons),
            rankability_reasons=("NO_COMMANDER_AFFINITY",),
            commander_level=None,
            commander_factor=None,
            components=MappingProxyType({}),
            reasons=(),
            available_quantity=available,
            catalog_version=catalog.catalog_version,
        )

    if not eligible:
        return CardSynergyScore(
            card_oracle_id=card.oracle_id,
            card_name=card.name,
            commander_oracle_id=commander_oracle_id,
            strategy_id=strategy_id,
            score=None,
            tier=None,
            eligible=False,
            rankable=False,
            eligibility_reasons=tuple(reasons),
            rankability_reasons=(),
            commander_level=affinity.level,
            commander_factor=catalog.scoring.commander_levels[affinity.level],
            components=MappingProxyType({}),
            reasons=(),
            available_quantity=available,
            catalog_version=catalog.catalog_version,
        )

    decisions = resolve_classification_overrides(
        card.classifications,
        overrides,
        catalog,
    )
    effective_evidence = tuple(
        evidence
        for decision in decisions
        if decision.included
        for evidence in decision.automatic_evidence
    )
    manual_inclusions = tuple(
        decision
        for decision in decisions
        if decision.included
        and decision.override is not None
    )
    if required_roles is not None:
        catalog_role_ids = {item.id for item in catalog.roles}
        unknown_roles = set(required_roles) - catalog_role_ids
        if unknown_roles:
            raise ValueError(
                f"Funções inexistentes no catálogo: {', '.join(sorted(unknown_roles))}."
            )
    tag_weights = {item.tag_id: item.weight for item in strategy.tag_affinities}
    components: dict[str, float | None] = {
        "strategy_evidence": _strategy_evidence_component(
            effective_evidence,
            strategy_id,
            tag_weights,
            manual_inclusions,
        ),
        "shared_synergy_tags": _tag_overlap_component(
            effective_evidence,
            commander_affinities,
            tag_weights,
            manual_inclusions,
        ),
        "role_coverage": (
            _role_coverage_component(
                effective_evidence,
                required_roles,
                manual_inclusions,
            )
            if required_roles
            else None
        ),
        "commander_text_support": affinity.strength_score,
    }

    active_weights = {
        name: weight
        for name, weight in catalog.scoring.components.items()
        if weight > 0 and components[name] is not None
    }
    if not active_weights or sum(active_weights.values()) <= 0:
        raise ValueError("Nenhum componente de score ativo está disponível.")
    denominator = sum(active_weights.values())
    commander_factor = catalog.scoring.commander_levels[affinity.level]
    component_contributions = {
        name: (
            100.0
            * active_weights[name]
            * float(components[name])
            / denominator
            * commander_factor
        )
        for name in active_weights
    }
    raw_score = sum(component_contributions.values()) + float(
        user_priority_adjustment
    )
    capped_score, cap_name = _apply_evidence_caps(
        raw_score,
        effective_evidence,
        catalog,
    )
    minimum = catalog.scoring.normalization.minimum
    maximum = catalog.scoring.normalization.maximum
    clamped_score = min(maximum, max(minimum, capped_score))
    rounded_score = round(clamped_score, catalog.scoring.normalization.round_digits)

    score_reasons = _score_reasons(
        effective_evidence,
        manual_inclusions,
        tag_weights,
        commander_affinities,
        strategy_id,
        required_roles,
        component_contributions,
        user_priority_adjustment,
        cap_name,
        clamped_score,
        rounded_score,
    )
    return CardSynergyScore(
        card_oracle_id=card.oracle_id,
        card_name=card.name,
        commander_oracle_id=commander_oracle_id,
        strategy_id=strategy_id,
        score=rounded_score,
        tier=_tier(rounded_score, catalog),
        eligible=True,
        rankable=True,
        eligibility_reasons=(),
        rankability_reasons=(),
        commander_level=affinity.level,
        commander_factor=commander_factor,
        components=MappingProxyType(
            {
                name: float(value)
                for name, value in components.items()
                if value is not None
            }
        ),
        reasons=score_reasons,
        available_quantity=available,
        catalog_version=catalog.catalog_version,
    )


def rank_scored_cards(
    scores: tuple[CardSynergyScore, ...],
) -> tuple[CardSynergyScore, ...]:
    """Order eligible, ranked cards deterministically, then availability and identity."""
    return tuple(
        sorted(
            scores,
            key=lambda item: (
                not item.eligible,
                not item.rankable,
                -(item.score if item.score is not None else -1.0),
                -(item.available_quantity if item.available_quantity is not None else -1),
                item.card_name.casefold(),
                item.card_oracle_id,
            ),
        )
    )


def _available_quantity(card: CardScoreInput) -> int | None:
    if card.available_quantity is None:
        return None
    return max(0, card.available_quantity - card.already_used_quantity)


def _strategy_evidence_component(
    evidence: tuple[ClassificationEvidence, ...],
    strategy_id: str,
    tag_weights: Mapping[str, float],
    manual_inclusions: tuple[ClassificationDecision, ...],
) -> float:
    signals = _strategy_evidence_signals(evidence, strategy_id, tag_weights)
    for decision in manual_inclusions:
        key = (decision.target_type, decision.target_id)
        if (
            decision.target_type == "strategy"
            and decision.target_id == strategy_id
        ) or (decision.target_type == "tag" and decision.target_id in tag_weights):
            confidence = decision.confidence_score
            if confidence is not None:
                signals[key] = confidence
            elif key not in signals:
                signals[key] = 1.0
    return min(1.0, sum(signals.values()))


def _strategy_evidence_signals(
    evidence: tuple[ClassificationEvidence, ...],
    strategy_id: str,
    tag_weights: Mapping[str, float],
) -> dict[tuple[str, str], float]:
    signals: dict[tuple[str, str], float] = {}
    for item in evidence:
        if item.target_type == "strategy" and item.target_id == strategy_id:
            target = ("strategy", strategy_id)
        elif item.target_type == "tag" and item.target_id in tag_weights:
            target = ("tag", item.target_id)
        else:
            continue
        contribution = min(1.0, item.weight * item.confidence)
        signals[target] = max(signals.get(target, 0.0), contribution)
    return signals


def _tag_overlap_component(
    evidence: tuple[ClassificationEvidence, ...],
    commander_affinities: CommanderAffinityProfile,
    tag_weights: Mapping[str, float],
    manual_inclusions: tuple[ClassificationDecision, ...],
) -> float:
    if not tag_weights:
        return 0.0
    card_tag_ids = {
        item.target_id
        for item in evidence
        if item.target_type == "tag" and item.target_id in tag_weights
    }
    card_tag_ids.update(
        decision.target_id
        for decision in manual_inclusions
        if decision.target_type == "tag" and decision.target_id in tag_weights
    )
    commander_tag_ids = {
        item.tag_id for item in commander_affinities.tag_affinities
    }
    relevant_ids = set(tag_weights)
    intersection = relevant_ids & card_tag_ids & commander_tag_ids
    union = (relevant_ids & card_tag_ids) | (relevant_ids & commander_tag_ids)
    if not union:
        return 0.0
    return sum(tag_weights[tag_id] for tag_id in intersection) / sum(
        tag_weights[tag_id] for tag_id in union
    )


def _role_coverage_component(
    evidence: tuple[ClassificationEvidence, ...],
    required_roles: tuple[str, ...] | None,
    manual_inclusions: tuple[ClassificationDecision, ...],
) -> float:
    if required_roles is None or not required_roles:
        return 0.0
    if len(set(required_roles)) != len(required_roles):
        raise ValueError("required_roles contém IDs duplicados.")
    found_roles = {
        item.target_id for item in evidence if item.target_type == "role"
    }
    found_roles.update(
        decision.target_id
        for decision in manual_inclusions
        if decision.target_type == "role"
    )
    return len(found_roles & set(required_roles)) / len(required_roles)


def _apply_evidence_caps(
    score: float,
    evidence: tuple[ClassificationEvidence, ...],
    catalog: StrategyCatalog,
) -> tuple[float, str | None]:
    if not evidence:
        return score, None
    unique_rules = {item.rule_id for item in evidence}
    caps: list[tuple[str, float]] = []
    if (
        len(unique_rules) == 1
        and len(evidence) == 1
        and evidence[0].source_field == "keywords"
        and "single_generic_keyword_max" in catalog.scoring.caps
    ):
        caps.append(
            (
                "single_generic_keyword_max",
                catalog.scoring.caps["single_generic_keyword_max"],
            )
        )
    if (
        all(
            item.source_field
            in {"type_line", "type_line_normalized"}
            for item in evidence
        )
        and "type_line_only_max" in catalog.scoring.caps
    ):
        caps.append(("type_line_only_max", catalog.scoring.caps["type_line_only_max"]))
    if (
        len(unique_rules) == 1
        and len(evidence) == 1
        and evidence[0].weight * evidence[0].confidence < 0.5
        and "single_weak_rule_max" in catalog.scoring.caps
    ):
        caps.append(("single_weak_rule_max", catalog.scoring.caps["single_weak_rule_max"]))
    if not caps:
        return score, None
    cap_name, cap_value = min(caps, key=lambda item: (item[1], item[0]))
    if score <= cap_value:
        return score, None
    return cap_value, cap_name


def _score_reasons(
    evidence: tuple[ClassificationEvidence, ...],
    manual_inclusions: tuple[ClassificationDecision, ...],
    tag_weights: Mapping[str, float],
    commander_affinities: CommanderAffinityProfile,
    strategy_id: str,
    required_roles: tuple[str, ...] | None,
    component_contributions: Mapping[str, float],
    user_priority_adjustment: float,
    cap_name: str | None,
    clamped_score: float,
    rounded_score: float,
) -> tuple[ScoreReason, ...]:
    reasons: list[ScoreReason] = []
    strategy_signals = _strategy_evidence_signals(evidence, strategy_id, tag_weights)
    signal_details: dict[tuple[str, str], ClassificationEvidence | ClassificationDecision] = {}
    for item in _deduplicate_evidence(
        [
            item
            for item in evidence
            if (
                item.target_type == "strategy" and item.target_id == strategy_id
            )
            or (item.target_type == "tag" and item.target_id in tag_weights)
        ]
    ):
        signal_details[(item.target_type, item.target_id)] = item
    for decision in manual_inclusions:
        key = (decision.target_type, decision.target_id)
        if (
            decision.target_type == "strategy" and decision.target_id == strategy_id
        ) or (decision.target_type == "tag" and decision.target_id in tag_weights):
            if decision.confidence_score is not None:
                strategy_signals[key] = decision.confidence_score
                signal_details[key] = decision
            elif key not in strategy_signals:
                strategy_signals[key] = (
                    1.0
                )
                signal_details[key] = decision
    strategy_signals = _cap_signal_values(strategy_signals, 1.0)
    strategy_denominator = sum(strategy_signals.values())
    strategy_component_contribution = component_contributions.get(
        "strategy_evidence",
        0.0,
    )
    if strategy_denominator and strategy_component_contribution:
        for key in sorted(strategy_signals):
            detail = signal_details[key]
            contribution = (
                strategy_component_contribution
                * strategy_signals[key]
                / strategy_denominator
            )
            if isinstance(detail, ClassificationEvidence):
                reasons.append(
                    ScoreReason(
                        code=(
                            "STRATEGY_RULE"
                            if detail.target_type == "strategy"
                            else "TAG_STRATEGY_EVIDENCE"
                        ),
                        rule_id=detail.rule_id,
                        tag_id=detail.target_id if detail.target_type == "tag" else None,
                        contribution=contribution,
                        detail=detail.evidence_label,
                    )
                )
            else:
                reasons.append(
                    ScoreReason(
                        code="MANUAL_OVERRIDE",
                        tag_id=detail.target_id if detail.target_type == "tag" else None,
                        contribution=contribution,
                        detail=detail.override.reason if detail.override else None,
                    )
                )

    commander_tag_ids = {
        item.tag_id for item in commander_affinities.tag_affinities
    }
    card_tag_evidence = [
        item
        for item in evidence
        if item.target_type == "tag"
        and item.target_id in tag_weights
        and item.target_id in commander_tag_ids
    ]
    card_tag_ids = {item.target_id for item in card_tag_evidence}
    card_tag_ids.update(
        decision.target_id
        for decision in manual_inclusions
        if decision.target_type == "tag"
        and decision.target_id in tag_weights
        and decision.target_id in commander_tag_ids
    )
    overlap_contribution = component_contributions.get("shared_synergy_tags", 0.0)
    if overlap_contribution > 0 and card_tag_ids:
        matched_tags = sorted(card_tag_ids)
        total_weight = sum(tag_weights[tag_id] for tag_id in matched_tags)
        for tag_id in matched_tags:
            contribution = (
                overlap_contribution * tag_weights[tag_id] / total_weight
            )
            reasons.append(
                ScoreReason(
                    code="TAG_OVERLAP",
                    tag_id=tag_id,
                    contribution=contribution,
                )
            )

    required_role_contribution = component_contributions.get("role_coverage", 0.0)
    role_ids = {
        item.target_id
        for item in evidence
        if item.target_type == "role"
        and required_roles is not None
        and item.target_id in required_roles
    }
    role_ids.update(
        decision.target_id
        for decision in manual_inclusions
        if decision.target_type == "role"
        and required_roles is not None
        and decision.target_id in required_roles
    )
    role_evidence_by_id = {
        item.target_id: item
        for item in evidence
        if item.target_type == "role" and item.target_id in role_ids
    }
    if required_role_contribution > 0 and role_ids:
        for role_id in sorted(role_ids):
            item = role_evidence_by_id.get(role_id)
            decision = next(
                (
                    value
                    for value in manual_inclusions
                    if value.target_type == "role" and value.target_id == role_id
                ),
                None,
            )
            reasons.append(
                ScoreReason(
                    code="ROLE_COVERAGE",
                    role_id=role_id,
                    rule_id=item.rule_id if item is not None else None,
                    contribution=required_role_contribution / len(role_ids),
                    detail=(
                        item.evidence_label
                        if item is not None
                        else decision.override.reason
                        if decision is not None and decision.override is not None
                        else None
                    ),
                )
            )

    commander_contribution = component_contributions.get(
        "commander_text_support",
        0.0,
    )
    if commander_contribution:
        affinity = next(
            item
            for item in commander_affinities.strategy_affinities
            if item.strategy_id == strategy_id
        )
        reasons.append(
            ScoreReason(
                code="COMMANDER_AFFINITY",
                contribution=commander_contribution,
                detail=affinity.rationale or f"Afinidade {affinity.level} configurada.",
            )
        )

    if user_priority_adjustment:
        reasons.append(
            ScoreReason(
                code="USER_PRIORITY",
                contribution=user_priority_adjustment,
                detail="Ajuste explícito de preferência do usuário.",
            )
        )
    if cap_name is not None:
        uncapped_score = sum(component_contributions.values()) + user_priority_adjustment
        cap_value = min(uncapped_score, clamped_score)
        reasons.append(
            ScoreReason(
                code="EVIDENCE_CAP",
                contribution=cap_value - uncapped_score,
                detail=cap_name,
            )
        )
    pre_rounding_sum = sum(reason.contribution for reason in reasons)
    if not math.isclose(pre_rounding_sum, rounded_score, abs_tol=1e-9):
        reasons.append(
            ScoreReason(
                code="NORMALIZATION",
                contribution=rounded_score - pre_rounding_sum,
                detail="Clamp e arredondamento da escala configurada.",
            )
        )
    return tuple(reasons)


def _cap_signal_values(
    signals: dict[tuple[str, str], float],
    maximum: float,
) -> dict[tuple[str, str], float]:
    total = sum(signals.values())
    if total <= maximum or total == 0:
        return signals
    factor = maximum / total
    return {key: value * factor for key, value in signals.items()}


def _deduplicate_evidence(
    evidence: list[ClassificationEvidence],
) -> tuple[ClassificationEvidence, ...]:
    strongest: dict[tuple[str, str, str], ClassificationEvidence] = {}
    for item in evidence:
        key = (item.target_type, item.target_id, item.rule_id)
        previous = strongest.get(key)
        if previous is None or item.weight * item.confidence > (
            previous.weight * previous.confidence
        ):
            strongest[key] = item
    by_target: dict[tuple[str, str], ClassificationEvidence] = {}
    for item in strongest.values():
        key = (item.target_type, item.target_id)
        previous = by_target.get(key)
        if previous is None or item.weight * item.confidence > (
            previous.weight * previous.confidence
        ):
            by_target[key] = item
    return tuple(by_target[key] for key in sorted(by_target))


def _tier(score: float, catalog: StrategyCatalog) -> str:
    thresholds = catalog.scoring.thresholds
    if score >= thresholds["strong"]:
        return "strong"
    if score >= thresholds["moderate"]:
        return "moderate"
    if score >= thresholds["weak"]:
        return "weak"
    return "below_threshold"
