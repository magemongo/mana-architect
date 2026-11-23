# CPA Phase 2 — League Guildmage dependency diagnostic

**Full Phase 2 release status — BLOCKED.** The implementation guide still requires an effect-cost model relative to functional peers, valuation of combined effects, accounting for timing, reliability and resources, and transparent explanations. The release gate must detect Thassa's Bounty-like inefficiency while preserving high-impact uses of expensive effects. The broader independently reviewed Golden set is also pending. This diagnostic establishes the cause of one mechanical abstention.

The regression uses League Guildmage (`08bb1f33-b232-4382-a268-4804d4e72d29`) from the local Scryfall `default-cards.jsonl.gz` snapshot. Its SHA-256 was verified as `d190c5ed83e3487daab01a69ff5f50c1d4ee55e1e83f0b08bfe295e2b9477672`, matching the [snapshot probe](phase-2-mana-tap-snapshot-probe.md). `tests/fixtures/cpa_league_guildmage.json` preserves the single card's Oracle text and CPA metadata. The regular test reads this fixture offline. This round adds one regression in `tests/test_collection_playability_dependencies.py`, the fixture and this report. The diagnostic ran on `c58dc8c` with the existing local mana-and-tap and probe changes.

## Observed path

Offsets are zero-based and end-exclusive. `CollectionInputs.analyze` resolves two activated boundaries on the same face. The draw boundary `[0:25]` has verified coverage, the exact `{3}{U}, {T}` cost, and a native activated draw of one card. Its `Draw a card` evidence at `[13:24]` belongs to the draw boundary's execution unit and remains attached to the mechanical use.

The spell-copy boundary `[26:147]` is resolved structurally and lacks verified semantic coverage. The dependency evaluator visits that boundary and finds no specific modifier pattern. Its fallback records `unmodeled_static_or_keyword`, status `unknown`, timing `conditional`, face relationship `same_face` and reason `card_dependency_unresolved`. The record cites the entire spell-copy span and identifies the draw boundary as affected. The mechanics adapter retains the draw evidence and cost prerequisites, sets applicability to `unknown` and returns an `unsupported` use. The draw evidence supplies no positive strategy context and the profile emits no efficiency findings. The path follows the documented [dependency policy](decisions/card-dependencies.md).

The clearly synthetic draw-only control removes the second Oracle ability and uses a synthetic card identity. The draw becomes conditional and retains `pay_ability_cost` and `tap_source`. This isolates the spell-copy boundary as the cause of the current downgrade on the intact record.

Both printed costs contain `{T}`, so paying either cost taps the same source. The source must be able to tap again before paying the other cost. The copy text states an effect on a target spell and contains no explicit instruction changing how many cards the other activation draws or what it costs. The evaluator reaches the generic unknown-scope fallback and provides no proof about direct modification. The synthetic result isolates that fallback and does not establish independence in a game state.

## Validation

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_collection_playability_dependencies.py -k league_guildmage
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
.\.venv\Scripts\python.exe -B tests/cpa_mana_tap_snapshot_probe.py data/cache/scryfall/default-cards.jsonl.gz
```

The isolated regression passed (1 passed, 19 deselected). The focused CPA, adversarial and boundary suites passed (176 passed, 0 failed, 0 skipped, 0 deselected). The snapshot probe reproduced 12 source matches, 11 resolved, evidence-matched and verified boundaries, and 5 conditional uses. League Guildmage still stopped at the final gate.

The Streamlit AppTests `test_suggestions_page_filters_by_identity_and_strategy_without_corrections` and `test_builder_generates_with_selected_catalog_strategy` were outside this run. Both remain unverified after the previous recorded individual timeouts.

Recommended next action. Propose a narrow dependency acceptance criterion that distinguishes League Guildmage's shared tap prerequisite from a direct draw modifier, using this regression as the baseline for a separately approved review.
