# CPU·GPU 병목 조사와 추가 개선

측정: 2026-10-16 KST, 정리·회귀 확인: 2026-10-17 KST.
기준선: `8cd2e73f1f18323a6257e0d329c42218bca6a1d5`.
Intel Core Ultra 7 356H / RTX 5070 Laptop, Python 3.13.15 / CuPy 14.2.0.
사용자 AI-Lecture 지침과 AGENTS.md를 적용했다. [사전 후보와 추가 이유](hashcash-bottleneck-plan-20261016.md)를 남겼다.

## 확인한 원인

**GPU의 계산 속도보다 CPU의 추가 기여가 작고, 그 기여를 얻는 관리 비용이 더 컸다.**
종료 구간의 실제 버그도 있었다. GPU가 정상인데 고정 계산 범위만 소진된 경우,
기존 루프는 CPU 결과를 기다리며 `ready()`를 쉬지 않고 확인했다. 같은 Python 프로세스의
Pool 결과 전달 스레드가 실행될 기회를 지연시킬 수 있는 구조였다. GPU 작업이 없고
CPU 결과만 남으면 취소 가능한 1ms 이벤트 대기로 양보하도록 고쳤다.

동일한 1,073,741,824개 nonce를 7회 교대로 처리한 최종 계측에서, **각 실행의 CPU 결과
전달 최대 지연의 중앙값**은 다음과 같다. CPU 계산 종료→Pool callback을 측정했다.

| CPU 작업 수 | 기존 | 수정 |
|---|---:|---:|
| 1 | 14.213ms | 0.468ms |
| 4 | 56.311ms | 0.845ms |
| 15 | 212.066ms | 1.610ms |

CPU 15개에서 마지막 GPU 종료→전체 반환도 217.237→10.170ms로 감소했다.
이것은 **계측한 종료 지연의 개선**이다. 무한 탐색 전체에 같은 비율의 속도 개선을
주장하지 않는다. 특히 이 버그는 유한 범위 종료를 크게 악화시켜, 앞선 보고서의
고정 계산량 hybrid 비교에도 영향을 주었다. 당시 원시 자료는 역사적 결과로 보존한다.

남은 비용도 분리했다. 네이티브 CPU 8개 process 비교에서 GPU 단독의 GPU 배치 사이
공백 합 중앙값은 0.626ms, 동시 모드는 15.60ms였다. 비커널 GPU host 시간도
16.15→22.31ms로 늘었다. 이는 전송·호출·스케줄링 등이 합쳐진 구간이다.
서로 다른 중앙값을 더해 정확한 전체 시간 차이라고 해석하지 않았다.

## CPU 코드를 C로 옮겨도 충분한가

기존 `hashlib`의 SHA-256 자체도 이미 네이티브다. 매 nonce의 Python 숫자 변환·객체
생성·상태 복사·여러 함수 호출을 줄이기 위해 **전체 nonce 루프를 C/OpenSSL로 옮긴
후보**를 만들었다. 기존 GCC 16.2.0 / OpenSSL 3.6.5를 사용했으며 새 패키지는 설치하지
않았다. `ctypes.CDLL` 호출 중에는 GIL을 해제한다. production CPU 경로는 바꾸지 않았다.

첫 비교는 동일 GPU 16M 배치·CPU 50k 배치·같은 1,073,741,824개 nonce·13회 교대다.
준비된 process Pool과 thread Pool을 모두 유지하여 GPU 단독의 유휴 환경도 같게 했다.

| 구성 | 중앙 처리량 GH/s | GPU 단독보다 빠른 횟수 |
|---|---:|---:|
| GPU 단독 | 4.529 | — |
| C CPU process 1 / 4 / 8 | 4.279 / 4.182 / 4.159 | 모두 0/13 |
| C CPU thread 1 / 4 / 8 | 4.243 / 4.075 / 3.920 | 모두 0/13 |

CPU 8개도 전체 nonce 중 약 2.02%를 담당했다. CPU 50k 작업이 GPU 배치보다 먼저
끝나고 다음 할당까지 기다리는 공백도 관측했다. 첫 표본의 작업 시간 점유율 추정은
약 59~68%였으며, 이는 OS CPU 사용률이 아니다.

