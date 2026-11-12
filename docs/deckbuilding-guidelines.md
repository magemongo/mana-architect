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

Before adding numeric targets for ramp, card advantage, interaction, or lands,
compare reference decks and different collections. In the future, user
preferences and deck context may adjust these targets without changing legality
rules.

The core roles are `spot_interaction`, `mass_interaction`, `ramp`, and
`card_advantage`. Protection is interaction too: a targeted shield is spot
interaction, while a fog or board-wide shield is mass interaction. Parser
mechanics and synergy tags should preserve the method (counter, destroy, exile,
return, prevention, and so on).

`main_strategy` is a dynamic role assigned against the selected deck strategy.
It can overlap with every core role. A card may contribute to +1/+1 counters
and card advantage at once, for example. Strategy tag affinities define the
initial evidence used for this dynamic role. Catalog-guided generation uses a
configurable soft target (currently half the nonland slots) for
`main_strategy`; this changes ranking but never blocks a complete deck. The
ratio is a starting heuristic, not a calibrated recommendation. Configure its
ratio and weight in `config/scoring.json`. Card selection and neutral card
replacement are useful optional signals, but are not universal deck quotas.

Card advantage is an outcome, not a synonym for drawing or recursion. A
one-for-one cantrip or loot effect does not qualify by itself; repeatable or
net-positive access to resources can. The current Oracle text heuristic is
conservative and should be refined as the parser captures quantities, costs,
repeatability, and selection choices more precisely.
