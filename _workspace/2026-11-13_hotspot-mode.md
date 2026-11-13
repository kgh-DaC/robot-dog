# 인수 메모: USB / 핫스팟 두 연결 모드 (2026-11-13, 코드·QA 완료, 미업로드)

다른 세션이나 다른 계정에서 이어서 작업할 때 이 파일부터 읽는다. 이전 상황은 `_workspace/2026-11-12_handoff.md`에 있다.

## 사용자 요청
하나의 웹 페이지(`software/robot_control_web/index.html`)에서 연결 방법을 둘 중 하나로 골라 로봇에 접속한다.
1. **USB 시리얼**: 노트북에서 `python esp32-robot-dog-code-master/software/usb_gateway/usb_gateway.py`를 실행하고 http://127.0.0.1:8787 로 접속한다.
2. **핫스팟 Wi-Fi**: 로봇(ESP32)이 휴대폰 핫스팟에 접속하고, 브라우저가 로봇의 IP 또는 http://robotdog.local 로 붙는다.

## 정한 설계
- 로봇 자체 AP(`RobotDog-0FA1` / `12345678` / 192.168.4.1)는 **항상 켜 둔다**. 핫스팟 접속에 실패해도 AP로 접속할 수 있다. 모드는 `WIFI_AP_STA`.
- 핫스팟 이름과 비밀번호는 **직렬(USB) 명령으로만** 설정한다. 보드 내부 저장소 `Preferences "wifiSta"`에 저장하며, git이나 코드, 응답에는 비밀번호를 남기지 않는다.
  - `WIFI_SET <ssid><TAB><password>`: 비밀번호를 비우면 공개망. 활성 상태에서는 `ERR armed`.
  - `WIFI_CLEAR`: 저장된 핫스팟을 지운다.
- 접속 시도는 15초(`STA_TRY_MS`)가 지나면 포기하고, 30초(`STA_RETRY_MS`) 뒤 다시 시도한다. **활성 상태에서는 다시 시도하지 않는다.** 채널 탐색이 AP 쪽 조종을 흔들지 않게 하기 위해서다. 드라이버 자동 재접속(`setAutoReconnect`)은 끄고, 드라이버 저장(`persistent`)도 끈다.
- 웹 페이지가 게이트웨이에서 열렸을 때(127.0.0.1:8787) 핫스팟 모드를 고르면, 브라우저가 로봇 IP로 **직접** 요청한다(CORS).
  - ESP32는 Origin이 `http://127.0.0.1:8787` 또는 `http://localhost:8787`일 때만 허용한다. OPTIONS 사전 요청을 처리하고, `Access-Control-Allow-Private-Network`도 응답한다.
  - 다른 사이트는 계속 막힌다. `X-Robot-Control` 헤더 보호는 그대로 유지된다.
- Host 검사는 `192.168.4.1`, 현재 핫스팟 IP, `robotdog.local`을 허용하고 `:80`은 제거한 뒤 비교한다(DNS 재바인딩 방지 유지).
- 안전 규칙(명시적 ARM, 1초 하트비트 임대, 단일 조종자, STOP 최우선)은 바꾸지 않는다. 연결 모드를 바꾸면 기존 경로의 하트비트가 끊기므로 펌웨어가 스스로 정지한다. 페이지에서는 활성 상태일 때 모드 전환을 막는다.

## 완료 (컴파일·테스트 통과, **업로드 안 함, 실물 미시험**)
- 펌웨어 `tools/servoInitPose/web_control.ino`
  - STA 상태 머신: `loadStaConfig`, `staBegin`, `staStop`, `wifiStaService`, `wifiStaSet`, `wifiStaClear`
  - mDNS `robotdog.local`, CORS(`corsOriginAllowed`, `handlePreflight`), Host 검사 확장
  - 상태 JSON에 `wifi.sta = {status, ip, ssid, host}` 추가(비밀번호 없음). 본문 버퍼 1792 B, 최악 약 1.5 kB.
  - `WIFI` 직렬 줄 끝에 ` sta=<OFF|CONNECTING|CONNECTED|NOT_FOUND|FAILED> sta_ip=<ip 또는 -> sta_ssid=<줄 끝까지, 공백 가능>`
  - 이벤트 줄: `EVENT WIFI_STA connected ip=…` / `EVENT WIFI_STA lost` / `EVENT WIFI_STA NOT_FOUND|FAILED`
- 펌웨어 `tools/servoInitPose/servoInitPose.ino`
  - `commandBuffer` 64 → 128, `WIFI_SET`/`WIFI_CLEAR`를 strtok보다 먼저 처리
  - CAPS에 `STA1` 추가, `loop()`에서 잠금 안에서 `wifiStaService()` 호출
- 컴파일: `cd esp32-robot-dog-code-master/tools/servoInitPose && arduino-cli compile --fqbn esp32:esp32:esp32 --warnings default .` → 1,071,871 B (81%), 전역 52,076 B.
- 테스트: `python -m unittest discover tests` → 80개 통과. 바뀐 Host 검사에 맞게 `tests/test_usb_gateway.py`의 정적 검사를 갱신했다.

