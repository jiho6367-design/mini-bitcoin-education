"""기능별 단위 테스트: python -m unittest discover -s tests -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from block import Block
from blockchain import BLOCK_REWARD, FAUCET_AMOUNT, Blockchain
from mempool import Mempool
from miner import mine as mine_block      # miner.mine() 직접 테스트용 (아래 helper mine()은 Node 채굴용)
from node import Network
from transaction import FAUCET, TRANSFER, Transaction
from wallet import Wallet


def faucet(net, node_id, wallet):
    ok, why = net.nodes[node_id].receive_transaction(Transaction(FAUCET, "", wallet.address, FAUCET_AMOUNT))
    assert ok, why


def transfer(net, node_id, src, dst, amount, nonce=None):
    node = net.nodes[node_id]
    if nonce is None:
        nonce = node.pending_state().nonces.get(src.address, 0)
    tx = Transaction(TRANSFER, src.public_key, dst.address, amount, nonce)
    tx.sign(src)
    return tx, node.receive_transaction(tx)


def mine(net, node_id, miner=None):
    node = net.nodes[node_id]
    node.start_mining(miner.address if miner else None)
    while True:
        r = node.mine_step(5000)
        if r["found"]:
            return r


class WalletTx(unittest.TestCase):
    def test_wallet_keys_and_restore(self):
        w = Wallet("a")
        self.assertEqual(len(w.private_key), 64)
        self.assertEqual(Wallet("b", w.private_key).address, w.address)

    def test_signature_detects_any_field_change(self):
        a, b = Wallet(), Wallet()
        tx = Transaction(TRANSFER, a.public_key, b.address, 10, 0)
        self.assertFalse(tx.basic_check()[0])          # 서명 전
        tx.sign(a)
        self.assertTrue(tx.basic_check()[0])
        for field, val in [("amount", 1000), ("receiver_address", "f" * 64), ("nonce", 5), ("timestamp", 1.0)]:
            t = tx.copy()
            setattr(t, field, val)
            self.assertFalse(t.verify_signature(), field)   # 이전 코드는 주소/서명 뒷부분 변조를 못 잡았음

    def test_forged_and_malformed(self):
        a, m, b = Wallet(), Wallet(), Wallet()
        tx = Transaction(TRANSFER, a.public_key, b.address, 10, 0)
        tx.sign(m)                                     # Mallory가 Alice 명의로 서명
        self.assertFalse(tx.basic_check()[0])
        tx.sender_public_key = "aa" * 64               # 깨진 공개키: 예외가 아니라 False
        self.assertFalse(tx.basic_check()[0])


class Rules(unittest.TestCase):
    def setUp(self):
        self.net = Network(difficulty=2)
        self.alice, self.bob = self.net.create_wallet("Alice"), self.net.create_wallet("Bob")

    def test_balance_and_amount_rules(self):
        faucet(self.net, "A", self.alice)
        _, r = transfer(self.net, "A", self.alice, self.bob, 9999)
        self.assertFalse(r[0]); self.assertIn("잔액 부족", r[1])
        _, r = transfer(self.net, "A", self.alice, self.bob, -50)
        self.assertFalse(r[0])
        _, r = transfer(self.net, "A", self.alice, self.bob, 30)
        self.assertTrue(r[0])
        _, r = transfer(self.net, "A", self.alice, self.bob, 30)   # Mempool 반영 후 잔액 20 -> 이중 지불 차단
        self.assertFalse(r[0])

    def test_faucet_once_and_replay(self):
        faucet(self.net, "A", self.alice)
        ok, _ = self.net.nodes["A"].receive_transaction(Transaction(FAUCET, "", self.alice.address, FAUCET_AMOUNT, 0, 1.0))
        self.assertFalse(ok)
        tx, r = transfer(self.net, "A", self.alice, self.bob, 10)
        self.assertTrue(r[0])
        mine(self.net, "A")
        ok, why = self.net.nodes["A"].receive_transaction(tx.copy())   # 같은 거래 재전송
        self.assertFalse(ok)

    def test_flow_balances(self):
        faucet(self.net, "A", self.alice)
        transfer(self.net, "A", self.alice, self.bob, 20)
        mine(self.net, "A", self.alice)                 # Alice가 채굴자: +10 보상
        st = self.net.nodes["A"].blockchain.state()
        self.assertEqual(st.balance(self.alice.address), 50 - 20 + BLOCK_REWARD)
        self.assertEqual(st.balance(self.bob.address), 20)


class Tamper(unittest.TestCase):
    def setUp(self):
        self.net = Network(difficulty=2)
        self.a, self.b = self.net.create_wallet("A"), self.net.create_wallet("B")
        faucet(self.net, "A", self.a)
        transfer(self.net, "A", self.a, self.b, 10)
        mine(self.net, "A")
        transfer(self.net, "A", self.a, self.b, 5)
        mine(self.net, "A")
        self.chain = self.net.nodes["A"].blockchain

    def test_untouched_valid(self):
        self.assertTrue(self.chain.is_valid())

    def test_amount_tamper_detected_at_every_level(self):
        tx = self.chain.chain[1].transactions[1]       # 블록1의 faucet 직후 transfer
        tx.amount = 1000
        ok, res = self.chain.validate_chain()
        self.assertFalse(ok); self.assertFalse(res[1]["checks"]["merkle"])
        blk = self.chain.chain[1]                      # 해시/머클 재계산(고급 해킹) -> PoW에서 걸림
        blk.merkle_root, blk.hash = blk.calculate_merkle_root(), blk.calculate_hash()
        ok, res = self.chain.validate_chain()
        self.assertFalse(res[1]["checks"]["pow"] and res[1]["checks"]["hash"] and res[1]["checks"]["txs"])

    def test_remine_all_still_fails_on_signature(self):
        tx = self.chain.chain[1].transactions[1]
        tx.amount = 1000
        for i in range(1, len(self.chain.chain)):      # 이후 모든 블록 재채굴
            blk = self.chain.chain[i]
            if i > 1:
                blk.previous_hash = self.chain.chain[i - 1].hash
            blk.merkle_root = blk.calculate_merkle_root()
            blk.mine_to_target()
        ok, res = self.chain.validate_chain()
        self.assertFalse(ok)                           # 해시/PoW/연결은 맞아도 서명을 위조할 수 없음
        self.assertTrue(res[1]["checks"]["pow"] and res[1]["checks"]["link"])
        self.assertFalse(res[1]["checks"]["txs"])

    def test_genesis_tamper(self):
        self.chain.chain[0].nonce += 1
        self.assertFalse(self.chain.validate_chain()[1][0]["valid"])

    def test_link_tamper(self):
        self.chain.chain[2].previous_hash = "0" * 64
        self.assertFalse(self.chain.validate_chain()[1][2]["checks"]["link"])   # 연결(Previous Hash) 끊김 탐지

    def test_signatureless_block_rejected(self):
        fake = Transaction(TRANSFER, self.a.public_key, self.b.address, 5, 99)
        cb = Transaction("coinbase", "", self.a.address, BLOCK_REWARD)
        blk = Block(len(self.chain.chain), self.chain.tip.hash, [cb, fake], 2)
        blk.mine_to_target()
        ok, why = self.chain.add_block(blk)
        self.assertFalse(ok); self.assertIn("서명", why)


class Miner(unittest.TestCase):
    """miner.mine()을 Node 없이 직접 테스트: 블록 연결, Mempool 비우기, 난이도."""
    def setUp(self):
        self.chain = Blockchain(difficulty=2)
        self.pool = Mempool(self.chain)
        self.a, self.b, self.m = Wallet("Alice"), Wallet("Bob"), Wallet("Miner")

    def test_block_link_and_mempool_cleared(self):
        self.assertTrue(self.pool.add(Transaction(FAUCET, "", self.a.address, FAUCET_AMOUNT))[0])
        blk1, _, _ = mine_block(self.chain, self.pool, self.m.address)
        self.assertTrue(blk1.hash.startswith("00"))
        self.assertEqual(blk1.previous_hash, self.chain.chain[0].hash)
        self.assertEqual(len(self.pool.txs), 0)                  # 블록에 담긴 거래는 Mempool에서 제거
        tx = Transaction(TRANSFER, self.a.public_key, self.b.address, 20, 0)
        tx.sign(self.a)
        self.pool.add(tx)
        blk2, _, _ = mine_block(self.chain, self.pool, self.m.address)
        self.assertEqual(blk2.previous_hash, blk1.hash)
        st = self.chain.state()
        self.assertEqual((st.balance(self.a.address), st.balance(self.b.address), st.balance(self.m.address)),
                         (30, 20, 2 * BLOCK_REWARD))

    def test_difficulty(self):
        for d in (1, 3):
            chain = Blockchain(difficulty=d)
            blk, _, _ = mine_block(chain, Mempool(chain), self.m.address)
            self.assertTrue(blk.hash.startswith("0" * d))


class Network3(unittest.TestCase):
    def setUp(self):
        self.net = Network(difficulty=2)
        self.a, self.b = self.net.create_wallet("A"), self.net.create_wallet("B")

    def test_tx_propagates_and_block_clears_mempool(self):
        faucet(self.net, "A", self.a)
        self.assertTrue(all(len(n.mempool) == 1 for n in self.net.nodes.values()))
        r = mine(self.net, "B")
        self.assertTrue(r["accepted"])
        for n in self.net.nodes.values():
            self.assertEqual(n.blockchain.tip.hash, r["hash"])
            self.assertEqual(len(n.mempool), 0)

    def test_nodes_do_not_share_objects(self):
        faucet(self.net, "A", self.a)
        mine(self.net, "A")
        self.net.nodes["B"].blockchain.chain[1].transactions[0].amount = 999
        self.assertEqual(self.net.nodes["A"].blockchain.chain[1].transactions[0].amount, BLOCK_REWARD)
        self.assertFalse(self.net.nodes["B"].blockchain.is_valid())
        self.assertTrue(self.net.nodes["A"].blockchain.is_valid())

    def test_offline_node_catches_up(self):
        self.net.nodes["C"].online = False
        faucet(self.net, "A", self.a)
        mine(self.net, "A"); mine(self.net, "A")
        self.assertEqual(self.net.nodes["C"].blockchain.tip.index, 0)
        self.net.nodes["C"].online = True
        self.assertTrue(self.net.nodes["C"].resolve_conflicts())
        self.assertEqual(self.net.nodes["C"].blockchain.tip.hash, self.net.nodes["A"].blockchain.tip.hash)

    def test_tampered_node_recovers_and_bad_chain_not_adopted(self):
        faucet(self.net, "A", self.a)
        mine(self.net, "A"); mine(self.net, "A")
        self.net.nodes["B"].blockchain.chain[1].transactions[0].amount = 999
        self.assertFalse(self.net.nodes["B"].blockchain.is_valid())
        self.net.nodes["A"].blockchain.chain[2].transactions[0].amount = 999   # A도 변조 -> B가 A를 채택하면 안 됨
        self.net.nodes["C"].resolve_conflicts()
        self.assertTrue(self.net.nodes["C"].blockchain.is_valid())
        self.net.nodes["A"].blockchain.chain[2].transactions[0].amount = BLOCK_REWARD   # A 복원
        self.assertTrue(self.net.nodes["B"].resolve_conflicts())                        # 깨진 B가 정상 체인으로 복구
        self.assertTrue(self.net.nodes["B"].blockchain.is_valid())
        self.assertEqual(self.net.nodes["B"].blockchain.tip.hash, self.net.nodes["C"].blockchain.tip.hash)

    def test_difficulty_enforced(self):
        self.net.set_difficulty(3)
        r = mine(self.net, "A")
        self.assertTrue(r["hash"].startswith("000"))
        with self.assertRaises(ValueError):
            self.net.set_difficulty(9)


if __name__ == "__main__":
    unittest.main()
