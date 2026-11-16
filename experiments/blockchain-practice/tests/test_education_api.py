"""The integrated Flask transport uses only temporary team databases."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask
from education_api import OWNER_COOKIE, PREFIX, register_education
from education_engine.core.errors import LabError


class EducationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "education.sqlite3")
        self.runtimes = []
        self.app, self.client, self.runtime = self.make_app()

    def tearDown(self):
        for runtime in self.runtimes:
            runtime.close()
        self.temp.cleanup()

    def make_app(self, cookie=None, **config):
        app = Flask(__name__)
        app.config.update(TESTING=True, TEAM_TEACHER_SECRET="team-test-secret", **config)
        runtime = register_education(app, self.path, start_runner=False)
        self.runtimes.append(runtime)
        client = app.test_client()
        if cookie:
            client.set_cookie(OWNER_COOKIE, cookie)
        return app, client, runtime

    def call(self, method, path, body=None, status=200, client=None, headers=None):
        response = (client or self.client).open(PREFIX + path, method=method, json=body, headers=headers)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        return response.get_json() if response.data else None

    def lab(self, client=None, difficulty=1):
        return self.call("POST", "/labs", {"mode": "example", "difficulty": difficulty}, 201, client)["lab"]

    def transfer(self, lab, fee=1):
        base = f"/labs/{lab['labId']}"
        draft = self.call("POST", base + "/nodes/A/drafts", {
            "senderAddress": lab["wallets"][0]["address"],
            "recipientAddress": lab["wallets"][1]["address"], "amount": 30, "fee": fee,
        })
        signed = self.call("POST", base + "/wallets/sign", {"draft": draft["draft"]})["signed"]
        submitted = self.call("POST", base + "/nodes/A/transactions", {"transaction": signed})
        return draft, signed, submitted

    def deliver_all(self, lab):
        for _ in range(30):
            response = self.call("POST", f"/labs/{lab['labId']}/messages/deliver-next")
            if response["validation"] is None:
                return response["lab"]
        self.fail("Message delivery did not drain")

    def mine(self, lab):
        base = f"/labs/{lab['labId']}"
        response = self.call("POST", base + "/mining", {"nodeId": "A", "mode": "fast"})
        for _ in range(100):
            if response["mining"]["status"] == "found":
                return response
            response = self.call("POST", base + f"/mining/{response['mining']['jobId']}/tick")
        self.fail("Bounded difficulty-one mining did not finish")

    def test_health_has_no_owner_cookie_or_personal_record(self):
        response = self.client.get(PREFIX + "/health")
        self.assertEqual(response.get_json()["version"], "3.0.0")
        self.assertIsNone(self.client.get_cookie(OWNER_COOKIE))
        self.assertEqual(self.runtime.store.list("personal"), [])
        self.assertIsNone(self.runtime.runner)

    def test_utxo_fee_mining_and_message_delivery_preserve_contract(self):
        lab = self.lab()
        draft, signed, submitted = self.transfer(lab)
        self.assertEqual([output["amount"] for output in draft["draft"]["outputs"]], [30, 69])
        self.assertTrue(submitted["validation"]["valid"])
        self.assertEqual(len(submitted["lab"]["nodes"]["B"]["mempool"]), 0)
        self.deliver_all(lab)
        found = self.mine(lab)
        self.assertEqual([len(found["lab"]["nodes"][node]["chain"]) for node in "ABC"], [2, 1, 1])
        final = self.deliver_all(lab)
        for node in "ABC":
            values = [final["nodes"][node]["balances"][wallet["address"]]["confirmed"] for wallet in lab["wallets"]]
            self.assertEqual(values, [69, 30])
            self.assertTrue(final["nodes"][node]["chainValidation"]["valid"])
        self.assertNotIn("privateKey", json.dumps(final))
        self.assertTrue(signed["id"])

    def test_other_browser_cannot_read_mutate_reveal_or_archive_personal_lab(self):
        lab = self.lab()
        practice = lab["storage"]["id"]
        other = self.app.test_client()
        self.assertEqual(self.call("GET", "/practices", client=other), {"practices": [], "total": 0})
        for method, path, body in [
            ("GET", f"/labs/{lab['labId']}", None),
            ("POST", f"/labs/{lab['labId']}/wallets", None),
            ("DELETE", f"/labs/{lab['labId']}", None),
            ("GET", f"/labs/{lab['labId']}/wallets/{lab['wallets'][0]['address']}/private-key", None),
            ("GET", f"/practices/{practice}", None),
            ("GET", f"/practices/{practice}/html", None),
            ("POST", f"/practices/{practice}/rename", {"title": "stolen"}),
            ("POST", f"/practices/{practice}/resume", {}),
            ("POST", f"/practices/{practice}/save", None),
            ("GET", f"/practices/{practice}/status", None),
            ("DELETE", f"/practices/{practice}", None),
        ]:
            with self.subTest(path=path, method=method):
                self.call(method, path, body, 404, other)
        self.assertEqual(self.call("GET", "/practices")["total"], 1)

    def test_owner_cookie_is_unpredictable_httponly_and_invalid_cookie_not_accepted(self):
        self.client.set_cookie(OWNER_COOKIE, "chosen")
        response = self.client.get(PREFIX + "/practices")
        token = self.client.get_cookie(OWNER_COOKIE).value
        self.assertEqual(len(token.split(".")[0]), 43)
        self.assertEqual(len(token.split(".")[1]), 64)
        self.assertNotEqual(token, "chosen")
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", response.headers["Set-Cookie"])
        self.client.set_cookie(OWNER_COOKIE, "a" * 43 + "." + "é" * 64)
        self.call("GET", "/practices")
        self.assertNotIn("é", self.client.get_cookie(OWNER_COOKIE).value)
        _, secure, _ = self.make_app(EDUCATION_COOKIE_SECURE=True)
        response = secure.get(PREFIX + "/practices", base_url="https://localhost")
        self.assertIn("Secure", response.headers["Set-Cookie"])

    def test_html_bootstrap_cookie_stays_stable_before_parallel_personal_requests(self):
        self.app.add_url_rule("/", "test_index", lambda: "<!doctype html><title>Team lab</title>")
        self.client.get("/")
        first = self.client.get_cookie(OWNER_COOKIE).value
        self.call("GET", "/practices")
        self.assertEqual(self.client.get_cookie(OWNER_COOKIE).value, first)
        self.lab()
        self.assertEqual(self.client.get_cookie(OWNER_COOKIE).value, first)
        self.client.set_cookie(OWNER_COOKIE, "a" * 43 + "." + "0" * 64)
        self.call("GET", "/practices")
        self.assertNotEqual(self.client.get_cookie(OWNER_COOKIE).value, first)

    def test_advanced_entry_keeps_the_same_owner_and_archives_across_mode_navigation(self):
        self.app.add_url_rule("/", "test_main", lambda: "<!doctype html><title>Basic lab</title>")
        self.app.add_url_rule("/advanced", "test_advanced", lambda: "<!doctype html><title>Earlier lab</title>")
        response = self.client.get("/advanced")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", response.headers["Set-Cookie"])
        owner = self.client.get_cookie(OWNER_COOKIE).value
        lab = self.lab()
        for path in ("/", "/?classroom=join&room=ABCD2345", "/advanced"):
            self.client.get(path)
            self.assertEqual(self.client.get_cookie(OWNER_COOKIE).value, owner)
        archive = self.call("GET", "/practices")
        self.assertEqual(archive["total"], 1)
        self.assertEqual(archive["practices"][0]["id"], lab["storage"]["id"])

        other = self.app.test_client()
        other.get("/advanced")
        self.assertNotEqual(other.get_cookie(OWNER_COOKIE).value, owner)
        self.call("GET", f"/labs/{lab['labId']}", status=404, client=other)
        self.assertEqual(self.call("GET", "/practices", client=other)["total"], 0)

    def test_creation_does_not_hold_store_lock_while_evicting_a_busy_lab(self):
        app = Flask("isolated-lock-regression")
        app.config.update(TESTING=True, EDUCATION_MAX_ACTIVE_LABS=1)
        runtime = register_education(app, ":memory:", start_runner=False)
        creator = app.test_client()
        original = creator.post(PREFIX + "/labs", json={"mode": "example", "difficulty": 1}).get_json()["lab"]
        old = runtime.registry.get(original["labId"])
        reached_save, release_save, reached_eviction = threading.Event(), threading.Event(), threading.Event()
        mutation_done, creation_done = threading.Event(), threading.Event()
        errors = []
        real_save = runtime.store.save
        real_create = runtime.registry.create

        def save(*args, **kwargs):
            if threading.current_thread().name == "busy-old-lab":
                reached_save.set()
                release_save.wait(3)
            return real_save(*args, **kwargs)

        def create():
            reached_eviction.set()
            return real_create()

        def mutation():
            try:
                old.add_wallet()
            except Exception as exc:
                errors.append(exc)
            finally:
                mutation_done.set()

        def creation():
            try:
                response = creator.post(PREFIX + "/labs", json={"mode": "manual"})
                if response.status_code != 201:
                    errors.append(response.get_data(as_text=True))
            except Exception as exc:
                errors.append(exc)
            finally:
                creation_done.set()

        try:
            with patch.object(runtime.store, "save", side_effect=save), patch.object(runtime.registry, "create", side_effect=create):
                threading.Thread(target=mutation, name="busy-old-lab", daemon=True).start()
                self.assertTrue(reached_save.wait(2))
                threading.Thread(target=creation, daemon=True).start()
                self.assertTrue(reached_eviction.wait(2))
                release_save.set()
                self.assertTrue(mutation_done.wait(2), "Mutation deadlocked with creation")
                self.assertTrue(creation_done.wait(2), "Creation deadlocked with eviction")
                self.assertEqual(errors, [])
        finally:
            release_save.set()
            if mutation_done.is_set() and creation_done.is_set():
                runtime.close()

    def test_archive_filter_rename_html_resume_delete_and_owner_survive_restart(self):
        lab = self.lab()
        practice = lab["storage"]["id"]
        self.transfer(lab)
        self.call("POST", f"/practices/{practice}/rename", {"title": "수수료 실습"})
        listing = self.call("GET", "/practices?q=수수료&limit=1&offset=0")
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["practices"][0]["summary"]["transactionCount"], 1)
        key_response = self.client.get(PREFIX + f"/labs/{lab['labId']}/wallets/{lab['wallets'][0]['address']}/private-key")
        key = key_response.get_json()["privateKey"]
        self.assertEqual(len(key), 64)
        self.assertEqual(key_response.headers["Cache-Control"], "no-store")
        html = self.client.get(PREFIX + f"/practices/{practice}/html")
        self.assertEqual(html.status_code, 200)
        self.assertIn("attachment", html.headers["Content-Disposition"])
        self.assertNotIn(key, html.get_data(as_text=True))
        self.call("DELETE", f"/practices/{practice}", status=409)
        cookie = self.client.get_cookie(OWNER_COOKIE).value
        _, resumed, _ = self.make_app(cookie)
        self.assertEqual(self.call("GET", "/practices", client=resumed)["total"], 1)
        restored = self.call("POST", f"/practices/{practice}/resume", {}, client=resumed)["lab"]
        self.assertNotEqual(restored["labId"], lab["labId"])
        self.assertEqual(restored["wallets"], lab["wallets"])
        restored_key = self.call("GET", f"/labs/{restored['labId']}/wallets/{lab['wallets'][0]['address']}/private-key", client=resumed)["privateKey"]
        self.assertEqual(restored_key, key)
        self.call("DELETE", f"/labs/{restored['labId']}", status=204, client=resumed)
        self.call("DELETE", f"/practices/{practice}", client=resumed)
        self.assertEqual(self.call("GET", "/practices", client=resumed)["total"], 0)

    def test_origin_guard_blocks_destructive_cross_origin_requests(self):
        lab = self.lab()
        for origin in ("https://other.example", "null", "http://localhost.evil.test", "http://[", "http://localhost/path"):
            result = self.call("DELETE", f"/labs/{lab['labId']}", status=403, headers={"Origin": origin})
            self.assertEqual(result["error"]["code"], "ORIGIN_FORBIDDEN")
        self.call("GET", f"/labs/{lab['labId']}")
        self.call("POST", f"/labs/{lab['labId']}/wallets", headers={"Origin": "http://localhost"})

    def test_failed_new_example_does_not_leave_an_active_or_saved_practice(self):
        self.call("POST", "/labs", {"mode": "example", "difficulty": 99}, 400)
        self.assertEqual(self.call("GET", "/practices"), {"practices": [], "total": 0})
        self.assertEqual(len(self.runtime.registry), 0)

    def test_invalid_json_nodes_models_and_queries_return_consistent_errors(self):
        lab = self.lab()
        for path, body in [
            ("/labs", {"mode": "wrong"}),
            (f"/labs/{lab['labId']}/mining", {"nodeId": "Z"}),
            (f"/labs/{lab['labId']}/nodes/Z/drafts", {}),
            (f"/labs/{lab['labId']}/race", {"action": "wrong"}),
            ("/classroom/rooms", {"secret": "x", "name": "   "}),
        ]:
            self.assertEqual(self.call("POST", path, body, 400)["error"]["code"], "INVALID_REQUEST")
        for raw in ("[]", "null", "not JSON", '{"mode":' + '[' * 3000 + '"a"' + ']' * 3000 + '}'):
            response = self.client.post(PREFIX + "/labs", data=raw, content_type="application/json")
            self.assertEqual(response.status_code, 400)
        self.call("GET", "/practices?limit=101", status=400)
        self.call("GET", "/practices?updatedFrom=bad", status=422)
        response = self.client.post(PREFIX + "/labs", data='{"mode":"manual","unused":"' + 'x' * 33000 + '"}', content_type="application/json")
        self.assertEqual(response.status_code, 413)

    def test_archive_capacity_is_bounded_without_exposing_other_owners(self):
        self.app.config["EDUCATION_MAX_PRACTICES_PER_OWNER"] = 1
        self.lab()
        self.assertEqual(self.call("POST", "/labs", {"mode": "manual"}, 429)["error"]["code"], "ARCHIVE_LIMIT")
        other = self.app.test_client()
        self.lab(other)
        self.app.config["EDUCATION_MAX_PRACTICES"] = 2
        third = self.app.test_client()
        self.call("POST", "/labs", {"mode": "manual"}, 429, third)

    def test_wallet_and_chain_limits_preserve_read_export_and_control_actions(self):
        self.app.config["EDUCATION_MAX_WALLETS"] = 3
        self.app.config["EDUCATION_MAX_CHAIN_BLOCKS"] = 2
        lab = self.lab()
        base = f"/labs/{lab['labId']}"
        self.call("POST", base + "/wallets")
        self.assertEqual(self.call("POST", base + "/wallets", status=409)["error"]["code"], "WALLET_LIMIT")
        race = self.call("POST", base + "/race", {"action": "prepare"})["lab"]["race"]
        self.mine(lab)
        self.assertEqual(self.call("POST", base + "/mining", {"nodeId": "A"}, 409)["error"]["code"], "CHAIN_LIMIT")
        self.call("POST", base + "/race", {"action": "pause", "raceId": race["id"]})
        self.call("POST", base + "/race", {"action": "stop", "raceId": race["id"]})
        final = self.deliver_all(lab)
        self.assertTrue(all(len(node["chain"]) == 2 for node in final["nodes"].values()))
        self.call("GET", base)
        html = self.client.get(PREFIX + f"/practices/{lab['storage']['id']}/html")
        self.assertEqual(html.status_code, 200)

    def test_queue_cap_can_be_recovered_by_delivery_and_pending_cap_rejects_new_spend(self):
        lab = self.lab()
        base = f"/labs/{lab['labId']}"
        self.app.config["EDUCATION_MAX_PENDING_MESSAGES"] = 2
        self.transfer(lab)
        self.assertEqual(self.call("POST", base + "/mining", {"nodeId": "A"}, 409)["error"]["code"], "QUEUE_LIMIT")
        self.deliver_all(lab)
        self.mine(lab)
        self.deliver_all(lab)
        self.app.config["EDUCATION_MAX_PENDING_MESSAGES"] = 300
        self.app.config["EDUCATION_MAX_PENDING_TRANSACTIONS"] = 1
        self.transfer(lab)
        second = self.call("POST", base + "/nodes/A/drafts", {
            "senderAddress": lab["wallets"][1]["address"],
            "recipientAddress": lab["wallets"][0]["address"], "amount": 5, "fee": 1,
        })
        signed = self.call("POST", base + "/wallets/sign", {"draft": second["draft"]})["signed"]
        rejected = self.call("POST", base + "/nodes/A/transactions", {"transaction": signed}, 409)
        self.assertEqual(rejected["error"]["code"], "MEMPOOL_LIMIT")
        self.assertEqual(len(self.call("GET", base)["lab"]["nodes"]["A"]["mempool"]), 1)
        self.mine(lab)
        self.deliver_all(lab)

    def test_activity_limit_keeps_pause_stop_read_save_and_delivery_available(self):
        lab = self.lab(difficulty=4)
        base = f"/labs/{lab['labId']}"
        self.transfer(lab)
        mining = self.call("POST", base + "/mining", {"nodeId": "A", "step": True})["mining"]
        self.app.config["EDUCATION_MAX_PERSONAL_EVENTS"] = 1
        self.assertEqual(self.call("POST", base + "/wallets", status=409)["error"]["code"], "EVENT_LIMIT")
        self.call("POST", base + f"/mining/{mining['jobId']}/pause")
        self.call("POST", base + f"/mining/{mining['jobId']}/stop")
        self.deliver_all(lab)
        self.call("GET", base)
        self.call("POST", f"/practices/{lab['storage']['id']}/save")

    def test_saved_failure_restores_personal_state_and_reports_retryable_error(self):
        lab = self.lab()
        before = self.call("GET", f"/labs/{lab['labId']}")
        with patch.object(self.runtime.store, "save", side_effect=LabError("SAVE_FAILED", "test full disk", 503)):
            self.call("POST", f"/labs/{lab['labId']}/wallets", status=503)
        after = self.call("GET", f"/labs/{lab['labId']}")
        self.assertEqual(before["lab"]["wallets"], after["lab"]["wallets"])
        self.assertEqual(before["lab"]["revision"], after["lab"]["revision"])

    def room(self):
        return self.call("POST", "/classroom/rooms", {"secret": "team-test-secret", "name": "교사", "difficulty": 1}, 201)

    def command(self, credential, action, payload=None, status=200, **extra):
        import uuid
        return self.call("POST", f"/classroom/rooms/{credential['code']}/commands", {
            "runId": credential["runId"], "commandId": str(uuid.uuid4()), "action": action,
            "payload": payload or {}, **extra,
        }, status, headers={"Authorization": "Bearer " + credential["token"]})

    def test_classroom_roles_stale_assignments_duplicate_commands_and_archive_auth(self):
        self.call("POST", "/classroom/rooms", {"secret": "wrong", "name": "교사"}, 403)
        teacher = self.room()
        observer = self.call("POST", f"/classroom/rooms/{teacher['code']}/join", {"name": "학생"}, 201)
        room = self.runtime.hub.room
        room.connected(room.teacher, 1)
        person = room.auth(observer["token"])
        room.connected(person, 1)
        self.command(observer, "prepare", status=403)
        self.command(teacher, "assign", {"participantId": person.id, "team": "A"})
        self.command(observer, "draft", {}, status=409, assignmentId="obsolete")
        body = {"runId": teacher["runId"], "commandId": "same-command-123", "action": "prepare", "payload": {}}
        headers = {"Authorization": "Bearer " + teacher["token"]}
        first = self.call("POST", f"/classroom/rooms/{teacher['code']}/commands", body, headers=headers)
        second = self.call("POST", f"/classroom/rooms/{teacher['code']}/commands", body, headers=headers)
        self.assertEqual(first, second)
        self.call("GET", "/classroom/practices", status=403, headers={"Authorization": "Bearer " + observer["token"]})
        self.call("GET", "/classroom/practices", status=403)
        saved = self.call("GET", "/classroom/practices", headers={"Authorization": "Bearer team-test-secret"})
        self.assertEqual(saved["total"], 1)
        self.assertEqual(self.call("GET", "/practices")["total"], 0)

    def test_classroom_restart_restore_uses_new_room_and_authority(self):
        teacher = self.room()
        room = self.runtime.hub.room
        room.connected(room.teacher, 1)
        self.command(teacher, "prepare")
        practice = room.practice_id
        _, restarted, _ = self.make_app()
        self.call("GET", f"/classroom/rooms/{teacher['code']}", status=410, client=restarted,
                  headers={"Authorization": "Bearer " + teacher["token"]})
        restored = self.call("POST", f"/classroom/practices/{practice}/resume", {"name": "새 교사"},
                             client=restarted, headers={"Authorization": "Bearer team-test-secret"})
        self.assertNotEqual(restored["code"], teacher["code"])
        self.assertNotEqual(restored["token"], teacher["token"])
        snapshot = self.call("GET", f"/classroom/rooms/{restored['code']}", client=restarted,
                             headers={"Authorization": "Bearer " + restored["token"]})
        self.assertEqual([person["role"] for person in snapshot["participants"]], ["teacher"])
        self.assertEqual(snapshot["lab"]["race"]["status"], "paused")


if __name__ == "__main__":
    unittest.main()
