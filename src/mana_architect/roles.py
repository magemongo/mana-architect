import re


_DRAW_PATTERN = re.compile(r"\bdraw\b[^.\n]{0,100}\bcards?\b", re.IGNORECASE)
_RAMP_PATTERNS = (
    re.compile(r"\badd\s+(?:\{[^}]+\})+", re.IGNORECASE),
    re.compile(r"\bsearch your library for (?:a|up to|any number of) [^.\n]*land", re.IGNORECASE),
    re.compile(r"\bcreate\b[^.\n]*\btreasure token", re.IGNORECASE),
)
_REMOVAL_PATTERNS = (
    re.compile(r"\bdestroy target\b", re.IGNORECASE),
    re.compile(r"\bexile target\b", re.IGNORECASE),
    re.compile(r"\breturn target\b[^.\n]*\bto its owner's hand", re.IGNORECASE),
    re.compile(r"\bdeals? \d+ damage to target\b", re.IGNORECASE),
)
_RECURSION_PATTERNS = (
    re.compile(r"\breturn target\b[^.\n]*\bfrom your graveyard\b", re.IGNORECASE),
    re.compile(r"\breturn\b[^.\n]*\bfrom your graveyard to your hand\b", re.IGNORECASE),
)


def classify_card_roles(type_line: str | None, oracle_text: str | None) -> tuple[str, ...]:
    """Return transparent, text-based initial role labels, not strategic judgments."""
    is_land = bool(
        type_line and "land" in type_line.casefold().split("—", maxsplit=1)[0].split()
    )
    if is_land:
        return ("Terreno",)

    roles: list[str] = []
    text = oracle_text or ""
    if any(pattern.search(text) for pattern in _RAMP_PATTERNS):
        roles.append("Ramp (heurística)")
    if _DRAW_PATTERN.search(text):
        roles.append("Compra de cartas (heurística)")
    if any(pattern.search(text) for pattern in _REMOVAL_PATTERNS):
        roles.append("Remoção (heurística)")
    if any(pattern.search(text) for pattern in _RECURSION_PATTERNS):
        roles.append("Recursão (heurística)")
    return tuple(roles)
