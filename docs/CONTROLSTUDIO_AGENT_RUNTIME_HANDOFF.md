# ControlStudio 에이전트 런타임 아키텍처

최종 갱신: 2026-07-30

결정 상태: read-only agent 실기 E2E 구현, Host 승인 mutation 계약은 자동 검증만 완료

이 문서는 ControlStudio Host 안의 에이전트, 모델 연결, MCP 생명주기, 상태와
승인 경계를 고정한다. 제품 범위와 안전 정책은 [PRD.md](PRD.md), GUI와 실행
경험은 [CONTROLSTUDIO_GUI_HANDOFF.md](CONTROLSTUDIO_GUI_HANDOFF.md), 전체
계층 요약은 [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md)를 따른다.
신규 구축과 부분 수정의 사람·에이전트 artifact 및 검토 흐름은
[ENGINEERING_COLLABORATION_WORKFLOWS.md](ENGINEERING_COLLABORATION_WORKFLOWS.md)를
따르고, Agent가 읽는 매뉴얼·확인된 기억·제어 패턴의 계약은
[KNOWLEDGE_MEMORY.md](KNOWLEDGE_MEMORY.md)를 따른다.

## 1. 결정 요약

- Python Host API는 FastAPI를 사용한다.
- 첫 에이전트 런타임은 **Pydantic AI**를 사용한다.
- 첫 제품은 하나의 `ControlStudio Engineering Agent`로 시작한다.
- 모델 연결의 기본 계약은 사용자 설정의 **OpenAI-compatible Chat Completions**다.
- `base_url`, `api_key`, `model`, 선택적 header와 timeout을 설정할 수 있다.
- Host가 Pydantic AI의 stdio MCP client 객체와 그 subprocess 수명주기를
  소유하되 안전 권위는 MCP가 소유한다.
- 대화·run·승인·이벤트 상태는 서버의 SQLite 저장소가 권위 있게 소유한다.
- Pydantic AI 내부 객체를 제품 API나 영속 스키마의 권위로 사용하지 않는다.
- 첫 제품에 LangGraph, LangChain Agent, CrewAI, AutoGen, DBOS, Temporal 또는
  다중 에이전트를 넣지 않는다.

```text
React Web/PWA
  → authenticated Host API
    → ControlStudio Run Service
      → AgentRuntime interface
        → PydanticAIRuntime
          ├─ OpenAI-compatible Model Adapter
          └─ capability-filtered MCP tools
            → MCP 안전 코어
              → vendor adapter
```

## 2. 책임 분리

### Engineering Agent

- 사용자의 자연어 요구 해석
- 필요한 읽기·분석 도구 선택
- 사실·추론·가설과 불확실성 구분
- 원인 후보와 추가 측정 제안
- 신규 설계 artifact와 변경안 생성
- 여러 대안의 효과·위험·검증·복구 비교
- 사용자에게 결과 설명

### ControlStudio Host

- 사용자·기기 인증과 세션
- 대화·run·event·승인 상태의 서버 권위 저장
- 모델 설정과 secret reference
- MCP 프로세스 시작, initialize, schema/tool 검증, 감시와 종료
- agent에 노출할 도구와 capability 필터링
- run 상태 기계, 취소, timeout과 재연결
- 승인 요청 저장과 인증된 결정 수집
- 중복 요청 차단과 crash recovery 조정

### MCP 안전 코어

- 정확한 workspace·프로젝트·IDE·controller identity
- capability와 승인 token 검증
- read-before-write, hash와 예상값 precondition
- 전역 잠금, durable change journal과 transaction idempotency
- 최소 변경, native readback, diff와 벤더 검사
- 저장·재열기·시뮬레이션·온라인 readback
- rollback 또는 `QUARANTINED`
- 독립 감사 증거

에이전트가 안전하다고 판단했다는 이유만으로 MCP가 쓰기를 허용하지 않는다.
Pydantic AI의 승인 기능과 Host API 인증도 MCP capability를 대체하지 않는다.

## 3. Pydantic AI 사용 범위

사용하는 범위:

- Python agent loop
- typed dependencies와 Pydantic output
- function tool과 MCP toolset
- 모델·tool 이벤트 스트리밍
- deferred tool approval를 이용한 run 중단
- 모델 공급자 교체
- 테스트 모델과 agent 단위 테스트

