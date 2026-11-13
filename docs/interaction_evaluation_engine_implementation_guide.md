# Interaction Evaluation Engine — Implementation Guide

**Project:** Magic: The Gathering deterministic / AI-assisted Commander deck builder  
**Module:** `interaction_evaluation_engine`  
**Status:** Implementation specification (v1)  
**Language:** English

This guide specifies the intended architecture and includes illustrative
weights. The implemented Suggestions behavior and current weights are described
in [Synergy scoring](./synergy-scoring.md#collection-integration).

## 1. Purpose and boundaries

Implement a deterministic, explainable classifier that identifies **spot interaction** and **mass interaction** in individual cards using structured Oracle text analysis. The module runs **before deck construction**. It must not require an existing deck, assume an opponent's board state, or simulate metagame threats. Those capabilities belong to a future `deck_refinement_engine`.

Interaction includes reactive or proactive effects that answer, disrupt, suppress, prevent, or protect against threats. **Mechanism** (exile, counter, protection, graveyard hate) and **scope** (spot or mass) are orthogonal. One card may support both scopes in distinct modes.

### Deliverables

- Per-card `interaction_profile` with normalized effects, mechanism tags, roles, scope and conditionality.
- Independent `spot_interaction` and `mass_interaction` flags and scores, with evidence and confidence.
- Declarative pattern catalog and deterministic rules; no LLM required for final classification.
- Unit and regression fixtures, including negative cases and multi-mode spells.

### Out of scope for v1

- Choosing an optimal answer to a specific opposing threat.
- Deck-level interaction counts, redundancy, commander vulnerability, matchup simulation, and meta-aware scores.
- Comprehensive rules-engine execution or precise expected battlefield outcomes.
- Treating every effect that mentions an opponent, creature, or graveyard as interaction.

## 2. Core design principles

1. **Parse abilities/effects, then aggregate to card.** Never classify solely from card-wide keyword presence.
2. **Separate mechanical facts from heuristic evaluations.** Parser outputs should not invent scores.
3. **Target count is not affected-object count.** A single targeted player can have an entire graveyard exiled.
4. **Preserve modes and alternate costs.** Normal Cyclonic Rift and overloaded Cyclonic Rift are separate execution variants.
5. **Preserve conditionality, restrictions, duration and symmetry.** `destroy all creatures` is not guaranteed to remove opposing threats or produce favorable exchanges.
6. **Use explicit `unknown` rather than guessing.** Record evidence spans and rule IDs for auditability.
7. **Never double-count one effect.** Multiple tags describe a single effect; they do not imply multiple independent answers.
8. **Keep scope and role independent.** A mass protection effect is not a board wipe; a fog is not removal.

## 3. Canonical taxonomy

### 3.1 Strategic tags

Only two primary interaction strategy tags:

- `spot_interaction`
- `mass_interaction`

A card can have both tags. `multi_target` is a scope subtype, **not** a third strategic category.

### 3.2 Mechanical tags

| ID | Meaning | Typical scope |
|---|---|---|
| `destroy_removal` | Destroy permanents | spot/mass |
| `exile_removal` | Exile permanents | spot/mass |
| `bounce` | Return permanents to hand | spot/mass |
| `tuck` | Move permanents to library | spot/mass |
| `counterspell` | Counter spells | spot/mass |
| `ability_counter` | Counter activated/triggered abilities | spot/mass |
| `graveyard_hate` | Exile, shuffle or restrict graveyard resources | spot/mass |
| `protection` | Grant protective abilities or prevent removal | spot/mass |
| `damage_prevention` | Prevent damage | spot/mass |
| `fog` | Broadly prevent combat damage | mass |
| `forced_sacrifice` | Force opponents to sacrifice | spot/mass |
| `discard_disruption` | Reduce opponents' hand resources | spot/mass |
| `tap_disable` | Tap, freeze or restrict untapping | spot/mass |
| `combat_interaction` | Alter attacks, blocks or combat outcomes | spot/mass |
| `control_change` | Gain control of objects | spot/mass |
| `stat_reduction` | Reduce power/toughness | spot/mass |
| `rule_restriction` | Prohibit or tax actions | variable |
| `board_wipe` | Mass removal outcome, with mechanism preserved separately | mass |

`board_wipe` is a derived role/tag, not a substitute for `destroy_removal`, `exile_removal`, `stat_reduction`, etc. `fog` is a narrower subtype of `damage_prevention`.

### 3.3 Roles

- `threat_answer`: removes or otherwise neutralizes a threat.
- `disruption`: interferes with an opposing action, hand, spell or resource.
- `protection`: safeguards friendly resources or players.
- `prevention`: prevents damage or another harmful event.
- `suppression`: restricts functionality while a condition persists.

Roles may coexist. Do not equate a role with guaranteed effectiveness.

### 3.4 Scope types

| `scope_type` | Definition | Default strategic mapping |
|---|---|---|
| `single` | One affected object or one spell/ability | spot |
| `multi_target` | Bounded small number of independently selected objects | spot; preserve maximum count |
| `mass` | All/each, an entire zone, or a broad class of affected objects | mass |
| `variable` | X or other scaling quantity, potentially small or broad | conditional mapping |
| `modal` | Multiple selectable modes with potentially different scopes | derive per mode |
| `unknown` | Scope cannot be resolved reliably | no forced mapping |

**Important:** Do not apply a universal numeric threshold such as `>= 2 = mass`. Two targeted creatures are generally `multi_target` spot interaction, while `each opponent sacrifices a creature` is a broad distributed effect. A targeted player's entire graveyard is mass interaction.

## 4. Integration with the existing architecture

```text
Oracle Text / Card Metadata
          |
          v
oracle_parser (ability segmentation, modes, costs, effects)
          |
          v
oracle_semantic_normalizer (action, object, scope, condition)
          |
          v
interaction_mechanic_classifier
          |
          v
interaction_scope_analyzer
          |
          +--------------------+
          |                    |
          v                    v
spot_interaction_evaluator  mass_interaction_evaluator
          |                    |
          +---------+----------+
                    v
            card_interaction_profile
```

Required inputs from existing card data: `oracle_text`, `type_line`, `mana_cost`, `mana_value`, and where available `layout`, `faces`, `keywords`, and timing/ability metadata. The parser must retain the original text and evidence offsets.

Keep `oracle_keywords`, `mechanical_tags`, `strategy_tags`, and `card_roles` as distinct fields.

## 5. Extraction and normalization

### 5.1 Segment Oracle text

- Split paragraphs/ability blocks without breaking a single sentence's conditions or exceptions.
- Recognize activated ability syntax `cost: effect`, triggered abilities (`when`, `whenever`, `at`), static abilities, and replacement/prevention effects.
- Recognize modal choices (`choose one`, `choose two`, `choose one or more`), alternate costs (`overload`, `kicker`), and X-dependent effects.
- Preserve linked clauses such as `unless its controller pays {3}`, `until end of turn`, `you don't control`, and `if` conditions.
- Distinguish reminder text and flavor text from operative rules text where card data exposes them.

### 5.2 Extract semantic dimensions

Each effect should retain:

- `action`: `destroy`, `exile`, `return_to_hand`, `counter_spell`, `counter_ability`, `prevent_damage`, `grant_keyword`, `force_sacrifice`, `discard`, `tap`, `reduce_stats`, etc.
- `object_types`: creature, artifact, enchantment, land, planeswalker, battle, permanent, spell, ability, player, card, damage event.
- `selection`: `target`, `chosen`, `all`, `each`, `non_targeted`, `unknown`.
- `target_count` **separately from** `affected_object_count`.
- `affected_zone`: battlefield, stack, graveyard, hand, etc.
- `controller_filter`: `you_control`, `opponent_controls`, `any`, `you_dont_control`, etc.
- `quantity`: fixed, `up_to`, X, all, each, or unknown.
- `duration`: until end of turn, while source remains, indefinite, next event, etc.
- `conditions`, `additional_costs`, `exceptions`, `restrictions` and `choices`.
- `timing`: instant-speed capability vs sorcery timing (not merely card type).
- `symmetry`: `opponent_only`, `self_only`, `symmetric`, `selective`, `unknown`.

### 5.3 Pattern examples

| Oracle phrase | Normalized meaning |
|---|---|
| `Exile target creature` | exile removal, one creature, spot |
| `Counter target spell` | counterspell, one spell, spot |
| `Counter up to two target spells` | counterspell, bounded multi-target, spot |
| `Destroy all creatures` | mass destroy removal, symmetric |
| `Exile all cards from target player's graveyard` | mass graveyard hate; one player target, all graveyard cards affected |
| `Each opponent sacrifices a creature` | distributed mass forced sacrifice; choice may be made by opponent |
| `Target creature gains hexproof until end of turn` | spot protection, temporary |
| `Permanents you control gain hexproof and indestructible until end of turn` | mass protection, self-only |
| `Prevent all combat damage that would be dealt this turn` | mass fog/prevention |
| `All creatures get -4/-4 until end of turn` | mass stat reduction, **potential** wipe, not guaranteed destruction |

**False-positive guardrails:** `target` alone is not spot; `all` alone is not a board wipe; `exile` alone is not removal (e.g. exiling your own card for impulse draw); `counter` may mean a +1/+1 counter rather than countering a spell. Require action + object + scope + contextual role.

## 6. Deterministic classification rules

Represent rules in a declarative registry. Every match produces an evidence record with `rule_id`, `effect_id`, source span, and normalized fields.

### 6.1 Spot removal

```text
IF action IN {destroy, exile, return_to_hand, tuck}
AND object_type IN {creature, artifact, enchantment, planeswalker, battle, permanent}
AND scope_type IN {single, multi_target}
AND effect is adversarially usable
THEN add mechanical tag and spot_interaction
```

`adversarially usable` means the text permits affecting an opponent's relevant resource, not that an opponent necessarily has one. Own-permanent-only blink/bounce should not automatically count as spot removal.

### 6.2 Counterspells and ability counters

```text
IF action = counter_spell AND object = spell
THEN counterspell + spot/mass according to affected spell set
IF action = counter_ability AND object IN {activated_ability, triggered_ability}
THEN ability_counter + spot/mass according to scope
```

Preserve conditional payments (`unless its controller pays`), spell-type restrictions, and alternative costs.

### 6.3 Protection

```text
IF effect grants hexproof/indestructible/protection/ward,
   regenerates, phases out, or prevents hostile effects
AND beneficiary is a friendly resource/player
THEN protection or prevention role
AND derive spot/mass from beneficiary set
```

Do not assume hexproof protects from non-targeting wipes or indestructible protects from exile, sacrifice, or toughness reduction.

### 6.4 Mass removal / board wipes

```text
IF removal-like action applies to ALL/EACH or a broad unbounded set
THEN mass_interaction
IF action can remove a broad battlefield permanent set
THEN add board_wipe (potential if damage/stat reduction is conditional)
```

Preserve whether the effect is symmetric or asymmetric. A global `-X/-X` is a **potential** wipe, with lethality depending on X and toughness.

### 6.5 Graveyard hate

```text
IF effect exiles/shuffles/restricts cards in graveyards
THEN graveyard_hate
IF one card or a bounded small selection is affected THEN spot
IF entire graveyard(s) or all cards of a broad class are affected THEN mass
```

Separate permanent graveyard restriction (`cards in graveyards can't be targeted`) from one-time exile; these have different roles and durations.

### 6.6 Fog and damage prevention

```text
IF prevents all or broad combat damage in a turn/window
THEN damage_prevention + fog + mass_interaction
ELSE IF prevents damage to one selected object
THEN damage_prevention + spot_interaction
```

A prevention effect can be interaction even if it does not remove anything.

### 6.7 Modal, alternative-cost and scalable effects

- Evaluate each mutually exclusive mode/variant separately.
- Derive card-level tags as a union of **possible** modes, not simultaneous effects.
- Store mode requirements and costs so the scoring system can avoid treating overloaded mass mode as free.
- For X-scaled effects, store `variable` and the supported range/conditions; do not assume a specific X.

## 7. Output schema (v1)

```json
{
  "card_id": "example_card",
  "interaction_profile": {
    "is_interaction": true,
    "strategy_tags": ["spot_interaction"],
    "mechanical_tags": ["exile_removal"],
    "roles": ["threat_answer"],
    "variants": [
      {
        "variant_id": "default",
        "cost": "{W}",
        "scope_type": "single",
        "interaction_types": ["spot"],
        "effects": [
          {
            "effect_id": "e1",
            "action": "exile",
            "object_types": ["creature"],
            "selection": "target",
            "target_count": {"min": 1, "max": 1},
            "affected_object_count": {"min": 1, "max": 1},
            "controller_filter": "any",
            "affected_zone": "battlefield",
            "duration": "indefinite",
            "conditions": [],
            "evidence": {
              "oracle_span": "Exile target creature.",
              "rule_ids": ["SPOT_EXILE_001"]
            }
          }
        ]
      }
    ],
    "scores": {
      "spot": null,
      "mass": null
    },
    "classification_confidence": "high",
    "warnings": []
  }
}
```

The example is schematic. In implementation, validate with JSON Schema or Pydantic and attach stable source spans/offsets. Avoid collapsing multiple independent abilities into a single effect.

## 8. Scoring — independent spot and mass evaluators

Scores are **heuristics**, not Oracle facts. Calculate only after mechanical classification. Use normalized 0–10 dimensions and document the weights. Unresolved inputs should remain `null` rather than defaulting to a favorable value.

### 8.1 Spot scoring dimensions

- `versatility`: eligible threat types and restrictions.
- `efficiency`: mana cost and additional resource costs relative to effect.
- `speed`: instant-speed availability / timing limitations.
- `reliability`: conditional payment, target restrictions, opponent choices, requirements.
- `answer_quality`: exile vs bounce vs temporary suppression, with caveats.
- `duration`: persistence of neutralization.

Illustrative formula (weights are configurable, not canon):

```text
spot_score = 0.20*versatility
           + 0.20*efficiency
           + 0.20*speed
           + 0.20*reliability
           + 0.15*answer_quality
           + 0.05*duration
```

### 8.2 Mass scoring dimensions

- `coverage`: affected classes, zones, and breadth.
- `asymmetry`: opponent-only vs symmetric or self-only effect.
- `scalability`: usefulness across increasing numbers of threats.
- `efficiency`: cost relative to broad effect.
- `speed`: timing.
- `reliability`: restrictions and conditional outcomes.
- `answer_quality`: permanence and completeness of neutralization.

Illustrative formula:

```text
mass_score = 0.20*coverage
           + 0.20*asymmetry
           + 0.15*scalability
           + 0.15*efficiency
           + 0.10*speed
           + 0.10*reliability
           + 0.10*answer_quality
```

**No deck context in v1:** scores describe general capability and intrinsic limitations. Do not score expected targets removed, favorable trades, deck coverage, or metagame relevance. Store per-variant scores; derive an optional card-level maximum for discoverability, clearly labeled as *best available mode*, not cumulative value. Avoid comparing spot and mass scores as if they measured the same property.

## 9. Worked fixtures

### 9.1 Swords to Plowshares

- Oracle effect: exile target creature; controller gains life equal to its power.
- Tags: `spot_interaction`, `exile_removal`, `threat_answer`.
- Scope: `single`; target: creature; timing: instant.
- Preserve life-gain drawback. Exile is not automatically irreversible.

### 9.2 Wrath of God

- Destroy all creatures; they can't be regenerated.
- Tags: `mass_interaction`, `destroy_removal`, `board_wipe`.
- Scope: `mass`; symmetry: `symmetric`; timing: sorcery.
- Do not assert positive card advantage or guaranteed opposing casualties.

### 9.3 Tormod's Crypt

- Target player; exile all cards from that player's graveyard.
- Tags: `mass_interaction`, `graveyard_hate`.
- `target_count = 1 player`; `affected_object_count = all cards in chosen graveyard`.
- The single target does **not** make this spot interaction.

### 9.4 Heroic Intervention

- Permanents you control gain hexproof and indestructible until end of turn.
- Tags: `mass_interaction`, `protection`.
- Role: `protection`; scope: all applicable friendly permanents; temporary.
- Not spot interaction and not a removal spell.

### 9.5 Cyclonic Rift

- **Normal variant:** return target nonland permanent you don't control to owner's hand → `spot_interaction`, `bounce`.
- **Overload variant:** return each nonland permanent you don't control to owner's hand → `mass_interaction`, `bounce`.
- Store both modes with their respective costs and scopes; do not add their effects together.

### 9.6 Austere Command

- Choose two modes from multiple broad destruction modes.
- Store four individual options and the legal `choose_two` combinations; aggregate card-level `mass_interaction`.
- Preserve the selected object classes and potential overlap; do not assume every mode fires.

### 9.7 Farewell

- Choose one or more modes that exile broad sets of artifacts, creatures, enchantments and/or graveyards.
- Store selectable modes and their union/combination rules; tags include `mass_interaction`, `exile_removal`, `graveyard_hate` where relevant.

### 9.8 Fog

- Prevent all combat damage that would be dealt this turn.
- Tags: `mass_interaction`, `damage_prevention`, `fog`.
- Role: `prevention`; temporary; no removal.

### 9.9 Tamiyo's Safekeeping

- Target permanent you control gains hexproof and indestructible until end of turn; you gain 2 life.
- Tags: `spot_interaction`, `protection`; target is one friendly permanent.
- Life gain is a secondary effect, not a second interaction event.

## 10. Pseudocode

```python
def build_interaction_profile(card, oracle_ast, catalog, scoring_config):
    variants = expand_legal_modes_and_alternate_costs(oracle_ast)
    analyzed = []

    for variant in variants:
        effects = []
        for node in variant.effect_nodes:
            facts = normalize_effect(node)
            mechanisms = match_interaction_mechanisms(facts, catalog)
            if not mechanisms:
                continue

            scope = classify_affected_scope(facts)  # not merely target count
            roles = derive_interaction_roles(facts, mechanisms)
            strategic_types = map_scope_to_interaction_types(scope, facts)

            effects.append({
                "facts": facts,
                "mechanisms": mechanisms,
                "scope": scope,
                "roles": roles,
                "interaction_types": strategic_types,
                "evidence": node.source_span,
            })

        analyzed.append({
            "variant": variant,
            "effects": effects,
            "spot_score": evaluate_spot(effects, variant, scoring_config),
            "mass_score": evaluate_mass(effects, variant, scoring_config),
        })

    return aggregate_possible_capabilities_without_double_counting(card, analyzed)
```

## 11. Testing strategy

### 11.1 Unit tests

- Case-insensitive and plural/inflected matching where appropriate.
- `target` versus actual affected set.
- `all`, `each`, `up to`, `X`, `unless`, `may`, `you don't control`.
- Zone-specific effects and own-resource-only effects.
- Activated vs triggered vs static abilities; prevention and replacement effects.
- Alternative costs, modal combinations and mutually exclusive variants.
- Evidence spans, deterministic output and deduplication.

### 11.2 Regression matrix

| Fixture | Expected strategic tags | Critical assertion |
|---|---|---|
| Swords to Plowshares | spot | Single creature exile |
| Counterspell | spot | Spell counter, not +1/+1 counter |
| Tamiyo's Safekeeping | spot | Friendly single-target protection |
| Wrath of God | mass | Symmetric mass destroy |
| Tormod's Crypt | mass | One targeted player, entire graveyard |
| Fog | mass | Broad combat prevention |
| Heroic Intervention | mass | Friendly mass protection |
| Cyclonic Rift | spot + mass | Different legal casting variants |
| Austere Command | mass | Choose-two mode combinations |
| Farewell | mass | One-or-more mode combinations |
| Divination | none | Draw spell is not interaction |
| Cultivate | none | Land ramp is not interaction |
| Ephemerate | none by default | Own-creature blink is not removal |
| Basic land | none | Mana ability is not interaction |

Add fixtures for targeted player discard, one-target graveyard exile, `each opponent` sacrifice, single-target damage, mass damage, global stat reduction, and repeatable restriction effects.

### 11.3 Quality gates

- 100% of regression fixtures classify with the expected tags and variants.
- No known negative fixture is labeled as interaction.
- No mass-graveyard false negative caused by a single player target.
- No duplicated resource/effect counts across overlapping tags or modes.
- Every positive classification includes at least one `rule_id` and Oracle evidence span.
- Re-running the parser on identical inputs yields identical output.
- Unknown/unsupported syntax is surfaced via warnings, not silently misclassified.

## 12. Implementation phases

**Phase 1 — Schema and catalog**  
Define enums, validated schemas, pattern registry, stable rule IDs, and representative fixtures.

**Phase 2 — Oracle AST integration**  
Consume normalized effects; add missing scope, target-vs-affected, mode and duration fields.

**Phase 3 — Deterministic mechanism classifier**  
Implement removal, counters, protection, graveyard hate, fog, forced sacrifice and disruption. Include negative rules.

**Phase 4 — Scope analyzer**  
Implement `single`, `multi_target`, `mass`, `variable`, `modal`, and per-variant aggregation.

**Phase 5 — Scoring**  
Add independent configurable spot/mass evaluators. Keep uncomputed scores null; expose component breakdowns.

**Phase 6 — Validation and observability**  
Run regression corpus, inspect mismatches, log evidence and warnings, version catalog and scoring weights.

## 13. Suggested repository layout

```text
src/
  interaction/
    schemas.py
    catalog.py
    mechanic_classifier.py
    scope_analyzer.py
    spot_evaluator.py
    mass_evaluator.py
    profile_builder.py
    rules/
      interaction_patterns.json
      exclusions.json
    tests/
      fixtures/
        interaction_cards.json
      test_mechanics.py
      test_scope.py
      test_modes.py
      test_scoring.py
      test_regressions.py
```

Adapt filenames to the project's existing stack; the conceptual boundaries matter more than the language.

## 14. Future: Deck Refinement integration (not v1)

Reserve an extension point for later evaluation of:

- Spot/mass interaction ratios and missing answer categories.
- Coverage against opposing threats and local metagame.
- Commander-specific vulnerabilities and protection needs.
- Interaction density, redundancy, mana curve and matchup suitability.
- Contextual evaluation of symmetric wipes and protective effects.

**Do not require a deck object in the current API.** Future context-aware scoring should consume the existing card-level profile rather than re-parse Oracle text.

## 15. Definition of done

The module is complete when a new collection card can be passed through the existing Oracle parser and deterministically receive an explainable interaction profile, with correctly separated mechanisms and scopes, independent spot/mass evaluations, legal mode handling, confidence/warnings, and passing positive/negative regression tests—**without access to a built deck**.
