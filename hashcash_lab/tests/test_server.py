"""Backend failures and HTTP lifecycle using real CPU proofs."""

import json
import tempfile
import threading
import time
import unittest
from hashlib import sha256
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hashcash_lab import server as lab


class CapturingJob(lab.SearchJob):
    """Capture CPU fallback without starting processes in failure unit tests."""

    cpu_base = None

    def run_cpu(self, base):
        self.cpu_base = base
        self.update(status="stopped", backend="CPU test")


def complete_batch(message, start, count):
    last = start + count - 1
    return {
        "checked": count, "nonce": None, "hash": None,
        "last_nonce": last,
        "last_hash": sha256(message.encode("utf-8") + str(last).encode("ascii")).hexdigest(),
    }


class SearchJobTests(unittest.TestCase):
    def test_cpu_mode_does_not_initialize_optional_gpu(self):
        def forbidden_gpu(*_):
            self.fail("Explicit CPU mode initialized the GPU")

        job = CapturingJob(lab.SearchConfig("message", 3, 123, 1, "cpu"), forbidden_gpu)
        job.run()
        self.assertEqual(job.cpu_base, 123)
        self.assertEqual(job.snapshot()["checked"], "0")

    def test_import_device_compile_and_selftest_failures_fall_back(self):
        failures = (
            ImportError("CuPy unavailable"), RuntimeError("No CUDA device"),
            RuntimeError("NVRTC compile failed"), ValueError("Self-test hash mismatch"),
        )
        for mode in ("auto", "gpu", "hybrid"):
            for failure in failures:
                with self.subTest(mode=mode, failure=str(failure)):
                    def unavailable(*_, failure=failure):
                        raise failure

                    job = CapturingJob(lab.SearchConfig(" \ud55c\uae00\U0001f642\n", 3, 19, 1, mode), unavailable)
                    job.run()
                    self.assertEqual(job.cpu_base, 19)
                    state = job.snapshot()
                    self.assertIn(str(failure), state["reason"])
                    self.assertIn("CPU", state["reason"])
                    self.assertIsNone(state["result"])
                    self.assertIsNone(state["error"])

    def test_runtime_failure_retries_first_unconfirmed_range(self):
        calls = []
        message = "exact \ud55c\uae00\n"

        class FailingGPU:
            name = "Fake GPU"
            closed = False

            def scan_batch(self, start, count):
                calls.append((start, count))
                if len(calls) == 2:
                    raise RuntimeError("failed launch")
                return complete_batch(message, start, count)

            def close(self):
                self.closed = True

        gpu = FailingGPU()
        job = CapturingJob(lab.SearchConfig(message, 64, 7, 1, "auto"), lambda *_: gpu)
        with patch.object(lab, "GPU_BATCH", 100):
            job.run()
        self.assertEqual(calls, [(7, 100), (107, 100)])
        self.assertEqual(job.cpu_base, 107)
        self.assertEqual(job.snapshot()["checked"], "100")
        self.assertEqual(job.snapshot()["lastNonce"], "106")
        self.assertTrue(gpu.closed)
        self.assertIn("failed launch", job.snapshot()["reason"])

    def test_wrong_gpu_hash_is_rejected_before_count_or_cursor_advance(self):
        class IncorrectGPU:
            name = "Incorrect GPU"

            def scan_batch(self, start, count):
                return {**complete_batch("message", start, count), "nonce": start, "hash": "0" * 64}

        job = CapturingJob(lab.SearchConfig("message", 3, 11, 1, "auto"), lambda *_: IncorrectGPU())
        job.run()
        state = job.snapshot()
        self.assertEqual(job.cpu_base, 11)
        self.assertEqual(state["checked"], "0")
        self.assertIsNone(state["result"])
        self.assertIsNone(state["lastNonce"])
        self.assertIn("CPU", state["reason"])

    def test_invalid_gpu_counts_do_not_skip_or_publish_a_range(self):
        for bad_count in (0, -1, True, 101):
            with self.subTest(checked=bad_count):
                class InvalidCountGPU:
                    name = "Invalid count GPU"
                    calls = 0

                    def scan_batch(self, start, count):
                        self.calls += 1
                        if self.calls > 1:
                            raise RuntimeError("stop malformed backend")
                        return {**complete_batch("message", start, count), "checked": bad_count}

                job = CapturingJob(lab.SearchConfig("message", 64, 9), lambda *_: InvalidCountGPU())
                with patch.object(lab, "GPU_BATCH", 100):
                    job.run()
                state = job.snapshot()
                self.assertEqual(job.cpu_base, 9)
                self.assertEqual(state["checked"], "0")
                self.assertIsNone(state["lastNonce"])
                self.assertIsNone(state["result"])

    def test_valid_gpu_hash_outside_completed_range_is_rejected(self):
        message = "Pyeongtaek University!"
        known_hash = "00028e62333a32b5b6bdf41418f42725f8c21d84dc0b7ff60e4480cc11a8677b"

        class OutOfRangeGPU:
            name = "Out-of-range GPU"

            def scan_batch(self, start, count):
                return {**complete_batch(message, start, count), "nonce": 8656, "hash": known_hash}

        job = CapturingJob(lab.SearchConfig(message, 3, 8657), lambda *_: OutOfRangeGPU())
        job.run()
        self.assertEqual(job.cpu_base, 8657)
        self.assertEqual(job.snapshot()["checked"], "0")
        self.assertIsNone(job.snapshot()["result"])

    def test_valid_gpu_proof_is_rechecked_and_keeps_nonce_as_string(self):
        message = "\ud55c\uae00\U0001f642"
        expected = "00091f64964396f15d07238d0d352913789e1dea831702392b3ceb73d6ac99ad"

        class ValidGPU:
            name = "Valid GPU"

            def scan_batch(self, start, count):
                return {**complete_batch(message, start, 981), "nonce": 980, "hash": expected}

        job = CapturingJob(lab.SearchConfig(message, 3), lambda *_: ValidGPU())
        job.run()
        state = job.snapshot()
        self.assertIsNone(job.cpu_base)
        self.assertEqual(state["status"], "found")
        self.assertEqual(state["checked"], "981")
        self.assertEqual(state["result"], {"nonce": "980", "hash": expected, "verified": True})

    def test_cancel_before_start_creates_no_cpu_work_or_result(self):
        job = CapturingJob(lab.SearchConfig("message", 64, 0, 1, "cpu"))
        job.stop()
        job.run()
        self.assertIsNone(job.cpu_base)
        self.assertEqual(job.snapshot()["status"], "stopped")
        self.assertEqual(job.snapshot()["checked"], "0")
        self.assertIsNone(job.snapshot()["result"])

    def test_terminal_elapsed_time_and_throughput_stay_frozen(self):
        job = CapturingJob(lab.SearchConfig("message", 64, 0, 1, "cpu"))
        job.record(complete_batch("message", 0, 100))
        job.run()
        finished = job.snapshot()
        with patch.object(lab.time, "perf_counter", return_value=job.started + 10_000):
            later = job.snapshot()
        self.assertEqual(later["elapsed"], finished["elapsed"])
        self.assertEqual(later["hashesPerSecond"], finished["hashesPerSecond"])
        self.assertEqual(later["checked"], "100")

    def test_terminal_status_waits_for_cleanup_before_finished(self):
        terminal = threading.Event()
        release_cleanup = threading.Event()

        class CleanupJob(lab.SearchJob):
            def run_cpu(self, base):
                self.update(status="stopped")
                terminal.set()
                if not release_cleanup.wait(timeout=5):
                    raise RuntimeError("Cleanup was not released")

        job = CleanupJob(lab.SearchConfig("cleanup", 64, 0, 1, "cpu"))
        job.start()
        try:
            self.assertTrue(terminal.wait(timeout=5))
            during_cleanup = job.snapshot()
            self.assertEqual(during_cleanup["status"], "stopped")
            self.assertFalse(during_cleanup["finished"])
        finally:
            release_cleanup.set()
            job.thread.join(timeout=5)
        after_cleanup = job.snapshot()
        self.assertTrue(after_cleanup["finished"])
        self.assertFalse(job.thread.is_alive())

    def test_new_job_does_not_reuse_a_previous_message_result(self):
        old = CapturingJob(lab.SearchConfig("\ud55c\uae00\U0001f642", 3))
        old.accept({"nonce": 980, "hash": "00091f64964396f15d07238d0d352913789e1dea831702392b3ceb73d6ac99ad"})
        new = CapturingJob(lab.SearchConfig("different", 3))
        self.assertNotEqual(new.snapshot()["id"], old.snapshot()["id"])
        self.assertIsNone(new.snapshot()["result"])
        self.assertEqual(new.snapshot()["checked"], "0")
        self.assertEqual(new.snapshot()["message"], "different")


