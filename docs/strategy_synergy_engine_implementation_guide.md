# Strategy Synergy Engine — Implementation Guide

**Project:** Deterministic Magic: The Gathering Commander Deck Builder\
**Status:** MVP implementation specification\
**Language:** English\
**Scope:** Evaluate owned-card candidates against a commander and one or more selected strategies, *before* deck construction. Do not assume a decklist exists.

## 1. Goals and non-goals

### Goals

- Identify mechanical capabilities from structured Oracle-text parsing, not merely keyword occurrence.
- Model all 15 existing strategies as configurable objectives and capability relationships.
- Evaluate one card against multiple strategies and against the commander's actual abilities.
- Identify complementary **enabler → payoff**, **generator → consumer**, **amplifier → event**, and **converter → resource** relationships.
- Produce deterministic, reproducible, explainable synergy evidence and scores.
- Preserve compatibility with independent Ramp, Card Advantage, Spot Interaction, and Mass Interaction engines.
- Support additional strategies by adding catalog definitions and rules rather than modifying parser logic.
- Reject illegal or impossible interactions and expose uncertain interactions explicitly.

### Non-goals for the MVP

- No deck curve, land-count, role quotas, probability of drawing combinations, or completed-deck simulation.
- No optimization based on a decklist, metagame, opponents, or playtesting.
- No automatic inference that a pair of cards forms an infinite combo.
- No LLM required in the deterministic evaluation path; an LLM may assist offline catalog authoring subject to review.

## 2. Inputs, outputs, and boundaries

**Inputs:** commander card data; selected strategy IDs and optional priority weights; candidate card data limited to the user's collection; normalized Oracle ability AST / mechanical profiles; strategy catalog; mechanical capability catalog; interaction rule catalog; Commander legality and color identity metadata.

**Outputs:** a `strategy_synergy_profile` per candidate, including individual strategy affinities, direct commander synergy, cross-strategy bridges, roles, dependencies, evidence, rule IDs, score components, confidence/coverage, and rejection reasons.

**Responsibilities:**

1. `oracle_parser`: extract costs, effects, events, targets, quantities, modes, restrictions, zones, controller/owner, replacement effects, and references.
2. `mechanical_capability_extractor`: normalize the parser's facts into typed capabilities.
3. `strategy_catalog`: declare goals, useful capabilities, weights, roles, and valid interactions.
4. `mechanical_interaction_engine`: match compatible producers, consumers, triggers, modifiers, and events.
5. `strategy_synergy_evaluator`: compute commander-specific and strategy-specific evidence and scores.
6. `candidate_evaluator`: later combine the independent synergy, ramp, card-advantage, and interaction outputs without overwriting them.

The synergy engine must not infer that `oracle_keywords` alone imply strategic fit.

## 3. Core domain model

### 3.1 Separate four layers

- `oracle_keywords`: exact or normalized lexical matches (e.g., `whenever`, `token`, `sacrifice`).
- `mechanical_capabilities`: typed, contextual actions and events (e.g., `create_creature_token`, `sacrifice_creature_as_cost`, `on_creature_dies_draw`).
- `strategy_affinities`: how a capability supports a particular strategy's goal.
- `card_roles`: functional role within a particular strategy (`generator`, `enabler`, `payoff`, `amplifier`, `converter`, `finisher`).

Do not treat these layers as interchangeable. A card can hold multiple capabilities, roles, and strategy affinities.

### 3.2 Capability record

```json
{
  "capability_id": "create_creature_token",
  "role_hints": ["generator"],
  "source_ability_id": "ability_1",
  "action": "create",
  "object": {"kind": "token", "types": ["creature"]},
  "quantity": {"kind": "fixed", "value": 2},
  "event": {"kind": "on_resolution"},
  "source_zone": null,
  "destination_zone": "battlefield",
  "controller": "you",
  "targeting": {"kind": "none"},
  "costs": [],
  "conditions": [],
  "timing": "sorcery_speed",
  "repeatability": "single_use",
  "evidence": {"oracle_span": "Create two 1/1 creature tokens.", "start": 0, "end": 31}
}
```

The example is a schema illustration, not a quotation of a specific card. In production, offsets must refer to the original ability text and be verified against the actual string.

**Required capability attributes:** action, event, object and subtype, quantity, zone transitions, controller/owner, targets, costs, conditions, timing, repeatability, effect duration, modality, and provenance. Support `unknown` instead of guessing missing fields.

### 3.3 Normalization and ontology

