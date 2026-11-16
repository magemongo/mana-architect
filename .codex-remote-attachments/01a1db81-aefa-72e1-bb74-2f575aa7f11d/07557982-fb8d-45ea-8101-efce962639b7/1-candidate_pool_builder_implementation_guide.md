# Candidate Pool Builder — Implementation Guide

**Project:** Deterministic MTG Commander Deck Builder  
**Module:** Candidate Pool Builder (CPB)  
**Status:** Implementation specification / MVP  
**Language:** English

## 1. Purpose and boundaries

The Candidate Pool Builder creates a **large, diverse, traceable pool of owned, Commander-legal candidate cards** for a chosen commander and one or more target strategies. It **does not** directly select a final 99-card deck. It consumes precomputed evaluations from the Ramp, Card Advantage, Interaction (Spot/Mass), and Strategy Synergy engines and preserves options for subsequent pairwise analysis, package discovery, composition, and refinement.

### In scope
- Filter eligible owned cards using commander color identity, format legality, collection quantities, and explicit user exclusions.
- Aggregate overlapping roles without duplicating card records.
- Gather a critical mass of candidates for each role, while preserving mechanical diversity and strategically relevant outliers.
- Explain every inclusion and rejection, retain dependency information, and expose unmet role coverage.
- Produce deterministic, versioned, reproducible results for downstream analysis.

### Out of scope
- Final deck size of 99, mana-curve optimization, land-count optimization, opening-hand simulation, matchup simulation, and probability-based consistency.
- Assuming all identified synergy packages are functional or infinite.
- Treating engine scores from different domains as directly comparable without calibration.
- Generating or recommending cards not owned by the user (unless explicitly running a separate discovery mode, which is not part of this MVP).

## 2. High-level architecture

```text
Owned Collection + Commander + Selected Strategies + User Constraints
                              |
                              v
                    Eligibility Filter
                              |
                              v
             Evaluation Profile Normalizer
             /          |          |        \
          Ramp      Card Advantage Interaction Strategy Synergy
                              |
                              v
                    Role Membership Mapper
                              |
                              v
          Role-Specific Retrieval and Quality Gates
                              |
                              v
             Diversity-Aware Candidate Selection
                              |
                              v
         Cross-Role Union + Evidence + Coverage Audit
                              |
                              v
                 Versioned Candidate Pool
                              |
              +---------------+----------------+
              v               v                v
         Pairwise Analyzer  Package Builder  Deck Composer
```

The existing Strategy Synergy Engine is responsible for card-to-commander and card-to-strategy affinity. The CPB uses that evidence but **does not reimplement** the strategy classifier.

## 3. Terminology

- **Card identity:** Stable canonical printing-independent identifier for gameplay identity, with a separate owned printing/inventory identifier. For cards with multiple faces, use the full canonical card identity and parsed face-specific capabilities.
- **Role:** Functional contribution, such as `ramp`, `card_advantage`, `spot_interaction`, `mass_interaction`, or `strategy_synergy`.
- **Mechanical subtype:** Specific capability within a role (e.g. `mana_rock`, `land_tutor_to_battlefield`, `exile_removal`, `sacrifice_outlet`).
- **Candidate:** Eligible owned card considered for selection.
- **Membership:** Evidence that a candidate qualifies for one or more roles; roles are nonexclusive.
- **Critical mass:** Sufficient qualified candidates and mechanical coverage to enable meaningful downstream comparisons; not a fixed deck quota.
- **Reserve:** Qualified alternative retained for later reconsideration.
- **Package:** A set of mutually complementary cards; discovery and validation belong to later modules.

## 4. Inputs and contracts

### 4.1 Required inputs

```json
{
  "commander": {
    "card_id": "canonical_commander_id",
    "color_identity": ["B", "R", "G"],
    "commander_count": 1
  },
  "selected_strategies": [
    {"id": "aristocrats", "weight": 0.6},
    {"id": "tokens", "weight": 0.4}
  ],
  "collection_snapshot_id": "collection-2026-11-14",
  "evaluation_snapshot_id": "evaluations-v3",
  "config_version": "cpb-v1",
  "constraints": {
    "owned_only": true,
    "exclude_card_ids": [],
    "locked_card_ids": []
  }
}
```

