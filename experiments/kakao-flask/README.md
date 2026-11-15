# 팀 블록체인 실습실 · Flask 3.0.0

개인 프로젝트에서 만든 UTXO·거래 추적·경쟁 채굴·저장·공동 수업 기능과 화면을 팀 Flask 앱 안으로 가져온 통합판입니다. 개인 프로젝트를 실행하거나 그 DB를 참조하지 않으며, 팀 저장소의 코드와 DB만 사용합니다.

**[무료 온라인 실습실 열기](https://kakao-team-flask.onrender.com)** · **[팀 공동 실습 참여](https://kakao-team-flask.onrender.com/?classroom=join&room=V5JHT522)**

Render Free와 Neon Free를 사용하며 결제 카드를 등록하지 않았습니다. 온라인 기록은 PostgreSQL에 저장해 서버 재시작 뒤에도 유지합니다. 한동안 쓰지 않았을 때 첫 접속은 약 1분이 걸릴 수 있습니다. 팀 실습은 교사 접속 없이 이름으로 참여하고 A/B/C 역할을 직접 맡습니다. 처음 시작한 사람이 나가거나 서버가 재시작돼도 같은 참여 링크에서 저장한 장부를 이어갑니다.

서버가 준비 중이면 화면에서 안내하고 준비가 끝나면 자동으로 연결합니다. 별도 프로그램을 실행할 필요가 없습니다. 연결이 끊겨도 현재 화면·입력을 유지하며, 거래·채굴·저장 요청을 자동으로 다시 실행하지 않습니다. 처리 여부가 불확실하다는 안내가 남으면 현재 기록을 확인한 뒤 직접 다시 조작합니다.

## 실행

Windows에서는 Python 3.13과 Node.js 24가 필요합니다. 이 폴더의 **Start-Team.cmd**를 실행하면 팀 전용 가상환경·화면 빌드를 준비한 뒤 Flask 서버를 시작합니다. 기본 주소는 `http://127.0.0.1:5003`이며 Ctrl+C로 종료합니다. 포트가 이미 사용 중이면 기존 프로세스를 종료하지 않고 안내합니다.

```powershell
.\Start-Team.cmd
# 다른 포트
.\Start-Team.cmd -Port 5004
```

직접 실행하려면 다음 순서를 사용합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend
npm.cmd ci
npm.cmd run build
cd ..
$env:TEAM_TEACHER_SECRET = '교사가 정한 긴 무작위 비밀코드'
$env:PORT = '5003'
.\.venv\Scripts\python.exe -B app.py
```

실행기는 비밀코드가 없으면 `instance/teacher-key.txt`에 무작위 코드를 생성해 재사용합니다. 참가자에게는 교사용 비밀코드 대신 방 코드·참여 링크를 전달합니다. 실습에 실제 자산이나 실제 지갑의 키를 넣지 않습니다.

## 하나의 앱, 세 실습 화면

| 경로 | 기능 |
| --- | --- |
| `/` | 통합 실습: UTXO·수수료·거스름돈·전자서명·경쟁 채굴·분기·거래 추적·보관함 |
| `/?classroom=teacher` | 교사 방 생성·대표 지정·전파·실행 제어 |
| `/?classroom=join` | 이름으로 팀 실습 시작·참여, A/B/C 직접 선택, 공동 실행 제어 |
| `/basic` | 기존 팀 실습: 계정 잔액·Merkle Tree·오프라인 분리·변조·복구 |

세 화면은 같은 Flask 서버에서 실행하고 모드 링크로 이동합니다. 통합·공동 실습의 HTTP와 WebSocket은 `/api/education`, 기존 기초 실습 API는 `/api`를 사용합니다. 별도 FastAPI 서버는 필요하지 않습니다.

## 가져온 기능

- UTXO 직접 선택, 입력 예약·소비, 거스름돈·수수료와 노드별 거래 추적
- 실제 Nonce·해시 계산, 세 채굴자 경쟁, 보상 50+수수료와 100블록 성숙
- 수동·자동 FIFO 메시지 전달, 수신 노드 검증, 경쟁 분기·누적 작업량·재구성
- 실습 보관함 검색·이름 변경·삭제·이어 하기·읽기 전용 결과 HTML
- 교사·대표·관찰자 권한, 실시간 WebSocket, 재접속·중복 명령·늦은 역할 변경 보호
- 팀 브라우저 소유자별 개인 실습·보관함·키 조회 분리와 저장 실패 시 원복

화면은 짧은 한국어 제목과 안내를 사용하며 문장 끝 마침표·홍보 문구를 줄였습니다. 첫 화면의 자막 영상은 기존 팀 앱의 2880×1800 원본을 유지합니다. 영상 속 화면은 이전 버전이며, 새 UTXO·공동 수업 시연 영상은 아직 없습니다.

## 저장과 복구

| 데이터 | 기본 파일 | 변경 환경 변수 |
| --- | --- | --- |
| 기존 기초 실습 | `instance/practices.sqlite3` | `KAKAO_PRACTICE_DB` |
| 통합·공동 실습, 소유자 정보 | `instance/education.sqlite3` | `TEAM_EDUCATION_DB` |

두 DB는 규칙·서명이 다른 별도 장부입니다. 같은 파일 경로를 지정하면 실행을 거부합니다. 기존 기초 실습을 UTXO로 자동 변환하지 않으며 기존 파일은 그대로 기초 화면에서 사용합니다. 원본 개인 프로젝트의 DB·쿠키·환경 변수를 가져오지 않습니다.

통합 실습은 같은 브라우저의 서명된 소유자 쿠키로 보관함 접근을 제한합니다. 쿠키를 지우면 이전 개인 보관함에 접근할 수 없으므로 결과 HTML·운영자 백업을 별도로 보관하세요. 팀 실습은 공유 코드와 저장 기록을 함께 보관하며, 같은 브라우저는 재시작 후 새 접속 권한을 자동으로 받습니다. 역할은 다시 선택하고 진행 계산은 멈춘 상태에서 이어갑니다. 쿠키를 지우거나 다른 기기를 쓰면 같은 링크에서 이름으로 다시 참여할 수 있습니다. 팀 실습 시작을 다시 눌러도 기존 공유 장부를 초기화하지 않습니다. 교사 비밀코드가 필요한 기존 교실 모드는 별도로 유지합니다.

운영 중 통합 DB의 일관된 백업과 서버 종료 후 복원을 지원합니다.
아래 명령은 로컬 SQLite용입니다. 공개 Neon 배포에는 PostgreSQL 백업을 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m education_engine.service.backup backup instance/education-backup.sqlite3
# 서버를 모두 종료한 뒤 복원
.\.venv\Scripts\python.exe -m education_engine.service.backup restore instance/education-backup.sqlite3 --confirm-server-stopped
```

기존 백업 이름은 덮어쓰지 않으며 복원 전 DB도 새 이름으로 보관합니다. 백업에는 교육용 개인키와 소유자 정보가 포함되므로 공유하지 않습니다. 결과 HTML은 서버 없이 읽는 결과 파일이며 복원용 DB가 아닙니다.

## 검사

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
node --test tests/ui_state.test.mjs
cd frontend
npm.cmd run check
npm.cmd run test:e2e:single
```

기초 화면 Chrome·Edge 검사는 저장소 루트의 `npm.cmd run test:flask:e2e`입니다. 기초 검사는 5010, 통합 검사는 4173과 `work/`의 별도 DB를 사용합니다. 실행 중인 사용자 서버나 개인 DB를 재사용하지 않습니다. 실제 실행 결과와 범위는 [검증 기록](VERIFICATION.md)에 있습니다.

## 운영 배포

단일 프로세스 Gunicorn과 외부 PostgreSQL로 무료 공개 배포했습니다. 실제 HTTPS·WSS와 Render 재시작 전후 기록·소유권 복원을 확인했습니다. 절차·무료 한도·수업 복구 방법은 [배포 안내](DEPLOYMENT.md), 검사 결과는 [검증 기록](VERIFICATION.md)에 있습니다. `app.py`의 Flask 개발 서버는 로컬 확인용입니다.

이 앱은 하나의 서버 안에서 A/B/C 모의 노드를 실행하는 교육 모델입니다. 실제 Bitcoin P2P 노드·실제 자산 지갑·상시 다중 서버 서비스가 아닙니다. 기초 모드는 잔액·순번, 통합 모드는 교육용 UTXO를 사용하며 주소·직렬화·발행·난이도는 실제 Bitcoin과 다릅니다.

## 소스 구조와 출처

- `app.py`: Flask 진입점·기초 API·통합 화면 제공
- `education_api.py`: 통합·공동·보관함 HTTP와 WebSocket
- `education_engine/`: 독립 복사한 Python 핵심 규칙·서비스·출처 해시
- `frontend/`: 팀 통합 React 화면·테스트·빌드
- `static/`: 기존 기초 화면·용어 사전·영상
- `storage.py`, `operations.py`, 기존 코어 모듈: 기초 실습과 저장

통합 소스는 개인 본체의 `42024d7` 커밋과 당시 3.2.0 미커밋 개발 내용을 기준으로 복사했습니다. 원본 출처·실제 파일 해시·팀 전용 수정은 `education_engine/PROVENANCE.json`과 각 README에 기록합니다. 원본 프로젝트와 이 팀 앱은 이후 독립적으로 관리합니다.

## 원본과 변경 이력

2026-11-15 받은 `PythonProject1 (2).zip`의 소스 16개를 시작점으로 사용했습니다. 원본 ZIP의 SHA-256은 다음과 같습니다.

```text
e098af6efa01de4fb337d147eef26fdd4a29227a8a984169ea2bcab2fcde7807
```

같은 날 `test_core.zip`과 후속 안내를 반영해 `test_mempool_miner.py`, `main.py`, `mining_gui.py`를 삭제하고 통합 테스트와 import를 정리했습니다. 테스트 ZIP의 SHA-256은 다음과 같습니다.

```text
9f7b6c983f83666e1bf91a40235bbb28810eb48372028545297d50fe9015dcbb
```

2026-11-16의 2.0.0은 이 실행 소스를 개선한 버전이므로 이후 소스가 원본 ZIP과 같다고 보장하지 않습니다. 가상환경·저장 DB·개인 설정·검증 산출물은 Git에 포함하지 않습니다.
