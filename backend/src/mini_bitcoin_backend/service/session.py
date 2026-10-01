"""브라우저 탭 하나가 진행하는 실습 하나."""

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from ..core import experiments
from ..core.crypto import create_wallet, hash_text, sign_transaction, signing_text
from ..core.errors import LabError
from ..core.lab import Lab
from ..core.mining import MiningJob, MiningMode
from ..core.transaction import (
    build_transaction,
    outpoint,
    parse_transaction,
    parse_unsigned_transaction,
)
from ..core.types import NodeId, Wallet
from ..core.wire import public_wallet, to_wire
from .snapshot import lab_snapshot, mining_wire


class LabSession:
    """실습의 모든 상태. 요청은 한 번에 하나씩 처리한다(lock)."""

    def __init__(self, lab_id: str, clock: Callable[[], float] = time.perf_counter) -> None:
        self.lab_id = lab_id
        self.wallets: list[Wallet] = []
        self.lab: Lab | None = None
        self.mining: MiningJob | None = None
        self.revision = 0
        self._clock = clock
        self._lock = threading.Lock()

    # ----- 상태 조회 -----

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> dict[str, Any]:
        return lab_snapshot(self.lab_id, self.revision, self.wallets, self.lab, self.mining)

    def _changed(self) -> dict[str, Any]:
        self.revision += 1
        return self._snapshot()

    def _running(self) -> Lab:
        if self.lab is None:
            raise LabError("LAB_NOT_STARTED", "실습을 먼저 시작하세요.", 409)
        return self.lab

    # ----- 지갑과 실습 시작 -----

    def add_wallet(self) -> dict[str, Any]:
        with self._lock:
            count = len(self.wallets)
            name = "Alice" if count == 0 else "Bob" if count == 1 else f"학생 {count + 1}"
            wallet = create_wallet(name)
            self.wallets.append(wallet)
            return {"wallet": public_wallet(wallet), "lab": self._changed()}

    def start(self, difficulty: Any, example: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.lab is not None:
                raise LabError("LAB_STARTED", "이미 시작한 실습입니다.", 409)
            wallets = [create_wallet("Alice"), create_wallet("Bob")] if example else self.wallets
            self.lab = Lab(wallets, difficulty)
            # 실습 중 추가한 지갑도 같은 목록에 쌓이도록 Lab의 목록을 함께 쓴다.
            self.wallets = self.lab.wallets
            return {"lab": self._changed()}

    def private_key(self, address: str) -> dict[str, str]:
        with self._lock:
            return {"privateKey": self._wallet(address=address).private_key}

    def _wallet(self, *, address: Any = None, public_key: Any = None) -> Wallet:
        for wallet in self.wallets:
            if wallet.address == address or wallet.public_key == public_key:
                return wallet
        raise LabError("UNKNOWN_WALLET", "이 실습에 없는 지갑입니다.")

    # ----- 거래 작성·서명·제출 -----

    def draft(
        self, node_id: NodeId, sender_address: Any, recipient_address: Any, amount: Any
    ) -> dict[str, Any]:
        with self._lock:
            lab = self._running()
            node = lab.nodes[node_id]
            draft = build_transaction(
                self._wallet(address=sender_address),
                recipient_address,
                amount,
                node.utxos,
                node.mempool,
                lab.session_id,
            )
            text = signing_text(draft)
            return {
                "draft": to_wire(draft),
                "inputs": to_wire([node.utxos[outpoint(i)] for i in draft.inputs]),
                "signingText": text,
                "signingHash": hash_text(text),
            }

    def sign(self, draft: Any) -> dict[str, Any]:
        with self._lock:
            self._running()
            unsigned = parse_unsigned_transaction(draft)
            if unsigned is None:
                raise LabError("INVALID_DRAFT", "서명할 거래 형식이 올바르지 않습니다.")
            wallet = self._wallet(public_key=unsigned.public_key)
            return {"signed": to_wire(sign_transaction(unsigned, wallet.private_key))}

    def submit(self, node_id: NodeId, transaction: Any) -> dict[str, Any]:
        with self._lock:
            validation = self._running().submit(node_id, transaction)
            return {"validation": to_wire(validation), "lab": self._changed()}

    def deliver_next(self) -> dict[str, Any]:
        with self._lock:
            validation = self._running().deliver_next()
            return {"validation": to_wire(validation), "lab": self._changed()}

    # ----- 채굴 -----

    def start_mining(self, node_id: NodeId, mode: MiningMode, step: bool) -> dict[str, Any]:
        with self._lock:
            lab = self._running()
            if self.mining is not None and self.mining.active:
                raise LabError("MINING_ACTIVE", "이미 진행 중인 채굴 작업이 있습니다.", 409)
            job = MiningJob(str(uuid.uuid4()), node_id, lab.candidate(node_id), mode, self._clock)
            self.mining = job
            job.start(step)
            return self._mining_result(job, was_found=False)

    def mining_command(self, job_id: str, command: str) -> dict[str, Any]:
        with self._lock:
            self._running()
            job = self.mining
            if job is None or job.job_id != job_id:
                # 중지·초기화 뒤에 늦게 도착한 요청은 새 작업에 영향을 주지 않는다.
                raise LabError("STALE_JOB", "이미 끝났거나 교체된 채굴 작업입니다.", 409)
            was_found = job.status == "found"
            getattr(job, command)()
            return self._mining_result(job, was_found)

    def _mining_result(self, job: MiningJob, was_found: bool) -> dict[str, Any]:
        lab = None
        if job.status == "found" and not was_found and job.latest is not None:
            # 채굴자가 찾은 블록도 노드의 일반 수신 경로로 다시 검증한 뒤 전파한다.
            validation = self._running().accept_mined(job.node, job.latest)
            if not validation.valid:
                job.fail(validation.message)
            lab = self._changed()
        return {"mining": mining_wire(job), "lab": lab}

    # ----- 실패 실험 -----

    def forged_signature(self, node_id: NodeId, transaction: Any) -> dict[str, Any]:
        with self._lock:
            lab = self._running()
            signed = parse_transaction(transaction)
            if signed is None:
                raise LabError("INVALID_TRANSACTION", "먼저 거래를 작성하고 서명하세요.")
            forged = experiments.forge_signature(signed)
            validation = lab.submit(node_id, forged)
            return {
                "checked": to_wire(forged),
                "validation": to_wire(validation),
                "lab": self._changed(),
            }

    def double_spend(self, node_id: NodeId) -> dict[str, Any]:
        with self._lock:
            conflict, inputs, validation = experiments.double_spend(self._running(), node_id)
            text = signing_text(conflict)
            return {
                "conflict": to_wire(conflict),
                "inputs": to_wire(inputs),
                "signingText": text,
                "signingHash": hash_text(text),
                "validation": to_wire(validation),
                "lab": self._changed(),
            }

    def invalid_pow_block(self, node_id: NodeId) -> dict[str, Any]:
        with self._lock:
            lab = self._running()
            if not lab.nodes[node_id].mempool:
                raise LabError("EMPTY_MEMPOOL", "이 실험에는 대기 중인 거래가 필요합니다.", 409)
            _, validation = experiments.invalid_pow_block(lab, node_id)
            return {"validation": to_wire(validation), "lab": self._changed()}

    def tamper(
        self, node_id: NodeId, height: Any, mode: Any, amount: Any, recompute_hashes: bool
    ) -> dict[str, Any]:
        with self._lock:
            lab = self._running()
            report = experiments.tamper(
                lab.nodes[node_id].chain, lab.genesis, height, mode, amount, recompute_hashes
            )
            return {
                "altered": to_wire(report.altered),
                "recomputedHash": report.recomputed_hash,
                "checks": [{"label": label, "ok": ok} for label, ok in report.checks],
                "chainValidation": to_wire(report.chain_validation),
                "hasNextBlock": report.has_next_block,
            }
