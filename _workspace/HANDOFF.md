# 로봇개 프로젝트 인수 문서 (2026-11-13 기준)

다른 계정이나 새 세션에서 이어 갈 때 **이 문서를 가장 먼저 읽는다.** 자세한 경과는 맨 아래 "자세한 기록"의 파일에 있다.

## 1. 프로젝트 한 줄 요약
ESP32 사족 로봇개(12개 서보, 다리당 3관절)를 웹 페이지 하나로 조종하고 상태를 보는 프로젝트다. 연결 경로는 세 가지다.
- **USB 게이트웨이**: 노트북에서 `usb_gateway.py` 실행 → `http://127.0.0.1:8787`
- **핫스팟 Wi-Fi**: 로봇이 휴대폰 핫스팟에 접속 → 같은 핫스팟의 기기에서 로봇 IP 또는 `http://robotdog.local`
- **로봇 자체 Wi-Fi(AP)**: `RobotDog-0FA1` / 비밀번호 `12345678` → `http://192.168.4.1` (항상 켜져 있는 예비 경로)

페이지 기능: 보행 조종(활성화 → 방향), 긴급 정지, 관절 모형(3D), 수평 상태(IMU roll/pitch), 수평 유지 켜짐/꺼짐 비교 그래프.

## 2. 지금 상태
| 항목 | 상태 |
| --- | --- |
| 펌웨어 | 수평 보정(`TRIM`) 펌웨어를 2026-11-13 **보드에 업로드 완료**, 해시 검증·부팅 확인(읽기 전용) 통과: `CAPS ... TRIM1`, `TRIM cur=9.0/1.0/-1.0/-9.0 source=DEFAULT`, IMU 영점·축 설정 유지됨 |
| 수평 보정 | 2026-11-13 추가: 펌웨어 `TRIM` 명령 + `level_calibrate.py`. 테스트·컴파일·업로드 완료. **실물 보정은 못 함: IMU가 I2C에서 응답하지 않음** (부팅 로그 `I2C none`, `IMU_ERROR not_found`; 업로드 전 게이트웨이에서도 `ready:false`) |
| 핫스팟 기능 | 코드·QA 완료, **실물 미시험** (핫스팟 설정을 아직 저장하지 않음, `sta=OFF`) |
| 3단계 보행 | 업로드됨, **바닥 실물 미시험** |
| 테스트 | `python -m unittest discover tests` → 118개 통과 |
| 컴파일 | 1,090,823 B (플래시 83%) |
| 루프 작업 목록 | `_workspace/loop/BACKLOG.md` L1~L8 모두 완료 |
| git | 브랜치 `main`, **원격 저장소 없음**. 다른 PC로 옮기려면 폴더째 복사하거나 원격을 추가한다 |

마지막 부팅 확인값: `STATE armed=0 walk=STOPPED settled=1`, 관절 90/45/90(RH ALPHA 86), IMU 0x68 정상(영점·축 설정 저장됨, rollmap=-Y pitchmap=+X), 서 있는 자세 보정 trim 9/1/-1/-9, 수평 유지 꺼짐.

## 3. 다음 할 일 (순서대로, 모두 사람이 로봇 옆에 있어야 함)
0. **수평 보정 (사용자 요청 2026-11-13: 90/45/90 기준으로 수평이 되는 서보 값을 찾아 초기값으로 저장)**
   1. (완료) 새 펌웨어 업로드, `TRIM1` 확인.
   1. **막힌 곳: IMU가 I2C 버스에 없다.** IMU 모듈의 VCC/GND/SDA/SCL 배선과 전원을 확인한다(사람이 해야 함). 직렬 `I2C` 명령에 `0x68`이 나오고 `IMU`가 `ready=1 valid=1`이면 해결된 것이다. 펌웨어는 비활성 상태에서 2초마다 다시 찾는다.
   2. 평평한 바닥에 세우고 서보 전원 ON, 손을 뗀 뒤 `python esp32-robot-dog-code-master/software/usb_gateway/level_calibrate.py --check`로 기울기 확인.
   3. 같은 명령을 `--check` 없이 실행 → 최소 조정 후 로봇에 저장. 결과(조정 전후 기울기, 새 보정값)를 이 문서 2절에 적는다.
   - 설명: `usb_gateway/README.md`의 "서 있는 자세 수평 보정". 수평 기준은 IMU 영점이다.
1. **핫스팟 실물 시험**
   1. 게이트웨이 실행 후 `http://127.0.0.1:8787` → 아래쪽 "ESP32 Wi-Fi 접속 정보" 카드의 "핫스팟 설정"에 이름·비밀번호 입력 → **저장 후 접속**. 아이폰은 "호환성 최대화" 켜기(2.4 GHz 필수).
   2. "핫스팟 상태"가 `연결됨 · <IP>`인지 확인 (15초 시도, 실패 시 30초마다 재시도).
   3. 노트북도 같은 핫스팟에 연결 → 맨 위 "연결 방법"에서 **핫스팟 Wi-Fi** → 상태가 들어오는지 확인.
   4. 로봇을 **들어 올린 채** 활성화 → 짧게 보행 → 정지.
   - 확인할 것: 크롬이 `127.0.0.1` 페이지에서 로봇 IP로 가는 요청(사전 요청 포함)을 통과시키는지, 안드로이드에서 `robotdog.local`이 되는지, 핫스팟 접속 때 AP 채널이 바뀌며 AP 기기가 얼마나 끊기는지.
