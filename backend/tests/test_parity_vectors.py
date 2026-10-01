"""TypeScript 핵심 로직(v1.1.0)이 만든 기준값을 Python 이식이 그대로 재현하는지 확인한다."""

import pytest
from conftest import mined

from mini_bitcoin_backend.core import experiments
from mini_bitcoin_backend.core.block import (
    calculate_nonce,
    create_candidate,
    header_text,
    target_hex,
    transaction_digest,
    validate_chain,
)
from mini_bitcoin_backend.core.crypto import (
    create_wallet,
    hash_text,
    sign_transaction,
    signing_text,
    verify_signature,
)
from mini_bitcoin_backend.core.lab import Lab
from mini_bitcoin_backend.core.transaction import (
    balance,
    build_transaction,
    parse_transaction,
    reserved_inputs,
    validate_transaction,
)
from mini_bitcoin_backend.core.types import NODE_IDS
from mini_bitcoin_backend.core.wire import to_wire


@pytest.fixture
def wallets(vectors):
    return [create_wallet(w["name"], w["privateKey"]) for w in vectors["wallets"]]


@pytest.fixture
def lab(vectors, wallets):
    meta = vectors["meta"]
    return Lab(wallets, meta["difficulty"], meta["genesisTimestamp"], vectors["sessionId"])


def test_hash_text(vectors):
    for case in vectors["hashText"]:
        assert hash_text(case["input"]) == case["hash"]


def test_targets(vectors):
    for case in vectors["targets"]:
        assert target_hex(case["difficulty"]) == case["target"]


def test_wallets(vectors, wallets):
    for expected, wallet in zip(vectors["wallets"], wallets, strict=True):
        assert wallet.private_key == expected["privateKey"]
        assert wallet.public_key == expected["publicKey"]
        assert wallet.address == expected["address"]


def test_genesis(vectors, lab):
    assert lab.session_id == hash_text("parity-v1") == vectors["sessionId"]
    assert to_wire(lab.genesis) == vectors["genesis"]


def test_transfer_signing(vectors, lab, wallets):
    expected = vectors["transfer"]
    alice, bob = wallets[0], wallets[1]
    unsigned = build_transaction(alice, bob.address, 30, lab.nodes["A"].utxos, [], lab.session_id)
    assert to_wire(unsigned) == expected["unsigned"]
    assert signing_text(unsigned) == expected["signingText"]
    assert hash_text(signing_text(unsigned)) == expected["signingHash"]
    signed = sign_transaction(unsigned, alice.private_key)
    assert to_wire(signed) == expected["signed"]
    result = validate_transaction(signed, lab.nodes["A"].utxos, lab.session_id)
    assert to_wire(result) == expected["validation"]


@pytest.mark.parametrize("case", ["highS", "forged", "wrongKey"])
def test_rejected_signatures(vectors, lab, case):
    expected = vectors[case]
    tx = parse_transaction(expected["transaction"])
    assert tx is not None
    assert verify_signature(tx) is expected["verifies"] is False
    result = validate_transaction(expected["transaction"], lab.nodes["A"].utxos, lab.session_id)
    assert to_wire(result) == expected["validation"]


def test_typescript_signature_verifies(vectors):
    tx = parse_transaction(vectors["transfer"]["signed"])
    assert tx is not None and verify_signature(tx)


def test_forged_signature_matches_experiment(vectors):
    signed = parse_transaction(vectors["transfer"]["signed"])
    assert to_wire(experiments.forge_signature(signed)) == vectors["forged"]["transaction"]


def test_candidate_hashes(vectors):
    expected = vectors["candidate"]
    step = next(s for s in vectors["steps"] if s["op"] == "mine")
    candidate = _block(expected["block"])
    assert header_text(candidate) == expected["headerText"]
    assert [calculate_nonce(candidate, n).hash for n in range(16)] == expected["hashes"]
    assert to_wire(mined(candidate)) == step["block"]


def _block(wire: dict):
    """후보 블록(hash가 빈 문자열)을 포함해 기준값의 블록을 객체로 바꾼다."""
    from mini_bitcoin_backend.core.types import Block, TxOutput

    transactions = tuple(parse_transaction(t) for t in wire["transactions"])
    assert all(t is not None for t in transactions)
    allocations = wire.get("allocations")
    return Block(
        session_id=wire["sessionId"],
        height=wire["height"],
        previous_hash=wire["previousHash"],
        transactions_hash=wire["transactionsHash"],
        timestamp=wire["timestamp"],
        difficulty=wire["difficulty"],
        nonce=wire["nonce"],
        transactions=transactions,
        hash=wire["hash"],
        allocations=None
        if allocations is None
        else tuple(TxOutput(a["address"], a["amount"]) for a in allocations),
    )


