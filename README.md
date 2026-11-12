# Mana Architect

A local application for organizing a Magic: The Gathering collection and
building Commander decks using cards the player owns.

See [project status](docs/project-status.md) for the product roadmap and the
[documentation index](docs/README.md) for technical references.

## Quickstart (Windows / PowerShell)

Requires Python 3.11 or later. From the project directory:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

To install the local UI as well:

```powershell
python -m pip install -e ".[app,dev]"
streamlit run .\src\mana_architect\streamlit_app.py
```

Streamlit opens Mana Architect in a browser. Choose a commander and generate a
draft on **Deck Builder**. Open **Deck** to browse the list in three compact
columns: Commander, Creatures, Artifacts, Enchantments, Instants, Sorceries,
and Lands. Hover over a card name to preview its image and Oracle text. Deck
validation and JSON/TXT downloads are on the Deck page. Analysis shows a
mana-curve histogram and known mana-cost symbols as compact icons. Direct
decklist editing is not available yet. The UI and CLI use the local database
configured in `.env`.

Anowon's legacy profile prioritizes Rogue tribal, mill, and combat damage. It
remains in `config/strategy_profiles.toml` and is the generator default. The
versioned strategy, tag, role, rule, and parameter catalog lives in JSON files
under `config/`. In Deck Builder or the CLI, choose a catalog strategy to guide
selection; this requires an explicit commander/strategy affinity configured on
**Suggestions**. See [deckbuilding guidelines](docs/deckbuilding-guidelines.md).
The catalog prioritizes candidates while the generator balances roles and curve
and respects collection availability, legality, color identity, and mana-base
color coverage.

The versioned rule classifier produces candidate evidence and retains human
review for ambiguous signals. Results can be persisted through an explicit
action. Text rules are limited literal heuristics: they do not interpret full
context or cross-card synergy, and infinite combos require explicit curation.

The domain layer supports manual overrides while preserving automatic evidence
and flagging contradictory exclusions. Commander profiles support multiple
strategy and tag affinities with `primary`, `secondary`, or `complementary`
levels and explanations. Affinity scores are heuristics, not probabilities.
Overrides, affinities, and evidence are stored in additive SQLite tables.
**Suggestions** supports editing multiple affinities in one table and
correcting a card classification without deleting automatic evidence. Roles
are grouped into deck services and strategy synergies. **Catalog** supports
viewing, creating, editing, and deleting strategies and roles, including
Oracle terms per role. Confirmed deletions remove current links and manual
overrides while preserving classification history. Config changes are
versioned; reclassification is incremental, explicit, and does not run when
the app opens. Read [catalog persistence](docs/classification-storage.md)
before persisting changes to the collection.

The deterministic scorer evaluates synergy for an explicitly selected
strategy. Suggestions shows scores, match reasons, detected roles, and
available quantities. Catalog-guided generation also uses the score alongside
role and curve balance. Missing or stale classifications are not recalculated
automatically; explicitly reclassify the collection on **Suggestions**. Cards
without current classifications are reported and excluded from guided mode.
Legality and color identity remain separate filters. See
[synergy scoring](docs/synergy-scoring.md) for details and limitations.

## Configuration and Database

The `.env` file is optional. Without it, the database defaults to
`data/mana_architect.db`. Set `MANA_ARCHITECT_DB_PATH` in `.env` to use another
path. If the `py` launcher is unavailable, install Python with the launcher
enabled or create the environment using the full executable path:

```powershell
& "C:\path\to\python.exe" -m venv .venv
```

## Import a ManaBox Collection

```powershell
python -m mana_architect import-csv .\collection.csv
```

The importer uses the `Name`, `Set code`, `Collector number`, `Foil`,
`Quantity`, `Scryfall ID`, `Condition`, and `Language` columns. Invalid rows
stop the import and report their line number; the source file is unchanged.
Reimporting sets each printing's quantity to the CSV value instead of adding
it again. Repeated rows for the same printing, language, finish, and condition
are grouped. Missing records in a later import are not deleted.

## Search and Sync Card Data

```powershell
python -m mana_architect search "anowon"
python -m mana_architect sync-scryfall
```

