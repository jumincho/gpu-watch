from __future__ import annotations
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server

class RestoreSchemaTests(unittest.TestCase):
    def test_restore_verifier_accepts_current_backup_and_rejects_future_schema(self):
        source = (ROOT / "scripts/restore-backup.sh").read_text(encoding="utf-8")
        begin = source.index("import sqlite3", source.index("verify_gpu_watch_database()"))
        payload = source[begin:source.index("\nPY", begin)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restore.sqlite3"
            with mock.patch.object(server, "DATA_DIR", Path(directory)), mock.patch.object(server, "BACKUP_DIR", Path(directory) / "backups"):
                server.Store(path)
            for version, expected in [(server.SCHEMA_VERSION, 0), (server.SCHEMA_VERSION + 1, 1)]:
                with sqlite3.connect(path) as connection:
                    connection.execute("update schema_meta set value=? where key='schema_version'", (str(version),))
                connection.execute("pragma wal_checkpoint(truncate)")
                connection.execute("pragma journal_mode=delete")
                connection.close()
                result = subprocess.run([sys.executable, "-B", "-c", payload, str(path), str(ROOT)], capture_output=True, text=True, timeout=15)
                with self.subTest(schema=version):
                    self.assertEqual(result.returncode, expected, result.stderr)
                    if expected:
                        self.assertIn("unsupported GPU Watch database schema_version", result.stderr)
if __name__ == "__main__":
    unittest.main()
