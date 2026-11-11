# 현재 웹 제어·모니터링 구성 (2026-11-11 코드 기준)

`software/web/`(바이너리 WebSocket)는 원본 프로젝트의 예전 UI다. 지금 실제로 쓰는 것은 아래 구성이다. 작업 전에 파일을 다시 읽어 이 문서가 여전히 맞는지 확인한다.

## 파일

| 역할 | 경로 (`esp32-robot-dog-code-master/` 기준) |
|---|---|
| 공용 웹 페이지 (원본) | `software/robot_control_web/index.html` — HTML/CSS/JS 한 파일 |
| 펌웨어 내장본 (생성물) | `tools/servoInitPose/web_page.h` — `python software/robot_control_web/embed_page.py`로 생성. 손으로 고치지 않는다 |
| 펌웨어 | `tools/servoInitPose/servoInitPose.ino`, `web_control.ino`, `imu_sensor.ino`, `level_control.ino` |
| USB 게이트웨이 | `software/usb_gateway/usb_gateway.py` (`index.html`을 직접 제공, `http://127.0.0.1:8787`) |
| 테스트 | 저장소 루트 `tests/test_control_page.py` + `tests/page_harness.js`(Node 가짜 DOM), `tests/test_usb_gateway.py` |

## 페이지 제약

- **외부 리소스 금지.** ESP32 핫스팟(`RobotDog-XXXX`, `http://192.168.4.1`)에는 인터넷이 없다. CDN 스크립트·글꼴·이미지를 쓰지 않고 한 파일 안에서 끝낸다(three.js 등 외부 라이브러리 불가 → Canvas 2D 또는 인라인 SVG로 직접 그린다).
- **크기.** 페이지는 플래시(PROGMEM)에 그대로 들어간다. 현재 약 25 kB. 늘어난 크기와 펌웨어 컴파일 결과(스케치 사용률)를 보고한다.
- **구문.** 기존 코드는 ES5 스타일(`var`, `function`)이고 `tests/page_harness.js`의 가짜 DOM에서 돈다. 새 DOM API를 쓰면 하네스에도 추가해야 한다.
- **두 경로 동일.** 페이지를 바꾸면 `embed_page.py`를 다시 실행한다. Wi-Fi 경로에 반영하려면 펌웨어 재업로드가 필요하다(사용자 승인, 서보 전원 OFF, BOOT 버튼).
- 밝은/어두운 테마 모두 CSS 변수로 지원한다.

## 상태 응답 `GET api/status?session=<hex>` (250 ms 주기로 폴링)

```
link "usb"|"wifi", connected, armed, controller "NONE|SERIAL|WIFI", youControl,
walk "STOPPED|FORWARD|BACKWARD|LEFT|RIGHT", homing, settled, resetReason, lastStop, uptimeMs, leaseTimeoutMs,
angles {LF|RF|LH|RH: [ALPHA, BETA, GAMMA]}   // 정수 도, 명령 목표각(피드백 아님), 중립 90/45/90
imu {ready, chip, whoAmI, address, valid, ageMs, accG{x,y,z}, axisDeg{x,y}, zeroed, rollMap, pitchMap, rollDeg, pitchDeg}
level {enabled, active, sensorUsable, rollCompDeg, pitchCompDeg, gammaOffsetDeg{LF,RF,LH,RH}, maxCompDeg, tiltLimitDeg}
standTrimDeg {LF,RF,LH,RH}   // 도. 서 있는 자세 GAMMA 보정. 2026-11-11 이후 펌웨어만 보냄. 없으면 알 수 없음(0 아님)
```

- `angles`에는 수평 보정(`level.gammaOffsetDeg`)과 서 있는 자세 보정(`standTrimDeg`)이 **포함되지 않는다**. 서보에 실제로 나가는 GAMMA = `angles` + `gammaOffsetDeg` + `standTrimDeg`. 직렬 경로에서는 LEVEL 줄의 `trim=` 필드로 오고 게이트웨이가 최상위 `standTrimDeg`로 옮긴다.
- 펌웨어 상태 본문 버퍼는 1536 B(`handleStatus`), IMU 360 B, level 320 B. 필드를 추가하면 최악 길이를 다시 계산한다.
- 게이트웨이는 같은 필드를 직렬 `STATUS` 응답에서 만들어 준다. 필드를 추가하면 펌웨어(직렬+Wi-Fi), 게이트웨이, 페이지, 테스트를 함께 바꾼다.
- roll: + = 오른쪽이 낮음, pitch: + = 앞쪽이 낮음. `rollMap`/`pitchMap`이 `NONE`이면 roll/pitch가 아니라 센서 축 값이다.

## 명령 `POST api/command` (`X-Robot-Control: 1`, form-urlencoded, `session` 포함)

`ARM`, `DISARM`, `HB`, `STOP`, `WALK dir=`, `LEVEL on=0|1`. 수평 유지 켜기는 이 페이지가 활성화(ARM)한 상태에서 축 설정이 저장돼 있을 때만 된다. 정지·비활성화·재부팅 시 수평 유지는 꺼진다.

## 바꾸면 안 되는 안전 동작

긴급 정지 버튼·Space/Esc, 250 ms 하트비트와 0.75초 신선도 조건, 탭 숨김·닫힘 시 STOP, 오래된 상태를 "알 수 없음"으로 표시하는 동작. 새 기능은 이 경로의 타이머·요청을 늦추거나 막지 않아야 한다(그리기는 가볍게, 폴링과 분리).
