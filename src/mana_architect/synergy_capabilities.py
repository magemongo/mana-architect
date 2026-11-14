"""Compile card-local mechanics from Oracle ability spans for strategy synergy."""

from dataclasses import dataclass, replace
import re
from typing import Literal

from mana_architect.interaction_rules_catalog import mask_reminder_text
from mana_architect.mana_resource_extractor import extract_mana_resources
from mana_architect.oracle_text import OracleParse
from mana_architect.ramp_rules_catalog import NUMBER_WORDS

CAPABILITY_VERSION = "1.3.0"

# One event can satisfy multiple strategies through its object types. Creating a
# Treasure therefore produces a single capability and a single quantity.
CAPABILITY_IDS = frozenset({
    "create_token", "multiply_token_creation", "sacrifice", "death_payoff",
    "sacrifice_payoff", "enter_payoff", "put_counters", "multiply_counter_placement",
    "double_existing_counters", "counter_payoff", "proliferate", "blink",
    "return_from_graveyard", "recover_to_hand", "self_mill", "discard",
    "extra_land_play", "put_land", "add_mana", "gain_life", "lifegain_payoff",
    "copy_spell", "cast_payoff", "cast_or_copy_payoff", "reduce_spell_cost",
    "untap", "draw", "deal_noncombat_damage", "multiply_damage", "counter_spell",
    "remove_permanent", "restrict_actions", "combat_buff", "extra_combat",
    "grant_haste", "grant_evasion", "equip", "attach_aura", "single_attacker_payoff",
    "attack_payoff", "artifact_count_payoff", "land_count_payoff",
    "token_count_payoff", "protect_creature", "resolve_spell", "grant_lifelink",
    "populate", "additional_draw", "tribal_buff", "combat_damage_payoff",
    "creature_presence", "self_evasion", "mill_opponent", "force_discard",
    "copy_permanent", "extra_turn", "mass_combat_buff", "goad", "take_control",
    "life_loss_opponent", "protect_from_attacks", "keyword_ability",
    "permanent_bounce", "investigate", "venture", "become_monarch",
    "gain_energy", "modified_payoff", "unblockable", "printed_subtype", "printed_supertype",
    "printed_type", "scry", "surveil", "creature_mana",
    "wheel", "commander_payoff", "search_library", "life_drain",
    "typal_entry_counters",
})

_FLAGS = re.IGNORECASE
_NUMBER = r"(?:a|an|one or more|that many|that much|[a-z]+|\d+)"
_SACRIFICE_COUNT = r"(?:one or more|one|two|three|four|five|\d+)"
_TYPES = frozenset({
    "artifact", "battle", "creature", "enchantment", "instant", "land",
    "planeswalker", "sorcery",
})
_TOKEN_TYPES = {
    "blood": "artifact", "clue": "artifact", "food": "artifact",
    "gold": "artifact", "junk": "artifact", "map": "artifact",
    "powerstone": "artifact", "treasure": "artifact", "incubator": "artifact",
}
_THEME_KEYWORDS = re.compile(
    r"(?P<keyword>affinity|annihilator|cascade|convoke|crew|cycling|deathtouch|defender|dredge|"
    r"evoke|exploit|firebending|flash|flashback|flying|foretell|haste|improvise|indestructible|"
    r"infect|kicker|madness|menace|modular|morph|mutate|myriad|ninjutsu|offspring|plot|prowess|"
    r"retrace|squad|storm|suspend|training)\b(?: (?:\{[^}]+\})+| \d+| for [a-z]+|"
    r" \d+—(?:\{[^}]+\})+|—.+| — .+)?"
)
_SUPERTYPES = {"basic", "legendary", "snow", "world", "ongoing"}
_WHEEL = re.compile(
    rf"each player discards? (?:their hand|all the cards in their hand)"
    rf"(?:,? (?:then|and) draws?|\.\s*then each player draws?) (?P<count>{_NUMBER}) cards?\.?"
)
_LIFE_DRAIN = re.compile(
    rf"(?P<target>each opponent|target opponent|target player) loses (?P<count>{_NUMBER}) life"
    rf"(?:,? and you gain|\.\s*you gain) (?P<gain>{_NUMBER}) life\.?"
)
_SELF = r"(?:this (?:creature|permanent|artifact|enchantment|spell|card|land|planeswalker)|@self)"
_BASIC_LANDS = frozenset({"plains", "island", "swamp", "mountain", "forest", "wastes"})
_QUANTITY_WORDS = {"a": 1, "an": 1, **NUMBER_WORDS}
_TRIGGER = re.compile(r"^(?:[^,.:]+? — )?(?:when(?:ever)?|at) (?P<event>[^,]+),\s*", _FLAGS)
_REPLACEMENT = re.compile(r"^if (?P<event>[^,]+), (?P<effect>.*\binstead)\.?$", _FLAGS)
_CLAUSE = re.compile(r"[^.\n]+")
_FACE_OR_LINE = re.compile(r" // |(?<!\S)//(?!\S)|[^\r\n]+?(?= // |(?<!\S)//(?!\S)|[\r\n]|$)")
_CONDITION = re.compile(
    r"\b(?:if|unless|only|as long as|for each|equal to|where|except|rather than)\b", _FLAGS,
)
_DEATH_REPLACEMENT = re.compile(
    r"^if (?P<event>[^,]+ would die),\s+(?:instead )?exile (?:it|that card) and "
    r"(?P<creation>create [^.]+?)(?P<instead> instead)?\.?$",
)
_EFFECT_CONDITION = re.compile(r"^(?P<condition>if [^,]+|for each [^,]+),\s+")
_TRIBAL_GROUP = (
    r"(?:(?:one or more|a|an|each) )?(?P<other>other )?"
    r"(?!(?:artifacts?|battles?|creatures?|enchantments?|lands?|permanents?|planeswalkers?|tokens?) )"
    r"(?P<subtype>[a-z][a-z'-]+?)(?:s| creature[s]?)(?: you control)"
)
_TRIBAL_BUFF = re.compile(rf"{_TRIBAL_GROUP} gets? \+\d+/\+\d+")
_TRIBAL_COMBAT = re.compile(rf"{_TRIBAL_GROUP} deals? combat damage to (?:a player|an opponent)")
_TOKEN_CHARACTERISTICS = re.compile(
    r"(?:[\dx*]+/[\dx*]+ )?(?:(?:white|blue|black|red|green|colorless)(?: and | ))*"
    r"(?P<subtypes>(?:[a-z][a-z'-]* )*)(?:artifact |enchantment )?creature"
)
_MULTIPLE_TOKEN_CLAUSES = re.compile(r"\b(?:and|or) (?:create |(?:a|an|\d+|one|two|three) .+\btokens?\b)")
_EVASION = re.compile(
    rf"(?:(?:{_SELF}) can't be blocked|flying|fear)\.?"
)
_CHANGELING = re.compile(r"^changeling(?:\s|$)", re.IGNORECASE | re.MULTILINE)
_TYPAL_ENTRY_COUNTERS = re.compile(
    r"each other (?P<subtype>[a-z][a-z' -]*?) you control enters(?: the battlefield)? "
    r"with an additional \+1/\+1 counter on it for each (?P=subtype) you already control\.?"
)


