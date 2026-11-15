"""Testes offline: nenhuma sessão real ou requisição ao Instagram é utilizada."""

import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from instafollowcheck.cli import main
from instafollowcheck.collector import collect_snapshot, username_arg, validate_session
from instafollowcheck.models import MonitorError, Snapshot, compare
from instafollowcheck.report import render_report
from instafollowcheck.storage import database, execution_lock, export_report, latest, save


def snapshot(followers=None, following=None, date="2026-11-07T08:15:00-03:00", owner="100"):
    return Snapshot(owner, "minhaconta", date, followers or {}, following or {})


class ComparisonTests(unittest.TestCase):
    def test_categories_and_suggestions(self):
        current = snapshot({"1": "ana", "2": "bia"}, {"1": "ana", "3": "caio"})
        groups = compare(current, None)
        self.assertEqual(groups["mutual"], {"1"})
        self.assertEqual(groups["not_following_back"], {"3"})
        self.assertEqual(groups["not_followed_back"], {"2"})
        report = render_report(current, None)
        self.assertIn("linha de base", report)
        self.assertIn("https://www.instagram.com/caio/", report)
        self.assertIn("Quem segue você: 2", report)

    def test_lost_and_possible_soft_blocks_over_actual_interval(self):
        old = snapshot({"1": "ana", "2": "bia"}, {"2": "bia", "3": "caio"},
                       date="2026-11-01T08:15:00-03:00")
        current = snapshot({}, {"3": "caio"})
        groups = compare(current, old)
        self.assertEqual(groups["lost_followers"], {"1", "2"})
        self.assertEqual(groups["stopped_following"], {"2"})
        self.assertEqual(groups["possible_soft_blocks"], {"2"})
        report = render_report(current, old)
        self.assertIn(old.captured_at, report)
        self.assertIn("- @bia (ID 2)", report)
        self.assertIn("apenas hipótese", report)

    def test_rename_does_not_count_as_loss(self):
        self.assertEqual(
            compare(snapshot({"1": "novo"}), snapshot({"1": "antigo"}))["lost_followers"],
            set(),
        )

    def test_username_reuse_is_different_account(self):
        self.assertEqual(
            compare(snapshot({"2": "ana"}), snapshot({"1": "ana"}))["lost_followers"], {"1"}
        )

    def test_reject_different_owner(self):
        with self.assertRaises(MonitorError):
            compare(snapshot(), snapshot(owner="999"))

    def test_empty_baseline(self):
        self.assertTrue(all(not group for group in compare(snapshot(), None).values()))
        self.assertIn("(nenhuma sugestão)", render_report(snapshot(), None))


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.session = self.root / "session-test"
        self.session.write_bytes(b"test fixture, not a real session")
        self.session.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_session_permissions_missing_and_symlink(self):
        validate_session(self.session)
        self.session.chmod(0o644)
        with self.assertRaises(MonitorError):
            validate_session(self.session)
        link = self.root / "link"
        link.symlink_to(self.session)
        with self.assertRaises(MonitorError):
            validate_session(link)
        with self.assertRaises(MonitorError):
            validate_session(self.root / "missing")

    def test_lock_prevents_overlapping_runs(self):
        with execution_lock(self.root), self.assertRaises(MonitorError):
            with execution_lock(self.root):
                self.fail("Não deveria adquirir o segundo lock")

    def test_history_isolated_and_report_recoverable(self):
        path = self.root / "history.sqlite3"
        with database(path) as connection:
            first = snapshot({"1": "ana"})
            first_id = save(connection, first, render_report(first, None))
            save(connection, snapshot(owner="999"), "outra conta")
            stored, report, row_id = latest(connection, owner_id="100")
            self.assertEqual(stored, first)
            self.assertEqual(row_id, first_id)
            self.assertIsNone(latest(connection, owner_id="absent"))
            target = export_report(self.root / "reports", stored, report, row_id)
            self.assertEqual(target.read_text(), report)
            self.assertIn("2026-11-07", target.name)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cli_success_and_offline_reexport(self):
        args = ["--usuario", "minhaconta", "--data-dir", str(self.root), "--quiet"]
        with patch("instafollowcheck.cli.collect_snapshot", return_value=snapshot()):
            self.assertEqual(main(args), 0)
        report = next((self.root / "reports").glob("*.txt"))
        original = report.read_text()
        report.unlink()
        with patch("instafollowcheck.cli.collect_snapshot") as collector:
            self.assertEqual(main(args + ["--report-only"]), 0)
            collector.assert_not_called()
        self.assertEqual(report.read_text(), original)

    def test_failed_collection_preserves_previous_snapshot(self):
        args = ["--usuario", "minhaconta", "--data-dir", str(self.root), "--quiet"]
        with patch("instafollowcheck.cli.collect_snapshot", return_value=snapshot()):
            self.assertEqual(main(args), 0)
        with patch("instafollowcheck.cli.collect_snapshot", side_effect=MonitorError("falha")):
            with redirect_stderr(StringIO()):
                self.assertEqual(main(args), 1)
        with database(self.root / "history.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)

    def test_export_failure_can_be_recovered(self):
        args = ["--usuario", "minhaconta", "--data-dir", str(self.root), "--quiet"]
        with patch("instafollowcheck.cli.collect_snapshot", return_value=snapshot()):
            with patch("instafollowcheck.cli.export_report", side_effect=OSError("disk")):
                with redirect_stderr(StringIO()) as error:
                    self.assertEqual(main(args), 1)
        self.assertIn("--report-only", error.getvalue())
        self.assertEqual(main(args + ["--report-only"]), 0)

    def fake_api(self):
        class APIError(Exception):
            pass

        class LoginError(APIError):
            pass

        class AbortError(Exception):
            pass

        profile = MagicMock(userid=100, followers=1, followees=1)
        profile.get_followers.return_value = [SimpleNamespace(userid=1, username="ana")]
        profile.get_followees.return_value = [SimpleNamespace(userid=2, username="bia")]
        api = SimpleNamespace(
            Instaloader=MagicMock(), Profile=MagicMock(),
            exceptions=SimpleNamespace(
                InstaloaderException=APIError, LoginRequiredException=LoginError,
                AbortDownloadException=AbortError,
            ),
        )
        api.Instaloader.return_value.test_login.return_value = "minhaconta"
        api.Profile.from_username.return_value = profile
        api.Profile.from_id.return_value = profile
        return api, profile

    def test_complete_collection_and_no_password_login(self):
        api, _ = self.fake_api()
        current = collect_snapshot("minhaconta", self.session, api)
        self.assertEqual(current.followers, {"1": "ana"})
        self.assertEqual(current.following, {"2": "bia"})
        loader = api.Instaloader.return_value
        loader.login.assert_not_called()
        loader.close.assert_called_once()

    def test_expired_and_wrong_account_sessions(self):
        for authenticated in (None, "outraconta"):
            api, _ = self.fake_api()
            api.Instaloader.return_value.test_login.return_value = authenticated
            with self.assertRaises(MonitorError):
                collect_snapshot("minhaconta", self.session, api)
            api.Profile.from_username.assert_not_called()

    def test_partial_list_and_midstream_failure(self):
        api, profile = self.fake_api()
        profile.followers = 2
        with self.assertRaises(MonitorError):
            collect_snapshot("minhaconta", self.session, api)
        api, profile = self.fake_api()

        def incomplete():
            yield SimpleNamespace(userid=1, username="ana")
            raise api.exceptions.InstaloaderException("HTTP response with private details")

        profile.get_followers.return_value = incomplete()
        with self.assertRaises(MonitorError) as caught:
            collect_snapshot("minhaconta", self.session, api)
        self.assertNotIn("private details", str(caught.exception))

    def test_rate_limit_aborts_and_closes(self):
        api, profile = self.fake_api()
        profile.get_followers.side_effect = api.exceptions.AbortDownloadException("429")
        with self.assertRaises(MonitorError):
            collect_snapshot("minhaconta", self.session, api)
        api.Instaloader.return_value.close.assert_called_once()

    def test_changed_counts_discard_collection(self):
        api, _ = self.fake_api()
        api.Profile.from_id.return_value = SimpleNamespace(followers=2, followees=1)
        with self.assertRaises(MonitorError):
            collect_snapshot("minhaconta", self.session, api)

    def test_input_validation(self):
        self.assertEqual(username_arg("@MinhaConta"), "minhaconta")
        for invalid in ("../session", "https://instagram.com/x", "", "a\nb"):
            with self.assertRaises(ValueError):
                username_arg(invalid)


if __name__ == "__main__":
    unittest.main()
