# Oracle Text Parser

The parser lives in `src/mana_architect/oracle_text.py`.
Lexical entries and supported mechanical patterns are loaded from
`config/oracle_lexicon.json` and `config/oracle_mechanics.json`. Parser output
is used by classification rules, persisted on each classification run, and
returned on `ClassificationResult.oracle_parse`.

## Current contract

- `parse_oracle_text(text, lexicon, mechanical_patterns)` returns the original
  text, paragraph-based ability segments, lexical features, and mechanical facts.
- Source offsets are Python Unicode code-point offsets into the original text.
- Features have stable IDs, category, surface spelling, canonical phrase,
  ability ID, and source span. Matching is case-insensitive and chooses the
  longest overlapping phrase at each position.
- Activated abilities are recognized when a mana-symbol cost precedes a colon.
  Replacement effects and common triggered forms receive structural labels.
- Mechanical patterns cover token creation, counter placement and doubling,
  quantity replacement, card draw, life gain, combat/noncombat damage, land
  search onto the battlefield, creature return from graveyard, and +1/+1 buffs.
  Captured quantities are normalized where available; replacement facts retain
  multiplier attributes and all facts carry source spans.
- Rule predicates `oracle_feature_any` and `oracle_mechanic_any` consume those
  outputs. Mechanic rules can constrain exact scalar attributes with `where`
  (currently used to distinguish your life gain from an opponent's).
- Classification runs evaluate tag rules before dependent rules. The
  `classification_tag_any` predicate lets a role rule consume already
  classified mechanical tags. For example, `counterspell`, `spot_removal`,
  and `protect_single_creature` can support `spot_interaction`, while
  `board_wipe` and `mass_protection` can support `mass_interaction`.
- Strategy matching consumes the same tag evidence through each strategy's
  `tag_affinities`. The selected strategy determines dynamic
  `main_strategy` contribution; tag and role evidence remain independently
  visible and explainable.
- Configured role terms are parsed as `configured_role_term` features before
  the compatibility mapping to role evidence, removing raw substring search
  while preserving existing role behavior.
- Strategy tags and card roles are deliberately not emitted by this parser.

## Integration boundary

`classification.py` supports both normalized-text predicates and parser-backed
predicates. Classification runs store the parser version, ability segmentation,
feature spans, and mechanics as JSON. Evidence rows also store source offsets.
Older runs remain readable with `oracle_parse=None`.

## Strategy capability extraction

`extract_mechanical_capabilities(parsed, name=..., type_line=...)` interprets
source-linked ability lines as typed events, costs, triggers, and replacement
effects. It reuses the mana resource extractor for land and mana development.
The same Treasure creation event carries both the token kind and artifact
type. Evidence offsets refer to the original Oracle text. Unrecognized
clauses and unsupported modal choices surface as unknowns.
The supported typal entry replacement records the qualifying creature type
and the variable count based on permanents already controlled. It can match
an entering creature, matching creature token, or the entrant's own
+1/+1-counter payoff. Passive and player-attributed counter triggers retain
their controller and self-reference constraints.
See the [mechanical scoring reference](synergy-scoring.md#mechanical-candidate-evaluation).

## Mana resource extraction

`extract_mana_resources(parsed)` extracts direct
mana production, land searches onto the battlefield, lands put into play from
hand, extra land plays, and creation of Treasure, Gold, and Powerstone tokens.
Events retain source offsets, activation costs, quantities, and recognized
conditions. Quantities that depend on the board or a variable remain unknown.
The versioned extraction patterns live in `ramp_rules_catalog.py`.

The Suggestions evaluator consumes these events for the Ramp role. Its
[scoring and coverage](./synergy-scoring.md) describe the current integration.

## Interaction evaluation

The interaction evaluator uses parsed ability spans and original Oracle text to
build a card profile. `classify_card_interaction(card, parsed)` accepts a collection card and
can reuse its existing `OracleParse`. `build_interaction_profile(card_id, parsed,
...)` accepts metadata directly. Interaction evidence uses the same absolute
source offsets as the parser. Effects retain separate target and affected-object
counts, mechanical tags, and spot or mass scope. Legal modal choices and supported
alternate costs are represented as execution variants.

The evaluators expose independent spot and mass scores and their 0–10
components. Card-level `scores` report the best available variant for each
scope. Within a variant, the strongest effect for each scope supplies its
score, so overlapping effects and modal combinations do not accumulate points.
Unknown weighted dimensions, such as the efficiency of an X cost, leave the
total `null`. Pass `scoring=None` for classification only. The current
[scoring reference](./synergy-scoring.md#core-roles) documents the
weights, and `config/interaction_profile.schema.json` defines the JSON output
contract.

Supported variants include distinct modal choices, overload, and common kicker
conditions. The profile carries warnings for recognized but unsupported
syntax or ambiguous scope, such as repeatable mode selection and complex
alternate costs. The profile is available through the card-level Python API
and supplies the base scores and evidence for Spot Interaction and Mass
Interaction on [Suggestions](./synergy-scoring.md#core-roles).
The registry covers the tested Oracle patterns, and the scores have yet to be
calibrated against a full collection corpus.

Suggestions builds interaction profiles from current card metadata and Oracle
text, reusing a current saved parse when available. Color-identity searches
require current classifications. Commander searches can classify and parse
cards live.

## Downstream card-advantage and selection heuristics

Deckbuilding role assignment currently adds a separate, reviewable heuristic
on top of parser classification. It keeps `card_selection` as an optional tag
and `card_advantage` as a core role. It does not rewrite the parser's lexical
features or mechanical facts.

- A draw/discard sequence in either order is detected as selection. Equal
  quantities are neutral and cannot establish card advantage by themselves.
- Activated draw is evaluated by reading the cost before the colon and the
  draw/discard effect after it. A card-consuming cost or offsetting discard
  prevents a one-card effect from being misread as reusable advantage.
- Reminder text that gives a Clue a sacrifice-to-draw ability does not make
  that one Clue source repeatable. The surrounding trigger must be capable of
  recurring for it to count as a recurring card-advantage source.
- Counter-linked draw patterns now include draws for creatures with +1/+1
  counters, draws when those counters are placed, and draws scaled by counters
  placed. The +1/+1 counter mechanics also recognize passive placement wording
  such as “counters are put,” explicit placement verbs, and creatures entering
  with counters. This supports card-advantage and strategy evidence without
  treating reminder text as an independent engine.

The Card Advantage ranking uses measured net gain for supported cast lines
and classification evidence as a fallback for effects outside that coverage.
See [core role scoring](synergy-scoring.md#core-roles).

## UI inspection

Open **Catalog → Oracle Parser** to search cards in the local collection and
inspect ability segments, lexical features, mechanical facts, parser-backed
classification rules, and evidence spans. If a card's saved classification is
stale, the page shows a live preview without writing it. **Save current parser
result for this card** explicitly reclassifies only the selected card; the
collection is never reclassified automatically from this page.

## Scope limits

The segmenter splits on paragraph boundaries. Its lexicon, ability shapes,
and mechanical patterns have partial coverage. Downstream evaluators preserve
evidence spans and report unresolved text where applicable.
