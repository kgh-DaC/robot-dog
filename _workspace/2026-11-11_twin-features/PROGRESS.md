# 수평 유지 비교 그래프 + 관절 기구학 모형 — 진행 기록

명세: `.claude/skills/robot-digital-twin/references/` (`level-comparison.md`, `kinematic-model.md`, `current-web-stack.md`)
작업 파일: `esp32-robot-dog-code-master/software/robot_control_web/index.html` (작업 전 원본 백업은 세션 scratchpad에만 있음 — 사라질 수 있음)
이어서 할 때: 이 파일의 체크리스트에서 첫 미완료 항목부터 진행. 항목을 끝낼 때마다 여기 체크.

## 체크리스트
- [x] 1. HTML/CSS: 관절 모형 카드(`modelCard`, `mdCanvas`, `mdV3d/mdVSide/mdVFront/mdVTop`, `mdCorr`, `mdGhost`, 표 `joints` 6열), 비교 영역(`cmpState`, `cmpRoll`, `cmpPitch`, `cmpRollTip`, `cmpPitchTip`, `cmpSummary`, `cmpBody`, `cmpClear`, `cmpCsv`)
- [x] 2. JS 순기구학 `fkLeg`(θ2=BETA 뒤쪽, 바깥 기울기=90−ALPHA, 무릎 사이각=GAMMA) + `window.RobotTwin`으로 테스트에 노출
- [x] 3. JS 모형 그리기(Canvas 2D 정사영, 3D 끌기 + 옆/앞/위), 관절 표 6열(무릎·발 좌표), 오래된 상태면 "알 수 없음"
- [x] 4. JS 비교 기록(활성 상태에서만, 켜짐=level.active, 조건=walk, 전환 후 1초 제외, 최소 20표본), 통계 표·요약·그래프 2개·지우기·CSV
- [x] 5. `tests/page_harness.js` + `tests/test_control_page.py` 확장 (FK 검증점, 비교 통계, 새 캔버스 stub)
- [x] 6. `python software/robot_control_web/embed_page.py` → `python -m unittest discover tests` 전부 통과
- [x] 7. 브라우저에서 눈으로 확인(게이트웨이 http://127.0.0.1:8787 — index.html을 직접 제공하므로 새로고침만 하면 됨)
- [x] 8. 메모리·01_resume 갱신, 사용자 보고

## 결과 (2026-11-11 완료)
- 테스트 69개 통과(기존 53 + 관절 모형 9 + 수평 비교 7). `web_page.h` 재생성 완료(46.7 kB, 이전 25 kB).
- USB 게이트웨이 페이지(127.0.0.1:8787)에서 헤드리스 Edge 화면 캡처로 모형·비교 영역 표시 확인(보드 0FA1, 비활성 상태).
- 남은 일(사용자 승인 필요): ① 펌웨어 컴파일 확인 + 업로드(Wi-Fi 경로에 새 페이지 반영, 스윙 -11도 이때 함께 들어감) ② 활성화 후 수평 유지 꺼짐/켜짐 각 5초 이상으로 실제 비교 ③ 앞뒤 어깨 간격 실측 → index.html `GEO.SY` ④ 서 있는 자세 보정(`standTrimDeg`)을 상태 응답에 추가할지 결정.
- 펌웨어 컴파일은 이번에 하지 않음(페이지가 21 kB 늘어남 — 플래시 여유 확인 필요).

## 주의 (작업 중 메모 — 위 결과가 최신)
- 현재 상태(1번만 완료)에서는 페이지 JS가 새 요소를 채우지 않는다. 관절 표는 6열 머리글에 4열 데이터라 어긋나 보임 → 2~4번을 끝내야 정상.
- `web_page.h`는 아직 다시 만들지 않음 → `tests`의 내장본 일치 테스트는 6번 전까지 실패한다.
- 펌웨어 업로드·실물 동작 명령 금지(사용자 승인 필요). 보드 0FA1에는 스윙 -14 펌웨어.
- 하네스 `querySelectorAll`은 방향 버튼만 돌려주므로 새 요소는 id로만 찾는다. `Date`는 `Date.now`만 있다.
