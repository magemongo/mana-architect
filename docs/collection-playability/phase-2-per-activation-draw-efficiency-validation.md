# CPA Phase 2 — Per-activation fixed draw efficiency validation

**Full Phase 2 release status — BLOCKED.** This increment compares a narrow class of printed activation costs. The implementation guide (§5.3 and §11) still calls for contextual effect bundles, repeatability and replenishment, timing and reliability across other shapes, additional mana and non-mana costs, and broader independently reviewed Golden coverage. The gate still requires detecting Thassa's Bounty-like inefficiency while preserving expensive high-impact uses.

## Model and observed result

`cpa-activation-draw-efficiency-v1` applies the same eligibility check to subject and peer. It requires owned, Commander-legal, single-face artifacts of the approved printed type with one complete verified fixed untargeted draw, matching native evidence, a resolved activation, conditional applicability and no unresolved dependency. Peers match quantity, timing, use and unit requirements, target semantics, color identity and fixed colored or colorless symbol multiset. The delta uses only generic mana paid per activation. The 64-peer budget applies after a model-specific lexical prefilter in Oracle ID order. The 65th match causes `peer_search_truncated`, even when an earlier compatible peer was found. Among qualifying peers the model selects the lowest generic payment, breaking ties by Oracle ID. Without a cheaper compatible owned peer it returns `unknown`.

The independently reviewed pair comes from the pinned local Scryfall snapshot SHA-256 `d190c5ed83e3487daab01a69ff5f50c1d4ee55e1e83f0b08bfe295e2b9477672`. Jayemdae Tome (`39ee576a-0803-4063-9c84-f2b537e4d44c`) pays `{4}, {T}` to draw one card. Arcane Encyclopedia (`d91ea728-2d69-42cb-bb5d-e2b058f0d7b1`) pays `{3}, {T}` for the same fixed draw. When both are owned, the Tome's activation receives `higher_mana_for_same_draw` with a difference of **one generic mana**. A casting-cost-only mutation leaves the finding unchanged. Comparing one activation does not establish deployment cost, a repetition count or whole-card value. Every later activation requires payable mana, legal timing and a source able to tap again. [Approved proposal](phase-2-per-activation-draw-efficiency-proposal.md)

The finding cites both exact pre-colon cost spans `[0:8]` and both native `Draw a card` spans `[10:21]`. Cost references use the respective card ID, linked boundary ID as `record_id`, and the activation model version as `source_version`. Their scoped extraction fields remain empty. Native references keep their original evidence identity. The public schema is unchanged. Profiles receiving the new evaluation may receive new content-derived IDs. The fixed-draw sorcery model keeps `cpa-efficiency-v1`, and the existing sorcery Golden and truncation tests passed. The two models do not supply peers to each other.

## Changes and verification

Production changes are in `src/mana_architect/collection_playability/efficiency.py` and `adapters.py`. `tests/fixtures/cpa_activation_draw_efficiency.json` adds offline records for Arcane Encyclopedia, Staff of Domination and Endbringer. The tests reuse the pinned Jayemdae Tome and League Guildmage records. `tests/test_collection_playability_activation_efficiency.py` covers evidence, incompatible peers, explicit unknowns, excluded costs and dependencies, the owned pair, replay, tie-breaks and search truncation. League Guildmage, Staff of Domination and Endbringer continue to abstain from positive efficiency findings.

`tests/test_collection_playability_repeatable_draw.py` now expects standalone Jayemdae Tome and eligible synthetic single activations to return `unknown`. The other pinned cases retain their unsupported expectations. The shared-tap regression in `tests/test_collection_playability_dependencies.py` verifies its prior draw-only serialization digest with activation efficiency disabled in the test harness, and checks the new unknown result under default evaluation. The prior two-activation digest checks and existing dependency assertions remain in place.

```powershell
$efficiencyTests = rg --files tests -g 'test_collection_playability*efficiency*.py' -g 'test_collection_playability_contracts.py' -g 'test_collection_playability_dependencies.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $efficiencyTests
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'
```

The final selections passed **65** focused efficiency, contract and dependency tests, **196** CPA, boundary and adversarial tests, and **676** tests in the applicable broad suite, including native engine and deck-building regressions. The broad suite deselected two tests. All final runs had zero failures and zero skips.

The excluded Streamlit AppTests remain unverified after the previously recorded individual timeouts. This round did not rerun them:

- `test_suggestions_page_filters_by_identity_and_strategy_without_corrections`
- `test_builder_generates_with_selected_catalog_strategy`

Recommended next action. Extend the existing pinned snapshot probe to measure how many eligible real draw activations have a compatible cheaper peer under the new model.
