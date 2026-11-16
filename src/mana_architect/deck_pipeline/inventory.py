"""Capture the linked and unresolved printing rows used by a deck-building run."""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.orm import Bundle, Session

from mana_architect.deck_pipeline.contracts import Artifact
from mana_architect.models import Card, CollectionPrinting, SyncMetadata


@dataclass(frozen=True)
class OwnedPrinting:
    printing_id: int
    scryfall_id: str
    card_id: str | None
    name: str
    set_code: str
    collector_number: str
    language: str
    finish: str
    condition: str
    quantity: int


@dataclass(frozen=True)
class CardSnapshot:
    card_id: str
    name: str
    type_line: str | None
    oracle_text: str | None
    mana_cost: str | None
    mana_value: float | None
    color_identity: tuple[str, ...] | None
    produced_mana: tuple[str, ...]
    keywords: tuple[str, ...]
    commander_legality: str | None
    colors: tuple[str, ...] | None = None


@dataclass(frozen=True)
class CollectionSnapshot(Artifact):
    cards: tuple[CardSnapshot, ...]
    printings: tuple[OwnedPrinting, ...]
    legality_data_as_of: str | None
    snapshot_id: str = field(init=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        cards = tuple(sorted(self.cards, key=lambda card: card.card_id))
        printings = tuple(sorted(self.printings, key=lambda row: row.printing_id))
        card_ids = {card.card_id for card in cards}
        if len(card_ids) != len(cards) or len({row.printing_id for row in printings}) != len(printings):
            raise ValueError("Snapshot card and printing IDs must be unique.")
        if any(type(row.quantity) is not int or row.quantity < 1 for row in printings):
            raise ValueError("Owned printing quantities must be positive integers.")
        if {row.card_id for row in printings if row.card_id is not None} != card_ids:
            raise ValueError("Linked printings must match the snapshot's card identities.")
        object.__setattr__(self, "cards", cards)
        object.__setattr__(self, "printings", printings)
        payload = {
            "schema_version": self.schema_version,
            "cards": [asdict(card) for card in cards],
            "printings": [asdict(row) for row in printings],
            "legality_data_as_of": self.legality_data_as_of,
        }
        digest = sha256(json.dumps(payload, sort_keys=True).encode("utf-8"))
        object.__setattr__(self, "snapshot_id", digest.hexdigest())


def load_collection_snapshot(session: Session) -> CollectionSnapshot:
    sync_date = select(SyncMetadata.value).where(
        SyncMetadata.key == "bulk_updated_at"
    ).scalar_subquery()
    printing_fields = Bundle(
        "printing", CollectionPrinting.id, CollectionPrinting.scryfall_id,
        CollectionPrinting.name, CollectionPrinting.set_code, CollectionPrinting.collector_number,
        CollectionPrinting.language, CollectionPrinting.finish, CollectionPrinting.condition,
        CollectionPrinting.quantity,
    )
    card_fields = Bundle(
        "card", Card.oracle_id, Card.name, Card.type_line, Card.oracle_text, Card.mana_cost,
        Card.cmc, Card.color_identity, Card.produced_mana, Card.keywords, Card.legalities, Card.colors,
    )
    statement = (
        select(printing_fields, card_fields, sync_date)
        .select_from(CollectionPrinting)
        .outerjoin(Card, Card.oracle_id == CollectionPrinting.oracle_id)
        .order_by(CollectionPrinting.id)
    )
    cards: dict[str, CardSnapshot] = {}
    printings: list[OwnedPrinting] = []
    data_as_of = None
    for row, card, data_as_of in session.execute(statement):
        card_id = card.oracle_id
        printings.append(OwnedPrinting(
            row.id, row.scryfall_id, card_id, row.name, row.set_code,
            row.collector_number, row.language, row.finish, row.condition, row.quantity,
        ))
        if card_id is not None and card_id not in cards:
            cards[card_id] = CardSnapshot(
                card_id, card.name, card.type_line, card.oracle_text,
                card.mana_cost, card.cmc,
                tuple(sorted(card.color_identity)) if card.color_identity is not None else None,
                tuple(card.produced_mana or ()), tuple(card.keywords or ()),
                (card.legalities or {}).get("commander"),
                tuple(sorted(card.colors)) if card.colors is not None else None,
            )
    if not printings:
        data_as_of = session.scalar(select(sync_date))
    return CollectionSnapshot(tuple(cards.values()), tuple(printings), data_as_of)
