"""거래 검증 판정과 교육용 설명"""

from dataclasses import replace

import pytest

from mini_bitcoin_backend.core.crypto import hash_text, sign_transaction
from mini_bitcoin_backend.core.lab import Lab
from mini_bitcoin_backend.core.transaction import (
    build_transaction,
    reserved_inputs,
    validate_transaction,
)
from mini_bitcoin_backend.core.types import Check, TxOutput

SUCCESS_MESSAGES = [
    "입력·출력, 키·서명 형식과 양의 정수 금액이 올바릅니다.",
    "거래의 실습 식별자가 현재 실습과 일치합니다.",
    "서명된 거래를 다시 해시한 값이 거래 ID와 일치합니다.",
    "공개키로 검증한 서명이 거래 원문과 일치합니다.",
    "한 거래 안에서 같은 출력을 중복 사용하지 않습니다.",
    "모든 입력이 존재하는 확정 UTXO를 가리킵니다.",
    "다른 대기 거래나 같은 블록의 거래가 사용 중인 입력이 없습니다.",
    "모든 입력이 서명자의 공개키에 대응하는 주소 소유입니다.",
    "입력 합계 100 코인과 출력 합계 100 코인이 일치합니다. 수수료는 0입니다.",
]

# (code, label, message, 거래를 바꾸는 방법)
REJECTIONS = [
    (
        "FORMAT",
        "형식·금액",
        "입력·출력, 키·서명 형식 또는 금액이 올바르지 않습니다. "
        "금액은 1 이상의 안전한 정수여야 합니다.",
        "zero-amount",
    ),
    (
        "SESSION",
        "실습 식별자",
        "거래의 실습 식별자가 현재 실습과 일치하지 않습니다.",
        "another-lab",
    ),
    (
        "TX_ID",
        "거래 ID",
        "서명된 거래를 다시 해시한 값이 거래 ID와 일치하지 않습니다.",
        "wrong-id",
    ),
    (
        "SIGNATURE",
        "전자서명",
        "공개키로 검증한 서명이 거래 원문과 일치하지 않습니다.",
        "bob-signs",
    ),
    (
        "DUPLICATE_INPUT",
        "중복 입력",
        "같은 출력을 한 거래에서 두 번 사용하려고 합니다.",
        "duplicate-input",
    ),
    (
        "MISSING_UTXO",
        "미사용 출력",
        "입력이 가리키는 확정 UTXO가 없습니다. "
        "존재하지 않거나 이미 소비한 출력은 사용할 수 없습니다.",
        "empty-utxos",
    ),
    (
        "DOUBLE_SPEND",
        "이중 지출",
        "선택한 입력을 다른 대기 거래나 같은 블록의 거래가 이미 사용 중입니다.",
        "reserved",
    ),
    (
        "OWNER",
        "소유권",
        "서명자의 공개키에 대응하는 주소가 소유하지 않은 입력이 있습니다.",
        "bob-owner",
    ),
    (
        "VALUE",
        "금액 보존",
        "입력·출력 합계는 안전한 정수여야 하며, 수수료가 0이므로 두 합계가 같아야 합니다.",
        "too-much",
    ),
]


@pytest.fixture
def fixture(alice, bob):
    lab = Lab([alice, bob], 1, 1000, hash_text("feedback-test"))
    draft = build_transaction(alice, bob.address, 30, lab.nodes["A"].utxos, [], lab.session_id)
    return lab, sign_transaction(draft, alice.private_key)


def test_valid_transaction_explains_every_passed_step(fixture):
    """정상 거래의 모든 단계가 통과 사실을 설명한다"""
    lab, tx = fixture
    result = validate_transaction(tx, lab.nodes["A"].utxos, lab.session_id)
    assert result.valid is True
    assert all(step.ok for step in result.steps)
    assert [step.message for step in result.steps] == SUCCESS_MESSAGES


@pytest.mark.parametrize(
    ("code", "label", "message", "case"), REJECTIONS, ids=[r[0] for r in REJECTIONS]
)
def test_failure_explanation_matches_failed_step(fixture, alice, bob, code, label, message, case):
    """실패 설명을 실제 실패 단계와 연결한다"""
    lab, tx = fixture
    utxos = lab.nodes["A"].utxos
    reserved: set[str] = set()
    value = tx
    if case == "zero-amount":
        value = replace(tx, outputs=(TxOutput(bob.address, 0),))
    elif case == "another-lab":
        value = sign_transaction(
            replace(tx, session_id=hash_text("another-lab")), alice.private_key
        )
    elif case == "wrong-id":
        value = replace(tx, id=hash_text("wrong-id"))
    elif case == "bob-signs":
        value = sign_transaction(tx, bob.private_key)
    elif case == "duplicate-input":
        value = sign_transaction(replace(tx, inputs=tx.inputs * 2), alice.private_key)
    elif case == "empty-utxos":
        utxos = {}
    elif case == "reserved":
        reserved = reserved_inputs([tx])
    elif case == "bob-owner":
        value = sign_transaction(replace(tx, public_key=bob.public_key), bob.private_key)
    elif case == "too-much":
        value = sign_transaction(
            replace(tx, outputs=(TxOutput(bob.address, 101),)), alice.private_key
        )

    result = validate_transaction(value, utxos, lab.session_id, reserved)
    assert result.valid is False
    assert result.code == code
    assert result.message == message
    assert result.steps[-1] == Check(label=label, ok=False, message=message)
    passed = result.steps[:-1]
    assert all(step.ok for step in passed)
    assert [step.message for step in passed] == SUCCESS_MESSAGES[: len(passed)]
