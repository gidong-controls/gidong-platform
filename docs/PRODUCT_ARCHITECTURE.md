# GIDONG 제품 아키텍처

저장소 간 공통 계약은 [루트 ARCHITECTURE](../../ARCHITECTURE.md)를 따른다. 이 문서는 구성 요소의 상세 구현을 설명하며, 현재 진행 상태는 [HANDOFF](../../HANDOFF.md)에서 관리한다.

최종 갱신: 2026-10-11
결정 상태: 실제 대형 XG5000 LD의 제어 구조 타당성을 인수했으며 이 구조로 제품 개발을
진행한다. Agent가 실제 관측에 근거해 조사·설계·부분 수정·저장·재개를 판단하고,
Platform이 작업공간과 대화 실행을 연결하며 MCP/벤더 CLI가 실제 편집 동작을 수행한다.
기존 작업본의 native 실행은 sidecar/broker에 연결돼 있다.

제품 기능·안전·출시 범위는 [제품 PRD](../PRD.md)가 우선한다. 이 문서는 공개
Platform과 비공개 Agent/PLC MCP가 결합되는 실행 책임을 고정하며, 실제 PLC
online 기능을 완료로 선언하지 않는다. 이 문서와 다른 오래된 설계·상태 문서는 작성
시점의 기록이다. 구현 상태는 현재 source/runtime evidence를 기준으로 확인하되,
그 evidence가 아직 구현되지 않은 기능을 제품 요구 범위에서 제외하는 근거는 아니다.
최신 사용자 요구는 오래된 PRD·설계의 요구보다 우선한다.

## 선정한 LD 실행 구조

1. XG5000의 열린 프로그램에서 네이티브 클립보드 형식 `XG5000LADDER`의 원시 데이터를 읽고 출처·프로그램·범위와
   원시 자료를 보관한다. 구조 지도와 부분 상세는 이 관측에서 파생한다.
2. Agent는 작은 구조 지도를 읽고 필요한 요소·행·주소·명령을 로컬 조회한다. 같은 캡처의
   상세를 조회할 때에는 저장된 결과를 읽는다. 모델이 현재 상태가 필요하다고 판단하면
   native 읽기를 선택한다.
3. 모델이 관측에 속한 요소·포트 참조와 수정 내용을 도구 호출로 지정한다. 실행기는 이를
   native 커서 위치·편집 명령·입력 필드 동작으로 해석한다. 회로 설계와 수정 판단은 모델에 있다.
4. 사전 해석 가능한 지원 편집은 순서 있는 묶음으로 실행한다. 결과는 완료된 변경, 실패·불명확한 변경,
   미시작 변경과 실제 관측을 구분한다. 위치를 바꾸는 편집 뒤에는 갱신한 관측을 사용한다.
5. 완료한 도구 요청·결과와 작업 진행을 다음 모델 호출 전에 저장한다. 저장·취소·재개도
   같은 작업공간과 대화 이력에서 이어간다. 도구 결과는 모델과 사용자 화면에 연결한다.

현행 도구 계약은 [구현 계획](AGENT_PRODUCT_PLAN.md)에 둔다.
source 참조는 해당 관측 안에서 유효하다. 모델의 요청과 지원되는 native 동작을 연결하는
공통 계약을 유지하고, 벤더별 위치 선택·편집·저장은 해당 어댑터가 구현한다.

## Agent·MCP·벤더 CLI의 선정 이유

선정한 구조는 `모델의 도구 선택 → Host의 MCP client → 얇은 MCP adapter → IDE별 CLI/backend`
이고 실제 결과가 같은 모델 대화로 돌아온다. MCP가 계획을 세우거나 정해진 제작 순서를
진행하는 것이 아니다. 에이전트의 핵심은 관측·판단·도구 실행·결과를 반복하는 모델 루프다.

CLI의 분리 단위는 개별 PLC 프로젝트가 아니라 XG5000, GX Works, Studio 5000 같은
engineering IDE 계열이다. IDE 버전·CPU별 차이는 그 adapter의 지원 명령과 capability로
표현한다. 모델에는 공통 읽기·참조 편집·저장 의미를 주고, native 형식·창·커서·명령 처리는
각 adapter가 맡는다. 현재는 LS 실행 명령만 설정하며 다른 브랜드·버전의 자동 선택은
구현되지 않았다. 후속 벤더도 같은 UI 구현이나 XG 좌표를 사용한다는 뜻이 아니다.

