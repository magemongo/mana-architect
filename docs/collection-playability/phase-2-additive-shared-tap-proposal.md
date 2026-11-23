# CPA Phase 2 — Additive shared tap evidence proposal

**Full Phase 2 release status — BLOCKED.** Sections 5.3 and 11 of the implementation guide still require peer-relative effect costs, contextual valuation of combined effects, timing, reliability, repeatability, resource constraints and transparent explanations. The release gate must detect Thassa's Bounty-like inefficiency while preserving the value of expensive high-impact effects. This proposal covers evidence for one printed prerequisite.

## Recommendation

Add an optional `PlayabilityProfile.shared_tap_prerequisites` field containing typed, symmetric groups of exact source costs. In this increment a group covers one permanent and face and includes at least one locally verified draw activation. Include additional resolved activations on that face when their printed cost meets the same source-binding criterion, even if their effects remain unverified. One group with three costs encodes three pairwise resource conflicts without duplicating citations.

An explicitly nonblocking `CardDependency` would reuse the existing collection, but that record has a directed source and one source span. It also carries a status that current consumers interpret as a block. `tests/test_cpa_adversarial_golden.py` builds its affected set from every dependency with status other than `independent`. Dependency tests use the same predicate to select blocking records, and the snapshot probe uses it to report why an affected use stopped. Adding a category or a `blocking` flag would require migrating all those readers, including external readers using the same predicate. Using `independent` for a real resource interaction would overload that status. The separate field keeps the blocking vocabulary and all those readers intact. This supersedes the representation option in the earlier criterion. [Shared tap acceptance criterion](phase-2-shared-tap-dependency-criterion.md)

`assess_dependencies` continues to return the existing records and affected-boundary set. The mechanics adapter derives the annotation independently from the resolved boundaries and their coverage. The annotation never enters the affected set, changes a coverage verdict, relaxes any gate, or supplies strategy or efficiency evidence. Include it on both profile return paths for an analyzed card. Existing consumers can continue reading `card_dependencies` for modifiers and applicability decisions.

## Evidence contract

Use one `SharedTapPrerequisite` value per card and face, with `card_id`, `face_index` and a tuple of `TapCostCitation` values. Each citation contains `boundary_id`, `start`, `end` and `text`. Offsets are zero-based, end-exclusive indices into the original Oracle text. `text` is the complete original cost up to the colon with its original punctuation and spacing. For example, the synthetic pair cites `[0:11]` as `{3}{U}, {T}` and `[26:34]` as `{2}, {T}`. The boundary IDs point to the existing `BoundaryCoverage` records, which already carry the exact surrounding text and any native evidence references. No new native evidence IDs or duplicate draw facts are needed.

An endpoint must be a distinct, resolved top-level activated boundary on the same known permanent face. The full pre-colon cost must match a contiguous run of brace tokens for ASCII generic numerals or `WUBRGCX`, followed by a comma, optional horizontal whitespace and exactly one `{T}`. Compare the exact cost to the resolver's `activation_cost` and to the original Oracle slice at emission. `{X}` is eligible for this printed tap annotation because it leaves the tap payment mandatory. Its mana amount and the copied effect remain unresolved. The first increment uses the existing single-face scope and this explicit cost grammar. Quoted or granted abilities, ambiguous spans, optional or alternate payments, `{T}` in the effect, payments that tap another object, and unmodeled cost forms cannot establish an endpoint. Any uncertainty about binding the symbol to the same source excludes that endpoint.

The profile contract validates that every citation resolves to a distinct boundary on the group's card and face and matches the boundary's exact pre-colon cost and offsets. Sort citations by `(start, end, boundary_id)` and groups by `(card_id, face_index)`. Deduplicate identical citations by boundary ID and identical groups by card and face. Reject conflicting duplicate citations or groups and overlapping cost spans. A group requires at least two remaining citations. Repeated analysis or reversed input traversal must lead to identical output.

This records that paying either printed `{T}` consumes the untapped state of the same source, so another tap payment requires that source to be able to tap again. Priority, permissions, future untaps, quantities of variable mana, activation frequency, opportunity cost and actual efficiency require other evidence.

## Serialization and rollout

