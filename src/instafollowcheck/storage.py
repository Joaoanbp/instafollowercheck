"""SQLite guarda snapshots e o texto exato do relatório na mesma transação."""

import fcntl
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .models import MonitorError, Snapshot


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise MonitorError("Diretório de dados deve pertencer ao usuário e não ser symlink.")
    path.chmod(0o700)


@contextmanager
def execution_lock(directory: Path):
    private_directory(directory)
    with (directory / "monitor.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MonitorError("Já existe uma execução usando este diretório de dados.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def database(path: Path):
    connection = sqlite3.connect(path)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY,
                owner_id TEXT NOT NULL,
                username TEXT NOT NULL,
                payload TEXT NOT NULL,
                report TEXT NOT NULL
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS owner_history ON snapshots(owner_id, id)"
        )
        connection.commit()
        path.chmod(0o600)
        yield connection
    finally:
        connection.close()


def latest(connection, *, owner_id: str | None = None, username: str | None = None):
    if owner_id is not None:
        row = connection.execute(
            "SELECT payload, report, id FROM snapshots WHERE owner_id=? ORDER BY id DESC LIMIT 1",
            (owner_id,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT payload, report, id FROM snapshots WHERE username=? ORDER BY id DESC LIMIT 1",
            (username,),
        ).fetchone()
    return (Snapshot(**json.loads(row[0])), row[1], row[2]) if row else None


def save(connection, snapshot: Snapshot, report: str) -> int:
    with connection:
        cursor = connection.execute(
            "INSERT INTO snapshots(owner_id, username, payload, report) VALUES (?, ?, ?, ?)",
            (snapshot.owner_id, snapshot.username, json.dumps(asdict(snapshot)), report),
        )
    return cursor.lastrowid


def export_report(directory: Path, snapshot: Snapshot, report: str, row_id: int) -> Path:
    private_directory(directory)
    day = snapshot.captured_at[:10]
    target = directory / f"relatorio_{snapshot.username}_{day}_{row_id}.txt"
    # replace() evita que o leitor encontre um relatório parcialmente gravado.
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(report)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target
