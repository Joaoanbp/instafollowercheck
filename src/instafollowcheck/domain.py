"""Comparações e janela de dias, sem consultas por contato ao Instagram."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import DEFAULT_WINDOW_DAYS
from .db import latest_snapshot


@dataclass(frozen=True)
class Person:
    key: str
    username: str
    seen_at: str | None = None

    def as_dict(self) -> dict:
        return {"key": self.key, "username": self.username, "seen_at": self.seen_at}


@dataclass
class Diff:
    account: dict
    latest: dict | None
    followers: list[Person] = field(default_factory=list)
    following: list[Person] = field(default_factory=list)
    mutual: list[Person] = field(default_factory=list)
    not_following_back: list[Person] = field(default_factory=list)
    not_followed_back: list[Person] = field(default_factory=list)
    lost_followers: list[Person] = field(default_factory=list)
    new_followers: list[Person] = field(default_factory=list)
    lost_following: list[Person] = field(default_factory=list)
    new_following: list[Person] = field(default_factory=list)
    possible_soft_blocks: list[Person] = field(default_factory=list)
    window_days: int = DEFAULT_WINDOW_DAYS
    effective_days: float = 0.0
    window_start: str | None = None
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "followers": len(self.followers),
            "following": len(self.following),
            "mutual": len(self.mutual),
            "not_following_back": len(self.not_following_back),
            "not_followed_back": len(self.not_followed_back),
            "lost_followers": len(self.lost_followers),
            "new_followers": len(self.new_followers),
            "lost_following": len(self.lost_following),
            "new_following": len(self.new_following),
            "possible_soft_blocks": len(self.possible_soft_blocks),
        }

    def as_dict(self) -> dict:
        return {
            "account": self.account,
            "latest": self.latest,
            "summary": self.summary(),
            "window_days": self.window_days,
            "effective_days": round(self.effective_days, 1),
            "window_start": self.window_start,
            "warnings": self.warnings,
        }

    def people(self, kind: str) -> list[Person]:
        allowed = {
            "followers": self.followers,
            "following": self.following,
            "mutual": self.mutual,
            "not_following_back": self.not_following_back,
            "not_followed_back": self.not_followed_back,
            "lost_followers": self.lost_followers,
            "new_followers": self.new_followers,
            "lost_following": self.lost_following,
            "new_following": self.new_following,
            "possible_soft_blocks": self.possible_soft_blocks,
        }
        if kind not in allowed:
            raise ValueError("Lista desconhecida.")
        return allowed[kind]


@dataclass
class _Seen:
    username: str
    first_at: str
    last_at: str


def _identities(connection: sqlite3.Connection, owner_key: str) -> dict[str, str]:
    """Une o mesmo @ entre snapshots que usam chaves ``name:`` e ``id:``.

    Exportações oficiais não trazem IDs; o navegador traz. Sem essa união, um
    snapshot de exportação e outro do navegador pareceriam listas totalmente
    diferentes e produziriam unfollows falsos.
    """
    rows = connection.execute(
        """
        SELECT m.username, m.account_key
        FROM members m JOIN snapshots s ON s.id = m.snapshot_id
        WHERE s.owner_key = ?
        ORDER BY s.captured_ts ASC, s.id ASC
        """,
        (owner_key,),
    ).fetchall()
    identities: dict[str, str] = {}
    for row in rows:
        username = str(row["username"]).lower()
        key = str(row["account_key"])
        current = identities.get(username)
        if current is None or (key.startswith("id:") and not current.startswith("id:")):
            identities[username] = key
    return identities


def _canonical_key(key: str, username: str, identities: dict[str, str]) -> str:
    if key.startswith("id:"):
        return key
    return identities.get(username.lower(), key)


def _members(
    connection: sqlite3.Connection,
    owner_key: str,
    snapshot_id: int,
    direction: str,
    identities: dict[str, str],
) -> dict[str, str]:
    rows = connection.execute(
        "SELECT account_key, username FROM members WHERE snapshot_id = ? AND direction = ?",
        (snapshot_id, direction),
    ).fetchall()
    members: dict[str, str] = {}
    for row in rows:
        key = _canonical_key(str(row["account_key"]), row["username"], identities)
        members[key] = row["username"]
    return members


def _aggregate(
    connection: sqlite3.Connection,
    owner_key: str,
    start_ts: int,
    end_ts: int,
    identities: dict[str, str],
) -> dict[str, dict[str, _Seen]]:
    rows = connection.execute(
        """
        SELECT m.direction, m.account_key, m.username, s.captured_at, s.captured_ts
        FROM members m JOIN snapshots s ON s.id = m.snapshot_id
        WHERE s.owner_key = ? AND s.is_complete = 1
          AND s.captured_ts >= ? AND s.captured_ts <= ?
        ORDER BY s.captured_ts ASC, s.id ASC
        """,
        (owner_key, start_ts, end_ts),
    ).fetchall()
    result: dict[str, dict[str, _Seen]] = {"follower": {}, "following": {}}
    for row in rows:
        bucket = result[row["direction"]]
        key = _canonical_key(str(row["account_key"]), row["username"], identities)
        current = bucket.get(key)
        if current is None:
            bucket[key] = _Seen(
                username=row["username"],
                first_at=row["captured_at"],
                last_at=row["captured_at"],
            )
        else:
            current.username = row["username"]
            current.last_at = row["captured_at"]
    return result


def _people(mapping: dict[str, _Seen], *, first: bool) -> list[Person]:
    people = [
        Person(key=key, username=item.username, seen_at=item.first_at if first else item.last_at)
        for key, item in mapping.items()
    ]
    return sorted(people, key=lambda person: (person.username.lower(), person.key))


def _account_dict(row) -> dict:
    return {
        "owner_key": row["owner_key"],
        "owner_id": row["owner_id"],
        "username": row["username"],
    }


def build_diff(
    connection: sqlite3.Connection,
    owner_key: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> Diff:
    account = connection.execute(
        "SELECT owner_key, owner_id, username FROM accounts WHERE owner_key = ?", (owner_key,)
    ).fetchone()
    if account is None:
        raise ValueError("Conta não encontrada no histórico.")
    latest = latest_snapshot(connection, owner_key)
    diff = Diff(account=_account_dict(account), latest=None, window_days=window_days)
    if latest is None:
        diff.warnings.append("Ainda não há nenhuma coleta completa para esta conta.")
        return diff

    diff.latest = {
        "id": latest["id"],
        "captured_at": latest["captured_at"],
        "source": latest["source"],
        "followers_count": latest["followers_count"],
        "following_count": latest["following_count"],
        "reported_followers": latest["reported_followers"],
        "reported_following": latest["reported_following"],
        "note": latest["note"],
    }
    latest_ts = int(latest["captured_ts"])
    now_ts = int((now or datetime.now(timezone.utc)).timestamp())
    cutoff = now_ts - max(1, window_days) * 86400

    baseline = connection.execute(
        """
        SELECT * FROM snapshots
        WHERE owner_key = ? AND is_complete = 1 AND captured_ts >= ? AND id != ?
        ORDER BY captured_ts ASC, id ASC
        LIMIT 1
        """,
        (owner_key, cutoff, latest["id"]),
    ).fetchone()
    if baseline is None:
        baseline = connection.execute(
            """
            SELECT * FROM snapshots
            WHERE owner_key = ? AND is_complete = 1 AND captured_ts < ?
            ORDER BY captured_ts DESC, id DESC
            LIMIT 1
            """,
            (owner_key, cutoff),
        ).fetchone()
    if baseline is None:
        baseline = latest
    if baseline is None:
        diff.warnings.append("Não há linha de base para comparação.")
        return diff

    baseline_ts = int(baseline["captured_ts"])
    diff.window_start = baseline["captured_at"]
    diff.effective_days = max(0.0, (latest_ts - baseline_ts) / 86400)

    identities = _identities(connection, owner_key)
    latest_followers = _members(connection, owner_key, latest["id"], "follower", identities)
    latest_following = _members(connection, owner_key, latest["id"], "following", identities)
    baseline_followers = _members(connection, owner_key, baseline["id"], "follower", identities)
    baseline_following = _members(connection, owner_key, baseline["id"], "following", identities)

    diff.followers = [
        Person(key=key, username=name)
        for key, name in sorted(latest_followers.items(), key=lambda item: item[1].lower())
    ]
    diff.following = [
        Person(key=key, username=name)
        for key, name in sorted(latest_following.items(), key=lambda item: item[1].lower())
    ]

    follower_keys = set(latest_followers)
    following_keys = set(latest_following)
    common = follower_keys & following_keys
    diff.mutual = [Person(key=key, username=latest_followers[key]) for key in common]
    diff.not_following_back = [
        Person(key=key, username=latest_following[key]) for key in following_keys - follower_keys
    ]
    diff.not_followed_back = [
        Person(key=key, username=latest_followers[key]) for key in follower_keys - following_keys
    ]

    window = _aggregate(connection, owner_key, baseline_ts, latest_ts, identities)
    lost_followers = {
        key: value for key, value in window["follower"].items() if key not in latest_followers
    }
    lost_following = {
        key: value for key, value in window["following"].items() if key not in latest_following
    }
    new_followers = {
        key: window["follower"][key]
        for key in latest_followers
        if key in window["follower"] and key not in baseline_followers
    }
    new_following = {
        key: window["following"][key]
        for key in latest_following
        if key in window["following"] and key not in baseline_following
    }

    diff.lost_followers = _people(lost_followers, first=False)
    diff.new_followers = _people(new_followers, first=True)
    diff.lost_following = _people(lost_following, first=False)
    diff.new_following = _people(new_following, first=True)
    common_lost = set(lost_followers) & set(lost_following)
    diff.possible_soft_blocks = _people(
        {key: lost_followers[key] for key in common_lost}, first=False
    )

    for group in (
        diff.mutual,
        diff.not_following_back,
        diff.not_followed_back,
    ):
        group.sort(key=lambda person: (person.username.lower(), person.key))

    if latest["id"] == baseline["id"]:
        diff.warnings.append(
            "Primeira coleta: ainda não há comparação. Os deltas aparecem na próxima coleta."
        )
    elif baseline_ts > cutoff:
        diff.warnings.append(
            f"O histórico começa em {baseline['captured_at']}; a janela efetiva é de "
            f"{diff.effective_days:.1f} dias, não de {window_days}."
        )
    elif baseline_ts < cutoff:
        diff.warnings.append(
            f"Não havia coleta dentro dos últimos {window_days} dias; a comparação usa "
            f"{baseline['captured_at']} como referência ({diff.effective_days:.1f} dias)."
        )
    if latest_ts < now_ts - 86400:
        days = (now_ts - latest_ts) / 86400
        diff.warnings.append(
            f"A última coleta foi há {days:.1f} dias; estes dados não são de agora."
        )
    if latest["note"]:
        diff.warnings.append(latest["note"])
    if latest["reported_followers"] is not None and (
        latest["reported_followers"] != latest["followers_count"]
    ):
        diff.warnings.append(
            "A contagem informada pelo Instagram difere da lista coletada; "
            "confira a observação da coleta."
        )
    return diff
