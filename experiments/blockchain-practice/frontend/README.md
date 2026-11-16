# 블록체인 실습실 화면

사용자가 선택한 기존 교육용 프런트엔드의 현재 개발 소스(원본 UI 3.2.0)를 블록체인 실습실 안으로 복사한 독립 사본이다. 팀 앱 버전은 3.0.0이며 원본 폴더를 수정하거나 실행 중 참조하지 않는다.

- `/`, `/basic`: 계정 잔액 모델의 기초 실습
- `/?classroom=join`, `/?classroom=teacher`: 공동 실습 참여·교사 화면
- `/hashcash`: 브라우저 CPU·GPU로 계산하는 넌스 찾기
- `/advanced`: UTXO·수수료·서명·채굴 경쟁·분기·저장 기록을 다루는 이전 통합 실습의 호환 경로
- `/architecture`: 기초 모델 구성도

HTTP와 WebSocket은 같은 Flask 서버의 `/api/education`을 사용한다. 별도 FastAPI 서버는 실행하지 않는다. 통합 실습과 기초 실습은 규칙과 저장 공간이 구분되며 서로의 장부를 자동 변환하지 않는다. 브라우저 설정은 `team-blockchain-theme`, 공동 수업 접속은 `team-blockchain-classroom` 키를 사용한다.

## 빌드·검사

이 폴더에서 `npm.cmd ci`, `npm.cmd run check`를 실행한다. 결과는 `dist/index.html`과 `dist/assets/`이며 Flask가 `/education-assets/` 아래의 파일로 제공한다. 사용자는 블록체인 실습실 서버의 `/`에서 연다. 시작하기와 이전 통합 화면의 시연 영상은 `/static/media/blockchain-practice-demo-20261117.mp4`를 사용한다 4분 33초 동안 기초 실습·세 노드 채굴 비교·공동 실습·넌스 찾기를 실제 조작하며 클릭 안내 자막을 제공한다

화면 개발 서버가 필요하면 `BLOCKCHAIN_PRACTICE_API_URL`로 블록체인 실습실 서버 주소를 지정하고 `npm.cmd run dev`를 실행한다 기존 `KAKAO_FLASK_API_URL`도 새 변수가 없을 때 호환한다 Vite 화면 경로는 `/education-assets/`다

`npm.cmd run test:e2e:single`은 빌드 후 이 폴더의 Playwright 설정으로 Chrome·Edge를 검사한다. `test:e2e`도 같은 단일 Flask 설정을 사용한다. 팀 가상환경의 Python으로 4173 포트에서 서버를 실행하고, 저장은 저장소의 `work/team-merge/e2e-basic.sqlite3`·`e2e-education.sqlite3`만 사용한다. 실사용 DB와 원본 백엔드는 사용하지 않는다. 교사 코드는 테스트 전용이며 `TEAM_TEACHER_SECRET`을 사용한다. Chromium 실행 파일을 따로 지정하려면 `TEAM_CHROME_PATH`를 설정한다.
