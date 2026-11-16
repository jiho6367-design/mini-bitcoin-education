"""HTTP boundaries for stable shared links and cookie-based member renewal."""
from pathlib import Path
import sys
import tempfile
import unittest

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from education_api import OWNER_COOKIE, PREFIX, register_education


class TeamApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.runtimes = []
        self.app, self.runtime = self.make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        for runtime in self.runtimes:
            runtime.close()
        self.folder.cleanup()

    def make_app(self, enabled=True, **config):
        app = Flask(__name__)
        app.config.update(TESTING=True, TEAM_TEACHER_SECRET="admin-only", **config)
        runtime = register_education(app, str(Path(self.folder.name) / "team.sqlite3"),
                                     start_runner=False, classroom_enabled=enabled)
        self.runtimes.append(runtime)
        return app, runtime

    def start(self, client=None):
        response = (client or self.client).post(PREFIX + "/team/rooms", json={"name": "민지", "difficulty": 1})
        self.assertEqual(response.status_code, 201, response.text)
        result = response.get_json()
        self.assertTrue(result["teamMode"])
        return result

    def test_same_link_reconnects_after_restart_without_teacher(self):
        original = self.start()
        cookie = self.client.get_cookie(OWNER_COOKIE).value
        self.runtime.close()
        app, runtime = self.make_app()
        client = app.test_client()
        client.set_cookie(OWNER_COOKIE, cookie)
        response = client.post(PREFIX + f"/team/rooms/{original['code']}/reconnect", json={})
        self.assertEqual(response.status_code, 200, response.text)
        renewed = response.get_json()
        self.assertEqual(renewed["code"], original["code"])
        self.assertEqual(renewed["participantId"], original["participantId"])
        self.assertNotEqual(renewed["token"], original["token"])
        self.assertNotEqual(renewed["runId"], original["runId"])
        route = PREFIX + f"/classroom/rooms/{original['code']}"
        self.assertEqual(client.get(route, headers={"Authorization": "Bearer " + original["token"]}).status_code, 401)
        current = client.get(route, headers={"Authorization": "Bearer " + renewed["token"]}).get_json()
        self.assertTrue(current["teamMode"])
        self.assertTrue(all(person["role"] != "teacher" for person in current["participants"]))
        self.assertEqual(runtime.team_rooms.default_code(), original["code"])

    def test_signed_owner_cookie_required_for_reconnect_but_new_members_can_join(self):
        original = self.start()
        foreign = self.app.test_client()
        reconnect = PREFIX + f"/team/rooms/{original['code']}/reconnect"
        denied = foreign.post(reconnect, json={})
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.get_json()["error"]["code"], "MEMBERSHIP_REQUIRED")
        foreign.set_cookie(OWNER_COOKIE, self.client.get_cookie(OWNER_COOKIE).value[:-1] + "z")
        self.assertEqual(foreign.post(reconnect, json={}).status_code, 404)
        joined = foreign.post(PREFIX + f"/classroom/rooms/{original['code']}/join", json={"name": "준서"})
        self.assertEqual(joined.status_code, 201, joined.text)
        self.assertNotEqual(joined.get_json()["participantId"], original["participantId"])
        self.assertEqual(foreign.post(reconnect, json={}).get_json()["participantId"], joined.get_json()["participantId"])

    def test_existing_team_can_be_joined_at_archive_limit_and_does_not_reset(self):
        original = self.start()
        self.app.config["EDUCATION_MAX_PRACTICES"] = 0
        other = self.start(self.app.test_client())
        self.assertEqual(other["code"], original["code"])
        config = self.client.get(PREFIX + "/classroom/config").get_json()
        self.assertEqual(config["defaultTeamCode"], original["code"])

    def test_legacy_create_cannot_replace_dormant_shared_link(self):
        original = self.start()
        self.runtime.close()
        app, runtime = self.make_app()
        client = app.test_client()
        denied = client.post(PREFIX + "/classroom/rooms", json={"secret": "admin-only", "name": "관리자"})
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertEqual(runtime.team_rooms.default_code(), original["code"])
        joined = client.post(PREFIX + f"/classroom/rooms/{original['code']}/join", json={"name": "새 팀원"})
        self.assertEqual(joined.status_code, 201, joined.text)

    def test_team_members_can_save_without_global_archive_privileges(self):
        member = self.start()
        headers = {"Authorization": "Bearer " + member["token"]}
        response = self.client.post(PREFIX + f"/team/rooms/{member['code']}/save", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.get_json()["savedAt"])
        self.assertEqual(self.client.get(PREFIX + "/classroom/practices", headers=headers).status_code, 403)
        room = self.runtime.team_rooms.get(member["code"])
        admin = {"Authorization": "Bearer admin-only"}
        route = PREFIX + f"/classroom/practices/{room.practice_id}"
        for action in (lambda: self.client.delete(route, headers=admin),
                       lambda: self.client.post(route + "/resume", headers=admin, json={})):
            denied = action()
            self.assertEqual(denied.status_code, 409, denied.text)
            self.assertEqual(denied.get_json()["error"]["code"], "PRACTICE_LINKED")

    def test_disabled_classroom_and_cross_origin_requests_cannot_create_team(self):
        app, _runtime = self.make_app(enabled=False)
        self.assertEqual(app.test_client().post(PREFIX + "/team/rooms", json={"name": "팀원"}).status_code, 404)
        rejected = self.client.post(PREFIX + "/team/rooms", json={"name": "팀원"}, headers={"Origin": "https://foreign.invalid"})
        self.assertEqual(rejected.status_code, 403)
        self.assertIsNone(self.runtime.team_rooms.default_code())


if __name__ == "__main__":
    unittest.main()
