# 블록체인 실습실 배포 안내

팀 전용 브랜치 `blockchain-practice`을 **Render Free + Neon Free**로 배포했습니다. 공개 주소는 **https://blockchain-practice.onrender.com**입니다. Flask·실시간 연결은 Render, 기초·통합·공동 기록과 소유자 정보는 Neon PostgreSQL에 저장합니다. 로컬 실행은 기존 SQLite를 사용합니다. 실제 HTTPS/WSS와 Render 재시작 뒤 복원을 확인했으며 결과는 검증 기록에 있습니다.

## 현재 운영 상태와 유지보수 기준 (2026-11-17)

아래 두 무료 서비스는 이미 생성돼 운영 중입니다 기존 서비스를 재배포하며 새 Render 서비스·Neon 프로젝트·유료 디스크를 만들 필요가 없습니다

| 기존 서비스 | 공개 주소 | 역할 | 소스 브랜치 |
| --- | --- | --- | --- |
| `blockchain-practice` | `https://blockchain-practice.onrender.com` | `TEAM_SITE_MODE=primary`, 실습·HTTP·WSS·Neon 저장 | `blockchain-practice` |
| `blockchain-practice-legacy` | `https://kakao-team-flask.onrender.com` | `TEAM_SITE_MODE=transfer`, 이전 접속 정보 연결 전용 | `blockchain-practice` |

두 서비스 모두 Dockerfile은 `experiments/blockchain-practice/Dockerfile`, 빌드 컨텍스트는 `experiments/blockchain-practice`, Root Directory는 빈 값입니다 2026-11-17에는 두 서비스가 같은 `9fca79179e4f93cfbde089dfb24d043aa9a8b952` 커밋으로 Live임을 확인했습니다 브랜치·폴더 이름만 정리했으며 기존 무료 플랜·주소·DB·환경 변수 값·쿠키·브라우저 저장 키를 유지했습니다

