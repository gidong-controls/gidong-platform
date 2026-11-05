# ControlStudio 사용자 GUI 및 실행 아키텍처

현행 개발 경계 갱신: 2026-10-11

결정 상태: chat-first 제품 방향 확정. 현재 저장소의 target-first/mission-first
화면은 이 방향으로 수렴시키는 구현 현황이지, 모든 사용자 요청의 필수 절차가 아니다.

이 문서는 [루트 PRD](../PRD.md)의 사용자 제품을 어떤 형태로 제공할지 고정한다.
기능 범위와 안전 정책은 PRD가 우선하며, 현재 Agent·Platform·MCP 실행 계약은
[PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md)와
[책임·데이터 소유권](refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md)을 따른다.
신규 구축과 기존 설비 부분 수정의 실제 화면·검토 순서는
[ENGINEERING_COLLABORATION_WORKFLOWS.md](ENGINEERING_COLLABORATION_WORKFLOWS.md)를
따른다.

현재 구현·개발 우선순위는 [구현 계획의 단계별 완료 조건](AGENT_PRODUCT_PLAN.md)을
따른다. 아래 화면 기능 목록에는 기존 거래 UI도 포함되며 정상 native 대화의 필수
단계가 아니다. 실제 사용자 작업과 저장본 결과를 같은 대화에서 표시하는 것이 기준이다.

## 1. 결정 요약

ControlStudio의 사용자 GUI는 **React + TypeScript 웹 애플리케이션**으로 만든다.

- Windows에서는 일반 브라우저 또는 설치 가능한 PWA/앱 창으로 사용한다.
- 같은 GUI를 고정 로컬 주소로 직접 열 수 있다.
- 선택적으로 인증된 원격 브라우저와 모바일에서도 같은 작업을 이어 본다.
- 첫 제품에 Tauri, Electron, Flutter, Qt 또는 Rust 네이티브 GUI를 넣지 않는다.
- Windows에 종속되는 것은 GUI가 아니라 XG5000을 제어하는 사용자 세션 Host다.
- 현재 Platform 제품 실행 경로에는 Tkinter 창이 없다. 과거 `xg5000_control.gui`
  콘솔은 Platform source에 없으며 제품 UI 계약에 포함하지 않는다.

```text
ControlStudio Windows 아이콘
  → Windows Launcher
    → ControlStudio Host
      ├─ React 정적 파일과 HTTP API
      ├─ 실시간 이벤트 스트림
      ├─ 서버 권위 대화·작업 저장소
      ├─ Agent process supervisor → private `gidong-agent` (Pydantic AI)
      └─ MCP process supervisor → private `gidong-plc-mcp` (안전 코어)
        → 이관된 LS 명령은 `controlstudio-xg5000-cli` subprocess/JSON 경계
        → LS XG5000 adapter → 벤더 IDE·시뮬레이터·PLC
```

Mitsubishi와 Rockwell은 공통 GUI schema에 남아 있는 후속 adapter 범위이며 현재
실행 adapter가 아니다. CLI로 이관되지 않은 legacy-native route의 실행·검증 책임은
계속 MCP에 있다.

React UI는 요청과 서버 상태를 표시하는 클라이언트다. 프로젝트 변경, PLC 제어,
성공 판정, 권한 승격 또는 복구 판정을 직접 수행하지 않는다.

## 2. 제품 GUI의 역할

첫 LS 제품에서 래더와 프로젝트 화면의 시각적 원본은 XG5000이다.
ControlStudio는 XG5000을 복제한 또 하나의 PLC IDE를 만들지 않는다.

ControlStudio가 제공할 핵심 화면은 다음이다.

