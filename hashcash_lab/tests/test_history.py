"""Real persisted proof, restart, filtering, precision, and retry checks."""
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from hashcash_lab import server as lab
from hashcash_lab.cpu import MAX_NONCE, proof_hash, scan_batch
from hashcash_lab.history import HistoryStore


def valid_values(job_id="job-1", participant_name="나", message="한글🙂", difficulty=3, nonce=980):
    return {
        "job_id": job_id, "participant_name": participant_name,
        "message": message, "difficulty": difficulty, "nonce": str(nonce),
        "hash_value": proof_hash(message, nonce), "backend": "CPU · 1개 작업",
        "checked": str(nonce + 1), "elapsed": 0.125,
    }


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = HistoryStore(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_new_store_after_restart_preserves_exact_message_and_proof(self):
        message = "  한글🙂\n"
        batch = scan_batch(message, 2, 0, 100_000)
        first = self.store.save(**valid_values(message=message, difficulty=2, nonce=batch["nonce"]))
        restarted = HistoryStore(self.directory.name)
        result = restarted.query()
        self.assertEqual(result["records"], [first])
        self.assertEqual(first["message"], message)
        self.assertEqual(first["hash"], proof_hash(message, int(first["nonce"])))
        self.assertTrue(first["createdAt"].endswith("Z"))
        self.assertTrue(restarted.path.is_file())

    def test_people_difficulty_and_message_filters_preserve_separate_searches(self):
        records = [
            self.store.save(**valid_values("a", " 지호 ")),
            self.store.save(**valid_values("b", "민수")),
            self.store.save(**valid_values("c", "지호", difficulty=2)),
            self.store.save(**valid_values("d", "지호", "", 3, 886)),
        ]
        self.assertEqual(self.store.query()["records"], list(reversed(records)))
        filtered = self.store.query(participant_name="지호", difficulty=3, message="한글🙂")
        self.assertEqual(filtered["records"], [records[0]])
        self.assertEqual(filtered["participants"], ["민수", "지호"])
        self.assertEqual(filtered["total"], 1)
        limited = self.store.query(limit=1)
        self.assertEqual(len(limited["records"]), 1)
        self.assertEqual(limited["total"], 4)
        self.assertEqual(self.store.query(message="")["records"], [records[3]])
        self.assertEqual(self.store.query(message=" 한글🙂")["total"], 0)

    def test_retry_is_idempotent_but_other_jobs_with_same_proof_are_retained(self):
        original = self.store.save(**valid_values())
        self.assertEqual(self.store.save(**valid_values()), original)
        other = self.store.save(**valid_values("other-job"))
        self.assertNotEqual(other["id"], original["id"])
        self.assertEqual(self.store.query()["total"], 2)
        with self.assertRaises(ValueError):
            self.store.save(**valid_values(participant_name="다른 사람"))
        self.assertEqual(self.store.query()["total"], 2)

    def test_independent_proof_verification_rejects_bad_or_insufficient_results(self):
        good = valid_values()
        cases = (
            {"hash_value": "0" * 64}, {"message": "different"}, {"difficulty": 4},
            {"nonce": str(MAX_NONCE + 1)}, {"nonce": "-1"}, {"nonce": 980},
            {"checked": "-1"}, {"checked": True}, {"elapsed": float("nan")},
            {"elapsed": float("inf")}, {"elapsed": -1}, {"elapsed": True},
            {"difficulty": True}, {"participant_name": " "},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.store.save(**{**good, **changes})
        self.assertEqual(self.store.query()["total"], 0)

    def test_uint64_nonce_and_checked_are_stored_and_returned_as_decimal_text(self):
        for nonce in (2 ** 53 + 1, MAX_NONCE):
            for index in range(1000):
                message = f" exact 한글🙂\n{index} "
                if proof_hash(message, nonce).startswith("0"):
                    break
            else:
                self.fail("Unable to find a bounded large-nonce test proof")
            record = self.store.save(**valid_values(str(nonce), message=message, difficulty=1, nonce=nonce))
            self.assertEqual(record["nonce"], str(nonce))
            self.assertEqual(record["checked"], str(nonce + 1))
        with closing(sqlite3.connect(self.store.path)) as connection:
            types = connection.execute("SELECT typeof(nonce), typeof(checked) FROM results").fetchall()
        self.assertEqual(types, [("text", "text"), ("text", "text")])
        self.assertEqual(HistoryStore(self.directory.name).query()["total"], 2)

    def test_concurrent_retry_commits_exactly_one_record(self):
        with ThreadPoolExecutor(max_workers=8) as workers:
            records = list(workers.map(lambda _: self.store.save(**valid_values()), range(8)))
        self.assertEqual(len({record["id"] for record in records}), 1)
        self.assertEqual(self.store.query()["total"], 1)

    def test_environment_override_and_invalid_query_limits(self):
        with patch.dict(os.environ, {"HASHCASH_DATA_DIR": self.directory.name}):
            self.assertEqual(HistoryStore().path, self.store.path)
        for limit in (0, 501, True, "1"):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    self.store.query(limit=limit)


class PersistingSearchJobTests(unittest.TestCase):
    def test_disk_failure_preserves_found_and_retry_after_storage_recovers(self):
        class KnownProofJob(lab.SearchJob):
            def run_cpu(self, base):
                self.update(backend="CPU test")
                self.record({"checked": 981, "last_nonce": 980, "last_hash": proof_hash(self.config.message, 980)})
                self.accept({"nonce": 980, "hash": proof_hash(self.config.message, 980)})

        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory) / "blocked"
            blocked.write_text("A file cannot also be a data directory", encoding="utf-8")
            store = HistoryStore(blocked)
            job = KnownProofJob(lab.SearchConfig("한글🙂", 3, mode="cpu", participant_name="지호"), history=store)
            job.run()
            failed = job.snapshot()
            self.assertEqual(failed["status"], "found")
            self.assertTrue(failed["result"]["verified"])
            self.assertIsNone(failed["error"])
            self.assertIsNone(failed["recordId"])
            self.assertIn("저장 실패", failed["saveError"])
            self.assertEqual(failed["participantName"], "지호")
            blocked.unlink()
            retried = job.save_result()
            self.assertEqual(retried["status"], "found")
            self.assertIsNone(retried["saveError"])
            self.assertIsNotNone(retried["recordId"])
            self.assertEqual(job.save_result()["recordId"], retried["recordId"])
            record = store.query()["records"][0]
            self.assertEqual(record["id"], retried["recordId"])
            self.assertEqual(record["checked"], "981")
            self.assertEqual(record["elapsed"], failed["elapsed"])

    def test_no_record_for_stopped_or_unverified_search(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            job = lab.SearchJob(lab.SearchConfig("message", 64, mode="cpu"), history=store)
            job.stop()
            job.run()
            self.assertEqual(job.snapshot()["status"], "stopped")
            with self.assertRaises(ValueError):
                job.save_result()
            self.assertEqual(store.query()["total"], 0)


class HistoryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.start_server()

    def start_server(self):
        self.server = lab.create_server(port=0, data_dir=self.directory.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/hashcash"

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def tearDown(self):
        self.close_server()
        self.directory.cleanup()

    def request(self, path, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(self.url + path, data=body, headers={"Content-Type": "application/json"} if body else {})
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers

    def test_actual_cpu_auto_save_survives_server_restart_and_export(self):
        status, started, _ = self.request("/start", {
            "participantName": " 지호 ", "message": "한글🙂", "difficulty": 3,
            "mode": "cpu", "workers": 1,
        })
        self.assertEqual(status, 200)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            _, payload, _ = self.request("/status")
            found = payload["job"]
            if found["finished"]:
                break
            time.sleep(0.02)
        self.assertTrue(found["finished"], found)
        self.assertEqual(found["status"], "found", found)
        self.assertEqual(found["participantName"], "지호")
        self.assertIsNone(found["saveError"])
        self.assertIsNotNone(found["recordId"])
        status, retried, _ = self.request("/history/save", {"jobId": started["job"]["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(retried["job"]["recordId"], found["recordId"])
        self.close_server()
        self.start_server()
        self.assertIsNone(self.request("/status")[1]["job"])
        status, history, _ = self.request("/history?" + urlencode({"participantName": "지호", "difficulty": 3, "message": "한글🙂"}))
        self.assertEqual(status, 200)
        self.assertEqual(history["total"], 1)
        record = history["records"][0]
        self.assertEqual(record["id"], found["recordId"])
        self.assertEqual(record["nonce"], "980")
        self.assertEqual(record["hash"], proof_hash(record["message"], int(record["nonce"])))
        status, exported, headers = self.request("/history/export?" + urlencode({"participantName": "지호"}))
        self.assertEqual(status, 200)
        self.assertEqual(exported["records"], [record])
        self.assertTrue(exported["exportedAt"].endswith("Z"))
        self.assertEqual(exported["protocol"], "sha256-utf8-decimal-hexzeros-v1")
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_bad_filters_and_invalid_retry_leave_service_usable(self):
        paths = (
            "/history?difficulty=65", "/history?difficulty=1.5", "/history?limit=501",
            "/history?limit=0", "/history?participantName=", "/history?unknown=1",
            "/history?limit=1&limit=2", "/history/export?limit=1",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 400)
        self.assertEqual(self.request("/history/save", {"jobId": "missing"})[0], 409)
        status, history, _ = self.request("/history")
        self.assertEqual(status, 200)
        self.assertEqual(history, {"records": [], "participants": [], "total": 0})

    def test_save_retry_failure_returns_found_snapshot_and_then_recovers(self):
        values = valid_values()
        job = lab.SearchJob(lab.SearchConfig("한글🙂", 3, mode="cpu"), history=self.server.history)
        job.update(backend=values["backend"], elapsed=values["elapsed"])
        job.record({"checked": 981})
        job.accept({"nonce": 980, "hash": values["hash_value"]})
        self.server.job = job
        with patch.object(self.server.history, "save", side_effect=OSError("disk unavailable")):
            status, failed, _ = self.request("/history/save", {"jobId": job.snapshot()["id"]})
        self.assertEqual(status, 503)
        self.assertEqual(failed["job"]["status"], "found")
        self.assertIsNone(failed["job"]["error"])
        self.assertIn("disk unavailable", failed["error"])
        status, rejected, _ = self.request("/start", {
            "message": "new search", "difficulty": 1, "workers": 1, "mode": "cpu",
        })
        self.assertEqual(status, 409)
        self.assertIn("기록 저장을 다시 시도", rejected["error"])
        self.assertIs(self.server.job, job)
        status, recovered, _ = self.request("/history/save", {"jobId": job.snapshot()["id"]})
        self.assertEqual(status, 200)
        self.assertIsNone(recovered["job"]["saveError"])
        self.assertIsNotNone(recovered["job"]["recordId"])
        self.assertEqual(self.request("/history")[1]["total"], 1)
        status, started, _ = self.request("/start", {
            "message": "new search", "difficulty": 1, "workers": 1, "mode": "cpu",
        })
        self.assertEqual(status, 200)
        self.assertNotEqual(started["job"]["id"], job.snapshot()["id"])


if __name__ == "__main__":
    unittest.main()
