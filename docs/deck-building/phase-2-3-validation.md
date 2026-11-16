# Phase 2 to Phase 3 integration validation

**Release decision — PASS on the curated Phase 2 to Phase 3 gate.** All P0 regressions, contract checks, and deterministic replay tests pass. General copy–untap and mana-loop discovery remain unsupported.

## Remediation results

| Output | Initial precision | Initial recall | After P0 recall | Final precision | Final recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pairwise relations | 100% | 73.0% (27/37) | 78.4% (29/37) | 100% | **94.6% (35/37)** |
| End-to-end packages | 100% | 63.3% (19/30) | 70.0% (21/30) | 100% | **90.0% (27/30)** |

Isolated Phase 3 recovered **27 of 29 packages whose golden relations were encodable (93.1%)**, or **27 of 30 (90.0%)** with the unencodable copy–untap case included as a miss. The diagnostic uses independently authored endpoint directions and event keys. It binds each declared relation to the exact Oracle capability spans under the active rule catalog and builds a production `PairwiseSynergyAnalysis` for the real package builder. It skips Phase 2 search and does not read its generated edge set. Both Altar seeds reached Phase 3 in this diagnostic and still lacked closed-loop packages. The two Kiki-Jiki relations could not be encoded with the current catalog.

All 43 scenarios completed and passed normal handoff integrity and seeded replay. The handoff rejected reversed and cost-stripped artifacts. All 13 known negative infinite-loop cases withheld both infinite and self-sustaining claims. The recognized life-feedback path is self-sustaining under its stated conditions and ends at a terminal state. The gate still checks its infinity flag separately. [Wizards’ ruling on the life-feedback chain](https://magic.wizards.com/en/news/feature/wilds-of-eldraine-release-notes)

The full suite passed **452 tests**. The focused Phase 2, Phase 3, and mechanic suites passed 173 tests. A separate run of the existing capability, relationship, strategy, candidate, and pipeline suites passed 97 tests. The focused runs overlap with the full suite.

```powershell
.venv/Scripts/python.exe -m pytest -q --tb=short
.venv/Scripts/python.exe -m pytest -q -s tests/test_phase23_golden.py -k "golden_release_gate or isolated_phase3"
```

The golden relation, package, and loop expectations in `scenarios.json` were preserved. The card records now also pin printed colors from the same hashed Oracle snapshot. The Big Score harness checks evidence-backed qualification using `role_profiles`. The former assertion read `roles`, which is the threshold-dependent admission result. Oracle evidence can establish that a card has both roles independently of Phase 1 admission policy. A focused regression checks both strategy routes, the single owned identity, and the additional cost that both routes require. Admission thresholds were preserved.

## P0 root causes and fixes

| Blocker | Cause and fix | Regression |
| --- | --- | --- |
| Big Score crash | The cast-cost branch supplied `costs` as an explicit keyword and expanded the matcher's `costs` entry in the same call. Cast-level additional costs now have their own field on spell effects, including Treasure creation, and activation costs keep their existing field. Copying an existing spell does not pay those costs again. | Real Big Score, sacrifice additional costs, and Reverberate copying Big Score. [Capability compiler](../../src/mana_architect/synergy_capabilities.py) |
| Reversed relation | Matching source spans and a declared rule label did not bind an edge's direction to actual capabilities. Phase 3 now binds each event to its ordered endpoint capability, rechecks the relationship, and requires Phase 2's critical prerequisites, timing, limitations, and resource requirements. | Reversed Oracle spans, discarded extra cost, and genuine independent edges in both directions. [Package handoff](../../src/mana_architect/deck_pipeline/synergy_packages.py) |
| Blood Artist target | Trigger normalization set the observer to untargeted and dropped the target from its resolution. The compiler now keeps the resolution's target description separately from the trigger subject, and the edge carries legal-target requirements into packages. | Six affected Blood Artist scenarios and a generic targeted-versus-each-opponent control test. [Pairwise matching](../../src/mana_architect/synergy_interactions.py) |

## Recall gaps traced after the P0 fixes

The entries below cover every post-P0 false negative. The scenario IDs select the original golden regressions. Every implemented fix operates on typed capability or effect patterns across cards.

| Scenario ID | Expected and post-P0 observed | First loss and diagnosis | Fix and regression |
| --- | --- | --- | --- |
| `paid-colorless-blink` | Activated blink edge to Wall of Omens and a paid repeatable package. Both absent. | Capability extraction. Missing mechanical capability for the tapped-return variant. | Accept explicit tapped return, carry the colorless activation cost and ownership. Covered by the typed-cost regression. |
| `scales-adds-counter` | Additive counter-replacement edge and package. Both absent. | Capability extraction and rule catalog. Only multiplicative replacement was modeled. | Add typed additive counter replacements and keep the counter-type and control match. Covered by the resource-and-event regression. |
| `ayara-tap-and-black-fodder` | Conditional death edge and repeatable paid package. Both absent. | Capability extraction. The sacrifice matcher rejected explicit color. | Model the color constraint and match it against printed colors for the recurring creature, with tap timing carried by Phase 2. Covered by the typed-cost regression. |
| `priest-needs-two-distinct-creatures` | Conditional death edge and a partial package. Both absent. | Capability extraction. The sacrifice matcher rejected quantified “other creatures”. | Preserve the quantity and report unresolved fodder multiplicity at closure. The single known creature cannot cover two cost objects. Covered by the typed-cost and independent-ledger regressions. |
| `cat-oven-awaits-untap` | Food-to-return edge and conditional repeatable package. Both absent. | Phase 2 missing relationship rule, then Phase 3 package assembly. | Join typed Food to an explicit Food activation cost. Close the feedback by checking the creature can pay the same producing outlet and preserve the required tap renewal. Covered by the resource-and-event regression. |
| `blood-bond-self-sustaining-until-terminal` | Life-loss edge completing the life-gain seed, plus the terminal feedback package. The life-gain edge alone existed. | Capability extraction and missing life-loss rule, then Phase 3 package assembly. | Bind equal life-change triggers to their actual producer abilities, with a starter event and legal remaining opponent. Confirm the terminal boundary and keep `infinite=false`. Both the resource-and-event regression and a once-per-turn counterexample exercise this. |
| `woe-strider-two-functional-roles` | One candidate fills generator and outlet roles using distinct abilities and a distinct token. Only the death edge was present. | Phase 3 package assembly. The default fodder path required a different card identity. | Bind the same-card creation witness to the outlet's separate sacrifice effect. Count the Goat as the sacrificed object and Woe Strider once in inventory. Covered by the resource-and-event regression. |
| `kiki-conscripts-loop` | Copy-to-entry and linked untap edges, then an optional loop package. Both edges and the package remain absent. | Copy-to-entry has no relationship rule. The untap path also lacks a capability linking “that permanent” to the original trigger target. Phase 3 has no copy–untap closure template. | **Deferred.** Add typed copy-to-ETB and same-target untap joins, then verify the optional activation and temporary-token lifecycle. This generalizes to other copy/untap enablers. The golden and isolated regressions retain the miss. |
| `mana-positive-ashnod-loop` | Closed pathway with surplus colorless mana. The death edge is present, but the positive-loop package remains absent even with isolated seeds. | Phase 3 package assembly. The current sacrifice template records paid return and has no proof that both mana outputs fund it. | **Deferred.** Add a colored resource ledger for mana ability, death trigger, Treasure payment, then recursion. It generalizes to resource-producing outlets. The golden, isolated, and independent-ledger regressions remain active. |
| `mana-neutral-phyrexian-loop` | Closed pathway with zero net mana. The death edge is present, but the neutral-loop package remains absent even with isolated seeds. | Phase 3 package assembly. The current path cannot prove that return cost is paid and the starting state restored. | **Deferred.** Apply the same ledger with color-choice and exact state restoration. The golden, isolated, and independent-ledger regressions remain active. |

Invigorating Surge now retains separate placement and doubling event IDs. Paid Ballista and Gisa activations carry renewable requirements, and rebound carries a single optional second cast. These fixes address classification and evidence checks independently of identity recall.

Production changes are limited to the [capability compiler](../../src/mana_architect/synergy_capabilities.py), [interaction rules](../../src/mana_architect/synergy_interactions.py), [strategy evidence](../../src/mana_architect/strategy_synergy.py), [inventory adapter](../../src/mana_architect/deck_pipeline/inventory.py), [pairwise analyzer](../../src/mana_architect/deck_pipeline/pairwise_synergy.py), [package templates](../../src/mana_architect/deck_pipeline/package_templates.py), and [package builder](../../src/mana_architect/deck_pipeline/synergy_packages.py). The architecture and shared artifact contracts were preserved.

The regression suite additions include [capability extraction](../../tests/test_synergy_capabilities.py), [direction and loop safety](../../tests/test_synergy_packages.py), and [golden end-to-end and isolated Phase 3 tests](../../tests/test_phase23_golden.py). The analyzer still uses a static lineup. Opponent board state, general loop solving, and the remaining unmatched mechanics bound the scope of this PASS.

## Golden Dataset

[Scenario expectations](../../tests/fixtures/phase23/scenarios.json) cover 43 cases built from [58 pinned Oracle records](../../tests/fixtures/phase23/cards.json). Each record includes the Scryfall Oracle and printing IDs, printed metadata, Oracle text, and the relevant mechanics. Each scenario specifies its card aliases, directed relation and event expectations, package members and commander anchors, resource or state assumptions, and rules rationale. An empty package list means that scenario expects no package in the stated scope. Resolved package expectations include classification, repeatability, self-sustaining status, and infinity. Partial expectations list missing roles.

Card text and legality were verified from the local Scryfall Default Cards snapshot captured on November 11, 2026. Its SHA-256 and per-printing source URLs are in the card catalog. Expectations were authored independently from the evaluation results, using that text and the rules effective November 6, 2026. The fixtures run offline. All included card records were verified. A future case with unresolved text or rules must declare `verification: "pending"` and document what needs verification. A verified case with an unsupported mechanic still counts toward recall.

The rules distinguish sacrificing from dying, copying a spell from casting it, and a permanent-spell copy entering as a token from a token creation instruction. Rules 107.5, 118.10, 302.6, 603, 608.2b, 614, 700.4, 701.10e, 701.21, 707.10, 733, and 903.5 provide the basis for the timing, ownership, cost, and loop checks. The Blood–Bond case also follows Wizards’ ruling on how the triggered chain terminates. [Wizards Comprehensive Rules, November 6, 2026](https://media.wizards.com/2026/downloads/MagicCompRules%2020261106.txt) · [Wilds of Eldraine release notes](https://magic.wizards.com/en/news/feature/wilds-of-eldraine-release-notes) · [Core Set 2021 release notes](https://magic.wizards.com/en/news/feature/core-set-2021-and-jumpstart-release-notes-2020-06-20) · [Wizards banned and restricted list](https://magic.wizards.com/en/banned-restricted-list)

## Boundary and coverage

The [pytest suite](../../tests/test_phase23_golden.py) runs the real builder, analyzer, and package builder with immutable inventory snapshots and the same rule catalog. Per-scenario locks isolate mechanical discovery from Phase 1 ranking. A separate unlocked Rampant Growth–Aesi case exercises normal admission and commander anchoring. The [architecture](ARCHITECTURE.md), [Phase 2 guide](02-pairwise-synergy-analyzer.md), and [Phase 3 guide](03-synergy-package-builder.md) define what the suite checks at the handoff.

| Layer | Coverage |
| --- | --- |
| Contracts | Pool provenance, legal owned Oracle identities versus printing IDs, evidence spans, directional endpoints, commander anchors, real edge references, prerequisite and cost propagation, functional roles, partial dependencies, and rejection of corrupted input. |
| Mechanical rules | Sacrifice/death/exile, creation/entry/cast/copy, counters and damage prevention. Exercises color and controller restrictions, ownership on return, legal targets, tap and summoning sickness, variable quantity, once-per-turn limits, and replacement effects. |
| Resource and sequence checks | Five independent step ledgers test paid recurrence, colorless mana deficiency, double consumption, neutral replenishment, and positive replenishment. They check whether each step can be paid before the next step adds resources. The ledgers express rules expectations and are external to the production analyzer. |
| Real pipeline integration | 43 golden pools, including incomplete sacrifice paths, shared blink benefit identity, a same-card generator/outlet, artifact and token strategy overlap, and the banned/off-color gate. |
| Generated properties | Fixed seeds 11, 23, 47, and 71 permute inventory, card, lock, and edge inputs across the dataset. The tests compare canonical pool, edge, and package JSON and replay one duplicate supporting edge. |
| Infinite safety | 13 negative loop cases include finite mana, missing or double-spent fodder, missing colored mana, limits on timing, and an active death replacement in the rules scenario. All 13 artifacts withheld both infinite and self-sustaining claims. |

The Rest in Peace case distinguishes a static lineup from an active board. Phase 2 can emit a structural conditional death path and forward its graveyard prerequisite. Its public API has no active-board state, so the fixture checks that prerequisite and records the blocked active-board sequence as rules evidence. The two valid Altar loops and the Kiki-Jiki loop remain positive recall cases. Their absence is not evidence of a working loop solver.

## Initial validation results

| Scored output | True positives | False positives | False negatives | Precision | Recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pairwise relations | 27 | 0 | 10 | 100% | **73.0%** |
| Materialized packages | 19 | 0 | 11 | 100% | **63.3%** |

Pairwise keys are source, target, relation and event. The fixture specifies the unordered endpoint pairs in scope so a reversed edge still counts as an error. Package keys are archetype, owned members and commander anchors. A legitimate alternate path can retain its own evidence and package ID while sharing a benefit ID. Partial opportunities, classification flags and prerequisites have separate assertions. Precision measures the scoped relation and package identities, so missing prerequisites and corrupted-artifact acceptance can block release even with 100% measured precision. The two scenarios that crash are reported separately. Their missing positive outputs remain in the recall denominators, and neither crash counts as a successful negative.

Full suite `.venv/Scripts/python.exe -m pytest -q --tb=short` produced **421 passed, 6 failed**. The six failures are two end-to-end Big Score crashes, the same two crashes on replay, acceptance of a reversed artifact, and the aggregate release gate. All 41 scenarios that reached the handoff passed the normal artifact integrity and deterministic replay tests. The dedicated corrupted-artifact test failed. Run the gate below for per-scenario misses, blocked inputs, classifications, and missing prerequisites.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_phase23_golden.py -k golden_release_gate --tb=short
```

## Initial findings and remediation proposals

| Priority | Finding | Proposed fix |
| --- | --- | --- |
| P0 | **Additional-cost extraction crashes.** Big Score reaches the discard matcher, which returns a `costs` field. The additional-cost branch also passes `costs` explicitly, causing a duplicate keyword error. Both scenarios abort before Phase 2. | Build the `costs` field once. Preserve cast-level additional costs on the resulting effects, including the Treasure creation edge, then run both regressions. [Capability extractor](../../src/mana_architect/synergy_capabilities.py#L678) |
| P0 | **Phase 3 can accept reversed evidence.** Swapping endpoints, original spans and event IDs on a genuine Parallel Lives edge passes the Phase 3 handoff and permits a reversed package. | Resolve the declared source and target capabilities against the respective endpoint events in the stored native profiles. Reject wrong capability bindings before template search. Reuse the Phase 2 validator when validating a reloaded edge requires a semantic recheck. [Package input validation](../../src/mana_architect/deck_pipeline/synergy_packages.py#L240) |
| P0 | **Blood Artist loses its target prerequisite.** Phase 2 observes the death, but the extractor labels the observer as untargeted even though its payoff targets a player. The missing requirement affects six golden cases. | Preserve separate target information from the triggered resolution and carry it into the relation’s legal-target prerequisites. Keep the death subject and the affected player distinct. [Observer extraction](../../src/mana_architect/synergy_capabilities.py#L656) |
| P1 | **Invigorating Surge conflates two counter placements.** The edge carries a span covering both instructions under one target event ID. | Extract the initial placement and the rules-defined doubling as ordered effects with distinct IDs. Retain the dependency of the second quantity on the first resolution. [Capability extractor](../../src/mana_architect/synergy_capabilities.py#L768) |
| P1 | **Phase 3 cannot close the Woe Strider path.** One owned Woe Strider supplies both the Goat and a separate outlet ability. The builder requires a distinct fodder-source card. | Validate creation and consumption of the distinct Goat as a package-level capability join. Preserve a single Woe Strider member and both role assignments. [Fodder compatibility](../../src/mana_architect/deck_pipeline/synergy_packages.py#L154) |

## Initial unsupported mechanics

| Priority | Component | Unsupported behavior and expected result |
| --- | --- | --- |
| P1 | Capability extractor and Phase 2 | **Typed sacrifices.** Ayara should join a death payoff with a black-creature and tap requirement. Priest of Forgotten Gods should yield a conditional death edge and a partial package when only one distinct fodder creature can be spent. Support quantity and controller restrictions before extending closure beyond one sacrifice. |
| P1 | Capability extractor and Phase 2 | **Eldrazi Displacer blink.** Preserve the compound colorless cost, different-creature targeting, tapped return and ownership in an edge to Wall of Omens. |
| P1 | Phase 2 rule catalog | **Additive counter replacement.** Hardened Scales should amplify Walking Ballista’s placement. |
| P1 | Phase 2 rule catalog and Phase 3 | **Food payments, life-loss triggers, and copy/untap sequences.** Witch’s Oven and Cauldron Familiar need an external untap. Exquisite Blood and Sanguine Bond renew until a terminal state. Kiki-Jiki and Zealous Conscripts can repeat an optional copy–untap sequence. Each needs explicit event and resource modeling. |
| P1 | Phase 3 | **Colored mana and closed-loop accounting.** Ashnod’s Altar with Pitiless Plunderer and Reassembling Skeleton yields surplus colorless mana. Phyrexian Altar can renew the same state with no surplus. Ashnod’s Altar alone cannot pay Skeleton’s black requirement. Keep infinity disabled until a solver can prove order, resources, and termination. |
| P2 | Phase 3 | **Renewal outside current templates.** Ballista’s paid placement, Gisa’s new mana/fodder/ready tap, and Ephemerate’s bounded rebound are real repeat paths. Add renewal evidence and explicit bounds before setting repeatability. |
| P2 | Phase 2 and future runtime reasoning | **External board state.** Retrieving a structural death or blink edge does not establish feasibility under an opponent’s or third permanent’s active replacement effect. Any future game-state API needs to take those effects as input. |

The initial validation was performed before the remediation described above.
