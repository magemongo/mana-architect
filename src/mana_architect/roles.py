import re


_RAMP_PATTERNS = (
    re.compile(r"\badd\s+(?:\{[^}]+\})+", re.IGNORECASE),
    re.compile(
        r"\bsearch your library for\b[^.;\n]{0,120}\bland card\b"
        r"[^.;\n]{0,160}\bput\b[^.;\n]{0,40}\bonto the battlefield\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bcreate\b[^.\n]*\btreasure token", re.IGNORECASE),
)
_MULTI_MANA_PATTERN = re.compile(
    r"\badd\s+(?P<mana>(?:\{[^}]+\})+)", re.IGNORECASE
)
_CARD_DRAW_PATTERN = re.compile(
    r"\bdraw\s+(?P<count>a|one|two|three|four|five|\d+)\s+cards?\b",
    re.IGNORECASE,
)
_MULTI_CARD_RECURSION_PATTERN = re.compile(
    r"\breturn\s+(?:up to\s+)?(?P<count>two|three|four|five|\d+)\s+"
    r"(?:target\s+)?[\w' -]{0,60}?cards?\s+from your graveyard\s+"
    r"to\s+(?:the battlefield|your hand|their owners' hands)",
    re.IGNORECASE,
)
_REPEATED_RESOURCE_PATTERN = re.compile(
    r"\b(?:whenever|at the beginning of each|at the beginning of your upkeep)\b"
    r"[^.\n]{0,160}\b(?:draw\s+(?:a|one|two|\d+)\s+cards?|"
    r"return\b[^.\n]*\bfrom your graveyard)\b",
    re.IGNORECASE,
)
_REPEATABLE_ACTIVATED_DRAW_PATTERN = re.compile(
    r"(?:^|\n)[^\n:]{0,100}:\s*[^\n.]*\bdraw\s+(?:a|one)\s+cards?\b",
    re.IGNORECASE,
)
_REPEATED_INVESTIGATE_PATTERN = re.compile(
    r"\b(?:whenever|when|at the beginning of)\b[^.\n]{0,180}\binvestigate\b",
    re.IGNORECASE,
)
_DRAW_THEN_TOPDECK_PATTERN = re.compile(
    r"\bdraw\s+(?P<draw>a|one|two|three|four|five|\d+)\s+cards?\b"
    r"[^.\n]{0,160}\bput\s+(?P<put>one|two|three|four|five|\d+)\s+"
    r"cards?\s+from your hand\s+on top of your library",
    re.IGNORECASE,
)
_LOOT_PATTERN = re.compile(
    r"\bdraw\s+(?P<draw>a|one|two|three|four|five|\d+)\s+cards?\b"
    r"[^.\n]{0,100}\bdiscard\s+(?P<discard>a|one|two|three|four|five|\d+)\s+cards?\b",
    re.IGNORECASE,
)
_SPOT_INTERACTION_PATTERNS = (
    re.compile(r"\bcounter target\b", re.IGNORECASE),
    re.compile(r"\b(?:destroy|exile|bounce) target\b", re.IGNORECASE),
    re.compile(r"\breturn target\b[^.\n]*\bto its owner's hand", re.IGNORECASE),
    re.compile(r"\bdeals? \d+ damage to target\b", re.IGNORECASE),
    re.compile(r"\btarget (?:player|opponent) sacrifices?\b", re.IGNORECASE),
    re.compile(r"\btarget\b[^.\n]{0,100}\b(?:gains? hexproof|gains? indestructible|gains? protection)", re.IGNORECASE),
    re.compile(r"\bprevent all damage that would be dealt to target\b", re.IGNORECASE),
)
_MASS_INTERACTION_PATTERNS = (
    re.compile(r"\b(?:destroy|exile|counter) all\b", re.IGNORECASE),
    re.compile(r"\breturn all\b[^.\n]*\bto\b[^.\n]{0,40}\bhands?\b", re.IGNORECASE),
    re.compile(r"\beach player sacrifices?\b", re.IGNORECASE),
    re.compile(r"\beach opponent sacrifices?\b", re.IGNORECASE),
    re.compile(r"\bdeals? \d+ damage to each (?:creature|artifact|planeswalker|player|opponent)\b", re.IGNORECASE),
    re.compile(r"\b(?:creatures|permanents|players) you control gain\b[^.\n]{0,100}\b(?:hexproof|indestructible|protection)", re.IGNORECASE),
    re.compile(r"\bprevent all combat damage that would be dealt this turn", re.IGNORECASE),
    re.compile(r"\bprevent all damage that would be dealt to (?:creatures|permanents|players) you control", re.IGNORECASE),
    re.compile(r"\b(?:you and )?(?:all )?permanents you control gain protection from everything", re.IGNORECASE),
    re.compile(r"\b(?:all )?permanents you control phase out", re.IGNORECASE),
)