첫 구현에서 사용하지 않는 범위:

- beta graph API
- 자동 생성 workflow
- 다중 에이전트와 subagent
- provider native computer-use를 통한 벤더 IDE 우회
- DBOS·Temporal·Prefect·Restate durable execution
- Pydantic AI Harness의 shell/filesystem 권한
- UI adapter가 제출한 전체 conversation history의 무조건 신뢰
- provider-native MCP가 제품 policy gateway와 동등하다는 가정

프레임워크 minor version은 구현 spike와 compatibility test를 통과한 버전으로
lockfile에 고정한다. beta API는 제품 핵심 계약에 사용하지 않는다.

제품 코드는 다음과 같은 자체 경계를 가진다.

```python
class AgentRuntime(Protocol):
    async def start_run(self, request: StartRunRequest) -> RunHandle: ...
    async def resume_run(self, request: ResumeRunRequest) -> RunHandle: ...
    async def cancel_run(self, request: CancelRunRequest) -> None: ...
    async def decide_approval(self, request: ApprovalDecision) -> RunHandle: ...
```

React API와 SQLite에는 Pydantic AI 클래스의 직렬화 결과를 그대로 저장하지
않는다. 제품 스키마와 mapper를 두어 프레임워크를 교체할 수 있게 한다.

## 4. 모델 연결 계약

설정 화면은 최소 다음을 제공한다.

```text
API protocol     OpenAI Compatible / Chat Completions
Base URL         사용자 입력
API Key          secret store에 저장
Model            사용자 입력 또는 gateway alias
Custom headers   선택
Timeout          선택
```

첫 범용 구현은 Pydantic AI의 `OpenAIChatModel`과 사용자 지정
`OpenAIProvider(base_url, api_key)` 또는 동등한 custom `AsyncOpenAI` client를
사용한다. Responses API 전용 기능을 기본 계약으로 요구하지 않는다.

제품 코드에 다음을 하드코딩하지 않는다.

- 특정 `gpt-*`, `claude-*`, `qwen-*` 모델명
- 특정 OpenAI·LiteLLM·Ollama URL
- 특정 provider의 tool schema
- 개발자의 개인 API key

루프백 endpoint를 제외한 model `base_url`은 HTTPS가 기본이다. 사설망의 기존
OpenAI-compatible gateway가 HTTP만 제공하는 경우에는 일반 외부 전송 동의와
별도의 `insecure_http_consent`를 정확한 설정 후보에 대해 함께 요구한다.
이는 TLS 보호가 없다는 위험을 사용자가 명시적으로 받아들이는 예외이며
인증서 검증을 끄는 옵션은 제공하지 않는다. endpoint, custom header와 secret
변경은 신뢰된 로컬 관리자 범위에서만 허용하고 원격 대화 클라이언트가 모델
연결을 바꿀 수 없게 한다.

모델 연결 전에 프로젝트 코드·설정·I/O·tool result가 지정 endpoint로 전송될
수 있음을 명확히 고지한다. workspace별로 `external_model_allowed`,
`local_model_only`, `model_disabled` 같은 egress 정책을 적용하고, 실제 요청에
포함된 artifact/evidence 범주와 endpoint identity를 감사 기록에 남긴다. 필요
이상의 전체 프로젝트를 자동 첨부하지 않고 현재 작업에 필요한 최소 증거만 보낸다.

OpenAI-compatible이라는 이름만으로 agent-ready로 판정하지 않는다. 설정의
`연결 테스트`는 최소 다음 capability를 실제 요청으로 확인한다.

1. 일반 completion
2. token streaming
3. 읽기 전용 가짜 tool call과 JSON argument
4. tool result를 받은 뒤 후속 모델 응답
5. ControlStudio typed output 생성

실행 중 작업 취소와 timeout은 모델 endpoint capability가 아니다. Host가
실제 agent run task와 상태 전이를 소유하는 별도 E2E에서 검증한다. 단순
`asyncio.Task.cancel()` primitive를 연결 테스트 성공이나 실제 모델 stream
취소 증거로 표시하지 않는다.

