"""Real native proof/cancel/restart checks; not a performance ranking."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time

from .gpu import CudaBackend
from .native_scheduler_experiment import NativeScheduler


def verify(traces, message, difficulty, stats):
    reserved_end = -1
    checked = 0
    for t in sorted(traces, key=lambda item: int(item["start"])):
        first, count, completed = int(t["start"]), int(t["count"]), int(t["checked"])
        if t["status"] or first <= reserved_end or not 1 <= completed <= count:
            raise RuntimeError("Invalid or overlapping native proof/cancel trace")
        reserved_end = first + count - 1
        last = first + completed - 1
        digest = hashlib.sha256(message.encode() + str(last).encode("ascii")).hexdigest()
        if digest != t["last_hash"]:
            raise RuntimeError("Independent native endpoint hash failed")
        if t["found"]:
            nonce = int(t["nonce"])
            value = hashlib.sha256(message.encode() + str(nonce).encode("ascii")).hexdigest()
            if not first <= nonce <= last or value != t["hash"] or not value.startswith("0" * difficulty):
                raise RuntimeError("Independent native proof failed")
        checked += completed
    if checked != int(stats["checked"]) or stats["gpu_failed"] or stats["trace_dropped"]:
        raise RuntimeError("Native trace accounting differs from final snapshot")


def run(workers=4, cpu_batch=64_000):
    message, difficulty, start, count = "You are the best of best!!!!", 8, 1_000_000_000, 4_294_967_296
    setup_start = time.perf_counter()
    backend = CudaBackend(message, difficulty)
    backend_setup = time.perf_counter() - setup_start
    results = []
    for w in (0, workers):
        setup_start = time.perf_counter()
        with NativeScheduler(message, difficulty, w, cpu_batch=cpu_batch) as runner:
            runner.attach_gpu(backend)
            setup = time.perf_counter() - setup_start
            runner.reset(start, count, search=True)
            began = time.perf_counter()
            stats = runner.run()
            total = time.perf_counter() - began
            traces = runner.traces()
            verify(traces, message, difficulty, stats)
            if not stats["found"] or stats["cancelled"]:
                raise RuntimeError("Expected real hard-target proof was not found")
            results.append({"workers": w, "stats": stats, "traces": traces,
                            "scheduler_and_kernel_setup_seconds": setup,
                            "proof_and_drain_wall_seconds": total})
    # Cancellation on another CPU thread exercises CUDA context push/pop and
    # GIL release in the same native entry point, then reuses the coordinator.
    with NativeScheduler(message, 64, workers, cpu_batch=cpu_batch) as runner:
        negative = CudaBackend(message, 64)
        runner.attach_gpu(negative)
        runner.reset(start, count, search=True)
        output, failures = [], []

        def calculate():
            try:
                output.append(runner.run())
            except BaseException as error:
                failures.append(repr(error))

        thread = threading.Thread(target=calculate)
        thread.start()
        deadline = time.perf_counter() + 20
        while not runner.stats()["checked"] and thread.is_alive() and time.perf_counter() < deadline:
            time.sleep(0.001)
        cancelled_at = time.perf_counter()
        runner.cancel()
        thread.join(timeout=20)
        if thread.is_alive() or failures or not output or not output[0]["cancelled"]:
            raise RuntimeError(f"Native cancellation failed: {failures}")
        cancellation = {"stats": output[0], "drain_after_cancel_seconds": time.perf_counter() - cancelled_at,
                        "traces": runner.traces()}
        verify(cancellation["traces"], message, 64, output[0])
        restarted = runner.reset(999_990, 100_003, search=False).run()
        verify(runner.traces(), message, 64, restarted)
        if restarted["checked"] != 100_003 or restarted["cancelled"] or restarted["found"]:
            raise RuntimeError("Native reset after cancellation retained stale state")
    return {"measured_at_utc": datetime.now(timezone.utc).isoformat(), "cuda_backend_setup_seconds": backend_setup,
            "proof_cases": results,
            "cancellation": cancellation, "restart": restarted,
            "participant_storage_used": False,
            "method": "Functional real CUDA proof and cancellation checks; one sample per mode, "
                      "preexisting compiler disk caches. Times are phase observations, not a comparative "
                      "performance distribution. Proof search needs no advance knowledge of its answer location."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cpu-batch", type=int, default=64_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = run(args.workers, args.cpu_batch)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"proofs": [{"workers": c["workers"], "nonce": c["stats"]["nonce"]}
                                 for c in document["proof_cases"]], "cancelled": True, "restart": True}))


if __name__ == "__main__":
    main()
