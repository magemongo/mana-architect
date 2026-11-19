# Phase 2 — independent fixed-draw validation

**Release status: BLOCKED.** This round validates only a bounded draw component. It does not meet the implementation guide's full Phase 2 scope or independent 40–60-card Golden plan. The tests below are not evidence of general Magic rules completeness or a precision estimate.

## Scope and changes

The existing Phase 2 changes were retained. This round adds `tests/fixtures/cpa_draw_efficiency.json` and `tests/test_collection_playability_efficiency_golden.py`; it changes `CollectionInputs.analyze` to report `peer_search_truncated` when a 65th draw-prefixed sorcery is encountered. The first 64 candidates and the decision algorithm are unchanged. Coverage reports truncation even if the retained candidates contain a peer; with no established peer the result remains unknown. The inexpensive scan stops on the 65th candidate, without executing native engines on it. The model continues to compare the cost of a draw component, not whole-card value, dominance or replaceability.

## Pinned fixtures and independent expectations

The eight records in the fixture come from the local Scryfall `default-cards.jsonl.gz` snapshot, SHA-256 `d190c5ed83e3487daab01a69ff5f50c1d4ee55e1e83f0b08bfe295e2b9477672`; the cache file's last-modified time was 2026-11-11 15:00:09 UTC. This filesystem time is not asserted to be the upstream publication date. Each fixture preserves Oracle ID, text, cost, type, color identity, Commander legality, and a Scryfall source URI. Expected properties below were authored from the pinned card text and the guide's qualitative requirements, before running the new cases; they are not generated from evaluator results.

| Cards | Expected property and observed result |
| --- | --- |
| Thassa's Bounty / Brilliant Plan | Both are blue sorceries drawing three. The former costs `{5}{U}` versus `{4}{U}`. A draw-component comparison is justified when both are owned; target legality and potential mill value remain conditions. Observed as expected; evidence spans resolve to both Oracle texts. |
| Thassa's Bounty alone | No cheaper compatible owned peer is established; observed unknown, not a negative finding. |
| Concentrate / Thassa's Bounty | Both draw three, but `{2}{U}{U}` has different color intensity from `{5}{U}`. The current exact-symbol rule abstains. This is a conservative limitation, not proof the cards cannot be compared in any deck. |
| Divination / Thassa's Bounty | Draw quantities differ; no same-draw comparison is established. Observed unknown. |
| Inspiration and Opportunity | Targeted instant draws are outside the fixed sorcery shape; no positive draw-cost observation. Observed as expected. |
| Painful Truths | Draw quantity depends on mana spent and costs life; outside the supported model. Observed no positive observation. |
| Time Stretch | Expensive, impactful extra turns do not imply inefficient draw. Observed no positive draw-cost observation. |

## Evidence, safety and limitations

The real positive test checks each referenced span and card identity, the source version and record ID, collection scope limitation, conditional mill explanation, the draw use's legal-target requirement, and its execution unit's `at_least_one_target_legal` requirement and target evidence. Classification and bulk eligibility remain unchanged. An adversarial test forces the Phase 1 draw use to unsupported and verifies that the efficiency evaluator emits no positive finding. Earlier boundary-scoped extraction and card-dependency regressions are included in the focused suite.

No false positive was identified in these limited expectations. The initial truncation test did expose a false-confidence risk: a result did not disclose that the prefilter had cut off an owned peer. The minimal correction now discloses this in coverage, including when another peer was found. It does not imply global or complete collection coverage. The 64-candidate test demonstrates a false abstention: a compatible peer sorted after the cutoff is missed. A different colored-symbol requirement also produces a deliberate conservative abstention; the test does not establish that such a comparison would be valid in every context. There may be other missed dependencies and false conclusions outside these examples.

The fixture-backed tests check repeated execution and reversed input order with stable printing identities. Metamorphic cases change cost, target text, secondary effect, identity and owned peers. These checks cover the exercised inputs, not all orderings or possible Magic semantics. No general throughput or memory benchmark was conducted.

## Validation

- Focused CPA command: `.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_collection_playability_efficiency_golden.py tests/test_collection_playability_efficiency.py tests/test_collection_playability_contracts.py tests/test_collection_playability_adapters.py tests/test_collection_playability_mechanics.py tests/test_collection_playability_dependencies.py tests/test_collection_playability_semantic_coverage.py` — **86 passed**.
- Applicable suite including native-engine and upstream deck-building regressions: `.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'` — **614 passed, 2 deselected**.
- The two deselected Streamlit AppTests were each attempted independently with a 20-second subprocess timeout. Both timed out and remain unverified; they are not included in passing totals. No cause was established in this round.

## Remaining work and next action

The guide still requires defensible context-sensitive effect bundles, timing, reliability, repeatability, non-mana resources, additional functional groups, independently reviewed wider Golden coverage, and assessment of high-impact cases. The MVP's requested local review report also needs a conservative design consistent with the later classification phase. Nothing here authorizes bulk exclusion or pipeline integration.

Recommended next action: independently review and add a small pinned set of real repeatable draw and non-mana-cost cases that currently abstain, before proposing any model extension.
