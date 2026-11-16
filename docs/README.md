# Documentation

- [Project scope](project-status.md) describes the product and its current workflows.
- [Repository README](../README.md) covers installation, the UI, and CLI usage.
- [Deck generation](deckbuilding-guidelines.md) describes selection and validation.
- [Deck-building pipeline architecture](deck-building/ARCHITECTURE.md) defines the staged workflow. [Phase 0 decisions](deck-building/decisions/phase-0.md) map it to this repository.
- [Candidate Pool Builder implementation guide](deck-building/01-candidate-pool-builder.md) defines candidate retrieval, role coverage, and audit requirements.
- [Pairwise Synergy Analyzer](deck-building/decisions/phase-2.md) documents the Phase 2 API, rule semantics, and limits.
- [Synergy Package Builder](deck-building/decisions/phase-3.md) documents package discovery, closure, and the Phase 4 handoff.
- [Phase 2 to Phase 3 validation](deck-building/phase-2-3-validation.md) records the golden scenarios, measured release gates, and recommended fixes.
- [Preliminary Deck Composer](deck-building/decisions/phase-4.md) documents composition, validation, and the CLI and Python APIs.
- [Synergy and role scoring](synergy-scoring.md) describes candidate ranking and scoring evidence.
- [EDHREC theme coverage](edhrec-themes.md) lists scored strategies and reference themes.
- [Oracle text parser](oracle-text-parser.md) describes the parser contract, downstream evaluators, and inspector.
- [Catalog and classification persistence](classification-storage.md) describes schema migrations, transactions, and classification history.

## Candidate Pool Builder API

Load a collection snapshot with `load_collection_snapshot(session)`. Bind it to a `DeckBuildingContext` and an `EligibilityGate`. Set the context's `config_version` to `builder.config.version` for the chosen `CandidatePoolBuilder`. Call `builder.build(gate)` to run the four adapters, or pass a compatible `EvaluationSnapshot` to replay precomputed profiles. Pass `PoolConstraints` for user locks and exclusions. The returned `CandidatePool` includes membership evidence, reserves, coverage gaps, and a JSON-ready view via `to_json()`.

Quality floors use role-local scales. Ramp and Strategy Synergy scores are divided by 100, and Interaction scores are divided by 10. Card Advantage maps a positive strict net card count `n` to `n / (1 + n)`. Lands have no quality score. Land coverage reports keep unique identities and usable copies separate. A land on the reverse face alone is reported as unsupported for land coverage when the snapshot lacks layout information.
