"""Compare unchanged source CUDA with the new backend on fixed nonce ranges.

Example: python -m hashcash_lab.gpu_benchmark --source work/source/Nonce

Both methods compute the entire requested range even if they find a proof.
This measures warmed compute, synchronization, host readback, and proof checks;
it excludes dependency loading, shader compilation, and initialization tests.
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
from typing import Any

from .gpu import CudaBackend, UINT64_MAX, batch_count
from .server import gpu_batch_size


def _reference(message: bytes, nonce: int) -> str:
    return hashlib.sha256(message + str(nonce).encode("ascii")).hexdigest()


def _load_source(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("hashcash_cuda_reference", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"원본 CUDA 파일을 읽을 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not isinstance(getattr(module, "_KERNEL_SOURCE_TEMPLATE", None), str):
        raise ValueError("원본 파일에 CUDA 커널 템플릿이 없습니다.")
    return module


class OriginalKernel:
    """Use the exact source kernel and source buffer layouts without editing it."""

    def __init__(self, source_file: Path, message: str, difficulty: int,
                 count: int, *, blocks: int, threads: int = 256):
        import cupy as cp

        self.cp = cp
        self.message = message.encode("utf-8")
        self.difficulty = difficulty
        self.count = count
        self.blocks = blocks
        self.threads = threads
        total_threads = blocks * threads
        if blocks < 1 or threads < 1 or count % total_threads:
            raise ValueError("원본 고정 탐색량은 blocks × threads의 배수여야 합니다.")
        if len(self.message) > 32:
            raise ValueError("원본 CUDA의 알려진 메시지 버퍼 결함을 피하려면 32바이트 이하를 사용하세요.")
        self.per_thread = count // total_threads
        source = _load_source(source_file)
        code = source._KERNEL_SOURCE_TEMPLATE.replace("__MESSAGE_LEN__", str(len(self.message)))
        self.kernel_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()
        self.kernel = cp.RawKernel(code, "search_kernel", options=())
        self.kernel.compile()
        self.stream = cp.cuda.Stream(non_blocking=True)
        with self.stream:
            self.device_message = cp.asarray(memoryview(self.message), dtype=cp.uint8)
            self.found_mask = cp.zeros(1, dtype=cp.uint64)
            self.found_nonces = cp.zeros(difficulty + 1, dtype=cp.uint64)
            self.found_hashes = cp.zeros((difficulty + 1) * 8, dtype=cp.uint32)
            self.stream.synchronize()

    def run(self, start: int) -> dict[str, Any]:
        cp = self.cp
        with self.stream:
            self.found_mask.fill(0)
            begin, end = cp.cuda.Event(), cp.cuda.Event()
            begin.record(self.stream)
            self.kernel(
                (self.blocks,), (self.threads,),
                (self.device_message, cp.uint64(start), cp.uint64(1),
                 cp.uint64(self.per_thread), cp.int32(self.difficulty), cp.int32(self.difficulty),
                 self.found_mask, self.found_nonces, self.found_hashes),
            )
            end.record(self.stream)
            end.synchronize()
            mask = int(cp.asnumpy(self.found_mask)[0])
            if mask not in (0, 1 << self.difficulty):
                raise RuntimeError("원본 CUDA 발견 마스크가 요청한 난이도와 다릅니다.")
            nonce = None
            digest = None
            if mask:
                # Read the same full slot arrays as gpu_search.run() does.
                nonces = cp.asnumpy(self.found_nonces)
                hashes = cp.asnumpy(self.found_hashes)
                nonce = int(nonces[self.difficulty])
                digest = "".join(f"{int(word):08x}" for word in hashes[self.difficulty * 8:(self.difficulty + 1) * 8])
            kernel_seconds = cp.cuda.get_elapsed_time(begin, end) / 1000
        if nonce is not None:
            expected = _reference(self.message, nonce)
            if not start <= nonce < start + self.count or digest != expected or not expected.startswith("0" * self.difficulty):
                raise RuntimeError("원본 CUDA 발견 결과가 독립 SHA-256 검증에 실패했습니다.")
        return {
            "checked": self.blocks * self.threads * self.per_thread,
            "nonce": nonce, "hash": digest,
            "last_nonce": start + self.count - 1,
            "kernel_seconds": kernel_seconds,
        }


def _run_candidate(backend: CudaBackend, start: int, count: int,
                   launch_count: int) -> dict[str, Any]:
    current, remaining = start, count
    checked = 0
    launches = 0
    proofs = []
    kernel_seconds = 0.0
    last_nonce = None
    while remaining:
        result = backend.scan_batch(current, min(launch_count, remaining))
        length = result["checked"]
        if length <= 0 or length > remaining:
            raise RuntimeError("새 CUDA 커널의 계산 횟수가 올바르지 않습니다.")
        if result["nonce"] is not None:
            # CudaBackend verifies every proof internally; also retain it for
            # report readers to verify against any independent SHA-256 tool.
            proofs.append({"nonce": result["nonce"], "hash": result["hash"]})
        checked += length
        launches += 1
        current += length
        remaining -= length
        kernel_seconds += result["kernel_seconds"]
        last_nonce = result["last_nonce"]
    if checked != count or last_nonce != start + count - 1:
        raise RuntimeError("새 CUDA 커널이 고정 탐색 범위를 끝까지 계산하지 않았습니다.")
    return {"checked": checked, "launches": launches, "proofs": proofs,
            "last_nonce": last_nonce, "kernel_seconds": kernel_seconds}


def _git_metadata(source_file: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"source_file": str(source_file),
                              "source_file_sha256": hashlib.sha256(source_file.read_bytes()).hexdigest()}
    for key, arguments in (("source_commit", ["rev-parse", "HEAD"]),
                           ("source_url", ["remote", "get-url", "origin"])):
        try:
            result[key] = subprocess.run(["git", "-C", str(source_file.parent), *arguments],
                                         capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            result[key] = None
    return result


def compare(source_file: Path, *, message: str, difficulty: int, start: int,
            count: int, repeats: int, interactive_batch: int,
            source_blocks: int, source_alt_blocks: int, warmup_seconds: float) -> dict[str, Any]:
    import cupy as cp

    if not 1 <= difficulty <= 60 or repeats < 5:
        raise ValueError("동일 원본 GPU 기준으로 difficulty1..60, repeats>=5가 필요합니다.")
    if not 0 <= start <= UINT64_MAX or start + count - 1 > UINT64_MAX:
        raise ValueError("고정 탐색 범위가 uint64를 넘습니다.")
    if count < 1 or count > (1 << 32) - 1 or interactive_batch < 1:
        raise ValueError("탐색량과 화면 배치는 1 이상이어야 합니다.")
    if batch_count(start, count) != count:
        raise ValueError("공정한 단일 커널 비교를 위해 한 십진수 길이 안의 탐색 범위를 사용하세요.")
    if warmup_seconds < 0:
        raise ValueError("warmup_seconds는 0 이상이어야 합니다.")
    began = time.perf_counter()
    candidate = CudaBackend(message, difficulty)
    source = OriginalKernel(source_file, message, difficulty, count, blocks=source_blocks)
    source_alt = OriginalKernel(source_file, message, difficulty, count, blocks=source_alt_blocks)
    # Force the candidate's actual measured nonce-length kernel to compile
    # before warmup and timing, independent of initialization-test lengths.
    candidate._kernel(len(str(start))).compile()
    initialization_seconds = time.perf_counter() - began
    production_batch = gpu_batch_size(difficulty)
    variants = {
        "source": lambda: source.run(start),
        "source_default_grid": lambda: source_alt.run(start),
        "candidate_bulk": lambda: _run_candidate(candidate, start, count, count),
        "candidate_production": lambda: _run_candidate(candidate, start, count, production_batch),
        "candidate_interactive": lambda: _run_candidate(candidate, start, count, interactive_batch),
    }
    warmup_began = time.perf_counter()
    warmup_rounds = 0
    while warmup_rounds == 0 or time.perf_counter() - warmup_began < warmup_seconds:
        for run in variants.values():
            run()
        warmup_rounds += 1
    warmup_duration = time.perf_counter() - warmup_began
    timings: dict[str, list[float]] = {name: [] for name in variants}
    kernel_timings: dict[str, list[float]] = {name: [] for name in variants}
    samples: dict[str, dict[str, Any]] = {}
    orders = []
    names = list(variants)
    for repetition in range(repeats):
        order = names if repetition % 2 == 0 else list(reversed(names))
        orders.append(order)
        for name in order:
            before = time.perf_counter()
            result = variants[name]()
            elapsed = time.perf_counter() - before
            if result["checked"] != count:
                raise RuntimeError(f"{name}: 요청한 고정 계산 횟수와 실제 횟수가 다릅니다.")
            timings[name].append(elapsed)
            kernel_timings[name].append(result["kernel_seconds"])
            samples[name] = result
    measurements = {}
    for name in variants:
        seconds = timings[name]
        median = statistics.median(seconds)
        measurements[name] = {
            "wall_seconds": seconds, "kernel_seconds": kernel_timings[name],
            "median_wall_seconds": median,
            "median_kernel_seconds": statistics.median(kernel_timings[name]),
            "hashes_per_second": count / median,
            "min_wall_seconds": min(seconds), "max_wall_seconds": max(seconds),
            "sample": samples[name],
        }
    props = cp.cuda.runtime.getDeviceProperties(cp.cuda.Device().id)
    label = props.get("name", "CUDA GPU")
    device_name = label.decode("utf-8", errors="replace") if isinstance(label, bytes) else str(label)
    ratios = {}
    for baseline in ("source", "source_default_grid"):
        ratios[baseline] = {
            "bulk_speedup": measurements[baseline]["median_wall_seconds"] / measurements["candidate_bulk"]["median_wall_seconds"],
            "production_speedup": measurements[baseline]["median_wall_seconds"] / measurements["candidate_production"]["median_wall_seconds"],
            "interactive_speedup": measurements[baseline]["median_wall_seconds"] / measurements["candidate_interactive"]["median_wall_seconds"],
        }
    fastest_baseline = min(("source", "source_default_grid"),
                           key=lambda name: measurements[name]["median_wall_seconds"])
    return {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "cupy": cp.__version__, "device": device_name,
                        "cuda_driver_version": cp.cuda.runtime.driverGetVersion(),
                        "cuda_runtime_version": cp.cuda.runtime.runtimeGetVersion(),
                        "multiprocessors": int(props.get("multiProcessorCount", 0)),
                        "compute_capability": [int(props.get("major", 0)), int(props.get("minor", 0))]},
        "source": _git_metadata(source_file),
        "method": "Same UTF-8 message, SHA-256(message+ASCII decimal nonce), leading hex-zero difficulty, "
                  "stride1, identical fixed contiguous nonce range. All requested candidates are processed even after "
                  "finding a proof. Unmodified original CUDA source (message-length placeholder substitution only). "
                  "Warm compilation/self-tests excluded. Alternating order; wall time includes flag reset, launch, "
                  "synchronization, original/full or candidate result readback, independent proof checks and Python calls. "
                  "Candidate production imports the app's gpu_batch_size(difficulty) policy and uses its actual batches; "
                  "candidate_interactive retains the previous262K batch for overhead comparison. Both also compute the real "
                  "per-batch last-hash sample. GPU events measure kernel time separately.",
        "message": message, "utf8_bytes": len(message.encode("utf-8")), "difficulty": difficulty,
        "start_nonce": start, "count_per_run": count, "repeats": repeats,
        "primary_comparison": f"candidate_production vs {fastest_baseline} (faster measured original grid)",
        "production_batch_size": production_batch,
        "production_batch_policy": "Imported server.gpu_batch_size(difficulty); production_batch_size records its returned value.",
        "initialization_seconds_excluded": initialization_seconds,
        "warmup_seconds": warmup_duration,
        "warmup_rounds": warmup_rounds, "orders": orders,
        "launch_geometry": {
            "source": {"blocks": source.blocks, "threads": source.threads, "per_thread": source.per_thread},
            "source_default_grid": {"blocks": source_alt.blocks, "threads": source_alt.threads, "per_thread": source_alt.per_thread},
            "candidate_bulk": {"blocks": (count + candidate.threads * candidate.per_thread - 1) // (candidate.threads * candidate.per_thread),
                               "threads": candidate.threads, "per_thread": candidate.per_thread, "launches": 1},
            "candidate_production": {"blocks_per_full_batch": (production_batch + candidate.threads * candidate.per_thread - 1) // (candidate.threads * candidate.per_thread),
                                     "threads": candidate.threads, "per_thread": candidate.per_thread,
                                     "batch_size": production_batch, "launches": (count + production_batch - 1) // production_batch},
            "candidate_interactive": {"blocks_per_full_batch": (interactive_batch + candidate.threads * candidate.per_thread - 1) // (candidate.threads * candidate.per_thread),
                                      "threads": candidate.threads, "per_thread": candidate.per_thread,
                                      "batch_size": interactive_batch, "launches": (count + interactive_batch - 1) // interactive_batch},
        },
        "measurements": measurements, "ratios": ratios,
        "proofs_independently_verified": True,
        "limitations": ["Single GPU and software environment; no claim for other devices.",
                        "Original default is1,073,741,824 attempts per launch; this fixed8M comparison retunes its per-thread count.",
                        "Both original grid sizes are reported to expose launch-geometry sensitivity.",
                        "Parallel valid nonces may differ; a matching minimum nonce is not required by the original GPU contract.",
                        f"The primary production measurement uses the real difficulty{difficulty} policy: "
                        f"{(count + production_batch - 1) // production_batch} batches of up to{production_batch} attempts.",
                        "candidate_interactive is the legacy262K policy, retained to disclose its launch/readback overhead.",
                        "Small interactive batches trade GPU throughput for progress updates and cancellation responsiveness."]
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("work/source/Nonce"))
    parser.add_argument("--message", default="GPU hashcash comparison test")
    parser.add_argument("--difficulty", type=int, default=5)
    parser.add_argument("--start", type=int, default=1_000_000_000)
    parser.add_argument("--count", type=int, default=8_388_608)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--interactive-batch", type=int, default=262_144)
    parser.add_argument("--source-blocks", type=int, default=128)
    parser.add_argument("--source-alt-blocks", type=int, default=1024)
    parser.add_argument("--warmup-seconds", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=Path("docs/hashcash-gpu-benchmark.json"))
    args = parser.parse_args()
    source_file = args.source.expanduser().resolve()
    if source_file.is_dir():
        source_file /= "gpu_search.py"
    document = compare(source_file, message=args.message, difficulty=args.difficulty,
                       start=args.start, count=args.count, repeats=args.repeats,
                       interactive_batch=args.interactive_batch, source_blocks=args.source_blocks,
                       source_alt_blocks=args.source_alt_blocks, warmup_seconds=args.warmup_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {"device": document["environment"]["device"], "output": str(args.output),
               "attempts_per_run": document["count_per_run"], "repeats": document["repeats"],
               "utf8_bytes": document["utf8_bytes"],
               "production_batch_size": document["production_batch_size"],
               "median_hashes_per_second": {name: value["hashes_per_second"] for name, value in document["measurements"].items()},
               "ratios": document["ratios"], "proofs_independently_verified": True}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
