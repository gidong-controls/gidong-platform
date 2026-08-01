# ControlStudio 3개 저장소 분리 리팩토링 계획

최종 갱신: 2026-08-01

상태: 구현 전 확정 계획
기준 커밋: `458dc6955a09d11e121e07f1cee535e6c7575556`

이 문서는 현재 단일 저장소를 다음 세 소스 저장소로 분리하기 위한 실행 계획이다.

```text
ControlStudio-workspace/          # Git 저장소가 아닌 로컬 상위 폴더
├─ controlstudio-platform/        # 공개 예정
├─ controlstudio-agent/           # 비공개
└─ controlstudio-plc-mcp/         # 비공개
```

이 문서는 저장소·프로세스·배포 경계를 정한다. 제품 기능과 안전 요구사항은
[PRD.md](PRD.md)가 우선하며, 이 리팩토링을 이유로 PRD 범위를 줄이거나 현재
미구현 기능을 완료로 표시하지 않는다.

## 1. 최종 결정

- 소스 저장소는 세 개로 분리한다.
- 공개 SDK와 계약은 `controlstudio-platform` 안의 독립 패키지로 둔다. 네 번째
  저장소는 만들지 않는다.
- 최종 공식 Agent는 Platform 프로세스 안에 import되는 패키지가 아니라 별도
  sidecar 프로세스로 실행한다.
- Platform Host가 Agent와 MCP 프로세스를 각각 시작·감시하고, MCP Host 및
  `ExecutionBroker` 역할을 소유한다.
- Agent는 MCP에 직접 연결하지 않는다. 모든 tool call은 실행 제안으로
  Platform에 반환한다.
- Platform은 승인·대상·권한·도구 schema를 검증하고 MCP 호출을 중계한다.
- MCP는 대상 결속, precondition, journal, lock, readback, rollback을 다시
  검증하고 실제 벤더 동작만 수행한다.
- 소스 저장소는 세 개지만 사용자 설치본과 실행 진입점은 하나다.
- 물리적 Git 분리는 논리적 코드 경계와 통합 테스트가 먼저 통과한 뒤 수행한다.

핵심 원칙은 다음과 같다.

> Agent는 분석하고 제안한다. Platform은 허가·중계·감사한다. MCP는 다시
> 검증하고 실제 엔지니어링 동작을 수행한다.

## 2. 외부 피드백 판정

외부 피드백은 다음과 같이 선별 반영한다.

| 제안 | 판정 | 이유 |
|---|---|---|
| 공개 Platform + 비공개 Agent + 비공개 MCP | 채택 | 공개성, IP, 벤더 제어 경계가 다름 |
| 공개 SDK를 독립 패키지로 제공 | 채택 | 계약 drift를 막되 네 번째 Git 저장소는 불필요 |
| 공식 Agent를 최종 in-process provider로 유지 | 폐기 | 장애·의존성·IP·업데이트 경계가 다시 섞임 |
| Agent sidecar와 버전화된 프로토콜 | 채택 | Host와 Agent를 실제 실행 경계로 분리 |
| ToolRuntime을 Agent 아래에 배치 | 폐기 | Agent가 제안과 실행 통로를 함께 소유하게 됨 |
| Platform `ExecutionBroker` | 채택 | 승인·대상·권한·감사 권위를 Host에 유지 |
| Agent의 production MCP 직접 연결 | 금지 | 모델·Agent 오류가 실행 권한으로 직결되는 경로 차단 |
| ACP 등 외부 Agent protocol 즉시 채택 | 보류 | 현재 PLC 계약에 불필요한 범용 복잡성을 추가함 |
| 최신 MCP wire version으로 즉시 전면 전환 | 보류 | 현재 검증된 동작을 유지하고 제품 계약을 별도 버전화 |
| 네 번째 distribution 저장소 | 현재 제외 | 실제 독립 배포팀·채널이 생길 때만 검토 |

## 3. 목표 실행 구조

