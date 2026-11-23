# CPA Phase 2 — Per-activation fixed draw efficiency proposal

**Full Phase 2 release status — BLOCKED.** The implementation guide (§5.3 and §11) calls for peer-relative effects and costs, contextual valuation of effect bundles, timing, reliability, repeatability and resource constraints. This proposed increment compares one activation under equal recorded prerequisites. It leaves broader effects, repeatability in a game state and other resource costs open. Production implementation requires explicit approval.

## Eligible subject and peer

Add a separate `cpa-activation-draw-efficiency-v1` path. Limit the initial subset to owned, Commander-legal, single-face cards with printed type `Artifact` before the subtype separator, known color identity and one complete locally verified Oracle activation. This includes Artifact — Book and excludes mixed types such as Artifact Creature. Require the existing mana-and-tap verifier, a matching native fixed draw fact and exact Oracle evidence, a conditional untargeted draw use and one resolved activated execution unit for the same card, face and boundary. Reject unresolved dependencies even when the local boundary is verified. Require the exact printed activation cost to be a supported fixed mana payment followed by mandatory `{T}`, with the cost span checked against the original Oracle text. Exclude additional costs, effects, conditions and activation clauses outside this verified grammar. [Mana and tap validation](phase-2-mana-tap-validation.md)

Compare only different owned Oracle IDs that satisfy the same activation gate. Match draw quantity, timing, use and unit requirements, target semantics, color identity and the multiset of fixed colored or colorless mana symbols. Use the generic mana component of the activation cost to establish a strictly cheaper peer. Printed casting cost and mana value do not enter the comparison. The existing sorcery path keeps its prefilter, vocabulary, model version and outputs. A subject and peer must belong to the same path.

Use the existing limit of 64 native peer evaluations. Prefilter the activation path by the supported artifact type and a plausible fixed mana-and-tap draw prefix before analysis. Walk candidates in snapshot Oracle ID order, report `peer_search_truncated` when a 65th prefilter candidate exists, and choose the lowest qualifying generic payment with Oracle ID as a stable tie-breaker. Return `unknown` for eligible inputs when the bounded search establishes no cheaper compatible peer. Out-of-scope inputs retain an unsupported efficiency evaluation. Neither outcome provides evidence that no better peer exists. [Existing efficiency decisions](decisions/phase-2.md)

## Ground truth and finding

Verified the local Scryfall cache against SHA-256 `d190c5ed83e3487daab01a69ff5f50c1d4ee55e1e83f0b08bfe295e2b9477672`. The reviewed records are separate Oracle identities in that snapshot. The expected delta follows from their printed costs and effect, independent of evaluator output. A local pre-implementation run confirmed both currently have conditional, activated draw uses with the same recorded timing and tap prerequisites. [Pinned snapshot validation](phase-2-shared-tap-snapshot-validation.md)

| Card | Oracle ID | Exact Oracle text | Printed casting cost |
| --- | --- | --- | --- |
| Jayemdae Tome | `39ee576a-0803-4063-9c84-f2b537e4d44c` | `{4}, {T}: Draw a card.` | `{4}` |
| Arcane Encyclopedia | `d91ea728-2d69-42cb-bb5d-e2b058f0d7b1` | `{3}, {T}: Draw a card.` | `{3}` |

With both owned, emit `higher_mana_for_same_draw` for Jayemdae Tome's execution unit with model `cpa-activation-draw-efficiency-v1`. Its explanation states that one activation uses one more generic mana for the same one-card draw. Cite both existing native draw references and two separately versioned `EvidenceReference` records with exact pre-colon Oracle spans, endpoint card IDs and the corresponding boundary IDs as record IDs. Reuse the existing profile and finding fields. The finding describes only the evaluated activation relative to an owned peer. Each later activation requires enough payable mana and a source able to tap again, under applicable timing and permission. The comparison leaves deployment cost, repeated activation opportunities, whole-card value and substitution out of scope.

## Files and acceptance after approval

- `src/mana_architect/collection_playability/efficiency.py` for the activation eligibility gate, peer comparison and source evidence. `src/mana_architect/collection_playability/adapters.py` for the model-specific peer prefilter and coverage. The existing schema and contract types support the result.
- `tests/fixtures/cpa_activation_draw_efficiency.json` for the minimal new Arcane Encyclopedia, Staff of Domination and Endbringer records and snapshot provenance. Reuse the pinned Jayemdae Tome and League Guildmage fixtures. The repeated regression runs use the checked-in records offline.
- `tests/test_collection_playability_activation_efficiency.py` for the expected delta and both endpoint costs and draw spans, absence of a compatible peer, a casting-cost-only change, mismatches in quantity, symbol or color identity, extra costs, unresolved dependencies, cross-model rejection, repeat and reversed-input replay, and activation peer truncation. Assert no positive finding on League Guildmage, Staff of Domination or Endbringer. The existing sorcery Golden cases and truncation assertions remain the regression baseline.
- `tests/test_collection_playability_repeatable_draw.py` for the narrow update from no efficiency evaluation to an explicit unknown finding when the eligible Jayemdae Tome is analyzed by itself. The other pinned cases retain their existing abstention expectations. `docs/collection-playability/phase-2-per-activation-draw-efficiency-validation.md` will record changes, model version, remaining limits and results.

```powershell
$efficiencyTests = rg --files tests -g 'test_collection_playability*efficiency*.py' -g 'test_collection_playability_contracts.py' -g 'test_collection_playability_dependencies.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $efficiencyTests
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'
```

Before implementation, the focused CPA and boundary command passed **185 tests**. The two Streamlit AppTests were outside the selection and remain unverified after earlier timeouts. The applicable broad suite includes the native engine and deck-building regressions and reports both AppTests separately.
