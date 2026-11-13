import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from mana_architect.interaction_profile import (
    InteractionEvidence,
    InteractionProfile,
    InteractionScore,
    Quantity,
)
from mana_architect.interaction_rules_catalog import INTERACTION_RULES, mask_reminder_text


@pytest.fixture(scope="module")
def profile_schema():
    path = Path(__file__).resolve().parents[1] / "config" / "interaction_profile.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_catalog_has_unique_stable_rule_ids():
    ids = [rule.id for rule in INTERACTION_RULES]
    assert len(ids) == len(set(ids))
    assert {"EXILE_001", "GY_EXILE_001", "SPELL_COUNTER_001", "PREVENT_001"} <= set(ids)


def test_reminder_and_granted_rules_are_masked_without_moving_offsets():
    text = 'Create a token (Exile an artifact.) with “Destroy all creatures.” Then draw a card.'
    masked = mask_reminder_text(text)
    assert "Exile" not in masked and "Destroy" not in masked
    assert masked.index("Then") == text.index("Then")
    assert len(masked) == len(text)


def test_profile_default_json_and_invariants(profile_schema):
    profile = InteractionProfile("basic-land", "oracle-lexical-v3", (), ())
    serialized = json.loads(json.dumps(profile.to_json()))
    profile_schema.validate(serialized)
    assert serialized["card_id"] == "basic-land"
    assert not serialized["interaction_profile"]["is_interaction"]
    assert serialized["interaction_profile"]["strategy_tags"] == []
    assert serialized["interaction_profile"]["scores"] == {"spot": None, "mass": None}
    with pytest.raises(ValueError):
        Quantity("fixed", 2, 1)
    with pytest.raises(ValueError):
        InteractionEvidence("Destroy it.", 0, 1, ("DESTROY_001",), "a1")
    with pytest.raises(ValueError):
        InteractionScore(11)
