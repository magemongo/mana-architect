# Oracle Text Parsing Engine: Implementation Guide

## 1. Purpose and design principles

Build a deterministic parser that converts a card's Oracle text into structured, explainable facts. The parser should identify what the text says; a separate strategy matcher should decide whether those facts support a commander or deck plan.

Keep these concepts separate:

- **Oracle keywords**: extracted lexical or normalized terms and phrases found in Oracle text, such as `landfall`, `counter`, or `instead`.
- **Mechanical tags**: normalized rules concepts inferred from one or more text spans, such as `creates_tokens`, `doubles_counters`, or `land_enters_battlefield`.
- **Strategy tags**: deck-building themes such as `+1/+1_counters`, `tokens`, `landfall`, or `aristocrats`. These are assigned by deterministic strategy rules downstream.
- **Card roles**: functional deck roles such as `counter_doubler`, `token_generator`, `ramp`, or `removal`. These are also derived downstream and can be many-to-many.

Do not make the parser decide that a card belongs to a strategy merely because it contains a word. Preserve source spans and evidence so downstream rules can be audited.

## 2. Pipeline architecture

Use a staged pipeline with versioned outputs:

1. **Input preparation**: retain the original Oracle text, card identity, language, and data source/version. Normalize line endings and whitespace without discarding punctuation, reminder text, mana symbols, or italicized ability words.
2. **Ability segmentation**: split the text into rules-bearing units: keyword ability lines, ability-word clauses, triggered abilities, activated abilities, static abilities, and other paragraphs. Preserve paragraph and span offsets. Handle multiline text and reminder text explicitly.
3. **Lexical extraction**: find candidate phrases from a versioned catalog and recognize general patterns (mana costs, counters, zones, numerals, player references). Use case-insensitive matching while preserving original surface forms and offsets.
4. **Semantic normalization**: map surface forms and inflections to canonical concepts, attach category and grammatical role, and resolve phrase overlap and local context.
5. **Mechanical classification**: produce explicit rules facts from normalized candidates and syntactic patterns. Avoid treating every lexical match as a mechanical fact.
6. **Downstream mapping**: apply versioned, deterministic rules to map mechanics to strategy tags and card roles. Keep evidence and rule IDs.

Each stage should be callable and testable independently. Store parser version, catalog version, and classifier version with results so an update can be reproduced.

## 3. Taxonomy

A single phrase may have multiple records when it plays more than one role. Prefer precise typed entries over a flat bag of words.

### 3.1 Ability keywords

Official keyword abilities, including evergreen and set-specific keywords: `flying`, `trample`, `deathtouch`, `lifelink`, `ward`, `hexproof`, `indestructible`, `menace`, `vigilance`, `flash`, `haste`, `reach`, `defender`, `cycling`, `equip`, `kicker`, `convoke`, `threshold`, and others.

Catalog metadata should include canonical name, aliases, rules category, whether the keyword is an ability word or keyword ability, and applicable rules/data version. Some keywords appear as ability names with reminder text; do not parse reminder text as if it were printed functional text unless the application explicitly requests it.

### 3.2 Ability words

Ability words are italicized labels that group abilities but have no independent rules meaning. Examples include `landfall`, `constellation`, `delirium`, `ferocious`, `morbid`, `metalcraft`, `raid`, `revolt`, `adamant`, and `parley`. Record the label as a lexical feature, but derive mechanics from the clause that follows. A label alone must not imply a particular effect.

### 3.3 Keyword actions

Rules-defined action verbs such as `create`, `transform`, `exile`, `sacrifice`, `destroy`, `discard`, `mill`, `proliferate`, `scry`, `surveil`, `investigate`, `populate`, `adapt`, `connive`, and `goad`. Track inflections and constructions (`creates`, `created`, `sacrificing`) under a canonical lemma while retaining surface text. Keyword actions are distinct from keyword abilities.

### 3.4 Triggers and timing

Capture trigger introducers and event/timing expressions: `when`, `whenever`, `at`, `at the beginning of`, `at the end of`, `enters`, `leaves`, `dies`, `attacks`, `blocks`, `upkeep`, `end step`, and `combat damage`. Model the event and its subject separately when possible, e.g. `event=enters_battlefield`, `subject=land`, `controller=you`.

### 3.5 Game objects

