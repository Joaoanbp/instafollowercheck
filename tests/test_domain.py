"""Testes de comparação, janela de 10 dias e união de chaves de exportação."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import NOW, iso_days_ago

from instafollowcheck.db import database, insert_export_snapshot, save_snapshot
from instafollowcheck.domain import build_diff

OWNER = "id:100"


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(
        self,
        days: float,
        followers: dict[str, str],
        following: dict[str, str],
        *,
        source: str = "browser",
        note: str = "",
    ) -> int:
        with database(self.root) as connection:
            return save_snapshot(
                connection,
                owner_key=OWNER,
                owner_id="100",
                username="minhaconta",
                captured_at=iso_days_ago(days),
                source=source,
                followers=followers,
                following=following,
                note=note,
            )

    def diff(self, window: int = 10):
        with database(self.root) as connection:
            return build_diff(connection, OWNER, window, now=NOW)

    def test_current_relations(self):
        self.snapshot(
            0,
            {"id:1": "ana", "id:2": "bia"},
            {"id:1": "ana", "id:3": "caio"},
        )
        result = self.diff()
        self.assertEqual([person.username for person in result.mutual], ["ana"])
        self.assertEqual([person.username for person in result.not_following_back], ["caio"])
        self.assertEqual([person.username for person in result.not_followed_back], ["bia"])
        self.assertEqual(result.summary()["followers"], 2)
        self.assertEqual(result.summary()["following"], 2)
        self.assertIn("Primeira coleta", " ".join(result.warnings))

    def test_lost_relations_and_soft_block(self):
        self.snapshot(
            9,
            {"id:1": "ana", "id:2": "bia", "id:3": "carla"},
            {"id:1": "ana", "id:3": "carla"},
        )
        self.snapshot(0, {"id:3": "carla"}, {"id:2": "bia", "id:3": "carla"})
        result = self.diff()
        self.assertEqual({person.username for person in result.lost_followers}, {"ana", "bia"})
        self.assertEqual({person.username for person in result.lost_following}, {"ana"})
        self.assertEqual({person.username for person in result.possible_soft_blocks}, {"ana"})
        self.assertEqual({person.username for person in result.new_following}, {"bia"})
        self.assertLessEqual(result.effective_days, 10)

    def test_baseline_inside_window_avoids_old_new_followers(self):
        self.snapshot(30, {"id:1": "ana", "id:2": "antigo"}, {"id:1": "ana"})
        self.snapshot(9, {"id:1": "ana"}, {"id:1": "ana"})
        self.snapshot(0, {"id:1": "ana", "id:4": "novo"}, {"id:1": "ana"})
        result = self.diff()
        self.assertEqual({person.username for person in result.new_followers}, {"novo"})
        self.assertEqual(result.lost_followers, [])
        self.assertIn("histórico começa", " ".join(result.warnings))

    def test_temporary_follower_is_detected_inside_window(self):
        self.snapshot(9, {"id:1": "ana"}, {"id:1": "ana"})
        self.snapshot(4, {"id:1": "ana", "id:2": "passou"}, {"id:1": "ana"})
        self.snapshot(0, {"id:1": "ana"}, {"id:1": "ana"})
        result = self.diff()
        self.assertEqual({person.username for person in result.lost_followers}, {"passou"})

    def test_export_then_browser_merges_same_account(self):
        with database(self.root) as connection:
            insert_export_snapshot(
                connection,
                username="minhaconta",
                followers={"name:ana": "ana", "name:bia": "bia"},
                following={"name:ana": "ana"},
                captured_at=iso_days_ago(5),
            )
            accounts = connection.execute(
                "SELECT owner_key FROM accounts ORDER BY owner_key"
            ).fetchall()
            self.assertEqual([row["owner_key"] for row in accounts], ["name:minhaconta"])
        self.snapshot(0, {"id:1": "ana", "id:2": "bia"}, {"id:1": "ana"})
        with database(self.root) as connection:
            accounts = connection.execute("SELECT owner_key FROM accounts").fetchall()
            snapshots = connection.execute(
                "SELECT owner_key FROM snapshots ORDER BY captured_ts"
            ).fetchall()
        self.assertEqual([row["owner_key"] for row in accounts], [OWNER])
        self.assertEqual({row["owner_key"] for row in snapshots}, {OWNER})
        result = self.diff()
        self.assertEqual(result.lost_followers, [])
        self.assertEqual(result.new_followers, [])

    def test_browser_then_export_uses_stable_ids(self):
        self.snapshot(5, {"id:1": "ana", "id:2": "bia"}, {"id:1": "ana"})
        with database(self.root) as connection:
            insert_export_snapshot(
                connection,
                username="minhaconta",
                followers={"name:ana": "ana", "name:bia": "bia"},
                following={"name:ana": "ana"},
                owner_id="100",
                captured_at=iso_days_ago(0),
            )
        result = self.diff()
        self.assertEqual(result.lost_followers, [])
        self.assertEqual(result.new_followers, [])
        self.assertEqual(len(result.followers), 2)

    def test_username_reuse_with_different_ids_is_not_merge(self):
        self.snapshot(5, {"id:1": "ana"}, {"id:1": "ana"})
        self.snapshot(0, {"id:2": "ana"}, {"id:2": "ana"})
        result = self.diff()
        self.assertEqual(result.lost_followers[0].key, "id:1")
        self.assertEqual(result.new_followers[0].key, "id:2")

    def test_old_baseline_outside_window_warns(self):
        self.snapshot(20, {"id:1": "ana", "id:2": "bia"}, {"id:1": "ana"})
        self.snapshot(0, {"id:1": "ana"}, {"id:1": "ana"})
        result = self.diff()
        self.assertEqual({person.username for person in result.lost_followers}, {"bia"})
        self.assertIn("Não havia coleta dentro", " ".join(result.warnings))
        self.assertGreater(result.effective_days, 10)


if __name__ == "__main__":
    unittest.main()
