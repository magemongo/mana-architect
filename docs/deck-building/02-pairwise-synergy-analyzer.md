# Pairwise Synergy Analyzer — Implementation Guide

**Pipeline phase:** 2 · **Status:** implementation specification · **Language:** English

> Read `ARCHITECTURE.md` first. Its versioned contracts and hard invariants take precedence. This guide elaborates the `PairwiseSynergyEdge` contract, not a competing data model.

## 1. Purpose and boundaries

Given an eligible `CandidatePool`, the selected commander configuration, and normalized mechanical capability/effect profiles, produce **rule-verified, directional, explainable interactions** between two cards (including commander-to-candidate interactions). Do not generate a deck, discover arbitrary multi-card combos, or rank cards solely by shared keywords. This phase should preserve the full candidate pool and never mutate inventory.

**Inputs:** `DeckBuildingContext`, `CandidatePool`, normalized abilities and capabilities, strategy relation catalog, rule/config versions. **Outputs:** `PairwiseSynergyAnalysis` containing canonical `PairwiseSynergyEdge[]`, rejection/coverage diagnostics, and provenance. Commander IDs may participate as sources/targets but are not main-deck candidates.

## 2. Integration contracts

The architecture's canonical edge shape is:

```json
{
  "edge_id": "edge_001",
  "source_card_id": "card_a",
  "target_card_id": "card_b",
  "relation": "enables",
  "source_capability": "sacrifice_outlet",
  "target_capability": "creature_death_payoff",
  "prerequisites": ["sacrificed_object_is_creature", "death_event_occurs"],
  "limitations": ["requires_sacrificeable_creature"],
  "evidence": [{"card_id": "card_a", "ability_id": "a1", "rule_id": "sacrifice_death_v1"}],
  "strength": 0.8,
  "confidence": "rule_verified",
  "directional": true
}
```

Keep these required fields. Add optional `schema_version`, `event_ids`, `effect_ids`, `resource_requirements`, `timing_constraints`, `strategy_ids`, `score_components`, `rule_version`, `explanation`, and `validation_status` only via shared-contract evolution. `confidence` describes verification tier, **not** model probability. Stable IDs derive from canonical source/target, relation, rule ID, and supporting ability IDs; do not use array positions.

### Suggested envelope

```json
{
  "schema_version": "1.0",
  "run_id": "run_001",
  "rule_catalog_version": "synergy_rules_v1",
  "edges": [],
  "diagnostics": {"considered_pairs": 0, "candidate_rule_matches": 0, "verified_edges": 0, "rejected_by_reason": {}},
  "provenance": {"collection_snapshot_id": "collection_001", "capability_extractor_version": "v1"}
}
```

## 3. Mechanical compatibility model

Use typed mechanical events, not strings alone. A normalized event should expose `event_type` (`create_token`, `sacrifice`, `dies`, `enters_battlefield`, `cast_spell`, `draw_card`, `put_counter`, etc.), `subject` (types, subtypes, token status), `controller`, `owner`, `zone_from`, `zone_to`, `quantity`, `targeting`, `timing`, `costs`, `restrictions`, `replacement_effects`, and source `ability_id`/`effect_id`.

Define directional relation semantics:

| Relation | Meaning | Example |
|---|---|---|
| `enables` | Produces event/resource another ability needs | Sacrifice outlet → sacrifice-trigger payoff |
| `rewards` | Rewards events another ability generates | ETB payoff → blink effect (record direction explicitly) |
| `amplifies` | Changes magnitude of compatible event | Token doubler → token creation |
| `converts` | Converts one resource into another | Sacrifice creature → mana |

Preserve the rule's declared direction; do not automatically emit the reverse edge. If both directions are valid, emit separately with distinct evidence.

## 4. Declarative rule catalog

Each rule specifies `rule_id`, `version`, source/target capability predicates, typed event join, mandatory conditions, optional constraints, evidence requirements, relation, base weight, and explanation template. Prefer YAML/JSON config with a limited typed predicate vocabulary, not arbitrary `eval` expressions.

```yaml
rule_id: sacrifice_enables_death_payoff
version: 1
relation: enables
source_capability: sacrifice_outlet
target_capability: creature_death_payoff
join:
  produced_event: sacrifice
  implied_event: dies
  object_type: creature
requires:
  - sacrificed_creature_moves_from_battlefield_to_graveyard
  - payoff_observes_matching_controller_and_event
limitations:
  - requires_sacrificeable_creature
base_strength: 0.8
```

**Critical rules:** sacrifice does not always cause a death (replacement exile effects); token creation differs from putting an existing token onto the battlefield; `dies` requires battlefield→graveyard; leaving battlefield is not necessarily dying; damage dealt is not life lost; casting differs from copying; targeting differs from non-targeted effects; spell countering differs from ability countering; `once each turn` and tap costs limit repetition; an activated ability requiring tapping cannot be activated while summoning sick unless the rules allow it.

## 5. Candidate indexing and evaluation

