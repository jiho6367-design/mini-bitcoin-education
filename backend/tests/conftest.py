import json
from pathlib import Path

import pytest

from mini_bitcoin_backend.core.block import calculate_nonce, valid_pow
from mini_bitcoin_backend.core.crypto import create_wallet
from mini_bitcoin_backend.core.types import Block

VECTORS = Path(__file__).parent / "vectors" / "ts-core-v1.json"


def fixed_key(n: str) -> str:
    return n.rjust(64, "0")


@pytest.fixture(scope="session")
def vectors() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def alice():
    return create_wallet("Alice", fixed_key("1"))


@pytest.fixture(scope="session")
def bob():
    return create_wallet("Bob", fixed_key("2"))


@pytest.fixture(scope="session")
def carol():
    return create_wallet("Carol", fixed_key("3"))


def mined(candidate: Block) -> Block:
    for n in range(1_000_000):
        b = calculate_nonce(candidate, n)
        if valid_pow(b.hash, b.difficulty):
            return b
    raise AssertionError("고정 테스트 벡터의 Nonce 탐색 한도를 초과했습니다.")
