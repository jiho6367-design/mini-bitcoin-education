"""python -m unittest discover -s tests -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blockchain import BLOCK_REWARD, FAUCET_AMOUNT, Blockchain
from mempool import Mempool
from miner import mine
from transaction import FAUCET, TRANSFER, Transaction
from wallet import Wallet


def setup(difficulty=2):
    chain = Blockchain(difficulty=difficulty)
    return chain, Mempool(chain), Wallet("Alice"), Wallet("Bob"), Wallet("Miner")


def fund(pool, w):
    assert pool.add(Transaction(FAUCET, "", w.address, FAUCET_AMOUNT))[0]


class TestWallet(unittest.TestCase):
    def test_keys_and_restore(self):
        w = Wallet("a")
        self.assertEqual(len(w.private_key), 64)
        self.assertEqual(Wallet("b", w.private_key).address, w.address)


class TestTransaction(unittest.TestCase):
    def test_sign_verify_and_tamper(self):
        a, b = Wallet(), Wallet()
        tx = Transaction(TRANSFER, a.public_key, b.address, 10, 0)
        self.assertFalse(tx.basic_check()[0])               # 서명 전
        tx.sign(a)
        self.assertTrue(tx.basic_check()[0])                # 서명 후
        for field, val in [("amount", 1000), ("receiver_address", "f" * 64), ("nonce", 5)]:
            t = tx.copy(); setattr(t, field, val)
            self.assertFalse(t.verify_signature(), field)

    def test_forged_and_malformed(self):
        a, m, b = Wallet(), Wallet(), Wallet()
        tx = Transaction(TRANSFER, a.public_key, b.address, 10, 0); tx.sign(m)
        self.assertFalse(tx.basic_check()[0])               # 남의 키로 서명
        tx.sender_public_key = "aa" * 64
        self.assertFalse(tx.basic_check()[0])               # 깨진 공개키도 예외 없이 거부


class TestMempool(unittest.TestCase):
    def test_rules(self):
        chain, pool, a, b, m = setup()
        fund(pool, a)
        self.assertFalse(pool.add(Transaction(FAUCET, "", a.address, FAUCET_AMOUNT, 0, 1.0))[0])   # faucet 중복
        def send(amount, nonce):
            tx = Transaction(TRANSFER, a.public_key, b.address, amount, nonce); tx.sign(a)
            return pool.add(tx)
        self.assertIn("잔액 부족", send(9999, 0)[1])
        self.assertFalse(send(-5, 0)[0])
        self.assertTrue(send(30, 0)[0])
        self.assertFalse(send(30, 1)[0])                    # Mempool 반영 후 잔액 20 -> 이중 지불 차단


class TestMining(unittest.TestCase):
    def test_flow_balances_and_link(self):
        chain, pool, a, b, m = setup()
        fund(pool, a)
        blk1, attempts, _ = mine(chain, pool, m.address)
        self.assertTrue(blk1.hash.startswith("00"))
        self.assertEqual(blk1.previous_hash, chain.chain[0].hash)
        self.assertEqual(len(pool.txs), 0)                  # 블록에 담긴 거래는 Mempool에서 제거
        tx = Transaction(TRANSFER, a.public_key, b.address, 20, 0); tx.sign(a); pool.add(tx)
        blk2, _, _ = mine(chain, pool, m.address)
        self.assertEqual(blk2.previous_hash, blk1.hash)
        st = chain.state()
        self.assertEqual((st.balance(a.address), st.balance(b.address), st.balance(m.address)),
                         (30, 20, 2 * BLOCK_REWARD))

    def test_difficulty(self):
        for d in (1, 3):
            chain, pool, a, b, m = setup(d)
            blk, _, _ = mine(chain, pool, m.address)
            self.assertTrue(blk.hash.startswith("0" * d))


class TestChainValidation(unittest.TestCase):
    def build(self):
        chain, pool, a, b, m = setup()
        fund(pool, a); mine(chain, pool, m.address)
        tx = Transaction(TRANSFER, a.public_key, b.address, 10, 0); tx.sign(a); pool.add(tx)
        mine(chain, pool, m.address)
        return chain

    def test_valid(self):
        self.assertTrue(self.build().is_valid())

    def test_amount_tamper_then_rehash_then_remine(self):
        chain = self.build()
        chain.chain[2].transactions[1].amount = 1000
        ok, res = chain.validate_chain()
        self.assertFalse(ok); self.assertFalse(res[2]["checks"]["merkle"])     # 1) 내용만 변조
        blk = chain.chain[2]
        blk.merkle_root = blk.calculate_merkle_root(); blk.hash = blk.calculate_hash()
        self.assertFalse(chain.is_valid())                                     # 2) 해시 재계산 -> PoW/서명에서 걸림
        blk.mine_to_target()
        ok, res = chain.validate_chain()
        self.assertFalse(ok)                                                   # 3) PoW까지 재수행해도 서명은 위조 불가
        self.assertTrue(res[2]["checks"]["pow"]); self.assertFalse(res[2]["checks"]["txs"])

    def test_genesis_and_link_tamper(self):
        chain = self.build(); chain.chain[0].nonce += 1
        self.assertFalse(chain.validate_chain()[1][0]["valid"])
        chain = self.build(); chain.chain[2].previous_hash = "0" * 64
        self.assertFalse(chain.validate_chain()[1][2]["checks"]["link"])


if __name__ == "__main__":
    unittest.main()