2. **3단계 보행 바닥 시험**: 들어 올린 채 3초 → 바닥 5초. 재부팅(브라운아웃)되면 `WALK_PULSE_STEP_US`를 5로, 기울면 들기 9~10 또는 보폭 6으로 (`servoInitPose.ino` 상수). 보행 중 방향을 바로 바꾸면 약 0.35초 흔들릴 수 있으니 정지 후 바꾼다.
3. **미해결: 서보 전원을 넣을 때 중립으로 안 가고 이상한 자세가 됨.** 원인 후보와 필요한 정보는 `_workspace/2026-11-12_handoff.md`의 "미해결" 절.
4. 기타 실측: 수평 유지 켜짐/꺼짐 비교 실측, 앞뒤 어깨 간격(`GEO.SY`, 지금 141 mm 미실측), 관절 모형 육안 확인.

## 4. 안전 규칙 (반드시 지킨다)
- **업로드, 직렬 포트 열기, 로봇에 움직임 명령 보내기는 사용자에게 먼저 묻는다.** 업로드 전에는 사용자에게 서보 전원 OFF를 확인받는다.
- 펌웨어가 강제하는 규칙은 바꾸지 않는다: 명시적 ARM, 1초 하트비트 임대, 조종자는 한 곳만, STOP은 누구나 언제나, 정지 시 중립(90/45/90)으로 천천히 복귀, 기울기 20° 넘으면 정지.
- 루프(`robot-dog-loop`)가 도는 동안에는 `.claude/hooks/loop_guard.py`가 업로드·포트 접근·로봇 명령을 막는다.
- 사용자의 작업 트리 변경(`robot_dog_servo_control.ipynb`, `CLAUDE.md`의 사용자 줄)은 커밋하지 않는다.

## 5. 자주 쓰는 명령 (저장소 루트 `c:\Users\ghkim\Downloads\robot_dog` 기준)
```
# 테스트 (모의 펌웨어, 실제 포트를 열지 않음. Node 있으면 페이지 테스트도 실행)
python -m unittest discover tests

# 게이트웨이 (COM14 기본). 끌 때는 Ctrl+C: 로봇을 정지시키고 자세 저장 확인 뒤 포트를 닫는다
python esp32-robot-dog-code-master/software/usb_gateway/usb_gateway.py

# 웹 페이지를 고친 뒤 펌웨어에 넣을 헤더 재생성 (테스트가 일치 여부를 검사함)
python esp32-robot-dog-code-master/software/robot_control_web/embed_page.py

# 컴파일만
cd esp32-robot-dog-code-master/tools/servoInitPose
arduino-cli compile --fqbn esp32:esp32:esp32 --warnings default .
```

**업로드 (사용자 승인 + 서보 전원 OFF 후에만)**
- 게이트웨이·Jupyter 노트북이 COM14를 잡고 있으면 먼저 닫는다.
- `arduino-cli compile --upload -p COM14`가 "COM14 열 수 없음"으로 자주 실패한다. 그러면 빌드 결과의 **앱 이미지만** 0x10000에 쓴다:
  ```
  arduino-cli compile --fqbn esp32:esp32:esp32 --output-dir <빌드폴더> .
  %LOCALAPPDATA%\Arduino15\packages\esp32\tools\esptool_py\5.3.1\esptool.exe --chip esp32 --port COM14 --baud 460800 ^
    --before default-reset --after no-reset --connect-attempts 8 write-flash -z 0x10000 <빌드폴더>\servoInitPose.ino.bin
  ```
- 업로드 뒤 보드가 부트로더에 남아 응답이 없으면(서보 전원이 보드에도 들어가 USB를 다시 꽂아도 리셋되지 않는 경우) RTS를 0.2초 내렸다 올려 리셋한다. 2026-11-13에는 이 방법으로 정상 부팅했고 포트 오류도 없었다.
- **`merged.bin`(4 MB)은 쓰지 않는다.** 보드에 저장된 자세·IMU 영점·축 설정(NVS)을 덮을 수 있다.
- 업로드 뒤 리셋하면 USB가 잠깐 끊겼다 돌아온다(`reset=BROWNOUT`/`WDT`가 남음, 리셋 순간 전원 흔들림으로 추정). 포트가 돌아오면 DTR/RTS를 내린 채 열어 `HELLO`, `CAPS`, `STATE`, `STATUS`, `WIFI`, `IMU`, `LEVEL`(읽기 전용)로 확인한다.

