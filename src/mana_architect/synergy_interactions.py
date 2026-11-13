"""Directional mechanical interaction rules and their constraint checks."""

from dataclasses import dataclass, replace
from enum import StrEnum
import re

from mana_architect.synergy_capabilities import MechanicalCapability, MechanicalObject

INTERACTION_RULE_VERSION = "1.0.0"


class MatchStatus(StrEnum):
    VALIDATED = "validated"
    CONDITIONAL = "conditional"
    POTENTIAL = "potential"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MechanicalRule:
    id: str
    source: str
    target: str
    relation: str
    event: str
    explanation: str
    object_type: str = ""
    dependency: str = ""
    weight: float = 1.0


INTERACTION_RULES = (
    MechanicalRule("sacrifice_triggers_death_payoff", "sacrifice", "death_payoff",
                   "directly_triggers", "death", "Sacrificing a creature can produce the observed death.",
                   "creature"),
    MechanicalRule("sacrifice_triggers_payoff", "sacrifice", "sacrifice_payoff",
                   "directly_triggers", "sacrifice", "The payoff observes the permanent being sacrificed."),
    MechanicalRule("fodder_feeds_sacrifice_outlet", "create_token", "sacrifice",
                   "enables", "token_creation", "The created creature token can pay the sacrifice cost.",
                   "creature", "sacrifice_created_token"),
    MechanicalRule("treasure_feeds_artifact_sacrifice", "create_token", "sacrifice_payoff",
                   "enables", "sacrifice", "The Treasure can pay its intrinsic tap and sacrifice cost to produce the observed event.",
                   "treasure", "activate_treasure_sacrifice_ability"),
    MechanicalRule("token_multiplier_amplifies_creation", "multiply_token_creation", "create_token",
                   "amplifies", "token_creation", "The replacement applies to this token creation event."),
    MechanicalRule("counter_multiplier_amplifies_placement", "multiply_counter_placement", "put_counters",
                   "amplifies", "counter_placement", "The replacement applies to the qualifying counter placement."),
    MechanicalRule("counter_placement_triggers_payoff", "put_counters", "counter_payoff",
                   "directly_triggers", "counter_placement", "The payoff observes the qualifying counters being placed."),
    MechanicalRule("blink_retriggers_etb", "blink", "enter_payoff",
                   "directly_triggers", "enter", "Exiling and returning the eligible card can cause the observed entry.",
                   dependency="eligible_card_can_leave_and_return"),
    MechanicalRule("recursion_reuses_etb", "return_from_graveyard", "enter_payoff",
                   "recurs", "enter", "Returning the eligible permanent card from the graveyard causes a battlefield entry.",
                   dependency="eligible_card_in_graveyard"),
    MechanicalRule("extra_land_play_enables_landfall", "extra_land_play", "enter_payoff",
                   "enables", "enter", "Playing an additional land produces a land entry when a land is available.",
                   "land", "land_available_to_play"),
    MechanicalRule("land_entry_triggers_payoff", "put_land", "enter_payoff",
                   "directly_triggers", "enter", "Putting a land onto the battlefield can cause the observed entry.",
                   "land"),
    MechanicalRule("spell_copy_amplifies_spell_effect", "copy_spell", "resolve_spell",
                   "amplifies", "spell_copy", "A legal copy of the spell can resolve its effects again.",
                   dependency="eligible_spell_on_stack"),
    MechanicalRule("spell_copy_triggers_payoff", "copy_spell", "cast_or_copy_payoff",
                   "directly_triggers", "spell_copy", "The trigger explicitly observes copying an eligible spell."),
    MechanicalRule("casting_triggers_payoff", "resolve_spell", "cast_payoff",
                   "directly_triggers", "cast", "Casting the eligible spell produces the observed cast event.",
                   dependency="spell_is_cast"),
    MechanicalRule("casting_or_copying_triggers_payoff", "resolve_spell", "cast_or_copy_payoff",
                   "directly_triggers", "cast", "Casting the eligible spell satisfies the cast-or-copy trigger.",
                   dependency="spell_is_cast"),
    MechanicalRule("lifegain_triggers_payoff", "gain_life", "lifegain_payoff",
                   "directly_triggers", "life_gain", "The payoff observes the qualifying player gaining life."),
    MechanicalRule("equipment_enables_voltron", "equip", "single_attacker_payoff",
                   "enables", "attack_alone", "Equipment can support the qualifying attacker after a legal equip activation.",
                   "creature", "equip_then_attack_alone"),
    MechanicalRule("artifact_etb_triggers_payoff", "create_token", "enter_payoff",
                   "directly_triggers", "enter", "The created artifact token enters under the qualifying player.",
                   "artifact"),
    MechanicalRule("token_etb_triggers_payoff", "create_token", "enter_payoff",
                   "directly_triggers", "enter", "The created token enters and satisfies the observed token or creature entry."),
)

