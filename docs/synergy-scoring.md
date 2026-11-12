# Deterministic Synergy Scoring

## Formula

`score_card_for_strategy` scores a card for an explicitly selected commander and
strategy. Components use the `[0, 1]` scale:

- `strategy_evidence`: sum of `weight * confidence`, capped at 1, for each
  distinct target across strategy and associated tag evidence. Repeated evidence
  for one target counts once, using the strongest match.
- `shared_synergy_tags`: weighted Jaccard overlap between tags observed on the
  card and tags declared for the commander, limited to the strategy's tags and
  weighted by that strategy's configuration.
- `role_coverage`: fraction of required roles found. It is included only when
  the caller supplies required roles; otherwise its weight is renormalized
  across the available components.
- `commander_text_support`: the `strength_score` from the validated strategy
  affinity profile. This is a curated heuristic scale, not a probability.

Active components are combined using the weights in `scoring.json`,
renormalized by the sum of available weights. The result is multiplied by the
commander-affinity level factor (`primary`, `secondary`, or `complementary`),
receives an optional preference adjustment within the configured range, then
is capped and rounded according to configuration. Tier thresholds come from
the same configuration version.

A strategy without a declared commander affinity gets no implicit factor: it
returns no score and cannot be ranked as strategy-specific synergy. This is
reported separately from ineligibility reasons.

## Eligibility and Availability

Format legality and color identity are filters, not points. Missing legality or
identity data makes a card ineligible until the data is known. In collection-
only mode, unknown or zero availability also makes a card ineligible. Copies
already used are subtracted from physical quantity but do not change a card's
intrinsic synergy score.

The score never replaces available quantity, mana curve, legality, or color
identity. Stable ordering uses eligibility, affinity presence, descending
score, descending availability, normalized name, and Oracle ID.

## Explanation and Limitations

The result includes components, affinity factor, tier, contribution reasons,
and ineligibility reasons. Contributions explain the rounded score within the
configured precision. Caps for generic evidence, type-line matches, and weak
rules are applied after the formula and shown in the reasons.

## Collection Integration

The Suggestions page reads cards and physical quantities from the collection,
excludes the commander, and applies legality, color identity, already-used
cards, and required roles before showing rankings. Results include score, tier,
detected roles, evidence, and remaining quantity. Only classifications whose
fingerprint still matches card data and the current rules are scored. The page
offers an explicit action to reclassify only cards in the collection; opening
the app does not trigger reclassification.

Commander affinities can be edited on this page and are persisted with a manual
source. Strategy, tag, and role overrides are linked to a card's Oracle ID,
require a reason, and remain separate from automatic evidence. Contradictory
exclusions are shown as conflicts; removing an override restores the automatic
classification.

The core role catalog contains `spot_interaction`, `mass_interaction`, `ramp`,
and `card_advantage`. Heuristic role evidence is calculated from Oracle text for
deck selection and Suggestions. Card draw and recursion remain mechanics: a
one-for-one replacement or one-shot recovery is not automatically card
advantage. `main_strategy` is a dynamic deckbuilding role derived from evidence
matching the selected strategy's tag affinities, not a universal card label.
Catalog role terms remain available for user-configured rules and are reviewable.

The generator also accepts an explicit catalog strategy. In that mode,
`build_deck` scores legal, color-identity-compatible cards available in the
collection using current classifications and overrides. The score is
normalized to `[0, 1]` using the configured scale and contributes a weight of
`0.25` alongside core-role, dynamic `main_strategy`, and mana-curve balance.
The generator updates those role counts as it selects cards. Suggestions can
optionally apply role coverage to requested core roles. For lands, strategy
score only breaks ties with the same structural color-coverage result, utility,
and tapped-land status. The initial soft target for dynamic `main_strategy` is
configurable in `config/scoring.json` (currently half the nonland slots); it
affects ranking but cannot prevent deck completion.

Guided mode requires an explicit commander/strategy affinity. Cards without a
current classification are not scored or selected; their count is reported,
and classifications are not persisted automatically. The CLI uses legacy mode
when `--strategy` is omitted, as does Deck Builder unless catalog mode is
selected. Final generation remains subject to the existing Commander
validator.

User priority adjustment represents a preference, not evidence about a card.
Weights and caps are initial values that need validation through real use; they
are not empirically calibrated. Suggestions does not modify the decklist or
provide manual decklist editing.
