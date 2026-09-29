# Mini Bitcoin Education Lab

비트코인 핵심 원리를 직접 조작하며 배우는 **웹 기반 교육용 축소 모델**입니다. 실제 자산이나 Bitcoin 네트워크에 연결하지 않습니다. 개인 졸업작품으로 이해하고 설명할 수 있도록 UI와 순수 TypeScript 핵심 로직을 분리했습니다.

**지갑 → 거래 작성·전자서명 → 검증 → Mempool → PoW 채굴 → 블록 전파·검증 → Blockchain** 전체 흐름과 과거 블록 변조를 실험합니다.

## 빠른 실행 — Windows PowerShell

Node.js **24.x**와 npm이 필요합니다. 저장소 폴더에서 실행합니다.

```powershell
node --version
npm.cmd ci
npm.cmd run dev
```

터미널에 표시된 `http://127.0.0.1:5173`을 Chrome 또는 Edge로 엽니다. 종료는 `Ctrl+C`입니다. PowerShell에서 `npm.ps1` 실행 정책 오류가 나면 위처럼 `npm.cmd`를 사용합니다. 실행 정책을 변경할 필요가 없습니다.

배포용 빌드 확인:

```powershell
npm.cmd run build
npm.cmd run preview
```

기본 미리보기 주소는 `http://127.0.0.1:4173`입니다. `dist/index.html`을 더블클릭하는 `file://` 방식 대신 로컬 서버로 실행하세요. 최초 설치 후 실습은 외부 API·폰트·계정·인터넷 연결을 사용하지 않습니다. Node.js는 개발·정적 파일 서버 역할이며 별도 애플리케이션 백엔드는 없습니다.

## 3분 사용법

1. **예제 실습 시작**: 새 Alice·Bob 지갑과 Alice의 100 코인을 준비합니다. 빠른 시연은 난이도 1, 기본 실습은 2를 사용합니다.
2. 지갑·거래에서 금액 30으로 **거래 작성 → 전자서명 → 검증·전파**를 누릅니다.
3. Mempool에서 각 노드의 대기 거래를 확인합니다. 이때 Alice의 확정 잔액은 아직 100입니다.
4. 채굴에서 **한 번 계산하기**로 실제 Nonce·Hash를 보고 **계속 채굴**로 진행하거나, 처음부터 **연속 채굴 시작**을 누릅니다.
5. 채굴 성공 후 **노드 전파 확인**으로 이동합니다. 대기열이 비면 A·B·C의 잔액은 모두 Alice 70 / Bob 30입니다.
6. 거래와 채굴을 한 번 더 진행하고 변조 실험에서 블록 #1을 수정합니다. 정상 체인은 그대로이고 복사본의 검증이 실패합니다.

새로고침·탭 닫기·초기화 시 모든 지갑과 실습 상태가 사라집니다. 자동 저장·복원 기능은 없습니다. 개인키는 교육용이며 실제 자산을 보관하지 마세요.

## 검증 명령

```powershell
npm.cmd run check
npm.cmd run test:e2e
```

`check`는 타입 검사·린트·Vitest·배포용 빌드를 순서대로 실행합니다. E2E는 **설치된 Chrome과 Edge**를 사용합니다. 둘 중 하나만 설치되어 있다면 `npm.cmd run test:e2e -- --project=chrome` 또는 `--project=edge`로 선택합니다. 두 브라우저의 실행 결과는 [테스트 기록](docs/test-results.md)을 참고하세요.

## 코드 읽는 순서

1. `src/core/types.ts`: 데이터 구조
2. `src/core/crypto.ts`, `transaction.ts`: 지갑·서명·UTXO와 검증
3. `src/core/block.ts`: 헤더·PoW·블록·체인 검증
4. `src/core/lab.ts`: 독립 노드·메시지·상태 반영
5. `src/mining/`: Worker 계산과 메시지 규약
6. `src/ui/`: 학습 화면과 실습 제어

React는 화면 표시, 핵심 TypeScript는 규칙 검사, Worker는 반복 해시 계산을 담당합니다.

## 문서와 제한

[문서 목차](docs/README.md)에서 사용 안내·설계·보고서·시연 대본을 확인하세요.

실제 secp256k1 ECDSA 서명과 SHA-256, UTXO, 독립 노드 검증을 사용하지만, Bitcoin 호환 주소·바이너리 형식·Merkle Tree·채굴 보상·실제 P2P·동시 채굴·체인 분기 선택은 구현하지 않습니다. 세 노드의 일치가 완전한 분산 합의 구현을 뜻하지 않습니다. 차이는 [단순화 목록](docs/bitcoin-differences.md)과 앱 안에 명시합니다.
