# Card Advantage Engine Implementation Guide

## Implementation status

The first increment lives in `src/mana_architect/card_advantage.py`.
Pass the versioned Oracle parser output and card metadata to `compile_profile`,
then pass a supported cast line to `evaluate_cast`. It evaluates casts from hand
through immediate consequences under the documented assumptions. Coverage
includes fixed draw spells, scry followed by fixed draw, and self-enters fixed
draw with optional evoke casting mode. Unsupported shapes return a reason and
no exact lines. Deck ranking and collection classification still use their
existing heuristics. The sections below describe the target architecture.

The second increment adds event-based lines for Rhystic Study and Skullclamp
and board-snapshot exchange for Wrath of God. Engine evaluation assumes the
source is already in play and the supplied opportunities are legal. Token
losses stay separate from strict stock. Deck forecasts and broader effective
resource valuation are still pending.

## 1. Scope and integration

Build a deterministic evaluator between the Oracle Text Parser and the deck builder. The parser produces structured abilities and events. The evaluator calculates their resource effects, finds mechanically compatible interactions, and estimates how often those interactions matter in the proposed deck. The strategy catalog supplies deck priorities and assumptions, such as token expendability and the expected supply of sacrifice fodder.

```text
Versioned Oracle data → Oracle Text Parser → Compiled ability profiles
                                                ↓
Collection + legal deck candidates → Interaction index → Scenario evaluator
                                                ↓
Commander + deck + strategy policy → Marginal scores → Deck builder
```

The deck builder owns legality, color identity, singleton and collection limits. The evaluator accepts cards that pass those gates, including the commander as a distinct availability source. AI can propose parsing rules, interaction templates, or explanations. Route proposals through the same schema validation, tests, and versioned rule registry before they influence ranking. Generate ranking explanations from the recorded ledger and matched rules.

## 2. Accounting model

Evaluate a **line** from a declared starting state to a declared checkpoint. A line is a legal sequence of actions, events, and resolutions with explicit assumptions. For intrinsic one-shot fixtures, the checkpoint follows successful resolution and its immediate consequences. Deck forecasts use a fixed turn horizon and keep unresolved or delayed effects visible. Always report which checkpoint and resolution assumptions were used.

### Strict card advantage

Track the stock of physical cards in hand or represented by nontoken permanents on the battlefield, attributed to the player who holds or controls them. Count each card at most once. This is an implementation convention for comparing lines. It counts a played creature as a retained card even if the creature has low board utility. Library, stack, graveyard, exile, and command zone contribute zero to strict stock. Apply the following rules.

| Transition | Strict effect |
|---|---|
| Library → hand by draw or search | +1 per card delivered to the recipient |
| Hand → stack → graveyard or exile | −1 for the spent card, posted once |
| Hand → stack → battlefield | 0 across the complete cast. Record the retained permanent. |
| Battlefield → graveyard, exile, or command zone | −1 for the former controller, unless another counted transition offsets it |
| Graveyard or exile → hand or battlefield | +1 on actual entry into the counted zone |
| Battlefield → hand | 0 if the same player retains the card. Record the change in board presence separately. |
| Battlefield → exile → battlefield | 0 if the same player retains the card at the checkpoint. Preserve both events for trigger matching. |
| Change of control of a nontoken permanent | −1 for the former controller and +1 for the new controller |
| Token, spell copy, or copy permanent unrepresented by a physical card | 0 in strict stock. Track board utility or resolve its effects separately. |

Keep the hand and board components visible. Compute `self_delta` and `opponent_deltas[player_id]` separately. Pairwise exchange against opponent `i` is `self_delta - opponent_deltas[i]`. Expose the aggregate exchange `self_delta - sum(opponent_deltas)` for table analysis. Use a configured weighted opponent component in ranking to control multiplayer scaling. Temporary moves can cross checkpoints. Carry forward the pending return or permission and preserve its actual controller and expiry.

Count a physical card once even if it generates multiple events. A blink creates a new game object on return. Preserve the physical card ID across the move and issue a new object ID for rules interactions. Costs are paid on their actual events, including costs paid before a spell is countered. Count starting setup only if it occurs within the line. For example, an already-present Mulldrifter is part of the opening state of a blink line.

### Effective card advantage and other resources

