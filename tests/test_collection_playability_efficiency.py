from dataclasses import replace
import pytest
from mana_architect.collection_playability.adapters import CollectionInputs
from mana_architect.collection_playability.contracts import PlayabilityContext, Classification
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.strategy_synergy import load_synergy_context


@pytest.fixture(scope="module")
def catalog():
    return load_synergy_context()


def card(ident, text, cost):
    return CardSnapshot(ident, ident, "Sorcery", text, cost, None, ("U",), (), (), "legal")


def analyze(catalog, subject, *others):
    cards = tuple(sorted((subject, *others), key=lambda c: c.card_id))
    snapshot = CollectionSnapshot(cards, tuple(OwnedPrinting(i, str(i), c.card_id, c.name, "t", str(i), "en", "nonfoil", "nm", 1) for i, c in enumerate(cards)), "2026-11-11")
    return CollectionInputs(PlayabilityContext(snapshot.snapshot_id, "test", ENGINE_VERSIONS), snapshot, catalog).analyze(subject.card_id).profile


def test_bounty_draw_component_compares_to_compatible_peer_and_preserves_mill(catalog):
    bounty = card("bounty", "Draw three cards. Target player mills three cards.", "{5}{U}")
    peer = card("peer", "Draw three cards.", "{3}{U}")
    profile = analyze(catalog, bounty, peer)
    [finding] = profile.efficiency_findings
    assert finding.assessment == "higher_mana_for_same_draw"
    assert finding.peer_card_id == "peer"
    assert "legal_target_at_resolution" in finding.conditions
    assert any("mill" in x for x in finding.explanation)
    assert {ref.card_id for ref in profile.evidence if ref.evidence_id in finding.evidence_ids} == {"bounty", "peer"}
    assert profile.classification == Classification.UNCERTAIN and not profile.bulk_review_eligibility


def test_expensive_different_effect_and_unsupported_text_abstain(catalog):
    peer = card("peer", "Draw three cards.", "{3}{U}")
    for text in ("Draw five cards.", "Draw three cards. You win the game."):
        profile = analyze(catalog, card("subject", text, "{7}{U}"), peer)
        assert all(x.assessment == "unknown" for x in profile.efficiency_findings)


def test_peer_compatibility_and_order(catalog):
    subject = card("subject", "Draw three cards.", "{5}{U}")
    peer = card("peer", "Draw three cards.", "{3}{U}")
    wrong = replace(peer, card_id="wrong", mana_cost="{2}{U}{U}")
    assert analyze(catalog, subject, wrong).efficiency_findings[0].assessment == "unknown"
    assert analyze(catalog, subject, peer, wrong).profile_id == analyze(catalog, subject, wrong, peer).profile_id


def test_unsupported_dependency_never_becomes_positive_efficiency(catalog):
    subject = card("subject", "Draw three cards.\nYou can't draw cards.", "{5}{U}")
    peer = card("peer", "Draw three cards.", "{3}{U}")
    profile = analyze(catalog, subject, peer)
    assert not any(x.assessment != "unknown" for x in profile.efficiency_findings)