def classify_card_roles(
    type_line: str | None,
    oracle_text: str | None,
    *,
    main_strategy: bool = False,
) -> tuple[str, ...]:
    """Return transparent deckbuilding roles; main strategy is context-dependent."""
    text = oracle_text or ""
    roles: list[str] = []
    if _is_ramp(type_line, text):
        roles.append("Ramp (heuristic)")

    if _has_card_advantage(text):
        roles.append("Card Advantage (heuristic)")
    if any(pattern.search(text) for pattern in _SPOT_INTERACTION_PATTERNS):
        roles.append("Spot Interaction (heuristic)")
    if any(pattern.search(text) for pattern in _MASS_INTERACTION_PATTERNS):
        roles.append("Mass Interaction (heuristic)")
    if main_strategy:
        roles.append("Main Strategy (dynamic)")
    return tuple(roles)


def _has_card_advantage(text: str) -> bool:
    for segment in re.split(r"\n\s*\n", text):
        if (
            _REPEATED_RESOURCE_PATTERN.search(segment)
            or _REPEATABLE_ACTIVATED_DRAW_PATTERN.search(segment)
            or _REPEATED_INVESTIGATE_PATTERN.search(segment)
        ):
            return True
        draw_then_topdeck = _DRAW_THEN_TOPDECK_PATTERN.search(segment)
        if draw_then_topdeck:
            # The drawn cards replace cards already in hand; cards put back
            # on top do not increase the hand, and a sacrificed source costs
            # another card. This is selection, not net card advantage.
            continue
        loot = _LOOT_PATTERN.search(segment)
        if loot and _quantity_value(loot.group("draw")) <= _quantity_value(
            loot.group("discard")
        ):
            continue
        draw_count = sum(
            _quantity_value(match.group("count").casefold())
            for match in _CARD_DRAW_PATTERN.finditer(segment)
        )
        draw_count += sum(
            _quantity_value(match.group("count").casefold())
            for match in _MULTI_CARD_RECURSION_PATTERN.finditer(segment)
        )
        discard_count = sum(
            _quantity_value(match.group("count").casefold())
            for match in re.finditer(
                r"\bdiscard\s+(?P<count>a|one|two|three|four|five|\d+)\s+cards?\b",
                segment,
                re.IGNORECASE,
            )
        )
        if draw_count - discard_count - 1 > 0:
            return True
    return False


def has_card_selection(text: str | None) -> bool:
    """Detect filtering/selection effects without treating them as advantage."""
    source = text or ""
    return bool(
        _DRAW_THEN_TOPDECK_PATTERN.search(source)
        or _LOOT_PATTERN.search(source)
        or re.search(r"\b(?:scry|surveil)\s+\d+\b", source, re.IGNORECASE)
    )


def _is_ramp(type_line: str | None, text: str) -> bool:
    is_land = bool(
        type_line
        and "land" in type_line.casefold().split("—", maxsplit=1)[0].split()
    )
    if not is_land:
        return any(pattern.search(text) for pattern in _RAMP_PATTERNS)
    for match in _MULTI_MANA_PATTERN.finditer(text):
        if len(re.findall(r"\{[^}]+\}", match.group("mana"))) > 1:
            return True
    return False


def _quantity_value(value: str) -> int:
    return {
        "a": 1,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
    }.get(value, int(value) if value.isdigit() else 1)
