"""SQLite local: contas, snapshots completos e membros de cada lista."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import (
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_WINDOW_DAYS,
    ensure_private_dir,
    normalize_username,
)

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    owner_key TEXT PRIMARY KEY,
    owner_id TEXT,
    username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_source TEXT
);
CREATE INDEX IF NOT EXISTS accounts_username ON accounts(username);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    owner_key TEXT NOT NULL REFERENCES accounts(owner_key) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    captured_ts INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('browser', 'export', 'legacy')),
    followers_count INTEGER NOT NULL,
    following_count INTEGER NOT NULL,
    reported_followers INTEGER,
    reported_following INTEGER,
    is_complete INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS snapshots_owner ON snapshots(owner_key, captured_ts);

CREATE TABLE IF NOT EXISTS members (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('follower', 'following')),
    account_key TEXT NOT NULL,
    user_id TEXT,
    username TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, direction, account_key)
);
CREATE INDEX IF NOT EXISTS members_account ON members(account_key, direction);
CREATE INDEX IF NOT EXISTS members_username ON members(username, direction);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def timestamp(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return int(parsed.timestamp())


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _member_key(value: object) -> str:
    """O banco antigo usava IDs numéricos como chave dos contatos."""
    text = str(value)
    if text.isdecimal():
        return f"id:{text}"
    return text


@contextmanager
def database(directory: Path):
    directory = ensure_private_dir(directory)
    path = directory / "history.sqlite3"
    connection = sqlite3.connect(path, timeout=20)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        _migrate_legacy(connection)
        connection.executescript(SCHEMA)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
        _harden(path)
        _seed_settings(connection)
        yield connection
    finally:
        connection.close()


def _harden(path: Path) -> None:
    try:
        path.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            extra = Path(str(path) + suffix)
            if extra.exists():
                extra.chmod(0o600)
    except OSError:
        pass


def _seed_settings(connection: sqlite3.Connection) -> None:
    defaults = {
        "window_days": str(DEFAULT_WINDOW_DAYS),
        "cooldown_minutes": str(DEFAULT_COOLDOWN_MINUTES),
    }
    connection.executemany(
        "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", defaults.items()
    )
    connection.commit()


def _migrate_legacy(connection: sqlite3.Connection) -> None:
    """Converte o banco antigo (payload JSON) sem apagar o histórico anterior."""
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='snapshots'"
    ).fetchone()
    if row is None:
        return
    columns = {item[1] for item in connection.execute("PRAGMA table_info(snapshots)")}
    if "payload" not in columns:
        return
    connection.execute("ALTER TABLE snapshots RENAME TO snapshots_legacy")
    connection.commit()
    connection.executescript(SCHEMA)
    rows = connection.execute(
        "SELECT owner_id, username, payload FROM snapshots_legacy ORDER BY id"
    ).fetchall()
    for legacy in rows:
        try:
            payload = json.loads(legacy["payload"])
        except (TypeError, ValueError):
            continue
        owner_id = str(legacy["owner_id"] or payload.get("owner_id") or "")
        username = normalize_username(legacy["username"] or payload.get("username") or "")
        owner_key = f"id:{owner_id}" if owner_id.isdecimal() else f"name:{username}"
        followers = {
            _member_key(key): normalize_username(name)
            for key, name in (payload.get("followers") or {}).items()
        }
        following = {
            _member_key(key): normalize_username(name)
            for key, name in (payload.get("following") or {}).items()
        }
        save_snapshot(
            connection,
            owner_key=owner_key,
            owner_id=owner_id if owner_id.isdecimal() else None,
            username=username,
            captured_at=str(payload.get("captured_at") or now_iso()),
            source="legacy",
            followers=followers,
            following=following,
            note="Migrado do formato antigo do InstaFollowCheck.",
        )
    connection.execute("PRAGMA user_version = 2")
    connection.commit()


def find_account(connection: sqlite3.Connection, username: str):
    normalized = normalize_username(username)
    return connection.execute(
        """
        SELECT * FROM accounts
        WHERE lower(username) = ?
        ORDER BY CASE WHEN owner_key LIKE 'id:%' THEN 0 ELSE 1 END, created_at DESC
        LIMIT 1
        """,
        (normalized,),
    ).fetchone()


def find_account_by_owner(connection: sqlite3.Connection, owner_key: str):
    return connection.execute("SELECT * FROM accounts WHERE owner_key = ?", (owner_key,)).fetchone()


def list_accounts(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT a.owner_key, a.owner_id, a.username, a.last_source,
               s.captured_at, s.followers_count, s.following_count
        FROM accounts a
        LEFT JOIN snapshots s ON s.id = (
            SELECT id FROM snapshots
            WHERE owner_key = a.owner_key
            ORDER BY captured_ts DESC, id DESC
            LIMIT 1
        )
        ORDER BY lower(a.username)
        """
    ).fetchall()
    return [dict(row) for row in rows]


def latest_snapshot(connection: sqlite3.Connection, owner_key: str):
    return connection.execute(
        """
        SELECT * FROM snapshots
        WHERE owner_key = ? AND is_complete = 1
        ORDER BY captured_ts DESC, id DESC
        LIMIT 1
        """,
        (owner_key,),
    ).fetchone()