@dataclass(frozen=True)
class CapabilityEvidence:
    ability_id: str
    start: int
    end: int
    oracle_span: str
    source_field: str = "oracle_text"


@dataclass(frozen=True)
class MechanicalObject:
    kind: str = "unknown"
    types: tuple[str, ...] = ()
    subtypes: tuple[str, ...] = ()
    reference: str = "any"
    restrictions: tuple[str, ...] = ()


@dataclass(frozen=True)
class MechanicalQuantity:
    kind: Literal["fixed", "variable", "up_to", "unknown"] = "unknown"
    value: int | None = None
    expression: str | None = None


@dataclass(frozen=True)
class MechanicalCapability:
    id: str
    action: str
    event: str
    object: MechanicalObject
    evidence: CapabilityEvidence
    quantity: MechanicalQuantity = MechanicalQuantity()
    source_zone: str = "unknown"
    destination_zone: str = "unknown"
    controller: str = "unknown"
    owner: str = "unknown"
    targeting: str = "none"
    costs: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    timing: str = "unknown"
    repeatability: str = "unknown"
    duration: str = "unknown"
    modality: str = "mandatory"
    resolution: str = "unknown"
    face_type_line: str = ""

    @property
    def event_key(self) -> str:
        return f"{self.evidence.ability_id}:{self.evidence.start}:{self.evidence.end}"

    @property
    def object_types(self) -> set[str]:
        types = set(self.object.types) | {self.object.kind}
        if self.object.reference == "self" and not self.object.types:
            types.update(self.face_type_line.partition(" — ")[0].casefold().split())
        return types


@dataclass(frozen=True)
class MechanicalProfile:
    capabilities: tuple[MechanicalCapability, ...]
    unknowns: tuple[CapabilityEvidence, ...]
    parser_version: str
    catalog_version: str = CAPABILITY_VERSION


def _quantity(text: str | None, *, up_to: bool = False) -> MechanicalQuantity:
    if text is None:
        return MechanicalQuantity()
    value = int(text) if text.isdecimal() else _QUANTITY_WORDS.get(text)
    if value is None:
        return MechanicalQuantity("variable", expression=text)
    return MechanicalQuantity("up_to" if up_to else "fixed", value)


def _object(text: str, *, kind: str = "permanent") -> MechanicalObject:
    words = set(re.findall(r"[a-z]+", text))
    types = tuple(sorted({word.removesuffix("s") for word in words} & _TYPES))
    subtypes = tuple(sorted(words & (_TOKEN_TYPES.keys() | _BASIC_LANDS)))
    if "legendary" in words or "historic" in words:
        subtypes = tuple(sorted(set(subtypes) | (words & {"legendary", "historic"})))
    if "token" in words or "tokens" in words or kind == "token":
        kind = "token"
        types = tuple(sorted(set(types) | {_TOKEN_TYPES[s] for s in subtypes if s in _TOKEN_TYPES}))
    if "card" in words or "cards" in words:
        kind = "card"
    if "spell" in words or "spells" in words:
        kind = "spell"
    reference = "any"
    if re.fullmatch(_SELF, text):
        reference = "self"
    elif "another" in words or "other" in words:
        reference = "other"
    known = (
        {"target", "a", "an", "the", "one", "or", "and", "each", "all", "any",
         "another", "other", "permanent", "card", "spell", "token", "you", "control",
         "controls", "own", "opponent", "opponents", "this", "self", "noncreature",
         "nontoken", "nonland", "basic", "one", "more", "up", "to", "of", "your"}
        | _TYPES | set(subtypes)
    )
    restrictions = set()
    if reference != "self":
        restrictions = {word for word in words if word not in known and word.removesuffix("s") not in known}
    restrictions.update(words & {"nontoken", "noncreature", "nonland", "basic"})
    if "and" in words and len(types) > 1:
        restrictions.add("all_listed_types")
    return MechanicalObject(kind, types, subtypes, reference, tuple(sorted(restrictions)))


def _controller(text: str, default: str = "unknown") -> str:
    if re.search(r"\b(?:opponent controls?|under (?:an|your) opponent's control)\b", text):
        return "opponent"
    if re.search(r"\b(?:you control|under your control)\b", text):
        return "you"
    return default


def _targeting(text: str) -> str:
    if re.search(r"\btarget\b", text):
        return "target"
    if re.search(r"\b(?:each|all)\b", text):
        return "all"
    return "none"


def _evidence(parsed: OracleParse, ability_id: str, start: int, end: int) -> CapabilityEvidence:
    return CapabilityEvidence(ability_id, start, end, parsed.oracle_text[start:end])


