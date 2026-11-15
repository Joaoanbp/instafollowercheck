#!/usr/bin/env bash
# Execute no terminal normal do Ubuntu; nunca execute este script com sudo.
set -euo pipefail
umask 077

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
instagram_user="${1:-}"
auth_browser="${2:-}"
cookie_file="${3:-}"

if [[ "$instagram_user" == '--help' ]]; then
    echo 'Uso: bash iniciar.sh [usuario] [librewolf|firefox|chrome|chromium|brave|edge] [arquivo-de-cookies]'
    echo 'Sem argumentos: pergunta o usuário e o navegador no terminal.'
    echo 'Sem navegador: reutiliza uma sessão existente; nunca pede senha do Instagram.'
    echo 'Com navegador: importa a sessão já autenticada; não pede senha do Instagram.'
    exit 0
fi

if [[ "$EUID" -eq 0 ]]; then
    echo 'Execute como seu usuário normal: bash iniciar.sh' >&2
    exit 1
fi
if [[ "$#" -eq 0 && -t 0 ]]; then
    read -r -p 'Usuário do Instagram (sem @): ' instagram_user
    read -r -p 'Navegador [firefox|chrome|edge|librewolf|chromium|brave; Enter para sessão salva]: ' auth_browser
fi
if [[ ! "$instagram_user" =~ ^[a-z0-9._]{1,30}$ ]]; then
    echo 'Informe o usuário sem @, usando letras minúsculas.' >&2
    exit 1
fi
if [[ "$#" -gt 3 ]] || [[ -n "$auth_browser" && ! "$auth_browser" =~ ^(librewolf|firefox|chrome|chromium|brave|edge)$ ]]; then
    echo 'Uso: bash iniciar.sh [usuario] [librewolf|firefox|chrome|chromium|brave|edge] [arquivo-de-cookies]' >&2
    exit 1
fi
if [[ -n "$cookie_file" && ! -e "$cookie_file" && "$auth_browser" != chrome && "$auth_browser" != chromium ]]; then
    echo 'O arquivo ou diretório de perfil informado não existe.' >&2
    exit 1
fi

if ! python3 -c 'import ensurepip' >/dev/null 2>&1 || ! command -v crontab >/dev/null; then
    echo 'Instalando suporte ao ambiente Python e cron (pode pedir a senha do Ubuntu)...'
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-pip cron
fi

echo 'Preparando as dependências do projeto...'
python3 -m venv "$repo_dir/.venv"
"$repo_dir/.venv/bin/python" -m pip install -e "$repo_dir"

session_dir="$HOME/.config/instaloader"
session_file="$session_dir/session-$instagram_user"
mkdir -p -- "$session_dir"
chmod 700 -- "$session_dir"
if [[ -n "$auth_browser" ]]; then
    if [[ "$auth_browser" == chrome || "$auth_browser" == chromium ]]; then
        path_options=()
        if [[ -n "$cookie_file" ]]; then
            path_options=("$cookie_file")
        fi
        if ! resolved_cookie_file="$("$repo_dir/.venv/bin/python" -m instafollowcheck.browser_paths \
            "$auth_browser" "${path_options[@]}")"; then
            if [[ -z "$cookie_file" && -t 0 ]]; then
                echo 'Cole somente o Caminho do perfil de chrome://version (sem aspas).'
                read -r -p 'Caminho do perfil: ' cookie_file
                if [[ -z "$cookie_file" ]]; then
                    echo 'Nenhuma autenticação foi tentada; o cron não foi alterado.' >&2
                    exit 1
                fi
                resolved_cookie_file="$("$repo_dir/.venv/bin/python" \
                    -m instafollowcheck.browser_paths "$auth_browser" "$cookie_file")"
            else
                echo 'Informe esse caminho como terceiro argumento do iniciador.' >&2
                echo 'Nenhuma autenticação foi tentada; o cron não foi alterado.' >&2
                exit 1
            fi
        fi
        cookie_file="$resolved_cookie_file"
    fi
    "$repo_dir/.venv/bin/python" -m pip install browser-cookie3
    echo "Importando a sessão de $instagram_user já aberta no $auth_browser..."
    cookie_options=()
    if [[ -n "$cookie_file" ]]; then
        cookie_options=(--cookiefile "$cookie_file")
    fi
    if ! "$repo_dir/.venv/bin/instaloader" --load-cookies "$auth_browser" \
        --sessionfile "$session_file" "${cookie_options[@]}"; then
        echo 'Não foi possível importar a sessão. Entre na conta correta pelo navegador,' >&2
        echo 'conclua as verificações e confira se foi escolhido o perfil correto.' >&2
        echo 'Para outro perfil, passe o caminho do arquivo de cookies como terceiro argumento.' >&2
        echo 'Nenhum login com senha será tentado automaticamente; o cron não foi alterado.' >&2
        exit 1
    fi
elif [[ ! -f "$session_file" ]]; then
    echo 'Não há sessão salva. O iniciador não tentará login com senha.' >&2
    echo 'Se sua conta está bloqueada, recupere o acesso pelo aplicativo oficial primeiro.' >&2
    echo 'Somente após normalizar o acesso, entre no navegador e importe a sessão:' >&2
    printf 'bash %q %q firefox\n' "$repo_dir/iniciar.sh" "$instagram_user" >&2
    echo 'Substitua firefox pelo navegador usado, se necessário. O cron não foi alterado.' >&2
    exit 1
fi
chmod 600 -- "$session_file"

echo 'Coletando os dados e gerando o primeiro relatório...'
if ! "$repo_dir/.venv/bin/python" -m instafollowcheck --usuario "$instagram_user"; then
    echo 'A coleta falhou. O cron não foi alterado. Confira o erro e o README.' >&2
    exit 1
fi

# Preserve todas as tarefas existentes. Somente a linha marcada desta conta é atualizada.
# Os arquivos temporários e o log são privados por causa do umask acima.
cron_temp="$(mktemp)"
trap 'rm -f -- "$cron_temp" "$cron_temp.err"' EXIT
if ! LC_ALL=C crontab -l >"$cron_temp" 2>"$cron_temp.err"; then
    if ! grep -q '^no crontab for ' "$cron_temp.err"; then
        cat "$cron_temp.err" >&2
        echo 'Não foi possível ler o cron; nenhuma tarefa foi alterada.' >&2
        exit 1
    fi
fi
"$repo_dir/.venv/bin/python" - "$repo_dir" "$instagram_user" "$cron_temp" <<'PY'
import shlex
import sys
from pathlib import Path

repo, username, filename = sys.argv[1:]
marker = f"# instafollowcheck:{username}"
path = Path(filename)
lines = [line for line in path.read_text().splitlines() if not line.endswith(marker)]
python = shlex.quote(str(Path(repo) / ".venv/bin/python"))
log = shlex.quote(str(Path.home() / ".local/share/instafollowcheck/cron.log"))
command = (
    f"umask 077; {python} -m instafollowcheck --usuario {username} --quiet >> {log} 2>&1"
)
lines.append("15 8 * * * " + command.replace("%", r"\%") + " " + marker)
path.write_text("\n".join(lines) + "\n")
PY
crontab "$cron_temp"

if ! systemctl is-active --quiet cron; then
    echo 'Ativando o serviço cron (pode pedir a senha do Ubuntu)...'
    sudo systemctl enable --now cron
fi

echo 'Pronto: relatório gerado e execução diária configurada para 08:15, no fuso do sistema.'
echo "Relatórios: $HOME/.local/share/instafollowcheck/reports/"
