"""Independently specified Phase 1 safety cases and metamorphic controls."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from mana_architect.ability_boundaries import resolve_ability_boundaries
from test_collection_playability_mechanics import _collection, _plummet, catalog


CASES = json.loads((Path(__file__).parent / "fixtures/cpa_adversarial.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_adversarial_golden_safety_and_evidence(catalog, case):
    card = replace(_plummet(), card_id=case.get("oracle_id", case["id"]),
                   name=case.get("card_name", case["id"]), type_line=case["type"], oracle_text=case["text"])
    adapter = _collection(catalog, card)
    inputs = adapter.analyze(card.card_id)
    assert inputs.parsed is not None
    boundaries = resolve_ability_boundaries(inputs.parsed, card_id=card.card_id, type_line=card.type_line)
    assert len(boundaries) == case["boundary_count"]
    assert [item.source_text for item in boundaries] == case["boundaries"]
    start = 0
    for boundary, expected_text in zip(boundaries, case["boundaries"], strict=True):
        start = case["text"].index(expected_text, start)
        assert (boundary.start, boundary.end) == (start, start + len(expected_text))
        start = boundary.end
    primary = boundaries[case["primary_boundary"]]
    assert (primary.kind, primary.activation_cost) == (case["kind"], case["cost"])
    assert all(card.oracle_text[item.start:item.end] == item.source_text for item in boundaries)
    assert boundaries == resolve_ability_boundaries(inputs.parsed, card_id=card.card_id, type_line=card.type_line)
    refs = {ref.evidence_id: ref for ref in inputs.profile.evidence}
    for ref in refs.values():
        assert getattr(card, ref.source_field)[ref.start:ref.end] == ref.text
    coverage = {item.boundary_id: item for item in inputs.profile.boundary_coverage}
    assert (coverage[primary.ability_id].state == "verified") == (case["coverage"] == "verified")
    relationship = case["relationship"]
    affected = {key for item in inputs.profile.card_dependencies if item.status != "independent"
                for key in item.affected_boundary_ids}
    assert (primary.ability_id in affected) == (relationship == "modifier")
    uses = [use for use in inputs.profile.mechanical_uses
            if use.role_id == case.get("required_role", case.get("role", "mana_acceleration"))]
    assert uses
    if case["expected"] == "unsupported":
        assert inputs.profile.confidence == "low"
        for use in inputs.profile.mechanical_uses:
            if all(refs[key].source_field == "type_line" for key in use.evidence_ids):
                continue
            assert use.safety == "unsupported"
            assert not any(set(use.evidence_ids) & set(context.evidence_ids)
                           for context in inputs.profile.viable_contexts)
    for use in uses:
        assert use.safety == case["expected"]
        assert use.applicability == case["applicability"]
        assert set(case["requirements"]) <= set(use.requirements)
    units = {item.unit_id: item for item in inputs.profile.execution_units}
    if relationship in {"shared", "targeted"}:
        for use in uses:
            assert units[use.execution_unit_id].target_evidence_ids
            assert "at_least_one_target_legal" in units[use.execution_unit_id].requirements
        assert coverage[primary.ability_id].targeting == ("mandatory_target",)
    elif relationship in {"optional", "multiple", "modes"}:
        assert coverage[primary.ability_id].targeting == ()
        assert "semantic_completeness_unverified" in coverage[primary.ability_id].reasons
        assert all(units[use.execution_unit_id].target_evidence_ids for use in uses)
    else:
        assert coverage[primary.ability_id].targeting == ()
    if relationship == "shared":
        [draw] = uses
        [mill] = [use for use in inputs.profile.mechanical_uses if use.role_id == "mill_opponent"]
        assert draw.execution_unit_id == mill.execution_unit_id
        assert set(draw.evidence_ids).isdisjoint(units[draw.execution_unit_id].target_evidence_ids)
        assert set(mill.evidence_ids) <= set(units[draw.execution_unit_id].target_evidence_ids)
    if relationship == "modes":
        modal_units = [unit for unit in units.values() if unit.mode_ids]
        assert len(modal_units) == 2
        assert all(unit.minimum_modes == unit.maximum_modes == len(unit.mode_ids) == 1 for unit in modal_units)
    if case["id"] == "two_activations":
        [mana] = uses
        [draw] = [use for use in inputs.profile.mechanical_uses if use.role_id == "draw"]
        [native_mana] = [use for use in inputs.profile.mechanical_uses if use.role_id == "add_mana"]
        assert native_mana.execution_unit_id == mana.execution_unit_id
        assert native_mana.requirements == mana.requirements == ("pay_ability_cost", "tap_source")
        assert mana.execution_unit_id != draw.execution_unit_id
        assert units[mana.execution_unit_id].activation_cost == "{T}"
        assert units[draw.execution_unit_id].activation_cost == "{1}"
        assert draw.requirements == ("pay_ability_cost",)
        for use, boundary in zip((mana, draw), boundaries, strict=True):
            assert coverage[boundary.ability_id].state == "verified"
            assert units[use.execution_unit_id].target_evidence_ids == ()
            for key in use.evidence_ids:
                ref = refs[key]
                assert ref.extraction_boundary_id == boundary.ability_id
                assert ref.extraction_parser_version == inputs.parsed.parser_version
                assert boundary.start <= ref.start < ref.end <= boundary.end
        with pytest.raises(ValueError, match="Scoped evidence must match"):
            replace(inputs.profile, evidence=(*inputs.profile.evidence,
                    replace(ref, extraction_boundary_id=boundaries[0].ability_id)))
    assert inputs.profile == adapter.analyze(card.card_id).profile


@pytest.mark.parametrize("separator", ["\n", "\n\n", " "])
def test_unmodeled_modifier_never_promotes_mana(catalog, separator):
    suffix = "Whenever a creature dies, exchange all resources."
    card = replace(_plummet(), card_id="separator", type_line="Artifact",
                   oracle_text="{T}: Add {C}." + separator + suffix)
    profile = _collection(catalog, card).analyze(card.card_id).profile
    assert any(use.role_id == "add_mana" for use in profile.mechanical_uses)
    assert all(use.safety == "unsupported" for use in profile.mechanical_uses
               if use.role_id in {"add_mana", "mana_acceleration"})


@pytest.mark.parametrize("type_line", ["Artifact", "Artifact Creature"])
@pytest.mark.parametrize("text", [
    "  {T}: Add {C}.   {1}: Draw a card.  ",
    "{T}: Add {C}.\n{1}: Draw a card.",
    "{T}: Add {C}.\r\n\r\n{1}: Draw a card.",
    "{1}: Draw a card.\n\n{T}: Add {C}.",
    "Flying\n\n{T}: Add {C}.\n\n{1}: Draw a card.",
])
def test_equivalent_independent_abilities_preserve_normalized_uses(catalog, text, type_line):
    card = replace(_plummet(), card_id="equivalent", type_line=type_line)

    def normalized(oracle_text):
        snapshot = replace(card, oracle_text=oracle_text)
        profile = _collection(catalog, snapshot).analyze(card.card_id).profile
        units = {unit.unit_id: unit for unit in profile.execution_units}
        return {
            (use.role_id, use.safety, use.applicability, use.requirements,
             units[use.execution_unit_id].activation_cost)
            for use in profile.mechanical_uses if use.role_id in {"draw", "mana_acceleration", "add_mana"}
        }

    assert normalized(text) == normalized("{T}: Add {C}. {1}: Draw a card.") == {
        ("mana_acceleration", "conditional", "conditional", ("pay_ability_cost", "tap_source"), "{T}"),
        ("add_mana", "conditional", "conditional", ("pay_ability_cost", "tap_source"), "{T}"),
        ("draw", "conditional", "conditional", ("pay_ability_cost",), "{1}"),
    }


def test_scoped_extraction_cannot_hide_a_changed_cost_or_unresolved_instruction(catalog):
    for first in ("{T}, Discard a card: Add {C}.", "{T}: Add {C}. Activate only if you control an artifact."):
        card = replace(_plummet(), card_id="unsafe-cost", type_line="Artifact",
                       oracle_text=f"{first} {{1}}: Draw a card.")
        profile = _collection(catalog, card).analyze(card.card_id).profile
        mana = [use for use in profile.mechanical_uses if use.role_id in {"add_mana", "mana_acceleration"}]
        assert mana and all(use.safety == "unsupported" for use in mana)


def test_scoping_preserves_target_evidence_for_unsupported_activation(catalog):
    card = replace(_plummet(), card_id="target-in-activation", type_line="Artifact",
                   oracle_text="{T}: Add {C}. {1}, Discard a card: Target opponent mills two cards.")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    [mill] = [use for use in profile.mechanical_uses if use.role_id == "mill_opponent"]
    [unit] = [unit for unit in profile.execution_units if unit.unit_id == mill.execution_unit_id]
    assert unit.activation_cost == "{1}, Discard a card"
    assert set(mill.evidence_ids) <= set(unit.target_evidence_ids)
    assert (mill.safety, mill.applicability, profile.confidence) == ("unsupported", "unknown", "low")
    assert "native_effect_coverage_unresolved" in mill.requirements
    assert not any(set(mill.evidence_ids) & set(context.evidence_ids) for context in profile.viable_contexts)


def test_compound_modifier_cannot_be_scoped_to_its_first_clause(catalog):
    modifier = "Mana abilities can't be activated. Players can't draw cards."
    card = replace(_plummet(), card_id="compound-modifier", type_line="Artifact",
                   oracle_text=f"{{T}}: Add {{C}}.\n\n{{1}}: Draw a card.\n\n{modifier}")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    uses = [use for use in profile.mechanical_uses if use.role_id in {"mana_acceleration", "draw"}]
    assert {use.role_id for use in uses} == {"mana_acceleration", "draw"}
    [dependency] = [item for item in profile.card_dependencies
                    if item.source_text == modifier and item.status != "independent"]
    assert dependency.status == "unknown"
    assert len(dependency.affected_boundary_ids) == 2
    assert card.oracle_text[dependency.source_start:dependency.source_end] == modifier
    assert profile.confidence == "low"
    for use in uses:
        assert (use.safety, use.applicability) == ("unsupported", "unknown")
        assert "card_dependency_unresolved" in use.requirements
        assert not any(set(use.evidence_ids) & set(context.evidence_ids) for context in profile.viable_contexts)
