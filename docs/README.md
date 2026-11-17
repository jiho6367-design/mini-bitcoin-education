# 블록체인 실습실 문서

현재 팀 앱 기준일: **2026-11-17** · 브랜치 `blockchain-practice` · 실행 폴더 `experiments/blockchain-practice/`

**[온라인 실습실](https://blockchain-practice.onrender.com)** · **[공동 실습 참여](https://blockchain-practice.onrender.com/?classroom=join&room=V5JHT522)**

| 문서 | 확인할 내용 |
| --- | --- |
| [사용 안내](user-guide.md) | 기초 6단계, 혼자·팀 공동 실습, 넌스 찾기와 문제 해결 |
| [요구사항](requirements.md) | 현재 기능·학습 목표·데이터 보존과 완료 조건 |
| [설계](architecture.md) | Flask·기초 장부·UTXO·공동 실습·브라우저 연산·저장 구조 |
| [Bitcoin과의 차이](bitcoin-differences.md) | 기초·공동·넌스 모델의 규칙과 실제 네트워크 대비 한계 |
| [검증 기록](test-results.md) | 확인한 커밋, 실제 검사 결과와 재현 명령 |
| [최종보고서 · 팀 확인본](final-report.md) | 과제 요약·기여·6단계 학습·정상/거부 검증·결과·한계 |
| [최종보고서 검토 반영](final-report-review.md) | 독립 추가 검토의 지적·반영 위치와 확인 범위 |
| [제출용 시연 대본](demo-script.md) | 최신 세 화면을 보여주는 5분 이내 구성 |
| [개발 기록](development-log.md) | 팀 앱으로 이어진 실제 커밋과 설계 결정 |
| [변경 이력](../CHANGELOG.md) | 팀 앱 최신 업데이트와 과거 기록 구분 |
| [검토·피드백 반영](review-resolution.md) | 화면 간소화·가이드·기록·연산 모드 등 반영 상태 |
| [배포 상태 JSON](release-status.json) | 마지막 확인한 운영 커밋·주소·무료 구성·검증 근거 |
| [앱 실행 안내](../experiments/blockchain-practice/README.md) | 로컬 실행, 현재 화면과 저장 방식 |
| [운영 안내](../experiments/blockchain-practice/DEPLOYMENT.md) | 기존 Render·Neon 서비스의 유지보수와 재배포 |
| [상세 검증 이력](../experiments/blockchain-practice/VERIFICATION.md) | 이전 검증 당시의 결과와 최신 검증 요약 |

실제 마우스 조작과 한국어 클릭 자막을 담은 **4분 33초 시연 영상**은 앱의 **시작하기 → 시연 영상 보기**와 [MP4 파일](../experiments/blockchain-practice/static/media/blockchain-practice-demo-20261117.mp4)에서 확인합니다

개인 React 모델의 옛 문서와 캡처는 [과거 자료](archive/personal-react/README.md)에 따로 보관했습니다 그 자료의 ‘백엔드 없음’, ‘상태 미저장’ 등은 당시 개인 모델 설명입니다 현재 팀 앱 안내는 이 목차의 문서를 사용합니다 제공된 `졸업논문_수행계획서.pdf`는 수정하지 않은 배경자료입니다

검증 결과는 실행 날짜·대상 커밋·범위를 함께 기록하며, 과거 결과를 새 검증 결과로 바꾸지 않습니다 비공개 DB 연결값·쿠키·교사 코드·키·개인 상태 파일은 문서나 Git에 포함하지 않습니다
