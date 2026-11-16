"""Optional loopback-only access to the original Python/C Hashcash engines.

Public hosting never starts native jobs. Each browser owns opaque, leased jobs;
neither this bridge nor the optional engines write to either learning ledger.
"""
from __future__ import annotations

import atexit
from dataclasses import dataclass
import importlib.util
import ipaddress
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

from flask import jsonify, request

PREFIX = "/api/hashcash-native"
OWNER_COOKIE = "team_hashcash_native_owner"
LEASE_SECONDS = 30
MAX_CONCURRENT = 4


@dataclass
class Entry:
    owner: str
    job: object
    touched: float


class NativeRegistry:
    def __init__(self, job_factory=None, *, clock=time.monotonic, start_reaper=False):
        self.factory = job_factory
        self.clock = clock
        self.lock = threading.RLock()
        self.entries: dict[str, Entry] = {}
        self.owners: dict[str, float] = {}
        self.closed = False
        self.done = threading.Event()
        self.reaper = None
        if start_reaper:
            self.reaper = threading.Thread(target=self._watch, daemon=True, name="native-hashcash-lease")
            self.reaper.start()

    def _watch(self):
        while not self.done.wait(1):
            self.reap()

    def reap(self):
        with self.lock:
            now = self.clock()
            for identifier, entry in list(self.entries.items()):
                if now - entry.touched < LEASE_SECONDS:
                    continue
                state = entry.job.snapshot()
                if state["finished"]:
                    del self.entries[identifier]
                else:
                    # Keep unjoined native jobs counted against the global bound.
                    entry.job.stop()
            occupied = {entry.owner for entry in self.entries.values()}
            for owner, touched in list(self.owners.items()):
                if owner not in occupied and now - touched > LEASE_SECONDS * 4:
                    del self.owners[owner]

    def owner(self, cookie, *, create=False):
        with self.lock:
            if cookie in self.owners:
                self.owners[cookie] = self.clock()
                return cookie
            if not create:
                return None
            self.reap()
            # A local page cannot create an unbounded idle token collection.
            if len(self.owners) >= 256:
                idle = sorted((stamp, token) for token, stamp in self.owners.items()
                              if not any(entry.owner == token for entry in self.entries.values()))
                if idle:
                    del self.owners[idle[0][1]]
            token = secrets.token_urlsafe(32)
            self.owners[token] = self.clock()
            return token

    def start(self, owner, config):
        from native_hashcash.hashcash_lab.search import SearchJob
        with self.lock:
            self.reap()
            if self.closed:
                raise RuntimeError("PC 연산 서버가 종료 중입니다")
            active = [entry for entry in self.entries.values() if not entry.job.snapshot()["finished"]]
            if any(entry.owner == owner for entry in active):
                raise BlockingIOError("이 브라우저의 PC 연산을 먼저 중지하거나 완료해 주세요")
            if len(active) >= MAX_CONCURRENT:
                raise BlockingIOError("PC 연산이 사용 중입니다 잠시 후 다시 시도해 주세요")
            job = (self.factory or SearchJob)(config)
            identifier = job.snapshot()["id"]
            self.entries[identifier] = Entry(owner, job, self.clock())
            try:
                job.start()
            except BaseException:
                self.entries.pop(identifier, None)
                job.stop()
                raise
            return self._snapshot(job)

    @staticmethod
    def _snapshot(job):
        result = job.snapshot()
        for key in ("reason", "error", "cleanupWarning"):
            if isinstance(result.get(key), str):
                result[key] = result[key].rstrip(".")
        if "No module named 'cupy'" in result.get("reason", "") or "No module named 'numpy'" in result.get("reason", ""):
            result["reason"] = "CUDA 실행 모듈이 없어 CPU로 계산합니다 GPU 의존성은 선택해서 설치할 수 있습니다"
        return result

    def get(self, owner, identifier, *, stop=False):
        with self.lock:
            self.reap()
            entry = self.entries.get(identifier)
            if entry is None or owner is None or entry.owner != owner:
                raise KeyError(identifier)
            entry.touched = self.clock()
            if stop:
                entry.job.stop()
            return self._snapshot(entry.job)

    def stop_owner(self, owner):
        with self.lock:
            count = 0
            for entry in self.entries.values():
                if owner is not None and entry.owner == owner:
                    entry.job.stop()
                    count += 1
            return count

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.done.set()
            jobs = [entry.job for entry in self.entries.values()]
            for job in jobs:
                job.stop()
        if self.reaper and self.reaper is not threading.current_thread():
            self.reaper.join(timeout=2)
        deadline = time.monotonic() + 2
        for job in jobs:
            thread = getattr(job, "thread", None)
            if thread and thread is not threading.current_thread():
                thread.join(timeout=max(0, deadline - time.monotonic()))


