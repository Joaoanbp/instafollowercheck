"""Valida o iniciador em um ambiente isolado, com comandos externos simulados."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipIf(os.getuid() == 0, "O iniciador exige usuário sem privilégios de root")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / "iniciar.sh"
        shutil.copyfile(Path(__file__).resolve().parents[1] / "iniciar.sh", self.script)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        venv = self.root / ".venv/bin"
        venv.mkdir(parents=True)
        # Nenhuma instalação, autenticação, coleta ou alteração de cron é real.
        self.executable(self.bin / "python3", "exit 0\n")
        self.executable(self.bin / "crontab", "exit 97\n")
        self.executable(
            venv / "python",
            'if [ "$2" = instafollowcheck.browser_paths ]; then\n'
            f'  exec {shlex.quote(sys.executable)} "$@"\nfi\n'
            'if [ "$2" = instafollowcheck ]; then exit 1; fi\nexit 0\n',
        )
        self.executable(
            venv / "instaloader",
            'printf "%s\\n" "$@" > "$HOME/import-arguments"\ntouch -- "$4"\n',
        )
        self.env = {
            **os.environ,
            "HOME": str(self.root),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "XDG_CONFIG_HOME": str(self.root / ".config"),
            "CHROME_CONFIG_HOME": str(self.root / ".config"),
            "CHROME_USER_DATA_DIR": str(self.root / ".config/google-chrome"),
        }

    def executable(self, path, body):
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def run_launcher(self, *args):
        return subprocess.run(
            ["bash", str(self.script), *args],
            env=self.env, capture_output=True, text=True, timeout=10,
        )

    def test_missing_session_never_attempts_password_login(self):
        result = self.run_launcher("usuario_exemplo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("não tentará login com senha", result.stderr)
        self.assertFalse((self.root / "import-arguments").exists())

    def test_librewolf_import_is_supported_without_password_fallback(self):
        result = self.run_launcher("usuario_exemplo", "librewolf")
        arguments = (self.root / "import-arguments").read_text().splitlines()
        self.assertEqual(arguments[:2], ["--load-cookies", "librewolf"])
        self.assertNotIn("--login", arguments)
        # A coleta simulada falha: o iniciador deve parar antes de agendar.
        self.assertEqual(result.returncode, 1)
        self.assertIn("O cron não foi alterado", result.stderr)

    def test_chrome_receives_explicit_modern_cookie_file(self):
        cookies = self.root / ".config/google-chrome/Default/Network/Cookies"
        cookies.parent.mkdir(parents=True)
        cookies.touch()
        result = self.run_launcher("usuario_exemplo", "chrome")
        arguments = (self.root / "import-arguments").read_text().splitlines()
        self.assertEqual(arguments[-2:], ["--cookiefile", str(cookies)])
        self.assertEqual(result.returncode, 1)  # Coleta simulada, sem agendamento.

    def test_missing_chrome_profile_stops_before_import(self):
        result = self.run_launcher("usuario_exemplo", "chrome")
        self.assertEqual(result.returncode, 1)
        self.assertIn("chrome://version", result.stderr)
        self.assertFalse((self.root / "import-arguments").exists())
