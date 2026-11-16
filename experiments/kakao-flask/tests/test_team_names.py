"""Explicit names must survive rejoining without changing membership or ledger."""

import copy
from pathlib import Path
import sys
import tempfile
import unittest

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from education_api import OWNER_COOKIE, PREFIX, register_education


class TeamNameTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.runtimes = []
        self.app, self.runtime = self.make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        for runtime in self.runtimes:
            runtime.close()
        self.folder.cleanup()

    def make_app(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, TEAM_TEACHER_SECRET="names-test")
        runtime = register_education(app, str(Path(self.folder.name) / "names.sqlite3"),
                                     start_runner=False)
        self.runtimes.append(runtime)
        return app, runtime

    def start(self, name="팀원", client=None):
        response = (client or self.client).post(PREFIX + "/team/rooms", json={"name": name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.get_json()

    def profile(self, who, name, client=None):
        return (client or self.client).post(PREFIX + f"/team/rooms/{who['code']}/profile",
            json={"name": name}, headers={"Authorization": "Bearer " + who["token"]})

    def member(self, who):
        room = self.runtime.team_rooms.get(who["code"])
        return room, room.people[who["participantId"]]

    def saved_name(self, code):
        return self.runtime.store.db.execute(
            "SELECT name FROM team_members WHERE code=?", (code,)).fetchone()["name"]

    def test_explicit_rejoin_uses_new_name_keeps_identity_role_and_ledger(self):
        who = self.start()
        room, person = self.member(who)
        person.role, person.team, person.connections = "representative", "A", 1
        person.draft = {"sentinel": "pending-local-draft"}
        previous = copy.deepcopy(room.snapshot()["lab"])
        assignment = person.assignment_id
        response = self.client.post(PREFIX + f"/classroom/rooms/{who['code']}/join",
                                    json={"name": "  지호  "})
        self.assertEqual(response.status_code, 201, response.text)
        joined = response.get_json()
        self.assertEqual(joined, who)
        self.assertIs(room.people[person.id], person)
        self.assertEqual(person.name, "지호")
        self.assertEqual(self.saved_name(room.code), "지호")
        self.assertEqual((person.role, person.team, person.connections), ("representative", "A", 1))
        self.assertEqual(person.assignment_id, assignment)
        self.assertEqual(person.draft, {"sentinel": "pending-local-draft"})
        self.assertEqual(len(room.people), 1)
        self.assertEqual(room.snapshot()["lab"], previous)
        self.assertEqual(room.published["participants"][0]["name"], "지호")
        # Ordinary default-room start is also an explicit entry, not a reconnect.
        self.assertEqual(self.start("예원"), who)
        self.assertEqual(person.name, "예원")

    def test_profile_changes_only_own_name_and_reconnect_retains_it(self):
        who = self.start("처음 이름")
        other = self.start("다른 팀원", self.app.test_client())
        room, person = self.member(who)
        before = copy.deepcopy(room.snapshot()["lab"])
        response = self.profile(who, "바꾼 별명")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.get_json(), {"name": "바꾼 별명"})
        self.assertEqual(person.name, "바꾼 별명")
        self.assertEqual(room.people[other["participantId"]].name, "다른 팀원")
        self.assertEqual(room.snapshot()["lab"], before)
        reconnected = self.client.post(PREFIX + f"/team/rooms/{who['code']}/reconnect", json={})
        self.assertEqual(reconnected.get_json(), who)
        self.assertEqual(person.name, "바꾼 별명")
        self.assertEqual(len(room.people), 2)

    def test_restart_reconnect_and_new_explicit_name_share_the_same_member(self):
        who = self.start()
        self.assertEqual(self.profile(who, "저장한 별명").status_code, 200)
        room, _ = self.member(who)
        previous = copy.deepcopy(room.snapshot()["lab"])
        cookie = self.client.get_cookie(OWNER_COOKIE).value
        self.runtime.close()
        self.app, self.runtime = self.make_app()
        self.client = self.app.test_client()
        self.client.set_cookie(OWNER_COOKIE, cookie)
        renewed = self.client.post(PREFIX + f"/team/rooms/{who['code']}/reconnect", json={})
        self.assertEqual(renewed.status_code, 200, renewed.text)
        current = renewed.get_json()
        restored, person = self.member(current)
        self.assertEqual(person.name, "저장한 별명")
        self.assertEqual(person.id, who["participantId"])
        self.assertNotEqual(person.token, who["token"])
        self.assertEqual(restored.snapshot()["lab"]["nodes"], previous["nodes"])
        self.assertEqual(self.start("다시 입력한 이름"), current)
        self.assertEqual(person.name, "다시 입력한 이름")
        self.assertEqual(self.saved_name(current["code"]), "다시 입력한 이름")

    def test_explicit_rejoin_after_restart_uses_input_instead_of_saved_name(self):
        who = self.start("예전 이름")
        cookie = self.client.get_cookie(OWNER_COOKIE).value
        self.runtime.close()
        self.app, self.runtime = self.make_app()
        self.client = self.app.test_client()
        self.client.set_cookie(OWNER_COOKIE, cookie)
        renewed = self.start("새 이름")
        room, person = self.member(renewed)
        self.assertEqual(person.name, "새 이름")
        self.assertEqual(person.id, who["participantId"])
        self.assertEqual(self.saved_name(room.code), "새 이름")

    def test_profile_requires_both_member_token_and_matching_browser_owner(self):
        who = self.start("원래 이름")
        other_browser = self.app.test_client()
        other = self.start("다른 사람", other_browser)
        self.assertEqual(self.profile(who, "도용", other_browser).status_code, 403)
        self.assertEqual(self.profile(other, "도용").status_code, 403)
        wrong_token = {**who, "token": "invalid"}
        self.assertEqual(self.profile(wrong_token, "도용").status_code, 401)
        self.assertEqual(self.member(who)[1].name, "원래 이름")
        self.assertEqual(self.member(other)[1].name, "다른 사람")

    def test_invalid_profile_names_are_rejected_without_changes(self):
        who = self.start("유효한 이름")
        for name in ("", "   ", "a" * 31, None, 3):
            with self.subTest(name=name):
                self.assertEqual(self.profile(who, name).status_code, 400)
        self.assertEqual(self.member(who)[1].name, "유효한 이름")
        self.assertEqual(self.saved_name(who["code"]), "유효한 이름")

    def test_failed_profile_or_rejoin_rolls_back_live_name_storage_and_activity(self):
        who = self.start("원래 이름")
        room, person = self.member(who)
        before = copy.deepcopy(room.snapshot())
        saved = self.runtime.store.read(room.practice_id, "classroom")
        self.runtime.store.db.execute("""CREATE TRIGGER reject_name_checkpoint BEFORE UPDATE ON practices
            BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        for action in (
            lambda: self.profile(who, "실패한 별명"),
            lambda: self.client.post(PREFIX + f"/classroom/rooms/{who['code']}/join", json={"name": "실패한 입장"}),
        ):
            response = action()
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.get_json()["error"]["code"], "SAVE_FAILED")
            self.assertIs(room.people[person.id], person)
            self.assertEqual(person.name, "원래 이름")
            self.assertEqual(room.snapshot(), before)
            self.assertEqual(self.saved_name(room.code), "원래 이름")
            self.assertEqual(self.runtime.store.read(room.practice_id, "classroom"), saved)
        self.runtime.store.db.execute("DROP TRIGGER reject_name_checkpoint")
        self.assertEqual(self.profile(who, "정상 변경").status_code, 200)
        self.assertEqual(person.name, "정상 변경")


if __name__ == "__main__":
    unittest.main()
