# 인계 메모: 웹 보행 제어 (USB + ESP32 Wi-Fi) — 2026-11-09

## 완료 (디스크에 저장됨)
- 서보 시작/종료 안정화 (보드 업로드 완료·검증됨)
  - 부팅 즉시 12개 서보 펄스 출력, 저장된 자세에서 중립까지 천천히 복귀 (약 10°/s)
  - 서보가 0.3초 이상 멈추면 자세를 NVS에 저장 (보행 중 포함, 1초 간격 제한)
  - `SETTLED`, `RESET_REASON` 명령 추가. 노트북은 DTR/RTS를 끈 채 포트를 열고, 자세 저장을 확인한 뒤 닫음
- 웹 제어 (코드 완료, 컴파일·테스트 통과, **아직 보드에 업로드 안 함**)
  - 펌웨어 `tools/servoInitPose/servoInitPose.ino`: ARM/DISARM/HB/WALKHB/STATE, 1초 하트비트 리스, 제어 주체(SERIAL/WIFI), 활성화 중에는 수동 명령 거부, CAPS `... WALK4 LEASE1 WIFI1`
  - `tools/servoInitPose/web_control.ino`: SoftAP `RobotDog-XXXX`(무작위 비밀번호를 NVS에 저장), `http://192.168.4.1`
  - `tools/servoInitPose/web_page.h`: `software/robot_control_web/embed_page.py`로 생성
  - `software/robot_control_web/index.html`: 두 경로가 함께 쓰는 페이지
  - `software/usb_gateway/usb_gateway.py` + `README.md`: `python usb_gateway.py --port COM14` → `http://127.0.0.1:8787`
  - 4way 노트북의 CAPS 검사를 완화함 (`WALK4` 토큰만 확인)
  - 테스트: `python -m pytest -q tests` → 20개 통과. 펌웨어 컴파일 OK (플래시 72%)

## QA 리뷰 결과 (2026-11-09 수신, 아직 미반영)
QA가 확인한 것: 테스트 20개 통과, 경고 없이 컴파일됨, web_page.h가 최신 상태, snprintf 버퍼 여유 있음(상태 JSON 최대 370B / 버퍼 640B), millis 오버플로 처리 정상, 부팅 후 자동 활성화 없음, STOP은 모든 경로에서 허용, 페이지·펌웨어·게이트웨이 사이 JSON 필드 일치, 노트북 두 개와 호환.

수정할 항목 (우선순위 순):
1. **HIGH:** 기존 시리얼 `WALK <dir>` 명령은 ARM과 리스 없이 계속 걷는다 (servoInitPose.ino 474-501행). 노트북 커널이 죽거나 USB가 빠지면 멈추지 않는다.
   → 기존 WALK에도 시리얼 리스를 적용하고, 4way 노트북을 ARM/WALKHB + 250ms HB 방식으로 바꾼다. 아니면 별도로 긴 타임아웃(예: 3초, 시리얼 명령이 올 때마다 갱신)을 두고 주석과 문서를 수정한다.
2. **MED-HIGH:** Wi-Fi API에 Host 헤더 검사가 없어 DNS 리바인딩으로 ARM·WALK가 가능하다 (web_control.ino handleStatus/handleCommand).
   → `webServer.hostHeader()`가 `192.168.4.1` 또는 `192.168.4.1:80`이 아니면 403 `bad_host`로 거부한다.
3. **MED:** 상태 정보가 오래돼도 페이지가 하트비트를 계속 보낸다 (index.html 하트비트 interval).
   → `Date.now()-lastOk > 750`이면 하트비트를 보내지 않는다. 게이트웨이 `status()`에서 int() 변환의 ValueError를 잡아 `connected:false`로 반환한다.
4. **MED:** `WebServer.handleClient()`가 블로킹이라 최대 5초 동안 리스 검사, 시리얼 STOP, 서보 이동이 멈출 수 있다.
   → 안전 로직(리스, 시리얼, 서보 이동)을 별도 FreeRTOS 태스크나 타이머로 분리하거나, 웹 서버를 별도 태스크(mutex 사용)로 돌리고 타임아웃을 줄인다.
5. **MED:** 게이트웨이 `connect()`가 최대 12초 동안 락을 잡고 있어 STOP이 지연될 수 있다.
   → HELLO 핸드셰이크는 락 밖에서 하고, STOP은 대기 없이 바로 쓰게 한다. 폴링 중에는 연결 마감 시간을 짧게 한다.
6. **LOW:** `_transact`가 이전 응답이나 늦게 온 응답과 잘못 짝지어질 수 있다.
   → 쓰기 전에 `reset_input_buffer()`를 호출하고, 기대하는 응답 줄 전체로 비교한다.
7. **LOW:** 게이트웨이 `status()`가 실제 오류 코드를 숨긴다.
   → `exc.code`를 반환하고, 포트가 실제로 닫혔을 때만 `connected:false`로 한다.
8. **LOW:** 연결이 안 된 상태에서 페이지가 "비활성"과 "undefined"를 표시한다.
   → 상태가 오래됐으면 "알 수 없음"과 "-"로 표시한다.
9. **LOW:** STOP을 누르면 노트북으로 잡아 둔 자세도 중립으로 돌아간다. 운영 안내 문서에 명시한다.
10. **LOW:** `--host ::1`은 동작하지 않는다. 127.x 주소만 허용한다.
11. **INFO:** 게이트웨이는 연결할 때 STOP을 보내지 않는다. 1번이 수정되면 문제없다.

테스트 보강: 가짜 시계로 리스 만료 테스트, 상태가 오래됐을 때 하트비트를 보내지 않는지 테스트, 펌웨어 Host 거부 테스트를 추가한다. FakeFirmware에 리스, controlled_by_other, homing을 모델링한다.

## 남은 일 (다음에 이어서)
1. **위 QA 1~8번 수정** (특히 1~4번은 업로드 전에 반드시)
2. 펌웨어 업로드: 서보 전원을 끄고 BOOT 버튼을 누른 상태에서 `arduino-cli upload -p COM14 --fqbn esp32:esp32:esp32 .`
3. 실물 확인: USB 게이트웨이 페이지와 Wi-Fi 페이지에서 활성화 → 앞으로 → 정지. 탭 닫기와 하트비트 끊김 시 자동 정지, 리셋 원인 표시 확인
4. 보행 개선: 현재 보행은 개방 루프 프로토타입(대각선 교대, beta ±5°, gamma +8°, 1.2초 위상). 실제로 앞으로 나아가는지는 시험하고 조정해야 함

## 주의
- 이 보드는 업로드할 때 BOOT 버튼이 필요함 (GPIO 2에 RH/BETA 서보가 연결돼 있어서일 수 있음)
- 배터리 하나가 ESP32와 서보에 함께 전원을 공급하므로 브라운아웃 가능성이 있음. Wi-Fi 송신 출력은 8.5 dBm으로 낮춤
- 실물 동작 명령과 업로드는 사용자 승인 후에만 수행
