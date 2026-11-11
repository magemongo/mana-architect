from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from mana_architect.models import Card, CollectionPrinting


VALID_COLORS = frozenset("WUBRG")


@dataclass(frozen=True)
class CollectionCount:
    name: str
    quantity: int
    mana_cost: str | None = None
    cmc: float | None = None
    type_line: str | None = None
    oracle_text: str | None = None


def _normalize_color_filter(value: str | None, field_name: str) -> set[str]:
    if value is None:
        return set()
    symbols = value.upper().replace(",", "").replace(" ", "")
    if not symbols or any(symbol not in VALID_COLORS for symbol in symbols):
        raise ValueError(f"{field_name} deve conter apenas símbolos W, U, B, R ou G.")
    return set(symbols)


def search_collection(
    session: Session,
    query: str | None = None,
    limit: int = 20,
    colors: str | None = None,
    color_identity: str | None = None,
    type_line: str | None = None,
    mana_cost: str | None = None,
    legal_commander_identity: tuple[str, ...] | None = None,
) -> list[CollectionCount]:
    term = (query or "").strip()
    color_filter = _normalize_color_filter(colors, "Cor")
    identity_filter = _normalize_color_filter(color_identity, "Identidade de cor")
    type_filter = (type_line or "").strip()
    mana_filter = (mana_cost or "").strip()
    if (
        not term
        and not color_filter
        and not identity_filter
        and not type_filter
        and not mana_filter
        and legal_commander_identity is None
    ):
        raise ValueError("Informe um termo de pesquisa ou ao menos um filtro.")
    if limit < 1:
        raise ValueError("O limite deve ser maior que zero.")

    card_name = func.coalesce(Card.name, CollectionPrinting.name)
    card_identity = func.coalesce(Card.oracle_id, CollectionPrinting.name)
    statement = (
        select(
            func.min(card_name),
            func.sum(CollectionPrinting.quantity),
            func.min(Card.mana_cost),
            func.min(Card.cmc),
            func.min(Card.type_line),
            Card.oracle_text,
        )
        .select_from(CollectionPrinting)
        .outerjoin(Card, CollectionPrinting.oracle_id == Card.oracle_id)
        .group_by(card_identity)
        .order_by(func.min(card_name))
        .limit(limit)
    )
    if term:
        statement = statement.where(
            func.lower(card_name).contains(term.casefold(), autoescape=True)
        )
    if type_filter:
        statement = statement.where(
            func.lower(Card.type_line).contains(type_filter.casefold(), autoescape=True)
        )
    if mana_filter:
        statement = statement.where(
            func.lower(Card.mana_cost).contains(mana_filter.casefold(), autoescape=True)
        )
    if legal_commander_identity is not None:
        identity_entries = (
            func.json_each(Card.color_identity)
            .table_valued("key", "value")
            .alias("card_color_identity")
        )
        statement = statement.where(
            Card.oracle_id.is_not(None),
            Card.legalities["commander"].as_string() == "legal",
            Card.color_identity.is_not(None),
            ~exists(
                select(1)
                .select_from(identity_entries)
                .where(identity_entries.c.value.not_in(legal_commander_identity))
            ),
        )

    for symbols, attribute in (
        (color_filter, Card.colors),
        (identity_filter, Card.color_identity),
    ):
        for symbol in sorted(symbols):
            entries = func.json_each(attribute).table_valued("key", "value").alias()
            statement = statement.where(
                exists(
                    select(1)
                    .select_from(entries)
                    .where(entries.c.value == symbol)
                )
            )

    return [
        CollectionCount(
            name=name,
            quantity=quantity,
            mana_cost=card_mana_cost,
            cmc=cmc,
            type_line=card_type_line,
            oracle_text=oracle_text,
        )
        for name, quantity, card_mana_cost, cmc, card_type_line, oracle_text
        in session.execute(statement)
    ]
