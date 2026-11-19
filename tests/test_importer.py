"""Leitura da exportação oficial: ZIP, pasta e pares de JSON."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from support import export_json, export_zip, write_export

from instafollowcheck.importer import (
    ExportError,
    import_export,
    import_export_bytes,
    import_export_uploads,
)


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_zip_with_nested_official_paths(self):
        data = export_zip(["ana", "bia"], ["ana", "caio"])
        followers, following = import_export_bytes(data, "instagram.zip")
        self.assertEqual(followers, {"name:ana": "ana", "name:bia": "bia"})
        self.assertEqual(following, {"name:ana": "ana", "name:caio": "caio"})

    def test_directory_import(self):
        write_export(self.root, ["ana"], ["ana", "bia"])
        followers, following = import_export(self.root)
        self.assertEqual(followers, {"name:ana": "ana"})
        self.assertEqual(following, {"name:ana": "ana", "name:bia": "bia"})

    def test_single_json_is_rejected_as_incomplete(self):
        data = export_json(["ana", "bia"])
        with self.assertRaises(ExportError):
            import_export_bytes(data, "followers_1.json")

    def test_two_json_uploads_are_combined(self):
        followers, following = import_export_uploads(
            [
                ("followers_1.json", export_json(["ana", "bia"])),
                ("following_1.json", export_json(["ana", "caio"])),
            ]
        )
        self.assertEqual(set(followers.values()), {"ana", "bia"})
        self.assertEqual(set(following.values()), {"ana", "caio"})

    def test_mixed_uploads_are_rejected(self):
        with self.assertRaises(ExportError):
            import_export_uploads(
                [
                    ("instagram.zip", export_zip(["ana"], ["ana"])),
                    ("followers_1.json", export_json(["ana"])),
                ]
            )

    def test_corrupted_zip_is_rejected(self):
        with self.assertRaises(ExportError):
            import_export_bytes(b"not a zip", "export.zip")

    def test_zip_without_lists_is_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("outros/arquivo.txt", "nada")
        with self.assertRaises(ExportError):
            import_export_bytes(buffer.getvalue(), "export.zip")

    def test_entry_without_username_is_rejected(self):
        with self.assertRaises(ExportError):
            import_export_uploads(
                [
                    ("followers_1.json", b'[{"title": ""}]'),
                    ("following_1.json", export_json(["ana"])),
                ]
            )

    def test_invalid_username_is_rejected(self):
        with self.assertRaises(ExportError):
            import_export_uploads(
                [
                    ("followers_1.json", b'[{"string_list_data": [{"value": "http://x"}]}]'),
                    ("following_1.json", export_json(["ana"])),
                ]
            )


if __name__ == "__main__":
    unittest.main()