def _tribal_group(match: re.Match[str]) -> MechanicalObject:
    return MechanicalObject(
        "permanent", ("creature",), (match["subtype"],),
        reference="other" if match["other"] else "any",
    )


def _match_action(text: str) -> tuple[str, str, MechanicalObject, dict] | None:
    if match := re.fullmatch(
        rf"(?P<actor>target opponent|each opponent|that opponent|target player|each player|that player) "
        rf"(?P<action>mills?|discards?) (?P<count>{_NUMBER}) cards?(?P<scale> for each .+)?", text,
    ):
        mill = match["action"].startswith("mill")
        action = "mill" if mill else "discard"
        return ("mill_opponent" if mill else "force_discard"), action, MechanicalObject("card"), dict(
            event=action, controller="opponent" if "opponent" in match["actor"] else "any",
            source_zone="library" if mill else "hand", destination_zone="graveyard",
            quantity=MechanicalQuantity("variable", expression=match["scale"].strip()) if match["scale"] else _quantity(match["count"]),
        )
    if match := re.fullmatch(
        r"create (?:a|one) token that's a copy of (?P<object>target .+?|@self|that (?:creature|permanent|artifact|enchantment))(?: except .+)?"
        r"|have @self enter(?: the battlefield)? as a copy of (?P<original>(?:any|a|another|target) [^.]+)", text,
    ):
        original = match["original"]
        return "copy_permanent", "copy", _object(original or match["object"]), dict(
            event="copy_as_enters" if original else "token_creation", controller="you",
            source_zone="battlefield", destination_zone="battlefield",
        )
    if match := re.fullmatch(rf".+ deals? (?P<count>{_NUMBER}) damage to (?P<recipient>each opponent|you)", text):
        recipient = {"each opponent": "each_opponent", "you": "self_player"}[match["recipient"]]
        return "deal_noncombat_damage", "deal", MechanicalObject("player", subtypes=(recipient,)), dict(
            event="noncombat_damage", quantity=_quantity(match["count"]),
        )
    if match := re.fullmatch(rf"create (?P<up_to>up to )?(?P<count>{_NUMBER}) (?P<object>.+?)(?: tokens?)(?P<tail>.*)", text):
        obj = match["object"]
        # Color, size, and the printed token name do not constrain a creator's
        # type. Keep copy and multiple-token specifications unresolved.
        if _MULTIPLE_TOKEN_CLAUSES.search(text):
            return None
        token = _object(obj, kind="token")
        characteristics = _TOKEN_CHARACTERISTICS.fullmatch(obj)
        subtypes = set(token.subtypes)
        if characteristics:
            subtypes.update(set(characteristics["subtypes"].split()) - _TYPES)
        tail = match["tail"].strip()
        conditions = () if tail in {"", "with flying", "with haste", "with lifelink", "with vigilance"} else (tail,)
        token = MechanicalObject("token", token.types, tuple(sorted(subtypes)))
        return "create_token", "create", token, dict(
            event="token_creation", destination_zone="battlefield",
            controller="you", owner="you", quantity=_quantity(match["count"], up_to=bool(match["up_to"])),
            conditions=conditions,
        )
    if match := re.fullmatch(rf"sacrifice (?:(?P<count>{_SACRIFICE_COUNT}) )?(?P<object>(?:another |a |an )?(?:artifact|creature|enchantment|land|permanent|token)(?:s| or (?:artifact|creature|land|permanent))?(?: you control)?|{_SELF})", text):
        return "sacrifice", "sacrifice", _object(match["object"]), dict(
            event="sacrifice", controller="you", source_zone="battlefield", destination_zone="graveyard",
            quantity=_quantity(match["count"] or "one"),
        )
    if match := re.fullmatch(rf"put (?P<count>{_NUMBER}) (?P<counter>\+1/\+1|[-−]1/[-−]1|[a-z]+) counters? on (?P<object>.+)", text):
        obj = _object(match["object"])
        return "put_counters", "put", MechanicalObject(
            obj.kind, obj.types, (match["counter"],), obj.reference, obj.restrictions,
        ), dict(event="counter_placement", controller=_controller(text, "you" if obj.reference == "self" else "any"),
                source_zone="battlefield", destination_zone="battlefield", quantity=_quantity(match["count"]))
    if match := re.fullmatch(r"return (?P<object>.+? card) from (?P<owner>your|a|an opponent's) graveyard (?:to|onto) (?P<zone>the battlefield|your hand|its owner's hand)(?: (?P<tail>.+))?", text):
        battlefield = match["zone"] == "the battlefield"
        owner = {"your": "you", "an opponent's": "opponent"}.get(match["owner"], "any")
        tail = match["tail"] or ""
        controller = _controller(tail, "you")
        if "owner's control" in tail:
            controller = owner
        obj = _object(match["object"])
        return ("return_from_graveyard" if battlefield else "recover_to_hand"), "return", obj, dict(
            event="zone_change", controller=controller, owner=owner, source_zone="graveyard",
            destination_zone="battlefield" if battlefield else "hand", quantity=_quantity("one"),
            conditions=(tail,) if tail and tail not in {"under your control", "under its owner's control", "tapped"} else (),
        )
    if match := re.fullmatch(rf"(?:(?P<actor>you|target player|each player|each opponent|an opponent) )?gains? (?P<count>{_NUMBER}) life", text):
        return "gain_life", "gain", MechanicalObject("player"), dict(
            event="life_gain", controller={None: "you", "you": "you", "each opponent": "opponent",
                                           "an opponent": "opponent", "each player": "any"}.get(match["actor"], "unknown"),
            quantity=_quantity(match["count"]),
        )
    if match := re.fullmatch(r"copy (?P<object>(?:target|that) (?:instant or sorcery spell|instant spell|sorcery spell|spell)(?: you control)?)", text):
        return "copy_spell", "copy", _object(match["object"]), dict(
            event="spell_copy", controller="you", source_zone="stack", destination_zone="stack",
            conditions=("eligible_spell_on_stack",),
            resolution=_controller(match["object"], "any"),
        )
    if match := re.fullmatch(
        r"search your library for (?P<object>(?:a|an|up to one) (?!basic land\b)[^,.]*?\bcard),? "
        r"(?:reveal (?:it|that card),? (?:then )?|then )?"
        r"(?:put (?:it|that card) (?:into your hand|onto the battlefield),? (?:then )?shuffle)", text,
    ):
        return "search_library", "search", _object(match["object"]), dict(
            event="library_search", source_zone="library", controller="you", quantity=_quantity("one"),
        )
    for capability_id, action, pattern in _SIMPLE_EFFECTS:
        match = pattern.fullmatch(text)
        if match is not None:
            fields = match.groupdict()
            object_text = fields.get("object") or ""
            obj = _object(object_text) if object_text else MechanicalObject({
                "draw": "card", "discard": "card", "self_mill": "card",
            }.get(capability_id, "unknown"))
            subtype = fields.get("subtype")
            if subtype is None and capability_id == "life_loss_opponent" and text.startswith("each opponent "):
                subtype = "each_opponent"
            if subtype:
                obj = replace(obj, subtypes=(subtype,))
            return capability_id, action, obj, dict(
                controller=_controller(object_text, "you" if obj.reference == "self" or obj.kind == "card" else "unknown"),
                quantity=(MechanicalQuantity("fixed", fields["count"].count("{e}"))
                          if capability_id == "gain_energy" else _quantity(fields.get("count"))),
                costs=(fields["cost"],) if fields.get("cost") else (),
            )
    return None