Use canonical identifiers with stable versions. Examples:

- `create_creature_token`, `create_treasure_token`, `create_artifact_token`
- `sacrifice_creature`, `sacrifice_permanent`, `creature_dies`
- `on_creature_dies_drain`, `on_permanent_sacrificed_draw`
- `put_plus_one_counter`, `double_counters`, `counter_count_payoff`
- `land_enters`, `extra_land_play`, `landfall_payoff`
- `cast_instant_or_sorcery`, `copy_spell`, `spell_cast_payoff`
- `blink_permanent`, `etb_draw`, `etb_token_generation`
- `artifact_enters`, `artifact_count_payoff`, `artifact_sacrifice_outlet`
- `gain_life`, `lifegain_payoff`, `deal_noncombat_damage`
- `attach_equipment`, `aura_buff`, `single_creature_combat_payoff`
- `graveyard_to_battlefield`, `graveyard_to_hand`, `self_mill`

Subtyping matters: Treasure is an artifact token but not a creature token; token creation and putting an existing permanent onto the battlefield are different events; a creature dying requires a battlefield-to-graveyard move (tokens can die before ceasing to exist).

## 4. Strategy catalog: initial 15 themes

Keep the user's existing strategy IDs if they differ; use migration aliases rather than silently renaming persisted records.

| Strategy ID | Goal | High-signal capability families | Typical roles |
|---|---|---|---|
| `tokens` | Create and exploit tokens | token creation, token ETB/count payoffs, token doublers, go-wide buffs | generator, payoff, amplifier |
| `plus_one_counters` | Place and exploit +1/+1 counters | counter placement, proliferation, counter doubling, counter payoffs | generator, amplifier, payoff |
| `combo` | Assemble rule-valid repeatable/decisive interactions | resource loops, untap, recursion, copy, cost reduction | enabler, converter, payoff |
| `artifacts` | Generate and exploit artifact permanents | artifact ETB, artifact count, artifact sacrifice, artifact recursion | generator, payoff, converter |
| `aggro` | Apply early combat pressure | efficient threats, haste, combat buffs, evasion, extra combat | generator, amplifier, finisher |
| `reanimator` | Reuse creatures/permanents from graveyard | graveyard setup, reanimation, ETB/death payoffs | enabler, converter, payoff |
| `spellslinger` | Repeatedly cast instants/sorceries | cast triggers, spell copying, spell refunds, cost reduction | enabler, payoff, amplifier |
| `lifegain` | Gain life and exploit lifegain events | lifegain, lifegain triggers, life-based scaling | generator, payoff |
| `control` | Constrain threats and win long games | counters, removal, disruption, protection, repeatable answers | enabler, payoff |
| `aristocrats` | Convert sacrifices and deaths into value | sacrifice outlets, fodder, death/sacrifice payoffs, recursion | generator, enabler, payoff |
| `burn` | Deal direct/noncombat damage | damage spells, damage triggers, damage amplification | generator, amplifier, finisher |
| `ramp` | Accelerate available mana/development | land acceleration, rocks/dorks, rituals, treasure, discounts | generator, enabler |
| `lands_matter` | Reward lands and land movement | landfall, extra land drops, land recursion, land count | generator, enabler, payoff |
| `voltron` | Concentrate power/protection on a primary attacker | equipment, auras, buffs, protection, evasion, commander damage | enabler, amplifier, finisher |
| `midrange` | Generate resilient incremental value | recurring value, efficient threats, flexible answers, recursion | generator, payoff, converter |

**Important:** `control`, `ramp`, `aggro`, `midrange`, and `combo` are broad game plans; do not mark every generic removal, mana source, or creature as a high-synergy card. Demand strategy-specific evidence for a strong score.

### 4.1 Strategy definition schema

```json
{
  "id": "aristocrats",
  "version": "1.0.0",
  "objective": "Convert creature sacrifices and deaths into repeatable value",
  "capability_preferences": [
    {"capability": "sacrifice_creature", "role": "enabler", "weight": 0.9},
    {"capability": "create_creature_token", "role": "generator", "weight": 0.6},
    {"capability": "on_creature_dies_drain", "role": "payoff", "weight": 1.0}
  ],
  "interaction_rule_ids": ["sacrifice_triggers_death_payoff", "fodder_feeds_sacrifice_outlet"],
  "exclusions": ["discard_is_not_sacrifice"],
  "notes": "Capabilities alone may require other pieces to realize value"
}
```

