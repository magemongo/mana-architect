# Catalog-Guided Deck Generation

> **Status: Complete.** This document records requirements and acceptance
> criteria for integrating the strategy catalog with automatic deck generation.
> It documents the delivered work, not the future roadmap; see
> [project-status.md](./project-status.md).

## Goal

Allow automatic generation to use an explicitly selected catalog strategy to
prioritize cards from the collection while preserving deck structure, Commander
constraints, and current behavior when no catalog strategy is selected.

This integration connects the catalog to the generator. Editing a generated
decklist manually from Suggestions remains future work.

## Starting Point

- `build_deck` filters collection cards by legality and color identity, selects
  lands to cover colors, then completes nonlands while balancing roles and mana
  curve.
- Legacy strategic priority comes from `strategy_profiles.toml`; the selection
  score also considers roles and curve.
- The catalog scorer is used by Suggestions to score a card for a commander and
  strategy. It does not evaluate overall deck quality or composition by itself.

## Expected Experience

1. In Deck Builder, choose a commander and optionally select **Catalog
   Strategy** mode.
2. Choose an active strategy. The UI confirms that an affinity exists for the
   commander/strategy pair.
3. The generator uses the catalog to prioritize candidates while still
   producing a complete list according to land, role, and curve targets.
4. The result identifies the strategy used and passes the existing validation
   before export is allowed.
5. If no catalog strategy is selected, legacy behavior remains in use. The app
   must not silently fall back when catalog mode was requested but required
   data or configuration is missing.

The flow is available in the Streamlit app and through an explicit strategy
option on the `build-deck` CLI command. Omitting the option preserves current
CLI behavior.

## Integration Rules

- **Hard filters before ranking:** collection availability, known and valid
  Commander legality, compatible color identity, and copy limits remain
  constraints. A score cannot make an ineligible card eligible.
- **Explicit affinity:** the chosen strategy must have a commander affinity. If
  not, explain how to add it; do not substitute an implicit profile or score.
- **Traceable classifications:** use current catalog classifications and
  manual overrides without erasing automatic evidence. Generation must not
  persist reclassification automatically.
- **Missing or stale classifications:** report the affected count and direct
  the user to explicit reclassification. Do not treat unscored cards as score
  zero or silently switch to legacy mode.
- **Structural composition:** combine strategy score with role and curve
  balance. Normalize using the configured range and cap its contribution at
  `0.25`. Do not simply select the highest-scoring 99 cards; scorer role
  coverage is disabled here because the generator updates roles during
  selection.
- **Mana base:** preserve color coverage, utility-land limits, inventory, and
  existing rules. Strategy score can break ties with the same structural
  result, but cannot outweigh color coverage or multicolor-source priority.
- **Determinism and explanation:** given the same cards, configuration,
  commander, strategy, and affinity, selection and tie-breaking are repeatable.
  The UI and exported files identify the strategy. Scores are heuristics, not a
  competitive-quality guarantee.
- **Final validation:** validate the generated list with the current validator.
  Do not export drafts with known deterministic violations; clearly report
  incomplete validation caused by missing data.

## Scope

### Included

- Explicit choice between legacy generation and catalog-guided generation,
  with legacy mode as the default.
- Strategy selection in Deck Builder and the matching CLI option.
- Loading affinity, current classifications, and overrides to score eligible
  candidates.
- Deterministic score integration with nonland selection while maintaining
  role and curve balance.
- Integration under existing land-selection constraints.
- Display of the chosen strategy, affinity/classification notices, and final
  validation.
- Unit and integration tests for both generation modes.

### Out of Scope

- Manually adding or replacing cards in Suggestions.
- Persisting decks or managing multiple decklists.
- Changing the collection, legality rules, strategy catalog, or scorer formula
  without demonstrated need.
- Calibrating weights with external data, evaluating competitive power level,
  or using AI/remote services.
- Partner commander support.

## Acceptance Criteria

- [x] Without a selected strategy, current generation behavior and filters are
      preserved.
- [x] Catalog mode explicitly uses the selected strategy and shows it in the
      result.
- [x] Guided generation gives an actionable error when the commander/strategy
      affinity is missing.
- [x] Missing or stale classifications are reported; generation neither
      persists them nor silently changes modes.
- [x] Normalized score contribution is capped at `0.25` and combined with role
      and curve balance without replacing structural selection.
- [x] Legality, color identity, availability, singleton rules, and land color
      coverage remain enforced.
- [x] The generated draft is validated before download.
- [x] Repeating generation with identical data and configuration produces the
      same decklist and tie-break order.
- [x] Tests show catalog mode changes prioritization in a controlled case
      without weakening hard constraints.
- [x] Omitting the CLI option and choosing legacy mode in the UI preserves
      previous behavior.

## Completion Evidence

- Implementation: `src/mana_architect/deckbuilding.py`,
  `src/mana_architect/cli.py`, and `src/mana_architect/streamlit_app.py`.
- Integration tests: `tests/test_deckbuilding.py`, `tests/test_cli.py`, and
  `tests/test_streamlit_app.py`.
- Full suite at revision `5533668`: `python -m pytest -q` — **131 passed**.
- Manual Streamlit exercise with a temporary database: Tokens strategy,
  **100/100** cards, 98 lands, and validation passed. The test database was
  removed afterward.
