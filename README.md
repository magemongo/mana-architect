# Mana Architect

A local application for organizing a Magic: The Gathering collection and
building Commander decks using cards the player owns.

See [project scope](docs/project-status.md) and the
[technical references](docs/README.md).

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
```

Import your collection and sync card data using the commands below. Then
start the local UI:

```powershell
streamlit run .\src\mana_architect\streamlit_app.py
```

Choose a commander in **Deck Builder**, then open **Deck** to inspect,
validate, and download the draft. Use **Suggestions** to rank owned candidates
and explicitly refresh classifications. **Catalog** manages strategies and
roles and includes an Oracle text inspector.

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
records, and links collection printings to their `oracle_id`. The bulk download
is cached under `data/cache/scryfall/`. Collection matching is local; collection IDs are not
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
based on Oracle text. The application supports one commander per deck.

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

## Rank Commander Candidates

On **Suggestions**, select an owned commander to rank cards using its
abilities and color identity. Select strategies and set their relative
priorities if desired, then click **Find suggestions**. Expand **Score
details** to see the score components, matched abilities, and conditions.

The CLI provides the same synergy ranking before deck construction:

```powershell
python -m mana_architect evaluate-candidates "Anowon, the Ruin Thief" --strategy tokens --strategy aristocrats=2 --limit 10
```

The JSON includes the ranked candidates, commander and strategy evidence,
rejection reasons for ineligible cards, and independent Ramp, Card Advantage,
and Interaction evaluations. Each `--strategy` accepts an optional priority.
Omit the strategy options to rank by commander interactions. See the
[mechanical scoring reference](docs/synergy-scoring.md#mechanical-candidate-evaluation).

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

For a commander with a saved affinity for an active catalog strategy, pass
that strategy to the builder:

```powershell
python -m mana_architect build-deck "Anowon, the Ruin Thief" --strategy tokens --output .\data\decks\anowon-tokens.json
```

Catalog score complements role and curve balance. Omitting `--strategy` uses
default generation, including a commander profile when one exists. See
[deck generation](docs/deckbuilding-guidelines.md) for the affinity requirement.
Catalog mode reports and skips cards without current classifications. A draft
is saved only when it has 100 cards and no known
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
