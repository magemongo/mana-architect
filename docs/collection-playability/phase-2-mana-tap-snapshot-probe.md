# CPA Phase 2 — Pinned snapshot probe for mana and tap draw

**Full Phase 2 release status: BLOCKED.** The implementation guide (§5.3 and §11) requires context-sensitive efficiency for effect bundles, timing, reliability, repeatability and resources, comparison within functional groups, and broader independently reviewed Golden coverage. This probe measures evidence and applicability for one activation shape in one snapshot. Efficiency, whole-card value and substitutes were outside the probe.

## Snapshot and evaluation scope

The local `data/cache/scryfall/default-cards.jsonl.gz` SHA-256 was `d190c5ed83e3487daab01a69ff5f50c1d4ee55e1e83f0b08bfe295e2b9477672`, matching `tests/fixtures/cpa_repeatable_draw.json`. The fixture records cache modification time `2026-11-11T15:00:09Z`. The probe refuses a mismatched file.

The run used HEAD `c58dc8cc2f41310d87c6d9270c5850b7e47831fc` and the pre-existing local changes from the [mana and tap implementation](phase-2-mana-tap-validation.md). The modified production files were `mechanics.py` (SHA-256 `0fb4c620e9972fad3f8340ca93544b58f931a600b4223798616af5d562d6a50d`) and `semantic_coverage.py` (SHA-256 `b41352fe7d9504e9142c6e46bdd04b1d9a0d4b11b7106a12e485bcc2ed1eb6e5`), both under `src/mana_architect/collection_playability/`. Existing local edits to three CPA test files were also present. This round added `tests/cpa_mana_tap_snapshot_probe.py`, `tests/test_collection_playability_mana_tap_probe.py` and this report.

Of 120,054 printing rows, 92 lacked an Oracle ID. The other rows deduplicated to 39,288 Oracle IDs. The probe compared all per-ID versions of CPA-relevant metadata and face eligibility, ignoring printing-specific fields. No conflicting records were found. Of 32,643 IDs marked `legal` in the snapshot's Commander legality field, 31,454 had top-level Oracle text and type line and met the single-face filter. The filter excludes records with multiple `card_faces`, `//` in the type line or ` // ` in the Oracle text. Legality comes from the pinned data and is not a current or deck-specific legality check.

The source scanner runs independently of the resolver and verifier. It scans physical lines for symbol-led activation text with a draw instruction after the colon. It also recognizes same-line starts after a period followed by horizontal space and a brace or trigger word, and excludes draw clauses followed by obvious continuation instructions. It checks exact fixed `{n}` or `{WUBRGC}` tokens, comma, one `{T}`, a period-terminated fixed draw and singular/plural agreement. The output includes exact source offsets. These lexical cut points define the search scope. The scanner does not resolve nested or arbitrarily formatted rules text. Nearby symbol-led draw clauses that fail the grammar go into the separate control set.

`CollectionInputs.analyze` ran against a synthetic collection containing one representative per selected ID. The probe checked the production resolver's spans and the returned native fact's cost, fixed quantity, timing and source evidence before reading boundary coverage or conditional use. The native facts are those returned after any normal CPA boundary-scoped extraction. A final use must cite the same evidence and unit, match its card, face, boundary and cost, and include both payment and tap requirements. Profile IDs matched for all 427 analyzed cards on repeat and reversal of card, printing and evaluation order.

## Results

| Stage | Reached / all candidate activations | Reached / previous stage |
| --- | ---: | ---: |
| Source grammar | 12 / 12 | 12 activations on 12 of 31,454 eligible Oracle IDs |
| Complete resolved boundary | 11 / 12 | 11 / 12 |
| Exact native draw and evidence | 11 / 12 | 11 / 11 |
| Verified boundary coverage | 11 / 12 | 11 / 11 |
| Conditional CPA use with cost and tap | 5 / 12 | 5 / 11 |