Add the field at the end of the profile dataclass with default `()`, preserving existing construction calls. `to_json()` includes the field only when populated. That leaves the serialized payload and `profile_id` unchanged for profiles without shared tap evidence. Populated profiles acquire a new content-derived ID because `profile_id` hashes `to_json()`. Boundary, unit, evidence and dependency IDs retain their current derivations. Consumers of `to_json()` should accept a missing field as absent evidence, and consumers with strict key validation need to accept the optional field before using populated profiles. Direct `dataclasses.asdict()` includes the dataclass field even when empty, so the byte-for-byte compatibility claim applies to `to_json()` only. Keep the shared deck artifact schema version unchanged for this optional CPA extension. Recompute externally persisted profiles if the new annotation is needed. The repository search found no CPA profile persistence or deserialization path to migrate.

## Acceptance cases

| Case | Annotation | Existing decisions to preserve |
| --- | --- | --- |
| Offline synthetic pair | One group with both exact costs, offsets, boundary IDs, card and face. | Two independently verified draw units, quantities one and two, conditional use and retained cost, tap and native evidence associations. |
| Same pair plus `Players can't draw cards.` | Same group and citations. | The `draw_restriction` still cites the complete static clause and blocks both uses with `card_dependency_unresolved`. Local verification stays intact. |
| Intact League Guildmage | One group if both costs pass source binding. Cite the printed draw and the `{X}{R}, {T}` copy cost at `[26:37]`. | The copy effect remains unresolved, its existing dependency still blocks draw, and the annotation provides no strategy or efficiency evidence. The draw-only control has no group. |
| Negative controls | Remove the second `{T}`, then separately put it inside quoted granted-ability text. No group in either case. | Each otherwise eligible draw retains its baseline treatment. Effects that mention tapping do not qualify as costs. |
| Integrity and replay | Reject wrong card or face, wrong boundary, altered spans and conflicting duplicate records. Reversed citations and repeated runs yield equivalent serialization and IDs. | Empty field preserves legacy wire shape. Adding evidence changes only the new field and profile ID when comparing the same analyzed input. |

See the [offline acceptance baseline](phase-2-shared-tap-acceptance-validation.md) and [League Guildmage diagnostic](phase-2-league-guildmage-dependency.md).

## Files and validation after approval

- `src/mana_architect/collection_playability/contracts.py` for the two evidence value types, profile validation and optional serialization.
- `src/mana_architect/collection_playability/shared_tap.py` for the pure extraction helper that checks resolved boundaries and exact cost slices. `src/mana_architect/collection_playability/mechanics.py` will attach the result to the profile.
- `tests/test_collection_playability_dependencies.py` for the synthetic pair, restriction, Guildmage and negative cases. `tests/test_collection_playability_contracts.py` for span integrity, ordering and serialization. The existing adversarial Golden expectations stay intact.

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_collection_playability_dependencies.py tests/test_collection_playability_contracts.py tests/test_cpa_adversarial_golden.py
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
```

The second command group passed **177 tests** in the proposal round. The first command passed **73 tests** in the pre-implementation review below. The proposed cases are for the implementation review. The two Streamlit AppTests were outside these selections and remain unverified.

## Pre-implementation baseline

Captured on `c58dc8cc2f41310d87c6d9270c5850b7e47831fc` with the existing local CPA changes and before adding any shared tap field. Use the synthetic card and collection helper from `test_synthetic_shared_tap_draws_keep_local_evidence_under_draw_restriction`. The draw-only input keeps that card's metadata and ID and uses only its first Oracle line.

| Analyzed input | Legacy `profile_id` and canonical JSON SHA-256 |
| --- | --- |
| Draw only | `3ad18070b3aada97f6a3199c8cb682cce055c43b7ca5d5cf42d84e6b65524f46` |
| Synthetic pair | `ca864ccd897ddab0fd93f52d05b92fbfa182174316a027803233ab9bf713353a` |
| Synthetic pair with static restriction | `df70adf9eae1d91e8dd384c644c3cc385e8e255d8839b6725a20eba5583c6975` |

The two columns represented by each digest were measured independently. Hash the UTF-8 bytes of `json.dumps(profile.to_json(), sort_keys=True, separators=(',', ':'), ensure_ascii=False)`. After implementation, draw-only JSON must omit the field and match both legacy digests. For each populated pair, verify the new field and compare the JSON with only that key removed to the corresponding legacy digest. The full payload must produce a deterministic new ID under replay and reordered inputs.

After the focused runs, execute the applicable broad suite and report the Streamlit AppTests separately. Produce `docs/collection-playability/phase-2-additive-shared-tap-validation.md` with the emitted evidence, blocker parity, compatibility checks and exact test outcomes.

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'
```
