# Oracle Text Parser

## Status

The parser foundation is implemented in `src/mana_architect/oracle_text.py`.
Lexical entries and supported mechanical patterns are loaded from
`config/oracle_lexicon.json` and `config/oracle_mechanics.json`. Parser output
is used by classification rules, persisted on each classification run, and
returned on `ClassificationResult.oracle_parse`.

## Current contract

- `parse_oracle_text(text, lexicon, mechanical_patterns)` returns the original text, paragraph-based ability
  segments, lexical feature occurrences, and conservative mechanical facts.
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
  (currently used to distinguish your life gain from an opponent's). All seven
  former literal Oracle-text rules now use mechanic IDs.
- Configured role terms are parsed as `configured_role_term` features before
  the compatibility mapping to role evidence, removing raw substring search
  while preserving existing role behavior.
- Strategy tags and card roles are deliberately not emitted by this parser.

## Integration boundary

`classification.py` supports both normalized-text predicates and parser-backed
predicates. Most rules still use normalized text and should migrate one at a
time. New classification runs store the parser version, ability segmentation,
feature spans, and mechanics as JSON. Evidence rows also store source offsets.
Older runs remain readable with `oracle_parse=None`.

## UI inspection

Open **Catalog → Oracle Parser** to search cards in the local collection and
inspect ability segments, lexical features, mechanical facts, parser-backed
classification rules, and evidence spans. If a card's saved classification is
stale, the page shows a live preview without writing it. **Save current parser
result for this card** explicitly reclassifies only the selected card; the
collection is never reclassified automatically from this page.

The next migration should:

1. Expand coverage and schema validation for the versioned parser catalogs.
2. Expand structured attributes and exact-match `where` predicates to cover
  targets, controller scope, zone transitions, and polarity. Keep ability words
  as lexical evidence; do not infer mechanics from a label.
3. Gradually replace `roles[].oracle_keywords` with explicit feature/mechanic
  rules; the compatibility mapping currently consumes parsed feature spans.
4. Compare classifications against a broader Oracle fixture corpus and review
  false positives as the parser catalog grows.

## Scope limits

The current segmenter splits paragraphs; it does not yet parse multiple
abilities within one paragraph or reminder-text boundaries. The starter
lexicon and pattern catalog are intentionally partial. Negation handles common
local forms, but full condition, target/controller scope, all zone movement
relations, and general mana-cost grammars remain future increments. The current
mechanical patterns are conservative, not a complete Magic rules interpreter.

See [the implementation guide](./oracle_text_parser_implementation_guide.md)
for the target taxonomy, schemas, worked examples, phases, and acceptance
criteria.
