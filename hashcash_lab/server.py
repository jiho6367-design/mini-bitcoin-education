"""Loopback-only HTTP bridge for the standalone learning screen.

CPU processes and optional CUDA run outside the browser. No proofs are reused
between messages, and every returned proof is verified with hashlib.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import threading
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .cpu import scan_batch

MAX_NONCE = (1 << 64) - 1
CPU_BATCH = 50_000
GPU_BATCH = 262_144


@dataclass(frozen=True)
class SearchConfig:
    message: str
    difficulty: int
    start_nonce: int = 0
    workers: int = 1
    mode: str = "auto"

    @classmethod
    def from_payload(cls, payload: dict) -> SearchConfig:
        message = payload.get("message")
        difficulty = payload.get("difficulty")
        start = payload.get("startNonce", "0")
        workers = payload.get("workers", min(64, os.cpu_count() or 1))
        mode = payload.get("mode", "auto")
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
        if mode not in ("auto", "cpu", "gpu"):
            raise ValueError("연산 모드가 올바르지 않습니다.")
        return cls(message, difficulty, nonce, workers, mode)


def make_gpu(message: str, difficulty: int):
    from .gpu import CudaBackend
    return CudaBackend(message, difficulty)


class SearchJob:
    def __init__(self, config: SearchConfig, gpu_factory: Callable = make_gpu):
        self.config = config
        self.gpu_factory = gpu_factory
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
            "difficulty": config.difficulty,
            "startNonce": str(config.start_nonce),
            "checked": "0",
            "lastNonce": None,
            "lastHash": None,
            "result": None,
            "error": None,
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
            state["finished"] = self.thread is None or not self.thread.is_alive()
            return state

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.cancel.set()

    def record(self, batch: dict):
        """Count only completed work and publish actual sampled hashes."""
        last = batch.get("last_nonce")
        with self.lock:
            self.checked += batch["checked"]
            self.state["checked"] = str(self.checked)
            if last is not None:
                self.state["lastNonce"] = str(last)
                self.state["lastHash"] = batch.get("last_hash")

    def accept(self, batch: dict) -> bool:
        nonce = batch.get("nonce")
        if nonce is None:
            return False
        value = sha256(self.config.message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
        if value != batch.get("hash") or not value.startswith("0" * self.config.difficulty):
            raise ValueError("연산 결과의 SHA-256 검증에 실패했습니다.")
        self.update(result={"nonce": str(nonce), "hash": value, "verified": True}, status="found")
        return True

    def run_cpu(self, base: int):
        self.update(status="running", backend=f"CPU · {self.config.workers}개 작업")
        pool = mp.get_context("spawn").Pool(self.config.workers)
        try:
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

    def run(self):
        base = self.config.start_nonce
        try:
            if self.config.mode != "cpu":
                gpu = None
                try:
                    gpu = self.gpu_factory(self.config.message, self.config.difficulty)
                    if self.cancel.is_set():
                        self.update(status="stopped")
                        return
                    self.update(status="running", backend=f"GPU · {gpu.name}", reason="CUDA 가용성·SHA-256 자체 검증 통과")
                    while base <= MAX_NONCE and not self.cancel.is_set():
                        count = min(GPU_BATCH, MAX_NONCE - base + 1)
                        batch = gpu.scan_batch(base, count)
                        checked = batch.get("checked")
                        nonce = batch.get("nonce")
                        if type(checked) is not int or not 1 <= checked <= count:
                            raise ValueError("GPU가 잘못된 탐색 개수를 반환했습니다.")
                        if nonce is not None and (type(nonce) is not int or not base <= nonce < base + checked):
                            raise ValueError("GPU 결과 nonce가 제출한 탐색 범위를 벗어났습니다.")
                        # Verify before advancing. A failing launch is retried on CPU.
                        found = self.accept(batch)
                        self.record(batch)
                        base += batch["checked"]
                        if found:
                            return
                    self.update(status="stopped" if self.cancel.is_set() else "exhausted")
                    return
                except Exception as exc:
                    self.update(reason=f"GPU 사용 불가 → CPU 자동 전환: {type(exc).__name__}: {str(exc)[:700]}")
                finally:
                    if gpu is not None and hasattr(gpu, "close"):
                        try:
                            gpu.close()
                        except Exception:
                            pass
            else:
                self.update(reason="CPU 모드를 직접 선택했습니다.")
            if self.cancel.is_set():
                self.update(status="stopped")
            else:
                self.run_cpu(base)
        except Exception as exc:
            self.update(status="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            self.update(elapsed=time.perf_counter() - self.started)


class LabServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, instance: str | None = None):
        super().__init__(address, Handler)
        self.job: SearchJob | None = None
        self.job_lock = threading.Lock()
        self.instance = instance

    def server_close(self):
        if self.job:
            self.job.stop()
            if self.job.thread:
                self.job.thread.join(timeout=5)
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: LabServer

    def log_message(self, *_):
        pass

    def reply(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/hashcash/health":
            self.reply(200, {"ok": True, "cpuCount": min(64, os.cpu_count() or 1), "protocol": "sha256-utf8-decimal-hexzeros-v1", "instance": self.server.instance})
        elif self.path == "/api/hashcash/status":
            self.reply(200, {"job": self.server.job.snapshot() if self.server.job else None})
        else:
            self.reply(404, {"error": "경로를 찾을 수 없습니다."})

    def do_POST(self):
        # The frontend is same-origin through Vite. Reject cross-origin writes.
        allowed = {"http://127.0.0.1:5186", "http://localhost:5186", f"http://127.0.0.1:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        if origin and origin not in allowed:
            self.reply(403, {"error": "허용되지 않은 요청 출처입니다."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json" or not 0 < length <= 100_000:
                raise ValueError("JSON 요청 형식이 필요합니다.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("요청 본문은 객체여야 합니다.")
            if self.path == "/api/hashcash/start":
                config = SearchConfig.from_payload(payload)
                with self.server.job_lock:
                    if self.server.job and not self.server.job.snapshot()["finished"]:
                        self.reply(409, {"error": "진행 중인 탐색을 먼저 중지하세요."})
                        return
                    self.server.job = SearchJob(config)
                    self.server.job.start()
                self.reply(200, {"job": self.server.job.snapshot()})
            elif self.path == "/api/hashcash/stop":
                if self.server.job:
                    self.server.job.stop()
                self.reply(200, {"ok": True})
            elif self.path == "/api/hashcash/verify":
                config = SearchConfig.from_payload({**payload, "startNonce": payload.get("nonce"), "workers": 1, "mode": "cpu"})
                value = sha256(config.message.encode("utf-8") + str(config.start_nonce).encode("ascii")).hexdigest()
                self.reply(200, {"hash": value, "valid": value.startswith("0" * config.difficulty)})
            else:
                self.reply(404, {"error": "경로를 찾을 수 없습니다."})
        except (ValueError, TypeError, UnicodeError) as exc:
            self.reply(400, {"error": str(exc)})


def create_server(host: str = "127.0.0.1", port: int = 9186, instance: str | None = None) -> LabServer:
    if host != "127.0.0.1":
        raise ValueError("서버는 이 컴퓨터의 127.0.0.1에서만 실행합니다.")
    return LabServer((host, port), instance)


def main():
    parser = argparse.ArgumentParser(description="Standalone hashcash loopback server")
    parser.add_argument("--port", type=int, default=9186)
    parser.add_argument("--instance", default=None)
    args = parser.parse_args()
    mp.freeze_support()
    server = create_server(port=args.port, instance=args.instance)
    print(f"Hashcash backend: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