```text
React Web/PWA
      │ authenticated HTTP + SSE
      ▼
ControlStudio Platform Host                    공개
├─ 사용자 세션·workspace·working copy
├─ conversation·run·approval·audit store
├─ Artifact Store
├─ Agent process supervisor / client
└─ ExecutionBroker / MCP Host
      │                              │
      │ Agent Protocol v1            │ MCP stdio + Product Contract v1
      ▼                              ▼
ControlStudio Agent sidecar          ControlStudio PLC MCP
├─ Pydantic AI                       ├─ capability·target 재검증
├─ 프롬프트·진단·계획                ├─ persistent operation journal
├─ RAG·메모리·평가                   ├─ lock·readback·rollback
└─ ExternalToolRequest 제안          └─ XG5000/XG-SIM/FEnet
      │                              │
      ▼                              ▼
OpenAI-compatible model              XG5000 / 향후 벤더 IDE·PLC
```

Agent와 MCP는 서로 직접 연결하지 않는다. Agent가 요청한 도구는 Platform
`ExecutionBroker`가 현재 run, target, capability, 승인과 결합한 뒤 MCP로
전달한다. Agent에는 MCP subprocess handle, PID/HWND, 승인 서명키 또는 실제
target path/hash를 제공하지 않는다.

## 4. 저장소별 책임

### 4.1 `controlstudio-platform` — 공개

포함한다.

- React/TypeScript Web/PWA
- Windows Launcher
- FastAPI Host와 브라우저 인증
- 사용자·세션·workspace·working copy 관리
- 사용자 업로드 원본 문서와 artifact 수명주기
- conversation·run·event·approval·audit 저장
- plan digest와 단기·1회용 approval record
- Agent/MCP process supervisor와 health
- `ExecutionBroker`, MCP transport, schema/result 검증
- 공개 schema, Python/TypeScript contracts SDK
- Mock Agent, Mock MCP, conformance test kit
- 공개 개발 문서와 black-box 제품 E2E

포함하지 않는다.

- `pydantic_ai` 직접 의존
- 공식 시스템 프롬프트와 PLC 추론 정책
- 사유 RAG ranking·메모리 정책·평가 데이터
- `xg5000_control` 직접 import
- XG5000 native payload·UI 자동화 구현
- 벤더 실기 fixture와 복구 알고리즘 구현

### 4.2 `controlstudio-agent` — 비공개

포함한다.

- Pydantic AI 기반 Engineering Agent
- OpenAI-compatible 모델 adapter
- 시스템 프롬프트와 structured-output validator
- 요구 해석, 진단, 대안 비교, 추천, 변경 계획 생성
- 검색 인덱스, retrieval/reranking, 사람 확인형 기억 정책
- 회사 제공 사유 지식베이스와 PLC 제어 패턴
- Agent checkpoint와 내부 평가·benchmark
- Agent Protocol v1 server
- 모델에 보여줄 도구 설명, alias, 선택·결과 요약 정책

포함하지 않는다.

- MCP subprocess 관리 또는 raw MCP client
- XG5000 PID/HWND/path 직접 조작
- approval token 또는 승인 서명키
- 실제 change apply, save, online write, rollback
- working copy 원본과 사용자 문서 원본의 최종 수명주기 권위

### 4.3 `controlstudio-plc-mcp` — 비공개

포함한다.

- MCP server와 ControlStudio PLC Product Contract 구현
- capability, target identity, precondition의 결정론적 검증
- operation lock, persistent journal, crash quarantine
- native payload 변경, diff, check, save/reopen, readback, rollback
- XG5000/XG-SIM/FEnet와 LS adapter
- 벤더별 profile, 실기 fixture, 개발 콘솔과 실기 E2E
- 향후 Mitsubishi·Rockwell adapter

포함하지 않는다.

- Pydantic AI와 시스템 프롬프트
- 모델 API key
- 사용자 대화 전체 이력
- Platform UI와 HTTP API
- Agent의 진단·추천 정책

## 5. 공개 계약

계약의 단일 원본은 `controlstudio-platform`이 소유한다.

