# Project Status and Roadmap

- **Reviewed:** 2026-11-12
- **Last fully audited revision:** `5533668` (the parser integration below has
  not received a full test-suite audit)
- **Approved development scope:** catalog, role, affinity administration, and
  Oracle text parser integration requested on 2026-11-12
- **Product language:** English

This is the canonical summary of the product and its known roadmap. The status
was checked against the code, configuration, and tests at the revision above. It
does not promise competitive deck quality and does not replace those sources.

## 1. Product Goal

Mana Architect is a local application for organizing a physical Magic: The
Gathering collection and building or analyzing Commander decks using cards the
player owns. The collection is the source of truth for ownership. External data
enriches card records and helps check legality, but never grants permission to
include cards that are not owned.

Selection and validation rules should remain deterministic, reproducible, and
explainable. Classification and strategy scores are reviewable heuristics, not
final legality decisions or guarantees of deck strength.

## 2. Roadmap

### Implemented capabilities

1. Local collection import and search.
2. Card data synchronization and basic Commander selection and validation.
3. Deterministic draft generation, analysis, and export.
4. Local interface for deck building, viewing, and suggestions.
5. Versioned catalog, explainable classification, affinities, and scorer.
6. Optional catalog-guided generation; legacy generation remains the default.

The integration described in [catalog-guided-deck-generation.md](./catalog-guided-deck-generation.md)
is complete. The next approved effort is improving the catalog, affinities, and
their administration in the UI. That work is in progress:

| Status | Work item | Current state |
|---|---|---|
| In progress | Multiple strategies per commander and a responsive editor for all three affinity levels | Domain and table editor support multiple strategies and levels on one screen; visual and workflow review remains |
| In progress | Separate universal deck-service roles from strategy-specific synergy roles | `deck_service` and `synergy_role` are separate filters |
| In progress | Admin screen for strategies and roles, including Oracle terms per role | Admin screen is implemented; visual review and delete/save workflow review remain |
| Later | Manually add, remove, or replace cards in a generated list and revalidate | Deferred; direct decklist editing is unavailable |
| Later | Save and manage multiple decks and versions | Not implemented; decks have no dedicated persistence |
| Later | Support Partner/Background commanders | Not implemented |
| Later | Evaluate and calibrate heuristics with reference decks | Pending; current weights are initial and not empirically calibrated |
| Later | Optional natural-language interface using an LLM | Idea from the initial document; not implemented |

Items marked Later are not approved or scheduled. Current work should preserve
catalog versioning and classification provenance.

## 3. Implemented Features

### Collection, data, and CLI

- Local SQLite collection with logical card identity (`oracle_id`) separate
  from physical printings and quantities.
- Import from ManaBox CSV with idempotent updates per printing. Moxfield and
  Archidekt parsers are not documented as supported.
- Scryfall Bulk Data synchronization with a local cache and printing linkage
  where a match is available. Search and collection processing remain local.
- CLI commands: `import-csv`, `sync-scryfall`, `search`, `commanders`,
  `validate-deck`, `build-deck`, and `analyze-deck`.

Sources: `src/mana_architect/{models,database,manabox,importer,scryfall,collection,commanders,cli}.py`
and the related import, sync, commander-selection, and CLI tests.

### Commander rules and generation

- Validation covers the 100-card size including the commander, commander
  eligibility, color identity, singleton rules with recognized exceptions,
  physical availability, and legality from the local snapshot.
- Validation distinguishes valid, invalid, and incomplete lists when data is
  missing.
- The deterministic generator respects inventory, legality, color identity,
  deck composition, and land selection based on color coverage.
- Legacy mode uses Anowon's initial profile in `config/strategy_profiles.toml`
  and remains the default.
- Optional catalog mode is available through `build-deck --strategy` and the
  Deck Builder. It requires an explicit commander/strategy affinity, uses
  current classifications and overrides, reports missing classifications, and
  does not reclassify or persist classifications during generation.
- Decklist analysis, mana curve, heuristic roles, and JSON/TXT export.

Sources: `src/mana_architect/{validation,deckbuilding,strategy,cli}.py` and
related validation and generation tests.

### Catalog and interface

- Versioned JSON configuration with **15 strategies, 106 tags, 20 roles, and
  10 classification rules**.
