"""Iniciador: ajuda, execução e instalação sem alterar o sistema real."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "iniciar.sh"


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.script = self.repo / "iniciar.sh"
        shutil.copyfile(SCRIPT, self.script)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.executable(self.bin / "google-chrome", "exit 0\n")
        self.python = self.repo / ".venv" / "bin" / "python"
        self.python.parent.mkdir(parents=True)
        self.env = {
            **os.environ,
            "HOME": str(self.root),
            "PATH": f"{self.bin}:/usr/bin:/bin",
        }

    def tearDown(self):
        self.temp.cleanup()

    def executable(self, path, body):
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o700)

    def run_launcher(self, *args):
        return subprocess.run(
            ["/bin/bash", str(self.script), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_help_does_not_need_environment(self):
        result = self.run_launcher("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Uso: bash iniciar.sh", result.stdout)
        self.assertFalse(self.python.exists())

    def test_executes_the_local_package(self):
        self.executable(
            self.python,
            'if [ "$1" = "-c" ]; then exit 0; fi\n'
            'if [ "$1" = "-m" ] && [ "$2" = "instafollowcheck" ]; then\n'
            '  printf "%s\\n" "$@" > "$HOME/args"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
        )
        result = self.run_launcher("serve", "--no-browser")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = (self.root / "args").read_text().splitlines()
        self.assertEqual(arguments, ["-m", "instafollowcheck", "serve", "--no-browser"])

    def test_installs_dependencies_when_missing(self):
        self.executable(
            self.python,
            'if [ "$1" = "-c" ]; then exit 1; fi\n'
            'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then\n'
            '  touch "$HOME/pip-called"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = "-m" ] && [ "$2" = "instafollowcheck" ]; then\n'
            '  printf "%s\\n" "$@" > "$HOME/args"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
        )
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "pip-called").exists())
        arguments = (self.root / "args").read_text().splitlines()
        self.assertEqual(arguments, ["-m", "instafollowcheck"])

    def test_missing_venv_explains_apt_package(self):
        shutil.rmtree(self.repo / ".venv")
        self.executable(self.bin / "python3", "exit 1\n")
        self.env["PATH"] = f"{self.bin}:/usr/bin:/bin"
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn("python3-venv", result.stderr)

    def test_downloads_playwright_browser_when_chrome_is_absent(self):
        os.unlink(self.bin / "google-chrome")
        os.symlink("/usr/bin/dirname", self.bin / "dirname")
        self.executable(self.bin / "python3", "exit 1\n")
        self.executable(
            self.python,
            'if [ "$1" = "-c" ]; then exit 0; fi\n'
            'if [ "$1" = "-m" ] && [ "$2" = "playwright" ]; then\n'
            '  : > "$HOME/playwright-called"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = "-m" ] && [ "$2" = "instafollowcheck" ]; then\n'
            '  printf "%s\\n" "$@" > "$HOME/args"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
        )
        self.env["PATH"] = str(self.bin)
        result = self.run_launcher("serve")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "playwright-called").exists())
        self.assertTrue((self.root / "args").exists())


if __name__ == "__main__":
    unittest.main()
