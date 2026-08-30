# ControlStudio Web

ControlStudio의 React + TypeScript 웹/PWA 표시 계층이다. PLC 래더 IDE를 복제하지
않으며, Host가 소유한 상태·대화·근거·활동·승인 계약을 보여 주는 companion UI다.

## 실행

```powershell
npm install
npm run dev
npm run build
```

제품에서는 `npm run build`가 만든 `dist/`를 ControlStudio Host가
`http://localhost:8765`의 same-origin 정적 파일로 제공한다. 개발 서버 단독 실행은
Host API가 없으므로 의도적으로 `HOST OFFLINE` 상태를 표시한다.

## 현재 제품 API 계약

React 클라이언트는 camelCase 응답을 제공하는 `/api/v1` 제품 계약만 사용한다.

- `GET /api/v1/bootstrap`
- `POST /api/session`
- `GET /api/v1/status`
- `POST /api/v1/conversations`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversationId}`
- `POST /api/v1/conversations/{conversationId}/messages`
- `POST /api/v1/runs/{runId}/cancel`
- `GET /api/v1/events?after_sequence=...`
- `GET /api/v1/settings/model`
- `PUT /api/v1/settings/model`
- `POST /api/v1/settings/model/test`
- `GET /api/v1/engineering/programs`
- `POST /api/v1/engineering/programs/{programId}/focus`
- `POST /api/v1/files/select-xg5000-project`
- `POST /api/v1/workspaces/open-local`
- `POST /api/v1/workspaces/{workspaceId}/resume`
- `GET /api/v1/workspaces/{workspaceId}`
- `GET /api/v1/xg5000/instances`
- `POST /api/v1/xg5000/attach`
- `POST /api/v1/native-projects`
- `GET /api/v1/native-projects/{nativeProjectId}`
- `POST /api/v1/native-projects/{nativeProjectId}/reconcile`
- `POST /api/v1/controllers/ls/connect`
- `GET /api/v1/controllers/ls/status`
- `POST /api/v1/controllers/ls/read`
- `DELETE /api/v1/controllers/ls/connection`
- `POST /api/v1/change-transactions`
- `GET /api/v1/change-transactions`
- `GET /api/v1/change-transactions/{transactionId}`
- `POST /api/v1/change-transactions/{transactionId}/decision`
- `POST /api/v1/change-transactions/{transactionId}/execute`
- `POST /api/v1/change-transactions/{transactionId}/reconcile`
- `POST /api/v1/host/shutdown`
- `POST /api/v1/host/restart`
- `GET /api/v1/host/restart/{restartReceipt}`

상태의 `mcp.toolCount`는 현재 서버가 등록한 공개 원자 도구 수이고
`mcp.agentToolCount`는 모델용 **최대 allowlist** 수다. 후자는 현재 run에 실제
보이는 도구 수가 아니다. engineering session이나 검증된 controller target이
없는 정의는 Pydantic AI 준비 단계에서 숨긴다.

메시지 요청에는 본문의 `operationId`와 같은 `Idempotency-Key`를 보낸다. SSE는
인증 헤더를 보낼 수 있는 `fetch` 스트림으로 연결하고, 마지막으로 확인한 sequence를
`Last-Event-ID`와 `after_sequence`에 함께 보내 재연결한다. `agent.token`은 바로
표시하고 `agent.message` 및 run 상태 이벤트가 오면 서버 대화를 다시 읽는다.
대화 내용과 실행 상태의 권위는 Host에 있고 브라우저에는 활성
`conversationId`만 저장한다.

## Launcher 세션 인증

Launcher는 master launch secret을 브라우저나 process argv에 전달하지 않는다.
검증된 Host의 인증된 loopback API에서 30초짜리 일회성 handoff ticket을 발급받아
UI 주소의 fragment `#handoff_ticket=<ticket>`으로 Launcher 소유 Chromium 앱
창을 연다. React는
이 fragment를 읽자마자 `history.replaceState`로 URL에서 제거한 뒤
`POST /api/session`으로 원자적으로 한 번만 교환한다. 재사용되거나 만료된 ticket은
거부되며 Host는 성공한 교환에만 `{sessionToken, expiresAt}`을 반환한다.
React는 이 파생 브라우저 세션 토큰을 현재 origin/port의 `sessionStorage`에만
저장하고 모든 `/api/v1` 요청과 fetch 기반 SSE에
`Authorization: Bearer <sessionToken>`을 보낸다. `/api/v1` 또는 SSE가 `401`을
반환하면 메모리와 `sessionStorage`의 세션을 즉시 지우고 Launcher 재실행을
안내한다.

