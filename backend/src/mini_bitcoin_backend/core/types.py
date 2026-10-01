"""실습에서 주고받는 데이터 구조.

거래·블록처럼 서명·해시의 대상이 되는 값은 수정할 수 없는(frozen) 객체로 둔다.
노드가 값을 넘겨받아도 원본이 바뀌지 않으므로 별도의 복사 없이 독립성을 유지한다.
"""

from dataclasses import dataclass, field
from typing import Literal

NodeId = Literal["A", "B", "C"]
NODE_IDS: tuple[NodeId, ...] = ("A", "B", "C")
MessageKind = Literal["transaction", "block"]


@dataclass(frozen=True, slots=True)
class Wallet:
    name: str
    private_key: str
    public_key: str
    address: str


@dataclass(frozen=True, slots=True)
class TxInput:
    tx_id: str
    output_index: int


@dataclass(frozen=True, slots=True)
class TxOutput:
    address: str
    amount: int


@dataclass(frozen=True, slots=True)
class UnsignedTransaction:
    version: int
    session_id: str
    inputs: tuple[TxInput, ...]
    outputs: tuple[TxOutput, ...]
    public_key: str


@dataclass(frozen=True, slots=True)
class Transaction(UnsignedTransaction):
    signature: str
    id: str


@dataclass(frozen=True, slots=True)
class UTXO:
    tx_id: str
    output_index: int
    address: str
    amount: int


# "txId:outputIndex" → 아직 쓰지 않은 출력
UtxoSet = dict[str, UTXO]


@dataclass(frozen=True, slots=True)
class Block:
    session_id: str
    height: int
    previous_hash: str
    transactions_hash: str
    timestamp: int
    difficulty: int
    nonce: int
    transactions: tuple[Transaction, ...]
    hash: str
    # Genesis에만 있는 초기 배정. 일반 블록에 있으면 형식 오류다.
    allocations: tuple[TxOutput, ...] | None = None


@dataclass(frozen=True, slots=True)
class Check:
    label: str
    ok: bool
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    code: str
    message: str
    steps: tuple[Check, ...] = ()


@dataclass(slots=True)
class NodeState:
    id: NodeId
    chain: list[Block]
    utxos: UtxoSet
    mempool: list[Transaction] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    last_validation: ValidationResult | None = None


@dataclass(frozen=True, slots=True)
class NetworkMessage:
    id: int
    sender: NodeId
    to: NodeId
    kind: MessageKind
    payload: Transaction | Block


@dataclass(frozen=True, slots=True)
class Delivery:
    message: NetworkMessage
    validation: ValidationResult


@dataclass(frozen=True, slots=True)
class LabEvent:
    id: int
    node: NodeId | Literal["LAB"]
    kind: str
    message: str
    ok: bool