def history(connection: sqlite3.Connection, owner_key: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT id, captured_at, captured_ts, source, followers_count, following_count,
               reported_followers, reported_following, note
        FROM snapshots
        WHERE owner_key = ?
        ORDER BY captured_ts DESC, id DESC
        """,
        (owner_key,),
    ).fetchall()
    return [dict(row) for row in rows]


def snapshot_members(connection: sqlite3.Connection, snapshot_id: int, direction: str):
    rows = connection.execute(
        "SELECT account_key, username FROM members WHERE snapshot_id = ? AND direction = ?",
        (snapshot_id, direction),
    ).fetchall()
    return {row["account_key"]: row["username"] for row in rows}


def resolve_member_key(
    connection: sqlite3.Connection, owner_key: str, username: str, user_id: str | None
) -> str:
    if user_id:
        return f"id:{user_id}"
    known = connection.execute(
        """
        SELECT m.account_key
        FROM members m JOIN snapshots s ON s.id = m.snapshot_id
        WHERE s.owner_key = ? AND lower(m.username) = ? AND m.account_key LIKE 'id:%'
        ORDER BY s.captured_ts DESC, s.id DESC
        LIMIT 1
        """,
        (owner_key, normalize_username(username)),
    ).fetchone()
    if known:
        return known["account_key"]
    return f"name:{normalize_username(username)}"


def save_snapshot(
    connection: sqlite3.Connection,
    *,
    owner_key: str,
    owner_id: str | None,
    username: str,
    captured_at: str,
    source: str,
    followers: dict[str, str],
    following: dict[str, str],
    reported_followers: int | None = None,
    reported_following: int | None = None,
    note: str = "",
) -> int:
    username = normalize_username(username)
    if source not in {"browser", "export", "legacy"}:
        raise ValueError("Origem de snapshot inválida.")
    with connection:
        connection.execute(
            """
            INSERT INTO accounts(owner_key, owner_id, username, created_at, last_source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_key) DO UPDATE SET
                username = excluded.username,
                owner_id = COALESCE(accounts.owner_id, excluded.owner_id),
                last_source = excluded.last_source
            """,
            (owner_key, owner_id, username, now_iso(), source),
        )
        if owner_key.startswith("id:"):
            # Uma exportação importada antes do navegador cria uma conta "name:".
            # Quando o ID real aparece, o histórico antigo passa a pertencer à
            # mesma conta, sem duplicar snapshots ou inventar unfollows.
            legacy_accounts = connection.execute(
                """
                SELECT owner_key FROM accounts
                WHERE lower(username) = ? AND owner_key LIKE 'name:%'
                """,
                (username,),
            ).fetchall()
            for legacy in legacy_accounts:
                connection.execute(
                    "UPDATE snapshots SET owner_key = ? WHERE owner_key = ?",
                    (owner_key, legacy["owner_key"]),
                )
                connection.execute(
                    "DELETE FROM accounts WHERE owner_key = ?", (legacy["owner_key"],)
                )
        captured_ts = timestamp(captured_at)
        cursor = connection.execute(
            """
            INSERT INTO snapshots(
                owner_key, captured_at, captured_ts, source, followers_count,
                following_count, reported_followers, reported_following, is_complete, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                owner_key,
                captured_at,
                captured_ts,
                source,
                len(followers),
                len(following),
                reported_followers,
                reported_following,
                note,
            ),
        )
        snapshot_id = int(cursor.lastrowid)
        rows = []
        for direction, members in (("follower", followers), ("following", following)):
            resolved: dict[str, str] = {}
            for raw_key, name in members.items():
                user_id = raw_key[3:] if raw_key.startswith("id:") else None
                key = resolve_member_key(connection, owner_key, name, user_id)
                resolved[key] = normalize_username(name)
            for key, name in resolved.items():
                user_id = key[3:] if key.startswith("id:") else None
                rows.append((snapshot_id, direction, key, user_id, name))
        connection.executemany(
            """
            INSERT OR REPLACE INTO members(
                snapshot_id, direction, account_key, user_id, username
            ) VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
    return snapshot_id


def delete_account(connection: sqlite3.Connection, owner_key: str) -> None:
    with connection:
        connection.execute("DELETE FROM accounts WHERE owner_key = ?", (owner_key,))


def setting(connection: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    with connection:
        connection.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def settings(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "window_days": int(setting(connection, "window_days", str(DEFAULT_WINDOW_DAYS)) or 10),
        "cooldown_minutes": int(
            setting(connection, "cooldown_minutes", str(DEFAULT_COOLDOWN_MINUTES)) or 10
        ),
    }


def account_for_export(
    connection: sqlite3.Connection, username: str, owner_id: str | None = None
) -> tuple[str, str | None]:
    """Resolve a conta da exportação para o mesmo histórico da conta do navegador."""
    normalized = normalize_username(username)
    if owner_id and str(owner_id).isdecimal():
        owner_key = f"id:{owner_id}"
        existing = find_account_by_owner(connection, owner_key)
        if existing and normalize_username(existing["username"]) != normalized:
            raise ValueError("O ID informado pertence a outra conta neste histórico.")
        return owner_key, str(owner_id)
    existing = find_account(connection, normalized)
    if existing:
        return existing["owner_key"], existing["owner_id"]
    return f"name:{normalized}", None


def insert_export_snapshot(
    connection: sqlite3.Connection,
    *,
    username: str,
    followers: dict[str, str],
    following: dict[str, str],
    owner_id: str | None = None,
    captured_at: str | None = None,
) -> tuple[int, str]:
    owner_key, resolved_owner_id = account_for_export(connection, username, owner_id)
    snapshot_id = save_snapshot(
        connection,
        owner_key=owner_key,
        owner_id=resolved_owner_id,
        username=username,
        captured_at=captured_at or now_iso(),
        source="export",
        followers=followers,
        following=following,
        note="Importado da exportação oficial do Instagram; use como referência.",
    )
    return snapshot_id, owner_key


def iter_members(connection: sqlite3.Connection, snapshot_id: int) -> Iterable[sqlite3.Row]:
    return connection.execute(
        "SELECT direction, account_key, username FROM members WHERE snapshot_id = ?",
        (snapshot_id,),
    )
