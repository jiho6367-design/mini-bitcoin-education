"""Shared single-node mining uses real hashes and keeps competing nodes idle."""

import copy
import json
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from education_engine.core.block import valid_pow, validate_chain
from education_engine.core.errors import LabError
from education_engine.service.checkpoint import capture, restore
from education_engine.service.classroom import Classroom
from education_engine.service.session import LabSession


class Command:
    def __init__(self, room, participant, action, payload):
        self.runId = room.run_id
        self.assignmentId = participant.assignment_id
        self.commandId = uuid.uuid4().hex
        self.action = action
        self.payload = payload

    def model_dump_json(self):
        return json.dumps(vars(self), sort_keys=True)


class ClassroomSingleMiningTests(unittest.TestCase):
    def setUp(self):
        self.room = Classroom("팀원", 1, team_mode=True)
        self.participant = self.room.teacher
        self.room.connected(self.participant, 1)

    def send(self, action, payload=None, *, room=None, participant=None):
        room = room or self.room
        participant = participant or self.participant
        return room.command(participant, Command(room, participant, action, payload or {}))

    def prepare(self, node="B", **options):
        self.send("prepare", {"singleNode": node, "guided": True, **options})
        return self.room.session.race

    def assert_error(self, code, action, payload, **kwargs):
        with self.assertRaises(LabError) as raised:
            self.send(action, payload, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def test_observer_can_select_one_node_and_only_that_node_computes(self):
        self.assertEqual(self.participant.role, "observer")
        self.assertIsNone(self.participant.team)
        race = self.prepare("B")
        self.assertEqual(race.wire()["singleNode"], "B")
        self.assertEqual(race.enabled, {"A": False, "B": True, "C": False})
        self.send("step", {"raceId": race.id})
        self.assertEqual(race.jobs["B"].attempts, 1)
        self.assertEqual(race.jobs["A"].attempts, 0)
        self.assertEqual(race.jobs["C"].attempts, 0)
        sample = race.jobs["B"].samples[-1]
        self.assertEqual(sample.nonce, 0)
        self.assertEqual(len(sample.hash), 64)
        self.assertEqual(race.status, "paused")

    def test_selected_node_finds_real_block_and_other_nodes_validate_delivery(self):
        race = self.prepare("C")
        lab = self.room.session.lab
        for _ in range(10000):
            self.send("step", {"raceId": race.id})
            if race.completed:
                break
        else:
            self.fail("실제 난이도 1 작업 증명을 찾지 못했습니다")
        block = lab.nodes["C"].chain[-1]
        self.assertTrue(valid_pow(block.hash, block.difficulty))
        self.assertEqual(block.height, 1)
        self.assertEqual(race.completed[-1]["node"], "C")
        self.assertEqual(len(lab.nodes["A"].chain), 1)
        self.assertEqual(len(lab.nodes["B"].chain), 1)
        self.assertEqual(race.jobs["A"].attempts, 0)
        self.assertEqual(race.jobs["B"].attempts, 0)
        while lab.queue:
            self.send("deliver")
        for node in lab.nodes.values():
            self.assertEqual(node.chain[-1].hash, block.hash)
            self.assertTrue(validate_chain(node.chain, lab.genesis).valid)

    def test_single_scope_blocks_team_participation_until_competition_is_prepared(self):
        self.send("claim", {"team": "A"})
        race = self.prepare("B")
        self.assert_error("SINGLE_MINING", "participate", {"raceId": race.id, "enabled": True})
        self.assertEqual(race.enabled, {"A": False, "B": True, "C": False})
        self.send("prepare", {"guided": True})
        competition = self.room.session.race
        self.assertIsNone(competition.wire()["singleNode"])
        self.assertFalse(any(competition.enabled.values()))
        self.send("participate", {"raceId": competition.id, "enabled": True})
        self.assertEqual(competition.enabled, {"A": True, "B": False, "C": False})
        self.assert_error("STALE_RACE", "resume", {"raceId": race.id})
        self.assertEqual(competition.status, "paused")

    def test_invalid_node_requests_cannot_replace_or_mutate_current_candidate(self):
        race = self.prepare("A")
        before = copy.deepcopy(race.wire())
        for invalid in (None, "", "D", True, 1, ["A"]):
            self.assert_error("INVALID_MINING_NODE", "prepare", {"singleNode": invalid})
            self.assertIs(self.room.session.race, race)
            self.assertEqual(race.wire(), before)
        self.assert_error("INVALID_MINING_NODE", "resume", {"raceId": race.id, "singleNode": "C"})
        self.assertEqual(race.wire(), before)
        self.assert_error("SINGLE_MINING", "advance", {"raceId": race.id, "nodeId": "C", "reward": "missing"})
        self.assertEqual(race.wire(), before)

    def test_legacy_teacher_room_does_not_gain_single_node_command(self):
        room = Classroom("교사", 1)
        room.connected(room.teacher, 1)
        self.assert_error("INVALID_MINING_NODE", "prepare", {"singleNode": "A"},
                          room=room, participant=room.teacher)
        self.assertIsNone(room.session.race)
        self.send("prepare", room=room, participant=room.teacher)
        self.assertIsNone(room.session.race.single_node)

    def test_checkpoint_retains_selected_scope_and_resumes_only_that_node(self):
        race = self.prepare("B")
        self.send("resume", {"raceId": race.id})
        before = capture(self.room.session)
        restored = LabSession("restored")
        restore(restored, json.loads(json.dumps(before)))
        self.assertEqual(restored.race.single_node, "B")
        self.assertEqual(restored.race.status, "paused")
        self.assertNotEqual(restored.race.id, race.id)
        self.assertEqual(restored.lab.genesis, self.room.session.lab.genesis)
        self.assertEqual(restored.lab.nodes, self.room.session.lab.nodes)
        # Team-room restart intentionally clears enabled flags; common controls
        # restore the explicit selection without needing a team representative.
        restored.race.enabled = dict.fromkeys(("A", "B", "C"), False)
        restored.race.command("resume")
        self.assertEqual(restored.race.enabled, {"A": False, "B": True, "C": False})
        restored.race.command("pause")
        restored.race.enabled = dict.fromkeys(("A", "B", "C"), False)
        restored.race.command("step")
        self.assertEqual(restored.race.jobs["B"].attempts, 1)
        self.assertEqual(restored.race.jobs["A"].attempts, 0)
        self.assertEqual(restored.race.jobs["C"].attempts, 0)

    def test_old_checkpoint_competition_defaults_to_no_single_selection(self):
        self.send("prepare")
        previous = capture(self.room.session)
        del previous["race"]["fields"]["single_node"]
        restored = LabSession("old-format")
        restore(restored, previous)
        self.assertIsNone(restored.race.single_node)
        previous["race"]["fields"]["unexpected"] = "A"
        with self.assertRaises(ValueError):
            restore(restored, previous)


if __name__ == "__main__":
    unittest.main()
