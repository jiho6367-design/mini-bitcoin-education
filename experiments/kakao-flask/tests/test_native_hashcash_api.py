"""Native mode is optional and local, with isolated leased browser ownership."""
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import uuid

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_hashcash_api import OWNER_COOKIE, PREFIX, register_native_hashcash


class FakeJob:
    def __init__(self, config):
        self.config = config
        self.id = uuid.uuid4().hex
        self.finished = True
        self.status = "preparing"
        self.cancelled = threading.Event()
        self.thread = None

    def snapshot(self):
        return {"id": self.id, "status": self.status, "finished": self.finished,
                "checked": "0", "cpuChecked": "0", "gpuChecked": "0", "elapsed": 0,
                "backend": "준비 중", "reason": "PC 연산 준비 중.", "result": None}

    def start(self):
        self.finished = False
        self.status = "running"

    def stop(self):
        self.cancelled.set()
        self.status = "stopped"
        self.finished = True


class NativeHashcashApiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"RENDER": "", "RENDER_SERVICE_ID": "", "TEAM_NATIVE_HASHCASH": ""})
        self.environment.start()
        self.registries = []
        self.jobs = []
        self.now = 0.0
        self.app, self.client, self.registry = self.make_app()

    def tearDown(self):
        for registry in self.registries:
            registry.close()
        self.environment.stop()

    def factory(self, config):
        job = FakeJob(config)
        self.jobs.append(job)
        return job

    def make_app(self, enabled=True, **options):
        app = Flask(__name__)
        app.config.update(TESTING=True, TEAM_NATIVE_HASHCASH_ENABLED=enabled)
        registry = register_native_hashcash(app, job_factory=self.factory,
                                           clock=options.pop("clock", lambda: self.now),
                                           start_reaper=options.pop("start_reaper", False))
        self.registries.append(registry)
        return app, app.test_client(), registry

    @staticmethod
    def payload(**changes):
        return {"message": "한글🙂", "difficulty": 3, "startNonce": "0", "workers": 1,
                "mode": "python-native", **changes}

    def start(self, client=None, **changes):
        result = (client or self.client).post(PREFIX + "/start", json=self.payload(**changes))
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()["job"]

    def test_disabled_public_endpoints_never_create_jobs_or_owner_cookie(self):
        _, client, registry = self.make_app(enabled=False)
        response = client.get(PREFIX + "/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["available"])
        self.assertNotIn("Set-Cookie", response.headers)
        for method, route in (("post", "/start"), ("get", "/status"), ("post", "/stop")):
            result = getattr(client, method)(PREFIX + route)
            self.assertEqual(result.status_code, 404)
        self.assertEqual(self.jobs, [])
        self.assertEqual(registry.entries, {})

    def test_render_cannot_enable_native_execution_even_with_local_headers(self):
        for variable in ("RENDER", "RENDER_SERVICE_ID"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: "true"}):
                _, client, _ = self.make_app(enabled=True)
                self.assertFalse(client.get(PREFIX + "/capabilities").get_json()["available"])
                self.assertEqual(client.post(PREFIX + "/start", json=self.payload()).status_code, 404)
        self.assertEqual(self.jobs, [])

    def test_capabilities_do_not_initialize_cuda_or_native_library(self):
        with patch("importlib.util.find_spec", return_value=None):
            response = self.client.get(PREFIX + "/capabilities")
        body = response.get_json()
        self.assertTrue(body["available"])
        self.assertTrue(body["python"])
        self.assertFalse(body["cuda"])
        self.assertGreaterEqual(body["cpuCount"], 1)
        self.assertLessEqual(body["cpuCount"], 4)
        self.assertEqual(self.jobs, [])
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=" + PREFIX, cookie)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_loopback_peer_and_literal_loopback_host_are_both_required(self):
        examples = [
            ("http://evil.example", "127.0.0.1"),
            ("http://localhost.evil.example", "127.0.0.1"),
            ("http://127.0.0.1", "192.168.0.2"),
            ("http://localhost", "not-an-ip"),
        ]
        for base_url, peer in examples:
            with self.subTest(base_url=base_url, peer=peer):
                kwargs = {"base_url": base_url, "environ_overrides": {"REMOTE_ADDR": peer}}
                self.assertFalse(self.client.get(PREFIX + "/capabilities", **kwargs).get_json()["available"])
                self.assertEqual(self.client.post(PREFIX + "/start", json=self.payload(), **kwargs).status_code, 404)
        ipv6 = self.client.get(PREFIX + "/capabilities", base_url="http://[::1]", environ_overrides={"REMOTE_ADDR": "::1"})
        self.assertTrue(ipv6.get_json()["available"])
        self.assertEqual(self.jobs, [])

    def test_proxy_headers_cannot_turn_a_remote_peer_into_a_local_one(self):
        from werkzeug.middleware.proxy_fix import ProxyFix
        self.app.wsgi_app = ProxyFix(self.app.wsgi_app, x_for=1)
        response = self.client.post(PREFIX + "/start", json=self.payload(),
                                    headers={"X-Forwarded-For": "127.0.0.1"},
                                    environ_overrides={"REMOTE_ADDR": "192.168.0.2"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.jobs, [])

    def test_cross_origin_mutations_and_status_are_rejected(self):
        state = self.start()
        for method, path, kwargs in (
            ("post", "/start", {"json": self.payload()}),
            ("get", "/status?jobId=" + state["id"], {}),
            ("post", "/stop", {"json": {"jobId": state["id"]}}),
        ):
            with self.subTest(path=path):
                response = getattr(self.client, method)(PREFIX + path, headers={"Origin": "https://evil.example"}, **kwargs)
                self.assertEqual(response.status_code, 403)
        self.assertFalse(self.jobs[0].cancelled.is_set())

    def test_start_maps_optional_python_and_c_modes_to_original_engines(self):
        first = self.start(participantName=" 팀원 ")
        self.assertEqual(self.jobs[0].config.mode, "hybrid")
        self.assertEqual(self.jobs[0].config.participant_name, "팀원")
        self.assertEqual(first["reason"], "PC 연산 준비 중")
        self.client.post(PREFIX + "/stop", json={"jobId": first["id"]})
        self.start(mode="c-native", startNonce="18446744073709551615")
        self.assertEqual(self.jobs[1].config.mode, "native")
        self.assertEqual(self.jobs[1].config.start_nonce, (1 << 64) - 1)

    def test_invalid_modes_inputs_and_workers_never_create_a_job(self):
        cases = [
            {"mode": "gpu"}, {"mode": []}, {"mode": None}, {"workers": True},
            {"workers": 0}, {"workers": 5}, {"difficulty": 0}, {"difficulty": 65},
            {"difficulty": True}, {"startNonce": "-1"}, {"startNonce": "1e3"},
            {"startNonce": "18446744073709551616"}, {"message": "x" * 4097},
            {"message": "\ud800"}, {"participantName": " "},
        ]
        for changes in cases:
            with self.subTest(changes=list(changes)):
                result = self.client.post(PREFIX + "/start", json=self.payload(**changes))
                self.assertEqual(result.status_code, 400, result.get_json())
        self.assertEqual(self.client.post(PREFIX + "/start", json=[]).status_code, 400)
        self.assertEqual(self.client.post(PREFIX + "/start", data="{}").status_code, 400)
        self.assertEqual(self.jobs, [])

    def test_one_browser_cannot_query_or_stop_another_browsers_job(self):
        first = self.start()
        other = self.app.test_client()
        second = self.start(client=other)
        for endpoint in ("/status?jobId=" + first["id"],):
            self.assertEqual(other.get(PREFIX + endpoint).status_code, 404)
        self.assertEqual(other.post(PREFIX + "/stop", json={"jobId": first["id"]}).status_code, 404)
        self.assertFalse(self.jobs[0].cancelled.is_set())
        self.assertEqual(other.post(PREFIX + "/stop", json={"all": True}).get_json()["stopped"], 1)
        self.assertFalse(self.jobs[0].cancelled.is_set())
        self.assertTrue(self.jobs[1].cancelled.is_set())
        self.assertEqual(other.get(PREFIX + "/status?jobId=" + second["id"]).status_code, 200)
        stranger = self.app.test_client()
        stranger.set_cookie(OWNER_COOKIE, "chosen-by-client", path=PREFIX)
        self.assertEqual(stranger.get(PREFIX + "/status?jobId=" + first["id"]).status_code, 404)

    def test_per_owner_and_global_active_limits_are_enforced(self):
        self.start()
        self.assertEqual(self.client.post(PREFIX + "/start", json=self.payload()).status_code, 409)
        for _ in range(3):
            self.start(client=self.app.test_client())
        result = self.app.test_client().post(PREFIX + "/start", json=self.payload())
        self.assertEqual(result.status_code, 409)
        self.assertEqual(len(self.jobs), 4)

    def test_status_renews_lease_and_abandoned_jobs_are_cancelled(self):
        state = self.start()
        self.now = 20
        self.assertEqual(self.client.get(PREFIX + "/status?jobId=" + state["id"]).status_code, 200)
        self.now = 40
        self.registry.reap()
        self.assertFalse(self.jobs[0].cancelled.is_set())
        self.now = 51
        self.registry.reap()
        self.assertTrue(self.jobs[0].cancelled.is_set())
        self.registry.reap()
        self.assertEqual(self.client.get(PREFIX + "/status?jobId=" + state["id"]).status_code, 404)

    def test_background_watchdog_cancels_unpolled_jobs_and_close_is_idempotent(self):
        with patch("native_hashcash_api.LEASE_SECONDS", 0.01):
            _, client, registry = self.make_app(clock=__import__("time").monotonic, start_reaper=True)
            self.start(client=client)
            self.assertTrue(self.jobs[-1].cancelled.wait(2), "Native watchdog must stop an abandoned browser job")
        registry.close()
        registry.close()
        self.assertTrue(registry.closed)
        self.assertFalse(registry.reaper.is_alive())


if __name__ == "__main__":
    unittest.main()