- 새 프로젝트와 기존 로컬 프로젝트의 두 핵심 시작 선택
- 지원되는 온라인 읽기 전용 관찰과 복구 작업의 명확히 구분된 보조 진입
- 에이전트 대화와 요청 이력
- 현재 workspace, 벤더, CPU, 프로젝트, XG5000 세션과 PLC 연결 상태
- 분석 진행 단계와 사용한 증거
- 확인된 사실, 추론, 가설, 불확실성과 추가 측정 항목
- 관련 program·network/rung·visual row·token 위치와 `XG5000에서 열기`
- 직접·간접 영향, full causal device chain, 상태와 시간축의 구조화된 결과
- 여러 해결안의 효과, 위험, 변경 범위와 추천 근거
- 대상·현재값·제안·영향·검증·복구를 갖춘 변경 계약
- 승인·거절·중단, 실행, readback, 검사, 복구와 감사 증거
- 지원하지 않는 기능과 비활성 이유

프로젝트 범위·검색 결과·관련 위치를 요약하거나 탐색할 수는 있지만, 첫 제품에서
벤더 IDE의 전체 프로젝트 트리, 래더 편집기, 모든 메뉴와 명령을 다시 구현하지
않는다. 향후 독립적인 읽기 전용 viewer가 실제 사용자 가치로 입증되면 별도
capability로 추가한다.

향후 Mitsubishi와 Rockwell을 추가해도 같은 원칙을 유지한다. GUI는 벤더별 좌표,
메뉴 또는 네이티브 payload를 알지 않고 공통 location과 evidence만 사용한다.
실제 위치 열기와 편집은 각 벤더 adapter가 담당한다.

### 2.1 화면 전환 원칙

기본 화면은 한 개의 간단한 대화 화면이다. 사용자는 target, 계획, 검증, 복구를
먼저 통과하지 않아도 질문을 보내고 모델 결과와 실시간 도구 타임라인을 볼 수
있어야 한다. 대상 선택·신규 프로젝트 brief·계획 검토·변경 계약은 대화의 문맥상
필요할 때 여는 보조 panel 또는 명시적 작업이며, 일반 읽기·분석 요청의 선행
절차가 아니다.

```text
CHAT (기본)
  ├─ 일반 질문/읽기·분석 → 모델 결과 + tool timeline
  ├─ 대상이 필요한 질문 → 대화 안에서 target 선택을 제안
  ├─ 후보/계획만 요청 → 읽기·설계안 설명 (적용 없음)
  ├─ 새 프로젝트 작성 → 필요한 요구/대상 준비 → 모델의 LD 작성·저장
  └─ 지정 작업본 수정 → 요청 범위로 native 실행 → 실제 결과·필요한 후속 작업
```

- target이 없거나 확인되지 않았다는 이유만으로 대화 surface를 숨기지 않는다.
  대신 가능한 범위와 target이 필요한 다음 행동을 명확히 말한다.
- 설정 panel을 열어도 대화와 현재 run/timeline은 유지한다. panel은 대화를
  대체하는 전체 화면 단계가 아니다.
- 대상·workspace 결속은 그 대상에서 실행하는 tool 또는 mutation에만 적용한다.
  순수 대화, 저장된 evidence 설명, target 불필요 분석까지 일괄 차단하지 않는다.
- 대화, 근거, 실행 상태와 변경 계약은 대상 또는 신규 설계 세션에 귀속한다.
  대상이 바뀌면 이전 맥락을 새 대상의 현재 맥락처럼 보여 주지 않는다.
- 완료·거절·만료된 변경 계약, recovery, Keep/Revert는 주 대화 화면을 점유하지
  않고 해당 change의 필요 시점 또는 작업 이력으로 축소한다.
- 구현되지 않은 PLC 전체 업로드·복원은 첫 화면의 비활성 카드로 노출하지 않는다.
  실제 capability가 생긴 뒤 진입 경로를 추가한다.
- 온라인 읽기 전용 관찰은 capability가 있을 때만 접힌 `다른 연결 방법`에
  표시한다.

### 2.2 신규 프로젝트 사용자 여정

신규 프로젝트도 일반 대화에서 시작한다. 짧은 guided brief는 모델이 구조화된
계획을 만들기 위해 필요한 정보를 수집하는 선택적 보조물이며, CPU·경로·계획 검토
화면을 채팅보다 먼저 강제하지 않는다.