Strategy weights must be nonnegative and normalized to sum to one; when absent, use equal weights. These weights influence strategy candidate retrieval, not legal eligibility.

### 4.2 Required per-card data

- Canonical card identifier and name; owned quantities and inventory reference.
- Commander color identity and legality status, including banned status for the configured rules snapshot.
- Card type(s), mana value, casting-cost information, Oracle text and parsed mechanical capabilities.
- Ramp, Card Advantage, Spot Interaction, Mass Interaction, and Strategy Synergy evaluation profiles (or explicit `not_evaluated` states).
- For each engine output: engine version, score definition/scale, confidence or evidence quality, and source evidence.

Missing engine results must not be interpreted as zero utility. Preserve `not_evaluated`, report partial coverage, and apply configured fallback policy.

### 4.3 Commander-specific legality

- Check color identity, not merely mana cost or printed colors.
- Exclude the commander itself from the 99-card candidate pool unless an explicit legal alternate configuration applies.
- Validate Commander format restrictions, including banned cards and singleton rules. Basic lands and other rule-defined exceptions require special handling.
- For partners, backgrounds, or other multi-commander configurations, use the combined commander identity and appropriate commander count.
- Validate available owned quantities before materializing selections. The candidate pool represents **card identities plus available copies**, not unlimited copies.
- Treat legality data as versioned; do not hardcode an eternal ban list.

## 5. Canonical role taxonomy

| Role ID | Source | Examples of subtypes |
|---|---|---|
| `ramp` | Ramp Engine | land ramp, mana rock, mana dork, treasure, ritual, cost reduction |
| `card_advantage` | Card Advantage Engine | direct draw, repeatable draw, recursion, impulse access, synergistic draw |
| `spot_interaction` | Interaction Engine | targeted removal, counterspell, spot protection, single-card grave hate |
| `mass_interaction` | Interaction Engine | board wipe, mass protection, fog, mass grave hate |
| `strategy_synergy` | Strategy Synergy Engine | generators, enablers, payoffs, amplifiers, converters, finishers |
| `lands` | Card type / future mana-base evaluator | basics, duals, utility lands, fixing lands |

The `lands` pool is an **eligible land inventory**, not a fully scored mana base. Land evaluation can be added as a separate engine later. A land can also carry other roles; do not erase them.

### 5.1 Role membership rules

Use engine-provided typed classification, not naive string matching on Oracle text. A role membership contains:

```json
{
  "role_id": "ramp",
  "qualifies": true,
  "subtypes": ["treasure_generation"],
  "score": 72,
  "score_scale": "ramp_v2_0_100",
  "evidence_ids": ["ramp:event:3"],
  "conditions": ["requires_creature_death"],
  "evaluation_state": "evaluated"
}
```

A card can qualify for several roles and several subtypes. Avoid assigning an exclusive primary role in the algorithm; a `display_primary_role` may be derived solely for UI.

## 6. Selection philosophy

The CPB optimizes **candidate coverage and diversity**, not the final deck's utility. Selection proceeds per role and is then merged into one unique pool.

Core principles:
1. **Hard gates first:** legality, ownership, exclusions, explicit constraints.
2. **Evidence over keyword coincidence:** membership must be supported by engine evidence.
3. **Role-specific quality:** compare scores only within their calibrated role/subtype domain.
4. **Diversity:** retain different mechanical ways of fulfilling the same role.
5. **Nonexclusive memberships:** a card may contribute to several role coverage counts.
6. **Context:** selected strategies and commander profile may influence retrieval priorities, but do not override legality.
7. **Reversibility:** retain reserves and rejected candidate reasons for downstream reconsideration.
8. **Determinism:** fixed input snapshots and configuration must yield identical ordered results.

## 7. Configurable critical-mass policy

Use **soft targets** rather than fixed quotas. The figures below are examples for initial tuning, **not recommended final-deck counts**.

