# 인수 메모: USB / 핫스팟 두 연결 모드 (2026-11-13, 진행 중)

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

## 남은 작업 (순서대로)
1. **게이트웨이** `software/usb_gateway/usb_gateway.py`
   - `_readline` / `_exchange`를 ASCII에서 UTF-8로 바꾼다(한글·’ 포함 핫스팟 이름).
   - `WIFI` 줄 파서를 추가한다. `sta_ssid=`는 `" sta_ssid="`로 partition해서 줄 끝까지 읽는다. 결과에 `wifi.sta = {status, ip, ssid, host}`를 넣고, 옛 펌웨어면 생략한다.
   - `_sensor_status` 주기(0.5초)나 약 2초 주기로 `WIFI`를 다시 읽어 IP 변화를 반영한다(지금은 핸드셰이크 때 한 번만 읽음).
   - 명령 `WIFI_SET`(ssid, pass 필드)과 `WIFI_CLEAR`를 추가한다. 유효한 세션이 필요하고, 활성 상태면 거부한다. ssid는 1~32바이트 UTF-8에 탭·제어문자 금지, pass는 비어 있거나 인쇄 가능한 ASCII 8~63자. `do_POST`에서 `field("ssid")`, `field("pass")`를 넘긴다. 비밀번호는 로그나 상태에 넣지 않는다.
   - 가짜 펌웨어(`tests/test_usb_gateway.py`의 `_handle`)에 WIFI 줄(sta 포함), `WIFI_SET`/`WIFI_CLEAR`를 추가하고 테스트를 작성한다(옛 펌웨어 WIFI 줄 호환 포함).
2. **웹 페이지** `software/robot_control_web/index.html`
   - 상단에 연결 카드를 둔다: `[USB 게이트웨이] [핫스팟 Wi-Fi]` 선택, 핫스팟용 주소 입력(기본값은 `state.wifi.sta.ip`, 저장값, `robotdog.local` 순), 접속 버튼, 현재 대상 표시.
   - 모든 요청(`api/status`, `api/command`)에 `apiBase`를 앞에 붙인다. 페이지를 연 곳과 같은 경로면 `""`(기존 상대 경로 그대로라 하네스 테스트 유지), 아니면 `http://<host>/`.
   - 페이지가 로봇에서 열렸을 때 USB 모드를 고르면, 직접 연결하지 않고 "노트북에서 http://127.0.0.1:8787 을 여세요"라고 안내한다(사설망에서 루프백으로 가는 요청은 막힘).
   - 활성 상태이고 이 페이지가 조종 중이면 모드 전환을 거부한다. 전환하면 `state=null`, `lastOk=0`으로 초기화한다.
   - 모드와 주소는 `localStorage`에 저장한다(try/catch로 감싼다).
   - USB 모드이고 상태가 최신일 때만 "핫스팟 설정" 칸(SSID, 비밀번호, 저장 후 접속, 설정 지우기)과 STA 상태(연결됨 IP / 연결 중 / 못 찾음 / 실패 / 설정 없음)를 보여 준다.
   - `wifiCard`의 AP 안내 문구에 핫스팟 정보를 덧붙인다.
   - 다음으로 `python software/robot_control_web/embed_page.py`로 `web_page.h`를 재생성하고, `tests/page_harness.js`와 `tests/test_control_page.py`에 모드 전환, apiBase, 활성 중 전환 거부 테스트를 추가한다.
3. **문서**: `software/usb_gateway/README.md`에 핫스팟 사용법을 쓴다(아이폰은 "호환성 최대화" 켜기 = 2.4 GHz 필수, ESP32는 5 GHz 불가).
4. **QA**: `robot-integration-qa`로 CORS 허용 범위, Host 검사, 비밀번호 비노출, 활성 중 재시도 없음을 검토한다.
5. **사용자 승인 후**: 펌웨어 업로드(서보 전원 OFF, COM14. 업로드 방법은 `2026-11-12_handoff.md`의 "2026-11-13 업로드" 참고) → `WIFI_SET` → 핫스팟 접속 확인 → 들어 올린 채 짧은 동작 시험.

## 알아 둘 점과 한계
- 핫스팟에 접속하면 AP 채널이 핫스팟 채널로 바뀌어, AP에 붙어 있던 기기가 잠깐 끊길 수 있다. 활성 상태였다면 하트비트가 끊겨 정지한다(안전한 쪽).
- 핫스팟 비밀번호를 아는 사람은 같은 망에서 페이지를 열 수 있다. 조종은 세션 하나만 가능하고 STOP은 누구나 보낼 수 있다(기존 AP와 같은 모델).
- `robotdog.local`은 안드로이드 크롬에서 잘 안 될 수 있다. 그때는 IP를 쓴다.
- 이 저장소에는 git 원격(remote)이 없다. 다른 PC에서 이어 가려면 폴더를 복사하거나 원격 저장소를 추가해야 한다.
- 작업 트리의 `CLAUDE.md`, `robot_dog_servo_control.ipynb` 변경은 사용자의 것이라 커밋하지 않는다.
