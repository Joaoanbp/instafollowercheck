"""Entrada de terminal e cron; caminhos padrão independem do diretório atual."""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from .collector import collect_snapshot, username_arg
from .models import MonitorError
from .report import render_report
from .storage import database, execution_lock, export_report, latest, save


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Monitor local de seguidores do Instagram")
    parser.add_argument("--usuario", required=True, type=username_arg)
    parser.add_argument("--session-file", type=Path, help="Sessão Instaloader local e confiável")
    parser.add_argument(
        "--data-dir", type=Path,
        default=Path.home() / ".local/share/instafollowcheck",
    )
    parser.add_argument("--quiet", action="store_true", help="Oculta mensagens de sucesso")
    parser.add_argument(
        "--report-only", action="store_true",
        help="Reexporta o último relatório salvo sem acessar o Instagram",
    )
    args = parser.parse_args(argv)
    os.umask(0o077)
    session = (args.session_file or (
        Path.home() / ".config/instaloader" / f"session-{args.usuario}"
    )).expanduser()
    directory = args.data_dir.expanduser().absolute()
    saved = False
    try:
        with execution_lock(directory), database(directory / "history.sqlite3") as connection:
            if args.report_only:
                stored = latest(connection, username=args.usuario)
                if stored is None:
                    raise MonitorError("Nenhum histórico salvo para esse usuário.")
                current, report, row_id = stored
            else:
                current = collect_snapshot(args.usuario, session)
                stored = latest(connection, owner_id=current.owner_id)
                report = render_report(current, stored[0] if stored else None)
                row_id = save(connection, current, report)
                saved = True
            result = export_report(directory / "reports", current, report, row_id)
        if not args.quiet:
            print(f"Relatório: {result}")
        return 0
    except (
        MonitorError, OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError,
    ) as exc:
        # Mensagens externas podem conter respostas HTTP ou dados privados.
        message = str(exc) if isinstance(exc, MonitorError) else type(exc).__name__
        print(f"Erro: {message}", file=sys.stderr)
        if saved:
            print("Snapshot salvo. Recupere o TXT com --report-only.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Execução interrompida.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
