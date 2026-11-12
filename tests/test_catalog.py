import json
from pathlib import Path
import shutil

import pytest

from mana_architect.catalog import DEFAULT_CATALOG_DIR, load_strategy_catalog


def _copy_catalog(tmp_path: Path) -> Path:
    destination = tmp_path / "config"
    shutil.copytree(DEFAULT_CATALOG_DIR, destination)
    return destination


def test_loads_versioned_catalog_and_all_initial_strategy_roles() -> None:
    catalog = load_strategy_catalog()

    assert catalog.catalog_version == "1.1.0"
    assert catalog.ruleset_version == "1.3.0"
    assert catalog.scoring_version == "1.0.0"
    assert len(catalog.strategies) == 15
    assert {strategy.id for strategy in catalog.strategies} == {
        "tokens",
        "plus_one_counters",
        "combo",
        "artifacts",
        "aggro",
        "reanimator",
        "spellslinger",
        "lifegain",
        "control",
        "aristocrats",
        "burn",
        "ramp",
        "lands_matter",
        "voltron",
        "midrange",
    }
    assert len(catalog.tags) >= 70
    assert {role.id for role in catalog.roles} >= {
        "ramp",
        "mana_fixing",
        "card_draw",
        "card_selection",
        "tutor",
        "removal",
        "board_wipe",
        "counterspell",
        "protection",
        "recursion",
        "graveyard_hate",
        "token_generator",
        "sacrifice_outlet",
        "enabler",
        "payoff",
        "engine",
        "finisher",
        "threat",
        "land",
        "win_condition",
    }
    assert catalog.scoring.components["strategy_evidence"] == 0.4
    assert any(rule.requires_review for rule in catalog.rules)


def test_catalog_records_have_valid_cross_references() -> None:
    catalog = load_strategy_catalog()
    strategy_ids = {strategy.id for strategy in catalog.strategies}
    tag_ids = {tag.id for tag in catalog.tags}

    assert all(
        affinity.tag_id in tag_ids
        for strategy in catalog.strategies
        for affinity in strategy.tag_affinities
    )
    assert all(
        strategy_id in strategy_ids
        for tag in catalog.tags
        for strategy_id in tag.related_strategy_ids
    )
    assert all(
        rule.target_id
        in {
            "strategy": strategy_ids,
            "tag": tag_ids,
            "role": {role.id for role in catalog.roles},
        }[rule.target_type]
        for rule in catalog.rules
    )


def test_rejects_unknown_rule_target_with_file_and_field(tmp_path: Path) -> None:
    config_dir = _copy_catalog(tmp_path)
    rules_path = config_dir / "classification_rules.json"
    rules_data = json.loads(rules_path.read_text(encoding="utf-8"))
    rules_data["rules"][0]["target_id"] = "missing_tag"
    rules_path.write_text(json.dumps(rules_data), encoding="utf-8")

    with pytest.raises(ValueError, match=r"classification_rules\.json: rules\[tag_create_tokens_oracle_text\]\.target_id"):
        load_strategy_catalog(config_dir)


def test_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    config_dir = _copy_catalog(tmp_path)
    catalog_path = config_dir / "strategy_catalog.json"
    catalog_data = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_data["schema_version"] = 2
    catalog_path.write_text(json.dumps(catalog_data), encoding="utf-8")

    with pytest.raises(ValueError, match=r"strategy_catalog\.json: schema_version"):
        load_strategy_catalog(config_dir)


def test_rejects_duplicate_strategy_ids(tmp_path: Path) -> None:
    config_dir = _copy_catalog(tmp_path)
    catalog_path = config_dir / "strategy_catalog.json"
    catalog_data = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_data["strategies"][1]["id"] = catalog_data["strategies"][0]["id"]
    catalog_path.write_text(json.dumps(catalog_data), encoding="utf-8")

    with pytest.raises(ValueError, match=r"strategy_catalog\.json: strategies: IDs duplicados"):
        load_strategy_catalog(config_dir)


def test_rejects_scoring_components_that_do_not_sum_to_one(tmp_path: Path) -> None:
    config_dir = _copy_catalog(tmp_path)
    scoring_path = config_dir / "scoring.json"
    scoring_data = json.loads(scoring_path.read_text(encoding="utf-8"))
    scoring_data["components"]["role_coverage"] = 0.2
    scoring_path.write_text(json.dumps(scoring_data), encoding="utf-8")

    with pytest.raises(ValueError, match=r"scoring\.json: components: os pesos ativos devem somar 1\.0"):
        load_strategy_catalog(config_dir)


def test_reports_malformed_json_file_and_location(tmp_path: Path) -> None:
    config_dir = _copy_catalog(tmp_path)
    (config_dir / "strategy_catalog.json").write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match=r"strategy_catalog\.json: invalid JSON at line 1"):
        load_strategy_catalog(config_dir)
