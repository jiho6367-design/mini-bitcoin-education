import dataclasses
import json
import re
from dataclasses import replace

import pytest
from conftest import mined

from mini_bitcoin_backend.core.block import (
    block_hash,
    calculate_nonce,
    create_candidate,
    target_hex,
    transaction_digest,
    valid_pow,
    validate_block,
    validate_chain,
)
from mini_bitcoin_backend.core.crypto import (
    hash_text,
    sign_transaction,
    signing_text,
    transaction_id,
    verify_signature,
)
from mini_bitcoin_backend.core.encoding import MAX_SAFE_INTEGER
from mini_bitcoin_backend.core.errors import LabError
from mini_bitcoin_backend.core.lab import Lab
from mini_bitcoin_backend.core.transaction import (
    balance,
    build_transaction,
    reserved_inputs,
    validate_transaction,
)
from mini_bitcoin_backend.core.types import TxInput, TxOutput
from mini_bitcoin_backend.core.wire import to_wire


@pytest.fixture
def lab(alice, bob, carol):
    return Lab([alice, bob, carol], 1, 1000, hash_text("test-session"))


@pytest.fixture
def transfer(alice, bob):
    def make(lab: Lab, amount=30):
        draft = build_transaction(
            alice, bob.address, amount, lab.nodes["A"].utxos, [], lab.session_id
        )
        return sign_transaction(draft, alice.private_key)

    return make


def with_amount(tx, amount):
    return replace(tx, outputs=(replace(tx.outputs[0], amount=amount), *tx.outputs[1:]))


class TestKeysSignaturesSerialization:
    """키·서명·직렬화"""

    def test_fixed_keys_reproduce_text_and_verify(self, lab, transfer, alice):
        """고정 키와 원문을 재현하고 정상 서명을 검증한다"""
        tx = transfer(lab)
        assert re.fullmatch(r"mini_[a-f0-9]{64}", alice.address)
        assert hash_text(signing_text(tx)) == hash_text(signing_text(replace(tx)))
        assert verify_signature(tx) is True
        assert transaction_id(tx, tx.signature) == tx.id
        assert alice.private_key not in signing_text(tx)

    def test_rejects_other_key_and_tampering(self, lab, transfer, bob):
        """다른 개인키, 변조 원문, 변조 거래 ID를 거부한다"""
        tx = transfer(lab)
        utxos = lab.nodes["A"].utxos
        wrong = sign_transaction(tx, bob.private_key)
        assert validate_transaction(wrong, utxos, lab.session_id).code == "SIGNATURE"
        tampered = with_amount(tx, tx.outputs[0].amount + 1)
        assert verify_signature(tampered) is False
        assert validate_transaction(tampered, utxos, lab.session_id).code == "TX_ID"


