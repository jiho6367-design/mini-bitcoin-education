# 블록체인 실습실 요구사항

현재 팀 앱 기준 · 2026-11-17

공개 주소는 [blockchain-practice.onrender.com](https://blockchain-practice.onrender.com), 브랜치는 `blockchain-practice`, 실행 폴더는 `experiments/blockchain-practice/`입니다 이전 개인 React 요구사항은 [보관 문서](archive/personal-react/docs/requirements.md)로 분리합니다

## 목표와 경계

입문자가 지갑 → 거래 작성·서명 → 검증 → 채굴 → 노드 반영을 화면에서 직접 조작하고 설명할 수 있게 합니다 기초 실습을 첫 화면으로 두고, 팀원이 같은 장부를 다루는 공동 실습과 해시 계산에 집중하는 넌스 찾기를 제공합니다

팀 앱은 개인 프로젝트에서 가져온 코드의 독립 사본을 포함하며 개인 본체·개인 브랜치·개인 DB를 실행 중 참조하지 않습니다 기초 계정 장부와 공동 UTXO 장부도 별도로 유지합니다 이 문서의 수용 조건은 확인할 동작이며, 모든 조건을 이번 문서 수정 때 새로 검증했다는 뜻은 아닙니다 실제 실행 결과는 [검증 기록](../experiments/blockchain-practice/VERIFICATION.md)에 날짜·범위별로 둡니다

## 기능 요구사항

| ID | 현재 요구사항·수용 조건 | 구현·확인 위치 |
| --- | --- | --- |
| B01 | 첫 화면에서 항상 보이는 6단계 안내와 현재 할 일·확인 결과를 제공 | `static/index.html`, `static/app.js` |
| B02 | 기초 지갑 생성 시 50코인 지급 거래를 대기열에 추가하고 채굴 후 확정 | `app.py`, `blockchain.py`, 기초 지갑·거래 대기열 |
| B03 | 기초 거래 작성·secp256k1 서명·노드 검증·제출을 분리하고 입력 변경 시 이전 초안을 무효화 | 기초 거래·서명, `transaction.py`, 기초 API |
| B04 | 기초 한 노드의 실제 Nonce·SHA-256 계산, 단계·느린·연속 실행·일시정지·재개·중지 제공 | 기초 채굴, `operations.py` |
| B05 | 기초 난이도 1~5와 높이별 정책을 검증하고 유효 블록의 선택 보상 지갑에 10코인 지급 | `blockchain.py`, `node.py` |
| B06 | 세 노드가 번갈아 실제 후보 해시를 계산하고 첫 발견 후 전파·검증 결과와 노드별 시도·Nonce·해시를 표시 | 기초 채굴의 세 노드 비교, `tests/test_basic_mining_comparison.py` |
| B07 | Genesis·블록 해시·Merkle Root·PoW·이전 연결·거래 규칙을 검증 | 기초 블록·변조, `block.py`, `blockchain.py` |
| B08 | 선택 노드의 내용 변경·해시 재계산·재채굴을 구분하고 정상 노드와 동기화해 복구 | 기초 블록·변조·노드·동기화 |
| B09 | 온라인·오프라인 노드 상태와 누적 작업량 기반 유효 체인 선택을 제공 | `node.py`, 기초 노드·동기화 |
| B10 | 기초 실습·지갑·거래·장부·결과를 서버 저장소에 보존하고 새 실습·초기화를 명시적으로 구분 | `storage.py`, `postgres_basic_store.py`, 기초 API |
| T01 | 팀원이 교사 없이 별명으로 입장하고 비어 있는 A/B/C 역할을 맡거나 반납 | `team_rooms.py`, `ClassroomApp.tsx`, `TeamIdentity.tsx` |
| T02 | 기존 링크는 기존 장부를 이어 열고, 명시적 새 실습만 별도 링크·장부를 생성 | 공동 입장 화면, `team_rooms.py` |
| T03 | 새 공동 실습의 팀별 초기 금액을 1~10000 정수로 선택하고 A/B/C에 같은 금액을 한 번 배정 | 공동 새 실습 만들기, `tests/test_team_practice_setup.py` |
| T04 | 확정 UTXO·사용 가능액·예약 입력·보상 성숙 대기를 구분하고 송금·거스름돈·수수료를 실제 규칙으로 계산 | `education_engine/`, `TeamWalletBalances.tsx` |
| T05 | 쉬운 실습에서 역할 → 전달 → 작성 → 서명 → 제출 → 단일 채굴 → 결과를 혼자 진행 | `TeamEasyStart.tsx`, 공동 쉬운 실습 |
| T06 | 공통 가이드의 완료 단계·다음 할 일을 선택한 실제 거래와 현재 노드 상태로 자동 판정 | `teamGuideProgress.ts`, `TeamPracticeGuide.tsx` |
| T07 | 한 노드 채굴과 대표 참여형 채굴 경쟁을 구분하고 실제 PoW·블록 전파·독립 검증을 수행 | 공동 채굴, `education_engine/`, `test_classroom_single_mining.py` |
| T08 | 실제 WebSocket으로 공동 상태를 전달하고 역할·실행 ID·명령 순서를 확인해 과거 권한·중복 명령을 거부 | `education_api.py`, `useClassroom.ts`, WebSocket·재시작 검사 |
| T09 | 서버 재시작 뒤 같은 링크·장부를 복원하고 역할을 다시 선택해 진행 | `team_rooms.py`, `tests/team_restart_probe.py` |
| T10 | 교사 모드와 이전 통합 실습·보관함을 호환 경로에서 유지하되 일반 메뉴는 기초·공동·넌스로 간결하게 제공 | `app.py`, `frontend/src/main.tsx` |
| H01 | `SHA256(UTF8(메시지 + 10진수 넌스))`를 실제 계산하고 원문 공백·줄바꿈을 보존 | `frontend/src/hashcash/core.ts` |
| H02 | 난이도 1~64, uint64 넌스 범위, 메시지 UTF-8 4096바이트 한도를 확인 | `hashcash/core.ts` |
| H03 | 자동·CPU·GPU·CPU+GPU 모드와 실제 장치·완료 계산 수·전환 이유를 표시하고 지원 실패 시 CPU로 전환 | `hashcash/runner.ts`, `gpu.ts`, `HashcashApp.tsx` |
| H04 | 발견한 해시를 별도 다시 계산해 확인하고 최근 결과 50개를 현재 브라우저에 저장 | `hashcash/core.ts`, `history.ts`, 넌스 찾기 |
| H05 | Python·C 고급 연산은 명시적으로 켠 로컬 PC 루프백에서만 허용하고 공개 무료 서버에서는 비활성 | `native_hashcash_api.py`, `native_hashcash/README.md` |
| U01 | PC·휴대폰 배치, 키보드 조작, 의미 있는 제목·버튼·상태 안내와 공통 시스템·라이트·다크 테마 제공 | `static/team-theme.css`, `static/theme.js`, React 테마·화면 |
| U02 | 서버 준비·일시 연결 장애를 안내하고 정상 준비 시 자동 연결하되 변경 명령을 자동 재전송하지 않음 | `static/app.js`, `api/serverConnection.ts`, `ServerConnection.tsx` |
| U03 | 시작하기의 6단계 아래에 최신 4분 33초 실제 조작 영상을 접어 제공하고 자동 재생·선다운로드를 피함 | `static/index.html`, 최신 MP4·poster |

위 표의 파일 경로는 별도 표시가 없으면 `experiments/blockchain-practice/` 기준입니다

## 저장·운영 요구사항

| 항목 | 현재 기준 |
| --- | --- |
| 공개 운영 | 기존 HTTPS 주소와 공동 링크를 유지하며 Render Free 한 서비스에서 앱 실행 |
| 영속 기록 | Neon PostgreSQL에 기초·공동·보관함·소유 정보 저장, 서버의 임시 디스크에만 의존하지 않음 |
| 로컬 실행 | Python 3.13·Node.js 24, `Start-Team.cmd`, 기본 포트 5003, 로컬 SQLite 사용 |
| 실행 구조 | 단일 Gunicorn 프로세스가 활성 방과 실행기를 관리, 여러 프로세스·인스턴스 자동 확장 제외 |
| 소유·역할 | 브라우저 소유 연결과 공동 참가·팀 역할을 확인하고 타인의 보관함·학습용 키 접근 제한 |
| 저장 실패 | 실패를 성공으로 표시하지 않고 저장 가능한 상태를 다시 확인, 불확실한 변경은 재시도 전 조회 |
| 비밀값 | 실제 DB 접속 문자열·운영 비밀코드·서명 키·토큰·백업을 Git·문서·영상에 넣지 않음 |
| 기존 기록 호환 | 브랜치·폴더·표시 이름을 바꿔도 기존 장부·방 코드·저장 키를 유지 |

기초·공동 기록은 서버 저장, 넌스 결과는 브라우저 저장입니다 서로 다른 보존 범위를 사용자에게 설명해야 합니다 무료 운영의 제한과 준비 시간은 [배포 안내](../experiments/blockchain-practice/DEPLOYMENT.md)에서 관리합니다

## 대표 수용 시나리오

- 기초: Alice·Bob 지갑 생성 → 각 50 지급 채굴 → Alice가 Bob에게 20 송금 → 실제 채굴·전파 후 Alice 30/Bob 70 확인, 보상은 별도 노드 지갑으로 수령
- 기초 비교: A/B/C의 실제 해시 표본 → 첫 블록 발견 → 연결된 노드의 같은 블록 수락 확인, 발견 노드는 고정하지 않음
- 기초 검증: 정상 A를 남기고 B의 서명된 송금 변경 → 실패 이유 확인 → 정상 온라인 노드와 동기화 복구
- 공동: 독립 새 방·각 100 → 혼자 A 역할 → 송금 10·수수료 1 → A만 채굴 → 세 노드 반영·A89/B110/C100 → 저장·같은 링크 재접속
- 연결: 준비 중 안내 → 정상 응답 후 자동 연결, 응답이 유실된 변경 요청을 자동 재실행하지 않음
- 넌스: CPU·난이도 3 → 실제 탐색 → 해시 다시 검증 → 같은 브라우저 새로고침 뒤 기록 유지
- 화면: 390px 모바일에서 주요 조작과 영상 비율을 확인하고 기초·공동·넌스 간 테마 선택 유지

## 제외와 한계

실제 자산·Bitcoin Mainnet·거래소·실제 P2P 노드 운영·완전한 Bitcoin 주소/직렬화/Script 호환은 제공하지 않습니다 모의 A/B/C는 한 서버 안의 독립 장부이며 물리적으로 분리된 분산 합의 네트워크가 아닙니다

기초는 계정 잔액·거래 순번 모델로 UTXO·수수료·자동 난이도 조정을 제외합니다 공동 UTXO 엔진은 분기·누적 작업량 선택·재구성·보상 성숙을 포함하지만 실제 네트워크의 모든 정책을 복제하지 않습니다 넌스 찾기는 장부를 변경하거나 실제 코인을 채굴하지 않습니다

이메일 계정 기반 로그인·기기 간 개인 보관함 자동 동기화·무료 서버의 상시 무중단·대규모 동시 접속 성능·교육 효과의 정량 검증은 현재 완료 조건으로 주장하지 않습니다