Launcher handoff ticket은 React state, DOM, 로그, `localStorage`,
`sessionStorage` 또는 대화 저장소에 넣지 않는다. 파생 세션 토큰도 DOM, 로그,
`localStorage`에 넣지 않는다. 유효한 파생 세션도 없고 Launcher 토큰도 유효하지
않으면 제품 기능 대신 Launcher로 다시 열어야 한다는 안내를 표시한다.

검증된 Host와 Launcher 소유 ControlStudio Chromium 앱 창이 하나 있으면 새
handoff나 창을 만들지 않고 그 창을 복원·전면화한다. 새 Host를 시작했거나
Launcher 소유 창이 여러 개인 복구 상태에서만 기존 소유 창을 닫고 single-use
handoff를 가진 앱 창 하나를 새로 연다. 정확히 한 창의 소유권과 foreground
전환을 확인하지 못하면 추가 창을 추측해 열지 않고 실패한다.
주소로 직접 연 일반 브라우저나 설치형 PWA는 같은 Host 상태를 볼 수 있지만
Host를 시작하거나 Launcher 소유 단일 창 계약을 대신하지 않는다.

## 실행 중 작업 취소

Host가 `activeOperation.runId`와 비종료 run 상태를 반환하면 UI에
`실행 중 작업 취소`를 표시한다. 사용자가 명시적으로 누른 경우에만
`POST /api/v1/runs/{runId}/cancel`을 호출하며, 이미 종료된 run에 대한 멱등 응답도
성공 결과로 표시한다. 브라우저 닫기, 새로고침 또는 Host 종료 요청이 run 취소로
해석되지는 않는다.

## 현재 일반 사용자 제품 경로

- 로컬 `.xgw`/`.xgwx`의 immutable source capture와 working copy 생성,
  XG5000 open/attach와 읽기·분석
- 실제 프로그램 목록을 stable `programId`로 표시하고 해당 위치를 XG5000에서
  여는 읽기 전용 호출. 실제 XG5000 창 전면화 증거가 없으면 완료로 표시하지 않음
- reviewed Engineering Plan 전체, typed I/O·mode·STEP sequence·TON timer·latched
  alarm·recovery·control network·process interlock과 기술 설정을
  `engineered_control_program` build bundle로 고정하고 exact PID/HWND의 빈
  XG5000 창에 네이티브 프로젝트와 제어 프로그램을 생성. dialog, mnemonic
  readback, LD 변환, Program Check, project path/hash와 bundle receipt가 모두
  일치한 뒤, 보호 working copy를 생성 PID와 다른 XG5000 프로세스에서 다시 열어
  exact 재추출·Program Check·disk hash 불변의 `REOPEN_VERIFIED` receipt까지
  확인. sequence 검토 화면은 STEP별 완료 조건과 `START 해제 필요/연속 반복`
  재기동 정책을 노출하며, 같은 bundle의 XG-SIM 정상·인터록·모드 해제·고장
  래치·복구·TON 완료·START 해제 재기동 시나리오, CPU STOP과 입력 원복 receipt가
  모두 일치하면 `OBSERVING`으로 표시. 사용자가 digest-bound receipt를 명시적으로
  채택한 뒤에만 완료 처리하고 보호 working copy를 현재 대상으로 연결
- 사용자가 지정한 사설망 LS XGT FEnet TCP 2004 endpoint의 상태, 최대 16개
  device 단발 읽기와 최대 30초 값 변화 trace를 제공하는 명시적 읽기 전용 연결.
  trace는 샘플·전이·최소/최대·변화 횟수와 전후 CPU 상태를 표시한다. 반환
  controller identity는 provisional이며 PLC 쓰기나 project upload 권한을 만들지 않음