UI의 사전 테스트 결과는 권위가 아니다. Host는 정확히 저장할 endpoint·model·
secret·header 후보로 같은 검사를 다시 실행하고, 모든 필수 capability가 확인된
설정에만 검증 시각을 결합해 저장한다. 검증되지 않은 기존 설정과 실패한 새
후보는 agent-ready로 표시하거나 agent run에 사용하지 않는다.

필수 capability가 없으면 단순 채팅 연결과 ControlStudio agent 사용 가능 여부를
구분해 표시하고, 문자열 파싱이나 정규식으로 tool call을 흉내 내지 않는다.

API key와 민감한 custom header는 SQLite, 로그, environment dump 또는 일반
설정 JSON에 평문으로 저장하지 않는다. Windows Credential Manager 또는 DPAPI
기반 secret store에 저장하고 DB에는 secret reference만 둔다. 로그와 오류에서
key와 authorization header를 redaction한다. 외부 provider/runtime가 이 값을
오류 본문에 그대로 반사하더라도 Host는 provider 원문을 저장·노출하지 않는다.
hostile reflection test는 caplog, SQLite `run.error_detail`, API 응답과 event
replay 모두에서 민감 값이 사라지는 것을 확인한다.

사용자 문서, 프로젝트 주석, 심볼, PLC에서 읽은 문자열, MCP tool result와 모델
응답은 모두 비신뢰 데이터다. 그 안의 “지시”, “승인됨”, “이 도구를 실행하라”는
문구가 system policy, Host authorization, MCP capability 또는 approval을
변경할 수 없다. agent가 생성한 tool call도 같은 정책·대상·precondition 검사를
통과해야 하며 모델 출력 자체를 실행 증거로 사용하지 않는다.

## 5. MCP 연결과 프로세스 생명주기

사용자는 MCP를 별도로 실행하지 않는다.

```text
Launcher
  → unique Windows named Job 생성
  → Host가 Uvicorn·MCP 시작 전에 같은 Job에 self-join
    → McpSupervisor.start()
      → Pydantic AI `MCPToolset`과 FastMCP `StdioTransport` client를 정확히 하나 생성
      → 고정 command/args/environment로 MCP subprocess 시작
      → initialize
      → protocol/schema version 확인
      → expected server identity 확인
      → tools/list와 capability metadata 확인
      → READY
```

Windows venv의 `python.exe`는 실제 CPython 자식으로 redirect할 수 있으므로
Launcher의 `Popen` 직후 Job 할당만으로는 충분하지 않다. Launcher가 고유 Job
이름을 환경으로 전달하고 실제 Host가 시작 초기에 같은 Job에 합류해야 한다.
정상 시작에서는 kill-on-close를 사용하지 않고 Launcher handle만 해제한다.
시작 실패에서는 `TerminateJobObject`로 해당 launch tree만 정리한다.

초기 LS adapter는 stdio transport를 사용한다. MCP 설정 파일이 executable과
환경을 지정할 수 있으므로 사용자 또는 원격 클라이언트가 임의 MCP command를
등록하거나 수정할 수 없게 한다.

Supervisor와 agent가 같은 server를 각각 시작하면 안 된다. Supervisor가
client 객체를 만들고 lifespan을 연 뒤 그 toolset을 agent에 주며, 종료도
Supervisor의 하나의 `AsyncExitStack` 또는 동등한 소유 경계에서 수행한다.

provider가 제공하는 native MCP 경로는 Host의 tool filtering, 승인, 감사,
오류 정책과 동일하다고 별도 검증되기 전까지 사용하지 않는다. ControlStudio가
생명주기와 tool call을 가로채 기록할 수 있는 framework-managed MCP client를
기본으로 한다.

Host는 다음을 소유한다.

- 하나의 논리 MCP server에 대한 하나의 supervisor
- 시작·종료 timeout
- stdout protocol과 stderr log 분리
- 비정상 종료 탐지
- server version과 tool schema compatibility
- 읽기 장애의 제한적 재시작
- mutation 경계의 자동 재실행 차단

Agent에 모든 MCP 도구를 무조건 노출하지 않는다.

```text
세션 capability
  → provider manifest
  → 현재 대상과 상태
  → Host tool filter
  → Pydantic AI MCP toolset
  → MCP 내부 정책 재검증
```

