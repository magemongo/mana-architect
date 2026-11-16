# Synergy Package Builder — Implementation Guide

**Pipeline phase:** 3 · **Status:** implementation specification

> This document extends `ARCHITECTURE.md` and consumes Phase 2's verified `PairwiseSynergyEdge` records. A package is a validated, conditional group of cards, not a claim of an infinite combo or a guarantee of availability in play.

## 1. Purpose

Find **small, functionally meaningful card groups** from the candidate pool: token generator + token payoff; sacrifice outlet + recurring fodder + death payoff; blink + ETB payoff; mana generator + cost reducer + spell payoff. Identify roles, prerequisites, benefits, fragility, and reasons for selection. Packages can overlap and can contain a commander as a **contextual anchor** without consuming a main-deck slot.

**Inputs:** `DeckBuildingContext`, `CandidatePool`, verified Phase 2 edges, normalized capabilities, strategy preferences. **Outputs:** canonical `SynergyPackage[]`, partial-package opportunities, diagnostics, and provenance. Do not mutate card ownership, require a deck to exist, or automatically select a package into a final deck.

## 2. Canonical contract

```json
{
  "package_id": "package_001",
  "member_card_ids": ["card_a", "card_b", "card_c"],
  "functional_roles": {"generator": ["card_a"], "outlet": ["card_b"], "payoff": ["card_c"]},
  "edge_ids": ["edge_001"],
  "requirements": ["available_creature", "mana_for_recursion"],
  "repeatable": true,
  "infinite": false,
  "self_sustaining": false,
  "explanation": "Repeatable value engine; additional mana is required."
}
```

Retain required keys. Shared-contract extensions may include `schema_version`, `commander_anchor_ids`, `package_archetype`, `role_coverage`, `effect_ids`, `resources_consumed`, `resources_produced`, `dependency_status`, `score_breakdown`, `strategy_ids`, `fragility`, `provenance`, and `partial` status. Do not overload `member_card_ids` with commander IDs without explicitly defining commander-anchor semantics; use `commander_anchor_ids` for this distinction.

## 3. Package templates and functional closure

A package is not just a connected subgraph. Define **template constraints** to avoid random clusters:

| Template | Required roles | Typical effect |
|---|---|---|
| `sacrifice_value` | sacrifice outlet + sacrificeable resource + sacrifice/death payoff | Value from sacrifices |
| `blink_etb` | blink/recursion enabler + ETB payoff | Repeated or one-shot ETB value |
| `tokens_wide` | token generator + token count/combat payoff | Board development |
| `artifact_value` | artifact producer + artifact event payoff | Artifact engine |
| `counters_growth` | counter source + counter payoff/multiplier | Scalable counters |
| `spell_chain` | spell enabler + cast payoff | Spellslinger engine |
| `graveyard_loop` | recursion + graveyard enabler + payoff | Repeated graveyard value |
| `lands_matter` | land entry source + landfall payoff | Land event value |
| `voltron_support` | primary attacker + protection/evasion/amplifier | Commander damage support |

Templates are extensible, not exhaustive. Each defines required and optional roles, compatible edge relations, event join constraints, minimum verified edges, and whether an external resource may satisfy a role. Do not mistake **connectedness** for **functional completeness**.

## 4. Graph representation and search

Build a directed labeled multigraph of candidate/commander nodes and verified edges. Index by `strategy_id`, role, capability, and template. Begin with high-confidence 2-card seeds, extend to a bounded number of members (suggested configurable default 4; do not treat as format rule), and prune branches with unsatisfied hard predicates or impossible resource/timing constraints.

```python
def build_packages(context, pool, pairwise_analysis, templates, limits):
    graph = build_verified_graph(pool, pairwise_analysis.edges, context.commander_card_ids)
    packages, diagnostics = [], PackageDiagnostics()
    for template in sorted(templates, key=lambda t: t.template_id):
        seeds = find_template_seeds(graph, template)
        for seed in seeds:
            for group in bounded_expand(seed, graph, template, limits):
                check = validate_package(group, template, graph)
                if check.complete:
                    packages.append(materialize_package(group, template, check))
                else:
                    diagnostics.record_partial(group, check.missing_roles)
    return stable_dedupe(packages), diagnostics
```

