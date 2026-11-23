# CPA Phase 2 — Additive shared tap validation

**Full Phase 2 release status — BLOCKED.** This increment records a printed resource prerequisite. Sections 5.3 and 11 of the implementation guide still require a peer-relative effect-cost model, contextual valuation of combined effects, timing, reliability, repeatability and resource constraints, and transparent explanations. The release gate still requires detecting Thassa's Bounty-like inefficiency while preserving valuable expensive effects.

The profile now exposes an optional `shared_tap_prerequisites` group for a supported single-face permanent with at least two resolved tap costs and a locally verified draw. Each citation includes the boundary ID, exact pre-colon text and offsets into the original Oracle snapshot. Full cost matching accepts `{X}` in the mana component as evidence of a mandatory printed tap payment. Its amount and the effects of a copied spell remain unresolved. Endpoints with uncertain source binding or unmodeled local activation instructions are excluded. The group describes competition for the source's untapped state and carries no inference about activation frequency, game-state feasibility, efficiency or strategy. [Approved proposal and legacy digests](phase-2-additive-shared-tap-proposal.md)

The synthetic pair emits one group with the costs `{3}{U}, {T}` and `{2}, {T}`. Each activation retains its own quantity, evidence, execution unit and conditional applicability. With `Players can't draw cards.` appended, the exact same group survives and `draw_restriction` still blocks both uses. League Guildmage cites `{3}{U}, {T}` at `[0:11]` and `{X}{R}, {T}` at `[26:37]`, while `card_dependency_unresolved` still blocks the draw. Its draw-only control emits no group. Negative tests cover missing payments, tap instructions in effects, a different object as payment, quoted and multiline-granted abilities, and alternate payment instructions. The contract rejects incorrect card or face, missing or conflicting boundaries or citations, nonmatching source spans, overlapping costs, conflicting groups and groups without a verified draw. Replay and reversed traversal preserve the annotation and profile identity.

The draw-only profile omits the new field and matches its pre-implementation JSON digest and ID. For both populated synthetic cases, the JSON with just the field removed matches the captured pre-implementation digest. Each full payload produces a deterministic new ID. The adapter regression now accounts for the documented distinction between `dataclasses.asdict()`, which includes the empty dataclass field, and `to_json()`, which omits it. The shared artifact schema version remains unchanged. External consumers that strictly validate JSON keys must allow the optional field before consuming annotated profiles. Consumers can recompute stored profiles to obtain the annotation.

Production edits are in `contracts.py`, `shared_tap.py` and `mechanics.py` under `src/mana_architect/collection_playability/`. Test edits are in `tests/test_collection_playability_contracts.py`, `tests/test_collection_playability_dependencies.py` and `tests/test_collection_playability_adapters.py`. The pre-existing local work and adversarial Golden expectations were preserved.

## Validation

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_collection_playability_contracts.py tests/test_collection_playability_dependencies.py tests/test_cpa_adversarial_golden.py
$cpaTests = rg --files tests -g 'test_collection_playability*.py' -g 'test_ability_boundaries.py' -g 'test_cpa_adversarial_golden.py'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider $cpaTests
.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'
```

The contract, dependency and adversarial Golden selection passed **80** tests. The focused CPA and boundary selection passed **184**. The applicable broad suite passed **664**, with **2 deselected**. The final runs had no failures or skips. The excluded Streamlit AppTests `test_suggestions_page_filters_by_identity_and_strategy_without_corrections` and `test_builder_generates_with_selected_catalog_strategy` remain unverified after their previously reported individual timeouts.

Recommended next action. Extend the existing pinned-snapshot probe to count shared tap groups and check that each annotated real card retains its prior applicability and blocking decisions.
