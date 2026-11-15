# 팀 Flask 배포 안내

팀 전용 브랜치 `codex/kakao-flask-run`을 단일 Flask 서버로 배포하는 구성입니다. 저장 기록은 컨테이너 밖의 영속 디스크에 보관합니다. 공개 호스팅 생성·실제 HTTPS/WSS·호스팅 재시작 검사는 완료 후 이 문서와 검증 기록에 따로 표시합니다.

## 실행 구조

```text
브라우저 ─ HTTPS·WSS ─ 호스팅 프록시 ─ Gunicorn 1프로세스·64스레드 ─ Flask
                                                   ├ 통합 React 화면
                                                   ├ 기초 화면
                                                   ├ HTTP API·실시간 수업
                                                   └ 영속 볼륨의 DB 두 개
```

교실의 활성 방·채굴 실행기·활성 실습은 한 프로세스가 관리합니다. `gunicorn.conf.py`의 worker 수를 늘리거나 여러 인스턴스로 자동 확장하지 않습니다. 현재 구조에서 다중 프로세스는 같은 실습의 작업 중지·캐시 충돌을 일으킬 수 있습니다. 많은 서버로 확장하려면 상태와 실행기를 별도 공유 서비스로 분리해야 합니다.

## 컨테이너 준비

이 폴더를 빌드 컨텍스트로 사용합니다. Node 24 빌드 단계와 Python 3.13 실행 단계를 분리합니다. 시작 진입점은 새로 마운트한 디스크의 지정 DB 경로·SQLite 보조 파일 권한만 준비한 뒤 UID/GID 10001로 전환해 Gunicorn을 실행합니다. 쓰기 확인이나 교사 비밀코드 검증에 실패하면 서버를 시작하지 않습니다.

```powershell
docker build -t team-blockchain:3.0.0 .
docker volume create team-blockchain-data
# 먼저 환경 변수에 실제 운영용 무작위 교사 비밀코드를 지정
docker run --name team-blockchain --rm -p 8080:8080 `
  --mount source=team-blockchain-data,target=/data `
  -e TEAM_TEACHER_SECRET `
  team-blockchain:3.0.0
```

로컬 확인 주소는 `http://127.0.0.1:8080`입니다. 외부 서비스에서는 호스팅 업체의 HTTPS와 WebSocket 지원을 설정합니다. `/data`가 재배포·재시작 후에도 유지되는 볼륨이어야 합니다. 임시 파일시스템에만 DB를 쓰면 저장 기록을 잃습니다.

## Render 구성

이 폴더의 `render.yaml`은 선택한 구성을 재현하는 Blueprint입니다. Blueprint의 사용자 지정 경로를 `experiments/kakao-flask/render.yaml`로 지정합니다. 경로는 YAML 파일 위치가 아닌 저장소 루트를 기준으로 해석합니다. 실제 서비스를 수동으로 만든 경우에는 같은 이름의 서비스를 중복 생성하지 않습니다.

| 항목 | 값 |
| --- | --- |
| 소스 | `jiho6367-design/mini-bitcoin-education` |
| 브랜치 | `codex/kakao-flask-run` |
| 런타임·리전 | Docker·Singapore |
| Dockerfile | `experiments/kakao-flask/Dockerfile` |
| Docker 빌드 컨텍스트 | `experiments/kakao-flask` |
| Root Directory | 비워 둠 |
| 서버 | `0.5c-512mb` 한 인스턴스, 자동 확장 없음 |
| 디스크 | `/data`에 1GB 영속 디스크 |
| 상태 검사 | `/api/education/health` |
| 배포 | 수동, 검증 후 최신 커밋 배포 |
| 교사 비밀코드 | 운영 환경에서 32~100자의 무작위 값 생성·재사용 |