Use a canonical signature of `(sorted member IDs, template ID, verified functional event path, commander anchors)` to prevent duplicate packages. Avoid exponential enumeration using maximum package size, maximum expansions per template, beam width, time/work budgets, and deterministic tie-breaking. Preserve separate packages where the same members genuinely support different functional paths, but do not inflate downstream utility from duplicated evidence.

## 5. Requirement closure and conditionality

Validate that each required role is provided by a member, a specified commander ability, or an explicitly marked external prerequisite. Classify:

- `complete`: required mechanical roles and event paths present in the package (may still require mana, turns, or game state).
- `conditional`: functional roles present but dependent on external resources/events.
- `partial`: missing required functional role; useful for diagnostics but **not** a complete package.
- `invalid`: impossible timing, incompatible events, or conflicting rules.

Preserve constraints such as mana costs, color restrictions, tap activation, creature summoning sickness, graveyard availability, target legality, once-per-turn caps, replacement effects, and control/ownership. A repeatable package may require repeated payment; a repeatable package is not automatically resource-neutral.

## 6. Repeatability and infinity policy

Set `repeatable=true` only if the demonstrated sequence can be performed more than once under a described renewal path; explicitly state costs and limiting resources. Set `self_sustaining=true` only if resources required for continuation are replenished within the closed loop and all timing conditions are met. **Default `infinite=false`.** For MVP, leave infinite detection disabled or require a separate verified loop solver checking resource balance, state changes, termination constraints, and valid activation order. Never infer infinity from cyclic graph edges alone.

Example: Reassembling Skeleton + Viscera Seer + Blood Artist forms a repeatable sacrifice/death-value engine **with mana needed to recur the Skeleton**. It is not an infinite, self-sustaining loop by itself.

## 7. Package evaluation

Score packages separately from standalone cards and pairwise edges. Suggested versioned dimensions: functional completeness, synergy strength, commander/strategy alignment, resource burden, activation speed, reliability, vulnerability to removal, redundancy, and role coverage. Use calibrated normalized features with recorded components. A package's bonus represents **incremental joint utility**, not the sum of member scores; deduplicate by shared `effect_ids` and trigger events.

For candidate group `P`, a conceptual model is:

`package_increment(P) = validated_joint_benefit(P) - already_accounted_effect_benefit(P)`

Avoid promising mathematically exact utility until the scoring scale is calibrated. Expose explanations with the interaction chain and external assumptions.

## 8. Examples and negative fixtures

| Cards / setup | Expected |
|---|---|
| Viscera Seer + Blood Artist + Reassembling Skeleton | Conditional repeatable value; not infinite |
| Blink effect + ETB draw creature | Functional package; repeatability depends on blink source |
| Token generator + Parallel Lives | Valid amplification; doubler alone is not generator |
| Sacrifice outlet + death payoff without fodder | Partial or conditional, depending on template/external resource policy |
| Two unrelated individually strong ramp cards | No synergy package by default |
| Three cards linked only by generic shared keyword | No package without verified event path |
| Package containing commander + two owned cards | Commander anchor does not consume a candidate copy |
| Multiple edges generated by one identical trigger | Deduplicate shared benefit |
| Exile replacement suppressing death | Invalidate death-dependent path |

## 9. Persistence and interfaces

Store packages as independent run artifacts keyed by pool hash, edge-set hash, template/rule versions, and context. Preserve canonical member IDs, relevant edge IDs, requirements, and provenance. Suggested interface:

```python
class SynergyPackageBuilder:
    def build(self, context, pool, pairwise_analysis, templates, config) -> PackageAnalysis: ...
```

Expose `build-packages` CLI/service operation. Partial packages may be surfaced to the composer as **opportunities**, not complete packages or guaranteed utility.

## 10. Tests and acceptance

**Unit:** template satisfaction, closure, graph direction, commander anchors, canonical IDs, duplicate suppression, requirement propagation. **Integration:** verified edges from Phase 2; no package references a nonexistent edge/card; deterministic output; bounded search on large pools. **Properties:** no unverified infinite claims; no double-counting by duplicate edges; package members are owned candidates except explicit commander anchors; no inventory mutation; missing enabler prevents complete status.

**Exit gate:** correct validated packages and partial diagnostics on fixture pools, deterministic bounded runtime, evidence-based explanations, strict compatibility with `ARCHITECTURE.md`, ready for composer consumption.

**Out of scope:** full-deck assembly, opponent simulation, arbitrary unrestricted combo discovery, probabilistic goldfishing, and claims of guaranteed win conditions.