_SIMPLE_EFFECTS = tuple(
    (capability, action, re.compile(pattern))
    for capability, action, pattern in (
        ("draw", "draw", rf"draw (?P<count>{_NUMBER}) cards?"),
        ("self_mill", "mill", rf"(?:you )?mill (?P<count>{_NUMBER}) cards?"),
        ("discard", "discard", rf"(?:you )?discard (?P<count>{_NUMBER}) cards?"),
        ("proliferate", "proliferate", r"proliferate"),
        ("populate", "populate", r"populate"),
        ("grant_lifelink", "grant", r"(?P<object>target creature.*|creatures you control|@self) gains? lifelink(?: .+)?"),
        ("additional_draw", "draw", rf"you draw (?P<count>{_NUMBER}) additional cards?"),
        ("artifact_count_payoff", "scale", r".+ (?:for each|equal to the number of) artifacts you control"),
        ("land_count_payoff", "scale", r".+ (?:for each|equal to the number of) lands you control"),
        ("token_count_payoff", "scale", r".+ (?:for each|equal to the number of) (?:creature )?tokens you control"),
        ("double_existing_counters", "double", r"double the number of (?:\+1/\+1 )?counters on (?P<object>.+)"),
        ("untap", "untap", r"untap (?P<object>target .+|all .+|this .+)"),
        ("counter_spell", "counter", r"counter (?P<object>target (?:.+ )?spell)"),
        ("remove_permanent", "remove", r"(?:destroy|exile) (?P<object>(?:target|all|each) (?:nonland )?(?:artifact|creature|enchantment|land|permanent|planeswalker)s?(?: (?:an opponent|you) controls?)?)"),
        ("permanent_bounce", "return", r"return (?P<object>(?:target|all|each) .+?) to (?:its owner's hand|their owners' hands)"),
        ("restrict_actions", "restrict", r"(?P<object>players|opponents|your opponents) (?:can't|cannot) (?:cast|activate|attack|block).+"),
        ("reduce_spell_cost", "reduce", r"(?P<object>.+ spells?) you cast cost \{[^}]+\} less to cast"),
        ("deal_noncombat_damage", "deal", rf".+ deals? (?P<count>{_NUMBER}) damage to (?P<object>.+)"),
        ("mass_combat_buff", "buff", r"(?P<object>(?:other |all |attacking )?creatures you control) get \+\d+/[+-]\d+(?: .+)?"),
        ("combat_buff", "buff", r"(?P<object>.+ creatures?.*|creatures?.*|@self) gets? \+\d+/\+\d+(?: .+)?"),
        ("grant_haste", "grant", r"(?P<object>.+ creatures?.*|creatures?.*|@self) (?:gain[s]?|ha[sv]e?s?) haste(?: .+)?"),
        ("grant_evasion", "grant", r"(?P<object>.+ creatures?.*|creatures?.*|@self) (?:gains?|has|have) (?P<subtype>flying|trample|menace)(?: .+)?"),
        ("protect_creature", "protect", r"(?P<object>target creature.*|@self) gains? (?P<subtype>hexproof|indestructible|protection from .+)(?: .+)?"),
        ("extra_combat", "permit", r"after this (?:main|combat) phase, there is an additional combat phase(?: .+)?"),
        ("extra_turn", "permit", r"(?:you )?take an extra turn after this one"),
        ("goad", "goad", r"goad (?P<object>target creature|that creature|each creature(?: an opponent controls)?|all creatures(?: target opponent controls)?)"),
        ("take_control", "gain_control", r"gain control of (?P<object>target (?:creature|artifact|enchantment|permanent|planeswalker)(?: an opponent controls)?)(?: until end of turn)?"),
        ("life_loss_opponent", "lose", rf"(?:each opponent|target opponent|that opponent) loses? (?P<count>{_NUMBER}) life"),
        ("protect_from_attacks", "restrict", r"creatures can't attack you(?: or planeswalkers you control)? unless .+"),
        ("investigate", "investigate", r"investigate"),
        ("venture", "venture", r"venture into (?:the dungeon|undercity)"),
        ("become_monarch", "become", r"you become the monarch"),
        ("gain_energy", "gain", r"(?:you )?get (?P<count>(?:\{e\})+)"),
        ("modified_payoff", "reward", r"(?P<object>modified creatures you control) (?:have|gain|get|can't be blocked)\b.*"),
        ("unblockable", "evade", rf"(?P<object>target creature|{_SELF}) can't be blocked(?: this turn)?"),
        ("scry", "scry", rf"scry (?P<count>{_NUMBER})"),
        ("surveil", "surveil", rf"surveil (?P<count>{_NUMBER})"),
        ("equip", "attach", r"equip (?P<cost>(?:\{[^}]+\})+)"),
        ("attach_aura", "attach", r"enchant (?P<object>creature(?: you control)?)"),
    )
)

