# Ramp Evaluation Engine — Implementation Guide

**Project:** Magic: The Gathering Commander Deck Builder  
**Stage:** Build from scratch (pre-deck construction)  
**Status:** Implementation specification  
**Language:** English

## 1. Purpose and scope

Build a deterministic module that recognizes and evaluates mana acceleration capabilities from card Oracle text and structured card metadata **before a deck exists**. The output is a reusable per-card `ramp_profile`, suitable for commander-aware candidate filtering and initial card selection.

**In scope:** lexical/semantic identification of mana generation, land acceleration, cost reduction, untapping mana sources, mana multipliers, and consumable mana resources; static scoring for speed, persistence, reliability, and flexibility; explicit requirements and restrictions; traceable evidence.

**Out of scope for this phase:** deck-list analysis, opening-hand probability, mana-curve simulation, projected commander cast turn, matchup/game-state simulation, and deck refinement. Keep extension points for these future capabilities, but do not implement or depend on them.

### Core principle

`ramp` is a strategic role; **mana production, mana development, and effective mana savings are distinct mechanics**. A card that mentions mana or lands is not automatically ramp. Temporary burst and persistent ramp should share the strategic tag `ramp` but receive different persistence and speed scores.

## 2. Architecture and data flow

```text
Card Oracle Text + Card Metadata
             |
             v
      Oracle Text Parser
  (abilities, costs, triggers,
   targets, quantities, zones)
             |
             v
     Mana Resource Extractor
  (produced mana, land moves,
   discounts, consumables, untaps)
             |
             v
     Ramp Mechanic Classifier
  (mechanical tags + constraints)
             |
             v
       Ramp Evaluator
  (baseline-relative acceleration,
   speed, persistence, reliability,
   flexibility; no deck required)
             |
             v
        ramp_profile
             |
             v
  Initial Deck Builder Candidate Pool
```

Implement independent services/modules:

- `mana_resource_extractor`: derive typed resource events from normalized Oracle abilities.
- `ramp_mechanic_classifier`: assign non-exclusive mechanical tags and determine whether acceleration is real, conditional, or merely fixing.
- `ramp_evaluator`: evaluate conservative intrinsic and conditional metrics without deck assumptions.
- `ramp_profile_serializer`: persist structured evidence and scores.
- `ramp_rules_catalog`: versioned patterns, semantic templates, and exceptions.

Reuse existing `oracle_keywords`, `mechanical_tags`, `strategy_tags`, and `card_roles` rather than collapsing them into one field.

## 3. Mechanical taxonomy

| Tag | Detection intent | Typical example | Important caveat |
|---|---|---|---|
| `land_tutor_to_battlefield` | Search for a land and place it onto battlefield | Nature's Lore | Check tapped state and timing |
| `land_put_from_hand` | Put additional land from hand onto battlefield | Sakura-Tribe Scout | Requires land in hand |
| `extra_land_drop` | Permit additional land plays | Exploration | Does not create lands in hand |
| `mana_rock` | Artifact with reusable mana ability | Arcane Signet | Check activation costs and restrictions |
| `mana_dork` | Creature with reusable mana ability | Llanowar Elves | Usually summoning sickness |
| `mana_enchantment` | Enchantment producing or enhancing mana | Wild Growth | May require enchanted source |
| `mana_multiplier` | Increase existing mana production | Mana Reflection | Depends on other sources |
| `mana_ritual` | One-shot burst of mana | Dark Ritual | Usually expires at step/phase boundary |
| `treasure_generation` | Create Treasure tokens | Big Score | Treasures are bankable but consumable |
| `other_mana_token` | Create Gold, Powerstone, or similar | Various | Token-specific mana restrictions |
| `mana_cost_reduction` | Reduce mana cost of eligible spells/abilities | Goblin Electromancer | Not literal mana production |
| `mana_untap` | Untap mana-producing resources | Kiora's Follower | Requires usable source |
| `mana_refund` | Produce/refund mana on an event | Birgi, God of Storytelling | Requires event and usable timing |
| `mana_scaling` | Mana amount scales with a board property | Priest of Titania | Do not assume a board state |
| `mana_fixing` | Change available colors without increasing quantity | Chromatic effects | Independent of ramp |