2026-11-16 확인한 월 기본 요금은 서버 $7 + 디스크 $0.25 = **$7.25**입니다. Hobby 작업공간의 포함 대역폭 5GB를 넘으면 GB당 $0.15가 붙으며 세금은 별도입니다. 정적 영상 재생도 대역폭에 포함됩니다. 유료 서비스 생성 전 비용 확인이 필요하며, 무료 Render 서버에는 영속 디스크를 붙일 수 없습니다. [Render 요금](https://render.com/pricing), [영속 디스크 제한](https://render.com/docs/disks)

공개 저장소 URL로 수동 연결하면 GitHub 전체 저장소 권한을 새로 부여할 필요가 없습니다. 교사 비밀코드는 Render의 Environment에서 관리하며 Git·웹 화면·참가 링크에 넣지 않습니다. `TEAM_TRUST_PROXY=1`과 두 DB의 `/data` 경로를 반드시 적용합니다. `TEAM_PUBLIC_URL`을 비워 두면 Render가 주는 `RENDER_EXTERNAL_URL`을 사용합니다. 사용자 지정 도메인으로 전환할 때만 해당 HTTPS 주소로 재설정합니다.

## 환경 변수

| 이름 | 용도 |
| --- | --- |
| `PORT` | 운영 리스닝 포트, 기본 8080 |
| `KAKAO_PRACTICE_DB` | 기존 기초 DB, 컨테이너 기본 `/data/basic.sqlite3` |
| `TEAM_EDUCATION_DB` | 통합·공동 DB, 컨테이너 기본 `/data/education.sqlite3` |
| `TEAM_TEACHER_SECRET` | 운영자가 설정하는 긴 무작위 교사 비밀코드 |
| `TEAM_PUBLIC_URL` | 참가 링크·QR에 사용할 외부 HTTPS 주소, 미설정 시 `RENDER_EXTERNAL_URL` |
| `TEAM_TRUST_PROXY` | 신뢰하는 단일 인입 프록시 뒤에서만 `1`로 설정 |
| `TEAM_DATA_DIR` | 컨테이너 영속 디스크 루트, 기본 `/data` |
| `TEAM_BACKUP_DIR` | 진입점이 준비하는 비공개 백업 디렉터리, 기본 `/data/backups` |

프록시를 신뢰하도록 설정하면 전달된 프로토콜·호스트·클라이언트 주소를 사용합니다. 외부가 임의로 전달 헤더를 조작할 수 있는 직접 공개 구성에서는 켜지 않습니다. HTTPS 인식은 Secure 쿠키와 같은 출처 검사에 영향을 줍니다. 공개 WebSocket은 `wss`를 사용합니다.

건강 검사는 `/api/education/health`, 기초 API 검사는 `/api/health`입니다. DB·백업·교사 비밀코드를 공개 정적 파일 폴더에 두지 않습니다. Docker 빌드에서는 로컬 `instance/`, 가상환경, 테스트 DB를 제외합니다.

## 접근과 저장

통합 개인 실습은 서명된 브라우저 소유자 쿠키로 보관함·실행 실습·키 조회를 제한합니다. 공동 수업은 교사 비밀코드와 참가 토큰·역할을 따로 확인합니다. 다른 출처의 변경·WebSocket 연결은 거부합니다. 이는 이메일 계정·조직 로그인이나 기기 간 개인 보관함 동기화 기능을 제공하는 구조는 아닙니다.

팀원과 같은 장부를 조작하려면 `/?classroom=teacher`에서 방을 만든 뒤 화면의 `/?classroom=join&room=…` 참여 링크를 전달합니다. 참가자는 별명을 입력해 가입 없이 입장하고 교사가 A/B/C 대표를 지정합니다. 루트 `/`의 개인 실습은 브라우저마다 분리됩니다.

통합 실습은 기본적으로 저장 기록 1브라우저 200개·서버 2,000개, 활성 실습 32개 한도입니다. 지갑·블록·대기 거래·메시지에도 상한을 적용하며, 한도에 도달해도 조회·저장·중지·전달·삭제 등의 복구 조작을 유지합니다. 공동 수업은 한 활성 방·최대 30명 구조입니다. 기초 실습도 저장 수·블록·대기 거래를 제한합니다.

이 한도는 무제한 공개 트래픽에 대한 부하 시험 결과가 아닙니다. 최초 공개 전에는 실제 호스팅에서 동시 참가·CPU 사용·디스크 증가·프록시 재연결과 운영 복구를 확인하고 공개 범위를 정합니다.

## 재배포와 복구

재배포 시 일시 중단과 WebSocket 끊김이 생길 수 있습니다. 개인 실습은 저장 보관함에서 다시 열고, 공동 수업은 교사가 저장 수업을 새 방으로 복원해 역할을 다시 지정합니다. 중단 없는 방 유지나 토큰의 자동 승계를 약속하지 않습니다.

통합 DB는 `python -m education_engine.service.backup`으로 일관된 온라인 백업을 만들 수 있습니다. 복원 전 서버를 모두 종료하고 `--confirm-server-stopped`를 지정합니다. 기초 DB도 별도로 백업하며, 실행 중인 SQLite 파일을 WAL 파일과 분리해 일반 파일 복사하지 않습니다. 복원 시험은 복사한 DB로 먼저 수행합니다.

## 확인 근거와 남은 배포 검사

- 현재 수행: Windows 단일 Flask HTTP·실제 WebSocket, 두 엔진 회귀 검사, Chrome·Edge 전체 실습과 모드 이동, Linux Docker·Gunicorn·같은 볼륨의 새 컨테이너 복원
- 남은 검사: 실제 Render 호스팅의 HTTPS/WSS·영속 볼륨·재시작·백업 복구·부하
- Render 생성 화면에 서버·디스크를 구성했으며, 유료 리소스 생성·결제와 공개 서비스 생성은 아직 하지 않음

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

공개 저장소의 `.github/workflows/team-flask-production.yml`은 표준 Ubuntu 러너에서 Docker 이미지를 빌드하고, root 소유의 빈 볼륨 → 권한 준비 → Gunicorn·실제 WebSocket → 컨테이너 삭제 → 같은 볼륨을 쓰는 새 컨테이너로 기록 복원을 확인합니다. 공개 서비스는 만들지 않으며 검사 쿠키·DB·이미지를 artifact나 캐시에 올리지 않습니다. 실행 결과가 성공하기 전에는 컨테이너 검증 완료로 간주하지 않습니다.

2026-11-16의 [운영 컨테이너 검사](https://github.com/jiho6367-design/mini-bitcoin-education/actions/runs/43741142718)가 성공했습니다. 검사 소스는 `e228206359f8a8d20ddfee2ff4698c91d1dd5549`이며, 실제 호스팅의 TLS·디스크 설정 검사를 대신하지 않습니다.
