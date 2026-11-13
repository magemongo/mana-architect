"""Versioned Oracle patterns for extracting mana resources."""

import re

RAMP_RULES_VERSION = "ramp-v1"

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
QUANTITY = rf"(?:{'|'.join(NUMBER_WORDS)}|\d+|x|that many|any number of)"
MANA_SYMBOL = r"\{(?P<symbol>[^{}]+)\}"

MANA_PRODUCTION = re.compile(
    rf"\badd (?:(?P<symbols>(?:\{{[WUBRGC]\}})+)"
    rf"(?P<alternative>(?: or (?:\{{[WUBRGC]\}})+)+)?|"
    rf"(?P<count>{QUANTITY}) mana(?: (?P<color_rule>of [^.;\n]+|in [^.;\n]+))?)",
    re.IGNORECASE,
)
_LAND_FILTER = r"(?:basic )?(?:snow )?(?:land|plains|island|swamp|mountain|forest)"
_LAND_FILTERS = rf"{_LAND_FILTER}(?:(?:,? or|,) {_LAND_FILTER})*"
LAND_SEARCH = re.compile(
    rf"\bsearch your library for (?P<up_to>up to )?(?P<count>{QUANTITY}) "
    rf"(?P<filter>{_LAND_FILTERS} cards?),? (?:reveal (?:it|them|those cards),? )?"
    r"(?:and )?put (?P<moved>it|that card|them|those cards|one of them|one) "
    r"onto the battlefield(?P<tapped> tapped| untapped)?\b",
    re.IGNORECASE,
)
LAND_FROM_HAND = re.compile(
    rf"\bput (?P<up_to>up to )?(?P<count>{QUANTITY}) (?P<filter>{_LAND_FILTERS} cards?) "
    r"from your hand onto the battlefield(?P<tapped> tapped| untapped)?\b",
    re.IGNORECASE,
)
EXTRA_LAND_PLAY = re.compile(
    rf"\byou may play (?P<up_to>up to )?(?P<count>{QUANTITY}) additional lands?"
    r"(?P<duration> on each of your turns| each turn| this turn)?\b",
    re.IGNORECASE,
)
MANA_TOKEN_CREATION = re.compile(
    rf"\bcreate (?P<up_to>up to )?(?P<count>{QUANTITY}) "
    r"(?P<tapped>tapped )?(?P<token>treasure|gold|powerstone) tokens?\b",
    re.IGNORECASE,
)

# Token abilities used by the supported creation rules.
MANA_TOKENS = {
    "treasure": (("any",), "{T}, Sacrifice this token", ()),
    "gold": (("any",), "Sacrifice this token", ()),
    "powerstone": (("C",), "{T}", ("Cannot be spent to cast a nonartifact spell.",)),
}