```text
controlstudio-platform/
├─ schemas/
│  ├─ http/v1/
│  ├─ events/v1/
│  ├─ agent/v1/
│  ├─ mcp-product/v1/
│  ├─ workspace/v1/
│  └─ audit/v1/
├─ packages/
│  ├─ python/controlstudio-contracts/
│  └─ typescript/controlstudio-contracts/
├─ host/
├─ web/
├─ launcher/
├─ mocks/
└─ conformance/
```

### 5.1 HTTP API v1

- FastAPI OpenAPI snapshot을 계약으로 보존한다.
- Web TypeScript 타입은 같은 계약에서 생성한다.
- 현재 `api_models.py`와 `web/src/api/types.ts`의 수동 중복을 제거한다.
- required 입력 변경·필드 의미 변경·삭제는 major 변경으로 취급한다.

### 5.2 Event v1

모든 이벤트는 최소 다음 envelope를 사용한다.

```text
event_id
sequence
occurred_at
trace_id
conversation_id
run_id
operation_id
event_type
payload_schema_version
payload
```

읽기 전용의 새로운 additive 이벤트는 구버전 UI가 무시할 수 있다. 승인·변경과
관련된 알 수 없는 schema는 fail-closed한다.

### 5.3 Agent Protocol v1

최종 transport는 Host가 시작한 child process의 stdio를 사용한다. 초기 구현은
작고 결정적인 newline-delimited JSON message framing을 사용하고, 장기 실행과
다중 client 요구가 실제로 생길 때만 Windows named pipe를 검토한다. ACP를 핵심
의존성으로 도입하지 않는다.

최소 계약은 다음을 포함한다.

- `initialize`, `health`, `shutdown`
- `run.start`, `run.resume`, `run.cancel`
- token·progress·evidence·final·error event
- `ExternalToolRequest`와 `ExternalToolResult`
- 모델 연결 검사와 sanitized capability 결과
- 지식 인덱스 갱신·검색·기억 제안/확인 결과
- protocol/version/capability manifest

Agent는 실행 요청을 제안할 뿐 실제 도구 결과는 Platform이 실행한 뒤 반환한다.
Pydantic AI의 직접 `MCPToolset` production 연결은 제거한다.

### 5.4 MCP Product Contract v1

MCP wire protocol과 ControlStudio 제품 계약을 분리한다. 현재 실제 E2E가 통과한
wire 동작을 기준으로 유지하고, 이 리팩토링에서 최신 wire protocol로 전면
교체하지 않는다.

제품 계약은 각 도구에 다음을 선언한다.

```text
tool_name / schema_digest
required_capability
risk_class / side_effect_class
target_binding_required
approval_required
reversible
idempotency_behavior
evidence_schema
operation_timeout
supported_vendor/product/version
```

Host는 MCP 구현 버전 문자열의 exact match가 아니라 product-contract major와
필수 schema digest로 호환성을 판단한다.

## 6. 실행·승인 안전 경계

공식 Agent도 신뢰 가능한 실행 권위로 취급하지 않는다. Agent가 선언한
`risk_class`, target 또는 검증 완료 주장을 그대로 믿지 않는다.

```text
Agent tool proposal
→ Platform schema·allowlist 검사
→ 현재 workspace·target binding 재확인
→ manifest에서 실제 capability·risk 조회
→ 사용자에게 exact plan과 인자 표시
→ 승인 digest와 single-use approval 생성
→ ExecutionBroker가 승인 정보를 MCP 호출에 주입
→ MCP가 target·tool·args·digest·expiry 재검증
→ journal 기록 후 실행
→ readback·evidence 검증
→ Platform audit와 UI 결과 확정
```

승인 record에는 최소 다음을 결합한다.

```text
approval_id
approver_user_id
run_id
target_id / target_digest
tool_name / arguments_digest
plan_digest
capability
issued_at / expires_at
nonce / single_use
```

