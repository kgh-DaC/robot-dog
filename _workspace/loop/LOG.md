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

## 회차 5 — L5 독립 QA 검토 — 완료
- 바꾼 파일: `BACKLOG.md`(L7, L8 추가), `LOG.md`. 코드 수정 없음(읽기 위주). 아래 줄 번호는 `software/robot_control_web/index.html` 기준.
- 비교 기준: git에는 2026-11-11 변경 전 페이지가 없어서, 2026-11-09 22:46 빌드에 남은 사본 `tools/servoInitPose/build/sketch/web_page.h`(25 kB)에서 옛 페이지를 꺼내 지금 페이지와 줄 단위로 비교했다. 11-09 밤부터 11-11 작업 시작 사이에 페이지가 바뀌었다면 그 차이도 함께 섞여 있다.
- 외부 URL 없음: `http(s)://`는 261줄의 안내 문구 `http://192.168.4.1` 하나뿐(옛 페이지에도 있음, 요청이 아니라 글자). `src=`, `@import`, `url(`, WebSocket, EventSource 없음. `fetch`는 217줄 `api/command`, 287줄 `api/status` 두 상대 경로뿐. 새로 생긴 `a.href`(722줄)는 CSV 저장용 Blob 주소.
- `web_page.h` 일치: `embed_page.build_header(index.html)`와 같음(테스트 `test_firmware_header_matches_page`도 통과).
- 하트비트·정지·오래된 상태: 옛 페이지에서 지워진 줄은 옛 관절 표(머리글, 안내 문구, 표 채우는 코드)와 상태 요청의 한 줄뿐이다. 그 한 줄은 289줄로, 끝에 `recordCompare(s, lastOk)` 호출만 더해졌다. 새 코드는 419~734줄에 한 덩어리로 들어갔고, 하트비트(735~747줄, 0.75초 조건 742줄), 정지·활성화 버튼(749~757줄), 키보드 정지(759~761줄), 탭 숨김·닫기 정지(762~767줄), `post`/`command`/`stop`(215~239줄), `validState`/`isFresh`(242~244줄), 버튼 비활성화(252~258줄), "알 수 없음" 표시(266~270줄)는 옛 페이지와 글자까지 같다.
- 수평 비교: 켜짐은 `level.active` 기준(589줄), 조건은 활성 상태의 `walk`(591줄), 축 미설정·센서 무효·비활성은 기록하지 않음(592줄), 표본 20개 미만이면 숫자 대신 안내(640줄). 비교 코드(582~732줄)에 `post`/`command` 호출 없음(테스트 `cmpSentCommands == 0`).
- 관절 모형: 보정 출처는 `level.gammaOffsetDeg`(454줄)와 `standTrimDeg`(441줄)로 생산자 필드와 이름이 같다. 앞뒤 어깨 간격은 "미실측"으로 표시(182줄).
- 발견한 문제(둘 다 낮음, 안전 경로와 무관):
  1. `render()`가 `renderLevel`·`renderCompare`·`renderModel`(263~265줄)을 부른 뒤에 "알 수 없음"을 쓴다(266~270줄). 그리기 코드가 예외를 던지면 연결이 끊겨도 상태 글자가 옛 값으로 남는다. 버튼 비활성화(253~258줄)는 그 앞이라 영향 없음. 구조는 옛 페이지와 같지만 앞에서 도는 코드가 300줄 늘었다 → L7.
  2. `modelAngles`가 `gammaOffsetDeg`가 없으면 0으로 더한다(454~455줄). 명세는 "값이 없으면 0으로 가정하지 말고 알 수 없음"이다. 서 있는 자세 보정은 알 수 없음을 표시하지만 수평 보정은 표시하지 않는다 → L8.
- 검증: `python -m unittest discover tests` 78개 통과(이 회차 기록 전에 실행한 회차 4의 결과와 같은 코드. 아래 커밋 전에 다시 실행).
- 참고: 이 기록을 셸 명령으로 쓰려다 안전 훅에 한 번 막혔다. 기록 문장 안의 명령 경로 글자가 걸린 것이고 로봇 명령은 보내지 않았다. 기록은 파일 편집으로 썼다.
- 남은 것 / 다음에 할 것: 펌웨어 쪽(직렬화, 리스, 기기 측 정지)은 이번 범위가 아니라 읽지 않았다. 실물 확인 없음. 다음 항목은 L6.

## 회차 6 — L6 사용 설명 갱신 — 완료
- 바꾼 파일: `software/usb_gateway/README.md`. "수평 유지 켜짐/꺼짐 비교"와 "관절 모형" 두 절을 "기울기 센서와 수평 유지" 절 뒤에 추가(사용법, 기록 규칙, 표현 한계: 명령 각도 기준, 4 Hz, 바닥 접촉 미표현). API 절의 상태 응답 목록에 `standTrimDeg` 한 줄 추가(L2에서 생긴 필드).
- 검증: `git diff`에서 지워진 줄은 API 목록의 한 줄(끝에 쉼표를 붙여 다시 쓴 것)뿐이고, 안전 규칙 절 두 곳은 그대로다. 설명의 숫자(20표본, 1초 제외, 60초, 치수)는 `index.html`의 상수와 대조했다. `python -m unittest discover tests` 78개 통과(코드 변경 없음).
- 남은 것 / 다음에 할 것: README의 "테스트" 절은 `python -m pytest -q tests`로 적혀 있고 루프는 `unittest`로 실행한다(둘 다 같은 테스트, 고치지 않음). 다음 항목은 L7.
