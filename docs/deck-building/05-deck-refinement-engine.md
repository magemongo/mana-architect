# Deck Refinement Engine — Implementation Guide

**Pipeline phase:** 5 · **Status:** implementation specification

> Follow `ARCHITECTURE.md`. This module optimizes a preliminary deck **as a whole**, using reversible, explainable changes while preserving legality and collection ownership. It must not replace the four existing card evaluation engines.

## 1. Goal and boundaries

Given a Phase 4 `DeckProposal`, candidate pool, reserve, verified pairwise edges, packages, commander/strategy context, and inventory/ruleset, improve the proposal's contextual utility through bounded local search. Analyze role coverage, enabler/payoff balance, curve, mana sources, interaction distribution, redundancy, package completeness, fragility, and resilience. Keep all decisions auditable and reversible.

**Inputs:** immutable proposal snapshot and versioned artifacts. **Outputs:** refined `DeckProposal`, swap history, before/after diagnostics, convergence reason, and any owned-only rediscovery requests. Do not invent cards, silently modify collection, or accept an illegal intermediate as final.

## 2. Architectural contracts

Reuse `DeckProposal` from `ARCHITECTURE.md`; add optional shared-contract fields for `refinement_run_id`, `iteration`, `parent_proposal_id`, `utility_breakdown`, `refinement_status`, `swap_log`, `convergence_reason`, and `diagnostics`. Every proposal is a versioned snapshot. Avoid mutating historical proposals in place.

```json
{
  "refinement_run_id": "refine_001",
  "parent_proposal_id": "deck_001",
  "result_proposal_id": "deck_002",
  "iterations": 12,
  "accepted_swaps": 3,
  "convergence_reason": "no_improving_legal_swap",
  "before_utility": 0.61,
  "after_utility": 0.68,
  "swap_log": [
    {
      "removed_card_id": "card_a",
      "added_card_id": "card_b",
      "utility_delta": 0.03,
      "reason_codes": ["improved_color_sources", "preserved_role_coverage"],
      "legality_validated": true
    }
  ]
}
```

Values are illustrative; actual utility must use one calibrated versioned definition. Use stable event IDs and package IDs to explain effects of a swap.

## 3. Deck-level diagnostics

| Dimension | Required signals |
|---|---|
| Functional coverage | Ramp, card advantage, spot/mass interaction, lands, strategy roles; overlap and saturation |
| Strategy engine health | Enabler/payoff/generator/amplifier balance; packages complete vs stranded |
| Mana and tempo | Curve, colored sources, early plays, tapped lands, acceleration timing |
| Redundancy | Repeated effect signatures, diminishing returns, duplicate functional bottlenecks |
| Fragility | Reliance on one payoff/outlet, graveyard dependence, removal susceptibility |
| Interaction | Answer diversity, coverage of permanent/spell/graveyard threats, timing |
| Resilience | Recursion, protection, alternate enablers, recovery after wipes |
| Inventory and legality | Ownership, copy limits, color identity, commander configuration, deck size |

Do not treat heuristic targets as universal truths. Commander strategy weights and play-style preferences should be configurable. Distinguish hard constraints from soft quality metrics.

## 4. Objective and marginal change

Reuse the **same utility function** from Phase 4 for all before/after comparisons; Phase 5 may extend it only through a versioned objective shared with composer. Define:

`delta_swap(out, in, D) = utility((D - {out}) ∪ {in}) - utility(D)`

Include role saturation, strategic contribution, validated packages, mana/curve, redundancy, resilience, and dependency penalties. Do not add raw Ramp/Card Advantage/Interaction/Synergy scores directly unless normalized to comparable features. Track shared `effect_ids` to prevent counting a single event repeatedly via card role, pair edge, and package.

**Swap feasibility:** inventory, format, color identity, copy limits, commander immutability (unless user explicitly changes commander), exact deck size, and any locked cards. A land-to-spell swap must re-evaluate mana consistency; replacing an enabler must re-evaluate every dependent package.

## 5. Deterministic refinement algorithm

Start with **single-card legal swaps** from selected cards against reserve/eligible pool. Later allow bounded two-for-two or package-aware moves to escape local maxima. For each move, compute affected metrics incrementally where possible, validate hard constraints, and accept only if utility improves by `epsilon` or a documented lexicographic hard-priority objective improves. Always break ties deterministically.

```python
def refine(context, proposal, pool, edges, packages, inventory, ruleset, config):
    current = validate_or_repair_initial_proposal(proposal, context, inventory, ruleset)
    if not current.validation.is_legal:
        return refinement_failure("invalid_initial_proposal", current.validation)
    history = []
    for iteration in range(config.max_iterations):
        moves = generate_bounded_swaps(current, pool, config)
        evaluated = []
        for move in moves:
            trial = apply_move_to_copy(current, move)
            if not validate_deck(trial, context, inventory, ruleset).is_legal:
                continue
            delta, breakdown = compare_utility(current, trial, edges, packages, config)
            evaluated.append((delta, stable_move_key(move), move, breakdown))
        best = deterministic_best(evaluated)
        if best is None or best.delta <= config.min_improvement:
            return finish(current, history, "no_improving_legal_swap")
        current = commit_new_snapshot(current, best.move)
        history.append(explain_move(best))
    return finish(current, history, "iteration_limit")
```

