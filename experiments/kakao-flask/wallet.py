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
        sig = self._sk.sign(message.encode(), hashfunc=hashlib.sha256)
        return binascii.hexlify(sig).decode()