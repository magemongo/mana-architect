# Initial Deckbuilding Guidelines

This document describes generator goals and tradeoffs; it is not a legality
rules list. The validator remains authoritative for deck size, color identity,
singleton rules, collection availability, and legality in the local snapshot.

## Principles

- The physical collection is the only source of selectable cards.
- Strategic preferences change priority; they do not exclude cards by themselves.
  Availability, color identity, and legality remain hard filters.
- Category counts are configurable targets, not universal ratios.
- Every strategic signal used by the engine should be presented as a heuristic
  and traceable to Oracle text, card type, or structural data.
- The mana base should first cover the commander's colors, favoring sources that
  help cover multiple colors. Utility lands are configurable preferences and
  compete for slots with colored sources.
- Power level should be contextual and explainable. It must never be an absolute
  grade that automatically removes a card from the collection.

## Structured Profiles

`config/strategy_profiles.toml` stores signals, initial weights, and commander-
specific preferences in a format readable by people and machines. Weights are
relative priorities for ranking candidates, not quality scores. Text patterns
are approximations and can produce false positives; review profiles against
reference decks and tests.

Anowon's initial profile covers Rogue tribal, mill, and combat damage. A
commander without a profile still uses the general role and mana-curve scoring.

## Evolution

Before adding numeric targets for ramp, card draw, removal, protection, or lands,
compare reference decks and different collections. In the future, user
preferences and deck context may adjust these targets without changing legality
rules.