```yaml
candidate_pool:
  roles:
    ramp: {soft_target: 24, quality_floor: 0.45, max_candidates: 40}
    card_advantage: {soft_target: 28, quality_floor: 0.45, max_candidates: 45}
    spot_interaction: {soft_target: 18, quality_floor: 0.45, max_candidates: 35}
    mass_interaction: {soft_target: 12, quality_floor: 0.45, max_candidates: 25}
    strategy_synergy: {soft_target: 55, quality_floor: 0.40, max_candidates: 100}
    lands: {soft_target: 48, quality_floor: null, max_candidates: null}
  diversity:
    minimum_subtype_coverage: 0.6
    preserve_high_synergy_outliers: true
  overflow:
    allow_above_soft_target: true
    allow_above_max_for_locked: true
  tie_breaker: canonical_card_id
```

Normalize quality scores **within the corresponding engine's documented scale**. Never assume a raw 72 in Ramp equals a raw 72 in Strategy Synergy. A quality floor may be overridden by a documented strategic exception, but not by a legality exception.

### 7.1 Adaptive target logic

For role `r`:

```text
eligible_r = all eligible cards with qualifying membership in r
qualified_r = eligible_r passing role-specific quality/evidence gates
soft_target_r = configured target
selected_r = diversity-aware subset of qualified_r, up to soft_target_r
if |qualified_r| < soft_target_r:
    select all qualified_r
    report coverage shortfall
```

When a role has scarce candidates, do **not** fill it with irrelevant cards simply to meet a numeric target. Report a gap. When multiple subtypes exist, preserve credible representatives of each before saturating the most common subtype.

### 7.2 Role-specific sampling

A practical deterministic algorithm:

1. Partition eligible candidates by role and subtype.
2. Apply quality and evidence gates.
3. For each subtype, rank by calibrated role score, commander synergy as a **secondary** criterion, reliability, and canonical ID.
4. Reserve representatives for undercovered subtypes, subject to availability and quality.
5. Fill remaining role slots by marginal subtype coverage, quality, and novelty.
6. Preserve strong strategic outliers (e.g. unusual synergistic rituals or repeatable protection) with an explicit override reason.
7. Record qualified alternatives in reserve.

No subtype must be populated if it is unavailable or irrelevant to the commander colors. Do not enforce a mana-dork quota for a color identity that cannot legally support it.

## 8. Diversity-aware scoring

Selection ranking should be **role-local** and transparent:

\[
R(c,r,P) = w_q Q_r(c) + w_s S_{commander}(c) + w_d D(c,r,P) + w_e E(c)
\]

Where:
- `Q_r(c)`: calibrated quality of card `c` for role `r`.
- `S_commander(c)`: strategy synergy score for the selected commander/strategies.
- `D(c,r,P)`: novelty / marginal subtype coverage relative to selected role pool `P`.
- `E(c)`: evidence completeness and reliability.

All terms must be normalized and documented. **Do not reuse this rank as the final deck optimization score.** Diversity is dynamic: a subtype's novelty decreases as it becomes well represented.

Example novelty:

```python
def novelty(card_subtypes: set[str], selected_subtype_counts: dict[str, int]) -> float:
    if not card_subtypes:
        return 0.0
    return sum(1.0 / (1 + selected_subtype_counts.get(t, 0)) for t in card_subtypes) / len(card_subtypes)
```

This is a heuristic to test and calibrate, not a universal MTG value function.

## 9. Data model

### 9.1 Pool-level object

```json
{
  "pool_id": "pool-001",
  "schema_version": "1.0.0",
  "commander_ids": ["commander_001"],
  "selected_strategies": [
    {"id": "aristocrats", "weight": 0.6},
    {"id": "tokens", "weight": 0.4}
  ],
  "collection_snapshot_id": "collection-2026-11-14",
  "evaluation_snapshot_id": "evaluations-v3",
  "config_version": "cpb-v1",
  "candidate_ids": ["card_001"],
  "candidates": [],
  "role_coverage": {},
  "warnings": [],
  "audit": {
    "eligible_count": 0,
    "unique_candidate_count": 0,
    "membership_count": 0
  }
}
```

### 9.2 Candidate object

