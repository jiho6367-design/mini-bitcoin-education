"""[2단계] Transaction: 생성 / 전자서명 / 검증."""
import binascii
import hashlib
import json
import time

import ecdsa

from wallet import address_from_public_key

SYSTEM = "SYSTEM"
TRANSFER, COINBASE, FAUCET = "transfer", "coinbase", "faucet"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _is_hex64(value) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False


class Transaction:
    def __init__(self, tx_type, sender_public_key, receiver_address, amount,
                 nonce=0, timestamp=None, signature=None):
        self.tx_type = tx_type
        self.sender_public_key = sender_public_key or ""
        self.receiver_address = receiver_address
        self.amount = amount
        self.nonce = nonce                       # 송신자 계정의 거래 순번 (재전송 공격 방지)
        self.timestamp = time.time() if timestamp is None else timestamp
        self.signature = signature

    # ---- 식별자 / 해시 -------------------------------------------------
    @property
    def sender_address(self) -> str:
        if not self.sender_public_key:
            return SYSTEM
        try:
            return address_from_public_key(self.sender_public_key)
        except Exception:
            return "INVALID"

    def payload(self) -> dict:
        """서명 대상이 되는 '모든' 필드 (일부만 해시하면 그 외 필드는 변조해도 못 잡는다)."""
        return {"type": self.tx_type, "sender_public_key": self.sender_public_key,
                "receiver_address": self.receiver_address, "amount": self.amount,
                "nonce": self.nonce, "timestamp": self.timestamp}

    def payload_json(self) -> str:
        return json.dumps(self.payload(), sort_keys=True, separators=(",", ":"))

    def signing_hash(self) -> str:
        return _sha256(self.payload_json())

    def txid(self) -> str:
        """거래 ID = 내용 + 서명의 해시. 블록의 Merkle Root 계산에 쓰인다."""
        return _sha256(self.payload_json() + (self.signature or ""))

    # ---- 서명 / 검증 ---------------------------------------------------
    def sign(self, wallet) -> None:
        self.signature = wallet.sign(self.signing_hash())

    def verify_signature(self) -> bool:
        if not self.signature or not self.sender_public_key:
            return False
        try:
            vk = ecdsa.VerifyingKey.from_string(binascii.unhexlify(self.sender_public_key),
                                                curve=ecdsa.SECP256k1, hashfunc=hashlib.sha256)
            return bool(vk.verify(binascii.unhexlify(self.signature),
                                  self.signing_hash().encode(), hashfunc=hashlib.sha256))
        except Exception:   # 형식이 깨진 공개키/서명도 예외 없이 False
            return False

    def basic_check(self):
        """형식 + 서명 검증 (잔액 검증은 State.apply에서 수행)."""
        if self.tx_type not in (TRANSFER, COINBASE, FAUCET):
            return False, "알 수 없는 거래 유형"
        if not isinstance(self.amount, int) or isinstance(self.amount, bool) or self.amount <= 0:
            return False, "금액은 0보다 큰 정수여야 합니다"
        if not _is_hex64(self.receiver_address):
            return False, "수신자 주소 형식 오류"
        if self.tx_type == TRANSFER:
            if not self.sender_public_key:
                return False, "송신자 공개키 없음"
            if not self.signature:
                return False, "전자서명 없음"
            if not self.verify_signature():
                return False, "전자서명 검증 실패 (내용 변조 또는 키 불일치)"
        elif self.sender_public_key or self.signature:
            return False, "시스템 거래(coinbase/faucet)는 서명·공개키를 가질 수 없습니다"
        return True, "ok"

    # ---- 직렬화 --------------------------------------------------------
    def to_dict(self) -> dict:
        d = self.payload()
        d.update({"signature": self.signature, "txid": self.txid(), "sender_address": self.sender_address})
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        return cls(d["type"], d.get("sender_public_key", ""), d["receiver_address"], d["amount"],
                   d.get("nonce", 0), d.get("timestamp"), d.get("signature"))

    def copy(self) -> "Transaction":
        return Transaction.from_dict(self.to_dict())