후보만 요청한 경우 적용하지 않는다. 명확한 작업본 생성·작성·저장 요청은 해당 범위의
권한으로 Host가 보존한다. 추가 범위나 물리 PLC 작업이 필요하면 그 항목을 확인한다.
계획 검토 panel을 모든 신규 생성의 필수 gate로 붙이지 않는다. 실제 생성·저장·시험의
지원 상태와 결과를 표시하며 계획만 작성한 상태를 완성 프로젝트로 부르지 않는다.

## 3. 실행 수명주기

### 3.1 기본값

다음 동작은 기본적으로 금지한다.

- Windows 부팅 또는 로그인만으로 자동 실행
- 사용자 동의 없는 백그라운드 상주
- 터미널 창 노출
- 사용자가 MCP 또는 에이전트 서버를 별도로 실행

사용자 경험은 다음 한 경로다.

```text
사용자가 ControlStudio 아이콘 클릭
  → Launcher가 기존 Host 확인
    ├─ 실행 중 + 검증된 소유 창 1개: 새 handoff 없이 기존 창 복원·전면화
    ├─ 실행 중 + 소유 창 없음: 새 handoff로 앱 창 1개 생성
    └─ 실행 안 됨:
       → Host 시작
       → DB와 모델 설정 읽기
       → MCP 자식 프로세스 시작·initialize
       → readiness 확인
       → ControlStudio GUI 열기
```

Pydantic AI/model/prompt/RAG는 Host가 고정된 명령과 환경으로 시작·감시하는 별도
`gidong-agent` sidecar에 있다. Host는 Agent Protocol로만 통신하며 Agent는 raw MCP,
PID/HWND, endpoint나 approval secret을 소유하지 않는다. MCP 안전 코어 역시 Host가
감시하는 별도 `gidong-plc-mcp` 프로세스다. 이관된 LS 명령은 MCP가 separate
`controlstudio-xg5000-cli` subprocess로 위임할 수 있지만, 이 경계가 MCP의
target/precondition/journal 책임을 이전하지는 않는다.

Windows Launcher는 자신이 새로 시작한 Host 프로세스 트리를 Job Object에
결합한다. readiness·Host challenge·GUI 열기 중 실패하면 해당 Job만 종료해
Host와 MCP 자식을 함께 정리하고, 성공하면 kill-on-close 없이 Launcher 전용
handle을 해제해 Host가 정상적으로 계속 실행되게 한다.

Host는 Windows Service가 아니다. XG5000과 같은 로그인 사용자, interactive
session, desktop과 호환되는 integrity level에서 실행한다. 사용자 전환, RDP
다중 세션, elevation 또는 desktop이 모호하면 UI 자동화를 시작하지 않고
fail-closed한다.

순수 PWA 아이콘은 임의의 Windows 실행 파일을 시작할 수 없다. 따라서 설치
프로그램이 만드는 기본 `ControlStudio` 바로가기는 **Windows Launcher**를
가리킨다. 현재 Launcher는 Host를 준비한 뒤 동일한 React URL을 지원 Chromium의
`--app` 창으로 연다. 별도로 설치한 PWA나 주소 직접 접속은 같은 Host를 보는
선택적 클라이언트이며 기본 Launcher 실행 경로를 대신하지 않는다.

Launcher는 `--new-window`를 강제하지 않는다. 검증된 Host와 Launcher 소유
ControlStudio 앱 창이 정확히 하나 있으면 새 handoff를 발급하지 않고 기존 창을
복원·전면화한다. 소유 창이 없으면 새 single-use handoff URL을 가진 앱 창
**하나만** 연다. 다중 소유 창은 손상된 과거 상태로 보고 검증된 창들만 닫은 뒤
새 창 하나로 복구한다. 새 창의 정확한 소유권과 foreground 전환을 확인할
때까지 Launcher mutex를 유지하므로 빠르게 여러 번 눌러도 창이 겹치지 않는다.
소유 표식이 없는 동명 Chromium 창, 닫기 실패, 둘 이상의 새 후보 창 또는
foreground 실패는 다른 창을 추측해 닫거나 추가 창을 여는 대신 fail-closed한다.