승인 이후 tool arguments, plan 또는 target이 바뀌면 기존 승인은 무효다. Agent는
approval secret을 받지 않고 Platform이 MCP 호출 직전에 결합한다.

## 7. 상태와 journal 소유권

한 프레임워크의 durable execution으로 모든 상태를 대체하지 않는다.

### Platform Run Journal

- 사용자 요청과 대화
- Agent run과 tool proposal
- 승인 요청·결정·만료
- MCP 호출·결과·사용자 표시
- 감사·artifact 참조

### Agent Checkpoint

- 모델 메시지와 검색 결과
- deferred tool request와 반환 결과
- 중단 가능한 분석 단계

Agent checkpoint는 실제 side effect 성공 여부의 권위가 아니다.

### MCP Operation Journal

- 대상 snapshot·프로젝트 hash·payload
- plan/arguments digest
- 실행 전 상태와 적용 시작
- readback·검사·rollback artifact
- terminal 또는 quarantined 상태

세 journal은 `trace_id`, `run_id`, `agent_step_id`, `tool_call_id`,
`operation_id`, `approval_id`, `target_id`로 연결한다. 결과가 불명확한 mutation은
자동 재시도하지 않고 MCP reconciliation 전까지 격리한다.

## 8. 현재 파일의 분리 판정

| 현재 파일·영역 | 최종 소유자 | 필요한 조치 |
|---|---|---|
| `web/**` | Platform | 그대로 이동, 생성된 contract 타입 사용 |
| `launcher/**` | Platform | Agent/MCP sidecar를 Host Job 안에서 감독하도록 조정 |
| `host/models.py`, `store.py` | Platform | run·event·approval·audit 권위 유지 |
| `host/api_models.py` | Platform contracts | 공개 DTO와 private 내부 DTO 분리 |
| `host/model_connection.py` | Platform + Agent | credential 수명주기는 Platform, 실제 모델 adapter는 Agent |
| `host/agent_runtime.py` | Agent | Protocol DTO를 공개 contract로 추출하고 구현 이동 |
| `host/control_patterns.py` | Agent | 비공개 지식으로 이동 |
| `host/knowledge.py` | 분할 | 원본·메타데이터는 Platform, 파생 index/ranking은 Agent |
| `host/knowledge_ingestion.py` | 분할 | 원본 수명주기 Platform, 파싱·indexing Agent |
| `host/change_execution.py` | 3개로 분할 | 계획은 Agent, 승인·orchestration은 Platform, 실제 변경은 MCP |
| `host/native_projects.py` | 3개로 분할 | workspace는 Platform, 구조 해석은 Agent, XG5000 동작은 MCP |
| `host/mcp_client.py` | Platform 중심 분할 | transport·supervisor·검증은 Platform, 모델-facing 설명만 Agent |
| `host/app.py` | Platform router로 축소 | RAG·Agent·변경 정책을 service/sidecar 경계 뒤로 이동 |
| `mcp/**` | PLC MCP | 현재 독립 package·실기 테스트 보존 |
| knowledge benchmark | Agent | private eval로 이동 |
| XG5000 실기 scripts | PLC MCP | MCP 개발·release gate로 이동 |
| 전체 제품 black-box E2E | Platform | private artifact를 경로로 주입해 실행 |

현재 가장 먼저 제거해야 하는 결합은 다음이다.

1. `host/pyproject.toml`의 `xg5000-control` path dependency
2. `native_projects.py`의 `xg5000_control` 직접 import
3. `app.py`와 `mcp_client.py`의 `pydantic_ai` 직접 결합
4. `app.py` 한 파일에 섞인 API·Agent·RAG·MCP·변경 orchestration
5. Python API 모델과 TypeScript 타입의 수동 중복

## 9. 단계별 실행 계획

### 단계 0 — 기준선과 원본 보존

- 현재 worktree가 깨끗한지 확인한다.
- 전체 테스트, 정적 검사, Web build와 실제 XG5000 evidence를 기록한다.
- 현재 모든 ref를 포함한 Git bundle과 독립 mirror를 만든다.
- dependency·license·secret·artifact inventory를 만든다.
- 현재 저장소와 기존 remote는 비공개 archive로 유지한다.
- 원본 디렉터리에서 history rewrite나 파일 삭제를 수행하지 않는다.

