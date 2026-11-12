from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Card(Base):
    __tablename__ = "cards"

    oracle_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mana_cost: Mapped[str | None] = mapped_column(String(255))
    cmc: Mapped[float | None] = mapped_column(Float)
    colors: Mapped[list[str] | None] = mapped_column(JSON)
    color_identity: Mapped[list[str] | None] = mapped_column(JSON)
    produced_mana: Mapped[list[str] | None] = mapped_column(JSON)
    type_line: Mapped[str | None] = mapped_column(String(255))
    oracle_text: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list[str] | None] = mapped_column(JSON)
    legalities: Mapped[dict[str, str] | None] = mapped_column(JSON)
    image_uri: Mapped[str | None] = mapped_column(String(2048))
    scryfall_updated_at: Mapped[str | None] = mapped_column(String(40))


class CollectionPrinting(Base):
    __tablename__ = "collection_printings"
    __table_args__ = (
        UniqueConstraint(
            "scryfall_id",
            "language",
            "finish",
            "condition",
            name="uq_collection_printing_identity",
        ),
        CheckConstraint("quantity > 0", name="ck_collection_printing_positive_quantity"),
        Index("ix_collection_printings_name", "name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scryfall_id: Mapped[str] = mapped_column(String(36), nullable=False)
    oracle_id: Mapped[str | None] = mapped_column(
        ForeignKey("cards.oracle_id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    set_code: Mapped[str] = mapped_column(String(16), nullable=False)
    collector_number: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    finish: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    condition: Mapped[str] = mapped_column(String(32), nullable=False)


class SyncMetadata(Base):
    __tablename__ = "sync_metadata"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClassificationCatalogVersion(Base):
    __tablename__ = "classification_catalog_versions"

    catalog_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    catalog_version: Mapped[str] = mapped_column(String(32), nullable=False)
    ruleset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scoring_version: Mapped[str] = mapped_column(String(32), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    configuration_snapshot: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClassificationRun(Base):
    __tablename__ = "classification_runs"
    __table_args__ = (
        UniqueConstraint(
            "oracle_id",
            "classification_hash",
            "input_fingerprint",
            name="uq_classification_run_input",
        ),
        Index("ix_classification_runs_oracle_id", "oracle_id"),
        Index("ix_classification_runs_catalog_hash", "catalog_hash"),
        Index("ix_classification_runs_classification_hash", "classification_hash"),
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    oracle_id: Mapped[str] = mapped_column(
        ForeignKey("cards.oracle_id", ondelete="CASCADE"),
        nullable=False,
    )
    catalog_hash: Mapped[str] = mapped_column(
        ForeignKey("classification_catalog_versions.catalog_hash", ondelete="RESTRICT"),
        nullable=False,
    )
    classification_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    card_updated_at: Mapped[str | None] = mapped_column(String(40))
    oracle_parse: Mapped[dict[str, object] | None] = mapped_column(JSON)
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CardClassification(Base):
    __tablename__ = "card_classifications"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('strategy', 'tag', 'role')",
            name="ck_card_classification_target_type",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_card_classification_confidence",
        ),
        CheckConstraint(
            "source = 'automatic'",
            name="ck_card_classification_source",
        ),
        UniqueConstraint(
            "run_id",
            "target_type",
            "target_id",
            name="uq_card_classification_target",
        ),
        Index("ix_card_classifications_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("classification_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    requires_review: Mapped[bool] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="automatic")


class ClassificationEvidenceRecord(Base):
    __tablename__ = "classification_evidence"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('strategy', 'tag', 'role')",
            name="ck_classification_evidence_target_type",
        ),
        CheckConstraint(
            "weight >= 0",
            name="ck_classification_evidence_weight",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_classification_evidence_confidence",
        ),
        ForeignKeyConstraint(
            ["run_id", "target_type", "target_id"],
            [
                "card_classifications.run_id",
                "card_classifications.target_type",
                "card_classifications.target_id",
            ],
            ondelete="CASCADE",
            name="fk_classification_evidence_target",
        ),
        Index("ix_classification_evidence_target", "target_type", "target_id"),
        Index("ix_classification_evidence_rule_id", "rule_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_field: Mapped[str] = mapped_column(String(64), nullable=False)
    matched_value: Mapped[str] = mapped_column(String(512), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    requires_review: Mapped[bool] = mapped_column(nullable=False)
    evidence_label: Mapped[str] = mapped_column(String(512), nullable=False)
    face_index: Mapped[int | None] = mapped_column(Integer)
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)


class ManualOverrideRecord(Base):
    __tablename__ = "manual_overrides"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('strategy', 'tag', 'role')",
            name="ck_manual_override_target_type",
        ),
        CheckConstraint(
            "action IN ('force_include', 'force_exclude', 'set_level')",
            name="ck_manual_override_action",
        ),
        CheckConstraint(
            "level IS NULL OR level IN ('primary', 'secondary', 'complementary')",
            name="ck_manual_override_level",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR "
            "(confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_manual_override_confidence",
        ),
        CheckConstraint(
            "(action = 'set_level' AND target_type = 'strategy' AND level IS NOT NULL) OR "
            "(action = 'force_exclude' AND level IS NULL AND confidence_score IS NULL) OR "
            "(action = 'force_include' AND "
            "(level IS NULL OR target_type = 'strategy'))",
            name="ck_manual_override_action_values",
        ),
        UniqueConstraint(
            "target_type",
            "target_id",
            name="uq_manual_override_target",
        ),
        Index("ix_manual_overrides_target_id", "target_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    level: Mapped[str | None] = mapped_column(String(16))
    confidence_score: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    recorded_at: Mapped[str] = mapped_column(String(40), nullable=False)


class CardClassificationOverrideRecord(Base):
    __tablename__ = "card_classification_overrides"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('strategy', 'tag', 'role')",
            name="ck_card_classification_override_target_type",
        ),
        CheckConstraint(
            "action IN ('force_include', 'force_exclude')",
            name="ck_card_classification_override_action",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR "
            "(confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_card_classification_override_confidence",
        ),
        CheckConstraint(
            "action != 'force_exclude' OR confidence_score IS NULL",
            name="ck_card_classification_override_exclusion_confidence",
        ),
        ForeignKeyConstraint(
            ["oracle_id"],
            ["cards.oracle_id"],
            ondelete="CASCADE",
            name="fk_card_classification_override_card",
        ),
        Index(
            "ix_card_classification_overrides_target",
            "target_type",
            "target_id",
        ),
    )

    oracle_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(255))
    recorded_at: Mapped[str] = mapped_column(String(40), nullable=False)


class CommanderAffinityRecord(Base):
    __tablename__ = "commander_affinities"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('strategy', 'tag')",
            name="ck_commander_affinity_target_type",
        ),
        CheckConstraint(
            "level IS NULL OR level IN ('primary', 'secondary', 'complementary')",
            name="ck_commander_affinity_level",
        ),
        CheckConstraint(
            "strength_score IS NULL OR "
            "(strength_score >= 0 AND strength_score <= 1)",
            name="ck_commander_affinity_strength",
        ),
        CheckConstraint(
            "(target_type = 'strategy' AND level IS NOT NULL AND strength_score IS NOT NULL) "
            "OR (target_type = 'tag' AND level IS NULL AND strength_score IS NULL)",
            name="ck_commander_affinity_target_values",
        ),
        CheckConstraint(
            "source IN ('manual', 'automatic')",
            name="ck_commander_affinity_source",
        ),
        ForeignKeyConstraint(
            ["commander_oracle_id"],
            ["cards.oracle_id"],
            ondelete="CASCADE",
            name="fk_commander_affinity_card",
        ),
        Index("ix_commander_affinities_target", "target_type", "target_id"),
    )

    commander_oracle_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    level: Mapped[str | None] = mapped_column(String(16))
    strength_score: Mapped[float | None] = mapped_column(Float)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
