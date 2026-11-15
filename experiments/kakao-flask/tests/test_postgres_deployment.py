"""Real PostgreSQL contracts; each test owns three disposable random schemas.

Set TEST_POSTGRES_URL to an explicitly designated test database. No hosted
database is discovered automatically, and no production schema is ever dropped.
The production workflow supplies an isolated PostgreSQL 18 container.
"""

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
import uuid


TEST_URL = os.environ.get("TEST_POSTGRES_URL", "").strip()
ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/api/education"
TEACHER_SECRET = "pg-test-" + "x" * 40


@unittest.skipUnless(TEST_URL, "TEST_POSTGRES_URL is not configured")
class PostgresDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # app.py also exports a default app. Its first import must not open a
        # developer's normal SQLite files or attach to a deployed PostgreSQL DB.
        cls.import_folder = tempfile.TemporaryDirectory(prefix="team-pg-import-")
        cls.addClassCleanup(cls.import_folder.cleanup)
        sys.path.insert(0, str(ROOT))
        with patch.dict(os.environ, {
            "TEAM_DATABASE_URL": "", "TEAM_EDUCATION_RUNNER": "0",
            "KAKAO_PRACTICE_DB": str(Path(cls.import_folder.name) / "basic.sqlite3"),
            "TEAM_EDUCATION_DB": str(Path(cls.import_folder.name) / "education.sqlite3"),
        }):
            already_imported = "app" in sys.modules
            cls.app_module = importlib.import_module("app")
        if not already_imported:
            cls.close_app(cls.app_module.app)
        cls.psycopg = importlib.import_module("psycopg")
        cls.sql = importlib.import_module("psycopg.sql")
        cls.Backend = importlib.import_module("postgres_backend").PostgresBackend
        cls.BackendError = importlib.import_module("postgres_backend").PostgresBackendError
        cls.BasicStore = importlib.import_module("postgres_basic_store").PostgresBasicStore
        cls.EducationStore = importlib.import_module("postgres_education_store").PostgresEducationStore
        cls.StorageError = importlib.import_module("storage").StorageError
        cls.RevisionConflict = importlib.import_module("storage").RevisionConflict
        cls.LabError = importlib.import_module("education_engine.core.errors").LabError
        cls.owner_cookie = importlib.import_module("education_api").OWNER_COOKIE

    @staticmethod
    def close_app(app):
        runtime = app.extensions.get("education")
        if runtime is not None:
            runtime.close()
        basic = app.extensions.get("practice_store")
        if basic is not None:
            basic.close()
        backend = app.extensions.get("postgres_backend")
        if backend is not None:
            backend.close()

    def setUp(self):
        self.schema_prefix = "test_" + uuid.uuid4().hex[:12]
        self.apps = []
        self.admin = None
        # Register cleanup before connecting or initializing an app: failed
        # setup still closes resources and drops only this test's known schemas.
        self.addCleanup(self.cleanup)
        self.admin = self.psycopg.connect(TEST_URL, autocommit=True, connect_timeout=10)
        self.app, self.client = self.make_app()
        self.backend = self.app.extensions["postgres_backend"]
        self.basic = self.app.extensions["practice_store"]
        self.education = self.app.extensions["education"].store
        self.assertIs(self.basic.backend, self.education.backend)

    def cleanup(self):
        try:
            for app in reversed(self.apps):
                self.close_app(app)
        finally:
            if self.admin is not None:
                try:
                    for kind in ("education", "basic", "control"):
                        schema = self.schema_prefix + "_" + kind
                        self.admin.execute(self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                            self.sql.Identifier(schema)))
                finally:
                    self.admin.close()

    def make_app(self, cookies=None, **config):
        settings = {
            "TESTING": True, "EDUCATION_ENABLED": True,
            "EDUCATION_RUNNER_ENABLED": False,
            "TEAM_DATABASE_URL": TEST_URL, "TEAM_DATABASE_PREFIX": self.schema_prefix,
            "TEAM_TEACHER_SECRET": TEACHER_SECRET,
            # Any accidental fallback remains in memory, not a user's database.
            "DATABASE": ":memory:", "EDUCATION_DATABASE": ":memory:",
        }
        settings.update(config)
        app = self.app_module.create_app(settings)
        self.apps.append(app)
        client = app.test_client()
        for name, value in (cookies or {}).items():
            client.set_cookie(name, value)
        return app, client

    def call(self, method, path, body=None, *, status=200, client=None):
        response = (client or self.client).open(path, method=method, json=body)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        return response.get_json() if response.data else None

    def lab(self, client=None):
        return self.call("POST", PREFIX + "/labs", {"mode": "example", "difficulty": 1},
                         status=201, client=client)["lab"]

    def cookies(self, client=None):
        client = client or self.client
        names = (self.owner_cookie, self.app_module.COOKIE_NAME)
        return {name: cookie.value for name in names if (cookie := client.get_cookie(name))}

    def test_basic_create_cas_and_literal_unicode_archive_queries(self):
        self.basic.create("cas-record", {"value": 0, "name": "기초 %_"})
        self.assertEqual(self.basic.read("cas-record")["revision"], 0)
        self.basic.save("cas-record", 0, {"value": 1})
        with self.assertRaises(self.RevisionConflict):
            self.basic.save("cas-record", 0, {"value": 999})
        self.assertEqual(self.basic.read("cas-record")["payload"], {"value": 1})

        labs = [self.lab() for _ in range(3)]
        titles = ("PG%_한글", "PGXY한글", "다른 실습")
        for index, (lab, title) in enumerate(zip(labs, titles)):
            practice = lab["storage"]["id"]
            self.call("POST", PREFIX + f"/practices/{practice}/rename", {"title": title})
            self.call("POST", PREFIX + f"/practices/{practice}/save")
            with self.backend.transaction(write=True) as connection:
                connection.execute(f"UPDATE {self.education.practices} SET updated=%s WHERE id=%s",
                                   (f"2026-11-16T03:00:0{index}.123456+00:00", practice))
        result = self.call("GET", PREFIX + "/practices?" + urlencode({"q": "pg%_한글"}))
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["practices"][0]["id"], labs[0]["storage"]["id"])
        ranged = self.call("GET", PREFIX + "/practices?" + urlencode({
            "updatedFrom": "2026-11-16T12:00:00.123456+09:00",
            "updatedUntil": "2026-11-16T12:00:00.123457+09:00",
        }))
        self.assertEqual([item["id"] for item in ranged["practices"]], [labs[0]["storage"]["id"]])
        page = self.call("GET", PREFIX + "/practices?limit=1&offset=1")
        self.assertEqual(page["total"], 3)
        self.assertEqual([item["id"] for item in page["practices"]], [labs[1]["storage"]["id"]])

    def test_restart_preserves_basic_wallet_owner_archive_and_private_key(self):
        basic = self.call("GET", "/api/state")
        wallet = self.call("POST", "/api/wallets", {"name": "PG 복원", "expected_revision": basic["revision"]})
        lab = self.lab()
        practice = lab["storage"]["id"]
        address = lab["wallets"][0]["address"]
        key = self.call("GET", PREFIX + f"/labs/{lab['labId']}/wallets/{address}/private-key")["privateKey"]
        self.call("POST", PREFIX + f"/practices/{practice}/rename", {"title": "재시작 기록"})
        self.call("POST", PREFIX + f"/practices/{practice}/save")
        cookies = self.cookies()
        self.close_app(self.app)
        replacement, owner = self.make_app(cookies)
        restored = self.call("GET", "/api/state", client=owner)
        self.assertEqual(restored["practice"]["id"], basic["practice"]["id"])
        wallets = self.call("GET", "/api/wallets", client=owner)
        self.assertEqual(next(item for item in wallets if item["address"] == wallet["address"])["name"], "PG 복원")
        listing = self.call("GET", PREFIX + "/practices", client=owner)
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["practices"][0]["title"], "재시작 기록")
        resumed = self.call("POST", PREFIX + f"/practices/{practice}/resume", {}, client=owner)["lab"]
        self.assertNotEqual(resumed["labId"], lab["labId"])
        self.assertEqual(resumed["wallets"], lab["wallets"])
        self.assertEqual(self.call("GET", PREFIX + f"/labs/{resumed['labId']}/wallets/{address}/private-key",
                                   client=owner)["privateKey"], key)
        stranger = replacement.test_client()
        self.assertEqual(self.call("GET", PREFIX + "/practices", client=stranger)["total"], 0)
        for method, path, body in (
            ("GET", f"/practices/{practice}", None),
            ("POST", f"/practices/{practice}/resume", {}),
            ("GET", f"/labs/{resumed['labId']}/wallets/{address}/private-key", None),
        ):
            self.call(method, PREFIX + path, body, status=404, client=stranger)
        self.call("DELETE", PREFIX + f"/labs/{resumed['labId']}", status=204, client=owner)
        self.call("DELETE", PREFIX + f"/practices/{practice}", client=owner)
        self.assertEqual(self.call("GET", PREFIX + "/practices", client=owner)["total"], 0)

    def test_new_deployment_fences_both_old_writers_without_overwriting_new_data(self):
        basic = self.call("GET", "/api/state")
        lab = self.lab()
        practice = lab["storage"]["id"]
        before = self.call("GET", PREFIX + f"/labs/{lab['labId']}")["lab"]
        replacement, new_client = self.make_app(self.cookies())
        self.call("POST", "/api/wallets", {"name": "새 배포 지갑"}, client=new_client)
        resumed = self.call("POST", PREFIX + f"/practices/{practice}/resume", {}, client=new_client)["lab"]
        self.call("POST", PREFIX + f"/labs/{resumed['labId']}/wallets", client=new_client)
        current_store = replacement.extensions["education"].store
        committed = current_store.read(practice, "personal")
        # Omit an outdated client revision so the request reaches the writer
        # fence after PracticeManager refreshes the newer committed revision.
        rejected = self.call("POST", "/api/wallets", {"name": "이전 배포 지갑"}, status=503)
        self.assertEqual(rejected["code"], "STORAGE_ERROR")
        rejected = self.call("POST", PREFIX + f"/labs/{lab['labId']}/wallets", status=503)
        self.assertEqual(rejected["error"]["code"], "SAVE_FAILED")
        old = self.call("GET", PREFIX + f"/labs/{lab['labId']}")["lab"]
        self.assertEqual(old["wallets"], before["wallets"])
        self.assertEqual(old["revision"], before["revision"])
        self.assertEqual(current_store.read(practice, "personal"), committed)
        names = {item["name"] for item in self.call("GET", "/api/wallets", client=new_client)}
        self.assertIn("새 배포 지갑", names)
        self.assertNotIn("이전 배포 지갑", names)
        # Reconnecting an old connection must not reclaim the deployment token.
        self.backend._connection.close()
        self.assertIsNotNone(self.basic.read(basic["practice"]["id"]))
        with self.assertRaises(self.StorageError):
            self.basic.save(basic["practice"]["id"], 1, {"stale": True})

    def test_sql_failure_rolls_back_checkpoint_events_and_http_memory(self):
        lab = self.lab()
        practice = lab["storage"]["id"]
        original = self.education.read(practice, "personal")
        with self.backend.transaction(write=True) as connection:
            connection.execute(f"ALTER TABLE {self.education.events} ADD CONSTRAINT reject_probe_event "
                               "CHECK (strpos(data, 'pg_injected_failure') = 0)")
        changed = {**original["checkpoint"], "attempted": "must roll back"}
        with self.assertRaises(self.LabError) as failure:
            self.education.save(practice, "personal", changed, {"attempted": True}, [
                {"at": "2026-11-16T00:00:00+00:00", "event": "would insert first"},
                {"at": "2026-11-16T00:00:01+00:00", "event": "pg_injected_failure"},
            ])
        self.assertEqual((failure.exception.code, failure.exception.status), ("SAVE_FAILED", 503))
        self.assertEqual(self.education.read(practice, "personal"), original)
        with self.backend.transaction(write=True) as connection:
            connection.execute(f"ALTER TABLE {self.education.practices} ADD CONSTRAINT two_wallet_limit "
                               "CHECK (jsonb_array_length(snapshot::jsonb->'wallets') <= 2)")
        before = self.call("GET", PREFIX + f"/labs/{lab['labId']}")["lab"]
        self.call("POST", PREFIX + f"/labs/{lab['labId']}/wallets", status=503)
        after = self.call("GET", PREFIX + f"/labs/{lab['labId']}")["lab"]
        self.assertEqual(after["wallets"], before["wallets"])
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(self.education.read(practice, "personal"), original)
        with self.backend.transaction(write=True) as connection:
            connection.execute(f"ALTER TABLE {self.education.practices} DROP CONSTRAINT two_wallet_limit")
        success = self.call("POST", PREFIX + f"/labs/{lab['labId']}/wallets")
        self.assertEqual(len(success["lab"]["wallets"]), 3)

    def test_failed_deferred_startup_preserves_writer_and_read_only_cannot_bypass_fence(self):
        self.basic.create("kept-writer", {"value": 1})
        with self.backend.transaction(write=True) as connection:
            connection.execute(f"ALTER TABLE {self.education.events} ADD COLUMN incompatible TEXT")
        deferred = self.Backend(TEST_URL, prefix=self.schema_prefix, defer_claim=True)
        self.addCleanup(deferred.close)
        with self.assertRaises(self.LabError):
            self.EducationStore(deferred)
        deferred.close()
        # Failed initialization must not retire the still-healthy deployment.
        self.basic.save("kept-writer", 0, {"value": 2})
        with self.backend.transaction(write=True) as connection:
            connection.execute(f"ALTER TABLE {self.education.events} DROP COLUMN incompatible")
        replacement, _ = self.make_app()
        self.backend.activate()  # Already activated old writers may not reclaim.
        with self.assertRaises(self.StorageError):
            self.basic.save("kept-writer", 1, {"value": 999})
        with self.assertRaises(self.BackendError):
            with self.backend.transaction() as connection:
                connection.execute(f"UPDATE {self.basic._table} SET revision=99 WHERE id=%s", ("kept-writer",))
        self.assertEqual(replacement.extensions["practice_store"].read("kept-writer")["payload"], {"value": 2})
        self.assertEqual(replacement.extensions["practice_store"].read("kept-writer")["revision"], 1)

    def test_incompatible_existing_tables_are_rejected_without_replacing_records(self):
        self.basic.create("keep-basic", {"original": "보존"})
        lab = self.lab()
        practice = lab["storage"]["id"]
        original = self.education.read(practice, "personal")
        with self.backend.transaction(write=True) as connection:
            connection.execute(f"ALTER TABLE {self.basic._table} ADD COLUMN original_extra TEXT")
            connection.execute(f"UPDATE {self.basic._table} SET original_extra='preserved'")
        with self.assertRaises(self.StorageError):
            self.BasicStore(self.backend)
        with self.backend.transaction() as connection:
            self.assertEqual(connection.execute(f"SELECT original_extra FROM {self.basic._table} WHERE id=%s",
                                                ("keep-basic",)).fetchone()["original_extra"], "preserved")
        self.assertEqual(self.basic.read("keep-basic")["payload"], {"original": "보존"})
        with self.backend.transaction(write=True) as connection:
            connection.execute(f"ALTER TABLE {self.education.events} ADD COLUMN original_extra TEXT")
        with self.assertRaises(self.LabError) as failure:
            self.EducationStore(self.backend)
        self.assertEqual((failure.exception.code, failure.exception.status), ("SAVE_FAILED", 503))
        self.assertEqual(self.education.read(practice, "personal"), original)
        with self.backend.transaction() as connection:
            columns = connection.execute("SELECT column_name FROM information_schema.columns "
                                         "WHERE table_schema=%s AND table_name='events'",
                                         (self.backend.schema_name("education"),)).fetchall()
        self.assertIn("original_extra", {row["column_name"] for row in columns})

    def test_basic_owner_total_and_event_quotas_keep_existing_records_readable(self):
        basic = self.call("GET", "/api/state")
        self.basic.max_practices = 1
        self.call("GET", "/api/state", status=503, client=self.app.test_client())
        self.assertEqual(self.call("GET", "/api/state")["practice"]["id"], basic["practice"]["id"])
        self.app.config["EDUCATION_MAX_PRACTICES_PER_OWNER"] = 1
        first = self.lab()
        self.call("POST", PREFIX + "/labs", {"mode": "manual"}, status=429)
        second = self.lab(self.app.test_client())
        self.app.config["EDUCATION_MAX_PRACTICES"] = 2
        self.call("POST", PREFIX + "/labs", {"mode": "manual"}, status=429, client=self.app.test_client())
        self.call("POST", PREFIX + "/classroom/rooms", {"secret": TEACHER_SECRET, "name": "교사"}, status=429)
        self.app.config["EDUCATION_MAX_PERSONAL_EVENTS"] = 1
        self.assertGreaterEqual(self.education.event_count(first["storage"]["id"]), 1)
        rejected = self.call("POST", PREFIX + f"/labs/{first['labId']}/wallets", status=409)
        self.assertEqual(rejected["error"]["code"], "EVENT_LIMIT")
        self.call("GET", PREFIX + f"/labs/{first['labId']}")
        self.call("POST", PREFIX + f"/practices/{first['storage']['id']}/save")
        self.assertEqual(self.education.total_count(), 2)
        self.assertEqual(self.education.read(second["storage"]["id"], "personal")["id"], second["storage"]["id"])

    def test_text_digests_reject_tampering_and_preserve_original_corrupt_bytes(self):
        self.basic.create("digest-basic", {"unicode": "한글 %_"})
        lab = self.lab()
        practice = lab["storage"]["id"]
        item = self.education.read(practice, "personal")
        checkpoint = {**item["checkpoint"], "검증": "한글 %_"}
        self.education.save(practice, "personal", checkpoint, item["snapshot"], [])
        with self.backend.transaction() as connection:
            raw = connection.execute(f"SELECT checkpoint,digest,pg_typeof(checkpoint)::text AS kind "
                                     f"FROM {self.education.practices} WHERE id=%s", (practice,)).fetchone()
        self.assertEqual(raw["kind"], "text")
        self.assertEqual(raw["checkpoint"], json.dumps(checkpoint, ensure_ascii=False))
        self.assertEqual(raw["digest"], hashlib.sha256(raw["checkpoint"].encode()).hexdigest())
        corrupt = '{"session": null, "검증": "수정됨"}'
        with self.backend.transaction(write=True) as connection:
            connection.execute(f"UPDATE {self.education.practices} SET checkpoint=%s WHERE id=%s", (corrupt, practice))
            connection.execute(f"UPDATE {self.basic._table} SET payload=%s WHERE id=%s", ('{"changed":true}', "digest-basic"))
        with self.assertRaises(self.StorageError):
            self.basic.read("digest-basic")
        for method, path, body in (
            ("GET", f"/practices/{practice}", None),
            ("POST", f"/practices/{practice}/resume", {}),
        ):
            result = self.call(method, PREFIX + path, body, status=409)
            self.assertEqual(result["error"]["code"], "CHECKPOINT_INVALID")
        with self.backend.transaction() as connection:
            current = connection.execute(f"SELECT checkpoint,digest FROM {self.education.practices} WHERE id=%s",
                                         (practice,)).fetchone()
        self.assertEqual(current["checkpoint"], corrupt)
        self.assertEqual(current["digest"], raw["digest"])


if __name__ == "__main__":
    unittest.main()
