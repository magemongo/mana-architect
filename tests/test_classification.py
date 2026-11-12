from dataclasses import replace

import pytest

from mana_architect.catalog import (
    ClassificationRule,
    MatchPredicate,
    StrategyCatalog,
    load_strategy_catalog,
)
from mana_architect.classification import (
    CardClassificationInput,
    ClassificationResult,
    classify_card,
    normalize_card,
    normalize_text,
)


@pytest.fixture
def catalog() -> StrategyCatalog:
    return load_strategy_catalog()


def _with_rules(
    catalog: StrategyCatalog,
    *rules: ClassificationRule,
) -> StrategyCatalog:
    return replace(catalog, rules=rules)


def _rule(
    rule_id: str,
    predicate: MatchPredicate,
    source_field: str,
    *,
    enabled: bool = True,
    target_type: str = "tag",
    target_id: str = "create_tokens",
) -> ClassificationRule:
    return ClassificationRule(
        id=rule_id,
        enabled=enabled,
        target_type=target_type,
        target_id=target_id,
        source_fields=(source_field,),
        match=predicate,
        weight=0.8,
        confidence=0.7,
        evidence_label="Test evidence",
        requires_review=False,
    )


def test_normalization_preserves_original_and_normalizes_apostrophes_mana_and_space() -> None:
    card = CardClassificationInput(
        oracle_text="Target creature’s controller adds { W }.\nDraw   a card.",
        mana_cost="{ 2 } { U }",
        type_line="Creature — Wizard",
    )

    normalized = normalize_card(card)

    assert normalized.oracle_text == card.oracle_text
    assert normalized.mana_cost == card.mana_cost
    assert normalized.oracle_text_normalized == (
        "target creature's controller adds {w}. draw a card."
    )
    assert normalized.mana_cost_normalized == "{2} {u}"
    assert normalized.type_line_normalized == "creature — wizard"
    assert normalize_text("  { W / U }  O’Neil  ") == "{w/u} o'neil"


def test_seed_rules_return_traceable_evidence_for_token_creation(catalog: StrategyCatalog) -> None:
    card = CardClassificationInput(
        type_line="Sorcery",
        oracle_text="Create a token that's a copy of target creature.",
    )

    result = classify_card(card, catalog)

    assert len(result.evidence) == 2
    evidence = result.evidence[0]
    assert evidence.rule_id == "tag_create_tokens_oracle_text"
    assert (evidence.target_type, evidence.target_id) == ("tag", "create_tokens")
    assert evidence.source_field == "oracle_text_normalized"
    assert evidence.matched_value == "create a token"
    assert evidence.weight == 0.8
    assert evidence.confidence == 0.75
    assert not evidence.requires_review
    assert result.evidence[1].target_id == "instant_sorcery_spell_density"


def test_artifact_type_is_only_a_weak_reviewable_candidate(catalog: StrategyCatalog) -> None:
    result = classify_card(
        CardClassificationInput(
            type_line="Artifact Creature — Golem",
            oracle_text="Flying",
            keywords=("Flying",),
        ),
        catalog,
    )

    assert [(item.target_id, item.weight, item.requires_review) for item in result.evidence] == [
        ("artifact_count", 0.3, True)
    ]
    assert all(item.target_id != "single_creature_buff" for item in result.evidence)


def test_burn_evidence_is_separate_from_combat_damage(catalog: StrategyCatalog) -> None:
    burn = classify_card(
        CardClassificationInput(oracle_text="Flame deals 3 damage to each opponent."),
        catalog,
    )
    combat = classify_card(
        CardClassificationInput(
            oracle_text="Whenever this creature deals combat damage to a player, draw a card."
        ),
        catalog,
    )

    assert [item.target_id for item in burn.evidence] == ["noncombat_damage"]
    combat_targets = {item.target_id for item in combat.evidence}
    assert "combat_damage" in combat_targets
    assert "noncombat_damage" not in combat_targets


def test_lifegain_rule_uses_actor_attribute_to_exclude_opponents() -> None:
    catalog = load_strategy_catalog()
    self_gain = classify_card(
        CardClassificationInput(oracle_text="You gain 3 life."), catalog
    )
    opponent_gain = classify_card(
        CardClassificationInput(oracle_text="Each opponent gains 3 life."), catalog
    )

    assert any(
        item.target_type == "strategy" and item.target_id == "lifegain"
        for item in self_gain.evidence
    )
    assert all(item.target_id != "lifegain" for item in opponent_gain.evidence)


def test_spot_interaction_role_is_derived_from_mechanical_tag(catalog: StrategyCatalog) -> None:
    result = classify_card(
        CardClassificationInput(
            type_line="Instant",
            oracle_text="Counter target spell.",
        ),
        catalog,
    )

    targets = {(item.target_type, item.target_id) for item in result.evidence}
    assert ("tag", "counterspell") in targets
    assert ("role", "spot_interaction") in targets
    role_evidence = next(
        item for item in result.evidence
        if item.target_type == "role" and item.target_id == "spot_interaction"
    )
    assert role_evidence.rule_id == "role_spot_interaction_from_mechanical_tags"
    assert role_evidence.source_field == "classification_tags"
    assert role_evidence.matched_value == "counterspell"


