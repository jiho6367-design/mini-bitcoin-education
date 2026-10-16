"""Opt-in real CUDA tests for leases crossing actual Python owner threads."""
import hashlib
import os
import threading
import time
import unittest

from hashcash_lab.gpu import CudaBackend
from hashcash_lab.gpu_resources import GpuReuseCache
from hashcash_lab.server import MAX_NONCE, SearchConfig, SearchJob


@unittest.skipUnless(os.environ.get("HASHCASH_TEST_NATIVE_CUDA") == "1",
                     "Requires the built C scheduler and real CUDA")
class GpuReuseHardwareTests(unittest.TestCase):
    def join(self, job):
        job.thread.join(timeout=45)
        self.assertFalse(job.thread.is_alive(), job.snapshot())
        state = job.snapshot()
        self.assertNotEqual(state["status"], "error", state)
        self.assertIsNone(state["cleanupWarning"], state)
        self.assertIn("GPU", state["backend"], state)
        self.assertEqual(int(state["checked"]), int(state["cpuChecked"]) + int(state["gpuChecked"]))
        if state["result"]:
            proof = state["result"]
            expected = hashlib.sha256(state["message"].encode("utf-8")
                                      + proof["nonce"].encode("ascii")).hexdigest()
            self.assertEqual(proof["hash"], expected)
            self.assertTrue(expected.startswith("0" * state["difficulty"]))
        return state

    def test_same_native_owner_transfers_to_distinct_live_threads_and_gpu_mode(self):
        made = []

        def factory(message, difficulty):
            backend = CudaBackend(message, difficulty)
            made.append(backend)
            return backend

        cache = GpuReuseCache(factory)
        message = " \n캐시🙂\x00 "
        first_done, hold_first_thread = threading.Event(), threading.Event()
        config = SearchConfig(message, 3, MAX_NONCE - 100_000, 2, "native")
        first = SearchJob(config, gpu_cache=cache)

        def first_owner():
            try:
                first.run()
            finally:
                first_done.set()
                hold_first_thread.wait(timeout=45)

        first.thread = threading.Thread(target=first_owner, daemon=True)
        first.thread.start()
        try:
            self.assertTrue(first_done.wait(timeout=45))
            self.assertEqual(first.snapshot()["status"], "found", first.snapshot())
            functions = made[0]._native_functions
            context = made[0]._cuda_context
            self.assertTrue(context)
            second = SearchJob(config, gpu_cache=cache)
            second.start()
            state = self.join(second)
            self.assertNotEqual(state["jobThreadId"], first.snapshot()["jobThreadId"])
            self.assertTrue(state["gpuResourcesReused"])
            self.assertTrue(state["nativeResourcesReused"])
            self.assertTrue(state["nativeKernelsReused"])
            self.assertEqual(len(made), 1)
            self.assertIs(made[0]._native_functions, functions)
            self.assertEqual(made[0]._cuda_context, context)
            gpu = SearchJob(SearchConfig(message, 3, MAX_NONCE - 100_000, 2, "gpu"), gpu_cache=cache)
            gpu.start()
            gpu_state = self.join(gpu)
            self.assertTrue(gpu_state["gpuResourcesReused"])
            self.assertEqual(gpu_state["searchPreparationElapsed"], 0)
            self.assertEqual(len(made), 1)
        finally:
            hold_first_thread.set()
            first.thread.join(timeout=45)
            cache.close()
        self.assertTrue(made[0]._closed)

    def test_cancel_then_reset_reuses_resources_without_result_or_count_leak(self):
        cache = GpuReuseCache(CudaBackend)
        message = "취소🙂\x00 "
        active = None
        try:
            warm = SearchJob(SearchConfig(message, 64, MAX_NONCE - 1000, 2, "native"), gpu_cache=cache)
            warm.start()
            self.assertEqual(self.join(warm)["checked"], "1001")
            active = SearchJob(SearchConfig(message, 64, 1_000_000_000, 2, "native"), gpu_cache=cache)
            active.start()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = active.snapshot()
                if int(state["cpuChecked"]) and int(state["gpuChecked"]) >= 5_000_000:
                    break
                time.sleep(0.01)
            else:
                self.fail(f"Cached native run did not use both devices: {active.snapshot()}")
            active.stop()
            stopped = self.join(active)
            self.assertEqual(stopped["status"], "stopped")
            self.assertIsNone(stopped["result"])
            self.assertGreater(stopped["hybridKernelOverlapLowerBound"], 0)
            restart = SearchJob(SearchConfig(message, 64, MAX_NONCE - 100_002, 2, "native"), gpu_cache=cache)
            restart.start()
            state = self.join(restart)
            self.assertEqual(state["status"], "exhausted")
            self.assertEqual(state["checked"], "100003")
            self.assertIsNone(state["result"])
            self.assertIsNone(state["proofElapsed"])
            self.assertTrue(state["gpuResourcesReused"])
            self.assertTrue(state["nativeResourcesReused"])
            self.assertNotEqual(state["id"], stopped["id"])
        finally:
            if active is not None:
                active.stop()
                if active.thread:
                    active.thread.join(timeout=15)
            cache.close()


if __name__ == "__main__":
    unittest.main()