class TestTransactionsAndValueConservation:
    """거래·금액 보존"""

    def test_100_input_becomes_30_and_70_change(self, lab, transfer):
        """100 입력을 30 수신·70 거스름돈으로 만든다"""
        tx = transfer(lab)
        assert [o.amount for o in tx.outputs] == [30, 70]
        assert validate_transaction(tx, lab.nodes["A"].utxos, lab.session_id).valid is True
        assert len(transfer(lab, 100).outputs) == 1

    @pytest.mark.parametrize(
        "amount", [0, -1, 1.5, float("nan"), float("inf"), MAX_SAFE_INTEGER + 1, 101]
    )
    def test_blocks_impossible_amounts(self, lab, transfer, amount):
        """송금 불가능 금액을 차단한다"""
        with pytest.raises(LabError):
            transfer(lab, amount)

    @pytest.mark.parametrize("amount", [True, "30", 30.0, None])
    def test_blocks_values_that_are_not_integers(self, lab, transfer, amount):
        with pytest.raises(LabError):
            transfer(lab, amount)

    def test_checks_are_independent(self, lab, transfer, alice, bob):
        """형식·다른 실습·입력 누락·중복·소유권·합계를 독립 검사한다"""
        base = transfer(lab)

        def check(tx):
            return validate_transaction(tx, lab.nodes["A"].utxos, lab.session_id).code

        def resign(tx):
            return sign_transaction(tx, alice.private_key)

        assert check(None) == "FORMAT"
        assert check(replace(base, outputs=(TxOutput(bob.address, -1),))) == "FORMAT"
        assert check(resign(replace(base, session_id=hash_text("elsewhere")))) == "SESSION"
        assert (
            check(resign(replace(base, inputs=(TxInput(hash_text("unknown"), 0),))))
            == "MISSING_UTXO"
        )
        assert check(resign(replace(base, inputs=base.inputs * 2))) == "DUPLICATE_INPUT"
        assert (
            check(sign_transaction(replace(base, public_key=bob.public_key), bob.private_key))
            == "OWNER"
        )
        assert check(resign(replace(base, outputs=(TxOutput(bob.address, 101),)))) == "VALUE"
        overflow = (TxOutput(bob.address, MAX_SAFE_INTEGER), TxOutput(bob.address, 1))
        assert check(resign(replace(base, outputs=overflow))) == "VALUE"

    def test_mempool_reserves_inputs_without_changing_balance(self, lab, transfer, alice):
        """Mempool은 확정 잔액을 변경하지 않고 입력을 예약한다"""
        tx = transfer(lab)
        lab.submit("A", tx)
        node = lab.nodes["A"]
        assert balance(node.utxos, alice.address) == 100
        assert balance(node.utxos, alice.address, reserved_inputs(node.mempool)) == 0
        assert lab.submit("A", tx).code == "DUPLICATE"
        assert lab.submit("A", transfer(lab, 20)).code == "DOUBLE_SPEND"
        assert len(node.mempool) == 1


class TestProofOfWorkBlocksAndChain:
    """작업증명·블록·체인"""

    def test_step_nonce_matches_real_hash_and_target(self, lab, transfer):
        """단계 Nonce가 실제 해시와 일치하고 난이도 목표값이 대응한다"""
        candidate = create_candidate(lab.genesis, [transfer(lab)], 1001)
        assert calculate_nonce(candidate, 0).hash == block_hash(candidate)
        assert calculate_nonce(candidate, 1).hash != calculate_nonce(candidate, 0).hash
        assert target_hex(2) == "00" + "f" * 62
        assert valid_pow("0" * 64, 0) is False
        with pytest.raises(LabError):
            calculate_nonce(candidate, -1)

    def test_validates_pow_link_difficulty_hashes_and_time(self, lab, transfer):
        """PoW, 연결, 난이도, 거래 해시, 헤더, 시각을 검증한다"""
        b = mined(create_candidate(lab.genesis, [transfer(lab)], 1001))

        def check(block):
            return validate_block(block, lab.genesis, lab.nodes["A"].utxos).code

        assert check(b) == "VALID"
        assert check(replace(b, previous_hash=hash_text("bad"))) == "LINK"
        assert check(replace(b, difficulty=2)) == "DIFFICULTY"
        assert check(replace(b, transactions_hash=hash_text("bad"))) == "TRANSACTIONS_HASH"
        assert check(replace(b, hash=hash_text("bad"))) == "BLOCK_HASH"
        assert check(replace(b, timestamp=999)) == "TIME"
        assert check(replace(b, allocations=())) == "BLOCK_FORMAT"
        invalid = calculate_nonce(b, 0)
        while valid_pow(invalid.hash, 1):
            invalid = calculate_nonce(b, invalid.nonce + 1)
        assert check(invalid) == "POW"

    def test_failed_later_transaction_applies_nothing(self, lab, transfer):
        """블록의 뒤쪽 거래가 실패하면 앞쪽 거래도 반영하지 않는다"""
        tx = transfer(lab)
        b = mined(create_candidate(lab.genesis, [tx, transfer(lab, 20)], 1001))
        before = dict(lab.nodes["A"].utxos)
        assert lab.receive_block("A", b).code == "TX_DOUBLE_SPEND"
        assert lab.nodes["A"].utxos == before
        assert len(lab.nodes["A"].chain) == 1

    def test_accepts_block_not_in_mempool_and_clears_conflicts(self, lab, transfer, bob):
        """Mempool에 없어도 블록을 검증하고 충돌 대기 거래를 정리한다"""
        tx = transfer(lab)
        lab.receive_transaction("B", transfer(lab, 20))
        block = mined(create_candidate(lab.genesis, [tx], 1001))
        assert lab.receive_block("B", block).valid is True
        assert lab.receive_block("C", block).valid is True
        assert len(lab.nodes["B"].mempool) == 0
        assert balance(lab.nodes["C"].utxos, bob.address) == 30
        assert lab.receive_transaction("C", tx).code == "MISSING_UTXO"

    def test_detects_genesis_and_past_transaction_tampering(self, lab, transfer, alice):
        """신뢰 Genesis 및 과거 거래 변조를 탐지한다"""
        lab.receive_block("A", mined(create_candidate(lab.genesis, [transfer(lab)], 1001)))
        chain = list(lab.nodes["A"].chain)
        assert validate_chain(chain, lab.genesis).valid is True
        tx = chain[1].transactions[0]
        chain[1] = replace(chain[1], transactions=(with_amount(tx, tx.outputs[0].amount + 1),))
        assert validate_chain(chain, lab.genesis).code == "TRANSACTIONS_HASH"
        chain[1] = mined(
            replace(chain[1], transactions_hash=transaction_digest(chain[1].transactions))
        )
        assert validate_chain(chain, lab.genesis).code == "TX_TX_ID"
        allocation = chain[0].allocations[0]
        chain[0] = replace(chain[0], allocations=(replace(allocation, amount=999),))
        assert validate_chain(chain, lab.genesis).code == "GENESIS"
        assert balance(lab.nodes["A"].utxos, alice.address) == 70


