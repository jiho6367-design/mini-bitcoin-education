"""Port regressions use memory or temporary team archives, never personal data."""

import json
import os
from dataclasses import replace
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from education_engine.api.schemas import RaceRequest
from education_engine.core import experiments
from education_engine.core.block import calculate_nonce, valid_pow, validate_chain
from education_engine.core.crypto import create_wallet, hash_text, sign_transaction, verify_signature
from education_engine.core.errors import LabError
from education_engine.core.lab import Lab
from education_engine.core.mining import MiningJob
from education_engine.core.transaction import balance, build_transaction, reserved_inputs, validate_transaction
from education_engine.service.archive_html import render
from education_engine.service.autosave import persist_session
from education_engine.service.checkpoint import capture, restore
from education_engine.service.classroom import ClassroomHub
from education_engine.service.race import MiningRace
from education_engine.service.registry import LabRegistry
from education_engine.service.session import LabSession
from education_engine.service.storage import PracticeStore, default_path


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Command:
    """The transport validates its schema; the engine receives this small contract."""

    def __init__(self, room, person, action, payload=None, *, assignment=None):
        self.runId = room.run_id
        self.assignmentId = person.assignment_id if assignment is None else assignment
        self.commandId = uuid.uuid4().hex
        self.action = action
        self.payload = payload or {}

    def model_dump_json(self):
        return json.dumps(vars(self), sort_keys=True)


def mine(lab, node="A", tag=None):
    candidate = lab.candidate(node, timestamp=lab.nodes[node].chain[-1].timestamp + 1,
                              extra_nonce=tag or uuid.uuid4().hex)
    for nonce in range(100000):
        block = calculate_nonce(candidate, nonce)
        if valid_pow(block.hash, block.difficulty):
            result = lab.accept_mined(node, block)
            if not result.valid:
                raise AssertionError(result)
            return block
    raise AssertionError("Difficulty-one mining exceeded its regression budget")


