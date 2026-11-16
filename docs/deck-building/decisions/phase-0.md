# Phase 0 decisions

## Integration

`deck_pipeline` sits alongside the existing Python modules. The CLI and Streamlit currently use `deckbuilding.build_deck`. The separate `candidate_evaluation.rank_collection_candidates` API orders by Strategy Synergy and applies a limit before evaluating the other roles. A later integration should use the eligibility gate and per-card adapters as its input boundary so that ranking policies can see every eligible card.

| Engine | Phase 0 entry point | Preserved output |
| --- | --- | --- |
| Ramp | `ramp_evaluator.evaluate_ramp` | Native line scores, mechanisms, requirements, and Oracle evidence |
| Card Advantage | `card_advantage.compile_profile` | Supported cast lines and source spans. `evaluate_cast` computes strict net cards |
| Spot and Mass Interaction | `interaction_evaluation_engine.classify_card_interaction` | Scored variants, separate spot and mass scores, effects, warnings, and evidence |
| Strategy Synergy | `strategy_synergy.evaluate_synergy` | Commander and strategy scores, capability profiles, conditions, and evidence |

The adapters share the catalog-aware Oracle parse used by the current evaluation command. They retain native profile types and engine versions. Ramp and Card Advantage have limited coverage for cards with multiple faces. Collection metadata supplies flattened face fields, so Interaction can report unresolved per-face execution details. No normalization or role admission policy is defined in Phase 0.

## Identity and inventory

Candidates and deck entries use `Card.oracle_id`. An owned printing ID is the local `CollectionPrinting.id`. Two rows may share a Scryfall printing ID and have different finishes, languages, or conditions. The snapshot preserves those rows independently. Deduplicating by Scryfall printing ID would lose inventory distinctions and allocating by Oracle ID alone would lose the requested printing. A deck entry may defer printing allocation or explicitly allocate the full entry across owned rows.

The snapshot captures linked card metadata, unlinked inventory, and the Scryfall legality date. Its ID fingerprints the captured content in canonical order. Unlinked rows remain visible in diagnostics and require synchronization or reconciliation before their copies can be used. A later export can resolve printing IDs against the snapshot for that run.

## Rules and commander configuration

`commander-snapshot-v1` checks ownership, dated Commander legality, color identity, intrinsic basic land colors, English-name copy limits, and total size. It uses the existing helpers for basic lands and Oracle copy exceptions. The snapshot has English names and Oracle IDs. It lacks a canonical equivalence key for interchangeable names that might be present under different Oracle IDs. Support for those cases requires verified name-equivalence data. Unmodeled special commander permissions or deck-construction modifiers also require distinct ruleset support.

The contract keeps commander IDs as a tuple and derives the expected main-deck size from the commander count. The current gate accepts a single commander eligible by front-face types or an explicit Oracle permission. Paired command zones receive a structured unsupported-configuration result. Supporting them requires pairing rules and an engine policy for direct synergy against multiple commanders. Applying the existing engine separately and summing scores could count shared effects twice.

The validator checks proposals against the recorded snapshot. This decision keeps replay independent of changes to the live ban list. An undated sync or unknown legality or color identity blocks validation. Rules references are the [Wizards Comprehensive Rules](https://magic.wizards.com/en/rules), [Commander Masters release notes](https://magic.wizards.com/en/news/feature/commander-masters-release-notes), and [interchangeable-name rules update](https://magic.wizards.com/en/news/announcements/comprehensive-rules-changes-4-20-22).

## Contract and migration boundary

Version 1.0 is an in-memory contract using frozen dataclasses and tuple collections. Pool role membership is derived from candidate IDs. Edges require ability and rule references. A package derives its infinite flag from recorded closed-loop, resource, and timing verification. A proposal defaults to unvalidated and can receive the report returned by the gate. Persistence, wire serialization, and a config content fingerprint remain open. The caller supplies the config version and the matching engine catalogs for a run.

Stage protocols define the handoff types without executable discovery, analysis, or composition. Phase 1 can consume the eligibility gate and adapters to define role-specific admission, subtype diversity, reserves, and gap diagnostics. Per-role normalization and budgets, edge confidence thresholds, package search bounds, and composition policy are decisions for the respective stages.
