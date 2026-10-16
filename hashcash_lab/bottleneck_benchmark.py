"""Exclusive same-PC bottleneck diagnostics; never reads participant storage.

Preserve 8cd2e73 sources in work/bottleneck-baseline before running. Timings
include instrumentation equally for all variants; confirmation uses the
ordinary uninstrumented harness separately. Worker timestamps use Windows'
system-wide performance counter, while kernel durations use CUDA events.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import multiprocessing as mp
from multiprocessing.pool import ThreadPool
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import threading

from . import hybrid
from .gpu import CudaBackend, batch_count
from .hybrid_benchmark import _audit, _telemetry, _warm_worker

BASELINE = "8cd2e73f1f18323a6257e0d329c42218bca6a1d5"


def _warm_native_worker(message):
    from .native_cpu_experiment import timed_native_scan
    timed_native_scan(message, 5, 1_000_000_000, 1000, False)
    time.sleep(0.02)
    return os.getpid()


def _warm_native_thread(message):
    _warm_native_worker(message)
    return threading.get_ident()


def hardware_state():
    return subprocess.check_output([
        "nvidia-smi", "--query-gpu=timestamp,power.draw,enforced.power.limit,power.default_limit,"
        "clocks_event_reasons.sw_power_cap,clocks.sm,temperature.gpu",
        "--format=csv,noheader,nounits"], text=True, timeout=10).strip()


def source_hashes():
    names = ("gpu.py", "cpu.py", "hybrid.py", "bottleneck_benchmark.py", "native_cpu_experiment.py")
    files = [Path(__file__).with_name(n) for n in names]
    files.append(Path(__file__).resolve().parents[1] / "experiments" / "cpu_openssl.c")
    return {str(p.relative_to(Path(__file__).resolve().parents[1])): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files}


def preserved_hybrid():
    path = Path("work/bottleneck-baseline/hybrid.py")
    original = subprocess.check_output(["git", "show", f"{BASELINE}:hashcash_lab/hybrid.py"])
    if path.read_bytes() != original:
        raise ValueError("Preserved baseline differs from Git")
    name = "hashcash_lab._bottleneck_preserved"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    # Spawn workers can import this real, unchanged top-level implementation.
    module.timed_cpu_scan = hybrid.timed_cpu_scan
    return module


class ObservedPool:
    def __init__(self, pool):
        self.pool = pool
        self.tasks = []

    def apply_async(self, worker, args):
        record = {"submitted": time.perf_counter(), "start_nonce": str(args[2]), "count": args[3]}
        self.tasks.append(record)

        def delivered(value):
            record["delivered"] = time.perf_counter()
            record["began"], record["ended"] = value[1:]

        task = self.pool.apply_async(worker, args, callback=delivered)

        class ObservedResult:
            def ready(self):
                return task.ready()

            def get(self):
                value = task.get()
                record["received"] = time.perf_counter()
                return value

        return ObservedResult()


def measure(backend, pool, module, workers, cpu_batch, gpu_batch, count, *, keep):
    message, difficulty, start = "You are the best of best!!!!", 5, 1_000_000_000
    traces, proofs = [], []
    observed = ObservedPool(pool)

    def completed(device, batch, timing):
        hybrid.validate_batch(message, difficulty, batch, timing.start, timing.count)
        traces.append(timing)
        if batch["nonce"] is not None:
            proofs.append({"device": device, "nonce": str(batch["nonce"]), "hash": batch["hash"]})

    began = time.perf_counter()
    if workers:
        result = module.HybridCoordinator(message, difficulty, workers, cpu_batch=cpu_batch,
                                          gpu_batch=gpu_batch).run(
            backend, start, count, search=False, pool=observed, on_batch=completed, trace_limit=None)
        if result.gpu_error or result.checked != count:
            raise RuntimeError("Hybrid fallback or inaccurate completed count")
        cpu_checked, gpu_checked = result.cpu_checked, result.gpu_checked
    else:
        cursor = start
        while cursor < start + count:
            take = batch_count(cursor, min(gpu_batch, start + count - cursor))
            first = time.perf_counter()
            batch = backend.scan_batch(cursor, take)
            last = time.perf_counter()
            completed("gpu", batch, hybrid.BatchTiming("gpu", cursor, take, take, first, last,
                                                       batch["kernel_seconds"]))
            cursor += take
        cpu_checked, gpu_checked = 0, count
    ended = time.perf_counter()
    fingerprint = _audit(traces, start, count)
    gpu = sorted((t for t in traces if t.device == "gpu"), key=lambda t: t.started)
    gpu_host = sum(t.finished - t.started for t in gpu)
    kernel = sum(t.kernel_seconds for t in gpu)
    gpu_gap = sum(max(0, b.started - a.finished) for a, b in zip(gpu, gpu[1:]))
    elapsed = ended - began
    last_cpu_end = max((t.finished for t in traces if t.device == "cpu"), default=gpu[-1].finished)
    lower_bound = hybrid.kernel_overlap_lower_bound(traces)
    if workers and not (cpu_checked and gpu_checked and lower_bound > 0):
        raise RuntimeError("No proven CPU/kernel overlap")
    task_summaries = {}
    for name, first, last in (("queue", "submitted", "began"),
                              ("delivery", "ended", "delivered"),
                              ("receive", "delivered", "received")):
        values = [max(0, t[last] - t[first]) for t in observed.tasks]
        task_summaries[name] = {"median_seconds": statistics.median(values) if values else 0,
                                "max_seconds": max(values, default=0)}
    value = {"seconds": elapsed, "hashes_per_second": count / elapsed,
             "cpu_checked": str(cpu_checked), "gpu_checked": str(gpu_checked),
             "gpu_kernel_seconds": kernel, "gpu_host_seconds": gpu_host,
             "gpu_non_kernel_host_seconds": gpu_host - kernel, "gpu_between_batches_seconds": gpu_gap,
             "after_last_gpu_seconds": ended - gpu[-1].finished,
             "actual_cpu_tail_seconds": max(0, last_cpu_end - gpu[-1].finished),
             "outside_gpu_spans_and_gaps_seconds": elapsed - gpu_host - gpu_gap,
             "cpu_task_latency": task_summaries, "cpu_batches": len(observed.tasks),
             "gpu_batches": len(gpu), "kernel_overlap_lower_bound_seconds": lower_bound,
             "coverage_sha256": fingerprint, "proofs_verified": proofs}
    if keep:
        value["cpu_tasks"] = observed.tasks
        value["batch_timings"] = [t.json() for t in traces]
        value["started"], value["finished"] = began, ended
    return value


def compare(stage, repeats, count):
    sources = source_hashes()
    old = preserved_hybrid()
    backend = CudaBackend("You are the best of best!!!!", 5)
    backend._kernel(10).compile()
    native = None
    native_env = None
    if stage in ("native", "native_threads"):
        from .native_cpu_experiment import timed_native_scan
        from .native_cpu_experiment import native_environment
        native = timed_native_scan
        native_env = native_environment()
    if stage == "diagnose":
        variants = {"gpu_only": (hybrid, 0, 10_000, 4_194_304)}
        for workers in (1, 4, 15):
            variants[f"old_{workers}"] = (old, workers, 10_000, 4_194_304)
            variants[f"yield_{workers}"] = (hybrid, workers, 10_000, 4_194_304)
    elif stage == "chunks":
        variants = {"gpu_only": (hybrid, 0, 10_000, 4_194_304)}
        variants.update({f"cpu{workers}_{chunk}": (hybrid, workers, chunk, 4_194_304)
                         for workers in (1, 4) for chunk in (10_000, 50_000, 250_000)})
        variants.update({f"gpu_batch_{batch}": (hybrid, 0, 10_000, batch)
                         for batch in (16_777_216, 67_108_864)})
    else:
        variants = {"gpu_only": (hybrid, 0, 10_000, 16_777_216)}
        variants.update({f"native_{workers}": (hybrid, workers, 50_000, 16_777_216)
                         for workers in (1, 4, 8)})
        if stage == "native_threads":
            variants.update({f"native_thread_{workers}": (hybrid, workers, 50_000, 16_777_216)
                             for workers in (1, 4, 8)})
    maximum = max(row[1] for row in variants.values())
    pool = mp.get_context("spawn").Pool(maximum)
    threads = ThreadPool(maximum) if stage == "native_threads" else None
    try:
        warmed = set()
        for _ in range(10):
            warmed.update(pool.map(_warm_native_worker if native else _warm_worker,
                                   [backend.message.decode()] * maximum * 4, chunksize=1))
            if len(warmed) == maximum:
                break
        if len(warmed) != maximum:
            raise RuntimeError("Some CPU workers were not warmed")
        if native:
            hybrid.timed_cpu_scan = native
        warmed_threads = set()
        if threads:
            for _ in range(10):
                warmed_threads.update(threads.map(_warm_native_thread, [backend.message.decode()] * maximum * 4,
                                                  chunksize=1))
                if len(warmed_threads) == maximum:
                    break
            if len(warmed_threads) != maximum:
                raise RuntimeError("Some native threads were not warmed")
        warm = time.perf_counter()
        while time.perf_counter() - warm < 2:
            backend.scan_batch(1_000_000_000, 4_194_304)
        results = {name: [] for name in variants}
        orders = []
        telemetry = [{"stage": "before", "value": _telemetry()}]
        for repetition in range(repeats):
            order = list(variants) if repetition % 2 == 0 else list(reversed(variants))
            orders.append(order)
            for name in order:
                module, workers, cpu_batch, gpu_batch = variants[name]
                selected_pool = threads if name.startswith("native_thread_") else pool
                state_before = hardware_state()
                sample = measure(backend, selected_pool, module, workers, cpu_batch, gpu_batch, count,
                                 keep=repetition == 0)
                sample["hardware_before"], sample["hardware_after"] = state_before, hardware_state()
                results[name].append(sample)
            telemetry.append({"stage": f"repeat_{repetition}", "value": _telemetry()})
        reference = statistics.median(t["seconds"] for t in results["gpu_only"])
        measurements = {}
        for name, samples in results.items():
            seconds = [s["seconds"] for s in samples]
            measurements[name] = {"median_seconds": statistics.median(seconds),
                                  "median_hashes_per_second": count / statistics.median(seconds),
                                  "speedup_vs_gpu_only": reference / statistics.median(seconds),
                                  "coefficient_of_variation": statistics.stdev(seconds) / statistics.mean(seconds),
                                  "pairs_faster": sum(a["seconds"] < b["seconds"] for a, b in
                                                      zip(samples, results["gpu_only"])), "samples": samples}
        if sources != source_hashes():
            raise RuntimeError("Source changed during measurement")
        return {"baseline_commit": BASELINE, "measured_at_utc": datetime.now(timezone.utc).isoformat(),
                "stage": stage, "count_per_sample": str(count), "repeats": repeats,
                "environment": {"python": platform.python_version(), "platform": platform.platform(),
                                "cupy": backend.cp.__version__, "gpu": backend.name, "cpu_count": os.cpu_count()},
                "warmed_worker_pids": sorted(warmed), "warmed_thread_ids": sorted(warmed_threads),
                "native_environment": native_env, "orders": orders, "telemetry": telemetry,
                "implementation_sha256": sources,
                "variants": {n: {"scheduler": "old" if row[0] is old else "yield", "workers": row[1],
                                  "cpu_batch": row[2], "gpu_batch": row[3],
                                  "executor": "threads" if n.startswith("native_thread_") else "processes"}
                             for n, row in variants.items()},
                "method": "Identical finite range, search=False, alternating order. Callback instrumentation "
                          "applied to every hybrid. CUDA-event kernel timing. Proofs/coverage independently checked. "
                          "No participant storage or other intentional load. Idle warmed Pool identical in GPU-only.",
                "measurements": measurements}
    finally:
        pool.terminate()
        pool.join()
        if threads:
            threads.terminate()
            threads.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("diagnose", "chunks", "native", "native_threads"), default="diagnose")
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--count", type=int, default=1_073_741_824)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 5:
        parser.error("at least 5 repeats required")
    result = compare(args.stage, args.repeats, args.count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({n: {k: v[k] for k in ("median_hashes_per_second", "speedup_vs_gpu_only", "pairs_faster")}
                      for n, v in result["measurements"].items()}, indent=2))


if __name__ == "__main__":
    mp.freeze_support()
    main()