| 비교한 연결 방식 | 유용한 경우와 비용 | ControlStudio 선택 |
| --- | --- | --- |
| Agent가 shell로 CLI 직접 호출 | 범용 코딩 작업에 조합하기 쉽고 필요한 명령 설명만 읽을 수 있음. 모델이 shell 문법·경로·출력 처리까지 맡음 | CLI는 독립 실행 가능하게 유지하되 제품 모델의 기본 인터페이스로 raw shell을 주지는 않음 |
| Agent runtime의 typed function이 backend 직접 호출 | 별도 protocol/process 없이 단순하게 연결 가능. runtime과 Windows 실행·배포가 결합됨 | 작은 단일 앱의 유효한 대안. 현재 Host의 작업공간·권한·프로세스 경계를 없앨 이점은 확인되지 않음 |
| Host/MCP가 typed tool을 IDE별 CLI/backend에 연결 | 에이전트와 IDE 구현을 독립 교체하고 다른 MCP client에서도 재사용 가능. schema·직렬화·process 비용을 관리해야 함 | 기존 경로를 유지. backend 구현을 중복하지 않고 현재 작업에 필요한 도구·간결한 결과만 모델에 전달 |

공개 구현과 현장 사용 기록을 함께 비교했다(2026-10-11 열람).

- [mini-swe-agent 실제 루프](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py)는 모델 호출과 환경 실행을 분리하고 결과를 대화에 넣으며 진행을 저장한다. MCP나 별도 planner service가 에이전트의 필수 요소는 아니다.
- [Pi 제작자의 구현 회고](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)는 작은 도구 집합과 필요할 때 읽는 CLI 설명을 선호한다. 2025-11-30 당시의 설계 의견이며, 현재 Pi의 모든 기능이나 PLC 성능을 증명하는 자료로 사용하지 않는다.
- [Playwright 구현자의 CLI/MCP 비교](https://github.com/microsoft/playwright-mcp/blob/main/README.md)는 코딩 에이전트의 CLI와 구조를 반복 관측하는 전용 MCP 루프를 구분한다. 어느 한 transport가 항상 우월하다는 결론이 아니다.
- [Playwright MCP 사용 이슈 #1329](https://github.com/microsoft/playwright-mcp/issues/1329)는 매번 큰 snapshot을 반환할 때의 문맥 팽창과 선택적 읽기의 비용을 보여 준다. 따라서 전체 LD 원문은 보관하고 모델에는 지도·필요한 상세를 주는 현재 선택을 유지한다.
- [MCP host/client/server 구조](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture)에서 실제 연결은 host의 client가 담당한다. 제품에서도 모델이 MCP transport를 직접 조작하지 않는다.

위 비교를 적용한 설계 판단은 **작은 모델용 의미 단위 도구 + 보관된 관측의 부분 조회 +
지원 편집 묶음 + 동일 실행 backend**다. MCP를 쓰는 것 자체가 작은 문맥이나 빠른 실행을
보장하지 않는다. 도구 설명·결과·모델 왕복과 실제 IDE 입력 비용을 줄이는 것이 핵심이다.
별도 범용 graph compiler, 새 planner, 모든 동작의 반복 전체 읽기는 추가하지 않는다.

현재 CLI와 UIA helper는 명령별 subprocess이고 MCP 연결과 batch 내부 준비는 재사용한다.
CLI 호출마다 IDE를 다시 띄우는 구조는 아니다. 상주 worker는 실제 process 시작 비용이
병목인 경우 같은 명령 계약 아래 선택할 최적화이며, 제품 구현의 선행 과제가 아니다.

## 목표와 현재 이행 구조

제품 중심은 채팅에서 실행되는 Agent loop다. 현재 code path와 변경 파일은
[구현 계획](AGENT_PRODUCT_PLAN.md)을 따른다. 기존 service 경계를 재사용하며 새 planner,
mission engine, validator service를 추가하지 않는다. receipt·approval 구현은 전환 대상이
포함된 현재 상태이지 모든 요청에 강제할 제품 요구가 아니다.

현행 native 대화의 모델 루프·참조 도구·진행 복원은 Agent의 `workspace_conversation.py`,
`native_editor.py`, `native_workspace.py`에 있다. Host의 `NativeWorkspaceExecution`이
작업공간·실제 대상을 결속하고, 기존 sidecar의 Host 도구 요청을 broker로 전달한다.
일반 `engineering` 요청 중 native 편집 범위는 이 경로를 사용한다. 신규 `new_project_planning`
요청도 실행 범위와 준비 상태에 따라 `NativeProjectPreparation`에서 대상 생성·연결을
진행하고 같은 native 실행 루프로 이어진다. 상담·계획만 요청한 경우에는 조사와 brief
응답 경로를 사용한다. 실행별 인수 범위는 [HANDOFF](../../HANDOFF.md), 다음 작업의
완료 조건은 [구현 계획](AGENT_PRODUCT_PLAN.md)을 따른다.

```text
사용자 → Windows Launcher → Platform Host / FastAPI → Web/PWA
                                  │
                    conversation · run · event · workspace · request scope
                                  │
                           ExecutionBroker
                         ┌────────┴────────┐
             Agent Protocol v1          MCP stdio + Product Contract v1
                       │                           │
      private gidong-agent            private gidong-plc-mcp
       Pydantic AI / model / RAG       vendor action / operation journal
       tool selection/result loop      tool contract / structured result
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
Agent는 CLI를 직접 호출하지 않고 MCP tool action을 선택해 Host에 실행을 요청한다. separate CLI는
모델 권한 경계가 아니라 독립적·결정적인 vendor adapter/API transport 경계다. 따라서
vendor별 process·JSON request/receipt·timeout을 MCP protocol과 분리할 수 있다. 아직
모든 MCP vendor route가 CLI로 이관된 것은 아니며, 이관되지 않은 route의 책임·검증
경계도 MCP에 남는다. CLI에는 process/app helper, wire, catalog/identity, LD reader/editor와
native 커서·구조 편집 소스가 이관돼 있다. 다만 fast-entry `open_project_entry.py`도 MCP의
`project_launcher`를 import하며 `legacy.py`에는 변경·검사·저장·재열기 역의존이 남아 있다.
따라서 별도 Git/process 경계는 존재하나 native backend 전체 독립은 미완료다.

## 컴포넌트 책임

| 컴포넌트 | 소유·수행 | 소유하지 않는 것 |
| --- | --- | --- |
| `gidong-platform` (public) | Launcher, UI, HTTP/SSE, 사용자 원본·workspace/working copy, conversation/run/approval/audit, Agent/MCP supervision와 MCP client, `ExecutionBroker`, 공개 schemas/SDK | PLC 설계 판단, vendor UI/native payload 구현, 실제 도구 결과와 다른 성공 상태 생성 |
| `gidong-agent` (private) | 모델 대화·PLC 설계·도구 선택, source 참조와 구조 지도·부분 조회, 실제 결과 소비와 작업 진행 복원, model adapter·파생 지식 | raw MCP 연결·벤더 UI 조작, 실행 권한·원본 문서의 authority |
| `gidong-plc-mcp` (private) | MCP Product Contract 구현, vendor adapter, target/precondition 재검증, operation lock/journal, apply/check/readback/rollback/reconciliation | Agent/model, Platform UI/HTTP, 사용자 전체 대화·원본 문서 authority |
| `controlstudio-xg5000-cli` (separate vendor repository) | subprocess/JSON 경계, instances/idle/list/focus/read, native editor·LD 커서·입력창·구조 편집·readback | Agent의 PLC 설계, Platform 요청 권한, MCP journal; 남은 launcher·legacy command 소유권은 별도 이관 범위 |

이 표는 소유권 기준이며 분리 완료를 뜻하지 않는다. 현재 Agent의 참조 변환에는 XG 전용
opcode·좌표·배선 분해가, Host의 저장 파일 관측에는 XGWX 해석이 남아 있다. MCP에도
CLI-owned vendor helper를 직접 호출하는 호환 경로가 있다. 정상 `xg5000.editor` subprocess에
실패 시 in-process fallback이 있다는 뜻은 아니다. 정리할 파일·공개 action 계약과 다음
소비자 변경은 [구현 계획](AGENT_PRODUCT_PLAN.md)에 명시한다.

세부 ownership과 데이터 흐름은
[RESPONSIBILITY_AND_DATA_OWNERSHIP.md](refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md)를
따른다.

## 실행·권한 경계

Agent의 tool call은 실행 권한이 아니라 이름과 arguments의 제안이다. Platform은
Agent alias만 노출하고 raw target identity, controller endpoint, approval material을
모델-facing schema에서 제거한다. `ExecutionBroker`가 run·workspace·target을
다시 결합하고 allowlist/schema/manifest/capability를 확인한 뒤 결과를 audit/event로
기록한다.

목표 동작에서 Host는 사용자가 요청한 작업 사본과 변경·저장 범위를 기록하고, 그 범위의
후속 도구에 권한을 전달한다. 후보만 요청하면 변경하지 않는다. 추가 확인은 새 범위나
되돌리기 어려운 작업에 필요한 경우 받는다. 모델은 사용자 권한을 임의로 확대할 수 없다.

현재 native 편집은 Host가 사용자 요청의 변경 범위와 작업 대상을 연결해 실행한다.
아래 exact plan/digest/nonce·quarantine 설명은 남아 있는 기존 proposal transaction의
복구 경로다. 해당 거래의 상태를 복구하는 절차이며 일반 native 편집의 필수 순서가 아니다.
대상 확인·작업 사본 보호·불명확 입력의 중복 실행 방지는 실제 도구 결과에서 처리한다.

기존 구현에서 격리된 offline change의 working-copy SHA가 journaled artifact와 달라진 경우,
Platform은 hash를 재결속하거나 파일을 직접 쓰지 않는다. 사용자가 현재 SHA를 검토하고
명시적으로 `restoreVerifiedBackup`을 요청한 경우에만, 같은 recovery lease 아래 MCP가
현재 바이트를 private preservation artifact로 보존하고 journal-pinned backup을 exact
path에 복원할 수 있다. MCP receipt는 native/Host transaction ID, plan/workspace, current·
preserved·backup·restored hash와 preservation reference/digest를 결속한다. 그 뒤에도
기존 native relaunch/readback/rollback 경로가 독립적으로 성공해야 quarantine이 해제된다;
이 동작은 PLC download·online write·기존 change approval 재사용을 포함하지 않는다.

이 복구 분기는 기존 미확정 작업을 다루는 호환 경로이며 모든 새 대화의 선행 조건이 아니다.

## 실시간 도구 결과

모델의 실제 선택 → Host dispatch 시작 → MCP/CLI 반환을 같은 run/toolCallId로 연결한다.
성공·오류 결과 모두 Agent의 다음 모델 호출과 Web에 전달한다. 기존 SSE/Agent 프로토콜을
사용하고 별도 이벤트 버스는 만들지 않는다. heartbeat는 실행 결과가 아니다.
카드에는 역할·대상·상태·경과 시간·짧은 결과를 표시하고 상세 인자/진단은 접는다.
독립 재열기·XG-SIM·Keep를 모든 save에 강제하는 흐름은 요청별 도구로 분리한다.

## 버전화된 경계

- HTTP/Event/Workspace/Audit/Agent/MCP Product v1 schema의 source of truth는
  Platform `schemas/`다. Python/TypeScript `controlstudio-contracts`는 DTO와
  validation만 제공한다.
- 모델의 reference-tool catalog와 Host의 sidecar 실행 capability는 서로 다른 경계다.
  현재 native primitive action 이름·필드는 Agent/Host에서 수동 동기화한다. 새 action을
  연결할 때 공개 versioned DTO/catalog로 맞추며 Host가 Agent private schema를 import하지 않는다.
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

Platform run journal, Agent checkpoint와 실제 실행 결과의 역할은 다르다. native checkpoint는
모델 계획뿐 아니라 실제 도구 반환·완료 prefix·관측 참조를 보존한다. 변경 사실의 근거는
그 안의 실제 실행 결과와 관측이며 모델의 완료 문장이 아니다. 불명확 입력은 불명확한 채로
이어받고 필요한 현재 상태를 읽는다. 별도 MCP operation journal을 쓰는 legacy 거래의
crash reconciliation은 해당 journal을 기준으로 한다. 이를 모든 native 편집에 강제하지 않는다.

## 패키징·실기 범위

공식 Platform/Agent/MCP는 세 core service artifact로 각각 빌드한다. 별도 vendor CLI
repository는 이관된 명령에 독립 executable/process 경계를 제공하지만, 아직 모든 native
backend의 독립 artifact·release lifecycle을 의미하지는 않는다. 현재 BOM은 세 core
artifact의 version, SHA-256, 여섯 contract version, signature metadata를 정확히
결속한다. 별도 CLI artifact를 BOM·Launcher release verification에 포함하는 통합은
아직 pending이다. unsigned/development HMAC artifact는 production 배포물이 아니다.

오프라인 native 편집은 결속한 작업 사본에서 실행하고 실제 입력·오류·관측·저장 결과를
반환한다. XG-SIM은 모델이 선택하는 별도 도구이며 매 편집의 선행 조건이 아니다.
물리 PLC write, force, mode change, download 또는 run edit은 대상·사용자 권한과 해당
장비의 실행·복구 계약을 갖춘 별도 capability에서만 제공한다. 오프라인 LD 성공으로
물리 장비 변경 권한이나 구현 완료를 추정하지 않는다.
