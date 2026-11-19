"""Utilitários compartilhados pelos testes offline."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOW = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)


def iso_days_ago(days: float, now: datetime = NOW) -> str:
    return (now - timedelta(days=days)).isoformat(timespec="seconds")


def entries(names: list[str]) -> list[dict]:
    return [
        {
            "title": name,
            "string_list_data": [{"href": f"https://www.instagram.com/{name}", "value": name}],
        }
        for name in names
    ]


def export_json(names: list[str]) -> bytes:
    return json.dumps(entries(names), ensure_ascii=False).encode("utf-8")


def export_zip(followers: list[str], following: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "connections/followers_and_following/followers_1.json",
            export_json(followers),
        )
        archive.writestr(
            "connections/followers_and_following/following.json",
            export_json(following),
        )
    return buffer.getvalue()


def write_export(directory: Path, followers: list[str], following: list[str]) -> None:
    target = directory / "connections" / "followers_and_following"
    target.mkdir(parents=True, exist_ok=True)
    (target / "followers_1.json").write_bytes(export_json(followers))
    (target / "following_1.json").write_bytes(export_json(following))
