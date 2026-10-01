"""거래 작성, UTXO 선택과 거래 검증."""

import re
from collections.abc import Iterable
from typing import Any

from .crypto import address_of, is_address, is_hash, transaction_id, verify_signature
from .encoding import is_positive, is_safe_integer
from .errors import LabError
from .types import (
    UTXO,
    Check,
    Transaction,
    TxInput,
    TxOutput,
    UnsignedTransaction,
    UtxoSet,
    ValidationResult,
    Wallet,
)
from .wire import to_wire

_PUBLIC_KEY = re.compile(r"(02|03)[0-9a-f]{64}")
_SIGNATURE = re.compile(r"[0-9a-f]{128}")


def outpoint(i: TxInput | UTXO) -> str:
    return f"{i.tx_id}:{i.output_index}"


def _record(v: Any) -> bool:
    return isinstance(v, dict)


def parse_unsigned_transaction(value: Any) -> UnsignedTransaction | None:
    """JSON 형태의 값이 거래 형식에 맞으면 객체로 바꾸고, 아니면 None을 돌려준다."""
    if isinstance(value, UnsignedTransaction):
        value = to_wire(value)
    if (
        not _record(value)
        or type(value.get("version")) is not int
        or value["version"] != 1
        or not is_hash(value.get("sessionId"))
        or not isinstance(value.get("publicKey"), str)
        or _PUBLIC_KEY.fullmatch(value["publicKey"]) is None
    ):
        return None
    inputs, outputs = value.get("inputs"), value.get("outputs")
    if not isinstance(inputs, list) or not inputs or not isinstance(outputs, list) or not outputs:
        return None
    for i in inputs:
        if (
            not _record(i)
            or not is_hash(i.get("txId"))
            or not is_safe_integer(i.get("outputIndex"))
            or i["outputIndex"] < 0
        ):
            return None
    for o in outputs:
        if not _record(o) or not is_address(o.get("address")) or not is_positive(o.get("amount")):
            return None
    return UnsignedTransaction(
        version=1,
        session_id=value["sessionId"],
        inputs=tuple(TxInput(i["txId"], i["outputIndex"]) for i in inputs),
        outputs=tuple(TxOutput(o["address"], o["amount"]) for o in outputs),
        public_key=value["publicKey"],
    )


def parse_transaction(value: Any) -> Transaction | None:
    if isinstance(value, Transaction):
        value = to_wire(value)
    unsigned = parse_unsigned_transaction(value)
    if (
        unsigned is None
        or not is_hash(value.get("id"))
        or not isinstance(value.get("signature"), str)
        or _SIGNATURE.fullmatch(value["signature"]) is None
    ):
        return None
    return Transaction(
        version=unsigned.version,
        session_id=unsigned.session_id,
        inputs=unsigned.inputs,
        outputs=unsigned.outputs,
        public_key=unsigned.public_key,
        signature=value["signature"],
        id=value["id"],
    )


def result(valid: bool, code: str, message: str, steps: Iterable[Check] = ()) -> ValidationResult:
    return ValidationResult(valid, code, message, tuple(steps))