Recognize objects and characteristics that appear in rules text: `permanent`, `card`, `spell`, `token`, `creature`, `artifact`, `enchantment`, `land`, `planeswalker`, `battle`, `player`, `opponent`, `you`, `your opponents`, and `library`. Record object type, number, controller/owner qualifiers, and any attached attributes where known.

### 3.6 Counters

Parse counter expressions as structured names, not just the word `counter`: `+1/+1 counter`, `-1/-1 counter`, `loyalty counter`, `charge counter`, and unknown named counter forms. Normalize singular/plural forms to a common counter type while preserving the printed phrase and count. Distinguish adding, removing, doubling, distributing, and referencing counters.

### 3.7 Targeting and scope

Capture targeting (`target creature`, `up to one target`, `target opponent`), quantifiers (`each`, `all`, `one or more`, `any number of`, `up to`), and scope/controller qualifiers (`you control`, `an opponent controls`, `other`, `another`, `among`). These features affect legality and mechanical matching and must remain attached to the affected object or action.

### 3.8 Rules operators and quantities

Recognize operators and relation phrases including `instead`, `would`, `may`, `can't`, `if`, `unless`, `as though`, `twice`, `double`, `half`, `plus`, `minus`, `equal to`, `more than`, and `less than`. Parse numbers and number words when practical. Preserve conditional and replacement-effect structure; an operator without its surrounding clause is rarely sufficient evidence.

### 3.9 Ability structure

Classify structural patterns such as triggered (`Whenever...`), activated (`{cost}: effect`), static, replacement (`If ... would ..., ... instead`), and spell/one-shot effects. Capture costs, conditions, effect clauses, and delimiters. A colon after a cost is a strong activated-ability marker, but punctuation and reminder text should not be the only signal.

### 3.10 Zones and movement

Recognize zones: `battlefield`, `hand`, `library`, `graveyard`, `stack`, `exile`, and `command zone`. Model movement verbs and source/destination where possible: enters, leaves, return, put onto the battlefield, cast, discard, mill, sacrifice, exile, and dies. Keep `exile` as both a zone concept and a keyword action only when the phrase has both roles.

## 4. Segmentation and parsing rules

### 4.1 Ability segmentation

Split on Oracle paragraph breaks first, then recognize multiple abilities within a paragraph using ability-word em dashes, mana-cost colons, and known keyword-line forms. Keep a stable `ability_id`, original text, offsets, and detected structure. Do not split a single condition/effect clause merely because it contains punctuation. Reminder text may be marked as `reminder` and optionally excluded from functional classification.

### 4.2 Mana and costs

Parse brace symbols into an ordered token sequence, including generic, colored, hybrid, phyrexian, snow, and variable symbols. Support tap/untap symbols, numerals, and additional costs such as sacrifice or discard when present. For activated abilities, distinguish the cost before `:` from the effect after it. Example `{3}{G}{G}:` yields generic 3 plus two green mana and an activated ability. Do not treat mana symbols in rules explanations or reminder text as a cost unless they occur in the appropriate structural position.

### 4.3 Context, negation, and conditions

A word match is not enough. Preserve local scope and polarity. `You may create a token` differs from `You can't create a token`; `if a creature would die` differs from a creature dying. Represent negation and modality on the relevant action, and attach `if`, `unless`, and intervening clauses to the action they govern. The classifier should only emit positive mechanics when the pattern supports them.

### 4.4 Longest-match policy

Prefer the longest catalog phrase at a given position (`+1/+1 counter` before `counter`, `at the beginning of your upkeep` before `upkeep`). Use a trie, token matcher, or equivalent deterministic phrase matcher. Retain nested candidates only when they add useful distinct meaning; otherwise mark the shorter match as subsumed. Never let catalog ordering arbitrarily change the result.

### 4.5 Singular/plural and inflection

Normalize grammatical variants (`token`/`tokens`, `counter`/`counters`, `create`/`creates`/`created`) to a canonical lemma and preserve surface form. Avoid broad stemming that turns unrelated words into false matches. Use explicit inflection tables or a conservative lemmatizer backed by regression tests.

### 4.6 Deduplication

Deduplicate repeated concepts at two levels:

- **Occurrence records**: retain each source span and its ability context.
- **Card-level feature summary**: unique canonical feature IDs with occurrence references and counts.

Do not collapse separate abilities or discard count/direction differences. Sort deterministically by source offset and stable feature ID.

## 5. Suggested data model and JSON schema

Use a stable, language-neutral representation. The following is illustrative and can be adapted to the existing application's naming conventions.