Weights are **configuration placeholders** until calibrated against a labeled benchmark; do not present them as empirical measurements.

## 5. Mechanical interaction rule catalog

Define directional, typed rules with requirements and explicit effects. A rule must reference capability IDs and not free-text keyword coincidences.

```json
{
  "id": "sacrifice_triggers_death_payoff",
  "version": "1.0.0",
  "source_capability": "sacrifice_creature",
  "target_capability": "on_creature_dies_drain",
  "relation": "enables",
  "constraints": [
    "sacrificed_object_is_creature",
    "object_moves_from_battlefield_to_graveyard",
    "trigger_observes_event",
    "required_controllers_match"
  ],
  "strategy_ids": ["aristocrats"],
  "evidence_weight": 1.0
}
```

**Core relationship types:** `enables`, `rewards`, `amplifies`, `converts`, `protects`, `recurs`, `shares_resource`, `directly_triggers`. Store direction and the triggering event explicitly.

### 5.1 Starter rules

| Rule ID | Enabler / source | Payoff / target | Key constraint |
|---|---|---|---|
| `sacrifice_triggers_death_payoff` | sacrifice creature | when creature dies | actual death event and controller compatibility |
| `fodder_feeds_sacrifice_outlet` | create creature tokens | sacrifice creature | created objects are legal sacrifice resources |
| `treasure_feeds_artifact_sacrifice` | create Treasure | artifact sacrifice payoff | artifact is actually sacrificed |
| `token_multiplier_amplifies_creation` | token multiplier | create token | **create** event under correct controller |
| `counter_multiplier_amplifies_placement` | counter multiplier | put counters | placement and affected permanent satisfy conditions |
| `blink_retriggers_etb` | blink | ETB payoff | legal object, leaves/returns, ETB actually triggers |
| `recursion_reuses_etb` | graveyard to battlefield | ETB payoff | legal reanimation target and zone transition |
| `extra_land_play_enables_landfall` | extra land play | landfall payoff | actual land entry; available land card not assumed |
| `spell_copy_amplifies_spell_effect` | copy spell | eligible spell effect | legal copy target and correct effect semantics |
| `lifegain_triggers_payoff` | gain life | lifegain trigger | qualifying life-gain event and player |
| `equipment_enables_voltron` | equipment attachment | single-attacker payoff | legal equip target, costs and timing |
| `artifact_etb_triggers_payoff` | create artifact token | artifact enters payoff | token enters under qualifying controller |

Do not assume an enabler alone generates value; if a payoff is absent from the commander or the candidate card, represent a **strategy-level potential** with unmet dependencies, not a proven two-card interaction.

### 5.2 Negative and ambiguous matches

- `sacrifice` is not `discard`; `exile` is not `dies`.
- `target` is not equivalent to `each` or `all`.
- A token doubler does not amplify a token merely *entering* the battlefield without being created.
- A death payoff may require *another* creature or a creature *you control*; preserve those restrictions.
- A spell-copy payoff might require *casting* a spell; copying a spell does not cast it unless explicitly stated.
- Blink can fail on illegal targets, tokens (which generally cannot return after leaving), or effects that do not trigger on return.
- Treasure sacrifice is an artifact sacrifice, but not a creature death.
- A triggered ability that says `may` must not be counted as guaranteed.
- Generic interaction/ramp should not automatically receive high affinity for every commander.
- The word `combo` is not evidence of an infinite loop; require a validated cycle with resource accounting.

## 6. Commander profile and multi-strategy matching

The commander is both a **mechanical partner** and a **strategy selector**. Extract its capabilities using the same pipeline as candidates. Do not replace its abilities with broad labels alone.

```json
{
  "commander_id": "commander_example",
  "selected_strategies": [
    {"id": "tokens", "priority": 0.7},
    {"id": "aristocrats", "priority": 1.0},
    {"id": "combo", "priority": 0.3}
  ],
  "capabilities": [
    {"capability_id": "on_permanent_sacrificed_draw", "source_ability_id": "commander_ability_2"}
  ]
}
```

Selected strategies may be manually chosen or determined by a separate commander strategy classifier. The synergy engine consumes them as input and does not silently change user selection.

### 6.1 Three distinct match classes

1. **Direct commander synergy:** candidate ability produces an event/resource that the commander consumes or rewards, or vice versa, with validated constraints.
2. **Strategy affinity:** candidate contributes a useful capability to an explicitly selected strategy, even if the commander does not directly reference it.
3. **Cross-strategy bridge:** one underlying capability supports distinct goals across two selected strategies (e.g., Treasure generation supports Artifacts and Aristocrats via different valid rules).

