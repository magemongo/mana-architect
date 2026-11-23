# CPA Phase 2 — Shared tap evidence on the pinned snapshot

**Full Phase 2 release status — BLOCKED.** The guide (§5.3 and §11) still requires peer-relative effect costs, contextual valuation of combined effects, timing, reliability, repeatability, resource constraints and transparent explanations. The release gate also requires detecting Thassa's Bounty-like inefficiency without penalizing expensive high-impact effects across the board. The shared tap evidence describes a printed prerequisite and contributes no efficiency verdict. [Implementation validation](phase-2-additive-shared-tap-validation.md)

## Scope and counts

The extended probe ran at HEAD `c58dc8cc2f41310d87c6d9270c5850b7e47831fc` with the existing local CPA work. The Scryfall cache SHA-256 was `d190c5ed83e3487daab01a69ff5f50c1d4ee55e1e83f0b08bfe295e2b9477672`, matching the repeatable draw and League Guildmage fixtures. The scanner, Commander legality and single-face filters are unchanged from the [previous probe](phase-2-mana-tap-snapshot-probe.md). It screened 31,454 eligible Oracle IDs and analyzed 427 selected cards. Only the probe, its offline tests and this report were changed.

| Set | Analyzed Oracle IDs | Annotated IDs | Groups | Cost citations |
| --- | ---: | ---: | ---: | ---: |
| Candidates | 12 | 3 | 3 | 8 |
| Controls | 415 | 0 | 0 | 0 |
| Union | 427 | 3 | 3 | 8 |

Each group below belongs to face `0`. Offsets are zero-based and end-exclusive. The probe checked every cost against the original Oracle slice through the colon and against both the resolved activation boundary and its profile coverage. It checked distinct boundaries, same card and face, nonoverlap, the mandatory tap token and verified native draw evidence in each group. Replay and reversal of card, printing and evaluation order preserved the groups and profile IDs for all 427 cards.

| Group and Oracle ID | Exact cost | Oracle span | Activation boundary ID |
| --- | --- | --- | --- |
| League Guildmage `08bb1f33-b232-4382-a268-4804d4e72d29` | `{3}{U}, {T}` | `[0:11]` | `9d6206f0692daef8c5a3e46b84f9fe1498d56ae7846b0aa438862757f0286100` |
| League Guildmage | `{X}{R}, {T}` | `[26:37]` | `7e835f235e3869426e20b0bc3eb99762f4ad4f84105fc7f5b6e6939cd67bd143` |
| Staff of Domination `d7888719-647d-4022-a211-822fa09f0791` | `{2}, {T}` | `[26:34]` | `bfed74ccf416d68d638e5cabb97f7daea9329740b4256b6fc7dedfb1d4eb604d` |
| Staff of Domination | `{3}, {T}` | `[53:61]` | `1b705c4fbd214be3dee7f6b6072334c16a25c89ed550906e8f1cf1cfdc8231b0` |
| Staff of Domination | `{4}, {T}` | `[86:94]` | `2ec4217d6bf26c0ce3dd3ecf4e5cc30d50464ab94ee1cfd202e42b9344e2d9da` |
| Staff of Domination | `{5}, {T}` | `[117:125]` | `a564ec620184d181ecacc703345ed953028fbc4b61c1c57106344d7c4c2e6acf` |
| Endbringer `db1ea6e0-4076-49dd-92f7-7e6d7eb65a27` | `{C}, {T}` | `[108:116]` | `9d3535499e6c1305da154da0711c7e10731a604baa0c6b77fa05820da844deb3` |
| Endbringer | `{C}{C}, {T}` | `[167:178]` | `971efbb579edefcc557b0380f3d51b89491c061f173681737431052f3b59a0af` |

## Per-card comparison

The previous Markdown report is the historical baseline available in the workspace. Its full profile JSON was not retained, and its HEAD included uncommitted changes, so HEAD alone cannot reproduce the exact source. The probe matches each reported Oracle ID, cost and native draw quantity to a unique activation in the same pinned text and joins by Oracle ID and reconstructed span. Only Aether Syphon had an explicit span recorded in the previous report. The earlier stage criteria support the successful cost, tap, evidence and execution-unit association checks. The earlier report does not supply exact historical unit IDs, complete requirement sets, profile IDs or every dependency record. Historical control checks are limited to the reported aggregate outcomes and the pinned cases.

The stage counts reproduced **12 source matches, 11 resolved boundaries, 11 native matches, 11 verified boundaries and 5 conditional uses**. All 12 historical outcomes and reported blocker paths matched. Each current verified draw still points to its own execution unit and preserves `pay_ability_cost` and `tap_source`. The probe emits current evidence IDs, unit links and structured blocker details by span.

| Card | Draw activation span | Decision compared with historical report | Shared tap evidence now |
| --- | --- | --- | --- |
| League Guildmage | `[0:25]` | Locally verified. Copy activation still causes `card_dependency_unresolved`. | 1 group, 2 citations |
| Benalish Heralds | `[0:25]` | Conditional. | None |
| Jayemdae Tome | `[0:22]` | Conditional. Pinned single-activation control. | None |
| Mercurial Chemister | `[0:25]` | Locally verified. The discard-and-damage activation still blocks the draw. | None |
| The Great Mound | `[170:192]` | Locally verified. The token clause still contributes a `casting_restriction` blocker. | None |
| Staff of Domination | `[117:139]` | Locally verified. Four dependencies still block the draw, including self-untap. | 1 group, 4 citations |
| Aether Syphon | `[145:167]` | Boundary gap. Resolver still joins `[145:314]` with the subsequent Max speed trigger. | None |
| Arcane Encyclopedia | `[0:22]` | Conditional. | None |
| Endbringer | `[167:192]` | Locally verified. Draw still blocked, including by `timing_or_frequency` for its untap text. | 1 group, 2 citations |
| Tower of Fortunes | `[0:26]` | Conditional. | None |
| Scepter of Insight | `[0:25]` | Conditional. | None |
| Snapping Voidcraw | `[50:75]` | Locally verified. Devoid still causes `card_dependency_unresolved`. | None |

The five pinned unsupported controls, Greed, Mind Stone, Tome of Legends, Arch of Orazca and Jalum Tome, remain unsupported. Across 421 control clauses none passed the mana-and-tap verifier and six had conditional draw uses through existing rules outside the scanned grammar. Both aggregates agree with the previous report.

**Current-code isolation check.** A separate fresh analysis of all 427 inputs patched the annotation emitter at its call site to return an empty tuple during evaluation. Comparing the full typed profiles modulo the annotation showed equality for every card, including coverage, use safety, applicability, requirements, execution-unit links, all dependency records, strategy and efficiency. The 424 unannotated profile IDs also matched the disabled run. This compares evaluation with emission enabled and disabled in the current code. It cannot establish full historical payload equality. Stripping the new JSON key from an already-emitted profile would only test serialization.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -B tests/cpa_mana_tap_snapshot_probe.py data/cache/scryfall/default-cards.jsonl.gz
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
```

The snapshot probe completed with **12 historical candidate matches and 427 isolation and replay matches**. The focused CPA, boundary and adversarial regressions passed **185 tests**, with zero failures or skips. The Streamlit AppTests `test_suggestions_page_filters_by_identity_and_strategy_without_corrections` and `test_builder_generates_with_selected_catalog_strategy` were outside this selection and remain unverified after the previously recorded timeouts.

Recommended next action. Add a pinned Aether Syphon diagnostic that isolates why the Max speed trigger merges into the draw boundary, while preserving the current abstention.