Track the utility of a line using typed resource deltas. Effective advantage can include realized strict gains and benefits such as usable temporary access, token bodies, and suppression of an opponent's board. Track mana, life, selection quality, and tempo as separate axes. A mana discount or Treasure used for mana contributes to the mana axis. An available Treasure can also be modeled as sacrifice fodder in an alternative line that spends it that way.

For a resolved line, a possible scalar is:

```text
effective_ca = self_delta - Σ(opponent_weight[i] × opponent_delta[i])
             + Σ(card_equivalent_weight[k] × residual_resource_delta[k])
```

Use signed residuals for benefits and costs absent from strict stock, such as producing or sacrificing creature tokens. An expired access grant contributes zero. An access grant already consumed to put a permanent on the battlefield is accounted for by the resulting transition. If access lets a player cast an instant from exile, that cast delivers its resolved effects without a hand-card debit. Its access slot receives no additional credit. A forecast can estimate value from the possible realized outcomes before the card is played. Keep selection and mana out of the card-advantage scalar even when the deck builder uses them in overall ranking.

Use one entry per realized benefit. An opponent's physical card removed from the battlefield already appears in strict exchange. A separately recorded board delta may inform a tempo score, with a declared weighting policy. A reanimation event that adds a permanent already earns its strict stock gain. A card tag, a permission to use the card, and the resulting transition are descriptions of the same value chain.

## 3. Evaluation dimensions

- **Intrinsic** measures the card's own line without a specific external payoff or enabler. List alternative casting modes separately. Report the value conditional on the stated resolution assumptions.
- **Conditional** models triggers, targets, costs, opponent choices, and other prerequisites with explicit branches. A repeatable draw engine can have an intrinsic deployment line and conditional per-trigger lines.
- **Synergistic** measures the marginal value of supplying or exploiting a mechanical dependency. Evaluate paired scenarios with identical exogenous events and compatible starting conditions, then compare the lines. An Ephemerate line compared with leaving an already-present Mulldrifter alone has a different baseline from a line that starts with both cards in hand.

A profile can expose all three dimensions. Treat them as views of the same scenario outcomes. For example, Skullclamp's draw from a sacrificed equipped creature can be both conditional and synergistic. Count it once in the scenario ledger. Report `positive_in_line` for a specific result, `positive_expected` for a modeled forecast, and `unknown` when the required evidence is missing. A positive upper bound alone establishes potential.

## 4. Mechanical taxonomy and scoring rules

Tags are multi-valued and describe the mechanism. The ledger determines the sign.

| Tags | Implementation rule |
|---|---|
| `direct_draw`, `cantrip` | Resolve quantity and recipient. For a spent one-shot that draws one and spends no additional cards, strict self delta is zero. A retained permanent that draws one is +1. Resolve X and count expressions from the relevant game state. |
| `card_selection` | Scry, surveil, reveal, and look-only effects have zero stock gain. A look-and-keep effect gains only cards actually moved to hand. Looting and rummaging count the draw, discard, cast cost, and their order separately. |
| `impulse_draw` | Create a permission for each eligible exiled card with actor, permitted action, deadline, timing, costs, land-play constraints, and any spending restrictions. No stock gain on exile. Forecast usage under the mana curve and deadline, using joint constraints if multiple cards compete for mana or land plays. |
| `tutoring` | Resolve target restrictions, eligible pool, destination, and number delivered. A spent one-card tutor to hand is usually zero if it finds. A tutor to top produces selection and no immediate strict gain. A tutor to battlefield adds a physical permanent. A tutor to graveyard can supply a recursion target. |
| `recursion`, `reanimation` | Count the actual recovered card in its destination. A spent spell returning one card to hand or battlefield is usually zero in strict stock. Record the returned permanent's mana and tempo implications separately. Temporary returns have pending expiry or sacrifice. Include the cost of filling the graveyard if it occurs in the line. |
| `multi_for_one` | Count actual opposing cards lost, own collateral, and the source debit. Evaluate legal targets, surviving permanents, destination replacements, and opposing access. Bounce to the same player's hand is ordinarily a tempo effect. A wipe needs a board scenario. |
| `token_generation` | Emit typed token entries with characteristics and duration. Strategy weights distinguish creatures, temporary copies, Clues, and Treasure. When a Clue is sacrificed to draw, post the token loss and the actual draw. |
| `repeatable_draw`, `triggered_value`, `etb_value` | Model deployments, surviving windows, eligible event counts, activation capacity, costs, optional choices, and payoff resolution. Distinguish natural triggers from triggers induced by another card. |
| `resource_conversion` | Preserve each input and output separately. Draw two after discarding one gives +1 for the ability before accounting for its source. Sacrificing a physical creature reduces strict stock. Sacrificing a token reduces effective resources. |
| `spell_copy`, `free_casting` | Follow actual copies or casts and resolve their effects. A copy produces no physical card. A copy placed on the stack normally supplies no cast event. A free cast saves mana and can produce card advantage through resolved effects or a counted zone entry. Apply card-specific wording. |

