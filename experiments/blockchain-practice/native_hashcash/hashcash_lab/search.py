"""Optional original local Python/C search jobs, without the standalone HTTP bridge.

CPU processes and optional CUDA run outside the browser. No proofs are reused
between messages, and every returned proof is verified with hashlib.
"""
from __future__ import annotations

import math
import multiprocessing as mp
import os
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable

from .cpu import scan_batch


def validate_participant_name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 60:
        raise ValueError("기록 이름은 1~60자로 입력하세요")
    return value.strip()

MAX_NONCE = (1 << 64) - 1
CPU_BATCH = 50_000
GPU_BATCH = 67_108_864


def gpu_batch_size(difficulty: int) -> int:
    """Amortize CUDA launch/readback costs while keeping low targets small.

    Keep easy proofs responsive; amortize host round trips for harder targets.
    Proof-search and batch-latency measurements are recorded separately in
    docs/hashcash-bottleneck-20261016.md. Cancellation is checked between
    completed batches, so larger launches also increase polling granularity.
    """
    target = (262_144 if difficulty <= 4 else 4_194_304 if difficulty == 5 else
              8_388_608 if difficulty == 6 else 16_777_216 if difficulty == 7 else 67_108_864)
    return min(GPU_BATCH, target)


def gpu_only_batch_size(difficulty: int) -> int:
    """Reduce host round trips for long GPU-only searches.

    CPU/GPU schedulers retain their smaller reservation policy. Each submitted
    range still finishes in full, with cancellation checked between launches.
    """
    return 1 << 30 if difficulty >= 9 else gpu_batch_size(difficulty)


@dataclass(frozen=True)
class SearchConfig:
    message: str
    difficulty: int
    start_nonce: int = 0
    workers: int = 1
    mode: str = "auto"
    participant_name: str = "나"

    @classmethod
    def from_payload(cls, payload: dict) -> SearchConfig:
        message = payload.get("message")
        difficulty = payload.get("difficulty")
        start = payload.get("startNonce", "0")
        workers = payload.get("workers", min(64, os.cpu_count() or 1))
        mode = payload.get("mode", "auto")
        participant_name = validate_participant_name(payload.get("participantName", "나"))
        if not isinstance(message, str):
            raise ValueError("메시지는 문자열이어야 합니다.")
        if len(message.encode("utf-8")) > 4096:
            raise ValueError("메시지는 UTF-8 4096바이트까지 입력할 수 있습니다.")
        if type(difficulty) is not int or not 1 <= difficulty <= 64:
            raise ValueError("난이도는 1~64 사이의 정수입니다.")
        if not isinstance(start, str) or not start.isascii() or not start.isdigit():
            raise ValueError("시작 nonce는 0 이상의 10진수 정수입니다.")
        nonce = int(start)
        if nonce > MAX_NONCE:
            raise ValueError("시작 nonce는 18446744073709551615 이하여야 합니다.")
        if type(workers) is not int or not 1 <= workers <= min(64, os.cpu_count() or 1):
            raise ValueError("CPU 작업 수가 이 기기의 범위를 벗어났습니다.")
        if mode not in ("auto", "cpu", "gpu", "hybrid", "native"):
            raise ValueError("연산 모드가 올바르지 않습니다.")
        return cls(message, difficulty, nonce, workers, mode, participant_name)


def make_gpu(message: str, difficulty: int):
    from .gpu import CudaBackend
    return CudaBackend(message, difficulty)


def make_native(*args, **kwargs):
    # Optional local build: never compile code or import CuPy on server startup.
    from .native_scheduler_experiment import NativeScheduler
    return NativeScheduler(*args, **kwargs)