```json
{
  "card_id": "string",
  "oracle_text": "string",
  "parser_version": "1.0.0",
  "catalog_version": "2026-01",
  "abilities": [
    {
      "ability_id": "a1",
      "kind": "static|triggered|activated|replacement|spell_effect|keyword|unknown",
      "text": "string",
      "span": {"start": 0, "end": 0},
      "cost": null,
      "condition": null,
      "trigger": null,
      "features": [],
      "mechanics": []
    }
  ],
  "oracle_keywords": [],
  "mechanics": [],
  "strategy_tags": [],
  "card_roles": []
}
```

Feature occurrence shape:

```json
{
  "id": "counter.plus_one_plus_one",
  "category": "counter",
  "surface": "+1/+1 counters",
  "lemma": "+1/+1 counter",
  "span": {"start": 42, "end": 58},
  "ability_id": "a1",
  "polarity": "positive",
  "attributes": {"number": "plural"}
}
```

Mechanical assertion shape:

```json
{
  "id": "counter.double",
  "subject": {"kind": "counter", "counter_type": "+1/+1"},
  "operation": "double",
  "scope": {"kind": "each", "object": "creature", "controller": "you"},
  "evidence": [{"ability_id": "a2", "span": {"start": 0, "end": 0}}],
  "confidence": "rule_pattern"
}
```

Treat confidence as provenance, not a probabilistic claim. Prefer enumerated reasons (`catalog_match`, `grammar_pattern`, `fallback`) to opaque model confidence.

## 6. Worked examples

Offsets below are illustrative. Mechanical assertions are intentionally separate from lexical features.

### 6.1 Doubling Season

Oracle text:

> If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead.\n>\n> If an effect would put one or more counters on a permanent you control, it puts twice that many of those counters on that permanent instead.

Illustrative parsed result:

```json
{
  "card_id": "doubling-season",
  "parser_version": "1.0.0",
  "abilities": [
    {
      "ability_id": "a1",
      "kind": "replacement",
      "text": "If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead.",
      "condition": {"kind": "would_event", "action": "create", "object": "token", "quantity": "one_or_more", "controller": "you"},
      "features": [
        {"id": "operator.if", "category": "rules_operator", "surface": "If"},
        {"id": "operator.would", "category": "rules_operator", "surface": "would"},
        {"id": "action.create", "category": "keyword_action", "surface": "create", "lemma": "create"},
        {"id": "object.token", "category": "game_object", "surface": "tokens", "lemma": "token"},
        {"id": "quantifier.one_or_more", "category": "scope", "surface": "one or more"},
        {"id": "operator.twice", "category": "rules_operator", "surface": "twice"},
        {"id": "operator.instead", "category": "rules_operator", "surface": "instead"}
      ],
      "mechanics": [
        {"id": "token.creation_replacement", "operation": "replace_create_quantity", "multiplier": 2, "scope": {"object": "token", "controller": "you"}}
      ]
    },
    {
      "ability_id": "a2",
      "kind": "replacement",
      "text": "If an effect would put one or more counters on a permanent you control, it puts twice that many of those counters on that permanent instead.",
      "condition": {"kind": "would_event", "action": "put", "object": "counter", "quantity": "one_or_more", "on": "permanent", "controller": "you"},
      "features": [
        {"id": "operator.if", "category": "rules_operator", "surface": "If"},
        {"id": "operator.would", "category": "rules_operator", "surface": "would"},
        {"id": "action.put", "category": "action", "surface": "put", "lemma": "put"},
        {"id": "counter.generic", "category": "counter", "surface": "counters", "lemma": "counter"},
        {"id": "object.permanent", "category": "game_object", "surface": "permanent"},
        {"id": "operator.twice", "category": "rules_operator", "surface": "twice"},
        {"id": "operator.instead", "category": "rules_operator", "surface": "instead"}
      ],
      "mechanics": [
        {"id": "counter.placement_replacement", "operation": "replace_put_quantity", "multiplier": 2, "scope": {"object": "counter", "on": "permanent", "controller": "you"}}
      ]
    }
  ],
  "strategy_tags": [
    {"id": "tokens", "evidence": ["token.creation_replacement"], "rule_id": "strategy.tokens.from_token_replacement"},
    {"id": "counters", "evidence": ["counter.placement_replacement"], "rule_id": "strategy.counters.from_counter_replacement"}
  ],
  "card_roles": [
    {"id": "token_doubler", "evidence": ["token.creation_replacement"], "rule_id": "role.token_doubler"},
    {"id": "counter_doubler", "evidence": ["counter.placement_replacement"], "rule_id": "role.counter_doubler"}
  ]
}
```

