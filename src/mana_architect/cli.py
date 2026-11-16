import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from mana_architect.candidate_evaluation import rank_collection_candidates
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
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS
from mana_architect.deck_pipeline.candidate_pool import CandidatePoolConfig, PoolConstraints
from mana_architect.deck_pipeline.composition_replay import CompositionReplay, ReplayError, read_saved_proposal
from mana_architect.deck_pipeline.composition_utility import CompositionConfig
from mana_architect.deck_pipeline.contracts import (
    DeckBuildingContext, DeckProposal, StrategyPreference, ValidationIssue, artifact_digest,
)
from mana_architect.deck_pipeline.eligibility import EligibilityGate, PARTNER_RULESET_VERSION
from mana_architect.deck_pipeline.inventory import CollectionSnapshot, load_collection_snapshot
from mana_architect.deck_pipeline.preliminary_composer import PreliminaryDeckComposer
from mana_architect.deck_pipeline.refinement import DeckRefinementEngine, RefinementConfig, RefinementResult
from mana_architect.importer import import_manabox_csv
from mana_architect.manabox import ImportFormatError
from mana_architect.models import SyncMetadata
from mana_architect.roles import classify_card_roles
from mana_architect.scryfall import ScryfallError, sync_collection
from mana_architect.strategy_synergy import SelectedStrategy, load_synergy_context
from mana_architect.validation import load_deck_file, validate_deck


def _composition_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--partner", help="Exact name or Oracle ID of the second Partner commander.")
    command.add_argument("--strategy", action="append", default=[], metavar="ID[=PRIORITY]")
    command.add_argument("--land-target", type=int, help="Soft target for front-face lands (default: 38).")
    command.add_argument("--lock", action="append", default=[], metavar="ORACLE_ID")
    command.add_argument("--exclude", action="append", default=[], metavar="ORACLE_ID")
    command.add_argument("--output", type=Path, help="Write the JSON result to a file.")
    command.add_argument("--overwrite", action="store_true")


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

    evaluate_command = commands.add_parser(
        "evaluate-candidates", help="Rank owned candidates by mechanical synergy before building a deck.",
    )
    evaluate_command.add_argument("commander", help="Exact name or Oracle ID of an owned commander.")
    evaluate_command.add_argument(
        "--strategy", action="append", default=[], metavar="ID[=PRIORITY]",
        help="Select a strategy with an optional priority. Repeat for multiple strategies.",
    )
    evaluate_command.add_argument("--limit", type=int, default=20, help="Number of ranked candidates to return.")

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

    compose_command = commands.add_parser(
        "compose", help="Compose and validate a preliminary Commander deck from owned cards.",
    )
    compose_command.add_argument("commander", help="Exact name or Oracle ID of an owned commander.")
    _composition_options(compose_command)

    refine_command = commands.add_parser("refine", help="Improve an owned Commander deck through bounded legal swaps.")
    refine_command.add_argument("commander", nargs="?", help="Compose and refine a deck for an owned commander.")
    refine_command.add_argument("--proposal", type=Path, help="Refine the exact deck in a saved proposal JSON file.")
    _composition_options(refine_command)
    refine_command.add_argument("--prefer", action="append", default=[], metavar="ORACLE_ID")
    limits = RefinementConfig()
    refine_command.add_argument("--max-swaps", type=int, default=limits.max_swaps)
    refine_command.add_argument("--attempts-per-iteration", type=int, default=limits.attempts_per_iteration)
    refine_command.add_argument("--max-attempts", type=int, default=limits.max_attempts)
    refine_command.add_argument("--min-improvement", type=float, default=limits.min_improvement)

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


def _snapshot_oracle_id(snapshot: CollectionSnapshot, identifier: str) -> str:
    value = identifier.strip()
    matches = [card.card_id for card in snapshot.cards if card.card_id.casefold() == value.casefold()]
    if not matches:
        matches = [card.card_id for card in snapshot.cards if card.name.casefold() == value.casefold()]
    if len(matches) > 1:
        raise ValueError(f"'{value}' matches multiple owned cards. Use one Oracle ID: {', '.join(matches)}.")
    return matches[0] if matches else value


def _composition_inputs(args: argparse.Namespace, snapshot: CollectionSnapshot) -> CompositionReplay:
    commander_ids = tuple(sorted(
        _snapshot_oracle_id(snapshot, value) for value in (args.commander, args.partner) if value is not None
    ))
    preferences = []
    for value in args.strategy:
        strategy_id, separator, priority = value.partition("=")
        preferences.append(StrategyPreference(strategy_id, float(priority) if separator else 1.0))
    strategies = tuple(sorted(preferences, key=lambda item: item.strategy_id))
    pool_config = CandidatePoolConfig()
    config = CompositionConfig() if args.land_target is None else CompositionConfig(land_target=args.land_target)
    constraints = PoolConstraints(tuple(args.exclude), tuple(args.lock))
    run_id = artifact_digest((
        snapshot.snapshot_id, commander_ids, [(item.strategy_id, item.weight) for item in strategies],
        PARTNER_RULESET_VERSION, pool_config.version,
    ))
    context = DeckBuildingContext(
        run_id, commander_ids, snapshot.snapshot_id, PARTNER_RULESET_VERSION,
        ENGINE_VERSIONS, pool_config.version, strategies,
    )
    return CompositionReplay(context, constraints, config, pool_config)


