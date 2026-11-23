# CPA Phase 2 — Personal-project MVP closure

**Revised Phase 2 MVP gate — CONDITIONAL PASS.** The current supported models satisfy the revised acceptance criteria. This decision uses the owner-approved personal-project scope from 2026-11-19. The original implementation guide remains the roadmap for wider efficiency coverage. Historical reports and their original gate decisions remain preserved.

## Verified acceptance

The closing round inspected HEAD `c58dc8cc2f41310d87c6d9270c5850b7e47831fc` and the existing local CPA changes. It exercised pinned fixtures through `CollectionInputs.analyze`, using the existing test collection helpers, and ran the current tests afresh. No concrete defect requiring production changes was identified in these supported examples. Closure changes only the Phase 2 decision record, this report and the Phase 3 brief.

| Revised requirement | Observed evidence |
| --- | --- |
| Supported sorcery draw comparisons | Thassa's Bounty with Brilliant Plan owned returns `higher_mana_for_same_draw` under `cpa-efficiency-v1`, comparing three drawn cards for `{5}{U}` versus `{4}{U}`. Both endpoint spans remain cited. The pure-draw and fixed-draw Golden cases pass. |
| Supported artifact activation comparisons | Jayemdae Tome with Arcane Encyclopedia owned returns the one-generic-mana activation difference under `cpa-activation-draw-efficiency-v1`. Without that peer it returns explicit `unknown`. Exact payment spans, native draw evidence and per-activation requirements remain attached. |
| Bundle prerequisites and contextual value | Default Bounty context keeps mill contribution unknown. Explicit `mill` produces conditional benefit only with its matching catalog preference. With no draw peer the mill annotation remains conditional and draw efficiency becomes unknown. Target legality is unassessed and bundle efficiency unknown in all those runs. |
| Target safety | Golden tests verify missing, mismatched and unverified target linkage for both supported mill wordings. Those cases emit no positive affected finding. Both effects retain the shared resolution prerequisite. |
| Unsupported and uncertain inputs | Time Stretch remains unsupported with no efficiency finding. Greed and Staff of Domination retain unsupported draw uses. The pinned League Guildmage, Endbringer, secondary-effect and additional-cost cases pass their abstention regressions. |
| Prior-phase safety | Reassembling Skeleton retains conditional recursion/fodder uses, and Plummet retains conditional restricted interaction. Overall profile confidence may remain low because other native coverage is partial. Classification remains uncertain and bulk review eligibility false. |
| Reproducibility and bounded search | Existing exact-evidence, payload, replay, reversed-order, peer-limit and truncation regressions pass. |

These observations establish the supported fixture behavior. They do not provide universal Magic coverage, an independently measured error rate across all cards, or proof of runtime target availability. No inventory deletion or automatic filtering is introduced.

## Closing validation

```powershell
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'
```

The fresh focused CPA, boundary and adversarial selection passed **215 tests**, with zero failures or skips. One fresh applicable broad suite passed **695 tests**, with zero failures, zero skips and **2 deselected**. It includes native-engine and deck-building regressions. Totals overlap and should not be added together.

The excluded Streamlit AppTests `test_suggestions_page_filters_by_identity_and_strategy_without_corrections` and `test_builder_generates_with_selected_catalog_strategy` remain unverified after earlier individual timeouts. They were not rerun during closure and contribute no passing results. Their timeout alone does not block the revised MVP gate.

## Limits and deferred work

The models cover narrow fixed draw sorceries, one conditional artifact activation and one qualitative draw-plus-mill shape. Owned peers are bounded to 64 native evaluations with truncation disclosed. Missing peers do not establish global quality. Target availability, actual activation counts, replenishment, non-mana payments, broader functional groups, more effect bundles, creature comparisons and wider independently reviewed Golden coverage remain follow-up work. The concise backlog is maintained in the [revised Phase 2 decision](decisions/phase-2.md).

The initial useful Phase 3 scope is documented in the [collection-relative implementation brief](phase-3-implementation-brief.md). It reuses the existing comparison contracts, starts within verified draw groups and requires explicit tradeoffs and non-comparability. Phase 3 implementation remains for the next separately authorized round.
