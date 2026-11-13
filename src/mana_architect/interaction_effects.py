"""Normalize supported interaction clauses with evidence from the Oracle AST."""

from dataclasses import dataclass, replace
import re

from mana_architect.interaction_profile import (
    InteractionEffect, InteractionEvidence, Quantity, Scope, Selection,
)
from mana_architect.interaction_rules_catalog import (
    CLAUSE_BREAK, CLAUSE_PREFIX, INTERACTION_RULES, InteractionRule,
)
from mana_architect.oracle_text import OracleParse

_COUNTS = dict(zip(
    "one two three four five six seven eight nine ten".split(), range(1, 11), strict=True,
)) | {"a": 1, "an": 1, "another": 1}
_QUANTITY = re.compile(
    rf"^(?P<up_to>up to )?(?P<count>{'|'.join(_COUNTS)}|\d+|x|that many|any number of)\b",
)
_TARGET = re.compile(r"\btarget\b")
_OBJECTS = {
    "creature": r"\bcreatures?\b", "artifact": r"\b(?:non)?artifacts?\b",
    "enchantment": r"\benchantments?\b", "land": r"\blands?\b",
    "planeswalker": r"\bplaneswalkers?\b", "battle": r"\bbattles?\b",
    "permanent": r"\b(?:nonland |noncreature )?permanents?\b", "token": r"\btokens?\b",
    "spell": r"\bspells?\b", "activated_ability": r"\bactivated(?: or triggered| and triggered)? abilit(?:y|ies)\b",
    "triggered_ability": r"\btriggered(?: or activated| and activated)? abilit(?:y|ies)\b",
    "card": r"\bcards?\b", "player": r"\b(?:players?|opponents?)\b",
    "damage_event": r"\bdamage\b",
}
_BATTLEFIELD = frozenset(("creature", "artifact", "enchantment", "land", "planeswalker",
                         "battle", "permanent", "token"))
_DAMAGE_TARGETS = ("creature", "planeswalker", "battle", "player")
_REMOVAL = {"destroy_removal", "exile_removal", "bounce", "tuck"}
_DURATION = re.compile(
    r"\b(?:until (?:the )?(?:end of (?:this )?turn|your next turn|its owner's next turn|"
    r"[^.;]+? leaves the battlefield)|this turn|for as long as [^.;]+|during [^.;]+)\b",
)
_QUALIFIERS = re.compile(r"\b(?:unless|if|except|with|without|other than|of your choice|"
                         r"chosen at random)\b[^.;]*")
_CONTROLLER = re.compile(r"\b(?:you (?:don't control|control|own)|"
                          r"(?:an|your|that) opponent controls?|"
                          r"(?:your|(?:an|one) opponent's) graveyards?)\b")
_MODIFIERS = re.compile(r"\b(?:nonland|noncreature|nonartifact|nonblack|monocolored|"
                        r"tapped|untapped|attacking|blocking|legendary|nontoken|other)\b")
_ACTION_CANDIDATE = re.compile(
    r"^(?:destroy|exile|return|counter|tap|prevent|shuffle|regenerate|gain control of)\b"
    r"|\b(?:sacrifices?|discards?|gains? (?:hexproof|indestructible)|phases? out|"
    r"gets? [-−]\d|can't (?:attack|block|cast|activate|untap))\b",
)
_ZONE_MOVES = re.compile(r"\b(?:from|in) (?:[^.;]+ )?(?:graveyards?|hand|library|exile)\b")
_EXILE_ACCESS = re.compile(r"\b(?:may (?:cast|play) (?:it|them|that card|those cards)|"
                           r"top .*?(?:library|libraries))\b")
_SELF_REFERENCE = re.compile(r"^(?:it|them|him|her|this (?:permanent|spell|card|creature|artifact|enchantment|land)|the exiled card)\b")
_COMBAT_SCOPE = re.compile(r"^(?:all |each )?combat damage\b")


def _quantity(phrase: str) -> Quantity:
    match = _QUANTITY.match(phrase)
    if match:
        count = match["count"]
        value = _COUNTS.get(count)
        if count.isdecimal():
            value = int(count)
        if value is None:
            return Quantity("variable", 0 if match["up_to"] or count in {"x", "any number of"} else None)
        return Quantity("up_to", 0, value) if match["up_to"] else Quantity("fixed", value, value)
    if phrase.startswith("all "):
        return Quantity("all", 0)
    if phrase.startswith("each "):
        return Quantity("each", 0)
    if phrase.startswith(("target ", "any target", "that card")):
        return Quantity("fixed", 1, 1)
    return Quantity()


