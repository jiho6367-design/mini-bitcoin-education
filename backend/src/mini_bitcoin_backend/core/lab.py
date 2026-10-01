"""세 개의 독립 노드와 그 사이의 메시지 대기열로 이루어진 모의 네트워크."""

import uuid
from collections.abc import Sequence
from typing import Any

from .block import create_candidate, create_genesis, genesis_utxos, parse_block, validate_block
from .crypto import hash_text
from .encoding import canonical_json, now_ms
from .errors import LabError
from .transaction import (
    apply_transaction,
    parse_transaction,
    reserved_inputs,
    result,
    validate_transaction,
)
from .types import (
    NODE_IDS,
    Block,
    Delivery,
    LabEvent,
    MessageKind,
    NetworkMessage,
    NodeId,
    NodeState,
    Transaction,
    ValidationResult,
    Wallet,
)
from .wire import to_wire

MAX_EVENTS = 200


def _fingerprint(kind: str, payload: Transaction | Block | None) -> str | None:
    """페이로드 전체 내용의 지문. 같은 ID를 주장해도 내용이 다르면 다른 지문이 된다."""
    if payload is None:
        return None
    return f"{kind}:{hash_text(canonical_json(to_wire(payload)))}"


class Lab:
    def __init__(
        self,
        wallets: Sequence[Wallet],
        difficulty: int = 2,
        timestamp: int | None = None,
        session_id: str | None = None,
    ) -> None:
        if len(wallets) < 2:
            raise LabError("NOT_ENOUGH_WALLETS", "실습을 시작하려면 지갑 두 개가 필요합니다.")
        self.session_id = session_id or hash_text(str(uuid.uuid4()))
        self.wallets: list[Wallet] = list(wallets)
        self.genesis = create_genesis(
            self.session_id,
            wallets[0].address,
            difficulty,
            now_ms() if timestamp is None else timestamp,
        )
        # 세 노드는 같은 Genesis에서 시작하지만 체인·UTXO·Mempool을 각자 따로 가진다.
        self.nodes: dict[NodeId, NodeState] = {
            node_id: NodeState(id=node_id, chain=[self.genesis], utxos=genesis_utxos(self.genesis))
            for node_id in NODE_IDS
        }
        self.queue: list[NetworkMessage] = []
        self.events: list[LabEvent] = []
        self.last_validation: ValidationResult | None = None
        self.last_delivery: Delivery | None = None
        # 메시지와 이벤트가 하나의 순번을 함께 사용한다.
        self._sequence = 0
        self.log(
            "LAB",
            "초기화",
            "독립 노드 A·B·C 생성. 동일 Genesis에서 Alice에게 학습용 100 코인을 배정했습니다.",
            True,
        )

    def _next_id(self) -> int:
        self._sequence += 1
        return self._sequence

    def log(self, node: str, kind: str, message: str, ok: bool) -> None:
        self.events.append(LabEvent(self._next_id(), node, kind, message, ok))
        if len(self.events) > MAX_EVENTS:
            self.events.pop(0)

    def _broadcast(self, sender: NodeId, kind: MessageKind, payload: Transaction | Block) -> None:
        for to in NODE_IDS:
            if to != sender:
                self.queue.append(NetworkMessage(self._next_id(), sender, to, kind, payload))
        label = "거래" if kind == "transaction" else "블록"
        self.log(sender, "전파 대기", f"{label} 사본을 다른 두 노드로 보냅니다.", True)

    def receive_transaction(self, node_id: NodeId, value: Any) -> ValidationResult:
        node = self.nodes[node_id]
        tx = parse_transaction(value)
        fingerprint = _fingerprint("tx", tx)
        if fingerprint in node.seen:
            return self._finish(
                node_id,
                result(True, "DUPLICATE", "이미 처리한 동일 거래입니다. 중복 저장하지 않습니다."),
            )
        v = validate_transaction(value, node.utxos, self.session_id, reserved_inputs(node.mempool))
        if v.valid and tx is not None and fingerprint is not None:
            node.mempool.append(tx)
            node.seen.add(fingerprint)
        return self._finish(node_id, v)

    def submit(self, node_id: NodeId, value: Any) -> ValidationResult:
        v = self.receive_transaction(node_id, value)
        tx = parse_transaction(value)
        if v.valid and v.code != "DUPLICATE" and tx is not None:
            self._broadcast(node_id, "transaction", tx)
        return v

    def receive_block(self, node_id: NodeId, value: Any) -> ValidationResult:
        node = self.nodes[node_id]
        block = parse_block(value)
        fingerprint = _fingerprint("block", block)
        if fingerprint in node.seen:
            return self._finish(
                node_id,
                result(True, "DUPLICATE", "이미 처리한 동일 블록입니다. 중복 저장하지 않습니다."),
            )
        v = validate_block(value, node.chain[-1], node.utxos)
        if v.valid and block is not None and fingerprint is not None:
            # 모든 검증을 통과한 뒤에만 UTXO 사본에 반영한다. 일부만 반영되는 일은 없다.
            updated = dict(node.utxos)
            for tx in block.transactions:
                apply_transaction(tx, updated)
            node.chain.append(block)
            node.utxos = updated
            node.seen.add(fingerprint)
            # 새 UTXO 기준으로 남은 대기 거래를 다시 검증해 확정·충돌 거래를 제거한다.
            remaining: list[Transaction] = []
            for tx in node.mempool:
                if validate_transaction(
                    tx, updated, self.session_id, reserved_inputs(remaining)
                ).valid:
                    remaining.append(tx)
            node.mempool = remaining
        return self._finish(node_id, v)

    def _finish(self, node_id: NodeId, validation: ValidationResult) -> ValidationResult:
        self.last_validation = validation
        self.nodes[node_id].last_validation = validation
        self.log(
            node_id,
            "검증 승인" if validation.valid else "검증 거부",
            validation.message,
            validation.valid,
        )
        return validation

    def deliver_next(self) -> ValidationResult | None:
        if not self.queue:
            return None
        message = self.queue.pop(0)
        label = "거래" if message.kind == "transaction" else "블록"
        self.log(
            message.to,
            "수신",
            f"{message.sender} → {message.to} · {label} 수신 후 독립 검증",
            True,
        )
        validation = (
            self.receive_transaction(message.to, message.payload)
            if message.kind == "transaction"
            else self.receive_block(message.to, message.payload)
        )
        self.last_delivery = Delivery(message, validation)
        return validation

    def candidate(self, node_id: NodeId, timestamp: int | None = None) -> Block:
        if any(m.kind == "block" for m in self.queue):
            raise LabError("BLOCK_PENDING", "이전 블록의 전달을 먼저 완료하세요.", 409)
        if len({self.nodes[n].chain[-1].hash for n in NODE_IDS}) != 1:
            raise LabError(
                "CHAIN_MISMATCH", "노드의 체인이 일치한 뒤 다음 채굴을 시작할 수 있습니다.", 409
            )
        node = self.nodes[node_id]
        return create_candidate(node.chain[-1], node.mempool, timestamp)

    def accept_mined(self, node_id: NodeId, block: Block) -> ValidationResult:
        v = self.receive_block(node_id, block)
        if v.valid and v.code != "DUPLICATE":
            self._broadcast(node_id, "block", block)
        return v
