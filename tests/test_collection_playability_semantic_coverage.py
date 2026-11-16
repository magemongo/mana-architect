from dataclasses import replace

import pytest

from mana_architect.collection_playability.contracts import SemanticCoverageState
from test_collection_playability_mechanics import _collection, _plummet, catalog


@pytest.mark.parametrize("text", [
    "{T}, Pay 2 life: Add {C}.",
    "{T}, Sacrifice this artifact: Add {C}.",
    "{T}, Discard a card: Add {C}.",
    "Destroy target creature if its controller has more life than you.",
    "As an additional cost to cast this spell, reveal your hand. Draw two cards.",
    "Draw two cards if you control an artifact.",
    "Draw two cards. Then return a card from your hand to its owner's library.",
    "{T}: Add {C}. Activate only if you control an artifact.",
    "Draw two cards unless an opponent pays {2}.",
])
def test_unverified_costs_and_dependencies_never_create_confident_use(catalog, text):
    card = replace(_plummet(), card_id="test", type_line="Artifact" if ":" in text else "Instant", oracle_text=text)
    result = _collection(catalog, card).analyze(card.card_id)
    assert all(item.state != SemanticCoverageState.VERIFIED for item in result.profile.boundary_coverage)
    for use in result.profile.mechanical_uses:
        if any(ref.evidence_id in use.evidence_ids and ref.source_field == "oracle_text" for ref in result.profile.evidence):
            assert use.applicability == "unknown"
            assert all(not set(use.evidence_ids) & set(context.evidence_ids)
                       for context in result.profile.viable_contexts)


def test_simple_verified_and_independent_unresolved_ability(catalog):
    card = replace(_plummet(), card_id="test", type_line="Artifact",
                   oracle_text="{T}: Add {C}.\n{1}, Sacrifice this artifact: Add {C}{C}.")
    profile = _collection(catalog, card).analyze(card.card_id).profile
    coverage = {item.source_text: item for item in profile.boundary_coverage}
    assert coverage["{T}: Add {C}."].state == SemanticCoverageState.VERIFIED
    assert coverage["{1}, Sacrifice this artifact: Add {C}{C}."].state != SemanticCoverageState.VERIFIED
    assert all(card.oracle_text[item.source_start:item.source_end] == item.source_text for item in coverage.values())
    assert profile.profile_id == _collection(catalog, card).analyze(card.card_id).profile.profile_id
