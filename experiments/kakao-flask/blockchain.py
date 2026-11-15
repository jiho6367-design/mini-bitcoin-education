"""높이별 난이도 정책과 원자적 상태 반영을 사용하는 교육용 계정 체인."""
from block import Block
from transaction import COINBASE, FAUCET, Transaction

MIN_DIFFICULTY, MAX_DIFFICULTY, DEFAULT_DIFFICULTY = 1, 5, 4
GENESIS_DIFFICULTY = 2
BLOCK_REWARD = 10
FAUCET_AMOUNT = 50
MAX_TX_PER_BLOCK = 10


def valid_difficulty(value):
    return type(value) is int and MIN_DIFFICULTY <= value <= MAX_DIFFICULTY


def normalize_rules(rules):
    if not isinstance(rules, (list, tuple)) or not rules:
        raise ValueError("난이도 이력이 필요합니다")
    result, previous = [], 0
    for entry in rules:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError("난이도 이력 형식 오류")
        height, difficulty = entry
        if type(height) is not int or height <= previous or not valid_difficulty(difficulty):
            raise ValueError("난이도 이력은 높이 순서와 1~5 정수 난이도를 사용해야 합니다")
        result.append((height, difficulty))
        previous = height
    if result[0][0] != 1:
        raise ValueError("첫 난이도 규칙은 높이 1부터 시작해야 합니다")
    return result


def make_genesis():
    block = Block(0, "0", [], GENESIS_DIFFICULTY, timestamp=0.0)
    block.mine_to_target()
    return block


GENESIS = make_genesis()


class State:
    def __init__(self):
        self.balances, self.nonces, self.faucet_used = {}, {}, set()

    def copy(self):
        state = State()
        state.replace(self)
        return state

    def replace(self, other):
        self.balances = dict(other.balances)
        self.nonces = dict(other.nonces)
        self.faucet_used = set(other.faucet_used)

    def balance(self, address):
        return self.balances.get(address, 0)

    def apply(self, tx):
        """실패 시 어떤 계정·순번·faucet 상태도 바꾸지 않는다."""
        if not isinstance(tx, Transaction):
            return False, "거래 형식 오류"
        ok, reason = tx.basic_check()
        if not ok:
            return False, reason
        sender = None
        if tx.tx_type == COINBASE:
            if tx.amount != BLOCK_REWARD:
                return False, f"채굴 보상은 {BLOCK_REWARD}이어야 합니다"
        elif tx.tx_type == FAUCET:
            if tx.amount != FAUCET_AMOUNT:
                return False, f"faucet 지급액은 {FAUCET_AMOUNT}이어야 합니다"
            if tx.receiver_address in self.faucet_used:
                return False, "이미 faucet을 받은 주소입니다"
        else:
            sender = tx.sender_address
            expected = self.nonces.get(sender, 0)
            if tx.nonce != expected:
                return False, f"nonce 불일치 (기대 {expected}, 입력 {tx.nonce}) - 재전송 방지"
            if self.balance(sender) < tx.amount:
                return False, f"잔액 부족 (보유 {self.balance(sender)}, 전송 {tx.amount})"
        if tx.tx_type == FAUCET:
            self.faucet_used.add(tx.receiver_address)
        if sender is not None:
            self.balances[sender] = self.balance(sender) - tx.amount
            self.nonces[sender] = tx.nonce + 1
        self.balances[tx.receiver_address] = self.balance(tx.receiver_address) + tx.amount
        return True, "ok"


