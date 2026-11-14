import json
import re

import pytest

from mana_architect.catalog import DEFAULT_CATALOG_DIR, load_strategy_catalog
from mana_architect.synergy_catalog import load_synergy_strategies


def test_definitions_cover_all_active_builtin_strategy_ids():
    legacy = load_strategy_catalog()
    mechanical = load_synergy_strategies()
    assert {strategy.id for strategy in mechanical.strategies} == {
        strategy.id for strategy in legacy.strategies if strategy.active
    }
    assert all(strategy.objective and strategy.exclusions for strategy in mechanical.strategies)


def test_edhrec_reference_covers_every_active_strategy():
    reference = DEFAULT_CATALOG_DIR.parent / "docs" / "edhrec-themes.md"
    rows = re.findall(r"^\| \d+ \| .+ \|", reference.read_text(encoding="utf-8"), re.MULTILINE)
    scored = [match[1] for row in rows if (match := re.search(r"Scored · `([a-z0-9_]+)`", row))]
    assert len(rows) == 284
    assert len(scored) == len(set(scored))
    assert set(scored) == {strategy.id for strategy in load_synergy_strategies().strategies}


def test_catalog_aliases_preserve_canonical_active_ids(tmp_path):
    data = json.loads((DEFAULT_CATALOG_DIR / "strategy_synergy.json").read_text())
    data["aliases"] = {"token_deck": "tokens", "artifact_deck": "artifacts"}
    (tmp_path / "strategy_synergy.json").write_text(json.dumps(data))
    catalog = load_synergy_strategies(tmp_path, active_strategy_ids=frozenset({"tokens"}))
    assert tuple(strategy.id for strategy in catalog.strategies) == ("tokens",)
    assert catalog.aliases == (("token_deck", "tokens"),)


@pytest.mark.parametrize("field,value", [
    ("capability", "oracle_keyword"),
    ("role", "synergy"),
    ("weight", 1.5),
    ("types", ["misspelled_type"]),
    ("reference", "commander"),
])
def test_invalid_capability_preferences_are_rejected(tmp_path, field, value):
    data = json.loads((DEFAULT_CATALOG_DIR / "strategy_synergy.json").read_text())
    data["strategies"][0]["preferences"][0][field] = value
    (tmp_path / "strategy_synergy.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match=r"strategy_synergy.json: strategies\[0\].preferences\[0\]"):
        load_synergy_strategies(tmp_path, active_strategy_ids=frozenset({"tokens"}))
