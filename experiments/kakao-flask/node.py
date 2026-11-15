"""[3, 6단계] Node: Mempool, 채굴, Transaction/Block 전파와 검증, 체인 동기화."""
import hashlib
import time
from collections import deque

from block import Block
from mempool import Mempool
from miner import build_candidate
from blockchain import DEFAULT_DIFFICULTY, MAX_DIFFICULTY, MIN_DIFFICULTY, Blockchain
from transaction import COINBASE, Transaction
from wallet import Wallet


class Node:
    def __init__(self, node_id: str, name: str, network: "Network", difficulty: int):
        self.id, self.name, self.network = node_id, name, network
        self.wallet = Wallet(f"{name} 채굴자")
        self.blockchain = Blockchain(difficulty=difficulty)
        self.pool = Mempool(self.blockchain)   # mempool.py: 검증을 통과한 대기 거래
        self.peers = []            # 연결된 노드 id
        self.online = True
        self.candidate = None      # 채굴 중인 후보 블록 {"block", "miner", "attempts"}
        self.seen_blocks = set()

    @property
    def mempool(self):
        """대기 중인 거래 목록 (Mempool 객체의 txs)."""
        return self.pool.txs

    def log(self, kind: str, msg: str):
        self.network.log(self.id, kind, msg)

    # ---- Mempool / 거래 수신 ------------------------------------------
    def pending_state(self):
        """확정 체인 + Mempool 거래를 모두 반영한 상태 (이중 지불/잔액 초과 방지용)."""
        return self.pool.pending_state()

    def receive_transaction(self, tx: Transaction, origin: str = None):
        if not self.online:
            return False, "노드 오프라인"
        txid = tx.txid()
        if any(t.txid() == txid for t in self.pool.txs):
            return False, "이미 Mempool에 있는 거래"
        if tx.tx_type == COINBASE:
            return False, "coinbase 거래는 외부에서 받을 수 없습니다"
        ok, reason = self.pool.add(tx)           # 형식·서명·잔액·거래 순번 검증 후 대기열 등록
        short = f"{tx.sender_address[:8]}→{tx.receiver_address[:8]} {tx.amount}코인 (txid {txid[:8]})"
        if not ok:
            self.log("reject", f"거래 거부: {short} - {reason}")
            return False, reason
        src = f"Node {origin}로부터 수신" if origin else "직접 제출"
        self.log("tx", f"거래 검증 통과 → Mempool 등록 ({src}): {short}")
        for pid in self.peers:
            if pid != origin:
                self.network.nodes[pid].receive_transaction(tx.copy(), origin=self.id)
        return True, "ok"

    def refresh_mempool(self):
        """새 블록이 체인에 들어간 뒤, 이미 처리됐거나 무효가 된 거래를 Mempool에서 제거."""
        self.pool.refresh()

    # ---- 채굴 ----------------------------------------------------------
    def start_mining(self, miner_address: str = None):
        addr = miner_address or self.wallet.address
        block = build_candidate(self.blockchain, self.pool, addr)   # miner.py: 대기 거래 + 채굴 보상
        self.candidate = {"block": block, "miner": addr, "attempts": 0}
        self.log("mine", f"후보 블록 #{block.index} 구성: 거래 {len(block.transactions) - 1}개 + coinbase, 목표 해시 '{'0' * block.difficulty}…'")
        return self.candidate_info()

    def candidate_info(self):
        if not self.candidate:
            return None
        b = self.candidate["block"]
        return {"index": b.index, "previous_hash": b.previous_hash, "merkle_root": b.merkle_root,
                "difficulty": b.difficulty, "nonce": b.nonce, "hash": b.hash,
                "tx_count": len(b.transactions), "attempts": self.candidate["attempts"]}

    def mine_step(self, batch: int = 3000):
        """nonce를 batch번 바꿔 가며 해시를 계산. 조건을 만족하면 블록을 체인에 추가하고 전파한다.
        (miner.mine()의 단계별 버전: 화면에 진행 과정을 보여 주기 위해 나눠서 실행한다)"""
        if not self.candidate:
            return {"error": "채굴 중인 후보 블록이 없습니다 (먼저 채굴 시작)"}
        block = self.candidate["block"]
        prefix, target = block.header_prefix(), "0" * block.difficulty
        samples, found, t0 = deque(maxlen=8), False, time.time()
        for _ in range(batch):
            h = hashlib.sha256((prefix + str(block.nonce)).encode()).hexdigest()
            self.candidate["attempts"] += 1
            samples.append({"nonce": block.nonce, "hash": h})
            if h.startswith(target):
                block.hash, found = h, True
                break
            block.nonce += 1
        if not found:
            block.hash = samples[-1]["hash"]
            return {"found": False, "samples": list(samples), **self.candidate_info()}
        attempts = self.candidate["attempts"]
        ok, reason = self.blockchain.add_block(block)
        self.candidate = None
        if not ok:
            self.log("reject", f"채굴한 블록이 거부됨: {reason}")
            return {"found": True, "accepted": False, "reason": reason, "samples": list(samples)}
        self.seen_blocks.add(block.hash)
        self.refresh_mempool()
        self.log("block", f"블록 #{block.index} 채굴 성공! nonce={block.nonce}, 시도 {attempts}회, hash={block.hash[:16]}…")
        results = self.broadcast_block(block)
        return {"found": True, "accepted": True, "attempts": attempts, "nonce": block.nonce,
                "hash": block.hash, "index": block.index, "samples": list(samples), "propagation": results}

    # ---- 블록 수신 / 전파 / 동기화 -------------------------------------
    def broadcast_block(self, block: Block, exclude: str = None):
        out = []
        for pid in self.peers:
            if pid != exclude:
                ok, reason = self.network.nodes[pid].receive_block(block.copy(), origin=self.id)
                out.append({"node": pid, "accepted": ok, "reason": reason})
        return out

    def receive_block(self, block: Block, origin: str = None):
        if not self.online:
            return False, "노드 오프라인"
        if block.hash in self.seen_blocks:
            return True, "이미 다른 노드를 통해 수신함"
        tip = self.blockchain.tip
        if block.index == tip.index + 1 and block.previous_hash == tip.hash:
            ok, reason = self.blockchain.add_block(block)
            if not ok:
                self.log("reject", f"블록 #{block.index} 검증 실패 (Node {origin}발): {reason}")
                return False, reason
            self.seen_blocks.add(block.hash)
            self.candidate = None                    # 내 후보는 이미 낡았으므로 폐기
            self.refresh_mempool()
            self.log("block", f"Node {origin}가 보낸 블록 #{block.index} 검증 통과 → 체인에 추가")
            self.broadcast_block(block, exclude=origin)
            return True, "ok"
        if block.index > tip.index:
            self.log("sync", f"블록 #{block.index} 수신, 내 체인보다 앞섬 → 동기화 시도")
            changed = self.resolve_conflicts()
            return changed, "체인 동기화됨" if changed else "동기화 실패 (더 긴 유효 체인 없음)"
        self.log("sync", f"블록 #{block.index} 무시: 이미 같은 높이 이상의 체인 보유")
        return False, "이미 같은 높이 이상의 체인 보유"

    def resolve_conflicts(self) -> bool:
        """가장 긴 '유효한' 체인을 채택 (Nakamoto 합의의 단순화)."""
        # 내 체인이 이미 깨져 있다면 길이와 상관없이 유효한 체인으로 교체한다
        own_valid = self.blockchain.is_valid()
        best, best_len = None, (len(self.blockchain.chain) if own_valid else 0)
        for pid in self.peers:
            peer = self.network.nodes[pid]
            if peer.online and len(peer.blockchain.chain) > best_len:
                cand = Blockchain([b.copy() for b in peer.blockchain.chain], self.blockchain.difficulty)
                if cand.is_valid():
                    best, best_len = cand, len(cand.chain)
                else:
                    self.log("reject", f"Node {pid}의 체인은 검증 실패 → 채택하지 않음")
        if best is None:
            return False
        self.blockchain = best
        self.pool.blockchain = best               # Mempool도 교체된 체인을 기준으로 검증
        self.seen_blocks = {b.hash for b in best.chain}
        self.candidate = None
        self.refresh_mempool()
        self.log("sync", f"유효한 체인(높이 {best_len - 1})으로 교체" + ("" if own_valid else " - 기존 체인은 검증 실패 상태였음"))
        return True

    def snapshot(self) -> dict:
        t = self.blockchain.tip
        return {"id": self.id, "name": self.name, "online": self.online, "peers": self.peers,
                "height": t.index, "tip_hash": t.hash, "mempool": len(self.mempool),
                "wallet": self.wallet.address, "mining": self.candidate is not None,
                "valid": self.blockchain.is_valid()}


