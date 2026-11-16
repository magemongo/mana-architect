# Phase 2 decisions

## Local API and integration

Call `PairwiseSynergyAnalyzer(synergy_context=catalog).analyze(gate, pool)` from `mana_architect.deck_pipeline.pairwise_synergy`. The analyzer consumes the same eligibility gate and catalog as the Candidate Pool Builder. It verifies pool provenance and inventory, reuses native Strategy Synergy capability profiles, and extracts the commander's through the existing Oracle capability extractor. The commander can appear at either endpoint and is identified separately through `commander_anchor_ids`.

The result is an immutable `PairwiseSynergyAnalysis`. Serialize its `to_json()` payload for a separate run artifact. The analyzer retains the last matching result in memory. Its ID includes the pool content, collection and evaluation provenance, and analyzer, capability, rule, and policy versions. Persistent caching and CLI orchestration are deferred. By default, analysis covers active candidates and the commander. Enable `PairwiseConfig(include_reserve=True)` to include the owned reserve using the same gate.

## Evidence and work bounds

The analyzer indexes native capabilities and joins only the capability groups declared by the existing directional catalog. It preserves rule direction and maps the existing `directly_triggers` and `recurs` relations to the shared `enables` relation. Selected strategy IDs annotate matching rules without multiplying edge strength. Edge IDs identify rules and ordered event signatures. The separate event IDs identify source spans that can be shared by more than one rule. Each emitted edge records source evidence for both endpoints, prerequisites, timing and resource constraints, and an explanation. Strength uses the native rule weight and the configured multiplier for conditional edges (default 0.7).

Only the native validator's validated or conditional matches can yield edges. Missing or incompatible profiles, unresolved modal branches, and reverse faces without layout data appear in coverage diagnostics. Known mandatory death-to-exile replacements on an endpoint can suppress a death edge. Other battlefield effects can still prevent the interaction, and death edges explicitly require a battlefield-to-graveyard move. The analyzer does not assume which third cards are in play. The shared edge contract requires distinct card identities, so self-interactions are outside the current artifact. Commander support inherits the gate's single-commander ruleset.

The work budget counts attempted joins in deterministic order. If the cap truncates the run, the result sets `diagnostics.work_limit_reached`. Package discovery can use that flag to distinguish incomplete coverage from a fully analyzed pool. Package templates will need to check requirement closure across edges and use shared event IDs to deduplicate benefits.
