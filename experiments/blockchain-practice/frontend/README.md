# 블록체인 실습실 React 화면

팀 브랜치는 `blockchain-practice`, 이 폴더는 `experiments/blockchain-practice/frontend`입니다 팀 앱 버전은 3.0.0이며 개인 본체의 당시 UI 3.2.0에서 독립 복사했습니다 원본 폴더를 실행 중 참조하거나 수정하지 않습니다

## 실제 화면과 저장 경계

| 주소 | 제공 화면 |
| --- | --- |
| `/`, `/basic` | Flask의 `static/index.html`이 제공하는 기초 실습·6단계 안내·선택형 시연 영상 |
| `/?classroom=join` | 이 React 앱의 교사 없는 공동 실습·쉬운 실습·자동 진행 가이드 |
| `/?classroom=teacher` | 기존 교사 방·대표 지정·실행 제어 |
| `/hashcash` | 브라우저 CPU·GPU·CPU+GPU 넌스 찾기와 선택적 Python·C PC 모드 |
| `/advanced` | 이전 UTXO 개인 실습·보관함의 호환 화면, 공개 메뉴에서 제외 |
| `/architecture` | Flask의 기초 모델 구성도 |

공개 메뉴는 **기초 실습 · 공동 실습 · 넌스 찾기**입니다 모든 화면은 같은 Flask 서버에서 제공하며 별도 FastAPI 서버는 실행하지 않습니다 공동·이전 개인 실습의 HTTP·WebSocket은 `/api/education`, 기초 장부는 `/api`, 선택적 로컬 PC 연산은 `/api/hashcash-native`를 사용합니다 기초 잔액 모델과 교육용 UTXO 장부는 별도 저장소이며 자동 변환하지 않습니다

공동 실습은 별명으로 기존 링크에 입장하거나 별도 새 실습을 만들 수 있습니다 혼자 A만 맡아 송금·서명·전송·한 노드 채굴을 끝낼 수 있고, 세 팀이 함께 채굴 경쟁을 진행할 수도 있습니다 가이드의 완료 표시는 실제 거래·블록·노드 상태를 읽으며 안내 화면 이동만으로 명령을 실행하지 않습니다 준비 중인 서버에는 자동으로 다시 연결하되 변경 요청을 자동 재전송하지 않습니다

브라우저 설정·기록은 기존 키를 유지합니다

- 테마: localStorage `team-blockchain-theme`, 이전 기초 키 `blockchain-lab-theme`는 공통 선택이 없을 때만 읽음
- 넌스 기록·별명: localStorage `team-hashcash-history-v1`·`team-hashcash-name-v1`, 최근 발견 결과 최대 50개
- 가이드 관찰 거래: localStorage `team-practice-guide-target:<방 코드>:<실습 세션>`
- 공동 접속 권한: sessionStorage `team-blockchain-classroom`, 서버 재시작 뒤 소유 쿠키로 재접속해 만료된 실행 ID·토큰을 갱신

서버 장부는 온라인에서 Neon PostgreSQL, 기본 로컬 실행에서 SQLite에 저장합니다 넌스 탐색 기록은 그 브라우저의 기록이며 공동 장부와 별개입니다 이전 호스트의 허용된 브라우저 기록·HttpOnly 실습 쿠키 연결은 `site_transfer.py`가 담당합니다

## 빌드와 개발

이 폴더에서 실행합니다

```powershell
npm.cmd ci
npm.cmd run check
```

`check`는 타입·ESLint·단위 검사·운영 빌드를 실행합니다 결과는 `dist/index.html`과 `dist/assets/`이며 Flask가 `/education-assets/` 경로로 제공합니다 실제 기초 첫 화면은 Flask의 `/`, 이 React 공동 화면은 `/?classroom=join`에서 엽니다

화면 개발 서버는 먼저 로컬 Flask를 실행한 뒤 API 주소를 지정합니다

```powershell
$env:BLOCKCHAIN_PRACTICE_API_URL = 'http://127.0.0.1:5003'
npm.cmd run dev
```

Vite의 `/education-assets/` 화면은 `/api/education` HTTP·WebSocket을 위 Flask로 프록시합니다 새 변수가 없을 때만 기존 `KAKAO_FLASK_API_URL`을 호환하며 둘 다 없으면 5000 포트를 사용합니다 Python·C PC 모드는 빌드된 화면을 `Start-Team.cmd -Native`로 제공하는 로컬 Flask에서 확인합니다

## 시연 영상과 검증 기준

기초 시작하기의 **시연 영상 보기**와 이전 개인 실습의 홈은 같은 `/static/media/blockchain-practice-demo-narrated.mp4`를 사용합니다 최신 화면의 기초 실습·세 노드 채굴 비교·공동 실습·넌스 찾기를 실제 조작한 4분 33초 영상이며 한국어 설명 음성과 클릭 설명 자막을 포함합니다 자동 재생 없이 `preload="none"`으로 제공합니다

2026-11-17 운영 기준 `9fca791`에서 프런트 단위 147개·타입·린트·빌드가 통과했습니다 별도 Linux 운영 컨테이너·PostgreSQL 재시작 [CI 43961448887](https://github.com/jiho6367-design/mini-bitcoin-education/actions/runs/43961448887)도 성공했습니다 이는 당시 실행 기록이며 이번 문서 정리에서 전체 브라우저 검사를 재실행한 것은 아닙니다

`npm.cmd run test:e2e:single`은 빌드 후 Chrome 4173·Edge 4176의 별도 Flask 서버로 검사합니다 `test:e2e`도 같은 기본 설정을 사용하지만 빌드는 먼저 준비해야 합니다 테스트 DB는 실행별 UUID·브라우저를 붙인 저장소 `work/team-merge/e2e-<실행 UUID>-<브라우저>-basic.sqlite3`·`-education.sqlite3`이며 실사용 서버·DB를 재사용하지 않습니다 팀·가이드·잔액·초기 설정·쉬운 실습 검사는 각각 `test:e2e:team`·`test:e2e:guide`·`test:e2e:balances`·`test:e2e:setup`·`test:e2e:easy` 명령의 별도 설정을 사용합니다

테스트는 팀 가상환경의 Python과 전용 `TEAM_TEACHER_SECRET`을 사용합니다 Chrome 실행 파일을 따로 지정할 때는 `TEAM_CHROME_PATH`를 설정합니다 실제 검사 범위와 과거 결과는 [검증 기록](../VERIFICATION.md)에 보존합니다
