# Documentation

## Current references

- [Project scope](project-status.md) describes the product and its current workflows.
- [Repository README](../README.md) covers installation, the UI, and CLI usage.
- [Offline development baseline](development-baseline.md) records the test procedure, measured results, and known issues.
- [Deck generation](deckbuilding-guidelines.md) describes selection and validation.
- [Deck-building pipeline architecture](deck-building/ARCHITECTURE.md) defines component boundaries and the staged workflow. Use the phase guides and applicable later decisions for implemented details. The [Phase 0 decisions](deck-building/decisions/phase-0.md) record the original foundation.
- [Candidate Pool Builder implementation guide](deck-building/01-candidate-pool-builder.md) defines candidate retrieval, role coverage, and audit requirements.
- The [Pairwise Synergy Analyzer guide](deck-building/02-pairwise-synergy-analyzer.md) defines the stage. The [Phase 2 decisions](deck-building/decisions/phase-2.md) document the API, rule semantics, and limits.
- The [Synergy Package Builder guide](deck-building/03-synergy-package-builder.md) defines the stage. The [Phase 3 decisions](deck-building/decisions/phase-3.md) document package discovery, closure, and the Phase 4 handoff.
- [Phase 4 implementation guide](deck-building/04-preliminary-deck-composer.md) specifies the Preliminary Deck Composer. [Phase 4 decisions](deck-building/decisions/phase-4.md) document composition, validation, and the CLI and Python APIs.
- [Phase 5 implementation guide](deck-building/05-deck-refinement-engine.md) specifies the Deck Refinement Engine. [Phase 5 decisions](deck-building/decisions/phase-5.md) document swap search, replay, and the refinement audit.
- Collection Playability Analysis (CPA) decisions cover [snapshot and evidence contracts](collection-playability/decisions/phase-0.md), [mechanical utility](collection-playability/decisions/phase-1.md), [semantic coverage](collection-playability/decisions/semantic-coverage.md), [card dependencies](collection-playability/decisions/card-dependencies.md), and the [revised Phase 2 MVP](collection-playability/decisions/phase-2.md). The [ability boundary decision](collection-playability/decisions/ability-boundaries.md) predates the [current CPA execution adapter's use of resolved boundaries](../src/mana_architect/collection_playability/mechanics.py).
- [Synergy and role scoring](synergy-scoring.md) describes candidate ranking and scoring evidence.
- [EDHREC theme coverage](edhrec-themes.md) lists scored strategies and reference themes.
- [Oracle text parser](oracle-text-parser.md) describes the parser contract, downstream evaluators, and inspector.
- [Catalog and classification persistence](classification-storage.md) describes schema migrations, transactions, and classification history.

## Historical evidence and proposals

- [Deck pipeline Phase 2 to Phase 3 validation](deck-building/phase-2-3-validation.md) and the [CPA Phase 2 closing report](collection-playability/phase-2-mvp-closing-report.md) record the results and scope of their respective gates.
- Dated CPA validation reports and probes preserve evidence for the inputs and revisions they cover. Proposals, including the [CPA Phase 3 brief](collection-playability/phase-3-implementation-brief.md), describe planned or exploratory work. Copies under `.codex-attachments/` and `.codex-remote-attachments/` preserve earlier context.

## Candidate Pool Builder API

Load a collection snapshot with `load_collection_snapshot(session)`. Bind it to a `DeckBuildingContext` and an `EligibilityGate`. Set the context's `config_version` to `builder.config.version` for the chosen `CandidatePoolBuilder`. Call `builder.build(gate)` to run the four adapters, or pass a compatible `EvaluationSnapshot` to replay precomputed profiles. Pass `PoolConstraints` for user locks and exclusions. The returned `CandidatePool` includes membership evidence, reserves, coverage gaps, and a JSON-ready view via `to_json()`.

Quality floors use role-local scales. Ramp and Strategy Synergy scores are divided by 100, and Interaction scores are divided by 10. Card Advantage maps a positive strict net card count `n` to `n / (1 + n)`. Lands have no quality score. Land coverage reports keep unique identities and usable copies separate. A land on the reverse face alone is reported as unsupported for land coverage when the snapshot lacks layout information.
