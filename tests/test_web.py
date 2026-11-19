"""Segurança local e APIs do painel, com banco temporário e sem rede."""

from __future__ import annotations

import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from support import export_json, export_zip, iso_days_ago

from instafollowcheck.app import create_app
from instafollowcheck.db import database, save_snapshot
from instafollowcheck.jobs import Job

TOKEN = "token-de-teste"


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(data_dir=self.root, token=TOKEN, testing=True)
        self.client = self.app.test_client()
        self.csrf = ""
        with database(self.root) as connection:
            save_snapshot(
                connection,
                owner_key="id:100",
                owner_id="100",
                username="minhaconta",
                captured_at=iso_days_ago(30),
                source="browser",
                followers={"id:1": "ana", "id:2": "bia"},
                following={"id:1": "ana", "id:3": "caio"},
            )
            save_snapshot(
                connection,
                owner_key="id:200",
                owner_id="200",
                username="outra_conta",
                captured_at=iso_days_ago(30),
                source="browser",
                followers={"id:1": "ana"},
                following={"id:1": "ana"},
            )

    def tearDown(self):
        self.temp.cleanup()

    def login(self):
        response = self.client.get(f"/auth?token={TOKEN}")
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.csrf = session["csrf"]

    def test_requires_token_and_csrf(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 403)
        self.assertIn("link não está autorizado", response.get_data(as_text=True))
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"][:6], "Sessão")
        response = self.client.get("/auth?token=errado")
        self.assertEqual(response.status_code, 403)
        self.login()
        response = self.client.post("/api/settings", json={"window_days": 7})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            "/api/settings",
            json={"window_days": 7},
            headers={"X-CSRF-Token": self.csrf, "Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 200)

    def test_host_and_origin_are_checked(self):
        self.login()
        response = self.client.get("/", headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            "/api/settings",
            json={"window_days": 7},
            headers={"X-CSRF-Token": self.csrf, "Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_state_and_lists(self):
        self.login()
        response = self.client.get("/api/state?account=id:200")
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["account"]["username"], "outra_conta")
        self.assertEqual(data["diff"]["summary"]["followers"], 1)
        self.assertEqual(data["settings"]["window_days"], 10)
        response = self.client.get("/api/list?kind=not_following_back&account=id:100")
        self.assertEqual(response.get_json()["total"], 1)
        self.assertEqual(response.get_json()["items"][0]["username"], "caio")
        response = self.client.get("/api/history?account=id:100")
        self.assertEqual(len(response.get_json()["history"]), 1)
        response = self.client.get("/api/export.csv?kind=following&account=id:100")
        self.assertEqual(response.status_code, 200)
        self.assertIn("caio", response.get_data(as_text=True))

    def test_import_zip_and_delete_account(self):
        self.login()
        data = export_zip(["ana", "nova"], ["ana", "caio"])
        response = self.client.post(
            "/api/import-export",
            data={
                "username": "minhaconta",
                "file": (io.BytesIO(data), "instagram.zip"),
            },
            headers={"X-CSRF-Token": self.csrf},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body["account_key"], "id:100")
        self.assertEqual(body["followers"], 2)
        self.assertEqual(body["following"], 2)

        state = self.client.get("/api/state?account=id:100").get_json()
        self.assertEqual(state["account"]["last_source"], "export")
        self.assertEqual(state["diff"]["latest"]["followers_count"], 2)

        response = self.client.post(
            "/api/accounts/delete",
            json={"account": "id:200"},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 200)
        state = self.client.get("/api/state").get_json()
        self.assertEqual([item["owner_key"] for item in state["accounts"]], ["id:100"])

    def test_single_json_upload_is_rejected(self):
        self.login()
        response = self.client.post(
            "/api/import-export",
            data={
                "username": "minhaconta",
                "file": (io.BytesIO(export_json(["ana"])), "followers_1.json"),
            },
            headers={"X-CSRF-Token": self.csrf},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("incompleta", response.get_json()["error"])

    def test_collect_uses_requested_account_for_cooldown(self):
        self.login()
        with database(self.root) as connection:
            save_snapshot(
                connection,
                owner_key="id:100",
                owner_id="100",
                username="minhaconta",
                captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source="browser",
                followers={"id:1": "ana"},
                following={"id:1": "ana"},
            )
        response = self.client.post(
            "/api/collect",
            json={"account": "id:100", "username": "minhaconta"},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 429)

        manager = self.app.extensions["manager"]
        job = Job(id="job-1", kind="collect", username="outra_conta")
        with patch.object(manager, "start_collect", return_value=job) as start:
            response = self.client.post(
                "/api/collect",
                json={"account": "id:100", "username": "outra_conta"},
                headers={"X-CSRF-Token": self.csrf},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["id"], "job-1")
        start.assert_called_once_with("outra_conta")

    def test_settings_and_security_headers(self):
        self.login()
        response = self.client.post(
            "/api/settings",
            json={"window_days": 21, "cooldown_minutes": 0},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.get_json(), {"window_days": 21, "cooldown_minutes": 0})
        state = self.client.get("/api/state").get_json()
        self.assertEqual(state["settings"], {"window_days": 21, "cooldown_minutes": 0})
        self.assertEqual(state["diff"]["window_days"], 21)
        response = self.client.get("/")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        static_response = self.client.get("/static/style.css")
        self.assertEqual(static_response.status_code, 200)
        static_response.close()


if __name__ == "__main__":
    unittest.main()