The phrase `would create` should not be reduced to an unconditional token-generation fact. The ability replaces a would-be event and modifies its quantity.

### 6.2 Bristly Bill, Spine Sower

Relevant Oracle text:

> Landfall — Whenever a land you control enters, put a +1/+1 counter on target creature.\n>\n> {3}{G}{G}: Double the number of +1/+1 counters on each creature you control.

Illustrative parsed result:

```json
{
  "card_id": "bristly-bill-spine-sower",
  "parser_version": "1.0.0",
  "abilities": [
    {
      "ability_id": "a1",
      "kind": "triggered",
      "text": "Landfall — Whenever a land you control enters, put a +1/+1 counter on target creature.",
      "trigger": {"event": "enters_battlefield", "subject": "land", "controller": "you"},
      "features": [
        {"id": "ability_word.landfall", "category": "ability_word", "surface": "Landfall"},
        {"id": "trigger.whenever", "category": "trigger_timing", "surface": "Whenever"},
        {"id": "object.land", "category": "game_object", "surface": "land"},
        {"id": "scope.you_control", "category": "scope", "surface": "you control"},
        {"id": "event.enters", "category": "trigger_timing", "surface": "enters"},
        {"id": "action.put", "category": "action", "surface": "put", "lemma": "put"},
        {"id": "counter.plus_one_plus_one", "category": "counter", "surface": "+1/+1 counter", "lemma": "+1/+1 counter"},
        {"id": "target.creature", "category": "targeting", "surface": "target creature"}
      ],
      "mechanics": [
        {"id": "land.enters_trigger", "subject": "land", "event": "enters_battlefield", "controller": "you"},
        {"id": "counter.put", "counter_type": "+1/+1", "quantity": 1, "target": "creature"}
      ]
    },
    {
      "ability_id": "a2",
      "kind": "activated",
      "text": "{3}{G}{G}: Double the number of +1/+1 counters on each creature you control.",
      "cost": {"mana": [{"generic": 3}, {"color": "G"}, {"color": "G"}]},
      "features": [
        {"id": "cost.mana", "category": "ability_structure", "surface": "{3}{G}{G}"},
        {"id": "action.double", "category": "action", "surface": "Double", "lemma": "double"},
        {"id": "counter.plus_one_plus_one", "category": "counter", "surface": "+1/+1 counters", "lemma": "+1/+1 counter"},
        {"id": "scope.each", "category": "scope", "surface": "each"},
        {"id": "object.creature", "category": "game_object", "surface": "creature"},
        {"id": "scope.you_control", "category": "scope", "surface": "you control"}
      ],
      "mechanics": [
        {"id": "counter.double", "counter_type": "+1/+1", "scope": {"kind": "each", "object": "creature", "controller": "you"}}
      ]
    }
  ],
  "strategy_tags": [
    {"id": "landfall", "evidence": ["land.enters_trigger"], "rule_id": "strategy.landfall.from_land_entry_trigger"},
    {"id": "plus_one_plus_one_counters", "evidence": ["counter.put", "counter.double"], "rule_id": "strategy.plus_counters.from_counter_mechanics"}
  ],
  "card_roles": [
    {"id": "counter_placer", "evidence": ["counter.put"], "rule_id": "role.counter_placer"},
    {"id": "counter_doubler", "evidence": ["counter.double"], "rule_id": "role.counter_doubler"}
  ]
}
```

`Landfall` remains an ability word feature; the trigger and counter effect are the evidence for mechanics and downstream strategy tags. The cost belongs to the activated ability, not to a keyword list.

## 7. Deterministic strategy matching

Implement downstream matching as declarative rules over mechanics, not raw substring search or generative output. Example rules:

- `counter.put` with counter type `+1/+1` -> strategy `plus_one_plus_one_counters` and role `counter_placer`.
- `counter.double` -> strategy `plus_one_plus_one_counters` when the type is `+1/+1`; role `counter_doubler`.
- `token.create` or `token.creation_replacement` -> strategy `tokens`; roles `token_generator` or `token_doubler` respectively.
- `land.enters_trigger` -> strategy `landfall`.

Each rule should have a stable ID, version, required facts, and emitted tags. Keep thresholds and exclusions explicit. For commander matching, compare normalized commander needs with these tags and return the exact evidence path. AI may help explain or rank candidates, but hard filtering should use deterministic facts and rules.

