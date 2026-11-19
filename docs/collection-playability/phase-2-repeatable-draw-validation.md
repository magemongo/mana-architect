# Phase 2 — repeatable draw and non-mana costs

**Release status: BLOCKED.** This review adds six pinned cases and safety tests, without extending production behavior. The Phase 2 guide requires broader context-sensitive efficiency, including effect bundles, timing, reliability, repeatability, non-mana resources and functional peer groups, as well as a broader independently reviewed Golden dataset. This small set does not satisfy those requirements or establish general Magic rules completeness.

## Scope and provenance

This round adds `tests/fixtures/cpa_repeatable_draw.json` and `tests/test_collection_playability_repeatable_draw.py`. No production code or previous Golden expectations were changed. Existing local changes remain in the working tree. The real-card fixture was independently transcribed from the local Scryfall default-cards cache with SHA-256 `d190c5ed83e3487daab01a69ff5f50c1d4ee55e1e83f0b08bfe295e2b9477672`. The cache file's recorded modification time is 2026-11-11 15:00:09 UTC, not a claimed upstream publication date. The fixture records Oracle IDs, full text, costs, type, identity, legality and source URIs. The expectations below were stated from the source text before probing the evaluator; they are not derived from its output.

| Card | Independently reviewed expected property | Observed Phase 1 / efficiency behavior |
| --- | --- | --- |
| Jayemdae Tome | Pay `{4}` and tap to draw. Further activations require the ability to pay and tap again. | Exact activation-cost text and source span retained; draw use unsupported, no efficiency finding. Comma-separated costs are outside the verified activation-cost pattern. |
| Greed | Pay `{B}` and 2 life for each activation. Neither resource is converted into an arbitrary common score. | Exact cost text retained; draw use unsupported, no efficiency finding. |
| Mind Stone | Pay `{1}`, tap and sacrifice the artifact to draw; the same sacrificed object cannot simply be activated again. It also has a separate mana ability. | Activation cost and separate execution units retained; draw use unsupported, no efficiency finding. |
| Tome of Legends | Pay `{1}`, tap and remove a page counter. Replenishment depends on commander events. | Cost text retained; draw use unsupported and card dependency unresolved; no efficiency finding. |
| Arch of Orazca | Pay `{5}` and tap; activation requires the city's blessing. | Cost text retained; draw use unsupported and dependency unresolved. The blessing restriction is not established as a reliable structured prerequisite; no efficiency finding. |
| Jalum Tome | Pay `{2}` and tap, then draw and discard on resolution. Discard is an effect, not an activation cost. | Exact cost text retained separately from source effect; draw use unsupported, no efficiency finding. |

Phase 1 retains activation cost as source text and links uses to execution units; it does not thereby verify or normalize all of the life, sacrifice, counter, timing or condition semantics. Source spans alone do not establish complete interpretation. In particular the conditional repeatability in this table is an independently authored expectation, not a claim that the evaluator has proven unlimited activations.

## Findings

No positive efficiency finding or false confidence was observed in the six tested cards or three synthetic mutations. The model intentionally abstains: its fixed-draw comparator accepts a narrow class of sorceries, not these permanents or their activated abilities. Jayemdae Tome is an example of a simple, mechanically meaningful activation left unsupported by the current coverage; it is a coverage gap, but no claim is made that an efficiency comparison can be made safely without an appropriate peer and context. Arch of Orazca and Tome of Legends demonstrate unresolved conditions and dependencies rather than established negatives. These findings cannot establish a false-positive rate.

Tests check source identity, exact Oracle spans, activation-cost text, draw-use safety and execution association, unsupported coverage and absence of bulk eligibility. Repeated runs and reversed input order produce identical profile IDs for the exercised cases. Clearly marked synthetic variants alter life payment, substitute discard as a cost, add a timing condition and change owned peers; they remain unsupported with no efficiency finding. The existing fixed-draw Golden tests, including truncation disclosure, and existing boundary-scoped extraction and dependency regressions remain in the focused run. The tests do not prove that every material cost is represented structurally, and no general timing, throughput or memory analysis was made.

## Validation

- Focused: `.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_collection_playability_repeatable_draw.py tests/test_collection_playability_efficiency_golden.py tests/test_collection_playability_efficiency.py tests/test_collection_playability_contracts.py tests/test_collection_playability_adapters.py tests/test_collection_playability_mechanics.py tests/test_collection_playability_dependencies.py tests/test_collection_playability_semantic_coverage.py` — **95 passed, 0 failed, 0 skipped**.
- Applicable suite, including native-engine and deck-building regressions: `.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'` — **623 passed, 0 failed, 0 skipped, 2 deselected**.
- Each deselected Streamlit AppTest was attempted separately using `python -B -m pytest -q -p no:cacheprovider tests/test_streamlit_app.py -k <test_name>` with a 20-second subprocess timeout. `test_suggestions_page_filters_by_identity_and_strategy_without_corrections` and `test_builder_generates_with_selected_catalog_strategy` both timed out and remain unverified; no cause was established. They are not counted as passes, failures or skips in the suite figures.

## Next action

Independently specify a narrow semantic contract for comma-separated mana-and-tap activation costs, including exact cost preservation and repeatability limits, before proposing any production implementation.