### 3.2 종료

- 명시적인 `ControlStudio 종료`는 진행 중인 mutation이 없는지 확인하고
  MCP와 Host를 정상 종료한다.
- 브라우저 탭이나 PWA 창을 닫는 것만으로 실행 중 작업을 취소하거나 mutation을
  재시도하지 않는다.
- 원격 접근이 꺼져 있고 활성 작업도 없으면 Launcher가 Host 종료를 제안하거나
  정책에 따라 종료할 수 있다.
- 원격 접근 또는 장기 작업 때문에 Host가 남아 있으면 트레이 또는 Launcher
  상태에서 실행 중임을 명확히 표시한다.
- 변경 결과가 불명확한 상태에서는 종료하더라도 다음 시작 시 `QUARANTINED`를
  복원하고 실제 상태 확인 전 mutation을 차단한다.

## 4. 웹·PWA와 주소 정책

초기 기본 주소는 다음으로 고정한다.

```text
http://127.0.0.1:8765
```

- 기본 bind는 loopback뿐이다.
- `127.0.0.1`을 canonical local origin으로 사용한다. `localhost` 이름 해석에
  의존하지 않는다.
- 포트 충돌 시 다른 포트로 몰래 이동하지 않고 명확한 오류와 설정 변경 절차를
  제공한다.
- Launcher는 기존 포트가 열렸다는 이유만으로 브라우저를 열지 않는다. 자신이
  시작하거나 검증한 정확한 Host process/build와 일회성 bootstrap challenge를
  확인한 뒤 GUI를 연다. 낯선 프로세스가 포트를 선점했으면 중단한다.
- 포트가 바뀌면 PWA origin도 달라짐을 안내한다.
- React 정적 파일과 API는 같은 Host가 same-origin으로 제공한다.
- 첫 milestone에는 service worker를 사용하지 않는다. 이후 도입해도 버전이
  붙은 정적 asset만 캐시한다.
- API, 이벤트, 프로젝트 상태, 승인과 실행 결과는 절대 캐시하지 않는다.

PWA는 사용자 경험상 독립 Windows 앱 창을 제공하지만 실행 권위는 아니다.
브라우저 또는 PWA가 새로고침·종료돼도 서버 소유 작업을 중복 시작하지 않는다.

## 5. 원격 접근과 여러 클라이언트

원격 접근은 기본적으로 꺼져 있다.

```text
기본
  local only: ON
  remote access: OFF

선택적 원격
  private HTTPS tunnel 또는 인증된 LAN HTTPS
  기기·사용자 인증
  명시적인 capability
```

초기 개인·개발 환경은 Tailscale 같은 사설 HTTPS 경로를 사용할 수 있지만,
특정 네트워크 제품을 ControlStudio의 필수 의존성으로 만들지 않는다.
`0.0.0.0` 무인증 HTTP 공개는 허용하지 않는다.

원격 접속 여부와 작업 권한을 분리한다.

| 원격 capability | 기본 |
|---|---|
| 대화와 이력 조회 | 선택적으로 허용 가능 |
| 읽기 전용 분석 요청 | 선택적으로 허용 가능 |
| 실행 상태·증거 조회 | 선택적으로 허용 가능 |
| 변경안 승인 | 기본 차단, 별도 활성화 |
| mutation 실행 요청 | 기본 차단, 별도 활성화 |
| 직접 제어 | 벤더·CPU별 시험과 현장 정책 승인 전 영구 차단 |

원격 인증에 성공했다는 이유만으로 `online_modify`나 `direct_control`을 열지
않는다. 벤더·CPU·firm웨어별 테스트벤치 증거, 현장 안전 조건, 별도 capability,
정확한 작업 승인과 MCP precondition을 모두 통과한 기능만 독립적으로 활성화할
수 있다.

데스크톱과 모바일은 독립된 에이전트나 MCP를 실행하지 않는다. 모두 하나의 Host
상태를 보는 클라이언트다.