그래서 마지막 후보로 **CPU 250k / GPU 64M**으로 시간을 맞추고, 마지막 GPU 두 배치에는
CPU 신규 할당을 억제해 tail을 줄였다. 같은 GPU-only 64M를 대조군으로 13회 비교했다.
미완료 CPU 작업을 끝까지 받아 범위·개수를 검증했고, 실패 GPU 범위의 CPU 재시도는 유지했다.
이 tail 억제는 사전에 아는 유한 종료점을 이용한 실험이다. 정답 위치를 모르는 실제
proof 탐색에 그대로 적용한 결과로 해석할 수 없다.

| 구성 | 중앙 GH/s | 단독 대비 | 빠른 횟수 |
|---|---:|---:|---:|
| GPU 단독 64M | 4.978 | 기준 | — |
| C CPU process 1 / 4 / 8 | 4.943 / 4.975 / 4.996 | -0.70% / -0.06% / +0.37% | 4 / 4 / 7회 |
| C CPU thread 1 / 4 / 8 | 4.918 / 4.910 / 4.897 | -1.20% / -1.36% / -1.63% | 2 / 1 / 0회 |

최고 후보의 차이는 0.37%, 변동계수는 약 0.86%, 승리는 7/13회다. **GPU 단독보다
확실히 빠르다는 근거는 부족하다.** 실험 파일과 빌드 재현 방법만 보존하고 자동 모드는
GPU 우선을 유지했다. 이 결과로 모든 미래 네이티브·SIMD·다른 스케줄러의 가능성까지
부정하지 않는다. 전력·열·GIL·IPC 각각의 인과 비중을 완전히 분리한 프로파일링도 아니다.

## 실제 채택한 GPU 개선