```json
{
  "card_id": "card_001",
  "owned_quantity": 1,
  "eligible": true,
  "status": "candidate",
  "role_memberships": [
    {
      "role_id": "ramp",
      "subtypes": ["treasure_generation"],
      "score": 72,
      "score_scale": "ramp_v2_0_100",
      "evidence_ids": ["ramp:event:3"]
    },
    {
      "role_id": "strategy_synergy",
      "subtypes": ["generator", "enabler"],
      "score": 91,
      "score_scale": "synergy_v1_0_100",
      "evidence_ids": ["synergy:rule:7"]
    }
  ],
  "capabilities": ["treasure_generation", "artifact_sacrifice_enabler"],
  "dependencies": ["requires_creature_death"],
  "selection_reasons": [
    {"code": "ROLE_QUALIFIED", "role": "ramp"},
    {"code": "STRATEGY_ALIGNMENT", "strategy": "aristocrats"}
  ],
  "evaluation_completeness": "complete",
  "locked": false
}
```

Keep `score: null` and `evaluation_state: not_evaluated` when data is missing. Do not fabricate scores. `status` may be `candidate`, `shortlisted`, `selected`, `reserve`, or `excluded`; CPB should normally emit `candidate` and `reserve`, while downstream modules own `shortlisted` and `selected`.

### 9.3 Role coverage report

```json
{
  "ramp": {
    "soft_target": 24,
    "qualified_available": 19,
    "selected_count": 19,
    "shortfall": 5,
    "subtype_counts": {
      "mana_rock": 7,
      "land_tutor_to_battlefield": 6,
      "treasure_generation": 4,
      "mana_ritual": 2
    },
    "warnings": ["BELOW_SOFT_TARGET"]
  }
}
```

Coverage counts are **role memberships**, not unique cards across roles. Report both unique card count and membership count explicitly.

## 10. Selection algorithm (reference pseudocode)

```python
def build_candidate_pool(request, collection, evaluations, config):
    validate_request(request, config)
    legal_owned = filter_by_ownership_and_commander_legality(
        collection, request.commander, request.constraints
    )

    candidates = []
    for card in sorted(legal_owned, key=lambda c: c.canonical_id):
        profile = evaluations.get(card.canonical_id)
        memberships = map_engine_outputs_to_roles(profile, config)
        candidates.append(make_candidate(card, memberships, profile))

    role_selections = {}
    role_reserves = {}
    for role in config.roles_in_stable_order:
        eligible = [c for c in candidates if qualifies(c, role)]
        qualified = apply_role_quality_and_evidence_gates(eligible, role, config)
        role_selections[role], role_reserves[role] = select_diverse(
            qualified, role, request.selected_strategies, config
        )

    selected_ids = set().union(*(set(ids) for ids in role_selections.values()))
    selected_ids.update(validate_and_include_locked_cards(request, candidates))
    reserve_ids = set().union(*(set(ids) for ids in role_reserves.values())) - selected_ids

    pool = merge_unique_candidates(candidates, selected_ids, reserve_ids)
    pool.role_coverage = audit_role_coverage(role_selections, candidates, config)
    pool.warnings += audit_missing_evaluations(candidates)
    pool.warnings += audit_dependency_fragility(pool)
    return canonicalize_and_version(pool)
```

**Important:** `audit_dependency_fragility` only annotates dependencies. It must not claim a package is active merely because the pool contains potentially compatible cards. Actual pairwise and package validation are downstream tasks.

## 11. Dependencies and future package integration

Preserve the dependency graph data already produced by the Strategy Synergy Engine:

```json
{
  "card_id": "payoff_card",
  "capability": "creature_death_payoff",
  "requires_events": ["creature_dies"],
  "possible_enablers": ["sacrifice_outlet", "creature_token_generation"],
  "dependency_strength": "external"
}
```

Distinguish **intrinsic** effects (self-contained), **conditional** effects (need a game state), and **synergistic** effects (need another card/capability). The CPB may favor coverage of both enablers and payoffs, but it should not assert that a specific loop works or is infinite.

### Downstream contracts

- **Pairwise Synergy Analyzer:** accepts pool candidates and their mechanical capabilities; returns validated card-to-card relationships, conditions, and evidence.
- **Synergy Package Builder:** groups validated interactions into small functional packages and distinguishes repeatable from infinite loops.
- **Preliminary Deck Composer:** uses role coverage, package evidence, mana curve, lands, and legal limits to choose a provisional 99.
- **Deck Refinement Engine:** recomputes marginal value, role coverage, redundancy, dependencies, and replacement options as the deck changes.

The CPB should not hard-code downstream optimization rules.

## 12. Operational concerns

### 12.1 Determinism and caching