class Blockchain:
    def __init__(self, chain=None, difficulty=DEFAULT_DIFFICULTY, difficulty_rules=None):
        if not valid_difficulty(difficulty):
            raise ValueError("난이도는 1~5의 정수여야 합니다")
        self.chain = [b.copy() for b in chain] if chain is not None else [GENESIS.copy()]
        self.set_difficulty_rules(difficulty_rules if difficulty_rules is not None else [(1, difficulty)])

    @property
    def difficulty(self):
        return self._difficulty

    @difficulty.setter
    def difficulty(self, value):
        if not valid_difficulty(value):
            raise ValueError("난이도는 1~5의 정수여야 합니다")
        height = max(1, len(self.chain))
        self.set_difficulty_rules([r for r in self.difficulty_rules if r[0] < height] + [(height, value)])

    def set_difficulty_rules(self, rules):
        self.difficulty_rules = normalize_rules(rules)
        self._difficulty = self.difficulty_rules[-1][1]

    def difficulty_for_height(self, height):
        difficulty = self.difficulty_rules[0][1]
        for start, value in self.difficulty_rules:
            if start > height:
                break
            difficulty = value
        return difficulty

    @property
    def tip(self):
        if not self.chain:
            raise ValueError("체인에 Genesis가 없습니다")
        return self.chain[-1]

    @property
    def cumulative_work(self):
        # 목표값 2^(256-4d)-1에 대한 기대 작업량은 정확히 16^d이다.
        return sum(16 ** b.difficulty for b in self.chain[1:]
                   if isinstance(b, Block) and valid_difficulty(b.difficulty))

    def state(self):
        # 변조 실험 중에도 표시 가능하다. 무효 거래는 반영하지 않으며
        # 이 값을 원장 갱신에 쓰기 전에는 반드시 전체 체인을 검증한다.
        state = State()
        for block in self.chain:
            for tx in block.transactions:
                state.apply(tx)
        return state

    def check_block(self, block, prev, state):
        """다섯 검사를 모두 통과해야 전달받은 state를 갱신한다."""
        checks = dict.fromkeys(("hash", "merkle", "pow", "link", "txs"), False)
        if not isinstance(block, Block):
            return checks, ["블록 형식 오류"]
        error = block.format_error()
        if error:
            return checks, [error]
        reasons = []
        try:
            checks.update({
                "hash": block.hash == block.calculate_hash(),
                "merkle": block.merkle_root == block.calculate_merkle_root(),
                "pow": block.meets_difficulty() and block.difficulty >= self.difficulty_for_height(block.index),
                "link": (isinstance(prev, Block) and block.previous_hash == prev.hash
                         and block.index == prev.index + 1 and block.timestamp >= prev.timestamp),
                "txs": True,
            })
        except (TypeError, ValueError, OverflowError):
            return dict.fromkeys(checks, False), ["블록 또는 거래 데이터 형식 오류"]
        for key, reason in (
                ("hash", "블록 해시 불일치 (헤더 변조)"),
                ("merkle", "Merkle Root 불일치 (거래 내용 변조)"),
                ("pow", f"작업 증명 또는 높이별 최소 난이도 미충족 (기대 {self.difficulty_for_height(block.index)})"),
                ("link", "이전 블록 연결·높이·생성 시각 불일치")):
            if not checks[key]:
                reasons.append(reason)
        txs = block.transactions
        if not txs or len(txs) > MAX_TX_PER_BLOCK or txs[0].tx_type != COINBASE:
            checks["txs"] = False
            reasons.append("첫 거래는 coinbase여야 하며 거래 수 제한을 지켜야 합니다")
        trial, seen = state.copy(), set()
        for index, tx in enumerate(txs):
            if index > 0 and tx.tx_type == COINBASE:
                checks["txs"] = False
                reasons.append(f"tx#{index}: coinbase는 블록당 1개만 허용")
                continue
            try:
                txid = tx.txid()
            except (TypeError, ValueError, OverflowError):
                checks["txs"] = False
                reasons.append(f"tx#{index}: 거래 직렬화 형식 오류")
                continue
            if txid in seen:
                checks["txs"] = False
                reasons.append(f"tx#{index}: 블록 안의 중복 거래")
                continue
            seen.add(txid)
            ok, why = trial.apply(tx)
            if not ok:
                checks["txs"] = False
                reasons.append(f"tx#{index}: {why}")
        if all(checks.values()):
            state.replace(trial)
        return checks, reasons

    def check_genesis(self, block):
        checks = dict.fromkeys(("hash", "merkle", "pow", "link", "txs"), False)
        if not isinstance(block, Block) or block.format_error():
            return checks, ["Genesis 형식 오류"]
        try:
            checks.update({"hash": block.hash == block.calculate_hash(),
                           "merkle": block.merkle_root == block.calculate_merkle_root(),
                           "pow": block.meets_difficulty(),
                           "link": block.to_dict() == GENESIS.to_dict(),
                           "txs": len(block.transactions) == 0})
        except (TypeError, ValueError, OverflowError):
            return checks, ["Genesis 데이터 형식 오류"]
        labels = {"hash": "해시 불일치", "merkle": "Merkle Root 불일치", "pow": "PoW 미충족",
                  "link": "표준 Genesis Block과 다름", "txs": "Genesis에 거래가 있음"}
        return checks, [labels[k] for k, value in checks.items() if not value]

    def validate_chain(self):
        if not self.chain:
            return False, []
        state, results = State(), []
        for index, block in enumerate(self.chain):
            checks, reasons = (self.check_genesis(block) if index == 0
                               else self.check_block(block, self.chain[index - 1], state))
            results.append({"index": getattr(block, "index", index), "checks": checks, "reasons": reasons,
                            "valid": all(checks.values())})
        return all(result["valid"] for result in results), results

    def is_valid(self):
        return self.validate_chain()[0]

    def add_block(self, block):
        if not self.is_valid():
            return False, "기존 체인이 유효하지 않습니다. 먼저 동기화로 복구하세요"
        checks, reasons = self.check_block(block, self.tip, self.state())
        if not all(checks.values()):
            return False, "; ".join(reasons)
        self.chain.append(block.copy())
        return True, "ok"