CPU를 해시 계산에 추가하는 대신 GPU가 큰 계산 묶음을 처리하도록 해 CPU↔GPU 왕복
횟수를 줄였다. [NVIDIA 공식 지침](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#data-transfer-between-host-and-device)과 같은 방향이지만, 채택 근거는 아래 같은 PC 실측이다.
커널의 해시 규칙·구현은 바꾸지 않았다.

마지막 확인은 표본당 268,435,456개 nonce를 끝까지 처리하고 13회 순서를 교대했다.
초기화·컴파일을 제외한 처리량이며 독립 SHA-256 검증과 결과 수집은 포함한다.

| 실제 적용 조건 | 이전→새 배치 | 기존→개선 GH/s | 변화 | 빠른 횟수 |
|---|---:|---:|---:|---:|
| 28B ASCII·10자리 nonce·난이도 7 | 8M→16M | 4.154→4.485 | +7.97% | 12/13 |
| 같은 입력·난이도 8 | 8M→64M | 4.132→4.893 | +18.42% | 13/13 |

난이도 1~4의 262,144, 난이도 5의 4,194,304, 난이도 6의 8,388,608 배치는 유지했다.
큰 배치를 무조건 쓰면 쉬운 문제의 이미 찾은 해답을 보고하기까지 불필요하게 계산한다.
한국어·20자리 nonce·작은 난이도도 같은 유한 집합으로 확인했지만, 해당 고정 계산량의
속도 이득을 그대로 실제 발견 시간의 이득이라고 주장하지 않는다.

별도의 **13개 사전 고정 한국어 메시지·난이도 7 실제 proof 탐색**에서는 8M→16M의
발견 시간 중앙값이 28.749→27.429ms, 평균은 65.714→61.219ms였고 8/13회 빨랐다.
각 메시지의 결과를 모두 보존했다. 준비 비용을 제외한 한 메시지 집합의 결과이므로
매번 그만큼 빨리 찾는다고 보장하지 않는다. 최대 관측 동기 배치 시간은 8M 3.538ms,
16M 4.823ms, 64M 15.433ms였다. 이는 배치 경계에서 취소를 확인하는 간격의 실측이며,
HTTP를 포함한 취소 지연이나 모든 전력 상태의 최악 지연 한도는 아니다.

명시 funnelshift/lop3 커널과 packed ASCII 증가도 여섯 profile·13회 비교했다.
전자는 대체로 느렸고, 후자는 약 -0.36~+0.75%로 일관된 우위가 없어 채택하지 않았다.

## 전력 상태와 측정 한계

초기 진단 중 드라이버의 `Current Power Limit=21W`, `SW Power Cap=Active`를 관측했다.
이후 같은 조사에서 한도가 90W로 바뀌었다. AC 연결과 SAMSUNG MODE 전원 구성표는
읽기만 확인했으며 이 작업에서 전력·드라이버·팬 설정을 변경하지 않았다.
무엇이 한도를 바꿨는지는 확인하지 못했다. 각 표본의 한도·클록·온도를 기록했고,
최초 21W 한도는 23:38:53 도구 출력의 발췌·전사로 출처를 명시해 보존했다.
이후 저장한 `gpu-power-diagnostic.txt`는 이미 90W인 별도 조회이며 최초 21W의 근거가 아니다.
21W 초기 실험과 이후 절대 GH/s를 직접 비교하지 않았다. GPU가 제공하는 성능은
전력 상태에 의존하므로 이 측정에서 장치 전체의 최대 성능을 증명한 것은 아니다.

GPU 측정은 한 담당자가 수행했다. 마지막 latency 검증과 진단이 잠시 겹친 한 실행은
폐기했고 두 검증을 각각 단독 재실행했다. 폐기 사유도 원시 묶음에 기록했다.
준비 시간·실제 발견 운·고정 처리량을 구분했고 개인 DB는 측정에 사용하지 않았다.

## 검증과 재현

Python **99개**(실제 CUDA 7개·native vector 5개 포함), TypeScript·lint·Vitest **41개**·빌드,
Edge E2E **5개**가 통과했다. 고정 범위의 중복·누락, uint64·Unicode·실패 재시도·취소·
재시작·proof 후 정리 오류·영구 저장을 검사했다. 별도 임시 DB에서 난이도 8의 GPU와
hybrid가 실제 64M 배치를 사용해 유효한 proof를 찾고 각각 한 건 저장하는 것도 확인했다.
제작과 독립 검토를 나눴다. 최종 검사 결과는 원시 묶음의 validation 항목에 있다.

전체 표본·타임라인·해시·빌드 정보·측정 당시 소스는
[압축 원시 JSON](hashcash-bottleneck-20261016.json.gz)에 있다. 압축은 저장 용량을 줄이기
위한 것이며 JSON 숫자나 표본을 버리지 않는다. 다음은 worktree 루트의 PowerShell 명령이다.

```powershell
# 측정 시작 전: Git 기준선 파일 보존
.\.venv\Scripts\python.exe -c "from pathlib import Path; import subprocess; p=Path('work/bottleneck-baseline'); p.mkdir(parents=True,exist_ok=True); [(p/n).write_bytes(subprocess.check_output(['git','show','8cd2e73:hashcash_lab/'+n])) for n in ('gpu.py','hybrid.py','cpu.py','server.py')]"
.\.venv\Scripts\python.exe -m hashcash_lab.native_cpu_experiment --build --self-test
# 반드시 하나씩 완료한 뒤 다음 명령 실행; 다른 연산 부하를 중단한 상태에서 비교
.\.venv\Scripts\python.exe -m hashcash_lab.bottleneck_benchmark --stage diagnose --repeats 7 --output work/diagnosis.json
.\.venv\Scripts\python.exe -m hashcash_lab.bottleneck_benchmark --stage native_threads --repeats 13 --output work/native.json
.\.venv\Scripts\python.exe -m hashcash_lab.native_amortized_benchmark --repeats 13 --output work/native-large.json
.\.venv\Scripts\python.exe -m hashcash_lab.gpu_followup_benchmark --stage kernels --repeats 13 --output work/kernels.json
.\.venv\Scripts\python.exe -m hashcash_lab.gpu_followup_benchmark --stage batches --repeats 13 --output work/batches.json
.\.venv\Scripts\python.exe -m hashcash_lab.batch_latency_benchmark --output work/latency.json
.\.venv\Scripts\python.exe -c "import gzip,json; d=json.load(gzip.open('docs/hashcash-bottleneck-20261016.json.gz','rt',encoding='utf-8')); print(d.keys())"
```

실험용 OpenSSL 저수준 SHA API는 deprecated 상태라 일반 실행 의존성으로 추가하지 않았다.
컴파일러·OpenSSL이 없는 PC에서도 기존 CPU/GPU 실행은 그대로 가능하다.
