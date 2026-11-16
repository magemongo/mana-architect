"""Hard constraints for candidate discovery and main-deck proposals."""

from collections import Counter, defaultdict
from dataclasses import dataclass
import re

from mana_architect.collection import VALID_COLORS
from mana_architect.deck_pipeline.contracts import (
    DeckBuildingContext,
    DeckProposal,
    ValidationIssue,
    ValidationReport,
)
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.interaction_rules_catalog import mask_reminder_text
from mana_architect.validation import deck_name_copy_limit, is_basic_land

RULESET_VERSION = "commander-snapshot-v1"
_BASIC_LAND_COLORS = dict(zip(
    ("plains", "island", "swamp", "mountain", "forest"), "WUBRG", strict=True,
))
_COMMANDER_PERMISSION = re.compile(
    r"^([^\r\n]+) can be your commander\.[ \t]*$", re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class CardEligibility:
    card_id: str
    owned_printing_ids: tuple[int, ...]
    available_quantity: int
    copy_limit: int | None
    violations: tuple[ValidationIssue, ...]

    @property
    def eligible(self) -> bool:
        return not self.violations


def _card_issues(card: CardSnapshot) -> tuple[ValidationIssue, ...]:
    issues = []
    if card.commander_legality != "legal":
        code = {
            "banned": "banned_card",
            "not_legal": "illegal_card",
            "restricted": "illegal_card",
        }.get(card.commander_legality, "unknown_card_legality")
        issues.append(ValidationIssue(
            code, f"{card.name}: Commander legality is {card.commander_legality or 'unknown'}.",
            card.card_id,
        ))
    if card.color_identity is None or not set(card.color_identity) <= VALID_COLORS:
        issues.append(ValidationIssue(
            "unknown_color_identity", f"{card.name} has no verified color identity.", card.card_id,
        ))
    if not card.type_line:
        issues.append(ValidationIssue("unknown_type_line", f"{card.name} has no type line.", card.card_id))
    return tuple(issues)


def _can_command(card: CardSnapshot) -> bool:
    front_type = (card.type_line or "").split(" // ", 1)[0].partition("—")[0]
    if {"legendary", "creature"} <= set(front_type.casefold().split()):
        return True
    front_text = (card.oracle_text or "").split(" // ", 1)[0]
    names = {card.name.split(" // ", 1)[0].casefold(), "this card", "this creature",
             "this planeswalker", "this permanent", "this artifact", "this enchantment"}
    return any(
        match[1].strip().casefold() in names
        for match in _COMMANDER_PERMISSION.finditer(mask_reminder_text(front_text))
    )


def _land_colors(type_line: str) -> set[str]:
    colors = set()
    for face in type_line.casefold().split(" // "):
        types, _, subtypes = face.partition("—")
        if "land" in types.split():
            colors.update(_BASIC_LAND_COLORS[s] for s in subtypes.split() if s in _BASIC_LAND_COLORS)
    return colors


class EligibilityGate:
    def __init__(self, context: DeckBuildingContext, snapshot: CollectionSnapshot):
        self.context = context
        self.snapshot = snapshot
        self._cards = {card.card_id: card for card in snapshot.cards}
        self._printings = {row.printing_id: row for row in snapshot.printings}
        self._by_card: dict[str, list[OwnedPrinting]] = defaultdict(list)
        for row in snapshot.printings:
            if row.card_id is not None:
                self._by_card[row.card_id].append(row)
        self.commander, self.violations = self._validate_context()

    def _validate_context(self) -> tuple[CardSnapshot | None, tuple[ValidationIssue, ...]]:
        issues = []
        if self.context.collection_snapshot_id != self.snapshot.snapshot_id:
            issues.append(ValidationIssue("snapshot_mismatch", "The context references a different collection."))
        if self.context.format != "commander" or self.context.ruleset_version != RULESET_VERSION:
            issues.append(ValidationIssue("unsupported_ruleset", "The format or ruleset is unsupported."))
        if not self.snapshot.legality_data_as_of:
            issues.append(ValidationIssue("missing_sync_date", "The collection has no dated legality snapshot."))
        ids = self.context.commander_card_ids
        if not ids or len(ids) > 2 or len(set(ids)) != len(ids) or not all(ids):
            issues.append(ValidationIssue("invalid_commander_configuration", "Invalid commander IDs."))
            return None, tuple(issues)
        if len(ids) == 2:
            issues.append(ValidationIssue(
                "unsupported_commander_configuration", "This ruleset adapter supports one commander.",
            ))
            return None, tuple(issues)
        commander = self._cards.get(ids[0])
        if commander is None:
            issues.append(ValidationIssue("commander_not_owned", "No linked owned commander.", ids[0]))
            return None, tuple(issues)
        issues.extend(_card_issues(commander))
        if not _can_command(commander):
            issues.append(ValidationIssue(
                "ineligible_commander", f"{commander.name} cannot command this deck.", commander.card_id,
            ))
        return commander, tuple(issues)

    @property
    def unlinked_printings(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            ValidationIssue(
                "unlinked_printing", f"{row.name} has no linked Oracle identity.",
                printing_id=row.printing_id,
            )
            for row in self.snapshot.printings if row.card_id is None
        )

    def check_card(self, card_id: str) -> CardEligibility:
        card = self._cards.get(card_id)
        rows = self._by_card.get(card_id, ())
        owned = sum(row.quantity for row in rows)
        copy_limit = None
        issues = list(self.violations)
        if card is None:
            issues.append(ValidationIssue("card_not_owned", "No linked owned copies.", card_id))
        else:
            issues.extend(_card_issues(card))
            if card_id in self.context.commander_card_ids:
                issues.append(ValidationIssue("card_is_commander", "The card is in the command zone.", card_id))
            if self.commander and self.commander.color_identity is not None and card.color_identity is not None:
                colors = set(card.color_identity) | _land_colors(card.type_line or "")
                excess = colors - set(self.commander.color_identity)
                if excess:
                    issues.append(ValidationIssue(
                        "outside_color_identity",
                        f"{card.name} requires colors outside the commander's identity: {', '.join(sorted(excess))}.",
                        card_id,
                    ))
            if not is_basic_land(card.type_line):
                copy_limit = deck_name_copy_limit(card.name, mask_reminder_text(card.oracle_text or ""))
        return CardEligibility(card_id, tuple(row.printing_id for row in rows), owned, copy_limit, tuple(issues))

    def validate_proposal(self, proposal: DeckProposal) -> ValidationReport:
        issues = list(self.violations)
        if proposal.run_id != self.context.run_id or proposal.commander_card_ids != self.context.commander_card_ids:
            issues.append(ValidationIssue("proposal_context_mismatch", "Proposal run or commander IDs differ."))
        quantities: Counter[str] = Counter()
        allocations: Counter[int] = Counter()
        for entry in sorted(proposal.main_deck, key=lambda entry: entry.card_id):
            if type(entry.quantity) is not int or entry.quantity < 1:
                issues.append(ValidationIssue("invalid_quantity", "Quantity must be a positive integer.", entry.card_id))
                continue
            quantities[entry.card_id] += entry.quantity
            allocated = 0
            for item in sorted(entry.printings, key=lambda item: item.printing_id):
                row = self._printings.get(item.printing_id)
                if type(item.quantity) is not int or item.quantity < 1:
                    issues.append(ValidationIssue(
                        "invalid_printing_quantity", "Printing quantity must be a positive integer.",
                        entry.card_id, item.printing_id,
                    ))
                    continue
                if row is None or row.card_id != entry.card_id:
                    issues.append(ValidationIssue(
                        "invalid_printing", "The printing is not owned for this card.",
                        entry.card_id, item.printing_id,
                    ))
                    continue
                allocated += item.quantity
                allocations[item.printing_id] += item.quantity
            if entry.printings and allocated != entry.quantity:
                issues.append(ValidationIssue(
                    "printing_allocation_mismatch", "Allocated printing quantities must equal the entry quantity.",
                    entry.card_id,
                ))

        expected = self.context.main_deck_size
        if sum(quantities.values()) != expected:
            issues.append(ValidationIssue("deck_size", f"Expected {expected} main-deck cards."))
        for printing_id, quantity in sorted(allocations.items()):
            row = self._printings[printing_id]
            if quantity > row.quantity:
                issues.append(ValidationIssue(
                    "insufficient_printing_quantity", f"Requested {quantity}, own {row.quantity}.",
                    row.card_id, printing_id,
                ))

        name_quantities: Counter[str] = Counter()
        if self.commander is not None:
            name_quantities[self.commander.name.casefold()] += 1
        copy_limits: dict[str, int] = {}
        for card_id, quantity in sorted(quantities.items()):
            result = self.check_card(card_id)
            issues.extend(issue for issue in result.violations if issue not in self.violations)
            if quantity > result.available_quantity:
                issues.append(ValidationIssue(
                    "insufficient_quantity", f"Requested {quantity}, own {result.available_quantity}.", card_id,
                ))
            if card_id in self._cards:
                name_quantities[self._cards[card_id].name.casefold()] += quantity
                if result.copy_limit is not None:
                    copy_limits[card_id] = result.copy_limit
        for card_id, limit in copy_limits.items():
            card = self._cards[card_id]
            quantity = name_quantities[card.name.casefold()]
            if quantity > limit:
                issues.append(ValidationIssue(
                    "copy_limit", f"{card.name}: requested {quantity}, limit {limit}.", card_id,
                ))
        # Stable even if equivalent proposal entries arrive in a different order.
        return ValidationReport(
            tuple(sorted(set(issues), key=lambda issue: (
                issue.code, issue.card_id or "", issue.printing_id or 0, issue.message,
            ))),
            self.snapshot.legality_data_as_of,
        )
