"""Durable team links and identities; never open the developer's normal DB."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from education_engine.core.block import calculate_nonce, valid_pow
from education_engine.core.crypto import sign_transaction
from education_engine.core.errors import LabError
from education_engine.core.transaction import balance, build_transaction
from education_engine.service.autosave import persist_room
from education_engine.service.classroom import ClassroomHub
from education_engine.service.storage import PracticeStore
from team_rooms import TeamRooms


def service(store):
    runtime = SimpleNamespace(store=store, hub=ClassroomHub(store=store), creation_lock=threading.RLock())
    return TeamRooms(runtime)


def saved_transfer(room):
    """Commit a real transfer and mined block, not just display metadata."""
    lab = room.session.lab
    sender, recipient = lab.wallets[:2]
    node = lab.nodes["A"]
    draft = build_transaction(sender, recipient.address, 17, node.utxos, [], lab.session_id,
                              fee=1, spend_height=1)
    tx = sign_transaction(draft, sender.private_key)
    assert lab.submit("A", tx).valid
    candidate = lab.candidate("A", timestamp=node.chain[-1].timestamp + 1, extra_nonce="team-restart")
    for nonce in range(100000):
        block = calculate_nonce(candidate, nonce)
        if valid_pow(block.hash, block.difficulty):
            assert lab.accept_mined("A", block).valid
            break
    else:
        raise AssertionError("Difficulty-one mining exceeded test budget")
    while lab.queue:
        lab.deliver_next()
    room.note("팀", "저장할 송금과 블록 생성")
    persist_room(room, room.pending_events)
    room.pending_events = []
    return tx.id, block.hash, balance(node.utxos, sender.address)


class TeamRoomContracts:
    def assert_error(self, code, operation, status=None):
        with self.assertRaises(LabError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)
        if status is not None:
            self.assertEqual(caught.exception.status, status)

    def test_restart_preserves_link_ledger_and_member_but_replaces_authority(self):
        room, first = self.teams.create("준성", 1, "owner-a")
        _, second = self.teams.join(room.code, "예원", "owner-b")
        tx_id, block_hash, amount = saved_transfer(room)
        old = (room.code, room.practice_id, room.run_id, first.id, first.token, first.assignment_id)
        first.role, first.team = "representative", "A"
        persist_room(room, [])
        self.restart()
        restored = self.teams.get(old[0])
        self.assertTrue(restored.team_mode)
        self.assertEqual(restored.practice_id, old[1])
        self.assertNotEqual(restored.run_id, old[2])
        self.assertEqual(restored.people, {})  # No unowned placeholder or old live authority.
        _, resumed = self.teams.reconnect(old[0], "owner-a")
        self.assertEqual(resumed.id, old[3])
        self.assertEqual(resumed.name, "준성")
        self.assertNotEqual(resumed.token, old[4])
        self.assertNotEqual(resumed.assignment_id, old[5])
        self.assertEqual((resumed.role, resumed.team, resumed.connections), ("observer", None, 0))
        self.assert_error("UNAUTHORIZED", lambda: restored.auth(old[4]), 401)
        _, other = self.teams.reconnect(old[0], "owner-b")
        self.assertEqual(other.id, second.id)
        for node in restored.session.lab.nodes.values():
            self.assertEqual(node.chain[-1].hash, block_hash)
            self.assertIn(tx_id, [tx.id for block in node.chain for tx in block.transactions])
            self.assertEqual(balance(node.utxos, restored.session.lab.wallets[0].address), amount)

    def test_create_reuses_one_shared_room_without_resetting_difficulty_or_records(self):
        room, original = self.teams.create("첫 참가자", 1, "owner-a")
        _, block_hash, _ = saved_transfer(room)
        joined, other = self.teams.create("다음 참가자", 4, "owner-b")
        self.assertIs(joined, room)
        self.assertNotEqual(original.id, other.id)
        self.assertEqual(joined.session.lab.nodes["A"].chain[-1].hash, block_hash)
        self.assertEqual(joined.session.lab.nodes["A"].chain[-1].difficulty, 1)
        self.assertEqual(self.teams.default_code(), room.code)
        self.assertTrue(self.teams.is_practice(room.practice_id))
        self.assertFalse(self.teams.is_practice(uuid.uuid4().hex))
        self.assertEqual(len(self.store.list("classroom")), 1)

    def test_same_browser_reconnect_and_concurrent_join_keep_current_token(self):
        room, original = self.teams.create("예원", 1, "owner-a")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.teams.join(room.code, "다른 이름", "owner-a"), range(8)))
        self.assertTrue(all(person is original for _, person in results))
        self.assertIs(self.teams.reconnect(room.code, "owner-a")[1], original)
        self.assertEqual(len(room.people), 1)

    def test_unknown_browser_cannot_reconnect_but_can_join_as_new_observer(self):
        room, first = self.teams.create("준성", 1, "owner-a")
        self.assert_error("MEMBERSHIP_REQUIRED", lambda: self.teams.reconnect(room.code, "owner-b"), 404)
        self.assertEqual(len(room.people), 1)
        _, second = self.teams.join(room.code, "수현", "owner-b")
        self.assertNotEqual(second.id, first.id)
        self.assertEqual(second.role, "observer")

    def test_arbitrary_saved_legacy_code_is_not_public_until_explicit_migration(self):
        legacy, teacher = self.teams.hub.create(self.teams.hub.secret, "교사", 1)
        _, block_hash, _ = saved_transfer(legacy)
        self.teams.hub.room = None
        self.assertFalse(self.teams.is_team(legacy.code))
        self.assert_error("TEAM_ROOM_NOT_FOUND", lambda: self.teams.get(legacy.code), 404)
        self.assertFalse(self.teams.migrate_existing("ZZZZZZZZ" if legacy.code != "ZZZZZZZZ" else "YYYYYYYY"))
        self.assertTrue(self.teams.migrate_existing(legacy.code))
        migrated = self.teams.get(legacy.code)
        self.assertEqual(migrated.practice_id, legacy.practice_id)
        self.assertEqual(migrated.session.lab.nodes["A"].chain[-1].hash, block_hash)
        self.assertEqual(migrated.people, {})
        self.assert_error("UNAUTHORIZED", lambda: migrated.auth(teacher.token), 401)
        self.assertTrue(self.teams.migrate_existing(legacy.code))
        _, member = self.teams.join(legacy.code, "팀원", "owner-new")
        self.assertEqual(member.role, "observer")
        self.assertEqual(self.teams.create("다음 팀원", 1, "owner-other")[0].code, legacy.code)

    def test_deleted_archive_returns_gone_without_silently_starting_over(self):
        room, _ = self.teams.create("팀원", 1, "owner-a")
        self.store.delete(room.practice_id, "classroom")
        self.teams.hub.room = None
        self.assert_error("ROOM_GONE", lambda: self.teams.get(room.code), 410)
        self.assert_error("ROOM_GONE", lambda: self.teams.create("팀원", 1, "owner-a"), 410)

    def test_no_bearer_tokens_are_saved_with_member_identity(self):
        room, person = self.teams.create("팀원", 1, "owner-a")
        raw = json.dumps(self.store.read(room.practice_id, "classroom"), ensure_ascii=False)
        with self.teams._transaction() as connection:
            identity = self.teams._member(room.code, "owner-a", connection)
        self.assertEqual(set(identity), {"code", "owner_hash", "participant_id", "name"})
        self.assertNotIn(person.token, raw + json.dumps(identity))
        self.assertNotIn(self.teams.hub.secret, raw + json.dumps(identity))


class SQLiteTeamRoomsTests(TeamRoomContracts, unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="team-room-test-")
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "education.sqlite3"
        self.store = PracticeStore(self.path)
        self.addCleanup(lambda: self.store.db.close())
        self.teams = service(self.store)

    def restart(self):
        self.store.db.close()
        self.store = PracticeStore(self.path)
        self.teams = service(self.store)

    def test_create_failure_rolls_back_archive_and_mapping_together(self):
        self.store.db.execute("""CREATE TRIGGER reject_checkpoint BEFORE INSERT ON practices
            BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        self.assert_error("SAVE_FAILED", lambda: self.teams.create("팀원", 1, "owner-a"), 503)
        self.assertIsNone(self.teams.hub.room)
        self.assertIsNone(self.teams.default_code())
        self.assertEqual(self.store.count("classroom"), 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM team_members").fetchone()[0], 0)

    def test_join_failure_rolls_back_live_roster_and_member_mapping(self):
        room, first = self.teams.create("준성", 1, "owner-a")
        before = room.snapshot()
        self.store.db.execute("""CREATE TRIGGER reject_checkpoint BEFORE UPDATE ON practices
            BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        self.assert_error("SAVE_FAILED", lambda: self.teams.join(room.code, "예원", "owner-b"), 503)
        self.assertIs(room.people[first.id], first)
        self.assertEqual(room.snapshot(), before)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM team_members").fetchone()[0], 1)
        self.store.db.execute("DROP TRIGGER reject_checkpoint")
        _, second = self.teams.join(room.code, "예원", "owner-b")
        self.assertEqual(len(room.people), 2)
        self.assertNotEqual(second.id, first.id)

    def test_migration_failure_retains_original_archive_and_live_room(self):
        legacy, _ = self.teams.hub.create(self.teams.hub.secret, "교사", 1)
        before = self.store.read(legacy.practice_id, "classroom")
        self.store.db.execute("""CREATE TRIGGER reject_checkpoint BEFORE UPDATE ON practices
            BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        self.assert_error("SAVE_FAILED", lambda: self.teams.migrate_existing(legacy.code), 503)
        self.assertIs(self.teams.hub.room, legacy)
        self.assertEqual(self.store.read(legacy.practice_id, "classroom"), before)
        self.assertFalse(self.teams.is_team(legacy.code))

    def test_corrupt_snapshot_is_reported_without_replacing_saved_bytes(self):
        room, _ = self.teams.create("팀원", 1, "owner-a")
        with self.store.db:
            self.store.db.execute("UPDATE practices SET snapshot=? WHERE id=?", ("{broken", room.practice_id))
        self.teams.hub.room = None
        self.assert_error("CHECKPOINT_INVALID", lambda: self.teams.get(room.code), 409)
        raw = self.store.db.execute("SELECT snapshot FROM practices WHERE id=?", (room.practice_id,)).fetchone()[0]
        self.assertEqual(raw, "{broken")
        self.assertIsNone(self.teams.hub.room)

    def test_unavailable_database_reports_retryable_storage_error(self):
        self.store.db.close()
        self.assert_error("READ_FAILED", self.teams.default_code, 503)


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL is not configured")
class PostgresTeamRoomsTests(TeamRoomContracts, unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from postgres_backend import PostgresBackend
        from postgres_education_store import PostgresEducationStore
        self.Backend, self.Store = PostgresBackend, PostgresEducationStore
        self.url = os.environ["TEST_POSTGRES_URL"]
        self.prefix = "test_room_" + uuid.uuid4().hex[:12]
        self.admin = psycopg.connect(self.url, autocommit=True)
        self.backend = None

        def cleanup():
            if self.backend is not None:
                self.backend.close()
            try:
                for kind in ("education", "basic", "control"):
                    self.admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(self.prefix + "_" + kind)))
            finally:
                self.admin.close()

        self.addCleanup(cleanup)
        self.backend = self.Backend(self.url, self.prefix)
        self.store = self.Store(self.backend)
        self.teams = service(self.store)

    def restart(self):
        self.backend.close()
        self.backend = self.Backend(self.url, self.prefix)
        self.store = self.Store(self.backend)
        self.teams = service(self.store)

    def test_retired_process_cannot_write_new_membership_or_checkpoint(self):
        room, _ = self.teams.create("팀원", 1, "owner-a")
        stale_backend, stale_teams = self.backend, self.teams
        self.addCleanup(stale_backend.close)
        self.backend = self.Backend(self.url, self.prefix)
        self.store = self.Store(self.backend)
        self.teams = service(self.store)
        self.assert_error("SAVE_FAILED", lambda: stale_teams.join(room.code, "다음 팀원", "owner-b"), 503)
        self.assertEqual(len(room.people), 1)
        self.assert_error("MEMBERSHIP_REQUIRED", lambda: self.teams.reconnect(room.code, "owner-b"), 404)


if __name__ == "__main__":
    unittest.main()
