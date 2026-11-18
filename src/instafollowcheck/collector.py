"""Coleta pela própria sessão web do Instagram em um Chrome separado."""

from __future__ import annotations

import json
import logging
import random
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from .config import browser_profile_directory, normalize_username

LOGGER = logging.getLogger("instafollowcheck")
APP_ID = "936619743392459"
PAGE_SIZE = 24
MAX_PAGES = 4000
MAX_JSON_NODES = 300_000
SLEEP_MIN = 0.9
SLEEP_MAX = 2.2


class CollectorError(Exception):
    """Erro apresentável sem expor cookies, corpos HTTP ou dados privados."""


class LoginRequired(CollectorError):
    pass


class ActionRequired(CollectorError):
    pass


class RateLimited(CollectorError):
    pass


class IncompleteData(CollectorError):
    pass


class Cancelled(CollectorError):
    pass


@dataclass
class ProfileInfo:
    owner_id: str
    username: str
    followers: int
    following: int


@dataclass
class SnapshotData:
    owner_id: str
    username: str
    captured_at: str
    followers: dict[str, str]
    following: dict[str, str]
    reported_followers: int
    reported_following: int
    note: str = ""
    warnings: list[str] = field(default_factory=list)


Progress = Callable[[str, str, int, int], None]


def _noop_progress(stage: str, message: str, current: int, total: int) -> None:
    del stage, message, current, total


def _normalize_user(node: dict) -> tuple[str, str] | None:
    if not isinstance(node, dict):
        return None
    username = node.get("username")
    if not username:
        return None
    try:
        username = normalize_username(str(username))
    except ValueError:
        return None
    user_id = node.get("pk") or node.get("id") or node.get("userid")
    if user_id is not None and str(user_id).isdecimal() and int(user_id) > 0:
        return f"id:{user_id}", username
    return f"name:{username}", username


