"""요청 본문의 형태.

금액·거래처럼 검증 규칙이 판정해야 하는 값은 Any로 받아 그대로 핵심 로직에 넘긴다.
형식이 잘못된 값도 HTTP 오류가 아니라 한국어 검증 결과나 실습 오류로 설명하기 위해서다.
"""

from typing import Any, Literal

from pydantic import BaseModel

from ..core.types import NodeId


class CreateLabRequest(BaseModel):
    mode: Literal["example", "manual"]
    difficulty: Any = 2


class StartLabRequest(BaseModel):
    difficulty: Any = 2


class DraftRequest(BaseModel):
    senderAddress: Any = None
    recipientAddress: Any = None
    amount: Any = None


class SignRequest(BaseModel):
    draft: Any = None


class TransactionRequest(BaseModel):
    transaction: Any = None


class MiningRequest(BaseModel):
    nodeId: NodeId
    mode: Literal["fast", "learning"] = "fast"
    step: bool = False


class TamperRequest(BaseModel):
    height: Any = None
    mode: Any = None
    amount: Any = None
    recomputeHashes: bool = False
