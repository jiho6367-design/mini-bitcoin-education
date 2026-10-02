# Hashcash Lab — 독립 해시캐시 실습

`mini-bitcoin-education`의 **`codex/hashcash-only` 독립 브랜치**입니다. 메시지·목표 난이도 입력, 실제 탐색 진행·유효한 nonce·전체 해시 표시, 중지·재시작과 브라우저 재검증을 제공합니다.

비교 원본: [k9102/Nonce](https://github.com/k9102/Nonce), commit `1bca090c5b3796c96b7ac12834cc5f00a05a8892`. 최신 CPU worker와 동일 조건으로 측정한 **16개 작업·800만 회·5회 중앙값은 16.095 → 26.368 MH/s, 약 1.64배**입니다. 계산 처리량 비교이며 GUI 시작 시간이나 GPU 속도 향상을 뜻하지 않습니다. [분석·측정 보고서](docs/hashcash-report.md), [원시 측정 결과](docs/hashcash-benchmark.json)를 참고하세요.

## 실행 — Windows PowerShell

Node.js **24.x**, Python **3.12 이상**이 필요합니다. CPU 연산은 Python 표준 라이브러리만 사용합니다. 다른 작업 중인 원래 프로젝트 폴더와 별도의 폴더에서 실행하세요.

```powershell
git clone --branch codex/hashcash-only https://github.com/jiho6367-design/mini-bitcoin-education.git hashcash-lab
cd hashcash-lab
npm.cmd ci
npm.cmd run hashcash
```

[http://127.0.0.1:5186](http://127.0.0.1:5186)을 Edge 또는 Chrome으로 엽니다. `Ctrl+C`로 웹·연산 서버·CPU 자식 프로세스를 종료합니다. 프런트엔드 `5186`, Python 백엔드 `9186`을 사용합니다. 이미 실행 중인 서버는 새 실행에서 재사용하지 않습니다.

Python 경로를 따로 선택해야 할 때:

```powershell
$env:HASHCASH_PYTHON = 'C:\실제\Python\python.exe'
npm.cmd run hashcash
```

## 사용법과 정확한 규칙

1. 메시지와 목표 난이도를 입력하고 **탐색 시작**을 누릅니다. 기본 난이도 3은 빠른 실습에 적합합니다.
2. 자동 모드는 NVIDIA CUDA 장치·런타임·컴파일·SHA-256 자체 검증을 확인하고 통과하면 GPU를 사용합니다. 사용 불가하면 이유를 표시하고 CPU로 전환합니다. Intel·AMD·Apple GPU 기기는 CPU를 사용합니다.
3. 확인된 계산 수·경과 시간·평균 속도와 실제 표본을 관찰합니다. 화면은 약 0.35초마다 갱신하며 모든 계산을 출력하지 않습니다.
4. 발견 nonce와 해시를 확인하고 **해시 다시 검증**으로 브라우저 Web Crypto의 SHA-256과 대조합니다.
5. **중지** 후 조건을 바꾸고 다시 시작할 수 있습니다. CPU 작업 수를 줄이면 다른 작업에 여유를 남길 수 있습니다.

입력은 **SHA256(UTF8(message) || ASCII(decimal_nonce))**입니다. 구분자·공백·0 채움을 추가하지 않으며 메시지의 공백·줄바꿈을 보존합니다. 단일 SHA-256이고 실제 Bitcoin 블록 헤더 채굴과 구분됩니다.

난이도는 선행 **16진수 0 개수 1~64**입니다. 한 자리는 4비트이고 난이도 1 증가 시 평균 계산량은 16배입니다. 발견 시간은 확률적이며 병렬 탐색은 최소 nonce를 보장하지 않습니다.

nonce는 정확한 10진수 문자열로 전달하며 0~**18446744073709551615 (`2^64−1`)**를 지원합니다. 메시지는 UTF-8 4096바이트까지이고, GPU는 단일 블록 계산을 위해 35바이트까지 지원합니다. 더 긴 메시지는 CPU로 전환합니다. 새 탐색은 기존 CSV·체크포인트를 사용하지 않으며 서버 종료 시 상태가 사라집니다.

## 선택 사항: NVIDIA GPU 환경

CPU에는 아래 설치가 필요 없습니다. 별도 가상환경에서 GPU 의존성을 설치할 수 있습니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r hashcash_lab\requirements-gpu.txt
$env:HASHCASH_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm.cmd run hashcash
```

CuPy CUDA 12.x `[ctk]` 옵션이 CUDA 런타임·NVRTC 구성 요소를 설치하며 호환 NVIDIA 드라이버가 필요합니다. [CuPy 공식 설치 안내](https://docs.cupy.dev/en/stable/install.html)를 참고하세요. **개발 PC에서는 Windows Application Control이 의존성 로딩을 차단하여 GPU 실행·성능은 검증하지 못했습니다.** 정책은 우회하지 않았습니다. GPU 초기화·컴파일·검증·계산 실패 시 미확인 범위부터 CPU로 이어갑니다.

## 검증과 성능 재현

```powershell
npm.cmd run check
python -m unittest discover -s hashcash_lab/tests -v
npm.cmd run test:hashcash:e2e -- --project=edge
```

Chrome 설치 기기는 `--project=chrome`을 사용할 수 있습니다. 기존 UTXO E2E는 원래 화면을 대상으로 하므로 이 브랜치는 전용 해시캐시 E2E를 사용합니다.

허용된 CUDA 환경에서 실제 GPU 검사를 실행할 때는 `$env:HASHCASH_TEST_GPU = '1'`을 설정하고 선택한 Python으로 테스트하세요. 기본 검사에서는 실제 GPU 테스트 5개를 건너뛰며 GPU 실행 성공으로 계산하지 않습니다.

동일 계산량 비교 재현:

```powershell
git clone https://github.com/k9102/Nonce.git work/source/Nonce
git -C work/source/Nonce checkout 1bca090c5b3796c96b7ac12834cc5f00a05a8892
python -m hashcash_lab.benchmark --source work/source/Nonce --workers 16 --count 8000000 --repeats 5 --output work/benchmark.json
```

두 방법은 같은 메시지·난이도·nonce 범위·프로세스 수를 사용합니다. 해답을 발견해도 고정 탐색량을 모두 계산하고 실행 순서를 번갈아 측정합니다. 프로세스 초기화·GUI·콘솔·디스크 출력은 제외합니다.

## 구성

- `src/hashcash/`: 독립 React 학습 화면·모바일·라이트/다크 테마.
- `hashcash_lab/cpu.py`: 메시지 SHA-256 상태 복사와 바이트 조건 검사.
- `hashcash_lab/gpu.py`: 선택적 CUDA 커널 생성·자체 검증·정확한 64비트 nonce.
- `hashcash_lab/server.py`: 로컬 HTTP API, 작업 제어·자동 전환·결과 재검증.
- `hashcash_lab/benchmark.py`, `hashcash_lab/tests/`, `tests/hashcash.spec.ts`: 측정·검증.
- `scripts/hashcash.mjs`: 한 명령으로 서버 실행·종료.

Python 연산 서버가 필요하므로 `dist/index.html`만 정적으로 호스팅하면 탐색이 실행되지 않습니다. 배포 빌드를 확인할 때는 `python -m hashcash_lab.server`와 `npm.cmd run preview -- --port 5186 --strictPort`를 각각 별도 터미널에서 실행하세요. 기존 UTXO 핵심 코드는 이 브랜치에도 보존하지만 첫 화면에는 연결하지 않습니다.