Mechanical tags can overlap. `ramp` is the strategic role; `mana_fixing` is an independent capability. A basic land is a standard mana source, not ramp merely because it taps for mana.

## 4. Resource model

Represent distinct resource flows instead of treating every mana-related effect identically:

- `mana_added`: amount, color choices, restrictions, timing, duration.
- `mana_spent`: spell/activation costs, additional costs, ongoing costs.
- `mana_source_created`: permanent type, tap requirements, availability delay, repeatability.
- `land_entered_battlefield`: quantity, tapped state, source zone, optionality.
- `land_play_permission`: extra land plays per turn, duration, prerequisites.
- `mana_resource_token_created`: subtype, quantity, bankability, sacrifice/activation cost, restrictions.
- `mana_cost_reduced`: reduction amount, eligible spell/ability scope, conditions.
- `mana_source_untapped`: eligible source type, timing, required source.

Track **gross mana** and **net incremental mana** separately. The evaluator must use a clearly defined reference scenario and avoid claiming an exact gain when source availability or quantities are unknown.

## 5. Oracle parsing rules

### 5.1 Ability segmentation

Parse each ability separately, including activated abilities (`cost: effect`), triggered abilities (`when`, `whenever`, `at`), static abilities, replacement effects, and spell effects. Keep source spans and original Oracle text for auditability.

### 5.2 Mana production

Recognize forms such as:

```text
{T}: Add {G}.
{T}: Add {C}{C}.
Add {B}{B}{B}.
Add one mana of any color.
Add X mana in any combination of colors.
```

Parse mana symbols structurally (`{G}`, `{C}`, `{2}`, `{X}`, hybrid symbols, phyrexian symbols where relevant), not as plain words. Identify activation costs, timing, whether the ability is repeatable, and constraints on spending mana.

### 5.3 Land acceleration

Recognize `search ... land ... put ... onto the battlefield`, `put a land card from your hand onto the battlefield`, and `you may play an additional land`. Distinguish these from searching a land **to hand**, which provides land access/fixing but not immediate acceleration. Preserve `tapped`, `untapped`, `sacrifice`, and `at the beginning of` clauses.

### 5.4 Consumable resources

Recognize Treasure, Gold, Powerstone, and other tokens by **token rules/abilities**, not just the word `token`. Treasure is generally bankable, consumable, and capable of producing one mana of any color; Powerstone mana has spending restrictions. Do not assume all tokens create mana.

### 5.5 Cost reduction and untapping

Extract cost-reduction scope (`creature spells`, `instants and sorceries`, `artifact spells`, etc.), quantity, and conditions. Extract untap targets and require a compatible mana source before treating untapping as realized ramp. Discounts and untapping must not be added to raw mana-production totals.

### 5.6 Negation and conditionality

Handle `may`, `if`, `unless`, `only`, `as long as`, `for each`, `up to`, `instead`, and opponent choices. Record uncertainty explicitly. Use longest-phrase matching and canonical singular/plural forms, but never infer an effect from an isolated keyword without its action, object, and scope.

## 6. Classification rules

1. **Reusable additional mana source:** classify as `ramp` if a card can create or function as an additional mana source beyond normal land development, subject to cost and timing.
2. **Additional land development:** classify as `ramp` when it puts lands onto the battlefield or permits extra land plays; mark prerequisites and immediate vs delayed effect.
3. **One-shot net-positive burst:** classify as `ramp` if mana produced exceeds the mana required for the effect in a supported baseline; retain `mana_ritual` and low persistence.
4. **Consumable mana resources:** classify as `ramp` with `bankable=true`, `consumable=true`; do not confuse with immediate ritual mana.
5. **Cost reduction:** classify as `functional_ramp` when it can save mana on eligible future casts; preserve scope and do not claim generic mana production.
6. **Multipliers, refunds, and untaps:** classify as `conditional_ramp` until the required mana sources or triggering actions are established.
7. **Fixing only:** if an effect changes colors but does not increase available mana or advance development, classify `mana_fixing` without positive acceleration.
8. **No acceleration:** a normal basic land or a card that only searches a land to hand should not receive a positive intrinsic ramp result solely for that reason.

