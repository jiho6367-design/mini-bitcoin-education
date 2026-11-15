# 카카오톡 추가 패키지 실행 확인

2026-11-15 카카오톡으로 받은 `추가.zip`의 파일을 원본 바이트 그대로 보관한 독립 실험 폴더입니다. 브랜치는 `codex/kakao-flask-run`이며, 기존 루트 React 앱과 별도로 구성했습니다.

## 현재 결과: 핵심 파일 누락으로 실행 불가

Python 3.13 가상환경을 만들고 Flask 3.1.3을 설치한 뒤 확인했습니다.

- `python -m unittest discover -s tests -v`: 두 테스트 모듈 모두 import 실패. `block`, `blockchain` 모듈을 찾을 수 없습니다. 실제 테스트 케이스는 실행되지 않았습니다.
- `python app.py`: `ModuleNotFoundError: No module named 'blockchain'`로 종료됩니다. 웹 서버는 시작되지 않았습니다.

ZIP의 코드가 직접 import하지만 패키지에 없는 파일은 아래 여섯 개입니다.

```text
block.py
blockchain.py
mempool.py
miner.py
transaction.py
wallet.py
```

이 파일들이 의존하는 추가 모듈도 원래 전체 프로젝트에서 함께 받아야 합니다. 저장소의 `feat/python-backend` 브랜치는 다른 UTXO 기반 API를 사용하므로 이 파일들을 대체할 수 없습니다. Downloads, PycharmProjects, OneDrive 문서의 관련 파일명 검색에서도 호환 원본은 발견하지 못했습니다.

## 받은 파일

```text
app.py
node.py
static/architecture.html
static/glossary.js
static/index.html
tests/test_core.py
tests/test_mempool_miner.py
```

원본 ZIP SHA-256:

```text
66282836f2b6ed7c33debf21e32a9a3f85305e101a165d52f5b16654444441ae
```

코드와 화면에는 지갑 생성, 거래 작성·서명·제출, Mempool, PoW 채굴, 체인 변조·검증, A/B/C 노드 전파·동기화 기능이 정의되어 있습니다. 핵심 모듈 누락으로 기능 동작은 검증하지 못했습니다.

## 전체 원본을 받은 뒤 재시도

이 폴더에 누락된 원본 모듈을 보완한 후 PowerShell에서 실행합니다. 원래 프로젝트의 의존성 목록이 있으면 함께 적용해야 합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe app.py
```

테스트가 통과하고 서버가 시작되면 `http://127.0.0.1:5000`으로 접속합니다. 첨부의 `app.py`는 로컬 주소에만 바인딩하며, 가상환경과 Python 캐시는 저장소의 기존 `.gitignore`에 의해 제외됩니다.
