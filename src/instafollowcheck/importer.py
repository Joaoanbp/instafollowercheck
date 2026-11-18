"""Importação local da exportação oficial do Instagram, inclusive ZIP."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from .config import normalize_username

MAX_FILES = 200
MAX_UNCOMPRESSED = 300 * 1024 * 1024


class ExportError(Exception):
    """Exportação inválida, incompleta ou ilegível."""


def _name_from_entry(entry: dict) -> str:
    data = entry.get("string_list_data")
    item = data[0] if isinstance(data, list) and data else {}
    value = item.get("value") if isinstance(item, dict) else None
    if not value and isinstance(item, dict):
        href = item.get("href")
        if href:
            value = str(href).rstrip("/").rsplit("/", 1)[-1]
    if not value:
        value = entry.get("title") or entry.get("username")
    if not value:
        raise ExportError("A exportação contém uma entrada sem nome de usuário.")
    try:
        return normalize_username(str(value))
    except ValueError as exc:
        raise ExportError("A exportação contém um nome de usuário inválido.") from exc


def _split_entries(data: object) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in (
            "relationships_following",
            "relationships_followers",
            "followers",
            "following",
            "string_list_data",
        ):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if "string_list_data" in data:
            return [data]
    raise ExportError("Formato inesperado na exportação do Instagram.")


def parse_export_json(data: object) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in _split_entries(data):
        name = _name_from_entry(entry)
        result[f"name:{name}"] = name
    return result


def _kind_from_name(name: str) -> str | None:
    lower = Path(name).name.lower()
    if "follower" in lower:
        return "followers"
    if "following" in lower:
        return "following"
    return None


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExportError("Arquivo de exportação inválido ou ilegível.") from exc


def _merge(bucket: dict[str, str], data: object) -> None:
    bucket.update(parse_export_json(data))


def _from_directory(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    followers: dict[str, str] = {}
    following: dict[str, str] = {}
    files = [path for path in root.rglob("*.json") if path.is_file()]
    if not files:
        raise ExportError("Não encontrei arquivos JSON na pasta da exportação.")
    for path in files:
        kind = _kind_from_name(path.name)
        if kind is None:
            continue
        _merge(followers if kind == "followers" else following, _read_json(path))
    return followers, following


def _from_files(paths: list[Path]) -> tuple[dict[str, str], dict[str, str]]:
    followers: dict[str, str] = {}
    following: dict[str, str] = {}
    for path in paths:
        kind = _kind_from_name(path.name)
        if kind is None:
            continue
        _merge(followers if kind == "followers" else following, _read_json(path))
    return followers, following


def _from_zip(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    followers: dict[str, str] = {}
    following: dict[str, str] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > MAX_FILES:
                raise ExportError("O ZIP contém arquivos demais.")
            total = sum(info.file_size for info in infos)
            if total > MAX_UNCOMPRESSED:
                raise ExportError("O ZIP é grande demais para importar com segurança.")
            found = False
            for info in infos:
                kind = _kind_from_name(info.filename)
                if kind is None or not info.filename.lower().endswith(".json"):
                    continue
                if info.file_size > MAX_UNCOMPRESSED:
                    raise ExportError("Um arquivo do ZIP é grande demais.")
                found = True
                try:
                    with archive.open(info) as handle:
                        data = json.loads(handle.read().decode("utf-8"))
                except (KeyError, ValueError, UnicodeDecodeError) as exc:
                    raise ExportError("Arquivo JSON inválido dentro do ZIP.") from exc
                _merge(followers if kind == "followers" else following, data)
            if not found:
                raise ExportError("O ZIP não contém arquivos de seguidores ou seguindo.")
    except zipfile.BadZipFile as exc:
        raise ExportError("O arquivo ZIP está corrompido.") from exc
    return followers, following


def import_export(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    source = path.expanduser()
    if not source.exists():
        raise ExportError("Caminho da exportação não encontrado.")
    if source.is_dir():
        followers, following = _from_directory(source)
    elif source.suffix.lower() == ".zip":
        followers, following = _from_zip(source)
    elif source.suffix.lower() == ".json":
        kind = _kind_from_name(source.name)
        if kind is None:
            raise ExportError("Não foi possível saber se o arquivo é de seguidores ou de seguindo.")
        data = _read_json(source)
        bucket = parse_export_json(data)
        followers, following = (bucket, {}) if kind == "followers" else ({}, bucket)
    else:
        raise ExportError("Use uma pasta, um ZIP ou um arquivo JSON da exportação oficial.")
    return _require_both(followers, following)


def _require_both(
    followers: dict[str, str], following: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    if not followers and not following:
        raise ExportError("A exportação está vazia ou não contém as listas necessárias.")
    if not followers or not following:
        raise ExportError(
            "Exportação incompleta: preciso dos arquivos de seguidores e de seguindo juntos."
        )
    return followers, following


def import_export_bytes(data: bytes, filename: str) -> tuple[dict[str, str], dict[str, str]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ExportError("Arquivo JSON inválido.") from exc
        kind = _kind_from_name(filename)
        if kind is None:
            raise ExportError("Não foi possível saber se o JSON é de seguidores ou seguindo.")
        bucket = parse_export_json(parsed)
        return _require_both(bucket, {}) if kind == "followers" else _require_both({}, bucket)
    if suffix == ".zip":
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ExportError("O arquivo ZIP está corrompido.") from exc
        with archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > MAX_FILES:
                raise ExportError("O ZIP contém arquivos demais.")
            if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED:
                raise ExportError("O ZIP é grande demais para importar com segurança.")
            followers: dict[str, str] = {}
            following: dict[str, str] = {}
            found = False
            for info in infos:
                kind = _kind_from_name(info.filename)
                if kind is None or not info.filename.lower().endswith(".json"):
                    continue
                found = True
                try:
                    parsed = json.loads(archive.read(info).decode("utf-8"))
                except (KeyError, ValueError, UnicodeDecodeError) as exc:
                    raise ExportError("Arquivo JSON inválido dentro do ZIP.") from exc
                _merge(followers if kind == "followers" else following, parsed)
            if not found:
                raise ExportError("O ZIP não contém arquivos de seguidores ou seguindo.")
            return _require_both(followers, following)
    raise ExportError("Use um arquivo ZIP ou JSON da exportação oficial.")


def import_export_uploads(files: list[tuple[str, bytes]]) -> tuple[dict[str, str], dict[str, str]]:
    """Importa o ZIP completo ou os dois JSON selecionados juntos."""
    usable = [(name, data) for name, data in files if data]
    if not usable:
        raise ExportError("Nenhum arquivo foi enviado.")
    if any(len(data) > MAX_UNCOMPRESSED for _, data in usable):
        raise ExportError("Um arquivo enviado é grande demais.")
    if sum(len(data) for _, data in usable) > MAX_UNCOMPRESSED:
        raise ExportError("Os arquivos enviados são grandes demais.")
    if len(usable) == 1:
        return import_export_bytes(usable[0][1], usable[0][0])
    if any(Path(name).suffix.lower() != ".json" for name, _ in usable):
        raise ExportError("Envie um ZIP ou apenas arquivos JSON, sem misturar formatos.")
    followers: dict[str, str] = {}
    following: dict[str, str] = {}
    for name, data in usable:
        kind = _kind_from_name(name)
        if kind is None:
            raise ExportError(
                "Não foi possível saber qual JSON é de seguidores e qual é de seguindo."
            )
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ExportError("Arquivo JSON inválido.") from exc
        _merge(followers if kind == "followers" else following, parsed)
    return _require_both(followers, following)
