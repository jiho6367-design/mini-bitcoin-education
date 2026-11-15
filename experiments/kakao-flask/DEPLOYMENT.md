# 팀 Flask 배포 안내

현재 저장소는 로컬 실행과 단일 Flask 서버 통합 검증을 마쳤으며, 아래 운영 구성을 제공합니다. 외부 호스팅 계정이나 인터넷 배포는 생성하지 않았습니다. 이 작업 환경에는 Docker가 없어 컨테이너 빌드·Linux Gunicorn 실행은 아직 확인하지 못했습니다.

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

이 폴더를 빌드 컨텍스트로 사용합니다. Node 24 빌드 단계와 Python 3.13 실행 단계를 분리했으며, 실행 이미지는 비관리자 계정으로 동작합니다.

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

## 환경 변수

| 이름 | 용도 |
| --- | --- |
| `PORT` | 운영 리스닝 포트, 기본 8080 |
| `KAKAO_PRACTICE_DB` | 기존 기초 DB, 컨테이너 기본 `/data/basic.sqlite3` |
| `TEAM_EDUCATION_DB` | 통합·공동 DB, 컨테이너 기본 `/data/education.sqlite3` |
| `TEAM_TEACHER_SECRET` | 운영자가 설정하는 긴 무작위 교사 비밀코드 |
| `TEAM_PUBLIC_URL` | 참가 링크·QR에 사용할 외부 HTTPS 주소 |
| `TEAM_TRUST_PROXY` | 신뢰하는 단일 인입 프록시 뒤에서만 `1`로 설정 |

프록시를 신뢰하도록 설정하면 전달된 프로토콜·호스트·클라이언트 주소를 사용합니다. 외부가 임의로 전달 헤더를 조작할 수 있는 직접 공개 구성에서는 켜지 않습니다. HTTPS 인식은 Secure 쿠키와 같은 출처 검사에 영향을 줍니다. 공개 WebSocket은 `wss`를 사용합니다.

건강 검사는 `/api/education/health`, 기초 API 검사는 `/api/health`입니다. DB·백업·교사 비밀코드를 공개 정적 파일 폴더에 두지 않습니다. Docker 빌드에서는 로컬 `instance/`, 가상환경, 테스트 DB를 제외합니다.

## 접근과 저장

통합 개인 실습은 서명된 브라우저 소유자 쿠키로 보관함·실행 실습·키 조회를 제한합니다. 공동 수업은 교사 비밀코드와 참가 토큰·역할을 따로 확인합니다. 다른 출처의 변경·WebSocket 연결은 거부합니다. 이는 이메일 계정·조직 로그인이나 기기 간 개인 보관함 동기화 기능을 제공하는 구조는 아닙니다.

통합 실습은 기본적으로 저장 기록 1브라우저 200개·서버 2,000개, 활성 실습 32개 한도입니다. 지갑·블록·대기 거래·메시지에도 상한을 적용하며, 한도에 도달해도 조회·저장·중지·전달·삭제 등의 복구 조작을 유지합니다. 공동 수업은 한 활성 방·최대 30명 구조입니다. 기초 실습도 저장 수·블록·대기 거래를 제한합니다.

이 한도는 무제한 공개 트래픽에 대한 부하 시험 결과가 아닙니다. 최초 공개 전에는 실제 호스팅에서 동시 참가·CPU 사용·디스크 증가·프록시 재연결과 운영 복구를 확인하고 공개 범위를 정합니다.

## 재배포와 복구

재배포 시 일시 중단과 WebSocket 끊김이 생길 수 있습니다. 개인 실습은 저장 보관함에서 다시 열고, 공동 수업은 교사가 저장 수업을 새 방으로 복원해 역할을 다시 지정합니다. 중단 없는 방 유지나 토큰의 자동 승계를 약속하지 않습니다.

통합 DB는 `python -m education_engine.service.backup`으로 일관된 온라인 백업을 만들 수 있습니다. 복원 전 서버를 모두 종료하고 `--confirm-server-stopped`를 지정합니다. 기초 DB도 별도로 백업하며, 실행 중인 SQLite 파일을 WAL 파일과 분리해 일반 파일 복사하지 않습니다. 복원 시험은 복사한 DB로 먼저 수행합니다.

## 확인 근거와 남은 배포 검사

- 현재 수행: Windows 단일 Flask HTTP·실제 WebSocket, 두 엔진 회귀 검사, Chrome·Edge 전체 실습과 모드 이동
- 남은 검사: 컨테이너 빌드·Linux Gunicorn, 실제 호스팅의 HTTPS/WSS·영속 볼륨·재배포·백업 복구·부하
- 외부 배포 비용·도메인·호스팅 계정은 선택하거나 결제하지 않음

Flask 개발 서버를 운영에 사용하지 않는 원칙은 [Flask 공식 배포 안내](https://flask.palletsprojects.com/en/stable/deploying/), threaded Gunicorn의 WebSocket 구성은 [Flask-Sock 공식 문서](https://flask-sock.readthedocs.io/en/latest/web_servers.html)를 참고했습니다. 예를 들어 [Render의 디스크 안내](https://render.com/docs/disks)는 기본 파일시스템이 임시이며 영속 디스크가 있는 서비스는 단일 인스턴스로 제한됨을 설명합니다. 플랫폼·요금 선택은 실제 공개 요구를 정한 뒤 별도로 합니다.
