import pytest

from mana_architect.catalog import load_strategy_catalog
from mana_architect.classification import ClassificationEvidence, ClassificationResult
from mana_architect.commander_affinity import (
    CommanderAffinityProfile,
    CommanderStrategyAffinity,
    CommanderTagAffinity,
)
from mana_architect.overrides import ManualOverride
from mana_architect.synergy import (
    CardScoreInput,
    rank_scored_cards,
    score_card_for_strategy,
)


@pytest.fixture
def catalog():
    return load_strategy_catalog()


def _evidence(
    target_type: str,
    target_id: str,
    *,
    rule_id: str = "fixture_rule",
    weight: float = 0.8,
    confidence: float = 0.75,
    source_field: str = "oracle_text_normalized",
) -> ClassificationEvidence:
    return ClassificationEvidence(
        rule_id=rule_id,
        target_type=target_type,
        target_id=target_id,
        source_field=source_field,
        matched_value="matched fixture value",
        weight=weight,
        confidence=confidence,
        requires_review=False,
        evidence_label="Fixture evidence.",
    )


def _affinities(
    commander_id: str = "commander-id",
    *,
    strategy: str = "tokens",
    level: str = "primary",
    strength: float = 0.8,
    tags: tuple[str, ...] = ("create_tokens",),
) -> CommanderAffinityProfile:
    return CommanderAffinityProfile(
        commander_oracle_id=commander_id,
        strategy_affinities=(
            CommanderStrategyAffinity(strategy, level, strength, rationale="Curated support."),
        ),
        tag_affinities=tuple(CommanderTagAffinity(tag_id) for tag_id in tags),
    )


def _card(
    oracle_id: str = "card-id",
    name: str = "Token Maker",
    *,
    evidence: tuple[ClassificationEvidence, ...] = (),
    identity: tuple[str, ...] | None = ("G",),
    legal: bool | None = True,
    available: int | None = 3,
    used: int = 0,
) -> CardScoreInput:
    return CardScoreInput(
        oracle_id=oracle_id,
        name=name,
        color_identity=identity,
        format_legal=legal,
        classifications=ClassificationResult(evidence=evidence),
        available_quantity=available,
        already_used_quantity=used,
    )


def _score(
    catalog,
    card: CardScoreInput,
    profile: CommanderAffinityProfile | None = None,
    strategy: str = "tokens",
    **kwargs,
):
    return score_card_for_strategy(
        card,
        "commander-id",
        ("G",),
        profile or _affinities(),
        strategy,
        catalog,
        **kwargs,
    )


def test_score_uses_configured_components_and_renormalizes_without_role_context(
    catalog,
) -> None:
    card = _card(evidence=(_evidence("tag", "create_tokens"),))

    result = _score(catalog, card)

    assert result.score == 77.6
    assert result.tier == "strong"
    assert result.components["strategy_evidence"] == pytest.approx(0.6)
    assert result.components["shared_synergy_tags"] == 1.0
    assert result.components["commander_text_support"] == 0.8
    assert sum(reason.contribution for reason in result.reasons) == pytest.approx(
        result.score
    )


def test_role_coverage_participates_only_when_needed_roles_are_provided(catalog) -> None:
    card = _card(
        evidence=(
            _evidence("tag", "create_tokens"),
            _evidence("role", "card_draw", rule_id="draw_rule"),
        )
    )

    result = _score(catalog, card, required_roles=("card_draw",))

    assert result.score == 81.0
    assert result.components["role_coverage"] == 1.0
    assert sum(reason.contribution for reason in result.reasons) == pytest.approx(
        result.score
    )


def test_affinity_level_scales_score_using_configured_factor(catalog) -> None:
    card = _card(evidence=(_evidence("tag", "create_tokens"),))
    primary = _score(catalog, card)
    secondary = _score(
        catalog,
        card,
        _affinities(level="secondary"),
    )
    complementary = _score(
        catalog,
        card,
        _affinities(level="complementary"),
    )

    assert primary.score > secondary.score > complementary.score
    assert secondary.commander_factor == 0.65
    assert complementary.commander_factor == 0.35


def test_unregistered_commander_strategy_is_not_given_default_affinity(catalog) -> None:
    profile = CommanderAffinityProfile(commander_oracle_id="commander-id")

    result = _score(catalog, _card(), profile)

    assert result.eligible
    assert not result.rankable
    assert result.score is None
    assert result.eligibility_reasons == ()
    assert result.rankability_reasons == ("NO_COMMANDER_AFFINITY",)


def test_colorless_symbol_is_not_a_color_identity(catalog) -> None:
    with pytest.raises(ValueError, match="Identidade de cor da carta"):
        _score(catalog, _card(identity=("C",)))


@pytest.mark.parametrize(
    ("card", "only_collection", "reason"),
    [
        (_card(identity=("G", "R")), False, "COLOR_IDENTITY_INCOMPATIBLE"),
        (_card(legal=False), False, "FORMAT_ILLEGAL"),
        (_card(legal=None), False, "FORMAT_LEGALITY_UNKNOWN"),
        (_card(identity=None), False, "COLOR_IDENTITY_UNKNOWN"),
        (_card(available=None), True, "COLLECTION_AVAILABILITY_UNKNOWN"),
        (_card(available=2, used=2), True, "NOT_AVAILABLE_IN_COLLECTION"),
    ],
)
def test_ineligible_cards_never_receive_synergy_score(
    catalog,
    card: CardScoreInput,
    only_collection: bool,
    reason: str,
) -> None:
    result = _score(catalog, card, only_collection=only_collection)

    assert not result.eligible
    assert not result.rankable
    assert result.score is None
    assert reason in result.eligibility_reasons


