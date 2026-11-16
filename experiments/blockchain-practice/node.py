"""독립 메모리 노드, 작업 식별자, 누적 작업량 합의와 JSON 실습 저장."""
import copy
import hashlib
import json
import math
import time
import uuid

from block import Block
from blockchain import DEFAULT_DIFFICULTY, MAX_DIFFICULTY, MIN_DIFFICULTY, Blockchain, normalize_rules, valid_difficulty
from mempool import Mempool
from miner import build_candidate
from transaction import COINBASE, Transaction
from wallet import Wallet


def fingerprint(block):
    return hashlib.sha256(json.dumps(block.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def plain_copy(value):
    return copy.deepcopy(value)


class Node:
    def __init__(self, node_id, name, network, difficulty, wallet=None):
        self.id, self.name, self.network = node_id, name, network
        self.wallet = wallet if wallet is not None else Wallet(f"{name} 채굴자")
        self.blockchain = Blockchain(difficulty=difficulty, difficulty_rules=network.difficulty_rules)
        self.pool = Mempool(self.blockchain)
        self.peers, self.online = [], True
        self.candidate, self.last_mining = None, None
        self.seen_blocks, self._seen_payloads = set(), set()

    @property
    def mempool(self):
        return self.pool.txs

    def log(self, kind, message):
        self.network.log(self.id, kind, message)

    def pending_state(self):
        return self.pool.pending_state()

    def receive_transaction(self, tx, origin=None):
        if not self.online:
            return False, "노드 오프라인"
        ok, reason = self.pool.add(tx)
        if not ok:
            self.log("reject", f"거래 거부: {reason}")
            return False, reason
        short = f"{tx.sender_address[:8]}→{tx.receiver_address[:8]} {tx.amount}코인 (txid {tx.txid()[:8]})"
        self.log("tx", f"거래 검증 통과 → Mempool 등록: {short}")
        for peer_id in self.peers:
            if peer_id != origin:
                self.network.nodes[peer_id].receive_transaction(tx.copy(), origin=self.id)
        return True, "ok"

    def refresh_mempool(self):
        self.pool.refresh()

    def invalidate_validation_cache(self):
        """변조 실험처럼 원장을 직접 바꾸는 경로에서 이전 검증 기록을 폐기한다."""
        self.seen_blocks.clear()
        self._seen_payloads.clear()

    def candidate_info(self):
        if self.candidate is None:
            return plain_copy(self.last_mining)
        job, block = self.candidate, self.candidate["block"]
        return {"job_id": job["job_id"], "status": job["status"], "index": block.index,
                "previous_hash": block.previous_hash, "merkle_root": block.merkle_root,
                "difficulty": block.difficulty, "nonce": block.nonce, "hash": block.hash,
                "next_nonce": job["next_nonce"], "tx_count": len(block.transactions),
                "attempts": job["attempts"], "elapsed_ms": job["elapsed_ms"],
                "samples": plain_copy(job["samples"])}

    def start_mining(self, miner_address=None):
        address = miner_address or self.wallet.address
        if address not in self.network.wallets:
            raise ValueError("없는 채굴자 지갑")
        block = build_candidate(self.blockchain, self.pool, address)
        if self.candidate is not None:
            self.stop_mining(reason="replaced")
        self.candidate = {"block": block, "miner": address, "job_id": uuid.uuid4().hex,
                          "status": "running", "next_nonce": 0, "attempts": 0,
                          "elapsed_ms": 0.0, "samples": []}
        self.last_mining = None
        self.log("mine", f"후보 블록 #{block.index} 구성: 대기 거래 {len(block.transactions)-1}개 + coinbase")
        return self.candidate_info()

    def _job_error(self, job_id):
        current = self.candidate
        if job_id is not None and (current is None or job_id != current["job_id"]):
            return {"ok": False, "code": "STALE_JOB", "error": "이미 끝났거나 교체된 채굴 작업입니다",
                    "job_id": job_id, "status": "stopped"}
        if current is None:
            return {"ok": False, "code": "NO_ACTIVE_JOB", "error": "채굴 중인 후보 블록이 없습니다",
                    "job_id": job_id, "status": "stopped"}
        return None

    def pause_mining(self, job_id=None):
        error = self._job_error(job_id)
        if error:
            return error
        self.candidate["status"] = "paused"
        return {"ok": True, **self.candidate_info()}

    def resume_mining(self, job_id=None):
        error = self._job_error(job_id)
        if error:
            return error
        self.candidate["status"] = "running"
        return {"ok": True, **self.candidate_info()}

    def stop_mining(self, job_id=None, reason="user_stopped"):
        error = self._job_error(job_id)
        if error:
            return error
        info = self.candidate_info()
        info.update({"status": "stopped", "reason": reason})
        self.candidate, self.last_mining = None, info
        return {"ok": True, **plain_copy(info)}

    def mining_candidate_current(self):
        if self.candidate is None:
            return False
        block = self.candidate["block"]
        return (self.blockchain.is_valid() and block.previous_hash == self.blockchain.tip.hash
                and block.index == self.blockchain.tip.index + 1
                and block.difficulty >= self.blockchain.difficulty_for_height(block.index))

    def mine_step(self, batch=3000, job_id=None, *, _candidate_checked=False):
        if type(batch) is not int or not 1 <= batch <= 50000:
            raise ValueError("계산 묶음은 1~50000의 정수여야 합니다")
        error = self._job_error(job_id)
        if error:
            return error
        job, block = self.candidate, self.candidate["block"]
        if job["status"] == "paused" and batch != 1:
            return {"ok": False, "code": "MINING_PAUSED", "error": "일시정지 중에는 한 번 계산만 가능합니다",
                    **self.candidate_info()}
        if not _candidate_checked and not self.mining_candidate_current():
            info = self.stop_mining(reason="chain_changed")
            return {**info, "ok": False, "code": "STALE_JOB", "error": "체인이나 난이도가 바뀌어 후보를 폐기했습니다"}
        prefix, target = block.header_prefix(), "0" * block.difficulty
        started, found = time.perf_counter(), False
        for _ in range(batch):
            block.nonce = job["next_nonce"]
            block.hash = hashlib.sha256((prefix + str(block.nonce)).encode()).hexdigest()
            job["next_nonce"] += 1
            job["attempts"] += 1
            found = block.hash.startswith(target)
            job["samples"].append({"nonce": block.nonce, "hash": block.hash, "passed": found})
            job["samples"] = job["samples"][-12:]
            if found:
                break
        job["elapsed_ms"] += (time.perf_counter() - started) * 1000
        info = self.candidate_info()
        if not found:
            return {"ok": True, "found": False, **info}
        ok, reason = self.blockchain.add_block(block)
        info.update({"status": "found" if ok else "stopped", "found": True, "accepted": ok,
                     "ok": ok, "reason": reason})
        self.candidate, self.last_mining = None, plain_copy(info)
        if not ok:
            self.log("reject", f"채굴한 블록이 거부됨: {reason}")
            return info
        self.seen_blocks.add(block.hash)
        self._seen_payloads.add(fingerprint(block))
        self.refresh_mempool()
        self.log("block", f"블록 #{block.index} 채굴 성공! nonce={block.nonce}, 시도 {info['attempts']}회")
        info["propagation"] = self.broadcast_block(block)
        self.last_mining = plain_copy(info)
        return info

    def broadcast_block(self, block, exclude=None):
        results = []
        for peer_id in self.peers:
            if peer_id == exclude:
                continue
            if not self.online:
                ok, reason = False, "송신 노드 오프라인: 로컬 블록만 유지"
            else:
                ok, reason = self.network.nodes[peer_id].receive_block(block.copy(), origin=self.id)
            results.append({"node": peer_id, "accepted": ok, "reason": reason})
        return results

    def receive_block(self, block, origin=None):
        if not self.online:
            return False, "노드 오프라인"
        if not isinstance(block, Block):
            return False, "블록 형식 오류"
        error = block.format_error()
        if error:
            return False, error
        try:
            identity = fingerprint(block)
            if identity in self._seen_payloads:
                return True, "이미 검증한 동일 블록입니다"
            if (block.hash != block.calculate_hash() or block.merkle_root != block.calculate_merkle_root()
                    or not block.meets_difficulty()
                    or block.difficulty < self.blockchain.difficulty_for_height(block.index)):
                return False, "블록 해시·Merkle Root·작업 증명·최소 난이도 검증 실패"
            for tx in block.transactions:
                if not tx.basic_check()[0]:
                    return False, "블록 거래 형식 또는 전자서명 검증 실패"
        except (TypeError, ValueError, OverflowError):
            return False, "블록 데이터 형식 오류"
        tip = self.blockchain.tip
        if block.index == tip.index + 1 and block.previous_hash == tip.hash:
            ok, reason = self.blockchain.add_block(block)
            if not ok:
                self.log("reject", f"블록 #{block.index} 검증 실패: {reason}")
                return False, reason
            self.seen_blocks.add(block.hash)
            self._seen_payloads.add(identity)
            if self.candidate is not None:
                self.stop_mining(reason="chain_changed")
            self.refresh_mempool()
            self.log("block", f"Node {origin}의 블록 #{block.index} 검증 통과 → 체인에 추가")
            self.broadcast_block(block, exclude=origin)
            return True, "ok"
        # 높이가 짧아도 누적 작업량이 더 클 수 있다. 동기화는 자기 규칙으로 전체 검증한다.
        changed = self.resolve_conflicts()
        return changed, "누적 작업량이 큰 유효 체인으로 동기화됨" if changed else "채택할 더 큰 작업량의 유효 체인이 없습니다"

    def resolve_conflicts(self):
        if not self.online:
            return False
        own_valid = self.blockchain.is_valid()
        best, best_work = None, self.blockchain.cumulative_work if own_valid else -1
        for peer_id in self.peers:
            peer = self.network.nodes[peer_id]
            if not peer.online:
                continue
            try:
                candidate = Blockchain(peer.blockchain.chain, self.blockchain.difficulty,
                                       difficulty_rules=self.blockchain.difficulty_rules)
            except (TypeError, ValueError, OverflowError, AttributeError):
                self.log("reject", f"Node {peer_id}의 체인 데이터 형식 오류 → 채택하지 않음")
                continue
            if candidate.is_valid() and candidate.cumulative_work > best_work:
                best, best_work = candidate, candidate.cumulative_work
        if best is None:
            return False
        confirmed = {tx.txid() for b in best.chain for tx in b.transactions}
        orphaned = [tx.copy() for b in self.blockchain.chain for tx in b.transactions
                    if tx.tx_type != COINBASE and tx.txid() not in confirmed]
        previous_pool = [tx.copy() for tx in self.mempool]
        if self.candidate is not None:
            self.stop_mining(reason="chain_changed")
        self.blockchain = best
        self.pool.blockchain = best
        self.pool.rebuild(previous_pool + orphaned)
        self.seen_blocks = {b.hash for b in best.chain}
        self._seen_payloads = {fingerprint(b) for b in best.chain}
        self.log("sync", f"유효한 체인(높이 {best.tip.index}, 누적 작업량 {best_work})으로 교체")
        return True

    def snapshot(self):
        info = self.candidate_info()
        return {"id": self.id, "name": self.name, "online": self.online, "peers": list(self.peers),
                "height": self.blockchain.tip.index, "tip_hash": self.blockchain.tip.hash,
                "mempool": len(self.mempool), "wallet": self.wallet.address,
                "mining": self.candidate is not None, "mining_status": info["status"] if info else "idle",
                "candidate": info, "valid": self.blockchain.is_valid(),
                "cumulative_work": self.blockchain.cumulative_work,
                "difficulty": self.blockchain.difficulty_for_height(self.blockchain.tip.index + 1)}


class Network:
    def __init__(self, names=("A", "B", "C"), difficulty=DEFAULT_DIFFICULTY):
        if not valid_difficulty(difficulty):
            raise ValueError("난이도는 1~5의 정수여야 합니다")
        names = list(names)
        if not names or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
            raise ValueError("노드 이름은 서로 다른 비어 있지 않은 문자열이어야 합니다")
        self.events, self._seq = [], 0
        self.difficulty, self.difficulty_rules = difficulty, [(1, difficulty)]
        self.wallets = {}
        self.nodes = {name: Node(name, f"Node {name}", self, difficulty) for name in names}
        self.mining_comparison = None
        self.restored_mining_cancelled = False
        for name, node in self.nodes.items():
            node.peers = [peer for peer in names if peer != name]
            self.wallets[node.wallet.address] = node.wallet
        self.log("-", "sys", f"네트워크 시작: 노드 {', '.join(names)}, 난이도 {difficulty}")

    def log(self, node_id, kind, message):
        self._seq += 1
        self.events.append({"seq": self._seq, "time": time.strftime("%H:%M:%S"),
                            "node": node_id, "kind": kind, "msg": message})
        self.events = self.events[-500:]

    def create_wallet(self, name):
        if not isinstance(name, str):
            raise ValueError("지갑 이름은 문자열이어야 합니다")
        wallet = Wallet(name or f"지갑{len(self.wallets)+1}")
        self.wallets[wallet.address] = wallet
        self.log("-", "sys", f"지갑 생성: {wallet.name} ({wallet.address[:8]}…)")
        return wallet

    def comparison_info(self):
        return plain_copy(self.mining_comparison)

    def start_mining_comparison(self):
        nodes = list(self.nodes.values())
        if len(nodes) < 2:
            raise ValueError("두 개 이상의 노드가 필요합니다")
        if any(n.candidate is not None for n in nodes):
            raise ValueError("진행 중인 채굴을 먼저 중지하세요")
        if any(not n.online for n in nodes):
            raise ValueError("모든 노드를 온라인으로 바꾼 뒤 비교하세요")
        if any(not n.blockchain.is_valid() for n in nodes):
            raise ValueError("유효하지 않은 체인을 동기화로 복구한 뒤 비교하세요")
        if len({(n.blockchain.tip.index, n.blockchain.tip.hash,
                 n.blockchain.difficulty_for_height(n.blockchain.tip.index + 1)) for n in nodes}) != 1:
            raise ValueError("세 노드를 같은 최신 블록으로 동기화한 뒤 비교하세요")
        jobs = {n.id: n.start_mining()["job_id"] for n in nodes}
        self.mining_comparison = {"comparison_id": uuid.uuid4().hex, "status": "running",
                                  "node_ids": list(self.nodes), "job_ids": jobs,
                                  "rounds": 0, "winner": None}
        self.log("-", "mine", "세 노드 채굴 비교 시작: 같은 난이도에서 한 번씩 번갈아 실제 해시 계산")
        return self.comparison_info()

    def _comparison(self, comparison_id):
        comparison = self.mining_comparison
        if comparison is None or comparison["comparison_id"] != comparison_id:
            raise ValueError("이미 끝났거나 교체된 채굴 비교입니다")
        if comparison["status"] not in ("running", "paused"):
            raise ValueError("이미 종료된 채굴 비교입니다")
        return comparison

    def stop_mining_comparison(self, comparison_id=None, *, reason="user_stopped"):
        if self.mining_comparison is None:
            raise ValueError("채굴 비교를 먼저 시작하세요")
        comparison = self._comparison(comparison_id or self.mining_comparison["comparison_id"])
        for nid, job_id in comparison["job_ids"].items():
            n = self.nodes[nid]
            if n.candidate is not None and n.candidate["job_id"] == job_id:
                n.stop_mining(job_id=job_id, reason=reason)
        comparison.update(status="stopped", reason=reason,
                          candidates={nid: self.nodes[nid].candidate_info() for nid in comparison["node_ids"]})
        return self.comparison_info()

    def control_mining_comparison(self, comparison_id, command):
        comparison = self._comparison(comparison_id)
        if command == "stop":
            return self.stop_mining_comparison(comparison_id)
        if command not in ("pause", "resume"):
            raise ValueError("없는 채굴 비교 명령입니다")
        for nid, job_id in comparison["job_ids"].items():
            n = self.nodes[nid]
            if n._job_error(job_id) or not n.online or not n.mining_candidate_current():
                return self.stop_mining_comparison(comparison_id, reason="state_changed")
        for nid, job_id in comparison["job_ids"].items():
            getattr(self.nodes[nid], command + "_mining")(job_id=job_id)
        comparison["status"] = "paused" if command == "pause" else "running"
        return self.comparison_info()

    def step_mining_comparison(self, comparison_id, batch=1):
        if type(batch) is not int or not 1 <= batch <= 5000:
            raise ValueError("계산 묶음은 1~5000의 정수여야 합니다")
        comparison = self._comparison(comparison_id)
        if comparison["status"] == "paused" and batch != 1:
            raise ValueError("일시정지 중에는 한 번씩 계산만 가능합니다")
        for nid, job_id in comparison["job_ids"].items():
            n = self.nodes[nid]
            if n._job_error(job_id) or not n.online or not n.mining_candidate_current():
                return self.stop_mining_comparison(comparison_id, reason="state_changed")
            if n.candidate["status"] != comparison["status"]:
                return self.stop_mining_comparison(comparison_id, reason="state_changed")
        ids = comparison["node_ids"]
        for _ in range(batch):
            # Rotate the first node each round so an API request never grants A
            # an entire batch before B/C have a chance to compute one hash.
            offset = comparison["rounds"] % len(ids)
            comparison["rounds"] += 1
            for nid in ids[offset:] + ids[:offset]:
                result = self.nodes[nid].mine_step(1, comparison["job_ids"][nid], _candidate_checked=True)
                if result.get("found"):
                    comparison.update(status="found", winner=nid, block_hash=result["hash"], index=result["index"],
                                      propagation=plain_copy(result.get("propagation", [])))
                    # Connected peers normally stop during broadcast. Also stop
                    # a remaining candidate if the simulated topology differs.
                    for peer_id, job_id in comparison["job_ids"].items():
                        peer = self.nodes[peer_id]
                        if peer.candidate is not None and peer.candidate["job_id"] == job_id:
                            peer.stop_mining(job_id=job_id, reason="comparison_finished")
                    comparison["candidates"] = {peer_id: self.nodes[peer_id].candidate_info() for peer_id in ids}
                    return self.comparison_info()
        return self.comparison_info()

    def set_difficulty(self, difficulty):
        if not valid_difficulty(difficulty):
            raise ValueError("난이도는 1~5의 정수여야 합니다")
        if self.mining_comparison and self.mining_comparison["status"] in ("running", "paused"):
            self.stop_mining_comparison(reason="difficulty_changed")
        activation = max(node.blockchain.tip.index for node in self.nodes.values()) + 1
        self.difficulty_rules = normalize_rules([r for r in self.difficulty_rules if r[0] < activation]
                                                + [(activation, difficulty)])
        self.difficulty = difficulty
        for node in self.nodes.values():
            node.blockchain.set_difficulty_rules(self.difficulty_rules)
            if node.candidate is not None:
                node.stop_mining(reason="difficulty_changed")
        self.log("-", "sys", f"높이 {activation}부터 최소 난이도 {difficulty} 적용 (과거 블록 규칙 유지)")
        return {"difficulty": difficulty, "from_height": activation,
                "difficulty_rules": [list(r) for r in self.difficulty_rules]}

    def to_dict(self):
        """JSON v1. 개인키는 이 로컬 실습 저장소에만 보관한다."""
        nodes = {}
        for node_id, node in self.nodes.items():
            candidate = None
            if node.candidate is not None:
                candidate = {k: (v.to_dict() if k == "block" else plain_copy(v))
                             for k, v in node.candidate.items()}
            nodes[node_id] = {"name": node.name, "wallet": node.wallet.address,
                              "peers": list(node.peers), "online": node.online,
                              "chain": [b.to_dict() for b in node.blockchain.chain],
                              "mempool": [tx.to_dict() for tx in node.mempool],
                              "candidate": candidate, "last_mining": plain_copy(node.last_mining)}
        return {"version": 1, "difficulty": self.difficulty,
                "difficulty_rules": [list(r) for r in self.difficulty_rules],
                "wallets": [w.to_dict() for w in self.wallets.values()], "nodes": nodes,
                "events": plain_copy(self.events), "seq": self._seq,
                "mining_comparison": self.comparison_info()}

    @classmethod
    def from_dict(cls, data, restore_jobs=False):
        """재시작은 미완료 채굴을 취소한다. 요청 간 재로딩은 restore_jobs=True로 이어간다.

        변조 실험의 의미상 무효 체인은 보존하되, 저장 형식과 난이도 정책은 검사한다.
        """
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("지원하지 않는 실습 저장 형식입니다")
        try:
            rules = normalize_rules(data["difficulty_rules"])
            if data["difficulty"] != rules[-1][1] or not valid_difficulty(data["difficulty"]):
                raise ValueError("저장된 난이도와 이력이 일치하지 않습니다")
            records = data["nodes"]
            if not isinstance(records, dict) or not records or not isinstance(data["wallets"], list):
                raise ValueError("저장된 노드·지갑 형식 오류")
            network = cls(names=records.keys(), difficulty=rules[0][1])
            network.difficulty, network.difficulty_rules = data["difficulty"], rules
            wallets = [Wallet.from_dict(record) for record in data["wallets"]]
            network.wallets = {wallet.address: wallet for wallet in wallets}
            if len(network.wallets) != len(wallets):
                raise ValueError("저장된 지갑 주소가 중복됩니다")
            network.events, network._seq = plain_copy(data["events"]), data["seq"]
            if type(network._seq) is not int or network._seq < 0 or not isinstance(network.events, list) or len(network.events) > 500:
                raise ValueError("저장된 이벤트 형식 오류")
            previous = 0
            for event in network.events:
                if (not isinstance(event, dict) or type(event.get("seq")) is not int
                        or not previous < event["seq"] <= network._seq
                        or any(not isinstance(event.get(key), str) for key in ("time", "node", "kind", "msg"))):
                    raise ValueError("저장된 이벤트 순서 또는 내용 오류")
                previous = event["seq"]
            for node_id, record in records.items():
                node = network.nodes[node_id]
                if not isinstance(record, dict) or type(record.get("online")) is not bool or not isinstance(record.get("name"), str):
                    raise ValueError("저장된 노드 형식 오류")
                node.name, node.online = record["name"], record["online"]
                node.wallet = network.wallets[record["wallet"]]
                peers = record["peers"]
                if not isinstance(peers, list) or len(set(peers)) != len(peers) or any(peer not in records or peer == node_id for peer in peers):
                    raise ValueError("저장된 노드 연결 형식 오류")
                node.peers = list(peers)
                if not isinstance(record["chain"], list) or not record["chain"]:
                    raise ValueError("저장된 체인에 Genesis가 없습니다")
                chain = [Block.from_dict(block) for block in record["chain"]]
                if any(block.format_error() for block in chain):
                    raise ValueError("저장된 블록 형식 오류")
                node.blockchain = Blockchain(chain, network.difficulty, rules)
                node.pool = Mempool(node.blockchain)
                if not isinstance(record["mempool"], list):
                    raise ValueError("저장된 Mempool 형식 오류")
                node.pool.txs = [Transaction.from_dict(tx) for tx in record["mempool"]]
                node.last_mining = plain_copy(record.get("last_mining"))
                if node.last_mining is not None and not isinstance(node.last_mining, dict):
                    raise ValueError("저장된 채굴 결과 형식 오류")
                raw_job = record.get("candidate")
                if raw_job is not None:
                    if not isinstance(raw_job, dict):
                        raise ValueError("저장된 채굴 작업 형식 오류")
                    job = plain_copy(raw_job)
                    job["block"] = Block.from_dict(job["block"])
                    if (not isinstance(job["job_id"], str) or not job["job_id"]
                            or job["status"] not in ("running", "paused")
                            or type(job["attempts"]) is not int or job["attempts"] < 0
                            or type(job["next_nonce"]) is not int or job["next_nonce"] != job["attempts"]
                            or type(job["elapsed_ms"]) not in (int, float)
                            or not math.isfinite(job["elapsed_ms"]) or job["elapsed_ms"] < 0
                            or not isinstance(job["samples"], list) or len(job["samples"]) > 12
                            or job["miner"] not in network.wallets):
                        raise ValueError("저장된 채굴 작업 값 오류")
                    block = job["block"]
                    if (block.format_error() or block.index != node.blockchain.tip.index + 1
                            or block.previous_hash != node.blockchain.tip.hash
                            or block.difficulty < node.blockchain.difficulty_for_height(block.index)
                            or block.hash != block.calculate_hash()
                            or block.merkle_root != block.calculate_merkle_root()
                            or (job["attempts"] and block.nonce != job["next_nonce"] - 1)):
                        raise ValueError("저장된 채굴 후보가 현재 체인 또는 진행 상태와 일치하지 않습니다")
                    for sample in job["samples"]:
                        if (not isinstance(sample, dict) or type(sample.get("nonce")) is not int
                                or not 0 <= sample["nonce"] < job["next_nonce"]
                                or type(sample.get("passed")) is not bool
                                or sample.get("hash") != hashlib.sha256((block.header_prefix() + str(sample["nonce"])).encode()).hexdigest()
                                or sample["passed"] != sample["hash"].startswith("0" * block.difficulty)):
                            raise ValueError("저장된 채굴 표본이 실제 계산 결과와 일치하지 않습니다")
                    node.candidate = job
                    if not restore_jobs:
                        node.stop_mining(reason="restore_cancelled")
                        network.restored_mining_cancelled = True
                if node.blockchain.is_valid():
                    node.seen_blocks = {block.hash for block in chain}
                    node._seen_payloads = {fingerprint(block) for block in chain}
            comparison = plain_copy(data.get("mining_comparison"))
            if comparison is not None:
                if (not isinstance(comparison, dict)
                        or not isinstance(comparison.get("comparison_id"), str)
                        or not comparison["comparison_id"]
                        or comparison.get("status") not in ("running", "paused", "found", "stopped")
                        or comparison.get("node_ids") != list(network.nodes)
                        or not isinstance(comparison.get("job_ids"), dict)
                        or set(comparison["job_ids"]) != set(network.nodes)
                        or any(not isinstance(job_id, str) or not job_id for job_id in comparison["job_ids"].values())
                        or type(comparison.get("rounds")) is not int or comparison["rounds"] < 0
                        or comparison.get("winner") not in (None, *network.nodes)):
                    raise ValueError("저장된 채굴 비교 형식 오류")
                network.mining_comparison = comparison
                if comparison["status"] in ("running", "paused"):
                    if not restore_jobs:
                        comparison.update(status="stopped", reason="restore_cancelled")
                    elif any(n.candidate is None or n.candidate["job_id"] != comparison["job_ids"][nid]
                             for nid, n in network.nodes.items()):
                        # A stale tab can stop one candidate independently.
                        # Preserve the ledger and cancel the remaining group.
                        network.stop_mining_comparison(reason="state_changed")
            if network.restored_mining_cancelled:
                network.log("-", "sys", "실습 복원: 미완료 채굴은 취소했습니다. 새 채굴을 시작하세요")
            return network
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("저장된 실습에 필수 데이터가 없거나 형식이 올바르지 않습니다") from exc