분석 세션에는 변경 도구가 보이지 않거나 호출 불가능해야 한다. 변경 도구가
모델에 보이는 경우에도 Pydantic AI approval, Host authorization, MCP capability
검사를 모두 통과해야 한다.

모델이 여러 tool call을 한 응답에서 생성해도 XG5000 UI를 만지는 호출은 병렬
실행하지 않는다. Host dispatcher가 `engineering_session` resource key별 단일
큐에 넣고, MCP도 같은 세션 잠금으로 재검증한다. immutable native artifact만
사용하는 순수 분석 도구만 명시적인 allowlist에서 병렬 실행할 수 있다.

사용자 진행 이벤트는 run에 귀속된 `tool.started`, `tool.completed`,
`tool.failed`만 발행한다. payload는 허용된 tool 이름, tool-call ID와 분류된
실패 종류로 제한하고 MCP 인자, 결과, 프로젝트 데이터와 원시 예외 문구는
전달하지 않는다. run별 event sink는 호출이 끝나면 제거하며 동시 run 사이에
이벤트가 섞이거나 이전 sink로 누출되면 fail-closed한다.

첫 로컬 제품에서는 대화가 서로 달라도 non-terminal agent run을 전역으로
하나만 허용한다. 데스크톱과 모바일 등 여러 client가 동시에 요청해도 Host가
하나의 `activeOperation`만 권위 있게 노출하고, 두 번째 대화의 실행 요청은
기존 run이 끝나거나 취소될 때까지 `409`로 거부한다. 같은 `operation_id`의
재전송만 기존 run을 반환하며 새 모델·MCP 실행을 만들지 않는다.

MCP 오류를 모델에게 돌려 자동 재시도시키는 일반 agent 기본값을 mutation에
사용하지 않는다. 읽기 전용·멱등 호출만 분류된 오류에 한해 제한적으로 재시도할
수 있고, `offline_mutate`, `simulate`, `online_modify`, `direct_control`은
typed failure로 fail-closed한다.

## 6. 서버 권위 데이터 모델

SQLite는 첫 로컬 제품의 대화와 작업 상태를 저장한다. 최소 entity는 다음이다.

- `conversation`
- `message`
- `run`
- `run_event`
- `finding`
- `change_proposal`
- `approval_request`
- `approval_decision`
- `tool_invocation`
- `evidence_ref`
- `client_session`

실제 프로젝트 mutation의 durable journal과 lock은 대화 DB만으로 대체하지
않는다. MCP 안전 코어의 `change_transaction`, backup, receipt와 recovery 상태가
최종 권위다. recovery owner handoff는 lease에 write-ahead transition을 먼저
기록하고 journal 갱신을 완료한다. 두 기록 사이 crash는 다음 recovery에서
repair하며 기존 owner process가 살아 있으면 경쟁 claim을 fail-closed한다.
Host DB는 해당 ID와 digest를 참조한다.

브라우저가 보내는 것:

- 새 사용자 메시지
- `conversation_id`
- 승인·거절·취소 명령
- 마지막으로 받은 event sequence

서버가 소유하는 것:

- system instruction
- 전체 대화 이력
- 모델 응답
- MCP tool call과 실제 result
- 승인 요청과 결정
- run 상태
- evidence와 change receipt

클라이언트가 보낸 과거 assistant 메시지, tool call, tool result 또는 승인
결정을 신뢰하지 않는다.

일반 앱 재접속에서 브라우저에 저장된 conversation ID가 없거나 Host에서
사라졌다면, Web은 Host의 최근 conversation 목록에서 최신 항목을 다시 읽는다.
단, 저장된 local workspace를 재개할 때는 최근 engineering conversation을
재사용하지 않는다. 현재 conversation에는 cryptographic workspace binding이
없으므로 원본과 working-copy hash를 다시 확인해 target을 복원한 뒤 새
target-scoped conversation을 생성한다.

## 7. Run 상태 기계

제품 상태는 Pydantic AI run의 내부 상태와 별도로 관리한다.