- 대화·run·승인 대기·증거는 서버가 소유한다.
- 테마·글자 크기·패널 접힘 정도만 클라이언트 설정으로 둘 수 있다.
- XG5000 PID·workspace·controller별 mutation lock은 서버와 MCP가 소유한다.
- `operation_id`와 `transaction_id`로 중복 요청을 차단한다.
- 승인한 사용자와 기기, 시각, 대상 plan digest를 감사 기록에 남긴다.
- 머신과 interactive engineering session 전체에서 mutation write lease는
  하나만 허용한다. 두 번째 Launcher, 다른 Windows/RDP session 또는 다른
  브라우저는 기존 lease를 임의로 탈취할 수 없다.
- mutation뿐 아니라 활성 창·프로그램 선택·클립보드가 바뀌는 모든 XG5000 UI
  읽기와 쓰기는 engineering session별 단일 작업 큐에서 직렬 실행한다. 순수
  native artifact 분석만 UI queue 밖에서 병렬화할 수 있다.

모바일은 별도 앱을 만들지 않는다. 반응형 웹에서 대화, 진행 상태, 결과와
허용된 승인만 제공하며 XG5000 자체 화면 조작을 복제하지 않는다.

## 6. 사용자 화면의 필수 상태

채팅이 기본이고, 현재 대상·권한·연결 상태는 작고 항상 보이는 context strip 또는
접을 수 있는 상세로 제공한다. 일반 사용자에게 PID/HWND·raw JSON·내부 journal을
먼저 요구하지 않는다.

```text
Host                  READY / DEGRADED / STOPPING
Agent                 READY / MODEL_UNAVAILABLE
MCP                    READY / RESTARTING / QUARANTINED
Engineering system    XG5000 PID와 project correlation
Workspace             source, revision, hash
Controller            DISCONNECTED / OBSERVING / VERIFIED / MISMATCH
Capability            READ / ANALYZE / ...
Active operation      operation_id, 단계, 시작 사용자
```

사용자에게 PID/HWND나 raw JSON을 기본 화면에 노출하지 않는다. 대신 프로젝트명,
벤더, CPU, 연결과 일치 여부, 읽기/변경 권한을 사람이 이해할 수 있게 표시한다.
기술 식별자는 개발자 상세 화면과 감사 증거에 남긴다.

다음 정보는 **실제 변경 승인**에만 최소한 보여준다. 질문, 읽기, 분석, 일반
모델 결과에 이 승인 card나 verification/recovery/Keep 절차를 붙이지 않는다.

변경 승인은 최소한 다음을 보여준다.

- 변경 ID와 대상 workspace/controller
- 벤더 IDE의 프로그램·네트워크·명령 위치
- 현재 구조 또는 값과 제안 구조 또는 값
- 의도와 기대 효과
- 직접·간접 영향과 위험 출력
- 불확실한 전제와 현장 확인 항목
- 검사·시뮬레이션·온라인 관찰 계획
- 실패·중단 조건과 롤백 가능성
- plan digest, 승인 만료와 필요한 capability

일반적인 채팅의 `확인` 버튼을 위험 변경 승인으로 재사용하지 않는다.

## 7. Host API와 실시간 이벤트

첫 구현은 다음 경계를 사용한다.

- HTTP JSON: 조회, 새 사용자 메시지, 취소, 승인·거절 요청
- SSE: 모델 토큰, agent 단계, MCP tool 상태와 run 상태의 순서화된 이벤트
- WebSocket: 고빈도 양방향 trace가 실제로 필요할 때만 추가

모든 이벤트는 최소한 다음 envelope를 가진다.

```text
event_id
sequence
occurred_at
conversation_id
run_id
operation_id
event_type
payload_schema_version
payload
```

클라이언트 재연결은 마지막 `sequence` 이후의 서버 저장 이벤트를 재생한다.
브라우저가 보낸 assistant 메시지, tool result, 승인 상태 또는 과거 run 상태를
권위 있는 값으로 믿지 않는다.

