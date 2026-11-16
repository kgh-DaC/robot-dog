# 로봇개 프로젝트 인수 문서 (2026-11-16 기준)

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
| 펌웨어 | **(2026-11-14 갱신: 기존 보드는 이제 `6342223`이다. 아래 "추가 기능" 행 참고. 이 칸의 나머지는 2026-11-13 기록.)** 보드에는 원래 펌웨어 `4ced7eb`(핫스팟 지원, `TRIM` 없음)가 올라가 있었다. 2026-11-13 밤 사용자 요청으로 되돌림, 해시 검증·부팅 확인 통과(`CAPS ... STA1`, AP `RobotDog-0FA1` 192.168.4.1 동작). 저장소 HEAD의 펌웨어(`TRIM`, `I2CDIAG` 포함)는 그 전에 올려 동작을 확인했으나 지금은 보드에 없다 |
| **새 ESP32 보드 (2026-11-14)** | 사용자가 시험용으로 준비한 새 보드(ESP32-D0WD-V3, AP 이름 `RobotDog-AC25`)에 **저장소 HEAD 펌웨어를 전체(부트로더 0x1000·파티션 0x8000·boot_app0 0xe000·앱 0x10000) 업로드**했다. 앱 이미지 `verify-flash` 일치, 부팅 확인 통과: `CAPS ... STA1 TRIM1`, `STATE armed=0 walk=STOPPED settled=1 reset=POWERON`(브라운아웃 없음), AP 192.168.4.1. **로봇에 꽂지 않은 단독 상태**(서보·센서 없음)라 `I2C none`, `IMU_ERROR not_found`는 정상이다. `I2CDIAG sda=1/1/0 scl=1/1/0` → 아무것도 연결 안 됨, **GPIO22(SCL)가 0으로 잡혀 있지 않음(핀 정상)**. 저장된 값이 없어 보정값은 기본값이다(trim 9/1/-1/-9 `source=DEFAULT`, IMU 영점·축 설정·핫스팟 설정 없음). **다음: 서보 전원 OFF 상태에서 로봇에 옮겨 꽂고 `I2C`에 `0x40 0x68`이 나오는지 확인** → 나오면 기존 보드의 GPIO22 고장, 안 나오고 `scl=0/0/0`이면 선·센서 문제. 그 뒤 IMU 영점·축 설정을 새로 저장해야 한다 |
| **2026-11-14 추가 기능 (코드 완료, 미업로드)** | ① **전원 켤 때 자동 전진**(사용자 요청으로 "명시적 활성화" 규칙에 예외 추가): 기본값 꺼짐, 직렬 `AUTOWALK ON/OFF` 또는 USB 페이지 카드로 켜서 보드에 저장, 리셋 원인이 `POWERON`일 때만 5초 대기 → 10초 전진 → 스스로 정지. 제어 주체 `AUTO`, STOP은 그대로 동작, 대기 중 STOP/ARM/자세 명령은 예약 취소. 새 파일 `tools/servoInitPose/auto_walk.ino`, `CAPS ... AUTOWALK1`. ② **웹의 USB 연결 끊기/다시 연결** 버튼(게이트웨이 `DISCONNECT`/`CONNECT`: 정지 → 자세 저장 확인 → 포트 놓음, 다시 연결 전까지 재연결 안 함). ③ **`robot_web.bat`** 더블클릭으로 게이트웨이+페이지 열기(`usb_gateway.py --open`). 테스트 132개·컴파일 통과(1,096,503 B). **기존 보드(`RobotDog-0FA1`)에는 2026-11-14 저녁 이 펌웨어(커밋 `6342223`)를 앱 영역만 올렸다**(해시 검증, 부팅 확인: `CAPS ... TRIM1 AUTOWALK1`, `AUTOWALK enabled=0`, 저장된 IMU 영점·축 설정 유지, RTS 리셋 때 포트가 한 번 끊겼다 6초 뒤 복구, `reset=WDT`). 업로드 뒤 `I2CDIAG sda=1/1/1 scl=0/0/0`, `I2C none` → **기존 보드는 여전히 SCL이 0으로 잡혀 있다.** 새 보드(`RobotDog-AC25`)에는 그 전 HEAD(`0f118b0`, 자동 전진 없음)가 들어 있다. ②③은 펌웨어와 무관하게 동작. 독립 QA(`robot-integration-qa`)는 하지 않았다. 실물 시험 전 주의: 지금 로봇의 IMU가 안 잡혀 자동 전진 중 기울기 정지가 동작하지 않는다 |
| **2026-11-16 자동 전진 확인** | 사용자가 "시작 시 자동으로 걷는 코드 업로드"를 요청, 동작은 기존 "한 번만 걷기"(5초 대기 → 10초 전진)로 선택. COM14 보드는 처음에 직렬 명령에 전혀 응답하지 않았고(원인 미확인), 사용자 승인 뒤 RTS 리셋(포트가 한 번 끊겼다 약 8초 뒤 복구)하니 정상 응답: `RobotDog-0FA1`, `CAPS ... TRIM1 AUTOWALK1`, **`AUTOWALK enabled=1 phase=IDLE`(이미 켜져 있음)**, `STATE armed=0 walk=STOPPED reset=BROWNOUT`, `I2C` 빈 응답(여전히 I2C 없음). 저장소 펌웨어는 `6342223` 이후 변경이 없고 컴파일 크기도 같아(1,096,503 B) **다시 업로드하지 않았다.** **미해결: 이 보드는 전원을 넣을 때 리셋 원인이 `BROWNOUT`으로 남는 일이 잦은데, 자동 전진은 `POWERON`일 때만 시작하므로 설정이 켜져 있어도 걷지 않을 수 있다.** 실제 전원 인가 때의 `STATE reset=` 값과 `EVENT AUTOWALK_PENDING` 출력 여부를 확인해야 한다. 브라운아웃 뒤에도 걷게 하려면 펌웨어 변경(재부팅 반복 시 계속 걷지 않게 횟수 제한 필요)과 사용자 결정이 필요하다. 같은 날 사용자가 "USB와 Wi-Fi를 한 페이지에서 별도 과정 없이 전환·종료"를 요청: A안(게이트웨이 유지, Wi-Fi 연결 끊기·192.168.4.1 바로 고르기 추가)과 B안(Web Serial, Python 불필요, PC Chrome·Edge 전용, 펌웨어 CORS 변경·업로드 필요)을 제시했고 **선택 대기 중, 구현 안 함** |
| **2026-11-16 자동 전진: 브라운아웃 부팅 허용 (코드 완료, 미업로드)** | 사용자가 전원을 켰는데 걷지 않는다고 보고. 직후 읽은 값: `STATE ... reset=BROWNOUT stop=boot uptime=38053`, `AUTOWALK enabled=1 phase=IDLE` → **전원 인가 부팅이 `BROWNOUT`으로 남아 자동 전진이 건너뛰어진 것으로 확인.** `auto_walk.ino` 변경: `POWERON` 또는 (`BROWNOUT`이면서 끝나지 않은 실행 표시 `pend`가 없을 때) 시작. 걷기 직전 `pend=1` 저장(저장 실패 시 걷지 않음), 정상 정지 뒤 `pend=0`. 걷다가 전원이 무너지면 다음 브라운아웃 부팅은 걷지 않음(`AUTOWALK ... blocked=1`), `AUTOWALK ON/OFF` 재저장이나 `POWERON` 부팅으로 해제. 페이지 안내문·README 갱신, `web_page.h` 재생성, 테스트 133개·컴파일 통과(1,097,099 B). **같은 날 `RobotDog-0FA1`에 앱 영역만 업로드 완료(커밋 `601c589`, 첫 시도는 포트 오류, 두 번째에 `Hash of data verified`), 서보 전원 OFF 상태.** RTS 리셋 뒤 COM14가 한동안 사라졌다 돌아왔고, 그 뒤 읽은 값이 `reset=BROWNOUT uptime=12268`, `AUTOWALK enabled=1 phase=IDLE blocked=1` → 리셋 뒤 자동 전진이 시작됐다가(서보 전원 OFF라 움직임 없음) 실행 중 보드가 다시 브라운아웃으로 재부팅되어 차단이 걸린 것으로 보인다(차단 동작은 확인됨, 재부팅 원인은 미확인: USB 전원 불안정 의심). `AUTOWALK ON`을 다시 저장해 `blocked=0`으로 풀어 둠. **실물(서보 전원 ON) 전원 인가 시험은 아직 안 함.** 걷다가 브라운아웃이 나면 다시 차단되므로 그때는 페이지에서 자동 전진을 껐다 켠다. 독립 QA 안 함 |
| **2026-11-16 브라우저 직접 연결 (B안, 코드 완료, 실물 미시험)** | 사용자 요청: USB와 Wi-Fi를 한 페이지에서 별도 프로그램 없이 전환·종료. `index.html`을 PC 파일(`file://`)로 열면 동작한다(`robot_page.bat` 더블클릭, Edge). 연결 방법: **USB 직렬(브라우저, Web Serial)** / **Wi-Fi** / **연결 끊기**, 시작은 연결 안 함. 직렬 쪽은 `usb_gateway.py`의 동작을 페이지 안 `serial` 모듈로 옮긴 것(같은 상태·명령 형식, STOP 즉시 쓰기+확인, 하트비트, 끊을 때 STOP→`SETTLED`→닫기, 재연결 후 자동 활성화 없음, 1초 넘게 밀린 명령은 버림). Wi-Fi 쪽은 로봇이 보낸 페이지를 iframe으로 보여 준다 → **펌웨어 CORS·Host 검사 변경 없음**(처음 안내한 'Origin 허용 펌웨어 변경'은 하지 않았다). 로봇 페이지가 iframe 안이면 자기 '연결 방법' 카드를 숨긴다. `[hidden]{display:none!important}` 추가(전에는 `.row`의 `display:flex` 때문에 숨김 속성이 안 먹던 줄이 있었다). 게이트웨이 페이지·로봇 페이지 동작은 그대로. 테스트 147개 통과(가짜 Web Serial 포트+가짜 펌웨어: `page_harness.js`의 `file`/`file-noserial`/`framed`), 헤드리스 Edge에서 파일로 열어 표시와 USB 직렬 버튼 활성화 확인, `web_page.h` 재생성·컴파일 통과. **같은 날 `RobotDog-0FA1`에 앱 영역만 업로드 완료(커밋 `b368662`, 1,121,515 B, 첫 시도에 `Hash of data verified`, 서보 전원 OFF).** RTS 리셋 뒤 브라운아웃 부팅에서 자동 전진이 실제로 끝까지 돈 것을 직렬로 확인: `reset=BROWNOUT` → `armed=1 ctrl=AUTO walk=FORWARD phase=RUNNING`(uptime 9.9초) → `armed=0 walk=STOPPED settled=1 stop=autowalk_done`, `AUTOWALK enabled=1 phase=IDLE blocked=0`, 관절 목표 90/45/90(RH ALPHA 86). 즉 브라운아웃 부팅 허용과 정상 종료 뒤 차단 해제는 보드에서 확인됨(서보 전원 OFF라 실제 움직임은 없음). **미확인(실물 필요): ① 브라우저가 포트를 열고 닫을 때 보드가 리셋되는지(DTR/RTS를 내린 채 열 수 없음; 연결 직후 자동 전진 예약이 보이면 STOP으로 취소하게 해 둠) ② 실제 포트에서 연결·활성화·보행·정지 ③ `file://` 페이지에서 `http://192.168.4.1` iframe이 뜨는지 ④ 하트비트가 직렬 왕복 지연 안에서 1초 임대를 지키는지.** 첫 시험은 서보 전원 OFF로. 독립 QA(`robot-integration-qa`) 안 함. 설명: `usb_gateway/README.md` '프로그램 없이 쓰기' |
| 수평 보정 | 코드 완료(펌웨어 `TRIM` + `level_calibrate.py`, 테스트 120개·컴파일 통과). **실물 보정은 못 함: I2C 버스 전체가 죽어 있음** (0x40 INA219, 0x68 IMU 둘 다 안 보임, 원래 펌웨어에서도 `I2C none`). `I2CDIAG` 결과 `sda=1/1/1 scl=0/0/0` → SCL(GPIO22) 선이 0으로 잡혀 있음. 선을 뽑은 상태의 측정은 아직 못 함(사용자가 선을 옮기지 않았다고 함) → **핀 고장인지 선·센서 문제인지 미확정**. 보정하려면 HEAD 펌웨어를 다시 올려야 한다 |
| 핫스팟 기능 | 코드·QA 완료, **실물 미시험** (핫스팟 설정을 아직 저장하지 않음, `sta=OFF`) |
| 3단계 보행 | 업로드됨, **바닥 실물 미시험** |
| 테스트 | `python -m unittest discover tests` → 120개 통과 |
| 컴파일 | HEAD 1,091,619 B (플래시 83%). 보드의 `4ced7eb`는 1,082,259 B |
| **메인 보드 PCB (2026-11-16)** | `회로도.jpg`를 50×70 mm 2층 기판으로 설계: `hardware/pcb/` (생성 스크립트 `make_pcb.py`, 주문용 `out/robotdog_main_gerber.zip`, KiCad 8 파일, 미리보기 PNG, 설명 `README.md`). MPU9250 AD0→GND 배선 추가(0x68), 서보 전원별 1000 µF 추가. **같은 날 v2: 사용자 확인으로 ESP32를 30핀 DevKit으로 변경**(핀 줄 간격 25.4 mm, 사용자 실측 확인), 서보 헤더마다 관절 표시(`A25` 등, A=ALPHA B=BETA G=GAMMA)와 다리 묶음 경계선 추가. 전원 스위치: 사용자가 SS-12D06 직각형(레버가 옆으로)을 갖고 있으나 **기판 밖에 두기로 결정**(배터리+ → 스위치 가운데 핀 → 끝 핀 → J1+). 나중에 기판에 넣으려면 오른쪽 아래 배치안이 있다: PS3를 1 mm 위로, 스위치를 오른쪽 아래 모서리에, J1 대신 배터리선 납땜 패드, 장착 구멍 3개 유지. SS-12D06은 제조사별 정격 0.3~3 A라 2~3 A 제품만 적합. 스크립트 자체 간격·연결 검사 통과, Gerber 재해석 일치. **미확인: KiCad로 열어 보기, 모듈 실측(Mini360 패드 간격·INA219 핀 순서·30핀 보드 핀-안테나 거리), 제작·전원 시험.** 기판에서는 IMU 방향이 바뀌므로 축 설정·영점을 다시 잡아야 한다. 다시 만들 때 `pip install shapely matplotlib` 필요 |
| 루프 작업 목록 | `_workspace/loop/BACKLOG.md` L1~L8 모두 완료 |
| git | 브랜치 `main`, **원격 저장소 없음**. 다른 PC로 옮기려면 폴더째 복사하거나 원격을 추가한다 |