```text
IDLE
ANALYZING
WAITING_FOR_USER
WAITING_FOR_APPROVAL
APPROVED_PENDING_EXECUTION
EXECUTING
VERIFYING
COMPLETED
CANCELLED
FAILED
QUARANTINED
```

주요 전이:

```text
사용자 요청
  → ANALYZING
    ├─ 추가 정보 필요 → WAITING_FOR_USER
    ├─ 읽기 결과 설명 → COMPLETED
    └─ 변경 필요 → WAITING_FOR_APPROVAL
       ├─ 거절 → CANCELLED
       └─ 승인 → APPROVED_PENDING_EXECUTION
          → MCP precondition 재검증
            → EXECUTING
              → VERIFYING
                ├─ 검증 성공 → COMPLETED
                ├─ 확정된 실패 → FAILED
                └─ 실제 결과 불명 → QUARANTINED
```

상태 전이는 append-only event와 함께 DB transaction으로 기록한다. 같은
`operation_id`의 중복 명령은 같은 결과를 반환하거나 거부한다.

## 8. 승인 계약

Pydantic AI의 deferred approval은 모델이 사람 승인 없이 도구를 실행하지
않도록 run을 멈추는 기능이다. 인증·인가 경계는 아니다.

승인 요청은 Host DB에 다음과 함께 저장한다.

```text
approval_id
actor/session scope
proposal_id
proposal_digest
workspace/project/controller identity
engineering session identity
requested capability
risk summary
requested_at
expires_at
single-use nonce
status
```

React는 서버가 저장한 승인 요청만 표시하고 결정만 제출한다. Host는 인증된
사용자와 권한을 검사하고, MCP는 target identity, plan digest, freshness와
capability를 다시 검사한다.

다음이면 승인을 무효화한다.

- 변경안 또는 digest 변경
- workspace, project, controller 또는 XG5000 PID 변경
- snapshot/online 상태 stale
- 승인 만료 또는 이미 사용됨
- 요구 capability 또는 사용자 권한 변경

`approved: true`, Pydantic AI의 `tool_call_approved`, UI 메시지 또는 모델
문장만으로 mutation을 실행하지 않는다.

## 9. 스트리밍과 GUI 연결

Host는 제품 소유 event schema를 제공한다.

- HTTP: 사용자 명령과 조회
- SSE: agent token, 단계, tool progress, approval과 terminal state
- 재연결: `run_id`와 마지막 sequence 이후 replay
- 고빈도 runtime trace: 필요가 검증되면 별도 WebSocket

Pydantic AI UI adapter를 내부 변환에 사용할 수 있지만, adapter endpoint를
그대로 무인증 공개하거나 client-submitted history를 제품 권위로 사용하지
않는다. 인증된 Host route에서 서버 저장 history와 dependency를 주입하고,
Pydantic AI event를 ControlStudio event로 변환한다.

## 10. 장애와 복구

안전하게 자동 복구할 수 있는 범위:

- 브라우저 종료·새로고침·SSE 재연결
- 모델 호출 전 네트워크 실패
- 읽기 전용 MCP 호출의 명확한 미실행·실패
- 승인 대기 상태의 Host 재시작

자동 재실행하지 않는 범위:

- XG5000 UI mutation 요청을 보낸 뒤 응답 유실
- 프로젝트 저장 요청 이후 Host/MCP 종료
- simulator 또는 controller 쓰기 중 연결 단절
- 실제 적용 여부를 증명할 receipt와 readback이 없음

후자의 경우:

```text
Host 재시작
  → 미완료 operation 발견
  → Host 전역 target-operation lease를 STALE_BLOCKED로 유지
  → MCP durable journal과 실제 XG5000/project 상태 조회
  → 자동 tool retry 금지
  → QUARANTINED
  → 같은 operation과 exact PID/HWND를 다시 증명한 reconcile 절차
  → 확정된 완료 / rollback / 수동 복구
```

Pydantic AI나 미래 durable framework의 retry 정책이 이 규칙을 우회할 수 없다.
stale lease는 시간 경과나 Host 재시작만으로 자동 해제하지 않는다.

## 11. 단일 에이전트 원칙

첫 제품은 하나의 Engineering Agent가 사용자와 대화한다.