## 완료 2 (2026-11-13 오후, 테스트 96개 통과, 컴파일 1,081,215 B (82%), **업로드 안 함, 실물 미시험**)
- **게이트웨이** `usb_gateway.py`: 직렬 UTF-8, `parse_wifi_line`(sta_ssid는 줄 끝까지), `wifi.sta`(옛 펌웨어면 없음), WIFI 2초 주기 재읽기(`_wifi_status`, 설정 변경 직후 즉시),
  `WIFI_SET`/`WIFI_CLEAR` 명령(`check_sta_settings`로 펌웨어와 같은 제한 사전 검사, 유효 세션 필요, 활성 중 `armed` 거부, `STA1` 없으면 `firmware_without_hotspot`),
  `do_POST`는 `keep_blank_values=True`(빈 `pass` = 공개망, `pass` 없음 = `invalid_password`). 시작할 때 핫스팟 IP 출력.
- **테스트** `test_usb_gateway.py`: 가짜 펌웨어에 STA(WIFI 줄, WIFI_SET/CLEAR, 끝 TAB 보존) 추가, `HotspotTests` 7개 + HTTP 1개.
- **웹 페이지** `index.html`: 상단 "연결 방법" 카드(USB 게이트웨이 / 핫스팟 Wi-Fi, 주소 입력, 현재 대상), `apiBase`(같은 출처면 `""`),
  `connGen`으로 이전 대상의 늦은 응답 무시 + 진행 중 요청 abort, 활성(3초 이내 최신 상태) 중 전환 거부, `localStorage`(`robotdog.conn`) 저장,
  로봇에서 연 페이지는 USB 선택 시 안내만. Wi-Fi 카드에 "핫스팟 설정"(USB·최신 상태·sta 있을 때만, 활성 중 버튼 비활성, 전송 후 비밀번호 칸 비움).
  `web_page.h` 재생성.
- **테스트** `page_harness.js`에 `gateway` 모드(location·localStorage 가짜) 추가, `ConnectionModeTests` 8개.
- **문서** `usb_gateway/README.md`: 핫스팟 사용법 절, 표·Host 규칙·CORS·API(`wifi.sta`, `WIFI_SET/CLEAR`)·한계 갱신.

## 남은 작업
1. ~~QA~~ 완료. `robot-integration-qa` 지적 5건을 모두 고침 (테스트 100개 통과, 컴파일 1,082,259 B (82%)):
   - 활성화되면 진행 중인 핫스팟 접속 시도도 바로 멈춤 (`wifiStaService`의 `if (armed && staTrying)`, 이벤트 `EVENT WIFI_STA FAILED armed`). 정지 후 다시 시도.
   - DHCP 갱신으로 IP가 바뀌면 `staIp`도 따라감 (`staIpRaw` 비교) → Host 검사·상태가 새 IP를 씀.
   - 직렬 줄이 127바이트를 넘으면 그 줄의 나머지를 버림 (`readSerialCommands`의 `discarding`). 비밀번호 뒷부분이 명령으로 실행되지 않음.
   - 페이지: 명령 응답을 기다리는 동안 연결 방법 변경 거부(`cmdBusy`), 바뀐 뒤 도착한 명령 응답은 표시하지 않음.
   - 페이지: 게이트웨이 주소가 8787 포트가 아니면 핫스팟 모드에서 CORS 경고 표시(`corsOk`).
2. **사용자 승인 후**: 펌웨어 업로드(서보 전원 OFF, COM14. 업로드 방법은 `2026-11-12_handoff.md`의 "2026-11-13 업로드" 참고) → 게이트웨이 페이지에서 핫스팟 설정 저장 → 상태 `연결됨` 확인 → "핫스팟 Wi-Fi"로 전환해 상태 수신 확인 → 들어 올린 채 짧은 동작 시험.
   실물에서 볼 것: 크롬이 127.0.0.1 페이지 → 172.20.x.x 요청을 사전 요청과 함께 통과시키는지, 안드로이드에서 `robotdog.local` 여부, AP 채널 변경 때 끊김.

## 알아 둘 점과 한계
- 핫스팟에 접속하면 AP 채널이 핫스팟 채널로 바뀌어, AP에 붙어 있던 기기가 잠깐 끊길 수 있다. 활성 상태였다면 하트비트가 끊겨 정지한다(안전한 쪽).
- 핫스팟 비밀번호를 아는 사람은 같은 망에서 페이지를 열 수 있다. 조종은 세션 하나만 가능하고 STOP은 누구나 보낼 수 있다(기존 AP와 같은 모델).
- `robotdog.local`은 안드로이드 크롬에서 잘 안 될 수 있다. 그때는 IP를 쓴다.
- 게이트웨이 페이지 + 핫스팟 모드에서 탭을 닫을 때의 keepalive STOP은 교차 출처 사전 요청 때문에 브라우저가 막을 수 있다. 하트비트 임대(1초)가 대신 멈춘다.
- 핫스팟 SSID 끝의 공백은 게이트웨이 상태 표시에서 잘린다(`strip()`). 저장에는 영향 없음.
- 이 저장소에는 git 원격(remote)이 없다. 다른 PC에서 이어 가려면 폴더를 복사하거나 원격 저장소를 추가해야 한다.
- 작업 트리의 `CLAUDE.md`, `robot_dog_servo_control.ipynb` 변경은 사용자의 것이라 커밋하지 않는다.
