"""Aplicação Flask local: dashboard, APIs de coleta e importação."""

from __future__ import annotations

import csv
import io
import logging
import secrets
import shutil
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import db
from .config import (
    DEFAULT_WINDOW_DAYS,
    MAX_UPLOAD_BYTES,
    browser_profile_directory,
    data_directory,
    ensure_private_dir,
    find_free_port,
    normalize_username,
)
from .domain import build_diff
from .importer import ExportError, import_export_uploads
from .jobs import BusyError, JobManager, JobNotFound

LOGGER = logging.getLogger("instafollowcheck")
ACTIVE_JOB_STATUSES = {"queued", "running", "waiting_login"}


def _configure_logging(root: Path) -> None:
    if LOGGER.handlers:
        return
    logs = ensure_private_dir(root / "logs")
    handler = logging.FileHandler(logs / "app.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def _json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _selected_account(connection, owner_key: str | None):
    accounts = db.list_accounts(connection)
    if not accounts:
        return None, accounts
    if owner_key:
        for account in accounts:
            if account["owner_key"] == owner_key:
                return account, accounts
    return accounts[0], accounts


def _window(connection) -> int:
    raw = request.args.get("window")
    if raw is not None:
        try:
            return max(1, min(365, int(raw)))
        except ValueError:
            pass
    return int(db.setting(connection, "window_days", str(DEFAULT_WINDOW_DAYS)) or 10)


def _cooldown_remaining(connection, account) -> int:
    """O intervalo protege o Instagram; importações não bloqueiam a coleta."""
    minutes = int(db.setting(connection, "cooldown_minutes", "10") or 10)
    if minutes <= 0 or account is None:
        return 0
    latest = connection.execute(
        """
        SELECT captured_ts FROM snapshots
        WHERE owner_key = ? AND is_complete = 1 AND source = 'browser'
        ORDER BY captured_ts DESC, id DESC
        LIMIT 1
        """,
        (account["owner_key"],),
    ).fetchone()
    if latest is None:
        return 0
    elapsed = int(datetime.now(timezone.utc).timestamp()) - int(latest["captured_ts"])
    return max(0, minutes * 60 - elapsed)


def create_app(
    *,
    data_dir: Path | None = None,
    token: str | None = None,
    testing: bool = False,
    port: int | None = None,
) -> Flask:
    root = ensure_private_dir(data_directory(data_dir))
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config.update(
        DATA_DIR=root,
        TOKEN=token or secrets.token_urlsafe(32),
        SECRET_KEY=secrets.token_hex(32) if not testing else "test-secret",
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
        SESSION_COOKIE_NAME="instafollowcheck_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=False,
        TESTING=testing,
        PORT=port,
    )
    app.json.ensure_ascii = False
    if not testing:
        _configure_logging(root)
    manager = JobManager(root)
    app.extensions["manager"] = manager
    app.extensions["data_dir"] = root

    @app.context_processor
    def inject_csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        return {"csrf_token": session["csrf"]}

    @app.before_request
    def guard():
        host = request.host.split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost"}:
            return (
                _json_error("Host não permitido.", 403)
                if request.path.startswith("/api/")
                else ("Host não permitido.", 403)
            )
        if request.path.startswith("/static/") or request.path in {"/auth", "/favicon.ico"}:
            return None
        if not session.get("auth"):
            if request.path.startswith("/api/"):
                return _json_error("Sessão local expirada. Abra o link do terminal.", 401)
            return render_template("unauthorized.html"), 403
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.path.startswith(
            "/api/"
        ):
            expected = session.get("csrf")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not secrets.compare_digest(str(expected), str(supplied)):
                return _json_error("Token CSRF inválido. Recarregue a página.", 403)
            origin = request.headers.get("Origin")
            if origin:
                host_name = request.host
                port_part = host_name.rsplit(":", 1)[-1] if ":" in host_name else ""
                allowed = {
                    f"http://{host_name}",
                    f"http://localhost:{port_part}" if port_part else "http://localhost",
                }
                if origin not in allowed:
                    return _json_error("Origem não permitida.", 403)
        return None

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        return response

    @app.errorhandler(405)
    def method_not_allowed(_):
        if request.path.startswith("/api/"):
            return _json_error("Método não permitido.", 405)
        return render_template("unauthorized.html"), 405

    @app.errorhandler(413)
    def too_large(_):
        return _json_error("Arquivo grande demais. Use uma exportação de até 80 MB.", 413)

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/"):
            return _json_error("Recurso não encontrado.", 404)
        return render_template("unauthorized.html"), 404

    @app.errorhandler(500)
    def internal_error(error):
        LOGGER.exception("Erro interno na aplicação: %s", type(error).__name__)
        if request.path.startswith("/api/"):
            return _json_error("Erro interno. Nada foi alterado; tente novamente.", 500)
        return render_template("unauthorized.html"), 500

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    @app.get("/auth")
    def auth():
        supplied = request.args.get("token", "")
        if not secrets.compare_digest(app.config["TOKEN"], supplied):
            return render_template("unauthorized.html"), 403
        session.clear()
        session["auth"] = True
        session["csrf"] = secrets.token_urlsafe(24)
        return redirect(url_for("index"))

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/history")
    def history_page():
        return render_template("history.html")

    @app.get("/settings")
    def settings_page():
        return render_template("settings.html")

    @app.get("/api/state")
    def api_state():
        with db.database(root) as connection:
            window = _window(connection)
            account, accounts = _selected_account(connection, request.args.get("account"))
            diff = build_diff(connection, account["owner_key"], window) if account else None
            active = manager.active()
            if active and active.status not in ACTIVE_JOB_STATUSES:
                active = None
            return jsonify(
                {
                    "accounts": accounts,
                    "account": account,
                    "diff": diff.as_dict() if diff else None,
                    "settings": db.settings(connection),
                    "active_job": active.as_dict() if active else None,
                    "data_dir": str(root),
                    "window": window,
                    "cooldown_remaining": _cooldown_remaining(connection, account),
                }
            )

    @app.get("/api/list")
    def api_list():
        kind = request.args.get("kind", "")
        query = (request.args.get("q") or "").strip().lower()
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1
        per_page = 100
        with db.database(root) as connection:
            account, _ = _selected_account(connection, request.args.get("account"))
            if account is None:
                return jsonify({"items": [], "total": 0, "page": 1, "pages": 0})
            window = _window(connection)
            diff = build_diff(connection, account["owner_key"], window)
            try:
                people = diff.people(kind)
            except ValueError as exc:
                return _json_error(str(exc), 404)
            if query:
                people = [
                    person
                    for person in people
                    if query in person.username.lower() or query in person.key.lower()
                ]
            total = len(people)
            pages = (total + per_page - 1) // per_page if total else 0
            start = (page - 1) * per_page
            items = [person.as_dict() for person in people[start : start + per_page]]
            return jsonify(
                {"items": items, "total": total, "page": page, "pages": pages, "kind": kind}
            )

    @app.get("/api/history")
    def api_history():
        with db.database(root) as connection:
            account, _ = _selected_account(connection, request.args.get("account"))
            if account is None:
                return jsonify({"account": None, "history": []})
            return jsonify(
                {"account": account, "history": db.history(connection, account["owner_key"])}
            )

    @app.post("/api/collect")
    def api_collect():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        with db.database(root) as connection:
            selected, _ = _selected_account(connection, data.get("account"))
            requested = data.get("username") or (selected["username"] if selected else "")
            try:
                username = normalize_username(requested)
            except ValueError as exc:
                return _json_error(str(exc), 400)
            existing = db.find_account(connection, username)
            remaining = _cooldown_remaining(connection, existing)
            if remaining:
                minutes = max(1, (remaining + 59) // 60)
                return _json_error(
                    f"Aguarde cerca de {minutes} minuto(s) antes de coletar novamente.", 429
                )
        try:
            job = manager.start_collect(username)
        except BusyError as exc:
            active = manager.active()
            return jsonify({"error": str(exc), "job": active.as_dict() if active else None}), 409
        return jsonify(job.as_dict()), 202

    @app.post("/api/connect")
    def api_connect():
        return api_collect()

    @app.get("/api/jobs/<job_id>")
    def api_job(job_id: str):
        try:
            return jsonify(manager.get(job_id).as_dict())
        except JobNotFound as exc:
            return _json_error(str(exc), 404)

    @app.post("/api/jobs/<job_id>/cancel")
    def api_cancel(job_id: str):
        try:
            return jsonify(manager.cancel(job_id).as_dict())
        except JobNotFound as exc:
            return _json_error(str(exc), 404)

    @app.post("/api/import-export")
    def api_import():
        username = request.form.get("username", "")
        owner_id = (request.form.get("owner_id") or "").strip() or None
        uploads = [item for item in request.files.getlist("file") if item and item.filename]
        try:
            username = normalize_username(username)
        except ValueError as exc:
            return _json_error(str(exc), 400)
        if owner_id is not None and not owner_id.isdecimal():
            return _json_error("O ID da conta deve conter somente números.", 400)
        if owner_id is not None:
            owner_id = str(int(owner_id))
        if not uploads:
            return _json_error(
                "Selecione o ZIP da exportação ou os dois arquivos JSON juntos.", 400
            )
        try:
            files = [(Path(item.filename or "").name, item.read()) for item in uploads]
            followers, following = import_export_uploads(files)
        except ExportError as exc:
            return _json_error(str(exc), 400)
        with db.database(root) as connection:
            try:
                snapshot_id, account_key = db.insert_export_snapshot(
                    connection,
                    username=username,
                    followers=followers,
                    following=following,
                    owner_id=owner_id,
                )
            except ValueError as exc:
                return _json_error(str(exc), 400)
        return jsonify(
            {
                "snapshot_id": snapshot_id,
                "account_key": account_key,
                "followers": len(followers),
                "following": len(following),
            }
        )

    @app.post("/api/settings")
    def api_settings():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        try:
            window = max(1, min(365, int(data.get("window_days", DEFAULT_WINDOW_DAYS))))
            cooldown = max(0, min(1440, int(data.get("cooldown_minutes", 10))))
        except (TypeError, ValueError):
            return _json_error("Valores de configuração inválidos.", 400)
        with db.database(root) as connection:
            db.set_setting(connection, "window_days", str(window))
            db.set_setting(connection, "cooldown_minutes", str(cooldown))
        return jsonify({"window_days": window, "cooldown_minutes": cooldown})

    @app.get("/api/export.csv")
    def api_export_csv():
        kind = request.args.get("kind", "followers")
        with db.database(root) as connection:
            account, _ = _selected_account(connection, request.args.get("account"))
            if account is None:
                return _json_error("Nenhuma conta no histórico.", 404)
            diff = build_diff(connection, account["owner_key"], _window(connection))
            try:
                people = diff.people(kind)
            except ValueError as exc:
                return _json_error(str(exc), 404)
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["usuario", "chave", "visto_em"])
        for person in people:
            username = person.username
            if username.startswith(("=", "+", "-", "@")):
                username = "'" + username
            writer.writerow([username, person.key, person.seen_at or ""])
        filename = f"instafollowcheck_{account['username']}_{kind}.csv"
        return Response(
            output.getvalue().encode("utf-8-sig"),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/accounts/delete")
    def api_delete_account():
        data = request.get_json(silent=True) or {}
        owner_key = data.get("account") or ""
        with db.database(root) as connection:
            account = db.find_account_by_owner(connection, owner_key)
            if account is None:
                return _json_error("Conta não encontrada.", 404)
            active = manager.active()
            if active and active.username == account["username"]:
                return _json_error("Feche a coleta em andamento antes de apagar esta conta.", 409)
            db.delete_account(connection, owner_key)
            username = account["username"]
        profile = browser_profile_directory(root, username)
        try:
            shutil.rmtree(profile)
        except OSError:
            LOGGER.warning("Não foi possível remover todo o perfil do navegador de %s", username)
        return jsonify({"deleted": owner_key, "username": username})

    @app.post("/api/browser/reset")
    def api_reset_browser():
        data = request.get_json(silent=True) or {}
        try:
            username = normalize_username(data.get("username", ""))
        except ValueError as exc:
            return _json_error(str(exc), 400)
        active = manager.active()
        if active and active.username == username:
            return _json_error("Feche a coleta em andamento antes de redefinir o navegador.", 409)
        profile = browser_profile_directory(root, username)
        try:
            shutil.rmtree(profile)
        except OSError:
            LOGGER.warning("Não foi possível apagar o perfil do navegador de %s", username)
            return _json_error(
                "Não foi possível apagar o perfil do navegador. Feche a janela do app.", 409
            )
        return jsonify({"reset": username})

    if port:
        app.config["PORT"] = port
    return app


def _auth_url(app: Flask, port: int) -> str:
    return f"http://127.0.0.1:{port}/auth?token={app.config['TOKEN']}"


def run_server(
    *,
    data_dir: Path | None = None,
    port: int | None = None,
    open_browser: bool = True,
) -> None:
    root = ensure_private_dir(data_directory(data_dir))
    selected = int(port or find_free_port())
    app = create_app(data_dir=root, port=selected)
    url = _auth_url(app, selected)
    print("\nInstaFollowCheck iniciado.")
    print(f"Abra no navegador: {url}")
    print(f"Dados locais: {root}")
    print("Para encerrar, pressione Ctrl+C.\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        from waitress import serve

        serve(app, host="127.0.0.1", port=selected, threads=6)
    except KeyboardInterrupt:
        print("\nEncerrado.")
    except ImportError as exc:
        raise SystemExit("Instale as dependências: python -m pip install -e .") from exc