class SearchJob:
    def __init__(self, config: SearchConfig, gpu_factory: Callable = make_gpu,
                 history=None, native_factory: Callable = make_native,
                 gpu_cache=None):
        self.config = config
        self.gpu_factory = gpu_factory
        self.native_factory = native_factory
        self.gpu_cache = gpu_cache
        self.gpu_lease = None
        self.history = history
        self.save_lock = threading.Lock()
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.started = time.perf_counter()
        self.state = {
            "id": uuid.uuid4().hex,
            "status": "preparing",
            "backend": "준비 중",
            "reason": "GPU 사용 가능 여부와 계산 결과를 검사합니다." if config.mode != "cpu" else "CPU 작업을 준비합니다.",
            "message": config.message,
            "participantName": config.participant_name,
            "difficulty": config.difficulty,
            "mode": config.mode,
            "startNonce": str(config.start_nonce),
            "checked": "0",
            "cpuChecked": "0",
            "gpuChecked": "0",
            "hybridOverlapSeconds": 0.0,
            "hybridKernelOverlapLowerBound": 0.0,
            "hybridTimingLimited": False,
            "hybridExecuted": False,
            "startupElapsed": None,
            "searchPreparationElapsed": 0.0,
            "proofElapsed": None,
            "gpuCacheEnabled": gpu_cache is not None,
            "gpuResourcesReused": False,
            "nativeResourcesReused": False,
            "nativeKernelsReused": False,
            "jobThreadId": None,
            "lastNonce": None,
            "lastHash": None,
            "result": None,
            "error": None,
            "cleanupWarning": None,
            "recordId": None,
            "saveError": None,
        }
        self.checked = 0

    def update(self, **changes):
        with self.lock:
            self.state.update(changes)

    def snapshot(self):
        with self.lock:
            state = dict(self.state)
            elapsed = self.state.get("elapsed", time.perf_counter() - self.started)
            state["elapsed"] = elapsed
            state["hashesPerSecond"] = self.checked / elapsed if elapsed else 0
            startup = state["startupElapsed"]
            state["preparationElapsed"] = None if startup is None else startup + state["searchPreparationElapsed"]
            state["proofSearchElapsed"] = (None if state["proofElapsed"] is None or startup is None
                                           else state["proofElapsed"] - state["preparationElapsed"])
            state["finished"] = self.thread is None or not self.thread.is_alive()
            return state

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.cancel.set()

    @contextmanager
    def borrow_gpu(self):
        if self.gpu_cache is None:
            gpu = self.gpu_factory(self.config.message, self.config.difficulty)
            try:
                yield gpu
            finally:
                if hasattr(gpu, "close"):
                    gpu.close()
        else:
            with self.gpu_cache.acquire(self.config.message, self.config.difficulty) as lease:
                self.gpu_lease = lease
                self.update(gpuResourcesReused=lease.reused,
                            nativeKernelsReused=getattr(lease.backend, "_native_functions", None) is not None)
                try:
                    yield lease.backend
                finally:
                    self.gpu_lease = None

    def invalidate_gpu(self):
        if self.gpu_lease is not None:
            self.gpu_lease.invalidate()

    def record(self, batch: dict, device: str = "cpu"):
        """Count only completed work and publish actual sampled hashes."""
        last = batch.get("last_nonce")
        with self.lock:
            self.checked += batch["checked"]
            self.state["checked"] = str(self.checked)
            field = "gpuChecked" if device == "gpu" else "cpuChecked"
            self.state[field] = str(int(self.state[field]) + batch["checked"])
            if last is not None:
                self.state["lastNonce"] = str(last)
                self.state["lastHash"] = batch.get("last_hash")

    def accept(self, batch: dict, *, proof_elapsed: float | None = None) -> bool:
        nonce = batch.get("nonce")
        if nonce is None:
            return False
        value = sha256(self.config.message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
        if value != batch.get("hash") or not value.startswith("0" * self.config.difficulty):
            raise ValueError("연산 결과의 SHA-256 검증에 실패했습니다.")
        if proof_elapsed is None:
            proof_elapsed = time.perf_counter() - self.started
        with self.lock:
            startup = self.state["startupElapsed"]
            if (isinstance(proof_elapsed, bool) or not isinstance(proof_elapsed, (int, float)) or
                    not math.isfinite(proof_elapsed) or proof_elapsed < (startup or 0)):
                raise ValueError("검증된 해답의 발견 시각이 올바르지 않습니다.")
            if self.state["result"] is None:
                self.state.update(result={"nonce": str(nonce), "hash": value, "verified": True},
                                  status="found", proofElapsed=proof_elapsed)
        return True

    def run_cpu(self, base: int):
        self.update(status="running", backend=f"CPU · {self.config.workers}개 작업")
        pool = mp.get_context("spawn").Pool(self.config.workers)
        try:
            self.update(startupElapsed=time.perf_counter() - self.started)
            while base <= MAX_NONCE and not self.cancel.is_set():
                pending = []
                for _ in range(self.config.workers):
                    if base > MAX_NONCE:
                        break
                    count = min(CPU_BATCH, MAX_NONCE - base + 1)
                    pending.append(pool.apply_async(scan_batch, (self.config.message, self.config.difficulty, base, count)))
                    base += count
                while pending and not self.cancel.is_set():
                    for task in list(pending):
                        if task.ready():
                            pending.remove(task)
                            batch = task.get()
                            self.record(batch)
                            if self.accept(batch):
                                return
                    self.cancel.wait(0.01)
            self.update(status="stopped" if self.cancel.is_set() else "exhausted")
        finally:
            pool.terminate()
            pool.join()

    def run_hybrid(self, gpu, base: int):
        from .hybrid import HybridCoordinator

        name = f"CPU + GPU · {self.config.workers}개 CPU 작업 + {gpu.name}"
        self.update(status="running", backend=name,
                    reason="CPU와 CUDA에 겹치지 않는 구간을 배정합니다. 동시 연산이 항상 더 빠르지는 않습니다.")

        def completed(device, batch, _timing):
            self.record(batch, device)
            # Already-submitted CPU tasks are drained after a GPU/CPU proof.
            # Keep the first independently verified result while counting all
            # completed batches, so cleanup cannot change a saved proof.
            if self.snapshot()["result"] is None:
                self.accept(batch)

        def gpu_failed(reason):
            self.invalidate_gpu()
            self.update(backend=f"CPU · {self.config.workers}개 작업 (동시 모드 GPU 전환)",
                        reason=f"GPU 사용 불가 → 미완료 구간 CPU 재시도: {reason}")

        runner = HybridCoordinator(self.config.message, self.config.difficulty, self.config.workers,
                                   cpu_batch=min(CPU_BATCH, 10_000),
                                   gpu_batch=gpu_batch_size(self.config.difficulty))
        result = runner.run(gpu, base, cancel=self.cancel, on_batch=completed,
                            on_gpu_failure=gpu_failed,
                            on_ready=lambda: self.update(startupElapsed=time.perf_counter() - self.started))
        self.update(hybridOverlapSeconds=result.overlap,
                    hybridKernelOverlapLowerBound=result.kernel_overlap_lower_bound,
                    hybridTimingLimited=result.timings_truncated,
                    cleanupWarning=result.cleanup_warning,
                    hybridExecuted=bool(result.cpu_checked and result.gpu_checked))
        if not result.found:
            self.update(status="stopped" if result.stopped else "exhausted")

    def run_native(self, gpu, base: int):
        """Keep scheduling/CUDA/readback in C; observe totals at 10 Hz only.

        An exclusive lease spans each run and its observer. Sequential jobs
        can transfer idle prepared resources to another thread; observers must
        join before reset, handoff or destruction. Failed ranges never restart.
        """
        from .hybrid import BatchTiming, kernel_overlap_lower_bound, overlap_seconds

        if self.cancel.is_set():
            self.update(status="stopped")
            return

        def create():
            return self.native_factory(self.config.message, self.config.difficulty,
                                       workers=self.config.workers, cpu_batch=64_000,
                                       gpu_batch=gpu_batch_size(self.config.difficulty),
                                       trace_capacity=8192)

        owned = True
        attached = False
        try:
            if gpu is not None and self.gpu_lease is not None:
                try:
                    runner, reused = self.gpu_lease.native_runner(
                        self.native_factory, workers=self.config.workers, cpu_batch=64_000,
                        gpu_batch=gpu_batch_size(self.config.difficulty), trace_capacity=8192)
                    owned, attached = False, True
                    self.update(nativeResourcesReused=reused)
                except Exception as exc:
                    self.invalidate_gpu()
                    gpu = None
                    self.update(reason=f"C CUDA 준비 불가 → C CPU 전환: {type(exc).__name__}: {str(exc)[:700]}")
                    runner = create()
            else:
                runner = create()
        except Exception as exc:
            self.update(reason=f"C 연산 준비 불가 → Python CPU 전환: {type(exc).__name__}: {str(exc)[:700]}")
            if self.cancel.is_set():
                self.update(status="stopped")
            else:
                self.run_cpu(base)
            return

        try:
            if gpu is not None and not attached:
                try:
                    runner.attach_gpu(gpu)
                except Exception as exc:
                    self.invalidate_gpu()
                    # No range has run yet. Discard any partial CUDA adapter.
                    runner.close()
                    runner = create()
                    gpu = None
                    self.update(reason=f"C CUDA 준비 불가 → C CPU 전환: {type(exc).__name__}: {str(exc)[:700]}")
            if self.cancel.is_set():
                self.update(status="stopped")
                return
            if gpu is not None:
                self.update(backend=f"C · CPU + GPU · {self.config.workers}개 CPU 작업 + {gpu.name}",
                            reason=("준비된 CUDA 커널과 C 연산 자원을 재사용합니다." if self.snapshot()["nativeResourcesReused"] else
                                    "작업 배분·해시 계산·CUDA 호출·결과 수집을 C에서 수행합니다."))
            else:
                self.update(backend=f"C · CPU · {self.config.workers}개 작업")
            self.update(status="running", startupElapsed=time.perf_counter() - self.started)
            cpu_offset = gpu_offset = 0
            host_overlap = kernel_overlap = 0.0
            timing_limited = False
            while base <= MAX_NONCE and not self.cancel.is_set():
                # C accepts uint64 count, so the 2^64 domain needs two runs.
                count = min(MAX_NONCE, MAX_NONCE - base + 1)
                runner.reset(base, count, search=True)
                observer_done = threading.Event()
                observer_errors = []

                def publish(stats):
                    cpu, cuda, checked = stats["cpu_checked"], stats["gpu_checked"], stats["checked"]
                    if (any(type(n) is not int or n < 0 for n in (cpu, cuda, checked)) or
                            checked != cpu + cuda or checked > count):
                        raise ValueError("C 장치별 완료 계산 수가 올바르지 않습니다.")
                    nonce = stats.get("nonce")
                    if nonce is not None and not base <= int(nonce) < base + count:
                        raise ValueError("C 결과 nonce가 제출한 탐색 범위를 벗어났습니다.")
                    with self.lock:
                        self.checked = cpu_offset + gpu_offset + checked
                        self.state.update(checked=str(self.checked),
                                          cpuChecked=str(cpu_offset + cpu),
                                          gpuChecked=str(gpu_offset + cuda),
                                          hybridExecuted=bool(cpu_offset + cpu and gpu_offset + cuda))
                        if stats.get("last_nonce") is not None:
                            self.state.update(lastNonce=str(stats["last_nonce"]), lastHash=stats.get("last_hash"))
                        has_proof = self.state["result"] is not None
                    if nonce is not None and not has_proof:
                        # C and Python use Windows QPC seconds on this path.
                        # Preserve C's first verified receipt time, rather than
                        # the observer's next 100ms publication time.
                        self.accept(stats, proof_elapsed=stats["begin"] + stats["first_proof_seconds"] - self.started)
                    if stats.get("gpu_failed"):
                        self.invalidate_gpu()
                        detail = stats.get("gpu_error") or "CUDA 배치 실패"
                        self.update(backend=f"C · CPU · {self.config.workers}개 작업 (GPU 전환)",
                                    reason=f"GPU 사용 불가 → 미완료 구간 C CPU 재시도: {str(detail)[:700]}")
                    if stats.get("cleanup_warning"):
                        self.invalidate_gpu()
                        self.update(cleanupWarning=str(stats["cleanup_warning"])[:1000])

                def observe():
                    while not observer_done.is_set():
                        try:
                            # hc_cancel before hc_run is a no-op. Repeat it so
                            # a stop during the transition cannot be lost.
                            if self.cancel.is_set() or observer_errors:
                                runner.cancel()
                            if not observer_errors:
                                publish(runner.stats())
                        except Exception as exc:
                            if not observer_errors:
                                observer_errors.append(exc)
                        observer_done.wait(0.1)

                observer = threading.Thread(target=observe, daemon=True)
                observer.start()
                try:
                    try:
                        stats = runner.run()
                    finally:
                        observer_done.set()
                        observer.join()
                except Exception:
                    # Native errors can arrive between observer ticks. Keep
                    # completed totals/proofs if the final snapshot validates,
                    # then propagate the original failure without a restart.
                    try:
                        publish(runner.stats())
                    except Exception:
                        pass
                    raise
                publish(stats)
                if observer_errors:
                    raise observer_errors[0]
                timings = [BatchTiming("cpu" if item["kind"] == 1 else "gpu",
                                       int(item["start"]), item["count"], item["checked"],
                                       item["begin"], item["end"], item["kernel_seconds"])
                           for item in runner.traces() if not item["status"] and item["checked"]]
                host_overlap += overlap_seconds(timings)
                frequency = stats.get("qpc_frequency", 0)
                uncertainty = (sum(item.device == "gpu" for item in timings) * 2 / frequency
                               if frequency > 0 else float("inf"))
                kernel_overlap += max(0.0, kernel_overlap_lower_bound(timings) - uncertainty)
                timing_limited |= bool(stats.get("trace_dropped"))
                self.update(hybridOverlapSeconds=host_overlap,
                            hybridKernelOverlapLowerBound=kernel_overlap,
                            hybridTimingLimited=timing_limited)
                if self.snapshot()["result"] is not None:
                    return
                if self.cancel.is_set() or stats.get("cancelled"):
                    self.update(status="stopped")
                    return
                if stats.get("status") or stats["checked"] != count:
                    raise RuntimeError(stats.get("error") or "C 탐색이 미완료 구간을 남겼습니다.")
                cpu_offset += stats["cpu_checked"]
                gpu_offset += stats["gpu_checked"]
                base += count
            self.update(status="stopped" if self.cancel.is_set() else "exhausted")
        finally:
            if owned:
                runner.close()

    def run_search(self, resources):
        base = self.config.start_nonce
        if self.cancel.is_set():
            self.update(status="stopped")
            return
        if self.config.mode == "native":
            gpu = None
            try:
                gpu = resources.enter_context(self.borrow_gpu())
            except Exception as exc:
                self.update(reason=f"GPU 사용 불가 → C CPU 사용: {type(exc).__name__}: {str(exc)[:700]}")
            self.run_native(gpu, base)
            return
        if self.config.mode != "cpu":
            gpu = None
            try:
                gpu = resources.enter_context(self.borrow_gpu())
                if self.cancel.is_set():
                    self.update(status="stopped")
                    return
                if self.config.mode == "hybrid":
                    self.run_hybrid(gpu, base)
                    return
                self.update(status="running", backend=f"GPU · {gpu.name}", reason="CUDA 가용성·SHA-256 자체 검증 통과")
                self.update(startupElapsed=time.perf_counter() - self.started)
                while base <= MAX_NONCE and not self.cancel.is_set():
                    count = min(gpu_only_batch_size(self.config.difficulty), MAX_NONCE - base + 1)
                    batch = gpu.scan_batch(base, count)
                    self.update(searchPreparationElapsed=getattr(gpu, "search_preparation_seconds", 0.0))
                    checked = batch.get("checked")
                    nonce = batch.get("nonce")
                    if type(checked) is not int or not 1 <= checked <= count:
                        raise ValueError("GPU가 잘못된 탐색 개수를 반환했습니다.")
                    if nonce is not None and (type(nonce) is not int or not base <= nonce < base + checked):
                        raise ValueError("GPU 결과 nonce가 제출한 탐색 범위를 벗어났습니다.")
                    found = self.accept(batch)
                    self.record(batch, "gpu")
                    base += batch["checked"]
                    if found:
                        return
                self.update(status="stopped" if self.cancel.is_set() else "exhausted")
                return
            except Exception as exc:
                self.invalidate_gpu()
                if self.config.mode == "hybrid" and gpu is not None:
                    raise
                self.update(reason=f"GPU 사용 불가 → CPU 자동 전환: {type(exc).__name__}: {str(exc)[:700]}")
        else:
            self.update(reason="CPU 모드를 직접 선택했습니다.")
        if self.cancel.is_set():
            self.update(status="stopped")
        else:
            self.run_cpu(base)

    def run(self):
        self.update(jobThreadId=threading.get_native_id())
        try:
            with ExitStack() as resources:
                self.run_search(resources)
        except Exception as exc:
            state = self.snapshot()
            if state["result"] and state["result"].get("verified"):
                self.update(status="found", cleanupWarning=f"작업 종료 중 오류: {type(exc).__name__}: {exc}")
            else:
                self.update(status="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            self.update(elapsed=time.perf_counter() - self.started)

