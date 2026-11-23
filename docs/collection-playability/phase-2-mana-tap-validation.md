# CPA Phase 2 — Mana and tap activation coverage

**Phase 2 release status: BLOCKED.** The implementation guide requires context-sensitive efficiency across effect bundles, timing, reliability, repeatability, non-mana resources and functional peer groups, with broader independently reviewed Golden coverage. This increment supplies mechanical evidence for one activation shape. The fixed-draw efficiency comparator still covers its existing sorcery subset.

The approved [semantic contract](mana-tap-activation-contract.md) and the [repeatable draw fixture review](phase-2-repeatable-draw-validation.md) define the expectations. The fixtures pin Oracle identities, text and snapshot provenance. The activation rules use the [Wizards Comprehensive Rules dated 2026-11-06](https://media.wizards.com/2026/downloads/MagicCompRules%2020261106.txt), including 107.5, 302.6, 602.1 and 602.5a.

## Coverage and evidence

`semantic_coverage.py` recognizes a complete activated draw boundary on a single-face card. It accepts contiguous fixed generic, colored or colorless mana symbols, a comma, one `{T}`, a colon and one fixed unconditional draw instruction. Horizontal spacing after the comma and colon is allowed when native extraction agrees. The verifier requires the same exact activation cost, draw quantity, timing and source-aligned native evidence. Its existing rules for unresolved warnings still apply, including boundary-scoped evidence. `mechanics.py` preserves card, face, boundary and execution-unit links and applies the existing dependency gates. The raw cost is preserved as the exact prefix of the original boundary ending immediately before the colon.

Pinned Jayemdae Tome has verified local coverage and an evaluated profile with medium confidence. Its draw use is conditional and requires `pay_ability_cost` and `tap_source` each time, along with legal activation timing and the creature tap restriction where applicable. A later activation depends on source and mana availability, including the ability to tap the source again. No frequency or runtime game state is calculated. This rule contributes mechanical evidence only. It yields no strategic context or efficiency finding for the draw.

The other five pinned cards retain low-confidence profiles with unknown draw applicability and unsupported uses. Greed, Mind Stone and Tome of Legends carry life, sacrifice or counter costs. Arch of Orazca adds the blessing restriction. Jalum Tome's discard remains part of resolution and its cost remains `{2}, {T}`. Synthetic controls preserve abstention for extra discard costs, targeted dependencies on drawing, explicit timing restrictions, quoted, modal and linked text, cross-face cases and unsupported mana or tap symbols. Separate abilities preserve their own costs and evidence. Disagreement with native quantity or cost, missing evidence and a boundary-local unknown all prevent verification.

The previous false abstention for Jayemdae Tome's printed activation is resolved. No false confidence was observed in the exercised counterexamples. Parser or extractor gaps, ambiguous associations and unresolved dependencies can still cause false abstentions. The verified subset excludes multiple faces. It leaves game-state feasibility and repeatability conditional. The fixed-draw peer search still discloses truncation and retains its existing limit.

## Compatibility and changed files

The new rule carries `cpa-boundary-coverage-mana-tap-draw-v1` in the existing `verifier_version` field. Profiles affected by the rule may have new content-derived IDs. Recompute externally persisted CPA profiles when current coverage is required. The CPA package has no persisted-profile store to migrate. The schema, native engines, resolver and native evidence IDs use their existing versions, so this change requires no regeneration of published native pairwise evidence.

Production changes are in `src/mana_architect/collection_playability/semantic_coverage.py` and `src/mana_architect/collection_playability/mechanics.py`. Tests are in `tests/test_collection_playability_repeatable_draw.py`, `tests/test_collection_playability_semantic_coverage.py` and `tests/test_collection_playability_dependencies.py`. The existing pinned fixture and efficiency tests retain their expectations. The Jayemdae-specific mechanical assertion now checks its verified conditional use.

## Validation

```powershell
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'
```

The focused CPA and boundary suite had **125 passed, 0 failed, 0 skipped, 0 deselected**. The applicable suite, including upstream native engine and deck-building regressions, had **645 passed, 0 failed, 0 skipped, 2 deselected**. The two named Streamlit AppTests were excluded from this run and remain unverified. The prior fixture review recorded a timeout for each.

Recommended next action. Probe the pinned Scryfall snapshot for real cards matching the new activation grammar and report which have verifiable native evidence, before proposing any efficiency comparison.