Cache key should include:
- Commander canonical ID(s) and color identity.
- Selected strategy IDs and weights.
- Collection snapshot hash.
- Engine evaluation snapshot hashes and versions.
- Legality snapshot/rules version.
- CPB configuration version and explicit constraints.

Stable sort all inputs, use deterministic tie-breakers, and record configuration in output metadata. Re-running identical inputs must produce byte-equivalent canonical JSON apart from explicitly excluded operational timestamps.

### 12.2 Incremental recomputation

- Collection changes: rerun eligibility and role selection for affected cards, then recompute affected coverage.
- Evaluation changes: update memberships and rerank affected roles.
- Strategy changes: rerank synergy-based retrieval; legal eligibility may remain cached.
- Commander changes: rerun color identity eligibility and commander-specific synergy.

Start with full recomputation for MVP if collection size is manageable; add incremental execution only after profiling.

### 12.3 Explainability and audit

For every selected candidate, retain:
- Which role(s) it qualified for.
- Which engine output and evidence supported qualification.
- Whether it was selected for quality, subtype diversity, strategy alignment, locked status, or an override.
- Which conditions or missing evaluations limit confidence.

For every omitted qualified card, retain reserve reason or exclusion reason (`LOW_ROLE_SCORE`, `SUBTYPE_SATURATED`, `MAX_CANDIDATES`, `NOT_OWNED`, `COLOR_IDENTITY`, `BANNED`, `USER_EXCLUDED`, etc.). Distinguish hard ineligibility from soft ranking decisions.

## 13. Edge cases

1. **Multi-role card:** one unique card record, several role memberships; count it in each relevant coverage report.
2. **Limited owned quantity:** do not materialize more copies than owned or Commander permits.
3. **Basic lands:** handle singleton exception and available quantities; avoid treating 48 land candidates as a requirement for 48 distinct land names.
4. **Modal double-faced / split / adventure cards:** canonical identity, color identity, and multiple capabilities must be handled correctly.
5. **Multi-commander setups:** commander count and combined color identity affect legality and eventual noncommander slots.
6. **Mass interaction that targets a player:** classify using affected objects, not just target count.
7. **Unscored card:** mark `not_evaluated`, do not silently assign zero; optionally route to an evaluation backlog.
8. **Conditional payoff:** preserve prerequisites; do not award self-contained value without evidence.
9. **Color-restricted mechanics:** do not enforce unavailable subtypes for a commander identity.
10. **User-locked card below threshold:** preserve if legal and owned, with `USER_LOCKED_OVERRIDE` audit reason.
11. **No cards in a role:** emit zero coverage and an explicit gap; do not invent filler.
12. **Strategy overlaps:** do not add the same underlying capability's score multiple times just because it supports two strategies.
13. **Cards with temporary ramp:** use Ramp Engine score and persistence metadata; do not reinterpret here.
14. **Replacement effects / unusual wording:** rely on parsed mechanical events and engine evidence rather than raw keyword counts.
15. **Oversized pool:** enforce soft caps with reserves and configurable hard resource limits; never assume 99 is the pool size.

## 14. Test strategy

### Unit tests

- Commander color identity and legality filtering.
- Ownership, quantity, exclusions, and locked-card validation.
- Role mapping and multi-role deduplication.
- Quality gates and score-scale validation.
- Subtype diversity and deterministic tie-breaking.
- Missing evaluation handling and audit reason generation.
- Soft-target shortfall behavior.
- Cross-strategy overlap without score duplication.

### Integration tests

- End-to-end input from all four existing engines.
- Stable canonical JSON across repeated runs.
- Candidate pool with legal, owned cards only.
- Coverage report counts agree with role memberships.
- Reserve promotion when a selected candidate is removed.
- Strategy weight change affects ranking without changing legal eligibility.

### Representative fixture scenarios

| Scenario | Expected outcome |
|---|---|
| Artifact that ramps and generates Treasure | One candidate with multiple roles/capabilities |
| 30 high-scoring mana rocks, 5 viable land-ramp cards | Preserve land-ramp representatives when quality permits |
| Sacrifice payoff without sacrifice outlet in collection | Include if qualified, annotate dependency gap |
| Card outside commander color identity | Exclude regardless of synergy score |
| Strong commander synergy but low generic role score | Allow documented strategy outlier if configured |
| Candidate pool totals 160 role memberships, 120 distinct cards | Report both counts correctly |
| 7 qualified mass interaction cards, target 12 | Select 7 and report shortfall of 5 |
| Missing interaction evaluation | Preserve `not_evaluated`, do not classify as no interaction |
| Identical inputs, two executions | Identical ordered output and reasons |

