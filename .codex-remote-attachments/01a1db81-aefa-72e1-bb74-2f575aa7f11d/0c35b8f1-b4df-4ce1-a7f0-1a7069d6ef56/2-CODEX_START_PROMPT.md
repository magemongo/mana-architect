# Codex kickoff prompt — Commander Deck Building Pipeline

You are working in my existing local Magic: The Gathering Commander deck-builder repository. **First inspect the codebase and its conventions; do not assume a blank project or rewrite working modules.**

## Context

The project builds Commander decks using **only cards I own**. Existing evaluation engines already cover Ramp, Card Advantage, Spot/Mass Interaction, and Strategy Synergy, using normalized Oracle/mechanical data. The former monolithic workflow attempted to choose a closed 99-card deck immediately. We are replacing it with a staged pipeline:

1. Candidate Pool Builder (broad, multi-role pool; no 99-card cap)
2. Pairwise Synergy Analyzer
3. Synergy Package Builder
4. Preliminary Deck Composer
5. Deck Refinement Engine

Read `docs/deck-building/ARCHITECTURE.md` as the cross-component contract and read any existing implementation guides, particularly the Candidate Pool Builder guide. Existing engine behavior and data models are the source of truth; use adapters rather than rewriting engines.

## Your first task: architecture alignment and Phase 0

1. Inspect repository structure, language/framework, test runner, current deck-building entry points, collection models, commander legality checks, normalized card/mechanical data, and each of the four engine APIs.
2. Summarize the current architecture and identify integration points, missing data, conflicts with the proposed architecture, and potential migration risks.
3. Propose a minimal folder/file reorganization **consistent with the current repository**. Avoid creating duplicate service layers or moving large amounts of existing code without clear benefit.
4. Add or align versioned shared contracts for `DeckBuildingContext`, `CardCandidate`, `CandidatePool`, `PairwiseSynergyEdge`, `SynergyPackage`, `DeckProposal`, and validation/decision records. Preserve card identity versus owned-printing identity and available quantities.
5. Implement thin adapters to the existing engines only where required. Do not change engine scoring or semantics without an explicit compatibility note and tests.
6. Add fixture-based contract and eligibility/inventory tests. Ensure deterministic output ordering and traceable evidence/reason codes.
7. Provide a brief implementation plan and file-by-file change list **before** editing files. Then implement Phase 0, run available tests, and report results.

## Suggested layout (adapt, do not blindly copy)

```text
docs/deck-building/
  ARCHITECTURE.md
  01-candidate-pool-builder.md
  02-pairwise-synergy-analyzer.md
  03-synergy-package-builder.md
  04-preliminary-deck-composer.md
  05-deck-refinement-engine.md
  decisions/
src/deck_building/
  contracts/
  adapters/
  eligibility/
  candidate_pool/
  pairwise_synergy/
  synergy_packages/
  composition/
  refinement/
  validation/
  orchestration/
  observability/
tests/deck_building/
  fixtures/
  unit/
  integration/
  regression/
```

If my project uses a different language, naming, or layered architecture, preserve its idioms and map these as logical modules rather than literal directories.

## Constraints

- **Owned cards only**; do not fabricate collection entries or add unowned cards to proposals.
- Apply Commander format legality, color identity, inventory counts, and copy-limit exceptions as hard constraints.
- Multi-role classification must not duplicate card records or inventory usage.
- Keep intrinsic engine scores separate from pairwise/package and deck-context scores; never add incompatible scales without normalization.
- No deck-dependent assumptions in Candidate Pool Builder.
- Pairwise synergy requires mechanical evidence and validated prerequisites, not keyword overlap alone.
- Packages must not be labeled infinite without verified closed-loop resource and rules conditions.
- Use versioned schemas, deterministic tie-breakers, structured errors, and audit-friendly reasons.
- Keep reserve candidates and reversible selection decisions.
- No UI overhaul, no new external infrastructure, no speculative ML components.
- Do not implement all five stages in one large change.

## After Phase 0: Phase 1, Candidate Pool Builder

Once contracts and adapters are tested, implement the Candidate Pool Builder according to its guide:

- Filter to eligible, owned cards.
- Collect candidates separately by Ramp, Card Advantage, Spot Interaction, Mass Interaction, Strategy Synergy, and Lands.
- Support flexible role budgets and quality thresholds, while preserving mechanical subtype diversity.
- Merge role memberships into unique candidates; retain selection reasons, provenance, and reserve options.
- Produce coverage/diversity diagnostics and explicit gaps, not an artificial 99-card list.
- Add tests for multi-role cards, sparse collections, color-identity exclusion, quantity accounting, deterministic replay, and score-scale isolation.

**Stop after Phase 0 unless I explicitly ask you to proceed with Phase 1.** If information is missing, state concrete questions and sensible assumptions rather than silently inventing APIs or rules.

## Expected response

Before edits: concise repository assessment, architecture-to-code mapping, proposed file tree, exact files to create/modify, and identified risks.

After edits: summary of implemented changes, test commands/results, unresolved decisions, and the precise next task for Candidate Pool Builder.
