# Phase 3 decisions

## Local API

Call `SynergyPackageBuilder(synergy_context=catalog).build(gate, pool, pairwise_analysis)` from `mana_architect.deck_pipeline.synergy_packages`. Use the catalog and immutable inputs from the earlier phases. The builder checks run, collection, pool, rule and evaluation provenance, rechecks ownership, and binds each edge to its ordered Oracle capabilities. It revalidates the rule and required constraints before search. By default it uses active candidates and the commander. Include reserves in both the pairwise analysis and package configuration to discover packages from reserve edges.

`PackageAnalysis.to_json()` provides a separate serializable artifact with materialized packages, partial opportunities, work diagnostics, and cache provenance. The builder keeps the last matching artifact in memory. Persistent storage and a standalone CLI command are deferred.

## Functional closure and repeatability

The versioned template catalog maps existing verified rules to roles for tokens, artifacts, blink and return effects, counters, lands, casting and copying, and equipment support. A sacrifice package requires a compatible fodder source. A token feeder must link to the exact outlet event used in the payoff edge. A self-returning creature must have a typed, activated return from its own graveyard for a fixed mana cost. The closure check tests compatibility with both the outlet and payoff, including required types, token status, and recognized mandatory exile replacements on all participants. A seed lacking compatible fodder remains a partial opportunity.

Each completed path carries its directed edge IDs, original ability spans, event IDs, and remaining resource and timing requirements. The commander can fill a role as a contextual anchor and stays out of `member_card_ids`. A path with prerequisites is conditional. Supported repeatability includes paid activations, bounded rebound, and Food recursion with an explicit tap reset. A single card may supply both its own ETB fodder generator and a separate outlet. Exact equal life-loss and life-gain responses can sustain their own feedback until a terminal state once seeded. All emitted packages have `infinite=false`. General closed mana loops and copy–untap loops remain unsupported. [Phase 2 to Phase 3 validation](../phase-2-3-validation.md)

## Work bounds and Phase 4 handoff

Search starts from Phase 2 edges in stable strength and ID order. The defaults allow four participants including a commander anchor, up to 32 continuations per seed, and 10,000 path attempts per archetype. Current templates produce paths of two or three participants. When the search truncates, `limited_templates` records the archetypes affected. `pairwise_work_limit_reached` propagates incomplete Phase 2 coverage.

Package score uses the weakest required edge support after grouping by event, documented resource burden, and commander or selected-strategy alignment. It uses none of the standalone card engine scores. Treat it as a package ranking heuristic. Phase 4 can use shared `benefit_event_ids` to recognize overlapping package benefits, check whether required cards and conditions are supported, and use the existing legality gate to enforce printing quantities and deck constraints.
