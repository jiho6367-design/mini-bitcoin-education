"""일부러 잘못된 거래·블록을 만들어 노드가 거부하는 이유를 보여 주는 실패 실험."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from .block import (
    block_hash,
    calculate_nonce,
    create_candidate,
    transaction_digest,
    valid_pow,
    validate_chain,
)
from .crypto import sign_transaction, transaction_id, verify_signature
from .encoding import is_positive
from .errors import LabError
from .lab import Lab
from .transaction import outpoint
from .types import UTXO, Block, NodeId, Transaction, TxOutput, ValidationResult

TamperMode = Literal["transaction", "header"]


def forge_signature(tx: Transaction) -> Transaction:
    """서명만 가짜 값으로 바꾼다. ID는 다시 계산해 서명 검사만 실패하게 한다."""
    signature = "ff" * 64
    return replace(tx, signature=signature, id=transaction_id(tx, signature))


def double_spend(lab: Lab, node_id: NodeId) -> tuple[Transaction, list[UTXO], ValidationResult]:
    """대기 중인 첫 거래와 같은 입력을 다른 수신자에게 보내는, 서명은 올바른 거래를 제출한다."""
    node = lab.nodes[node_id]
    if not node.mempool:
        raise LabError("EMPTY_MEMPOOL", "이중 지출 실험에는 대기 중인 거래가 필요합니다.", 409)
    original = node.mempool[0]
    first = original.outputs[0]
    one, two = lab.wallets[0].address, lab.wallets[1].address
    recipient = two if first.address == one else one
    owner = next((w for w in lab.wallets if w.public_key == original.public_key), None)
    if owner is None:
        raise LabError("UNKNOWN_WALLET", "대기 거래에 서명한 지갑을 찾을 수 없습니다.", 409)
    conflict = sign_transaction(
        replace(original, outputs=(TxOutput(recipient, first.amount), *original.outputs[1:])),
        owner.private_key,
    )
    validation = lab.submit(node_id, conflict)
    inputs = [node.utxos[outpoint(i)] for i in conflict.inputs if outpoint(i) in node.utxos]
    return conflict, inputs, validation


def invalid_pow_block(
    lab: Lab, node_id: NodeId, timestamp: int | None = None
) -> tuple[Block, ValidationResult]:
    """헤더와 해시는 올바르지만 목표값을 만족하지 못하는 블록을 노드에 직접 전달한다."""
    node = lab.nodes[node_id]
    bad = calculate_nonce(create_candidate(node.chain[-1], node.mempool, timestamp), 0)
    while valid_pow(bad.hash, bad.difficulty):
        bad = calculate_nonce(bad, bad.nonce + 1)
    return bad, lab.receive_block(node_id, bad)


@dataclass(frozen=True, slots=True)
class TamperReport:
    altered: Block
    recomputed_hash: str
    checks: tuple[tuple[str, bool], ...]
    chain_validation: ValidationResult
    has_next_block: bool


def tamper(
    chain: Sequence[Block],
    genesis: Block,
    height: Any,
    mode: TamperMode,
    amount: Any = None,
    recompute_hashes: bool = False,
) -> TamperReport:
    """체인의 복사본에서 과거 블록 하나를 바꾸고 어떤 관계가 깨지는지 검사한다.

    원본 체인은 바꾸지 않는다. recompute_hashes는 거래 목록 해시와 블록 해시만 다시
    계산하며, 작업증명(Nonce)과 다음 블록의 연결은 고치지 않는다.
    """
    if type(height) is not int or not 1 <= height < len(chain):
        raise LabError("INVALID_HEIGHT", "변조할 블록을 선택하세요. Genesis는 대상이 아닙니다.")
    copy = list(chain)
    block = copy[height]
    if mode == "transaction":
        if not is_positive(amount):
            raise LabError("INVALID_AMOUNT", "변경 금액은 1 이상의 안전한 정수여야 합니다.")
        tx = block.transactions[0]
        changed = replace(tx, outputs=(replace(tx.outputs[0], amount=amount), *tx.outputs[1:]))
        block = replace(block, transactions=(changed, *block.transactions[1:]))
    elif mode == "header":
        block = replace(block, timestamp=block.timestamp + 1)
    else:
        raise LabError("INVALID_MODE", "변경할 데이터를 선택하세요.")
    if recompute_hashes:
        block = replace(block, transactions_hash=transaction_digest(block.transactions))
        block = replace(block, hash=block_hash(block))
    copy[height] = block

    tx = block.transactions[0]
    recomputed = block_hash(block)
    checks: list[tuple[str, bool]] = [
        ("거래 전자서명", verify_signature(tx)),
        ("거래 ID 일치", tx.id == transaction_id(tx, tx.signature)),
        ("거래 목록 해시", block.transactions_hash == transaction_digest(block.transactions)),
        ("헤더와 블록 해시 일치", block.hash == recomputed),
        ("현재 헤더의 작업증명", valid_pow(recomputed, block.difficulty)),
    ]
    has_next = height + 1 < len(copy)
    if has_next:
        checks.append(
            ("다음 블록의 Previous Hash 연결", copy[height + 1].previous_hash == recomputed)
        )
    return TamperReport(block, recomputed, tuple(checks), validate_chain(copy, genesis), has_next)
