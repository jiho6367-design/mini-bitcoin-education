"""Real WebSocket upgrade and authority tests on an ephemeral local port."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask
from simple_websocket import Client
from werkzeug.serving import make_server
from education_api import PREFIX, register_education


class EducationWebSocketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, TEAM_TEACHER_SECRET="socket-teacher")
        self.runtime = register_education(self.app, str(Path(self.temp.name) / "socket.sqlite3"), start_runner=True)
        self.http = self.app.test_client()
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.sockets = []
        response = self.http.post(PREFIX + "/classroom/rooms", json={
            "secret": "socket-teacher", "name": "교사", "difficulty": 1,
        })
        self.assertEqual(response.status_code, 201)
        self.teacher = response.get_json()

    def tearDown(self):
        for socket in self.sockets:
            try:
                socket.close()
            except Exception:
                pass
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not any(person.connections for person in self.runtime.hub.room.people.values()):
                break
            time.sleep(0.01)
        self.runtime.stop_event.set()
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.runtime.close()
        self.temp.cleanup()

    def connect(self, credential, *, token=None, origin=None):
        url = f"ws://127.0.0.1:{self.server.server_port}{PREFIX}/classroom/rooms/{credential['code']}/stream"
        socket = Client.connect(url, headers={"Origin": origin} if origin else None)
        self.sockets.append(socket)
        socket.send(json.dumps({"token": credential["token"] if token is None else token}))
        raw = socket.receive(timeout=3)
        self.assertIsNotNone(raw)
        return socket, json.loads(raw)

    def command(self, credential, action, payload=None):
        response = self.http.post(PREFIX + f"/classroom/rooms/{credential['code']}/commands", json={
            "runId": credential["runId"], "commandId": str(uuid.uuid4()),
            "action": action, "payload": payload or {},
        }, headers={"Authorization": "Bearer " + credential["token"]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_authenticated_snapshot_patch_and_ended_follow_real_http_commands(self):
        teacher_ws, first = self.connect(self.teacher)
        self.assertEqual(first["type"], "snapshot")
        self.assertEqual(first["state"]["runId"], self.teacher["runId"])
        observer = self.http.post(PREFIX + f"/classroom/rooms/{self.teacher['code']}/join",
                                  json={"name": "대표 후보"}).get_json()
        observer_ws, observer_first = self.connect(observer)
        self.assertEqual(observer_first["type"], "snapshot")
        self.command(self.teacher, "assign", {"participantId": observer["participantId"], "team": "A"})
        previous = first["state"]["seq"]
        assigned = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            frame = json.loads(teacher_ws.receive(timeout=2))
            if frame["type"] == "ping":
                teacher_ws.send("pong")
                continue
            self.assertEqual(frame["type"], "patch")
            self.assertEqual(frame["baseSeq"], previous)
            self.assertGreater(frame["seq"], previous)
            previous = frame["seq"]
            assigned = any(person["id"] == observer["participantId"] and person["role"] == "representative"
                           for person in frame["patch"].get("participants", []))
            if assigned:
                break
        self.assertTrue(assigned)
        self.command(self.teacher, "close")
        for _ in range(8):
            raw = observer_ws.receive(timeout=2)
            self.assertIsNotNone(raw)
            frame = json.loads(raw)
            if frame["type"] == "ended":
                break
        self.assertEqual(frame["type"], "ended")

    def test_invalid_first_frame_and_cross_origin_never_attach_authority(self):
        _socket, frame = self.connect(self.teacher, token="invalid")
        self.assertEqual(frame["type"], "ended")
        self.assertEqual(self.runtime.hub.room.teacher.connections, 0)
        _socket, frame = self.connect(self.teacher, origin="https://other.example")
        self.assertEqual(frame["type"], "ended")
        self.assertEqual(self.runtime.hub.room.teacher.connections, 0)

    def test_disconnect_releases_socket_authority_and_reconnects_cleanly(self):
        socket, frame = self.connect(self.teacher)
        self.assertEqual(frame["type"], "snapshot")
        self.assertEqual(self.runtime.hub.room.teacher.connections, 1)
        socket.close()
        deadline = time.monotonic() + 2
        while self.runtime.hub.room.teacher.connections and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.runtime.hub.room.teacher.connections, 0)
        response = self.http.post(PREFIX + f"/classroom/rooms/{self.teacher['code']}/commands", json={
            "runId": self.teacher["runId"], "commandId": str(uuid.uuid4()), "action": "prepare", "payload": {},
        }, headers={"Authorization": "Bearer " + self.teacher["token"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "OFFLINE")
        _socket, frame = self.connect(self.teacher)
        self.assertEqual(frame["type"], "snapshot")
        self.command(self.teacher, "prepare")


if __name__ == "__main__":
    unittest.main()