`sync-scryfall` downloads Scryfall's `default_cards` Bulk Data, enriches card
records, and links collection printings to their `oracle_id`. The initial
download is approximately 77 MB compressed and is cached under
`data/cache/scryfall/`. Collection matching is local; collection IDs are not
sent to the API. Sync requires internet, while search and import remain local.
Existing downloads can be reprocessed from cache. Canonical card names, type
lines, and Oracle text use Scryfall's English data; physical printing language
remains collection metadata. Printings that cannot be linked remain in the
collection and are reported.

Search accepts combined filters:

```powershell
python -m mana_architect search --color U --identity WU
python -m mana_architect search "anowon" --type Creature
python -m mana_architect search --mana-cost "{2}{U}"
```

Color and identity filters require all specified symbols. Type and mana-cost
filters search for a substring.

## Choose a Commander

```powershell
python -m mana_architect commanders
python -m mana_architect commanders --query "Anowon"
python -m mana_architect search --commander "Anowon, the Ruin Thief"
python -m mana_architect search --commander "PASTE_ORACLE_ID_HERE" --roles
```

The commander list shows eligible commanders that are legal in the latest
synced data and present in the collection. The player chooses one. Commander
search includes only owned cards marked legal by synchronized Scryfall data and
whose color identity is a subset of the commander's. Legality data can become
stale; sync and check the displayed date. Role classification is a heuristic
based on Oracle text. Partner/Background commander selection is not implemented.

## Validate a Commander Deck

Create a JSON file with the commander and quantities by `oracle_id`. The
commander must appear in the list and counts toward the 100-card total:

```json
{
  "commander": "COMMANDER_ORACLE_ID",
  "cards": [
    {"oracle_id": "COMMANDER_ORACLE_ID", "quantity": 1},
    {"oracle_id": "ANOTHER_CARD_ORACLE_ID", "quantity": 99}
  ]
}
```

```powershell
python -m mana_architect validate-deck .\deck.json
```

Validation checks deck size, commander, legality from the Scryfall snapshot,
color identity, singleton rules (including basic lands and explicit Oracle
text exceptions), and physical availability. Missing data can make the result
incomplete. A `valid` result is conditional on synchronized data and does not
guarantee the ban list is current today. Run `sync-scryfall` before validating.
Two-commander partner decks are not supported yet.

## Generate a Deck Draft

```powershell
python -m mana_architect build-deck "Anowon, the Ruin Thief" --output .\data\decks\anowon.json
python -m mana_architect build-deck "Anowon, the Ruin Thief" --lands 36 --output .\data\decks\anowon.json
```

The default is 38 lands. The generator includes the commander, distributes
basic lands across its colors when available, and ranks other cards using
heuristic roles (ramp, card advantage, spot interaction, and mass interaction),
with mana value as a
secondary criterion. This is not a guaranteed recommendation; review synergy,
curve, lands, and game plan.

To use a catalog strategy, configure the commander affinity on **Suggestions**
and pass an active strategy:

```powershell
python -m mana_architect build-deck "Anowon, the Ruin Thief" --strategy tokens --output .\data\decks\anowon-tokens.json
```

Catalog score complements role and curve balance. Without `--strategy`, the
CLI stays in legacy mode. Catalog mode reports and skips cards without current
classifications and does not reclassify or persist classifications
automatically. A draft is saved only when it has 100 cards and no known
deterministic violations. If data cannot complete it, no incomplete deck is
written. Incomplete validation due to missing data is included in JSON and
returns exit code 2. Output is not overwritten unless `--overwrite` is passed.
Files in `data/decks/` are not tracked by Git.

## Analyze and Export a Decklist

```powershell
python -m mana_architect analyze-deck .\data\decks\anowon.json
python -m mana_architect analyze-deck .\data\decks\anowon.json --export-text .\data\decks\anowon.txt
```

Analysis shows card names, mana curve, land count, heuristic roles, and
validation. The `.txt` export separates the commander from the main deck and
uses `quantity card name` format. It is not overwritten without `--overwrite`.
Role labels can overlap and are not a strategic evaluation.

Run tests with `python -m pytest`.

## Current Scope

See [project status](docs/project-status.md) for current capabilities,
limitations, and next candidates. This README covers installation and usage;
the [documentation index](docs/README.md) lists technical and historical
documents.
