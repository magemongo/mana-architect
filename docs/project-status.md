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

Commander selection supports one commander per deck. Draft generation uses
heuristic weights, and the UI provides draft generation and review. Oracle
parsing and mechanical matching cover supported patterns and report unresolved
text. Catalog-guided generation requires saved affinity data and current
classifications.

Use temporary SQLite databases for tests. The
[README](../README.md) covers setup and usage. The
[technical references](README.md) describe scoring and persistence.