An effect that grants an opponent cards produces deltas for that opponent. Evaluate each eligible target and recipient. Track simultaneous outcomes as a batch so that “one or more,” “each,” “up to,” and “once each turn” preserve their distinct cardinalities.

## 5. Mechanical interaction rules

Compile producer and consumer signatures. Match event kind, source and destination zones, actor and controller relations, object type, required choices, timing, quantity, costs, and limits. An enabler should supply a reachable event with legal objects and resources. A strategy label can change priority after the match. It cannot establish compatibility.

| Enabler → payoff | Required match and accounting |
|---|---|
| Blink → enters ability | Enabler can target the permanent, return succeeds, and the relevant player controls the resulting trigger. The returning permanent must meet the enters filter. A token leaves and cannot return. A creature owned by an opponent may return to that opponent. Rebound is a separate optional cast with its own legal target. |
| Sacrifice outlet → dies payoff | A legal creature is available, the outlet can be used, and the creature reaches a graveyard from the battlefield. A token can die. A replacement sending it to exile can leave the sacrifice event intact while suppressing the dies event. Charge the sacrificed resource once and resolve every distinct eligible trigger. |
| Token producer → sacrifice or draw engine | Token type, eligibility, controller, and timing must satisfy the outlet and payoff. Bound repetitions by actual token supply and activation resources. |
| Self-mill or discard → recursion | The card actually reaches the graveyard, satisfies the recursion filter, and remains available at resolution. Moving a card from library to graveyard changes potential access and adds zero strict stock. Discard from hand debits strict stock. |
| Extra land entries → landfall | Check land type, controller, timing, and the enters batch. A land entering from hand changes hand and board components without increasing strict stock. The landfall reward is its own event. |
| Spell casts or copies → cast payoff | Match the actual cast event, source zone, types, and controller. A spell can be countered after it triggers a cast payoff. Copying a spell directly on the stack does not supply a cast. |
| Trigger multiplier or replacement → existing payoff | Match the event or ability the modifier actually affects and apply its scope and limits. A replacement modifies the original event. An additional trigger creates a distinct trigger instance. |

Use ordered replay for mixed interactions. For example, Mulldrifter can enter via evoke, and an immediate blink before the evoke sacrifice trigger resolves can return a new object. The pending sacrifice of the old object does not sacrifice the returned creature. The initial enters trigger still exists. Report this as a distinct line beginning before deployment, with its own costs and timings. For delayed blink, represent the wait and the possibility that the return fails. Stop recursive exploration at the scenario horizon and a deterministic event budget. Detect repeated loop states and emit a loop or truncation flag. Claim an unbounded loop only when a supported proof establishes repeatability, costs, choices, and the relevant game constraints.

## 6. Contracts and schemas

Use stable Oracle identifiers for card definitions. Use printing identifiers for collection ownership. Include face and ability IDs. Store the Oracle snapshot, parser and evaluator versions, strategy policy, and scenario-set version in result provenance. The following contract is language-neutral. Use tagged unions for expressions and operations so unsupported constructs can be carried explicitly.

