# 모의 로봇 게이트웨이

이 Node.js 서비스는 로컬 모의 장치만 제어하는 게이트웨이 경계 실험용이다. ESP32에 연결하지 않고, 인증 토큰도 테스트 목적으로 프로세스에 주입한다. TLS, 실제 사용자 로그인/OIDC, 영속 저장소, 기기 신뢰·인증, 네트워크 운영 보안은 제공하지 않으므로 배포하거나 공용 네트워크에 노출하지 않는다.

## 실행과 테스트

- 테스트: `npm test`
- 로컬 실행: PowerShell에서 테스트용 임의 토큰을 환경 변수로 설정한 뒤 `npm start`를 실행한다. 서버는 `127.0.0.1:8787`에만 바인딩한다.
- `ROBOT_GATEWAY_OPERATOR_TOKEN`은 제어자, `ROBOT_GATEWAY_VIEWER_TOKEN`은 조회 전용 사용자 토큰이다. 실제 비밀이나 로봇 자격 증명을 사용하지 않는다.

## 현재 모의 API

- `GET /health`: 인증 없는 프로세스 상태이며 모의 모드만 표시한다.
- `GET /v1/telemetry`: viewer/operator Bearer 토큰 필요. 목업 디바이스 텔레메트리만 반환한다.
- `POST /v1/commands`: operator Bearer 토큰만 허용한다. JSON 봉투는 UUID `requestId`, 사용자 단위 단조 증가 `sequence`, `issuedAt`/`expiresAt` Unix 밀리초, `command`를 포함한다.
- 봉투와 명령 객체는 정의된 키만 허용하고 `Content-Type: application/json`을 요구한다. 요청 본문은 4 KiB 이하만 허용한다.
- `ARM`: 최대 3초 유효한 요청이고 MockDevice 연결 및 최신 IMU interlock을 통과해야 한다.
- `MOVE`: 최대 3초 유효하며 여섯 uint16 정규화 값은 각각 `0..20000`이어야 하고, 모의 기기가 이미 ARM 상태여야 한다.
- `STOP`: 인증된 operator의 명령 형식이 유효하면 만료·순번과 관계없이 즉시 모의 장치를 정지시킨다. viewer나 미인증 요청은 허용하지 않는다.

명령은 메모리 안에서만 처리되며 재시작하면 모의 장치는 비활성화 상태로 초기화된다. 시퀀스는 사용자별 메모리 상태이고 다중 인스턴스 간 재생 방지 수단이 아니다. 실제 하드웨어 브리지는 포함하지 않는다. 다음 설계 단계에서 실제 IdP, TLS 종료, 기기 인증 및 장치 측 재검증 계약을 정한 뒤 별도 보안 검토가 필요하다.