### Property-based invariants

```text
All selected cards are owned and Commander-legal.
Every selected card appears exactly once in candidates.
Each role membership has an evidence-backed source or explicit fallback state.
Selected IDs are a subset of eligible IDs.
Reserve and selected IDs do not overlap.
No role soft-target shortfall is hidden.
Changing input order does not change the canonical output.
No final 99-card constraint is enforced by CPB.
```

## 15. Delivery roadmap

### Phase 1 — Contracts and eligibility
- Define input/output schemas and versioning.
- Implement ownership, legality, commander, and user constraint filtering.
- Implement explicit errors for incompatible or stale snapshots.

### Phase 2 — Role mapping
- Normalize the four engine profiles into a unified role membership schema.
- Preserve subtypes, evidence, dependencies, and score scales.
- Add multi-role deduplication.

### Phase 3 — Critical mass and diversity
- Implement per-role quality gates and soft targets.
- Add deterministic subtype diversity sampling and reserve management.
- Add configurable strategic outlier rules.

### Phase 4 — Audit and explainability
- Emit role coverage, shortfalls, exclusions, and selection reasons.
- Add reproducibility metadata and snapshot-aware caching.

### Phase 5 — Downstream readiness
- Expose stable APIs for pairwise synergy and package discovery.
- Add integration fixtures for Tokens + Aristocrats and other mixed strategies.
- Benchmark pool generation on realistic collection sizes.

## 16. Suggested interfaces

```python
class CandidatePoolBuilder:
    def build(self, request: PoolBuildRequest) -> CandidatePool: ...

class EligibilityFilter:
    def filter(self, cards, commander, constraints) -> list: ...

class RoleMembershipMapper:
    def map(self, card, evaluation_profile) -> list[RoleMembership]: ...

class RoleCandidateSelector:
    def select(self, role, candidates, config, strategy_profile) -> RoleSelection: ...

class CoverageAuditor:
    def audit(self, pool, role_selections, config) -> CoverageReport: ...
```

Recommended public API:

```text
POST /candidate-pools/build
GET  /candidate-pools/{pool_id}
GET  /candidate-pools/{pool_id}/coverage
GET  /candidate-pools/{pool_id}/candidates?role=ramp&status=candidate
GET  /candidate-pools/{pool_id}/reserves
```

Persist a pool as a snapshot. User edits should be modeled as explicit decisions/overrides rather than mutating source evaluation facts.

## 17. Acceptance criteria

The MVP is complete when:

1. A commander and selected strategies produce a pool containing **only owned and legal cards**.
2. The pool may contain more than 99 unique cards and never tries to finalize a deck.
3. Ramp, Card Advantage, Spot Interaction, Mass Interaction, Strategy Synergy, and eligible lands are represented as nonexclusive roles.
4. Quality and subtype diversity both influence retrieval, without arbitrary filler.
5. Every inclusion has traceable evidence and every omission has an auditable reason.
6. Missing engine data is explicitly surfaced, never silently scored as zero.
7. Soft-target shortfalls are reported accurately.
8. Candidate records are unique across roles, and role membership counts remain correct.
9. Repeated runs with identical inputs yield deterministic results.
10. The output is directly consumable by future pairwise, package, composition, and refinement modules.

## 18. Future extensions (not MVP)

- Pairwise Synergy Analyzer with event/constraint validation.
- Synergy Package Builder with dependency graphs and resource-loop verification.
- Land Evaluation Engine and mana-base optimization.
- Deck Composition Optimizer with multi-objective constraints and 99-card finalization.
- Deck Refinement Engine with marginal contribution, redundancy, role gaps, mana curve, and probabilistic consistency.
- User-facing interactive pool browser with manual locks, reserves, and explanation views.

---

**Design principle:** *Gather a broad, diverse, evidence-backed set of eligible cards first. Evaluate their collective contribution second. Optimize the final 99 only after those relationships are understood.*