def _selection(phrase: str) -> Selection:
    if _TARGET.search(phrase) and not re.search(r"\bother than (?:the )?target\b", phrase):
        return Selection.TARGET
    if phrase.startswith("all "):
        return Selection.ALL
    if phrase.startswith("each "):
        return Selection.EACH
    if "choose " in phrase or "of your choice" in phrase:
        return Selection.CHOSEN
    return Selection.NON_TARGETED


def _scope(quantity: Quantity) -> Scope:
    if quantity.kind in {"all", "each"}:
        return Scope.MASS
    if quantity.kind == "variable":
        return Scope.VARIABLE
    if quantity.max == 1:
        return Scope.SINGLE
    if quantity.max and quantity.max > 1:
        return Scope.MULTI_TARGET
    return Scope.UNKNOWN


def _objects(phrase: str) -> tuple[str, ...]:
    subject = re.split(r"\b(?:you (?:don't )?control|an opponent controls?|with|from|except|"
                       r"unless|that (?:was|were|is|are)|whose)\b", phrase, maxsplit=1)[0]
    if "any target" in subject:
        return _DAMAGE_TARGETS
    return tuple(kind for kind, pattern in _OBJECTS.items() if re.search(pattern, subject))


def _controller(phrase: str) -> tuple[str, str]:
    if "you don't control" in phrase:
        return "you_dont_control", "opponent_only"
    if re.search(r"\b(?:you (?:control|own)|your graveyard|yourself|dealt to you)\b", phrase):
        return "you_control", "self_only"
    if re.search(r"\b(?:opponents? controls?|each opponent|target opponent|opponent's graveyard)\b", phrase):
        return "opponent_controls", "opponent_only"
    return "any", "unknown"


@dataclass(frozen=True)
class _Facts:
    objects: tuple[str, ...]
    zone: str
    controller: str
    symmetry: str
    targets: Quantity
    affected: Quantity
    selection: Selection
    wipe: str | None = None


def _facts(
    rule: InteractionRule, groups: dict[str, str | None], clause: str, context: str,
) -> _Facts | None:
    subject = groups["subject"] or ""
    actor = groups.get("actor") or ""
    source_zone = groups.get("zone") or ""
    recipient = groups.get("recipient") or ""
    mechanic = rule.mechanism
    scope_phrase = subject
    # Targeting in the source zone can select a player without selecting cards.
    selection_phrase = subject
    objects = _objects(subject)
    zone = "battlefield"
    wipe = None
    controller, symmetry = _controller(" ".join((subject, actor, source_zone, recipient)))

    if mechanic == "graveyard_hate":
        if controller == "you_control":
            return None
        zone = "graveyard"
        if "graveyard" in subject or rule.id in {"GY_RESTRICT_001", "GY_REPLACE_001"}:
            if rule.id == "GY_RESTRICT_001" and "card" not in objects:
                return None
            scope_phrase = "all cards"
        if _TARGET.search(source_zone):
            selection_phrase = source_zone
        if actor:
            selection_phrase = actor
            if actor.startswith(("each ", "opponents")):
                scope_phrase = "each " + subject
        objects = ("card",)
    elif mechanic in {"counterspell", "ability_counter"}:
        zone = "stack"
        if not objects or controller == "you_control":
            return None
    elif mechanic in {"forced_sacrifice", "discard_disruption"}:
        if not re.match(r"(?:each|target|that|an) (?:opponent|player)\b|opponents\b", actor):
            return None
        if mechanic == "discard_disruption":
            zone, objects = "hand", ("card",)
            if re.match(r"(?:their|his or her|that player's) hand\b", subject):
                scope_phrase = "all cards"
        elif not (_BATTLEFIELD & set(objects)):
            return None
        selection_phrase = actor
        if actor == "that player":
            reference = re.search(r"\btarget (?:player|opponent)\b", context)
            if reference:
                selection_phrase = reference.group()
                controller, symmetry = _controller(selection_phrase)
        if actor.startswith(("each ", "opponents")):
            scope_phrase = "each " + subject
    elif mechanic == "damage_prevention":
        zone, objects = "damage", ("damage_event",)
        if "damage" not in subject:
            return None
        if "target" in recipient:
            selection_phrase = scope_phrase = recipient.removeprefix(" to ").strip()
        elif recipient.startswith(" to you"):
            scope_phrase = "a player"
        elif _COMBAT_SCOPE.match(subject) and not subject.startswith("the next "):
            scope_phrase = "all damage"
        elif subject.startswith("the next ") and recipient.startswith(" to "):
            scope_phrase = recipient.removeprefix(" to ")
    elif mechanic == "rule_restriction":
        zone = "stack" if "cast" in clause else "rules"
        if subject in {"players", "opponents"}:
            scope_phrase = "all " + subject
        if "spell" in objects and "player" in objects:
            objects = ("spell",)
        if re.search(r"\byou (?:can't|cannot)\b", clause):
            return None
    elif mechanic == "protection":
        if symmetry == "opponent_only":
            return None
        if subject == "you":
            objects, scope_phrase, symmetry = ("player",), "a player", "self_only"
        if _SELF_REFERENCE.fullmatch(subject):
            objects = objects or ("permanent",)
            scope_phrase, symmetry = "a permanent", "self_only"
        if not objects and _SELF_REFERENCE.match(subject):
            objects, scope_phrase, symmetry = ("permanent",), "a permanent", "self_only"
        if not (_BATTLEFIELD.intersection(objects) or "player" in objects):
            return None
    else:
        if controller == "you_control" or _ZONE_MOVES.search(subject):
            return None
        if mechanic in _REMOVAL and _SELF_REFERENCE.match(subject):
            return None
        if mechanic == "exile_removal":
            if _EXILE_ACCESS.search(subject) or ("card" in objects and "token" not in objects):
                return None
        if mechanic == "stat_reduction" and not re.search(r"(?:^|/)[−-]", groups["stats"] or ""):
            return None
        if not (_BATTLEFIELD.intersection(objects) or mechanic == "damage_removal" and "player" in objects):
            return None
        if mechanic == "damage_removal" and objects == ("player",):
            return None

    if scope_phrase == subject and not _QUANTITY.match(scope_phrase):
        # Bare plurals are universal subjects for predicates such as "creatures
        # you control gain". A pronoun without a linked subject stays unknown.
        if re.match(r"(?:other |nonland |noncreature |attacking |blocking |nonartifact )*"
                    r"(?:creatures|permanents|spells|artifacts|enchantments|opponents|players|cards|lands|tokens)\b",
                    scope_phrase):
            scope_phrase = "all " + scope_phrase
    affected = _quantity(scope_phrase)
    selection = _selection(selection_phrase)
    if selection == Selection.NON_TARGETED and affected.kind in {"all", "each"}:
        selection = Selection(affected.kind)
    target_count = _quantity(selection_phrase) if selection == Selection.TARGET else Quantity("fixed", 0, 0)
    if selection == Selection.TARGET and target_count.kind == "unknown":
        target_count = Quantity("fixed", 1, 1)
    if affected.kind in {"all", "each"} and symmetry == "unknown" and zone in {"battlefield", "stack", "graveyard", "hand", "damage"}:
        symmetry = "selective" if selection == Selection.TARGET else "symmetric"
    if affected.kind in {"all", "each"} and zone == "battlefield":
        if mechanic in {"destroy_removal", "exile_removal", "bounce", "tuck"}:
            wipe = "direct"
        elif mechanic == "damage_removal" or mechanic == "stat_reduction" and re.search(r"/[−-]", groups["stats"] or ""):
            wipe = "potential"
    return _Facts(objects or ("unknown",), zone, controller, symmetry, target_count, affected, selection, wipe)