def _write_json(args: argparse.Namespace, data: dict[str, object]) -> None:
    payload = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False)
    if args.output is None:
        print(payload)
    else:
        with args.output.open("w" if args.overwrite else "x", encoding="utf-8", newline="\n") as output:
            output.write(payload + "\n")


def _compose(args: argparse.Namespace, snapshot: CollectionSnapshot) -> int:
    replay = _composition_inputs(args, snapshot)
    gate = EligibilityGate(replay.context, snapshot)
    catalog = load_synergy_context()
    artifacts = replay.build(gate, catalog)
    if isinstance(artifacts, DeckProposal):
        result = artifacts
    else:
        result = PreliminaryDeckComposer(replay.composition_config, synergy_context=catalog).compose(gate, *artifacts)
    payload = result.to_json()
    if result.proposal_status == "valid":
        payload["replay"] = replay.manifest(result)
    _write_json(args, payload)
    return 0 if result.proposal_status == "valid" else 1


def _run_refinement(
    args: argparse.Namespace, snapshot: CollectionSnapshot,
) -> tuple[RefinementResult, CompositionReplay]:
    if (args.proposal is None) == (args.commander is None):
        raise ReplayError("conflicting_refine_inputs", "Provide either a commander or --proposal.")
    if args.proposal is not None:
        if args.partner is not None or args.strategy or args.land_target is not None:
            raise ReplayError("conflicting_refine_inputs", "The saved proposal supplies the partner, strategy, and land target.")
        proposal, replay = read_saved_proposal(args.proposal)
        if replay.context.collection_snapshot_id != snapshot.snapshot_id:
            raise ReplayError("stale_collection_snapshot", "The collection snapshot changed. Compose a new proposal.")
        extra_constraints = PoolConstraints(tuple(args.exclude), tuple(args.lock))
    else:
        proposal = None
        replay = _composition_inputs(args, snapshot)
        extra_constraints = PoolConstraints()

    search = RefinementConfig(args.max_swaps, args.attempts_per_iteration, args.max_attempts, args.min_improvement)
    catalog = load_synergy_context()
    gate = EligibilityGate(replay.context, snapshot)
    artifacts = replay.build(gate, catalog)
    if isinstance(artifacts, DeckProposal):
        return RefinementResult.failure(*artifacts.validation.violations), replay
    engine = DeckRefinementEngine(
        search, composition_config=replay.composition_config, constraints=extra_constraints,
        preferred_card_ids=tuple(args.prefer), synergy_context=catalog,
    )
    if proposal is None:
        proposal = engine.composer.compose(gate, *artifacts)
        if proposal.validation.violations:
            return RefinementResult.failure(*proposal.validation.violations), replay
    return engine.refine(gate, proposal, *artifacts), replay


def _refine(args: argparse.Namespace, snapshot: CollectionSnapshot) -> int:
    try:
        result, replay = _run_refinement(args, snapshot)
    except ReplayError as error:
        result, replay = RefinementResult.failure(error.issue), None
    except ValueError as error:
        result, replay = RefinementResult.failure(ValidationIssue("invalid_refinement_inputs", str(error))), None

    payload = result.to_json()
    if replay is not None and result.proposal is not None:
        for state, snapshot in zip(payload["snapshots"], result.snapshots, strict=True):
            state["proposal"]["replay"] = replay.manifest(snapshot.proposal)
        payload["proposal"] = payload["snapshots"][-1]["proposal"]
    _write_json(args, payload)
    return 1 if result.status == "invalid_input" else 0


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

        if args.command == "evaluate-candidates":
            strategies = []
            for value in args.strategy:
                strategy_id, separator, priority = value.partition("=")
                strategies.append(SelectedStrategy(strategy_id, float(priority) if separator else 1.0))
            session_factory = create_session_factory(engine)
            with session_factory() as session:
                result = rank_collection_candidates(
                    session, args.commander, tuple(strategies), limit=args.limit,
                )
            print(json.dumps(result.to_json(), indent=2, allow_nan=False))
            return 0

        if args.command in {"compose", "refine"}:
            session_factory = create_session_factory(engine)
            with session_factory() as session:
                snapshot = load_collection_snapshot(session)
            return _compose(args, snapshot) if args.command == "compose" else _refine(args, snapshot)

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
