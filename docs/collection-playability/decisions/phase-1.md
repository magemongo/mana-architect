# Phase 1 mechanical utility and applicability

Call `CollectionInputs.analyze(oracle_id)` with the same snapshot, catalog, and context used in Phase 0. The result contains the native engine profiles and an evidence-linked playability profile. Each mechanical use records its role, timing, requirements, target coverage, and restrictions. Native records retain the detailed effect semantics, costs, zones, and quantities. The original `card(oracle_id)` method supplies the raw inputs.

Phase 1 matches native capabilities to the existing strategy preferences. It considers the strategies requested in the context, or the configured strategy catalog when none are specified. A match identifies a plausible or conditional role. For repeatable sacrifice fodder, the ability must return the creature itself from its owner's graveyard to the battlefield under that player's control. The strategy must include the existing fodder-to-outlet rule for the corresponding contextual finding. The finding lists a compatible sacrifice outlet as a prerequisite.

Applicability describes conditions on a use. Target coverage distinguishes a typed target class, extra target restrictions, effects with no target, and unknown coverage. The Interaction engine supplies target restrictions and execution timing for removal. Applicability uses qualitative values and carries forward unsupported engine coverage. The profile keeps its uncertain classification. Later phases own effect efficiency, final classification, and bulk review.

The shared capability extractor now records when a recognized graveyard return enters tapped. This changes its version to 1.4.1. Regenerate published pairwise evidence created with the previous extractor before attaching it to Phase 1 inputs.

## Release gate

**PASS.** Reassembling Skeleton has an Aristocrats fodder context with graveyard, activation payment, and outlet prerequisites. A return-to-hand variant and keyword-only text do not qualify. Plummet has an instant-speed spot interaction use with a flying-creature restriction and a legal-target requirement. Changing its mana cost preserves the applicability findings.

The regression run passed 500 tests, including the existing upstream Golden gates. It deselected the same two Streamlit AppTests that previously timed out during Windows socket setup. The existing 58 pinned cards produced 56 evaluated and two unsupported profiles. All 58 profile IDs matched on reversed card and printing order. These figures measure input coverage and determinism.

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_collection_playability_mechanics.py
.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider -k 'not test_suggestions_page_filters_by_identity_and_strategy_without_corrections and not test_builder_generates_with_selected_catalog_strategy'
```