class EducationCoreTests(unittest.TestCase):
    def setUp(self):
        self.alice = create_wallet("Alice", "1" * 64)
        self.bob = create_wallet("Bob", "2" * 64)
        self.lab = Lab([self.alice, self.bob], 1, 1000, hash_text("team-port-regression"))

    def transfer(self, amount=30, fee=2):
        node = self.lab.nodes["A"]
        draft = build_transaction(self.alice, self.bob.address, amount, node.utxos,
                                  node.mempool, self.lab.session_id, fee=fee,
                                  spend_height=node.chain[-1].height + 1)
        return sign_transaction(draft, self.alice.private_key)

    def drain(self):
        while self.lab.queue:
            self.lab.deliver_next()

    def test_utxo_change_fee_and_coinbase_are_conserved(self):
        tx = self.transfer()
        self.assertEqual([o.amount for o in tx.outputs], [30, 68])
        self.assertTrue(self.lab.submit("A", tx).valid)
        self.assertEqual(balance(self.lab.nodes["A"].utxos, self.alice.address), 100)
        self.assertEqual(balance(self.lab.nodes["A"].utxos, self.alice.address,
                                 reserved_inputs(self.lab.nodes["A"].mempool)), 0)
        block = mine(self.lab)
        self.assertEqual(block.transactions[0].outputs[0].amount, 52)
        self.drain()
        for node in self.lab.nodes.values():
            self.assertEqual(balance(node.utxos, self.alice.address), 68)
            self.assertEqual(balance(node.utxos, self.bob.address), 30)
            self.assertTrue(validate_chain(node.chain, self.lab.genesis).valid)

    def test_mempool_rejects_conflicting_spend_without_mutating_confirmed_utxos(self):
        first, conflict = self.transfer(), self.transfer(amount=20)
        self.assertTrue(self.lab.submit("A", first).valid)
        self.assertEqual(self.lab.submit("A", first).code, "DUPLICATE")
        self.assertEqual(self.lab.submit("A", conflict).code, "DOUBLE_SPEND")
        self.assertEqual(len(self.lab.nodes["A"].mempool), 1)
        self.assertEqual(balance(self.lab.nodes["A"].utxos, self.alice.address), 100)

    def test_signatures_are_deterministic_and_bound_to_content_and_practice(self):
        tx = self.transfer()
        self.assertEqual(tx.signature, self.transfer().signature)
        self.assertTrue(verify_signature(tx))
        changed = replace(tx, outputs=(replace(tx.outputs[0], amount=31), *tx.outputs[1:]))
        self.assertFalse(verify_signature(changed))
        other = sign_transaction(replace(tx, session_id=hash_text("another practice")),
                                 self.alice.private_key)
        self.assertEqual(validate_transaction(other, self.lab.nodes["A"].utxos,
                                              self.lab.session_id).code, "SESSION")

    def test_coinbase_matures_at_the_actual_spend_height_boundary(self):
        mine(self.lab)
        node = self.lab.nodes["A"]
        draft = build_transaction(self.lab.miners["A"], self.alice.address, 1, node.utxos,
                                  [], self.lab.session_id, spend_height=101)
        tx = sign_transaction(draft, self.lab.miners["A"].private_key)
        self.assertEqual(validate_transaction(tx, node.utxos, self.lab.session_id,
                                              spend_height=100).code, "IMMATURE_COINBASE")
        self.assertTrue(validate_transaction(tx, node.utxos, self.lab.session_id,
                                             spend_height=101).valid)

    def test_reorganization_preserves_side_branch_and_requeues_orphan_transfer(self):
        tx = self.transfer()
        self.lab.receive_transaction("A", tx)
        first = mine(self.lab, "A", "a1")
        side = mine(self.lab, "B", "b1")
        self.assertTrue(self.lab.receive_block("A", side).valid)
        self.assertEqual(self.lab.nodes["A"].chain[-1].hash, first.hash)
        self.assertIn(side.hash, self.lab.nodes["A"].blocks)
        stronger = mine(self.lab, "B", "b2")
        self.assertTrue(self.lab.receive_block("A", stronger).valid)
        node = self.lab.nodes["A"]
        self.assertEqual(node.chain[-1].hash, stronger.hash)
        self.assertIn(tx.id, [t.id for t in node.mempool])
        self.assertEqual(balance(node.utxos, self.alice.address), 100)
        self.assertEqual(self.lab.work(node.chain), 2 * 16)

    def test_child_arriving_before_parent_is_validated_after_parent_delivery(self):
        parent = mine(self.lab, "B", "parent")
        child = mine(self.lab, "B", "child")
        self.assertEqual(self.lab.receive_block("A", child).code, "PARENT_PENDING")
        self.assertEqual(self.lab.nodes["A"].chain[-1].height, 0)
        self.assertTrue(self.lab.receive_block("A", parent).valid)
        self.assertEqual(self.lab.nodes["A"].chain[-1].hash, child.hash)
        self.assertFalse(self.lab.nodes["A"].pending_blocks)

    def test_same_hash_with_changed_payload_is_not_approved_as_a_duplicate(self):
        block = mine(self.lab)
        changed = replace(block.transactions[0], outputs=(replace(block.transactions[0].outputs[0], amount=999),))
        forged = replace(block, transactions=(changed,))
        self.assertFalse(self.lab.receive_block("A", forged).valid)
        self.assertEqual(self.lab.nodes["A"].chain[-1], block)

    def test_failure_experiments_preserve_the_normal_chain(self):
        tx = self.transfer()
        self.assertFalse(self.lab.submit("A", experiments.forge_signature(tx)).valid)
        self.assertTrue(self.lab.submit("A", tx).valid)
        _, rejected = experiments.invalid_pow_block(self.lab, "A")
        self.assertEqual(rejected.code, "POW")
        mine(self.lab)
        before = list(self.lab.nodes["A"].chain)
        report = experiments.tamper(before, self.lab.genesis, 1, "transaction", 999, True)
        self.assertFalse(report.chain_validation.valid)
        self.assertFalse(dict(report.checks)["거래 전자서명"])
        self.assertEqual(self.lab.nodes["A"].chain, before)

    def test_actual_nonce_samples_pause_resume_and_observation_time(self):
        candidate = replace(self.lab.candidate("A", extra_nonce="samples"), difficulty=4)
        while any(valid_pow(calculate_nonce(candidate, n).hash, 4) for n in range(3)):
            candidate = replace(candidate, timestamp=candidate.timestamp + 1)
        clock = Clock()
        job = MiningJob("job", "A", candidate, "learning", clock)
        job.start(step=False)
        clock.now += .25
        job.pause()
        self.assertEqual(job.observation_ms, 250)
        job.step()
        self.assertEqual((job.status, job.attempts, job.latest.nonce), ("paused", 2, 1))
        self.assertEqual(job.latest, calculate_nonce(candidate, 1))
        clock.now += 5
        job.resume()
        self.assertEqual(job.attempts, 3)
        self.assertEqual(job.observation_ms, 250)
        job.stop()
        job.tick()
        self.assertEqual((job.status, job.attempts), ("stopped", 3))

    def test_mining_race_advances_maturity_by_mining_and_validating_real_blocks(self):
        race = MiningRace(self.lab, guided=True, mode="learning")
        for _ in range(2000):
            race.command("step")
            if race.completed:
                break
        self.assertTrue(race.completed)
        winner = race.completed[0]["node"]
        self.drain()
        reward = self.lab.nodes[winner].chain[1].transactions[0].id + ":0"
        race.advance(winner, reward)
        for _ in range(10000):
            race.compute(single=False)
            if race.target_reward is None:
                break
        self.assertIsNone(race.target_reward)
        self.assertEqual(self.lab.nodes[winner].chain[-1].height, 100)
        for node in self.lab.nodes.values():
            self.assertTrue(validate_chain(node.chain, self.lab.genesis).valid)
            self.assertEqual(node.chain[-1].height, 100)

    def test_restart_checkpoint_pauses_and_rekeys_jobs_without_changing_ledger(self):
        session = LabSession("original")
        session.lab, session.wallets = self.lab, self.lab.wallets
        session.race_action(RaceRequest(action="prepare"))
        session.race.status = "running"
        old_id = session.race.id
        restored = LabSession("restored")
        restore(restored, capture(session))
        self.assertEqual(restored.race.status, "paused")
        self.assertNotEqual(restored.race.id, old_id)
        self.assertEqual(restored.lab.genesis, self.lab.genesis)
        self.assertEqual(restored.wallets, session.wallets)
        self.assertIs(restored.race.lab, restored.lab)
        with self.assertRaises(LabError) as error:
            restored.mining_command("old job", "step")
        self.assertEqual(error.exception.code, "STALE_JOB")


class EducationArchiveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = PracticeStore(Path(self.folder.name) / "education.sqlite3")
        self.addCleanup(self.store.db.close)
        self.registry = LabRegistry(store=self.store)
        self.session = self.registry.create()
        self.session.start(1, example=True)

    def test_database_default_ignores_all_personal_environment_paths(self):
        with patch.dict(os.environ, {"MINI_BITCOIN_DB": "do-not-open.sqlite3"}, clear=True):
            self.assertEqual(default_path(), Path(__file__).resolve().parents[1] / "instance" / "education.sqlite3")
        chosen = str(Path(self.folder.name) / "chosen-team.sqlite3")
        with patch.dict(os.environ, {"TEAM_EDUCATION_DB": chosen}):
            self.assertEqual(default_path(), Path(chosen))

    def test_legacy_database_schema_is_rejected_before_any_file_change(self):
        legacy = Path(self.folder.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy)) as db, db:
            db.executescript("""
                CREATE TABLE practices (id TEXT PRIMARY KEY, revision INTEGER,
                    created_at TEXT, saved_at TEXT, payload TEXT, digest TEXT);
                PRAGMA user_version=1;
            """)
        before = legacy.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "원본을 변경하지 않았습니다"):
            PracticeStore(legacy)
        self.assertEqual(legacy.read_bytes(), before)
        legacy.rename(legacy.with_suffix(".closed.sqlite3"))

    def test_archive_resume_keeps_wallet_keys_and_retires_the_old_session(self):
        item = self.store.read(self.session.practice_id, "personal")
        resumed = self.registry.resume(item)
        self.assertEqual(resumed.wallets, self.session.wallets)
        self.assertEqual(resumed.lab.genesis, self.session.lab.genesis)
        self.assertTrue(self.session.retired)
        with self.assertRaises(LabError) as error:
            self.session.add_wallet()
        self.assertEqual(error.exception.code, "STALE_LAB")

    def test_save_failure_rolls_back_before_acknowledgement(self):
        before = self.session.snapshot()
        with patch.object(self.store, "save", side_effect=LabError("SAVE_FAILED", "disk full", 503)):
            with self.assertRaises(LabError):
                self.session.add_wallet()
        after = self.session.snapshot()
        self.assertEqual(after["wallets"], before["wallets"])
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(self.session.storage_error, "disk full")

    def test_archive_search_utc_date_boundaries_and_export_do_not_reveal_keys(self):
        self.session.wallets[0] = replace(self.session.wallets[0], name="<script>unsafe</script>")
        persist_session(self.session, [])
        identity = self.session.practice_id
        self.store.rename(identity, "personal", "Team experiment")
        with self.store.db:
            self.store.db.execute("UPDATE practices SET updated=? WHERE id=?", ("2026-11-16T00:00:00.123456+09:00", identity))
        self.assertEqual(self.store.count("personal", "experiment", "2026-11-15T15:00:00.123456Z", "2026-11-15T15:00:00.123457Z"), 1)
        self.assertEqual(self.store.count("personal", "experiment", updated_until="2026-11-15T15:00:00.123456Z"), 0)
        html = render(self.store.read(identity, "personal"))
        self.assertIn("&lt;script&gt;unsafe&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)
        for wallet in self.session.wallets:
            self.assertNotIn(wallet.private_key, html)
        self.assertNotIn("privateKey", json.dumps(self.session.snapshot()))

    def test_backup_validates_corruption_before_replacing_the_team_archive(self):
        backup = Path(self.folder.name) / "backup.sqlite3"
        self.store.backup(backup)
        self.session.add_wallet()
        live = self.store.read(self.session.practice_id, "personal")
        with closing(sqlite3.connect(backup)) as db, db:
            db.execute("UPDATE practices SET checkpoint='{}'")
        with self.assertRaises(ValueError):
            self.store.restore_backup(backup)
        self.assertEqual(self.store.read(self.session.practice_id, "personal")["checkpoint"], live["checkpoint"])

    def test_valid_backup_restores_a_complete_archive_and_can_be_reopened(self):
        backup = Path(self.folder.name) / "complete-backup.sqlite3"
        self.store.backup(backup)
        expected = self.store.read(self.session.practice_id, "personal")["checkpoint"]
        self.session.add_wallet()
        self.store.restore_backup(backup)
        item = self.store.read(self.session.practice_id, "personal")
        self.assertEqual(item["checkpoint"], expected)
        resumed = LabSession("from-backup")
        restore(resumed, item["checkpoint"]["session"])
        self.assertEqual(len(resumed.wallets), 2)
        self.assertTrue(validate_chain(resumed.lab.nodes["A"].chain, resumed.lab.genesis).valid)
        backup.rename(backup.with_suffix(".closed.sqlite3"))


class EducationClassroomTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = PracticeStore(":memory:")
        self.addCleanup(self.store.db.close)
        self.hub = ClassroomHub("team-secret", clock=self.clock, store=self.store)
        self.room, self.teacher = self.hub.create("team-secret", "Teacher", 1)
        self.student = self.room.add("Student")
        self.room.connected(self.teacher, 1)
        self.room.connected(self.student, 1)

    def send(self, person, action, payload=None, **kwargs):
        return self.room.command(person, Command(self.room, person, action, payload, **kwargs))

    def test_roles_assignments_and_command_retries_are_authoritative(self):
        with self.assertRaises(LabError) as error:
            self.send(self.student, "prepare")
        self.assertEqual(error.exception.code, "FORBIDDEN")
        previous_assignment = self.student.assignment_id
        command = Command(self.room, self.teacher, "assign", {"participantId": self.student.id, "team": "A"})
        self.room.command(self.teacher, command)
        seq = self.room.seq
        self.room.command(self.teacher, command)
        self.assertEqual(self.room.seq, seq)
        self.assertEqual(self.student.role, "representative")
        with self.assertRaises(LabError) as error:
            self.send(self.student, "draft", assignment=previous_assignment)
        self.assertEqual(error.exception.code, "STALE_ROLE")
        other = self.room.session.wallets[1].address
        with self.assertRaises(LabError) as error:
            self.send(self.student, "draft", {"senderAddress": other})
        self.assertEqual(error.exception.code, "FORBIDDEN")

    def test_disconnected_representative_stops_participating_after_grace_period(self):
        self.send(self.teacher, "assign", {"participantId": self.student.id, "team": "A"})
        self.send(self.teacher, "prepare")
        race = self.room.session.race
        self.send(self.student, "participate", {"raceId": race.id, "enabled": True})
        self.send(self.teacher, "resume", {"raceId": race.id})
        self.room.connected(self.student, -1)
        self.clock.now += 16
        self.room.tick()
        self.assertTrue(self.student.suspended)
        self.assertFalse(race.enabled["A"])
        self.assertEqual(race.status, "paused")

    def test_classroom_history_contains_no_live_tokens_or_teacher_secret(self):
        item = self.store.read(self.room.practice_id, "classroom")
        payload = json.dumps(item, ensure_ascii=False)
        for secret in (self.teacher.token, self.student.token, self.hub.secret):
            self.assertNotIn(secret, payload)
        with self.assertRaises(LabError) as error:
            self.room.auth("invalid token")
        self.assertEqual(error.exception.code, "UNAUTHORIZED")


if __name__ == "__main__":
    unittest.main()
