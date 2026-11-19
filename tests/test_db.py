"""Persistência SQLite, migração do formato antigo e permissões privadas."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from support import iso_days_ago

from instafollowcheck.db import (
    account_for_export,
    database,
    find_account,
    insert_export_snapshot,
    list_accounts,
    save_snapshot,
)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_accounts_and_private_permissions(self):
        self.root.chmod(0o755)
        with database(self.root) as connection:
            save_snapshot(
                connection,
                owner_key="id:100",
                owner_id="100",
                username="minhaconta",
                captured_at=iso_days_ago(0),
                source="browser",
                followers={"id:1": "ana"},
                following={"id:1": "ana", "id:2": "bia"},
                reported_followers=1,
                reported_following=2,
            )
            accounts = list_accounts(connection)
            account = find_account(connection, "@MINHACONTA")
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["owner_key"], "id:100")
        self.assertEqual(accounts[0]["followers_count"], 1)
        self.assertEqual(accounts[0]["following_count"], 2)
        self.assertEqual(account["owner_id"], "100")
        self.assertEqual(oct(self.root.stat().st_mode & 0o777), "0o700")
        database_file = self.root / "history.sqlite3"
        self.assertEqual(oct(database_file.stat().st_mode & 0o777), "0o600")

    def test_export_reuses_existing_id_account(self):
        with database(self.root) as connection:
            save_snapshot(
                connection,
                owner_key="id:100",
                owner_id="100",
                username="minhaconta",
                captured_at=iso_days_ago(5),
                source="browser",
                followers={"id:1": "ana", "id:2": "bia"},
                following={"id:1": "ana"},
            )
            snapshot_id, owner_key = insert_export_snapshot(
                connection,
                username="minhaconta",
                followers={"name:ana": "ana", "name:bia": "bia"},
                following={"name:ana": "ana"},
                owner_id="100",
                captured_at=iso_days_ago(0),
            )
            members = connection.execute(
                "SELECT account_key FROM members WHERE snapshot_id = ? ORDER BY account_key",
                (snapshot_id,),
            ).fetchall()
        self.assertEqual(owner_key, "id:100")
        self.assertEqual({row["account_key"] for row in members}, {"id:1", "id:2"})
        self.assertEqual(len(members), 3)

    def test_export_owner_id_cannot_change_username(self):
        with database(self.root) as connection:
            save_snapshot(
                connection,
                owner_key="id:100",
                owner_id="100",
                username="conta_antiga",
                captured_at=iso_days_ago(5),
                source="browser",
                followers={},
                following={},
            )
            with self.assertRaises(ValueError):
                account_for_export(connection, "outra_conta", "100")

    def test_legacy_payload_is_migrated_without_loss(self):
        path = self.root / "history.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE snapshots (
                id INTEGER PRIMARY KEY,
                owner_id TEXT,
                username TEXT,
                payload TEXT
            );
            """
        )
        payload = {
            "captured_at": iso_days_ago(3),
            "followers": {"1": "ana", "2": "bia"},
            "following": {"1": "ana"},
        }
        connection.execute(
            "INSERT INTO snapshots(owner_id, username, payload) VALUES (?, ?, ?)",
            ("100", "minhaconta", json.dumps(payload)),
        )
        connection.commit()
        connection.close()
        with database(self.root) as migrated:
            accounts = list_accounts(migrated)
            snapshot = migrated.execute(
                "SELECT source, followers_count, following_count FROM snapshots"
            ).fetchone()
            members = migrated.execute(
                "SELECT account_key, direction, username FROM members ORDER BY account_key"
            ).fetchall()
        self.assertEqual(accounts[0]["owner_key"], "id:100")
        self.assertEqual(accounts[0]["username"], "minhaconta")
        self.assertEqual(snapshot["source"], "legacy")
        self.assertEqual(snapshot["followers_count"], 2)
        self.assertEqual(snapshot["following_count"], 1)
        self.assertEqual(
            {(row["account_key"], row["direction"], row["username"]) for row in members},
            {
                ("id:1", "follower", "ana"),
                ("id:2", "follower", "bia"),
                ("id:1", "following", "ana"),
            },
        )

    @unittest.skipIf(os.getuid() == 0, "root ignora permissões de diretório")
    def test_symlink_data_directory_is_rejected(self):
        target = self.root / "real"
        target.mkdir()
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            with database(link):
                pass


if __name__ == "__main__":
    unittest.main()
