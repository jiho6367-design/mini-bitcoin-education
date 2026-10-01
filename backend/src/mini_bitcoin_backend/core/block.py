"""블록 헤더, 작업증명(PoW)과 블록·체인 검증."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from .crypto import hash_text, is_hash
from .encoding import canonical_json, is_safe_integer, now_ms
from .errors import LabError
from .transaction import (
    apply_transaction,
    outpoint,
    parse_transaction,
    result,
    validate_transaction,
)
from .types import UTXO, Block, Check, Transaction, TxOutput, UtxoSet, ValidationResult
from .wire import to_wire

GENESIS_AMOUNT = 100


def transaction_digest(transactions: Sequence[Transaction]) -> str:
    """거래 목록 해시. 거래 ID뿐 아니라 각 거래의 실제 필드와 서명을 모두 포함한다."""
    return hash_text(
        canonical_json(
            [
                [
                    t.id,
                    t.version,
                    t.session_id,
                    [[i.tx_id, i.output_index] for i in t.inputs],
                    [[o.address, o.amount] for o in t.outputs],
                    t.public_key,
                    t.signature,
                ]
                for t in transactions
            ]
        )
    )


def header_text(b: Block) -> str:
    return canonical_json(
        [
            b.session_id,
            b.height,
            b.previous_hash,
            b.transactions_hash,
            b.timestamp,
            b.difficulty,
            b.nonce,
        ]
    )


def block_hash(b: Block) -> str:
    return hash_text(header_text(b))


def valid_difficulty(d: Any) -> bool:
    return type(d) is int and 1 <= d <= 4


def target_hex(d: Any) -> str:
    if not valid_difficulty(d):
        raise LabError("INVALID_DIFFICULTY", "난이도 범위는 1~4입니다.")
    return "0" * d + "f" * (64 - d)


def valid_pow(block_hash_hex: Any, difficulty: Any) -> bool:
    # 소문자 16진수 64자리끼리의 문자열 비교는 256비트 정수 비교와 같다.
    return (
        valid_difficulty(difficulty)
        and is_hash(block_hash_hex)
        and block_hash_hex <= target_hex(difficulty)
    )


def create_genesis(session_id: str, address: str, difficulty: int, timestamp: int) -> Block:
    if not valid_difficulty(difficulty):
        raise LabError("INVALID_DIFFICULTY", "난이도 범위는 1~4입니다.")
    allocations = (TxOutput(address, GENESIS_AMOUNT),)
    genesis = Block(
        session_id=session_id,
        height=0,
        previous_hash="0" * 64,
        transactions_hash=hash_text(canonical_json(to_wire(allocations))),
        timestamp=timestamp,
        difficulty=difficulty,
        nonce=0,
        transactions=(),
        hash="",
        allocations=allocations,
    )
    return replace(genesis, hash=block_hash(genesis))


def genesis_utxos(genesis: Block) -> UtxoSet:
    return {
        f"{genesis.hash}:{index}": UTXO(genesis.hash, index, o.address, o.amount)
        for index, o in enumerate(genesis.allocations or ())
    }


def create_candidate(
    tip: Block, transactions: Sequence[Transaction], timestamp: int | None = None
) -> Block:
    if not transactions:
        raise LabError("EMPTY_MEMPOOL", "채굴할 유효 거래가 Mempool에 없습니다.", 409)
    return Block(
        session_id=tip.session_id,
        height=tip.height + 1,
        previous_hash=tip.hash,
        transactions_hash=transaction_digest(transactions),
        timestamp=max(tip.timestamp, now_ms() if timestamp is None else timestamp),
        difficulty=tip.difficulty,
        nonce=0,
        transactions=tuple(transactions),
        hash="",
    )


def calculate_nonce(candidate: Block, nonce: Any) -> Block:
    if not is_safe_integer(nonce) or nonce < 0:
        raise LabError("NONCE_RANGE", "Nonce 범위를 초과했습니다. 새 후보를 만드세요.")
    block = replace(candidate, nonce=nonce)
    return replace(block, hash=block_hash(block))


def parse_block(value: Any) -> Block | None:
    """JSON 형태의 값이 일반 블록 형식에 맞으면 객체로 바꾸고, 아니면 None을 돌려준다."""
    if isinstance(value, Block):
        value = to_wire(value)
    if (
        not isinstance(value, dict)
        or not is_hash(value.get("sessionId"))
        or not is_safe_integer(value.get("height"))
        or value["height"] <= 0
        or not is_hash(value.get("previousHash"))
        or not is_hash(value.get("transactionsHash"))
        or not is_safe_integer(value.get("timestamp"))
        or value["timestamp"] < 0
        or not valid_difficulty(value.get("difficulty"))
        or not is_safe_integer(value.get("nonce"))
        or value["nonce"] < 0
        or not is_hash(value.get("hash"))
        or "allocations" in value
        or not isinstance(value.get("transactions"), list)
        or not value["transactions"]
    ):
        return None
    transactions = [parse_transaction(t) for t in value["transactions"]]
    if any(t is None for t in transactions):
        return None
    return Block(
        session_id=value["sessionId"],
        height=value["height"],
        previous_hash=value["previousHash"],
        transactions_hash=value["transactionsHash"],
        timestamp=value["timestamp"],
        difficulty=value["difficulty"],
        nonce=value["nonce"],
        transactions=tuple(transactions),
        hash=value["hash"],
    )


def validate_block(value: Any, previous: Block, utxos: UtxoSet) -> ValidationResult:
    steps: list[Check] = []

    def check(ok: bool, label: str, message: str) -> bool:
        steps.append(Check(label, ok, message))
        return ok

    def fail(code: str) -> ValidationResult:
        return result(False, code, steps[-1].message, steps)

    b = parse_block(value)
    if not check(
        b is not None,
        "블록 형식",
        "일반 블록에는 올바른 헤더와 서명된 거래가 필요하며 초기 배정을 넣을 수 없습니다.",
    ):
        return fail("BLOCK_FORMAT")
    assert b is not None
    if not check(
        b.session_id == previous.session_id
        and b.height == previous.height + 1
        and b.previous_hash == previous.hash,
        "이전 블록 연결",
        "실습 식별자·높이·Previous Hash가 현재 체인과 일치해야 합니다.",
    ):
        return fail("LINK")
    if not check(
        b.timestamp >= previous.timestamp,
        "생성 시각",
        "이전 블록보다 이른 시각일 수 없습니다.",
    ):
        return fail("TIME")
    if not check(
        b.difficulty == previous.difficulty,
        "공통 난이도",
        "실습 시작 때 정한 난이도를 사용해야 합니다.",
    ):
        return fail("DIFFICULTY")
    if not check(
        b.transactions_hash == transaction_digest(b.transactions),
        "거래 목록 해시",
        "블록에 담긴 실제 거래 목록과 헤더의 해시가 일치해야 합니다.",
    ):
        return fail("TRANSACTIONS_HASH")
    if not check(
        b.hash == block_hash(b),
        "블록 해시",
        "헤더를 다시 계산한 해시가 저장된 해시와 일치해야 합니다.",
    ):
        return fail("BLOCK_HASH")
    if not check(
        valid_pow(b.hash, b.difficulty),
        "작업증명",
        "해시가 실습의 목표값 이하여야 합니다.",
    ):
        return fail("POW")
    # 같은 블록 안에서 아직 확정되지 않은 출력을 소비하지 못하게 원래 UTXO로 검증한다.
    used: set[str] = set()
    for tx in b.transactions:
        validation = validate_transaction(tx, utxos, b.session_id, used)
        if not validation.valid:
            return result(
                False,
                f"TX_{validation.code}",
                f"블록 내 거래 거부: {validation.message}",
                [*steps, *validation.steps],
            )
        used.update(outpoint(i) for i in tx.inputs)
    steps.append(
        Check("모든 거래", True, "서명·소유권·금액 보존·이중 지출 검증을 모두 통과했습니다.")
    )
    return result(True, "VALID", "블록 검증 통과 · 체인에 추가할 수 있습니다.", steps)


def validate_chain(chain: Sequence[Block], trusted_genesis: Block) -> ValidationResult:
    if not chain or chain[0] != trusted_genesis:
        return result(False, "GENESIS", "실습 시작 시 공유한 Genesis와 다릅니다.")
    utxos = genesis_utxos(trusted_genesis)
    for i in range(1, len(chain)):
        v = validate_block(chain[i], chain[i - 1], utxos)
        if not v.valid:
            return replace(v, message=f"블록 #{i} 검증 실패: {v.message}")
        for tx in chain[i].transactions:
            apply_transaction(tx, utxos)
    return result(True, "VALID", f"Genesis부터 블록 #{len(chain) - 1}까지 검증 통과")
