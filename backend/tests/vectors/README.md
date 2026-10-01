# TypeScript 핵심 로직 기준값

`ts-core-v1.json`은 버전 1.1.0(커밋 `7b323cc`)의 TypeScript 핵심 로직(`src/core/*.ts`, @noble/curves 2.4.0, @noble/hashes 2.4.0)이 만든 값입니다. 2.0.0에서 규칙을 Python으로 옮기면서, 옮긴 구현이 같은 결과를 내는지 확인하려고 TypeScript 코드를 삭제하기 전에 기록했습니다.

고정한 입력은 개인키 `1`·`2`·`3`(64자리로 0을 채움), 실습 ID `SHA-256("parity-v1")`, Genesis 시각 1000, 난이도 1입니다. 포함한 값은 다음과 같습니다.

- 해시, 난이도 목표값, 지갑의 공개키·주소, Genesis 블록
- 30 코인 송금의 미서명 거래·서명 원문·원문 해시·서명·거래 ID·검증 결과
- 거부되어야 하는 서명 세 가지: 큰 쪽 s 값, 가짜 서명, 다른 개인키의 서명
- 거래 목록 해시, 후보 블록의 헤더 원문과 Nonce 0~15의 해시
- 제출·중복·이중 지출·잘못된 PoW·전달·채굴을 포함한 세 블록 시나리오의 단계별 검증 결과, 메시지 순번, 최종 노드 상태와 이벤트
- 변조 실험 여섯 가지의 검사 결과

`generate-ts-core-v1.test.ts.txt`는 이 파일을 만든 Vitest 생성기의 보관본입니다. 현재 저장소에는 TypeScript 핵심 로직이 없으므로 실행되지 않습니다. 다시 만들려면 커밋 `7b323cc`에서 이 파일을 `src/core/parity-vectors.test.ts`로 두고 다음을 실행합니다.

```powershell
$env:WRITE_VECTORS = '1'; npx.cmd vitest run src/core/parity-vectors.test.ts
```

기준값은 `tests/test_parity_vectors.py`가 사용합니다. 줄바꿈 방식의 차이에 영향을 받지 않도록 파일의 바이트가 아니라 JSON으로 읽은 값을 비교합니다.