hidden tab의 timer, 브라우저 생존 여부 또는 클라이언트 watchdog을 작업 진행,
승인 만료, transaction timeout과 rollback의 권위로 사용하지 않는다. 모든
시간과 sequence 판단은 Host와 MCP가 소유한다.

GUI가 직접 호출할 수 없는 항목:

- 임의 shell 또는 executable
- 임의 파일 경로 읽기·쓰기
- 벤더 UI 좌표·메뉴·클립보드
- MCP capability grant
- 프로젝트 mutation
- PLC 쓰기·force·모드 변경
- transaction 성공·복구 판정

## 8. 웹 보안 기본값

loopback 주소도 무신뢰 웹페이지의 요청 대상이 될 수 있으므로 다음을 강제한다.

- `Host`와 `Origin` allowlist
- CORS 기본 차단
- 상태 변경 요청의 CSRF 방어
- launcher별 local secret과 공개 health 뒤의 HMAC nonce challenge
- 검증된 Host의 인증된 loopback API에서 30초 one-time handoff ticket 발급
- URL fragment에는 handoff ticket만 전달하고 browser가 즉시 제거
- handoff ticket을 origin별 단기 browser session token으로 원자적으로 한 번 교환
- launcher secret은 browser URL·process argv에 전달하지 않음
- 파생 session만 `sessionStorage`에 두고 모든 API와 fetch 기반 SSE에
  `Authorization` header로 전달; loopback-wide cookie, query string,
  `localStorage`에는 secret을 두지 않음
- CSP와 정적 asset 무결성
- WebSocket/SSE를 포함한 모든 원격 요청 인증
- API 응답과 민감 상태 `no-store`
- 승인 요청의 사용자·세션·plan digest·만료 검증
- 원격 기기 등록, 조회와 해제

API가 인증됐다는 사실은 MCP capability를 대신하지 않는다. Host와 MCP가 각각
사용자 권한, 대상 identity와 작업 precondition을 다시 검사한다.

## 10. 구현 완료 기준

### 실행과 설치

- 아이콘 한 번으로 Launcher → Host → MCP → GUI가 준비됨
- 부팅·로그인만으로는 실행되지 않음
- 터미널이나 수동 명령이 필요 없음
- 이미 실행 중이면 중복 Host/MCP를 만들지 않고 기존 세션을 엶
- 검증된 Launcher 소유 창 하나는 그대로 복원·전면화하고 새 handoff·창을
  만들지 않음
- 다중 소유 창 또는 새 Host 시작은 검증된 기존 창만 정리하고 새 handoff 앱
  창 하나로 복구하며 두 번째 앱 창을 남기지 않음
- 준비 실패 원인과 복구 방법을 사용자에게 표시
- 포트 선점 시 낯선 Host를 열지 않고 identity challenge 실패로 중단
- 다른 Windows 사용자·RDP session·elevation의 XG5000을 자동 제어하지 않음

### 사용자 작업

- 최초 진입과 재접속의 기본 surface는 대화이며, 사용자는 즉시 자연어 요청을
  보낼 수 있음
- 각 run에서 선택된 도구가 `selected → running → completed/failed`로 바뀌는
  timeline과 모델 결과가 같은 대화 문맥에 실시간 표시됨
- 일반 질문·읽기·분석은 plan review, verification, recovery, Keep/Revert 또는
  target setup을 선행 조건으로 만들지 않음. 특정 tool에 target/evidence가
  필요하면 그 tool만 정확한 이유와 선택 동작을 제시함
- 새 프로젝트 brief/plan, 기존 프로젝트 선택, 온라인 관찰은 대화에서 시작하는
  보조 흐름임. 후보만 요청하면 적용하지 않고, 명확한 작업본 작성·수정·저장은
  Host가 보존한 사용자 요청 범위로 실행함
- 새 권한 확인은 범위를 벗어나는 대상/작업에 한정함. 오프라인 수정 요청으로 물리 PLC
  운전 권한이 생기지는 않으며, 같은 요청 범위 안의 도구마다 재승인을 요구하지 않음
