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