class SearchConfigTests(unittest.TestCase):
    def test_default_worker_count_stays_inside_limit_on_large_machine(self):
        with patch.object(lab.os, "cpu_count", return_value=128):
            config = lab.SearchConfig.from_payload({"message": "test", "difficulty": 3})
        self.assertEqual(config.workers, 64)

    def test_hybrid_mode_preserves_explicit_cpu_worker_count(self):
        config = lab.SearchConfig.from_payload({"message": "test", "difficulty": 3,
                                              "mode": "hybrid", "workers": 1})
        self.assertEqual(config.mode, "hybrid")
        self.assertEqual(config.workers, 1)

    def test_exact_message_and_large_decimal_nonce_survive_json(self):
        message = " \ud55c\uae00\U0001f642\n"
        for nonce in (2 ** 53 + 1, lab.MAX_NONCE):
            with self.subTest(nonce=nonce):
                payload = json.loads(json.dumps({
                    "message": message, "difficulty": 64,
                    "startNonce": str(nonce), "workers": 1, "mode": "cpu",
                }))
                config = lab.SearchConfig.from_payload(payload)
                self.assertEqual(config.message, message)
                self.assertEqual(config.start_nonce, nonce)

    def test_invalid_payloads_are_rejected(self):
        good = {"message": "", "difficulty": 1, "workers": 1, "mode": "cpu"}
        cases = (
            {"message": None}, {"message": "x" * 4097}, {"message": "\ud800"},
            {"difficulty": 0}, {"difficulty": 65}, {"difficulty": True},
            {"difficulty": 1.5}, {"difficulty": "3"},
            {"startNonce": "-1"}, {"startNonce": "1.0"}, {"startNonce": 2 ** 53 + 1},
            {"startNonce": str(lab.MAX_NONCE + 1)}, {"startNonce": "\uff11"},
            {"workers": 0}, {"workers": True}, {"mode": "unknown"},
            {"participantName": ""}, {"participantName": "   "},
            {"participantName": "x" * 61}, {"participantName": None},
        )
        for changes in cases:
            with self.subTest(changes=ascii(changes)):
                with self.assertRaises(ValueError):
                    lab.SearchConfig.from_payload({**good, **changes})

    def test_loopback_server_cannot_bind_to_a_public_interface(self):
        for host in ("0.0.0.0", "::", "localhost"):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    lab.create_server(host=host, port=0)


class HashcashHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = tempfile.TemporaryDirectory()
        cls.server = lab.create_server(port=0, instance="http-test-instance", data_dir=cls.data_dir.name)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/api/hashcash"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=5)
        cls.data_dir.cleanup()

    def request(self, path, payload=None, *, raw=None, headers=None):
        body = raw if raw is not None else (json.dumps(payload).encode("utf-8") if payload is not None else None)
        req_headers = {"Content-Type": "application/json"} if body is not None else {}
        req_headers.update(headers or {})
        request = Request(self.base_url + path, data=body, headers=req_headers)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def wait_for(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, payload = self.request("/status")
            self.assertEqual(status, 200)
            job = payload["job"]
            if job and predicate(job):
                return job
            time.sleep(0.02)
        self.fail(f"Timed out waiting for job: {self.request('/status')[1]}")

    def tearDown(self):
        self.request("/stop", {})
        if self.server.job and self.server.job.thread:
            self.server.job.thread.join(timeout=5)
        self.server.job = None

    def test_health_and_idle_status(self):
        status, health = self.request("/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(health["protocol"], "sha256-utf8-decimal-hexzeros-v1")
        self.assertEqual(health["instance"], "http-test-instance")
        self.assertGreaterEqual(health["cpuCount"], 1)
        self.assertEqual(self.request("/status"), (200, {"job": None}))

    def test_actual_cpu_search_and_verify_over_http(self):
        message = "\ud55c\uae00\U0001f642"
        status, payload = self.request("/start", {
            "message": message, "difficulty": 3, "workers": 1, "mode": "cpu",
        })
        self.assertEqual(status, 200)
        job_id = payload["job"]["id"]
        found = self.wait_for(lambda job: job["status"] in ("found", "error"))
        self.assertEqual(found["status"], "found", found)
        self.assertEqual(found["id"], job_id)
        self.assertEqual(found["message"], message)
        self.assertEqual(found["result"]["nonce"], "980")
        self.assertEqual(found["checked"], "981")
        expected = sha256(message.encode("utf-8") + b"980").hexdigest()
        self.assertEqual(found["result"]["hash"], expected)
        self.assertTrue(found["result"]["verified"])
        status, verified = self.request("/verify", {"message": message, "difficulty": 3, "nonce": "980"})
        self.assertEqual(status, 200)
        self.assertEqual(verified, {"hash": expected, "valid": True})

    def test_hybrid_http_with_unavailable_gpu_completes_and_saves_on_cpu(self):
        real_job = lab.SearchJob

        def unavailable(*_):
            raise ImportError("test intentionally has no CUDA installation")

        def factory(config, history):
            return real_job(config, gpu_factory=unavailable, history=history)

        with patch.object(lab, "SearchJob", side_effect=factory):
            status, _ = self.request("/start", {"message": "한글🙂", "difficulty": 3,
                                                "mode": "hybrid", "workers": 1,
                                                "participantName": "HTTP fallback"})
            self.assertEqual(status, 200)
            found = self.wait_for(lambda job: job["finished"])
        self.assertEqual(found["status"], "found", found)
        self.assertEqual(found["result"]["nonce"], "980")
        self.assertEqual(found["cpuChecked"], "981")
        self.assertEqual(found["gpuChecked"], "0")
        self.assertFalse(found["hybridExecuted"])
        self.assertIn("CPU", found["backend"])
        self.assertIn("no CUDA installation", found["reason"])
        status, records = self.request("/history?participantName=HTTP%20fallback")
        self.assertEqual(status, 200)
        self.assertEqual(records["total"], 1)
        self.assertEqual(records["records"][0]["jobId"], found["id"])

    def test_high_nonce_verification_preserves_every_decimal_digit(self):
        message = " exact\n\ud55c\uae00\U0001f642 "
        for nonce in (2 ** 53 + 1, lab.MAX_NONCE):
            with self.subTest(nonce=nonce):
                status, verified = self.request("/verify", {"message": message, "difficulty": 64, "nonce": str(nonce)})
                self.assertEqual(status, 200)
                self.assertEqual(verified["hash"], sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest())
                self.assertFalse(verified["valid"])

    def test_search_at_uint64_end_exhausts_without_wrapping(self):
        message = "range end"
        status, _ = self.request("/start", {
            "message": message, "difficulty": 64, "workers": 1,
            "mode": "cpu", "startNonce": str(lab.MAX_NONCE),
        })
        self.assertEqual(status, 200)
        exhausted = self.wait_for(lambda job: job["status"] in ("exhausted", "found", "error"))
        self.assertEqual(exhausted["status"], "exhausted", exhausted)
        self.assertEqual(exhausted["checked"], "1")
        self.assertEqual(exhausted["lastNonce"], str(lab.MAX_NONCE))
        self.assertEqual(exhausted["lastHash"], sha256(message.encode("utf-8") + str(lab.MAX_NONCE).encode("ascii")).hexdigest())
        self.assertIsNone(exhausted["result"])

    def test_cancel_real_cpu_work_then_restart_has_no_stale_result(self):
        status, payload = self.request("/start", {
            "message": "cancel test", "difficulty": 64, "workers": 1, "mode": "cpu",
        })
        self.assertEqual(status, 200)
        old_id = payload["job"]["id"]
        self.wait_for(lambda job: int(job["checked"]) > 0)
        self.assertEqual(self.request("/start", {"message": "duplicate", "difficulty": 1, "workers": 1, "mode": "cpu"})[0], 409)
        self.assertEqual(self.request("/stop", {}), (200, {"ok": True}))
        stopped = self.wait_for(lambda job: job["status"] in ("stopped", "error"))
        self.assertEqual(stopped["status"], "stopped", stopped)
        self.assertIsNone(stopped["result"])
        old_job = self.server.job
        old_job.thread.join(timeout=5)
        self.assertFalse(old_job.thread.is_alive())
        status, payload = self.request("/start", {
            "message": "Pyeongtaek University!", "difficulty": 1, "workers": 1, "mode": "cpu",
        })
        self.assertEqual(status, 200)
        self.assertNotEqual(payload["job"]["id"], old_id)
        found = self.wait_for(lambda job: job["status"] in ("found", "error"))
        self.assertEqual(found["status"], "found", found)
        self.assertEqual(found["message"], "Pyeongtaek University!")
        self.assertEqual(found["result"]["nonce"], "2")

    def test_bad_requests_do_not_break_next_valid_request(self):
        for raw in (b"not json", b"[]", b"null"):
            with self.subTest(raw=raw):
                status, payload = self.request("/start", raw=raw)
                self.assertEqual(status, 400)
                self.assertIn("error", payload)
        for payload in ({}, {"message": "test", "difficulty": 65}, {"message": "test", "difficulty": True}):
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/start", payload)[0], 400)
        self.assertEqual(self.request("/missing")[0], 404)
        self.assertEqual(self.request("/missing", {})[0], 404)
        self.assertEqual(self.request("/stop", {})[0], 200)
        self.assertEqual(self.request("/verify", {"message": "", "difficulty": 3, "nonce": "886"})[0], 200)

    def test_cross_origin_writes_are_rejected(self):
        status, _ = self.request("/start", {"message": "test", "difficulty": 1}, headers={"Origin": "https://other.example"})
        self.assertEqual(status, 403)
        self.assertIsNone(self.server.job)


class ConfigurableOriginTests(unittest.TestCase):
    def test_custom_frontend_port_accepts_only_configured_loopback_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            server = lab.create_server(port=0, frontend_port=5187, data_dir=directory)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/hashcash/stop"
                origins = (
                    ("http://127.0.0.1:5187", 200),
                    ("http://localhost:5187", 200),
                    (f"http://127.0.0.1:{server.server_port}", 200),
                    (f"http://localhost:{server.server_port}", 200),
                    ("http://127.0.0.1:5186", 403),
                    ("http://localhost:5186", 403),
                    ("https://evil.example:5187", 403),
                    ("http://localhost.evil.example:5187", 403),
                )
                for origin, expected in origins:
                    with self.subTest(origin=origin):
                        request = Request(url, data=b"{}", headers={
                            "Content-Type": "application/json", "Origin": origin,
                        })
                        try:
                            response = urlopen(request, timeout=5)
                        except HTTPError as error:
                            response = error
                        with response:
                            self.assertEqual(response.status, expected)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)

    def test_frontend_port_validation_rejects_invalid_values_before_binding(self):
        for port in (0, -1, 65536, True, 5187.0, "5187", None):
            with self.subTest(port=port), self.assertRaises(ValueError):
                lab.create_server(port=0, frontend_port=port)


if __name__ == "__main__":
    unittest.main()
