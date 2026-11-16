"""API regressions use temporary SQLite files and never contact a running server."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import COOKIE_NAME, create_app
from block import Block
from storage import StorageError


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = str(Path(self.folder.name) / "practices.sqlite3")
        self.app = create_app({"TESTING": True, "DATABASE": self.database})
        self.client = self.app.test_client()
        self.other = self.app.test_client()
        self.extra_apps = []

    def tearDown(self):
        for app in [self.app, *self.extra_apps]:
            app.extensions["practice_store"].close()
        self.folder.cleanup()

    def get(self, path, *, client=None, status=200):
        r = (client or self.client).get(path)
        self.assertEqual(r.status_code, status, r.get_json())
        return r.get_json()

    def post(self, path, body=None, *, client=None, status=200):
        r = (client or self.client).post(path, json={} if body is None else body)
        self.assertEqual(r.status_code, status, r.get_json())
        return r.get_json()

    def state(self, client=None):
        return self.get("/api/state", client=client)

    def mine(self, nid="A", client=None):
        start = self.post(f"/api/nodes/{nid}/mine/start", client=client)
        job_id = start["job_id"]
        for _ in range(100):
            r = self.post(f"/api/nodes/{nid}/mine/step", {"job_id": job_id, "batch": 5000}, client=client)
            if r["found"]:
                self.assertTrue(r["accepted"], r)
                return r
        self.fail("Difficulty-two mining did not finish within the test budget")

    def setup_transfer(self):
        self.post("/api/difficulty", {"difficulty": 2})
        alice = self.post("/api/wallets", {"name": "Alice"})["address"]
        bob = self.post("/api/wallets", {"name": "Bob"})["address"]
        self.mine()
        tx = self.post("/api/tx/prepare", {"from": alice, "to": bob, "amount": 20})["tx"]
        tx = self.post("/api/tx/sign", {"tx": tx})["tx"]
        self.assertTrue(self.post("/api/tx/submit", {"tx": tx})["ok"])
        self.mine()
        return alice, bob

    def new_app_client(self, cookie=None):
        app = create_app({"TESTING": True, "DATABASE": self.database})
        self.extra_apps.append(app)
        client = app.test_client()
        if cookie is not None:
            client.set_cookie(COOKIE_NAME, cookie)
        return app, client

    def remine_body(self, *, ensure_first_miss=False):
        body = {"block_index": 2, "tx_index": 1, "field": "amount", "value": 1000, "mode": "remine"}
        if ensure_first_miss:
            original = self.get("/api/nodes/B/chain")["blocks"][2]
            for value in range(1000, 2024):
                b = Block.from_dict(original)
                b.transactions[1].amount = value
                b.merkle_root = b.calculate_merkle_root()
                b.nonce = 0
                b.hash = b.calculate_hash()
                if not b.meets_difficulty():
                    body["value"] = value
                    return body
            self.fail("Could not select a non-winning first nonce")
        return body

    def test_static_routes_and_health_do_not_create_database_or_cookie(self):
        for path in ("/", "/architecture", "/api/health", "/api/hashcash-native/capabilities"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200)
            self.assertNotIn("Set-Cookie", r.headers)
            r.close()
        self.assertFalse(Path(self.database).exists())

    def test_hashcash_page_uses_team_bundle_without_creating_a_practice(self):
        frontend = Path(self.folder.name) / "frontend"
        frontend.mkdir()
        (frontend / "index.html").write_text("<title>team hashcash bundle</title>", encoding="utf-8")
        self.app.config["EDUCATION_FRONTEND"] = str(frontend)
        for path in ("/hashcash", "/hashcash/"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertIn(b"team hashcash bundle", r.data)
                self.assertEqual(r.headers["Cache-Control"], "no-cache")
                self.assertNotIn("Set-Cookie", r.headers)
                r.close()
        self.assertFalse(Path(self.database).exists())

    def test_clients_are_isolated_and_reset_only_current_practice(self):
        a, b = self.state(), self.state(self.other)
        self.assertNotEqual(a["practice"]["id"], b["practice"]["id"])
        self.post("/api/wallets", {"name": "Alice"})
        self.assertIn("Alice", [w["name"] for w in self.get("/api/wallets")])
        self.assertNotIn("Alice", [w["name"] for w in self.get("/api/wallets", client=self.other)])
        self.post("/api/wallets", {"name": "Bob"}, client=self.other)
        self.post("/api/reset")
        self.assertEqual(len(self.get("/api/wallets")), 3)
        self.assertIn("Bob", [w["name"] for w in self.get("/api/wallets", client=self.other)])

    def test_invalid_or_unknown_cookie_cannot_choose_a_fixed_practice_id(self):
        self.client.set_cookie(COOKIE_NAME, "chosen-by-client")
        state = self.state()
        self.assertNotEqual(state["practice"]["id"], "chosen-by-client")
        self.assertGreaterEqual(len(state["practice"]["id"]), 32)
        r = self.client.get("/api/state")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertEqual(int(r.headers["X-Practice-Revision"]), r.get_json()["revision"])

    def test_private_keys_are_only_in_explicit_reveal(self):
        addr = self.post("/api/wallets", {"name": "Alice"})["address"]
        for path in ("/api/state", "/api/wallets", "/api/events", "/api/nodes/A/chain"):
            content = json.dumps(self.get(path))
            self.assertNotIn('"private_key"', content)
        r = self.client.get(f"/api/wallets/{addr}/keys")
        keys = r.get_json()
        self.assertEqual(len(keys["private_key"]), 64)
        self.assertEqual(keys["address"], addr)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.get(f"/api/wallets/{addr}/keys", client=self.other, status=404)

    def test_persistence_survives_new_app_and_keeps_cookie_and_wallet_keys(self):
        alice, bob = self.setup_transfer()
        before = self.state()
        cookie = self.client.get_cookie(COOKIE_NAME).value
        key = self.get(f"/api/wallets/{alice}/keys")["private_key"]
        _, resumed = self.new_app_client(cookie)
        after = self.state(resumed)
        self.assertEqual(after["practice"]["id"], before["practice"]["id"])
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(self.get(f"/api/wallets/{alice}/keys", client=resumed)["private_key"], key)
        wallets = {w["address"]: w["balance"] for w in self.get("/api/wallets", client=resumed)}
        self.assertEqual((wallets[alice], wallets[bob]), (30, 70))
        self.assertTrue(all(n["height"] == 2 and n["valid"] for n in after["nodes"]))

    def test_restart_cancels_unfinished_mining_and_remine_without_partial_apply(self):
        self.post("/api/difficulty", {"difficulty": 5})
        self.post("/api/nodes/A/mine/start")
        cookie = self.client.get_cookie(COOKIE_NAME).value
        _, resumed = self.new_app_client(cookie)
        state = self.state(resumed)
        self.assertTrue(state["practice"]["job_cancelled_on_restore"])
        self.assertFalse(state["nodes"][0]["mining"])
        self.assertEqual(state["nodes"][0]["height"], 0)
        # A separate practice demonstrates restart during an asynchronous tamper job.
        self.client = resumed
        self.post("/api/reset")
        self.setup_transfer()
        original = self.get("/api/nodes/B/chain")["blocks"]
        self.post("/api/nodes/B/tamper", self.remine_body(), status=202)
        _, resumed = self.new_app_client(cookie)
        state = self.state(resumed)
        self.assertTrue(state["practice"]["job_cancelled_on_restore"])
        self.assertIsNone(state["tamper_job"])
        self.assertEqual(self.get("/api/nodes/B/chain", client=resumed)["blocks"], original)

    def test_restart_cancellation_is_durable_and_old_owner_cannot_apply_late_job(self):
        self.setup_transfer()
        before = self.state()
        cookie = before["practice"]["id"]
        original = self.get("/api/nodes/B/chain")["blocks"]
        job = self.post("/api/nodes/B/tamper", self.remine_body(), status=202)
        _, resumed = self.new_app_client(cookie)
        restored = self.state(resumed)
        self.assertEqual(restored["revision"], job["revision"] + 1)
        self.assertIsNone(self.app.extensions["practice_store"].read(cookie)["payload"]["tamper_pending"])
        result = self.post("/api/nodes/B/tamper/step", {"job_id": job["job_id"], "batch": 5000}, status=409)
        self.assertEqual(result["code"], "STALE_TAMPER_JOB")
        self.assertIsNone(self.state()["tamper_job"])
        self.assertEqual(self.get("/api/nodes/B/chain")["blocks"], original)

    def test_new_practice_changes_cookie_and_preserves_old_record(self):
        before = self.state()
        self.post("/api/wallets", {"name": "Alice"})
        old_id = before["practice"]["id"]
        after = self.post("/api/practices/new")
        self.assertNotEqual(after["practice"]["id"], old_id)
        self.assertEqual(len(self.get("/api/wallets")), 3)
        self.assertIsNotNone(self.app.extensions["practice_store"].read(old_id))

    def test_expected_revision_conflict_has_no_side_effects(self):
        old = self.state()["revision"]
        self.post("/api/wallets", {"name": "Alice", "expected_revision": old})
        r = self.post("/api/wallets", {"name": "Bob", "expected_revision": old}, status=409)
        self.assertEqual(r["code"], "STALE_REVISION")
        self.assertEqual(r["revision"], old + 1)
        self.assertNotIn("Bob", [w["name"] for w in self.get("/api/wallets")])

    def test_changed_practice_cookie_rejects_old_tab_even_when_revision_matches(self):
        old = self.state()
        current = self.post("/api/practices/new")
        self.assertEqual(old["revision"], current["revision"])
        for path, extra in (("/api/wallets", {"name": "not-created"}), ("/api/reset", {}), ("/api/practices/new", {})):
            result = self.post(path, {"expected_practice_id": old["practice"]["id"],
                                      "expected_revision": old["revision"], **extra}, status=409)
            self.assertEqual(result["code"], "PRACTICE_CHANGED")
            self.assertEqual(result["practice"]["id"], current["practice"]["id"])
        self.assertEqual(len(self.get("/api/wallets")), 3)

    def test_concurrent_mutations_share_the_per_practice_revision_lock(self):
        before = self.state()
        def create(name):
            client = self.app.test_client()
            client.set_cookie(COOKIE_NAME, before["practice"]["id"])
            r = client.post("/api/wallets", json={"name": name, "expected_revision": before["revision"],
                                                  "expected_practice_id": before["practice"]["id"]})
            return r.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ("Alice", "Bob")))
        self.assertEqual(sorted(results), [200, 409])
        wallets = self.get("/api/wallets")
        self.assertEqual(len(wallets), 4)
        self.assertEqual(sum(w["name"] in ("Alice", "Bob") for w in wallets), 1)

    def test_sqlite_compare_and_swap_conflict_rolls_back_and_reloads(self):
        before = self.state()
        _, other_process = self.new_app_client(before["practice"]["id"])
        self.state(other_process)
        self.post("/api/wallets", {"name": "Alice"})
        result = self.post("/api/wallets", {"name": "Bob", "expected_revision": before["revision"]}, client=other_process, status=409)
        self.assertEqual(result["code"], "STALE_REVISION")
        names = [w["name"] for w in self.get("/api/wallets", client=other_process)]
        self.assertIn("Alice", names)
        self.assertNotIn("Bob", names)

    def test_compare_and_swap_reload_keeps_the_latest_mining_job(self):
        before = self.state()
        _, other_process = self.new_app_client(before["practice"]["id"])
        self.state(other_process)
        self.post("/api/difficulty", {"difficulty": 5})
        job = self.post("/api/nodes/A/mine/start")["job_id"]
        self.post("/api/wallets", {"name": "blocked-by-version", "expected_revision": before["revision"]}, client=other_process, status=409)
        candidate = self.state(other_process)["nodes"][0]["candidate"]
        self.assertEqual(candidate["job_id"], job)
        self.assertEqual(candidate["status"], "running")
        self.assertEqual(candidate["attempts"], 0)
        result = self.post("/api/nodes/A/mine/step", {"job_id": job, "batch": 1}, client=other_process)
        self.assertEqual(result["attempts"], 1)

    def test_cached_reader_converges_and_can_use_a_newer_expected_revision(self):
        before = self.state()
        _, other_process = self.new_app_client(before["practice"]["id"])
        self.state(other_process)
        latest = self.post("/api/wallets", {"name": "Alice"})
        result = self.post("/api/wallets", {"name": "Bob", "expected_practice_id": before["practice"]["id"],
                                             "expected_revision": latest["revision"]}, client=other_process)
        self.assertEqual(result["revision"], latest["revision"] + 1)
        # The first cache also refreshes on a GET, without needing a failed mutation.
        current = self.state()
        self.assertEqual(current["revision"], result["revision"])
        self.assertEqual([w["name"] for w in self.get("/api/wallets")][-2:], ["Alice", "Bob"])

    def test_compare_and_swap_reload_cannot_bypass_pending_remine(self):
        self.setup_transfer()
        before = self.state()
        _, other_process = self.new_app_client(before["practice"]["id"])
        self.state(other_process)
        original = self.get("/api/nodes/B/chain")["blocks"]
        job = self.post("/api/nodes/B/tamper", self.remine_body(ensure_first_miss=True), status=202)
        self.post("/api/wallets", {"name": "stale"}, client=other_process, status=409)
        restored = self.state(other_process)["tamper_job"]
        self.assertEqual(restored["job_id"], job["job_id"])
        blocked = self.post("/api/wallets", {"name": "still-blocked"}, client=other_process, status=409)
        self.assertEqual(blocked["code"], "TAMPER_BUSY")
        result = self.post("/api/nodes/B/tamper/cancel", {"job_id": job["job_id"]}, client=other_process)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.get("/api/nodes/B/chain", client=other_process)["blocks"], original)
        # The former owner also reloads the cancellation rather than applying a late copy.
        self.post("/api/nodes/B/tamper/step", {"job_id": job["job_id"], "batch": 1}, status=409)
        self.assertIsNone(self.state()["tamper_job"])

    def test_storage_failure_rolls_back_wallet_and_preserves_active_job(self):
        before = self.state()
        store = self.app.extensions["practice_store"]
        with patch.object(store, "save", side_effect=StorageError("test save failure")):
            self.post("/api/wallets", {"name": "Alice"}, status=503)
        self.assertEqual(self.state()["revision"], before["revision"])
        self.assertNotIn("Alice", [w["name"] for w in self.get("/api/wallets")])
        self.post("/api/difficulty", {"difficulty": 5})
        job = self.post("/api/nodes/A/mine/start")["job_id"]
        before = self.state()
        with patch.object(store, "save", side_effect=StorageError("test save failure")):
            self.post("/api/nodes/A/mine/step", {"job_id": job, "batch": 1}, status=503)
        after = self.state()
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["nodes"][0]["candidate"], before["nodes"][0]["candidate"])

    def test_malformed_requests_return_json_without_mutation(self):
        self.state()
        cases = [
            ("/api/wallets", {"name": []}),
            ("/api/wallets", {"name": "Alice", "node": []}),
            ("/api/wallets", {"name": "Alice", "node": "missing"}),
            ("/api/difficulty", {"difficulty": True}),
            ("/api/difficulty", {"difficulty": "2"}),
            ("/api/difficulty", {"difficulty": 99}),
            ("/api/nodes/A/online", {"online": "false"}),
            ("/api/nodes/A/mine/step", {"job_id": "x", "batch": 0}),
            ("/api/nodes/A/mine/step", {"job_id": "x", "batch": -1}),
            ("/api/nodes/A/mine/step", {"job_id": "x", "batch": 5001}),
            ("/api/nodes/A/mine/step", {"job_id": "x", "batch": 1.2}),
            ("/api/nodes/A/mine/step", {"job_id": "x", "batch": True}),
            ("/api/tx/sign", {"tx": []}),
            ("/api/tx/sign", {"tx": {}}),
            ("/api/tx/submit", {"tx": {"type": "coinbase"}}),
            ("/api/nodes/A/tamper", {"block_index": 999, "tx_index": 0, "field": "amount", "value": 2}),
            ("/api/reset", {"expected_revision": False}),
        ]
        for path, body in cases:
            with self.subTest(path=path, body=body):
                before = self.state()["revision"]
                r = self.client.post(path, json=body)
                self.assertIn(r.status_code, (400, 404))
                self.assertFalse(r.get_json()["ok"])
                self.assertEqual(self.state()["revision"], before)
        for body, content_type in (("not json", "application/json"), ("[]", "application/json"), ("null", "application/json"), ("name=Alice", "application/x-www-form-urlencoded")):
            r = self.client.post("/api/wallets", data=body, content_type=content_type)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.get_json()["code"], "INVALID_JSON")
        deep = '{"name":' + '[' * 3000 + '"a"' + ']' * 3000 + '}'
        r = self.client.post("/api/wallets", data=deep, content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "INVALID_JSON")
        for path in ("/api/nodes/unknown/chain", "/api/nodes/unknown/mempool", "/api/missing"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get("/api/events?since=NaN").status_code, 400)

    def test_transaction_schema_rejects_nonfinite_and_boolean_fields(self):
        alice = self.post("/api/wallets", {"name": "Alice"})["address"]
        bob = self.post("/api/wallets", {"name": "Bob"})["address"]
        tx = self.post("/api/tx/prepare", {"from": alice, "to": bob, "amount": 1})["tx"]
        for name, value in (("nonce", True), ("nonce", -1), ("amount", "1"), ("amount", False), ("timestamp", float("nan")), ("timestamp", float("inf")), ("timestamp", 10 ** 500), ("timestamp", True), ("sender_public_key", {}), ("signature", {})):
            with self.subTest(field=name, value=str(value)[:40]):
                changed = dict(tx, **{name: value})
                self.post("/api/tx/sign", {"tx": changed}, status=400)

    def test_wallet_creation_with_bad_node_never_creates_wallet(self):
        before = self.get("/api/wallets")
        self.post("/api/wallets", {"name": "not-created", "node": "missing"}, status=404)
        self.assertEqual(self.get("/api/wallets"), before)

    def test_mining_pause_resume_single_step_stop_and_stale_job(self):
        self.post("/api/difficulty", {"difficulty": 5})
        first = self.post("/api/nodes/A/mine/start")["job_id"]
        paused = self.post("/api/nodes/A/mine/pause", {"job_id": first})
        self.assertEqual(paused["status"], "paused")
        result = self.post("/api/nodes/A/mine/step", {"job_id": first, "batch": 2}, status=409)
        self.assertEqual(result["code"], "MINING_PAUSED")
        result = self.post("/api/nodes/A/mine/step", {"job_id": first, "batch": 1})
        self.assertEqual(result["attempts"], 1)
        if not result["found"]:
            self.assertEqual(result["status"], "paused")
            self.post("/api/nodes/A/mine/resume", {"job_id": first})
            self.post("/api/nodes/A/mine/stop", {"job_id": first})
        second = self.post("/api/nodes/A/mine/start")["job_id"]
        before = self.state()["nodes"][0]["candidate"]
        self.assertNotEqual(first, second)
        self.post("/api/nodes/A/mine/step", {"job_id": first, "batch": 1}, status=409)
        self.assertEqual(self.state()["nodes"][0]["candidate"], before)
        self.post("/api/reset")
        self.post("/api/nodes/A/mine/step", {"job_id": second, "batch": 1}, status=409)

    def test_offline_partition_can_mine_but_does_not_broadcast_or_sync(self):
        self.post("/api/difficulty", {"difficulty": 2})
        self.post("/api/nodes/A/online", {"online": False})
        self.mine()
        state = {n["id"]: n for n in self.state()["nodes"]}
        self.assertEqual([state[n]["height"] for n in "ABC"], [1, 0, 0])
        self.post("/api/nodes/A/sync", status=409)
        self.post("/api/nodes/A/online", {"online": True})
        self.assertTrue(self.post("/api/nodes/B/sync")["changed"])
        self.assertEqual(self.get("/api/nodes/B/chain")["height"], 1)

    def test_async_remine_cancellation_is_atomic_and_progress_matches_sample(self):
        self.setup_transfer()
        original = self.get("/api/nodes/B/chain")["blocks"]
        body = self.remine_body(ensure_first_miss=True)
        job = self.post("/api/nodes/B/tamper", body, status=202)
        self.assertEqual(self.get("/api/nodes/B/chain")["blocks"], original)
        self.post("/api/wallets", {"name": "blocked"}, status=409)
        progress = self.post("/api/nodes/B/tamper/step", {"job_id": job["job_id"], "batch": 1})
        self.assertEqual(progress["status"], "running")
        self.assertEqual(progress["attempts"], 1)
        self.assertEqual((progress["nonce"], progress["hash"]), (progress["samples"][-1]["nonce"], progress["samples"][-1]["hash"]))
        sample = progress["samples"][-1]
        block = Block.from_dict(original[2]); block.transactions[1].amount = body["value"]
        block.merkle_root = block.calculate_merkle_root()
        block.nonce = sample["nonce"]
        self.assertEqual(sample["hash"], block.calculate_hash())
        self.assertEqual(len(sample["hash"]), 64)
        self.assertEqual(self.get("/api/nodes/B/chain")["blocks"], original)
        cancelled = self.post("/api/nodes/B/tamper/cancel", {"job_id": job["job_id"]})
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.get("/api/nodes/B/chain")["blocks"], original)
        self.post("/api/nodes/B/tamper/step", {"job_id": job["job_id"], "batch": 1}, status=409)

    def test_async_remine_completion_preserves_signature_rejection_and_sync(self):
        self.setup_transfer()
        job = self.post("/api/nodes/B/tamper", self.remine_body(), status=202)
        result = None
        for _ in range(100):
            result = self.post("/api/nodes/B/tamper/step", {"job_id": job["job_id"], "batch": 5000})
            if result["status"] == "completed":
                break
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["valid"])
        checks = result["results"][2]["checks"]
        self.assertTrue(checks["hash"] and checks["merkle"] and checks["pow"] and checks["link"])
        self.assertFalse(checks["txs"])
        wallets = self.get("/api/wallets?node=B")
        self.assertTrue(all(w["balance"] is None and not w["balance_valid"] for w in wallets))
        self.assertTrue(self.get("/api/nodes/A/chain")["valid"])
        self.assertTrue(self.post("/api/nodes/B/sync")["changed"])
        self.assertTrue(self.get("/api/nodes/B/chain")["valid"])

    def test_reset_invalidates_pending_remine_job(self):
        self.setup_transfer()
        job = self.post("/api/nodes/B/tamper", self.remine_body(), status=202)
        self.post("/api/reset")
        self.post("/api/nodes/B/tamper/step", {"job_id": job["job_id"], "batch": 1}, status=409)
        self.assertTrue(all(n["height"] == 0 for n in self.state()["nodes"]))

    def test_invalid_experiment_chain_is_restored_without_claiming_a_balance(self):
        self.setup_transfer()
        self.post("/api/nodes/B/tamper", dict(self.remine_body(), mode="simple"))
        cookie = self.client.get_cookie(COOKIE_NAME).value
        _, resumed = self.new_app_client(cookie)
        self.assertFalse(self.get("/api/nodes/B/chain", client=resumed)["valid"])
        self.assertTrue(all(w["balance"] is None for w in self.get("/api/wallets?node=B", client=resumed)))
        self.assertTrue(self.post("/api/nodes/B/sync", client=resumed)["changed"])


if __name__ == "__main__":
    unittest.main()
