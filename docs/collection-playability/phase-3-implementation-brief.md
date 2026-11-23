# CPA Phase 3 — Initial collection-relative functional competition

Implement bounded comparison between owned cards for one verified function and context. The guide (§5.4 and Phase 3 in §11) requires capability-based peers, explicit tradeoffs, non-comparability and separate collection/global evidence. Start with the verified fixed untargeted draw sorceries and single-activation artifacts supported in Phase 2.

Reuse the immutable collection snapshot, native capabilities, execution units, applicability and efficiency findings. Reuse `ComparisonEvidence`, `ComparisonOutcome` and `PlayabilityProfile.comparisons`. Retrieve distinct owned Oracle IDs within the same model, quantity, timing, identity and payment constraints. Use stable ordering, bounded work and explicit truncation. A missing alternative remains incomplete search coverage.

Emit `functional_substitute` only for the stated printed draw role and its prerequisites. Preserve both endpoint evidence and every material difference. Sorcery comparisons include casting payments, timing, additional costs, targeting and secondary effects. Artifact comparisons include printed deployment costs, per-activation payments, tap requirements and relevant type/subtype differences. Equal activation payment alone cannot settle total cost over a game. Emit `not_comparable` when purpose, coverage or constraints cannot be established. Reserve exact equivalence, contextual upgrade and dominance for later validated rules.

Jayemdae Tome and Arcane Encyclopedia can demonstrate conditional substitution for a one-card activation role with their deployment and activation tradeoffs. Bounty's draw-only efficiency finding cannot establish that Brilliant Plan replaces its draw-plus-mill bundle. Keep its mill value and target prerequisites explicit. Creature cases and unsupported effects retain abstention.

## First acceptance gate

- An offline owned pair cites both endpoint spans, defined role and explicit deployment/activation differences.
- A bundle-purpose case is non-comparable. Missing owned peers, unresolved dependencies and a synthetic cost tradeoff retain uncertainty and material differences.
- Output is deterministic on repeat and reversed input, respects owned identities and bounded search, and passes existing CPA, contract and deck-building regressions.
- Findings remain CPA diagnostics. Candidate Pool filtering, global corpus claims, uniqueness, classification and bulk exclusion belong to separately scoped deliveries.

Likely files are a new `src/mana_architect/collection_playability/competition.py`, orchestration in `adapters.py`, focused offline tests and a decision record. Change `contracts.py` only for a demonstrated gap in the existing comparison contract. Version the comparison rules and account for profile evidence and ID changes. Keep the engines acyclic and do not add CPA points to composer scores.

Implementation awaits the next separately authorized round.