class TestIndependentNodesAndMessages:
    """독립 노드와 메시지"""

    def test_other_nodes_do_not_overwrite_failure_explanation(self, lab, transfer, bob):
        """다른 노드의 수신이 현재 노드의 실패 설명을 덮어쓰지 않는다"""
        lab.submit("A", transfer(lab))
        wrong = sign_transaction(transfer(lab), bob.private_key)
        lab.submit("A", wrong)
        lab.deliver_next()
        lab.deliver_next()
        assert lab.nodes["A"].last_validation.code == "SIGNATURE"
        assert lab.nodes["B"].last_validation.code == "VALID"

    def test_nodes_are_independent_until_delivery(self, lab, transfer, alice, bob):
        """전파 전 상태는 독립적이고 전달 후 70/30과 체인이 일치한다"""
        tx = transfer(lab)
        lab.submit("A", tx)
        assert len(lab.nodes["B"].mempool) == 0
        assert len(lab.queue) == 2
        # 거래 객체는 수정할 수 없으므로 제출 뒤 원본을 바꿔 노드 상태를 오염시킬 수 없다.
        with pytest.raises(dataclasses.FrozenInstanceError):
            tx.outputs[0].amount = 999
        assert lab.nodes["A"].mempool[0].outputs[0].amount == 30
        lab.deliver_next()
        lab.deliver_next()
        assert len(lab.nodes["B"].mempool) == 1
        block = mined(lab.candidate("A"))
        assert lab.accept_mined("A", block).valid is True
        assert len(lab.nodes["B"].chain) == 1
        with pytest.raises(LabError, match="전달"):
            lab.candidate("B")
        lab.deliver_next()
        lab.deliver_next()
        for node in lab.nodes.values():
            assert node.chain[-1].hash == block.hash
            assert balance(node.utxos, alice.address) == 70
            assert balance(node.utxos, bob.address) == 30
            assert len(node.mempool) == 0
            assert validate_chain(node.chain, lab.genesis).valid is True
        assert lab.receive_block("B", block).code == "DUPLICATE"
        changed = with_amount(block.transactions[0], 31)
        bad = replace(block, transactions=(changed,))
        assert lab.receive_block("B", bad).valid is False
        assert alice.private_key not in json.dumps(to_wire(lab.queue))
        assert alice.private_key not in json.dumps(to_wire(lab.events))
