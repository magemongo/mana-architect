"""Source evidence for activations that consume the same untapped state."""

import re

from mana_architect.ability_boundaries import AbilityBoundary
from mana_architect.collection_playability.contracts import MANA_AND_TAP_COST, SharedTapPrerequisite, TapCostCitation
from mana_architect.deck_pipeline.inventory import CardSnapshot


_PERMANENT_TYPES = {"Artifact", "Battle", "Creature", "Enchantment", "Land", "Planeswalker"}
_ACTIVATION_INSTRUCTION = re.compile(r"\b(?:activate|activation)\b", re.IGNORECASE)


def _top_level(source_prefix: str) -> bool:
    """Exclude cost starts inside quoted or parenthesized text split across boundaries."""
    closing_quote = None
    parentheses = 0
    for char in source_prefix:
        if closing_quote:
            if char == closing_quote:
                closing_quote = None
        elif char in {'"', '“'}:
            closing_quote = '”' if char == '“' else '"'
        elif char == '(':
            parentheses += 1
        elif char == ')':
            parentheses -= 1
            if parentheses < 0:
                return False
    return closing_quote is None and parentheses == 0


def find_shared_tap_prerequisites(
    card: CardSnapshot, boundaries: tuple[AbilityBoundary, ...], verified_draw_ids: set[str],
) -> tuple[SharedTapPrerequisite, ...]:
    type_line, oracle = card.type_line or "", card.oracle_text or ""
    if (not verified_draw_ids or "//" in type_line or " // " in oracle
            or not _PERMANENT_TYPES.intersection(type_line.partition(" — ")[0].split())):
        return ()

    costs = []
    for boundary in boundaries:
        if (boundary.card_id != card.card_id or boundary.face_index != 0
                or boundary.kind != "activated" or boundary.status != "resolved"):
            continue
        cost, colon, effect = boundary.source_text.partition(":")
        if (not colon or cost != boundary.activation_cost or not MANA_AND_TAP_COST.fullmatch(cost)
                or _ACTIVATION_INSTRUCTION.search(effect)
                or oracle[boundary.start:boundary.end] != boundary.source_text
                or not _top_level(oracle[:boundary.start])):
            continue
        costs.append(TapCostCitation(boundary.ability_id, boundary.start, boundary.start + len(cost), cost))

    if len(costs) < 2 or not any(cost.boundary_id in verified_draw_ids for cost in costs):
        return ()
    return (SharedTapPrerequisite(card.card_id, 0, tuple(costs)),)
