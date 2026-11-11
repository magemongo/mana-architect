import csv
from dataclasses import dataclass
from pathlib import Path


REQUIRED_COLUMNS = (
    "Name",
    "Set code",
    "Collector number",
    "Foil",
    "Quantity",
    "Scryfall ID",
    "Condition",
    "Language",
)
FINISHES = {
    "normal": "nonfoil",
    "foil": "foil",
    "etched": "etched",
}


class ImportFormatError(ValueError):
    """Raised when a ManaBox CSV cannot be imported without data loss."""


@dataclass(frozen=True)
class CollectionEntry:
    scryfall_id: str
    name: str
    set_code: str
    collector_number: str
    language: str
    finish: str
    quantity: int
    condition: str

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.scryfall_id, self.language, self.finish, self.condition)


@dataclass(frozen=True)
class ParsedCollection:
    entries: list[CollectionEntry]
    rows_read: int

    @property
    def duplicate_rows_combined(self) -> int:
        return self.rows_read - len(self.entries)


def parse_manabox_csv(path: Path) -> ParsedCollection:
    entries: dict[tuple[str, str, str, str], CollectionEntry] = {}
    rows_read = 0

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            raise ImportFormatError("O arquivo CSV está vazio ou não tem cabeçalho.")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ImportFormatError("O cabeçalho contém nomes de coluna duplicados.")

        missing_columns = [column for column in REQUIRED_COLUMNS if column not in reader.fieldnames]
        if missing_columns:
            missing = ", ".join(missing_columns)
            raise ImportFormatError(f"Colunas obrigatórias ausentes: {missing}.")

        for line_number, row in enumerate(reader, start=2):
            if not row or all(value is None or not value.strip() for value in row.values()):
                continue
            rows_read += 1
            if None in row:
                raise ImportFormatError(
                    f"Linha {line_number}: há mais valores do que colunas no cabeçalho."
                )

            values = {column: (row.get(column) or "").strip() for column in REQUIRED_COLUMNS}
            blank_columns = [column for column, value in values.items() if not value]
            if blank_columns:
                missing = ", ".join(blank_columns)
                raise ImportFormatError(
                    f"Linha {line_number}: campos obrigatórios vazios: {missing}."
                )

            try:
                quantity = int(values["Quantity"])
            except ValueError as error:
                raise ImportFormatError(
                    f"Linha {line_number}: Quantity deve ser um inteiro positivo."
                ) from error
            if quantity <= 0:
                raise ImportFormatError(
                    f"Linha {line_number}: Quantity deve ser um inteiro positivo."
                )

            finish_source = values["Foil"].casefold()
            if finish_source not in FINISHES:
                allowed = ", ".join(FINISHES)
                raise ImportFormatError(
                    f"Linha {line_number}: acabamento '{values['Foil']}' inválido; "
                    f"valores aceitos: {allowed}."
                )

            entry = CollectionEntry(
                scryfall_id=values["Scryfall ID"].casefold(),
                name=values["Name"],
                set_code=values["Set code"].upper(),
                collector_number=values["Collector number"],
                language=values["Language"].casefold(),
                finish=FINISHES[finish_source],
                quantity=quantity,
                condition=values["Condition"].casefold(),
            )
            previous = entries.get(entry.identity)
            if previous is None:
                entries[entry.identity] = entry
            else:
                if (
                    previous.name != entry.name
                    or previous.set_code != entry.set_code
                    or previous.collector_number != entry.collector_number
                ):
                    raise ImportFormatError(
                        f"Linha {line_number}: a mesma impressão possui dados conflitantes."
                    )
                entries[entry.identity] = CollectionEntry(
                    scryfall_id=previous.scryfall_id,
                    name=previous.name,
                    set_code=previous.set_code,
                    collector_number=previous.collector_number,
                    language=previous.language,
                    finish=previous.finish,
                    quantity=previous.quantity + entry.quantity,
                    condition=previous.condition,
                )

    return ParsedCollection(entries=list(entries.values()), rows_read=rows_read)

