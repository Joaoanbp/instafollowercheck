#!/usr/bin/env bash
# Inicia o InstaFollowCheck no terminal normal do Ubuntu. Nunca use sudo.
set -euo pipefail
umask 077

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
venv_python="$repo_dir/.venv/bin/python"

if [[ "${EUID}" -eq 0 ]]; then
    echo 'Execute como seu usuário normal, sem sudo: bash iniciar.sh' >&2
    exit 1
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Uso: bash iniciar.sh [comando] [opções]

Sem comando, abre o painel local no navegador.
Comandos disponíveis:
  serve [--port N] [--no-browser]   inicia o painel
  collect --username USUARIO        faz uma coleta manual
  import-export --username USUARIO --file CAMINHO
  accounts                          lista os históricos locais

Exemplos:
  bash iniciar.sh
  bash iniciar.sh serve --no-browser
  bash iniciar.sh import-export --username seu.usuario --file ~/Downloads/instagram.zip
EOF
    exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo 'Python 3 não encontrado. Instale com: sudo apt install python3 python3-venv python3-pip' >&2
    exit 1
fi

if [[ ! -x "$venv_python" ]]; then
    echo 'Criando o ambiente Python do projeto...'
    if ! python3 -m venv "$repo_dir/.venv"; then
        echo 'Não foi possível criar o ambiente. Instale o suporte com:' >&2
        echo '  sudo apt install python3-venv python3-pip' >&2
        exit 1
    fi
fi

if ! "$venv_python" -c 'import flask, playwright, waitress, instafollowcheck' >/dev/null 2>&1; then
    echo 'Instalando as dependências locais (Flask, Playwright e Waitress)...'
    if ! "$venv_python" -m pip install --disable-pip-version-check -e "$repo_dir"; then
        echo 'A instalação falhou. Confira a conexão e tente novamente.' >&2
        exit 1
    fi
fi

browser_found=0
for name in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$name" >/dev/null 2>&1; then
        browser_found=1
        break
    fi
done
if [[ "$browser_found" -eq 0 ]]; then
    if ! "$venv_python" - <<'PY' >/dev/null 2>&1
from pathlib import Path
from playwright.sync_api import sync_playwright

playwright = sync_playwright().start()
try:
    executable = Path(playwright.chromium.executable_path)
finally:
    playwright.stop()
raise SystemExit(0 if executable.is_file() else 1)
PY
    then
        echo 'Chrome/Chromium não encontrado. Baixando o Chromium do Playwright...'
        if ! "$venv_python" -m playwright install chromium; then
            echo 'Não foi possível baixar o navegador. Instale o Google Chrome e tente novamente.' >&2
            exit 1
        fi
    fi
fi

echo 'Iniciando o InstaFollowCheck...'
exec "$venv_python" -m instafollowcheck "$@"
