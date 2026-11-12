from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mana_architect.catalog import StrategyCatalog, load_strategy_catalog
from mana_architect.classification import (
    CardClassificationInput,
    ClassificationEvidence,
    ClassificationResult,
    classify_card,
)
from mana_architect.commander_affinity import (
    CommanderAffinityProfile,
    CommanderStrategyAffinity,
    CommanderTagAffinity,
    validate_commander_affinity_profile,
)
from mana_architect.models import (
    Card,
    CardClassification,
    CardClassificationOverrideRecord,
    ClassificationCatalogVersion,
    ClassificationEvidenceRecord,
    ClassificationRun,
    CommanderAffinityRecord,
    ManualOverrideRecord,
)
from mana_architect.overrides import (
    ManualOverride,
    resolve_classification_overrides,
)
from mana_architect.oracle_text import OracleParse, PARSER_VERSION


NORMALIZER_VERSION = "2.0.0"
_CATALOG_FILES = (
    "strategy_catalog.json",
    "synergy_tags.json",
    "card_roles.json",
    "classification_rules.json",
    "scoring.json",
    "oracle_lexicon.json",
    "oracle_mechanics.json",
)


@dataclass(frozen=True)
class ReclassificationSummary:
    catalog_hash: str
    examined: int
    classified: int
    skipped: int


def catalog_configuration_hash(config_dir: Path) -> str:
    return _configuration_hash(config_dir, _CATALOG_FILES)


def classification_configuration_hash(
    config_dir: Path,
    catalog: StrategyCatalog,
) -> str:
    return _classification_configuration_hash(config_dir, catalog)


def _configuration_hash(
    config_dir: Path,
    filenames: tuple[str, ...],
) -> str:
    digest = hashlib.sha256()
    for filename in filenames:
        path = config_dir / filename
        try:
            contents = path.read_bytes()
        except OSError as error:
            raise ValueError(f"{filename}: não foi possível ler o arquivo: {error}") from error
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(contents)
        digest.update(b"\0")
    return digest.hexdigest()


