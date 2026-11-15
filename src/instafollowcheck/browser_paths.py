"""Localiza arquivos de cookies por metadados; não lê cookies nem acessa a rede."""

import argparse
import os
import re
import sys
from pathlib import Path

from .models import MonitorError


def resolve_cookie_file(browser: str, location: Path | None = None) -> Path:
    if location is not None:
        raw = str(location)
        windows_path = re.match(r"^[A-Za-z]:[\\/]", raw) or re.match(
            r"^/mnt/[a-z]/Users/[^/]+/AppData/", raw, re.IGNORECASE
        )
        if sys.platform.startswith("linux") and windows_path:
            raise MonitorError(
                "Esse perfil é do Chrome/Chromium do Windows; o monitor está no Linux/WSL. "
                "Converter o caminho para /mnt/c não resolve a criptografia dos cookies. "
                "Use uma sessão de navegador Linux ou importe cookies.sqlite do Firefox "
                "do Windows com: bash iniciar.sh USUARIO firefox /mnt/c/.../cookies.sqlite. "
                "Não desative as proteções do Chrome."
            )
        location = location.expanduser()
        if location.is_file():
            return location
        if location.is_dir():
            # O terceiro argumento também aceita o Profile Path de chrome://version.
            for relative in ("Network/Cookies", "Cookies"):
                candidate = location / relative
                if candidate.is_file():
                    return candidate
        raise MonitorError(
            "Não encontrei Cookies ou Network/Cookies no caminho informado. "
            "No Chrome, abra chrome://version e copie o campo Caminho do perfil (Profile Path)."
        )

    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    chrome_config = Path(os.environ.get("CHROME_CONFIG_HOME", config))
    if browser == "chrome":
        roots = [chrome_config / name for name in (
            "google-chrome", "google-chrome-beta", "google-chrome-unstable",
            "google-chrome-canary", "google-chrome-for-testing",
        )]
        roots.append(home / ".var/app/com.google.Chrome/config/google-chrome")
    elif browser == "chromium":
        roots = [
            chrome_config / "chromium",
            home / "snap/chromium/common/chromium",
            home / ".var/app/org.chromium.Chromium/config/chromium",
        ]
    else:
        raise MonitorError("A localização automática suporta Chrome e Chromium.")
    if os.environ.get("CHROME_USER_DATA_DIR"):
        roots.insert(0, Path(os.environ["CHROME_USER_DATA_DIR"]).expanduser())
    candidates = set()
    for root in roots:
        if root.is_dir():
            for profile in root.iterdir():
                if profile.is_dir():
                    # Prefira o local moderno quando os dois arquivos coexistirem.
                    for relative in ("Network/Cookies", "Cookies"):
                        candidate = profile / relative
                        if candidate.is_file():
                            candidates.add(candidate.resolve())
                            break
    if len(candidates) == 1:
        return candidates.pop()
    if candidates:
        paths = "\n".join(f"  {path.parent}" for path in sorted(candidates))
        raise MonitorError(
            "Há vários perfis; não vou escolher uma conta automaticamente.\n"
            f"Arquivos encontrados nos diretórios:\n{paths}\n"
            "No Chrome com sua conta aberta, copie o Caminho do perfil em chrome://version."
        )
    raise MonitorError(
        "Não encontrei o arquivo de cookies nos diretórios conhecidos. "
        "No Chrome, abra chrome://version e copie o Caminho do perfil (Profile Path)."
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("browser", choices=("chrome", "chromium"))
    parser.add_argument("location", nargs="?", type=Path)
    args = parser.parse_args(argv)
    try:
        print(resolve_cookie_file(args.browser, args.location))
        return 0
    except (MonitorError, OSError) as exc:
        message = str(exc) if isinstance(exc, MonitorError) else "Sem acesso ao perfil local."
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
