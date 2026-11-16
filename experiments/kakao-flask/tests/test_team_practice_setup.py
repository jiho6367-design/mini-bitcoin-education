"""Independent, configurable practice links must not rewrite an existing ledger."""

from pathlib import Path
import sys
import tempfile
import unittest

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from education_api import OWNER_COOKIE, PREFIX, register_education
from education_engine.core.block import calculate_nonce, valid_pow
from education_engine.core.crypto import create_wallet, sign_transaction
from education_engine.core.errors import LabError
from education_engine.core.lab import Lab
from education_engine.core.transaction import balance, build_transaction
from education_engine.service.autosave import persist_room
from team_rooms import MAX_ACTIVE_TEAM_ROOMS


def transfer(room, amount=10):
    lab = room.session.lab
    sender, recipient = lab.wallets[:2]
    draft = build_transaction(sender, recipient.address, amount, lab.nodes["A"].utxos,
                              [], lab.session_id, fee=1, spend_height=1)
    tx = sign_transaction(draft, sender.private_key)
    assert lab.submit("A", tx).valid
    return tx


def mine_and_save(room):
    lab = room.session.lab
    candidate = lab.candidate("A", timestamp=lab.genesis.timestamp + 1, extra_nonce="setup")
    for nonce in range(100000):
        block = calculate_nonce(candidate, nonce)
        if valid_pow(block.hash, block.difficulty):
            assert lab.accept_mined("A", block).valid
            break
    else:
        raise AssertionError("Difficulty-one mining exceeded its test budget")
    while lab.queue:
        lab.deliver_next()
    persist_room(room, [])
    return block