Store `is_ramp_capable`, `ramp_classification`, and `requirements` separately. Conditional cards are eligible for candidate pools without pretending their acceleration is guaranteed.

## 7. Persistence and timing

Do **not** create separate strategic categories for temporary versus permanent ramp. Instead, annotate:

```json
{
  "persistence": {
    "mode": "persistent | consumable | one_shot | conditional",
    "bankable": false,
    "repeatable": false,
    "first_use_delay": "none | summoning_sickness | enters_tapped | conditional | unknown"
  }
}
```

Interpretations:

- **Persistent:** recurring production over future turns, but vulnerable to removal.
- **Consumable/bankable:** mana resource survives until spent, such as Treasure.
- **One-shot:** mana is produced during a specific resolution/window, such as Dark Ritual.
- **Conditional:** depends on other sources, spells, permanents, or events.

A single card may have multiple modes; annotate per ability/resource event rather than forcing one card-level mode.

## 8. Scoring without a deck

Compute a **static, context-free ramp profile**. Do not calculate exact expected mana per turn from unknown deck state. Use separate normalized dimensions (0–1), with configurable versioned weights:

- `acceleration`: net additional usable mana or land development in a defined reference case.
- `persistence`: ability to keep providing mana after first use.
- `speed`: how soon acceleration becomes usable relative to cost/timing.
- `reliability`: independence from board state, targets, opponent choices, and additional cards.
- `flexibility`: color options and restrictions on paying costs.

Illustrative formula:

```text
base_ramp_score = 100 * (
  w_acceleration * acceleration +
  w_persistence  * persistence  +
  w_speed        * speed        +
  w_reliability  * reliability  +
  w_flexibility  * flexibility
)

sum(weights) = 1
```

**Do not hardcode example weights as ground truth.** Keep dimensions visible for calibration. For unknown quantities (`X`, `for each`, board-dependent mana), use `null`, ranges, or a conditional expression; never silently substitute a favorable board state. Maintain `score_confidence` and `score_assumptions`.

### 8.1 Reference-case evaluation

For one-shot spells, net burst mana may be directly computable:

```text
Dark Ritual: produces {B}{B}{B}, costs {B}
net immediate burst = +2 black mana
```

For persistent permanents, preserve the initial investment and recurring production as separate values:

```text
Arcane Signet: mana investment = 2
mana per successful activation = 1
repeatable = true
first usable turn = turn cast (if untapped and usable)
```

Do not claim a single unconditional net mana total for a permanent without a time horizon and activation assumptions.

### 8.2 Optional commander context

At build-from-scratch time, the commander and intended strategy **may** be known even though the deck does not exist. Permit optional, explainable compatibility metadata (`lands_matter`, `artifacts`, `spellslinger`, `combo`, etc.), but **do not** infer payoff density or calculate deck-dependent synergy scores. Keep intrinsic score and commander/strategy compatibility distinct.

## 9. Canonical output schema

```json
{
  "card_id": "example-card-id",
  "oracle_parser_version": "1.0.0",
  "ramp_rules_version": "1.0.0",
  "mechanical_tags": ["treasure_generation"],
  "strategy_tags": ["ramp"],
  "card_roles": ["mana_accelerator"],
  "ramp_profile": {
    "is_ramp_capable": true,
    "classification": "consumable_ramp",
    "mechanisms": ["mana_resource_token_generation"],
    "resource_events": [
      {
        "action": "create_token",
        "object": "treasure",
        "quantity": {"kind": "fixed", "value": 3},
        "mana_per_token": 1,
        "color_options": ["any"],
        "restrictions": [],
        "persistence": {
          "mode": "consumable",
          "bankable": true,
          "repeatable": false,
          "first_use_delay": "none"
        },
        "requirements": [],
        "evidence": {"ability_index": 0, "oracle_span": "Create three Treasure tokens."}
      }
    ],
    "scores": {
      "acceleration": null,
      "persistence": null,
      "speed": null,
      "reliability": null,
      "flexibility": null,
      "base_ramp_score": null,
      "score_confidence": "not_evaluated"
    },
    "assumptions": [],
    "strategy_compatibility": ["artifacts", "tokens"]
  }
}
```

