# Phase 5 — Deck Refinement

## Running refinement

Run `mana-architect refine COMMANDER` with the usual composition options to
compose and refine in one run. Run `mana-architect refine --proposal PATH` to
start from the deck quantities and printing allocations in a saved proposal.
Saved mode uses the proposal's partner, strategy, and land target. It accepts
additional locks for cards in the starting main deck and exclusions for cards
outside both starting zones.

Repeat `--prefer ORACLE_ID` to break ties among improving swaps. Use
`--max-swaps`, `--attempts-per-iteration`, `--max-attempts`, and
`--min-improvement` to configure the search. The defaults are eight accepted
swaps, 2,048 attempted pairs per iteration, 8,192 pairs per run, and a strictly
greater improvement than 0.000001. Results go to stdout or to `--output PATH`.
Use `--overwrite` for an existing output file. Exit status 0 includes valid
unchanged proposals. Exit status 1 accompanies errors.

From Python, call `DeckRefinementEngine(config, composition_config=...,
constraints=..., preferred_card_ids=...).refine(gate, proposal, pool, pairwise,
packages)` with the original Phase 4 composition configuration and matching
artifacts. The result contains the final proposal and the full search report.

## Replay and restoration

New valid `compose` JSON files include a replay manifest with the pipeline
context, configurations, and original constraints. Saved refinement checks
the deck fingerprint, requires the same collection snapshot, rebuilds Phases
1–3, and verifies the rebuilt provenance against the proposal. Older CLI
proposals require a fresh composition to obtain the manifest.

Each entry in `snapshots` contains a full proposal and its utility and
diagnostics. To restore an earlier deck on disk, save that entry's nested
`proposal` object as a proposal JSON file. CLI snapshots retain the original
replay manifest. Additional refinement locks, exclusions, and preferences
are recorded in the result and can be supplied again for another run.

## Search and audit

The engine ranks outgoing and incoming copies by separate marginal utility
and attempts pairs in increasing combined rank. It considers the candidate
pool, reserve, and lands admitted by Phase 4. A lock protects at least one
copy. The command zone and unaffected printing allocations carry through each
swap. The authoritative validator checks every proposed acceptance before
the new deck enters the history.

All trials use the same Phase 4 utility and original effective land target.
Land–spell swaps recompute sources, curve, coverage, and package support.
Preferred cards and Oracle IDs break equal improvement scores. The report
distinguishes a complete scan with no legal or improving swap from a search
stopped by a work limit.

The `attempts` array has one record per attempted pair. A constraint
rejection identifies its violation and omits the trial score. Scored trials
show the before and after utility and nonzero component deltas. Their outcomes
distinguish accepted swaps, improving moves passed over, and moves below the
improvement threshold. Accepted swaps reference the corresponding attempt
and the before and after snapshots.

## Diagnostics and scope

The report includes role overlap, early ramp, interaction diversity,
repeated role subtypes, strategy roles, protection and recursion evidence,
and package dependencies. These use existing card profiles and event IDs.
Package requirements and mana estimates retain the Phase 4 scoring rules.

When a role target is unmet and the available cards offer no qualifying
legal swap, the report lists that role for owned rediscovery. The coordinator
loop, separate upstream artifact persistence, and content-hash caching
remain pipeline-level work.
