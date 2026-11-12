import argparse
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from mana_architect.collection import search_collection
from mana_architect.commanders import list_available_commanders, search_for_commander
from mana_architect.deckbuilding import (
    analyze_deck,
    build_deck,
    export_deck,
    export_decklist,
)
from mana_architect.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from mana_architect.importer import import_manabox_csv
from mana_architect.manabox import ImportFormatError
from mana_architect.models import SyncMetadata
from mana_architect.roles import classify_card_roles
from mana_architect.scryfall import ScryfallError, sync_collection
from mana_architect.validation import load_deck_file, validate_deck


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mana-architect")
    commands = parser.add_subparsers(dest="command", required=True)

    import_command = commands.add_parser(
        "import-csv",
        help="Importa um CSV exportado pelo ManaBox.",
    )
    import_command.add_argument("path", type=Path, help="Caminho do arquivo CSV.")

    search_command = commands.add_parser(
        "search",
        help="Pesquisa cartas na coleção.",
    )
    search_command.add_argument(
        "query",
        nargs="?",
        help="Texto opcional a pesquisar no nome da carta.",
    )
    search_command.add_argument("--limit", type=int, default=20)
    search_command.add_argument(
        "--commander",
        help="Filtra pela identidade e legalidade de um comandante escolhido por você.",
    )
    search_command.add_argument(
        "--roles",
        action="store_true",
        help="Exibe classificações iniciais baseadas em texto Oracle.",
    )

    commanders_command = commands.add_parser(
        "commanders",
        help="Lista comandantes elegíveis e legais que você possui.",
    )
    commanders_command.add_argument(
        "--query",
        help="Filtra opções pelo nome do comandante.",
    )
    commanders_command.add_argument("--limit", type=int, default=100)

    sync_command = commands.add_parser(
        "sync-scryfall",
        help="Atualiza dados de cartas e associa impressões da coleção.",
    )
    sync_command.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/scryfall"),
        help="Pasta local para o cache Bulk Data.",
    )

    validate_command = commands.add_parser(
        "validate-deck",
        help="Valida uma decklist Commander JSON contra regras e sua coleção.",
    )
    validate_command.add_argument("path", type=Path, help="Arquivo JSON da decklist.")

    analyze_command = commands.add_parser(
        "analyze-deck",
        help="Mostra nomes, curva de mana, terrenos e funções heurísticas de um deck.",
    )
    analyze_command.add_argument("path", type=Path, help="Arquivo JSON da decklist.")
    analyze_command.add_argument(
        "--export-text",
        type=Path,
        help="Também exporta uma lista legível em texto neste caminho.",
    )
    analyze_command.add_argument(
        "--overwrite",
        action="store_true",
        help="Permite substituir o arquivo de texto se já existir.",
    )

    build_command = commands.add_parser(
        "build-deck",
        help="Gera um rascunho de deck com cartas legais e disponíveis na coleção.",
    )
    build_command.add_argument(
        "commander",
        help="Nome exato ou oracle_id do comandante escolhido pelo usuário.",
    )
    build_command.add_argument(
        "--lands",
        type=int,
        default=38,
        help="Quantidade de terrenos no rascunho (padrão: 38).",
    )
    build_command.add_argument(
        "--strategy",
        help=(
            "ID de uma estratégia ativa do catálogo; exige afinidade cadastrada "
            "para o comandante. Sem esta opção, usa o gerador legado."
        ),
    )
    build_command.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Arquivo JSON de destino; não será sobrescrito sem --overwrite.",
    )
    build_command.add_argument(
        "--overwrite",
        action="store_true",
        help="Permite substituir o arquivo de destino se ele já existir.",
    )

    search_command.add_argument(
        "--color",
        help="Exige todas as cores indicadas (ex.: U ou WU).",
    )
    search_command.add_argument(
        "--identity",
        help="Exige os símbolos de identidade indicados (ex.: WU).",
    )
    search_command.add_argument(
        "--type",
        dest="type_line",
        help="Filtra por trecho do tipo de carta (ex.: Creature).",
    )
    search_command.add_argument(
        "--mana-cost",
        help="Filtra por trecho do custo de mana (ex.: {2}{U}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    engine = create_database_engine()

    try:
        initialize_database(engine)
        if args.command == "import-csv":
            summary = import_manabox_csv(args.path, engine)
            print(f"Linhas lidas: {summary.rows_read}")
            print(f"Impressões distintas no arquivo: {summary.unique_printings}")
            print(f"Linhas repetidas agrupadas: {summary.duplicate_rows_combined}")
            print(f"Quantidade total indicada no arquivo: {summary.quantities_in_file}")
            print(f"Novos registros: {summary.inserted}")
            print(f"Registros atualizados: {summary.updated}")
            print("Registros ausentes do arquivo não foram removidos.")
            return 0

        if args.command == "sync-scryfall":
            summary = sync_collection(engine, cache_directory=args.cache_dir)
            print(f"Bulk Data atualizado em: {summary.bulk_updated_at}")
            print(
                "Bulk Data baixado: "
                + ("sim" if summary.bulk_downloaded else "não (cache reutilizado)")
            )
            print(f"Cartas atualizadas: {summary.cards_updated}")
            print(f"Impressões associadas: {summary.printings_linked}")
            print(f"Impressões ainda sem associação: {summary.unresolved_printings}")
            return 0

        if args.command == "commanders":
            if args.limit < 1:
                parser.error("--limit deve ser maior que zero.")
            session_factory = create_session_factory(engine)
            with session_factory() as session:
                options = list_available_commanders(
                    session,
                    query=args.query,
                    limit=args.limit,
                )
                bulk_sync = session.get(SyncMetadata, "bulk_updated_at")
            if bulk_sync is not None:
                print(f"Legalidade conforme dados Scryfall de {bulk_sync.value}.")
            else:
                print("Sem dados de legalidade sincronizados; execute sync-scryfall.")
            if not options:
                print("Nenhum comandante elegível e legal foi encontrado na coleção.")
                return 0
            print("Escolha você um comandante (nome ou oracle_id):")
            for option in options:
                identity = "".join(option.color_identity) or "sem cor"
                print(
                    f"{option.name}  [{identity}]  "
                    f"quantidade: {option.quantity}  oracle_id: {option.oracle_id}"
                )
            return 0

        if args.command == "validate-deck":
            commander_identifier, deck_cards = load_deck_file(args.path)
            session_factory = create_session_factory(engine)
            with session_factory() as session:
                result = validate_deck(session, commander_identifier, deck_cards)
            print(f"Comandante: {result.commander_name}")
            print(f"Cartas no deck: {result.total_cards}/100")
            print(f"Status: {result.status}")
            print(f"Regras: {result.rules_source}")
            if result.data_as_of:
                print(
                    f"Legalidade: {result.legality_source}, dados de "
                    f"{result.data_as_of}"
                )
            for issue in result.errors:
                print(f"ERRO [{issue.code}]: {issue.message}")
            for issue in result.unverified:
                print(f"NÃO VERIFICADO [{issue.code}]: {issue.message}")
            for issue in result.warnings:
                print(f"AVISO [{issue.code}]: {issue.message}")
            if result.status == "invalid":
                return 1
            if result.status == "incomplete":
                return 2
            return 0

        if args.command == "analyze-deck":
            commander_identifier, deck_cards = load_deck_file(args.path)
            session_factory = create_session_factory(engine)
            with session_factory() as session:
                analysis = analyze_deck(
                    session,
                    commander_identifier,
                    deck_cards,
                )
            print(f"Comandante: {analysis.commander_name}")
            print(
                f"Cartas: {analysis.total_cards}/100 "
                f"({analysis.land_count} terrenos, "
                f"{analysis.nonland_count} não-terrenos)"
            )
            print(f"Validação: {analysis.validation.status}")
            if analysis.validation.data_as_of:
                print(
                    f"Legalidade conforme dados de "
                    f"{analysis.validation.data_as_of}"
                )
            print("Curva de mana (não-terrenos, incluindo comandante):")
            if analysis.mana_curve:
                for mana_value, quantity in analysis.mana_curve.items():
                    print(f"  {mana_value:g}: {quantity}")
            else:
                print("  sem dados de custo de mana")
            print("Funções detectadas (heurísticas; categorias podem se sobrepor):")
            for role, quantity in analysis.role_counts.items():
                print(f"  {role}: {quantity}")
            print("Lista:")
            for card in analysis.cards:
                marker = " [comandante]" if card.is_commander else ""
                cost = f" — {card.mana_cost}" if card.mana_cost else ""
                roles = f" [{', '.join(card.roles)}]" if card.roles else ""
                print(f"  {card.quantity}x {card.name}{marker}{cost}{roles}")
            for issue in analysis.validation.errors:
                print(f"ERRO [{issue.code}]: {issue.message}")
            for issue in analysis.validation.unverified:
                print(f"NÃO VERIFICADO [{issue.code}]: {issue.message}")
            for warning in analysis.validation.warnings:
                print(f"AVISO [{warning.code}]: {warning.message}")
            if args.export_text:
                export_decklist(
                    analysis,
                    args.export_text,
                    overwrite=args.overwrite,
                )
                print(f"Decklist em texto salva: {args.export_text}")
            if analysis.validation.status == "invalid":
                return 1
            if analysis.validation.status == "incomplete":
                return 2
            return 0

        if args.command == "build-deck":
            session_factory = create_session_factory(engine)
            with session_factory() as session:
                result = build_deck(
                    session,
                    commander_identifier=args.commander,
                    land_count=args.lands,
                    strategy_id=args.strategy,
                )
            export_deck(result, args.output, overwrite=args.overwrite)
            print(f"Rascunho para {result.commander.name}")
            if result.catalog_strategy_name:
                print(
                    "Estratégia do catálogo: "
                    f"{result.catalog_strategy_name} ({result.catalog_strategy_id})"
                )
            if result.unclassified_count:
                print(
                    "AVISO: "
                    f"{result.unclassified_count} cartas não tinham classificação "
                    "atual e não foram consideradas pelo modo do catálogo."
                )
            print(f"Cartas: {sum(card.quantity for card in result.cards)}/100")
            print(f"Terrenos: {result.land_count}")
            print(f"Status do validador: {result.validation.status}")
            if result.validation.data_as_of:
                print(f"Legalidade conforme dados de {result.validation.data_as_of}")
            for role, count in result.role_counts.items():
                print(f"{role}: {count}")
            for warning in result.validation.warnings:
                print(f"AVISO: {warning.message}")
            for issue in result.validation.unverified:
                print(f"NÃO VERIFICADO: {issue.message}")
            print(f"Arquivo salvo: {args.output}")
            if result.validation.status == "incomplete":
                print("O rascunho foi exportado, mas a validação está incompleta.")
                return 2
            print(
                "A lista usa seleção heurística; revise estratégia, curva e "
                "sinergias antes de jogar."
            )
            return 0

        if args.limit < 1:
            parser.error("--limit deve ser maior que zero.")
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            if args.commander:
                commander, matches = search_for_commander(
                    session,
                    commander_identifier=args.commander,
                    query=args.query,
                    limit=args.limit,
                    colors=args.color,
                    color_identity=args.identity,
                    type_line=args.type_line,
                    mana_cost=args.mana_cost,
                )
            else:
                matches = search_collection(
                    session,
                    query=args.query,
                    limit=args.limit,
                    colors=args.color,
                    color_identity=args.identity,
                    type_line=args.type_line,
                    mana_cost=args.mana_cost,
                )
        if not matches:
            print("Nenhuma carta encontrada.")
            return 0
        if args.commander:
            identity = "".join(commander.color_identity) or "sem cor"
            print(f"Comandante escolhido: {commander.name} [{identity}]")
        for match in matches:
            details = []
            if match.mana_cost:
                details.append(match.mana_cost)
            if match.type_line:
                details.append(match.type_line)
            if args.roles:
                roles = classify_card_roles(match.type_line, match.oracle_text)
                details.extend(roles or ("sem classificação inicial",))
            suffix = f"  ({'; '.join(details)})" if details else ""
            print(f"{match.quantity:>4}  {match.name}{suffix}")
        return 0
    except (ImportFormatError, ScryfallError, OSError, SQLAlchemyError, ValueError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