## 8. Catalog and extensibility

Store catalogs as data files rather than hard-coded conditionals. Suggested fields: stable ID, canonical phrase, category, aliases, inflections, boundaries, precedence/longest-match behavior, rules version, reminder-text behavior, and deprecation/replacement metadata. Keep mechanical patterns and strategy mappings in separate catalogs.

Support unknown phrases: retain unmatched text spans or route them to a review queue; do not silently discard them. Catalog changes should be additive where possible and versioned. Localize the matching layer only if parsing non-English Oracle translations is a goal; keep canonical mechanic IDs language-independent.

## 9. Testing strategy

Build a fixture corpus from real Oracle text across card types, sets, and templating eras. Include:

- Exact examples for Doubling Season and Bristly Bill.
- Positive and negative pairs (`create` vs `can't create`; `would die` vs `dies`).
- Triggered, activated, static, replacement, spell, and keyword abilities.
- Multiple abilities in one card, reminder text, ability words, punctuation, and line breaks.
- Mana costs with hybrid/phyrexian/variable symbols and additional costs.
- Singular/plural, inflections, named counter types, and unknown terms.
- Longest-match collisions (`at the beginning of your upkeep`, `+1/+1 counter`).
- Zone movement, target/quantifier scope, player/controller scope, and conditions.
- Snapshot tests for stable JSON and property tests for offsets, deterministic ordering, and no duplicate summary features.

Measure extraction precision/recall by category and mechanical classification accuracy separately. Review false positives in downstream strategy tags independently from parser errors. Maintain regression tests whenever a catalog or pattern changes.

## 10. Acceptance criteria

The implementation is ready when:

1. Oracle input is segmented into stable abilities with accurate source offsets.
2. The taxonomy distinguishes ability keywords, ability words, keyword actions, triggers/timing, game objects, counters, targeting/scope, rules operators, ability structure, and zones/movement.
3. Mana costs and activated ability boundaries are represented structurally.
4. Longest-match, inflection, singular/plural, context, negation, and deduplication behavior is deterministic and covered by tests.
5. The two worked examples produce the intended mechanical facts without confusing lexical features, mechanics, strategy tags, and roles.
6. Every emitted mechanic and downstream tag can be traced to source text and a versioned rule.
7. Unknown text is preserved for later catalog extension, and parser/catalog versions make results reproducible.
8. Hard deck filters consume deterministic tags and never depend on opaque free-form model reasoning.

## 11. Implementation phases

### Phase 1: Data contract and fixtures

Define canonical IDs, JSON structures, offset conventions, versioning, and a small gold fixture corpus. Document whether offsets are Unicode code points, UTF-16 code units, or bytes; use one convention everywhere.

### Phase 2: Segmentation and lexical catalog

Implement paragraph/ability segmentation, mana symbol tokenization, catalog matching, longest-match selection, and occurrence records. Start with a focused catalog for common objects, counters, triggers, actions, and ability words.

### Phase 3: Normalization and context

Add inflection normalization, quantities, scope, target markers, negation/modality, conditional clauses, zones, and movement relations. Preserve ambiguities rather than inventing structure when a pattern is uncertain.

### Phase 4: Mechanical patterns

Add deterministic patterns for token creation, counter placement/doubling, land-entry triggers, zone movement, and other project priorities. Keep lexical extraction independent from these patterns.

### Phase 5: Strategy and role rules

Create separate rule catalogs that map mechanics to strategy tags and card roles. Add evidence paths and evaluate filter precision against commander/deck use cases.

### Phase 6: Expansion and maintenance

Grow coverage using unmatched-text review and regression fixtures. Version catalogs and rules, compare metrics across releases, and provide migration handling when canonical IDs change.

## 12. Practical implementation checklist

- [ ] Preserve raw Oracle text and all source spans.
- [ ] Separate parser output from strategy and role classifications.
- [ ] Use stable, versioned feature and mechanic IDs.
- [ ] Apply longest phrase matches before shorter nested terms.
- [ ] Keep conditions, scope, negation, and replacement language attached to actions.
- [ ] Parse activated costs independently from effects.
- [ ] Retain occurrences while deduplicating only card-level summaries.
- [ ] Make every downstream classification traceable to evidence and a rule ID.
- [ ] Add each discovered false positive or false negative to the fixture corpus.
