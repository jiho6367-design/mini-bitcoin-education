# Mini Bitcoin Education Lab — 백엔드

교육용 Mini Bitcoin의 모든 규칙을 가진 Python 서비스입니다. 화면(`../frontend`)은 이 서비스의 HTTP API만 호출합니다. 전체 안내는 [저장소 README](../README.md)를 참고하세요.

## 실행

```powershell
uv sync
uv run mini-bitcoin-backend            # http://127.0.0.1:8000
uv run mini-bitcoin-backend --port 8173
```

실행 중에는 `http://127.0.0.1:8000/api/docs`에서 경로 목록을 볼 수 있습니다. 서버는 `127.0.0.1`에서만 요청을 받고, 실습 상태는 메모리에만 보관합니다. 서버를 끄면 모든 지갑·체인이 사라집니다.

## 구조

| 위치 | 내용 |
|---|---|
| `src/mini_bitcoin_backend/core/` | 순수 Python 규칙. FastAPI에 의존하지 않음 |
| `core/types.py` | 지갑·거래·UTXO·블록·검증 결과·노드 상태 |
| `core/crypto.py` | SHA-256, secp256k1 키와 ECDSA 서명·검증 |
| `core/transaction.py` | 거래 작성, UTXO 선택, 거래 검증 9단계 |
| `core/block.py` | 헤더·PoW·블록 검증·체인 검증 |
| `core/lab.py` | 독립 노드 A·B·C와 메시지 대기열 |
| `core/mining.py` | 채굴 작업(한 번 계산·빠른 실행·학습 실행·일시정지·중지) |
| `core/experiments.py` | 잘못된 서명·이중 지출·잘못된 PoW·변조 실험 |
| `service/` | 실습 하나의 상태 보관, 화면용 상태(JSON) 구성 |
| `api/` | FastAPI 경로와 오류 응답 형식 |

## 검사

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

`tests/vectors/ts-core-v1.json`은 버전 1.1.0의 TypeScript 핵심 로직이 만든 기준값입니다. `tests/test_parity_vectors.py`가 이 Python 구현이 같은 주소·서명·거래 ID·블록 해시·검증 결과를 재현하는지 확인합니다.

화면 단위 테스트가 사용하는 예시 상태는 이 백엔드로 만듭니다. 응답 형태를 바꿨다면 다시 생성하세요.

```powershell
uv run python scripts/export_frontend_fixtures.py
```