| Card | Oracle ID | Exact cost | Draw | Observed outcome and first block |
| --- | --- | --- | ---: | --- |
| League Guildmage | `08bb1f33-b232-4382-a268-4804d4e72d29` | `{3}{U}, {T}` | 1 | Verified locally. Conditional use blocked by `card_dependency_unresolved` for the separate spell-copy activation. |
| Benalish Heralds | `09ee1332-741f-4c55-abc3-8bcff9031cd9` | `{3}{U}, {T}` | 1 | Conditional. |
| Jayemdae Tome | `39ee576a-0803-4063-9c84-f2b537e4d44c` | `{4}, {T}` | 1 | Conditional. Pinned positive control. |
| Mercurial Chemister | `4243baf6-9ac2-4f87-988d-3ebf5e177f3b` | `{U}, {T}` | 2 | Verified locally. Conditional use blocked by `card_dependency_unresolved` for the separate discard-and-damage activation. |
| The Great Mound | `6b78e417-44f8-4ab6-9d3c-e71704fc648e` | `{6}, {T}` | 1 | Verified locally. Conditional use blocked by a `casting_restriction` dependency attributed to the separate token-making ability and its reminder text. |
| Staff of Domination | `d7888719-647d-4022-a211-822fa09f0791` | `{5}, {T}` | 1 | Verified locally. Conditional use blocked by `card_dependency_unresolved` for four other activations, including self-untap. |
| Aether Syphon | `d8956775-fda8-475d-9e8a-b61196a958ea` | `{2}, {T}` | 1 | Boundary gap. Oracle `[145:167]` is the draw activation, but the resolver returns `[145:314]` including the following `Max speed` draw trigger. |
| Arcane Encyclopedia | `d91ea728-2d69-42cb-bb5d-e2b058f0d7b1` | `{3}, {T}` | 1 | Conditional. |
| Endbringer | `db1ea6e0-4076-49dd-92f7-7e6d7eb65a27` | `{C}{C}, {T}` | 1 | Verified locally. Conditional use blocked by `card_dependency_unresolved`, including `timing_or_frequency` for its untap text. |
| Tower of Fortunes | `dfb23df9-f912-4daa-9842-01bd12b4a72a` | `{8}, {T}` | 4 | Conditional. |
| Scepter of Insight | `e2725411-a600-4ff3-94fb-5d52f5eda414` | `{3}{U}, {T}` | 1 | Conditional. |
| Snapping Voidcraw | `e495033b-f727-41e3-b7fc-7f5fa8abdd87` | `{3}{C}, {T}` | 1 | Verified locally. Conditional use blocked by `card_dependency_unresolved` for Devoid. |

The main drop is the card-level dependency gate. Staff of Domination and Endbringer have printed untap effects relevant to later activations, so their abstentions are conservative and preserve a material uncertainty. The other four dependency stops are plausible coverage gaps for the draw use. The separate copy and damage abilities share the source's tap availability but do not directly change the printed draw instructions. The Great Mound's restriction is in token reminder text, and Devoid changes color rather than the printed draw. These are hypotheses about the scope of the dependency gate, not exemptions established by the probe. For Aether Syphon the incomplete boundary is a separate gap. Splitting it would still leave a draw-linked trigger to assess.

The control set comprised 421 clauses across 415 Oracle IDs. None received verified coverage under the mana-and-tap rule. The five pinned negative controls all retained unsupported draw uses. Greed pays life, Mind Stone sacrifices the artifact, Tome of Legends removes a page counter, Arch of Orazca requires the city's blessing, and Jalum Tome discards during resolution. Six other control clauses had conditional draw uses through existing rules outside the probed grammar. Those outcomes were kept separate from the new rule's numerator. The probe reports snapshot coverage only.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -B tests/cpa_mana_tap_snapshot_probe.py data/cache/scryfall/default-cards.jsonl.gz
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
```

The probe emits JSON with all candidate and control records, stage diagnostics, inventory counts and conflicts. The focused CPA and boundary regression run had **135 passed, 0 failed, 0 skipped, 0 deselected**. The two Streamlit AppTests were outside this run and remain unverified. Their last recorded individual attempts timed out. See the [repeatable draw review](phase-2-repeatable-draw-validation.md).

Recommended next action. Add a focused diagnostic test for League Guildmage that isolates how its separate spell-copy ability triggers `card_dependency_unresolved` on the locally verified draw, with the current abstention preserved.
