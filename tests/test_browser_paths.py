import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from instafollowcheck.browser_paths import resolve_cookie_file
from instafollowcheck.models import MonitorError


class BrowserPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict("os.environ", {"HOME": str(self.root)}, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def make_file(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_modern_chrome_path(self):
        cookies = self.make_file(".config/google-chrome/Default/Network/Cookies")
        self.assertEqual(resolve_cookie_file("chrome"), cookies)
        self.assertEqual(resolve_cookie_file("chrome", cookies.parents[1]), cookies)

    def test_legacy_profile_with_spaces(self):
        cookies = self.make_file("custom/Profile 2/Cookies")
        self.assertEqual(resolve_cookie_file("chrome", cookies.parent), cookies)
        self.assertEqual(resolve_cookie_file("chrome", cookies), cookies)

    def test_flatpak_chrome(self):
        cookies = self.make_file(
            ".var/app/com.google.Chrome/config/google-chrome/Default/Network/Cookies"
        )
        self.assertEqual(resolve_cookie_file("chrome"), cookies)

    def test_multiple_profiles_require_explicit_choice(self):
        self.make_file(".config/google-chrome/Default/Cookies")
        self.make_file(".config/google-chrome/Profile 1/Cookies")
        with self.assertRaisesRegex(MonitorError, "vários perfis"):
            resolve_cookie_file("chrome")

    def test_missing_cookie_file(self):
        with self.assertRaisesRegex(MonitorError, "chrome://version"):
            resolve_cookie_file("chrome")
        with self.assertRaises(MonitorError):
            resolve_cookie_file("chrome", self.root)

    def test_custom_user_data_directory(self):
        cookies = self.make_file("alternative/Default/Cookies")
        with patch.dict("os.environ", {"CHROME_USER_DATA_DIR": str(cookies.parents[1])}):
            self.assertEqual(resolve_cookie_file("chrome"), cookies)

    def test_windows_chrome_profile_has_actionable_error_on_linux(self):
        with patch("instafollowcheck.browser_paths.sys.platform", "linux"):
            for location in (
                Path(r"C:\Users\Usuario\AppData\Local\Google\Chrome\User Data\Default"),
                Path("/mnt/c/Users/Usuario/AppData/Local/Google/Chrome/User Data/Default"),
            ):
                with self.assertRaisesRegex(MonitorError, "Windows.*Linux/WSL"):
                    resolve_cookie_file("chrome", location)
