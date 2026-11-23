# CPA Phase 2 — Offline shared tap acceptance baseline

**Full Phase 2 release status — BLOCKED.** This regression establishes a mechanical baseline. Sections 5.3 and 11 of the guide still require peer-relative effect costs, contextual evaluation of combined effects, timing, reliability, repeatability and resource constraints, and transparent explanations backed by broader Golden coverage.

The new test in `tests/test_collection_playability_dependencies.py` uses a synthetic artifact and the existing in-memory collection helper. `CollectionInputs.analyze` resolves distinct boundaries and execution units for `{3}{U}, {T}: Draw a card.` and `{2}, {T}: Draw two cards.` on the same card and face. Each unit retains its own exact Oracle cost span, fixed native draw quantity and evidence. Both uses are conditional and require `pay_ability_cost` and `tap_source`. The two source-bound costs are the evidence for the shared prerequisite. [Acceptance criterion](phase-2-shared-tap-dependency-criterion.md)

Appending the separate static clause `Players can't draw cards.` preserves both boundary IDs, local verification, costs and evidence associations. A `draw_restriction` record cites the entire clause and points to both draw boundaries. Both uses become unsupported with `card_dependency_unresolved`, and the profile emits no positive strategy evidence from either use and no efficiency findings. The existing League Guildmage regression still passes with its intact-card abstention. [League Guildmage diagnostic](phase-2-league-guildmage-dependency.md)

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_collection_playability_dependencies.py::test_synthetic_shared_tap_draws_keep_local_evidence_under_draw_restriction
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_collection_playability_dependencies.py
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
```

The isolated test, dependency suite and focused regression run passed **1**, **21** and **177** tests respectively, with zero failures, skips or deselections within those selections. The test runs offline without a bulk cache. The two Streamlit AppTests, `test_suggestions_page_filters_by_identity_and_strategy_without_corrections` and `test_builder_generates_with_selected_catalog_strategy`, were outside the selection and remain unverified.

Recommended next action. Review a separately approved proposal for an additive shared-tap record that preserves the direct restriction's blocking behavior established by this test.
