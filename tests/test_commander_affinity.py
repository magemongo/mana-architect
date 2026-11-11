import pytest

from mana_architect.catalog import load_strategy_catalog
from mana_architect.commander_affinity import (
    CommanderAffinityProfile,
    CommanderStrategyAffinity,
    CommanderTagAffinity,
    validate_commander_affinity_profile,
)


@pytest.fixture
def catalog():
    return load_strategy_catalog()


def test_commander_can_have_multiple_explained_strategy_and_tag_affinities(catalog) -> None:
    profile = CommanderAffinityProfile(
        commander_oracle_id="commander-oracle-id",
        strategy_affinities=(
            CommanderStrategyAffinity(
                strategy_id="tokens",
                level="primary",
                strength_score=0.92,
                evidence_ids=("commander_rule_tokens",),
                rationale="Gera fichas repetidamente.",
            ),
            CommanderStrategyAffinity(
                strategy_id="aristocrats",
                level="secondary",
                strength_score=0.61,
                evidence_ids=("commander_rule_death",),
                rationale="As fichas podem ser sacrificadas para valor.",
            ),
            CommanderStrategyAffinity(
                strategy_id="aggro",
                level="complementary",
                strength_score=0.35,
                rationale="Plano alternativo possível.",
            ),
        ),
        tag_affinities=(
            CommanderTagAffinity(
                tag_id="create_tokens",
                evidence_ids=("commander_rule_tokens",),
                rationale="O texto cria fichas.",
            ),
            CommanderTagAffinity(tag_id="creature_death"),
        ),
    )

    assert validate_commander_affinity_profile(profile, catalog) == profile


def test_commander_affinity_validation_does_not_infer_from_colors_or_partners(
    catalog,
) -> None:
    profile = CommanderAffinityProfile(
        commander_oracle_id="commander-oracle-id",
        strategy_affinities=(
            CommanderStrategyAffinity(
                strategy_id="tokens",
                level="complementary",
                strength_score=0.35,
                source="manual",
            ),
        ),
    )

    validated = validate_commander_affinity_profile(profile, catalog)

    assert validated.strategy_affinities[0].source == "manual"


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        (
            CommanderAffinityProfile(
                commander_oracle_id="commander-id",
                strategy_affinities=(
                    CommanderStrategyAffinity("unknown_strategy", "primary", 0.9),
                ),
            ),
            "Strategy inexistente",
        ),
        (
            CommanderAffinityProfile(
                commander_oracle_id="commander-id",
                tag_affinities=(CommanderTagAffinity("unknown_tag"),),
            ),
            "Tag inexistente",
        ),
        (
            CommanderAffinityProfile(
                commander_oracle_id="commander-id",
                strategy_affinities=(
                    CommanderStrategyAffinity("tokens", "primary", 1.1),
                ),
            ),
            "strength_score",
        ),
        (
            CommanderAffinityProfile(
                commander_oracle_id="commander-id",
                strategy_affinities=(
                    CommanderStrategyAffinity(
                        "tokens",
                        "primary",
                        0.9,
                        evidence_ids=("not a valid id",),
                    ),
                ),
            ),
            "IDs de evidência inválidos",
        ),
    ],
)
def test_invalid_commander_affinities_are_rejected(catalog, profile, message) -> None:
    with pytest.raises(ValueError, match=message):
        validate_commander_affinity_profile(profile, catalog)


def test_duplicate_affinities_are_rejected(catalog) -> None:
    profile = CommanderAffinityProfile(
        commander_oracle_id="commander-id",
        strategy_affinities=(
            CommanderStrategyAffinity("tokens", "primary", 0.9),
            CommanderStrategyAffinity("tokens", "secondary", 0.6),
        ),
    )

    with pytest.raises(ValueError, match="Affinity duplicada para strategy"):
        validate_commander_affinity_profile(profile, catalog)


def test_complementary_affinity_is_accepted_as_heuristic_strength(catalog) -> None:
    profile = CommanderAffinityProfile(
        commander_oracle_id="commander-id",
        strategy_affinities=(
            CommanderStrategyAffinity("tokens", "complementary", 0.35),
        ),
    )

    result = validate_commander_affinity_profile(profile, catalog)

    assert result.strategy_affinities[0].strength_score == 0.35
