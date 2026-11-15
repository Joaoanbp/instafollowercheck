"""Coleta autenticada com sessão existente; nunca solicita senha ou faz login."""

import os
import pickle
import re
import stat
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

from .models import MonitorError, Snapshot


def username_arg(value: str) -> str:
    value = value.removeprefix("@").lower()
    if not re.fullmatch(r"[a-z0-9._]{1,30}", value):
        raise ValueError("Informe um usuário válido, sem URL (com ou sem @).")
    return value


def validate_session(path: Path) -> None:
    """Sessões Instaloader são pickle: aceite apenas arquivos locais confiáveis."""
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise MonitorError("Sessão ausente. Gere a sessão no terminal conforme o README.") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise MonitorError("A sessão deve ser um arquivo regular do seu usuário, sem symlink.")
    if info.st_mode & 0o077:
        raise MonitorError("Sessão acessível a outros usuários. Execute chmod 600 no arquivo.")


def _accounts(profiles) -> dict[str, str]:
    result = {}
    for profile in profiles:
        account_id = str(profile.userid)
        name = username_arg(profile.username)
        if not account_id.isdecimal() or int(account_id) <= 0:
            raise MonitorError("Resposta do Instagram contém ID inválido; coleta descartada.")
        result[account_id] = name
    return result


def collect_snapshot(username: str, session: Path, api=None) -> Snapshot:
    validate_session(session)
    if api is None:
        try:
            import instaloader as api
        except ImportError as exc:
            raise MonitorError("Instale as dependências: python -m pip install -e .") from exc

    # Impede que logs internos incluam URLs, respostas ou dados da sessão.
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
        loader = api.Instaloader(
            quiet=True,
            sleep=True,
            max_connection_attempts=1,
            request_timeout=60,
            fatal_status_codes=[400, 401, 403, 429],
        )
        try:
            try:
                loader.load_session_from_file(username, filename=str(session))
            except (
                OSError, pickle.UnpicklingError, EOFError, ValueError, TypeError, AttributeError,
            ) as exc:
                raise MonitorError("Sessão ilegível ou corrompida; gere outra manualmente.") from exc
            authenticated = loader.test_login()
            if not authenticated:
                raise MonitorError(
                    "Sessão expirada. Renove-a manualmente conforme o README."
                )
            if authenticated.lower() != username:
                raise MonitorError("A sessão pertence a outra conta; confira o usuário informado.")

            profile = api.Profile.from_username(loader.context, username)
            expected = (profile.followers, profile.followees)
            followers = _accounts(profile.get_followers())
            following = _accounts(profile.get_followees())
            # Um perfil novo evita reutilizar as contagens em cache do primeiro objeto.
            refreshed = api.Profile.from_id(loader.context, profile.userid)
            counts_after = (refreshed.followers, refreshed.followees)
            if expected != counts_after or expected != (len(followers), len(following)):
                raise MonitorError(
                    "Listas incompletas ou contagens mudaram durante a coleta. "
                    "Histórico preservado; tente em outro horário."
                )
            return Snapshot(
                str(profile.userid), username, datetime.now().astimezone().isoformat(),
                followers, following,
            )
        except api.exceptions.LoginRequiredException as exc:
            raise MonitorError("Sessão inválida ou expirada. Renove-a manualmente.") from exc
        except api.exceptions.AbortDownloadException as exc:
            raise MonitorError(
                "Instagram interrompeu o acesso (restrição, limite ou desafio). "
                "Verifique o aplicativo oficial e aguarde; não há nova tentativa automática."
            ) from exc
        except api.exceptions.InstaloaderException as exc:
            raise MonitorError(
                "Falha de acesso ao Instagram. Verifique conexão, sessão e eventuais "
                "restrições no aplicativo oficial. Nenhuma coleta parcial será salva."
            ) from exc
        finally:
            loader.close()
