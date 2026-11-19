# Phase 2: effect efficiency — partial implementation

**Release decision: BLOCKED.** The fixed-draw component model below is a bounded first increment, not the full Phase 2 model in the CPA implementation guide. In particular, it does not assess removal, mana, recurring abilities, non-mana resource costs, or general high-impact effects; it does not value the mill component, nor has it been calibrated with an independent real-card Golden dataset. The Thassa's Bounty-like case requires a compatible owned peer and is not an unconditional assessment. Absence of a finding is not evidence of efficiency or inefficiency. No classification, bulk review decision, global ranking or Phase 3 comparison is produced.

## Model and safety

`CollectionInputs.analyze` retains Phase 1 evidence, execution units, uses, target requirements, semantic coverage and dependencies. `cpa-efficiency-v1` compares a verified, legal, single-boundary fixed draw sorcery, optionally with the exact supported targeted mill clause, to an owned legal sorcery with the same draw quantity and no secondary effect. Color identity and fixed colored/colorless mana symbols must match; a greater generic cost gives a relative observation about the draw component only. The finding cites both card evidence records, records the actual costs, timing, targeting conditions and conditional value of secondary mill. Snapshot provenance pins the metadata. It neither calls the peer a substitute nor claims dominance, and does not assess the whole bundle's net value.

Only uses whose Phase 1 safety is supported or conditional can produce a positive observation. Unsupported or unverified abilities, additional costs, mixed and linked effects, face structures and unresolved dependencies are excluded by the exact shape and safety checks. The model cannot detect every rule or dependency: its verifier is bounded as described in [semantic coverage](semantic-coverage.md) and [card dependencies](card-dependencies.md). The search processes at most 64 draw-prefixed owned sorceries in snapshot order, so missing a peer cannot establish that none exists. The `effect_efficiency` coverage explicitly reports unsupported, insufficient evidence or evaluated with these limitations. Findings are deterministic and versioned; this model is not a calibrated score.

When a 65th eligible prefilter candidate exists, coverage now includes `peer_search_truncated`, whether or not a peer was found. See the [independent draw validation](../phase-2-draw-validation.md) for the pinned real-card cases, results and remaining release work.

## Remaining release work

Implement independently evidenced, context-sensitive functional peer groups and meaningful treatment of effect bundles, timing, reliability, repeatability and non-mana costs. Assess high-cost high-impact counterexamples without extrapolating from mana value. Independently pin and review real-card fixtures, measure performance and coverage, and establish the guide's Golden and regression gates. The guide's MVP also requests a local review report while reserving classification for Phase 5; resolve that tension conservatively without asserting unsupported classifications. Do not claim PASS until the Phase 2 deliverables and gate have been independently validated.

The two pre-existing Streamlit AppTests are separate from this semantic gate. Both timed out when individually attempted with a 20-second limit; this does not establish their failure or success.

## Verification in this increment

The regression command `python -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'` passed 604 tests, with those two tests deselected. The focused efficiency, dependency and semantic-coverage run passed 31 tests. The efficiency tests use synthetic fixed-draw examples; they are not the independently reviewed real-card Golden dataset required above.
