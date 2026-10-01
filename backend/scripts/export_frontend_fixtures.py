"""화면 단위 테스트가 사용하는 실습 상태 예시를 백엔드의 실제 규칙으로 만든다.

    uv run python scripts/export_frontend_fixtures.py

고정 키·실습 ID·시각을 사용하므로 결과는 항상 같다. 저장된 파일이 현재 백엔드와
어긋나면 tests/test_frontend_fixtures.py 가 실패한다.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from mini_bitcoin_backend.core.block import calculate_nonce, create_candidate, valid_pow
from mini_bitcoin_backend.core.crypto import (
    create_wallet,
    hash_text,
    sign_transaction,
    signing_text,
)
from mini_bitcoin_backend.core.lab import Lab
from mini_bitcoin_backend.core.transaction import build_transaction, outpoint
from mini_bitcoin_backend.core.types import Block, NodeId
from mini_bitcoin_backend.core.wire import to_wire
from mini_bitcoin_backend.service.snapshot import lab_snapshot

TARGET = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "test-fixtures"
    / "lab-scenarios.json"
)
ALICE = create_wallet("Alice", "1".rjust(64, "0"))
BOB = create_wallet("Bob", "2".rjust(64, "0"))


class Scenario:
    def __init__(self, name: str) -> None:
        self.lab = Lab([ALICE, BOB], 1, 1000, hash_text(name))
        self.name = name
        self.revision = 0

    def snapshot(self) -> dict[str, Any]:
        self.revision += 1
        return lab_snapshot(self.name, self.revision, self.lab.wallets, self.lab, None)

    def draft(self, node: NodeId = "A", amount: int = 30) -> dict[str, Any]:
        state = self.lab.nodes[node]
        draft = build_transaction(
            ALICE, BOB.address, amount, state.utxos, state.mempool, self.lab.session_id
        )
        text = signing_text(draft)
        return {
            "draft": to_wire(draft),
            "inputs": to_wire([state.utxos[outpoint(i)] for i in draft.inputs]),
            "signingText": text,
            "signingHash": hash_text(text),
        }

    def mine(self, node: NodeId, timestamp: int) -> tuple[Block, Block]:
        state = self.lab.nodes[node]
        candidate = create_candidate(state.chain[-1], state.mempool, timestamp)
        block = calculate_nonce(candidate, 0)
        while not valid_pow(block.hash, block.difficulty):
            block = calculate_nonce(candidate, block.nonce + 1)
        assert self.lab.accept_mined(node, block).valid
        return candidate, block


def learning() -> dict[str, Any]:
    s = Scenario("learning-flow")
    out: dict[str, Any] = {"initial": s.snapshot(), "draft": s.draft()}
    unsigned = build_transaction(
        ALICE, BOB.address, 30, s.lab.nodes["A"].utxos, [], s.lab.session_id
    )
    signed = sign_transaction(unsigned, ALICE.private_key)
    wrong = sign_transaction(unsigned, BOB.private_key)
    out["signed"] = to_wire(signed)
    out["validation"] = to_wire(s.lab.submit("A", signed))
    out["submitted"] = s.snapshot()
    out["wrong"] = to_wire(wrong)
    out["wrongValidation"] = to_wire(s.lab.submit("A", wrong))
    out["afterWrong"] = s.snapshot()
    candidate, block = s.mine("A", 1001)
    out["candidate"] = to_wire(candidate)
    out["block"] = to_wire(block)
    out["minedAtA"] = s.snapshot()
    while len(s.lab.queue) > 1:
        s.lab.deliver_next()
    out["oneMessageLeft"] = s.snapshot()
    s.lab.deliver_next()
    out["delivered"] = s.snapshot()
    out["secondDraft"] = s.draft(amount=10)

    # 다른 노드의 정상 수신이 가장 최근 검증 결과가 된 상태.
    other = Scenario("learning-flow")
    other.lab.submit("A", signed)
    other.lab.submit("A", wrong)
    other.lab.deliver_next()
    out["otherNodeAccepted"] = other.snapshot()
    return out


def network() -> dict[str, Any]:
    s = Scenario("network-display")
    signed = sign_transaction(
        build_transaction(ALICE, BOB.address, 30, s.lab.nodes["A"].utxos, [], s.lab.session_id),
        ALICE.private_key,
    )
    s.lab.submit("A", signed)
    out: dict[str, Any] = {"submitted": s.snapshot()}
    s.lab.deliver_next()
    out["afterFirstDelivery"] = s.snapshot()

    # C가 보낸 거래 사본 하나의 서명을 전달 도중 훼손해 수신 노드의 거부를 만든다.
    c = Scenario("network-display")
    c.lab.submit("C", signed)
    message = c.lab.queue[0]
    assert message.kind == "transaction"
    c.lab.queue[0] = replace(message, payload=replace(message.payload, signature="0" * 128))
    out["invalidMessageId"] = message.id
    assert c.lab.deliver_next().valid is False
    out["rejectedDelivery"] = c.snapshot()
    assert c.lab.deliver_next().valid is True
    out["acceptedDelivery"] = c.snapshot()
    return out


def build() -> dict[str, Any]:
    return {"learning": learning(), "network": network()}


if __name__ == "__main__":
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"기록: {TARGET}")
