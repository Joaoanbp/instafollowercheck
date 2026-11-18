"""Entrada de terminal: servidor local, coleta manual e importação."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .app import run_server
from .collector import CollectorError, collect_account
from .config import data_directory, normalize_username
from .db import database, insert_export_snapshot, list_accounts, save_snapshot
from .domain import build_diff
from .importer import ExportError, import_export

SUMMARY_LABELS = {
    "followers": "Seguidores",
    "following": "Seguindo",
    "mutual": "Mútuos",
    "not_following_back": "Não te seguem de volta",
    "not_followed_back": "Você não segue de volta",
    "lost_followers": "Deixaram de seguir",
    "new_followers": "Novos seguidores",
    "lost_following": "Você deixou de seguir",
    "new_following": "Novos seguindo",
    "possible_soft_blocks": "Possíveis soft blocks",
}


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Diretório privado de dados (padrão: ~/.local/share/instafollowcheck)",
    )
    return parser


def _parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="instafollowcheck",
        description="Painel local de seguidores do Instagram, sem extensões de terceiros.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Diretório privado de dados (padrão: ~/.local/share/instafollowcheck)",
    )
    subcommands = parser.add_subparsers(dest="command", title="comandos")
    subcommands.add_parser(
        "serve",
        parents=[common],
        help="Abre o servidor local e o painel no navegador (padrão)",
    )
    serve = subcommands.choices["serve"]
    serve.add_argument("--port", type=int, help="Porta local; por padrão, procura uma livre")
    serve.add_argument(
        "--no-browser",
        action="store_true",
        help="Não abre o navegador automaticamente; exiba o link do terminal",
    )

    collect = subcommands.add_parser(
        "collect", parents=[common], help="Coleta manualmente pelo Chrome e salva um snapshot"
    )
    collect.add_argument("--username", required=True, help="Usuário do Instagram, sem @")
    collect.add_argument(
        "--headless",
        action="store_true",
        help="Não mostra a janela; use apenas se o perfil já estiver autenticado",
    )

    importer = subcommands.add_parser(
        "import-export",
        parents=[common],
        help="Importa a exportação oficial (ZIP, pasta ou dois JSON)",
    )
    importer.add_argument("--username", required=True, help="Usuário da conta exportada")
    importer.add_argument("--file", type=Path, required=True, help="ZIP, pasta ou JSON")
    importer.add_argument("--owner-id", help="ID numérico da conta, quando conhecido")

    subcommands.add_parser(
        "accounts", parents=[common], help="Lista as contas e o último snapshot local"
    )
    return parser


def _print_summary(diff) -> None:
    print("\nResumo:")
    for key, label in SUMMARY_LABELS.items():
        print(f"  {label}: {diff.summary()[key]}")
    if diff.window_start:
        print(f"  Janela efetiva: {diff.effective_days:.1f} dias desde {diff.window_start}")
    for warning in diff.warnings:
        print(f"  Aviso: {warning}")


def _collect(args: argparse.Namespace) -> int:
    username = normalize_username(args.username)
    directory = data_directory(args.data_dir)
    last_stage = ""

    def progress(stage: str, message: str, current: int, total: int) -> None:
        nonlocal last_stage
        if stage == last_stage:
            return
        last_stage = stage
        if total:
            print(f"{message} ({current} de {total})", flush=True)
        else:
            print(message, flush=True)

    print(f"Coletando @{username} pelo Chrome. Feche a janela apenas ao terminar.")
    snapshot = collect_account(
        directory,
        username,
        progress=progress,
        headless=args.headless,
    )
    with database(directory) as connection:
        snapshot_id = save_snapshot(
            connection,
            owner_key=f"id:{snapshot.owner_id}",
            owner_id=snapshot.owner_id,
            username=snapshot.username,
            captured_at=snapshot.captured_at,
            source="browser",
            followers=snapshot.followers,
            following=snapshot.following,
            reported_followers=snapshot.reported_followers,
            reported_following=snapshot.reported_following,
            note=snapshot.note,
        )
        diff = build_diff(connection, f"id:{snapshot.owner_id}")
    print(f"Snapshot #{snapshot_id} salvo em {directory}.")
    _print_summary(diff)
    return 0


def _import(args: argparse.Namespace) -> int:
    username = normalize_username(args.username)
    owner_id = args.owner_id.strip() if args.owner_id else None
    if owner_id is not None and not owner_id.isdecimal():
        raise ValueError("O ID da conta deve conter somente números.")
    if owner_id is not None:
        owner_id = str(int(owner_id))
    directory = data_directory(args.data_dir)
    followers, following = import_export(args.file)
    with database(directory) as connection:
        snapshot_id, account_key = insert_export_snapshot(
            connection,
            username=username,
            followers=followers,
            following=following,
            owner_id=owner_id,
        )
        diff = build_diff(connection, account_key)
    print(
        f"Exportação importada para @{username}: "
        f"{len(followers)} seguidores e {len(following)} seguindo."
    )
    print(f"Snapshot #{snapshot_id} salvo em {directory} ({account_key}).")
    _print_summary(diff)
    return 0


def _accounts(args: argparse.Namespace) -> int:
    directory = data_directory(args.data_dir)
    with database(directory) as connection:
        accounts = list_accounts(connection)
    if not accounts:
        print("Nenhuma conta no histórico local.")
        return 0
    for account in accounts:
        captured = account["captured_at"] or "sem coleta completa"
        followers = account["followers_count"]
        following = account["following_count"]
        print(
            f"@{account['username']} ({account['owner_key']}): {captured}; "
            f"{followers if followers is not None else '?'} seguidores; "
            f"{following if following is not None else '?'} seguindo"
        )
    return 0


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command in (None, "serve"):
        try:
            run_server(
                data_dir=args.data_dir,
                port=getattr(args, "port", None),
                open_browser=not getattr(args, "no_browser", False),
            )
        except KeyboardInterrupt:
            return 130
        return 0
    try:
        if args.command == "collect":
            return _collect(args)
        if args.command == "import-export":
            return _import(args)
        if args.command == "accounts":
            return _accounts(args)
    except (CollectorError, ExportError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Execução interrompida.", file=sys.stderr)
        return 130
    parser.error("Comando desconhecido.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
