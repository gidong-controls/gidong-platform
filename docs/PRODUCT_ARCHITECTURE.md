# GIDONG 제품 아키텍처

최종 갱신: 2026-08-31
결정 상태: Platform·Agent·MCP는 세 core service 경계다. 별도 vendor CLI repository가
이관된 XG5000 명령의 결정적 process/JSON 경계를 제공한다. 그러나 CLI의 native backend
source ownership 추출과 release independence는 아직 완료가 아니라 deferred 상태다.
production release와 실제 PLC online 기능은 별도 evidence gate가 남아 있다.

제품 기능·안전·출시 범위는 [루트 PRD](../PRD.md)가 우선한다. 이 문서는 공개
Platform과 비공개 Agent/PLC MCP가 결합되는 실행 책임을 고정하며, 실제 PLC
online 기능을 완료로 선언하지 않는다. 이 문서와 다른 오래된 설계·상태 문서는 작성
시점의 기록이다. 구현 상태는 현재 source/runtime evidence를 기준으로 확인하되,
그 evidence가 아직 구현되지 않은 기능을 제품 요구 범위에서 제외하는 근거는 아니다.
최신 사용자 요구는 오래된 PRD·설계의 요구보다 우선한다.

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
                                                       │
            migrated vendor commands → separate deterministic vendor CLI repository
                                                       │
                                              XG5000 adapter / UI API
```

Platform은 Agent와 MCP child process를 시작·health check·종료하고, 두 프로세스의
독립 실패를 처리한다. Agent는 MCP subprocess/raw client를 갖지 않으며 MCP는
Agent prompt/model credential/user conversation을 갖지 않는다.

MCP가 vendor command를 CLI로 위임한 경로의 실행 사슬은
`Agent → Platform Host/ExecutionBroker → MCP adapter → separate CLI → XG5000`다.
Agent는 CLI를 직접 호출하지 않고 여전히 MCP tool action만 제안한다. separate CLI는
모델 권한 경계가 아니라 독립적·결정적인 vendor adapter/API transport 경계다. 따라서
vendor별 process·JSON request/receipt·timeout을 MCP protocol과 분리할 수 있다. 아직
모든 MCP vendor route가 CLI로 이관된 것은 아니며, 이관되지 않은 route의 책임·검증
경계도 MCP에 남는다. CLI base package의 `instances` vendor closure는 독립 실행되지만,
나머지 legacy-native 명령은 현재 선택적 `legacy-core` extra를 통해 MCP가 소유한
`xg5000_control`을 import한다. 따라서 process/JSON 위임은 구현되어도 native backend
전체의 물리적 추출 완료를 뜻하지 않는다.

## 컴포넌트 책임

| 컴포넌트 | 소유·수행 | 소유하지 않는 것 |
| --- | --- | --- |
| `gidong-platform` (public) | Launcher, UI, HTTP/SSE, 사용자 원본·workspace/working copy, conversation/run/approval/audit, Agent/MCP supervision, `ExecutionBroker`, 공개 schemas/SDK | Pydantic AI prompt/retrieval ranking, vendor UI/native payload 구현, MCP operation journal의 execution truth |
| `gidong-agent` (private) | Pydantic AI sidecar, model adapter, prompt·structured output, analysis/planning, disposable RAG index/ranking/evaluation, `ExternalToolRequest` proposal | MCP 연결/PID/HWND/target raw identity, approval secret, 실제 apply/save/online/rollback, 원본 문서의 authority |
| `gidong-plc-mcp` (private) | MCP Product Contract 구현, vendor adapter, target/precondition 재검증, operation lock/journal, apply/check/readback/rollback/reconciliation | Agent/model, Platform UI/HTTP, 사용자 전체 대화·원본 문서 authority |
| `controlstudio-xg5000-cli` (separate vendor repository) | 이관된 명령의 deterministic subprocess/JSON request-result boundary, vendor-specific receipt 반환, base `instances` closure | Agent/Platform approval·audit authority, MCP target/precondition 재검증·operation journal, 아직 MCP 소유 `xg5000_control`에 남은 legacy-native backend 전체 |

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

격리된 offline change의 working-copy SHA가 journaled artifact와 달라진 경우,
Platform은 hash를 재결속하거나 파일을 직접 쓰지 않는다. 사용자가 현재 SHA를 검토하고
명시적으로 `restoreVerifiedBackup`을 요청한 경우에만, 같은 recovery lease 아래 MCP가
현재 바이트를 private preservation artifact로 보존하고 journal-pinned backup을 exact
path에 복원할 수 있다. MCP receipt는 native/Host transaction ID, plan/workspace, current·
preserved·backup·restored hash와 preservation reference/digest를 결속한다. 그 뒤에도
기존 native relaunch/readback/rollback 경로가 독립적으로 성공해야 quarantine이 해제된다;
이 동작은 PLC download·online write·기존 change approval 재사용을 포함하지 않는다.

## 버전화된 경계

- HTTP/Event/Workspace/Audit/Agent/MCP Product v1 schema의 source of truth는
  Platform `schemas/`다. Python/TypeScript `controlstudio-contracts`는 DTO와
  validation만 제공한다.
- Agent Protocol은 NDJSON stdio `1.0`이다. Platform은 initialize manifest의
  protocol version/capabilities를 검사하고 stdout의 protocol framing만 신뢰한다.
  장시간 model request 중 `run.progress`는 15초 heartbeat로 transport 생존만
  증명하며, 계획 완료·도구 성공·mutation 증거로 사용하지 않는다.
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

## 패키징·실기 범위

공식 Platform/Agent/MCP는 세 core service artifact로 각각 빌드한다. 별도 vendor CLI
repository는 이관된 명령에 독립 executable/process 경계를 제공하지만, 아직 모든 native
backend의 독립 artifact·release lifecycle을 의미하지는 않는다. 현재 BOM은 세 core
artifact의 version, SHA-256, 여섯 contract version, signature metadata를 정확히
결속한다. 별도 CLI artifact를 BOM·Launcher release verification에 포함하는 통합은
아직 pending이다. unsigned/development HMAC artifact는 production 배포물이 아니다.

실제 XG5000·XG-SIM·controller 작업은 현재 target identity, capability, operation
journal과 native readback이 모두 갖춰진 경로만 연다. PLC write, force, mode change,
download 또는 run edit이 이 조건을 충족하지 못하면 계속 fail-closed다.