_REQUIRED_PAYOFF_TYPES = {
    "artifact_etb_triggers_payoff": "artifact",
    "extra_land_play_enables_landfall": "land",
    "land_entry_triggers_payoff": "land",
}


def rule_applies_to_capability(rule: MechanicalRule, capability: MechanicalCapability) -> bool:
    if capability.id == rule.target:
        required_type = _REQUIRED_PAYOFF_TYPES.get(rule.id)
        if required_type and required_type not in capability.object.types:
            return False
        if rule.id == "token_etb_triggers_payoff":
            return capability.object.kind == "token" or "creature" in capability.object.types
    if capability.id == rule.source and rule.object_type == "treasure":
        return "treasure" in capability.object.subtypes
    return capability.id in {rule.source, rule.target}


@dataclass(frozen=True)
class RuleMatch:
    status: MatchStatus
    explanation: str
    dependencies: tuple[str, ...] = ()


def _compatible_object(produced: MechanicalObject, required: MechanicalObject) -> RuleMatch | None:
    if required.kind in {"token", "spell", "player"} and produced.kind != required.kind:
        if produced.kind in {"unknown", "permanent"}:
            return RuleMatch(MatchStatus.UNKNOWN, "The source does not specify the required object kind.")
        return RuleMatch(MatchStatus.INVALID, "The event concerns a different object kind.")
    available, needed = set(produced.types), set(required.types)
    if needed and available and not available & needed:
        return RuleMatch(MatchStatus.INVALID, "The object types are incompatible.")
    if "all_listed_types" in required.restrictions and not needed <= available:
        return RuleMatch(MatchStatus.UNKNOWN, "The object must have all listed types.")
    if needed and not available:
        return RuleMatch(MatchStatus.UNKNOWN, "The source does not specify the required object type.")
    if required.subtypes and not set(required.subtypes) & set(produced.subtypes):
        status = MatchStatus.INVALID if produced.subtypes else MatchStatus.UNKNOWN
        return RuleMatch(status, "The required object subtype is unresolved or incompatible.")
    if "nontoken" in required.restrictions and produced.kind == "token":
        return RuleMatch(MatchStatus.INVALID, "The qualifying object must be nontoken.")
    if "noncreature" in required.restrictions and "creature" in available:
        return RuleMatch(MatchStatus.INVALID, "The qualifying object must be noncreature.")
    if "nonland" in required.restrictions and "land" in available:
        return RuleMatch(MatchStatus.INVALID, "The qualifying object must be nonland.")
    remaining = set(required.restrictions) - {"nontoken", "noncreature", "nonland", "all_listed_types"}
    if remaining:
        return RuleMatch(MatchStatus.UNKNOWN, "Unresolved object restrictions.", tuple(sorted(remaining)))
    return None


def _card_object(type_line: str, kind: str = "card") -> MechanicalObject:
    types = re.findall(r"\b(?:artifact|battle|creature|enchantment|instant|land|planeswalker|sorcery)\b",
                       type_line.casefold().split(" // ")[0].split(" — ")[0])
    return MechanicalObject(kind, tuple(sorted(set(types))), reference="self")