This is a **schema illustration**, not a claim about an actual card named `example-card-id`. Null scores mean the scoring step has not run. In production, distinguish `unknown`, `not_applicable`, and `not_evaluated` if your storage layer supports tagged missing values.

## 10. Worked fixtures

### Dark Ritual

```json
{
  "card": "Dark Ritual",
  "tags": ["mana_ritual"],
  "classification": "burst_ramp",
  "gross_mana": {"B": 3},
  "mana_cost": {"B": 1},
  "net_immediate_mana": {"B": 2},
  "persistence": "one_shot",
  "bankable": false
}
```

### Nature's Lore

```json
{
  "card": "Nature's Lore",
  "tags": ["land_tutor_to_battlefield"],
  "classification": "land_ramp",
  "land_count": 1,
  "source_zone": "library",
  "destination_zone": "battlefield",
  "land_filter": "Forest card",
  "enters_tapped_by_effect": false,
  "persistence": "persistent"
}
```

The fetched Forest may enter tapped due to its own characteristics or other effects; `enters_tapped_by_effect=false` does not guarantee untapped entry.

### Arcane Signet

```json
{
  "card": "Arcane Signet",
  "tags": ["mana_rock"],
  "classification": "persistent_ramp",
  "mana_investment": 2,
  "mana_per_activation": 1,
  "color_rule": "one color in commander color identity",
  "repeatable": true,
  "first_use_delay": "none",
  "persistence": "persistent"
}
```

### Exploration

```json
{
  "card": "Exploration",
  "tags": ["extra_land_drop"],
  "classification": "conditional_land_ramp",
  "additional_land_plays_per_turn": 1,
  "requirements": ["land_available_to_play"],
  "persistence": "persistent"
}
```

### Goblin Electromancer

```json
{
  "card": "Goblin Electromancer",
  "tags": ["mana_cost_reduction"],
  "classification": "functional_ramp",
  "generic_mana_discount": 1,
  "eligible_spells": ["instant", "sorcery"],
  "requires_eligible_spell": true,
  "direct_mana_production": 0
}
```

## 11. False positives and edge cases

| Input case | Expected behavior |
|---|---|
| Basic land taps for mana | `standard_mana_source`, not automatic ramp |
| Search a land into hand | `land_access` / fixing, no immediate ramp |
| Search a land onto battlefield tapped | Ramp, delayed availability |
| Treasure created only on death | Conditional consumable ramp |
| Mana ability with activation cost | Net cost and production tracked separately |
| Mana dork with tap ability | Summoning sickness modeled |
| Mana restricted to creature spells | Restriction retained |
| Powerstone token | Spending restriction retained |
| Cost reduction on eligible spells | Functional ramp, not mana produced |
| Untap a permanent | Conditional until compatible mana source exists |
| Mana multiplier | Conditional until mana source exists |
| Ritual produces less than or equal to its cost | No positive net burst from that event alone |
| Landfall creates Treasure | Triggered conditional ramp, not guaranteed |
| Treasure also triggers artifact synergies | One mana resource event; do not double count |
| A land enters tapped due to its own text | Resolve with metadata, not search-spell text alone |
| Mana generation with `may` or opponent choice | Optional/uncertain, not guaranteed |

## 12. Implementation pseudocode

```python
def build_ramp_profile(card, parsed_oracle, rules_catalog):
    events = []
    for ability in parsed_oracle.abilities:
        events.extend(extract_mana_resource_events(ability, card, rules_catalog))

    events = normalize_events(events)
    events = attach_requirements_restrictions_and_evidence(events)
    events = deduplicate_same_effect(events)

    mechanisms = classify_ramp_mechanisms(events, card)
    classification = classify_acceleration(events, mechanisms, card)
    dimensions = evaluate_static_dimensions(
        events=events,
        classification=classification,
        reference_scenarios=rules_catalog.reference_scenarios,
    )

    return RampProfile(
        card_id=card.id,
        mechanisms=mechanisms,
        classification=classification,
        resource_events=events,
        scores=dimensions,
        rule_version=rules_catalog.version,
    )
```

