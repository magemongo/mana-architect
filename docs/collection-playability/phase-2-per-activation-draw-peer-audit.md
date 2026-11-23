# CPA Phase 2 — Pinned snapshot audit of per-activation draw peers

**Full Phase 2 release status — BLOCKED.** The audit reproduces one per-activation cost finding. Sections 5.3 and 11 of the implementation guide still require contextual valuation of effect bundles, timing, reliability, repeatability, resource constraints and broader peer and Golden coverage. The gate also requires detecting Thassa's Bounty-like inefficiency while preserving expensive high-impact effects. [Model proposal](phase-2-per-activation-draw-efficiency-proposal.md) · [Implementation validation](phase-2-per-activation-draw-efficiency-validation.md)

## Input and method

The probe used HEAD `c58dc8cc2f41310d87c6d9270c5850b7e47831fc` with the existing local CPA changes and the pinned `default-cards.jsonl.gz` SHA-256 `d190c5ed83e3487daab01a69ff5f50c1d4ee55e1e83f0b08bfe295e2b9477672`. Its independent scanner selected 12 candidate Oracle IDs and 415 controls. The peer universe was the **synthetic collection of 427 selected Oracle IDs**, each represented by one synthetic owned printing from the snapshot. The probe makes no claim about peers in an actual collection or elsewhere in Magic. [Previous snapshot comparison and filters](phase-2-shared-tap-snapshot-validation.md)

The reference table uses printed type, pre-colon cost and draw quantity from the pinned records, with linked timing, requirements, dependency and target records from the mechanical profiles. The probe computes compatible and cheaper peers across that independent table before validating the selected finding. It records actual calls to `CollectionInputs.card` separately to check which peers the production search examined. The JSON includes the full Oracle IDs, draw spans, first exclusion, current reference keys, compatible and cheaper IDs, examined IDs, selected peer, numeric delta and its evidence.

## Funnel and exclusions

| Stage | Count |
| --- | ---: |
| Independent Oracle text match | 12 |
| Complete resolved activation boundary | 11 |
| Matching native draw fact | 11 |
| Verified activation coverage | 11 |
| Conditional draw use with both cost prerequisites | 5 |
| Eligible for the activation efficiency model | 4 |
| Cheaper compatible owned peer finding | 1 |

| Excluded subject | First disqualifying condition in funnel order |
| --- | --- |
| Aether Syphon | Resolver still merges the draw with the subsequent Max speed trigger. |
| League Guildmage | Its spell-copy activation leaves an unresolved card dependency. |
| Mercurial Chemister | The separate discard-and-damage activation leaves an unresolved card dependency. |
| The Great Mound | The token clause contributes an unresolved casting restriction. |
| Staff of Domination | Unresolved card dependencies include self-untap. |
| Endbringer | Unresolved card dependencies include the untap timing effect. |
| Snapping Voidcraw | Devoid leaves an unresolved card dependency in the current model. |
| Benalish Heralds | Its draw is conditional, but its printed type is `Creature — Human Soldier`, outside the artifact scope. |

## Reference and observed peer decisions

All four eligible use and execution-unit timings were `activated`. Each use recorded `legal_activation_timing`, `meet_creature_tap_restriction_if_applicable`, `pay_ability_cost` and `tap_source`. The units added no separate requirements, and all draws were untargeted with no use restrictions.

| Eligible subject | Printed type, Oracle cost, draw quantity | Identity and fixed symbols | Compatible peers | Cheaper peers and observed result |
| --- | --- | --- | --- | --- |
| Jayemdae Tome `39ee576a-0803-4063-9c84-f2b537e4d44c` | Artifact — Book, `{4}, {T}`, 1 | Empty identity and symbol multiset | Arcane Encyclopedia | Arcane Encyclopedia selected. `higher_mana_for_same_draw`, delta +1 generic mana. |
| Arcane Encyclopedia `d91ea728-2d69-42cb-bb5d-e2b058f0d7b1` | Artifact — Book, `{3}, {T}`, 1 | Empty identity and symbol multiset | Jayemdae Tome | None. `unknown` because the only compatible peer costs one more generic mana. |
| Tower of Fortunes `dfb23df9-f912-4daa-9842-01bd12b4a72a` | Artifact, `{8}, {T}`, 4 | Empty identity and symbol multiset | None | `unknown`. No other eligible activation draws four cards. |
| Scepter of Insight `e2725411-a600-4ff3-94fb-5d52f5eda414` | Artifact, `{3}{U}, {T}`, 1 | Identity `U`, symbol `{U}` | None | `unknown`. No other eligible one-card activation matches its identity and fixed mana symbols. |

The search examined 16 distinct peers per eligible subject in Oracle ID order. No compatible peer was omitted by the lexical prefilter, and no search reached the 65th prefilter match or reported truncation. The production result agreed with the unrestricted reference for all four subjects. No discrepancy required a reduced reproduction.

The positive finding cites Jayemdae Tome's `{4}, {T}` and Arcane Encyclopedia's `{3}, {T}`, both at Oracle `[0:8]`, and each native `Draw a card` ref at `[10:21]`. Each cost record resolves to its own endpoint boundary and uses `cpa-activation-draw-efficiency-v1` as the citation version. The other three findings cite only their own cost and draw evidence. Removing Arcane Encyclopedia from the synthetic collection yielded `unknown` for Jayemdae Tome with the same mechanical use. The existing five pinned unsupported controls and the local-versus-card-level dependency outcomes were preserved. The 427 profiles kept stable IDs under repeated and reversed traversal, and the shared-tap isolation check still passed. The sorcery Golden expectations and truncation tests passed in the focused suite.

## Commands and results

```powershell
.\.venv\Scripts\python.exe -B tests/cpa_mana_tap_snapshot_probe.py data/cache/scryfall/default-cards.jsonl.gz
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
```

The extended probe reproduced **4 eligible subjects and 1 cheaper-peer finding**. The focused suite passed **197 tests**, with zero failures or skips. This round edited the probe, its offline audit test and this report. The two Streamlit AppTests `test_suggestions_page_filters_by_identity_and_strategy_without_corrections` and `test_builder_generates_with_selected_catalog_strategy` were outside the selection and remain unverified after their earlier individual timeouts.

Recommended next action. Specify a pinned eligibility diagnostic for Benalish Heralds that identifies the additional creature tap conditions a separately approved creature peer model would need to preserve.