- 자연어 요청에서 실제 MCP 읽기 도구와 결과까지 연결
- 관련 위치를 벤더 IDE에서 열 수 있음
- 증거·가설·대안·위험·검증은 결과에 관련된 경우 표시하고, 모든 답변의 필수
  checklist로 만들지 않음
- 추가 범위의 실제 변경이 필요할 때만 해당 대상·내용을 확인
- 새로고침·재연결·복수 클라이언트에서 동일 run 상태 유지

### 안전과 장애

- 브라우저가 MCP나 벤더 UI를 직접 제어할 수 없음
- 승인 위조, 중복 요청과 stale plan을 서버와 MCP가 거부
- 읽기 단계 장애는 안전하게 복구
- native 입력 결과불명은 unknown과 실제 이력을 보존하고 현재 관측에서 이어감.
  기존 transaction의 `QUARANTINED` 복구 계약과 구분하며 완료 입력을 자동 재발행하지 않음
- 원격 접근은 기본 차단이며 HTTPS·인증·capability 없이는 열리지 않음
- 실제 PLC 쓰기와 런중 수정은 별도 제품 합격 기준 전까지 비활성
- stale UI build/API schema 불일치에서는 모든 write가 fail-closed
- 두 번째 탭·Launcher·RDP session이 기존 write lease를 획득하지 못함
- 병렬 read 요청도 XG5000 UI queue에서 잘못된 프로그램으로 교차되지 않음

### Windows 사용자 경험

- 한국어 IME와 복사·붙여넣기
- 키보드 전용 탐색과 screen reader 이름
- Windows 100%~300% DPI
- 다중 모니터와 지원하는 RDP 사용 조건
- 지원 Edge/Chrome 버전과 PWA/일반 브라우저 양쪽

을 실제 Windows 테스트에서 검증한다.

## 11. 현재 저장소 상태

현재 제품 slice에는 다음이 구현됐다.

- React·TypeScript 제품 GUI와 PWA manifest; service worker는 의도적으로 없음
- **현재 구현과 목표의 차이:** 저장소는 아직 대상 선택 전 시작 화면과 target 확보
  후 agent workspace를 분리하고, mission/review/recovery panel을 대화보다 앞세울
  수 있다. 이것은 위 chat-first 기본 화면의 완료 상태가 아니라 교체 대상이다.
- Host API/SSE는 이미 chat-first에 필요한 서버 소유 conversation/run/event와
  sequence 재생을 제공한다. MCP tool event는 tool 이름·call ID·상태만 사용자
  activity로 보이며, web은 시작/완료/실패를 running/completed/failed로 갱신한다.
- 보통 run은 취소 API가 있으나, 승인 후 실제 mutation 실행·검증 상태는 실제
  engineering state reconciliation 전 취소하지 않는다. Keep/Revert와 recovery는
  XG-SIM observing 또는 `QUARANTINED` change에 한정된 예외 흐름이다.
- 사용자 호출형 Windows Launcher와 브라우저 app-mode 실행
- HMAC Host challenge, origin별 browser session과 인증된 API/event stream
- ControlStudio Host/FastAPI, 서버 권위 conversation/run/event 저장소
- Pydantic AI 읽기·분석 runtime과 MCP process supervisor
- 기존 작업본의 `engineering/permanent_offline_revision/offline_native_check_only`
  native 실행, source-bound 참조 도구·순차 batch·저장·checkpoint 왕복
- target 없이 시작하는 신규 프로젝트 요구사항 브리프와 `new_project_planning`
  요청 UI. 현재 targetless planning의 문자열 응답과 Host의 plan-artifact 검증은
  불일치하며 정상 종료 계약을 교체할 대상
- 로컬 XG5000 경로 입력, 원본 보존 작업 복사본 생성·열기와 자동 세션 연결
- 인증·CSRF 보호 Host API가 여는 Windows 네이티브 `.xgw`/`.xgwx` 파일
  선택 창. 브라우저는 임의 로컬 경로를 직접 읽지 않음
