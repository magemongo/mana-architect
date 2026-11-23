# CPA Phase 2 — Benalish Heralds creature draw eligibility

**Full Phase 2 release status — BLOCKED.** The guide (§5.3 and §11) still requires contextual evaluation of effects and costs against functional peers, including combined effects, timing, reliability, repeatability and other resources. This diagnostic establishes a printed creature activation with local mechanical evidence. The searched scope supplies no credible second creature for an efficiency comparison. [Per-activation peer audit](phase-2-per-activation-draw-peer-audit.md)

## Pinned facts and observed result

`tests/fixtures/cpa_benalish_heralds.json` pins Oracle ID `09ee1332-741f-4c55-abc3-8bcff9031cd9` from the local Scryfall `default-cards.jsonl.gz` snapshot. The measured SHA-256 was `d190c5ed83e3487daab01a69ff5f50c1d4ee55e1e83f0b08bfe295e2b9477672`. Before running the test, the pinned record establishes `Creature — Human Soldier`, Commander legality, white-blue color identity, white printed color and casting cost `{3}{W}`. Its complete Oracle text is `{3}{U}, {T}: Draw a card.`. That text establishes a printed payment of three generic mana, one blue mana and tapping the source, for one untargeted draw. The casting cost belongs to deployment and would not enter a per-activation delta.

`CollectionInputs.analyze` returned verified face-0 coverage at `[0:25]`, with the activation cost at `[0:11]` and the draw instruction at `[13:25]`. All offsets are zero-based and end-exclusive. The fixed native draw fact and its profile reference cite `Draw a card` at `[13:24]`. The use, native evidence and verified boundary agree on the cost and link to the same activated execution unit. The draw is conditional, with `legal_activation_timing`, `meet_creature_tap_restriction_if_applicable`, `pay_ability_cost` and `tap_source`. No unresolved card dependency was recorded. The profile also contains `creature_presence` from printed type, separate from the draw's execution unit.

The current artifact model rejects the printed creature type before comparison. The profile's effect-efficiency coverage is unsupported and it emits no efficiency finding. A synthetic control with identical Oracle and payment data, a synthetic identity and the type changed to `Artifact` enters the existing model and returns `unknown` without an owned peer. This isolates the printed type exclusion through the normal entry point. A future creature gate would also have to distinguish creature presence from the single Oracle activation, since the current artifact gate requires one mechanical use. The existing real artifact control still finds the one-mana activation difference between Jayemdae Tome and Arcane Encyclopedia. [Artifact model proposal](phase-2-per-activation-draw-efficiency-proposal.md)

## Evidence a creature comparison would require

| Condition | Current printed/profile evidence | State a future comparison must assume explicitly or verify |
| --- | --- | --- |
| Source, controller and control history | Creature type, owner-independent Oracle identity and `meet_creature_tap_restriction_if_applicable`. | The source is a creature on the battlefield controlled by the activating player. The player has controlled it continuously since the start of their most recent turn, or haste applies at activation. Ownership alone cannot establish control history. |
| Haste | The pinned Oracle text grants no haste. The creature tap condition remains on the use. | Verify actual haste from the current game state if continuous control is absent. |
| Timing and permission | `activated`, `legal_activation_timing` and no printed additional timing restriction. | Verify priority and the absence of a prohibition or a relevant restriction at the chosen activation window. |
| Tap and payment | `tap_source`, `pay_ability_cost` and the exact `{3}{U}, {T}` cost. | Verify that the source is untapped and can pay the tap cost and that the mana payment is available. |
| Further activation | The printed cost recurs with each activation. | Recheck control, permission, priority, mana and the ability to tap again each time. Untap timing and effects, prior tapping and resource availability are game-state facts. The profile supplies no activation count or guaranteed repeatability. |

The rules basis is the same November 6, 2026 edition named in the mana-and-tap contract. Rule 107.5 governs the tap payment, 302.6 and 602.5a govern creature control history, 702.10c supplies the haste exception, and 117.1b and 602.1–602.5 cover priority, payment and activation restrictions. The PDF is the same dated edition as the contract's TXT source. [Mana-and-tap contract](mana-tap-activation-contract.md) · [Wizards Comprehensive Rules, November 6, 2026](https://media.wizards.com/2026/downloads/MagicCompRules%2020261106.pdf)

## Peer search and decision

The read-only search reused the checksum-verified probe scope, with 427 selected Oracle IDs, 12 candidate activations, and 421 near-match clauses across 415 control IDs. A source-only scan followed by mechanical inspection found five creature candidates among the 12 matches. The table reads cost, quantity and color identity from the pinned records and prerequisite and dependency facts from the mechanical profiles. It does not use `cpa-activation-draw-efficiency-v1` to select creature peers. All five draw uses record activated timing and the four prerequisites listed for Heralds. The other four uses also carry `card_dependency_unresolved`.

| Creature | Printed activation | Draw | Identity | Eligibility for a matched pair |
| --- | --- | ---: | --- | --- |
| Benalish Heralds | `{3}{U}, {T}` | 1 | WU | Conditional anchor with no unresolved dependency. |
| League Guildmage | `{3}{U}, {T}` | 1 | UR | Matching quantity and fixed `{U}`, different identity and an unresolved copy dependency. |
| Mercurial Chemister | `{U}, {T}` | 2 | UR | Different quantity and identity, plus the unresolved discard-and-damage activation. |
| Endbringer | `{C}{C}, {T}` | 1 | Empty | Different fixed mana and identity, plus unresolved abilities including untap timing. |
| Snapping Voidcraw | `{3}{C}, {T}` | 1 | GU | Different fixed mana and identity, with Devoid unresolved by the dependency gate. |

The controls included 201 clauses on 197 creature IDs. The four white-blue creature control clauses also fail the source grammar. Niambi, Esteemed Speaker adds discard and draws two. Arcade Gannon and Senate Guildmage draw and then discard, and Arcade Gannon also adds a counter. Sphinx of the Revelation uses variable energy and draw. No second creature in the searched cohort matches Heralds' fixed one-card draw, color identity, fixed blue payment and verified conditional prerequisites. The result is limited to the existing lexical probe and its single-face Commander-legal cohort. It does not establish availability across every card or collection. **Defer creature comparison implementation** until a credible pinned pair and an explicit treatment of creature activation conditions are available.

## Validation

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_collection_playability_benalish_heralds.py tests/test_collection_playability_activation_efficiency.py::test_pinned_books_compare_one_activation_with_exact_endpoint_evidence
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
.\.venv\Scripts\python.exe -B tests/cpa_mana_tap_snapshot_probe.py data/cache/scryfall/default-cards.jsonl.gz
```

The diagnostic and pinned artifact control passed **2 tests**. The focused CPA, boundary and adversarial regression selection passed **198 tests**, with zero failures or skips. The full snapshot probe reproduced 12 source matches, 11 verified activations, 5 conditional uses, 4 artifact efficiency subjects and 1 cheaper-peer finding. It reported `printed_type_outside_artifact_scope` for Heralds. This round added only the offline fixture, focused test and report. The Streamlit AppTests `test_suggestions_page_filters_by_identity_and_strategy_without_corrections` and `test_builder_generates_with_selected_catalog_strategy` were outside the selection and remain unverified after previously recorded individual timeouts.

Recommended next action. Conduct a read-only Oracle search beyond the existing probe cohort for a second creature with a fixed one-card draw, white-blue identity and a fixed blue mana-and-tap payment that can be pinned as a compatible peer.