완료 조건:

- 원본, 검증된 bundle, 독립 mirror라는 세 복구점이 존재한다.
- 기준 SHA와 테스트·실기 결과가 문서화돼 있다.

### 단계 1 — 공개 계약 추출

아직 저장소를 나누지 않는다.

- `schemas/http`, `events`, `agent`, `mcp-product`, `workspace`, `audit` v1 생성
- `controlstudio-contracts` Python/TypeScript package 생성
- API·event contract snapshot과 breaking-change 검사 추가
- Web 타입을 계약에서 생성하고 기존 수동 union drift 제거
- Mock Agent와 Mock MCP contract fixture 추가

완료 조건:

- 기존 사용자 동작에 변화가 없다.
- Python·TypeScript 계약 snapshot이 일치한다.
- 호환되지 않는 major/schema digest는 실행 전에 거부된다.

### 단계 2 — Platform ExecutionBroker 도입

이 단계에서는 Agent를 임시로 같은 프로세스에서 실행할 수 있지만 인터페이스는
이미 직렬화 가능한 Agent Protocol DTO만 사용한다.

- MCP lifecycle·transport·tool catalog를 Platform service로 분리
- Host session/target identity 주입을 `ExecutionBroker`로 이동
- Agent가 MCP client를 직접 참조하지 않는 external/deferred tool 경로 구현
- tool proposal, authorization, result를 run audit에 기록
- `change_execution.py`, `native_projects.py`, `mcp_client.py` 책임 분해

완료 조건:

- Agent 코드의 raw MCP client 접근이 0개다.
- Agent 코드의 `xg5000_control` import가 0개다.
- 승인 없는 mutation과 stale target은 전부 거부된다.
- 현재 검증된 read/change 경로가 회귀하지 않는다.

### 단계 3 — Agent sidecar 전환

- 임시 `InProcessAgentAdapter`와 동일 계약을 구현하는
  `SubprocessAgentClient`를 만든다.
- Pydantic AI, 프롬프트, RAG index와 평가 코드를 Agent package로 이동한다.
- Host는 Agent manifest와 protocol version을 검증한 뒤 child process를 사용한다.
- Agent stdout은 protocol 전용, 일반 로그는 stderr 또는 구조화된 log sink를 쓴다.
- Agent crash 이후 read-only run은 명시적으로 재개할 수 있지만 mutation 결과는
  MCP journal 확인 전 자동 재시도하지 않는다.

완료 조건:

- Platform production dependency에서 `pydantic_ai`가 제거된다.
- Agent 종료가 Host 또는 MCP를 함께 종료시키지 않는다.
- 공식 Agent와 Mock Agent가 동일한 contract test를 통과한다.
- Platform은 공식 Agent 바이너리 없이 mock/demo로 실행된다.

### 단계 4 — MCP Product Contract와 journal 정렬

기존 안전 코어를 재작성하지 않고 공개 제품 계약 뒤에 정렬한다.

- 구현 버전 exact match를 contract/schema compatibility 검사로 교체
- target binding, plan/args digest, single-use approval을 계약화
- 기존 operation lock, journal, crash reconciliation evidence를 정규화
- Agent가 제공한 위험 등급이 아닌 MCP manifest 위험 등급 사용
- 현재 검증된 MCP wire baseline을 유지

완료 조건:

- PID·project hash·arguments·approval이 달라지면 실행이 거부된다.
- approval nonce 재사용이 거부된다.
- MCP 강제 종료 뒤 미완료 mutation이 감지·격리된다.
- 동일 target의 경쟁 mutation 중 하나만 허용된다.

### 단계 5 — 로컬 Git 저장소 3개 생성

논리 경계와 통합 테스트가 통과한 뒤에만 수행한다.

