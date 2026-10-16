"""Repeated warmed proof search on the predetermined default message.

This complements fixed-work throughput and the separate one-shot startup/cancel
smoke. The same message/start/target is used in every paired sample; no best
nonce is selected and no proof from an earlier run is reused.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import time

from .gpu import CudaBackend, batch_count
from .bottleneck_benchmark import hardware_state
from .native_scheduler_experiment import NativeScheduler, native_environment
from .native_scheduler_benchmark import GPU_BATCH, sources
from .native_scheduler_smoke import verify


def compare(repeats):
    signatures = sources()
    signatures["search_benchmark"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    message, difficulty, start, count = "You are the best of best!!!!", 8, 1_000_000_000, 4_294_967_296
    backend = CudaBackend(message, difficulty)
    runners = {}
    try:
        for workers in (0, 4):
            runner = NativeScheduler(message, difficulty, workers, cpu_batch=64_000).attach_gpu(backend)
            runners["c_gpu" if not workers else "c_hybrid"] = runner
            if workers:
                warmed = set()
                for _ in range(10):
                    runner.reset(start, 4_000_000, search=False).run_with_callback(None)
                    warmed.update(t["worker"] for t in runner.traces() if t["kind"] == 1)
                    if warmed == set(range(workers)):
                        break
                if warmed != set(range(workers)):
                    raise RuntimeError("Not all native CPU workers were warmed")
        warm = time.perf_counter()
        while time.perf_counter() - warm < 2:
            backend.scan_batch(start, GPU_BATCH)
        results = {"python_gpu": [], "c_gpu": [], "c_hybrid": []}
        orders = []
        for repetition in range(repeats):
            order = list(results) if repetition % 2 == 0 else list(reversed(results))
            orders.append(order)
            for name in order:
                before = hardware_state()
                if name == "python_gpu":
                    began = time.perf_counter()
                    cursor, traces, winner = start, [], None
                    while cursor < start + count:
                        take = batch_count(cursor, min(GPU_BATCH, start + count - cursor))
                        first = time.perf_counter()
                        batch = backend.scan_batch(cursor, take)
                        last = time.perf_counter()
                        traces.append({"start": str(cursor), "count": take, "checked": batch["checked"],
                                       "kind": 2, "status": 0, "found": batch["nonce"] is not None,
                                       "nonce": str(batch["nonce"]) if batch["nonce"] is not None else None,
                                       "hash": batch["hash"], "last_hash": batch["last_hash"],
                                       "begin": first, "end": last, "kernel_seconds": batch["kernel_seconds"]})
                        cursor += batch["checked"]
                        if batch["nonce"] is not None:
                            winner = batch
                            break
                    elapsed = time.perf_counter() - began
                    stats = {"checked": cursor - start, "gpu_failed": 0, "trace_dropped": 0,
                             "found": int(winner is not None), "nonce": str(winner["nonce"]) if winner else None,
                             "hash": winner["hash"] if winner else None}
                else:
                    runner = runners[name]
                    runner.reset(start, count, search=True)
                    began = time.perf_counter()
                    stats = runner.run()
                    elapsed = time.perf_counter() - began
                    traces = runner.traces()
                after = hardware_state()
                verify(traces, message, difficulty, stats)
                if not stats["found"]:
                    raise RuntimeError("The predetermined range unexpectedly had no proof")
                sample = {"seconds_to_verified_return": elapsed, "checked": str(stats["checked"]),
                          "nonce": stats["nonce"], "hash": stats["hash"],
                          "hardware_before": before, "hardware_after": after}
                if repetition == 0:
                    sample["traces"], sample["stats"] = traces, stats
                results[name].append(sample)
        summary = {}
        for name, samples in results.items():
            values = [s["seconds_to_verified_return"] for s in samples]
            summary[name] = {"median_seconds": statistics.median(values), "mean_seconds": statistics.mean(values),
                             "coefficient_of_variation": statistics.stdev(values) / statistics.mean(values),
                             "faster_pairs_python": sum(s["seconds_to_verified_return"] < b["seconds_to_verified_return"]
                                                        for s, b in zip(samples, results["python_gpu"])),
                             "faster_pairs_c_gpu": sum(s["seconds_to_verified_return"] < b["seconds_to_verified_return"]
                                                       for s, b in zip(samples, results["c_gpu"])), "samples": samples}
        final = sources()
        final["search_benchmark"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if final != signatures:
            raise RuntimeError("Source changed during proof timing")
        return {"measured_at_utc": datetime.now(timezone.utc).isoformat(), "message": message,
                "difficulty": difficulty, "start": str(start), "maximum_count": str(count), "repeats": repeats,
                "source_sha256": signatures, "native_environment": native_environment(),
                "orders": orders, "measurements": summary,
                "method": "Same predetermined default message and finite domain per pair, real search=True. "
                          "Alternating order, no prior proof reuse. Time until independently verified return, "
                          "including native CPU drain; setup/compile/thread warmup excluded. Independent "
                          "trace endpoint/proof/count checks after timing. This one-message repeated test "
                          "does not establish a distribution across all messages or cold starts."}
    finally:
        for runner in runners.values():
            runner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=13)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 5:
        parser.error("At least5 repeats required")
    document = compare(args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({n: {k: v[k] for k in ("median_seconds", "mean_seconds", "faster_pairs_python", "faster_pairs_c_gpu")}
                      for n, v in document["measurements"].items()}, indent=2))


if __name__ == "__main__":
    main()