def _entries(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    users = payload.get("users")
    if isinstance(users, list):
        return [item for item in users if isinstance(item, dict)]
    edges = payload.get("data", {}).get("user", {}).get("edge_followed_by", {}).get("edges")
    if not isinstance(edges, list):
        edges = payload.get("data", {}).get("user", {}).get("edge_follow", {}).get("edges")
    if not isinstance(edges, list):
        edges = payload.get("edges")
    if not isinstance(edges, list):
        return []
    result = []
    for edge in edges:
        if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
            result.append(edge["node"])
    return result


def _next_cursor(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("next_max_id", "next_cursor"):
        value = payload.get(key)
        if value not in (None, "", 0, "0", False):
            return str(value)
    for source in (payload.get("page_info"),):
        if isinstance(source, dict):
            value = source.get("end_cursor")
            if value and source.get("has_next_page", True):
                return str(value)
    user = payload.get("data", {}).get("user", {}) if isinstance(payload, dict) else {}
    for edge_name in ("edge_followed_by", "edge_follow"):
        page_info = user.get(edge_name, {}).get("page_info", {}) if isinstance(user, dict) else {}
        if isinstance(page_info, dict) and page_info.get("has_next_page"):
            value = page_info.get("end_cursor")
            if value:
                return str(value)
    return None


def parse_profile_payload(payload: dict) -> ProfileInfo:
    data = payload.get("data") if isinstance(payload, dict) else None
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        user = payload.get("user") if isinstance(payload, dict) else None
    if not isinstance(user, dict):
        raise IncompleteData("O Instagram não devolveu os dados do perfil.")
    username = user.get("username")
    user_id = user.get("id") or user.get("pk") or user.get("userid")
    followers = (user.get("edge_followed_by") or {}).get("count")
    following = (user.get("edge_follow") or {}).get("count")
    if not username or not user_id or followers is None or following is None:
        raise IncompleteData("A resposta do perfil veio incompleta.")
    try:
        normalized = normalize_username(str(username))
        owner_id = str(int(user_id))
        return ProfileInfo(owner_id, normalized, int(followers), int(following))
    except (TypeError, ValueError) as exc:
        raise IncompleteData("A resposta do perfil veio com dados inválidos.") from exc


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _owner_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return str(value)
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return str(int(value))
    return None


def _profile_from_node(node: dict, expected_username: str) -> ProfileInfo | None:
    """Reconhece o objeto de perfil nas várias formas usadas pelo Instagram."""
    username = node.get("username")
    if not isinstance(username, str):
        return None
    try:
        normalized = normalize_username(username)
    except ValueError:
        return None
    if normalized != expected_username:
        return None

    owner_id = _owner_id(node.get("id") or node.get("pk") or node.get("userid"))
    followers = _non_negative_int(node.get("follower_count"))
    if followers is None and isinstance(node.get("edge_followed_by"), dict):
        followers = _non_negative_int(node["edge_followed_by"].get("count"))
    following = _non_negative_int(node.get("following_count"))
    if following is None and isinstance(node.get("edge_follow"), dict):
        following = _non_negative_int(node["edge_follow"].get("count"))
    if owner_id is None or followers is None or following is None:
        return None
    return ProfileInfo(owner_id, normalized, followers, following)


def find_profile_info(payloads: list[object], username: str) -> ProfileInfo | None:
    """Percorre o JSON embutido na página do perfil e devolve os dados exatos.

    O Instagram removeu ``web_profile_info`` em 2026, mas a própria página do
    perfil ainda entrega ``id``, ``follower_count`` e ``following_count`` no
    JSON que hidrata a interface. O percurso por todos os nós evita depender do
    caminho interno (``require[...].__bbox...``), que muda com frequência.
    """
    expected = normalize_username(username)
    for payload in payloads:
        stack: list[object] = [payload]
        visited = 0
        while stack and visited < MAX_JSON_NODES:
            node = stack.pop()
            visited += 1
            if isinstance(node, dict):
                info = _profile_from_node(node, expected)
                if info is not None:
                    return info
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return None


def parse_friendship_payload(payload: dict) -> tuple[dict[str, str], str | None]:
    users: dict[str, str] = {}
    for node in _entries(payload):
        parsed = _normalize_user(node)
        if parsed:
            key, username = parsed
            users[key] = username
    return users, _next_cursor(payload)


def api_error(payload: dict, status: int) -> None:
    message = ""
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("error") or "").lower()
    if status in (401, 403) or "login_required" in message or "checkpoint" in message:
        raise LoginRequired(
            "A sessão do Instagram expirou ou pediu verificação. "
            "Entre novamente no navegador aberto pelo app."
        )
    if status == 429 or "please wait" in message or "rate limit" in message:
        raise RateLimited(
            "O Instagram limitou as solicitações. Aguarde alguns minutos e tente novamente."
        )
    if "feedback_required" in message or "challenge" in message or "restrict" in message:
        raise ActionRequired(
            "O Instagram pediu uma verificação ou restringiu a conta. "
            "Resolva no aplicativo oficial e tente mais tarde."
        )
    if status == 404:
        raise CollectorError("O perfil não foi encontrado ou não está disponível.")
    if status >= 400:
        raise CollectorError(
            "O Instagram recusou a solicitação. Aguarde e tente novamente mais tarde."
        )


def validate_counts(reported: int | None, actual: int, label: str) -> str:
    if reported is None:
        return ""
    difference = abs(int(reported) - int(actual))
    tolerance = max(3, round(int(reported) * 0.01))
    if difference > tolerance:
        raise IncompleteData(
            f"A lista de {label} veio incompleta ({actual} de {reported}). "
            "Nada foi salvo para evitar unfollows falsos."
        )
    if difference:
        return (
            f"A contagem do perfil era {reported}, mas a lista tinha {actual} contas. "
            "Pode haver conta desativada ou alteração durante a coleta."
        )
    return ""


def collect_pages(
    fetch_page: Callable[[str | None], dict],
    expected: int,
    direction: str,
    *,
    progress: Progress = _noop_progress,
    cancel: Callable[[], bool] | None = None,
    pacer: Callable[[], None] | None = None,
    max_pages: int = MAX_PAGES,
) -> dict[str, str]:
    """Pagina uma lista usando somente o fetch injetado; testável sem navegador."""
    users: dict[str, str] = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    empty_pages = 0
    delay = pacer or (lambda: time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX)))
    label = "seguidores" if direction == "followers" else "seguindo"
    for page in range(1, max_pages + 1):
        if cancel and cancel():
            raise Cancelled("Coleta cancelada.")
        payload = fetch_page(cursor)
        page_users, next_cursor = parse_friendship_payload(payload)
        added = 0
        for key, username in page_users.items():
            if key not in users:
                users[key] = username
                added += 1
        progress(
            direction,
            f"Lendo {label} ({len(users)} de {expected or '?'})",
            len(users),
            expected,
        )
        if not next_cursor or next_cursor in seen_cursors:
            break
        if added == 0:
            # Páginas de fronteira podem repetir usuários; só desiste depois de
            # várias páginas seguidas sem nenhuma conta nova.
            empty_pages += 1
            if empty_pages >= 3:
                break
        else:
            empty_pages = 0
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        delay()
    else:
        raise IncompleteData(
            f"A lista de {label} passou de {max_pages} páginas; coleta interrompida."
        )
    if expected and not users and expected > 0:
        raise IncompleteData(f"A lista de {label} voltou vazia, mas o perfil informa {expected}.")
    return users


