"""Finite GPU kernel/batch candidates against the preserved 8cd2e73 backend."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types

from .gpu import CudaBackend
from .optimization_benchmark import PROFILES, environment, load_baseline, run_fixed, telemetry
from .bottleneck_benchmark import hardware_state

BASELINE = "8cd2e73f1f18323a6257e0d329c42218bca6a1d5"


def packed_increment(slots, indent="        "):
    groups = []
    for word in sorted({word for word, _ in slots}, reverse=True):
        shifts = [shift for w, shift in slots if w == word]
        groups.append((word, min(shifts), len(shifts)))
    lines = [indent + "{"]
    for index, (word, shift, size) in enumerate(groups):
        prefix = indent + "    " * (index + 1)
        mask = (1 << (size * 8)) - 1
        repeated = lambda byte: sum(byte << (8 * i) for i in range(size))
        lines.extend([
            prefix + f"unsigned int value{index} = (seed{word} >> {shift}u) & 0x{mask:08x}u;",
            prefix + f"unsigned long long sum{index} = (value{index} & 0x{repeated(15):08x}u)"
                     f" + 0x{repeated(246):x}ull + 1ull;",
            prefix + f"unsigned int digit{index} = (unsigned int)sum{index} & 0x{mask:08x}u;",
            prefix + f"digit{index} -= ((digit{index} >> 7u) & 0x{repeated(1):08x}u) * 0xf6u;",
            prefix + f"seed{word} = (seed{word} & ~0x{(mask << shift):08x}u)"
                     f" | ((digit{index} | 0x{repeated(48):08x}u) << {shift}u);",
        ])
        if index + 1 < len(groups):
            lines.append(prefix + f"if ((sum{index} >> {size * 8}u) != 0ull) {{")
    for index in range(len(groups) - 1, 0, -1):
        lines.append(indent + "    " * index + "}")
    lines.append(indent + "}")
    return lines


def experiment_factory(kind):
    source = Path("work/bottleneck-baseline/gpu.py").read_text(encoding="utf-8-sig")
    module = types.ModuleType(f"hashcash_gpu_experiment_{kind}")
    sys.modules[module.__name__] = module
    if kind == "intrinsics":
        source = source.replace('#define RR(x,n) (((x) >> (n)) | ((x) << (32u - (n))))',
                                '#define RR(x,n) __funnelshift_r((x),(x),(n))')
        source = source.replace('#define CH(x,y,z) (((x) & (y)) ^ (~(x) & (z)))',
                                '#define CH(x,y,z) lop3_ch((x),(y),(z))')
        source = source.replace('#define MAJ(x,y,z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))',
                                '#define MAJ(x,y,z) lop3_maj((x),(y),(z))')
    exec(compile(source, module.__name__, "exec"), module.__dict__)
    build = module._build_cuda
    if kind == "packed":
        module._increment_code = packed_increment
    if kind == "intrinsics":
        prefix = ('__device__ __forceinline__ unsigned int lop3_ch(unsigned int x, unsigned int y, unsigned int z) '
                  '{ unsigned int r; asm("lop3.b32 %0, %1, %2, %3, 0xca;" : "=r"(r) : "r"(x), "r"(y), "r"(z)); return r; }\n'
                  '__device__ __forceinline__ unsigned int lop3_maj(unsigned int x, unsigned int y, unsigned int z) '
                  '{ unsigned int r; asm("lop3.b32 %0, %1, %2, %3, 0xe8;" : "=r"(r) : "r"(x), "r"(y), "r"(z)); return r; }\n')

        def build_intrinsics(*args, **kwargs):
            return prefix + build(*args, **kwargs)

        module._build_cuda = build_intrinsics
    return module.CudaBackend


def compare(stage, repeats, count):
    path = Path("work/bottleneck-baseline/gpu.py")
    if path.read_bytes() != subprocess.check_output(["git", "show", f"{BASELINE}:hashcash_lab/gpu.py"]):
        raise ValueError("Baseline differs from Git")
    original = load_baseline(path)
    source_files = (Path(__file__), Path(__file__).with_name("gpu.py"),
                    Path(__file__).with_name("optimization_benchmark.py"),
                    Path(__file__).with_name("bottleneck_benchmark.py"), path)
    def source_hashes():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    sources = source_hashes()
    factories = {"baseline": original.CudaBackend}
    if stage == "kernels":
        factories.update({name: experiment_factory(name) for name in ("intrinsics", "packed")})
    else:
        factories.update({"batch16M": CudaBackend, "batch64M": CudaBackend})
    profiles = []
    profiles_to_run = (*PROFILES, ("ascii28_digits10_d8", "You are the best of best!!!!", 8,
                                  1_000_000_000, 8_388_608))
    for name, message, difficulty, start, batch in profiles_to_run:
        backends = {n: factory(message, difficulty) for n, factory in factories.items()}
        batches = {n: batch if n == "baseline" or stage == "kernels" else
                   16_777_216 if n == "batch16M" else 67_108_864 for n in backends}
        for n, backend in backends.items():
            backend._kernel(len(str(start))).compile()
            run_fixed(backend, message, difficulty, start, batches[n], 1)
        warm = time.perf_counter()
        while time.perf_counter() - warm < 1:
            for backend in backends.values():
                backend.scan_batch(start, batch)
        results = {n: [] for n in backends}
        orders = []
        before = telemetry()
        cuda_sources = {n: hashlib.sha256(backend._kernel(len(str(start))).code.encode()).hexdigest()
                        for n, backend in backends.items()}
        for repetition in range(repeats):
            order = list(backends) if repetition % 2 == 0 else list(reversed(backends))
            orders.append(order)
            for n in order:
                state_before = hardware_state()
                began = time.perf_counter()
                sample = run_fixed(backends[n], message, difficulty, start, batches[n], count // batches[n])
                elapsed = time.perf_counter() - began
                results[n].append({"seconds": elapsed, "hashes_per_second": count / elapsed,
                                   "kernel_seconds": sample["kernel_seconds"], "sample": sample,
                                   "hardware_before": state_before, "hardware_after": hardware_state()})
        reference = statistics.median(s["seconds"] for s in results["baseline"])
        measurements = {}
        for n, samples in results.items():
            seconds = [s["seconds"] for s in samples]
            measurements[n] = {"median_hashes_per_second": count / statistics.median(seconds),
                               "speedup_vs_baseline": reference / statistics.median(seconds),
                               "coefficient_of_variation": statistics.stdev(seconds) / statistics.mean(seconds),
                               "pairs_faster": sum(s["seconds"] < b["seconds"] for s, b in
                                                   zip(samples, results["baseline"])), "samples": samples}
        profiles.append({"name": name, "message": message, "difficulty": difficulty, "start": str(start),
                         "count": str(count), "batches": batches, "orders": orders,
                         "generated_cuda_sha256": cuda_sources,
                         "telemetry_before": before, "telemetry_after": telemetry(),
                         "measurements": measurements})
    if sources != source_hashes():
        raise RuntimeError("Source changed during measurement")
    return {"baseline_commit": BASELINE, "measured_at_utc": datetime.now(timezone.utc).isoformat(),
            "stage": stage, "repeats": repeats, "environment": environment(), "profiles": profiles,
            "implementation_sha256": sources,
            "method": "Same count and finite range for every variant; alternating order. Self-test and "
                      "compile excluded; all returned proofs and last-hash samples independently verified. "
                      "No other intentional GPU/CPU load. Performance applies only to warmed fixed work."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("kernels", "batches"), required=True)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--count", type=int, default=268_435_456)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 5 or args.count < 67_108_864 or args.count % 67_108_864:
        parser.error("Need >=5 repeats and count a positive multiple of 67,108,864")
    result = compare(args.stage, args.repeats, args.count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({p["name"]: {n: {k: row[k] for k in ("median_hashes_per_second", "speedup_vs_baseline", "pairs_faster")}
                                          for n, row in p["measurements"].items()} for p in result["profiles"]}, indent=2))


if __name__ == "__main__":
    main()
