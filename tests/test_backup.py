import tempfile
import unittest
from pathlib import Path

from onebot_llm_bridge.backup import create_backup, restore_backup


class BackupTests(unittest.TestCase):
    def test_backup_round_trip_includes_referenced_local_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.local").write_text('PERSONA_FILE="persona.txt"\n', encoding="utf-8")
            (root / "persona.txt").write_text("persona", encoding="utf-8")
            archive = create_backup(root, root / "backup.zip")
            (root / "persona.txt").write_text("changed", encoding="utf-8")
            restored = restore_backup(root, archive)
            self.assertIn((root / "persona.txt").resolve(), restored)
            self.assertEqual((root / "persona.txt").read_text(encoding="utf-8"), "persona")

    def test_restore_rejects_path_escape(self) -> None:
        import zipfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            archive = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../outside.txt", "bad")
            self.assertEqual(restore_backup(root, archive), [])
            self.assertFalse((Path(directory) / "outside.txt").exists())
