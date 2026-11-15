"""해시 입력을 만드는 직렬화 규칙과 정수 범위 규칙."""

import json
import time
from typing import Any

# 브라우저(JavaScript)가 정확히 표현할 수 있는 정수 범위. 금액·Nonce·시각에 적용한다.
MAX_SAFE_INTEGER = 2**53 - 1


def canonical_json(value: Any) -> str:
    """공백 없는 JSON 문자열. 같은 값은 항상 같은 문자열이 되어 해시 입력으로 쓸 수 있다."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def is_safe_integer(value: Any) -> bool:
    # bool은 int의 하위 타입이고 30.0 == 30 이므로 타입을 정확히 비교한다.
    return type(value) is int and -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER


def is_positive(value: Any) -> bool:
    return is_safe_integer(value) and value > 0


def now_ms() -> int:
    return int(time.time() * 1000)