def _replay(vectors, lab, wallets):
    """기준값의 시나리오를 같은 순서로 실행하며 단계마다 결과를 비교한다."""
    transactions = []
    for index, step in enumerate(vectors["steps"]):
        op = step["op"]
        where = f"step {index} ({op})"
        if op == "submit":
            validation = lab.submit(step["node"], step["transaction"])
            transactions.append(parse_transaction(step["transaction"]))
        elif op == "doubleSpend":
            conflict, _, validation = experiments.double_spend(lab, step["node"])
            assert to_wire(conflict) == step["conflict"], where
        elif op == "invalidPow":
            block, validation = experiments.invalid_pow_block(lab, step["node"], step["timestamp"])
            assert to_wire(block) == step["block"], where
        elif op == "deliver":
            validation = lab.deliver_next()
        elif op == "mine":
            node = lab.nodes[step["node"]]
            candidate = create_candidate(node.chain[-1], node.mempool, step["timestamp"])
            assert to_wire(candidate) == step["candidate"], where
            block = mined(candidate)
            assert to_wire(block) == step["block"], where
            validation = lab.accept_mined(step["node"], block)
        elif op == "build":
            node = lab.nodes[step["node"]]
            sender, recipient = wallets[step["sender"]], wallets[step["recipient"]]
            draft = build_transaction(
                sender, recipient.address, step["amount"], node.utxos, node.mempool, lab.session_id
            )
            assert to_wire(draft) == step["draft"], where
            assert to_wire(sign_transaction(draft, sender.private_key)) == step["signed"], where
            validation = "skip"
        else:
            raise AssertionError(f"알 수 없는 단계: {op}")
        if validation != "skip":
            assert to_wire(validation) == step["validation"], where
        queue = [{"id": m.id, "from": m.sender, "to": m.to, "kind": m.kind} for m in lab.queue]
        assert queue == step["queue"], where
        assert lab.events[-1].id == step["lastEventId"], where
    return [t for t in transactions if t is not None]


def test_scenario_and_final_state(vectors, lab, wallets):
    transactions = _replay(vectors, lab, wallets)
    final = vectors["final"]
    for node_id in NODE_IDS:
        node, expected = lab.nodes[node_id], final["nodes"][node_id]
        assert to_wire(node.chain) == expected["chain"]
        assert to_wire(node.utxos) == expected["utxos"]
        assert to_wire(node.mempool) == expected["mempool"]
        assert to_wire(node.last_validation) == expected["lastValidation"]
        assert to_wire(validate_chain(node.chain, lab.genesis)) == expected["chainValidation"]
        for wallet in wallets:
            assert expected["balances"][wallet.address] == {
                "confirmed": balance(node.utxos, wallet.address),
                "spendable": balance(node.utxos, wallet.address, reserved_inputs(node.mempool)),
            }
    assert to_wire(lab.queue) == final["queue"]
    assert to_wire(lab.events) == final["events"]
    assert to_wire(lab.last_validation) == final["lastValidation"]
    assert to_wire(lab.last_delivery) == final["lastDelivery"]

    unique = list(dict.fromkeys(transactions))
    digests = vectors["digests"]
    assert transaction_digest(unique[:1]) == digests["one"]
    assert transaction_digest(unique[:2]) == digests["two"]
    assert transaction_digest(unique[:3]) == digests["three"]


def test_pending_balance(vectors, lab, wallets):
    alice = wallets[0]
    lab.submit("A", vectors["transfer"]["signed"])
    node = lab.nodes["A"]
    assert vectors["pendingAliceBalance"] == {
        "confirmed": balance(node.utxos, alice.address),
        "spendable": balance(node.utxos, alice.address, reserved_inputs(node.mempool)),
    }


def test_tamper_reports(vectors, lab, wallets):
    _replay(vectors, lab, wallets)
    chain = lab.nodes["A"].chain
    for expected in vectors["tamper"]:
        report = experiments.tamper(
            chain,
            lab.genesis,
            expected["height"],
            expected["mode"],
            expected["amount"] if expected["mode"] == "transaction" else None,
            expected["recompute"],
        )
        where = f"{expected['mode']} @ {expected['height']} recompute={expected['recompute']}"
        assert to_wire(report.altered) == expected["altered"], where
        assert report.recomputed_hash == expected["recomputedHash"], where
        assert [list(c) for c in report.checks] == expected["checks"], where
        assert to_wire(report.chain_validation) == expected["chainValidation"], where
    assert to_wire(chain) == vectors["final"]["nodes"]["A"]["chain"]
