"""[4~5단계] Block: Merkle Root, Hash, Nonce 탐색(PoW)."""
import hashlib
import time

from transaction import Transaction


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Block:
    def __init__(self, index, previous_hash, transactions, difficulty,
                 timestamp=None, nonce=0, merkle_root=None, block_hash=None):
        self.index = index
        self.previous_hash = previous_hash
        self.transactions = list(transactions)
        self.difficulty = difficulty
        self.timestamp = time.time() if timestamp is None else timestamp
        self.nonce = nonce
        self.merkle_root = merkle_root if merkle_root is not None else self.calculate_merkle_root()
        self.hash = block_hash if block_hash is not None else self.calculate_hash()

    def calculate_merkle_root(self) -> str:
        """모든 거래(내용+서명)가 Merkle Root 하나로 요약된다. 거래 1비트만 바뀌어도 값이 달라진다."""
        layer = [tx.txid() for tx in self.transactions] or [sha256("")]
        while len(layer) > 1:
            if len(layer) % 2:
                layer.append(layer[-1])
            layer = [sha256(layer[i] + layer[i + 1]) for i in range(0, len(layer), 2)]
        return layer[0]

    def header_prefix(self) -> str:
        """nonce를 제외한 헤더. 채굴 중에는 nonce만 바꿔 가며 해시를 반복 계산한다."""
        return f"{self.index}|{self.timestamp}|{self.previous_hash}|{self.merkle_root}|{self.difficulty}|"

    def calculate_hash(self) -> str:
        return sha256(self.header_prefix() + str(self.nonce))

    def meets_difficulty(self) -> bool:
        return self.difficulty >= 1 and self.hash.startswith("0" * self.difficulty)

    def mine_to_target(self) -> int:
        """한 번에 끝까지 채굴 (Genesis 생성, 변조 시뮬레이션용). 시도 횟수를 반환."""
        prefix, target, attempts = self.header_prefix(), "0" * self.difficulty, 0
        self.nonce = 0
        while True:
            h = sha256(prefix + str(self.nonce))
            attempts += 1
            if h.startswith(target):
                self.hash = h
                return attempts
            self.nonce += 1

    def to_dict(self) -> dict:
        return {"index": self.index, "timestamp": self.timestamp, "previous_hash": self.previous_hash,
                "merkle_root": self.merkle_root, "difficulty": self.difficulty, "nonce": self.nonce,
                "hash": self.hash, "transactions": [tx.to_dict() for tx in self.transactions]}

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(d["index"], d["previous_hash"], [Transaction.from_dict(t) for t in d["transactions"]],
                   d["difficulty"], d["timestamp"], d["nonce"], d["merkle_root"], d["hash"])

    def copy(self) -> "Block":
        return Block.from_dict(self.to_dict())