```text
ControlStudio Engineering Agent
  ├─ 신규 프로젝트 요구 구체화
  ├─ 기존 프로젝트 분석
  ├─ 진단과 영향 분석
  ├─ 대안 비교
  └─ 검증·복구 결과 설명
```

Planner, Ladder, Safety, Simulation, Reviewer 같은 역할을 처음부터 별도 LLM
에이전트로 복제하지 않는다. 결정론적 분석은 Python/MCP 도구로 구현한다.
병렬 분석, 장시간 trace, 복수 승인처럼 실제 요구가 확인될 때만 workflow 또는
subagent를 추가한다.

### 11.1 한 에이전트, 분리된 실행 모드

단일 Engineering Agent 원칙은 모든 대화에 같은 도구를 노출한다는 뜻이 아니다.
제품이 선택한 workflow에 따라 system instruction과 tool surface를 분리한다.

| workflow | 필요한 대상 | 모델에 제공하는 도구 | 결과 |
|---|---|---|---|
| `new_project_planning` | 요구사항 브리프 | Host read-only 지식 검색만 제공, MCP·mutation 없음 | 구조화된 Project Plan과 미결 질문 |
| `engineering` | 읽기 준비된 workspace 또는 controller | Host read-only 지식 검색, 허용된 MCP read/analyze와 제한된 plan 도구 | 근거 기반 분석 또는 승인 대기 변경안 |

`new_project_planning`은 target이 없어도 실행할 수 있지만 다음을 강제한다.

- Pydantic AI Agent를 MCP toolset과 mutation function tool 없이 별도로 구성하고,
  Host 소유 지식 검색만 read-only function tool로 제공
- 대상 벤더가 미정이면 vendor-neutral pattern만 기본 검색하며, 매뉴얼·기억은
  기록된 vendor/product/version/scope 경계를 넘어 사용하지 않음
- 검색 결과를 현재 프로젝트 사실이나 실행 권한으로 취급하지 않고, 사용한
  `knowledge_ref`를 답변 근거로 인용
- 프로젝트 생성·저장·검사·시뮬레이션·PLC 작업을 수행했다고 주장하지 않음
- 목적, 범위, 가정, 미결 질문, hardware/I/O, mode, sequence, safety,
  alarm/recovery, program structure와 validation을 typed output으로 반환
- `needs_clarification` 또는 `ready_for_review`를 반환
- `ready_for_review`는 사람에게 계획 검토가 가능하다는 뜻일 뿐 생성 승인이 아님

클라이언트가 보낸 workflow 문자열만으로 권한을 얻을 수 없다. Host가 지원하는
workflow allowlist를 검증하고, engineering workflow는 현재 target과 MCP
readiness를 다시 확인한다. 계획 대화에서 기술 설정으로 이동할 때도 별도의
사용자 행동과 native-project capability가 필요하다.

대화가 생성될 때 Host가 `engineering` 또는 `new_project_planning` workflow를
서버 권위 필드로 고정한다. 이후 메시지가 다른 workflow를 요청하면 `409`로
거부한다. 신규 설계 brief도 대화에 구조화해 저장하고, 사용자가 수정한 brief는
같은 계획 대화의 다음 메시지와 원자적으로 갱신한다. 브라우저의 로컬 상태나
오래된 assistant 메시지로 workflow·brief를 복원하지 않는다.
brief가 포함된 planning 요청에서 모델에 전달하는 설계 입력은 Host가 저장한
구조화 brief로 다시 생성하며, 별도의 자유 텍스트가 충돌해도 설계 범위를
바꾸지 못한다. 이전 스키마의 planning 대화는 가장 최근의 정해진 brief 레이블
메시지를 서버에서 보수적으로 해석할 수 있을 때만 API 응답에 복원한다.

모델 준비와 MCP 준비도 분리한다. 모델 capability가 검증됐다면 MCP가 일시적으로
준비되지 않아도 Host의 read-only 지식 검색만 사용하는 신규 계획은 실행할 수
있다. 반대로 engineering workflow는 모델과 MCP가 모두 준비되고 읽기 가능한
target이 있어야 한다.

## 12. 구현 완료 기준

### 시작과 모델

