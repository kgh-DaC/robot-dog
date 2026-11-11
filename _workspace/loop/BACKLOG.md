# 루프 작업 목록

상태: `[ ]` 대기 · `[~]` 진행 중 · `[x]` 완료 · `[!]` 차단(사용자 확인 필요)
규칙은 `.claude/skills/robot-dog-loop/SKILL.md`. 한 회차에 한 항목만 한다.

## 작업

- [x] L1. 펌웨어 컴파일 확인 (업로드 없음)
  - 범위: `esp32-robot-dog-code-master/tools/servoInitPose/` (코드 수정 없음이 기본)
  - 완료 기준: `arduino-cli compile`이 오류 없이 끝나고, 스케치 크기·사용률을 `LOG.md`에 기록한다. 페이지가 25 kB → 47 kB로 커진 뒤 첫 컴파일이다. 보드 FQBN과 이전 컴파일 방법은 `_workspace/2026-11-09_web-control/01_resume_2026-11-09_qa-fixes.md`에서 찾는다. 찾지 못하면 `[!]`.
- [x] L2. 상태 응답에 서 있는 자세 보정값 `standTrimDeg` 추가
  - 범위: `servoInitPose.ino`(직렬 `STATUS`), `web_control.ino`(Wi-Fi JSON, 버퍼 최악 길이 재계산), `usb_gateway.py`, `tests/test_usb_gateway.py`
  - 완료 기준: 두 경로 모두 `standTrimDeg {LF,RF,LH,RH}`(도)를 내보내고, 필드가 없는 옛 펌웨어 응답에서도 게이트웨이가 오류 없이 동작한다(필드 생략). 테스트 추가, 전체 통과, 컴파일 통과.
  - 참고: `.claude/skills/robot-digital-twin/references/kinematic-model.md` "어떤 각도를 그릴 것인가"
- [x] L3. 관절 모형에 서 있는 자세 보정 반영
  - 범위: `software/robot_control_web/index.html`, `tests/page_harness.js`, `tests/test_control_page.py`
  - 완료 기준: `standTrimDeg`가 있으면 "보정 포함"일 때 GAMMA에 더하고, 없으면 더하지 않고 안내 문구에 "미포함"을 유지한다. 표에 보정 출처가 드러난다. 테스트 추가(있음/없음 두 경우), 전체 통과, `web_page.h` 재생성.
  - 의존: L2
- [x] L4. 수평 비교 통계의 순수 함수 테스트 보강
  - 범위: `tests/page_harness.js`, `tests/test_control_page.py`
  - 완료 기준: `RobotTwin.cmpStats`를 직접 호출해 평균·평균 절대값·RMS·최대·폭을 부호가 섞인 표본으로 검증한다. 전환 후 1초 제외와 60초 창 제한을 검증한다. 전체 통과.
- [x] L5. 독립 QA 검토
  - 범위: 읽기 위주. `robot-integration-qa` 에이전트의 필수 검증 항목 중 웹 페이지·수평 비교·기구학 모형 부분
  - 완료 기준: `index.html`에 외부 URL이 없고, `web_page.h`가 일치하고, 하트비트·정지·오래된 상태 표시 경로가 2026-11-11 변경 전과 같은지 확인한 결과를 `LOG.md`에 근거(파일:줄)와 함께 적는다. 발견한 문제는 새 항목으로 이 목록 끝에 추가한다.
- [x] L6. 사용 설명 갱신
  - 범위: `esp32-robot-dog-code-master/software/usb_gateway/README.md`
  - 완료 기준: "관절 모형"과 "수평 유지 켜짐/꺼짐 비교" 사용법, 표현 한계(명령 각도 기준, 4 Hz, 바닥 접촉 미표현)를 한국어로 추가한다. 기존 안전 규칙 절은 바꾸지 않는다.
- [x] L7. 그리기 오류가 나도 오래된 상태 표시가 갱신되게 하기 (L5에서 발견, 낮음)
  - 범위: `software/robot_control_web/index.html`의 `render()`, `tests/page_harness.js`, `tests/test_control_page.py`
  - 완료 기준: 연결이 끊긴 상태에서 `renderModel` 또는 `renderCompare`가 예외를 던져도 `sArmed`·`sWalk` 등이 "알 수 없음"으로 바뀐다(상태 글자 갱신을 그리기 호출보다 먼저 하거나 그리기를 예외로부터 분리). 하네스에서 캔버스가 예외를 던지는 경우를 추가해 검증. 버튼 비활성화·하트비트·정지 코드는 바꾸지 않는다. 전체 통과, `web_page.h` 재생성.
- [x] L8. 수평 보정값이 없을 때 "보정 포함"이 0으로 가정하지 않고 알 수 없음을 표시 (L5에서 발견, 낮음)
  - 범위: `index.html`의 `modelAngles`·`renderModel` 안내 줄, `tests/page_harness.js`, `tests/test_control_page.py`
  - 완료 기준: 상태 응답에 `level.gammaOffsetDeg`가 없거나 숫자가 아니면 안내 줄에 "수평 보정: 알 수 없음"이 나오고, 값이 있으면 지금과 같다. 테스트 추가(있음/없음), 전체 통과, `web_page.h` 재생성.
  - 참고: `.claude/skills/robot-digital-twin/references/kinematic-model.md` "값이 없으면 0으로 가정하지 말고"

## 사용자 확인 필요

루프가 할 수 없거나 판단할 수 없는 일. 루프는 여기에 적기만 한다.

- 펌웨어 업로드(새 페이지를 Wi-Fi 경로에 반영, 스윙 들기 -11 포함). 서보 전원 OFF, BOOT 버튼 필요.
- 실물 수평 비교 측정: 활성화 후 수평 유지 꺼짐/켜짐 각 5초 이상.
- 앞뒤 어깨 간격 실측 → `index.html`의 `GEO.SY`.
- 모형이 실물과 닮았는지 눈으로 확인.
