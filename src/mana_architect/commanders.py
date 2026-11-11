from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from mana_architect.collection import CollectionCount, search_collection
from mana_architect.models import Card, CollectionPrinting


class CommanderError(ValueError):
    """Raised when a requested commander is unavailable or not eligible."""


@dataclass(frozen=True)
class CommanderOption:
    oracle_id: str
    name: str
    color_identity: tuple[str, ...]
    quantity: int


def _eligibility_condition():
    return or_(
        Card.type_line.ilike("%legendary creature%"),
        Card.oracle_text.ilike("%can be your commander%"),
    )


def list_available_commanders(
    session: Session,
    query: str | None = None,
    limit: int = 100,
) -> list[CommanderOption]:
    if limit < 1:
        raise ValueError("O limite deve ser maior que zero.")

    statement = (
        select(
            Card.oracle_id,
            Card.name,
            Card.color_identity,
            func.sum(CollectionPrinting.quantity),
        )
        .join(CollectionPrinting, CollectionPrinting.oracle_id == Card.oracle_id)
        .where(
            _eligibility_condition(),
            Card.legalities["commander"].as_string() == "legal",
            Card.color_identity.is_not(None),
        )
        .group_by(Card.oracle_id)
        .order_by(Card.name)
        .limit(limit)
    )
    term = (query or "").strip()
    if term:
        statement = statement.where(
            func.lower(Card.name).contains(term.casefold(), autoescape=True)
        )
    return [
        CommanderOption(
            oracle_id=oracle_id,
            name=name,
            color_identity=tuple(color_identity),
            quantity=quantity,
        )
        for oracle_id, name, color_identity, quantity in session.execute(statement)
    ]


def resolve_commander(session: Session, identifier: str) -> CommanderOption:
    selection = identifier.strip()
    if not selection:
        raise CommanderError("Informe o nome exato ou oracle_id do comandante.")

    statement = (
        select(
            Card.oracle_id,
            Card.name,
            Card.color_identity,
            func.sum(CollectionPrinting.quantity),
        )
        .join(CollectionPrinting, CollectionPrinting.oracle_id == Card.oracle_id)
        .where(
            _eligibility_condition(),
            Card.legalities["commander"].as_string() == "legal",
            Card.color_identity.is_not(None),
            or_(
                func.lower(Card.name) == selection.casefold(),
                func.lower(Card.oracle_id) == selection.casefold(),
            ),
        )
        .group_by(Card.oracle_id)
        .order_by(Card.oracle_id)
    )
    matching = [
        CommanderOption(
            oracle_id=oracle_id,
            name=name,
            color_identity=tuple(color_identity),
            quantity=quantity,
        )
        for oracle_id, name, color_identity, quantity in session.execute(statement)
    ]
    if not matching:
        raise CommanderError(
            f"'{selection}' não corresponde a um comandante elegível e legal "
            "registrado na sua coleção. Consulte `commanders` para ver as opções."
        )
    if len(matching) > 1:
        oracle_ids = ", ".join(option.oracle_id for option in matching)
        raise CommanderError(
            f"'{selection}' corresponde a mais de um comandante; selecione pelo "
            f"oracle_id: {oracle_ids}."
        )
    return matching[0]


def search_for_commander(
    session: Session,
    commander_identifier: str,
    query: str | None = None,
    limit: int = 20,
    colors: str | None = None,
    color_identity: str | None = None,
    type_line: str | None = None,
    mana_cost: str | None = None,
) -> tuple[CommanderOption, list[CollectionCount]]:
    commander = resolve_commander(session, commander_identifier)
    compatible_cards = search_collection(
        session,
        query=query,
        limit=limit,
        colors=colors,
        color_identity=color_identity,
        type_line=type_line,
        mana_cost=mana_cost,
        legal_commander_identity=commander.color_identity,
    )
    return commander, compatible_cards
