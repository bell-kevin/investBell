from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from investbell import backup


def files_under(root):
    return sorted(str(path.relative_to(root)) for path in Path(root).rglob("*") if path.is_file())


def rows(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("select value from items order by value").fetchall()


class BackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / "data"
        self.staging = Path(temporary.name) / "staging"
        (self.data / "reports").mkdir(parents=True)
        (self.data / "models").mkdir()
        (self.data / "cache" / "py-yfinance").mkdir(parents=True)
        (self.data / "reports" / "2026-09-24-SPY.json").write_text('{"report": 1}')
        (self.data / "models" / "model.json").write_text('{"model": 1}')
        (self.data / "models" / ".training.lock").write_text("")
        (self.data / "cache" / "py-yfinance" / "cookies.db").write_text("cache")
        (self.data / "paper.plan.json").write_text('{"plan": 1}')

    def database(self, name, *, wal=False):
        connection = sqlite3.connect(self.data / name)
        if wal:
            # Keep committed rows in the WAL file, as a running writer would.
            connection.execute("pragma journal_mode = wal")
            connection.execute("pragma wal_autocheckpoint = 0")
        connection.execute("create table items (value integer)")
        connection.executemany("insert into items values (?)", [(1,), (2,)])
        connection.commit()
        return connection

    def test_copies_data_with_consistent_databases_and_without_cache_or_journals(self):
        with closing(self.database("market.sqlite3", wal=True)), closing(self.database("paper.sqlite3")):
            self.assertTrue((self.data / "market.sqlite3-wal").exists())
            databases = backup.stage(self.data, self.staging)

        self.assertEqual(databases, [Path("market.sqlite3"), Path("paper.sqlite3")])
        self.assertEqual(files_under(self.staging), [
            "market.sqlite3", "models/.training.lock", "models/model.json", "paper.plan.json",
            "paper.sqlite3", "reports/2026-09-24-SPY.json"])
        for name in ("reports/2026-09-24-SPY.json", "models/model.json", "paper.plan.json"):
            self.assertEqual((self.staging / name).read_bytes(), (self.data / name).read_bytes())
        for name in databases:
            self.assertEqual(rows(self.staging / name), [(1,), (2,)])

    def test_replaces_an_earlier_staging_copy(self):
        self.database("market.sqlite3").close()
        self.staging.mkdir()
        (self.staging / "deleted-since.json").write_text("old")
        backup.stage(self.data, self.staging)
        self.assertFalse((self.staging / "deleted-since.json").exists())
        self.assertTrue((self.staging / "market.sqlite3").exists())

    def test_refuses_a_missing_directory_or_one_without_a_database(self):
        with self.assertRaises(FileNotFoundError):
            backup.stage(self.data / "missing", self.staging)
        with self.assertRaises(FileNotFoundError):
            backup.stage(self.data, self.staging)

    def test_command_line_logs_the_databases(self):
        self.database("market.sqlite3").close()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(backup.main([str(self.data), str(self.staging)]), 0)
        event = json.loads(output.getvalue())
        self.assertEqual((event["event"], event["databases"]), ("backup_staged", ["market.sqlite3"]))


if __name__ == "__main__":
    unittest.main()
