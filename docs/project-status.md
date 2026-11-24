# Project Scope

Mana Architect is a local tool for managing a physical Magic: The Gathering
collection and drafting, evaluating, and validating Commander decks from cards
the player owns. The collection establishes ownership and quantity. Scryfall
supplies card data and a dated legality snapshot.

Deck selection and evaluation are deterministic. Strategy and role scores
explain why cards rank where they do. The validator checks the deck separately
against known rules and reports missing data as incomplete validation.

## Current workflows

- Import ManaBox CSV, sync Scryfall data, search the collection, and choose an
  owned commander.
- Rank owned, legal candidates by commander abilities, weighted strategies,
  color identity, or a core role.
- Generate a draft, review its mana curve and roles, validate the list, and
  export JSON or plain text.
- Manage the strategy and role catalog, inspect Oracle parser evidence, and
  explicitly refresh saved classifications.

The UI and legacy `build-deck` workflow support one commander. The staged
pipeline CLI supports one commander or two with standalone Partner through
[`compose`](deck-building/decisions/phase-4.md) and
[`refine`](deck-building/decisions/phase-5.md). Legacy draft generation uses
heuristic weights. Catalog-guided generation requires saved affinity data and
current classifications. Oracle parsing and mechanical matching cover supported
patterns and report unresolved text.

## Collection Playability Analysis

[CPA Phase 2](collection-playability/decisions/phase-2.md) conditionally closed
under the revised MVP scope for supported draw comparisons and conditional
targeted mill with explicit strategy intent. Final classifications remain
uncertain and bulk review is disabled. The
[Phase 3 brief](collection-playability/phase-3-implementation-brief.md) plans
bounded functional comparisons among owned cards.

The [README](../README.md) covers setup and usage. The
[technical references](README.md) describe scoring and persistence. The
[offline development baseline](development-baseline.md) records isolated
validation commands, results, and outstanding test coverage.
