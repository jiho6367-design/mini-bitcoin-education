# 카카오톡 교육용 블록체인 앱

2026-11-15 카카오톡으로 받은 `PythonProject1 (2).zip`의 실행 소스를 보관한 독립 실험 폴더입니다. 브랜치는 `codex/kakao-flask-run`이며, 기존 루트 React 앱과 별도로 실행합니다.

## 실행 결과

Python 3.13, Flask 3.1.3, ecdsa 0.19.2로 **실행 및 검증을 완료했습니다.**

- 원본 단위 테스트: 25개 모두 통과.
- 별도 프로세스의 Flask `test_client`: 55개 HTTP 요청 검증 통과. 지갑 생성, faucet, 거래 작성·서명·제출, 채굴, A/B/C 전파, 오프라인 노드 동기화, 변조 검출·복구를 확인했습니다.
- 실제 브라우저: Alice와 Bob 지갑 생성 → 지급 거래 채굴 → Alice에서 Bob으로 20코인 작성·서명·제출 → 두 번째 채굴까지 확인했습니다. 최종 잔액은 Alice 30 / Bob 70이며, 세 노드 모두 높이 2, 같은 tip hash, 유효한 체인을 보유했습니다.
- 웹 서버는 `http://127.0.0.1:5000`에서 동작합니다. 데이터는 서버 메모리에 보관되므로 서버를 재시작하면 초기화됩니다.

## 실행 방법

PowerShell에서 이 폴더로 이동한 후 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe app.py
```

브라우저에서 `http://127.0.0.1:5000`으로 접속합니다. 서버를 실행한 터미널에서 Ctrl+C로 중지할 수 있습니다. `app.py`는 로컬 주소에만 바인딩합니다.

앱 화면은 지갑 생성, 거래 작성·서명·제출, Mempool, PoW 채굴, 체인 변조·검증, A/B/C 노드 전파·동기화를 제공합니다. 빠른 확인에는 난이도 2가 편리하며 원본 기본값은 4입니다. 좁은 화면에서는 상단 고정 메뉴가 높아져 조작 영역을 가릴 수 있어, PC 브라우저의 넓은 창에서 확인하는 편이 좋습니다.

## 가져온 원본과 구성

`PythonProject1 (2).zip`에서 소스 16개를 원본 바이트 그대로 가져왔습니다. 압축에 포함된 가상환경, IDE 설정, 캐시는 가져오지 않았습니다. 의존성은 이 폴더의 새 가상환경에 별도로 설치했습니다.

```text
app.py                  Flask 웹 진입점
block.py                블록·머클 루트·PoW
blockchain.py           체인·잔액·거래 검증
wallet.py               ECDSA 지갑
transaction.py          거래·전자서명
mempool.py              거래 대기열
miner.py                후보 블록·채굴
node.py                 노드 전파·동기화
main.py                 콘솔 진입점
demo.py                 별도 tkinter 데모
mining_gui.py           별도 tkinter 채굴 화면
static/index.html       웹 학습 화면
static/architecture.html
static/glossary.js
tests/test_core.py
tests/test_mempool_miner.py
```

ZIP SHA-256:

```text
e098af6efa01de4fb337d147eef26fdd4a29227a8a984169ea2bcab2fcde7807
```

README와 requirements.txt는 실행 재현을 위해 이 브랜치에서 추가했습니다. 가상환경, Python 캐시, 로그 및 `work/` 검증 산출물은 저장소의 기존 `.gitignore`에 의해 제외됩니다.

## 처음 받은 추가 패키지와의 차이

처음 받은 `추가.zip`에는 app.py, node.py, 정적 파일 3개, 테스트 2개만 들어 있었습니다. 핵심 모듈 6개가 없어 테스트 import와 서버 시작이 실패했습니다. 이후 재전송된 전체 프로젝트에서 누락 파일을 확보했습니다.

겹치는 파일 중 app.py, 정적 파일 3개, 테스트 2개는 두 ZIP에서 바이트가 같습니다. node.py는 중복 거래 검사 한 줄만 다릅니다.

```python
# 추가.zip: 전체 프로젝트의 Mempool에 없는 메서드를 호출
if self.pool.contains(tx):

# 재전송된 전체 프로젝트 원본: 현재 보관·검증한 코드
if any(t.txid() == txid for t in self.pool.txs):
```

전체 프로젝트의 node.py를 함께 사용해야 합니다. 이 폴더의 실행 소스는 재전송된 전체 프로젝트와 동일하며 별도의 기능 수정을 하지 않았습니다.

최초 `추가.zip` SHA-256:

```text
66282836f2b6ed7c33debf21e32a9a3f85305e101a165d52f5b16654444441ae
```

참고로 과거 2026-10-28 업로드된 `PythonProject1.zip`과 개별 코드 `.txt` 파일들은 2026-11-11에 만료됐습니다. 기존 ZIP과 wallet.txt 다운로드는 만료 오류로 실패했지만, 이번에 새로 받은 `PythonProject1 (2).zip`으로 필요한 소스를 확보했습니다.
