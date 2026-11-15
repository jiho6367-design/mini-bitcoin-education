"""Real restart probe; prepare/verify also work across a remote host restart.

The state file contains an owner cookie. Keep it in ignored work/ or a private
temporary directory. Teacher secrets are read from the environment, never saved.
"""

import argparse
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener
import uuid

from simple_websocket import Client

PREFIX = "/api/education"
OWNER_COOKIE = "team_education_owner"


class ProbeWebSocket(Client):
    """Keep the URL port in Host; simple-websocket 1.1.0 drops it by default."""

    def __init__(self, url, *args, **kwargs):
        self.authority = urlsplit(url).netloc
        super().__init__(url, *args, **kwargs)

    def handshake(self):
        hostname = self.host
        self.host = self.authority
        try:
            return super().handshake()
        finally:
            self.host = hostname


class Browser:
    def __init__(self, base, cookies=()):
        parsed = urlsplit(base)
        if (parsed.scheme not in ("http", "https") or not parsed.netloc
                or parsed.path not in ("", "/") or parsed.username is not None
                or parsed.query or parsed.fragment):
            raise ValueError("Use the site's http(s) origin without a path")
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        for cookie in cookies:
            self.jar.set_cookie(http.cookiejar.Cookie(**cookie))
        self.opener = build_opener(HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None, *, token=None, status=200):
        headers = {"Origin": self.base}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        api_path = path if path.startswith("/api/") else PREFIX + path
        request = Request(self.base + api_path, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            value = json.loads(content) if content else None
            if response.status != status:
                raise AssertionError(f"{method} {path}: expected {status}, got {response.status}: {value}")
            return value

    def cookies(self):
        fields = ("version", "name", "value", "port", "port_specified", "domain",
                  "domain_specified", "domain_initial_dot", "path", "path_specified",
                  "secure", "expires", "discard", "comment", "comment_url", "rfc2109")
        return [{**{key: getattr(cookie, key) for key in fields}, "rest": dict(cookie._rest)}
                for cookie in self.jar]

    def owner(self):
        return next(cookie.value for cookie in self.jar if cookie.name == OWNER_COOKIE)


def ledger(lab):
    return {"sessionId": lab["sessionId"], "wallets": lab["wallets"],
            "nodes": {node: {key: data[key] for key in ("chain", "utxos", "mempool", "balances", "chainWork")}
                      for node, data in lab["nodes"].items()}}


def drain(browser, lab):
    while lab["queue"]:
        lab = browser.call("POST", f"/labs/{lab['labId']}/messages/deliver-next")["lab"]
    return lab


def basic_practice(browser):
    initial = browser.call("GET", "/api/state")
    name = "Restart probe " + uuid.uuid4().hex[:12]
    created = browser.call("POST", "/api/wallets", {"name": name})
    wallet = next(item for item in browser.call("GET", "/api/wallets")
                  if item["address"] == created["address"])
    assert wallet["balance"] == 0 and wallet["pending_in"] == 50
    return {"practice_id": initial["practice"]["id"], "revision": created["revision"],
            "name": name, "address": wallet["address"], "public_key": wallet["public_key"],
            "balance": 0, "pending_in": 50}


def personal_transfer(browser):
    lab = browser.call("POST", "/labs", {"mode": "example", "difficulty": 1}, status=201)["lab"]
    base = f"/labs/{lab['labId']}"
    alice, bob = lab["wallets"]
    draft = browser.call("POST", base + "/nodes/A/drafts", {
        "senderAddress": alice["address"], "recipientAddress": bob["address"], "amount": 30, "fee": 1,
    })
    signed = browser.call("POST", base + "/wallets/sign", {"draft": draft["draft"]})["signed"]
    submitted = browser.call("POST", base + "/nodes/A/transactions", {"transaction": signed})
    assert submitted["validation"]["valid"]
    drain(browser, submitted["lab"])
    result = browser.call("POST", base + "/mining", {"nodeId": "A", "mode": "fast"})
    for _ in range(100):
        if result["mining"]["status"] == "found":
            break
        result = browser.call("POST", base + f"/mining/{result['mining']['jobId']}/tick")
    assert result["mining"]["status"] == "found", "Difficulty-one mining did not finish"
    lab = drain(browser, result["lab"])
    assert all(node["balances"][alice["address"]]["confirmed"] == 69
               and node["balances"][bob["address"]]["confirmed"] == 30 for node in lab["nodes"].values())
    key = browser.call("GET", base + f"/wallets/{alice['address']}/private-key")["privateKey"]
    practice = lab["storage"]["id"]
    title = "Restart probe " + uuid.uuid4().hex[:12]
    browser.call("POST", f"/practices/{practice}/rename", {"title": title})
    browser.call("POST", f"/practices/{practice}/save")
    return {"practice_id": practice, "old_lab_id": lab["labId"], "title": title,
            "ledger": ledger(lab), "key_sha256": hashlib.sha256(key.encode()).hexdigest()}


def classroom_transfer(browser, secret):
    teacher = browser.call("POST", "/classroom/rooms", {
        "secret": secret, "name": "Restart probe teacher", "difficulty": 1,
    }, status=201)
    path = f"/classroom/rooms/{teacher['code']}"
    student = browser.call("POST", path + "/join", {"name": "Restart probe representative"}, status=201)
    sockets = []

    def command(who, action, payload=None):
        return browser.call("POST", path + "/commands", {
            "runId": who["runId"], "commandId": uuid.uuid4().hex,
            "action": action, "payload": payload or {},
        }, token=who["token"])

    def current():
        return browser.call("GET", path, token=teacher["token"])

    def deliver():
        state = current()
        while state["lab"]["queue"]:
            command(teacher, "deliver")
            state = current()
        return state

    state = current()
    try:
        for who in (teacher, student):
            url = browser.base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
            ws = ProbeWebSocket.connect(url + PREFIX + path + "/stream", headers={"Origin": browser.base})
            sockets.append(ws)
            ws.send(json.dumps({"token": who["token"]}))
            first = json.loads(ws.receive(timeout=5))
            assert first["type"] == "snapshot", first
        command(teacher, "assign", {"participantId": student["participantId"], "team": "A"})
        lab = current()["lab"]
        draft = command(student, "draft", {
            "senderAddress": lab["wallets"][0]["address"], "recipientAddress": lab["wallets"][1]["address"],
            "amount": 30, "fee": 1,
        })
        signed = command(student, "sign", {"draftId": draft["signingHash"]})["signed"]
        assert command(student, "submit", {"transactionId": signed["id"]})["validation"]["valid"]
        deliver()
        command(teacher, "prepare", {"mode": "fast", "guided": True})
        race_id = current()["lab"]["race"]["id"]
        command(student, "participate", {"raceId": race_id, "enabled": True})
        command(teacher, "resume", {"raceId": race_id})
        deadline = time.monotonic() + 15
        while len(current()["lab"]["nodes"]["A"]["chain"]) < 2:
            if time.monotonic() > deadline:
                raise AssertionError("Classroom runner did not mine a block")
            time.sleep(.05)
        # Keep this probe below the command transport's per-participant rate bound.
        time.sleep(1.05)
        state = deliver()
        alice, bob = state["lab"]["wallets"][:2]
        assert all(node["balances"][alice["address"]]["confirmed"] == 69
                   and node["balances"][bob["address"]]["confirmed"] == 130
                   for node in state["lab"]["nodes"].values())
    except Exception:
        # This room was created by this probe. Never close an unrelated live room.
        with suppress(Exception):
            time.sleep(1.05)
            command(teacher, "leave")
            browser.call("DELETE", f"/classroom/practices/{state['storage']['id']}", token=secret)
        raise
    finally:
        for ws in sockets:
            with suppress(Exception):
                ws.close()
    browser.call("POST", f"/classroom/practices/{state['storage']['id']}/save", token=secret)
    return {"practice_id": state["storage"]["id"], "old_code": teacher["code"],
            "old_run_id": teacher["runId"], "ledger": ledger(state["lab"])}


def prepare(base, secret):
    browser = Browser(base)
    browser.call("GET", "/health")
    basic = basic_practice(browser)
    personal = personal_transfer(browser)
    try:
        classroom = classroom_transfer(browser, secret)
    except Exception:
        with suppress(Exception):
            browser.call("DELETE", f"/labs/{personal['old_lab_id']}", status=204)
            browser.call("DELETE", f"/practices/{personal['practice_id']}")
        raise
    return {"format_version": 1, "base_url": browser.base,
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "cookies": browser.cookies(), "basic": basic,
            "personal": personal, "classroom": classroom}


def verify(state, secret, *, cleanup=False):
    browser = Browser(state["base_url"], state["cookies"])
    old_cookie = browser.owner()
    basic = state["basic"]
    basic_state = browser.call("GET", "/api/state")
    assert basic_state["practice"]["id"] == basic["practice_id"]
    assert basic_state["revision"] == basic["revision"]
    saved_wallet = next(item for item in browser.call("GET", "/api/wallets")
                        if item["address"] == basic["address"])
    for field in ("name", "public_key", "balance", "pending_in"):
        assert saved_wallet[field] == basic[field], ("Basic wallet changed", field)
    personal, classroom = state["personal"], state["classroom"]
    listing = browser.call("GET", "/practices")
    assert any(item["id"] == personal["practice_id"] and item["title"] == personal["title"]
               for item in listing["practices"])
    assert browser.owner() == old_cookie, "Persistent owner signing key or cookie changed"
    stranger = Browser(state["base_url"])
    stranger.call("GET", f"/practices/{personal['practice_id']}", status=404)
    assert stranger.call("GET", "/practices")["total"] == 0
    resumed = browser.call("POST", f"/practices/{personal['practice_id']}/resume", {})["lab"]
    assert resumed["labId"] != personal["old_lab_id"]
    assert ledger(resumed) == personal["ledger"], "Personal ledger changed across restart"
    address = resumed["wallets"][0]["address"]
    key = browser.call("GET", f"/labs/{resumed['labId']}/wallets/{address}/private-key")["privateKey"]
    assert hashlib.sha256(key.encode()).hexdigest() == personal["key_sha256"]
    stranger.call("GET", f"/labs/{resumed['labId']}/wallets/{address}/private-key", status=404)
    old = browser.call("GET", f"/classroom/rooms/{classroom['old_code']}", status=410)
    assert old["error"]["code"] == "ROOM_GONE", "Old classroom authority survived restart"
    saved = browser.call("GET", "/classroom/practices", token=secret)
    assert any(item["id"] == classroom["practice_id"] for item in saved["practices"])
    restored = browser.call("POST", f"/classroom/practices/{classroom['practice_id']}/resume",
                            {"name": "Restored probe teacher"}, token=secret)
    assert restored["runId"] != classroom["old_run_id"]
    room = browser.call("GET", f"/classroom/rooms/{restored['code']}", token=restored["token"])
    assert ledger(room["lab"]) == classroom["ledger"], "Classroom ledger changed across restart"
    assert len(room["participants"]) == 1 and room["participants"][0]["role"] == "teacher"
    assert room["lab"]["race"]["status"] != "running"
    result = {"basic_practice_restored": True, "owner_cookie_retained": True,
              "personal_archive_and_key_restored": True,
              "other_browser_denied": True, "classroom_archive_restored": True,
              "classroom_new_authority_only": True, "http_and_websocket_exercised": True,
              "personal_balances": [69, 30], "classroom_balances": [69, 130, 100]}
    if cleanup:
        browser.call("DELETE", f"/labs/{resumed['labId']}", status=204)
        browser.call("DELETE", f"/practices/{personal['practice_id']}")
        browser.call("POST", f"/classroom/rooms/{restored['code']}/commands", {
            "runId": restored["runId"], "commandId": uuid.uuid4().hex, "action": "leave", "payload": {},
        }, token=restored["token"])
        browser.call("DELETE", f"/classroom/practices/{classroom['practice_id']}", token=secret)
        result["education_probe_records_cleaned"] = True
        # The basic engine intentionally has no archive-delete API.
        result["basic_probe_record_retained"] = True
    return result


BOOTSTRAP = """
import os,sys,threading
from app import app
from werkzeug.serving import make_server
@app.after_request
def process_identity(response):
    response.headers['X-Test-Process']=str(os.getpid())
    return response
server=make_server('127.0.0.1',int(os.environ['PORT']),app,threaded=True)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
    sys.stdin.buffer.readline()
finally:
    server.shutdown();server.server_close()
    app.extensions['education'].close();app.extensions['practice_store'].close()
"""


def local_restart(server="flask"):
    app_dir = Path(__file__).resolve().parents[1]
    if server == "gunicorn" and os.name == "nt":
        raise RuntimeError("Gunicorn validation requires Linux or another supported POSIX host")
    with tempfile.TemporaryDirectory(prefix="team-restart-probe-") as temporary:
        folder = Path(temporary)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        secret = secrets.token_urlsafe(32)
        env = {**os.environ, "PORT": str(port), "TEAM_TEACHER_SECRET": secret,
               "KAKAO_PRACTICE_DB": str(folder / "basic.sqlite3"),
               "TEAM_EDUCATION_DB": str(folder / "education.sqlite3"),
               "TEAM_EDUCATION_RUNNER": "1", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        process = None

        def stop():
            nonlocal process
            if process is None:
                return
            try:
                if server == "flask":
                    process.stdin.write(b"stop\n"); process.stdin.flush()
                else:
                    process.terminate()
                process.wait(timeout=10)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            finally:
                if process.stdin:
                    process.stdin.close()
                process = None
            with suppress(OSError, URLError):
                with build_opener().open(base + PREFIX + "/health", timeout=.3):
                    raise AssertionError("The previous server is still listening after process exit")

        def start(log):
            nonlocal process
            command = ([sys.executable, "-B", "-c", BOOTSTRAP] if server == "flask" else
                       [sys.executable, "-B", "-m", "gunicorn", "--config", "gunicorn.conf.py", "app:app"])
            process = subprocess.Popen(command, cwd=app_dir, env=env, stdin=subprocess.PIPE,
                                       stdout=log, stderr=log, start_new_session=os.name != "nt",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError("Probe server exited before readiness")
                try:
                    with build_opener().open(base + PREFIX + "/health", timeout=.5) as response:
                        return int(response.headers.get("X-Test-Process", process.pid))
                except (OSError, URLError):
                    time.sleep(.05)
            raise AssertionError("Probe server readiness timed out")

        try:
            with (folder / "server.log").open("wb") as log:
                first = start(log)
                state = prepare(base, secret)
                state_path = folder / "probe-state.json"
                write_private(state_path, state)
                stop()
                second = start(log)
                assert first != second, "Server process identity did not change"
                result = verify(json.loads(state_path.read_text(encoding="utf-8")), secret, cleanup=True)
                stop()
                result.update(server=server, complete_process_restart=True,
                              before_pid=first, after_pid=second, temporary_databases=True,
                              state_file_roundtrip=True,
                              production_gunicorn_verified=server == "gunicorn")
                return result
        except Exception as error:
            log_text = (folder / "server.log").read_text(encoding="utf-8", errors="replace")
            raise AssertionError(f"{error}\nProbe server log tail:\n{log_text[-6000:]}") from error
        finally:
            stop()


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with suppress(OSError):
        path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("local", "prepare", "verify"))
    parser.add_argument("--base-url")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--server", choices=("flask", "gunicorn"), default="flask")
    parser.add_argument("--cleanup", action="store_true", help="After verification remove only this probe's records")
    args = parser.parse_args()
    if args.phase == "local":
        result = local_restart(args.server)
    else:
        secret = os.environ.get("TEAM_VERIFY_TEACHER_SECRET") or os.environ.get("TEAM_TEACHER_SECRET")
        if not secret or not args.state_file:
            parser.error("prepare/verify require --state-file and TEAM_VERIFY_TEACHER_SECRET")
        if args.phase == "prepare":
            if not args.base_url:
                parser.error("prepare requires --base-url")
            if args.state_file.exists():
                parser.error("Refusing to overwrite a probe state file")
            state = prepare(args.base_url, secret)
            write_private(args.state_file, state)
            result = {"prepared": True, "state_file_contains_private_cookie": True,
                      "next": "Restart the host completely, then run verify with the same state file"}
        else:
            state = json.loads(args.state_file.read_text(encoding="utf-8"))
            if args.base_url and args.base_url.rstrip("/") != state["base_url"]:
                parser.error("The origin must match the one used during prepare")
            result = verify(state, secret, cleanup=args.cleanup)
    if args.result_file:
        write_private(args.result_file, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
