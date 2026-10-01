"""내부 객체를 API·테스트에서 쓰는 JSON 형태(camelCase)로 바꾼다."""

from dataclasses import fields, is_dataclass
from typing import Any

from .types import Wallet

_RENAMED = {"sender": "from"}


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def to_wire(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Wallet):
        # 개인키가 실수로 응답·메시지·이벤트에 섞이지 않도록 일반 변환을 막는다.
        raise TypeError("Wallet은 public_wallet()으로만 직렬화합니다.")
    if is_dataclass(value):
        wire: dict[str, Any] = {}
        for f in fields(value):
            item = getattr(value, f.name)
            if f.name == "allocations" and item is None:
                continue
            wire[_RENAMED.get(f.name, _camel(f.name))] = to_wire(item)
        return wire
    if isinstance(value, dict):
        return {key: to_wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_wire(item) for item in value]
    raise TypeError(f"직렬화할 수 없는 값: {type(value).__name__}")


def public_wallet(wallet: Wallet) -> dict[str, str]:
    return {"name": wallet.name, "publicKey": wallet.public_key, "address": wallet.address}
