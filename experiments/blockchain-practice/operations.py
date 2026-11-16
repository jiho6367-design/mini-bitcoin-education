"""Practice isolation, atomic mutations and bounded, cancellable remine experiments."""
import copy
import hashlib
import json
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from block import Block
from node import Network
from storage import RevisionConflict, StorageError


class OperationError(Exception):
    def __init__(self, code, reason, status=400):
        super().__init__(reason)
        self.code, self.reason, self.status = code, reason, status


def chain_fingerprint(chain):
    raw = json.dumps([b.to_dict() for b in chain], sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class TamperJob:
    node_id: str
    block_index: int
    tx_index: int
    field_name: str
    value: object
    chain: list
    original_fingerprint: str
    job_id: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    attempts: int = 0
    current_index: int = 0
    prepared: bool = False
    status: str = "running"
    started: float = field(default_factory=time.monotonic)
    samples: deque = field(default_factory=lambda: deque(maxlen=12))

    def __post_init__(self):
        self.current_index = self.block_index
        setattr(self.chain[self.block_index].transactions[self.tx_index], self.field_name, self.value)

    def progress(self):
        block = self.chain[min(self.current_index, len(self.chain) - 1)]
        latest = self.samples[-1] if self.samples else None
        return {"job_id": self.job_id, "status": self.status, "node": self.node_id,
                "block_index": self.current_index, "start_block": self.block_index,
                "total_blocks": len(self.chain) - self.block_index,
                "completed_blocks": self.current_index - self.block_index,
                "attempts": self.attempts, "nonce": latest["nonce"] if latest else None,
                "hash": latest["hash"] if latest else None, "next_nonce": block.nonce,
                "elapsed_ms": round((time.monotonic() - self.started) * 1000, 2),
                "samples": list(self.samples), "original_unchanged": self.status != "completed"}

    def step(self, batch):
        for _ in range(batch):
            if self.current_index >= len(self.chain):
                break
            block = self.chain[self.current_index]
            if not self.prepared:
                if self.current_index > self.block_index:
                    block.previous_hash = self.chain[self.current_index - 1].hash
                block.merkle_root = block.calculate_merkle_root()
                block.nonce = 0
                self.prepared = True
            digest = hashlib.sha256((block.header_prefix() + str(block.nonce)).encode()).hexdigest()
            passed = digest.startswith("0" * block.difficulty)
            self.attempts += 1
            self.samples.append({"block_index": block.index, "nonce": block.nonce, "hash": digest, "passed": passed})
            block.hash = digest
            if passed:
                self.current_index += 1
                self.prepared = False
            else:
                block.nonce += 1
        return self.current_index >= len(self.chain)

    def to_dict(self):
        return {"version": 1, "node_id": self.node_id, "block_index": self.block_index,
                "tx_index": self.tx_index, "field_name": self.field_name, "value": self.value,
                "chain": [b.to_dict() for b in self.chain],
                "original_fingerprint": self.original_fingerprint, "job_id": self.job_id,
                "attempts": self.attempts, "current_index": self.current_index,
                "prepared": self.prepared, "status": self.status,
                "elapsed_ms": (time.monotonic() - self.started) * 1000,
                "samples": list(self.samples)}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("재채굴 실험 저장 형식 오류")
        if (not isinstance(data.get("node_id"), str)
                or data.get("field_name") not in ("amount", "receiver_address")
                or data.get("status") != "running"
                or not isinstance(data.get("chain"), list) or not data["chain"]
                or not isinstance(data.get("job_id"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", data["job_id"])
                or not isinstance(data.get("original_fingerprint"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", data["original_fingerprint"])):
            raise ValueError("재채굴 실험 저장 데이터 오류")
        blocks = [Block.from_dict(b) for b in data["chain"]]
        for key in ("block_index", "tx_index", "attempts", "current_index"):
            if type(data.get(key)) is not int or data[key] < 0:
                raise ValueError("재채굴 실험 순번 오류")
        bi, ti = data["block_index"], data["tx_index"]
        if not (bi < len(blocks) and ti < len(blocks[bi].transactions)
                and bi <= data["current_index"] < len(blocks)
                and type(data.get("prepared")) is bool):
            raise ValueError("재채굴 실험 진행 상태 오류")
        if (data["field_name"] == "amount" and type(data.get("value")) is not int
                or data["field_name"] == "receiver_address" and not isinstance(data.get("value"), str)):
            raise ValueError("재채굴 실험 변조 값 오류")
        samples = data.get("samples")
        if not isinstance(samples, list) or len(samples) > 12:
            raise ValueError("재채굴 실험 표본 오류")
        for sample in samples:
            if (not isinstance(sample, dict) or type(sample.get("nonce")) is not int
                    or type(sample.get("block_index")) is not int or type(sample.get("passed")) is not bool
                    or not isinstance(sample.get("hash"), str) or not re.fullmatch(r"[a-f0-9]{64}", sample["hash"])):
                raise ValueError("재채굴 실험 표본 형식 오류")
        elapsed = data.get("elapsed_ms", 0)
        if type(elapsed) not in (int, float) or not 0 <= elapsed < 10 ** 12:
            raise ValueError("재채굴 실험 시간 형식 오류")
        job = cls(data["node_id"], bi, ti, data["field_name"], data["value"], blocks,
                  data["original_fingerprint"], job_id=data["job_id"])
        job.attempts, job.current_index, job.prepared = data["attempts"], data["current_index"], data["prepared"]
        job.started = time.monotonic() - elapsed / 1000
        job.samples = deque(samples, maxlen=12)
        return job


@dataclass
class Practice:
    id: str
    network: Network
    revision: int
    saved_at: str
    tamper_job: TamperJob | None = None
    recovery_notice: str = ""
    job_cancelled_on_restore: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)

    def metadata(self):
        return {"id": self.id, "revision": self.revision, "saved_at": self.saved_at,
                "persistent": True, "recovery_notice": self.recovery_notice,
                "job_cancelled_on_restore": self.job_cancelled_on_restore}

    def payload(self):
        return {"format": 1, "network": self.network.to_dict(),
                "tamper_pending": self.tamper_job.to_dict() if self.tamper_job else None}


class PracticeManager:
    def __init__(self, store):
        self.store = store
        self.practices = {}
        self.lock = threading.RLock()

    def create(self):
        with self.lock:
            p = Practice(secrets.token_urlsafe(32), Network(), 0, "")
            p.saved_at = self.store.create(p.id, p.payload())
            self.practices[p.id] = p
            return p

    def _restore(self, record, *, restore_jobs=False):
        payload = record["payload"]
        if not isinstance(payload, dict) or payload.get("format") != 1:
            raise StorageError("지원하지 않는 실습 데이터입니다. 원본은 변경하지 않았습니다.")
        try:
            network = Network.from_dict(payload["network"], restore_jobs=restore_jobs)
        except (ValueError, KeyError, TypeError, IndexError) as error:
            raise StorageError("저장된 실습을 복원하지 못했습니다. 원본은 변경하지 않았습니다.") from error
        p = Practice(record["id"], network, record["revision"], record["saved_at"])
        if payload.get("tamper_pending"):
            if restore_jobs:
                try:
                    job = TamperJob.from_dict(payload["tamper_pending"])
                    if (job.node_id not in network.nodes
                            or chain_fingerprint(network.nodes[job.node_id].blockchain.chain) != job.original_fingerprint):
                        raise ValueError("재채굴 원본 체인이 다릅니다")
                    p.tamper_job = job
                except (ValueError, TypeError, KeyError, IndexError) as error:
                    raise StorageError("저장된 재채굴 실험을 복원하지 못했습니다. 원본은 변경하지 않았습니다.") from error
            else:
                p.job_cancelled_on_restore = True
                p.recovery_notice = "서버 재시작으로 미완료 재채굴 실험을 취소했습니다. 원본 체인은 유지됐습니다."
        if getattr(network, "restored_mining_cancelled", False):
            p.job_cancelled_on_restore = True
            p.recovery_notice = "저장된 실습을 복원했습니다. 진행 중이던 채굴은 중지됐으며 새로 시작할 수 있습니다."
        return p

    def get(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            return self.create()
        with self.lock:
            if token not in self.practices:
                for _ in range(3):
                    record = self.store.read(token)
                    if record is None:
                        return self.create()
                    restored = self._restore(record)
                    if restored.job_cancelled_on_restore:
                        try:
                            restored.saved_at = self.store.save(token, restored.revision, restored.payload())
                            restored.revision += 1
                        except RevisionConflict:
                            continue
                    self.practices[token] = restored
                    return restored
                else:
                    raise OperationError("STALE_REVISION", "다른 서버 요청에서 실습을 사용 중입니다. 잠시 후 상태를 다시 확인하세요.", 409)
            practice = self.practices[token]
        # Never wait for a practice lock while holding the manager lock: creating a
        # new practice from an existing one takes those locks in the opposite order.
        self.refresh(practice)
        return practice

    @staticmethod
    def _adopt(practice, restored):
        practice.network, practice.tamper_job = restored.network, restored.tamper_job
        practice.revision, practice.saved_at = restored.revision, restored.saved_at
        practice.recovery_notice = restored.recovery_notice or "다른 요청의 최신 상태를 다시 불러왔습니다."
        practice.job_cancelled_on_restore = restored.job_cancelled_on_restore

    def refresh(self, practice):
        with practice.lock:
            latest = self.store.read(practice.id)
            if latest is None:
                raise StorageError("실습 저장 기록을 찾을 수 없습니다. 현재 파일을 확인하세요.")
            if latest["revision"] != practice.revision:
                self._adopt(practice, self._restore(latest, restore_jobs=True))

    def mutate(self, practice, expected_revision, action, *, allow_tamper=False):
        with practice.lock:
            self.refresh(practice)
            if expected_revision is not None and expected_revision != practice.revision:
                raise OperationError("STALE_REVISION", "다른 요청에서 실습이 변경됐습니다. 현재 상태를 확인한 뒤 다시 실행하세요.", 409)
            if practice.tamper_job and not allow_tamper:
                raise OperationError("TAMPER_BUSY", "재채굴 실험을 완료하거나 취소한 뒤 다른 작업을 실행하세요.", 409)
            before_network = copy.deepcopy(practice.network)
            before_job = copy.deepcopy(practice.tamper_job)
            before_notice = practice.recovery_notice
            before_cancelled = practice.job_cancelled_on_restore
            try:
                result = action(practice)
                saved_at = self.store.save(practice.id, practice.revision, practice.payload())
            except Exception as error:
                practice.network, practice.tamper_job = before_network, before_job
                practice.recovery_notice, practice.job_cancelled_on_restore = before_notice, before_cancelled
                if isinstance(error, RevisionConflict):
                    latest = self.store.read(practice.id)
                    if latest is None:
                        raise StorageError("다른 작업에서 실습 기록이 변경됐습니다. 원본을 확인하세요.") from error
                    restored = self._restore(latest, restore_jobs=True)
                    self._adopt(practice, restored)
                    raise OperationError("STALE_REVISION", str(error), 409) from error
                raise
            practice.revision += 1
            practice.saved_at = saved_at
            return result
