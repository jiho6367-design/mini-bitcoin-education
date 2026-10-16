"""Web/native lifecycle contracts without loading CUDA or the platform DLL.

These fakes model the native API's absolute snapshots and active-run-only cancel
semantics. They exercise the HTTP job adapter, not scheduler hashing throughput.
"""

import tempfile
import threading
import time
import unittest
from hashlib import sha256

from hashcash_lab import server as lab
from hashcash_lab.history import HistoryStore


def digest(message, nonce):
    return sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()


def first_proof(message, start, difficulty=1):
    for nonce in range(start, start + 10_000):
        if digest(message, nonce).startswith("0" * difficulty):
            return nonce
    raise AssertionError("Small independent proof fixture not found")


class StubGPU:
    name = "Isolated native GPU fixture"

    def __init__(self):
        self.closed = False

    def scan_batch(self, *_):
        raise AssertionError("Native web mode called Python's GPU batch loop")

    def close(self):
        self.closed = True


class FakeNative:
    """One native owner, with snapshots safe for the progress watcher thread."""

    def __init__(self, message, difficulty, workers=1, *, on_run=None,
                 before_running=None, close_error=None, close_gate=None, **options):
        self.message, self.difficulty, self.workers = message, difficulty, workers
        self.options = options
        self.on_run = on_run
        self.before_running = before_running
        self.close_error, self.close_gate = close_error, close_gate
        self.lock = threading.Lock()
        self.entered = threading.Event()
        self.active = threading.Event()
        self.cancelled = threading.Event()
        self.closing = threading.Event()
        self.closed = False
        self.cancel_calls = 0
        self.resets = []
        self.attached = None
        self.trace_rows = []
        self.current = {}
        self.began = time.perf_counter()

    def attach_gpu(self, gpu):
        self.attached = gpu
        return self

    def reset(self, start, count, *, search=False, tail_guard=0):
        if self.active.is_set():
            raise AssertionError("Reset raced an active native run")
        if not 1 <= count <= lab.MAX_NONCE or start + count - 1 > lab.MAX_NONCE:
            raise AssertionError("Web adapter supplied an invalid uint64 range")
        self.resets.append((start, count, search, tail_guard))
        self.cancelled.clear()
        self.set_stats()
        return self

    def set_stats(self, *, cpu=0, gpu=0, nonce=None, last_nonce=None, **extra):
        start, count, search, _ = self.resets[-1]
        checked = cpu + gpu
        if last_nonce is None and checked:
            last_nonce = start + checked - 1
        value = {
            "abi_version": 1, "status": 0, "running": int(self.active.is_set()),
            "cancelled": 0, "found": int(nonce is not None), "gpu_failed": 0,
            "trace_count": len(self.trace_rows), "trace_dropped": 0,
            "workers": self.workers, "workers_ready": self.workers,
            "search": int(search), "checked": checked, "cpu_checked": cpu,
            "gpu_checked": gpu, "nonce": str(nonce) if nonce is not None else None,
            "last_nonce": str(last_nonce) if last_nonce is not None else None,
            "assigned": checked, "range_start": start, "range_count": count,
            "cpu_batches": int(bool(cpu)), "gpu_batches": int(bool(gpu)),
            "retries": 0, "begin": self.began, "end": time.perf_counter(),
            "elapsed": time.perf_counter() - self.began,
            "first_proof_seconds": time.perf_counter() - self.began if nonce is not None else 0.0,
            "cpu_seconds": 1.0 if cpu else 0.0, "gpu_seconds": 1.0 if gpu else 0.0,
            "gpu_kernel_seconds": 0.9 if gpu else 0.0,
            "qpc_frequency": 10_000_000.0,
            "hash": digest(self.message, nonce) if nonce is not None else None,
            "last_hash": digest(self.message, last_nonce) if last_nonce is not None else None,
            "error": "", "gpu_error": "",
        }
        value.update(extra)
        with self.lock:
            self.current = value
        return value

    def run(self):
        self.entered.set()
        if self.before_running:
            self.before_running(self)
        self.active.set()
        self.began = time.perf_counter()
        with self.lock:
            self.current["running"] = 1
        try:
            if self.on_run:
                self.on_run(self)
            else:
                self.set_stats(cpu=self.resets[-1][1])
            return self.stats()
        finally:
            self.active.clear()
            with self.lock:
                self.current["running"] = 0

    def stats(self):
        with self.lock:
            return dict(self.current)

    def cancel(self):
        if self.closed:
            raise AssertionError("Progress watcher used a destroyed native runner")
        self.cancel_calls += 1
        # hc_cancel intentionally does not cancel a future run. The controller
        # must forward a stop again after preparation has entered hc_run.
        if self.active.is_set():
            self.cancelled.set()

    def traces(self):
        return list(self.trace_rows)

    def close(self):
        if self.active.is_set():
            raise AssertionError("Native runner closed before its C call drained")
        self.closing.set()
        if self.close_gate and not self.close_gate.wait(timeout=5):
            raise AssertionError("Test did not release native cleanup")
        self.closed = True
        if self.close_error:
            raise self.close_error