def _classify_clause(
    clause: str, context: str, span: tuple[str, int, int, str],
    linked_subject: str | None,
) -> tuple[InteractionEffect | None, str | None, bool]:
    prefix = CLAUSE_PREFIX.match(clause)
    assert prefix is not None
    operative = clause[prefix.end():]
    if clause.startswith("you ") and operative.startswith(("gain hexproof", "have hexproof", "gain protection", "have protection")):
        operative = clause
    for rule in INTERACTION_RULES:
        match = rule.pattern.match(operative)
        if match is None and rule.id == "GY_REPLACE_001":
            match = rule.pattern.match(clause)
        if not match:
            continue
        groups = match.groupdict()
        raw_subject = groups["subject"] or ""
        if linked_subject and re.fullmatch(r"(?:it|they|those creatures|that creature|that permanent)", raw_subject):
            # The same target is affected again; this linked clause adds no new target.
            groups["subject"] = linked_subject
        facts = _facts(rule, groups, operative, context)
        if facts is None:
            continue
        if groups["subject"] != raw_subject:
            facts = replace(facts, targets=Quantity("fixed", 0, 0), selection=Selection.NON_TARGETED)
        mechanic = rule.mechanism
        tags = [mechanic]
        if facts.wipe:
            tags.append("board_wipe")
        if mechanic == "damage_prevention" and _COMBAT_SCOPE.match(raw_subject) and facts.affected.kind == "all":
            tags.append("fog")
        duration_match = _DURATION.search(clause)
        duration = duration_match.group() if duration_match else "indefinite"
        if not duration_match and rule.role == "suppression" and rule.action != "tap":
            duration = "while_source_remains"
        if not duration_match and rule.action == "tap":
            duration = "until_untapped"
        if not duration_match and rule.action == "phase_out":
            duration = "until_phased_in"
        if not duration_match and rule.action == "regenerate":
            duration = "next_event"
        if not duration_match and "next " in clause and mechanic == "damage_prevention":
            duration = "next_event"
        qualifiers = tuple(item.group() for item in _QUALIFIERS.finditer(operative))
        conditions = tuple(filter(None, (prefix["condition"], "optional" if prefix["optional"] else None)))
        conditions += tuple(item for item in qualifiers if item.startswith(("unless ", "if ")))
        exceptions = tuple(item for item in qualifiers if item.startswith(("except ", "other than ")))
        restrictions = tuple(dict.fromkeys((
            *(item for item in qualifiers if item.startswith(("with ", "without "))),
            *(item.group() for item in _CONTROLLER.finditer(operative)),
            *(item.group() for item in _MODIFIERS.finditer(raw_subject)),
            *(filter(None, (groups.get("restriction"), groups.get("stats"), groups.get("damage"), groups.get("grants")))),
        )))
        if mechanic in {"forced_sacrifice", "discard_disruption"} and facts.affected.kind != "all":
            if "at random" in clause:
                choices = ("random",)
            elif re.search(r"\byou choose .+? from (?:it|that player's hand)", context):
                choices = ("controller_chooses",)
            else:
                choices = ("affected_player_chooses",)
        else:
            choices = ()
        text, start, end, ability_id = span
        evidence = InteractionEvidence(text, start, end, (rule.id,), ability_id)
        effect = InteractionEffect(
            f"e{start}", rule.action, facts.objects, facts.selection, facts.targets, facts.affected, facts.zone,
            facts.controller, duration, facts.symmetry, _scope(facts.affected), tuple(tags), (rule.role,),
            evidence, conditions=conditions, exceptions=exceptions, restrictions=restrictions,
            choices=choices, wipe_potential=facts.wipe,
        )
        return effect, groups["subject"], False
    # An unsupported positive-looking imperative gets an explicit coverage warning.
    return None, linked_subject, bool(_ACTION_CANDIDATE.search(operative))


