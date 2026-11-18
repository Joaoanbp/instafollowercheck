"""Caminhos privados e pequenas utilidades compartilhadas."""

import os
import re
import sys
from pathlib import Path

APP_NAME = "instafollowcheck"
DEFAULT_DATA_DIR = Path.home() / ".local/share/instafollowcheck"
DEFAULT_WINDOW_DAYS = 10
DEFAULT_COOLDOWN_MINUTES = 10
MAX_UPLOAD_BYTES = 80 * 1024 * 1024
USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")


def normalize_username(value: str) -> str:
    """Aceita @usuario ou usuario e devolve a forma canônica."""
    text = str(value or "").strip().removeprefix("@").lower()
    if not USERNAME_RE.fullmatch(text):
        raise ValueError("Informe um usuário do Instagram válido, sem URL e sem @.")
    return text


def data_directory(value: str | Path | None = None) -> Path:
    raw = value or os.environ.get("INSTAFOLLOWCHECK_DATA_DIR") or DEFAULT_DATA_DIR
    return Path(raw).expanduser().absolute()


def ensure_private_dir(path: Path) -> Path:
    """Cria ou valida um diretório que só o usuário atual pode acessar."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise RuntimeError("O diretório de dados deve pertencer ao seu usuário e não ser symlink.")
    path.chmod(0o700)
    return path


def ensure_subdir(root: Path, name: str) -> Path:
    return ensure_private_dir(root / name)


def browser_profile_directory(root: Path, username: str) -> Path:
    safe = re.sub(r"[^a-z0-9._-]+", "_", normalize_username(username))
    return ensure_private_dir(root / "browser" / safe)


def find_free_port(preferred: int = 8765) -> int:
    import socket

    for candidate in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError("Não encontrei uma porta livre entre 8765 e 8804.")


def supported_python() -> bool:
    return sys.version_info >= (3, 10)