_OBSERVERS = tuple(
    (capability, event, re.compile(pattern))
    for capability, event, pattern in (
        ("death_payoff", "death", r"(?P<object>.+?) (?:dies|die)"),
        ("enter_payoff", "enter", r"(?P<object>.+?) enters?(?: the battlefield)?(?P<tail>.*)"),
        ("sacrifice_payoff", "sacrifice", r"(?P<actor>you|a player|an opponent) sacrifices? (?P<object>.+)"),
        ("lifegain_payoff", "life_gain", r"(?P<actor>you|a player|an opponent) gains? life"),
        ("cast_or_copy_payoff", "cast_or_copy", r"(?P<actor>you|a player|an opponent) casts? or (?:copy|copies) (?P<object>.+ spell)"),
        ("cast_payoff", "cast", r"(?P<actor>you|a player|an opponent) casts? (?P<object>.+ spell)"),
        ("counter_payoff", "counter_placement", r"(?:one or more )?(?P<counter>\+1/\+1) counters? (?:is|are) put on (?P<object>.+)"),
        ("counter_payoff", "counter_placement", r"(?P<actor>you|an opponent|a player) puts? (?:one or more )?(?P<counter>\+1/\+1) counters? on (?P<object>.+)"),
        ("single_attacker_payoff", "attack_alone", r"(?P<object>.+) attacks? alone"),
        ("attack_payoff", "attack", r"(?P<object>.+) attacks?"),
    )
)

_REPLACEMENTS = (
    ("multiply_token_creation", "token_creation", re.compile(
        r"(?:(?P<actor>you|a player|an opponent|an effect) would create (?P<active>.+?tokens?)|"
        r"(?P<passive_object>.+?tokens?) would be created)(?: under your control)?")),
    ("multiply_counter_placement", "counter_placement", re.compile(
        r"(?:(?P<actor>you|a player|an opponent|an effect) would put (?P<counter>.+?) counters? on |"
        r"(?P<passive>.+?) counters? would be put on )(?P<object>.+)")),
    ("multiply_damage", "noncombat_damage", re.compile(
        r"(?P<object>.+?) would deal (?:noncombat )?damage(?: to .+)?")),
)

_BLINK = re.compile(
    r"exile (?P<object>(?:target|another) [^.,]+?)\s*(?:\.\s+(?:then )?|,?\s+then )return (?:it|that card) to the battlefield "
    r"under (?P<owner>its owner's|your) control(?P<tail>[^.]*)\.?"
)


def extract_mechanical_capabilities(
    parsed: OracleParse, *, name: str = "", type_line: str = "",
) -> MechanicalProfile:
    """Interpret the parser's source-linked abilities without using lexical tags."""
    capabilities: list[MechanicalCapability] = []
    unknowns: list[CapabilityEvidence] = []
    # Scryfall joins faces inline. Mask only their separators when asking the
    # resource extractor to find abilities, preserving absolute offsets.
    resource_parse = parsed
    if " // " in parsed.oracle_text:
        resource_parse = replace(
            parsed, oracle_text=parsed.oracle_text.replace(" // ", "\n   "),
            abilities=tuple(
                replace(ability, text=ability.text.replace(" // ", "\n   "))
                for ability in parsed.abilities
            ),
        )
    resource_events = {}
    for resource in extract_mana_resources(resource_parse):
        if resource.action != "create_token":
            leading = len(resource.evidence.oracle_span) - len(resource.evidence.oracle_span.lstrip())
            resource_events.setdefault(resource.evidence.start + leading, []).append(resource)
    names = tuple(part.casefold() for part in name.split(" // ") if part)
    printed_face_types = tuple(type_line.split(" // "))
    face_types = tuple(part.casefold() for part in printed_face_types)
    changeling = bool(_CHANGELING.search(parsed.oracle_text.split(" // ", 1)[0]))
    face = 0
    spell_faces = set()
    for ability in parsed.abilities:
        for line in _FACE_OR_LINE.finditer(ability.text):
            if line[0].strip() == "//":
                face += 1
                continue
            leading = len(line[0]) - len(line[0].lstrip())
            start, end = ability.start + line.start() + leading, ability.start + line.end()
            evidence = _evidence(parsed, ability.id, start, end)
            text = line[0][leading:]
            masked = mask_reminder_text(text)
            while (updated := mask_reminder_text(masked)) != masked:
                masked = updated
            normalized = "".join(
                character.casefold() if len(character.casefold()) == 1 else character
                for character in masked
            ).replace("’", "'")
            # Pad the self marker to keep offsets in the original ability.
            if face < len(names) and len(names[face]) >= 5:
                name_pattern = r"(?<![\w@])" + re.escape(names[face]) + r"(?!\w)"
                normalized = re.sub(name_pattern, lambda m: "@self".ljust(len(m[0])), normalized)
            face_type = face_types[min(face, len(face_types) - 1)]
            extracted, incomplete = _compile_line(
                parsed, evidence, normalized, face_type, resource_events.get(start, ()),
            )
            if face == 0 and changeling:
                extracted = [
                    replace(cap, resolution="changeling") if cap.id == "self_evasion" else cap
                    for cap in extracted
                ]
            capabilities.extend(extracted)
            spell_type = re.match(r"^(?:kindred )?(instant|sorcery)\b", face_type)
            if spell_type and extracted and face not in spell_faces and re.match(
                r"^(?:draw|create|you |return|exile|destroy|counter|put|add|copy|@self|target |each |take |gain control|goad)", normalized.strip(),
            ):
                spell_faces.add(face)
                capabilities.append(MechanicalCapability(
                    "resolve_spell", "resolve", "spell_resolution",
                    MechanicalObject("spell", (spell_type[1],), reference="self"),
                    evidence, source_zone="stack", controller="you",
                    timing=f"{spell_type[1]}_speed", repeatability="single_use",
                    face_type_line=face_type,
                ))
            if incomplete:
                unknowns.append(evidence)
    first_face = face_types[0]
    card_types, _, subtypes = first_face.partition(" — ")
    if "creature" in card_types.split():
        printed = printed_face_types[0]
        capabilities.append(MechanicalCapability(
            "creature_presence", "enter_as_creature", "battlefield_presence",
            MechanicalObject("permanent", ("creature",), tuple(sorted(set(subtypes.split()))), "self"),
            CapabilityEvidence("type_line", 0, len(printed), printed, "type_line"),
            destination_zone="battlefield", controller="you",
            resolution="changeling" if changeling else "unknown",
            face_type_line=first_face,
        ))
    # Type-line evidence needs no inferred casting or entry event.
    face_start = 0
    for printed in printed_face_types:
        face_type = printed.casefold()
        face_types_text, _, face_subtypes = face_type.partition(" — ")
        types = set(face_types_text.split())
        evidence = CapabilityEvidence("type_line", face_start, face_start + len(printed), printed, "type_line")
        face_start += len(printed) + len(" // ")
        for capability_id, words in (
            ("printed_type", tuple(sorted(types & {"battle", "planeswalker"}))),
            ("printed_supertype", tuple(sorted(types & {"legendary", "snow"}))),
            ("printed_subtype", tuple(sorted(set(face_subtypes.split()) & {
                "cave", "desert", "gate", "kraken", "leviathan", "octopus", "saga", "serpent", "shrine", "vehicle",
            }))),
        ):
            if words:
                capabilities.append(MechanicalCapability(
                    capability_id, "has_type", "type_line",
                    MechanicalObject("card", tuple(sorted(types - _SUPERTYPES)), words, "self"),
                    evidence, face_type_line=face_type,
                ))
    return MechanicalProfile(tuple(capabilities), tuple(unknowns), parsed.parser_version)


