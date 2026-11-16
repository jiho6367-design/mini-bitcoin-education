# 블록체인 실습실 검토 반영 기록

2026-11-17 기준 `blockchain-practice` 팀 앱의 구현·문서 정합성을 정리합니다 2026-09-30 개인 React 앱 검토보고서의 F-01~G-05 기록은 [당시 원문](archive/personal-react/docs/review-resolution.md)에 보존했습니다 아래 표는 그 지적의 교육적 목적을 현재 팀 앱에서 어떻게 다루는지 설명하며 과거 검사 결과를 현재 실행 결과로 바꾸지 않습니다

## 학습 흐름과 관찰

| 관점 | 현재 팀 앱의 반영 | 근거 |
| --- | --- | --- |
| F-01 · 새 거래를 과거 체인 높이로 완료 처리하지 않기 | 기초는 작성·서명·접수한 거래를 추적하고, 공동 가이드는 선택 거래의 예약·후보·실제 블록 포함과 세 노드 일치로 완료를 판정 | `static/app.js`, `frontend/src/ui/teamGuideProgress.ts`, `frontend/src/ui/ClassroomApp.tsx` |
| F-02 · 성공과 실패 설명 구분 | 기초와 교육 엔진이 각자 실제 검증 결과를 반환하고 화면에 실패 이유와 결과를 표시 | `transaction.py`, `blockchain.py`, `education_engine/core/transaction.py` |
| F-03 · 실제 송수신 관계 표시 | 공동 실습은 노드별 독립 상태와 FIFO 큐·실제 전달 결과를 관찰 기초 노드에도 수신 검증·블록 전달 결과 표시 | `node.py`, `education_engine/core/lab.py`, `frontend/src/ui/network.ts` |
| G-01 · 자동 진행으로 관찰을 놓치지 않기 | 공동 실습은 자동 전달 상태를 표시하고 수동 전달도 지원 기초·공동 가이드의 화면 이동 자체는 거래·채굴을 실행하지 않음 | `frontend/src/ui/ClassroomApp.tsx`, `education_engine/service/classroom.py` |
| G-02 · 실제 채굴 표본 표시 | 한 번 계산·연속 계산·일시정지와 실제 Nonce·해시·횟수를 관찰 첫 시도 성공도 그대로 인정 | `node.py`, `education_engine/core/mining.py`, `tests/test_basic_mining_comparison.py` |
| G-03 · 예약·소비·거스름돈 설명 | 공동 카드가 확정 잔액·사용 가능·예약·보상 성숙 대기를 구분 초기 100 입력에서 송금 10·수수료 1·거스름돈 89 관계 설명 | `education_engine/core/transaction.py`, `frontend/src/ui/ClassroomApp.tsx`, `tests/test_education_core.py` |
| G-04 · 따라가기 가이드 | 기초의 6단계 안내와 공동의 쉬운 실습·자동 완료 체크·다음 동작 표시를 제공 퀴즈·학습자 채점은 미구현 | `static/index.html`, `frontend/src/ui/ClassroomApp.tsx`, `frontend/src/ui/teamGuideProgress.test.ts` |
| G-05 · 비교 실험 | 기초에 A/B/C 세 노드 실제 채굴 비교, 별도 넌스 찾기에 장치별 계산과 결과 이력 제공 통계 연구나 항상 더 빠른 GPU를 보장하는 기능은 아님 | `tests/test_basic_mining_comparison.py`, `frontend/src/hashcash/runner.ts` |

기초는 계정·거래 순번, 공동 실습은 UTXO를 사용하므로 모든 화면에 UTXO 설명을 적용하지 않습니다 서명·접수·채굴·전파를 각각 관찰하며 단순히 탭을 열었다는 이유로 실제 작업을 완료 처리하지 않습니다

## 팀 작업으로 확장하며 반영한 사항