Ensure no cycles: strictly positive improvement, stable tie-break, and visited-proposal signatures. Bound total evaluated swaps, wall time if supported, and package search. A targeted owned-only rediscovery request can be emitted when reserve lacks necessary role coverage; orchestration controls the loop and reruns affected stages as needed.

## 6. Package-aware swaps

When removing a card, invalidate or downgrade packages requiring that card, then recompute downstream package contribution and functional coverage. When adding a card, detect newly completed packages using existing Phase 2/3 artifacts. For a card absent from the pool, do not assume existing edges/packages cover it; request targeted rediscovery and recomputation before scoring synergy. Package-level moves can replace a redundant cluster with an alternate cluster, but must respect search limits and ownership.

**Example:** Replacing the only sacrifice outlet with a generic high-score draw spell may improve standalone card advantage but break a three-card Aristocrats engine; the objective must account for lost package value. Conversely, adding a second independent outlet may increase resilience but has diminishing returns after sufficient redundancy.

## 7. Mana-base and probability diagnostics

At refinement time, deck-level statistics are meaningful because a concrete deck exists. Begin with deterministic curve/color-source and role-density heuristics. Optional later enhancements include hypergeometric opening-hand estimates, goldfish draws, commander casting probabilities, mulligan models, and land-drop simulations. Clearly label modeled assumptions and random seed. Do not present heuristic color-source counts as guaranteed casting probabilities.

When a swap changes colored mana demand or mana production, recompute mana-base metrics; a land substitution is not automatically equivalent merely because both cards are lands.

## 8. User control and explainability

Support `locked`, `preferred`, and `excluded` per-proposal user decisions; do not override locks. Store reason-coded swap traces including before/after coverage, curve, mana colors, package status, utility components, and legality check. Preserve removed cards in reserve unless explicitly excluded. Allow reversible undo by restoring an earlier proposal snapshot, not by reconstructing hidden state.

Suggested reasons: `fills_role_gap`, `completes_package`, `reduces_dependency_risk`, `improves_color_sources`, `reduces_curve_pressure`, `adds_answer_diversity`, `replaces_redundant_effect`, `user_locked`, `no_legal_improvement`.

## 9. Interfaces and orchestration

```python
class DeckRefinementEngine:
    def refine(self, context, proposal, pool, pairwise, packages, inventory, ruleset, config) -> RefinementResult: ...
```

Persist each accepted proposal snapshot, audit record, input hashes, versions, and convergence status. Expose `refine` CLI/local API. Keep cache invalidation precise: a score-weight change can reuse Oracle parsing and rule-verified edges; a capability-rule change invalidates dependent pairwise/packages and any utility derived from them.

## 10. Test scenarios

| Scenario | Expected |
|---|---|
| Single high-score payoff with no enabler | Penalize stranded dependency |
| Adding outlet completes Aristocrats package | Capture incremental package benefit |
| Removing sole outlet breaks package | Penalize loss of functional closure |
| Duplicate removal effects crowd out needed ramp | Prefer coverage if utility improves |
| Swap loses needed colored mana source | Reject or penalize according to legality/soft mana constraints |
| Illegal singleton duplicate | Reject |
| Swap requires unowned card | Reject |
| Commander anchored package | Commander never counted as main-deck copy |
| No improving legal swap | Stop deterministically |
| Equal-scoring swaps | Stable tie-break |
| Reserve missing needed role | Report targeted owned-only rediscovery |
| Locked card suggested for removal | Reject |

**Property tests:** exact format deck size after every accepted swap, legality/ownership always hold, no hidden mutation, strictly improving utility for accepted moves, stable replay, no repeated proposal cycles, consistent accounting for multi-role cards and shared events.

## 11. Delivery plan

1. Share composer objective/validator and add immutable proposal snapshots.
2. Implement deck diagnostics and versioned utility breakdown.
3. Implement bounded legal one-for-one swaps and deterministic stop rules.
4. Add package-aware incremental updates and reserve lifecycle.
5. Add user locks, audit, orchestration hooks, and optional controlled rediscovery.
6. Add integration/performance regression tests and `refine` entry point.

**Exit gate:** a valid Phase 4 proposal remains legal and owned after every accepted change; measurable versioned objective improves or a clear stop reason is returned; every decision is explainable and reversible; tests cover package breakage, mana impact, multi-role deduplication, and deterministic replay.

**Out of scope (MVP):** automatic purchases/proxies, stochastic opponent simulation, unsupported infinite-loop inference, and silently changing the commander or user-selected strategy.
