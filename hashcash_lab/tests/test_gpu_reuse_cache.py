"""GPU resource ownership and input isolation without importing CUDA.

The cache retains mutable result buffers, so reuse must be exclusive and a
failed resource must never become the next job's apparently healthy backend.
"""

import subprocess
import sys
import threading
import unittest

from hashcash_lab.gpu_resources import GpuReuseCache
from hashcash_lab import server as lab
from hashcash_lab.tests.test_native_web import FakeNative as WebFakeNative
from hashcash_lab.tests.test_native_web import NativeFactory as WebNativeFactory
from hashcash_lab.tests.test_native_web import first_proof


class FakeGPU:
    name = "isolated cached GPU fixture"

    def __init__(self, message, difficulty, events=None):
        self.message = message.encode("utf-8")
        self.difficulty = difficulty
        self.self_test_passed = True
        self.closed = False
        self.close_calls = 0
        self.search_preparation_seconds = 0.0
        self.metric_resets = 0
        self.events = events

    def reset_search_metrics(self):
        self.search_preparation_seconds = 0.0
        self.metric_resets += 1

    def close(self):
        if self.closed:
            raise AssertionError("Cached GPU was closed twice")
        self.close_calls += 1
        self.closed = True
        if self.events is not None:
            self.events.append(("gpu-close", self))


class GPUFactory:
    def __init__(self, events=None):
        self.instances = []
        self.events = events

    def __call__(self, message, difficulty):
        backend = FakeGPU(message, difficulty, self.events)
        self.instances.append(backend)
        return backend


class FakeNativeRunner:
    def __init__(self, message, difficulty, workers, *, events, fail_attach=False, **options):
        self.message, self.difficulty, self.workers = message, difficulty, workers
        self.events, self.options = events, options
        self.fail_attach = fail_attach
        self.backend = None
        self.closed = False
        self.attach_calls = 0

    def attach_gpu(self, backend):
        if backend.closed:
            raise AssertionError("Native adapter attached a destroyed GPU")
        self.backend = backend
        self.attach_calls += 1
        self.events.append(("attach", self))
        if self.fail_attach:
            raise RuntimeError("fixture native CUDA attachment failed")
        return self

    def close(self):
        if self.closed:
            raise AssertionError("Cached native scheduler was closed twice")
        if self.backend is not None and self.backend.closed:
            raise AssertionError("Native scheduler lost its GPU owners before destruction")
        self.closed = True
        self.events.append(("native-close", self))


class NativeFactory:
    def __init__(self, events, *, fail_first_attach=False):
        self.events, self.fail_first_attach = events, fail_first_attach
        self.instances = []

    def __call__(self, message, difficulty, workers=1, **options):
        runner = FakeNativeRunner(message, difficulty, workers, events=self.events,
                                  fail_attach=self.fail_first_attach and not self.instances,
                                  **options)
        self.instances.append(runner)
        return runner


NATIVE_OPTIONS = {"workers": 4, "cpu_batch": 64_000, "gpu_batch": 67_108_864,
                  "trace_capacity": 8192}