- 동일 길이 LD operand 한 개의 구조화 proposal, 승인·거절, 실행과
  `QUARANTINED` 항목의 명시적 PID/HWND reconcile. 단, verified workspace와
  strong XG5000 identity가 없으면 `offline_mutate/currentReady=false`로 차단

네이티브 프로젝트 생성과 FEnet 관찰은 MCP가 준비된 경우 열리며, 각 작업의
대상·정체성·권한 조건을 충족하지 못하면 해당 요청만 거부한다. 신규 생성은
독립 재열기와 신규 build XG-SIM 인수까지 포함한다. 기존 프로젝트의 한 operand 변경 transaction은
`offline_native_check_only` 범위이며 XG-SIM·online readback을 완료로 표시하지
않는다.

XG5000 IDE attach와 LS FEnet controller connection은 서로 다른 상태다.
선택한 PID와 로컬 프로젝트 정체성이 증명돼도 실제 PLC에 연결됐거나 PLC
프로젝트와 일치한다는 뜻이 아니다.

## 아직 비활성인 제품 경로

다음은 안전한 제품 계약 또는 실기 증거가 없으므로 명시적으로 비활성이다.

- PLC에서 프로그램·파라미터를 읽어 workspace로 복원
- 강한 controller identity와 로컬/PLC project compare
- 실제 PLC/XG-SIM의 STEP·timer·scan·통신 trace
- 실제 PLC write, run edit, force, mode change와 download
- 임의 기존 프로그램의 일반 LD 런·접점·분기 생성·삭제
- 요구사항 기반 신규 하드웨어·I/O·태스크·심볼 구성
- Mitsubishi/Rockwell adapter

UI는 capability가 없다는 사실을 숨기거나 성공 상태를 추정하지 않는다.

## 명시적 종료

브라우저/PWA 창을 닫는 동작은 Host 종료 요청으로 해석하지 않는다. 헤더의
`ControlStudio 종료`에서 한 번 더 확인한 경우에만
`POST /api/v1/host/shutdown`을 호출한다. 활성 작업 때문에 Host가 `409`를 반환하면
종료됐다고 추정하지 않고 서버가 제공한 거부 이유와 작업을 취소하거나 완료를
기다리라는 안내를 표시한다. 성공 응답을 받은 경우에만 세션 토큰을 지우고 종료
완료 화면으로 전환한다.

## 개발 Host 재시작

`코드 다시 불러오기 (UAC 없음)`은 authenticated browser session과 CSRF proof로만
`POST /api/v1/host/restart`를 요청한다. Host가 active run, mutation, native creation 또는
ACTIVE target lease를 감지하면 `409`으로 거부한다. 성공 시 UI는 non-secret
`restartReceipt`를 poll하고 replacement Host가 `ready`가 되면 같은 session을 유지한 채
새로고침한다. Host-owned helper는 현재 Host가 8767을 반납할 때까지 기다린 뒤 fixed
executable/module만 실행한다. XG5000은 이 경로에서 종료하거나 재시작하지 않는다.

## 모델 자격 증명

설정 화면은 OpenAI 호환 Chat Completions의 Base URL, 모델, API 키와 선택적
사용자 지정 헤더를 Host에 전달한다. 키와 헤더는 별도 OS 자격 증명 저장소 없이
Host의 로컬 SQLite `model_settings` 행에 함께 저장한다. 저장된 값은 API로 다시
받거나 표시하지 않고 `hasApiKey`와 `customHeaderNames`만 보여 준다. localhost가 아닌
엔드포인트는 명시적인 `externalModelConsent` 없이는 테스트하거나 저장할 수 없다.
localhost가 아닌 HTTP endpoint는 여기에 더해 정확한 설정 후보에 대한
`insecureHttpConsent`가 필요하며 TLS로 보호되지 않는다는 경고를 표시한다.
사용자가 새로 입력한 키와 헤더 값도 저장 성공 또는 설정 창 닫기 즉시 UI
메모리에서 비운다.

## PWA

`manifest.webmanifest`와 아이콘만 제공한다. 서비스 워커를 등록하거나 생성하지 않으며,
API·작업 상태·승인 상태를 브라우저 캐시에 저장하지 않는다.
