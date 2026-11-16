"""[1단계] Wallet: 개인키/공개키/주소 생성과 서명."""
import binascii
import hashlib

import ecdsa


def address_from_public_key(public_key_hex: str) -> str:
    """공개키 -> 주소 (학습용: SHA-256 한 번. 실제 비트코인은 RIPEMD-160 + Base58Check)."""
    return hashlib.sha256(binascii.unhexlify(public_key_hex)).hexdigest()


class Wallet:
    def __init__(self, name: str = "", private_key_hex: str = None):
        if private_key_hex:
            self._sk = ecdsa.SigningKey.from_string(
                binascii.unhexlify(private_key_hex), curve=ecdsa.SECP256k1, hashfunc=hashlib.sha256)
        else:
            self._sk = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1, hashfunc=hashlib.sha256)
        self.name = name
        self.public_key = binascii.hexlify(self._sk.get_verifying_key().to_string()).decode()
        self.address = address_from_public_key(self.public_key)

    @property
    def private_key(self) -> str:
        return binascii.hexlify(self._sk.to_string()).decode()

    def sign(self, message: str) -> str:
        """개인키로 메시지(문자열)에 서명하고 hex 문자열로 반환."""
        sig = self._sk.sign_deterministic(message.encode(), hashfunc=hashlib.sha256,
                                          sigencode=ecdsa.util.sigencode_string_canonize)
        return binascii.hexlify(sig).decode()

    def to_dict(self):
        return {"name": self.name, "private_key": self.private_key,
                "public_key": self.public_key, "address": self.address}

    @classmethod
    def from_dict(cls, data):
        if (not isinstance(data, dict) or not isinstance(data.get("name", ""), str)
                or not isinstance(data.get("private_key"), str) or len(data["private_key"]) != 64):
            raise ValueError("저장된 지갑 데이터 형식 오류")
        try:
            wallet = cls(data.get("name", ""), data["private_key"])
        except (ValueError, binascii.Error, ecdsa.errors.MalformedPointError) as exc:
            raise ValueError("저장된 지갑 개인키 형식 오류") from exc
        if data.get("address", wallet.address) != wallet.address or data.get("public_key", wallet.public_key) != wallet.public_key:
            raise ValueError("저장된 지갑의 개인키·공개키·주소가 일치하지 않습니다")
        return wallet
