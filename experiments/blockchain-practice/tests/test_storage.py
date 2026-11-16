"""SQLite atomicity and integrity tests always use temporary storage."""
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage import PracticeStore, RevisionConflict, StorageError


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = str(Path(self.folder.name) / "store.sqlite3")
        self.store = PracticeStore(self.path)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_creation_limit_preserves_previously_saved_practices(self):
        self.store.max_practices = 1
        self.store.create("kept", {"value": "saved"})
        with self.assertRaises(StorageError):
            self.store.create("extra", {"value": "rejected"})
        self.assertEqual(self.store.read("kept")["payload"], {"value": "saved"})
        self.assertIsNone(self.store.read("extra"))

    def test_lazy_creation_and_durable_round_trip(self):
        self.assertFalse(Path(self.path).exists())
        self.store.create("practice", {"version": 1, "value": "한글"})
        self.store.close()
        row = self.store.read("practice")
        self.assertEqual(row["revision"], 0)
        self.assertEqual(row["payload"], {"version": 1, "value": "한글"})
        self.store.save("practice", 0, {"version": 1, "value": "changed"})
        self.assertEqual(self.store.read("practice")["revision"], 1)

    def test_compare_and_swap_never_overwrites_newer_record(self):
        self.store.create("practice", {"value": 0})
        other = PracticeStore(self.path)
        try:
            self.store.save("practice", 0, {"value": 1})
            with self.assertRaises(RevisionConflict):
                other.save("practice", 0, {"value": 2})
            self.assertEqual(other.read("practice")["payload"], {"value": 1})
            self.assertEqual(other.read("practice")["revision"], 1)
        finally:
            other.close()

    def test_digest_rejects_modified_payload_without_overwriting_it(self):
        self.store.create("practice", {"value": 0})
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE practices SET payload=? WHERE id=?", ('{"value":999}', "practice"))
        with self.assertRaises(StorageError):
            self.store.read("practice")
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT payload FROM practices").fetchone()[0], '{"value":999}')

    def test_unknown_database_version_is_not_replaced(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA user_version=99")
            db.execute("CREATE TABLE keep_me(value)")
        with self.assertRaises(StorageError):
            self.store.read("practice")
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='keep_me'").fetchone())

    def test_memory_store_works_across_operations(self):
        store = PracticeStore(":memory:")
        try:
            store.create("practice", {"value": 0})
            store.save("practice", 0, {"value": 1})
            self.assertEqual(store.read("practice")["payload"], {"value": 1})
        finally:
            store.close()

    def test_unrecognized_existing_table_is_not_migrated_or_replaced(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE practices (id TEXT PRIMARY KEY, original TEXT)")
            db.execute("INSERT INTO practices VALUES ('keep', 'original data')")
        with self.assertRaises(StorageError):
            self.store.read("keep")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT original FROM practices").fetchone()[0], "original data")
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
