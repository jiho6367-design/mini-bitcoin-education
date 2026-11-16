# 블록체인 실습실 교육 엔진

`blockchain-practice` 팀 앱의 Python UTXO 규칙·실행·보관함 패키지입니다 개인 프로젝트의 코드와 당시 미커밋 서비스 변경을 독립 복사했으며 원본 커밋·파일 해시는 [PROVENANCE.json](PROVENANCE.json)에 보존합니다 현재 팀 전용 수정과 출처 원본 해시는 구분하며, 브랜드 변경이나 문서 갱신으로 출처 기록을 다시 쓰지 않습니다

공동 실습과 `/advanced` 호환 화면이 이 엔진을 사용합니다 첫 화면 기초 실습의 계정 잔액·거래 순번 장부는 별도 코어이며 자동으로 UTXO로 바꾸지 않습니다 `/hashcash` 넌스 찾기와 선택 Python/C 엔진도 이 장부의 채굴·발행과는 별개입니다

## 책임

| 경로 | 역할 |
| --- | --- |
| `core/` | secp256k1·SHA-256, 거래 원문·서명·UTXO, 블록 검증·PoW, 노드별 큐·분기·재구성 |
| `service/session.py`, `race.py` | 실습·실제 채굴 작업·경쟁과 작업 수명 |
| `service/classroom.py` | 참가자·대표·교사 권한과 공동 명령·실행 상태 |
| `service/checkpoint.py`, `autosave.py` | 저장 가능한 상태와 복원·저장 실패 시 처리 |
| `service/storage.py`, `archive_html.py` | 로컬 SQLite 보관함과 키를 제외한 결과 HTML |
| `service/backup.py` | 로컬 SQLite의 일관된 백업·서버 종료 후 복원 CLI |
| `api/schemas.py` | Flask 어댑터가 사용하는 입력 검증 모델 |

패키지 import 자체는 서버나 DB를 시작하지 않습니다 HTTP·WebSocket·브라우저 소유권은 상위 [`education_api.py`](../education_api.py), 교사 없는 팀 링크·이름·재접속은 [`team_rooms.py`](../team_rooms.py)가 연결합니다 PostgreSQL은 상위 저장 어댑터에서 선택합니다 별도 FastAPI 프로세스를 실행할 필요가 없습니다

## 거래와 블록 규칙

일반 거래는 한 지갑의 확정 UTXO를 선택해 수신·거스름돈 출력을 만들고 정수 수수료를 계산합니다 이미 대기 거래가 예약한 입력은 다시 선택하지 않습니다 확정 잔액과 실제 사용 가능액을 구분하고, 서명·소유권·입력 중복·금액·성숙 조건을 검증한 뒤 Mempool에 접수합니다 접수만으로 확정 UTXO를 소비하지 않습니다

서명 원문은 버전·실습 ID·입력·출력·공개키를 고정 순서 JSON 배열로 만듭니다 UTF-8 원문의 SHA-256을 secp256k1 ECDSA로 서명하며 추가 암묵 해시를 막는 `Prehashed`와 deterministic low-S compact 서명을 사용합니다 주소는 `mini_`와 압축 공개키 바이트의 SHA-256입니다 Bitcoin 주소·거래 직렬화와 호환되지 않습니다

블록 헤더는 실습 ID·높이·이전 해시·거래 목록 해시·시각·난이도·Nonce의 JSON입니다 단일 SHA-256을 사용하고, 거래 목록 해시에는 ID뿐 아니라 실제 필드와 서명을 포함합니다 난이도는 실습별 1~4이며 `hash <= 2^(256−4d)−1`을 확인합니다 실제 Nonce·해시 표본·계산 횟수·계산 시간과 학습 관찰 대기를 구분합니다

검증은 체인 연결·PoW·거래·UTXO를 확인한 뒤 상태를 적용합니다 A/B/C는 체인·UTXO·Mempool과 메시지 처리가 분리되며, 유효 분기의 누적 작업량이 더 클 때 재구성하고 분리된 거래를 다시 검토합니다 수동·자동 FIFO 전파, 한 노드 채굴과 세 채굴자 경쟁을 제공합니다 모두 한 Flask 서버 프로세스의 모의 노드입니다

