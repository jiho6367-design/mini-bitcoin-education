"""교육용 발행 거래. 일반 송금 서명과 분리된 블록 검증 규칙."""

from dataclasses import replace
from typing import Any

from .crypto import hash_text, is_address, is_hash
from .encoding import canonical_json, is_positive, is_safe_integer
from .types import Transaction, TxOutput
from .wire import to_wire

SUBSIDY = 50
COINBASE_MATURITY = 100


def issuance_id(tx: Transaction) -> str:
    return hash_text(
        canonical_json(
            [
                tx.kind,
                tx.session_id,
                tx.height,
                tx.extra_nonce,
                [[o.address, o.amount] for o in tx.outputs],
            ]
        )
    )


def issuance(
    session_id: str,
    height: int,
    address: str,
    amount: int,
    extra_nonce: str,
    kind: str = "coinbase",
) -> Transaction:
    tx = Transaction(
        1, session_id, (), (TxOutput(address, amount),), "", "", "", kind, height, extra_nonce
    )
    return replace(tx, id=issuance_id(tx))


def parse_issuance(value: Any) -> Transaction | None:
    if isinstance(value, Transaction):
        value = to_wire(value)
    if not isinstance(value, dict):
        return None
    outputs = value.get("outputs")
    if (
        value.get("kind") not in ("coinbase", "initial")
        or type(value.get("version")) is not int
        or value["version"] != 1
        or not is_hash(value.get("sessionId"))
        or not is_safe_integer(value.get("height"))
        or value["height"] < 0
        or not isinstance(value.get("extraNonce"), str)
        or not 1 <= len(value["extraNonce"]) <= 128
        or value.get("inputs") != []
        or value.get("publicKey") != ""
        or value.get("signature") != ""
        or not isinstance(outputs, list)
        or not 1 <= len(outputs) <= (3 if value.get("kind") == "initial" else 1)
        or any(
            not isinstance(o, dict)
            or not is_address(o.get("address"))
            or not is_positive(o.get("amount"))
            for o in outputs
        )
    ):
        return None
    tx = issuance(
        value["sessionId"],
        value["height"],
        outputs[0]["address"],
        outputs[0]["amount"],
        value["extraNonce"],
        value["kind"],
    )
    tx = replace(tx, outputs=tuple(TxOutput(o["address"], o["amount"]) for o in outputs))
    tx = replace(tx, id=issuance_id(tx))
    return tx if value.get("id") == tx.id else None