```ts
type Quantity =
  | { kind: "fixed"; value: number }
  | { kind: "variable"; binding: string }
  | { kind: "count"; selectorId: string; snapshot: "event" | "resolution" }
  | { kind: "unknown"; reason: string };

type Zone = "library" | "hand" | "stack" | "battlefield" |
            "graveyard" | "exile" | "command";
type Selector = {
  zones: Zone[];
  actorRelation: "self" | "opponent" | "any";
  objectTypes: string[];
  actorField: "owner" | "controller" | "affected_player";
  predicateIds: string[];
};
type Ability = {
  id: string;
  source: { oracleId: string; faceId: string; textSpan: [number, number] };
  kind: "spell" | "activated" | "triggered" | "static" | "replacement";
  event?: { kind: string; selector: Selector; multiplicity: "each" | "batch" };
  costs: Cost[];
  branches: { guardIds: string[]; choiceId?: string; operations: Operation[] }[];
  tags: string[];
  coverage: "supported" | "partial" | "unsupported";
};
type Cost = {
  kind: "mana" | "life" | "tap" | "discard" | "sacrifice" | "exile";
  quantity: Quantity;
  selectorId?: string;
  manaPattern?: string;
};
type Operation =
  | { kind: "draw"; recipientId: string; quantity: Quantity }
  | { kind: "move"; selectorId: string; to: Zone; quantity: Quantity }
  | { kind: "grant_access"; selectorId: string; permissionId: string }
  | { kind: "create_token"; definitionId: string; quantity: Quantity }
  | { kind: "rule_action"; actionId: string; bindingIds: string[] }
  | { kind: "unknown"; reason: string };

type LedgerEntry = {
  eventId: string;
  parentEventId?: string;
  abilityId: string;
  playerId: string;
  physicalCardId?: string;
  objectId?: string;
  from?: Zone;
  to?: Zone;
  resource: string;
  delta: number;
};
type Interaction = {
  ruleId: string;
  producerAbilityId: string;
  consumerAbilityId: string;
  bindingIds: string[];
  requiredPredicateIds: string[];
  useLimitId?: string;
};
type ScenarioResult = {
  scenarioId: string;
  checkpointId: string;
  baselineScenarioId?: string;
  status: "exact" | "bounded" | "unknown";
  tags: string[];
  assumptions: string[];
  selfDelta: number | null;
  opponentDeltas: Record<string, number | null>;
  effectiveCA: number | null;
  bounds?: {
    selfDelta?: [number, number];
    opponentDeltas?: Record<string, [number, number]>;
    effectiveCA?: [number, number];
  };
  ledger: LedgerEntry[];
  pendingEffectIds: string[];
  unresolvedPredicateIds: string[];
  interactionRuleIds: string[];
};
```

Resolve guards, selectors, action IDs, and permission IDs through the versioned rule registry. A permission record includes beneficiary, permitted cards, actions, expiry, timing overrides, cost treatment, and remaining uses. Bind quantities to nonnegative integers once the applicable state is known. Keep selected cast modes, alternative costs, activated costs, and effect choices explicit. Preserve source spans for diagnostics. Validate references, enum values, probabilities, and units at the boundary. An unsupported operation that can affect the answer yields a bounded result if valid bounds are available, otherwise unknown. Supported partial ledger entries can still be returned.

## 7. Deck context and scoring

Use deck composition and scenario assumptions to estimate joint availability. Inputs include draw and tutor access, colors available by turn, curve, repeatable outlets, compatible payoff density, commander deployment and tax, eligible targets or fodder, opponent count, expected spell volume, and the score horizon. Separate access to a card from an ability to deploy and retain it. A commander in the command zone is available under casting rules, with mana, timing, and commander tax still required.

Use deterministic weighted scenarios. For a baseline model, hypergeometric draws can estimate access to a tagged group in a known deck. Evaluate overlaps with disjoint categories or a joint draw model. If two cards must coexist, use their joint availability and legal sequencing. Multiply marginal probabilities only where independence is explicitly modeled and justified. Use user-supplied or versioned strategy priors for unknown opponent behavior, and show a sensitivity range when a prior drives the ranking. For Rhystic Study, condition the chance of payment on each modeled cast and ability resolution. An opponent casting a spell and paying produces zero draw. A tax paid belongs on a separate mana-pressure axis.

For candidate ranking, compare deck configurations with the same number of slots. Use the supplied replacement card when available. Otherwise use an explicitly declared reference slot with zero modeled contribution. Apply the same scenario weights, horizon, policy, and exogenous inputs to both configurations.

```text
marginal_ca(candidate) = Σ p(s) × [effective_ca(with_candidate, s)
                                  − effective_ca(reference, s)]
deck_score_delta = w_ca × marginal_ca
                 + w_selection × marginal_selection
                 + w_mana × marginal_mana
                 + w_tempo × marginal_tempo
```

Configure the axis weights by strategy. Keep strict values and realized ledger entries available alongside the score. Record coverage and unknown scenario mass. Exclude unknown results from an exact point estimate or bound them explicitly, and expose the policy to the caller. Avoid redistributing unknown probability mass across known outcomes. Attribute synergy using paired differences, with stable ordering or an explicit shared attribution policy when several cards enable the same event. Never sum standalone card scores and pair bonuses as a substitute for deck replay.

