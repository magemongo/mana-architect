import pytest

from mana_architect.ability_boundaries import associate_evidence, resolve_ability_boundaries
from mana_architect.oracle_text import parse_oracle_text
from mana_architect.synergy_capabilities import extract_mechanical_capabilities


def resolve(text, type_line="Artifact"):
    return resolve_ability_boundaries(parse_oracle_text(text), card_id="oracle-id", type_line=type_line)


def test_multiple_activations_in_a_single_parser_paragraph():
    text = "{T}: Add {C}. {1}, Sacrifice this artifact: Draw a card."
    parsed = parse_oracle_text(text)
    boundaries = resolve(text)
    assert len(parsed.abilities) == 1
    assert [item.kind for item in boundaries] == ["activated", "activated"]
    assert [item.activation_cost for item in boundaries] == ["{T}", "{1}, Sacrifice this artifact"]
    assert len({item.ability_id for item in boundaries}) == 2
    assert all(item.status == "resolved" for item in boundaries)
    assert all(text[item.start:item.end] == item.source_text for item in boundaries)


@pytest.mark.parametrize("text", [
    "{T}: Add {C}. Whenever a creature dies, draw a card.",
    "Whenever a creature dies, draw a card. {T}: Add {C}.",
])
def test_activated_and_triggered_abilities_do_not_share_identity(text):
    boundaries = resolve(text)
    assert {item.kind for item in boundaries} == {"activated", "triggered"}
    assert len({item.ability_id for item in boundaries}) == 2


def test_separate_triggers_and_native_evidence_map_to_separate_spans():
    text = "Whenever a creature dies, draw a card. Whenever an artifact enters, draw a card."
    parsed = parse_oracle_text(text)
    boundaries = resolve(text, "Creature")
    assert [item.kind for item in boundaries] == ["triggered", "triggered"]
    mechanics = extract_mechanical_capabilities(parsed, name="Test", type_line="Creature")
    matched = [associate_evidence(boundaries, start=cap.evidence.start, end=cap.evidence.end)
               for cap in mechanics.capabilities if cap.evidence.source_field == "oracle_text"]
    assert any(item.status == "resolved" for item in matched)
    # A native span spanning both printed abilities cannot be safely assigned.
    assert any(item.status == "ambiguous" for item in matched)


def test_multiple_spell_effects_share_a_boundary_across_paragraphs():
    text = "Draw three cards.\n\nTarget player mills three cards."
    parsed = parse_oracle_text(text)
    [boundary] = resolve(text, "Sorcery")
    assert boundary.kind == "spell"
    assert boundary.parser_ability_ids == ("a1", "a2")
    profile = extract_mechanical_capabilities(parsed, name="Test", type_line="Sorcery")
    for cap in profile.capabilities:
        association = associate_evidence((boundary,), start=cap.evidence.start, end=cap.evidence.end)
        assert association.status == "resolved" and association.ability_id == boundary.ability_id


def test_modal_and_quoted_structures_are_explicitly_ambiguous():
    for text in (
        "Choose one —\n• Draw a card.\n• Destroy target creature.",
        'Creatures you control have "{T}: Draw a card."',
    ):
        boundaries = resolve(text, "Sorcery")
        assert any(item.status == "ambiguous" for item in boundaries)
        item = next(item for item in boundaries if item.status == "ambiguous")
        assert associate_evidence(boundaries, start=item.start, end=item.end).status == "ambiguous"


def test_modal_options_keep_distinct_source_spans():
    text = "Choose one —\n• Draw a card.\n• Destroy target creature."
    [modal] = resolve(text, "Sorcery")
    assert modal.kind == "modal" and modal.status == "ambiguous"
    assert tuple(text[start:end] for start, end in modal.modal_choices) == (
        "• Draw a card.", "• Destroy target creature.",
    )


def test_faces_source_spans_and_ids_are_stable():
    text = "{T}: Add {C}. // Whenever a creature dies, draw a card."
    parsed = parse_oracle_text(text)
    result = resolve_ability_boundaries(parsed, card_id="oracle-id", type_line="Artifact // Creature")
    assert [item.face_index for item in result] == [0, 1]
    assert result == resolve_ability_boundaries(parsed, card_id="oracle-id", type_line="Artifact // Creature")
    assert all(text[item.start:item.end] == item.source_text for item in result)
    assert associate_evidence(result, start=result[0].start, end=result[1].end).status == "ambiguous"
