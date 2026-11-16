# Phase 4 — Preliminary Deck Composer

## Entry points

Run `mana-architect compose COMMANDER` with the exact owned card name or Oracle
ID. Supply `--partner COMMANDER` for a second commander with the standalone
Partner ability. Repeat `--strategy ID[=PRIORITY]`, `--lock ORACLE_ID`, or
`--exclude ORACLE_ID` to set preferences and card constraints. Use
`--land-target COUNT` to change the soft target of 38 front-face lands.
The command writes JSON to stdout or to `--output PATH`. Existing output
requires `--overwrite`. Exit status 0 means the proposal passed validation.
An infeasible proposal returns 1 and includes reason-coded violations.

From Python, load a `CollectionSnapshot`, create a `DeckBuildingContext`
using `CandidatePoolConfig().version` and `PARTNER_RULESET_VERSION`, and bind
both to an `EligibilityGate`. Call `run_preliminary_pipeline(gate,
constraints=PoolConstraints(...), config=CompositionConfig(...))`. When the
upstream artifacts already exist, call
`PreliminaryDeckComposer(config).compose(gate, pool, pairwise, packages)`.
Both entry points return the shared `DeckProposal`. The proposal and the
original artifacts can be passed to future deck refinement.

## Legality and inventory

`commander-snapshot-v2` supports one commander or two standalone Partner
commanders, 99 or 98 main-deck cards, and the union of the commanders' color
identities. The validator checks the dated Scryfall snapshot, English-name copy
limits, Oracle copy exceptions, and owned printing allocations across both
zones. Card locks require one main-deck copy. Exclusions apply across both
zones. The earlier single-commander ruleset remains available.

Other pairing variants and command-zone Rulebreaker abilities receive
structured unsupported-configuration errors. If imported interchangeable names
carry distinct Oracle IDs and distinct English names, the snapshot needs
verified name-equivalence data to group them. Commander legality is evaluated
against the collection snapshot date printed in the proposal.

## Selection and evidence

The composer checks the maximum legal capacity of the candidate pool,
reserve, and eligible collection before ranking cards. It admits additional
owned front-face lands when necessary. It draws from the Phase 1 reserve if
needed to complete the deck or get closer to the land target. A remaining
shortfall identifies whether the available pool or the collection is the
limiting factor.

Selection compares marginal utility for candidate additions and package
groups while keeping enough legal inventory to fill the remaining land and
spell slots. Role targets default to 10 ramp, 10 card advantage, eight spot
interaction, three mass interaction, and 12 strategy cards. A multi-role
card contributes to each applicable coverage count and shares one
calibrated utility budget per copy. The composer also scores mana sources,
curve, and verified joint benefits. A bounded composition repair can improve
an unmet objective using the same utility. Oracle IDs break equivalent ties.

One joint utility contribution is credited per benefit event. Missing
members and unmodeled runtime requirements remain visible in package
diagnostics. A conditional package whose only open requirement is paying
recorded fixed printed spell costs can receive discounted credit when
distinct owned land copies can support each recorded cost. Its Phase 3
classification and requirements remain in the output. The
[curated validation report](../phase-2-3-validation.md) tracks the unsupported
copy–untap and Altar mana-loop cases.

## Mana estimates

Source diagnostics distinguish direct, potentially untapped, conditional,
fetch, and modal sources. Fetch estimates require compatible selected target
copies. Spell-front modal lands remain optional sources. After a Scryfall
refresh, verified modal layout distinguishes playable land faces from
transform faces. Older rows with ambiguous back-face lands carry an unknown
layout diagnostic. Color demand uses fixed printed pips, with extra weight
for early spells and commanders. The proposal reports variable, alternative,
and unresolved costs. These are deck composition estimates, and unmet mana
targets can accompany a valid proposal.
