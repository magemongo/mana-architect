# Deck Generation

Deck Builder and `build-deck` produce a deterministic draft from owned
printings. The generator uses available quantities, commander color identity,
known Commander legality, and copy limits. The final validator checks the
100-card list, including the commander.

## Default generation

Nonland selection balances the mana curve and the core roles of ramp, card
advantage, spot interaction, and mass interaction. Commander profiles in
`config/strategy_profiles.toml` add priority signals. Anowon's profile favors
Rogues, mill, and combat damage. Land selection prioritizes color coverage and
considers utility lands within the available inventory.

## Catalog-guided generation

Select **Catalog Strategy** in Deck Builder or pass `build-deck --strategy`.
This mode requires a saved affinity for the selected commander and strategy.
Affinities are stored by `save_commander_affinity_profile` in the Python
repository layer. The generator uses current classifications with per-card
overrides and reports cards it skips because their classifications are stale
or missing. Refresh classifications explicitly in Suggestions.

The [catalog score](synergy-scoring.md#catalog-guided-generation) contributes
up to 0.25 to nonland ranking. Selection updates role counts as it fills the
deck. Evidence for the chosen strategy contributes to a deck-specific
`main_strategy` role that can overlap with core roles. Its soft target is set
in `config/scoring.json`. For lands, the catalog score breaks ties within the
structural priorities for color coverage, utility, and tapped-land status.
