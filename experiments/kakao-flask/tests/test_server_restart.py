"""실제 HTTP 서버를 재시작해 쿠키·저장 데이터·브라우저 분리를 확인한다."""
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


class ServerRestart(unittest.TestCase):
    def test_real_process_restart_restores_only_the_same_browser_practice(self):
        app_dir = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            env = {**os.environ, "PORT": str(port),
                   "KAKAO_PRACTICE_DB": str(temp / "restart.sqlite3"), "PYTHONUTF8": "1"}
            browser = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            process = None

            def stop():
                nonlocal process
                if process is not None:
                    try:
                        process.stdin.write(b"stop\n")
                        process.stdin.flush()
                        process.wait(timeout=5)
                    except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            process.kill()
                        process.wait(timeout=5)
                    process.stdin.close()
                    process = None

            def start(log):
                nonlocal process
                # The Windows venv executable may be a redirector. A stdin
                # shutdown reaches the actual server process and closes SQLite.
                bootstrap = """
import os, sys, threading
from app import app
from werkzeug.serving import make_server
@app.after_request
def process_identity(response):
    response.headers['X-Test-Process'] = str(os.getpid())
    return response
server = make_server('127.0.0.1', int(os.environ['PORT']), app, threaded=True)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    sys.stdin.buffer.readline()
finally:
    server.shutdown()
    server.server_close()
    app.extensions['practice_store'].close()
"""
                process = subprocess.Popen(
                    [sys.executable, "-B", "-c", bootstrap], cwd=app_dir, env=env,
                    stdin=subprocess.PIPE, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail("테스트 서버가 준비 전에 종료됐습니다")
                    try:
                        with browser.open(base + "/api/health", timeout=1) as response:
                            if response.status == 200:
                                return int(response.headers["X-Test-Process"])
                    except (OSError, URLError):
                        time.sleep(.05)
                self.fail("테스트 서버가 제한 시간 안에 준비되지 않았습니다")

            def request(path, body=None, opener=browser):
                data = None if body is None else json.dumps(body).encode("utf-8")
                req = Request(base + path, data=data,
                              headers={"Content-Type": "application/json"})
                with opener.open(req, timeout=5) as response:
                    return json.load(response)

            try:
                with (temp / "server.log").open("wb") as log:
                    first_pid = start(log)
                    initial = request("/api/state")
                    created = request("/api/wallets", {"name": "Restart proof"})
                    saved_revision = created["revision"]
                    stop()
                    second_pid = start(log)
                    self.assertNotEqual(first_pid, second_pid)
                    restored = request("/api/state")
                    self.assertEqual(restored["practice"]["id"], initial["practice"]["id"])
                    self.assertEqual(restored["revision"], saved_revision)
                    saved = next(w for w in request("/api/wallets") if w["name"] == "Restart proof")
                    self.assertEqual(saved["address"], created["address"])
                    self.assertEqual(saved["pending_in"], 50)
                    self.assertNotIn("private_key", saved)
                    second_browser = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
                    other = request("/api/state", opener=second_browser)
                    self.assertNotEqual(other["practice"]["id"], restored["practice"]["id"])
                    self.assertFalse(any(w["name"] == "Restart proof"
                                         for w in request("/api/wallets", opener=second_browser)))
            finally:
                stop()


if __name__ == "__main__":
    unittest.main()
