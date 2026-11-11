# 로봇개 프로젝트

## 하네스: 로봇개 IoT 및 디지털 트윈

**목표:** 안전한 ESP32 자세 안정화, INA219 에너지 분석, 내구성 있는 텔레메트리 로깅, 인터넷 모니터링, 인증된 원격 보행 제어를 포함하는 디지털 트윈 작업을 조율한다.

**호출 조건:** 로봇개 펌웨어, 센서, 텔레메트리, 웹 대시보드, 디지털 트윈, 원격 제어, 통합 또는 관련 테스트 요청에는 `robot-dog-orchestrator` 스킬을 사용한다. 간단하고 무관한 질문은 직접 답할 수 있다.

**루프 실행:** 작업 목록을 반복 처리할 때는 `robot-dog-loop` 스킬을 따른다. 작업 목록은 `_workspace/loop/BACKLOG.md`, 시작 명령은 `_workspace/loop/LOOP_PROMPT.md`에 있다. 루프가 도는 동안 `.claude/hooks/loop_guard.py`가 펌웨어 업로드, 직렬 포트 접근, 로봇 명령을 막는다.

**변경 이력:**
| 날짜 | 변경 내용 | 범위 | 사유 |
| --- | --- | --- | --- |
| 2026-11-05 | Harness v2 최초 구성 | 하네스 전체 | 임베디드, 텔레메트리, 디지털 트윈, QA를 물리적 안전 요구사항과 조율 |
| 2026-11-05 | 하네스 문서 한국어화 | 전체 문서 | 사용자 요청에 따라 문서 언어 통일 |
| 2026-11-11 | 수평 유지 ON/OFF 비교 그래프·관절 기구학 모형 명세 추가, 현재 웹 구성 문서화 | digital-twin 스킬·에이전트, QA 에이전트, 오케스트레이터 | 사용자 요청 기능을 구현할 수 있도록 실제 작업 대상(robot_control_web, servoInitPose, usb_gateway)과 검증 기준을 하네스에 반영 |
| 2026-11-11 | 루프 하네스 추가 (robot-dog-loop 스킬, 작업 목록·기록·시작 명령, 안전 훅) | `.claude/skills/robot-dog-loop`, `.claude/hooks`, `.claude/settings.json`, `_workspace/loop` | Ralph Loop로 작업 목록을 반복 처리하되 실물 동작은 사람 승인으로 남기기 위해 |
| 2026-11-11 | 루프 시작 명령을 `/ralph-loop:ralph-loop`으로 정정, jq 필요 조건 추가, 안전 훅의 상태 파일 이름 수정(`ralph-loop.local.md`) | `_workspace/loop/LOOP_PROMPT.md`, `robot-dog-loop` 스킬, `loop_guard.py` | `/ralph-loop`이 Unknown command로 나옴. 플러그인 실제 동작이 도움말과 달랐음 |