Never require a card to match every selected strategy. A strong specialist may be better than a weak generalist.

### 6.2 Example: Korvold-style sacrifice commander

If a commander draws when **you sacrifice a permanent**, a Treasure generator provides potential fuel, but generating the Treasure does not itself trigger the commander. The evidence chain is:

`create Treasure → Treasure exists → sacrifice Treasure (additional action) → commander sacrifice trigger → draw`

The intermediate sacrifice must be explicitly present in a capability, intrinsic Treasure ability, or commander ability; record it as a dependency rather than skipping the step.

## 7. Deterministic evaluation algorithm

```python
def evaluate_candidate(commander, candidate, strategies, catalogs):
    assert candidate.is_owned
    if not commander_legal(candidate, commander):
        return rejected_profile(candidate, "commander_legality")

    commander_caps = extract_capabilities(commander)
    candidate_caps = extract_capabilities(candidate)

    direct = match_rules(candidate_caps, commander_caps, catalogs.rules)
    reverse = match_rules(commander_caps, candidate_caps, catalogs.rules)
    affinity = match_strategy_capabilities(candidate_caps, strategies, catalogs.strategies)
    bridges = find_cross_strategy_bridges(candidate_caps, affinity, catalogs.rules)

    evidence = validate_constraints(direct + reverse + affinity + bridges)
    evidence = deduplicate_by_mechanical_event_and_rule(evidence)

    return score_and_explain(candidate, evidence, strategies, catalogs.scoring)
```

`match_rules` must not invent a second card or assume a missing capability exists. A strategy-level rule may yield `potential` with explicit `missing_capabilities` rather than `validated`.

### 7.1 Suggested evaluation stages

1. Check owned-card eligibility, format legality, banned list, and commander color identity using authoritative metadata (keep policy versioned).
2. Load cached normalized capability profiles for commander and candidate.
3. Match intrinsic strategy capabilities and assign role candidates.
4. Match candidate ↔ commander rules in both directions.
5. Discover cross-strategy bridges from the **same validated evidence**, not by naive score addition.
6. Validate constraints and classify evidence as `validated`, `conditional`, `potential`, `invalid`, or `unknown`.
7. Deduplicate shared mechanical events and produce a score with explanations.
8. Store catalog versions and provenance for reproducibility.

## 8. Scoring design

Keep **scores separate from facts**. Recommended outputs:

- `direct_commander_synergy`: validated interactions with commander.
- `strategy_affinity`: per-strategy contribution.
- `cross_strategy_bridge`: distinct strategy goals supported by a capability.
- `reliability`: constraints, costs, timing, and dependency burden.
- `coverage_confidence`: extraction completeness and unresolved semantics (not a substitute for strength).

A configurable initial formula:

```text
raw_score = 0.40 * direct_commander_synergy
          + 0.35 * weighted_strategy_affinity
          + 0.15 * cross_strategy_bridge
          + 0.10 * interaction_reliability
```

Normalize each component to `[0, 1]`, then scale to `[0, 100]` if desired. **These weights are MVP defaults for testing only**, not validated rankings. Keep all raw components in output.

### 8.1 Aggregating multiple strategies

- Normalize user-supplied strategy priorities to a sum of 1.
- Compute each strategy's affinity independently from its supported evidence.
- Deduplicate underlying events/capabilities before assigning any cross-strategy bonus.
- Cap bridge bonuses and only award them when *different strategic objectives* are genuinely served.
- Avoid averaging in a way that punishes specialist cards for not matching every selected strategy: use a weighted top-contribution approach or configurable blend of weighted max and weighted mean.
- Do not add Ramp/Card Advantage/Interaction scores here; that belongs to a separate candidate-ranking stage.

Example aggregator:

```python
def aggregate_affinity(scores, normalized_priorities, specialist_bias=0.65):
    weighted = [scores[s] * normalized_priorities[s] for s in scores]
    weighted_mean = sum(weighted)
    top = max(scores[s] for s in scores) if scores else 0.0
    return specialist_bias * top + (1 - specialist_bias) * weighted_mean
```

Treat this as a configurable heuristic; compare it against labeled test cases. Explicitly label `potential` synergies and apply a dependency discount rather than presenting them as realized value.

## 9. Output schema