- 저장된 로컬 workspace를 시작 화면에서 다시 열고, 원본/working-copy hash
  drift가 있으면 XG5000 실행 전에 중단하는 재개 흐름
- workspace 재개 성공 시 이전 설비 대화를 재사용하지 않고 새 target 전용
  conversation을 시작하며, 일반 재접속은 Host의 최근 conversation을 복원
- XG5000 인스턴스 선택, 대화·활동·모델 설정, run 취소와 명시적 종료
- 실제 XG5000 프로그램 목록을 보여 주는 좁은 읽기 전용 탐색 패널과
  `XG5000에서 열기` Host/Web 호출
- `Engineering Analysis v4`의 evidence-bound exact program·network/rung·visual
  row·token 위치, relation/full causal chain, explicit coverage·truncation·
  limitations를 `근거 위치`, `신호 관계`, `조사 범위`로 나누어 표시하는 분석 패널.
  위치의 프로그램명이 현재 프로그램 목록에서 exact하게 하나만 일치할 때만
  해당 위치에 프로그램 전면화용 `XG5000에서 열기`를 표시. exact network/rung/token
  이동은 아직 지원하지 않음
- MCP tool 진행을 이름·tool-call ID·상태만 남긴 서버 이벤트로 표시하고
  도구 인자·결과·원시 오류는 사용자 화면에 노출하지 않는 활동 흐름
- 동일 길이 LD operand 한 개에 한정된 구조화 변경 계약, 승인·거절·실행과
  `QUARANTINED` transaction의 명시적 PID/HWND 선택 복구 화면
- LS FEnet TCP 2004의 상태·device 단발 읽기와 최대 30초 bounded 값 변화 trace를
  표시하는 읽기 전용 연결 화면

이 구조화 표시는 Host evidence에 결속된 정적 분석 범위를 전달한다. 현재 PLC
runtime truth, 실제 field wiring 또는 프로그램 이름에서 추정한 목적을 증명하지
않으며, 대표 production 프로젝트의 실제 분석 acceptance도 아직 완료되지 않았다.

UI는 실행 성공을 추정하지 않는다. Host와 MCP가 반환한 target identity,
operation 상태, exact native readback과 evidence receipt를 그대로 표시하며,
입력 완료·현재 관측·저장·기능 시험을 구분해 표시한다. 현재 native 이벤트는 이름·상태
위주라 실제 batch prefix/unknown·관측·저장 결과의 공개 요약 투영이 추가로 필요하다.

Web은 permanent 요청 범위를 후속 대화에 유지하며, 보존된 native 실행에는 별도의
이어가기 버튼이 연결돼 있다. 신규 생성 요청도 `new_project_planning`과
`permanent_offline_revision`으로 제출되어 Host의 준비 실행을 거쳐 native 대화로 이어진다.
구현 순서와 완료 조건은 [구현 계획의 단계별 표](AGENT_PRODUCT_PLAN.md), 실제 설치·실행
인수 범위는 [HANDOFF](../../HANDOFF.md)를 따른다.

배포물 서명, 원격 접근, 기기 pairing, PLC에서 프로젝트
복원, 강한 online identity·controller-native scan trace, 실제 PLC write/run edit/force/mode/download
등은 별도 인수 범위다. 설치·일반 Web 신규 제작의 실행 근거는 HANDOFF에 기록하며,
개별 프로젝트의 인수를 제품 전체 완료로 확대하지 않는다.

기존 `xg5000_control.gui` Tkinter 개발·진단 콘솔은 제품 실행 경로가 아니어서
제거했다. 제품 UI는 Platform Web과 Host API만 사용하며, 문서가 추가됐다는
이유로 구현되지 않은 기능을 완료로 주장하지 않는다.

## 12. 기술 근거

- [Microsoft Edge PWA](https://learn.microsoft.com/en-us/microsoft-edge/progressive-web-apps/how-to/)
- [Windows interactive service 제한](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services)
- [OWASP CSRF 방어](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [OWASP WebSocket 보안](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html)
