# Preliminary Deck Composer — Implementation Guide

**Pipeline phase:** 4 · **Status:** implementation specification

> Follow `ARCHITECTURE.md`. The composer consumes eligible owned candidates and validated synergies to produce a **legal preliminary proposal or an explicit infeasibility report**. It must not silently invent missing cards.

## 1. Goal and inputs

Produce an initial Commander deck proposal from the candidate pool, including appropriate lands, balancing functional roles and strategy packages while respecting inventory and format legality. The composer is **not** a top-99 sort. It makes contextual selections based on marginal value and hard constraints. Refinement is a separate Phase 5.

Inputs: `DeckBuildingContext`, Phase 1 `CandidatePool`, Phase 2 edges, Phase 3 packages, commander configuration, owned inventory snapshot, legality/ruleset adapter, normalized card metadata (mana value, costs, colors, land types, mana production), and versioned composition config.

## 2. Canonical proposal contract

```json
{
  "proposal_id": "deck_001",
  "run_id": "run_001",
  "commander_card_ids": ["commander_001"],
  "main_deck": [{"card_id": "card_a", "quantity": 1}],
  "reserve_card_ids": ["card_b"],
  "selected_package_ids": [],
  "role_coverage": {},
  "mana_curve": {},
  "mana_base_diagnostics": {},
  "validation": {"is_legal": false, "violations": ["incomplete_deck"]},
  "decision_log": []
}
```

Extend via shared contracts for `proposal_status` (`draft`, `valid`, `infeasible`), `utility_breakdown`, `inventory_allocation`, `constraint_diagnostics`, `config_version`, and `provenance`. Do not claim legal status until the format validator passes.

## 3. Hard constraints vs soft objectives

**Hard constraints (non-negotiable):** valid commander(s) and eligibility, format deck size, color identity, copy limits and exceptions, card legality/bans under versioned ruleset, inventory quantity/printings, and any explicit user exclusions. For ordinary single-commander Commander, 99 main-deck cards plus one commander; derive main-deck size for legal partner/multi-commander variants instead of hard-coding 99. Basic lands and card-specific exceptions require format-aware copy logic.

**Soft objectives:** role coverage, strategy affinity, direct commander synergy, validated package completeness, mana curve, colored mana availability, ramp timing, card advantage, interaction distribution, protection, redundancy, resilience, and user preferences. Soft targets are configurable, not universal EDH laws.

**Ownership:** every main-deck quantity must be backed by available collection inventory; commander allocations must reduce available copies when appropriate. Distinguish canonical card identity from owned printings; export chooses valid printing allocations later or during inventory validation.

## 4. Critical prerequisite: mana base

Land selection is a real optimization subproblem, not an afterthought. Use owned legal lands and actual color production constraints; distinguish lands that enter tapped, utility lands, fetch-like lands, MDFCs, colorless sources, and basics. Do not assume unlimited basic lands unless user inventory policy explicitly allows them. Treat land count as a configurable range or feasibility-driven target. Estimate color source needs from selected spell requirements and commander costs, while preserving uncertainty from variable costs, hybrid symbols, alternative casting costs, and ramp.

Include a land candidate source even when the Phase 1 pool's role coverage is sparse; never broaden outside owned inventory without an explicit policy.

## 5. Marginal utility and double-count prevention

Use the same objective definition for addition, removal, and swap:

`marginal_value(card, D) = utility(D ∪ {card}) - utility(D)`

Conceptual decomposition:

`utility(D) = functional_coverage(D) + strategy_fit(D) + validated_joint_synergy(D) + mana_consistency(D) + resilience(D) - curve_penalty(D) - redundancy_penalty(D) - dependency_risk(D)`

Keep raw engine scores separate from normalized composer features. Multi-role membership does not create multiple card copies or automatically additive bonuses. Cap coverage contributions at saturation; identify shared effect/event evidence so a single trigger cannot earn multiple equivalent synergy bonuses. Apply package completion bonuses **only when required members and conditions are present**; do not count the same pairwise benefit again through its package.

## 6. Deterministic baseline algorithm

A greedy-plus-repair baseline is appropriate for MVP; use deterministic tie-breaking by canonical ID and versioned configs. A solver-based optimizer can be introduced later.