class NativeFactory:
    def __init__(self, **behavior):
        self.behavior = behavior
        self.instances = []

    def __call__(self, message, difficulty, workers=1, **options):
        runner = FakeNative(message, difficulty, workers, **self.behavior, **options)
        self.instances.append(runner)
        return runner


class NoPythonFallbackJob(lab.SearchJob):
    def run_cpu(self, _base):
        raise AssertionError("Native work restarted in the Python CPU loop")

    def run_hybrid(self, _gpu, _base):
        raise AssertionError("Native work restarted in the Python coordinator")


class NativeWebTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Native web lifecycle condition did not become true")

    def job(self, factory, *, message="native fixture", difficulty=64, start=100,
            gpu=None, history=None):
        backend = gpu or StubGPU()
        return NoPythonFallbackJob(
            lab.SearchConfig(message, difficulty, start, 2, "native", "Native tester"),
            gpu_factory=lambda *_: backend, history=history, native_factory=factory,
        )

    def finish(self, job):
        if job.thread:
            job.thread.join(timeout=5)
            self.assertFalse(job.thread.is_alive(), job.snapshot())
        return job.snapshot()

    def test_native_mode_preserves_explicit_payload_and_uint64(self):
        message = " \ud55c\uae00\U0001f642\n\x00 "
        config = lab.SearchConfig.from_payload({
            "message": message, "difficulty": 64, "startNonce": str(lab.MAX_NONCE),
            "workers": 1, "mode": "native", "participantName": "Native tester",
        })
        self.assertEqual(config.message, message)
        self.assertEqual(config.start_nonce, lab.MAX_NONCE)
        self.assertEqual(config.mode, "native")
        self.assertEqual(config.workers, 1)

    def test_uint64_last_nonce_is_checked_once_without_wrap(self):
        factory, gpu = NativeFactory(), StubGPU()
        job = self.job(factory, start=lab.MAX_NONCE, gpu=gpu)
        job.run()
        state = job.snapshot()
        runner = factory.instances[0]
        self.assertEqual(runner.resets[0][:3], (lab.MAX_NONCE, 1, True))
        self.assertIs(runner.attached, gpu)
        self.assertEqual(runner.options["cpu_batch"], 64_000)
        self.assertEqual(state["status"], "exhausted", state)
        self.assertEqual(state["checked"], "1")
        self.assertEqual(state["lastNonce"], str(lab.MAX_NONCE))
        self.assertEqual(state["lastHash"], digest(job.config.message, lab.MAX_NONCE))
        self.assertTrue(runner.closed)
        self.assertTrue(gpu.closed)

    def test_full_uint64_domain_splits_without_gap_or_overflow(self):
        factory = NativeFactory()
        job = self.job(factory, start=0)
        job.run()
        state = job.snapshot()
        self.assertEqual(state["status"], "exhausted", state)
        resets = factory.instances[0].resets
        self.assertEqual([row[:3] for row in resets],
                         [(0, lab.MAX_NONCE, True), (lab.MAX_NONCE, 1, True)])
        self.assertEqual(state["checked"], str(1 << 64))
        self.assertEqual(state["cpuChecked"], str(1 << 64))
        self.assertEqual(state["gpuChecked"], "0")
        self.assertEqual(state["lastNonce"], str(lab.MAX_NONCE))

    def test_live_native_snapshots_are_absolute_and_cancel_drains(self):
        def work(runner):
            runner.set_stats(cpu=5, gpu=7)
            if not runner.cancelled.wait(timeout=5):
                raise AssertionError("Web stop did not reach active native work")
            runner.set_stats(cpu=6, gpu=7, cancelled=1)

        factory = NativeFactory(on_run=work)
        job = self.job(factory)
        job.start()
        try:
            self.wait_for(lambda: job.snapshot()["checked"] == "12")
            # Polling the same absolute counters must never count them again.
            time.sleep(0.25)
            self.assertEqual(job.snapshot()["checked"], "12")
            self.assertEqual(job.snapshot()["cpuChecked"], "5")
            self.assertEqual(job.snapshot()["gpuChecked"], "7")
            self.assertFalse(job.snapshot()["finished"])
        finally:
            job.stop()
            state = self.finish(job)
        self.assertEqual(state["status"], "stopped", state)
        self.assertEqual(state["checked"], "13")
        self.assertIsNone(state["result"])
        runner = factory.instances[0]
        self.assertGreater(runner.cancel_calls, 0)
        self.assertTrue(runner.closed)

    def test_stop_between_preparation_and_c_run_is_not_lost(self):
        gate = threading.Event()

        def prepare(_runner):
            if not gate.wait(timeout=5):
                raise AssertionError("Test did not release native start")

        def work(runner):
            runner.set_stats(cpu=1)
            if not runner.cancelled.wait(timeout=5):
                raise AssertionError("Stop was lost before the native run became active")
            runner.set_stats(cpu=1, cancelled=1)

        factory = NativeFactory(on_run=work, before_running=prepare)
        job = self.job(factory)
        job.start()
        try:
            self.wait_for(lambda: bool(factory.instances))
            runner = factory.instances[0]
            self.assertTrue(runner.entered.wait(timeout=5))
            job.stop()
            gate.set()
            state = self.finish(job)
        finally:
            gate.set()
            job.stop()
            self.finish(job)
        self.assertEqual(state["status"], "stopped", state)
        self.assertGreater(runner.cancel_calls, 0)
        self.assertTrue(runner.closed)

    def test_stopped_job_has_no_c_work_and_restart_has_fresh_counters(self):
        factory = NativeFactory()
        old = self.job(factory)
        old.stop()
        old.run()
        self.assertEqual(old.snapshot()["status"], "stopped")
        self.assertEqual(old.snapshot()["checked"], "0")
        self.assertTrue(all(not item.resets for item in factory.instances))

        message = " New \ud55c\uae00\U0001f642\n"
        nonce = first_proof(message, (1 << 53) + 1)

        def proof(runner):
            start = runner.resets[-1][0]
            runner.set_stats(cpu=nonce - start + 1, nonce=nonce)

        restarted = self.job(NativeFactory(on_run=proof), message=message, difficulty=1,
                             start=(1 << 53) + 1)
        restarted.run()
        state = restarted.snapshot()
        self.assertNotEqual(state["id"], old.snapshot()["id"])
        self.assertEqual(state["message"], message)
        self.assertEqual(state["status"], "found", state)
        self.assertEqual(state["checked"], str(nonce - (1 << 53)))
        self.assertEqual(state["result"], {
            "nonce": str(nonce), "hash": digest(message, nonce), "verified": True,
        })

    def test_finished_waits_for_native_resource_cleanup(self):
        gate = threading.Event()
        factory = NativeFactory(close_gate=gate)
        job = self.job(factory, start=lab.MAX_NONCE)
        job.start()
        try:
            self.wait_for(lambda: bool(factory.instances))
            self.assertTrue(factory.instances[0].closing.wait(timeout=5))
            self.assertFalse(job.snapshot()["finished"])
        finally:
            gate.set()
            state = self.finish(job)
        self.assertEqual(state["status"], "exhausted", state)
        self.assertTrue(state["finished"])

    def test_gpu_failure_reports_native_cpu_retry_without_python_restart(self):
        def retry(runner):
            runner.set_stats(cpu=1, gpu_failed=1, retries=1,
                             gpu_error="fixture CUDA launch failed; range retried")

        factory = NativeFactory(on_run=retry)
        job = self.job(factory, start=lab.MAX_NONCE)
        job.run()
        state = job.snapshot()
        self.assertEqual(state["status"], "exhausted", state)
        self.assertEqual(state["checked"], "1")
        self.assertEqual(state["cpuChecked"], "1")
        self.assertEqual(state["gpuChecked"], "0")
        self.assertFalse(state["hybridExecuted"])
        self.assertIn("fixture CUDA launch failed", state["reason"])
        self.assertTrue(factory.instances[0].closed)

    def test_unavailable_cuda_uses_c_cpu_and_keeps_the_explanation(self):
        def unavailable(*_):
            raise ImportError("fixture CuPy is unavailable")

        factory = NativeFactory()
        job = NoPythonFallbackJob(
            lab.SearchConfig("native CPU fallback", 64, lab.MAX_NONCE, 1, "native"),
            gpu_factory=unavailable, native_factory=factory,
        )
        job.run()
        state = job.snapshot()
        self.assertEqual(state["status"], "exhausted", state)
        self.assertIsNone(factory.instances[0].attached)
        self.assertEqual(state["cpuChecked"], "1")
        self.assertEqual(state["gpuChecked"], "0")
        self.assertTrue(state["backend"].startswith("C · CPU"), state)
        self.assertIn("fixture CuPy is unavailable", state["reason"])

    def test_missing_native_library_falls_back_before_allocating_any_work(self):
        class CapturingFallbackJob(lab.SearchJob):
            cpu_base = None

            def run_cpu(self, base):
                self.cpu_base = base
                self.update(status="stopped", backend="Python CPU fixture")

        def unavailable(*_, **__):
            raise RuntimeError("fixture native DLL is missing")

        gpu = StubGPU()
        job = CapturingFallbackJob(
            lab.SearchConfig("native missing", 64, (1 << 53) + 1, 1, "native"),
            gpu_factory=lambda *_: gpu, native_factory=unavailable,
        )
        job.run()
        state = job.snapshot()
        self.assertEqual(job.cpu_base, (1 << 53) + 1)
        self.assertEqual(state["checked"], "0")
        self.assertIn("Python CPU", state["backend"])
        self.assertIn("fixture native DLL is missing", state["reason"])
        self.assertIsNone(state["error"])
        self.assertTrue(gpu.closed)

    def test_native_cuda_preparation_failure_recreates_clean_c_cpu_owner(self):
        instances = []

        class AttachFailureNative(FakeNative):
            def attach_gpu(self, _gpu):
                raise RuntimeError("fixture native CUDA adapter failed")

        def factory(message, difficulty, workers=1, **options):
            kind = AttachFailureNative if not instances else FakeNative
            runner = kind(message, difficulty, workers, **options)
            instances.append(runner)
            return runner

        job = self.job(factory, start=lab.MAX_NONCE)
        job.run()
        state = job.snapshot()
        self.assertEqual(state["status"], "exhausted", state)
        self.assertEqual(len(instances), 2)
        self.assertTrue(instances[0].closed)
        self.assertEqual(instances[0].resets, [])
        self.assertTrue(instances[1].closed)
        self.assertIsNone(instances[1].attached)
        self.assertEqual(instances[1].resets[0][:3], (lab.MAX_NONCE, 1, True))
        self.assertEqual(state["cpuChecked"], "1")
        self.assertEqual(state["gpuChecked"], "0")
        self.assertIn("fixture native CUDA adapter failed", state["reason"])

    def test_overlap_uses_completed_intervals_and_marks_trace_loss(self):
        def work(runner):
            runner.trace_rows = [
                {"start": "100", "count": 5, "checked": 5, "kind": 1, "status": 0,
                 "begin": 10.0, "end": 11.0, "kernel_seconds": 0.0},
                {"start": "105", "count": 7, "checked": 7, "kind": 2, "status": 0,
                 "begin": 10.1, "end": 11.1, "kernel_seconds": 0.9},
                # Failed reservations contribute no completed work or overlap.
                {"start": "112", "count": 3, "checked": 0, "kind": 2, "status": 1,
                 "begin": 9.0, "end": 20.0, "kernel_seconds": 10.0},
            ]
            runner.set_stats(cpu=5, gpu=7, cancelled=1, trace_dropped=3)

        job = self.job(NativeFactory(on_run=work))
        job.run()
        state = job.snapshot()
        self.assertEqual(state["status"], "stopped", state)
        self.assertEqual(state["checked"], "12")
        self.assertTrue(state["hybridExecuted"])
        self.assertTrue(state["hybridTimingLimited"])
        self.assertAlmostEqual(state["hybridOverlapSeconds"], 0.9)
        self.assertGreater(state["hybridKernelOverlapLowerBound"], 0.79)
        self.assertLessEqual(state["hybridKernelOverlapLowerBound"], 0.8)

    def test_native_runtime_failure_does_not_restart_original_range(self):
        def fail(runner):
            runner.set_stats(cpu=5, status=3, error="fixture native worker failed")
            raise RuntimeError("fixture native worker failed")

        factory = NativeFactory(on_run=fail)
        job = self.job(factory)
        job.run()
        state = job.snapshot()
        self.assertEqual(state["status"], "error", state)
        self.assertIn("fixture native worker failed", state["error"])
        self.assertEqual(state["checked"], "5")
        self.assertEqual(state["cpuChecked"], "5")
        self.assertEqual(state["gpuChecked"], "0")
        self.assertIsNone(state["result"])
        self.assertEqual(len(factory.instances[0].resets), 1)
        self.assertTrue(factory.instances[0].closed)

    def test_monitor_snapshot_failure_cancels_and_drains_before_destroy(self):
        instances = []

        class MonitorFailureNative(FakeNative):
            owner = None

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.monitor_failures = 0
                self.access_after_close = []

            def run(self):
                self.owner = threading.get_ident()
                return super().run()

            def stats(self):
                if self.closed:
                    self.access_after_close.append("stats")
                    raise AssertionError("Snapshot used a destroyed context")
                if self.active.is_set() and threading.get_ident() != self.owner:
                    self.monitor_failures += 1
                    raise RuntimeError("fixture progress snapshot failed")
                return super().stats()

            def cancel(self):
                if self.closed:
                    self.access_after_close.append("cancel")
                super().cancel()

        def work(runner):
            runner.set_stats(cpu=5)
            if not runner.cancelled.wait(timeout=5):
                raise AssertionError("Monitor failure did not cancel the native C call")
            # In-flight native work drains before the owner destroys resources.
            runner.set_stats(cpu=6, cancelled=1)

        def factory(message, difficulty, workers=1, **options):
            runner = MonitorFailureNative(message, difficulty, workers, on_run=work, **options)
            instances.append(runner)
            return runner

        job = self.job(factory)
        job.start()
        try:
            state = self.finish(job)
        finally:
            # Release the fixture even if a broken adapter fails the join, so
            # a regression cannot leave a test-owned background job alive.
            if job.thread.is_alive():
                for runner in instances:
                    runner.cancelled.set()
                job.thread.join(timeout=5)
        self.assertEqual(state["status"], "error", state)
        self.assertIn("fixture progress snapshot failed", state["error"])
        self.assertEqual(state["checked"], "6")
        self.assertIsNone(state["result"])
        runner = instances[0]
        self.assertGreater(runner.monitor_failures, 0)
        self.assertGreater(runner.cancel_calls, 0)
        self.assertTrue(runner.closed)
        self.assertFalse(runner.active.is_set())
        self.assertEqual(runner.access_after_close, [])
        self.assertEqual(len(runner.resets), 1)

    def test_wrong_native_proof_is_rejected_and_never_saved(self):
        def wrong(runner):
            runner.set_stats(cpu=1, nonce=runner.resets[-1][0], hash="0" * 64)

        with tempfile.TemporaryDirectory() as directory:
            history = HistoryStore(directory)
            job = self.job(NativeFactory(on_run=wrong), difficulty=1, history=history)
            job.run()
            state = job.snapshot()
            self.assertEqual(state["status"], "error", state)
            self.assertIsNone(state["result"])
            self.assertEqual(history.query()["total"], 0)

    def test_verified_proof_survives_native_close_error_and_is_saved_once(self):
        message = " exact\n\ud55c\uae00\U0001f642\x00 "
        start = (1 << 53) + 1
        nonce = first_proof(message, start)

        def proof(runner):
            runner.set_stats(cpu=nonce - start + 1, nonce=nonce)

        with tempfile.TemporaryDirectory() as directory:
            history = HistoryStore(directory)
            factory = NativeFactory(on_run=proof, close_error=RuntimeError("fixture native close failed"))
            job = self.job(factory, message=message, difficulty=1, start=start, history=history)
            job.run()
            state = job.snapshot()
            self.assertEqual(state["status"], "found", state)
            self.assertEqual(state["result"]["nonce"], str(nonce))
            self.assertIn("fixture native close failed", state["cleanupWarning"])
            self.assertIsNone(state["saveError"])
            self.assertIsNotNone(state["recordId"])
            job.save_result()
            rows = history.query(participant_name="Native tester")
            self.assertEqual(rows["total"], 1)
            self.assertEqual(rows["records"][0]["message"], message)
            self.assertEqual(rows["records"][0]["nonce"], str(nonce))
            self.assertEqual(rows["records"][0]["hash"], digest(message, nonce))


if __name__ == "__main__":
    unittest.main()