| 문제·요청 | 현재 동작 | 회귀 검사 위치 |
| --- | --- | --- |
| 개인 앱과 팀 작업 구분 | 팀 브랜치·폴더·DB로 독립 실행 출처 해시 보존, 개인 소스·DB를 런타임에 사용하지 않음 | `tests/test_app_integration.py`, `tests/test_education_core.py` |
| 첫 화면이 복잡하고 통합 메뉴가 중복됨 | 기초 실습이 첫 화면 공개 모드는 기초·공동·넌스 찾기 이전 통합은 `/advanced` 호환 경로로 유지 | `tests/browser/reference.spec.ts`, `frontend/tests/visibility.spec.ts` |
| 휴대폰에서 백엔드 실행 안내로 막힘 | 같은 HTTPS 서버의 API에 연결 준비 중 안내·상태 재조회·자동 연결, 변경 요청의 무분별한 자동 재실행 방지 | `tests/ui_state.test.mjs`, `frontend/tests/server-readiness.spec.ts` |
| 교사나 최초 참가자가 없으면 진행 불가 | 별명으로 팀 참여·A/B/C 선택, 팀원의 공통 채굴·전파 제어 자기 팀 서명 권한은 유지 | `tests/test_team_classroom.py`, `tests/test_team_api.py` |
| 별명이 제대로 표시되지 않음 | 명시 입장·이름 변경·재접속에서 실제 별명을 유지하고 접속 참가자 표시 | `tests/test_team_names.py` |
| 혼자 실습하기 어려움 | 팀 A 한 명으로 쉬운 실습을 따라 송금·서명·접수·채굴·결과 확인 B/C 담당자 없어도 진행 | `frontend/tests/classroom-easy-start.spec.ts`, `tests/test_classroom_single_mining.py` |
| 초기 코인이 고정인지 불분명 | 기본 팀별 100을 Genesis에서 읽어 표시 새 방만 팀별 1~10000 선택, 기존 링크·장부·추가 참가로 금액 변경 없음 | `tests/test_team_practice_setup.py` |
| 한 노드 채굴과 비교 구분 필요 | 기초 한 노드 기본 + 세 노드 비교 공동 한 노드 채굴 + 경쟁을 별도 선택 | `tests/test_basic_mining_comparison.py`, `tests/test_classroom_single_mining.py` |
| 서버 재시작 후 기록·접속 링크 유지 | 외부 PostgreSQL의 장부·방 코드·소유 정보 복원 역할·임시 토큰·실행 계산은 새 권한으로 재개 | `tests/test_team_rooms.py`, `tests/test_education_restart.py`, `tests/test_postgres_deployment.py` |
| 새 주소에서 이전 브라우저 기록 유지 | 이전 호스트는 DB 없는 transfer 모드 허용된 쿠키·저장 키만 암호화한 일회 POST로 연결, 새 주소의 기존 정보 보호 | `tests/test_site_transfer.py` |
| 공개 서버에서 PC 연산 기능 노출 | 기본 꺼짐·Render 강제 비활성·루프백 제한 이미 시작됐을 수 있는 요청은 중복 CPU 작업을 만들지 않음 | `tests/test_native_hashcash_api.py`, `frontend/src/hashcash/runner.test.ts` |

위 코드·검사 경로는 모두 `experiments/blockchain-practice/` 기준입니다 테스트 존재와 특정 실행에서의 성공은 구분하며 개별 검사로 모든 교육 효과를 입증한다는 의미가 아닙니다

## 현재 문서에서 바로잡은 옛 설명

- “백엔드와 DB가 없고 새로고침하면 초기화”는 과거 개인 브라우저 앱 설명 현재 팀 앱은 Flask·SQLite/Neon 저장과 재접속을 사용
- “초기 100·채굴 보상 없음”은 현재 공통 규칙이 아님 기초는 faucet 50·보상 10, 공동은 초기 배정·보상 50+수수료·성숙 100을 구분
- “동시 채굴·분기·재구성 제외”도 현재 교육 엔진과 다름 실제 계산을 사용하는 경쟁·분기 모델을 제공하되 한 서버 안의 모의 노드임을 명시
- 이전 `codex/kakao-flask-run` 브랜치·폴더 표기는 현재 명칭 `blockchain-practice`로 안내 역사적 출처·옛 호스트·호환 환경 변수·쿠키·저장 키는 보존

현재 구조는 [설계](architecture.md), 규칙 차이는 [Bitcoin 비교](bitcoin-differences.md), 실제 실행 결과는 [팀 검증 기록](../experiments/blockchain-practice/VERIFICATION.md)에 따릅니다 이 문서 수정만으로 새 회귀 검사를 실행한 것으로 기록하지 않습니다

## 남은 범위

사용자 대상 교육 효과 연구·채점, 여러 서버의 분산 상태·P2P 통신, 계정 로그인 기반 기기 간 개인 보관함 복구는 구현하지 않았습니다 무료 호스팅의 장기 가용성·대규모 동시 접속·운영자 PostgreSQL 백업 복원 훈련도 기존 기능 검사와 구분합니다

해시·서명·PoW·저장 검사는 실제 계산을 확인하지만 Bitcoin 전체 호환성이나 실제 자산 지갑의 보안을 보증하지 않습니다 운영 절차와 남은 검사는 [배포 안내](../experiments/blockchain-practice/DEPLOYMENT.md)에 남깁니다