```text
ControlStudio-workspace/          # Git 아님
├─ controlstudio-platform/        # 새 local Git
├─ controlstudio-agent/           # 새 local Git
└─ controlstudio-plc-mcp/         # 새 local Git
```

- 현재 저장소 안에 nested Git 저장소를 만들지 않는다.
- 세 sibling 디렉터리에 정제된 현재 snapshot을 복사하고 각각 `git init`한다.
- 당장은 remote를 만들거나 push하지 않는다.
- 기존 monorepo history는 private archive와 bundle에 보존한다.
- 공개 Platform은 private 코드가 있었던 기존 history를 가져오지 않고 clean
  genesis commit으로 시작한다.
- private Agent/MCP도 우선 clean genesis로 시작해 분리 위험을 줄인다. 과거
  component history가 실제로 필요할 때만 별도 candidate clone에서
  `git-filter-repo`를 사용한다.
- 배포용 dependency에 sibling 상대 경로를 커밋하지 않는다. 로컬 compose 설정만
  ignored 파일 또는 command argument로 경로를 주입한다.

완료 조건:

- 세 저장소가 각각 독립 build/test를 통과한다.
- 상대 저장소의 source import가 0개다.
- Git submodule과 네 번째 composition 저장소가 없다.
- 세 build artifact를 사용한 packaged cross-repo E2E가 통과한다.

### 단계 6 — 패키징·서명·설치본

- Platform build, private Agent build, private MCP build를 독립 생성한다.
- Agent와 MCP는 release에서 소스 wheel이 아닌 compiled artifact로 배포한다.
- 각 artifact에 version, contract version, SHA-256과 서명 정보를 남긴다.
- private release pipeline이 exact artifact를 조합해 하나의 설치본을 만든다.
- 사용자가 ControlStudio 아이콘을 누르면 Host가 Agent와 MCP를 시작하고 Web/PWA를
  연다. Windows 로그인만으로 자동 실행하지 않는다.

```json
{
  "product_release": "0.1.0",
  "api_contract": "1",
  "event_contract": "1",
  "agent_contract": "1.0",
  "mcp_product_contract": "1.0",
  "components": [
    {"id": "platform", "version": "0.1.0", "sha256": "..."},
    {"id": "agent", "version": "0.1.0", "sha256": "..."},
    {"id": "plc-mcp", "version": "0.1.0", "sha256": "..."}
  ]
}
```

완료 조건:

- Python·Node·uv가 없는 clean Windows 환경에서 설치·실행된다.
- 서명·hash·contract 불일치 artifact는 실행하지 않는다.
- 사용자 DB·원본 문서·workspace는 업그레이드 시 보존된다.
- 제거 시 사용자 데이터를 기본적으로 자동 삭제하지 않는다.

## 10. 로컬 개발과 검증

상위 `ControlStudio-workspace`는 Git 저장소로 만들지 않는다. Platform의 개발
script가 sibling 경로를 명시적으로 입력받아 조립한다.

- Platform 단독: Mock Agent + Mock MCP
- Agent 단독: Fake Platform Services + Fake MCP results
- MCP 단독: 기존 unit/integration/실기 harness
- 전체 제품: 세 저장소에서 만든 wheel/exe를 사용한 black-box E2E

editable source 조합 테스트만으로 release를 통과시키지 않는다. 마지막 E2E는
실제 패키지 artifact를 사용한다.

검증 순서는 다음과 같다.

1. Platform unit·contract·API·Web test
2. Agent prompt·structured output·RAG·evaluation test
3. MCP unit·integration·crash·safety test
4. Mock 조합 E2E
5. 공식 Agent + 공식 MCP packaged E2E
6. 실제 XG5000 read·offline mutation·rollback release gate
7. 실제 PLC·online capability는 별도 환경이 없으면 미검증으로 유지

## 11. 공개 저장소 보안 검사

현재 저장소는 이미 Agent와 MCP 구현 이력을 포함하므로 기존 Git remote를 공개
Platform remote로 재사용하지 않는다.

