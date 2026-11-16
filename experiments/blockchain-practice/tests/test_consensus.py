"""합의·분리·재구성·채굴 작업·저장 경계의 회귀 테스트."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from block import Block
from blockchain import BLOCK_REWARD, FAUCET_AMOUNT, Blockchain, State
from mempool import Mempool
from miner import build_candidate, mine
from node import Network
from transaction import COINBASE, FAUCET, TRANSFER, Transaction
from wallet import Wallet


def finish(node):
    info = node.start_mining()
    for _ in range(1000):
        result = node.mine_step(5000, info["job_id"])
        if result.get("found"):
            assert result["accepted"], result
            return result
    raise AssertionError("채굴이 제한 시간 안에 끝나지 않았습니다")


def payment(sender, receiver, amount, nonce=0):
    tx = Transaction(TRANSFER, sender.public_key, receiver.address, amount, nonce)
    tx.sign(sender)
    return tx


def force_first_failure(node):
    block = node.candidate["block"]
    while block.calculate_hash().startswith("0" * block.difficulty):
        block.timestamp += 1
    block.hash = block.calculate_hash()


class ConsensusRegression(unittest.TestCase):
    def test_offline_mining_stays_local_and_offline_sync_is_blocked(self):
        net = Network(difficulty=1)
        a = net.nodes["A"]
        a.online = False
        result = finish(a)
        self.assertEqual([n.blockchain.tip.index for n in net.nodes.values()], [1, 0, 0])
        self.assertTrue(all(not p["accepted"] for p in result["propagation"]))
        self.assertFalse(a.resolve_conflicts())
        a.online = True
        self.assertTrue(net.nodes["B"].resolve_conflicts())
        self.assertEqual(net.nodes["B"].blockchain.tip.hash, a.blockchain.tip.hash)

    def test_sync_and_direct_add_enforce_the_receivers_difficulty(self):
        net = Network(difficulty=3)
        weak = Blockchain(difficulty=1)
        block, _, _ = mine(weak, Mempool(weak), net.nodes["B"].wallet.address)
        self.assertFalse(net.nodes["A"].blockchain.add_block(block)[0])
        net.nodes["B"].blockchain = weak
        self.assertFalse(net.nodes["A"].resolve_conflicts())
        self.assertEqual(net.nodes["A"].blockchain.tip.index, 0)

    def test_historical_difficulty_is_preserved_for_lagging_nodes(self):
        net = Network(difficulty=1)
        finish(net.nodes["A"])
        net.nodes["C"].online = False
        finish(net.nodes["A"])
        policy = net.set_difficulty(2)
        self.assertEqual(policy["from_height"], 3)
        self.assertEqual(net.difficulty_rules, [(1, 1), (3, 2)])
        self.assertTrue(all(n.blockchain.is_valid() for n in net.nodes.values()))
        self.assertEqual(net.nodes["C"].start_mining()["difficulty"], 1)
        self.assertEqual(net.nodes["A"].start_mining()["difficulty"], 2)
        net.set_difficulty(3)
        self.assertEqual(net.difficulty_rules, [(1, 1), (3, 3)])
        self.assertTrue(all(n.candidate is None for n in net.nodes.values()))
        self.assertTrue(all(n.blockchain.is_valid() for n in net.nodes.values()))

    def test_shorter_chain_with_more_work_wins(self):
        net = Network(difficulty=1)
        long_chain = Blockchain(difficulty=1)
        for _ in range(3):
            mine(long_chain, Mempool(long_chain), net.nodes["A"].wallet.address)
        strong = Blockchain(difficulty=1)
        block = build_candidate(strong, Mempool(strong), net.nodes["B"].wallet.address)
        block.difficulty = 2
        block.mine_to_target()
        self.assertTrue(strong.add_block(block)[0])
        net.nodes["A"].blockchain = long_chain
        net.nodes["A"].pool = Mempool(long_chain)
        net.nodes["B"].blockchain = strong
        self.assertTrue(net.nodes["A"].resolve_conflicts())
        self.assertEqual(net.nodes["A"].blockchain.tip.index, 1)
        self.assertEqual(net.nodes["A"].blockchain.cumulative_work, 256)

    def test_seen_hash_does_not_approve_a_different_payload(self):
        net = Network(difficulty=1)
        a = net.nodes["A"]
        finish(a)
        bad = a.blockchain.tip.copy()
        bad.transactions[0].amount = 999
        self.assertFalse(a.receive_block(bad, origin="B")[0])
        self.assertTrue(a.receive_block(a.blockchain.tip.copy(), origin="B")[0])
        self.assertTrue(a.blockchain.is_valid())

    def test_reorg_requeues_valid_orphaned_transfers(self):
        net = Network(difficulty=1)
        alice, bob = net.create_wallet("Alice"), net.create_wallet("Bob")
        a, b, c = (net.nodes[name] for name in "ABC")
        self.assertTrue(a.receive_transaction(Transaction(FAUCET, "", alice.address, FAUCET_AMOUNT))[0])
        finish(a)
        b.online = c.online = False
        tx = payment(alice, bob, 20)
        self.assertTrue(a.receive_transaction(tx)[0])
        a.online = False
        finish(a)
        b.online = c.online = True
        finish(b)
        finish(b)
        a.online = True
        self.assertTrue(a.resolve_conflicts())
        self.assertEqual([t.txid() for t in a.mempool], [tx.txid()])
        self.assertEqual(a.blockchain.state().balance(alice.address), 50)
        self.assertEqual(a.pending_state().balance(alice.address), 30)
        finish(a)
        self.assertTrue(all(n.blockchain.is_valid() for n in net.nodes.values()))
        self.assertTrue(all(n.blockchain.state().balance(bob.address) == 20 for n in net.nodes.values()))

    def test_empty_or_malformed_chain_is_not_valid(self):
        self.assertFalse(Blockchain(chain=[]).is_valid())
        chain = Blockchain(difficulty=1)
        block = build_candidate(chain, Mempool(chain), Wallet().address)
        block.nonce = -1
        block.hash = block.calculate_hash()
        self.assertFalse(chain.add_block(block)[0])
        malformed = Transaction(FAUCET, "", Wallet().address, 50, signature=["bad"])
        chain.chain[0].transactions.append(malformed)
        self.assertFalse(chain.is_valid())


class AtomicValidation(unittest.TestCase):
    def test_block_failure_does_not_partially_update_passed_state(self):
        alice, bob, miner = Wallet(), Wallet(), Wallet()
        state = State()
        state.apply(Transaction(FAUCET, "", alice.address, FAUCET_AMOUNT))
        before = state.copy()
        chain = Blockchain(difficulty=1)
        block = Block(1, chain.tip.hash,
                      [Transaction(COINBASE, "", miner.address, BLOCK_REWARD),
                       payment(alice, bob, 20), payment(alice, bob, 9999, 1)], 1)
        block.mine_to_target()
        checks, _ = chain.check_block(block, chain.tip, state)
        self.assertFalse(checks["txs"])
        self.assertEqual(state.balances, before.balances)
        self.assertEqual(state.nonces, before.nonces)
        self.assertEqual(state.faucet_used, before.faucet_used)

    def test_nonce_address_and_time_formats_are_checked_before_mutation(self):
        alice, bob = Wallet(), Wallet()
        for nonce in (False, 0.0, -1, "0"):
            tx = payment(alice, bob, 1, nonce)
            self.assertFalse(tx.basic_check()[0])
        for address in ("-" + "f" * 63, "+" + "a" * 63, " " + "a" * 63):
            self.assertFalse(Transaction(FAUCET, "", address, 50).basic_check()[0])
        self.assertFalse(Transaction(FAUCET, "", bob.address, 50, timestamp=float("nan")).basic_check()[0])
        self.assertFalse(Transaction(FAUCET, "", bob.address, 50, timestamp=10**1000).basic_check()[0])

    def test_mempool_and_candidate_copy_mutable_inputs(self):
        net = Network(difficulty=1)
        alice = net.create_wallet("Alice")
        tx = Transaction(FAUCET, "", alice.address, 50)
        self.assertTrue(net.nodes["A"].receive_transaction(tx)[0])
        tx.amount = 999
        self.assertTrue(all(n.mempool[0].amount == 50 for n in net.nodes.values()))
        a = net.nodes["A"]
        a.start_mining()
        a.mempool[0].amount = 999
        self.assertEqual(a.candidate["block"].transactions[1].amount, 50)


class MiningJobs(unittest.TestCase):
    def test_pause_single_step_resume_stop_and_actual_hash_pair(self):
        node = Network(difficulty=1).nodes["A"]
        job_id = node.start_mining()["job_id"]
        force_first_failure(node)
        self.assertEqual(node.pause_mining(job_id)["status"], "paused")
        blocked = node.mine_step(20, job_id)
        self.assertEqual(blocked["code"], "MINING_PAUSED")
        self.assertEqual(node.candidate["attempts"], 0)
        result = node.mine_step(1, job_id)
        self.assertFalse(result["found"])
        self.assertEqual(result["status"], "paused")
        self.assertEqual((result["nonce"], result["next_nonce"]), (0, 1))
        self.assertEqual(result["hash"], node.candidate["block"].calculate_hash())
        self.assertEqual(result["samples"], [{"nonce": 0, "hash": result["hash"], "passed": False}])
        self.assertEqual(node.resume_mining(job_id)["status"], "running")
        self.assertEqual(node.stop_mining(job_id)["status"], "stopped")
        self.assertIsNone(node.candidate)
        self.assertEqual(node.candidate_info()["job_id"], job_id)

    def test_old_job_cannot_step_pause_or_stop_a_replacement(self):
        node = Network(difficulty=1).nodes["A"]
        old = node.start_mining()["job_id"]
        new = node.start_mining()["job_id"]
        for operation in (lambda: node.mine_step(1, old), lambda: node.pause_mining(old), lambda: node.stop_mining(old)):
            self.assertEqual(operation()["code"], "STALE_JOB")
        self.assertEqual(node.candidate["job_id"], new)
        self.assertEqual(node.candidate["attempts"], 0)
        for batch in (0, -1, True, 1.5, 50001):
            with self.assertRaises(ValueError):
                node.mine_step(batch, new)

    def test_new_block_cancels_old_candidate_without_late_commit(self):
        net = Network(difficulty=1)
        old = net.nodes["A"].start_mining()["job_id"]
        finish(net.nodes["B"])
        a = net.nodes["A"]
        self.assertIsNone(a.candidate)
        self.assertEqual(a.candidate_info()["reason"], "chain_changed")
        self.assertEqual(a.mine_step(1, old)["code"], "STALE_JOB")
        self.assertEqual(a.blockchain.tip.index, 1)


class Serialization(unittest.TestCase):
    def test_json_roundtrip_preserves_wallets_pool_policy_and_events(self):
        net = Network(difficulty=1)
        alice = net.create_wallet("Alice")
        net.nodes["A"].receive_transaction(Transaction(FAUCET, "", alice.address, 50))
        finish(net.nodes["A"])
        net.set_difficulty(2)
        net.nodes["C"].online = False
        data = json.loads(json.dumps(net.to_dict(), allow_nan=False))
        restored = Network.from_dict(data)
        self.assertEqual(restored.to_dict(), data)
        self.assertEqual(restored.wallets[alice.address].private_key, alice.private_key)
        self.assertFalse(restored.nodes["C"].online)

    def test_request_reload_keeps_job_but_restart_cancels_it(self):
        net = Network(difficulty=1)
        a = net.nodes["A"]
        job_id = a.start_mining()["job_id"]
        force_first_failure(a)
        a.pause_mining(job_id)
        a.mine_step(1, job_id)
        data = json.loads(json.dumps(net.to_dict(), allow_nan=False))
        request_reload = Network.from_dict(data, restore_jobs=True)
        job = request_reload.nodes["A"].candidate
        self.assertEqual((job["job_id"], job["status"], job["attempts"], job["next_nonce"]), (job_id, "paused", 1, 1))
        self.assertEqual(request_reload.to_dict(), data)
        restarted = Network.from_dict(data)
        self.assertTrue(restarted.restored_mining_cancelled)
        self.assertIsNone(restarted.nodes["A"].candidate)
        self.assertEqual(restarted.nodes["A"].candidate_info()["reason"], "restore_cancelled")

    def test_intentional_chain_tampering_survives_restore_then_recovers(self):
        net = Network(difficulty=1)
        alice = net.create_wallet("Alice")
        net.nodes["A"].receive_transaction(Transaction(FAUCET, "", alice.address, 50))
        finish(net.nodes["A"])
        net.nodes["B"].blockchain.chain[1].transactions[1].amount = 999
        restored = Network.from_dict(json.loads(json.dumps(net.to_dict())))
        self.assertFalse(restored.nodes["B"].blockchain.is_valid())
        self.assertEqual(restored.nodes["B"].blockchain.chain[1].transactions[1].amount, 999)
        self.assertTrue(restored.nodes["B"].resolve_conflicts())
        self.assertTrue(restored.nodes["B"].blockchain.is_valid())

    def test_bad_version_history_or_wallet_is_rejected(self):
        data = Network(difficulty=1).to_dict()
        for bad in ({**data, "version": 2}, {**data, "difficulty_rules": [[2, 1]]},
                    {**data, "difficulty_rules": [[1, True]]}):
            with self.assertRaises(ValueError):
                Network.from_dict(bad)
        data["wallets"][0]["address"] = "a" * 64
        with self.assertRaises(ValueError):
            Network.from_dict(data)
        data = Network(difficulty=1).to_dict()
        data["wallets"][0]["private_key"] = "0" * 64
        with self.assertRaises(ValueError):
            Network.from_dict(data)
        net = Network(difficulty=1)
        job = net.nodes["A"].start_mining()
        force_first_failure(net.nodes["A"])
        net.nodes["A"].mine_step(1, job["job_id"])
        data = net.to_dict()
        data["nodes"]["A"]["candidate"]["block"]["nonce"] = 999
        with self.assertRaises(ValueError):
            Network.from_dict(data, restore_jobs=True)


if __name__ == "__main__":
    unittest.main()
