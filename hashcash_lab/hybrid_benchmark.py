"""Compare GPU-only and real CPU+GPU work on identical finite nonce ranges.

Run only when other GPU/CPU benchmarks are idle. Compilation and CPU process
startup are excluded. A second, separate application test covers cold startup.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import statistics
import subprocess
import time

from .cpu import MAX_NONCE, scan_batch
from .gpu import CudaBackend, batch_count
from .hybrid import (BatchTiming, HybridCoordinator, kernel_overlap_lower_bound as kernel_overlap_lower_bound_seconds,
                     overlap_seconds, validate_batch)
from .server import gpu_batch_size


def _warm_worker(message: str):
    scan_batch(message, 5, 1_000_000_000, 1000, search=False)
    time.sleep(0.02)
    return os.getpid()


def _telemetry():
    try:
        value = subprocess.run([
            "nvidia-smi", "--query-gpu=name,temperature.gpu,power.draw,clocks.sm,utilization.gpu",
            "--format=csv,noheader,nounits",
        ], capture_output=True, text=True, check=True, timeout=10)
        return value.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)


def _audit(timings: list[BatchTiming], start: int, count: int) -> str:
    cursor = start
    fingerprint = hashlib.sha256()
    for item in sorted(timings, key=lambda entry: entry.start):
        if item.start != cursor or item.count != item.checked:
            raise RuntimeError("Fixed-work allocation has a gap, overlap, or partial batch")
        cursor += item.checked
        fingerprint.update(f"{item.device}:{item.start}:{item.count}\n".encode("ascii"))
    if cursor != start + count:
        raise RuntimeError("Fixed-work coverage differs from the requested nonce range")
    return fingerprint.hexdigest()


def measure(backend, message: str, difficulty: int, start: int, count: int,
            workers: int, pool, cpu_batch: int, gpu_batch: int, *, keep_timings: bool):
    timings = []
    proofs = []
    proof_count = 0

    def completed(device, batch, timing):
        nonlocal proof_count
        nonce = batch.get("nonce")
        if nonce is not None:
            reference = hashlib.sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
            if reference != batch["hash"] or not reference.startswith("0" * difficulty):
                raise RuntimeError("Independent proof verification failed")
            proof_count += 1
            if len(proofs) < 8:
                proofs.append({"device": device, "nonce": str(nonce), "hash": reference})
        timings.append(timing)

    began = time.perf_counter()
    if workers:
        result = HybridCoordinator(message, difficulty, workers,
                                   cpu_batch=cpu_batch, gpu_batch=gpu_batch).run(
            backend, start, count, search=False, pool=pool, on_batch=completed, trace_limit=None,
        )
        elapsed = time.perf_counter() - began
        if result.gpu_error:
            raise RuntimeError(f"Hybrid measurement unexpectedly fell back: {result.gpu_error}")
        cpu_checked, gpu_checked = result.cpu_checked, result.gpu_checked
        if result.checked != count:
            raise RuntimeError("Hybrid completed count differs from the fixed workload")
    else:
        cursor = start
        while cursor < start + count:
            take = batch_count(cursor, min(gpu_batch, start + count - cursor))
            first = time.perf_counter()
            batch = backend.scan_batch(cursor, take)
            last = time.perf_counter()
            validate_batch(message, difficulty, batch, cursor, take)
            completed("gpu", batch, BatchTiming("gpu", cursor, take, batch["checked"], first, last,
                                                batch.get("kernel_seconds", 0.0)))
            cursor += batch["checked"]
        elapsed = time.perf_counter() - began
        cpu_checked, gpu_checked = 0, count
    # Independent coverage sorting/audit and overlap calculation are outside
    # the measured interval for both variants. The production scheduler and
    # all in-loop validation/IPC/readback overhead remain inside the interval.
    coverage = _audit(timings, start, count)
    overlap = overlap_seconds(timings)
    kernel_overlap = kernel_overlap_lower_bound_seconds(timings)
    if workers and (cpu_checked == 0 or gpu_checked == 0 or kernel_overlap <= 0):
        raise RuntimeError("Both devices did not complete genuinely overlapping work")
    value = {
        "seconds": elapsed, "hashes_per_second": count / elapsed,
        "checked": str(count), "cpu_checked": str(cpu_checked), "gpu_checked": str(gpu_checked),
        "cpu_batches": sum(item.device == "cpu" for item in timings),
        "gpu_batches": sum(item.device == "gpu" for item in timings),
        "host_interval_overlap_seconds": overlap,
        "kernel_overlap_lower_bound_seconds": kernel_overlap,
        "unique_coverage_sha256": coverage,
        "proofs_verified": proof_count, "proof_samples": proofs,
    }
    if keep_timings:
        value["batch_timings"] = [item.json() for item in timings]
    return value


def compare(*, message: str, difficulty: int, start: int, count: int,
            workers: list[int], repeats: int, cpu_batch: int):
    if repeats < 5 or count < 1 or start < 0 or start + count - 1 > MAX_NONCE:
        raise ValueError("Need repeats>=5 and a nonempty uint64 range")
    if not workers or any(type(value) is not int or not 1 <= value <= (os.cpu_count() or 1) for value in workers):
        raise ValueError("Worker counts must fit this CPU")
    def source_hashes():
        return {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                for name in ("gpu.py", "cpu.py", "hybrid.py", "server.py", "hybrid_benchmark.py")}
    sources = source_hashes()
    began = time.perf_counter()
    backend = CudaBackend(message, difficulty)
    backend._kernel(len(str(start))).compile()
    maximum_workers = max(workers)
    pool = mp.get_context("spawn").Pool(maximum_workers)
    try:
        warmed_pids = set()
        for _ in range(10):
            warmed_pids.update(pool.map(_warm_worker, [message] * (maximum_workers * 4), chunksize=1))
            if len(warmed_pids) == maximum_workers:
                break
        if len(warmed_pids) != maximum_workers:
            raise RuntimeError("Not all CPU processes finished warmup; startup exclusion unproven")
        setup = time.perf_counter() - began
        batch = gpu_batch_size(difficulty)
        names = {"gpu_only": 0, **{f"hybrid_{value}": value for value in workers}}
        # Warm the GPU after process creation, keeping idle Pool ownership
        # identical during GPU-only and hybrid timing.
        warmup = time.perf_counter()
        while time.perf_counter() - warmup < 1:
            backend.scan_batch(start, batch)
        results = {name: [] for name in names}
        orders = []
        telemetry = [{"stage": "before", "value": _telemetry()}]
        for repetition in range(repeats):
            order = list(names) if repetition % 2 == 0 else list(reversed(names))
            orders.append(order)
            for name in order:
                results[name].append(measure(
                    backend, message, difficulty, start, count, names[name], pool,
                    cpu_batch, batch, keep_timings=repetition == 0,
                ))
            telemetry.append({"stage": f"after_repeat_{repetition + 1}", "value": _telemetry()})
        reference = statistics.median(item["seconds"] for item in results["gpu_only"])
        measurements = {}
        for name, samples in results.items():
            seconds = [item["seconds"] for item in samples]
            median = statistics.median(seconds)
            measurements[name] = {
                "median_seconds": median, "median_hashes_per_second": count / median,
                "speedup_vs_gpu_only": reference / median,
                "faster_than_gpu_only_repeats": sum(sample["seconds"] < reference_sample["seconds"]
                    for sample, reference_sample in zip(samples, results["gpu_only"])),
                "median_paired_speedup": statistics.median(reference_sample["seconds"] / sample["seconds"]
                    for sample, reference_sample in zip(samples, results["gpu_only"])),
                "min_seconds": min(seconds), "max_seconds": max(seconds),
                "coefficient_of_variation": statistics.stdev(seconds) / statistics.mean(seconds),
                "samples": samples,
            }
        properties = backend.cp.cuda.runtime.getDeviceProperties(0)
        if source_hashes() != sources:
            raise RuntimeError("Implementation changed during the benchmark; discard this timing run")
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True, timeout=10).stdout.strip()
        return {
            "measured_at_utc": datetime.now(timezone.utc).isoformat(),
            "environment": {"platform": platform.platform(), "python": platform.python_version(),
                            "cupy": backend.cp.__version__, "gpu": backend.name,
                            "cpu_count": os.cpu_count(), "gpu_sm_count": properties["multiProcessorCount"]},
            "message": message, "utf8_bytes": len(message.encode("utf-8")),
            "difficulty": difficulty, "start_nonce": str(start), "count_per_sample": str(count),
            "cpu_batch": cpu_batch, "gpu_batch": batch, "repeats": repeats,
            "baseline_commit": commit, "implementation_source_sha256": sources,
            "arguments": {"message": message, "difficulty": difficulty, "start": str(start),
                          "count": str(count), "workers": workers, "repeats": repeats,
                          "cpu_batch": cpu_batch},
            "concurrency_condition": "Exclusive benchmark window; no other intentional GPU/CPU workload. "
                                     "Windows desktop applications remain present; GPU telemetry recorded.",
            "cpu_clock": time.get_clock_info("perf_counter").implementation,
            "setup_seconds_excluded": setup, "warmed_cpu_worker_pids": sorted(warmed_pids),
            "orders": orders, "telemetry": telemetry, "measurements": measurements,
            "method": "Identical finite nonce set for every variant, search=False; same live CUDA backend and "
                      "same warmed spawn pool. Independent proof checks and actual production coordinator "
                      "IPC/readback/validation costs included. Sorted unique-coverage audit and overlap "
                      "calculation excluded for every variant. Both devices must finish overlapping work.",
            "limitations": [
                "One PC/input/range; no universal fastest hardware or strategy claim.",
                "Startup, compilation, process creation, HTTP and SQLite persistence excluded.",
                "GPU host interval includes launch/readback. The positive kernel-overlap lower bound "
                "subtracts all non-kernel host time using actual CUDA-event kernel durations.",
                "CPU timestamps come from actual worker execution, not submission or queue time.",
                "Fixed-work hybrid waits for its CPU tail; real proof search has separately tested cancellation/cleanup.",
                "Only the first sample retains full batch timings; every sample audits exact unique coverage.",
            ],
        }
    finally:
        pool.terminate()
        pool.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", default="You are the best of best!!!!")
    parser.add_argument("--difficulty", type=int, default=5)
    parser.add_argument("--start", type=int, default=1_000_000_000)
    parser.add_argument("--count", type=int, default=1_073_741_824)
    parser.add_argument("--workers", default="1,2,4,8,15")
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--cpu-batch", type=int, default=10_000)
    parser.add_argument("--output", type=Path, default=Path("docs/hashcash-hybrid-benchmark.json"))
    args = parser.parse_args()
    document = compare(message=args.message, difficulty=args.difficulty, start=args.start,
                       count=args.count, workers=[int(value) for value in args.workers.split(",")],
                       repeats=args.repeats, cpu_batch=args.cpu_batch)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: {key: entry[key] for key in ("median_hashes_per_second", "speedup_vs_gpu_only", "coefficient_of_variation")}
                      for name, entry in document["measurements"].items()}, indent=2))


if __name__ == "__main__":
    mp.freeze_support()
    main()
