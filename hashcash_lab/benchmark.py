"""Compare fixed nonce workloads against the original repository's CPU worker.

Run ``python -m hashcash_lab.benchmark --source work/source/Nonce --workers 4``.
The source file is imported without modification. Its queues and stop event are
small in-process adapters; both methods omit GUI/console output and disk writes.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from typing import Any

from .cpu import MAX_NONCE, proof_hash, scan_batch, validate_difficulty, validate_nonce

_SOURCE: Any = None


def _load_source(path: str) -> Any:
    name = "hashcash_reference_worker"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"원본 Python 파일을 읽을 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, "worker", None)):
        raise ValueError("원본 파일에 worker() 함수가 없습니다.")
    return module


def _initialize_worker(source_path: str, barrier: Any) -> None:
    global _SOURCE
    _SOURCE = _load_source(source_path)
    # Wait until every Windows-spawned process is ready, outside timed work.
    barrier.wait(timeout=60)


class _StopEvent:
    def __init__(self) -> None:
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped


class _SourceQueue:
    def __init__(self, stop: _StopEvent) -> None:
        self.stop = stop
        self.nonce: int | None = None
        self.hash_value: str | None = None
        self.next_nonce: int | None = None

    def put(self, item: dict[str, Any]) -> None:
        if item["type"] == "found" and self.nonce is None:
            self.nonce = int(item["nonce"])
            self.hash_value = str(item["hash"])
        if item["type"] == "progress":
            self.next_nonce = int(item["next_nonce"])
            self.stop.stopped = True


def _run_job(job: tuple[str, str, int, int, int, int]) -> dict[str, Any]:
    backend, message, difficulty, start, step, count = job
    if backend == "optimized":
        return dict(scan_batch(message, difficulty, start, count, search=False, step=step))
    stop = _StopEvent()
    queue = _SourceQueue(stop)
    _SOURCE.worker(message, 0, start, step, difficulty, difficulty, count, 0, stop, queue)
    if queue.next_nonce != start + step * count:
        raise RuntimeError("원본 worker가 요청한 고정 개수를 계산하지 않았습니다.")
    last_nonce = start + step * (count - 1)
    return {
        "checked": count,
        "nonce": queue.nonce,
        "hash": queue.hash_value,
        "last_nonce": last_nonce,
        "last_hash": proof_hash(message, last_nonce),
    }


def _jobs(
    backend: str, message: str, difficulty: int, start: int, count: int, workers: int
) -> list[tuple[str, str, int, int, int, int]]:
    per_worker, extra = divmod(count, workers)
    return [
        (backend, message, difficulty, start + index, workers, per_worker + (index < extra))
        for index in range(workers)
    ]


def _metadata(source_path: Path) -> dict[str, Any]:
    source_directory = source_path.parent
    cpu_name = platform.processor()
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as processor_key:
                cpu_name = str(winreg.QueryValueEx(processor_key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    try:
        commit = subprocess.run(
            ["git", "-C", str(source_directory), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        url = subprocess.run(
            ["git", "-C", str(source_directory), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
        url = None
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_name": cpu_name,
        "logical_processors": os.cpu_count(),
        "source_url": url,
        "source_commit": commit,
        "source_file": str(source_path),
    }


def compare(
    source_path: Path,
    *,
    message: str,
    difficulty: int,
    start: int,
    count: int,
    workers: int,
    repeats: int,
) -> dict[str, Any]:
    validate_difficulty(difficulty)
    validate_nonce(start)
    if workers < 1 or repeats < 2 or count < workers:
        raise ValueError("workers>=1, repeats>=2, count>=workers여야 합니다.")
    if start + count - 1 > MAX_NONCE:
        raise ValueError("고정 탐색 범위가 64비트 Nonce 범위를 넘습니다.")
    proof_hash(message, start)
    context = mp.get_context("spawn")
    timings: dict[str, list[float]] = {"source": [], "optimized": []}
    reference: list[dict[str, Any]] | None = None
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=context,
        initializer=_initialize_worker,
        initargs=(str(source_path), context.Barrier(workers)),
    ) as pool:
        warm_count = max(workers, min(count, workers * 20_000))
        for backend in timings:
            list(pool.map(_run_job, _jobs(backend, message, difficulty, start, warm_count, workers)))
        for repetition in range(repeats):
            order = ["source", "optimized"] if repetition % 2 == 0 else ["optimized", "source"]
            for backend in order:
                began = time.perf_counter()
                results = list(pool.map(_run_job, _jobs(backend, message, difficulty, start, count, workers)))
                timings[backend].append(time.perf_counter() - began)
                if sum(item["checked"] for item in results) != count:
                    raise RuntimeError("실제 계산 횟수가 요청한 고정 개수와 다릅니다.")
                if reference is None:
                    reference = results
                elif results != reference:
                    raise RuntimeError(f"{backend} 탐색 결과가 원본과 다릅니다.")
    source_seconds = statistics.median(timings["source"])
    optimized_seconds = statistics.median(timings["optimized"])
    return {
        "message": message,
        "utf8_bytes": len(message.encode("utf-8")),
        "difficulty": difficulty,
        "start_nonce": start,
        "count": count,
        "workers_per_backend": workers,
        "repeats": repeats,
        "source": {"seconds": timings["source"], "median_seconds": source_seconds,
                   "hashes_per_second": count / source_seconds},
        "optimized": {"seconds": timings["optimized"], "median_seconds": optimized_seconds,
                      "hashes_per_second": count / optimized_seconds},
        "speedup": source_seconds / optimized_seconds,
        "results_match": True,
        "worker_results": reference,
    }


def main() -> None:
    # Windows pipes may default to cp949, which cannot render emoji messages.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="같은 고정 탐색량으로 원본 CPU worker와 비교합니다.")
    parser.add_argument("--source", type=Path, required=True, help="원본 main.py 또는 저장소 폴더")
    parser.add_argument("--message", default=None)
    parser.add_argument("--difficulty", type=int, default=5)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=2_000_000, help="모든 프로세스의 총 계산 개수")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--include-long-message", action="store_true")
    args = parser.parse_args()
    source_path = args.source.expanduser().resolve()
    if source_path.is_dir():
        source_path = source_path / "main.py"
    source = _load_source(str(source_path))
    message = args.message if args.message is not None else source.DEFAULT_MESSAGE
    messages = [message]
    if args.include_long_message:
        messages.append("난스 😀\n메시지" * 32)
    document = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": _metadata(source_path),
        "method": "Fixed nonce count; identical worker count and target; alternating order; "
                  "warm process pool; GUI, console, disk I/O and process startup excluded; "
                  "original worker imported unchanged with local queue/stop adapters.",
        "gpu_measured": False,
        "cases": [
            compare(source_path, message=value, difficulty=args.difficulty,
                    start=args.start, count=args.count, workers=args.workers, repeats=args.repeats)
            for value in messages
        ],
    }
    output = json.dumps(document, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
