"""Compare nonce visitation orders over the same finite, disjoint CPU workload.

This is a benchmark experiment, not the application's CPU scheduler. No strategy
uses a known solution, changes the message, or visits a different nonce domain.
Run ``python -m hashcash_lab.strategy_benchmark --output docs/hashcash-strategies.json``.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
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

from .cpu import MAX_NONCE, ScanResult, proof_hash, validate_difficulty, validate_nonce

STRATEGIES = ("forward", "reverse", "front_back_middle", "strided")


@dataclass(frozen=True)
class NonceLane:
    start: int
    count: int
    step: int

    @property
    def last(self) -> int:
        return self.start + self.step * (self.count - 1)

    def nonces(self) -> range:
        return range(self.start, self.start + self.step * self.count, self.step)


def _positive_integer(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")


def plan_lanes(strategy: str, start: int, count: int, workers: int) -> list[NonceLane]:
    """Partition [start, start+count) without overlap, including uneven tails.

    Four mixed workers visit the first quarter forwards, last quarter backwards,
    upper middle forwards, and lower middle backwards. Other worker counts keep
    the same front/end/middle assignment principle and alternate direction.
    """
    validate_nonce(start)
    _positive_integer(count, "count")
    _positive_integer(workers, "workers")
    if workers > count:
        raise ValueError("workers cannot exceed the finite nonce count")
    validate_nonce(start + count - 1)
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    per_worker, extra = divmod(count, workers)
    if strategy == "strided":
        lanes = [NonceLane(start + index, per_worker + (index < extra), workers)
                 for index in range(workers)]
    else:
        chunks: list[NonceLane] = []
        next_start = start
        for index in range(workers):
            size = per_worker + (index < extra)
            chunks.append(NonceLane(next_start, size, 1))
            next_start += size
        if strategy == "forward":
            lanes = chunks
        elif strategy == "reverse":
            lanes = [NonceLane(chunk.last, chunk.count, -1) for chunk in reversed(chunks)]
        else:
            preferred = [0, workers - 1, workers // 2, (workers - 1) // 2]
            order = list(dict.fromkeys(preferred + list(range(workers))))
            lanes = []
            for index, chunk_index in enumerate(order):
                chunk = chunks[chunk_index]
                lanes.append(chunk if index % 2 == 0 else NonceLane(chunk.last, chunk.count, -1))
    validate_partition(lanes, start, count)
    return lanes


def validate_partition(lanes: list[NonceLane], start: int, count: int) -> None:
    """Prove exact coverage for these contiguous or residue-class partitions."""
    validate_nonce(start)
    _positive_integer(count, "count")
    validate_nonce(start + count - 1)
    if not lanes or sum(lane.count for lane in lanes) != count:
        raise ValueError("The partition must contain exactly count nonce visits")
    for lane in lanes:
        _positive_integer(lane.count, "lane.count")
        if isinstance(lane.step, bool) or not isinstance(lane.step, int) or lane.step == 0:
            raise ValueError("A lane's step must be a nonzero integer")
        validate_nonce(lane.start)
        validate_nonce(lane.last)
        if min(lane.start, lane.last) < start or max(lane.start, lane.last) >= start + count:
            raise ValueError("A lane leaves the finite common nonce window")
    if all(abs(lane.step) == 1 for lane in lanes):
        cursor = start
        for low, high in sorted((min(lane.start, lane.last), max(lane.start, lane.last))
                                for lane in lanes):
            if low != cursor:
                raise ValueError("Contiguous lanes overlap or omit a nonce")
            cursor = high + 1
        if cursor != start + count:
            raise ValueError("Contiguous lanes do not end at the window boundary")
    else:
        workers = len(lanes)
        if any(lane.step != workers for lane in lanes):
            raise ValueError("Strided lanes must use one shared worker-count stride")
        ordered = sorted(lanes, key=lambda lane: lane.start)
        for index, lane in enumerate(ordered):
            expected_count = (count - 1 - index) // workers + 1
            if lane.start != start + index or lane.count != expected_count:
                raise ValueError("Strided lanes overlap or omit a residue class")


def scan_lane(message: str, difficulty: int, lane: NonceLane, *, search: bool = False) -> ScanResult:
    """Use the production CPU hashing operations with a signed visiting step.

    Every visit performs SHA-256 and the same byte predicate. In fixed-work mode
    the first proof is recorded but the complete lane is still processed.
    """
    # proof_hash also validates exact UTF-8 conversion and the nonce domain.
    proof_hash(message, lane.start)
    validate_difficulty(difficulty)
    _positive_integer(lane.count, "lane.count")
    if isinstance(lane.step, bool) or not isinstance(lane.step, int) or lane.step == 0:
        raise ValueError("A lane's step must be a nonzero integer")
    validate_nonce(lane.last)
    if not isinstance(search, bool):
        raise ValueError("search must be bool")
    copy_prefix = sha256(message.encode("utf-8")).copy
    full_bytes, odd_digit = divmod(difficulty, 2)
    zero_prefix = bytes(full_bytes)
    first_limit = 0x10 if difficulty == 1 else 1
    found_nonce: int | None = None
    found_hash: str | None = None
    for nonce in range(lane.start, lane.start + lane.step * lane.count, lane.step):
        hasher = copy_prefix()
        hasher.update(str(nonce).encode("ascii"))
        digest = hasher.digest()
        if digest[0] < first_limit and digest.startswith(zero_prefix) and (
            not odd_digit or digest[full_bytes] < 0x10
        ):
            if found_nonce is None:
                found_nonce = nonce
                found_hash = digest.hex()
                if proof_hash(message, nonce) != found_hash:
                    raise RuntimeError("Independent proof hash does not match")
            if search:
                return {"checked": (nonce - lane.start) // lane.step + 1,
                        "nonce": found_nonce, "hash": found_hash,
                        "last_nonce": nonce, "last_hash": digest.hex()}
    return {"checked": lane.count, "nonce": found_nonce, "hash": found_hash,
            "last_nonce": nonce, "last_hash": digest.hex()}


def _run_job(job: tuple[str, int, NonceLane, bool]) -> dict[str, Any]:
    message, difficulty, lane, search = job
    began = time.perf_counter()
    result = scan_lane(message, difficulty, lane, search=search)
    return {"pid": os.getpid(), "lane": asdict(lane), "result": result,
            "worker_seconds": time.perf_counter() - began}


def _initialize_worker(barrier: Any) -> None:
    barrier.wait(timeout=60)


def _validate_results(message: str, difficulty: int, jobs: list[dict[str, Any]], *, full: bool) -> None:
    for job in jobs:
        lane = NonceLane(**job["lane"])
        result = job["result"]
        if full and result["checked"] != lane.count:
            raise RuntimeError("A fixed-work lane stopped early")
        expected_last = lane.start + lane.step * (result["checked"] - 1)
        if result["last_nonce"] != expected_last or result["last_hash"] != proof_hash(message, expected_last):
            raise RuntimeError("The final nonce/hash does not match the executed lane")
        if result["nonce"] is not None:
            nonce = result["nonce"]
            distance = (nonce - lane.start) // lane.step
            if not 0 <= distance < result["checked"] or lane.start + distance * lane.step != nonce:
                raise RuntimeError("A proof is outside its visited lane")
            independent = sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
            if result["hash"] != independent or not independent.startswith("0" * difficulty):
                raise RuntimeError("A reported proof failed independent SHA-256 validation")


def _summary(values: list[float], count: int) -> dict[str, Any]:
    median = statistics.median(values)
    return {"seconds": values, "median_seconds": median,
            "min_seconds": min(values), "max_seconds": max(values),
            "sample_stdev_seconds": statistics.stdev(values),
            "median_hashes_per_second": count / median}


def compare(*, message: str, difficulty: int, start: int, count: int,
            workers: int, repeats: int) -> dict[str, Any]:
    """Measure warmed full workloads. Startup is explicitly outside the timer."""
    validate_difficulty(difficulty)
    _positive_integer(repeats, "repeats")
    if repeats < 2:
        raise ValueError("At least two repetitions are required")
    plans = {name: plan_lanes(name, start, count, workers) for name in STRATEGIES}
    if len(str(start)) != len(str(start + count - 1)):
        raise ValueError("This benchmark requires the same decimal nonce length throughout")
    timings: dict[str, list[float]] = {name: [] for name in STRATEGIES}
    raw: list[dict[str, Any]] = []
    verified_results: dict[str, list[dict[str, Any]]] = {}
    context = mp.get_context("spawn")
    began = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers, mp_context=context,
                             initializer=_initialize_worker,
                             initargs=(context.Barrier(workers),)) as pool:
        warm_count = max(workers, min(count, workers * 20_000))
        for name in STRATEGIES:
            lanes = plan_lanes(name, start, warm_count, workers)
            results = list(pool.map(_run_job, [(message, difficulty, lane, False) for lane in lanes]))
            _validate_results(message, difficulty, results, full=True)
        setup_seconds = time.perf_counter() - began
        for repetition in range(repeats):
            # Reverse each complete sequence on the next repeat. Thus no strategy
            # is always favoured by warm-up or the first/last position.
            order = STRATEGIES if repetition % 2 == 0 else tuple(reversed(STRATEGIES))
            for name in order:
                began = time.perf_counter()
                results = list(pool.map(_run_job, [(message, difficulty, lane, False)
                                                 for lane in plans[name]]))
                elapsed = time.perf_counter() - began
                _validate_results(message, difficulty, results, full=True)
                if sum(job["result"]["checked"] for job in results) != count:
                    raise RuntimeError("The finite shared nonce window was not fully hashed")
                deterministic = [{"lane": job["lane"], "result": job["result"]} for job in results]
                if name not in verified_results:
                    verified_results[name] = deterministic
                elif deterministic != verified_results[name]:
                    raise RuntimeError("Repeated deterministic scans returned different proofs or hashes")
                timings[name].append(elapsed)
                raw.append({"repeat": repetition + 1, "order": list(order),
                            "strategy": name, "seconds": elapsed,
                            "checked": count, "worker_pids": [job["pid"] for job in results],
                            "worker_seconds": [job["worker_seconds"] for job in results]})
    summaries = {name: _summary(values, count) for name, values in timings.items()}
    baseline = summaries["forward"]["median_seconds"]
    for name, summary in summaries.items():
        summary["speedup_vs_forward"] = baseline / summary["median_seconds"]
        summary["paired_speedups_vs_forward"] = [forward / candidate for forward, candidate
                                                   in zip(timings["forward"], timings[name])]
        summary["repetitions_faster_than_forward"] = sum(candidate < forward for forward, candidate
                                                        in zip(timings["forward"], timings[name]))
    return {"message": message, "utf8_bytes": len(message.encode("utf-8")),
            "difficulty": difficulty, "start_nonce": str(start),
            "end_nonce_inclusive": str(start + count - 1), "nonce_decimal_digits": len(str(start)),
            "count": count, "workers": workers, "repeats": repeats,
            "setup_and_warmup_seconds_excluded": setup_seconds,
            "plans": {name: [asdict(lane) for lane in lanes] for name, lanes in plans.items()},
            "summaries": summaries, "raw_runs": raw, "verified_worker_results": verified_results,
            "proofs_and_last_hashes_independently_verified": True,
            "partition_check": "Contiguous intervals tile the window, or residue classes exactly partition it"}


def proof_order_trials(*, base_message: str, difficulty: int, start: int,
                       count: int, workers: int, trials: int) -> dict[str, Any]:
    """Explore visit-order luck with a synchronous equal-rate worker model.

    Each lane is truly hashed until its first proof, and that proof is independently
    validated. Minimum visits among lanes models simultaneous equal-rate workers.
    This is not an elapsed-time or live cancellation benchmark; no global winner
    is used to choose the next message or nonce window.
    """
    _positive_integer(trials, "trials")
    plans = {name: plan_lanes(name, start, count, workers) for name in STRATEGIES}
    rows: list[dict[str, Any]] = []
    for trial in range(trials):
        message = f"{base_message} [strategy trial {trial + 1:02d}]"
        for name, lanes in plans.items():
            results = [_run_job((message, difficulty, lane, True)) for lane in lanes]
            _validate_results(message, difficulty, results, full=False)
            proofs = [job for job in results if job["result"]["nonce"] is not None]
            winner = min(proofs, key=lambda job: job["result"]["checked"]) if proofs else None
            rows.append({"trial": trial + 1, "message": message, "strategy": name,
                         "ideal_equal_rate_rounds_to_first_proof":
                             winner["result"]["checked"] if winner else None,
                         "first_proof_nonce": str(winner["result"]["nonce"]) if winner else None,
                         "first_proof_hash": winner["result"]["hash"] if winner else None,
                         "worker_visits_until_first_proof_or_end": [job["result"]["checked"] for job in results],
                         "all_reported_proofs_independently_verified": True})
    wins = {name: 0 for name in STRATEGIES}
    ties = 0
    for trial in range(1, trials + 1):
        candidates = [row for row in rows if row["trial"] == trial and
                      row["ideal_equal_rate_rounds_to_first_proof"] is not None]
        if not candidates:
            continue
        fewest = min(row["ideal_equal_rate_rounds_to_first_proof"] for row in candidates)
        winners = [row["strategy"] for row in candidates if
                   row["ideal_equal_rate_rounds_to_first_proof"] == fewest]
        if len(winners) > 1:
            ties += 1
        else:
            wins[winners[0]] += 1
    return {"method": "Actual SHA-256 scans; equal-rate simultaneous-worker visit-count model, not elapsed time",
            "message_selection": "Deterministic numbered suffixes fixed before any proof scan",
            "difficulty": difficulty, "count_per_trial": count, "trials": trials,
            "sole_wins_by_visits": wins, "ties": ties, "rows": rows}


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor()


def _stringify_nonce_fields(value: Any) -> Any:
    """Keep decimal nonce values exact in the saved JSON, including uint64."""
    if isinstance(value, dict):
        return {key: str(item) if key in {"start", "nonce", "last_nonce"} and isinstance(item, int)
                else _stringify_nonce_fields(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_stringify_nonce_fields(item) for item in value]
    return value


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", default="You are the best of best!!!!")
    parser.add_argument("--difficulty", type=int, default=5)
    parser.add_argument("--start", type=int, default=1_000_000_000)
    parser.add_argument("--count", type=int, default=2_000_000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--proof-trials", type=int, default=12)
    parser.add_argument("--proof-difficulty", type=int, default=3)
    parser.add_argument("--proof-count", type=int, default=100_000)
    parser.add_argument("--measurement-notes", default="Unspecified: caller must record concurrent workload conditions")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    document = {"measured_at_utc": datetime.now(timezone.utc).isoformat(),
                "environment": {"python": sys.version, "platform": platform.platform(),
                                "processor": _cpu_name(), "logical_processors": os.cpu_count(),
                                "baseline_commit": _git_commit()},
                "benchmark_source_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                "command_arguments": sys.argv[1:],
                "finite_candidate_set": list(STRATEGIES),
                "method": "Identical finite nonce set, message, difficulty and worker count; fixed full work; "
                          "alternating order; same warmed process pool; independent proof validation outside timing",
                "assumption": "If distinct SHA-256 inputs behave like independent uniform 256-bit outputs, "
                              "each unique nonce has success probability 16^(-difficulty). Expected attempts "
                              "are 16^difficulty and expected seconds are approximately 16^difficulty / "
                              "aggregate_unique_hashes_per_second in an unbounded-trial approximation. "
                              "A finite nonce window may contain no proof. Visiting a different region gives no guaranteed advantage.",
                "measurement_notes": args.measurement_notes,
                "gpu_work_launched_by_this_tool": False,
                "fixed_work": compare(message=args.message, difficulty=args.difficulty, start=args.start,
                                      count=args.count, workers=args.workers, repeats=args.repeats)}
    if args.proof_trials:
        document["proof_visit_order_trials"] = proof_order_trials(
            base_message=args.message, difficulty=args.proof_difficulty, start=args.start,
            count=args.proof_count, workers=args.workers, trials=args.proof_trials)
    output = json.dumps(_stringify_nonce_fields(document), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