- Deterministic rule-based classification with evidence, fingerprints, and
  review indicators. Per-card overrides and commander affinities are persisted
  separately.
- Additive SQLite persistence for configuration versions, runs,
  classifications, evidence, manual overrides, and affinities.
- Deterministic scorer explains its components and reasons. Suggestions are
  filtered by collection, legality, and color identity.
- The domain and UI support multiple strategy and tag affinities per commander,
  with `primary`, `secondary`, and `complementary` levels. The responsive table
  editor still needs visual verification.
- Roles use the `deck_service` and `synergy_role` groups, shown separately in
  Suggestions and its filters.
- Roles accept `oracle_keywords`, literal terms that produce reviewable
  classification evidence. Terms now pass through parser-backed lexical
  extraction before the compatibility mapping to role evidence.
- The Oracle parser uses versioned lexicon and mechanic catalogs, supports
  parser-backed classification predicates, and persists the parse document and
  source spans additively. All seven Oracle-text classification rules now use
  mechanical IDs; broader target/zone modeling and corpus review remain. See
  [oracle-text-parser.md](./oracle-text-parser.md).
- Catalog now includes an Oracle Parser inspector for collection cards, with
  live previews and explicit single-card reclassification.
- The Catalog page can view, create, edit, and delete strategies and roles.
  After confirmation, deletion removes linked rules, affinities, and current
  manual overrides while preserving classification history. Changes are saved
  in `config/`, versioned, and require explicit reclassification.
- Local Streamlit pages include Deck Builder, Deck, Suggestions, and Catalog.
  Affinities and classification overrides can be reviewed in the UI.
- Collection reclassification is an explicit user action and does not run when
  the app opens.

Sources: `config/*.json`, `src/mana_architect/{catalog,classification,classification_repository,commander_affinity,overrides,synergy,suggestions,streamlit_app}.py`,
and related tests.

## 4. Open Work and Decisions

### Confirmed gaps

- Edit a generated decklist manually in the UI.
- Persist and manage saved decks, multiple lists, and version history.
- Add Partner/Background commander rules.
- Add a natural-language/LLM interface.
- Empirically validate and calibrate scorer weights, caps, and results.

The first four items are not implicitly approved for the next sprint. Empirical
validation is needed before treating scores as calibrated; they are currently
heuristics.

### Current operational limitations

- Legality and the ban list depend on locally synchronized data. A passing
  validation does not guarantee that the snapshot reflects today's ban list;
  sync and check the displayed date.
- Text rules do not understand full context or cross-card synergy. Infinite
  combos require explicit curation.
- Catalog-guided generation needs a configured affinity and current
  classifications. Cards without current classifications are omitted and
  reported.
- The generator creates a heuristic draft, not a guaranteed competitive list.

## 5. Maintenance and Audit Notes

### Stack and data

- Python 3.11+, SQLAlchemy 2, and SQLite. Streamlit is optional for the UI;
  Pytest is used for tests.
- The personal database, CSV files, `.env`, Scryfall cache, and exported decks
  are excluded from Git by default. Never use or modify the real collection
  database for tests; use fixtures and temporary databases.
- `sync-scryfall` requires internet. The scorer and Suggestions operate locally
  and do not call an LLM or remote service.
- Persistent classification operations must use transactions. See
  [classification-storage.md](./classification-storage.md).

### Guidance for AI agents

1. Read this document and the [documentation index](./README.md), then read the
   technical reference for the task.
2. Confirm behavior in code, configuration, and tests. This summary does not
   imply that a proposal has been implemented.
3. Preserve collection ownership, legality, color identity, availability,
   determinism, and classification provenance.
4. Do not perform destructive changes, external syncs, or reclassification of
   the personal database without explicit authorization.
5. When work is complete, record changed files, evidence, tests, limitations,
   and needed documentation updates. Update the roadmap only after a product
   decision has been made.

### Evidence for this review

- Audited code revision: `5533668` (`feat: integrate strategy catalog into deck generation`).
- Tests at that revision: `python -m pytest -q` — **131 passed**. The current
  changes had not been tested at the time of that review.
- Manual Streamlit exercise with a temporary database: catalog mode, Tokens
  strategy, **100/100** cards, 98 lands, and validation passed. The temporary
  database was removed afterward.

A behavior change invalidates the corresponding evidence. Update the reviewed
revision, run relevant tests, and record the new result.
