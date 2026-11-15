"""Hold a real classroom WebSocket while CI sends SIGTERM to the container.

Requires persistence_probe.py prepare's private state file. Neither credentials
nor that state are printed; this probe creates only a disposable observer.
"""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from simple_websocket import Client, ConnectionClosed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=12)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 60:
        parser.error("--timeout must be 1–60 seconds")
    code = json.loads(args.state_file.read_text(encoding="utf-8"))["classroom"]["old_code"]
    base = args.base_url.rstrip("/")
    request = Request(base + f"/api/education/classroom/rooms/{code}/join", method="POST",
                      data=json.dumps({"name": "종료 검사"}).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        person = json.load(response)
    parsed = urlsplit(base)
    socket_url = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc,
                            f"/api/education/classroom/rooms/{code}/stream", "", ""))
    socket = Client.connect(socket_url)
    try:
        socket.send(json.dumps({"token": person["token"]}))
        first = json.loads(socket.receive(timeout=5))
        if first.get("type") != "snapshot":
            raise RuntimeError("The shutdown observer did not authenticate")
        temporary = args.ready_file.with_suffix(args.ready_file.suffix + ".tmp")
        temporary.write_text('{"ready":true}', encoding="utf-8")
        temporary.replace(args.ready_file)
        started = time.monotonic()
        while time.monotonic() - started < args.timeout:
            try:
                frame = socket.receive(timeout=min(1, args.timeout - (time.monotonic() - started)))
            except ConnectionClosed:
                print(json.dumps({"closed": True, "seconds": round(time.monotonic() - started, 3)}))
                return
            if frame is not None and json.loads(frame).get("type") == "ping":
                socket.send("pong")
        raise RuntimeError("The live WebSocket did not close within the graceful shutdown budget")
    finally:
        try:
            socket.close()
        except ConnectionClosed:
            pass


if __name__ == "__main__":
    main()
