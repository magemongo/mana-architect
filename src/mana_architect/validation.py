import re
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from mana_architect.models import Card, CollectionPrinting, SyncMetadata


RULES_SOURCE = "https://mtgcommander.net/index.php/rules/"
LEGALITY_SOURCE = "Scryfall Bulk Data: default_cards"
NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
ANY_NUMBER_PATTERN = re.compile(
    r"\ba deck can have any number of cards named\s+([^.\n]+)",
    re.IGNORECASE,
)
UP_TO_NUMBER_PATTERN = re.compile(
    r"\ba deck can have up to\s+(one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"\s+cards named\s+([^.\n]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DeckCard:
    oracle_id: str
    quantity: int


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class DeckValidationResult:
    status: Literal["valid", "invalid", "incomplete"]
    total_cards: int
    commander_name: str
    data_as_of: str | None
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]
    unverified: tuple[ValidationIssue, ...]
    rules_source: str
    legality_source: str


def is_basic_land(type_line: str | None) -> bool:
    if not type_line:
        return False
    card_types = type_line.split("—", maxsplit=1)[0].split()
    lowered = {card_type.casefold() for card_type in card_types}
    return "basic" in lowered and "land" in lowered


def _named_card(text: str) -> str:
    return text.strip().strip("'\"").strip()


def deck_name_copy_limit(name: str, oracle_text: str | None) -> int:
    if not oracle_text:
        return 1

    for match in ANY_NUMBER_PATTERN.finditer(oracle_text):
        if _named_card(match.group(1)).casefold() == name.casefold():
            return 100

    for match in UP_TO_NUMBER_PATTERN.finditer(oracle_text):
        if _named_card(match.group(2)).casefold() == name.casefold():
            value = match.group(1).casefold()
            return int(value) if value.isdecimal() else NUMBER_WORDS[value]
    return 1


def _load_commander(session: Session, identifier: str) -> tuple[Card | None, int]:
    selection = identifier.strip()
    if not selection:
        return None, 0

    statement = (
        select(Card, func.coalesce(func.sum(CollectionPrinting.quantity), 0))
        .outerjoin(CollectionPrinting, CollectionPrinting.oracle_id == Card.oracle_id)
        .where(
            or_(
                func.lower(Card.oracle_id) == selection.casefold(),
                func.lower(Card.name) == selection.casefold(),
            )
        )
        .group_by(Card.oracle_id)
        .order_by(Card.oracle_id)
    )
    matching = list(session.execute(statement))
    if not matching:
        return None, 0
    if len(matching) > 1:
        raise ValueError(
            f"'{selection}' corresponde a várias identidades; informe o oracle_id."
        )
    card, quantity = matching[0]
    return card, quantity


def load_deck_file(path: Path) -> tuple[str, list[DeckCard]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(f"JSON inválido na linha {error.lineno}: {error.msg}.") from error
    if not isinstance(payload, dict):
        raise ValueError("O arquivo do deck deve conter um objeto JSON.")

    commander_identifier = payload.get("commander")
    if not isinstance(commander_identifier, str) or not commander_identifier.strip():
        raise ValueError("O campo commander deve conter o nome ou oracle_id.")

    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        raise ValueError("O campo cards deve ser uma lista.")

    deck_cards: list[DeckCard] = []
    for index, raw_card in enumerate(raw_cards, start=1):
        if not isinstance(raw_card, dict):
            raise ValueError(f"cards[{index}] deve ser um objeto.")
        oracle_id = raw_card.get("oracle_id")
        quantity = raw_card.get("quantity")
        if not isinstance(oracle_id, str) or not oracle_id.strip():
            raise ValueError(f"cards[{index}].oracle_id deve ser um texto não vazio.")
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ValueError(f"cards[{index}].quantity deve ser um inteiro.")
        deck_cards.append(DeckCard(oracle_id=oracle_id, quantity=quantity))
    return commander_identifier, deck_cards


def validate_deck(
    session: Session,
    commander_identifier: str,
    deck_cards: list[DeckCard],
) -> DeckValidationResult:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    unverified: list[ValidationIssue] = []
    normalized_cards: dict[str, int] = {}
    for entry in deck_cards:
        oracle_id = entry.oracle_id.strip()
        if not oracle_id:
            errors.append(ValidationIssue("invalid_card_id", "Uma carta não tem oracle_id."))
            continue
        if isinstance(entry.quantity, bool) or not isinstance(entry.quantity, int):
            errors.append(
                ValidationIssue(
                    "invalid_quantity",
                    f"A quantidade de {oracle_id} deve ser um inteiro.",
                )
            )
            continue
        if entry.quantity <= 0:
            errors.append(
                ValidationIssue(
                    "invalid_quantity",
                    f"A quantidade de {oracle_id} deve ser maior que zero.",
                )
            )
            continue
        normalized_cards[oracle_id] = (
            normalized_cards.get(oracle_id, 0) + entry.quantity
        )

    total_cards = sum(normalized_cards.values())
    if total_cards != 100:
        errors.append(
            ValidationIssue(
                "deck_size",
                f"O deck tem {total_cards} cartas; Commander exige exatamente 100 "
                "incluindo o comandante.",
            )
        )

    commander, commander_owned_quantity = _load_commander(
        session,
        commander_identifier,
    )
    commander_name = commander.name if commander is not None else commander_identifier
    if commander is None:
        unverified.append(
            ValidationIssue(
                "unknown_commander",
                f"Comandante '{commander_identifier}' não foi encontrado nos dados "
                "sincronizados; não é possível confirmar sua identidade ou elegibilidade.",
            )
        )
    else:
        commander_count = normalized_cards.get(commander.oracle_id, 0)
        if commander_count != 1:
            errors.append(
                ValidationIssue(
                    "commander_count",
                    f"O comandante {commander.name} deve aparecer exatamente uma vez; "
                    f"o deck lista {commander_count}.",
                )
            )
        if commander_owned_quantity < 1:
            errors.append(
                ValidationIssue(
                    "commander_not_owned",
                    f"O comandante {commander.name} não está registrado na coleção.",
                )
            )

        type_line = commander.type_line or ""
        is_eligible = (
            "legendary creature" in type_line.casefold()
            or bool(
                commander.oracle_text
                and re.search(
                    r"\bcan be your commander\b",
                    commander.oracle_text,
                    re.IGNORECASE,
                )
            )
        )
        if not is_eligible:
            errors.append(
                ValidationIssue(
                    "ineligible_commander",
                    f"{commander.name} não está identificado como comandante elegível.",
                )
            )
        legality = (commander.legalities or {}).get("commander")
        if legality == "banned":
            errors.append(
                ValidationIssue(
                    "banned_commander",
                    f"{commander.name} consta como banido em Commander nos dados "
                    "sincronizados.",
                )
            )
        elif legality == "not_legal":
            errors.append(
                ValidationIssue(
                    "illegal_commander",
                    f"{commander.name} consta como não legal em Commander nos dados "
                    "sincronizados.",
                )
            )
        elif legality != "legal":
            unverified.append(
                ValidationIssue(
                    "unknown_commander_legality",
                    f"A legalidade Commander de {commander.name} não está confirmada "
                    "nos dados sincronizados.",
                )
            )
        if commander.color_identity is None:
            unverified.append(
                ValidationIssue(
                    "unknown_commander_identity",
                    f"A identidade de cor de {commander.name} não está disponível.",
                )
            )

    card_ids = set(normalized_cards)
    if commander is not None:
        card_ids.add(commander.oracle_id)
    cards = {
        card.oracle_id: card
        for card in session.scalars(select(Card).where(Card.oracle_id.in_(card_ids)))
    } if card_ids else {}
    owned_quantities = {
        oracle_id: quantity
        for oracle_id, quantity in session.execute(
            select(
                CollectionPrinting.oracle_id,
                func.sum(CollectionPrinting.quantity),
            )
            .where(CollectionPrinting.oracle_id.in_(card_ids))
            .group_by(CollectionPrinting.oracle_id)
        )
    } if card_ids else {}

    commander_identity = (
        set(commander.color_identity)
        if commander is not None and commander.color_identity is not None
        else None
    )
    for oracle_id in normalized_cards:
        card = cards.get(oracle_id)
        if card is None:
            unverified.append(
                ValidationIssue(
                    "unknown_card",
                    f"A carta {oracle_id} não foi encontrada nos dados sincronizados; "
                    "não é possível confirmar seus atributos.",
                )
            )
            continue
        if commander_identity is not None:
            if card.color_identity is None:
                unverified.append(
                    ValidationIssue(
                        "unknown_card_identity",
                        f"A identidade de cor de {card.name} não está disponível.",
                    )
                )
            else:
                excess_colors = set(card.color_identity) - commander_identity
                if excess_colors:
                    errors.append(
                        ValidationIssue(
                            "color_identity",
                            f"{card.name} tem identidade de cor incompatível com "
                            f"{commander.name}: {''.join(sorted(excess_colors))}.",
                        )
                    )

        legality = (card.legalities or {}).get("commander")
        if commander is not None and oracle_id == commander.oracle_id:
            continue
        if legality == "banned":
            errors.append(
                ValidationIssue(
                    "banned_card",
                    f"{card.name} consta como banida em Commander nos dados "
                    "sincronizados.",
                )
            )
        elif legality == "not_legal":
            errors.append(
                ValidationIssue(
                    "illegal_card",
                    f"{card.name} consta como não legal em Commander nos dados "
                    "sincronizados.",
                )
            )
        elif legality != "legal":
            unverified.append(
                ValidationIssue(
                    "unknown_card_legality",
                    f"A legalidade Commander de {card.name} não está confirmada "
                    "nos dados sincronizados.",
                )
            )

    for oracle_id, requested_quantity in normalized_cards.items():
        card = cards.get(oracle_id)
        if card is None:
            continue
        available_quantity = owned_quantities.get(oracle_id, 0)
        if requested_quantity > available_quantity:
            errors.append(
                ValidationIssue(
                    "insufficient_quantity",
                    f"{card.name}: o deck usa {requested_quantity}, mas a coleção "
                    f"registra {available_quantity}.",
                )
            )

    name_groups: dict[str, list[Card]] = {}
    for oracle_id in normalized_cards:
        card = cards.get(oracle_id)
        if card is not None:
            name_groups.setdefault(card.name.casefold(), []).append(card)
    for cards_with_same_name in name_groups.values():
        quantity = sum(normalized_cards[card.oracle_id] for card in cards_with_same_name)
        if any(is_basic_land(card.type_line) for card in cards_with_same_name):
            continue
        name = cards_with_same_name[0].name
        maximum = max(
            (
                deck_name_copy_limit(card.name, card.oracle_text)
                for card in cards_with_same_name
            ),
            default=1,
        )
        if quantity > maximum:
            errors.append(
                ValidationIssue(
                    "singleton",
                    f"{name} aparece {quantity} vezes; o limite é {maximum}.",
                )
            )

    metadata = session.get(SyncMetadata, "bulk_updated_at")
    data_as_of = metadata.value if metadata is not None else None
    if data_as_of is None:
        unverified.append(
            ValidationIssue(
                "missing_sync_date",
                "Não há data de sincronização registrada; legalidade e banlist "
                "não podem ser declaradas atualizadas.",
            )
        )
    else:
        warnings.append(
            ValidationIssue(
                "snapshot_legality",
                f"Legalidade e banlist foram verificadas apenas conforme "
                f"{LEGALITY_SOURCE} de {data_as_of}; isso não confirma vigência atual.",
            )
        )
    if errors:
        status: Literal["valid", "invalid", "incomplete"] = "invalid"
    elif unverified:
        status = "incomplete"
    else:
        status = "valid"
    return DeckValidationResult(
        status=status,
        total_cards=total_cards,
        commander_name=commander_name,
        data_as_of=data_as_of,
        errors=tuple(errors),
        warnings=tuple(warnings),
        unverified=tuple(unverified),
        rules_source=RULES_SOURCE,
        legality_source=LEGALITY_SOURCE,
    )
