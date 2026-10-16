"""Disjoint CPU/CUDA scheduling, also usable for fixed-work measurements.

Only this controller allocates ranges. CUDA stays on its creating thread, while
spawned CPU processes report their own calculation timestamps. Optional CuPy is
never imported by this module or by its CPU worker entry point.
"""
from __future__ import annotations

import multiprocessing as mp
import math
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Callable

from .cpu import MAX_NONCE, scan_batch, validate_difficulty, validate_nonce
from .gpu import batch_count


def timed_cpu_scan(message: str, difficulty: int, start: int, count: int, search: bool):
    """Top-level spawn-safe worker, with timestamps of actual CPU calculation."""
    started = time.perf_counter()
    batch = scan_batch(message, difficulty, start, count, search=search)
    return batch, started, time.perf_counter()


@dataclass(frozen=True)
class BatchTiming:
    device: str
    start: int
    count: int
    checked: int
    started: float
    finished: float
    kernel_seconds: float = 0.0

    def json(self) -> dict:
        value = asdict(self)
        value["start"] = str(self.start)
        value["checked"] = str(self.checked)
        return value


def _merged_intervals(timings, device):
    spans = sorted((item.started, item.finished) for item in timings if item.device == device)
    result = []
    for first, last in spans:
        if result and first <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], last))
        else:
            result.append((first, last))
    return result


def overlap_seconds(timings) -> float:
    """Wall-time intersection of completed CPU and GPU batch execution periods.

    CPU process intervals are unioned first, so multiple cores are not counted
    twice. GPU intervals include launch/readback and are not CUDA-event timings.
    """
    cpu, gpu = _merged_intervals(timings, "cpu"), _merged_intervals(timings, "gpu")
    left = right = 0
    total = 0.0
    while left < len(cpu) and right < len(gpu):
        total += max(0.0, min(cpu[left][1], gpu[right][1]) - max(cpu[left][0], gpu[right][0]))
        if cpu[left][1] <= gpu[right][1]:
            left += 1
        else:
            right += 1
    return total


def _kernel_seconds(value, host_duration: float) -> float:
    # A kernel cannot occupy more time than its enclosing synchronous host
    # interval. Contradictory or missing metadata is not overlap evidence.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) if math.isfinite(value) and 0 <= value <= host_duration else 0.0


def kernel_overlap_lower_bound(timings) -> float:
    """Conservative actual-kernel/CPU overlap without synchronizing clocks.

    Inside each synchronous CUDA host span, at most ``host - kernel`` seconds
    can be compilation/launch/readback instead of kernel execution. Subtract
    all of that non-kernel time from the CPU-union intersection. A positive
    remainder proves actual kernel and CPU computation overlapped. Discarded
    trace entries can only reduce this lower bound.
    """
    cpu = _merged_intervals(timings, "cpu")
    total = 0.0
    for item in timings:
        if item.device != "gpu":
            continue
        duration = max(0.0, item.finished - item.started)
        kernel = _kernel_seconds(item.kernel_seconds, duration)
        intersection = sum(max(0.0, min(last, item.finished) - max(first, item.started))
                           for first, last in cpu)
        total += max(0.0, intersection - (duration - kernel))
    return total


def validate_batch(message: str, difficulty: int, batch: dict, start: int, count: int,
                   *, partial: bool = False) -> None:
    """Reject malformed results before either counting or moving a range."""
    checked = batch.get("checked")
    nonce = batch.get("nonce")
    if type(checked) is not int or not 1 <= checked <= count:
        raise ValueError("연산 장치가 잘못된 탐색 개수를 반환했습니다.")
    if checked != count and not (partial and nonce is not None):
        raise ValueError("연산 장치가 예약한 탐색 구간을 끝내지 않았습니다.")
    last = start + checked - 1
    if type(batch.get("last_nonce")) is not int or batch["last_nonce"] != last:
        raise ValueError("연산 장치의 마지막 nonce가 완료 구간과 다릅니다.")
    last_hash = sha256(message.encode("utf-8") + str(last).encode("ascii")).hexdigest()
    if batch.get("last_hash") != last_hash:
        raise ValueError("마지막 nonce의 SHA-256 검증에 실패했습니다.")
    if nonce is not None:
        if type(nonce) is not int or not start <= nonce < start + checked:
            raise ValueError("연산 결과 nonce가 제출한 탐색 범위를 벗어났습니다.")
        value = sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
        if batch.get("hash") != value or not value.startswith("0" * difficulty):
            raise ValueError("연산 결과의 SHA-256 검증에 실패했습니다.")
    elif batch.get("hash") is not None:
        raise ValueError("Nonce 없이 해시를 반환했습니다.")


