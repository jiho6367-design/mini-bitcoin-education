"""Real peer-room HTTP/WebSocket restart probe, without teacher credentials.

The private state file contains signed browser cookies and obsolete bearer tokens.
Use an isolated test installation and a private temporary directory. This probe
retains its shared room: disposal belongs to the test database/container owner.
"""

import argparse
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch
import uuid

from simple_websocket import ConnectionClosed

import persistence_probe as transport
from persistence_probe import PREFIX, ProbeWebSocket, ledger, write_private


class Browser(transport.Browser):
    def call(self, method, path, body=None, **kwargs):
        try:
            return super().call(method, path, body, **kwargs)
        except AssertionError:
            # Unexpected response bodies can contain credentials. Never print them.
            raise AssertionError(f"{method} {path}: unexpected HTTP response") from None


class ConnectedMember:
    def __init__(self, browser, credential):
        url = browser.base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        self.ws = ProbeWebSocket.connect(
            url + PREFIX + f"/classroom/rooms/{credential['code']}/stream",
            headers={"Origin": browser.base},
        )
        self.ws.send(json.dumps({"token": credential["token"]}))
        first = json.loads(self.ws.receive(timeout=5))
        assert first.get("type") == "snapshot", "WebSocket authentication did not return a snapshot"
        self.snapshot = first["state"]
        self.patch_seen = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.receive, daemon=True)
        self.thread.start()

    def receive(self):
        try:
            while not self.stopped.is_set():
                raw = self.ws.receive(timeout=.25)
                if raw is None:
                    continue
                frame = json.loads(raw)
                if frame.get("type") == "ping":
                    self.ws.send("pong")
                elif frame.get("type") == "patch":
                    self.patch_seen.set()
                elif frame.get("type") == "ended":
                    return
        except (ConnectionClosed, TimeoutError, OSError, ValueError):
            return

    def close(self):
        self.stopped.set()
        with suppress(ConnectionClosed, OSError):
            self.ws.close()
        self.thread.join(timeout=2)


def state(browser, who):
    return browser.call("GET", f"/classroom/rooms/{who['code']}", token=who["token"])


def request(who, action, payload=None, *, assignment=None):
    return {"runId": who["runId"], "commandId": uuid.uuid4().hex,
            "assignmentId": assignment, "action": action, "payload": payload or {}}


def command(browser, who, action, payload=None, **kwargs):
    return browser.call("POST", f"/classroom/rooms/{who['code']}/commands",
                        request(who, action, payload), token=who["token"], **kwargs)


