"""CPU hashcash scanner compatible with the original Nonce UTF-8 input rule."""

from __future__ import annotations

from hashlib import sha256
from typing import TypedDict

MAX_NONCE = (1 << 64) - 1


class ScanResult(TypedDict):
    checked: int
    nonce: int | None
    hash: str | None
    last_nonce: int
    last_hash: str


def validate_difficulty(value: int) -> int:
    """Difficulty counts leading zero hexadecimal digits, from 1 to 64."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 64:
        raise ValueError("난이도는 1~64 사이의 정수여야 합니다.")
    return value


def validate_nonce(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_NONCE:
        raise ValueError("Nonce는 0~18446744073709551615 사이의 정수여야 합니다.")
    return value


def _message_bytes(message: str) -> bytes:
    if not isinstance(message, str):
        raise ValueError("메시지는 문자열이어야 합니다.")
    try:
        return message.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("메시지에 올바르지 않은 Unicode 문자가 있습니다.") from error


def proof_hash(message: str, nonce: int) -> str:
    """Hash UTF8(message) followed directly by the nonce's decimal ASCII text."""
    data = _message_bytes(message)
    validate_nonce(nonce)
    return sha256(data + str(nonce).encode("ascii")).hexdigest()


def meets_difficulty(digest: bytes, difficulty: int) -> bool:
    validate_difficulty(difficulty)
    if not isinstance(digest, bytes) or len(digest) != 32:
        raise ValueError("SHA-256 digest는 32바이트여야 합니다.")
    full_bytes, odd_digit = divmod(difficulty, 2)
    return digest.startswith(bytes(full_bytes)) and (
        not odd_digit or digest[full_bytes] < 0x10
    )


def scan_batch(
    message: str,
    difficulty: int,
    start: int,
    count: int,
    *,
    search: bool = True,
    step: int = 1,
) -> ScanResult:
    """Scan an increasing nonce range, returning the first matching proof.

    ``search=False`` still checks every requested nonce after finding a proof.
    This supports fixed-work benchmarks whose duration does not depend on luck.
    ``step`` partitions the same nonce range among CPU processes without overlap.
    """
    data = _message_bytes(message)
    validate_difficulty(difficulty)
    validate_nonce(start)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("탐색 개수는 1 이상의 정수여야 합니다.")
    if isinstance(step, bool) or not isinstance(step, int) or step < 1:
        raise ValueError("Nonce 간격은 1 이상의 정수여야 합니다.")
    if not isinstance(search, bool):
        raise ValueError("search는 bool 값이어야 합니다.")
    validate_nonce(start + step * (count - 1))

    # Copy the already-encoded message's hash state. This also reuses compressed
    # full message blocks when a message is longer than SHA-256's 64-byte block.
    copy_prefix = sha256(data).copy
    full_bytes, odd_digit = divmod(difficulty, 2)
    zero_prefix = bytes(full_bytes)
    # Most candidates fail on their first byte. Avoid a Python leading-zero
    # counting function and full hexadecimal rendering in that hot path.
    first_limit = 0x10 if difficulty == 1 else 1
    found_nonce: int | None = None
    found_hash: str | None = None
    for nonce in range(start, start + step * count, step):
        hasher = copy_prefix()
        hasher.update(str(nonce).encode("ascii"))
        digest = hasher.digest()
        if digest[0] < first_limit and digest.startswith(zero_prefix) and (
            not odd_digit or digest[full_bytes] < 0x10
        ):
            if found_nonce is None:
                found_nonce = nonce
                found_hash = digest.hex()
                # Independently reconstruct the source's exact input for every
                # proof, so reported results can always be checked separately.
                if proof_hash(message, nonce) != found_hash:
                    raise RuntimeError("찾은 Nonce의 SHA-256 재검증에 실패했습니다.")
            if search:
                checked = (nonce - start) // step + 1
                return {
                    "checked": checked,
                    "nonce": found_nonce,
                    "hash": found_hash,
                    "last_nonce": nonce,
                    "last_hash": digest.hex(),
                }

    return {
        "checked": count,
        "nonce": found_nonce,
        "hash": found_hash,
        "last_nonce": nonce,
        "last_hash": digest.hex(),
    }