## 6. 파일 지도
| 경로 | 내용 |
| --- | --- |
| `esp32-robot-dog-code-master/tools/servoInitPose/servoInitPose.ino` | 실제로 쓰는 펌웨어 본체: 서보, 보행, 직렬 명령, 리스·안전, `loop()` |
| `.../servoInitPose/web_control.ino` | Wi-Fi AP+핫스팟(STA), 웹 서버(별도 태스크), Host 검사, CORS, mDNS, 상태 JSON |
| `.../servoInitPose/level_control.ino`, `imu_sensor.ino` | 수평 유지, IMU(MPU 호환, 0x68/0x69) |
| `.../servoInitPose/web_page.h` | `index.html`을 넣은 생성 파일 (직접 고치지 않음) |
| `esp32-robot-dog-code-master/software/robot_control_web/index.html` | 웹 페이지 하나 (게이트웨이와 로봇이 같은 페이지를 보냄) |
| `esp32-robot-dog-code-master/software/usb_gateway/usb_gateway.py` | USB 게이트웨이 (루프백 전용, 직렬 ↔ 같은 JSON API) |
| `esp32-robot-dog-code-master/software/usb_gateway/level_calibrate.py` | 수평 보정 스크립트 (기울기 측정 → 보정값 최소 조정 → `TRIM SAVE`) |
| `esp32-robot-dog-code-master/software/usb_gateway/README.md` | **사용 설명서**: 경로별 사용법, 안전 규칙, 핫스팟, 수평 유지, 관절 모형, API |
| `tests/` | `test_level_calibrate.py`(수평 보정), `test_usb_gateway.py`(가짜 펌웨어 + 펌웨어 소스 정적 검사), `page_harness.js` + `test_control_page.py`(Node로 페이지 실행), `test_walk_notebook_safety.py` |
| `robot_dog_*.ipynb` | 사용자가 쓰는 노트북 (서보 조정, IMU 확인, 4방향 보행) |
| `.claude/` | 하네스: 에이전트(embedded, telemetry, digital-twin, integration-qa), 스킬, 루프 안전 훅 |
| `_workspace/loop/` | 루프 작업 목록·기록·시작 명령 |
| `esp32-robot-dog-code-master/software/robot_dog_esp32`, `gateway`, `web` | 원본/초기 코드. 지금 경로에서는 쓰지 않음 |

## 7. 핫스팟 모드 설계 요점 (코드를 고칠 때 알아야 할 것)
- 펌웨어는 `WIFI_AP_STA`: AP는 항상 켜고, 핫스팟 접속은 덤이다.
- 핫스팟 이름·비밀번호는 **직렬로만** 설정한다(`WIFI_SET <ssid><TAB><pass>` / `WIFI_CLEAR`). 보드 `Preferences "wifiSta"`에 저장된다. 비밀번호는 상태 JSON·직렬 출력·게이트웨이 응답·페이지 어디에도 나오지 않는다.
- 활성 상태에서는 설정 변경을 거부하고, 핫스팟 검색(접속 시도·재시도)도 하지 않는다. 활성화되는 순간 진행 중인 시도도 멈춘다.
- 게이트웨이 페이지의 핫스팟 모드는 브라우저가 로봇 IP로 **직접** 요청한다. 펌웨어는 Origin `http://127.0.0.1:8787` / `http://localhost:8787`만 CORS 허용한다. Host 검사는 `192.168.4.1`, 지금 핫스팟 IP, `robotdog.local`만 받는다.
- 페이지는 활성(최근 3초 상태) 중이거나 명령 응답 대기 중이면 연결 방법을 바꾸지 않는다. 바뀐 뒤 이전 대상에서 온 늦은 응답은 버린다(`connGen`). 모드·주소는 `localStorage`의 `robotdog.conn`에 저장된다.
- 상태 응답의 `wifi.sta = {status, ip, ssid, host}` (`OFF|CONNECTING|CONNECTED|NOT_FOUND|FAILED`).

## 8. 하네스 사용법
- 로봇개 작업 요청은 `robot-dog-orchestrator` 스킬로 조율한다 (`CLAUDE.md`).
- 독립 검토는 `robot-integration-qa` 에이전트를 쓴다. 핫스팟 작업에서 지적 5건을 받아 모두 고쳤다.
- 작업 목록을 반복 처리할 때는 `robot-dog-loop` 스킬과 `_workspace/loop/LOOP_PROMPT.md`(시작 명령 `/ralph-loop:ralph-loop`, jq 필요).
- 작업을 끝낼 때마다 이 문서의 2·3절을 갱신하고, 자세한 경과는 날짜별 메모에 남긴다.

## 9. 자세한 기록
- `_workspace/2026-11-13_hotspot-mode.md`: 핫스팟 모드 설계·구현·QA·업로드 경과
- `_workspace/2026-11-12_handoff.md`: 3단계 보행, 이전 업로드 경과, 미해결 "이상한 자세" 문제
- `_workspace/2026-11-11_twin-features/`, `_workspace/2026-11-09_web-control/`: 관절 모형·수평 비교, 웹 제어 초기 설계
- `_workspace/loop/LOG.md`: 루프 L1~L8 기록
- 커밋 기록: `git log --oneline`