class GpuReuseCacheTests(unittest.TestCase):
    def test_native_owner_and_cuda_adapter_are_reused_and_disposed_before_gpu(self):
        events = []
        gpu_factory, native_factory = GPUFactory(events), NativeFactory(events)
        cache = GpuReuseCache(gpu_factory)
        try:
            with cache.acquire("native cached", 8) as lease:
                runner, reused = lease.native_runner(native_factory, **NATIVE_OPTIONS)
                self.assertFalse(reused)
                self.assertEqual(runner.message, "native cached")
                self.assertEqual(runner.difficulty, 8)
                self.assertEqual(runner.workers, 4)
                self.assertEqual(runner.options, {key: value for key, value in NATIVE_OPTIONS.items()
                                                  if key != "workers"})
                backend = lease.backend
            with cache.acquire("native cached", 8) as lease:
                again, reused = lease.native_runner(native_factory, **NATIVE_OPTIONS)
                self.assertTrue(reused)
                self.assertIs(again, runner)
                self.assertIs(again.backend, backend)
                self.assertEqual(again.attach_calls, 1)
            self.assertFalse(runner.closed)
            self.assertFalse(backend.closed)
            self.assertEqual(len(native_factory.instances), 1)
        finally:
            cache.close()
        self.assertEqual([event for event, _ in events], ["attach", "native-close", "gpu-close"])

    def test_changing_native_worker_configuration_replaces_only_native_owner(self):
        events = []
        gpu_factory, native_factory = GPUFactory(events), NativeFactory(events)
        cache = GpuReuseCache(gpu_factory)
        try:
            with cache.acquire("native workers", 8) as lease:
                old, reused = lease.native_runner(native_factory, **NATIVE_OPTIONS)
                self.assertFalse(reused)
                backend = lease.backend
            with cache.acquire("native workers", 8) as lease:
                fresh, reused = lease.native_runner(native_factory,
                                                    **{**NATIVE_OPTIONS, "workers": 2})
                self.assertFalse(reused)
                self.assertIsNot(fresh, old)
                self.assertTrue(old.closed)
                self.assertFalse(backend.closed)
                self.assertIs(fresh.backend, backend)
                self.assertEqual(fresh.workers, 2)
            with cache.acquire("native workers", 8) as lease:
                same, reused = lease.native_runner(native_factory,
                                                   **{**NATIVE_OPTIONS, "workers": 2})
                self.assertTrue(reused)
                self.assertIs(same, fresh)
            self.assertEqual(len(gpu_factory.instances), 1)
            self.assertEqual(len(native_factory.instances), 2)
            self.assertEqual([event for event, _ in events], ["attach", "native-close", "attach"])
        finally:
            cache.close()

    def test_partial_native_attachment_failure_closes_owner_and_invalidates_backend(self):
        events = []
        gpu_factory = GPUFactory(events)
        native_factory = NativeFactory(events, fail_first_attach=True)
        cache = GpuReuseCache(gpu_factory)
        try:
            with self.assertRaisesRegex(RuntimeError, "attachment failed"):
                with cache.acquire("native attach failure", 8) as lease:
                    lease.native_runner(native_factory, **NATIVE_OPTIONS)
            self.assertTrue(native_factory.instances[0].closed)
            self.assertTrue(gpu_factory.instances[0].closed)
            self.assertEqual([event for event, _ in events],
                             ["attach", "native-close", "gpu-close"])
            with cache.acquire("native attach failure", 8) as lease:
                self.assertFalse(lease.reused)
                runner, reused = lease.native_runner(native_factory, **NATIVE_OPTIONS)
                self.assertFalse(reused)
                self.assertFalse(runner.closed)
            self.assertEqual(len(gpu_factory.instances), 2)
        finally:
            cache.close()

    def test_cache_module_import_does_not_load_optional_cuda_libraries(self):
        source = """
import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in ('cupy', 'cupy_backends', 'numpy'):
        raise AssertionError('Eager optional GPU import: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from hashcash_lab.gpu_resources import GpuReuseCache
"""
        result = subprocess.run([sys.executable, "-c", source], capture_output=True,
                                text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_job_resets_timing_metrics_without_discarding_prepared_resources(self):
        factory = GPUFactory()
        cache = GpuReuseCache(factory)
        try:
            with cache.acquire("preparation accounting", 8) as first:
                backend = first.backend
                prepared = object()
                backend.prepared_kernel = prepared
                backend.search_preparation_seconds = 1.25
            with cache.acquire("preparation accounting", 8) as second:
                self.assertIs(second.backend, backend)
                self.assertIs(second.backend.prepared_kernel, prepared)
                self.assertEqual(second.backend.search_preparation_seconds, 0.0)
                self.assertEqual(second.backend.metric_resets, 2)
        finally:
            cache.close()

    def test_matching_verified_backend_is_constructed_once_and_retained(self):
        factory = GPUFactory()
        cache = GpuReuseCache(factory, capacity=2)
        try:
            with cache.acquire("same input", 8) as first:
                backend = first.backend
                self.assertFalse(first.reused)
                self.assertFalse(backend.closed)
            with cache.acquire("same input", 8) as second:
                self.assertIs(second.backend, backend)
                self.assertTrue(second.reused)
                self.assertFalse(backend.closed)
            self.assertEqual(len(factory.instances), 1)
            self.assertEqual(backend.close_calls, 0)
        finally:
            cache.close()
        self.assertEqual(backend.close_calls, 1)

    def test_cache_key_preserves_exact_unicode_whitespace_and_difficulty(self):
        factory = GPUFactory()
        cache = GpuReuseCache(factory, capacity=8)
        inputs = [(" 한글🙂\n\x00 ", 8), ("한글🙂\n\x00 ", 8),
                  (" 한글🙂\n\x00", 8), (" 한글🙂\r\n\x00 ", 8),
                  (" 한글🙂\n\x00 ", 7)]
        backends = []
        try:
            for message, difficulty in inputs:
                with cache.acquire(message, difficulty) as lease:
                    self.assertFalse(lease.reused)
                    self.assertEqual(lease.backend.message, message.encode("utf-8"))
                    self.assertEqual(lease.backend.difficulty, difficulty)
                    backends.append(lease.backend)
            for key, expected in zip(inputs, backends):
                with cache.acquire(*key) as lease:
                    self.assertTrue(lease.reused)
                    self.assertIs(lease.backend, expected)
            self.assertEqual(len({id(item) for item in backends}), len(inputs))
            self.assertEqual(len(factory.instances), len(inputs))
        finally:
            cache.close()

    def test_default_two_entry_lru_keeps_alternating_inputs_and_evicts_oldest(self):
        factory = GPUFactory()
        cache = GpuReuseCache(factory)
        try:
            with cache.acquire("a", 8) as lease:
                a = lease.backend
            with cache.acquire("b", 8) as lease:
                b = lease.backend
            with cache.acquire("a", 8) as lease:
                self.assertIs(lease.backend, a)
                self.assertTrue(lease.reused)
            with cache.acquire("c", 8) as lease:
                c = lease.backend
            self.assertTrue(b.closed)
            self.assertFalse(a.closed)
            self.assertFalse(c.closed)
            with cache.acquire("b", 8) as lease:
                self.assertIsNot(lease.backend, b)
                self.assertFalse(lease.reused)
            self.assertTrue(a.closed)
            self.assertEqual(len(factory.instances), 4)
        finally:
            cache.close()
        self.assertTrue(all(item.closed for item in factory.instances))

    def test_same_backend_cannot_be_leased_concurrently(self):
        factory = GPUFactory()
        cache = GpuReuseCache(factory)
        attempted, acquired = threading.Event(), threading.Event()
        errors = []

        def borrow():
            attempted.set()
            try:
                with cache.acquire("exclusive", 8) as lease:
                    self.assertFalse(lease.backend.closed)
                    acquired.set()
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=borrow, daemon=True)
        try:
            with cache.acquire("exclusive", 8) as first:
                thread.start()
                self.assertTrue(attempted.wait(timeout=2))
                self.assertFalse(acquired.wait(timeout=0.05))
                self.assertFalse(first.backend.closed)
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "Waiting lease was never released")
            self.assertTrue(acquired.is_set())
            self.assertFalse(errors, errors)
            self.assertEqual(len(factory.instances), 1)
        finally:
            cache.close()

    def test_failed_resource_is_discarded_after_lease_and_recreated(self):
        factory = GPUFactory()
        cache = GpuReuseCache(factory)
        try:
            with cache.acquire("CUDA failure", 8) as lease:
                failed = lease.backend
                lease.invalidate()
                # Invalidation must not destroy buffers an active run owns.
                self.assertFalse(failed.closed)
            self.assertTrue(failed.closed)
            with cache.acquire("CUDA failure", 8) as replacement:
                self.assertFalse(replacement.reused)
                self.assertIsNot(replacement.backend, failed)
                fresh = replacement.backend
            with cache.acquire("CUDA failure", 8) as healthy:
                self.assertTrue(healthy.reused)
                self.assertIs(healthy.backend, fresh)
            self.assertEqual(len(factory.instances), 2)
        finally:
            cache.close()

    def test_failure_of_one_input_preserves_other_prepared_input(self):
        factory = GPUFactory()
        cache = GpuReuseCache(factory)
        try:
            with cache.acquire("healthy message", 8) as lease:
                healthy = lease.backend
            with cache.acquire("failed message", 8) as lease:
                failed = lease.backend
                lease.invalidate()
            self.assertTrue(failed.closed)
            self.assertFalse(healthy.closed)
            with cache.acquire("healthy message", 8) as lease:
                self.assertTrue(lease.reused)
                self.assertIs(lease.backend, healthy)
            self.assertEqual(len(factory.instances), 2)
        finally:
            cache.close()

    def test_factory_failure_does_not_poison_or_lock_the_next_job(self):
        factory = GPUFactory()
        attempts = []

        def fail_once(message, difficulty):
            attempts.append((message, difficulty))
            if len(attempts) == 1:
                raise RuntimeError("fixture CUDA initialization failed")
            return factory(message, difficulty)

        cache = GpuReuseCache(fail_once)
        try:
            with self.assertRaisesRegex(RuntimeError, "initialization failed"):
                with cache.acquire("retry initialization", 8):
                    self.fail("Failed backend was handed to the job")
            with cache.acquire("retry initialization", 8) as lease:
                self.assertFalse(lease.reused)
            with cache.acquire("retry initialization", 8) as lease:
                self.assertTrue(lease.reused)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(len(factory.instances), 1)
        finally:
            cache.close()

    def test_shutdown_waits_for_active_lease_before_closing_and_rejects_new_work(self):
        factory = GPUFactory()
        cache = GpuReuseCache(factory)
        entered, release = threading.Event(), threading.Event()
        close_attempted, closed = threading.Event(), threading.Event()
        errors = []

        def own_resource():
            try:
                with cache.acquire("shutdown", 8) as lease:
                    entered.set()
                    if not release.wait(timeout=3):
                        raise AssertionError("Test did not release active GPU lease")
                    self.assertFalse(lease.backend.closed)
            except BaseException as exc:
                errors.append(exc)

        def shutdown():
            close_attempted.set()
            try:
                cache.close()
                closed.set()
            except BaseException as exc:
                errors.append(exc)

        owner = threading.Thread(target=own_resource, daemon=True)
        closer = threading.Thread(target=shutdown, daemon=True)
        owner.start()
        try:
            self.assertTrue(entered.wait(timeout=2))
            closer.start()
            self.assertTrue(close_attempted.wait(timeout=2))
            self.assertFalse(closed.wait(timeout=0.05))
            self.assertFalse(factory.instances[0].closed)
        finally:
            release.set()
            owner.join(timeout=3)
            if closer.ident is not None:
                closer.join(timeout=3)
            cache.close()
        self.assertFalse(owner.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertFalse(errors, errors)
        self.assertTrue(closed.is_set())
        self.assertEqual(factory.instances[0].close_calls, 1)
        with self.assertRaisesRegex(RuntimeError, "closed|shutdown"):
            with cache.acquire("shutdown", 8):
                self.fail("Closed cache accepted new work")


class CachedSearchJob(lab.SearchJob):
    """Keep fallback tests isolated from actual CPU/GPU hashing."""

    cpu_base = None

    def run_cpu(self, base):
        self.cpu_base = base
        self.update(status="stopped", backend="isolated CPU fallback")


class CachedSearchJobTests(unittest.TestCase):
    def test_returned_native_cleanup_failure_keeps_proof_and_discards_cached_owner(self):
        message = "cached native cleanup warning"
        nonce = first_proof(message, 1)

        def proof_with_cleanup_failure(runner):
            # NativeScheduler can preserve a valid proof while returning a
            # cleanup warning instead of raising. That owner is not reusable.
            runner.set_stats(cpu=1, nonce=nonce, status=3,
                             cleanup_warning="fixture native cleanup failed")

        gpu_factory = GPUFactory()
        native_factory = WebNativeFactory(on_run=proof_with_cleanup_failure)
        cache = GpuReuseCache(gpu_factory)
        try:
            job = CachedSearchJob(lab.SearchConfig(message, 1, nonce, 2, "native"),
                                  native_factory=native_factory, gpu_cache=cache)
            job.run()
            state = job.snapshot()
            self.assertEqual(state["status"], "found", state)
            self.assertTrue(state["result"]["verified"])
            self.assertEqual(state["result"]["nonce"], str(nonce))
            self.assertEqual(state["checked"], "1")
            self.assertEqual(state["cleanupWarning"], "fixture native cleanup failed")
            self.assertTrue(native_factory.instances[0].closed)
            self.assertTrue(gpu_factory.instances[0].closed)
            with cache.acquire(message, 1) as lease:
                self.assertFalse(lease.reused)
            self.assertEqual(len(gpu_factory.instances), 2)
        finally:
            cache.close()

    def test_native_runtime_exception_discards_cached_owner_and_preserves_verified_work(self):
        message = "cached native runtime error"
        nonce = first_proof(message, 1)
        for with_proof in (False, True):
            with self.subTest(with_proof=with_proof):
                def fail(runner):
                    runner.set_stats(cpu=1, nonce=nonce if with_proof else None,
                                     status=3, error="fixture native runtime failure")
                    raise RuntimeError("fixture native runtime failure")

                gpu_factory, native_factory = GPUFactory(), WebNativeFactory(on_run=fail)
                cache = GpuReuseCache(gpu_factory)
                try:
                    job = CachedSearchJob(lab.SearchConfig(message, 1, nonce, 2, "native"),
                                          native_factory=native_factory, gpu_cache=cache)
                    job.run()
                    state = job.snapshot()
                    self.assertEqual(state["checked"], "1")
                    self.assertEqual(state["cpuChecked"], "1")
                    self.assertIsNone(job.cpu_base, "Failed native range restarted on Python CPU")
                    if with_proof:
                        self.assertEqual(state["status"], "found", state)
                        self.assertTrue(state["result"]["verified"])
                        self.assertIn("fixture native runtime failure", state["cleanupWarning"])
                    else:
                        self.assertEqual(state["status"], "error", state)
                        self.assertIn("fixture native runtime failure", state["error"])
                        self.assertIsNone(state["result"])
                    self.assertTrue(native_factory.instances[0].closed)
                    self.assertTrue(gpu_factory.instances[0].closed)
                    self.assertEqual(len(native_factory.instances[0].resets), 1)
                    with cache.acquire(message, 1) as lease:
                        self.assertFalse(lease.reused)
                finally:
                    cache.close()

    def test_swallowed_gpu_launch_and_invalid_proof_failures_invalidate_lease(self):
        for failure in ("launch", "invalid-proof"):
            with self.subTest(failure=failure):
                factory = GPUFactory()

                def make_gpu(message, difficulty):
                    backend = factory(message, difficulty)

                    def scan(start, _count):
                        if failure == "launch":
                            raise RuntimeError("fixture failed launch")
                        return {"checked": 1, "nonce": start, "hash": "0" * 64,
                                "last_nonce": start, "last_hash": "0" * 64}

                    backend.name = "isolated failure GPU"
                    backend.scan_batch = scan
                    return backend

                cache = GpuReuseCache(make_gpu)
                try:
                    job = CachedSearchJob(lab.SearchConfig("failed cache", 64, lab.MAX_NONCE,
                                                          1, "gpu"), gpu_cache=cache)
                    job.run()
                    state = job.snapshot()
                    self.assertEqual(job.cpu_base, lab.MAX_NONCE)
                    self.assertEqual(state["checked"], "0")
                    self.assertIsNone(state["result"])
                    self.assertTrue(factory.instances[0].closed)
                    with cache.acquire("failed cache", 64) as lease:
                        self.assertFalse(lease.reused)
                    self.assertEqual(len(factory.instances), 2)
                finally:
                    cache.close()

    def test_native_gpu_failure_with_verified_proof_discards_failed_resources(self):
        message = "cached verified proof"
        nonce = first_proof(message, 1)

        def proof_after_gpu_failure(runner):
            runner.set_stats(cpu=1, nonce=nonce, gpu_failed=1,
                             gpu_error="fixture failed GPU range retried on CPU")

        gpu_factory = GPUFactory()
        native_factory = WebNativeFactory(on_run=proof_after_gpu_failure)
        cache = GpuReuseCache(gpu_factory)
        try:
            job = CachedSearchJob(lab.SearchConfig(message, 1, nonce, 2, "native"),
                                  native_factory=native_factory, gpu_cache=cache)
            job.run()
            state = job.snapshot()
            self.assertEqual(state["status"], "found", state)
            self.assertTrue(state["result"]["verified"])
            self.assertEqual(state["result"]["nonce"], str(nonce))
            self.assertIn("fixture failed GPU", state["reason"])
            self.assertTrue(native_factory.instances[0].closed)
            self.assertTrue(gpu_factory.instances[0].closed)
            with cache.acquire(message, 1) as lease:
                self.assertFalse(lease.reused)
        finally:
            cache.close()

    def test_swallowed_native_attach_failure_discards_lease_before_c_cpu_fallback(self):
        instances = []

        class AttachFailure(WebFakeNative):
            def attach_gpu(self, _backend):
                raise RuntimeError("fixture native attachment failed")

        def make_native(message, difficulty, workers=1, **options):
            kind = AttachFailure if not instances else WebFakeNative
            runner = kind(message, difficulty, workers, **options)
            instances.append(runner)
            return runner

        gpu_factory = GPUFactory()
        cache = GpuReuseCache(gpu_factory)
        try:
            job = CachedSearchJob(lab.SearchConfig("attach fallback", 64, lab.MAX_NONCE,
                                                  1, "native"),
                                  native_factory=make_native, gpu_cache=cache)
            job.run()
            state = job.snapshot()
            self.assertEqual(state["status"], "exhausted", state)
            self.assertEqual(state["cpuChecked"], "1")
            self.assertEqual(state["gpuChecked"], "0")
            self.assertIn("fixture native attachment failed", state["reason"])
            self.assertTrue(all(runner.closed for runner in instances))
            self.assertTrue(gpu_factory.instances[0].closed)
            with cache.acquire("attach fallback", 64) as lease:
                self.assertFalse(lease.reused)
        finally:
            cache.close()

    def test_cancel_and_restart_reuse_owner_with_fresh_counters_and_proof(self):
        message = "cached cancelled then proof"
        nonce = first_proof(message, 1)
        first_entered = threading.Event()
        calls = []

        def work(runner):
            calls.append(runner)
            if len(calls) == 1:
                runner.set_stats(cpu=3, gpu=4)
                first_entered.set()
                if not runner.cancelled.wait(timeout=3):
                    raise AssertionError("Cancellation did not drain cached scheduler")
                runner.set_stats(cpu=3, gpu=4, cancelled=1)
            else:
                self.assertFalse(runner.cancelled.is_set())
                self.assertEqual(runner.stats()["checked"], 0)
                self.assertIsNone(runner.stats()["nonce"])
                runner.set_stats(cpu=1, nonce=nonce)

        native_factory, gpu_factory = WebNativeFactory(on_run=work), GPUFactory()
        cache = GpuReuseCache(gpu_factory)
        old = CachedSearchJob(lab.SearchConfig(message, 1, nonce, 2, "native"),
                              native_factory=native_factory, gpu_cache=cache)
        try:
            old.start()
            self.assertTrue(first_entered.wait(timeout=3))
            old.stop()
            old.thread.join(timeout=3)
            self.assertFalse(old.thread.is_alive())
            self.assertEqual(old.snapshot()["status"], "stopped", old.snapshot())
            self.assertEqual(old.snapshot()["checked"], "7")
            self.assertIsNone(old.snapshot()["result"])
            restarted = CachedSearchJob(lab.SearchConfig(message, 1, nonce, 2, "native"),
                                        native_factory=native_factory, gpu_cache=cache)
            restarted.run()
            state = restarted.snapshot()
            self.assertEqual(state["status"], "found", state)
            self.assertEqual(state["checked"], "1")
            self.assertEqual(state["cpuChecked"], "1")
            self.assertEqual(state["gpuChecked"], "0")
            self.assertEqual(state["result"]["nonce"], str(nonce))
            self.assertTrue(state["result"]["verified"])
            self.assertEqual(len(native_factory.instances), 1)
            self.assertEqual(len(gpu_factory.instances), 1)
            self.assertEqual(len(native_factory.instances[0].resets), 2)
            self.assertFalse(native_factory.instances[0].closed)
            self.assertFalse(gpu_factory.instances[0].closed)
        finally:
            old.stop()
            if old.thread:
                old.thread.join(timeout=3)
            cache.close()


if __name__ == "__main__":
    unittest.main()