Index consumers by event kind and coarse selector, then apply full compatibility checks to shortlisted pairs. Recompute dependencies and marginal scores as cards are added or removed. Cache compiled profiles by Oracle snapshot, card, and rule versions. Cache contextual results by deck fingerprint, commander, scenario set, evaluator and policy versions. Sort equal scores by stable Oracle ID and mode ID. Include outcome contributors and blocking prerequisites in the explanation.

## 8. Reference evaluation loop

```text
compile(card, snapshot, registry):
    ast = oracle_parser.parse(card, snapshot)
    profile = registry.normalize(ast)
    validate_references_and_units(profile)
    return profile

evaluate(profile_set, scenario):
    state = copy(scenario.initial_state)
    initial_stock = strict_stock(state)
    queue = seed_actions_and_exogenous_events(scenario)
    ledger = []

    while queue has events within checkpoint and event budget:
        event = pop_in_legal_game_order(queue, scenario.choice_policy)
        legal_branches = check_costs_targets_and_guards(state, event)
        branch = choose_or_branch(legal_branches, scenario)
        applied_batch = apply_replacements_and_resolve(state, branch)
        append_unique_transitions(ledger, applied_batch)
        apply_state_based_actions_until_stable(state, ledger)
        enqueue_new_triggers_and_delayed_effects(queue, state, new_events_only)

    final_stock = strict_stock(state)
    deltas = final_stock - initial_stock
    reconcile(deltas, ledger)
    residuals = value_uncredited_resources(state, ledger, scenario.policy)
    return explain(deltas, residuals, pending_events(queue), coverage)

score_candidate(candidate, reference, context):
    scenarios = build_joint_scenarios(context, candidate, reference)
    for scenario in scenarios in stable order:
        replay both configurations against the same exogenous inputs
        record the paired differences or valid bounds
    return aggregate_with_coverage_and_stable_ties()
```

Process state-based actions and gather waiting triggers before the next priority point. Use the correct event snapshot for leaves and dies triggers. Each actual trigger instance is keyed by ability, triggering event or batch, and occurrence, with any distinct additional triggers represented separately. Ledger posting is idempotent per atomic resource transition. A replacement that alters a destination posts the final transition once. An immutable ledger can be replayed to verify the stock deltas independently of the ranking code.

## 9. Regression fixtures

Pin card definitions and fixture states to a specific Oracle and rules snapshot. For the successful fixtures, assume legal targets, enough mana, a sufficient library, and no unrelated effects unless stated. Start before the evaluated cast unless the row declares an existing permanent. `S` is strict self delta. `O` is the relevant opponent's strict delta, with other opponents at zero.

| Card and line | Expected result |
|---|---|
| **Divination** resolves | Draw 2, spend 1. `S = +1`. If countered after casting, `S = −1`. |
| **Opt** resolves | Scry contributes selection. Draw 1, spend 1. `S = 0`. |
| **Mulldrifter**, normal cast | Draw 2 and retain the creature. Hand delta +1, board delta +1, `S = +2`. |
| **Mulldrifter**, evoke without intervention | Draw 2 and lose the creature to the evoke sacrifice. Hand delta +1, terminal board delta 0, `S = +1`. Evoke uses its own mana cost. |
| **Ephemerate** targeting an owned nontoken creature with no payoff, both casts resolve | Net zero from creature movement and −1 for the spell. `S = −1`. Protection can have value in a separate scenario with a threat and a counterfactual. |
| **Ephemerate** with an already-present, owned Mulldrifter | First blink only gives `S = +1`. With both casts and both enters triggers resolving, `S = +3`. Charge Ephemerate once. If the first cast fails to resolve, `S = −1` and no rebound is scheduled. |
| **Skullclamp**, deployed and retained with no equipped death | `S = 0`. Later attach it to an unmodified 1/1 you control and let the equipped creature die from zero toughness. A token gives `S = +2` and one token loss. A nontoken gives `S = +1`. Equipping a surviving creature produces zero immediate draw. |
| **Rhystic Study**, deployed and retained | `S = 0` with no eligible cast or with payment on every trigger. One unpaid trigger followed by a chosen, successful draw gives `S = +1`. The triggering spell may be countered without undoing the trigger. |
| **Wrath of God** destroys 1 own and 3 opposing nontoken creatures | `S = −2`, `O = −3`, pairwise exchange +1. Reverse board counts to get `S = −4`, `O = −1`, exchange −3. An empty board yields −1. Indestructible creatures survive and tokens use the separate ledger. |