def register_native_hashcash(app, *, job_factory=None, clock=time.monotonic, start_reaper=True):
    enabled = app.config.get("TEAM_NATIVE_HASHCASH_ENABLED", os.environ.get("TEAM_NATIVE_HASHCASH") == "1") is True
    enabled = enabled and not os.environ.get("RENDER") and not os.environ.get("RENDER_SERVICE_ID")
    registry = NativeRegistry(job_factory, clock=clock, start_reaper=bool(enabled and start_reaper))
    app.extensions["native_hashcash"] = registry
    if enabled:
        atexit.register(registry.close)

    def local():
        if not enabled:
            return False
        try:
            original = request.environ.get("werkzeug.proxy_fix.orig", {}).get("REMOTE_ADDR", request.remote_addr)
            addresses = [ipaddress.ip_address(value or "") for value in (request.remote_addr, original)]
            loopback = all(address.is_loopback or bool(getattr(address, "ipv4_mapped", None)
                                                       and address.ipv4_mapped.is_loopback) for address in addresses)
            host = urlsplit(request.host_url).hostname
        except ValueError:
            return False
        return loopback and host in {"localhost", "127.0.0.1", "::1"}

    def origin_allowed():
        origin = request.headers.get("Origin")
        return not origin or origin.rstrip("/") == request.host_url.rstrip("/")

    def reply(body, status=200, owner=None):
        result = jsonify(body)
        result.status_code = status
        result.headers["Cache-Control"] = "no-store"
        if owner and request.cookies.get(OWNER_COOKIE) != owner:
            result.set_cookie(OWNER_COOKIE, owner, httponly=True, secure=request.is_secure,
                              samesite="Strict", path=PREFIX)
        return result

    def denied():
        return reply({"error": "PC 전용 연산은 로컬 팀 Flask에서만 사용할 수 있습니다"}, 404)

    @app.get(PREFIX + "/capabilities")
    def native_hashcash_capabilities():
        available = local() and origin_allowed()
        if not available:
            return reply({"available": False, "reason": "Python·C 모드는 PC에서 실행한 팀 Flask 전용입니다",
                          "cpuCount": 1, "python": True, "c": False, "cuda": False})
        from native_hashcash.hashcash_lab.native_scheduler_experiment import selected_dll
        try:
            cuda = importlib.util.find_spec("cupy") is not None
        except (ImportError, ValueError):
            cuda = False
        owner = registry.owner(request.cookies.get(OWNER_COOKIE), create=True)
        return reply({"available": True, "reason": "PC 전용 Python·C 연산을 사용할 수 있습니다 실제 장치는 탐색 시 확인합니다",
                      "cpuCount": min(4, max(1, os.cpu_count() or 1)), "python": True,
                      "c": selected_dll().is_file(), "cuda": cuda}, owner=owner)

    @app.post(PREFIX + "/start")
    def native_hashcash_start():
        if not local():
            return denied()
        if not origin_allowed():
            return reply({"error": "다른 출처의 PC 연산 요청은 허용하지 않습니다"}, 403)
        payload = request.get_json(silent=True) if request.is_json else None
        if not isinstance(payload, dict):
            return reply({"error": "JSON 객체로 탐색 조건을 전달하세요"}, 400)
        from native_hashcash.hashcash_lab.search import SearchConfig
        mode = payload.get("mode")
        if not isinstance(mode, str) or mode not in {"python-native", "c-native"}:
            return reply({"error": "Python·C PC 전용 모드를 선택하세요"}, 400)
        workers = payload.get("workers", 1)
        if type(workers) is not int or not 1 <= workers <= min(4, max(1, os.cpu_count() or 1)):
            return reply({"error": "CPU 작업 수는 이 PC의 1~4개 범위에서 선택하세요"}, 400)
        try:
            config = SearchConfig.from_payload({**payload, "workers": workers,
                                                "mode": "hybrid" if mode == "python-native" else "native"})
        except (ValueError, UnicodeError) as error:
            return reply({"error": str(error).rstrip(".")}, 400)
        owner = registry.owner(request.cookies.get(OWNER_COOKIE), create=True)
        try:
            state = registry.start(owner, config)
        except BlockingIOError as error:
            return reply({"error": str(error)}, 409, owner)
        except RuntimeError as error:
            return reply({"error": str(error)}, 503, owner)
        return reply({"job": state}, owner=owner)

    @app.get(PREFIX + "/status")
    def native_hashcash_status():
        if not local():
            return denied()
        if not origin_allowed():
            return reply({"error": "다른 출처의 PC 연산 조회는 허용하지 않습니다"}, 403)
        owner = registry.owner(request.cookies.get(OWNER_COOKIE))
        try:
            state = registry.get(owner, request.args.get("jobId", ""))
        except KeyError:
            return reply({"error": "이 브라우저의 PC 연산을 찾을 수 없습니다"}, 404)
        return reply({"job": state})

    @app.post(PREFIX + "/stop")
    def native_hashcash_stop():
        if not local():
            return denied()
        if not origin_allowed():
            return reply({"error": "다른 출처의 PC 연산 중지는 허용하지 않습니다"}, 403)
        payload = request.get_json(silent=True) if request.is_json else None
        if not isinstance(payload, dict):
            return reply({"error": "JSON 객체로 중지할 PC 연산을 전달하세요"}, 400)
        owner = registry.owner(request.cookies.get(OWNER_COOKIE))
        if payload.get("all") is True:
            return reply({"stopped": registry.stop_owner(owner)})
        try:
            state = registry.get(owner, payload.get("jobId", ""), stop=True)
        except (KeyError, TypeError):
            return reply({"error": "이 브라우저의 PC 연산을 찾을 수 없습니다"}, 404)
        return reply({"job": state})

    return registry