class BrowserCollector:
    def __init__(
        self,
        data_dir: Path,
        username: str,
        *,
        progress: Progress = _noop_progress,
        cancel: Callable[[], bool] | None = None,
        headless: bool = False,
    ) -> None:
        self.data_dir = data_dir
        self.username = normalize_username(username)
        self.progress = progress
        self.cancel = cancel or (lambda: False)
        self.headless = headless
        self._playwright = None
        self._context = None

    def _channel(self) -> str | None:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            if shutil.which(name):
                return "chrome" if "chrome" in name else None
        return None

    def close(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _check_cancel(self) -> None:
        if self.cancel():
            raise Cancelled("Coleta cancelada.")

    def run(self) -> SnapshotData:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CollectorError(
                "Playwright não está instalado. Execute: python -m pip install -e ."
            ) from exc

        profile = browser_profile_directory(self.data_dir, self.username)
        try:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                channel=self._channel(),
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
                locale="pt-BR",
                ignore_default_args=["--enable-automation"],
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            page = self._context.pages[0] if self._context.pages else self._context.new_page()
            page.set_default_timeout(60000)
            self._login(page)
            info = self._profile(page)
            self._verify_owner(info)
            followers = self._collect(page, info, "followers", info.followers)
            following = self._collect(page, info, "following", info.following)
            note_parts = [
                validate_counts(info.followers, len(followers), "seguidores"),
                validate_counts(info.following, len(following), "seguindo"),
            ]
            note = " ".join(part for part in note_parts if part).strip()
            self.progress("saving", "Salvando snapshot...", 0, 0)
            return SnapshotData(
                owner_id=info.owner_id,
                username=info.username,
                captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                followers=followers,
                following=following,
                reported_followers=info.followers,
                reported_following=info.following,
                note=note,
            )
        except (LoginRequired, ActionRequired, RateLimited, IncompleteData, Cancelled):
            raise
        except PlaywrightError as exc:
            text = str(exc).lower()
            if "closed" in text or "target page" in text or "browser has been closed" in text:
                raise CollectorError(
                    "A janela do navegador foi fechada. Abra novamente e repita a coleta."
                ) from exc
            raise CollectorError(
                "Não foi possível controlar o Chrome. Verifique se ele está instalado e "
                "se a janela do app não está travada por outro processo."
            ) from exc
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError(
                "Falha inesperada durante a coleta. Nada foi salvo. Tente novamente."
            ) from exc
        finally:
            self.close()

    def _login(self, page) -> None:
        self.progress("login", "Verificando a sessão do Instagram...", 0, 0)
        cookies = self._context.cookies("https://www.instagram.com")
        if any(item.get("name") == "sessionid" and item.get("value") for item in cookies):
            return
        if self.headless:
            raise LoginRequired(
                "Não há sessão salva para esta conta. Execute a coleta com a janela "
                "do Chrome visível para entrar uma vez."
            )
        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
        self.progress(
            "waiting_login",
            "Faça login na janela do Chrome que abriu. O app continua sozinho depois disso.",
            0,
            0,
        )
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            self._check_cancel()
            try:
                cookies = self._context.cookies("https://www.instagram.com")
            except Exception as exc:
                raise CollectorError("A janela do navegador foi fechada.") from exc
            if any(item.get("name") == "sessionid" and item.get("value") for item in cookies):
                page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
                return
            time.sleep(2)
        raise LoginRequired(
            "O login não foi concluído em 10 minutos. Abra o app novamente e repita a conexão."
        )

    def _profile(self, page) -> ProfileInfo:
        self._check_cancel()
        self.progress("profile", "Lendo os dados do perfil...", 0, 0)
        page.goto(
            f"https://www.instagram.com/{quote(self.username)}/",
            wait_until="domcontentloaded",
        )
        if "/accounts/login" in page.url or f"/{self.username}" not in page.url.lower():
            raise LoginRequired("A sessão expirou. Faça login novamente na janela do app.")

        # O endpoint web_profile_info foi retirado pelo Instagram em 2026. A
        # própria página do perfil ainda carrega os números exatos no JSON que
        # hidrata a interface, inclusive para contas privadas.
        info: ProfileInfo | None = None
        scripts: list[str] = []
        for attempt in range(8):
            self._check_cancel()
            raw_scripts = page.eval_on_selector_all(
                'script[type="application/json"]',
                "els => els.map(e => e.textContent)",
            )
            scripts = [text for text in (raw_scripts or []) if isinstance(text, str)]
            payloads: list[object] = []
            for text in scripts:
                if self.username not in text.lower():
                    continue
                try:
                    payloads.append(json.loads(text))
                except (TypeError, ValueError):
                    continue
            info = find_profile_info(payloads, self.username)
            if info is not None:
                break
            page.wait_for_timeout(700 if attempt else 400)
        if info is None:
            LOGGER.warning(
                "Perfil de @%s não localizado no JSON embutido (scripts=%d, url=%s).",
                self.username,
                len(scripts),
                page.url.split("?", 1)[0],
            )
            raise IncompleteData(
                "Não foi possível ler os dados do perfil. A página do Instagram pode ter "
                "mudado ou pedido verificação. Abra a janela do app e tente novamente."
            )
        return info

    def _verify_owner(self, info: ProfileInfo) -> None:
        cookies = self._context.cookies("https://www.instagram.com")
        cookie_map = {item.get("name"): item.get("value") for item in cookies}
        logged_id = cookie_map.get("ds_user_id") or cookie_map.get("c_user")
        if not logged_id:
            raise LoginRequired("Não foi possível identificar a conta logada. Entre novamente.")
        if str(logged_id) != info.owner_id:
            raise CollectorError(
                "A sessão do navegador está logada em outra conta. "
                "Saia dela ou use um perfil separado para esta conta."
            )

    def _collect(self, page, info: ProfileInfo, direction: str, expected: int) -> dict[str, str]:
        self.progress(direction, f"Preparando {direction}...", 0, expected)
        endpoint = "followers" if direction == "followers" else "following"

        def fetch_page(cursor: str | None) -> dict:
            self._check_cancel()
            url = f"/api/v1/friendships/{info.owner_id}/{endpoint}/?count={PAGE_SIZE}"
            if cursor:
                url += f"&max_id={quote(str(cursor))}"
            return self._fetch_json(page, url)

        return collect_pages(
            fetch_page,
            expected,
            direction,
            progress=self.progress,
            cancel=self.cancel,
            pacer=lambda: time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX)),
        )

    def _fetch_json(self, page, url: str) -> dict:
        cookies = self._context.cookies("https://www.instagram.com")
        csrf = next((item.get("value") for item in cookies if item.get("name") == "csrftoken"), "")
        headers = {
            "x-ig-app-id": APP_ID,
            "x-requested-with": "XMLHttpRequest",
            "accept": "*/*",
        }
        if csrf:
            headers["x-csrftoken"] = csrf
        result = page.evaluate(
            """
            async ({url, headers}) => {
                try {
                    const response = await fetch(url, {
                        method: 'GET',
                        headers,
                        credentials: 'include',
                        mode: 'same-origin',
                    });
                    const text = await response.text();
                    let data = null;
                    try { data = JSON.parse(text); } catch (e) { data = null; }
                    return {
                        status: response.status,
                        contentType: response.headers.get('content-type'),
                        data,
                        text: data ? null : text.slice(0, 200),
                    };
                } catch (e) {
                    return {
                        status: 0,
                        contentType: null,
                        data: null,
                        text: String(e).slice(0, 200),
                    };
                }
            }
            """,
            {"url": url, "headers": headers},
        )
        status = int(result.get("status") or 0)
        payload = result.get("data")
        if status == 0:
            raise CollectorError(
                "Não foi possível falar com o Instagram. Verifique a conexão e tente novamente."
            )
        if not isinstance(payload, dict):
            text = str(result.get("text") or "")
            lowered = text.lower()
            LOGGER.warning(
                "Resposta não-JSON em %s: status=%s tipo=%s trecho=%r",
                url.split("?", 1)[0],
                status,
                result.get("contentType"),
                text[:120],
            )
            if status in (401, 403) or "login" in lowered or "checkpoint" in lowered:
                raise LoginRequired("A sessão expirou. Faça login novamente.")
            if status == 429:
                raise RateLimited(
                    "O Instagram limitou as solicitações. Aguarde alguns minutos e tente novamente."
                )
            if status == 404 or "page not found" in lowered:
                raise CollectorError(
                    "O Instagram não encontrou a interface de amigos. A interface do site "
                    "pode ter mudado; nada foi salvo."
                )
            raise CollectorError(
                "O Instagram devolveu uma página inesperada (provavelmente login ou bloqueio). "
                "Abra a janela do app e conclua a verificação."
            )
        api_error(payload, status)
        if str(payload.get("status", "")).lower() == "fail":
            message = str(payload.get("message") or "").lower()
            if "login" in message or "checkpoint" in message:
                raise LoginRequired("A sessão expirou. Faça login novamente.")
            if "wait" in message or "limit" in message:
                raise RateLimited("O Instagram limitou as solicitações. Aguarde e tente depois.")
            raise ActionRequired(
                "O Instagram recusou a coleta. Resolva eventuais verificações no app oficial."
            )
        return payload


def collect_account(
    data_dir: Path,
    username: str,
    *,
    progress: Progress = _noop_progress,
    cancel: Callable[[], bool] | None = None,
    headless: bool = False,
) -> SnapshotData:
    return BrowserCollector(
        data_dir, username, progress=progress, cancel=cancel, headless=headless
    ).run()