```json
{
  "card_id": "candidate_123",
  "commander_id": "commander_456",
  "engine_version": "1.0.0",
  "catalog_versions": {
    "capabilities": "1.0.0",
    "strategies": "1.0.0",
    "interaction_rules": "1.0.0"
  },
  "eligible": true,
  "mechanical_capabilities": ["create_treasure_token"],
  "strategy_affinities": [
    {
      "strategy_id": "artifacts",
      "score": 0.7,
      "roles": ["generator"],
      "evidence_ids": ["e1"]
    },
    {
      "strategy_id": "aristocrats",
      "score": 0.5,
      "roles": ["generator", "enabler"],
      "evidence_ids": ["e2"]
    }
  ],
  "commander_synergy": {
    "score": 0.6,
    "evidence_ids": ["e2"]
  },
  "cross_strategy_bridges": [
    {"strategies": ["artifacts", "aristocrats"], "evidence_ids": ["e1", "e2"]}
  ],
  "evidence": [
    {
      "id": "e1",
      "status": "validated",
      "rule_id": "artifact_etb_triggers_payoff",
      "source_ability_id": "ability_1",
      "explanation": "Produces an artifact token that can satisfy artifact-entry payoffs when present."
    },
    {
      "id": "e2",
      "status": "conditional",
      "rule_id": "treasure_feeds_artifact_sacrifice",
      "source_ability_id": "ability_1",
      "missing_capabilities": ["artifact_sacrifice_payoff"],
      "explanation": "Treasure can be sacrificed, but the desired payoff must be available."
    }
  ],
  "scores": {
    "direct_commander_synergy": 0.6,
    "weighted_strategy_affinity": 0.65,
    "cross_strategy_bridge": 0.4,
    "interaction_reliability": 0.8,
    "overall": 60.75
  },
  "unknowns": []
}
```

**Schema note:** The example values are illustrative. A rule must not be marked `validated` unless both sides of the relevant interaction are actually present; if a payoff is merely hypothetical, its evidence status must be `potential`/`conditional`, not `validated`. Calculate `overall` programmatically from the chosen weights rather than trusting example numbers.

## 10. Integration with existing engines

Each engine consumes the shared normalized card profile and produces a separate result:

```json
{
  "card_id": "candidate_123",
  "evaluations": {
    "ramp": {"is_ramp": true, "score": null},
    "card_advantage": {"classification": "conditional", "score": null},
    "interaction": {"spot": false, "mass": false},
    "strategy_synergy": {"overall_score": 60.75, "profile_ref": "synergy_profile_123"}
  }
}
```

Do not double-count a Treasure as three separate mana sources because it is simultaneously a token, artifact, and sacrificeable permanent. Its single underlying resource event can support multiple **explanations**, but resource quantity must be accounted for once. A later candidate-ranking module can blend synergy and functional role scores with explicit policy.

## 11. Data layout and interfaces

Suggested project structure:

```text
src/
  oracle/
    parser.py
    capability_extractor.py
  synergy/
    models.py
    capability_catalog.py
    strategy_catalog.py
    interaction_rules.py
    constraint_validator.py
    matcher.py
    scoring.py
    evaluator.py
    explanations.py
  candidate_evaluation/
    aggregator.py
catalogs/
  capabilities.v1.json
  strategies.v1.json
  interaction_rules.v1.json
  scoring_policy.v1.json
tests/
  fixtures/
  test_capabilities.py
  test_interaction_rules.py
  test_commander_matching.py
  test_multi_strategy.py
  test_negative_cases.py
  test_scoring_determinism.py
```

Suggested public interface:

```python
def evaluate_synergy(
    commander_card: Card,
    candidate_card: Card,
    selected_strategies: list[SelectedStrategy],
    context: SynergyCatalogContext,
) -> StrategySynergyProfile:
    ...
```

Batch mode should precompute card capabilities once and cache by Oracle text hash, parser version, and capability-catalog version. Invalidate strategy matches when strategy/rule versions change. Sort all output deterministically and avoid unordered-set-dependent scores.

## 12. Test matrix