def test_availability_does_not_change_intrinsic_score(catalog) -> None:
    evidence = (_evidence("tag", "create_tokens"),)
    plenty = _score(catalog, _card(evidence=evidence, available=4))
    used_copy = _score(catalog, _card(evidence=evidence, available=4, used=3))

    assert plenty.score == used_copy.score
    assert plenty.available_quantity == 4
    assert used_copy.available_quantity == 1


def test_weighted_tag_overlap_uses_only_tags_shared_with_commander(catalog) -> None:
    card = _card(
        evidence=(
            _evidence("tag", "create_tokens"),
            _evidence("tag", "token_doubler", rule_id="doubler_rule"),
        )
    )
    profile = _affinities(tags=("create_tokens",))

    result = _score(catalog, card, profile)

    assert result.components["shared_synergy_tags"] == pytest.approx(0.5)
    overlap_reasons = [reason for reason in result.reasons if reason.code == "TAG_OVERLAP"]
    assert [reason.tag_id for reason in overlap_reasons] == ["create_tokens"]


def test_one_weak_type_line_candidate_is_capped_by_config(catalog) -> None:
    artifact_affinity = _affinities(
        strategy="artifacts",
        strength=0.8,
        tags=("artifact_count",),
    )
    result = _score(
        catalog,
        _card(
            evidence=(
                _evidence(
                    "tag",
                    "artifact_count",
                    weight=0.3,
                    confidence=0.4,
                    source_field="type_line_normalized",
                ),
            )
        ),
        artifact_affinity,
        strategy="artifacts",
    )

    assert result.score == 15
    assert any(
        reason.code == "EVIDENCE_CAP" and reason.detail == "type_line_only_max"
        for reason in result.reasons
    )
    assert sum(reason.contribution for reason in result.reasons) == pytest.approx(15)


def test_one_generic_keyword_candidate_is_capped(catalog) -> None:
    aggro_affinity = _affinities(strategy="aggro", tags=("haste",))
    result = _score(
        catalog,
        _card(
            evidence=(
                _evidence(
                    "tag",
                    "haste",
                    weight=0.6,
                    confidence=0.8,
                    source_field="keywords",
                ),
            )
        ),
        aggro_affinity,
        strategy="aggro",
    )

    assert result.score == 20
    assert any(
        reason.code == "EVIDENCE_CAP"
        and reason.detail == "single_generic_keyword_max"
        for reason in result.reasons
    )


def test_manual_inclusion_can_supply_candidate_signal_without_erasing_provenance(
    catalog,
) -> None:
    result = _score(
        catalog,
        _card(),
        overrides=(
            ManualOverride(
                "tag",
                "create_tokens",
                "force_include",
                reason="Reviewed by a person.",
            ),
        ),
    )

    assert result.score is not None
    assert any(reason.code == "MANUAL_OVERRIDE" for reason in result.reasons)
    assert sum(reason.contribution for reason in result.reasons) == pytest.approx(
        result.score
    )


def test_manual_exclusion_removes_automatic_tag_contribution(catalog) -> None:
    card = _card(evidence=(_evidence("tag", "create_tokens"),))
    automatic = _score(catalog, card)
    excluded = _score(
        catalog,
        card,
        overrides=(ManualOverride("tag", "create_tokens", "force_exclude"),),
    )

    assert excluded.score < automatic.score
    assert excluded.components["strategy_evidence"] == 0
    assert excluded.components["shared_synergy_tags"] == 0
    assert sum(reason.contribution for reason in excluded.reasons) == pytest.approx(
        excluded.score
    )


def test_manual_confidence_replaces_signal_strength(catalog) -> None:
    result = _score(
        catalog,
        _card(evidence=(_evidence("tag", "create_tokens"),)),
        overrides=(
            ManualOverride(
                "tag",
                "create_tokens",
                "force_include",
                confidence_score=0.2,
                reason="Revisado como sinal fraco.",
            ),
        ),
    )

    assert result.components["strategy_evidence"] == pytest.approx(0.2)
    assert sum(reason.contribution for reason in result.reasons) == pytest.approx(
        result.score
    )


def test_deterministic_ranking_uses_score_availability_name_and_oracle_id(
    catalog,
) -> None:
    evidence = (_evidence("tag", "create_tokens"),)
    first = _score(catalog, _card("z-id", "Same", evidence=evidence, available=4))
    second = _score(catalog, _card("b-id", "Same", evidence=evidence, available=2))
    third = _score(catalog, _card("a-id", "Alpha", evidence=evidence, available=4))
    ordered = rank_scored_cards((second, first, third))

    assert [item.card_oracle_id for item in ordered] == ["a-id", "z-id", "b-id"]


def test_validation_rejects_unknown_strategies_roles_and_bad_adjustments(catalog) -> None:
    with pytest.raises(ValueError, match="Estratégia ativa inexistente"):
        _score(catalog, _card(), strategy="not_real")
    with pytest.raises(ValueError, match="Funções inexistentes"):
        _score(catalog, _card(), required_roles=("not_real",))
    with pytest.raises(ValueError, match="número finito"):
        _score(catalog, _card(), user_priority_adjustment=float("nan"))
    with pytest.raises(ValueError, match="fora da faixa"):
        _score(catalog, _card(), user_priority_adjustment=10.1)


def test_nonzero_user_priority_is_explained_separately(catalog) -> None:
    result = _score(
        catalog,
        _card(evidence=(_evidence("tag", "create_tokens"),)),
        user_priority_adjustment=4.5,
    )

    assert result.score == 82.1
    assert any(
        reason.code == "USER_PRIORITY" and reason.contribution == 4.5
        for reason in result.reasons
    )
    assert sum(reason.contribution for reason in result.reasons) == pytest.approx(
        result.score
    )