class Network:
    def __init__(self, names=("A", "B", "C"), difficulty=DEFAULT_DIFFICULTY):
        self.events, self._seq = [], 0
        self.difficulty = difficulty
        self.wallets = {}
        self.nodes = {n: Node(n, f"Node {n}", self, difficulty) for n in names}
        for n, node in self.nodes.items():
            node.peers = [p for p in names if p != n]          # 전체 연결(full mesh)
            self.wallets[node.wallet.address] = node.wallet
        self.log("-", "sys", f"네트워크 시작: 노드 {', '.join(names)}, 난이도 {difficulty}")

    def log(self, node_id: str, kind: str, msg: str):
        self._seq += 1
        self.events.append({"seq": self._seq, "time": time.strftime("%H:%M:%S"),
                            "node": node_id, "kind": kind, "msg": msg})
        del self.events[:-500]

    def create_wallet(self, name: str) -> Wallet:
        w = Wallet(name or f"지갑{len(self.wallets) + 1}")
        self.wallets[w.address] = w
        self.log("-", "sys", f"지갑 생성: {w.name} ({w.address[:8]}…)")
        return w

    def set_difficulty(self, d: int):
        if not MIN_DIFFICULTY <= d <= MAX_DIFFICULTY:
            raise ValueError(f"난이도는 {MIN_DIFFICULTY}~{MAX_DIFFICULTY} 범위여야 합니다")
        self.difficulty = d
        for node in self.nodes.values():
            node.blockchain.difficulty = d
            node.candidate = None
        self.log("-", "sys", f"난이도 변경: {d} (해시가 '{'0' * d}'로 시작해야 함)")