def _compile_line(parsed, evidence, text, type_line, resource_events):
    capabilities = []
    timing, repeatability = "unknown", "unknown"
    if re.search(r"\binstant\b", type_line):
        timing, repeatability = "instant_speed", "single_use"
    elif re.search(r"\bsorcery\b", type_line):
        timing, repeatability = "sorcery_speed", "single_use"
    costs = ()
    conditions = ()
    remainder_start = 0
    text = text.rstrip()
    compact = " ".join(text.split())
    if not compact:
        return [], False

    def emit(capability_id, action, obj, span=evidence, **fields):
        source = span.oracle_span.casefold()
        local_conditions = fields.pop("conditions", ())
        duration = "until_end_of_turn" if "until end of turn" in source else "unknown"
        details = dict(
            evidence=span, timing=timing, repeatability=repeatability, costs=costs,
            event="resolution", targeting=_targeting(source), duration=duration,
            conditions=conditions + local_conditions,
            modality="optional" if re.search(r"\b(?:may|up to)\b", source) else "mandatory",
            face_type_line=type_line,
        )
        if fields.get("costs") == ():
            fields.pop("costs")
        details.update(fields)
        capabilities.append(MechanicalCapability(capability_id, action, object=obj, **details))

    if entry := _TYPAL_ENTRY_COUNTERS.fullmatch(compact):
        subtype = entry["subtype"]
        emit(
            "typal_entry_counters", "put", MechanicalObject("permanent", ("creature",), (subtype,), "other"),
            event="counter_placement", controller="you", destination_zone="battlefield",
            quantity=MechanicalQuantity("variable", expression=f"one for each {subtype} already controlled"),
            timing="replacement", repeatability="repeatable",
            conditions=("source_on_battlefield_before_entry", f"control_a_{subtype.replace(' ', '_')}_before_entry"),
        )
        return capabilities, False

    if re.match(r"^(?:while |as long as )?(?:your commander|a commander you control) (?:has|gains?|gets?|is|can|can't)\b", compact) or re.match(
        r"^(?:if|whenever|when) (?:you (?:cast|control) your commander|(?:your commander|a commander you control) (?:enters|attacks|deals|dies))\b",
        compact,
    ):
        emit("commander_payoff", "reward", MechanicalObject("card"), conditions=("qualifying_commander",))

    if replacement := _DEATH_REPLACEMENT.fullmatch(compact):
        if ", instead " not in compact and not replacement["instead"]:
            return [], True
        if result := _match_action(replacement["creation"]):
            capability_id, action, obj, fields = result
            conditions = (replacement["event"],)
            emit(capability_id, action, obj, timing="replacement", repeatability="repeatable", **fields)
            return capabilities, False
        return [], True

    if replacement := _REPLACEMENT.fullmatch(compact):
        effect = replacement["effect"]
        multiplier = re.search(r"\b(?:twice|double|triple|three times)\b", effect)
        for capability_id, event, pattern in _REPLACEMENTS:
            match = pattern.fullmatch(replacement["event"])
            if match and multiplier:
                if event == "token_creation" and not re.fullmatch(
                    r"(?:(?:you |that player |they |it )?creates? (?:twice|double|triple|three times) that many (?:of those )?tokens|"
                    r"(?:twice|double|triple|three times) that many (?:of those )?tokens (?:are|would be) created) instead", effect,
                ):
                    continue
                if event == "counter_placement" and not re.fullmatch(
                    r"(?:(?:you |that player |they |it )?puts? (?:twice|double|triple|three times) that many .*counters? on (?:it|that permanent|that creature)|"
                    r"(?:twice|double|triple|three times) that many .*counters? (?:are|would be) put on (?:it|that permanent|that creature)) instead",
                    effect,
                ):
                    continue
                if event == "noncombat_damage" and " damage " not in effect:
                    continue
                fields = match.groupdict()
                object_text = fields.get("object") or fields.get("active") or fields.get("passive_object") or ""
                obj = _object(object_text, kind="token" if event == "token_creation" else "permanent")
                counter = fields.get("counter") or fields.get("passive")
                if counter and counter not in {"one or more", "a", "any number of"}:
                    counter_type = counter.split()[-1]
                    obj = MechanicalObject(obj.kind, obj.types, (counter_type,), obj.reference, obj.restrictions)
                default = {"you": "you", "an opponent": "opponent", "a player": "any"}.get(
                    fields.get("actor"), "any" if fields.get("passive_object") else "unknown",
                )
                controller = _controller(replacement["event"], default)
                emit(capability_id, "multiply", obj, timing="replacement", repeatability="repeatable",
                     event=event, controller=controller, targeting="none",
                     quantity=_quantity("two" if multiplier[0] in {"twice", "double"} else "three"))
                return capabilities, False
        return [], True
    if re.match(r"^if .+\bwould\b[^,]*,\s+instead\b", compact):
        return [], True

    if trigger := _TRIGGER.match(text.lstrip()):
        timing, repeatability = "triggered", "repeatable"
        observed = " ".join(trigger["event"].split())
        remainder_start = len(text) - len(text.lstrip()) + trigger.end()
        conditions = (observed,)
        if group := _TRIBAL_COMBAT.fullmatch(observed):
            resolution = evidence.oracle_span[remainder_start:].strip()
            emit(
                "combat_damage_payoff", "observe", _tribal_group(group), event="combat_damage",
                controller="you", targeting="none", resolution=resolution,
            )
            # Recognize the initial mill while keeping subsequent conditions
            # for drawing cards on the full observer resolution.
            raw_effect = text[remainder_start:].partition(".")[0]
            first_effect = raw_effect.strip()
            if result := _match_action(" ".join(first_effect.split())):
                capability_id, action, obj, fields = result
                if capability_id == "mill_opponent":
                    start = evidence.start + remainder_start + len(raw_effect) - len(raw_effect.lstrip())
                    span = _evidence(parsed, evidence.ability_id, start, start + len(first_effect))
                    emit(capability_id, action, obj, span, **fields)
            return capabilities, False
        for capability_id, event, pattern in _OBSERVERS:
            if match := pattern.fullmatch(observed):
                fields = match.groupdict()
                object_text = fields.get("object") or ""
                obj = _object(object_text) if object_text else MechanicalObject("player")
                if fields.get("counter"):
                    obj = MechanicalObject(obj.kind, obj.types, (fields["counter"],), obj.reference, obj.restrictions)
                controller = _controller(object_text, {"you": "you", "an opponent": "opponent", "a player": "any"}.get(fields.get("actor"), "you" if obj.reference == "self" else "any"))
                zone = {"enter": "battlefield", "death": "graveyard", "sacrifice": "graveyard",
                        "cast": "stack", "cast_or_copy": "stack"}.get(event, "unknown")
                source = "battlefield" if event in {"death", "sacrifice"} else "unknown"
                tail = (fields.get("tail") or "").strip()
                if tail:
                    obj = MechanicalObject(obj.kind, obj.types, obj.subtypes, obj.reference, obj.restrictions + (tail,))
                    controller = _controller(tail, controller)
                conditions = ()
                if _CONDITION.search(text[remainder_start:]):
                    conditions = (evidence.oracle_span[remainder_start:].strip(),)
                emit(capability_id, "observe", obj, event=event, controller=controller,
                     source_zone=source, destination_zone=zone, targeting="none",
                     resolution=evidence.oracle_span[remainder_start:].strip())
                conditions = (observed,)
                break
    elif match := re.fullmatch(r"as an additional cost to cast (?:this spell|@self), (.+?)\.?", compact):
        if result := _match_action(match[1]):
            capability_id, action, obj, fields = result
            emit(capability_id, action, obj, costs=(match[1],), **fields)
            return capabilities, False
        return [], True
    elif ":" in text and not text.lstrip().startswith(("choose", "if ")):
        cost, _, _ = text.partition(":")
        timing = "sorcery_speed" if "activate only as a sorcery" in text else "activated"
        repeatability = "repeatable"
        remainder_start = len(cost) + 1
        original_cost = evidence.oracle_span[:len(cost)].strip()
        costs = (original_cost,)
        for part in re.finditer(r"(?:^|,\s*|\band\s+)([^,]+?)(?=,|$)", cost):
            if result := _match_action(" ".join(part[1].split())):
                capability_id, action, obj, fields = result
                left = part.start(1) + len(part[1]) - len(part[1].lstrip())
                span = _evidence(parsed, evidence.ability_id, evidence.start + left,
                                 evidence.start + part.start(1) + len(part[1].rstrip()))
                emit(capability_id, action, obj, span, **fields)

    remainder = text[remainder_start:]
    stripped = " ".join(remainder.split())
    optional_prefix = re.match(r"^(?:you )?may ", stripped)
    effect_text = stripped[optional_prefix.end():] if optional_prefix else stripped
    if wheel := _WHEEL.fullmatch(effect_text):
        emit("wheel", "redraw", MechanicalObject("card"), event="discard_and_draw",
             controller="any", quantity=_quantity(wheel["count"]))
        return capabilities, False
    if drain := _LIFE_DRAIN.fullmatch(effect_text):
        emit("life_drain", "drain", MechanicalObject("player"), event="life_loss_and_gain",
             controller="any" if drain["target"] == "target player" else "opponent",
             quantity=_quantity(drain["count"]))
    blink = _BLINK.fullmatch(effect_text)
    if blink:
        obj = _object(blink["object"])
        emit("blink", "exile_and_return", obj, event="zone_change", source_zone="battlefield",
             destination_zone="battlefield", controller=_controller(blink["object"]),
             owner="you" if "you own" in blink["object"] else "unknown",
             resolution=blink["owner"], conditions=(blink["tail"].strip(),) if blink["tail"].strip() else ())
        return capabilities, False

    if stripped.startswith("choose ") or re.search(r"\bor (?:you may )?(?:create|gain|put|draw|exile|return)\b", stripped):
        return capabilities, True
    unparsed = False
    for clause in _CLAUSE.finditer(remainder):
        clause_text = " ".join(clause[0].split())
        if not clause_text:
            continue
        clause_start = evidence.start + remainder_start + clause.start()
        clause_span = _evidence(
            parsed, evidence.ability_id, clause_start + len(clause[0]) - len(clause[0].lstrip()),
            clause_start + len(clause[0].rstrip()),
        )
        effect = re.sub(r"^(?:then )?", "", clause_text)
        local_condition = _EFFECT_CONDITION.match(effect)
        if local_condition:
            effect = effect[local_condition.end():]
        effect = re.sub(r"^(?:you )?may ", "", effect)
        if not remainder_start and not local_condition and effect == clause_text:
            keyword_parts = tuple(re.finditer(r"[^,]+", clause[0]))
            if keyword_parts and all(_THEME_KEYWORDS.fullmatch(part[0].strip()) for part in keyword_parts):
                for part in keyword_parts:
                    keyword = _THEME_KEYWORDS.match(part[0].lstrip())["keyword"]
                    start = clause_start + part.start() + len(part[0]) - len(part[0].lstrip())
                    span = _evidence(parsed, evidence.ability_id, start, clause_start + part.start() + len(part[0].rstrip()))
                    emit("keyword_ability", "has_keyword", MechanicalObject(
                        "card", subtypes=(keyword,), reference="self",
                    ), span)
                    if keyword == "flying":
                        emit("self_evasion", "evade", MechanicalObject("permanent", ("creature",), reference="self"),
                             span, controller="you", event="combat_damage")
                continue
        if group := _TRIBAL_BUFF.fullmatch(effect):
            emit("tribal_buff", "buff", _tribal_group(group), clause_span, controller="you",
                 event="battlefield_presence", targeting="none",
                 conditions=(local_condition["condition"],) if local_condition else ())
            continue
        if _EVASION.fullmatch(effect):
            obj = MechanicalObject("permanent", ("creature",), reference="self")
            restrictions = (local_condition["condition"],) if local_condition else ()
            emit("self_evasion", "evade", obj, clause_span, controller="you",
                 event="combat_damage", conditions=restrictions)
            if effect.endswith("can't be blocked"):
                emit("unblockable", "evade", obj, clause_span, controller="you",
                     event="combat_damage", conditions=restrictions)
            continue
        # Parse a common compound of independent imperative effects while
        # retaining each event's original span.
        parts = re.split(r"(?:\s+and |,?\s+then )(?=(?:create|draw|gain|put|sacrifice|untap|discard)\b)", effect)
        cursor = 0
        for part in parts:
            part = part.strip()
            if result := _match_action(part):
                capability_id, action, obj, fields = result
                part_words = r"\s+".join(re.escape(word) for word in part.split())
                match = re.search(part_words, clause[0][cursor:])
                if match is None:
                    unparsed = True
                    continue
                start = evidence.start + remainder_start + clause.start() + cursor + match.start()
                end = start + len(match[0])
                cursor += match.end()
                span = _evidence(parsed, evidence.ability_id, start, end)
                if local_condition:
                    fields["conditions"] = (*fields.get("conditions", ()), local_condition["condition"])
                    if local_condition["condition"].startswith("for each "):
                        fields["quantity"] = MechanicalQuantity("variable", expression=local_condition["condition"])
                if _CONDITION.search(part):
                    fields["conditions"] = tuple(dict.fromkeys((*fields.get("conditions", ()), clause_text)))
                if re.search(r"\bmay\b", clause_text) or "up to " in part:
                    fields["modality"] = "optional"
                if capability_id == "equip":
                    fields.update(costs=(span.oracle_span[6:],), timing="sorcery_speed",
                                  repeatability="repeatable", targeting="target", controller="you",
                                  source_zone="battlefield", destination_zone="battlefield")
                    obj = MechanicalObject("permanent", ("creature",))
                if capability_id == "attach_aura":
                    fields.update(targeting="target", source_zone="stack", destination_zone="battlefield")
                emit(capability_id, action, obj, span, **fields)
                if capability_id == "mass_combat_buff":
                    emit("combat_buff", action, obj, span, **fields)
                if capability_id == "permanent_bounce":
                    emit("remove_permanent", "remove", obj, span, **fields)
            else:
                unparsed = True

    for resource in resource_events:
        capability_id = {"permit_land_play": "extra_land_play", "put_land": "put_land", "add_mana": "add_mana"}[resource.action]
        emit(capability_id, resource.action,
             MechanicalObject("mana" if capability_id == "add_mana" else "card",
                              () if capability_id == "add_mana" else ("land",)),
             controller="you", source_zone=resource.source_zone or "unknown",
             destination_zone="battlefield" if capability_id != "add_mana" else "mana_pool",
             event=resource.action,
             quantity=MechanicalQuantity(resource.quantity.kind, resource.quantity.value, resource.quantity.expression),
             conditions=tuple(item for item in (*resource.requirements, *resource.restrictions) if item not in conditions),
             modality="optional" if resource.optional else "mandatory")
        if capability_id == "add_mana" and "creature" in type_line.partition(" — ")[0].split():
            capabilities.append(replace(capabilities[-1], id="creature_mana"))
    return capabilities, unparsed and (
        not resource_events or bool(capabilities[:-len(resource_events)])
    )
