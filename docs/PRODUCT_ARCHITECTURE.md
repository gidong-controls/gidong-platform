# ControlStudio 제품 아키텍처

최종 갱신: 2026-08-01
결정 상태: three-repository 논리 경계 구현 기준. 물리 Git 분리와 production
release는 별도 evidence gate가 남아 있다.

제품 기능·안전·출시 범위는 [PRD.md](PRD.md)가 우선한다. 이 문서는 공개
Platform과 비공개 Agent/PLC MCP가 결합되는 실행 책임을 고정하며, 실제 PLC
online 기능을 완료로 선언하지 않는다.

## 확정 구조

```text
사용자 → Windows Launcher → Platform Host / FastAPI → Web/PWA
                                  │
                    conversation · run · approval · audit · workspace authority
                                  │
                           ExecutionBroker
                         ┌────────┴────────┐
             Agent Protocol v1          MCP stdio + Product Contract v1
                       │                           │
      private gidong-agent            private gidong-plc-mcp
       Pydantic AI / model / RAG       vendor action / operation journal
       proposal only                   precondition / readback / rollback
```

Platform은 Agent와 MCP child process를 시작·health check·종료하고, 두 프로세스의
독립 실패를 처리한다. Agent는 MCP subprocess/raw client를 갖지 않으며 MCP는
Agent prompt/model credential/user conversation을 갖지 않는다.

## 컴포넌트 책임

| 컴포넌트 | 소유·수행 | 소유하지 않는 것 |
| --- | --- | --- |
| `gidong-platform` (public) | Launcher, UI, HTTP/SSE, 사용자 원본·workspace/working copy, conversation/run/approval/audit, Agent/MCP supervision, `ExecutionBroker`, 공개 schemas/SDK, mocks/conformance | Pydantic AI prompt/retrieval ranking, vendor UI/native payload 구현, MCP operation journal의 execution truth |
| `gidong-agent` (private) | Pydantic AI sidecar, model adapter, prompt·structured output, analysis/planning, disposable RAG index/ranking/evaluation, `ExternalToolRequest` proposal | MCP 연결/PID/HWND/target raw identity, approval secret, 실제 apply/save/online/rollback, 원본 문서의 authority |
| `gidong-plc-mcp` (private) | MCP Product Contract 구현, vendor adapter, target/precondition 재검증, operation lock/journal, apply/check/readback/rollback/reconciliation | Agent/model, Platform UI/HTTP, 사용자 전체 대화·원본 문서 authority |

세부 ownership과 데이터 흐름은
[RESPONSIBILITY_AND_DATA_OWNERSHIP.md](refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md)를
따른다.

## 실행·승인 경계

Agent의 tool call은 실행 권한이 아니라 이름과 arguments의 제안이다. Platform은
Agent alias만 노출하고 raw target identity, controller endpoint, approval material을
모델-facing schema에서 제거한다. `ExecutionBroker`가 run·workspace·target을
다시 결합하고 allowlist/schema/manifest/capability를 확인한 뒤 결과를 audit/event로
기록한다.

mutation은 exact plan, tool, arguments digest, target digest, capability, actor,
expiry, single-use nonce가 결합된 Platform approval 없이는 진행하지 않는다. MCP는
그 claim과 현재 target/precondition을 독립 재검증하고 operation journal을 먼저
기록한다. PID/project hash/arguments/approval nonce가 달라지거나 결과가 불명확하면
거부·quarantine하며 자동 재시도하지 않는다.

## 버전화된 경계

- HTTP/Event/Workspace/Audit/Agent/MCP Product v1 schema의 source of truth는
  Platform `schemas/`다. Python/TypeScript `controlstudio-contracts`는 DTO와
  validation만 제공한다.
- Agent Protocol은 NDJSON stdio `1.0`이다. Platform은 initialize manifest의
  protocol version/capabilities를 검사하고 stdout의 protocol framing만 신뢰한다.
- MCP wire version과 Product Contract는 별개다. Platform은 product-contract major와
  required tool schema digest/manifest digest로 호환성을 판단한다.
- breaking HTTP change, required field 삭제/추가는 해당 major와 snapshot을 함께
  갱신하지 않으면 contract guard에서 거부되어야 한다.

## 지식과 상태의 경계

Platform은 사용자가 업로드한 원본, metadata, citation, revision, confirmation,
workspace와 audit를 보존한다. Agent는 Platform이 명시적으로 sync한 sanitized
source snapshot에서 chunk/index/ranking을 파생하며, 이는 재생성 가능한
Agent-local data다. 문서·검색 결과·모델 출력은 비신뢰 입력이고 권한·approval 또는
PLC target identity를 만들 수 없다.

Platform run journal, Agent checkpoint, MCP operation journal은 대체 관계가
아니다. 연결 ID와 authoritative record는 ownership 문서에 정의한다. 특히
Agent checkpoint가 side effect 성공을 확정하지 않으며, MCP journal만 mutation
reconciliation의 기준이다.

## 검증·패키징·실기 범위

Platform은 private artifact 없이 Mock Agent+Mock MCP로 실행·contract test할 수
있어야 한다. 공식 Agent/MCP는 각자 독립 build/test 후 immutable packaged artifact
만으로 black-box E2E를 수행한다. BOM은 platform/agent/plc-mcp 세 artifact의
version, SHA-256, 여섯 contract version, signature metadata를 정확히 일치시킨다.
unsigned/development HMAC artifact는 production이 아니다.

과거 실제 XG5000 read/offline mutation/rollback evidence는 회귀 gate로 보존한다.
현재 replay 진단은 `NOT_EXECUTED`이며, real PLC online identity/write/force/mode
change/download/run edit은 미검증이고 fail-closed다. 자세한 구분은
[XG5000 regression evidence](refactor/XG5000_REGRESSION_EVIDENCE.md)를 따른다.
remote 생성·push는 사용자 지시 전까지 하지 않는다.
