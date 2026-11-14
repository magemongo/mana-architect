# Deterministic Synergy Scoring

## Mechanical candidate evaluation

`evaluate_synergy` scores one card against the commander's Oracle abilities
and optional weighted strategies. `rank_collection_candidates` applies the
same evaluation to owned cards, using Commander legality and color identity
from the synchronized collection. The `evaluate-candidates` CLI command
returns that batch as JSON, with scores, source offsets, rule IDs, and unmet
dependencies. It also includes independent Ramp, Card Advantage, and
Interaction profiles for each returned card.

The overall score combines direct commander synergy at 40%, strategy affinity
at 35%, cross-strategy bridges at 15%, and reliability at 10%. The evaluator
uses the strongest supported contribution for each strategy and favors that
specialist score when combining strategies. A bridge needs evidence for
distinct objectives on the same event. Conditional and potential matches are
discounted, and unsupported or incompatible matches contribute zero.
Coverage reports unresolved Oracle clauses separately from strength.

Subtype-specific commander abilities use creature type lines, printed token
subtypes, and recognized changeling abilities. The evaluator treats a subtype
buff as contingent on both permanents being in play. Combat-damage payoffs
also require a qualifying creature to connect, with stronger evidence from
recognized evasion such as flying or unconditional unblockability. The
trigger's resolution text retains any additional condition for drawing cards.
Token creation from a supported death replacement effect retains the required
event, so it receives conditional affinity and earns no validated bridge.

Giada's entry replacement gives matching Angel creatures, Angel tokens, and
changelings conditional commander synergy. A qualifying creature's own
+1/+1-counter trigger can score an additional interaction. For Exemplar of
Light, the counters it enters with can trigger its draw ability while Giada
is already on the battlefield. The count includes Giada.
[Foundations release notes](https://magic.wizards.com/en/news/feature/foundations-release-notes)

The mechanical strategy definitions are in `config/strategy_synergy.json`.
The expanded themes also use the same extracted capabilities as evidence in
saved classifications. The [EDHREC theme coverage](edhrec-themes.md) lists the
themes available in both ranking paths.
Interaction rules live in `synergy_interactions.py`, with scoring defaults in
`strategy_synergy.py`. The parser preserves evidence from supported
[Oracle ability forms](oracle-text-parser.md#strategy-capability-extraction).

In Suggestions, choose an owned commander to use the same score. The
commander supplies the color identity. Strategies are optional, and selected
strategies can have different priorities. The picker shows strategies defined
in the mechanical catalog. Each card's score details show
the components, matched abilities, dependencies, and unresolved Oracle text.
Commander synergy is evaluated live and saved classifications are optional.
When a core role is selected, its score remains the base and mechanical
synergy supplies the bonus. Role exclusions still apply.

Suggestions also accepts a creature type independently of the selected themes.
With a commander or theme selected, the typal affinity supplies 20% of the
combined synergy score. A typal-only search ranks by typal affinity. The
highest applicable signal sets that affinity, with matching creatures and
changelings at 100%, supported type payoffs at 85%, and matching creature token
creation at 75%. The creature-card filter requires a matching front-face
creature or changeling. Typal evidence is evaluated live.

## Catalog-guided generation

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

Generation combines this score with role, curve, and land priorities. It
applies ownership, legality, color identity, and copy limits during selection.
The [generation reference](deckbuilding-guidelines.md#catalog-guided-generation)
describes the required affinity and classification data.

## Color-identity Suggestions

Without a selected commander, Suggestions reads owned cards and quantities and
applies Commander legality, color identity, and classification filters
before showing rankings. The page accepts zero or more strategies and zero or
one core role. In a strategy-only search, cards need evidence for at least one
selected strategy. A supported core role can be searched by itself.

Current classifications supply strategy and role evidence. Strategy ranking
uses the best score among the selected strategies, based on the strength and
weight of matching tags or direct strategy evidence. Missing or stale saved
classifications are reported and can be refreshed explicitly in Suggestions.

## Core roles

Card Advantage measures strict net gain in cards in hand and on the
battlefield. Supported lines cover fixed draw on resolution, fixed draw when
the card enters, and supported evoke costs. The highest positive net gain
supplies the base score. When the evaluator lacks coverage, an included Card
Advantage classification receives one ranking point. This is a ranking value
with unmeasured net gain. Measured zero or negative lines and manual role
exclusions are omitted. The best selected strategy score divided by 100 is
added to the base. Strategy selection is optional.

Ramp can also be selected without a strategy. Its base is the best supported
Oracle ability, scored on acceleration, persistence, speed, and reliability.
A selected strategy adds the same bonus as in Card Advantage. The result shows
the mechanism, its Oracle evidence, and exact net mana for simple fixed bursts
such as Dark Ritual. Board-dependent quantities remain unknown. Basic lands
and mana-neutral production are excluded by the evaluator. Explicit catalog
role evidence and manual inclusions can supply a one-point fallback, and
manual role exclusions take precedence. The current evaluator covers direct
mana production, land development, and mana tokens.

Spot Interaction and Mass Interaction use the best available variant's score
for the selected scope. A selected strategy adds its score divided by 20,
for up to five ranking points. A card that can perform both roles may rank
differently in each search. The result shows the
mechanism, variant cost, and Oracle evidence. Engine matches with unresolved
scores and manual role inclusions use a one-point ranking fallback. Manual
role exclusions take precedence. Automatic interaction eligibility comes from
the engine's affected scope. Removing attachments from one chosen permanent
qualifies as spot interaction. Casting taxes limited by the spell's origin
retain their interaction evidence and do not qualify for either Suggestions role.

Spot scoring gives reliability and answer quality 55% of the weight combined.
Fixed damage and toughness reduction score lower when the amount limits which
threats they can remove. Activated effects count half the source's known mana
value toward setup. Mass scoring gives reliability and answer quality 35% of
the weight combined. These weights are heuristic.

| Dimension | Spot | Mass |
| --- | ---: | ---: |
| Versatility | 15% | |
| Coverage | | 20% |
| Asymmetry | | 10% |
| Scalability | | 10% |
| Efficiency | 15% | 15% |
| Speed | 10% | 10% |
| Reliability | 30% | 15% |
| Answer quality | 25% | 20% |
| Duration | 5% | |

The Ramp evaluator scores the strongest supported event. Cost reductions,
untaps, and multipliers are outside its current coverage. The strategy bonus
uses the best selected strategy score in color-identity searches and the
overall mechanical score in commander searches. Role searches can run with an
empty strategy selection in either mode.

Per-card manual overrides can affect classification-based ranking. The
[classification storage reference](classification-storage.md) describes how
these decisions are stored alongside automatic evidence.
