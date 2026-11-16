"""SHA-256 해시, secp256k1 키와 ECDSA 전자서명."""

import hashlib
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .encoding import canonical_json
from .types import Transaction, UnsignedTransaction, Wallet

# secp256k1 곡선의 위수 n
CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_CURVE = ec.SECP256K1()
_HASH = re.compile(r"[0-9a-f]{64}")
_ADDRESS = re.compile(r"mini_[0-9a-f]{64}")


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def address_of(public_key: str) -> str:
    return "mini_" + hashlib.sha256(bytes.fromhex(public_key)).hexdigest()


def is_address(value: Any) -> bool:
    return isinstance(value, str) and _ADDRESS.fullmatch(value) is not None


def is_hash(value: Any) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _private_key(secret: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int(secret, 16), _CURVE)


def create_wallet(name: str, secret: str | None = None) -> Wallet:
    key = _private_key(secret) if secret else ec.generate_private_key(_CURVE)
    public_key = key.public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint).hex()
    return Wallet(
        name=name,
        private_key=f"{key.private_numbers().private_value:064x}",
        public_key=public_key,
        address=address_of(public_key),
    )


def signing_text(tx: UnsignedTransaction) -> str:
    """서명 원문. 필드 순서를 배열로 고정해 같은 거래가 항상 같은 문자열이 되게 한다."""
    return canonical_json(
        [
            tx.version,
            tx.session_id,
            [[i.tx_id, i.output_index] for i in tx.inputs],
            [[o.address, o.amount] for o in tx.outputs],
            tx.public_key,
        ]
    )


def transaction_id(tx: UnsignedTransaction, signature: str) -> str:
    return hash_text(canonical_json([signing_text(tx), signature]))


def _signing_digest(tx: UnsignedTransaction) -> bytes:
    return hashlib.sha256(signing_text(tx).encode("utf-8")).digest()


def sign_transaction(tx: UnsignedTransaction, private_key: str) -> Transaction:
    # 이미 SHA-256 처리한 값에 서명하므로 라이브러리의 추가 해시는 끈다(Prehashed).
    # deterministic_signing: 같은 키와 원문은 항상 같은 서명이 된다(RFC 6979).
    der = _private_key(private_key).sign(
        _signing_digest(tx),
        ec.ECDSA(Prehashed(hashes.SHA256()), deterministic_signing=True),
    )
    r, s = decode_dss_signature(der)
    # (r, s)와 (r, n - s)는 둘 다 유효하므로 작은 쪽 s만 사용해 서명을 하나로 고정한다.
    if s > CURVE_ORDER // 2:
        s = CURVE_ORDER - s
    signature = f"{r:064x}{s:064x}"
    return Transaction(
        version=tx.version,
        session_id=tx.session_id,
        inputs=tx.inputs,
        outputs=tx.outputs,
        public_key=tx.public_key,
        signature=signature,
        id=transaction_id(tx, signature),
    )


def verify_signature(tx: Transaction) -> bool:
    try:
        raw = bytes.fromhex(tx.signature)
        if len(raw) != 64:
            return False
        r = int.from_bytes(raw[:32], "big")
        s = int.from_bytes(raw[32:], "big")
        # 범위를 벗어난 값과 큰 쪽 s(n - s)는 받지 않는다.
        if not (1 <= r < CURVE_ORDER and 1 <= s <= CURVE_ORDER // 2):
            return False
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            _CURVE, bytes.fromhex(tx.public_key)
        )
        public_key.verify(
            encode_dss_signature(r, s),
            _signing_digest(tx),
            ec.ECDSA(Prehashed(hashes.SHA256())),
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