def test_mass_interaction_role_is_derived_from_board_wipe_tag(catalog: StrategyCatalog) -> None:
    result = classify_card(
        CardClassificationInput(
            type_line="Sorcery",
            oracle_text="Destroy all creatures.",
        ),
        catalog,
    )

    targets = {(item.target_type, item.target_id) for item in result.evidence}
    assert ("tag", "board_wipe") in targets
    assert ("role", "mass_interaction") in targets


def test_aristocrats_strategy_signals_are_classified_as_tags(
    catalog: StrategyCatalog,
) -> None:
    result = classify_card(
        CardClassificationInput(
            type_line="Enchantment",
            oracle_text=(
                "Whenever another creature dies, each opponent loses 1 life. "
                "Sacrifice a creature: Draw a card."
            ),
        ),
        catalog,
    )

    tag_ids = {item.target_id for item in result.evidence if item.target_type == "tag"}
    assert {
        "sacrifice_creature",
        "sacrifice_outlet",
        "dies_trigger",
        "aristocrats_payoff",
        "death_drain",
    } <= tag_ids


def test_land_drop_and_expensive_creature_are_not_ramp_or_reanimation(
    catalog: StrategyCatalog,
) -> None:
    land_drop = classify_card(
        CardClassificationInput(
            type_line="Creature — Elf",
            oracle_text="You may play an additional land on each of your turns.",
            cmc=2,
        ),
        catalog,
    )
    expensive_creature = classify_card(
        CardClassificationInput(
            type_line="Creature — Demon",
            oracle_text="Flying",
            cmc=8,
        ),
        catalog,
    )

    assert all(item.target_id != "land_ramp" for item in land_drop.evidence)
    assert expensive_creature.evidence == ()


def test_island_subtype_is_not_misclassified_as_land_role(catalog: StrategyCatalog) -> None:
    result = classify_card(
        CardClassificationInput(type_line="Creature — Island"),
        catalog,
    )

    assert all(item.target_id != "land" for item in result.evidence)


def test_reanimation_returns_specific_evidence_and_face_provenance(
    catalog: StrategyCatalog,
) -> None:
    card = CardClassificationInput(
        oracle_text=(
            "Exile this card.\n\n"
            "Return target creature card from your graveyard to the battlefield."
        )
    )

    result = classify_card(card, catalog)

    assert len(result.evidence) == 1
    assert result.evidence[0].target_id == "reanimate_creature"
    assert result.evidence[0].face_index == 1


def test_phrase_all_requires_phrases_in_one_face_and_does_not_cross_faces(
    catalog: StrategyCatalog,
) -> None:
    rule = _rule(
        "same_face",
        MatchPredicate(type="phrase_all", values=("draw a card", "gain life")),
        "oracle_text_normalized",
    )
    two_faces = CardClassificationInput(
        oracle_text="Draw a card.\n\nYou gain life."
    )
    one_face = CardClassificationInput(
        oracle_text="Draw a card. You gain life."
    )

    assert classify_card(two_faces, _with_rules(catalog, rule)).evidence == ()
    assert len(classify_card(one_face, _with_rules(catalog, rule)).evidence) == 1


@pytest.mark.parametrize(
    ("rule", "card", "expected_match"),
    [
        (
            _rule(
                "type_line",
                MatchPredicate(type="type_line_contains", values=("creature",)),
                "type_line_normalized",
            ),
            CardClassificationInput(type_line="Creature — Wizard"),
            "creature",
        ),
        (
            _rule(
                "keyword",
                MatchPredicate(type="keyword_contains", values=("flying",)),
                "keywords",
            ),
            CardClassificationInput(keywords=("Flying",)),
            "flying",
        ),
        (
            _rule(
                "cmc",
                MatchPredicate(
                    type="numeric_threshold",
                    field="cmc",
                    operator="gte",
                    value=5,
                ),
                "cmc",
            ),
            CardClassificationInput(cmc=5),
            "gte 5",
        ),
        (
            _rule(
                "color",
                MatchPredicate(type="field_equals", field="color_identity", value="u"),
                "color_identity",
            ),
            CardClassificationInput(color_identity=("U",)),
            "u",
        ),
    ],
)
def test_supported_structured_predicates(
    catalog: StrategyCatalog,
    rule: ClassificationRule,
    card: CardClassificationInput,
    expected_match: str,
) -> None:
    result = classify_card(card, _with_rules(catalog, rule))

    assert [item.matched_value for item in result.evidence] == [expected_match]


def test_disabled_rule_returns_no_candidate(catalog: StrategyCatalog) -> None:
    rule = _rule(
        "disabled",
        MatchPredicate(type="phrase_any", values=("create a token",)),
        "oracle_text_normalized",
        enabled=False,
    )

    result = classify_card(
        CardClassificationInput(oracle_text="Create a token."),
        _with_rules(catalog, rule),
    )

    assert result == ClassificationResult(evidence=())


def test_classification_is_deterministic_and_does_not_multiply_repeated_matches(
    catalog: StrategyCatalog,
) -> None:
    card = CardClassificationInput(
        oracle_text="Create a token. Then create a token.",
    )

    first = classify_card(card, catalog)
    second = classify_card(card, catalog)

    assert first == second
    assert [item.rule_id for item in first.evidence] == ["tag_create_tokens_oracle_text"]


@pytest.mark.parametrize("cmc", [-1, float("nan"), float("inf")])
def test_normalizer_rejects_invalid_cmc(cmc: float) -> None:
    with pytest.raises(ValueError, match="cmc"):
        normalize_card(CardClassificationInput(cmc=cmc))
