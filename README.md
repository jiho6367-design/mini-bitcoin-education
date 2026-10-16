# Hashcash Lab — 독립 해시캐시 실습

`mini-bitcoin-education`의 **`codex/hashcash-only` 독립 브랜치**입니다. 메시지·목표 난이도 입력, 실제 탐색 진행·유효한 nonce·전체 해시 표시, 중지·재시작과 브라우저 재검증을 제공합니다.

비교 원본: [k9102/Nonce](https://github.com/k9102/Nonce), commit `1bca090c5b3796c96b7ac12834cc5f00a05a8892`. 최신 CPU worker와 동일 조건으로 측정한 **16개 작업·800만 회·5회 중앙값은 16.095 → 26.368 MH/s, 약 1.64배**입니다. 계산 처리량 비교이며 GUI 시작 시간이나 GPU 속도 향상을 뜻하지 않습니다. [분석·측정 보고서](docs/hashcash-report.md), [원시 측정 결과](docs/hashcash-benchmark.json)를 참고하세요.

RTX 5070 Laptop GPU의 실제 CUDA 실행도 검증했습니다. 난이도 5·고정 8,388,608회·7회 측정에서 **실제 서버 배치 크기의 scanner는 원본 3.114 → 새 코드 3.309 GH/s, 약 6.3% 개선**되었습니다. 초기 준비 시간을 제외한 계산 처리량이며 [GPU 측정 결과](docs/hashcash-gpu-benchmark.json)에 대량·작은 배치 차이도 기록했습니다.

**2026-10-16 추가 개선:** 위 버전(`0daf95a`)보다 GPU 결과 전송·동기화 비용을 줄여, 이 PC의 5개 입력 조건에서 warmed 계산 처리량이 **약 6.1~29.3% 개선**됐습니다. **CPU + GPU 동시 모드**도 추가했고 실제 커널 중첩을 검증했습니다. 현재 Python CPU·CUDA 조합의 실측에서는 GPU 단독이 더 빨랐습니다. [최신 비교·한계·재현 방법](docs/hashcash-performance-20261016.md)을 참고하세요.

## 실행 — Windows PowerShell

Node.js **24.x**, Python **3.12 이상**이 필요합니다. CPU 연산은 Python 표준 라이브러리만 사용합니다. 다른 작업 중인 원래 프로젝트 폴더와 별도의 폴더에서 실행하세요.

```powershell
git clone --branch codex/hashcash-only https://github.com/jiho6367-design/mini-bitcoin-education.git hashcash-lab
cd hashcash-lab
npm.cmd ci
npm.cmd run hashcash
```

[http://127.0.0.1:5186](http://127.0.0.1:5186)을 Edge 또는 Chrome으로 엽니다. `Ctrl+C`로 웹·연산 서버·CPU 자식 프로세스를 종료합니다. 프런트엔드 `5186`, Python 백엔드 `9186`을 사용합니다. 이미 실행 중인 서버는 새 실행에서 재사용하지 않습니다.

별도 포트가 필요하면 `$env:HASHCASH_PORT='5187'`, `$env:HASHCASH_API_PORT='9187'`을 지정한 뒤 같은 명령을 실행합니다.

실행기는 `HASHCASH_PYTHON` 환경 변수 → 프로젝트 `.venv` → PATH의 `python` 순서로 선택하며 선택한 경로를 출력합니다. Python 경로를 따로 선택해야 할 때:

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
6. **기록 이름**에 이름·별칭을 입력합니다. 유효한 해답을 찾으면 해당 이름으로 자동 저장되며, 아래 **발견 기록**에서 이름·난이도별로 조회하고 원래 메시지의 해시를 다시 검증할 수 있습니다.
7. **CPU + GPU 동시 연산**을 선택하면 CPU 작업 1개부터 시작합니다. 장치별 완료 계산 수와 CPU·GPU 커널이 실제로 겹친 시간의 하한을 확인할 수 있습니다. CPU 작업 수는 변경할 수 있으며, 동시 모드가 항상 빠르지는 않습니다. 자동 모드는 기존과 같이 CUDA를 우선 사용합니다.

입력은 **SHA256(UTF8(message) || ASCII(decimal_nonce))**입니다. 구분자·공백·0 채움을 추가하지 않으며 메시지의 공백·줄바꿈을 보존합니다. 단일 SHA-256이고 실제 Bitcoin 블록 헤더 채굴과 구분됩니다.

난이도는 선행 **16진수 0 개수 1~64**입니다. 한 자리는 4비트이고 난이도 1 증가 시 평균 계산량은 16배입니다. 발견 시간은 확률적이며 병렬 탐색은 최소 nonce를 보장하지 않습니다.

nonce는 정확한 10진수 문자열로 전달하며 0~**18446744073709551615 (`2^64−1`)**를 지원합니다. 메시지는 UTF-8 4096바이트까지이고, GPU는 단일 블록 계산을 위해 35바이트까지 지원합니다. 더 긴 메시지는 CPU로 전환합니다. 새 탐색은 기존 CSV·체크포인트·발견 기록을 답으로 재사용하지 않습니다. 진행 중인 작업은 서버 종료 시 끝나지만 저장된 발견 기록은 유지됩니다.

## 사용자별 발견 기록

이름·별칭은 기록을 구분하는 표식이며 로그인 계정은 아닙니다. 같은 이름은 같은 필터에 모입니다. 기록에는 이름, 목표 난이도, 입력 메시지, 정확한 10진수 nonce, 전체 SHA-256, 연산 장치, 확인된 계산 수, 경과 시간, 저장 시각을 남깁니다. 발견 후 연산 정리가 끝나면 서버가 SQLite 트랜잭션으로 자동 저장하고, 저장 실패 시 결과 옆에 오류와 재시도 버튼을 표시합니다.

기본 저장 파일은 프로젝트의 **`data/hashcash-history.sqlite3`**입니다. 서버·브라우저를 다시 시작해도 기록을 읽을 수 있으며 Git에는 포함되지 않습니다. 같은 서버를 이용하는 사람들의 기록을 함께 보관합니다. 각각 별도 PC에서 실행하면 각 설치의 기록 파일에 저장되고, 여러 PC 간 공유·동기화 기능은 없습니다.

**발견 기록**에서 이름·난이도·입력 메시지로 필터링하고, 기록의 조건을 다시 불러오거나 브라우저에서 해시를 검증할 수 있습니다. **JSON 내보내기**는 필터에 맞는 전체 기록을 파일로 보관합니다. 화면 목록은 최근 100개까지 표시합니다.

프로젝트 밖의 고정 폴더에 저장하려면 실행 전에 경로를 설정합니다. 새 브랜치 폴더로 옮겨도 이 경로를 계속 사용하면 같은 기록을 읽습니다.

```powershell
$env:HASHCASH_DATA_DIR = 'C:\HashcashData'
npm.cmd run hashcash
```

기존 기록의 위치를 옮길 때는 서버를 종료한 뒤 저장 폴더 전체를 복사하세요. Python 서버를 직접 실행할 때는 `--data-dir`도 사용할 수 있습니다.

## 선택 사항: NVIDIA GPU 환경

CPU에는 아래 설치가 필요 없습니다. 프로젝트 가상환경에 GPU 의존성을 설치하면 실행기가 자동으로 해당 Python을 사용합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r hashcash_lab\requirements-gpu.txt
npm.cmd run hashcash
```

CuPy CUDA 12.x `[ctk]` 옵션이 CUDA 런타임·NVRTC 구성 요소를 설치하며 호환 NVIDIA 드라이버가 필요합니다. [CuPy 공식 설치 안내](https://docs.cupy.dev/en/stable/install.html)를 참고하세요. **개발 PC의 RTX 5070 Laptop GPU에서 실제 CUDA 테스트 7개와 화면의 GPU 자동 선택·해시 재검증이 통과했습니다.** 이전 실패는 과거 의존성 로딩 차단, CUDA 구성요소 누락, 앱이 CuPy 없는 Python을 선택하던 실행 설정에서 발생했습니다. 보안 정책을 변경하지 않았으며 현재 환경에서 정상 실행했습니다. GPU 초기화·컴파일·검증·계산 실패 시 미확인 범위부터 CPU로 이어갑니다.

## 검증과 성능 재현

```powershell
npm.cmd run check
python -m unittest discover -s hashcash_lab/tests -v
npm.cmd run test:hashcash:e2e -- --project=edge
```

Chrome 설치 기기는 `--project=chrome`을 사용할 수 있습니다. 기존 UTXO E2E는 원래 화면을 대상으로 하므로 이 브랜치는 전용 해시캐시 E2E를 사용합니다.

E2E는 화면 `5188`·API `9188`과 `work/hashcash-e2e/` 아래의 임시 기록 폴더를 사용합니다. 실습용 서버와 기록 데이터베이스에 테스트 결과를 쓰지 않습니다. 필요하면 `HASHCASH_E2E_PORT`·`HASHCASH_E2E_API_PORT`로 검사 포트를 지정합니다.

실제 CUDA 검사와 화면의 GPU 선택을 함께 확인할 때:

```powershell
$env:HASHCASH_TEST_GPU = '1'
.\.venv\Scripts\python.exe -m unittest discover -s hashcash_lab/tests -v
$env:HASHCASH_EXPECT_GPU = '1'
npm.cmd run test:hashcash:e2e -- --project=edge
```

`HASHCASH_TEST_GPU`를 설정하지 않은 기본 검사에서는 실제 GPU 테스트 7개를 건너뜁니다. GPU 초기화·자체 검증·메시지별 컴파일 시간은 화면의 경과 시간에 포함됩니다.

동일 계산량 비교 재현:

```powershell
git clone https://github.com/k9102/Nonce.git work/source/Nonce
git -C work/source/Nonce checkout 1bca090c5b3796c96b7ac12834cc5f00a05a8892
python -m hashcash_lab.benchmark --source work/source/Nonce --workers 16 --count 8000000 --repeats 5 --output work/benchmark.json
```

두 방법은 같은 메시지·난이도·nonce 범위·프로세스 수를 사용합니다. 해답을 발견해도 고정 탐색량을 모두 계산하고 실행 순서를 번갈아 측정합니다. 프로세스 초기화·GUI·콘솔·디스크 출력은 제외합니다.

같은 장치에서 원본 CUDA와 새 CUDA의 고정 계산량을 비교할 때:

```powershell
.\.venv\Scripts\python.exe -m hashcash_lab.gpu_benchmark --source work/source/Nonce --output work/gpu-benchmark.json
```

컴파일·자체 검증을 마친 뒤 같은 범위를 끝까지 계산합니다. 원본 커널 두 launch 구성, 새 커널의 대량 배치, 실제 서버 배치와 이전 작은 배치를 구분해 기록합니다. [GPU 원시 측정 결과](docs/hashcash-gpu-benchmark.json)를 참고하세요.

## 구성

- `src/hashcash/`: 독립 React 학습 화면·모바일·라이트/다크 테마.
- `hashcash_lab/cpu.py`: 메시지 SHA-256 상태 복사와 바이트 조건 검사.
- `hashcash_lab/gpu.py`: 선택적 CUDA 커널 생성·자체 검증·정확한 64비트 nonce.
- `hashcash_lab/server.py`: 로컬 HTTP API, 작업 제어·자동 전환·결과 재검증.
- `hashcash_lab/history.py`: SQLite에 검증된 발견 기록 저장·필터 조회·내보내기.
- `hashcash_lab/hybrid.py`: 겹치지 않는 CPU·GPU 구간 배정·실패 재시도·실제 중첩 계측.
- `hashcash_lab/optimization_benchmark.py`, `hybrid_benchmark.py`, `strategy_benchmark.py`: 같은 PC의 추가 최적화·동시 모드·방문 방향 비교.
- `hashcash_lab/benchmark.py`, `hashcash_lab/tests/`, `tests/hashcash.spec.ts`: 측정·검증.
- `scripts/hashcash.mjs`: 한 명령으로 서버 실행·종료.

Python 연산 서버가 필요하므로 `dist/index.html`만 정적으로 호스팅하면 탐색이 실행되지 않습니다. 배포 빌드를 확인할 때는 `python -m hashcash_lab.server`와 `npm.cmd run preview -- --port 5186 --strictPort`를 각각 별도 터미널에서 실행하세요. 기존 UTXO 핵심 코드는 이 브랜치에도 보존하지만 첫 화면에는 연결하지 않습니다.