@dataclass
class HybridResult:
    cpu_checked: int = 0
    gpu_checked: int = 0
    found: bool = False
    stopped: bool = False
    gpu_error: str | None = None
    cleanup_warning: str | None = None
    timings: list[BatchTiming] | deque[BatchTiming] = field(default_factory=list)
    timings_truncated: bool = False

    @property
    def checked(self):
        return self.cpu_checked + self.gpu_checked

    @property
    def overlap(self):
        return overlap_seconds(self.timings)

    @property
    def kernel_overlap_lower_bound(self):
        return kernel_overlap_lower_bound(self.timings)


class HybridCoordinator:
    """Allocate finite contiguous batches to CUDA and CPU without overlap.

    ``count=None`` searches through uint64; a finite count plus ``search=False``
    finishes exactly that many unique nonces regardless of proof discovery.
    An external already-warmed spawn Pool can be supplied for measurements. It
    remains owned by the caller. The ordinary application creates its own Pool.
    Production retains at most 4096 timing records; measurements can explicitly
    pass ``trace_limit=None``. With truncated traces, overlap metrics are lower
    bounds from the retained window; aggregate completed counts remain exact.
    """

    def __init__(self, message: str, difficulty: int, workers: int, *,
                 cpu_batch: int = 50_000, gpu_batch: int = 8_388_608,
                 pool_factory: Callable | None = None):
        validate_difficulty(difficulty)
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be positive")
        if type(cpu_batch) is not int or cpu_batch < 1:
            raise ValueError("cpu_batch must be positive")
        if type(gpu_batch) is not int or not 1 <= gpu_batch < 1 << 32:
            raise ValueError("gpu_batch must be in 1..2^32-1")
        self.message, self.difficulty, self.workers = message, difficulty, workers
        self.cpu_batch, self.gpu_batch = cpu_batch, gpu_batch
        self.pool_factory = pool_factory or (lambda size: mp.get_context("spawn").Pool(size))

    def run(self, gpu, start: int, count: int | None = None, *, search: bool = True,
            cancel: threading.Event | None = None, pool=None,
            on_batch: Callable | None = None, on_gpu_failure: Callable | None = None,
            on_ready: Callable | None = None, trace_limit: int | None = 4096) -> HybridResult:
        validate_nonce(start)
        if count is not None and (type(count) is not int or count < 1 or start + count - 1 > MAX_NONCE):
            raise ValueError("count must describe a nonempty uint64 range")
        if type(search) is not bool:
            raise ValueError("search must be bool")
        if trace_limit is not None and (type(trace_limit) is not int or trace_limit < 1):
            raise ValueError("trace_limit must be a positive integer or None")
        cancel = cancel if cancel is not None else threading.Event()
        result = HybridResult(timings=[] if trace_limit is None else deque(maxlen=trace_limit))
        if cancel.is_set():
            result.stopped = True
            return result
        stop = MAX_NONCE + 1 if count is None else start + count
        cursor = start
        retries = deque()
        pending = []
        own_pool = pool is None
        pool = self.pool_factory(self.workers) if own_pool else pool

        def allocate_cpu():
            nonlocal cursor
            if retries:
                first, length = retries.popleft()
                take = min(self.cpu_batch, length)
                if take < length:
                    retries.appendleft((first + take, length - take))
                return first, take
            if cursor >= stop:
                return None
            take = min(self.cpu_batch, stop - cursor)
            first, cursor = cursor, cursor + take
            return first, take

        def complete(device, span, batch, began, ended):
            validate_batch(self.message, self.difficulty, batch, *span, partial=device == "cpu" and search)
            kernel = _kernel_seconds(batch.get("kernel_seconds"), ended - began) if device == "gpu" else 0.0
            timing = BatchTiming(device, *span, batch["checked"], began, ended, kernel)
            if trace_limit is not None and len(result.timings) >= trace_limit:
                result.timings_truncated = True
            result.timings.append(timing)
            if device == "cpu":
                result.cpu_checked += batch["checked"]
            else:
                result.gpu_checked += batch["checked"]
            if search and batch.get("nonce") is not None:
                result.found = True
            if on_batch is not None:
                on_batch(device, batch, timing)

        def receive_cpu(block=False):
            for item in list(pending):
                task, span = item
                if block or task.ready():
                    pending.remove(item)
                    try:
                        batch, began, ended = task.get()
                        complete("cpu", span, batch, began, ended)
                    except Exception as exc:
                        if not result.found:
                            raise
                        # A later worker failure cannot invalidate an already
                        # independently verified proof. Finish cleanup and let
                        # the caller save it, while retaining a visible warning.
                        warning = f"CPU 정리 중 오류: {type(exc).__name__}: {str(exc)[:700]}"
                        result.cleanup_warning = (result.cleanup_warning + "; " + warning
                                                  if result.cleanup_warning else warning)

        try:
            if on_ready is not None:
                on_ready()
            while not cancel.is_set() and not result.found:
                receive_cpu()
                if result.found:
                    break
                # Reserve CUDA before submitting CPU work. A CUDA range never
                # crosses decimal length, even when CPU tasks advance cursor.
                gpu_span = None
                if gpu is not None and cursor < stop:
                    take = batch_count(cursor, min(self.gpu_batch, stop - cursor))
                    gpu_span = (cursor, take)
                    cursor += take
                while len(pending) < self.workers:
                    span = allocate_cpu()
                    if span is None:
                        break
                    task = pool.apply_async(timed_cpu_scan, (self.message, self.difficulty, *span, search))
                    pending.append((task, span))
                if gpu_span is not None:
                    began = time.perf_counter()
                    try:
                        batch = gpu.scan_batch(*gpu_span)
                        ended = time.perf_counter()
                        validate_batch(self.message, self.difficulty, batch, *gpu_span)
                    except Exception as exc:
                        # No result from this range has been counted. Existing
                        # CPU tasks keep their reservations; only this range is
                        # returned to the CPU retry queue, before later work.
                        retries.appendleft(gpu_span)
                        gpu = None
                        result.gpu_error = f"{type(exc).__name__}: {str(exc)[:700]}"
                        if on_gpu_failure is not None:
                            on_gpu_failure(result.gpu_error)
                    else:
                        complete("gpu", gpu_span, batch, began, ended)
                receive_cpu()
                if gpu_span is None and not pending and not retries and cursor >= stop:
                    break
                # CUDA may still be healthy after the finite range is fully
                # reserved. Yield while only CPU results remain, otherwise
                # this loop starves the Pool result-handler under the GIL.
                if gpu_span is None and pending and not result.found:
                    cancel.wait(0.001)
            # On success all already-submitted finite CPU batches are drained,
            # verified and counted. The first accepted proof remains the result.
            # On cancellation unfinished batches are terminated and not counted.
            if result.found or not own_pool:
                receive_cpu(block=True)
            else:
                receive_cpu()
            result.stopped = cancel.is_set() and not result.found
            return result
        finally:
            if own_pool:
                active_error = sys.exc_info()[1]
                cleanup_error = None
                for cleanup in (pool.terminate, pool.join):
                    try:
                        cleanup()
                    except Exception as exc:
                        if not result.found:
                            cleanup_error = cleanup_error or exc
                        else:
                            warning = f"작업 종료 중 오류: {type(exc).__name__}: {str(exc)[:700]}"
                            result.cleanup_warning = (result.cleanup_warning + "; " + warning
                                                      if result.cleanup_warning else warning)
                if cleanup_error is not None and active_error is None:
                    raise cleanup_error