| Fixture | Expected behavior |
|---|---|
| Token creator + token-entry payoff | Positive when token type/controller/event match |
| Token doubler + create tokens | Amplification with replacement-effect constraints |
| Token doubler + put existing token onto battlefield | No creation multiplier match |
| Sacrifice outlet + creature-death payoff | Positive when sacrificed creature dies |
| Sacrifice outlet + discard payoff | No direct synergy |
| Treasure creator + artifact-entry payoff | Positive for artifact entry; not creature entry |
| Treasure creator + commander sacrifice trigger | Conditional chain requiring actual Treasure sacrifice |
| Blink + creature ETB draw | Positive if target is legal and ETB retriggers |
| Blink + token creature | Do not claim token returns after leaving |
| Reanimation + ETB payoff | Positive for legal target returned to battlefield |
| Landfall payoff + extra land play | Conditional on land entering |
| Spell copy + on-cast trigger | Copying alone does not count as casting |
| +1/+1 counter placement + counter doubler | Positive if affected permanent meets constraints |
| Lifegain producer + lifegain payoff | Positive if player/event constraints match |
| Generic removal + Voltron commander | Utility, not necessarily strategic synergy |
| Single-strategy specialist vs multi-strategy generalist | Specialist not unfairly diluted |
| Same Treasure event matches Tokens and Artifacts | No duplicated resource quantity |
| Unknown Oracle construction | Explicit unknown, not forced positive |

### 12.1 Acceptance criteria

- Every positive synergy has a rule ID, source ability evidence, and explanation.
- No strategy match depends exclusively on a keyword occurrence without a normalized capability.
- Direct commander matches enforce controller, object, event, zone, timing, and target constraints.
- Unmet dependencies appear explicitly and reduce confidence/realizability.
- Multi-strategy cards retain independent strategy affinities and can be specialists.
- Negative fixtures above never produce false validated synergies.
- Identical inputs and catalog versions yield byte-stable serialized results (excluding intentionally generated IDs/timestamps).
- Adding a strategy requires catalog additions, not a parser rewrite.
- Candidate evaluation operates with commander + candidate + strategies, with **no decklist required**.
- Existing Ramp, Card Advantage, and Interaction profiles remain independent and reusable.

## 13. Phased implementation plan

### Phase 1 — Capability ontology

- Audit existing Oracle parser output and mechanical tags.
- Define 50–100 high-value capabilities covering the 15 strategies as an initial target, not a hard limit.
- Implement typed capability extraction, source evidence, and negative fixtures.

**Deliverable:** versioned capability catalog + extractor tests.

### Phase 2 — Strategy definitions

- Add objectives, role mappings, capability weights, exclusions, and aliases to the existing 15 strategies.
- Keep strategy catalog data-driven and versioned.

**Deliverable:** strategy catalog + schema validation.

### Phase 3 — Interaction graph and constraints

- Implement directional rules and constraint validator.
- Support validated, conditional, potential, invalid, and unknown evidence.
- Start with high-signal pairs: sacrifice/death, token amplification, ETB/blink, artifact ETB, landfall, counters, lifegain, spellslinger.

**Deliverable:** rule catalog + positive/negative pair tests.

### Phase 4 — Commander and multi-strategy evaluator

- Extract commander capabilities; match candidate ↔ commander in both directions.
- Compute per-strategy affinities, cross-strategy bridges, roles, and evidence.
- Add configurable, explainable scoring with deduplication.

**Deliverable:** deterministic `evaluate_synergy` API and snapshot tests.

### Phase 5 — Candidate evaluation integration

- Use owned collection as the candidate universe.
- Enforce legality/color identity before ranking.
- Store independent Ramp/Card Advantage/Interaction/Synergy evaluations.
- Return ranked candidates with explanations and evidence links.

**Deliverable:** candidate-evaluation integration tests and representative Commander fixtures.

## 14. Future work — Deck Refinement Engine (explicitly deferred)

Only after a full deck exists:

- Role quotas, deck balance, curve and land count, mana-color requirements.
- Payoff/enabler density, redundancy, probability of assembling interactions.
- Conditional value realization under actual deck composition.
- Matchup-specific interaction coverage and resilience.
- Monte Carlo draws, turn simulations, and sensitivity analysis.
- Deck-level combo detection with full loop termination/resource checks.

Do **not** introduce these as MVP dependencies.

## 15. Final implementation principles

1. **Parse mechanics, not just words.**
2. **Represent interactions as directed, constraint-checked rules.**
3. **Separate strategy affinity from direct commander synergy.**
4. **Support multiple strategies without requiring every card to match all of them.**
5. **Never invent missing combo pieces or silently assume triggers resolve.**
6. **Preserve evidence, uncertainty, and catalog versions.**
7. **Score candidates before deck construction; refine deck-level fit later.**
8. **Keep functional engines independent and compose their outputs downstream.**
