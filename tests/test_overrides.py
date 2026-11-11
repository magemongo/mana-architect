from datetime import datetime, timezone

import pytest

from mana_architect.catalog import load_strategy_catalog
from mana_architect.classification import (
    CardClassificationInput,
    classify_card,
)
from mana_architect.overrides import (
    ClassificationDecision,
    ManualOverride,
    resolve_classification_overrides,
)


@pytest.fixture
def catalog():
    return load_strategy_catalog()


def test_manual_exclusion_overrides_and_preserves_automatic_evidence(catalog) -> None:
    automatic = classify_card(
        CardClassificationInput(oracle_text="Create a token."),
        catalog,
    )
    override = ManualOverride(
        target_type="tag",
        target_id="create_tokens",
        action="force_exclude",
        reason="A regra gerou um falso positivo neste caso.",
        author="tester",
        recorded_at=datetime.now(timezone.utc),
    )

    decisions = resolve_classification_overrides(automatic, (override,), catalog)

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.included is False
    assert decision.source == "manual"
    assert decision.conflict is True
    assert decision.automatic_evidence == automatic.evidence
    assert decision.override == override


def test_manual_include_creates_decision_and_records_confidence(catalog) -> None:
    override = ManualOverride(
        target_type="strategy",
        target_id="tokens",
        action="force_include",
        level="secondary",
        confidence_score=0.82,
        reason="Afinidade confirmada em revisão.",
    )

    decisions = resolve_classification_overrides(
        classify_card(CardClassificationInput(), catalog),
        (override,),
        catalog,
    )

    assert decisions == (
        ClassificationDecision(
            target_type="strategy",
            target_id="tokens",
            included=True,
            level="secondary",
            confidence_score=0.82,
            source="manual",
            automatic_evidence=(),
            override=override,
            conflict=False,
        ),
    )


def test_manual_set_level_keeps_automatic_evidence_and_removal_restores_auto(
    catalog,
) -> None:
    automatic = classify_card(
        CardClassificationInput(oracle_text="Create a token."),
        catalog,
    )
    override = ManualOverride(
        target_type="tag",
        target_id="create_tokens",
        action="force_include",
    )
    level_override = ManualOverride(
        target_type="strategy",
        target_id="tokens",
        action="set_level",
        level="primary",
    )

    overridden = resolve_classification_overrides(
        automatic,
        (override, level_override),
        catalog,
    )
    restored = resolve_classification_overrides(automatic, (), catalog)

    assert next(item for item in overridden if item.target_id == "tokens").level == "primary"
    assert next(
        item for item in overridden if item.target_id == "create_tokens"
    ).source == "mixed"
    assert restored == (
        ClassificationDecision(
            target_type="tag",
            target_id="create_tokens",
            included=True,
            level=None,
            confidence_score=0.75,
            source="automatic",
            automatic_evidence=automatic.evidence,
            override=None,
            conflict=False,
        ),
    )


def test_exclusion_has_precedence_over_manual_inclusion(catalog) -> None:
    automatic = classify_card(
        CardClassificationInput(oracle_text="Create a token."),
        catalog,
    )
    decisions = resolve_classification_overrides(
        automatic,
        (
            ManualOverride("tag", "create_tokens", "force_include"),
            ManualOverride("tag", "create_tokens", "force_exclude"),
        ),
        catalog,
    )

    assert len(decisions) == 1
    assert not decisions[0].included
    assert decisions[0].override is not None
    assert decisions[0].override.action == "force_exclude"


def test_conflicting_manual_values_for_same_target_are_rejected(catalog) -> None:
    overrides = (
        ManualOverride("strategy", "tokens", "force_include"),
        ManualOverride("strategy", "tokens", "set_level", level="primary"),
    )

    with pytest.raises(ValueError, match="Overrides manuais conflitantes"):
        resolve_classification_overrides(
            classify_card(CardClassificationInput(), catalog),
            overrides,
            catalog,
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        (ManualOverride("tag", "missing_tag", "force_include"), "ID inexistente"),
        (
            ManualOverride("role", "land", "set_level", level="primary"),
            "só é válido para alvos strategy",
        ),
        (
            ManualOverride("strategy", "tokens", "set_level", level="bad"),
            "exige level",
        ),
        (
            ManualOverride("tag", "create_tokens", "force_include", confidence_score=1.1),
            "confidence_score",
        ),
        (
            ManualOverride("tag", "create_tokens", "force_exclude", confidence_score=0.5),
            "não aceita confidence_score",
        ),
        (
            ManualOverride(
                "strategy",
                "tokens",
                "force_include",
                recorded_at=datetime(2026, 1, 1),
            ),
            "recorded_at deve conter timezone",
        ),
    ],
)
def test_invalid_manual_override_is_rejected(catalog, override, message) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_classification_overrides(
            classify_card(CardClassificationInput(), catalog),
            (override,),
            catalog,
        )