def digest(lab):
    return hashlib.sha256(json.dumps(ledger(lab), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def drain(browser, who):
    current = state(browser, who)
    while current["lab"]["queue"]:
        command(browser, who, "deliver")
        current = state(browser, who)
    return current


def prepare(base):
    creator_browser, member_browser = Browser(base), Browser(base)
    creator_browser.call("GET", "/health")
    creator = creator_browser.call("POST", "/team/rooms", {
        "name": "Restart probe creator", "difficulty": 1,
    }, status=201)
    assert creator.get("teamMode") is True, "The room still requires a teacher"
    member = member_browser.call("POST", f"/classroom/rooms/{creator['code']}/join", {
        "name": "Restart probe member",
    }, status=201)
    sockets = []
    try:
        creator_socket = ConnectedMember(creator_browser, creator)
        sockets.append(creator_socket)
        member_socket = ConnectedMember(member_browser, member)
        sockets.append(member_socket)
        assert all(p["role"] != "teacher" for p in state(member_browser, member)["participants"])
        command(creator_browser, creator, "claim", {"team": "C"})
        creator_socket.close()
        # Exercise the complete old teacher-disconnect grace period on the real
        # runner, and prove its released team can be selected by the remaining peer.
        deadline = time.monotonic() + 20
        while True:
            current = state(member_browser, member)
            owner = next(p for p in current["participants"] if p["id"] == creator["participantId"])
            if not owner["connected"] and owner["team"] is None:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("The disconnected creator retained its team lease")
            time.sleep(.2)
        command(member_browser, member, "claim", {"team": "C"})
        command(member_browser, member, "release")
        command(member_browser, member, "claim", {"team": "A"})
        lab = state(member_browser, member)["lab"]
        draft = command(member_browser, member, "draft", {
            "senderAddress": lab["wallets"][0]["address"],
            "recipientAddress": lab["wallets"][1]["address"], "amount": 17, "fee": 1,
        })
        signed = command(member_browser, member, "sign", {"draftId": draft["signingHash"]})["signed"]
        assert command(member_browser, member, "submit", {"transactionId": signed["id"]})["validation"]["valid"]
        drain(member_browser, member)
        # Stay below the 10 commands/second member transport limit.
        time.sleep(1.05)
        command(member_browser, member, "prepare", {"mode": "fast", "guided": True})
        race = state(member_browser, member)["lab"]["race"]["id"]
        command(member_browser, member, "participate", {"raceId": race, "enabled": True})
        command(member_browser, member, "resume", {"raceId": race})
        deadline = time.monotonic() + 15
        while len(state(member_browser, member)["lab"]["nodes"]["A"]["chain"]) < 2:
            if time.monotonic() >= deadline:
                raise AssertionError("A peer could not mine after the creator disconnected")
            time.sleep(.05)
        time.sleep(1.05)
        current = drain(member_browser, member)
        alice, bob = current["lab"]["wallets"][:2]
        assert all(node["balances"][alice["address"]]["confirmed"] == 82
                   and node["balances"][bob["address"]]["confirmed"] == 117
                   for node in current["lab"]["nodes"].values()), "The signed transfer was not confirmed"
        member_browser.call("POST", f"/team/rooms/{member['code']}/save", {}, token=member["token"])
        assert member_socket.patch_seen.wait(3), "No live WebSocket screen updates were observed"
        old_assignment = next(p["assignmentId"] for p in current["participants"] if p["id"] == member["participantId"])
        return {"format_version": 1, "base_url": base.rstrip("/"), "code": member["code"],
                "practice_id": current["storage"]["id"], "ledger_sha256": digest(current["lab"]),
                "creator": creator, "member": member,
                "creator_cookies": creator_browser.cookies(), "member_cookies": member_browser.cookies(),
                "old_command": request(member, "prepare", assignment=old_assignment)}
    finally:
        for connection in sockets:
            connection.close()


def verify(saved):
    creator_browser = Browser(saved["base_url"], saved["creator_cookies"])
    member_browser = Browser(saved["base_url"], saved["member_cookies"])
    stranger = Browser(saved["base_url"])
    old_cookie = member_browser.owner()
    path = f"/classroom/rooms/{saved['code']}"
    rejected = stranger.call("POST", f"/team/rooms/{saved['code']}/reconnect", {}, status=404)
    assert rejected["error"]["code"] == "MEMBERSHIP_REQUIRED"
    old = member_browser.call("GET", path, token=saved["member"]["token"], status=401)
    assert old["error"]["code"] == "UNAUTHORIZED"
    member = member_browser.call("POST", f"/team/rooms/{saved['code']}/reconnect", {})
    creator = creator_browser.call("POST", f"/team/rooms/{saved['code']}/reconnect", {})
    assert member_browser.owner() == old_cookie, "The signed owner cookie changed across restart"
    for new, old in ((member, saved["member"]), (creator, saved["creator"])):
        assert new["teamMode"] and new["code"] == saved["code"]
        assert new["participantId"] == old["participantId"], "Saved browser membership was lost"
        assert new["runId"] != old["runId"] and new["token"] != old["token"], "Old live authority survived restart"
    current = state(member_browser, member)
    assert current["storage"]["id"] == saved["practice_id"], "A different practice replaced the shared link"
    assert digest(current["lab"]) == saved["ledger_sha256"], "The saved ledger changed across restart"
    assert current["lab"]["race"]["status"] != "running" and not current["automatic"]
    assert all(p["role"] == "observer" and p["team"] is None for p in current["participants"])
    rejected = member_browser.call("POST", path + "/commands", saved["old_command"], token=member["token"], status=409)
    assert rejected["error"]["code"] == "STALE_ROOM"
    member_browser.call("POST", path + "/commands", saved["old_command"], token=saved["member"]["token"], status=401)
    connection = ConnectedMember(member_browser, member)
    newcomer_connection = None
    try:
        command(member_browser, member, "claim", {"team": "B"})
        command(member_browser, member, "prepare", {"guided": True})
        command(member_browser, member, "pause", {"raceId": state(member_browser, member)["lab"]["race"]["id"]})
        newcomer = stranger.call("POST", path + "/join", {"name": "Restart probe newcomer"}, status=201)
        newcomer_connection = ConnectedMember(stranger, newcomer)
        command(stranger, newcomer, "claim", {"team": "A"})
        command(stranger, newcomer, "automatic", {"enabled": False})
        assert digest(state(stranger, newcomer)["lab"]) == saved["ledger_sha256"]
        assert connection.patch_seen.wait(3), "Restarted peer controls did not stream updates"
    finally:
        connection.close()
        if newcomer_connection:
            newcomer_connection.close()
    return {"stable_team_link_restored": True, "same_practice_and_ledger_restored": True,
            "owner_cookies_and_membership_retained": True, "fresh_live_authority": True,
            "old_token_and_command_rejected": True, "unknown_owner_reconnect_denied": True,
            "new_peer_join_and_controls_work": True, "creator_not_required": True,
            "http_and_websocket_exercised": True, "persistent_team_record_retained": True}


def local_restart(server):
    # Reuse process/container-independent host scaffolding, while isolating this
    # run from any configured hosted database or migration code in the environment.
    with patch.dict(os.environ, {"TEAM_DATABASE_URL": "", "TEAM_SHARED_ROOM_CODE": ""}), \
            patch.object(transport, "prepare", lambda base, _secret: prepare(base)), \
            patch.object(transport, "verify", lambda saved, _secret, **_kwargs: verify(saved)):
        return transport.local_restart(server)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("local", "prepare", "verify"))
    parser.add_argument("--base-url")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--server", choices=("flask", "gunicorn"), default="flask")
    args = parser.parse_args()
    if args.phase == "local":
        result = local_restart(args.server)
    else:
        if not args.state_file:
            parser.error("prepare/verify require --state-file")
        if args.phase == "prepare":
            if not args.base_url or args.state_file.exists():
                parser.error("prepare requires --base-url and a new private --state-file")
            write_private(args.state_file, prepare(args.base_url))
            result = {"prepared": True, "state_file_contains_private_cookies": True,
                      "next": "Replace the whole app process/container, then run verify with the same state file"}
        else:
            saved = json.loads(args.state_file.read_text(encoding="utf-8"))
            if args.base_url and args.base_url.rstrip("/") != saved["base_url"]:
                parser.error("The origin must match prepare")
            result = verify(saved)
    if args.result_file:
        write_private(args.result_file, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