Add synthetic fixtures that isolate parser and evaluator boundaries.

- One-shot draw two with an additional discard cost gives `S = 0` on resolution and `S = −2` if countered after the cost is paid.
- A spent tutor to hand gives zero when it finds one. A tutor to top gives −1 and selection. A legally failed search to hand gives −1. A one-card reanimation spell retrieving a legal nontoken target gives zero strict stock plus a board and mana profile.
- One-shot impulse access to two cards gives −1 at expiry when neither is played. Playing one exiled land and letting the other expire yields zero strict stock if the land is retained. With one remaining land play, two exiled lands cannot both be credited.
- Blink of a token produces no return and no enters trigger from the target. Sacrifice of a token that reaches the graveyard triggers an eligible dies ability. Sacrifice with an exile replacement does not. A “one or more” payoff triggers once for a qualifying simultaneous batch.
- A spell copy on the stack gives zero cast triggers. Copying Divination can produce two more draws on resolution with no new physical spell card spent. A mana-only discount contributes zero strict stock and zero effective card advantage.
- Test mandatory and optional effects, X equal to zero and above zero, empty or insufficient libraries, draw prevention and replacement, illegal targets, modal exclusivity, once-per-turn limits, commander zone decisions, and unsupported text. Represent loss conditions such as a failed mandatory draw from an empty library explicitly.
- Test one legal fodder object offered to two outlets, two distinct payoffs on one death, overlapping interaction templates for one trigger, and deterministic termination for a repeatable loop. Verify that a high strategy weight cannot override a failed mechanical guard.

## 10. Delivery phases and acceptance criteria

| Phase | Deliverable | Acceptance criteria |
|---|---|---|
| 1. Parser contract and ledger | Versioned profiles, event schema, strict stock evaluator, source spans and diagnostics | Divination, Opt, and both Mulldrifter modes pass with exact ledger reconciliation. Unsupported clauses surface with coverage. The same inputs produce identical normalized output. |
| 2. Conditional and effective evaluation | Choices, typed access, token accounting, recursion, targeted exchange, engine forecasts | Rhystic Study, Skullclamp, Wrath of God, tutor, and impulse fixtures pass. Mana-only benefits produce zero card advantage. All baseline and forecast results expose assumptions and the checkpoint. |
| 3. Interaction evaluation | Producer/consumer index, legal bindings, trigger attribution, ordered replay | Both Ephemerate paths and sacrifice replacement fixtures pass. False matches for token blink, invalid controllers, depleted fodder, and copy-versus-cast are rejected. Shared payoff events count once. |
| 4. Deck integration | Context scoring, cache keys, candidate explanations and versioning | A blink candidate receives positive marginal card value in the fixture with accessible draw ETBs and sufficient mana, and receives zero synergy credit with incompatible targets. In a Skullclamp fixture, increasing usable fodder increases expected draw when equip mana and opportunities suffice. A counterexample with unusable fodder leaves that forecast unchanged. Ranking is reproducible and legal deck constraints hold. |
| 5. Calibration | Versioned scenario priors, weights, and a manually reviewed sample of collection cards | Review errors by mechanism and publish fixture coverage, unknown rate, false-positive counts, and runtime on a fixed collection and scenario set. Set project-specific thresholds before enabling the module in ranking. Preserve representative negative fixtures for every supported mechanism. |

## Rules references

- Use the Comprehensive Rules and official Oracle data as the authority for zones, new objects, triggers, replacements, costs, state-based actions, and card definitions. [Magic rules](https://magic.wizards.com/en/rules) · [Gatherer](https://gatherer.wizards.com/)
- Use the official release notes for concrete timing and controller examples for rebound, evoke, and Rhystic Study. [Ephemerate rulings](https://magic.wizards.com/en/news/feature/marvel-super-heroes-release-notes) · [Evoke rulings](https://magic.wizards.com/en/news/feature/commander-masters-release-notes) · [Rhystic Study rulings](https://magic.wizards.com/en/news/feature/wilds-of-eldraine-release-notes)