def validate_transaction(
    value: Any,
    utxos: UtxoSet,
    session_id: str,
    reserved: Iterable[str] = (),
) -> ValidationResult:
    reserved = set(reserved)
    steps: list[Check] = []

    def check(ok: bool, label: str, success: str, failure: str) -> bool:
        steps.append(Check(label, ok, success if ok else failure))
        return ok

    def fail(code: str) -> ValidationResult:
        return result(False, code, steps[-1].message, steps)

    tx = parse_transaction(value)
    if not check(
        tx is not None,
        "형식·금액",
        "입력·출력, 키·서명 형식과 양의 정수 금액이 올바릅니다.",
        "입력·출력, 키·서명 형식 또는 금액이 올바르지 않습니다. "
        "금액은 1 이상의 안전한 정수여야 합니다.",
    ):
        return fail("FORMAT")
    assert tx is not None
    if not check(
        tx.session_id == session_id,
        "실습 식별자",
        "거래의 실습 식별자가 현재 실습과 일치합니다.",
        "거래의 실습 식별자가 현재 실습과 일치하지 않습니다.",
    ):
        return fail("SESSION")
    if not check(
        tx.id == transaction_id(tx, tx.signature),
        "거래 ID",
        "서명된 거래를 다시 해시한 값이 거래 ID와 일치합니다.",
        "서명된 거래를 다시 해시한 값이 거래 ID와 일치하지 않습니다.",
    ):
        return fail("TX_ID")
    if not check(
        verify_signature(tx),
        "전자서명",
        "공개키로 검증한 서명이 거래 원문과 일치합니다.",
        "공개키로 검증한 서명이 거래 원문과 일치하지 않습니다.",
    ):
        return fail("SIGNATURE")
    keys = [outpoint(i) for i in tx.inputs]
    if not check(
        len(set(keys)) == len(keys),
        "중복 입력",
        "한 거래 안에서 같은 출력을 중복 사용하지 않습니다.",
        "같은 출력을 한 거래에서 두 번 사용하려고 합니다.",
    ):
        return fail("DUPLICATE_INPUT")
    if not check(
        all(k in utxos for k in keys),
        "미사용 출력",
        "모든 입력이 존재하는 확정 UTXO를 가리킵니다.",
        "입력이 가리키는 확정 UTXO가 없습니다. "
        "존재하지 않거나 이미 소비한 출력은 사용할 수 없습니다.",
    ):
        return fail("MISSING_UTXO")
    if not check(
        all(k not in reserved for k in keys),
        "이중 지출",
        "다른 대기 거래나 같은 블록의 거래가 사용 중인 입력이 없습니다.",
        "선택한 입력을 다른 대기 거래나 같은 블록의 거래가 이미 사용 중입니다.",
    ):
        return fail("DOUBLE_SPEND")
    owner = address_of(tx.public_key)
    if not check(
        all(utxos[k].address == owner for k in keys),
        "소유권",
        "모든 입력이 서명자의 공개키에 대응하는 주소 소유입니다.",
        "서명자의 공개키에 대응하는 주소가 소유하지 않은 입력이 있습니다.",
    ):
        return fail("OWNER")
    total_in = sum(utxos[k].amount for k in keys)
    total_out = sum(o.amount for o in tx.outputs)
    if not check(
        is_safe_integer(total_in) and is_safe_integer(total_out) and total_in == total_out,
        "금액 보존",
        f"입력 합계 {total_in} 코인과 출력 합계 {total_out} 코인이 일치합니다. 수수료는 0입니다.",
        "입력·출력 합계는 안전한 정수여야 하며, 수수료가 0이므로 두 합계가 같아야 합니다.",
    ):
        return fail("VALUE")
    return result(True, "VALID", "모든 검증 통과 · Mempool에 들어갈 수 있는 거래입니다.", steps)


def reserved_inputs(mempool: Iterable[Transaction]) -> set[str]:
    return {outpoint(i) for tx in mempool for i in tx.inputs}


def balance(utxos: UtxoSet, address: str, reserved: Iterable[str] = ()) -> int:
    reserved = set(reserved)
    return sum(u.amount for key, u in utxos.items() if u.address == address and key not in reserved)


def build_transaction(
    wallet: Wallet,
    recipient: Any,
    amount: Any,
    utxos: UtxoSet,
    mempool: Iterable[Transaction],
    session_id: str,
) -> UnsignedTransaction:
    if not is_address(recipient):
        raise LabError("INVALID_RECIPIENT", "올바른 교육용 수신 주소를 선택하세요.")
    if not is_positive(amount):
        raise LabError("INVALID_AMOUNT", "금액은 1 이상의 안전한 정수여야 합니다.")
    reserved = reserved_inputs(mempool)
    # 같은 조건이면 항상 같은 입력을 고르도록 outpoint 순서로 정렬한다.
    available = sorted(
        key for key, u in utxos.items() if u.address == wallet.address and key not in reserved
    )
    inputs: list[TxInput] = []
    total = 0
    for u in (utxos[key] for key in available):
        inputs.append(TxInput(u.tx_id, u.output_index))
        total += u.amount
        if total >= amount:
            break
    if total < amount:
        raise LabError(
            "INSUFFICIENT_FUNDS",
            f"송금 가능 잔액 부족: {total} 코인입니다. "
            "대기 거래가 예약한 UTXO와 미확정 거스름돈은 사용할 수 없습니다.",
        )
    outputs = [TxOutput(recipient, amount)]
    if total > amount:
        outputs.append(TxOutput(wallet.address, total - amount))
    return UnsignedTransaction(
        version=1,
        session_id=session_id,
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        public_key=wallet.public_key,
    )


def apply_transaction(tx: Transaction, utxos: UtxoSet) -> None:
    for i in tx.inputs:
        utxos.pop(outpoint(i), None)
    for index, o in enumerate(tx.outputs):
        utxos[f"{tx.id}:{index}"] = UTXO(tx.id, index, o.address, o.amount)
