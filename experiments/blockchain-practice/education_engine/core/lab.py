"""세 개의 독립 노드와 그 사이의 메시지 대기열로 이루어진 모의 네트워크."""

import uuid
from collections.abc import Sequence
from typing import Any

from .block import create_candidate, create_genesis, genesis_utxos, parse_block, validate_block
from .crypto import create_wallet, hash_text
from .encoding import canonical_json, now_ms
from .errors import LabError
from .rewards import SUBSIDY, issuance
from .transaction import (
    apply_transaction,
    outpoint,
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
    TxOutput,
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
        team_allocations: bool = False,
        team_initial_balance: int = 100,
    ) -> None:
        if len(wallets) < 2:
            raise LabError("NOT_ENOUGH_WALLETS", "실습을 시작하려면 지갑 두 개가 필요합니다.")
        if type(team_initial_balance) is not int or not 1 <= team_initial_balance <= 10000:
            raise LabError("INVALID_INITIAL_BALANCE", "초기 코인은 1~10000 사이의 정수로 입력하세요")
        self.session_id = session_id or hash_text(str(uuid.uuid4()))
        self.wallets: list[Wallet] = list(wallets)
        self.genesis = create_genesis(
            self.session_id,
            wallets[0].address,
            difficulty,
            now_ms() if timestamp is None else timestamp,
            tuple(TxOutput(w.address, team_initial_balance) for w in wallets) if team_allocations else None,
        )
        # 세 노드는 같은 Genesis에서 시작하지만 체인·UTXO·Mempool을 각자 따로 가진다.
        self.nodes: dict[NodeId, NodeState] = {
            node_id: NodeState(id=node_id, chain=[self.genesis], utxos=genesis_utxos(self.genesis))
            for node_id in NODE_IDS
        }
        self.miners = {n: create_wallet(f"채굴자 {n}") for n in NODE_IDS}
        for node in self.nodes.values():
            node.blocks[self.genesis.hash] = self.genesis
        self.queue: list[NetworkMessage] = []
        self.events: list[LabEvent] = []
        self.last_validation: ValidationResult | None = None
        self.last_delivery: Delivery | None = None
        # 메시지와 이벤트가 하나의 순번을 함께 사용한다.
        self._sequence = 0
        self.log(
            "LAB",
            "초기화",
            (
                f"독립 노드 A·B·C 생성. 각 팀에 학습용 {team_initial_balance} 코인 배정."
                if team_allocations
                else (
                    "독립 노드 A·B·C 생성. 동일 Genesis에서 "
                    "Alice에게 학습용 100 코인을 배정했습니다."
                )
            ),
            True,
        )

    def _next_id(self) -> int:
        self._sequence += 1
        return self._sequence

    def log(self, node: str, kind: str, message: str, ok: bool) -> None:
        self.events.append(LabEvent(self._next_id(), node, kind, message, ok))
        if sink := getattr(self, "_event_sink", None):
            sink(self.events[-1])
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
        v = validate_transaction(
            value,
            node.utxos,
            self.session_id,
            reserved_inputs(node.mempool),
            node.chain[-1].height + 1,
        )
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

    def branch(self, node: NodeState, tip: Block) -> list[Block]:
        chain = [tip]
        while chain[-1].height:
            chain.append(node.blocks[chain[-1].previous_hash])
        return list(reversed(chain))

    def branch_utxos(self, chain: list[Block]):
        utxos = genesis_utxos(self.genesis)
        for block in chain[1:]:
            for tx in block.transactions:
                apply_transaction(tx, utxos, block.height)
        return utxos

    @staticmethod
    def work(chain: list[Block]) -> int:
        # 고정 target: floor(2**256 / (target + 1)) = 16**difficulty.
        return sum(16**block.difficulty for block in chain[1:])

    def receive_block(self, node_id: NodeId, value: Any) -> ValidationResult:
        node = self.nodes[node_id]
        block = parse_block(value)
        if block is None:
            return self._finish(
                node_id, result(False, "BLOCK_FORMAT", "올바른 블록 형식이 아닙니다.")
            )
        if block.hash in node.blocks:
            # 동일 해시를 주장하는 변조된 내용까지 중복으로 승인하지 않는다.
            if block != node.blocks[block.hash]:
                return self._finish(
                    node_id, result(False, "BLOCK_HASH", "같은 해시의 내용이 다릅니다.")
                )
            return self._finish(node_id, result(True, "DUPLICATE", "이미 검증한 블록입니다."))
        if block.previous_hash not in node.blocks:
            node.pending_blocks[block.hash] = block
            return self._finish(
                node_id, result(False, "PARENT_PENDING", "부모 블록을 기다린 뒤 검증합니다.")
            )
        parent = node.blocks[block.previous_hash]
        branch = self.branch(node, parent)
        updated = self.branch_utxos(branch)
        v = validate_block(block, parent, updated)
        if not v.valid:
            return self._finish(node_id, v)
        node.blocks[block.hash] = block
        branch.append(block)
        if self.work(branch) > self.work(node.chain):
            old_chain = node.chain
            pool = list(node.mempool)
            active_hashes = {b.hash for b in branch}
            for old in old_chain[1:]:
                if old.hash not in active_hashes:
                    pool.extend(tx for tx in old.transactions if tx.kind == "regular")
            for tx in block.transactions:
                apply_transaction(tx, updated, block.height)
            node.chain = branch
            node.utxos = updated
            included = {tx.id for b in branch for tx in b.transactions}
            remaining = []
            handled = set(included)
            for tx in pool:
                if (
                    tx.id not in handled
                    and validate_transaction(
                        tx, updated, self.session_id, reserved_inputs(remaining), block.height + 1
                    ).valid
                ):
                    remaining.append(tx)
                handled.add(tx.id)
            node.mempool = remaining
            # 분리되었던 거래의 재전파를 영구적으로 막지 않는다.
            node.seen = {_fingerprint("tx", tx) for tx in remaining}
            if old_chain[-1].hash != block.previous_hash:
                self.log(
                    node_id,
                    "체인 재구성",
                    "누적 작업량이 더 큰 분기로 변경 · UTXO·멤풀·보상을 다시 계산했습니다.",
                    True,
                )
        else:
            self.log(
                node_id,
                "분기 보관",
                "유효한 경쟁 블록입니다. 작업량 동률이면 현재 체인을 유지합니다.",
                True,
            )
        for pending in list(node.pending_blocks.values()):
            if pending.previous_hash in node.blocks:
                del node.pending_blocks[pending.hash]
                self.receive_block(node_id, pending)
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

    def candidate(
        self, node_id: NodeId, timestamp: int | None = None, extra_nonce: str | None = None
    ) -> Block:
        node = self.nodes[node_id]
        fees = sum(
            sum(node.utxos[outpoint(i)].amount for i in tx.inputs)
            - sum(o.amount for o in tx.outputs)
            for tx in node.mempool
        )
        reward = issuance(
            self.session_id,
            node.chain[-1].height + 1,
            self.miners[node_id].address,
            SUBSIDY + fees,
            extra_nonce or str(uuid.uuid4()),
        )
        return create_candidate(node.chain[-1], [reward, *node.mempool], timestamp)

    def accept_mined(self, node_id: NodeId, block: Block) -> ValidationResult:
        v = self.receive_block(node_id, block)
        if v.valid and v.code != "DUPLICATE":
            self._broadcast(node_id, "block", block)
        return v
