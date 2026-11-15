"""[5단계] Blockchain: 연결(Previous Hash), 잔액 상태, 전체 검증."""
from block import Block
from transaction import COINBASE, FAUCET

MIN_DIFFICULTY, MAX_DIFFICULTY, DEFAULT_DIFFICULTY = 1, 5, 4
GENESIS_DIFFICULTY = 2
BLOCK_REWARD = 10        # 채굴 보상(coinbase)
FAUCET_AMOUNT = 50       # 지갑당 1회 지급하는 학습용 코인
MAX_TX_PER_BLOCK = 10    # coinbase 포함


def make_genesis() -> Block:
    """모든 노드가 동일하게 갖는 Genesis Block (시간·내용 고정 -> 항상 같은 해시)."""
    g = Block(0, "0", [], GENESIS_DIFFICULTY, timestamp=0.0)
    g.mine_to_target()
    return g


GENESIS = make_genesis()


class State:
    """체인을 처음부터 적용해 얻는 계정 상태. 잔액은 지갑에 저장되지 않고 거래 내역에서 계산된다."""

    def __init__(self):
        self.balances, self.nonces, self.faucet_used = {}, {}, set()

    def copy(self) -> "State":
        s = State()
        s.balances, s.nonces, s.faucet_used = dict(self.balances), dict(self.nonces), set(self.faucet_used)
        return s

    def balance(self, address: str) -> int:
        return self.balances.get(address, 0)

    def apply(self, tx):
        """거래가 현재 상태에서 유효하면 반영하고 (True,'ok'), 아니면 상태 변경 없이 (False,사유)."""
        ok, reason = tx.basic_check()
        if not ok:
            return False, reason
        if tx.tx_type == COINBASE:
            if tx.amount != BLOCK_REWARD:
                return False, f"채굴 보상은 {BLOCK_REWARD}이어야 합니다"
        elif tx.tx_type == FAUCET:
            if tx.amount != FAUCET_AMOUNT:
                return False, f"faucet 지급액은 {FAUCET_AMOUNT}이어야 합니다"
            if tx.receiver_address in self.faucet_used:
                return False, "이미 faucet을 받은 주소입니다"
            self.faucet_used.add(tx.receiver_address)
        else:
            sender = tx.sender_address
            expected = self.nonces.get(sender, 0)
            if tx.nonce != expected:
                return False, f"nonce 불일치 (기대 {expected}, 입력 {tx.nonce}) - 재전송 방지"
            if self.balance(sender) < tx.amount:
                return False, f"잔액 부족 (보유 {self.balance(sender)}, 전송 {tx.amount})"
            self.balances[sender] -= tx.amount
            self.nonces[sender] = expected + 1
        self.balances[tx.receiver_address] = self.balance(tx.receiver_address) + tx.amount
        return True, "ok"


class Blockchain:
    def __init__(self, chain=None, difficulty=DEFAULT_DIFFICULTY):
        self.chain = chain if chain is not None else [GENESIS.copy()]
        self.difficulty = difficulty

    @property
    def tip(self) -> Block:
        return self.chain[-1]

    def state(self) -> State:
        st = State()
        for block in self.chain:
            for tx in block.transactions:
                st.apply(tx)
        return st

    # ---- 블록 검사 -----------------------------------------------------
    def check_block(self, block: Block, prev, state: State):
        """(checks, reasons). 5개 항목: 해시/머클/PoW/연결/거래. state는 거래 반영 결과로 갱신된다."""
        checks = {
            "hash": block.hash == block.calculate_hash(),
            "merkle": block.merkle_root == block.calculate_merkle_root(),
            "pow": block.meets_difficulty(),
            "link": prev is not None and block.previous_hash == prev.hash and block.index == prev.index + 1,
            "txs": True,
        }
        reasons = []
        if not checks["hash"]:
            reasons.append("블록 해시 불일치 (헤더 변조)")
        if not checks["merkle"]:
            reasons.append("Merkle Root 불일치 (거래 내용 변조)")
        if not checks["pow"]:
            reasons.append(f"작업 증명(PoW) 미충족: 해시가 '{'0' * block.difficulty}'로 시작하지 않음")
        if not checks["link"]:
            reasons.append("이전 블록과의 연결(Previous Hash) 끊김")
        txs = block.transactions
        if len(txs) == 0 or len(txs) > MAX_TX_PER_BLOCK or txs[0].tx_type != COINBASE:
            checks["txs"] = False
            reasons.append("첫 거래는 coinbase여야 하며 거래 수 제한을 지켜야 합니다")
        for i, tx in enumerate(txs):
            if i > 0 and tx.tx_type == COINBASE:
                checks["txs"] = False
                reasons.append(f"tx#{i}: coinbase는 블록당 1개만 허용")
                continue
            ok, why = state.apply(tx)
            if not ok:
                checks["txs"] = False
                reasons.append(f"tx#{i}: {why}")
        return checks, reasons

    def check_genesis(self, block: Block):
        checks = {"hash": block.hash == block.calculate_hash(),
                  "merkle": block.merkle_root == block.calculate_merkle_root(),
                  "pow": block.meets_difficulty(),
                  "link": block.hash == GENESIS.hash,   # 모든 노드가 같은 Genesis를 가져야 함
                  "txs": len(block.transactions) == 0}
        names = {"hash": "해시 불일치", "merkle": "Merkle Root 불일치", "pow": "PoW 미충족",
                 "link": "표준 Genesis Block과 다름", "txs": "Genesis에 거래가 있음"}
        return checks, [names[k] for k, v in checks.items() if not v]

    def validate_chain(self):
        """체인 전체 검증. (전체 유효 여부, 블록별 결과 리스트)"""
        state, results = State(), []
        for i, block in enumerate(self.chain):
            if i == 0:
                checks, reasons = self.check_genesis(block)
            else:
                checks, reasons = self.check_block(block, self.chain[i - 1], state)
            results.append({"index": block.index, "checks": checks, "reasons": reasons,
                            "valid": all(checks.values())})
        return all(r["valid"] for r in results), results

    def is_valid(self) -> bool:
        return self.validate_chain()[0]

    def add_block(self, block: Block):
        if block.difficulty < self.difficulty:
            return False, f"난이도 미달 (블록 {block.difficulty} < 네트워크 {self.difficulty})"
        checks, reasons = self.check_block(block, self.tip, self.state())
        if all(checks.values()):
            self.chain.append(block)
            return True, "ok"
        return False, "; ".join(reasons)
