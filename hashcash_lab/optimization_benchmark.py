"""Reproduce same-PC, fixed-work CUDA tuning against the preserved baseline.

The baseline is an exact saved Python source file from a named Git commit.
All variants finish identical nonce ranges even after discovering a proof.
Compilation and mandatory backend self-tests are excluded from warmed timing.
Every returned proof is checked again here with the standard library SHA-256.
This module does not read or write participant history.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import types
from typing import Any

from .gpu import CudaBackend, UINT64_MAX, batch_count


GEOMETRIES = tuple((threads, per_thread)
                   for threads in (64, 128, 256)
                   for per_thread in (8, 16, 32, 64, 128))
PROFILES = (
    ("ascii28_digits10_d5", "You are the best of best!!!!", 5, 1_000_000_000, 4_194_304),
    ("korean_digits10_d5", "해시캐시 성능 검증", 5, 1_000_000_000, 4_194_304),
    ("ascii28_digits20_d5", "You are the best of best!!!!", 5, 10_000_000_000_000_000_000, 4_194_304),
    ("ascii28_digits10_d7", "You are the best of best!!!!", 7, 1_000_000_000, 8_388_608),
    ("ascii28_digits10_d3", "You are the best of best!!!!", 3, 1_000_000_000, 262_144),
)


def load_baseline(path: Path) -> Any:
    """Load a UTF-8/BOM source snapshot without importing another checkout."""
    spec = importlib.util.spec_from_file_location("hashcash_preserved_gpu_baseline", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load baseline: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves its defining module.
    spec.loader.exec_module(module)
    return module


def pageable_candidate() -> Any:
    """Reproduce the rejected intermediate host-transfer experiment.

    Its warmed path reused events and one device buffer, but allocated a fresh
    pageable host array and synchronized twice. The final candidate retains a
    pinned array and uses only the blocking same-stream copy. Construction is
    excluded, so the final constructor's unused pinned allocation is harmless.
    Keeping this two-line, checked transform in the benchmark avoids adding
    experimental switches to the production backend.
    """
    path = Path(__file__).with_name("gpu.py")
    source = path.read_text(encoding="utf-8")
    before = ("                self._result.get(out=self._host_result, stream=self._stream, blocking=True)\n"
              "                host = self._host_result")
    after = ("                end.synchronize()\n"
             "                host = cp.asnumpy(self._result)")
    if source.count(before) != 1:
        raise ValueError("Production readback changed; review the pageable experiment transform")
    module = types.ModuleType("hashcash_pageable_readback_experiment")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(source.replace(before, after), str(path), "exec"), module.__dict__)
    return module.CudaBackend


def telemetry() -> dict[str, Any]:
    command = ["nvidia-smi", "--query-gpu=name,temperature.gpu,power.draw,clocks.sm,clocks.mem,utilization.gpu",
               "--format=csv,noheader,nounits"]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
        return {"utc": datetime.now(timezone.utc).isoformat(), "csv": result.stdout.strip(),
                "fields": "name,temperature_C,power_W,sm_MHz,memory_MHz,gpu_utilization_percent"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": str(exc)}


def run_fixed(backend: Any, message: str, difficulty: int, start: int,
              batch: int, launches: int) -> dict[str, Any]:
    """Finish all batches, retain proofs, and validate every reported range."""
    count = batch * launches
    if start + count - 1 > UINT64_MAX or batch_count(start, count) != count:
        raise ValueError("The measured range must stay in one decimal length and uint64")
    proofs = []
    kernel_seconds = 0.0
    message_bytes = message.encode("utf-8")
    current = start
    for _ in range(launches):
        result = backend.scan_batch(current, batch)
        if result["checked"] != batch or result["last_nonce"] != current + batch - 1:
            raise RuntimeError("Backend did not finish the exact fixed batch")
        last_hash = hashlib.sha256(message_bytes + str(result["last_nonce"]).encode("ascii")).hexdigest()
        if result["last_hash"] != last_hash:
            raise RuntimeError("Last-hash sample failed independent SHA-256")
        nonce, digest = result["nonce"], result["hash"]
        if nonce is not None:
            reference = hashlib.sha256(message_bytes + str(nonce).encode("ascii")).hexdigest()
            if not current <= nonce < current + batch or reference != digest or not reference.startswith("0" * difficulty):
                raise RuntimeError("Proof failed independent SHA-256/range/target checks")
            proofs.append({"nonce": str(nonce), "hash": digest})
        elif digest is not None:
            raise RuntimeError("Digest returned without a nonce")
        current += batch
        kernel_seconds += result["kernel_seconds"]
    return {"checked": count, "first_nonce": str(start), "last_nonce": str(current - 1),
            "launches": launches, "kernel_seconds": kernel_seconds, "proofs": proofs}


def _summary(wall: list[float], kernel: list[float], count: int,
             sample: dict[str, Any]) -> dict[str, Any]:
    median = statistics.median(wall)
    return {"wall_seconds": wall, "kernel_seconds": kernel,
            "median_wall_seconds": median, "median_kernel_seconds": statistics.median(kernel),
            "hashes_per_second": count / median,
            "minimum_wall_seconds": min(wall), "maximum_wall_seconds": max(wall),
            "coefficient_of_variation": statistics.pstdev(wall) / statistics.mean(wall),
            "every_sample_at_least_50ms": min(wall) >= 0.05, "sample": sample}


def measure(variants: dict[str, Any], *, message: str, difficulty: int, start: int,
            batch: int, launches: int, repeats: int, warm_seconds: float = 2.0) -> dict[str, Any]:
    initialization = {}
    for name, backend in variants.items():
        began = time.perf_counter()
        backend._kernel(len(str(start))).compile()
        run_fixed(backend, message, difficulty, start, batch, 1)
        initialization[name] = time.perf_counter() - began
    warm_began = time.perf_counter()
    while time.perf_counter() - warm_began < warm_seconds:
        for backend in variants.values():
            run_fixed(backend, message, difficulty, start, batch, 1)
    warm_elapsed = time.perf_counter() - warm_began
    before = telemetry()
    wall = {name: [] for name in variants}
    kernel = {name: [] for name in variants}
    samples: dict[str, Any] = {}
    orders = []
    names = list(variants)
    for repetition in range(repeats):
        order = names if repetition % 2 == 0 else list(reversed(names))
        orders.append(order)
        for name in order:
            began = time.perf_counter()
            result = run_fixed(variants[name], message, difficulty, start, batch, launches)
            elapsed = time.perf_counter() - began
            wall[name].append(elapsed)
            kernel[name].append(result["kernel_seconds"])
            samples[name] = result
    measurements = {name: _summary(wall[name], kernel[name], batch * launches, samples[name]) for name in variants}
    baseline_seconds = measurements["baseline"]["median_wall_seconds"]
    for row in measurements.values():
        row["speedup_vs_baseline"] = baseline_seconds / row["median_wall_seconds"]
    for name, row in measurements.items():
        pairs = [reference / candidate for reference, candidate in zip(wall["baseline"], wall[name])]
        row["paired_speedups_vs_baseline"] = pairs
        row["median_paired_speedup_vs_baseline"] = statistics.median(pairs)
        row["pairs_faster_than_baseline"] = sum(value > 1 for value in pairs)
    return {"message": message, "utf8_bytes": len(message.encode("utf-8")), "difficulty": difficulty,
            "start_nonce": str(start), "count_per_sample": batch * launches, "batch_size": batch,
            "launches_per_sample": launches, "repeats": repeats, "orders": orders,
            "initialization_and_compile_seconds_excluded": initialization,
            "warmup_seconds": warm_elapsed, "telemetry_before": before, "telemetry_after": telemetry(),
            "measurements": measurements, "all_returned_proofs_independently_verified": True}


def environment() -> dict[str, Any]:
    import cupy as cp
    props = cp.cuda.runtime.getDeviceProperties(0)
    label = props["name"]
    return {"python": sys.version, "platform": platform.platform(), "cupy": cp.__version__,
            "device": label.decode() if isinstance(label, bytes) else label,
            "cuda_runtime_version": cp.cuda.runtime.runtimeGetVersion(),
            "cuda_driver_version": cp.cuda.runtime.driverGetVersion(),
            "multiprocessors": props["multiProcessorCount"],
            "compute_capability": [props["major"], props["minor"]]}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path("work/performance-baseline/gpu_before.py"))
    parser.add_argument("--baseline-commit", default="0daf95a")
    parser.add_argument("--stage", choices=("screen", "confirm"), default="screen")
    parser.add_argument("--candidate", choices=("baseline", "pinned", "pageable"), default="pinned",
                        help="Geometry screen always uses unchanged baseline code; confirmation selects this code")
    parser.add_argument("--threads", type=int, default=256)
    parser.add_argument("--per-thread", type=int, default=16)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--output", type=Path, default=Path("docs/hashcash-optimization-gpu.json"))
    args = parser.parse_args()
    original = subprocess.run(["git", "show", f"{args.baseline_commit}:hashcash_lab/gpu.py"],
                              check=True, capture_output=True).stdout.decode("utf-8")
    snapshot = args.baseline.read_text(encoding="utf-8-sig")
    normalized_original = original.replace("\r\n", "\n").rstrip("\n")
    normalized_snapshot = snapshot.replace("\r\n", "\n").rstrip("\n")
    if normalized_original != normalized_snapshot:
        raise ValueError("Saved baseline differs from the named Git commit")
    baseline_module = load_baseline(args.baseline.resolve())
    candidate_factory = (baseline_module.CudaBackend if args.candidate == "baseline"
                         else pageable_candidate() if args.candidate == "pageable" else CudaBackend)
    repeats = args.repeats or (3 if args.stage == "screen" else 7)
    if repeats < (3 if args.stage == "screen" else 7):
        raise ValueError("Screening needs at least 3 repetitions; confirmation needs at least 7")
    profiles = []
    if args.stage == "screen":
        _, message, difficulty, start, batch = PROFILES[0]
        variants = {"baseline": baseline_module.CudaBackend(message, difficulty)}
        for threads, per_thread in GEOMETRIES:
            if (threads, per_thread) != (256, 16):
                variants[f"threads{threads}_per_thread{per_thread}"] = baseline_module.CudaBackend(
                    message, difficulty, threads=threads, per_thread=per_thread)
        row = measure(variants, message=message, difficulty=difficulty, start=start,
                      batch=batch, launches=16, repeats=repeats)
        row["profile"] = PROFILES[0][0]
        profiles.append(row)
    else:
        for name, message, difficulty, start, batch in PROFILES:
            variants = {"baseline": baseline_module.CudaBackend(message, difficulty),
                        "candidate": candidate_factory(message, difficulty, threads=args.threads,
                                                        per_thread=args.per_thread)}
            launches = max(16, 268_435_456 // batch)
            row = measure(variants, message=message, difficulty=difficulty, start=start,
                          batch=batch, launches=launches, repeats=repeats)
            row["profile"] = name
            profiles.append(row)
            print(json.dumps({"profile": name, "median_Hps": {key: value["hashes_per_second"]
                                  for key, value in row["measurements"].items()},
                              "speedup": row["measurements"]["candidate"]["speedup_vs_baseline"]}), flush=True)
    document = {"measured_at_utc": datetime.now(timezone.utc).isoformat(), "stage": args.stage,
                "environment": environment(), "baseline_commit": args.baseline_commit,
                "baseline_file": str(args.baseline),
                "baseline_file_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
                "baseline_matches_git_source_ignoring_bom_newlines": True,
                "baseline_normalized_source_sha256": hashlib.sha256(normalized_original.encode("utf-8")).hexdigest(),
                "candidate_file_sha256": hashlib.sha256(Path(__file__).with_name("gpu.py").read_bytes()).hexdigest(),
                "finite_geometry_candidates": [{"threads": threads, "per_thread": per_thread}
                                                for threads, per_thread in GEOMETRIES],
                "candidate_geometry": {"threads": args.threads, "per_thread": args.per_thread},
                "candidate": "baseline_geometry" if args.stage == "screen" else args.candidate,
                "method": "Identical UTF-8 input and contiguous finite nonce ranges; all hashes completed regardless of proof; "
                          "compilation and runtime self-tests excluded; current production batch sizes unchanged; "
                          "alternating execution order; event kernel time and complete backend wall time reported separately; "
                          "each returned proof and per-batch last hash checked with independent hashlib SHA-256.",
                "profiles": profiles,
                "limitations": ["Measurements apply only to this PC, runtime, message lengths and decimal nonce lengths.",
                                "Fixed-work throughput is not startup latency or guaranteed time to a random proof.",
                                "No participant database is touched."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "stage": args.stage,
                      "profiles": len(profiles)}, ensure_ascii=False), flush=True)
    if args.stage == "screen":
        for name, row in sorted(profiles[0]["measurements"].items(), key=lambda item: -item[1]["hashes_per_second"]):
            print(f"{name}: {row['hashes_per_second'] / 1e6:.2f} M H/s, "
                  f"{row['speedup_vs_baseline']:.4f}x baseline, CV={row['coefficient_of_variation']:.4f}", flush=True)


if __name__ == "__main__":
    main()
