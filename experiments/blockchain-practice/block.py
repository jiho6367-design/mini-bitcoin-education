"""교육용 블록 헤더·Merkle Root·SHA-256 작업증명."""
import hashlib
import math
import time
from transaction import Transaction


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Block:
    def __init__(self, index, previous_hash, transactions, difficulty,
                 timestamp=None, nonce=0, merkle_root=None, block_hash=None):
        self.index, self.previous_hash = index, previous_hash
        self.transactions, self.difficulty = list(transactions), difficulty
        self.timestamp = time.time() if timestamp is None else timestamp
        self.nonce = nonce
        self.merkle_root = merkle_root if merkle_root is not None else self.calculate_merkle_root()
        self.hash = block_hash if block_hash is not None else self.calculate_hash()

    def format_error(self):
        if type(self.index) is not int or self.index < 0:
            return "블록 높이는 음이 아닌 정수여야 합니다"
        if type(self.difficulty) is not int or not 1 <= self.difficulty <= 5:
            return "블록 난이도는 1~5의 정수여야 합니다"
        if type(self.nonce) is not int or self.nonce < 0:
            return "채굴 Nonce는 음이 아닌 정수여야 합니다"
        try:
            valid_time = type(self.timestamp) in (int, float) and math.isfinite(self.timestamp) and self.timestamp >= 0
        except OverflowError:
            valid_time = False
        if not valid_time:
            return "블록 시각 형식 오류"
        for value in (self.hash, self.merkle_root):
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                return "블록 해시 또는 Merkle Root 형식 오류"
        if not isinstance(self.previous_hash, str):
            return "이전 블록 해시 형식 오류"
        if self.index > 0 and (len(self.previous_hash) != 64 or any(c not in "0123456789abcdef" for c in self.previous_hash)):
            return "이전 블록 해시 형식 오류"
        if not isinstance(self.transactions, list) or not all(isinstance(t, Transaction) for t in self.transactions):
            return "블록 거래 목록 형식 오류"
        return None

    def calculate_merkle_root(self):
        layer = [tx.txid() for tx in self.transactions] or [sha256("")]
        while len(layer) > 1:
            if len(layer) % 2:
                layer.append(layer[-1])
            layer = [sha256(layer[i] + layer[i + 1]) for i in range(0, len(layer), 2)]
        return layer[0]

    def header_prefix(self):
        return f"{self.index}|{self.timestamp}|{self.previous_hash}|{self.merkle_root}|{self.difficulty}|"

    def calculate_hash(self):
        return sha256(self.header_prefix() + str(self.nonce))

    def meets_difficulty(self):
        return (type(self.difficulty) is int and 1 <= self.difficulty <= 5
                and isinstance(self.hash, str) and self.hash.startswith("0" * self.difficulty))

    def mine_to_target(self):
        if type(self.difficulty) is not int or not 1 <= self.difficulty <= 5:
            raise ValueError("블록 난이도는 1~5의 정수여야 합니다")
        prefix, target, attempts = self.header_prefix(), "0" * self.difficulty, 0
        self.nonce = 0
        while True:
            h = sha256(prefix + str(self.nonce))
            attempts += 1
            if h.startswith(target):
                self.hash = h
                return attempts
            self.nonce += 1

    def to_dict(self):
        return {"index": self.index, "timestamp": self.timestamp, "previous_hash": self.previous_hash,
                "merkle_root": self.merkle_root, "difficulty": self.difficulty, "nonce": self.nonce,
                "hash": self.hash, "transactions": [tx.to_dict() for tx in self.transactions]}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or not isinstance(data.get("transactions"), list):
            raise ValueError("블록 데이터 형식 오류")
        try:
            return cls(data["index"], data["previous_hash"], [Transaction.from_dict(t) for t in data["transactions"]],
                       data["difficulty"], data["timestamp"], data["nonce"], data["merkle_root"], data["hash"])
        except (KeyError, TypeError) as exc:
            raise ValueError("블록 데이터 형식 오류") from exc

    def copy(self):
        return Block.from_dict(self.to_dict())
