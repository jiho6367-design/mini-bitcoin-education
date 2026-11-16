# 블록체인 실습실 선택형 Python·C 엔진

팀 브랜치 `blockchain-practice`의 `experiments/blockchain-practice/native_hashcash`에 있는 선택형 PC 연산 모듈입니다 `codex/hashcash-only`의 `c7f5aa72075310479eb954b4224a03cd0c0fa76a`에서 가져왔으며 원본 독립 서버나 서버 측 기록 저장기는 포함하지 않습니다 원래 SHA-256·uint64 규칙을 유지하는 Python CPU 탐색기, CuPy CUDA 엔진, 구간이 겹치지 않는 동시 계산, C 스케줄러와 C/header 소스 4개를 사용합니다

## 웹 모드와 PC 모드

일반 **CPU · GPU · CPU+GPU**는 접속한 브라우저의 Web Crypto·WebGPU로 계산하며 이 모듈 설치가 필요하지 않습니다 온라인 무료 Render에서는 네이티브 연산을 항상 끕니다 공개 화면의 **Python CPU+GPU · PC 전용**, **C CPU+GPU · PC 전용**을 고르면 지원 여부를 확인한 뒤 브라우저 CPU로 전환합니다

Python·C 모드는 이 PC에서 실행한 Flask 서버의 `/api/hashcash-native`를 사용합니다 `TEAM_NATIVE_HASHCASH=1`이어도 실제 연결과 원래 연결 주소가 루프백이고 Host가 `localhost`·`127.0.0.1`·`::1`일 때만 허용합니다 CUDA import·장치 초기화·C DLL 로드는 사용자가 작업을 시작할 때 수행하며 서버 시작이나 기능 조회만으로 설치·빌드하지 않습니다

Python 모드는 CUDA를 준비하지 못하면 Python CPU로 계산합니다 C 모드는 준비된 C 스케줄러에서 CUDA 사용이 불가능하면 C CPU를 사용합니다 PC API 자체가 없거나 사전 지원 확인에 실패하면 브라우저 CPU로 전환하고 실제 장치·이유를 화면에 표시합니다 이미 PC 시작 요청을 보낸 뒤 응답을 잃었을 때는 같은 작업을 브라우저에서 자동 재실행하지 않습니다

## 선택 설치와 실행

`experiments/blockchain-practice` 폴더에서 실행합니다 일반 웹 연산만 쓸 때는 `Start-Team.cmd`만 실행하면 됩니다

```powershell
# Python PC 모드 사용, CUDA가 없으면 Python CPU
.\Start-Team.cmd -Native
# 호환 NVIDIA 드라이버가 있는 PC에 선택 GPU 의존성 설치
.\Start-Team.cmd -Native -InstallGpu
# GCC·OpenSSL 개발 환경이 있는 PC에서 C 엔진 빌드·자체 검증
.\Start-Team.cmd -Native -BuildNative -Compiler <gcc.exe> -OpenSSLRoot <ucrt64>
```

GPU 선택 의존성은 이 폴더의 `requirements-gpu.txt`에 있습니다 호환 NVIDIA 드라이버가 필요하며 기본 Python CPU 탐색은 표준 라이브러리만 사용합니다 컴파일러·OpenSSL·GPU 런타임은 서버 시작 시 자동 설치하지 않습니다 C 빌드 도구가 준비되지 않은 사용자는 해당 모드를 선택하지 않아도 됩니다

기존 가상환경에서 C 빌드·자체 검증만 직접 실행하려면 다음 명령을 사용합니다

```powershell
.\.venv\Scripts\python.exe -m native_hashcash.hashcash_lab.native_scheduler_experiment --build --self-test --compiler <gcc.exe> --openssl-root <ucrt64>
```

빌드 산출물과 메타데이터는 Git에서 제외한 `native_hashcash/work/native_scheduler/`에 둡니다 소스·DLL 해시가 빌드 메타데이터와 일치해야 로드하며 바이너리를 저장소에 포함하지 않습니다

## 작업과 기록 보존

`hashcash_lab/search.py`의 원본 SearchConfig/SearchJob을 Flask 브리지가 브라우저별 작업으로 관리합니다 한 브라우저당 실행 작업 1개·서버 동시 작업 4개를 제한하고, 중지·화면 이탈 시 취소합니다 조회 연결이 사라지면 30초 임대 만료 후 작업을 정리합니다

결과는 기존 브라우저의 최근 50개 넌스 기록에 남습니다 기초·공동 실습 장부나 Neon DB에 결과를 쓰지 않으며 기존 기록을 이전·초기화하지 않습니다 메시지·난이도·넌스 범위는 웹 모드와 같은 단일 `SHA256(UTF8(메시지 + 10진수 넌스))` 규칙을 사용합니다

2026-11-17 운영 기준 `9fca791`에서는 공개 네이티브 비활성화와 CPU 전환을 확인했습니다 이전 로컬 검증에서는 실제 Python CPU·C CPU 실행, 재검증·기록 유지·중지를 확인했지만 팀 가상환경에 CuPy가 없어 실제 Python·C CUDA 실행은 확인하지 않았습니다 브라우저 WebGPU의 실제 GPU 검증과 이 PC CUDA 검증은 구분합니다 전체 검사·제한은 [검증 기록](../VERIFICATION.md)에 남깁니다
