# 루프 회차 기록

회차마다 아래 형식으로 뒤에 덧붙인다.

```
## 회차 N — <항목 ID> <제목> — 완료 | 진행 중 | 차단
- 바꾼 파일:
- 검증: (실행한 명령과 결과, 테스트 수)
- 남은 것 / 다음에 할 것:
```

## 회차 1 — L1 펌웨어 컴파일 확인 (업로드 없음) — 완료
- 바꾼 파일: 없음 (코드 수정 없음). `BACKLOG.md`, `LOG.md`만 갱신.
- 검증: `tools/servoInitPose`에서 `arduino-cli compile --fqbn esp32:esp32:esp32 --warnings default .` → 오류·경고 없이 완료. 스케치 1,027,095 B (78%, 최대 1,310,720 B), 전역 변수 49,804 B (15%). 이전 기록은 76%였으므로 페이지 증가분(약 21 kB)이 반영된 값이다. `python -m unittest discover tests` 69개 통과.
- 남은 것 / 다음에 할 것: 플래시 여유 약 284 kB. 업로드는 하지 않았다(사용자 확인 필요 항목). 기본 빌드 경로로 컴파일해서 `tools/servoInitPose/build/`의 이전 바이너리는 그대로다. 다음 항목은 L2.

## 회차 2 — L2 상태 응답에 서 있는 자세 보정값 standTrimDeg 추가 — 완료
- 바꾼 파일: `tools/servoInitPose/level_control.ino`(직렬 LEVEL 줄 끝에 `trim=LF/RF/LH/RH` 추가), `tools/servoInitPose/web_control.ino`(Wi-Fi 상태 JSON에 최상위 `standTrimDeg` 추가, 버퍼 주석 갱신), `software/usb_gateway/usb_gateway.py`(`parse_stand_trim`, 상태 응답에 `standTrimDeg`), `tests/test_usb_gateway.py`(테스트 4개 추가), `.claude/skills/robot-digital-twin/references/current-web-stack.md`(계약 반영).
- 계약: `standTrimDeg {LF,RF,LH,RH}`(도, 소수 1자리). 직렬 경로는 LEVEL 줄의 마지막 필드라서 옛 게이트웨이가 무시해도 동작한다. 옛 펌웨어처럼 필드가 없거나 형식이 틀리면 게이트웨이는 `standTrimDeg`를 응답에서 뺀다(0이 아니라 알 수 없음).
- 검증: `python -m unittest discover tests` 73개 통과(69 + 4). `arduino-cli compile --fqbn esp32:esp32:esp32 --warnings default .` 오류·경고 없음, 스케치 1,027,299 B (78%). Wi-Fi JSON 버퍼: 추가분 최악 약 75 B, 본문 최악 약 1.2 kB / 1536 B.
- 남은 것 / 다음에 할 것: 보드에는 아직 옛 펌웨어라 실제 응답에는 `standTrimDeg`가 없다(업로드는 사용자 확인 필요). 실행 중인 게이트웨이 프로세스도 옛 코드다(재시작은 루프에서 하지 않음). 페이지는 아직 이 필드를 쓰지 않는다 → L3.

## 회차 3 — L3 관절 모형에 서 있는 자세 보정 반영 — 완료
- 바꾼 파일: `software/robot_control_web/index.html`(`standTrim`, `modelAngles`에 보정 합산, 버튼 이름 "보정 포함", 안내 줄 `mdTrim`), `tools/servoInitPose/web_page.h`(재생성, 47.8 kB), `tests/page_harness.js`, `tests/test_control_page.py`(테스트 1개 추가).
- 동작: `standTrimDeg`가 네 다리 모두 숫자로 오면 "보정 포함"일 때 GAMMA에 더한다(수평 보정 + 자세 보정 = 서보에 보내는 각도). 필드가 없거나 일부가 비면 더하지 않고 "알 수 없음 — 포함되지 않습니다"로 표시한다. 연결이 끊기면 안내 줄을 비운다.
- 검증: `embed_page.py` 실행 후 `python -m unittest discover tests` 74개 통과(73 + 1). 펌웨어 컴파일은 이번 회차에 다시 하지 않았다(바뀐 것은 내장 페이지 문자열 약 1 kB뿐, L2에서 78%).
- 남은 것 / 다음에 할 것: 보드와 실행 중인 게이트웨이는 옛 코드라 지금 화면에는 "알 수 없음"으로 나온다. 업로드와 게이트웨이 재시작 뒤에 값이 보인다(사용자 확인 필요). 다음 항목은 L4.

## 회차 4 — L4 수평 비교 통계의 순수 함수 테스트 보강 — 완료
- 바꾼 파일: `tests/page_harness.js`(`RobotTwin.cmpStats` 직접 호출, 짧은 구간·73초 구간 시나리오 추가), `tests/test_control_page.py`(테스트 4개 추가). 페이지(`index.html`)는 바꾸지 않아 `web_page.h` 재생성 없음.
- 검증한 것: 부호가 섞인 표본(a = 3, -1, -4, 2 / b = -2, 2, -6, 6)으로 평균 0, 평균 절대값, RMS, 최대 절대값(음수 쪽), 폭, 구간 길이. 빈 배열은 `null`, 표본 1개는 폭 0. 전환 후 1초 미만 표본은 CSV의 `used_in_stats`가 0이고 요약의 표본 수에서 빠진다. 73초 기록 뒤에는 구간이 60초 이하로 잘리고 표의 구간 길이와 일치한다.
- 검증: `python -m unittest discover tests` 78개 통과(74 + 4).
- 남은 것 / 다음에 할 것: 전환 후 1초 제외와 60초 창은 `recordCompare`가 내보내지지 않아 모의 상태 폴링과 CSV로 간접 확인했다. 실물 측정값 검증은 아니다. 다음 항목은 L5.
