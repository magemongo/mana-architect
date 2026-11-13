"""Versioned Oracle clauses and the interaction facts they can establish."""

from dataclasses import dataclass
import re

INTERACTION_RULES_VERSION = "interaction-v1"

# Oracle groups the object, qualifiers and scope in one phrase. The classifier
# resolves those separately so a targeted player can have a whole zone affected.
_SUBJECT = r"(?P<subject>.+?)"
_ACTIONS = r"(?:destroy|exile|return|counter|tap|prevent|shuffle|put|regenerate)"
CLAUSE_BREAK = re.compile(
    rf"[.;!?](?:\s+|$)|,? (?:and |then )?(?={_ACTIONS} )"
    r"|(?:,? and |,? then )(?=(?:(?:you|it|they|its controller|that player) )?"
    r"(?:gains?|loses?|draws?|creates?) \d)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InteractionRule:
    id: str
    action: str
    mechanism: str
    role: str
    pattern: re.Pattern[str]


def _rule(rule_id: str, action: str, mechanism: str, role: str, pattern: str) -> InteractionRule:
    return InteractionRule(rule_id, action, mechanism, role, re.compile(pattern, re.IGNORECASE))


INTERACTION_RULES = (
    _rule("GY_EXILE_001", "exile", "graveyard_hate", "disruption",
          rf"exile {_SUBJECT} (?:from|in) (?P<zone>.+\bgraveyards?)(?: |$)"),
    _rule("GY_SHUFFLE_001", "shuffle", "graveyard_hate", "disruption",
          r"(?:shuffle|(?P<actor>.+?) shuffles?) (?P<subject>.+?) (?:from|in) "
          r"(?P<zone>.+?\bgraveyards?) into "),
    _rule("GY_RESTRICT_001", "restrict", "graveyard_hate", "suppression",
          rf"{_SUBJECT} in graveyards? (?:can't be|cannot be) (?P<restriction>targeted|cast|returned)\b"),
    _rule("GY_REPLACE_001", "exile", "graveyard_hate", "suppression",
          r"if (?P<subject>.+?) would be put into (?P<zone>.+?\bgraveyard) .+?, "
          r"exile (?:it|that card) instead\b"),
    _rule("DESTROY_001", "destroy", "destroy_removal", "threat_answer", r"destroy (?P<subject>.+)"),
    _rule("EXILE_001", "exile", "exile_removal", "threat_answer", r"exile (?P<subject>.+)"),
    _rule("BOUNCE_001", "return_to_hand", "bounce", "threat_answer",
          rf"return {_SUBJECT} to (?:their|its|that player's|your|(?:its |their )?owners?['’]?) (?:owners?['’] )?hands?\b"),
    _rule("TUCK_001", "tuck", "tuck", "threat_answer",
          rf"(?:put|return|shuffle) {_SUBJECT} (?:on |onto |into |on top of |on the bottom of |"
          r"(?:into|on) (?:the )?(?:top|bottom) of )(?:its |their |target |that player's |your )?"
          r"(?:owners?['’] )?librar(?:y|ies)\b"),
    _rule("ABILITY_COUNTER_001", "counter_ability", "ability_counter", "disruption",
          rf"counter (?P<subject>.+?\b(?:activated|triggered) abilit(?:y|ies))\b"),
    _rule("SPELL_COUNTER_001", "counter_spell", "counterspell", "disruption",
          r"counter (?P<subject>.+?\bspells?)\b"),
    _rule("PROTECT_001", "grant_keyword", "protection", "protection",
          rf"{_SUBJECT} (?:gains?|has|have) (?P<grants>(?:hexproof|indestructible|protection from|ward)\b.+|"
          r"(?:hexproof|indestructible|ward)\b)"),
    _rule("PROTECT_TARGET_001", "restrict_targeting", "protection", "protection",
          rf"{_SUBJECT} (?:can't|cannot) be (?:the targets? of|targeted by) (?P<restriction>.+)"),
    _rule("REGENERATE_001", "regenerate", "protection", "protection", r"regenerate (?P<subject>.+)"),
    _rule("PHASE_001", "phase_out", "protection", "protection", rf"{_SUBJECT} phases? out\b"),
    _rule("PREVENT_001", "prevent_damage", "damage_prevention", "prevention",
          rf"prevent {_SUBJECT} (?:that would be dealt|dealt)\b(?P<recipient>.*)"),
    _rule("EDICT_001", "force_sacrifice", "forced_sacrifice", "threat_answer",
          rf"(?P<actor>.+?) sacrifices? (?P<subject>.+)"),
    _rule("DISCARD_001", "discard", "discard_disruption", "disruption",
          r"(?P<actor>.+?) discards? (?P<subject>.+)"),
    _rule("TAP_001", "tap", "tap_disable", "suppression", r"tap (?P<subject>.+)"),
    _rule("FREEZE_001", "restrict_untap", "tap_disable", "suppression",
          rf"{_SUBJECT} (?:doesn't|don't|can't|cannot) untap\b"),
    _rule("COMBAT_001", "restrict_combat", "combat_interaction", "suppression",
          rf"{_SUBJECT} (?:can't|cannot) (?:attack|block)\b"),
    _rule("CONTROL_001", "gain_control", "control_change", "threat_answer",
          r"gain control of (?P<subject>.+)"),
    _rule("SHRINK_001", "reduce_stats", "stat_reduction", "threat_answer",
          rf"{_SUBJECT} gets? (?P<stats>[+−-][\dX]+/[+−-][\dX]+)\b"),
    _rule("DAMAGE_001", "deal_damage", "damage_removal", "threat_answer",
          rf".+? deals? (?P<damage>\d+|x) damage to (?P<subject>.+)"),
    _rule("RESTRICT_001", "restrict", "rule_restriction", "suppression",
          rf"{_SUBJECT} (?:can't|cannot) (?P<restriction>cast spells?|activate abilities|play lands?|gain life|draw cards?)\b"),
    _rule("TAX_001", "tax", "rule_restriction", "suppression",
          rf"{_SUBJECT} costs? (?P<restriction>\{{[^{{}}]+\}} more to cast)\b"),
)

# Only imperative actions in these contexts are extraction candidates. For
# example, a trigger about an opponent exiling a card is not an exile effect.
CLAUSE_PREFIX = re.compile(
    r"^(?:(?P<condition>(?:when(?:ever)?|at|if|for each)\b.+?),\s+)?"
    r"(?:(?:then|instead) )?(?:(?:you )?(?P<optional>may) )?(?:you )?",
    re.IGNORECASE,
)

_PARENTHESES_OR_QUOTE = re.compile(r'\([^()]*\)|"[^"\n]*"|“[^”\n]*”')


def mask_reminder_text(text: str) -> str:
    return _PARENTHESES_OR_QUOTE.sub(lambda match: " " * len(match.group()), text)