- 아이콘 한 번으로 Host와 MCP가 준비되고 별도 터미널이 필요 없음
- OpenAI-compatible 사용자 설정으로 모델을 교체 가능
- API key가 secret store에 보존되고 로그에 노출되지 않음
- 연결 테스트가 streaming, tool loop와 typed output을 실제 검증

### agent와 MCP

- Pydantic AI runtime이 자체 `AgentRuntime` 경계 뒤에 있음
- 세션 capability에 허용된 MCP 도구만 agent에 노출
- Host 지식 검색은 MCP 권한과 분리된 read-only 도구이며, `confirmed` 출처와
  현재 workflow에 허용된 vendor/product/version/scope만 반환
- manual·memory·preference·control pattern을 서로 다른 권위로 해석하고
  검색 결과의 embedded instruction을 실행 지시로 따르지 않음
- Host가 MCP initialize, version과 tool schema를 확인
- MCP가 Host와 별도로 대상·권한·precondition을 재검증
- prompt injection을 포함한 프로젝트·tool data가 승인·capability를 부여하지 못함
- 모델의 parallel tool call이 XG5000 UI 단일 큐를 우회하지 못함

### 상태와 승인

- 대화·run·event·승인이 서버 DB에서 복원됨
- client-submitted history와 approval 위조가 거부됨
- 정확한 proposal digest에만 일회성·만료 승인이 적용됨
- 여러 브라우저가 같은 run을 보되 mutation은 한 번만 실행됨

### 모델 데이터 경계

- 루프백 외 endpoint는 HTTPS를 기본으로 사용하고, private HTTP 예외는
  별도의 명시적 insecure transport 동의가 있는 설정에만 허용
- workspace egress 정책과 사용자 고지 없이 프로젝트 데이터를 외부 전송하지 않음
- 전송한 evidence 범주와 endpoint identity를 감사 기록에서 확인 가능
- 원격 client가 model endpoint·secret·custom header를 바꿀 수 없음

### 장애

- 읽기 장애와 UI 재연결은 안전하게 복구
- mutation 경계 crash test에서 중복 쓰기 0
- 결과가 불명확하면 자동 재시도 없이 `QUARANTINED`
- workspace open·attach·native create·오프라인 change·reconcile은 하나의
  Host 전역 XG5000 target-operation lease로 직렬화
- stale lease는 exact operation·target recovery에서만 재획득
- MCP receipt와 실제 readback 없이는 완료 상태로 전이하지 않음

## 13. 현재 저장소 상태

이 결정의 읽기·분석 vertical slice와 Host 소유 변경 경계가 구현됐다.

- `host/`: FastAPI, 제품 소유 SQLite schema, Pydantic AI runtime,
  OpenAI-compatible 연결, model secret의 Windows 보안 저장과 MCP supervisor
- `host/`: target 없이 동작하며 MCP와 mutation planner를 받지 않는 typed
  `new_project_planning` workflow. 현재는 Host read-only 지식 검색만 추가 제공
- `launcher/`: 사용자 호출형 Windows Launcher, 고정 Host identity 검사와
  HMAC launch challenge, 인증된 loopback handoff와 브라우저에만 전달되는
  30초 single-use ticket
- `web/`: React·TypeScript 웹/PWA, 서버 대화·event replay, XG5000 인스턴스
  선택, Windows 네이티브 프로젝트 파일 선택, 저장된 workspace 재개,
  프로그램 목록과 `XG5000에서 열기`, run 취소, 변경 승인·복구,
  신규 프로젝트와 FEnet 읽기 전용 화면, 명시적 종료
- `mcp/`: 독립 LS adapter/MCP 경계를 그대로 유지하며 Host가 subprocess
  수명주기를 소유

현재 모델용 최대 allowlist는 Host가 고정한 engineering session과 controller
endpoint의 12개 읽기·분석 도구다. target이 없는 정의는 준비 단계에서 숨기며,
일반 제품 Host는 online observe를 비활성화하므로 controller 2개를 노출하지
않는다. `program.focus`는 분석 결과의 stable
`program_id`에 해당하는 exact XG5000 MDI child와 bound main window만
전면화하는 read-only 도구이며 편집·저장·simulator·PLC 접근은 하지 않는다.
Host의 인증된 프로그램 목록 API와 CSRF 보호 focus API, Web 탐색 패널까지
연결하고 단위·MCP·Host·Web 통합 시험을 통과했지만, 실제 XG5000 live 창
전면화는 아직 검증하지 않았다.
실제 대동 프로젝트에서 사용자 설정 모델
→ Pydantic AI → MCP `project.list_programs`를 호출해 같은 engineering
session에 새 receipt를 남겼고, 최종 응답과 receipt에서 프로그램 4개
(`NewProgram`, `알람`, `초기동작`, `출력`)가 정확히 일치했다. Base URL,
모델명과 secret은 사용자 설정이며 특정 공급자에 하드코딩하지 않는다.