공개 전 다음을 working tree와 전체 reachable history에서 확인한다.

- Agent 프롬프트·RAG policy·evaluation fixture가 없음
- MCP source·profiles·실기 fixture가 없음
- API key, custom header, 실제 endpoint, SQLite DB가 없음
- 사용자 문서·프로젝트·`artifacts/`가 없음
- private package build artifact가 없음
- 삭제된 private 파일 blob과 과거 remote-tracking ref가 없음
- 공개 license, third-party notices, trademark 정책이 명확함

공개 history 감사가 조금이라도 불확실하면 history 보존을 포기하고 검증된 현재
Platform 파일만으로 clean genesis를 만든다. 공개 이력보다 비공개 코드 보호가
우선이다.

## 12. 이 리팩토링에서 하지 않는 것

- Mitsubishi·Rockwell adapter 구현
- 실제 PLC online write·force·mode change 활성화
- 새로운 범용 workflow engine·멀티 Agent·DBOS·Temporal 도입
- ACP를 핵심 Agent protocol로 채택
- MCP wire protocol 전면 업그레이드
- 네 번째 SDK 또는 distribution Git 저장소 생성
- GUI 재설계
- 기존 MCP 안전 코어의 전면 재작성
- 저장소 분리와 무관한 리팩토링

이 항목들은 PRD에서 제거되는 것이 아니라 이번 저장소 분리 작업의 비범위다.

## 13. 커밋 단위

한 번에 세 저장소를 만들지 않고 다음과 같은 coherent increment로 진행한다.

1. `docs: define three-repository refactor plan`
2. `feat: add versioned public contracts and mocks`
3. `refactor: route model tool proposals through execution broker`
4. `refactor: isolate agent behind protocol adapter`
5. `feat: run official agent as supervised sidecar`
6. `refactor: align PLC MCP with product contract`
7. `chore: create audited local repository snapshots`
8. `build: compose signed three-component product`

각 커밋은 관련 unit test와 기존 제품 E2E를 통과한 상태여야 한다. 저장소가
물리적으로 갈라진 뒤의 커밋은 각 저장소에서 독립적으로 기록한다.

## 14. 전체 완료 조건

- 공개 Platform, 비공개 Agent, 비공개 PLC MCP의 세 local Git 저장소가 존재한다.
- 공개 Platform은 private artifact 없이 Mock 구성으로 실제 실행된다.
- 공식 Agent는 별도 sidecar이고 Platform과 버전화된 계약으로만 통신한다.
- Agent에는 MCP connection, target raw identity, approval secret이 없다.
- Platform `ExecutionBroker`가 모든 tool proposal·승인·MCP 호출·감사를 중계한다.
- MCP가 mutation precondition·journal·readback·rollback을 독립 강제한다.
- 사용자 원본 데이터는 Platform이 소유하고 Agent index는 재생성 가능한 파생물이다.
- 세 journal의 권위와 연결 ID가 명확하다.
- 세 저장소가 각각 독립 build/test되고 packaged E2E가 통과한다.
- 공개 Git history에 Agent/MCP 구현과 민감 데이터가 없다.
- 하나의 설치본과 아이콘으로 Platform·Agent·MCP가 함께 시작된다.
- 저장소 분리 전의 실제 XG5000 검증 범위가 회귀하지 않는다.
- 실제 PLC·online 미검증 범위는 계속 fail-closed로 표시된다.

## 15. 다음 작업 단위

이 문서를 승인한 다음 바로 수행할 첫 작업은 물리 Git 분리가 아니다.

> 공개 `contracts`의 최소 schema와 `ExecutionBroker` 인터페이스를 현재
> monorepo에 추가하고, 기존 Pydantic AI의 tool call을 external proposal 경로로
> 통과시키되 사용자 동작과 MCP 실행 결과는 바꾸지 않는다.

이 increment가 기존 Host·Web·MCP 테스트와 실제 XG5000 read 경로를 통과한 뒤
Agent sidecar와 물리 저장소 분리로 진행한다.
