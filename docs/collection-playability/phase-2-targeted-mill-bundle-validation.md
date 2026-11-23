# CPA Phase 2 — Contextual draw and targeted mill validation

**Full Phase 2 release status — BLOCKED.** The guide's sections 5.3 and 11 still require broader contextual effect bundles, reliability, repeatability, resource constraints, functional peer coverage and independent Golden validation. This increment adds a qualitative assessment for one verified bundle and closes its target-evidence guard. Full-bundle efficiency remains unknown.

## Behavior and evidence

The pinned Thassa's Bounty and Brilliant Plan records retain the existing comparison of three drawn cards for `{5}{U}` versus `{4}{U}`. The v1 draw-component finding cites both native draw references. The shared spell unit must have exactly one target reference, bound to the exact native mill clause and its printed player or opponent target. Both uses must remain supported or conditional and carry the resolution prerequisite. Missing, mismatched or unverified linkage suppresses the positive component finding and exposes `bundle_target_evidence_unresolved`. The guard covers both targeted mill wordings accepted by the existing sorcery model.

Only `Target player` receives the optional `targeted_mill_assessment`, version `cpa-draw-targeted-mill-v1`. The record cites the mill and target evidence and their execution unit. It reports `unknown` mill contribution by default. Explicitly selecting `mill` with the versioned catalog's matching `mill_theme_mill_opponent` preference yields `conditional_benefit`. The explanation requires choosing a legal opponent and resolving the spell. Empty or automatically enumerated strategy context cannot establish that benefit. Removing the catalog preference yields unknown contribution while preserving a valid draw-only comparison. The annotation is evaluated even without an owned draw peer.

Target legality is always `unassessed`, and whole-bundle efficiency is always `unknown`. If the sole target is illegal when the spell would resolve, neither effect occurs. The explanation retains this dependency for both components. No conversion between drawn and milled cards is assigned. The approved proposal records the pinned rules and source evidence. [Contextual bundle proposal](phase-2-targeted-mill-bundle-proposal.md)

## Changed files and compatibility

Production edits are in `src/mana_architect/collection_playability/contracts.py`, `efficiency.py` and `adapters.py`. The contract validates the new assessment's execution, evidence and explicit strategy links. The optional field is omitted from `to_json()` when absent, so unaffected serialized payloads and their content-derived IDs retain the established format. Existing shared-tap legacy digest assertions passed. Annotated profiles and bundles rejected by the stronger guard may receive new IDs. Direct `asdict()` includes the optional empty field, so the adapter serialization test now accounts for its omission from public JSON. The underlying deck schema, native engines, dependency policy, activation model and peer-search bounds keep their existing versions.

Tests changed in `tests/test_collection_playability_efficiency_golden.py`, `tests/test_collection_playability_contracts.py` and `tests/test_collection_playability_adapters.py`. They reuse the existing pinned cards. Default and explicitly selected mill contexts were checked both with and without a peer. The target guard was tested for both wordings with missing, mismatched and unverified evidence. Valid opponent-target mill retains its existing positive component finding and emits no player-target annotation. Contract tests reject invalid unit, evidence, target-legality and context/catalog provenance claims. Time Stretch, unsupported secondary effects, pure draw, activation comparisons and truncation regressions remain covered.

## Validation

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_collection_playability_efficiency_golden.py tests/test_collection_playability_contracts.py
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'
```

The focused contract and Golden selection passed **40 tests**. The CPA, boundary and adversarial selection passed **215 tests**. The applicable broad suite, including native and deck-building regressions, passed **695 tests**, with **2 deselected**. Final runs had zero failures or skips. The Streamlit AppTests `test_suggestions_page_filters_by_identity_and_strategy_without_corrections` and `test_builder_generates_with_selected_catalog_strategy` were excluded and remain unverified after earlier individual timeouts.

Recommended next action. Add an independent adversarial review of the selected-strategy and target bindings before extending this qualitative bundle model to other secondary effects.