Evaluation should be deterministic for a given Oracle version, rules version, and metadata snapshot. Unknown cases should be reported rather than hallucinated or coerced into a confident positive score.

## 13. Testing strategy

1. **Parser tests:** symbols, pluralization, nested clauses, multiple abilities, conditionals, costs, tapped/untapped states, source/destination zones.
2. **Classification fixtures:** Dark Ritual, Seething Song, Sol Ring, Arcane Signet, Llanowar Elves, Nature's Lore, Cultivate, Exploration, Sakura-Tribe Scout, Wild Growth, Mana Reflection, Big Score, Goblin Electromancer, Kiora's Follower, Priest of Titania.
3. **Negative fixtures:** basic lands, land-to-hand-only search, fixing-only effects, non-mana tokens, mana-neutral rituals.
4. **Interaction fixtures:** conditional untap, mana multiplier without source, cost reduction with no eligible spell, triggered Treasure creation.
5. **Snapshot tests:** ensure stable normalized JSON and explicit Oracle evidence across catalog versions.
6. **Regression tests:** previously misclassified cards and ambiguous Oracle patterns.

Use a curated, manually labeled validation set. Measure precision and recall for `ramp_capable`, each mechanical tag, and `conditional` vs `intrinsic` classification separately. Inspect false positives first because they can pollute initial deck construction.

## 14. Implementation phases

### Phase 1 — Resource event extraction
- Define resource event schema and versioned rules catalog.
- Integrate Oracle ability segmentation and mana symbol parsing.
- Implement mana production, land-to-battlefield, extra land plays, and Treasure creation.

### Phase 2 — Mechanic classification
- Add mana rocks/dorks/enchantments, rituals, discounts, untaps, multipliers, refunds.
- Attach costs, prerequisites, scopes, restrictions, and evidence.
- Distinguish ramp from fixing and standard mana sources.

### Phase 3 — Static evaluation
- Implement conservative reference cases and independent scoring dimensions.
- Record one-shot vs bankable vs persistent behavior.
- Handle unknown and conditional quantities explicitly.

### Phase 4 — Build-from-scratch integration
- Expose `ramp_profile` to candidate filtering and commander/strategy compatibility.
- Keep selection independent from deck-list statistics.
- Add rule-versioned cache and regression fixtures.

### Future phase — Deck refinement (not now)
- Evaluate actual land count, mana curve, card density, and color requirements.
- Simulate opening hands and draw sequences.
- Estimate commander cast turn and probability of hitting mana milestones.
- Re-rank ramp options based on the built deck's real needs.

## 15. Acceptance criteria

- [ ] Produces a structured `ramp_profile` from Oracle text and metadata without requiring a deck list.
- [ ] Recognizes land ramp, mana rocks, dorks, enchantments, rituals, Treasures, discounts, untaps, multipliers, and conditional generation.
- [ ] Distinguishes mana production, mana fixing, land access, and cost reduction.
- [ ] Separates mechanical tags from strategic role `ramp`.
- [ ] Captures costs, quantities, restrictions, conditions, timing, and repeatability.
- [ ] Models persistent, consumable/bankable, and one-shot effects through score dimensions, not separate strategic categories.
- [ ] Avoids treating unknown board state as guaranteed mana gain.
- [ ] Avoids double counting mana and associated artifact/token synergies.
- [ ] Exposes evidence spans and catalog/parser versions for every classification.
- [ ] Passes curated positive, negative, and edge-case fixtures.
- [ ] Does not depend on mana-curve simulation or an existing deck.

## 16. Design decisions summary

1. **One strategy category:** `ramp`; detailed mechanical tags describe how acceleration works.
2. **Temporary vs persistent is a scoring dimension:** not a separate strategic category.
3. **Resource events before scores:** preserve actual mechanics and constraints.
4. **Context-free first:** score only what can be inferred without an assembled deck.
5. **Optional commander/strategy metadata:** compatibility is allowed; deck-dependent payoff estimates are not.
6. **Deterministic, explainable, versioned:** every output must be reproducible and auditable.
7. **Deck refinement deferred:** simulations belong to a later, distinct module.