_SIDE_EFFECT = re.compile(r"^(?:its controller|that player|each player|that permanent's controller) (?:gains?|loses?|draws?|creates?)\b")
_LINKED_EXCEPTION = re.compile(r"^(?:they|it|those creatures) (?:can't|cannot) be regenerated\b")
_BLINK_RETURN = re.compile(r"\breturn (?:it|them|that card|those cards|the exiled card) (?:to|onto) the battlefield\b")


def extract_interaction_effects(
    parsed: OracleParse, ability_id: str, text: str, offset: int, *, overload: bool = False,
) -> tuple[tuple[InteractionEffect, ...], tuple[str, ...]]:
    """Read an operative AST slice, retaining its original Oracle source offsets."""
    effects: list[InteractionEffect] = []
    warnings = []
    cursor = 0
    linked_subject = None
    # Normalize straight and typographic punctuation without changing offsets.
    normalized = text.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ’", "abcdefghijklmnopqrstuvwxyz'"))
    boundaries = [(part.start(), part.end()) for part in CLAUSE_BREAK.finditer(normalized)
                  if part.group().strip() in {".", ";", "!", "?"}
                  or not re.match(r"\s*(?:when(?:ever)?|at|if|for each)\b[^,.;]*$", normalized[:part.start()])]
    for boundary, next_cursor in (*boundaries, (len(normalized), len(normalized))):
        raw = normalized[cursor:boundary]
        start = offset + cursor + len(raw) - len(raw.lstrip())
        end = offset + cursor + len(raw.rstrip())
        clause = re.sub(r"\s+", " ", raw.strip())
        cursor = next_cursor
        if not clause:
            continue
        # Include terminal punctuation in evidence, but leave separators out.
        if parsed.oracle_text[end:end + 1] in {".", "!", "?"}:
            end += 1
        if overload:
            clause = _TARGET.sub("each", clause)
        if effects and _SIDE_EFFECT.match(clause):
            previous = effects[-1]
            effects[-1] = replace(previous, restrictions=previous.restrictions + (clause,))
            continue
        if effects and _LINKED_EXCEPTION.match(clause):
            previous = effects[-1]
            effects[-1] = replace(previous, exceptions=previous.exceptions + (clause,))
            continue
        if _BLINK_RETURN.search(normalized) and clause.startswith(("exile ", "return ")):
            continue
        evidence = (parsed.oracle_text[start:end], start, end, ability_id)
        effect, linked_subject, unsupported = _classify_clause(clause, normalized, evidence, linked_subject)
        if effect:
            effects.append(effect)
        elif unsupported and not _SELF_REFERENCE.fullmatch(clause.split(" ", 1)[-1]):
            warnings.append(f"Unresolved interaction clause at {start}: {evidence[0]}")
    return tuple(effects), tuple(warnings)
