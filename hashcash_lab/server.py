"""Loopback-only HTTP bridge for the standalone learning screen.

CPU processes and optional CUDA run outside the browser. No proofs are reused
between messages, and every returned proof is verified with hashlib.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from .cpu import scan_batch
from .history import HistoryStore, PROTOCOL, utc_now, validate_participant_name

MAX_NONCE = (1 << 64) - 1
CPU_BATCH = 50_000
GPU_BATCH = 8_388_608


def gpu_batch_size(difficulty: int) -> int:
    """Amortize CUDA launch/readback costs while keeping low targets small.

    On the measured RTX 5070 Laptop, even the largest warmed batch takes only
    a few milliseconds. Cancellation is checked between completed batches.
    """
    target = 262_144 if difficulty <= 4 else 4_194_304 if difficulty == 5 else 8_388_608
    return min(GPU_BATCH, target)


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
        if mode not in ("auto", "cpu", "gpu"):
            raise ValueError("연산 모드가 올바르지 않습니다.")
        return cls(message, difficulty, nonce, workers, mode, participant_name)


def make_gpu(message: str, difficulty: int):
    from .gpu import CudaBackend
    return CudaBackend(message, difficulty)


class SearchJob:
    def __init__(self, config: SearchConfig, gpu_factory: Callable = make_gpu, history: HistoryStore | None = None):
        self.config = config
        self.gpu_factory = gpu_factory
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
            "startNonce": str(config.start_nonce),
            "checked": "0",
            "lastNonce": None,
            "lastHash": None,
            "result": None,
            "error": None,
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
            state["finished"] = self.thread is None or not self.thread.is_alive()
            return state

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.cancel.set()

    def save_result(self):
        """Persist only a completed verified proof; disk errors preserve found.

        Called after calculation cleanup. An HTTP retry is serialized with an
        automatic save, and the database job identifier makes retries idempotent.
        """
        with self.save_lock:
            state = self.snapshot()
            if state["status"] != "found" or not state["result"] or not state["result"].get("verified"):
                raise ValueError("검증을 마친 탐색 결과만 저장할 수 있습니다.")
            if self.history is None:
                return state
            try:
                record = self.history.save(
                    job_id=state["id"], participant_name=self.config.participant_name,
                    message=self.config.message, difficulty=self.config.difficulty,
                    nonce=state["result"]["nonce"], hash_value=state["result"]["hash"],
                    backend=state["backend"], checked=state["checked"], elapsed=state["elapsed"],
                )
            except Exception as exc:
                self.update(saveError=f"기록 저장 실패: {type(exc).__name__}: {str(exc)[:500]}")
            else:
                self.update(recordId=record["id"], saveError=None)
            return self.snapshot()

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
                        count = min(gpu_batch_size(self.config.difficulty), MAX_NONCE - base + 1)
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
            if self.snapshot()["status"] == "found" and self.history is not None:
                self.save_result()


class LabServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, instance: str | None = None, data_dir: str | None = None):
        super().__init__(address, Handler)
        self.history = HistoryStore(data_dir)
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

    def reply(self, status: int, data: dict, *, headers: dict | None = None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Changing history filters aborts the previous browser request.
            # A disconnected client must not become a storage failure.
            self.close_connection = True

    def do_GET(self):
        try:
            url = urlsplit(self.path)
            if url.path == "/api/hashcash/health":
                self.reply(200, {"ok": True, "cpuCount": min(64, os.cpu_count() or 1), "protocol": PROTOCOL, "instance": self.server.instance})
            elif url.path == "/api/hashcash/status":
                self.reply(200, {"job": self.server.job.snapshot() if self.server.job else None})
            elif url.path in ("/api/hashcash/history", "/api/hashcash/history/export"):
                export = url.path.endswith("/export")
                query = parse_qs(url.query, keep_blank_values=True)
                allowed = {"participantName", "difficulty", "message"} | (set() if export else {"limit"})
                if set(query) - allowed or any(len(values) != 1 for values in query.values()):
                    raise ValueError("기록 조회 조건이 올바르지 않습니다.")
                options = {"limit": None if export else 100}
                for key, field in (("participantName", "participant_name"), ("message", "message")):
                    if key in query:
                        options[field] = query[key][0]
                for key in ("difficulty", "limit"):
                    if key in query:
                        value = query[key][0]
                        if not value.isascii() or not value.isdigit():
                            raise ValueError("난이도와 기록 조회 수는 정수여야 합니다.")
                        options[key] = int(value)
                result = self.server.history.query(**options)
                if export:
                    result.update(exportedAt=utc_now(), protocol=PROTOCOL)
                    self.reply(200, result, headers={"Content-Disposition": 'attachment; filename="hashcash-history.json"'})
                else:
                    self.reply(200, result)
            else:
                self.reply(404, {"error": "경로를 찾을 수 없습니다."})
        except (ValueError, TypeError, UnicodeError) as exc:
            self.reply(400, {"error": str(exc)})
        except (OSError, sqlite3.Error) as exc:
            self.reply(503, {"error": f"기록을 읽지 못했습니다: {type(exc).__name__}: {str(exc)[:500]}"})

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
                    if self.server.job:
                        previous = self.server.job.snapshot()
                        if not previous["finished"]:
                            self.reply(409, {"error": "진행 중인 탐색을 먼저 중지하세요."})
                            return
                        if previous["status"] == "found" and previous["result"] and previous["result"].get("verified") and not previous["recordId"] and previous["saveError"]:
                            self.reply(409, {"error": "발견 결과의 기록 저장을 다시 시도한 후 새 탐색을 시작하세요."})
                            return
                    self.server.job = SearchJob(config, history=self.server.history)
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
            elif self.path == "/api/hashcash/history/save":
                with self.server.job_lock:
                    job = self.server.job
                    if job is None or payload.get("jobId") != job.snapshot()["id"]:
                        self.reply(409, {"error": "현재 탐색의 결과만 저장할 수 있습니다."})
                        return
                    state = job.snapshot()
                    if not state["finished"] or state["status"] != "found":
                        self.reply(409, {"error": "탐색과 검증이 끝난 후 저장할 수 있습니다."})
                        return
                    state = job.save_result()
                    if state["saveError"]:
                        self.reply(503, {"job": state, "error": state["saveError"]})
                    else:
                        self.reply(200, {"job": state})
            else:
                self.reply(404, {"error": "경로를 찾을 수 없습니다."})
        except (ValueError, TypeError, UnicodeError) as exc:
            self.reply(400, {"error": str(exc)})


def create_server(host: str = "127.0.0.1", port: int = 9186, instance: str | None = None, data_dir: str | None = None) -> LabServer:
    if host != "127.0.0.1":
        raise ValueError("서버는 이 컴퓨터의 127.0.0.1에서만 실행합니다.")
    return LabServer((host, port), instance, data_dir)


def main():
    parser = argparse.ArgumentParser(description="Standalone hashcash loopback server")
    parser.add_argument("--port", type=int, default=9186)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--data-dir", default=None, help="Directory for durable result history (or HASHCASH_DATA_DIR)")
    args = parser.parse_args()
    mp.freeze_support()
    server = create_server(port=args.port, instance=args.instance, data_dir=args.data_dir)
    print(f"Hashcash backend: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