Index by capability and event signature, then join only potentially compatible groups. Complexity should approximate relevant capability joins rather than naive O(N² × abilities²) scans. Deduplicate by canonical `(source_id, target_id, rule_id, source_ability_id, target_ability_id, normalized_event_signature)`; retain multiple independent edges where evidence is genuinely distinct.

```python
def analyze_pairs(context, pool, ability_profiles, rules):
    profiles = index_profiles(pool.candidates, context.commander_card_ids, ability_profiles)
    edges, diagnostics = [], Diagnostics()
    for rule in sorted(rules, key=lambda r: r.rule_id):
        sources = profiles.by_capability(rule.source_capability)
        targets = profiles.by_capability(rule.target_capability)
        for source, target in deterministic_join(sources, targets):
            if source.card_id == target.card_id and not rule.allows_self_interaction:
                continue
            diagnostics.considered_pairs += 1
            match = validate_rule(rule, source, target, context)
            if match.valid:
                edges.append(make_edge(rule, source, target, match.evidence))
            else:
                diagnostics.reject(match.reason_code)
    return PairwiseSynergyAnalysis(edges=stable_dedupe_and_sort(edges), diagnostics=diagnostics)
```

Allow same-card self-interaction only when explicitly modeled, with no assumption of duplicate physical copies. Respect commander color identity and candidate eligibility already validated upstream. Unknown/missing ability data must yield `unverified`/rejected diagnostic, not an invented synergy.

## 6. Preconditions and feasibility

Check the following before emitting an edge:

1. **Event compatibility:** source really generates the event consumed by target.
2. **Object compatibility:** creature vs artifact, token subtype, permanent vs spell, zone, owner/controller.
3. **Timing:** trigger ordering, instant/sorcery windows, tap/summoning-sickness restrictions, delayed triggers.
4. **Costs:** mana, sacrifice, discard, exile, tap, and once-per-turn limitations; a resource is not free merely because the effect is repeatable.
5. **Replacement/prevention:** effects that change or prevent events can invalidate joins.
6. **Dependencies:** additional permanents, sacrificeable fodder, graveyard targets, mana, combat, and opponents' actions are explicit limitations.
7. **Modal choices:** edges only exist for feasible mode selections; preserve mode identity.

An edge is evidence of potential interaction under stated requirements, **not** proof that a deck can reliably assemble or execute it.

## 7. Strength and explanation

Keep `strength` in `[0,1]`, calibrated as **interaction strength** rather than card quality or deck win rate. A transparent starting policy can weight directness, event frequency, reliability, resource burden, and restrictions. All weights belong in versioned config; log feature contributions. Avoid stacking multiple edges based on the same effect into independent utility bonuses downstream: attach stable `event_ids`/`effect_ids` for deduplication.

Example explanation: `Viscera Seer can sacrifice a creature; if that creature dies, Blood Artist can trigger. Requires a creature to sacrifice and an applicable death event.`

## 8. Required fixtures and counterexamples

| Pair | Expected result |
|---|---|
| Token generator + Parallel Lives | `amplifies`, if token is created under appropriate controller |
| Viscera Seer + Blood Artist | `enables`, conditional on creature death |
| Blink effect + ETB draw creature | Positive if creature returns and ETB condition holds |
| Treasure generator + artifact-enter payoff | Positive only if artifact token enters appropriately |
| Sacrifice outlet + discard-trigger payoff | No direct match |
| Creature sacrificed but exiled by replacement | No `dies` edge |
| Card that merely searches land to hand + landfall payoff | No immediate ETB-land edge |
| Non-targeted wipe + hexproof | Do not assert protection against the wipe |
| Commander payoff + candidate enabler | Edge allowed; commander excluded from main-deck inventory |
| Token doubler + non-creation token move | No amplification |

Use known Oracle fixtures and synthetic minimal effects. Include modal, variable X, owner/controller mismatch, opposing-player dependency, and incomplete parser outputs.

## 9. Testing and observability

**Unit:** predicate evaluation, directional edges, ID stability, missing evidence, constraints, deduplication, deterministic ordering. **Integration:** consume Phase 1 pool, join commander abilities, persist edges with `run_id`. **Properties:** no edge without rule/evidence; no false symmetric edges; replay is byte-stable under same snapshots; no mutation of pool; bounded runtime. Record considered/verified/rejected counts by rule and failure reason; sample explainable trace for each accepted edge.

## 10. Implementation steps and exit gate

1. Reuse existing strategy capability graph and event normalization; inventory existing rule IDs.
2. Implement shared edge schema and rule validation with fixtures.
3. Build indexed capability joins and directional edge emission.
4. Add precondition validators, evidence and explanation generation.
5. Add caching keyed by pool/profile/rule versions and deterministic regression tests.
6. Export `analyze-pairs` CLI/service operation using repository conventions.

**Done when:** Phase 1 pool can be analyzed without deck construction; positive/negative fixtures pass; each edge is traceable to rule and abilities; commander edges are supported; unknowns do not become verified; output matches `ARCHITECTURE.md` and is ready for package discovery.

**Out of scope:** inferring infinite combos, assembling 99 cards, using EDHREC frequency as mechanical proof, and scoring full-deck consistency.