이 배포의 [GitHub CI 43961448887](https://github.com/jiho6367-design/mini-bitcoin-education/actions/runs/43961448887)은 로컬 볼륨 재시작과 PostgreSQL 재시작 검사 모두 성공했습니다 배포 전후 READ ONLY 감사에서 기초 10개·교육 실습 36개·이벤트 1,803개·소유권 30개·팀 링크 6개·참여자 12명의 전체 저장 행이 동일했습니다 기존 공동 코드 `V5JHT522`의 장부와 이벤트 835개도 유지됐습니다 이 집계는 해당 배포 시점의 보존 근거입니다

유지보수 시 두 서비스의 위 소스 경로를 확인하고, primary만 기존 Neon 연결·`team` 접두어를 이어 사용하게 둡니다 기존 `TEAM_DATABASE_URL`, `TEAM_TEACHER_SECRET`, 소유 쿠키 서명 키와 두 서비스의 동일한 `TEAM_SITE_TRANSFER_KEY`를 새로 생성하거나 초기화하지 않습니다 legacy를 일반 실습 서버로 되돌리지 않습니다

## 주소 변경과 이전 기록 연결

사이트명은 **블록체인 실습실**, 현재 실습 서비스명은 `blockchain-practice`입니다 기존 `kakao-team-flask.onrender.com` 호스트는 `blockchain-practice-legacy` 무료 서비스에서 접속 정보를 이전하는 용도로 유지합니다 주소를 바꿀 때 현재 primary를 별도로 생성했으며, 지금은 위 두 기존 서비스를 유지합니다

새 서비스만 기존 Neon 데이터베이스·`team` 스키마·소유 쿠키 서명 키·공동 방 코드 `V5JHT522`를 이어 사용합니다 이전 서비스는 `TEAM_SITE_MODE=transfer`로 실행하며 Flask 저장소·실습 실행기·PostgreSQL 쓰기 소유권을 생성하지 않습니다 같은 DB를 두 실습 서버가 동시에 쓰지 않습니다

두 서비스에 동일한 `TEAM_SITE_TRANSFER_KEY`(Fernet 키), `TEAM_SITE_PREVIOUS_ORIGIN=https://kakao-team-flask.onrender.com`, `TEAM_SITE_CANONICAL_ORIGIN=https://blockchain-practice.onrender.com`을 설정합니다 새 서비스는 `TEAM_SITE_MODE=primary`, `TEAM_PUBLIC_URL=https://blockchain-practice.onrender.com`을 사용합니다 키는 비공개 환경 변수에만 저장하고 서로 다른 키로 다시 생성하지 않습니다

첫 접속에서 새 호스트가 180초 유효한 브라우저 nonce를 만든 뒤 이전 호스트를 최상위 화면으로 엽니다 이전 호스트의 HttpOnly 실습·소유 쿠키는 원래 값을 URL에 넣지 않고 암호화된 POST 본문으로 보냅니다 새 호스트는 출처·대상·유효 시간·브라우저 nonce와 한 번만 사용 가능한 요청인지 확인한 뒤 쿠키를 재설정합니다 허용된 이 앱의 localStorage·sessionStorage만 복사하며 새 호스트에 이미 있는 값은 보존합니다

이전 도중의 페이지는 캐시하지 않고 다른 사이트의 프레임에 표시할 수 없습니다 자동 POST를 보내는 이전 페이지만 `Referrer-Policy: origin`을 사용해 정확한 출처 검증을 유지하고, 나머지 이전 응답은 `no-referrer`를 사용합니다 새 호스트에 기존 실습 쿠키가 있으면 자동 이전으로 덮어쓰지 않습니다 예전 주소에 이미 열어 둔 탭의 API는 이전 안내와 410을 반환하며 명령을 재전송하지 않습니다 탭을 새로고침하면 새 주소에서 이어갑니다

두 서비스 모두 Free 플랜이고 유료 디스크를 사용하지 않습니다 두 무료 웹 서비스는 Render 작업공간의 무료 인스턴스 시간 한도를 함께 사용합니다 이전 호스트가 비활성 상태였다면 최초 기록 연결에 추가 준비 시간이 걸릴 수 있습니다 브라우저 연결 정보 이전이 끝나기 전에 이전 호스트를 삭제하지 않습니다

## 실행 구조

```text
브라우저 ─ HTTPS·WSS ─ 호스팅 프록시 ─ Gunicorn 1프로세스·64스레드 ─ Flask
                                                   ├ 기초 화면 (첫 화면)
                                                   ├ 공동 React 화면
                                                   ├ HTTP API·실시간 수업
                                                   └ Neon PostgreSQL (기초·통합 스키마)
```

교실의 활성 방·채굴 실행기·활성 실습은 한 프로세스가 관리합니다. `gunicorn.conf.py`의 worker 수를 늘리거나 여러 인스턴스로 자동 확장하지 않습니다. 현재 구조에서 다중 프로세스는 같은 실습의 작업 중지·캐시 충돌을 일으킬 수 있습니다. 많은 서버로 확장하려면 상태와 실행기를 별도 공유 서비스로 분리해야 합니다.

## 로컬 SQLite 컨테이너 준비 (선택 사항)

이 폴더를 빌드 컨텍스트로 사용합니다. Node 24 빌드 단계와 Python 3.13 실행 단계를 분리합니다. 시작 진입점은 새로 마운트한 디스크의 지정 DB 경로·SQLite 보조 파일 권한만 준비한 뒤 UID/GID 10001로 전환해 Gunicorn을 실행합니다. 쓰기 확인이나 교사 비밀코드 검증에 실패하면 서버를 시작하지 않습니다.

```powershell
docker build -t blockchain-practice:3.0.0 .
docker volume create blockchain-practice-local-data
# 먼저 환경 변수에 실제 운영용 무작위 교사 비밀코드를 지정
docker run --name blockchain-practice-local --rm -p 8080:8080 `
  --mount source=blockchain-practice-local-data,target=/data `
  -e TEAM_TEACHER_SECRET `
  blockchain-practice:3.0.0
```

로컬 확인 주소는 `http://127.0.0.1:8080`입니다. 외부 서비스에서는 호스팅 업체의 HTTPS와 WebSocket 지원을 설정합니다. `/data`가 재배포·재시작 후에도 유지되는 볼륨이어야 합니다. 임시 파일시스템에만 DB를 쓰면 저장 기록을 잃습니다.

## Render 구성

이 폴더의 `render.yaml`은 primary 구성을 재현하는 Blueprint입니다 legacy 서비스를 추가 생성하는 파일은 아닙니다 기존 운영 서비스는 Render 설정에서 소스 브랜치·빌드 경로만 맞춰 유지합니다 재구성이 필요한 별도 환경에서 Blueprint를 사용할 때 사용자 지정 경로는 `experiments/blockchain-practice/render.yaml`이며, 내부 경로는 YAML 파일 위치가 아닌 저장소 루트를 기준으로 해석합니다 기존 서비스를 중복 생성하거나 Blueprint의 비밀값 생성 설정으로 운영 키를 교체하지 않습니다

| 항목 | 값 |
| --- | --- |
| 소스 | `jiho6367-design/mini-bitcoin-education` |
| 브랜치 | `blockchain-practice` |
| 런타임·리전 | Docker·Ohio (Neon AWS US East 2와 인접) |
| Dockerfile | `experiments/blockchain-practice/Dockerfile` |
| Docker 빌드 컨텍스트 | `experiments/blockchain-practice` |
| Root Directory | 비워 둠 |
| 서버 | `free` 한 인스턴스, 자동 확장 없음 |
| 저장소 | Neon Free PostgreSQL, Render 디스크 사용 안 함 |
| 상태 검사 | `/api/education/health` |
| 배포 | 수동, CI를 통과한 정확한 커밋을 기존 서비스에 배포 |
| 교사 비밀코드 | 운영 환경에서 32~100자의 무작위 값 생성·재사용 |

현재 두 Render 서비스와 Neon 프로젝트는 Free이며 유료 서버·디스크를 사용하지 않습니다 2026-11-16에는 카드 미등록·청구액 $0.00도 확인했습니다 무료 서버가 쉬고 있으면 첫 접속에 준비 시간이 걸리고 두 서비스는 작업공간의 무료 자원을 함께 사용합니다 정적 영상 재생도 Render 대역폭에 포함됩니다 사용량·정책별 최신 한도는 대시보드와 [Render 무료 조건](https://render.com/docs/free), [Neon 무료 조건](https://neon.com/pricing)을 확인하며 이 운영 구성에서 유료 플랜·추가 기능을 자동으로 켜지 않습니다

공개 저장소 URL로 수동 연결하면 GitHub 전체 저장소 권한을 새로 부여할 필요가 없습니다. 교사 비밀코드와 Neon의 TLS 연결 문자열은 Render Environment에 보관하며 Git·웹 화면·참가 링크에 넣지 않습니다. `TEAM_DATABASE_URL`과 `TEAM_TRUST_PROXY=1`을 적용합니다. `TEAM_PUBLIC_URL`을 비워 두면 `RENDER_EXTERNAL_URL`을 사용합니다.

PostgreSQL은 `team_basic`, `team_education`, `team_control` 스키마로 두 장부와 배포 제어를 분리합니다. 소유 쿠키 서명 키도 DB에 보존합니다. 초기화에 성공한 새 배포만 쓰기 권한을 넘겨받고, 이전 배포의 변경은 거부해 오래된 메모리 상태의 덮어쓰기를 막습니다. 연결 오류 시 변경을 성공으로 응답하지 않습니다. `TEAM_DATABASE_URL`이 설정되면 컨테이너의 SQLite 파일을 사용하지 않습니다.

## 환경 변수

| 이름 | 용도 |
| --- | --- |
| `PORT` | 운영 리스닝 포트, 기본 8080 |
| `TEAM_DATABASE_URL` | 무료 공개 배포의 Neon TLS PostgreSQL URL, 비밀값 |
| `TEAM_DATABASE_PREFIX` | PostgreSQL 스키마 접두어, 기본 `team` |
| `KAKAO_PRACTICE_DB` | 기존 기초 DB, 컨테이너 기본 `/data/basic.sqlite3` |
| `TEAM_EDUCATION_DB` | 통합·공동 DB, 컨테이너 기본 `/data/education.sqlite3` |
| `TEAM_TEACHER_SECRET` | 운영자가 설정하는 긴 무작위 교사 비밀코드 |
| `TEAM_SHARED_ROOM_CODE` | 기존 교실을 팀 실습으로 한 번 연결할 정확한 코드, 현재 `V5JHT522` |
| `TEAM_PUBLIC_URL` | 참가 링크·QR에 사용할 외부 HTTPS 주소, 미설정 시 `RENDER_EXTERNAL_URL` |
| `TEAM_TRUST_PROXY` | 신뢰하는 단일 인입 프록시 뒤에서만 `1`로 설정 |
| `TEAM_SITE_MODE` | 새 실습 서버는 `primary`, 이전 호스트의 DB 없는 연결 서버는 `transfer` |
| `TEAM_SITE_TRANSFER_KEY` | 두 호스트가 함께 사용하는 비공개 Fernet 이전 키 |
| `TEAM_SITE_PREVIOUS_ORIGIN` | 이전 호스트의 정확한 HTTPS 출처 |
| `TEAM_SITE_CANONICAL_ORIGIN` | 새 호스트의 정확한 HTTPS 출처 |
| `TEAM_DATA_DIR` | 컨테이너 영속 디스크 루트, 기본 `/data` |
| `TEAM_BACKUP_DIR` | 진입점이 준비하는 비공개 백업 디렉터리, 기본 `/data/backups` |

프록시를 신뢰하도록 설정하면 전달된 프로토콜·호스트·클라이언트 주소를 사용합니다. 외부가 임의로 전달 헤더를 조작할 수 있는 직접 공개 구성에서는 켜지 않습니다. HTTPS 인식은 Secure 쿠키와 같은 출처 검사에 영향을 줍니다. 공개 WebSocket은 `wss`를 사용합니다.

건강 검사는 `/api/education/health`, 기초 API 검사는 `/api/health`입니다. DB·백업·교사 비밀코드를 공개 정적 파일 폴더에 두지 않습니다. Docker 빌드에서는 로컬 `instance/`, 가상환경, 테스트 DB를 제외합니다.

## 접근과 저장

통합 개인 실습은 서명된 브라우저 소유자 쿠키로 보관함·실행 실습·키 조회를 제한합니다. 공동 수업은 교사 비밀코드와 참가 토큰·역할을 따로 확인합니다. 다른 출처의 변경·WebSocket 연결은 거부합니다. 이는 이메일 계정·조직 로그인이나 기기 간 개인 보관함 동기화 기능을 제공하는 구조는 아닙니다.

팀원은 `/?classroom=join&room=V5JHT522`에서 이름만 입력해 같은 장부에 참여합니다. 비어 있는 A/B/C 역할을 직접 맡고 반납하며 모든 팀원이 채굴·전파를 제어할 수 있습니다. 처음 입장한 사람이 없어도 계속 실습합니다. 일반 팀원에게 다른 사람 퇴출·방 종료·전체 저장 기록 삭제 권한은 주지 않습니다. 기존 교사 모드는 관리자 비밀코드를 가진 사용자를 위해 유지합니다. 루트 `/`와 `/basic`은 같은 기초 실습을 열며 브라우저마다 분리됩니다. 이전 개인 실습·보관함은 `/advanced`에서 호환하고 공개 모드 메뉴에는 표시하지 않습니다. 기존 DB·소유자 쿠키·공동 링크는 그대로 유지합니다.

통합 실습은 기본적으로 저장 기록 1브라우저 200개·서버 2,000개, 개인 활성 실습 32개 한도입니다 지갑·블록·대기 거래·메시지에도 상한을 적용하며, 한도에 도달해도 조회·저장·중지·전달·삭제 등의 복구 조작을 유지합니다 팀 공동 실습은 기본 방과 추가 방 3개까지 동시에 활성화하며 방마다 최대 30명이 참여합니다 기존 교사 모드는 한 활성 방을 사용합니다 기초 실습도 저장 수·블록·대기 거래를 제한합니다

이는 현재 코드의 사용량 제한이며 대규모 동시 접속이나 장기 부하 시험을 통과했다는 뜻은 아닙니다 현재 수행한 공개 배포·재시작·저장 복구 검증 범위는 아래와 [검증 기록](VERIFICATION.md)에 남깁니다

## 재배포와 복구

재배포 시 일시 중단과 WebSocket 끊김이 생길 수 있습니다. 개인 실습은 저장 보관함에서 다시 엽니다. 팀 실습은 같은 코드·참여 링크·저장 장부를 복원하고 서명된 브라우저 소유 쿠키로 참가자 접속 권한을 새로 발급합니다. 이전 토큰·실행 ID·대기 명령은 승계하지 않습니다. 재접속 후 A/B/C 역할을 다시 선택하고 채굴을 재개합니다. 모든 참가자가 나가면 계산을 멈춰 무료 자원을 아낍니다. 공유 장부를 다시 만들거나 링크를 새로 나눌 필요가 없습니다. 교사 모드의 예전 교실은 종전의 새 방 복원 절차를 유지합니다.

팀 링크와 참가자 소유 해시는 통합 저장소의 `team_rooms`·`team_members` 테이블에 보관하며 참가 토큰은 DB에 넣지 않습니다. `TEAM_SHARED_ROOM_CODE` 마이그레이션은 정확히 일치하는 기존 저장 기록 하나만 연결합니다. 다른 개인·교실 보관함을 공개하지 않습니다. 이미 공유 링크에 연결한 실습은 기존 관리자 보관함의 삭제·새 방 복원을 거부해 연결을 보호합니다. 새 설치에서는 이 변수를 비워 두면 첫 팀 실습 시작 시 공유 방을 만듭니다.

명시적인 **새 실습 만들기**만 독립된 팀 링크를 생성하고 초기 배정액을 설정합니다. 기본 링크 선택은 최초 등록 순서를 유지합니다. 기본 실습은 캐시에서 퇴출하지 않고 추가 실습은 최대 3개까지 활성 상태로 유지합니다. 접속 중인 실습은 퇴출하지 않으며, 빈 추가 실습은 저장·일시정지 뒤 메모리에서만 정리합니다. 모든 활성 추가 실습이 사용 중이면 새 생성·복원을 잠시 제한하고 기존 링크는 보존합니다. 저장 실습·이벤트의 기존 전체 한도도 적용합니다. 새 DB 테이블이나 유료 자원은 필요하지 않습니다.

SQLite 로컬 배포는 `python -m education_engine.service.backup`으로 통합 DB의 일관된 온라인 백업을 만들 수 있습니다. 복원 전 서버를 모두 종료하고 `--confirm-server-stopped`를 지정합니다. 기초 DB도 별도로 백업하며, 실행 중인 SQLite 파일을 WAL 파일과 분리해 일반 파일 복사하지 않습니다. **Neon PostgreSQL 배포에는 이 SQLite 백업 명령을 사용하지 않습니다**. PostgreSQL 백업은 Neon의 무료 복구 범위 또는 `pg_dump`로 수행하며 개인정보·교육용 키가 포함된 백업을 공개하지 않습니다.

## 확인 근거와 남은 운영 검사

- 현재 수행: Windows 두 엔진 회귀 검사, Chrome·Edge 실습, 실제 Neon PostgreSQL 18 저장 검사, Linux 볼륨 없는 앱 교체·DB 복원, 공개 HTTPS/WSS·보안 쿠키·Render 실제 재시작 후 같은 팀 링크 복원
- 최신 운영 기준: `9fca791` 두 서비스 Live, CI `43961448887`의 두 재시작 작업 통과, 이름·경로 변경 전후 전체 저장 행 동일
- 최신 시연 영상: 기초 시작하기의 기본 접힌 영상, 4분 33초·클릭 안내 자막, 공개 MP4 바이트 해시·206 Range·실제 재생 확인
- 남은 범위: 대규모 부하·장기 운영·PostgreSQL 운영자 백업의 별도 복구 훈련
- Render 서비스 Free, Neon 프로젝트 Free, Render Hobby 청구액 $0.00·결제 카드 없음 확인

Flask 개발 서버를 운영에 사용하지 않는 원칙은 [Flask 공식 배포 안내](https://flask.palletsprojects.com/en/stable/deploying/), threaded Gunicorn의 WebSocket 구성은 [Flask-Sock 공식 문서](https://flask-sock.readthedocs.io/en/latest/web_servers.html)를 참고했습니다. [Render 디스크 안내](https://render.com/docs/disks)는 기본 파일시스템이 임시이며 영속 디스크가 있는 서비스는 단일 인스턴스로 제한됨을 설명합니다. 플랫폼 요금은 실제 생성 화면에서 다시 확인합니다.

## 재시작 검사 실행

`tests/persistence_probe.py`는 실제 HTTP·WebSocket을 이용해 개인 송금·채굴·보관함·키, 공동 수업 송금·채굴·저장, 기초 지갑을 준비합니다. 재시작 뒤 같은 소유 쿠키로 두 DB의 기록을 읽고, 다른 브라우저의 접근 거부와 공동 수업의 새 권한 발급을 확인합니다.

```powershell
# 먼저 TEAM_VERIFY_TEACHER_SECRET에 운영 교사 비밀코드를 설정
.\.venv\Scripts\python.exe -B tests/persistence_probe.py prepare `
  --base-url https://실제서비스주소 --state-file ../../work/deploy-validation/host-state.json
# Render에서 서비스를 완전히 재시작한 뒤
.\.venv\Scripts\python.exe -B tests/persistence_probe.py verify `
  --state-file ../../work/deploy-validation/host-state.json --cleanup
```

상태 파일에는 소유 쿠키가 있으므로 Git이나 공개 검사 산출물에 올리지 않습니다. `--cleanup`은 검사에서 만든 통합·공동 기록만 정리하며 기초 지갑을 만든 검사 실습은 남깁니다. 실제 수업이 진행 중인 서버에서 실행하지 않습니다.

공개 저장소의 `.github/workflows/blockchain-practice-production.yml`은 표준 Ubuntu 러너에서 Docker 이미지를 빌드하고, root 소유의 빈 볼륨 → 권한 준비 → Gunicorn·실제 WebSocket → 컨테이너 삭제 → 같은 볼륨을 쓰는 새 컨테이너로 기록 복원을 확인합니다. 공개 서비스는 만들지 않으며 검사 쿠키·DB·이미지를 artifact나 캐시에 올리지 않습니다. 실행 결과가 성공하기 전에는 컨테이너 검증 완료로 간주하지 않습니다.

초기 운영 컨테이너 검증은 2026-11-16의 [실행 43741142718](https://github.com/jiho6367-design/mini-bitcoin-education/actions/runs/43741142718), 소스 `e228206359f8a8d20ddfee2ff4698c91d1dd5549`입니다 현재 배포 `9fca791`의 확인 결과는 맨 위 요약과 CI `43961448887`을 기준으로 읽습니다 컨테이너 검사는 실제 호스팅의 TLS·저장·접속 복원 확인과 구분합니다