def _classification_configuration_hash(
    config_dir: Path,
    catalog: StrategyCatalog,
) -> str:
    rules_path = config_dir / "classification_rules.json"
    try:
        rules_data = json.loads(rules_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(
            f"classification_rules.json: não foi possível ler o arquivo: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ValueError(
            "classification_rules.json: configuração inválida; carregue e valide "
            "o catálogo antes da reclassificação."
        ) from error
    semantics = {
        "normalizer_version": NORMALIZER_VERSION,
        "oracle_parser_version": PARSER_VERSION,
        "strategy_ids": sorted(strategy.id for strategy in catalog.strategies),
        "tag_ids": sorted(tag.id for tag in catalog.tags),
        "role_ids": sorted(role.id for role in catalog.roles),
        "role_oracle_keywords": {
            role.id: role.oracle_keywords for role in catalog.roles
        },
        "oracle_lexicon": [
            {"id": entry.id, "category": entry.category,
             "phrase": entry.phrase, "aliases": entry.aliases}
            for entry in catalog.oracle_lexicon
        ],
        "oracle_mechanical_patterns": [
            {"id": item.id, "matcher": item.matcher,
             "mechanic_id": item.mechanic_id, "attributes": item.attributes}
            for item in catalog.oracle_mechanical_patterns
        ],
        "rules": rules_data,
    }
    encoded = json.dumps(
        semantics,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reclassify_cards(
    session: Session,
    catalog: StrategyCatalog,
    config_dir: Path,
    oracle_ids: tuple[str, ...] | None = None,
) -> ReclassificationSummary:
    """Classify changed cards and persist evidence inside a caller-owned transaction."""
    configured_catalog = load_strategy_catalog(config_dir)
    if configured_catalog != catalog:
        raise ValueError(
            "O catálogo carregado não corresponde aos arquivos de config_dir."
        )
    catalog_hash = catalog_configuration_hash(config_dir)
    classification_hash = classification_configuration_hash(config_dir, catalog)
    if oracle_ids is not None:
        if not all(isinstance(oracle_id, str) and oracle_id for oracle_id in oracle_ids):
            raise ValueError("oracle_ids deve conter apenas textos não vazios.")
        if len(set(oracle_ids)) != len(oracle_ids):
            raise ValueError("oracle_ids contém valores duplicados.")

    _register_catalog_version(session, catalog, catalog_hash, config_dir)
    statement = select(Card).order_by(Card.oracle_id)
    if oracle_ids is not None:
        if not oracle_ids:
            cards: list[Card] = []
        else:
            cards = list(
                session.scalars(statement.where(Card.oracle_id.in_(oracle_ids)))
            )
            found_ids = {card.oracle_id for card in cards}
            missing_ids = set(oracle_ids) - found_ids
            if missing_ids:
                raise ValueError(
                    f"oracle_id inexistente: {', '.join(sorted(missing_ids))}."
                )
    else:
        cards = list(session.scalars(statement))

    classified = 0
    skipped = 0
    for card in cards:
        fingerprint = _card_input_fingerprint(card, catalog)
        existing_run_id = session.scalar(
            select(ClassificationRun.run_id).where(
                ClassificationRun.oracle_id == card.oracle_id,
                ClassificationRun.classification_hash == classification_hash,
                ClassificationRun.input_fingerprint == fingerprint,
            )
        )
        if existing_run_id is not None:
            skipped += 1
            continue

        card_input = _classification_input(card)
        result = classify_card(card_input, catalog)
        run = ClassificationRun(
            run_id=str(uuid.uuid4()),
            oracle_id=card.oracle_id,
            catalog_hash=catalog_hash,
            classification_hash=classification_hash,
            input_fingerprint=fingerprint,
            card_updated_at=card.scryfall_updated_at,
            oracle_parse=result.oracle_parse.to_json() if result.oracle_parse else None,
            classified_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.flush()
        _persist_classification_result(session, run.run_id, result)
        classified += 1

    return ReclassificationSummary(
        catalog_hash=catalog_hash,
        examined=len(cards),
        classified=classified,
        skipped=skipped,
    )


def load_latest_classification(
    session: Session,
    oracle_id: str,
) -> ClassificationResult | None:
    run = session.scalar(
        select(ClassificationRun)
        .where(ClassificationRun.oracle_id == oracle_id)
        .order_by(ClassificationRun.classified_at.desc(), ClassificationRun.run_id.desc())
        .limit(1)
    )
    if run is None:
        return None
    records = session.scalars(
        select(ClassificationEvidenceRecord)
        .where(ClassificationEvidenceRecord.run_id == run.run_id)
        .order_by(ClassificationEvidenceRecord.id)
    )
    return ClassificationResult(
        evidence=tuple(
            ClassificationEvidence(
                rule_id=record.rule_id,
                target_type=record.target_type,
                target_id=record.target_id,
                source_field=record.source_field,
                matched_value=record.matched_value,
                weight=record.weight,
                confidence=record.confidence,
                requires_review=record.requires_review,
                evidence_label=record.evidence_label,
                face_index=record.face_index,
                span_start=record.span_start,
                span_end=record.span_end,
            )
            for record in records
        ),
        oracle_parse=OracleParse.from_json(run.oracle_parse),
    )


def load_current_classifications(
    session: Session,
    oracle_ids: tuple[str, ...],
    catalog: StrategyCatalog,
    config_dir: Path,
) -> dict[str, ClassificationResult]:
    if not all(isinstance(oracle_id, str) and oracle_id for oracle_id in oracle_ids):
        raise ValueError("oracle_ids deve conter apenas textos não vazios.")
    if len(set(oracle_ids)) != len(oracle_ids):
        raise ValueError("oracle_ids contém valores duplicados.")
    if not oracle_ids:
        return {}

    cards = {
        card.oracle_id: card
        for card in session.scalars(
            select(Card).where(Card.oracle_id.in_(oracle_ids))
        )
    }
    current_fingerprints = {
        oracle_id: _card_input_fingerprint(card, catalog)
        for oracle_id, card in cards.items()
    }
    expected_hash = classification_configuration_hash(config_dir, catalog)
    runs = session.scalars(
        select(ClassificationRun)
        .where(ClassificationRun.oracle_id.in_(oracle_ids))
        .order_by(
            ClassificationRun.oracle_id,
            ClassificationRun.classified_at.desc(),
            ClassificationRun.run_id.desc(),
        )
    )
    latest_runs: dict[str, ClassificationRun] = {}
    for run in runs:
        latest_runs.setdefault(run.oracle_id, run)
    latest_runs = {
        oracle_id: run
        for oracle_id, run in latest_runs.items()
        if run.classification_hash == expected_hash
        and run.input_fingerprint == current_fingerprints.get(oracle_id)
    }

    records_by_run: dict[str, list[ClassificationEvidenceRecord]] = {
        run.run_id: [] for run in latest_runs.values()
    }
    if records_by_run:
        records = session.scalars(
            select(ClassificationEvidenceRecord)
            .where(ClassificationEvidenceRecord.run_id.in_(records_by_run))
            .order_by(ClassificationEvidenceRecord.id)
        )
        for record in records:
            records_by_run[record.run_id].append(record)

    return {
        oracle_id: _classification_result(
            records_by_run[run.run_id], OracleParse.from_json(run.oracle_parse)
        )
        for oracle_id, run in latest_runs.items()
    }


def _classification_result(
    records: list[ClassificationEvidenceRecord],
    oracle_parse: OracleParse | None = None,
) -> ClassificationResult:
    return ClassificationResult(
        evidence=tuple(
            ClassificationEvidence(
                rule_id=record.rule_id,
                target_type=record.target_type,
                target_id=record.target_id,
                source_field=record.source_field,
                matched_value=record.matched_value,
                weight=record.weight,
                confidence=record.confidence,
                requires_review=record.requires_review,
                evidence_label=record.evidence_label,
                face_index=record.face_index,
                span_start=record.span_start,
                span_end=record.span_end,
            )
            for record in records
        ),
        oracle_parse=oracle_parse,
    )


def load_card_classification_overrides(
    session: Session,
    oracle_ids: tuple[str, ...],
    catalog: StrategyCatalog,
) -> dict[str, tuple[ManualOverride, ...]]:
    if not all(isinstance(oracle_id, str) and oracle_id for oracle_id in oracle_ids):
        raise ValueError("oracle_ids deve conter apenas textos não vazios.")
    if len(set(oracle_ids)) != len(oracle_ids):
        raise ValueError("oracle_ids contém valores duplicados.")
    if not oracle_ids:
        return {}

    records = session.scalars(
        select(CardClassificationOverrideRecord)
        .where(CardClassificationOverrideRecord.oracle_id.in_(oracle_ids))
        .order_by(
            CardClassificationOverrideRecord.oracle_id,
            CardClassificationOverrideRecord.target_type,
            CardClassificationOverrideRecord.target_id,
        )
    )
    overrides_by_card: dict[str, list[ManualOverride]] = {}
    valid_catalog_ids = {
        "strategy": {item.id for item in catalog.strategies},
        "tag": {item.id for item in catalog.tags},
        "role": {item.id for item in catalog.roles},
    }
    for record in records:
        # Ignore overrides for removed catalog roles. This project treats
        # role taxonomy edits as playground changes; obsolete overrides should
        # not prevent loading current classifications or building a deck.
        if (
            record.target_type == "role"
            and record.target_id not in valid_catalog_ids["role"]
        ):
            continue
        override = ManualOverride(
            target_type=record.target_type,  # validated by resolver below
            target_id=record.target_id,
            action=record.action,  # validated by resolver below
            confidence_score=record.confidence_score,
            reason=record.reason,
            author=record.author,
            recorded_at=datetime.fromisoformat(record.recorded_at),
        )
        overrides_by_card.setdefault(record.oracle_id, []).append(override)
    result = {
        oracle_id: tuple(overrides)
        for oracle_id, overrides in overrides_by_card.items()
    }
    for overrides in result.values():
        resolve_classification_overrides(
            ClassificationResult(evidence=()),
            overrides,
            catalog,
        )
    return result


def save_card_classification_override(
    session: Session,
    oracle_id: str,
    override: ManualOverride,
    catalog: StrategyCatalog,
) -> ManualOverride:
    if not isinstance(oracle_id, str) or not oracle_id.strip():
        raise ValueError("oracle_id deve ser um texto não vazio.")
    if (
        not isinstance(override.action, str)
        or override.action not in {"force_include", "force_exclude"}
    ):
        raise ValueError(
            "Correções de classificação aceitam force_include ou force_exclude."
        )
    if not isinstance(override.reason, str) or not override.reason.strip():
        raise ValueError("A correção manual exige uma justificativa não vazia.")
    resolve_classification_overrides(
        ClassificationResult(evidence=()),
        (override,),
        catalog,
    )
    if session.get(Card, oracle_id) is None:
        raise ValueError(f"Carta desconhecida: '{oracle_id}'.")

    recorded_at = override.recorded_at or datetime.now(timezone.utc)
    stored_override = replace(override, recorded_at=recorded_at)
    record = session.get(
        CardClassificationOverrideRecord,
        (oracle_id, override.target_type, override.target_id),
    )
    if record is None:
        record = CardClassificationOverrideRecord(
            oracle_id=oracle_id,
            target_type=override.target_type,
            target_id=override.target_id,
            action=override.action,
            confidence_score=override.confidence_score,
            reason=override.reason.strip(),
            author=override.author,
            recorded_at=recorded_at.isoformat(),
        )
        session.add(record)
    else:
        record.action = override.action
        record.confidence_score = override.confidence_score
        record.reason = override.reason.strip()
        record.author = override.author
        record.recorded_at = recorded_at.isoformat()
    return stored_override


def remove_card_classification_override(
    session: Session,
    oracle_id: str,
    target_type: str,
    target_id: str,
    catalog: StrategyCatalog,
) -> bool:
    if not isinstance(oracle_id, str) or not oracle_id.strip():
        raise ValueError("oracle_id deve ser um texto não vazio.")
    candidate = ManualOverride(
        target_type=target_type,  # validation is performed by resolver
        target_id=target_id,
        action="force_include",
        reason="validation",
    )
    resolve_classification_overrides(
        ClassificationResult(evidence=()),
        (candidate,),
        catalog,
    )
    record = session.get(
        CardClassificationOverrideRecord,
        (oracle_id, target_type, target_id),
    )
    if record is None:
        return False
    session.delete(record)
    return True


def save_manual_override(
    session: Session,
    override: ManualOverride,
    catalog: StrategyCatalog,
) -> ManualOverride:
    resolve_classification_overrides(ClassificationResult(evidence=()), (override,), catalog)
    recorded_at = override.recorded_at or datetime.now(timezone.utc)
    stored_override = replace(override, recorded_at=recorded_at)
    record = session.scalar(
        select(ManualOverrideRecord).where(
            ManualOverrideRecord.target_type == override.target_type,
            ManualOverrideRecord.target_id == override.target_id,
        )
    )
    if record is None:
        record = ManualOverrideRecord(
            target_type=override.target_type,
            target_id=override.target_id,
            action=override.action,
            level=override.level,
            confidence_score=override.confidence_score,
            reason=override.reason,
            author=override.author,
            recorded_at=recorded_at.isoformat(),
        )
        session.add(record)
    else:
        record.action = override.action
        record.level = override.level
        record.confidence_score = override.confidence_score
        record.reason = override.reason
        record.author = override.author
        record.recorded_at = recorded_at.isoformat()
    return stored_override


def load_manual_overrides(
    session: Session,
    catalog: StrategyCatalog,
) -> tuple[ManualOverride, ...]:
    records = session.scalars(
        select(ManualOverrideRecord).order_by(
            ManualOverrideRecord.target_type,
            ManualOverrideRecord.target_id,
        )
    )
    overrides = tuple(
        ManualOverride(
            target_type=record.target_type,  # validated by resolver below
            target_id=record.target_id,
            action=record.action,  # validated by resolver below
            level=record.level,
            confidence_score=record.confidence_score,
            reason=record.reason,
            author=record.author,
            recorded_at=datetime.fromisoformat(record.recorded_at),
        )
        for record in records
    )
    resolve_classification_overrides(ClassificationResult(evidence=()), overrides, catalog)
    return overrides


def remove_manual_override(
    session: Session,
    target_type: str,
    target_id: str,
    catalog: StrategyCatalog,
) -> bool:
    candidate = ManualOverride(
        target_type=target_type,  # validation is performed by resolver
        target_id=target_id,
        action="force_include",
    )
    resolve_classification_overrides(
        ClassificationResult(evidence=()),
        (candidate,),
        catalog,
    )
    record = session.scalar(
        select(ManualOverrideRecord).where(
            ManualOverrideRecord.target_type == target_type,
            ManualOverrideRecord.target_id == target_id,
        )
    )
    if record is None:
        return False
    session.delete(record)
    return True


def save_commander_affinity_profile(
    session: Session,
    profile: CommanderAffinityProfile,
    catalog: StrategyCatalog,
) -> CommanderAffinityProfile:
    validated = validate_commander_affinity_profile(profile, catalog)
    if session.get(Card, validated.commander_oracle_id) is None:
        raise ValueError(
            f"Comandante desconhecido: '{validated.commander_oracle_id}'."
        )

    session.execute(
        delete(CommanderAffinityRecord).where(
            CommanderAffinityRecord.commander_oracle_id
            == validated.commander_oracle_id
        )
    )
    records = [
        CommanderAffinityRecord(
            commander_oracle_id=validated.commander_oracle_id,
            target_type="strategy",
            target_id=affinity.strategy_id,
            level=affinity.level,
            strength_score=affinity.strength_score,
            evidence_ids=list(affinity.evidence_ids),
            rationale=affinity.rationale,
            source=affinity.source,
        )
        for affinity in validated.strategy_affinities
    ]
    records.extend(
        CommanderAffinityRecord(
            commander_oracle_id=validated.commander_oracle_id,
            target_type="tag",
            target_id=affinity.tag_id,
            level=None,
            strength_score=None,
            evidence_ids=list(affinity.evidence_ids),
            rationale=affinity.rationale,
            source=affinity.source,
        )
        for affinity in validated.tag_affinities
    )
    session.add_all(records)
    return validated


def load_commander_affinity_profile(
    session: Session,
    commander_oracle_id: str,
    catalog: StrategyCatalog,
) -> CommanderAffinityProfile | None:
    records = tuple(
        session.scalars(
            select(CommanderAffinityRecord)
            .where(
                CommanderAffinityRecord.commander_oracle_id == commander_oracle_id
            )
            .order_by(
                CommanderAffinityRecord.target_type,
                CommanderAffinityRecord.target_id,
            )
        )
    )
    if not records:
        return None

    profile = CommanderAffinityProfile(
        commander_oracle_id=commander_oracle_id,
        strategy_affinities=tuple(
            CommanderStrategyAffinity(
                strategy_id=record.target_id,
                level=record.level,  # validated by profile validator below
                strength_score=record.strength_score,
                evidence_ids=tuple(record.evidence_ids),
                rationale=record.rationale,
                source=record.source,  # validated by profile validator below
            )
            for record in records
            if record.target_type == "strategy"
        ),
        tag_affinities=tuple(
            CommanderTagAffinity(
                tag_id=record.target_id,
                evidence_ids=tuple(record.evidence_ids),
                rationale=record.rationale,
                source=record.source,  # validated by profile validator below
            )
            for record in records
            if record.target_type == "tag"
        ),
    )
    return validate_commander_affinity_profile(profile, catalog)


def _register_catalog_version(
    session: Session,
    catalog: StrategyCatalog,
    catalog_hash: str,
    config_dir: Path,
) -> None:
    if session.get(ClassificationCatalogVersion, catalog_hash) is not None:
        return
    snapshot: dict[str, str] = {}
    for filename in _CATALOG_FILES:
        try:
            snapshot[filename] = (config_dir / filename).read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError(
                f"{filename}: não foi possível ler o arquivo: {error}"
            ) from error
    session.add(
        ClassificationCatalogVersion(
            catalog_hash=catalog_hash,
            catalog_version=catalog.catalog_version,
            ruleset_version=catalog.ruleset_version,
            scoring_version=catalog.scoring_version,
            normalizer_version=NORMALIZER_VERSION,
            configuration_snapshot=snapshot,
            registered_at=datetime.now(timezone.utc),
        )
    )
    session.flush()


def _classification_input(card: Card) -> CardClassificationInput:
    return CardClassificationInput(
        type_line=card.type_line,
        oracle_text=card.oracle_text,
        mana_cost=card.mana_cost,
        cmc=card.cmc,
        colors=tuple(card.colors) if card.colors is not None else None,
        color_identity=(
            tuple(card.color_identity) if card.color_identity is not None else None
        ),
        produced_mana=(
            tuple(card.produced_mana) if card.produced_mana is not None else None
        ),
        keywords=tuple(card.keywords) if card.keywords is not None else None,
    )


def _card_input_fingerprint(card: Card, catalog: StrategyCatalog) -> str:
    values = {
        "type_line": card.type_line,
        "type_line_normalized": card.type_line,
        "oracle_text": card.oracle_text,
        "oracle_text_normalized": card.oracle_text,
        "mana_cost": card.mana_cost,
        "cmc": card.cmc,
        "colors": card.colors,
        "color_identity": card.color_identity,
        "produced_mana": card.produced_mana,
        "keywords": card.keywords,
    }
    relevant_fields = sorted(
        {
            field
            for rule in catalog.rules
            if rule.enabled
            for field in rule.source_fields
        }
    )
    relevant_fields = sorted(set(relevant_fields) | {"oracle_text"})
    if any(role.oracle_keywords for role in catalog.roles):
        relevant_fields = sorted(set(relevant_fields) | {"oracle_text", "keywords"})
    if {"oracle_features", "oracle_mechanics", "classification_tags"}.intersection(
        relevant_fields
    ):
        relevant_fields = sorted(
            (
                set(relevant_fields)
                - {"oracle_features", "oracle_mechanics", "classification_tags"}
            )
            | {"oracle_text"}
        )
    payload = {field: values[field] for field in relevant_fields}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _persist_classification_result(
    session: Session,
    run_id: str,
    result: ClassificationResult,
) -> None:
    grouped: dict[tuple[str, str], list[ClassificationEvidence]] = {}
    for evidence in result.evidence:
        key = (evidence.target_type, evidence.target_id)
        grouped.setdefault(key, []).append(evidence)
    for (target_type, target_id), evidence_group in grouped.items():
        session.add(
            CardClassification(
                run_id=run_id,
                target_type=target_type,
                target_id=target_id,
                confidence_score=max(item.confidence for item in evidence_group),
                requires_review=any(item.requires_review for item in evidence_group),
                source="automatic",
            )
        )
    session.flush()
    session.add_all(
        ClassificationEvidenceRecord(
            run_id=run_id,
            target_type=evidence.target_type,
            target_id=evidence.target_id,
            rule_id=evidence.rule_id,
            source_field=evidence.source_field,
            matched_value=evidence.matched_value[:512],
            weight=evidence.weight,
            confidence=evidence.confidence,
            requires_review=evidence.requires_review,
            evidence_label=evidence.evidence_label[:512],
            face_index=evidence.face_index,
            span_start=evidence.span_start,
            span_end=evidence.span_end,
        )
        for evidence in result.evidence
    )