def validate_interaction(
    rule: MechanicalRule,
    source: MechanicalCapability,
    target: MechanicalCapability,
    *,
    source_type: str = "",
    target_type: str = "",
    same_card: bool = False,
) -> RuleMatch | None:
    """Check the event and object contracts for a pair of explicit capabilities."""
    if (source.id, target.id) != (rule.source, rule.target):
        return None
    if not rule_applies_to_capability(rule, source) or not rule_applies_to_capability(rule, target):
        return None

    dependencies = set(filter(None, (rule.dependency,)))
    producer, observer = (target, source) if rule.relation == "amplifies" else (source, target)
    produced, required = producer.object, observer.object
    controller = producer.controller
    required_controller = observer.controller
    if producer.object.reference == "self" and producer.id != "resolve_spell":
        card = _card_object(target_type if producer is target else source_type, kind="permanent")
        produced = replace(produced, types=card.types)
    if rule.id == "spell_copy_amplifies_spell_effect":
        required_controller = source.resolution
    if rule.id == "equipment_enables_voltron":
        if target.object.reference == "self":
            produced = _card_object(target_type, kind="permanent")
        required_controller = target.controller
    if rule.id in {"blink_retriggers_etb", "recursion_reuses_etb"} and target.object.reference == "self":
        card = _card_object(target_type)
        if not card.types:
            return RuleMatch(MatchStatus.UNKNOWN, "The returning card's types are missing.")
        if mismatch := _compatible_object(card, produced):
            return mismatch
        produced = card
        required = card
        if source.id == "blink" and source.resolution == "its owner's":
            controller = "you"
        if source.id == "blink" and source.controller == "opponent":
            return RuleMatch(MatchStatus.INVALID, "The blink requires a permanent controlled by an opponent.")
        if source.id == "return_from_graveyard" and source.owner == "opponent":
            return RuleMatch(MatchStatus.INVALID, "The effect requires a card in an opponent's graveyard.")
    elif required.reference == "self" and rule.id not in {
        "equipment_enables_voltron", "spell_copy_amplifies_spell_effect",
    }:
        if produced.reference != "self" or not same_card:
            return RuleMatch(MatchStatus.INVALID, "The ability observes its own card only.")
    if required.reference == "other" and produced.reference == "self" and same_card:
        return RuleMatch(MatchStatus.INVALID, "The ability requires another object.")

    if rule.object_type == "treasure" and "treasure" not in produced.subtypes:
        return None
    if rule.object_type and rule.object_type != "treasure":
        if rule.object_type not in produced.types:
            if produced.types:
                return RuleMatch(MatchStatus.INVALID, f"The event requires a {rule.object_type} object.")
            if source.id != "sacrifice":
                return RuleMatch(MatchStatus.UNKNOWN, f"The {rule.object_type} type is unresolved.")
            dependencies.add(f"sacrifice_a_{rule.object_type}")
            produced = MechanicalObject(produced.kind, (rule.object_type,), reference=produced.reference)

    if source.id == "blink" and source.object.kind == "token":
        return RuleMatch(MatchStatus.INVALID, "A token cannot return after leaving the battlefield.")
    if rule.relation == "amplifies" and rule.id != "spell_copy_amplifies_spell_effect":
        if source.event != rule.event or target.event != rule.event:
            return RuleMatch(MatchStatus.INVALID, "The replacement requires the same kind of event.")
    if target.action == "observe" and target.event not in {rule.event, "cast_or_copy"}:
        return RuleMatch(MatchStatus.INVALID, "The payoff observes a different event.")
    if source.id == "sacrifice" and (
        source.source_zone != "battlefield" or source.destination_zone != "graveyard"
    ):
        return RuleMatch(MatchStatus.INVALID, "The sacrifice must move the object from battlefield to graveyard.")
    if rule.event == "enter" and source.destination_zone != "battlefield":
        return RuleMatch(MatchStatus.INVALID, "The effect must cause a battlefield entry.")
    if rule.id == "recursion_reuses_etb" and source.source_zone != "graveyard":
        return RuleMatch(MatchStatus.INVALID, "The card must return from the graveyard.")
    if rule.id == "spell_copy_amplifies_spell_effect" and source.source_zone != "stack":
        return RuleMatch(MatchStatus.INVALID, "The spell must be on the stack to be copied.")
    if source.quantity.kind == "fixed" and source.quantity.value == 0:
        return RuleMatch(MatchStatus.INVALID, "The ability produces no qualifying event.")

    if mismatch := _compatible_object(produced, required):
        return mismatch
    if rule.relation != "amplifies" and produced.restrictions:
        if set(produced.restrictions) - {"all_listed_types", "nontoken", "noncreature", "nonland", "basic"}:
            return RuleMatch(MatchStatus.UNKNOWN, "The effect has unresolved object restrictions.",
                             produced.restrictions)
    if controller == "unknown" and required_controller not in {"any", "unknown"}:
        return RuleMatch(MatchStatus.UNKNOWN, "The source does not establish the relevant controller.")
    if required_controller == "unknown" and rule.relation == "amplifies":
        return RuleMatch(MatchStatus.UNKNOWN, "The replacement's controller restriction is unresolved.")
    if {controller, required_controller} == {"you", "opponent"}:
        return RuleMatch(MatchStatus.INVALID, "The event is attributed to an incompatible player.")
    if controller == "any" and required_controller in {"you", "opponent"}:
        dependencies.add("qualifying_player_controls_object")
    if rule.relation == "amplifies" and target.quantity.kind in {"up_to", "variable"}:
        dependencies.add("positive_event_quantity")
    if source.id == "blink" and target.object.reference != "self" and source.resolution == "its owner's":
        if source.owner != "you" and required_controller != "any":
            dependencies.add("return_under_qualifying_controller")
    if source.id == "sacrifice" and source.object.reference != "self":
        dependencies.add("sacrifice_eligible_permanent")
    if rule.id == "spell_copy_amplifies_spell_effect":
        dependencies.add("legal_copy_target")
    if source.targeting == "target" or target.targeting == "target":
        dependencies.add("legal_target_at_resolution")
    if source.costs or target.costs:
        dependencies.add("pay_ability_cost")
    if (source.timing == "sorcery_speed" and source.costs
            or target.timing == "sorcery_speed" and target.costs):
        dependencies.add("sorcery_timing")
    if source.modality == "optional" or target.modality == "optional":
        dependencies.add("choose_optional_effect")
    if source.quantity.kind in {"up_to", "variable"}:
        dependencies.add("positive_event_quantity")
    dependencies.update(source.conditions)
    dependencies.update(target.conditions)
    status = MatchStatus.CONDITIONAL if dependencies else MatchStatus.VALIDATED
    return RuleMatch(status, rule.explanation, tuple(sorted(dependencies)))
