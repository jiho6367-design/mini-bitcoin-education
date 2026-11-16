"""Peer room authority and lifecycle regressions use fresh educational ledgers only."""

import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from education_engine.core.block import valid_pow, validate_chain
from education_engine.core.errors import LabError
from education_engine.service.classroom import Classroom


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Command:
    def __init__(self, room, person, action, payload=None, *, assignment=None):
        self.runId = room.run_id
        self.assignmentId = person.assignment_id if assignment is None else assignment
        self.commandId = uuid.uuid4().hex
        self.action = action
        self.payload = payload or {}

    def model_dump_json(self):
        return json.dumps(vars(self), sort_keys=True)


class TeamClassroomTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.room = Classroom("Creator", 1, self.clock, team_mode=True, code="TEAM0123")
        self.creator = self.room.teacher
        self.member = self.room.add("Member")
        self.room.connected(self.creator, 1)
        self.room.connected(self.member, 1)

    def send(self, person, action, payload=None, **kwargs):
        return self.room.command(person, Command(self.room, person, action, payload, **kwargs))

    def assert_error(self, code, person, action, payload=None, **kwargs):
        with self.assertRaises(LabError) as raised:
            self.send(person, action, payload, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def prepare(self, participant=None):
        self.send(participant or self.member, "prepare", {"guided": False, "mode": "learning"})
        return self.room.session.race

    def test_ordinary_creator_and_members_share_controls_without_teacher(self):
        self.assertTrue(self.room.snapshot()["teamMode"])
        self.assertEqual(self.creator.role, "observer")
        self.assertEqual(self.room.add("Not a teacher", teacher=True).role, "observer")
        race = self.prepare()
        self.send(self.member, "claim", {"team": "A"})
        self.send(self.member, "participate", {"raceId": race.id, "enabled": True})
        self.send(self.creator, "step", {"raceId": race.id})
        self.assertEqual(race.jobs["A"].attempts, 1)
        self.send(self.member, "configure", {"raceId": race.id, "guided": False})
        self.send(self.member, "resume", {"raceId": race.id})
        self.send(self.creator, "pause", {"raceId": race.id})
        self.send(self.member, "automatic", {"enabled": True})
        self.assertTrue(self.room.automatic)
        self.send(self.member, "stop", {"raceId": race.id})
        self.assertFalse(self.room.automatic)
        self.send(self.creator, "fork")
        self.assertNotEqual(self.room.session.race.id, race.id)

    def test_simultaneous_claims_have_exactly_one_representative(self):
        barrier = threading.Barrier(2)
        outcomes = []

        def claim(person):
            barrier.wait(timeout=5)
            try:
                self.send(person, "claim", {"team": "B"})
                outcomes.append("ok")
            except LabError as error:
                outcomes.append(error.code)

        threads = [threading.Thread(target=claim, args=(p,)) for p in (self.creator, self.member)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["ok", "TEAM_TAKEN"])
        self.assertEqual(sum(p.team == "B" for p in self.room.people.values()), 1)

    def test_failed_switch_keeps_own_assignment_and_retries_do_not_repeat_claim(self):
        command = Command(self.room, self.member, "claim", {"team": "A"})
        self.room.command(self.member, command)
        assignment, seq = self.member.assignment_id, self.room.seq
        self.room.command(self.member, command)
        self.assertEqual((self.member.assignment_id, self.room.seq), (assignment, seq))
        self.send(self.creator, "claim", {"team": "B"})
        self.assert_error("TEAM_TAKEN", self.member, "claim", {"team": "B"})
        self.assertEqual((self.member.team, self.member.assignment_id), ("A", assignment))
        self.assert_error("FORBIDDEN", self.member, "release", {"participantId": self.creator.id})
        self.assertEqual(self.creator.team, "B")

    def test_team_members_cannot_gain_destructive_authority_even_with_old_teacher_role(self):
        for role in ("observer", "teacher"):
            self.creator.role = role
            for action in ("assign", "remove", "close", "reset"):
                with self.subTest(role=role, action=action):
                    error = self.assert_error(
                        "FORBIDDEN", self.creator, action,
                        {"participantId": self.member.id, "team": "A"},
                    )
                    self.assertEqual(error.status, 403)
                    self.assertIn(self.member.id, self.room.people)
                    self.assertFalse(self.room.closed)
        self.send(self.creator, "leave")
        self.assertFalse(self.room.closed)
        self.prepare()

    def test_creator_disconnect_does_not_stop_other_members_real_mining(self):
        self.send(self.member, "claim", {"team": "A"})
        race = self.prepare()
        self.send(self.member, "participate", {"raceId": race.id, "enabled": True})
        self.send(self.member, "resume", {"raceId": race.id})
        self.room.connected(self.creator, -1)
        self.clock.now = 16
        for _ in range(1024):
            self.room.tick()
            if len(self.room.session.lab.nodes["A"].chain) > 1:
                break
        node = self.room.session.lab.nodes["A"]
        self.assertEqual(race.status, "running")
        self.assertGreater(len(node.chain), 1)
        self.assertTrue(valid_pow(node.chain[-1].hash, node.chain[-1].difficulty))
        self.assertTrue(validate_chain(node.chain, self.room.session.lab.genesis).valid)
        while self.room.session.lab.queue:
            self.send(self.member, "deliver")
        self.assertEqual(len({n.chain[-1].hash for n in self.room.session.lab.nodes.values()}), 1)

    def test_only_current_representative_can_sign_and_old_drafts_cannot_transfer(self):
        wallets = self.room.session.wallets
        self.assert_error("FORBIDDEN", self.member, "draft")
        self.send(self.creator, "claim", {"team": "A"})
        self.assert_error("FORBIDDEN", self.creator, "draft", {"senderAddress": wallets[1].address})
        draft = self.send(self.creator, "draft", {
            "senderAddress": wallets[0].address, "recipientAddress": wallets[1].address,
            "amount": 7, "fee": 1,
        })
        signed = self.send(self.creator, "sign", {"draftId": draft["signingHash"]})
        stale_assignment = self.creator.assignment_id
        stale_submit = Command(self.room, self.creator, "submit", {"transactionId": signed["signed"]["id"]})
        self.send(self.creator, "release")
        self.assertIsNone(self.creator.draft)
        self.assertIsNone(self.creator.signed)
        self.assertNotEqual(self.creator.assignment_id, stale_assignment)
        with self.assertRaises(LabError) as raised:
            self.room.command(self.creator, stale_submit)
        self.assertEqual(raised.exception.code, "STALE_ROLE")
        self.send(self.member, "claim", {"team": "A"})
        self.assert_error("NO_SIGNATURE", self.member, "submit", {"transactionId": signed["signed"]["id"]})
        self.assertFalse(self.room.session.lab.nodes["A"].mempool)

    def test_current_representative_can_submit_real_signed_transfer(self):
        self.send(self.member, "claim", {"team": "B"})
        wallets = self.room.session.wallets
        draft = self.send(self.member, "draft", {
            "senderAddress": wallets[1].address, "recipientAddress": wallets[2].address,
            "amount": 9, "fee": 1,
        })
        signed = self.send(self.member, "sign", {"draftId": draft["signingHash"]})
        result = self.send(self.member, "submit", {"transactionId": signed["signed"]["id"]})
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(len(self.room.session.lab.nodes["B"].mempool), 1)
        self.assertIsNone(self.member.signed)

    def test_offline_slot_reclaim_revokes_old_assignment_before_next_tick(self):
        self.send(self.creator, "claim", {"team": "C"})
        old_assignment = self.creator.assignment_id
        self.creator.draft, self.creator.signed = {"old": "draft"}, {"old": "signature"}
        self.room.connected(self.creator, -1)
        self.clock.now = 14.9
        self.assert_error("TEAM_TAKEN", self.member, "claim", {"team": "C"})
        self.clock.now = 15
        self.send(self.member, "claim", {"team": "C"})
        self.assertEqual(self.member.team, "C")
        self.assertIsNone(self.creator.team)
        self.assertIsNone(self.creator.draft)
        self.assertIsNone(self.creator.signed)
        self.assertFalse(self.creator.commands)
        self.assertNotEqual(self.creator.assignment_id, old_assignment)
        self.room.connected(self.creator, 1)
        self.assertEqual(self.creator.role, "observer")
        self.assert_error("STALE_ROLE", self.creator, "prepare", assignment=old_assignment)

    def test_reconnect_after_grace_cannot_keep_an_unexpired_by_tick_slot(self):
        self.send(self.creator, "claim", {"team": "A"})
        self.room.connected(self.creator, -1)
        self.clock.now = 15
        self.room.connected(self.creator, 1)
        self.assertIsNone(self.creator.team)
        self.assertEqual(self.creator.role, "observer")
        self.send(self.member, "claim", {"team": "A"})

    def test_empty_room_pauses_and_preserves_same_room_and_ledger_past_thirty_minutes(self):
        self.send(self.member, "claim", {"team": "A"})
        race = self.prepare()
        self.send(self.member, "participate", {"raceId": race.id, "enabled": True})
        self.send(self.member, "resume", {"raceId": race.id})
        self.send(self.member, "automatic", {"enabled": True})
        genesis = self.room.session.lab.genesis.hash
        self.room.connected(self.creator, -1)
        self.room.connected(self.member, -1)
        self.room.tick()
        self.assertEqual(race.status, "paused")
        self.assertFalse(self.room.automatic)
        self.clock.now = 3600
        self.room.tick()
        self.assertFalse(self.room.closed)
        self.assertEqual(self.room.code, "TEAM0123")
        self.assertEqual(self.room.session.lab.genesis.hash, genesis)
        self.assertIsNone(self.member.team)
        newcomer = self.room.add("Later member")
        self.room.connected(newcomer, 1)
        self.send(newcomer, "claim", {"team": "A"})
        self.prepare(newcomer)

    def test_failed_role_save_rolls_back_authority_without_a_teacher(self):
        self.send(self.member, "claim", {"team": "A"})
        assignment = self.member.assignment_id
        self.room.store = Mock()
        self.room.store.save.side_effect = LabError("SAVE_FAILED", "Unavailable", 503)
        self.assert_error("SAVE_FAILED", self.member, "release")
        current = self.room.people[self.member.id]
        self.assertEqual((current.team, current.assignment_id), ("A", assignment))
        self.assertEqual(current.role, "representative")
        self.assertEqual(self.room.storage_error, "Unavailable")

    def test_legacy_classroom_keeps_teacher_authority_by_default(self):
        legacy = Classroom("Teacher", 1, self.clock)
        student = legacy.add("Student")
        legacy.connected(legacy.teacher, 1)
        legacy.connected(student, 1)
        self.assertFalse(legacy.snapshot()["teamMode"])
        self.assertEqual(legacy.teacher.role, "teacher")
        with self.assertRaises(LabError) as raised:
            legacy.command(student, Command(legacy, student, "prepare"))
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        legacy.command(legacy.teacher, Command(legacy, legacy.teacher, "prepare"))
        legacy.command(legacy.teacher, Command(legacy, legacy.teacher, "leave"))
        self.assertTrue(legacy.closed)


if __name__ == "__main__":
    unittest.main()