마지막 부팅 확인값: `STATE armed=0 walk=STOPPED settled=1`, 관절 90/45/90(RH ALPHA 86), IMU 0x68 정상(영점·축 설정 저장됨, rollmap=-Y pitchmap=+X), 서 있는 자세 보정 trim 9/1/-1/-9, 수평 유지 꺼짐.

## 3. 다음 할 일 (순서대로, 모두 사람이 로봇 옆에 있어야 함)
- **PCB (2026-11-16)**: 주문 전 실측 3가지 → ESP32 30핀 보드의 핀-안테나 거리(안테나가 위쪽 가장자리 밖 약 4 mm로 가정), Mini360 패드 간격(14.4 × 8.4 mm 가정), INA219 핀 순서(Adafruit 순서 가정). 맞으면 `hardware/pcb/out/robotdog_main_gerber.zip` 주문. 다르면 `make_pcb.py` 숫자 수정 후 재실행(`pip install shapely matplotlib`). 부품 목록은 `hardware/pcb/README.md`.
0. **수평 보정 (사용자 요청 2026-11-13: 90/45/90 기준으로 수평이 되는 서보 값을 찾아 초기값으로 저장)**
   1. (완료) 새 펌웨어 업로드, `TRIM1` 확인.
   1. **막힌 곳: SCL(GPIO22)이 0으로 잡혀 I2C 버스가 죽어 있다.** 사람이 SCL 선을 확인한다: GND와 닿았는지, 다른 핀에 꽂혔는지, 센서 모듈을 하나씩 뽑았을 때 풀리는지. 직렬 `I2CDIAG`(읽기 전용, 비활성·정지 상태에서만)가 `scl=1/1/1`이고 `I2C`에 `0x40 0x68`이 나오면 해결된 것이다. `I2CDIAG` 값 읽는 법: 세 숫자는 풀 없음/내부 풀업/내부 풀다운으로 읽은 선의 값. 1/1/1 = 외부 풀업 있음(정상), 0/1/0 또는 1/1/0 = 아무것도 연결 안 됨, 0/0/0 = 0으로 잡혀 있음.
   - 대안: IMU 없이 휴대폰 수평계로 읽은 기울기로 `TRIM SET`/`TRIM SAVE`를 직접 쓴다 (roll>0 오른쪽이 낮으면 오른쪽 다리 +, 좌우 차 1°당 roll 약 0.9°; pitch는 앞뒤 차 1°당 약 0.5°).
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
- 2026-11-14 사용자 요청으로 생긴 유일한 예외: "전원 켤 때 자동 전진" 설정을 켠 보드는 전원 인가 뒤 스스로 10초 전진한다(기본값 꺼짐, `usb_gateway/README.md` 참고). 서보 전원을 켜거나 USB를 꽂기 전에 이 설정이 켜져 있는지(`AUTOWALK`) 확인한다.
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
- esptool이 리셋 신호를 줄 때 USB(CP210x)가 끊기며 `Cannot configure port`/`COM14 열 수 없음`이 나는 일이 잦다(2026-11-13에 4번 중 3번 실패). 몇 초 뒤 포트가 돌아오면 같은 명령을 다시 실행한다. 업로드 뒤에는 BOOT 버튼을 놓아야 정상 부팅한다. 부팅 후 `reset=BROWNOUT`이 자주 남는다(전원 흔들림 의심, 미해결).
- 2026-11-13 밤: USB(CP210x)가 가만히 있어도 끊겼다 붙는 일이 있었다(전원 불안정 의심, `reset=BROWNOUT` 반복). 원인 미확인. 센서 선 4가닥을 모두 뽑고 USB 안정 여부를 보는 것이 다음 확인 순서.
- SCL 핀을 19/23/32/33으로 옮길 수 있게 하는 펌웨어 변경안(`I2CSCL <핀>`, 컴파일·테스트 통과, 미업로드·미적용)은 `_workspace/2026-11-13_i2c-scl-pin-option.patch`에 있다. GPIO22 고장이 확인될 때만 `git apply`로 쓴다.
- **새(빈) 보드 업로드 (2026-11-14에 쓴 방법)**: 앱만이 아니라 네 조각을 모두 쓴다. `arduino-cli upload`는 `Wrong boot mode (0x13)`으로 실패했다(새 보드도 자동 다운로드 모드 진입 안 됨). esptool을 `--before default-reset --after hard-reset --connect-attempts 0 write-flash -z --flash-mode keep --flash-freq keep --flash-size keep 0x1000 <빌드>\servoInitPose.ino.bootloader.bin 0x8000 <빌드>\servoInitPose.ino.partitions.bin 0xe000 %LOCALAPPDATA%\Arduino15\packages\esp32\hardware\esp32\3.3.12\tools\partitions\boot_app0.bin 0x10000 <빌드>\servoInitPose.ino.bin`로 백그라운드 실행하고, 사용자가 **BOOT를 누른 채 EN을 한 번 짧게** 누르면 연결된다. 마지막 앱 검증 단계에서 `Invalid head of packet`으로 끝났지만, BOOT를 누른 채 `--before no-reset --after no-reset verify-flash 0x10000 <앱>`으로 확인하니 일치했다. BOOT를 놓은 뒤 RTS 펄스로 리셋하면 정상 부팅한다.
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
| `robot_page.bat` | 더블클릭: 게이트웨이 없이 `index.html`을 Edge에서 파일로 연다 (브라우저 직접 USB 직렬 / Wi-Fi) |
| `esp32-robot-dog-code-master/software/usb_gateway/usb_gateway.py` | USB 게이트웨이 (루프백 전용, 직렬 ↔ 같은 JSON API) |
| `esp32-robot-dog-code-master/software/usb_gateway/level_calibrate.py` | 수평 보정 스크립트 (기울기 측정 → 보정값 최소 조정 → `TRIM SAVE`) |
| `esp32-robot-dog-code-master/software/usb_gateway/README.md` | **사용 설명서**: 경로별 사용법, 안전 규칙, 핫스팟, 수평 유지, 관절 모형, API |
| `hardware/pcb/` | 메인 보드 PCB: `make_pcb.py`(연결·배치·배선·검사·출력 전부), `out/`(Gerber zip, `.kicad_pcb`, PNG), `README.md`(부품·실측 확인·조립 순서) |
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