## 공동 실습의 초기 배정·권한

기본은 A/B/C 팀 지갑에 각각 100코인을 Genesis에서 한 번 배정합니다 새 방은 팀별 동일 초기 금액을 1~10000 정수로 정합니다 기존 방 입장·추가 참가·재접속은 재발행하지 않습니다 채굴 보상은 팀 송금 지갑과 별도의 보상 지갑에 50+수수료로 쌓이며 100블록 성숙 규칙이 있습니다

팀원은 별명으로 참여하고 비어 있는 대표 역할을 직접 맡습니다 자기 팀 거래 서명은 대표만 하며 채굴·전파 등 공통 명령은 교사 접속 없이 팀원이 실행할 수 있습니다 교사 비밀코드를 쓰는 기존 교실 모드도 유지합니다 프런트 가이드의 완료 체크는 현재 선택 거래·실제 예약·후보·노드별 블록 포함 상태를 읽으며 직접 명령을 대신 실행하지 않습니다

## 저장과 복원

로컬 기본 DB는 이 프로젝트의 `instance/education.sqlite3`이며 `TEAM_EDUCATION_DB`로 명시 변경합니다 개인 프로젝트 환경 변수·파일 경로는 사용하지 않습니다 기초 DB와 같은 파일을 지정하지 않습니다

운영에서 `TEAM_DATABASE_URL`을 설정하면 상위 `postgres_education_store.py`가 Neon PostgreSQL의 `team_education` 스키마를 사용합니다 기초는 `team_basic`, 배포 쓰기 권한은 `team_control`로 분리합니다 소유 쿠키 서명 키·방 코드·참가자 소유 해시·저장 장부를 유지하고, 새 배포 초기화 성공 후 이전 writer의 변경을 차단합니다

방 복원은 같은 링크와 장부를 이어 사용하지만 임시 참가 토큰·역할 점유·실행 중 계산을 재사용하지 않습니다 같은 브라우저는 서명된 소유 쿠키로 새 참가 권한을 얻고 역할을 다시 선택합니다 접속 없는 추가 방은 저장·일시정지 후 메모리에서 비울 수 있어도 링크는 유지합니다

교육용 개인키는 서버 체크포인트·백업에 포함됩니다 개인 보관함은 브라우저 소유권으로 제한하며 이메일 로그인이나 기기 간 자동 복구 기능은 없습니다 결과 HTML은 읽기 전용 결과물로 복원용 DB가 아닙니다 이전 호스트의 브라우저 정보 이전은 상위 `site_transfer.py`가 처리하고 이 패키지의 데이터 형식·쿠키 이름을 변경하지 않습니다

로컬 SQLite 백업은 앱 폴더에서 실행합니다

```powershell
.\.venv\Scripts\python.exe -m education_engine.service.backup backup instance/education-backup.sqlite3
# 모든 실습 서버를 종료한 뒤 복원
.\.venv\Scripts\python.exe -m education_engine.service.backup restore instance/education-backup.sqlite3 --confirm-server-stopped
```

기존 백업을 덮어쓰지 않으며 복원 전 DB도 별도 보관합니다 `TEAM_DATABASE_URL`이 있는 PostgreSQL 배포에서는 이 SQLite CLI가 실행을 거부합니다 PostgreSQL은 운영자 백업 절차를 사용합니다

## 검사와 관련 문서

`tests/test_education_core.py`는 UTXO·수수료·서명·성숙·분기·실제 채굴·체크포인트·실패 원복을 검사합니다 API·권한·WebSocket·별명·팀 초기 금액·다중 링크·재시작·PostgreSQL·주소 이전은 각각 팀 폴더의 관련 회귀 검사에서 확인합니다

현재 실행·운영 범위는 [팀 README](../README.md), [검증 기록](../VERIFICATION.md), [배포 안내](../DEPLOYMENT.md)에 있습니다 저장소의 [설계](../../../docs/architecture.md)와 [Bitcoin 비교](../../../docs/bitcoin-differences.md)도 팀 앱 기준입니다 실제 Bitcoin P2P·프로토콜 호환·다중 서버 합의를 구현한 패키지로 설명하지 않습니다