```python
def compose(context, pool, edges, packages, inventory, ruleset, config):
    commander = validate_commander_configuration(context, inventory, ruleset)
    eligible = filter_eligible_owned_candidates(pool, inventory, commander, ruleset)
    if not feasible_inventory_upper_bound(eligible, commander, ruleset):
        return infeasible_report("insufficient_legal_owned_cards")
    deck = initialize_empty_proposal(commander)
    deck = seed_feasible_mana_base(deck, eligible, config)
    deck = seed_critical_roles_and_validated_packages(deck, eligible, packages, config)
    while deck.needs_cards():
        feasible = [c for c in eligible if can_add(c, deck, inventory, ruleset)]
        if not feasible:
            break
        choice = stable_argmax(feasible, key=lambda c: marginal_utility(c, deck, config))
        deck.add(choice)
    deck = bounded_repair(deck, eligible, packages, inventory, ruleset, config)
    validation = validate_deck(deck, context, inventory, ruleset)
    if not validation.is_legal:
        return proposal_with_explicit_infeasibility(deck, validation)
    return finalize_valid_preliminary_proposal(deck, validation)
```

Do not greedily seed a package that prevents hard-feasible deck completion. Repair should support add/remove/swap and land/spell adjustments with bounded iterations. A deck can be **legal but low quality**; do not conflate legality with soft target attainment.

## 7. Package selection as group-aware optimization

Packages are options, not mandatory blocks. Account for package members already selected, overlapping packages, shared triggers, and partial completion. A package's contribution is its **incremental benefit given current deck**, not sum of package score plus all edge/member scores. Prefer robust packages over fragile high-score packages when configured. Packages containing commander anchors should not consume extra slots.

Example: If two packages share Blood Artist, count the card once and do not double-count its same death-trigger output for the same death event. Preserve provenance for each satisfied package.

## 8. Diagnostics and decision trace

For every selected/reserved/rejected candidate log: `reason_code`, previous/current status, utility delta, constraints evaluated, role coverage delta, package activation delta, and tie-break reason. Output curve by mana value, estimated colored sources, land counts, tapped-land indicators, ramp/card advantage/interaction coverage, and unsatisfied targets. Keep numeric score scales and normalization version visible.

If the pool cannot yield a legal deck, return **structured infeasibility**:

```json
{
  "proposal_status": "infeasible",
  "validation": {"is_legal": false, "violations": ["insufficient_owned_legal_cards"]},
  "constraint_diagnostics": {"required_main_deck_size": 99, "max_available_legal_cards": 87},
  "suggested_actions": ["review_candidate_pool_coverage", "allow_owned_reserve_rediscovery"]
}
```

Never quietly insert an unowned basic land, proxy, or duplicate copy. A controlled rediscovery from **eligible owned reserve** is allowed when needed; record it.

## 9. Suggested implementation boundaries

- `composition/constraints`: legality and inventory-backed feasibility wrappers.
- `composition/utility`: normalized context-dependent objective and event deduplication.
- `composition/mana_base`: land allocation, color-source diagnostics, mana curve.
- `composition/seeding`: role/package-aware initial selection.
- `composition/repair`: bounded deterministic local repair.
- `composition/explain`: decision log and feasibility reports.
- `validation/`: shared authoritative deck legality validation (do not duplicate format rules).

Suggested public API: `compose(context, pool, pairwise, packages, inventory, ruleset, config) -> DeckProposal`. Expose `compose` CLI/local API operation consistent with repository conventions.

## 10. Test plan

**Hard invariant fixtures:** normal Commander 99+1; legal multi-commander variant; banned/out-of-identity card; insufficient owned copies; basic-land and card-specific copy exceptions; commander also present in inventory; incomplete candidate pool; zero eligible lands; illegal color source; modal double-faced card identity. **Behavior:** greedy top-score trap, package completion vs isolated payoffs, high-ramp but poor mana-color deck, duplicate multi-role credit, overlapping packages, missing commander partner, deterministic tie-break. **Properties:** never overallocate inventory; no unowned cards; no illegal final proposal; legal proposals have exact deck size; identical inputs yield identical output; score deltas use same utility function.

## 11. Delivery phases and acceptance

1. Integrate authoritative format/collection validator and typed proposal contract.
2. Build owned-land/mana-base allocator and baseline feasibility checks.
3. Implement normalized objective and deterministic marginal selection.
4. Add package-aware seeding, overlap handling, and bounded repair.
5. Add diagnostics, audit, integration tests and `compose` entry point.

**Done when:** fixture collection yields a legal 99+commander proposal where feasible; otherwise a clear infeasibility report; ownership, legality, and deterministic replay hold; packages and role coverage influence selection without duplicate accounting; Phase 5 can consume proposal plus reserve and decision trace.

**Out of scope:** exhaustive tournament optimization, opponent metagame simulation, unconstrained purchase recommendations, and unverified combo claims.