class TeamPracticeSetupTests(unittest.TestCase):
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
        app.config.update(TESTING=True, TEAM_TEACHER_SECRET="setup-test")
        runtime = register_education(app, str(Path(self.folder.name) / "setup.sqlite3"),
                                     start_runner=False)
        self.runtimes.append(runtime)
        return app, runtime

    def create(self, **options):
        response = self.client.post(PREFIX + "/team/rooms", json={"name": "팀원", **options})
        self.assertEqual(response.status_code, 201, response.text)
        credential = response.get_json()
        return self.runtime.team_rooms.get(credential["code"]), credential

    def assert_initial(self, room, amount):
        lab = room.session.lab
        self.assertEqual([output.amount for output in lab.genesis.allocations], [amount] * 3)
        for node in lab.nodes.values():
            self.assertEqual([balance(node.utxos, wallet.address) for wallet in lab.wallets], [amount] * 3)

    def test_explicit_custom_practice_does_not_replace_default_or_its_ledger(self):
        original, first = self.create()
        original_tx = transfer(original)
        original_block = mine_and_save(original)
        original_id = original.practice_id
        custom, second = self.create(newPractice=True, initialBalance=50)
        self.assertNotEqual(first["code"], second["code"])
        self.assertNotEqual(original_id, custom.practice_id)
        self.assert_initial(custom, 50)
        transfer(custom)
        mine_and_save(custom)
        self.assertEqual(balance(custom.session.lab.nodes["A"].utxos,
                                 custom.session.lab.wallets[0].address), 39)
        self.assertEqual(self.runtime.team_rooms.default_code(), first["code"])
        self.assertIs(self.runtime.hub.room, original)
        self.assertIs(self.runtime.team_rooms.get(first["code"]), original)
        self.assertEqual(original.session.lab.nodes["A"].chain[-1], original_block)
        self.assertIn(original_tx.id, [tx.id for tx in original_block.transactions])
        joined, _ = self.create(initialBalance=999, difficulty=4)
        self.assertIs(joined, original)
        self.assertEqual(joined.session.lab.genesis.allocations[0].amount, 100)
        self.assertEqual(joined.session.lab.genesis.difficulty, 1)
        self.assertEqual(self.client.get(PREFIX + "/classroom/config").get_json()["defaultTeamCode"], first["code"])

    def test_both_links_and_custom_ledger_restore_after_runtime_replacement(self):
        original, first = self.create()
        custom, second = self.create(newPractice=True, initialBalance=250)
        tx = transfer(custom, 17)
        block = mine_and_save(custom)
        original_genesis = original.session.lab.genesis.hash
        owner_cookie = self.client.get_cookie(OWNER_COOKIE).value
        self.runtime.close()
        app, restarted = self.make_app()
        restored_extra = restarted.team_rooms.get(second["code"])
        self.assertIsNone(restarted.hub.room)  # Loading an extra never steals the primary slot.
        restored_default = restarted.team_rooms.get(first["code"])
        self.assert_initial(restored_default, 100)
        self.assertEqual(restored_default.session.lab.genesis.hash, original_genesis)
        self.assertEqual(restored_extra.session.lab.genesis.allocations[0].amount, 250)
        self.assertEqual(restored_extra.session.lab.nodes["A"].chain[-1].hash, block.hash)
        self.assertEqual(balance(restored_extra.session.lab.nodes["A"].utxos,
                                 restored_extra.session.lab.wallets[0].address), 232)
        self.assertIn(tx.id, [item.id for item in restored_extra.session.lab.nodes["A"].chain[-1].transactions])
        self.assertEqual(restarted.team_rooms.default_code(), first["code"])
        self.assertEqual(len(restarted.team_rooms.active_rooms()), 2)
        resumed_browser = app.test_client()
        resumed_browser.set_cookie(OWNER_COOKIE, owner_cookie)
        for old in (first, second):
            response = resumed_browser.post(PREFIX + f"/team/rooms/{old['code']}/reconnect", json={})
            self.assertEqual(response.status_code, 200, response.text)
            renewed = response.get_json()
            self.assertEqual(renewed["participantId"], old["participantId"])
            self.assertNotEqual(renewed["token"], old["token"])

    def test_setup_rejects_coercion_out_of_range_and_non_boolean_new_request(self):
        wallets = [create_wallet("A"), create_wallet("B"), create_wallet("C")]
        for value in (True, False, 0, -1, 10001, 1.5, "50", None):
            with self.subTest(value=value):
                response = self.client.post(PREFIX + "/team/rooms", json={
                    "name": "팀원", "newPractice": True, "initialBalance": value,
                })
                self.assertEqual(response.status_code, 400, response.text)
                with self.assertRaises(LabError) as caught:
                    Lab(wallets, team_allocations=True, team_initial_balance=value)
                self.assertEqual(caught.exception.code, "INVALID_INITIAL_BALANCE")
        for value in ("true", 1, None):
            response = self.client.post(PREFIX + "/team/rooms", json={"name": "팀원", "newPractice": value})
            self.assertEqual(response.status_code, 400, response.text)
        self.assertIsNone(self.runtime.team_rooms.default_code())
        room, _ = self.create(newPractice=True, initialBalance=1)
        self.assert_initial(room, 1)
        largest, _ = self.create(newPractice=True, initialBalance=10000)
        self.assert_initial(largest, 10000)

    def test_runner_delivers_messages_in_each_independent_active_room(self):
        original, _ = self.create()
        custom, _ = self.create(newPractice=True, initialBalance=50)
        transactions = [transfer(room) for room in (original, custom)]
        for room in (original, custom):
            room.teacher.connections = 1
            room.automatic = True
        self.runtime.run_once()
        for room, tx in zip((original, custom), transactions):
            self.assertIn(tx.id, [item.id for item in room.session.lab.nodes["B"].mempool])
        self.assertNotIn(transactions[1].id, [item.id for item in original.session.lab.nodes["B"].mempool])
        self.assertNotIn(transactions[0].id, [item.id for item in custom.session.lab.nodes["B"].mempool])

    def test_idle_extra_eviction_keeps_link_and_pauses_saved_work(self):
        original, first = self.create()
        extras = [self.create(newPractice=True, initialBalance=amount)[0]
                  for amount in range(20, 20 + MAX_ACTIVE_TEAM_ROOMS - 1)]
        first_extra = extras[0]
        old_token = first_extra.teacher.token
        first_extra.automatic = True
        saved_hash = first_extra.session.lab.genesis.hash
        self.create(newPractice=True, initialBalance=77)
        self.assertTrue(first_extra.closed)  # A stale in-memory token cannot mutate it.
        self.assertFalse(first_extra.automatic)
        self.assertFalse(self.runtime.store.read(first_extra.practice_id, "classroom")["snapshot"]["automatic"])
        with self.assertRaises(LabError) as caught:
            first_extra.auth(old_token)
        self.assertEqual(caught.exception.code, "ROOM_GONE")
        self.assertLessEqual(len(self.runtime.team_rooms.active_rooms()), MAX_ACTIVE_TEAM_ROOMS)
        restored = self.runtime.team_rooms.get(first_extra.code)
        self.assertEqual(restored.session.lab.genesis.hash, saved_hash)
        self.assert_initial(restored, 20)
        self.assertEqual(restored.people, {})  # Browser membership is renewed, never restored as live authority.
        self.assertIs(self.runtime.hub.room, original)
        self.assertEqual(self.runtime.team_rooms.default_code(), first["code"])

    def test_connected_rooms_are_never_evicted_and_new_creation_obeys_archive_limit(self):
        self.create()
        for _ in range(MAX_ACTIVE_TEAM_ROOMS - 1):
            room, _ = self.create(newPractice=True, initialBalance=50)
            room.teacher.connections = 1
        response = self.client.post(PREFIX + "/team/rooms", json={"name": "새 팀원", "newPractice": True})
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.get_json()["error"]["code"], "ACTIVE_ROOM_LIMIT")
        self.app.config["EDUCATION_MAX_PRACTICES"] = 0
        response = self.client.post(PREFIX + "/team/rooms", json={"name": "새 팀원", "newPractice": True})
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.get_json()["error"]["code"], "ARCHIVE_LIMIT")
        # Joining the preserved default still works despite both creation limits.
        self.create(initialBalance=777)


if __name__ == "__main__":
    unittest.main()