신규 프로젝트 계획 대화에는 이 최대 12개 MCP 도구를 포함한 어떤 MCP·mutation
도구도 보이지 않는다. Host의 read-only 지식 검색만 보이며, 계획 결과는 typed
schema에서 사용자용 Markdown으로 변환하고
`needs_clarification` 또는 `ready_for_review` 상태만 안전한 API 필드로 노출한다.
계획 대화에서 나온 문장이나 상태는 native project 생성 또는 이후 mutation의
승인으로 사용하지 않는다.

변경 transaction은 모델이 임의로 직접 실행하는 도구가 아니다. Host가 정확한
target·plan digest·actor·만료를 저장하고 사용자 결정을 받은 뒤, MCP가 다시
일회성 HMAC authorization과 project identity를 확인한다. 현재 제품 구현은
동일 길이 LD operand 한 개의 snapshot·backup·plan·apply·diff·XG5000 검사·
저장과 crash reconciliation까지만 포함한다. 외부 재열기와 XG-SIM/runtime
verification은 실행되지 않은 follow-up으로 명시한다.

현재 실기 session assurance는 `controlled_launch_read_only`이며 강한
`project_identity_verified_against_selected_pid`가 아니다. 따라서 위 mutation
계약은 구현·자동 테스트 상태지만 이 설치에서 live product mutation/save를
허용하지 않는다. 제한된 elevated x86 COM identity broker가 먼저 필요하다.
Actual XG5000 V4.82.1 저장 workspace 재개는 fresh Host/fresh XG5000 controlled
launch에서 bounded path-readback retry를 거쳐 HTTP 200으로 통과했다. 반면 이미
실행 중인 PID를 같은 workspace로 반복 재개한 시도는 strong ROT
PID/path/hash readback 부재로 fail-closed 및 unverified이며, 기존 PID 재획득
성공으로 취급하지 않는다.

`project.create_native`, `controller.ls.status`, `controller.ls.read`와 recovery
lookup도 Host-owned 경로이며 모델에 raw PID/HWND, destination 또는 PLC
endpoint 선택 권한을 주지 않는다. 신규 생성은 fake backend만 통과했고 현재
실제 XG5000 시도는 modal/elevation 불일치로 변경 전에 거부됐다. FEnet은 fake
TCP까지만 검증됐고 실제 PLC에는 연결하지 않았다. Host는 두 기능을
`preview/fake_only/currentReady=false`로 보고하며 Web은 일반 사용자 경로에서
online observe를 활성화하지 않는다.

native create가 `QUARANTINED`이면 사용자가 exact PID/HWND와 예상 project
path를 다시 선택한 read-only reconcile만 현재 artifact를 판정한다. 생성
operation을 자동 재적용하지 않으며 identity가 맞지 않으면 quarantine과 전역
target-operation lease를 유지한다.

PLC에서 프로젝트 복원, 온라인 write/run edit/force/mode/download, 일반 LD
구성과 전체 신규 설비 로직, Mitsubishi/Rockwell adapter는 여전히 구현되지
않았고 모델 tool surface에도 노출하지 않는다.

## 14. 기술 근거

- [Pydantic AI OpenAI-compatible models](https://pydantic.dev/docs/ai/models/openai/)
- [Pydantic AI MCP client](https://pydantic.dev/docs/ai/mcp/client/)
- [Pydantic AI deferred tool approval](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)
- [Pydantic AI UI/FastAPI integration](https://pydantic.dev/docs/ai/integrations/ui/overview/)
- [Pydantic AI version policy](https://pydantic.dev/docs/ai/project/version-policy/)
