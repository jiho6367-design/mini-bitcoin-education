"""Same-PC fixed-work comparison of Python vs fully native CPU/CUDA control.

Only setup and independent post-run audits use Python for native variants.
Do not run concurrently with other GPU/CPU performance work. No participant
database is imported or used. See the finite experiment plan in docs/.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import time

from .gpu import CudaBackend, batch_count
from .hybrid import BatchTiming, kernel_overlap_lower_bound
from .bottleneck_benchmark import hardware_state

BASELINE = "3d9091a048aa34139682f4a8bf190a20004d0e30"
GPU_BATCH = 67_108_864
PROFILES = {
    "ascii": ("You are the best of best!!!!", 8, 1_000_000_000),
    "korean": ("해시캐시 성능 검증", 8, 1_000_000_000),
    "digits20": ("You are the best of best!!!!", 8, 10_000_000_000_000_000_000),
}


def sources():
    root = Path(__file__).resolve().parents[1]
    names = ("hashcash_lab/gpu.py", "hashcash_lab/native_scheduler_experiment.py",
             "hashcash_lab/native_scheduler_benchmark.py", "experiments/native_scheduler.c",
             "experiments/native_scheduler.h", "experiments/native_cuda_driver.c",
             "experiments/native_cuda_driver.h")
    return {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in names}


def audit(traces, message, difficulty, start, count, cpu_expected, qpc_frequency):
    cursor = start
    proof_samples, cpu, gpu = [], 0, 0
    fingerprint = hashlib.sha256()
    for t in sorted(traces, key=lambda x: int(x["start"])):
        if t["status"] or int(t["start"]) != cursor or int(t["checked"]) != int(t["count"]):
            raise RuntimeError("Fixed-work native coverage has a gap, overlap or failed/partial batch")
        checked = int(t["checked"])
        last = cursor + checked - 1
        reference = hashlib.sha256(message.encode("utf-8") + str(last).encode("ascii")).hexdigest()
        if reference != t["last_hash"]:
            raise RuntimeError("Independent last-hash verification failed")
        if t["found"]:
            nonce = int(t["nonce"])
            reference = hashlib.sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
            if not cursor <= nonce <= last or reference != t["hash"] or not reference.startswith("0" * difficulty):
                raise RuntimeError("Independent native proof verification failed")
            proof_samples.append({"device": t["kind"], "nonce": str(nonce), "hash": reference})
        if t["kind"] == 1:
            cpu += checked
        else:
            gpu += checked
        fingerprint.update(f"{t['kind']}:{cursor}:{checked}\n".encode())
        cursor += checked
    if cursor != start + count or cpu + gpu != count or (cpu_expected and (not cpu or not gpu)):
        raise RuntimeError("Not all fixed work completed on expected devices")
    timings = [BatchTiming("cpu" if t["kind"] == 1 else "gpu", int(t["start"]), int(t["count"]),
                           int(t["checked"]), t["begin"], t["end"], t["kernel_seconds"]) for t in traces]
    lower_bound = kernel_overlap_lower_bound(timings)
    # QPC timestamps on different threads can have an ordering uncertainty of
    # one tick. Subtract a conservative two ticks per retained GPU interval.
    lower_bound = max(0, lower_bound - sum(t["kind"] == 2 for t in traces) * 2 / qpc_frequency)
    if cpu_expected and lower_bound <= 0:
        raise RuntimeError("Actual CPU/GPU kernel overlap was not established")
    return {"cpu_checked": str(cpu), "gpu_checked": str(gpu), "coverage_sha256": fingerprint.hexdigest(),
            "proofs_verified": len(proof_samples), "proof_samples": proof_samples,
            "kernel_overlap_lower_bound_seconds": lower_bound}


def python_gpu(backend, message, difficulty, start, count):
    traces, cursor = [], start
    began = time.perf_counter()
    while cursor < start + count:
        take = batch_count(cursor, min(GPU_BATCH, start + count - cursor))
        first = time.perf_counter()
        result = backend.scan_batch(cursor, take)
        last = time.perf_counter()
        if result["checked"] != take or result["last_nonce"] != cursor + take - 1:
            raise RuntimeError("Python GPU reference completed an invalid range")
        traces.append({"kind": 2, "worker": 2 ** 32 - 1, "status": 0,
                       "start": str(cursor), "count": str(take), "checked": str(take),
                       "begin": first, "end": last, "kernel_seconds": result["kernel_seconds"],
                       "found": bool(result["nonce"] is not None), "nonce": str(result["nonce"] or 0),
                       "hash": result["hash"], "last_hash": result["last_hash"]})
        cursor += take
    elapsed = time.perf_counter() - began
    return elapsed, traces, {"begin": began, "end": began + elapsed, "elapsed": elapsed,
                             "gpu_kernel_seconds": sum(t["kernel_seconds"] for t in traces)}


def summarize(results, count):
    reference = statistics.median(s["seconds"] for s in results["python_gpu"])
    native_reference = statistics.median(s["seconds"] for s in results["c_gpu"])
    summary = {}
    for name, samples in results.items():
        seconds = [s["seconds"] for s in samples]
        median = statistics.median(seconds)
        summary[name] = {"median_seconds": median, "median_hashes_per_second": count / median,
                         "speedup_vs_python_gpu": reference / median,
                         "speedup_vs_c_gpu": native_reference / median,
                         "pairs_faster_python": sum(s["seconds"] < b["seconds"] for s, b in
                                                   zip(samples, results["python_gpu"])),
                         "pairs_faster_c_gpu": sum(s["seconds"] < b["seconds"] for s, b in
                                                  zip(samples, results["c_gpu"])),
                         "coefficient_of_variation": statistics.stdev(seconds) / statistics.mean(seconds),
                         "min_seconds": min(seconds), "max_seconds": max(seconds), "samples": samples}
    return summary


def compare(profile, stage, repeats, count, workers, cpu_batch):
    from .native_scheduler_experiment import NativeScheduler, native_environment
    original = subprocess.check_output(["git", "show", f"{BASELINE}:hashcash_lab/gpu.py"])
    if original != Path("hashcash_lab/gpu.py").read_bytes():
        raise RuntimeError("GPU implementation differs from preserved baseline")
    signatures = sources()
    message, difficulty, start = PROFILES[profile]
    if not 1 <= count < 2 ** 64 or start + count - 1 >= 2 ** 64:
        raise ValueError("Count must fit a nonempty uint64 range")
    began = time.perf_counter()
    backend = CudaBackend(message, difficulty)
    backend._kernel(len(str(start))).compile()
    gpu_setup = time.perf_counter() - began
    configurations = [(0, cpu_batch)]
    configurations += ([(w, b) for w in (1, 4, 8) for b in (64_000, 250_000, 1_000_000)]
                       if stage == "screen" else [(workers, cpu_batch)])
    runners, setup = {}, {}
    try:
        for w, b in configurations:
            name = "c_gpu" if not w else f"c_hybrid_{w}_{b}"
            began = time.perf_counter()
            runner = NativeScheduler(message, difficulty, w, cpu_batch=b, gpu_batch=GPU_BATCH,
                                     trace_capacity=65536).attach_gpu(backend)
            runners[name] = runner
            if runner.stats()["workers_ready"] != w:
                raise RuntimeError("A persistent native worker was not ready")
            setup[name] = time.perf_counter() - began
            # Warm actual CPU hashing in every worker, not just thread creation.
            if w:
                warmed = set()
                for _ in range(10):
                    runner.reset(start, max(w * b * 4, 1), search=False)
                    runner.run_with_callback(None)
                    warmed.update(int(t["worker"]) for t in runner.traces() if t["kind"] == 1)
                    if warmed == set(range(w)):
                        break
                if warmed != set(range(w)):
                    raise RuntimeError("A native CPU worker never executed warmup")
            runner.reset(start, GPU_BATCH * 4, search=False)
            runner.run()
        warm = time.perf_counter()
        while time.perf_counter() - warm < 2:
            backend.scan_batch(start, GPU_BATCH)
        names = ["python_gpu", *runners]
        results = {n: [] for n in names}
        orders = []
        for repetition in range(repeats):
            order = names if repetition % 2 == 0 else list(reversed(names))
            orders.append(order)
            for name in order:
                before = hardware_state()
                if name == "python_gpu":
                    elapsed, traces, stats = python_gpu(backend, message, difficulty, start, count)
                else:
                    runner = runners[name]
                    runner.reset(start, count, search=False, tail_guard=0)
                    began = time.perf_counter()
                    stats = runner.run()
                    elapsed = time.perf_counter() - began
                    traces = runner.traces()
                    if stats["trace_dropped"] or stats["gpu_failed"] or int(stats["checked"]) != count:
                        raise RuntimeError("Native benchmark failed, fell back or lost its full trace")
                after = hardware_state()
                value = audit(traces, message, difficulty, start, count,
                              name not in ("python_gpu", "c_gpu"), stats.get("qpc_frequency", 10_000_000))
                gpu = [t for t in traces if t["kind"] == 2]
                cpu = [t for t in traces if t["kind"] == 1]
                gpu_host = sum(t["end"] - t["begin"] for t in gpu)
                kernel = sum(t["kernel_seconds"] for t in gpu)
                last_gpu = max(t["end"] for t in gpu)
                value.update({"seconds": elapsed, "hashes_per_second": count / elapsed,
                              "native_elapsed": stats["elapsed"], "gpu_kernel_seconds": kernel,
                              "gpu_host_seconds": gpu_host,
                              "gpu_non_kernel_host_seconds": gpu_host - kernel,
                              "gpu_between_batch_gap_seconds": sum(max(0, b["begin"] - a["end"])
                                  for a, b in zip(gpu, gpu[1:])),
                              "after_last_gpu_seconds": max(0, stats["end"] - last_gpu),
                              "actual_cpu_tail_seconds": max(0, max((t["end"] for t in cpu), default=last_gpu) - last_gpu),
                              "cpu_task_seconds": sum(t["end"] - t["begin"] for t in cpu),
                              "cpu_batches": len(cpu), "gpu_batches": len(gpu),
                              "hardware_before": before, "hardware_after": after})
                if repetition == 0:
                    value["batch_timings"], value["native_stats"] = traces, stats
                results[name].append(value)
        if sources() != signatures:
            raise RuntimeError("Source changed during measurement")
        return {"baseline_commit": BASELINE, "measured_at_utc": datetime.now(timezone.utc).isoformat(),
                "profile": profile, "message": message, "difficulty": difficulty, "start": str(start),
                "count_per_sample": str(count), "repeats": repeats, "orders": orders,
                "environment": {"platform": platform.platform(), "python": platform.python_version(),
                                "cupy": backend.cp.__version__, "gpu": backend.name},
                "native_environment": native_environment(), "source_sha256": signatures,
                "setup_seconds_excluded": {"cuda_backend": gpu_setup, **setup},
                "gpu_batch": GPU_BATCH, "tail_guard": 0, "measurements": summarize(results, count),
                "method": "Same finite nonce set completed in every sample, unchanged CUDA SHA-256 kernel and "
                          "geometry; alternating order; all persistent native contexts remain alive and idle for "
                          "both controls. Native hashing/allocation/launch/readback/result validation executes "
                          "inside one ctypes call, with no Python callback. Compile/thread setup excluded. "
                          "All trace hashes/proofs and sorted unique coverage independently audited after timing. "
                          "No other intentional GPU/CPU benchmark overlaps this run.",
                "limitations": ["Fixed-work throughput excludes startup, HTTP and persistence.",
                                "Power/clock endpoints do not individually profile each power or thermal cause.",
                                "Actual proof discovery and cancellation are validated separately."]}
    finally:
        for runner in runners.values():
            runner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("screen", "confirm"), default="screen")
    parser.add_argument("--profile", choices=tuple(PROFILES), default="ascii")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cpu-batch", type=int, default=250_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--count", type=int, default=4_294_967_296)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 5 or not 1 <= args.workers <= 64 or args.cpu_batch < 1:
        parser.error("At least 5 repeats, positive CPU batch, and workers1..64 required")
    document = compare(args.profile, args.stage, args.repeats, args.count, args.workers, args.cpu_batch)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({n: {k: row[k] for k in ("median_hashes_per_second", "speedup_vs_python_gpu",
                                              "speedup_vs_c_gpu", "pairs_faster_c_gpu")}
                      for n, row in document["measurements"].items()}, indent=2))


if __name__ == "__main__":
    main()
