# 책임과 데이터 소유권

최종 갱신: 2026-08-31

이 문서는 [제품 아키텍처](../PRODUCT_ARCHITECTURE.md)와
[제품 배포·버전 정책](PRODUCT_DISTRIBUTION_AND_VERSIONING.md)의 실행 경계를
코드·test·release 검토에 적용하는 기준이다. Platform은 공개,
`gidong-agent`와 `gidong-plc-mcp`는 비공개다. 이 구분은 PRD의
기능·안전 범위를 축소하지 않는다.

## 책임 분리

실행 경로는 `Agent → Platform/ExecutionBroker → MCP → 벤더 CLI → 벤더 프로그램`이다.
별도 `controlstudio-xg5000-cli` 저장소는 LS XG5000의 프로세스·프로젝트·LD 조작을
명령별 JSON 계약으로 제공한다. MCP는 제품 도구 계약, 대상 결속 및 작업 journal을
소유하고, CLI는 전달받은 대상에서 벤더 작업을 수행한다. 이 분리는 향후 브랜드별
CLI를 추가해도 Agent가 창 클래스나 COM 구현을 알 필요 없게 하려는 것이다.

현재 CLI 실행 경계는 분리됐지만 벤더 코어 일부는 MCP 저장소에서 가져오는 이행
단계다. 아래 MCP의 벤더 구현 소유권은 이 남은 코드까지 포함한 현재 상태이며,
전체 소스 추출 완료를 뜻하지 않는다. CLI 추출이 사용자 승인·workspace 소유권을
CLI로 옮기거나 새 사용자 승인 단계를 추가하는 것은 아니다.

| 경계 | Platform | Agent | PLC MCP |
| --- | --- | --- | --- |
| 사용자 경험 | Launcher, Web/PWA, HTTP/SSE와 인증 | 모델 응답을 위한 analysis | 없음 |
| 프로세스 | Agent/MCP 시작·manifest/health·종료 감독 | supervised NDJSON sidecar | supervised MCP stdio server |
| 실행 권위 | `ExecutionBroker`, allowlist, target binding, approval, audit | `ExternalToolRequest` 제안만 | 실제 tool 실행 전 재검증 |
| 변경 안전 | request/approval/result를 durable run journal에 기록 | 계획·설명·구조화 output | lock, write-ahead operation journal, apply, readback, rollback, quarantine |
| 벤더/PLC | vendor implementation을 import하지 않음 | raw MCP/PID/HWND/endpoint 없음 | XG5000/XG-SIM/FEnet 및 향후 adapter |
| 모델/RAG | credential lifecycle와 원본 source 전달 정책 | Pydantic AI, prompt, model adapter, derived index/ranking/eval | 없음 |

Platform이 owning하는 `ExecutionBroker`는 model-facing alias를 raw MCP method로
매핑한다. Agent가 제안한 risk/capability 또는 “완료” 설명은 권위가 아니다.
Platform은 Product Contract manifest에서 tool schema digest, required capability,
risk/side-effect, target-binding/approval requirement를 확인한다. MCP도 동일
precondition을 스스로 확인하므로 Platform만 통과해도 mutation이 허용되지 않는다.

## 승인과 대상 결속

1. Agent는 run-scoped `ExternalToolRequest`(alias, arguments, request/tool-call ID)를
   Platform에 보낸다.
2. Platform은 current workspace/working copy, target identity, Tool Contract,
   allowlist와 arguments schema를 확인한다. 모델에는 raw PID/HWND/path/hash,
   controller endpoint, approval material을 주지 않는다.
3. mutation이면 사용자가 exact plan과 arguments를 확인한다. Platform approval은
   `approval_id`, actor, `run_id`, `target_id`/target digest, tool,
   arguments digest, plan digest, capability, issued/expiry, nonce와 single-use를
   결합한다.
4. `ExecutionBroker`만 MCP 호출 직전에 승인 claim과 validated target context를
   주입하고 Platform audit/event를 남긴다.
5. MCP는 target, tool, args/plan digest, capability, expiry 및 nonce를 다시
   검증한 뒤 journal을 기록하고 실행한다. 불일치, stale target, 소모된 nonce,
   애매한 readback은 fail-closed다.

approval 뒤 target·plan·arguments가 하나라도 바뀌면 새 승인이 필요하다. Agent는
secret/signature를 저장하거나 받을 수 없다. `confirm: true` 같은 caller flag는
승인을 대체하지 않는다.

## journal과 상관관계 ID

| 기록 | 권위 소유자 | 내용 | side effect 결론 |
| --- | --- | --- | --- |
| Platform run journal/audit | Platform | 대화, run, proposal, approval 결정, broker invocation/result, 사용자 표시, artifact reference | 사용자 workflow·승인의 권위이나 vendor mutation 결과를 자체 확정하지 않음 |
| Agent checkpoint | Agent | 모델 message, retrieval/result, deferred tool request/result, 재개 가능한 analysis | 실제 PLC/project 변경 성공의 권위가 아님 |
| MCP operation journal | PLC MCP | target snapshot/hash/payload, plan/args digest, lock, apply, readback/check, rollback, terminal/quarantine evidence | mutation/reconciliation의 권위 |

Agent checkpoint의 직렬화 형식은 Agent가 소유하고 Platform은 bounded opaque payload와
digest, mission/target/run linkage만 내구화한다. 완료된 cycle의 model/tool history는
재주입할 수 있지만, crash 시점의 미완료 tool request는 자동 재발행하지 않는다.
read-only 결과는 Host evidence에서 다시 확인하고 mutation 결과는 MCP journal로만
reconcile한다.

세 record는 가능한 모든 event/receipt에서 `trace_id`, `conversation_id`, `run_id`,
`agent_step_id`, `tool_call_id`, `operation_id`, `approval_id`, `target_id`와 연결한다.
`request_id`는 Agent Protocol request/response 상관관계, `event_id`와 `sequence`는
Platform event replay 순서에 사용한다. ID가 누락된 mutation receipt는 성공으로
승격하지 않는다. crash 또는 응답 유실 뒤에는 동일 변경을 자동 재실행하지 않고
MCP journal reconciliation 결과가 `COMMITTED`, `ROLLED_BACK_SAVED`,
`ABORTED_NO_MUTATION` 또는 `QUARANTINED` 중 무엇인지 확인한다.

## 원본과 파생 지식

Platform은 사용자 원본 문서·프로젝트, workspace working copy, capture hash,
metadata, source revision, citation, confirmation status와 audit를 소유한다. 사용자의
원본은 Agent cache 또는 index가 아닌 Platform record에서 보존·삭제·업그레이드
정책을 적용한다.

Agent는 Platform이 허용하고 sanitize하여 `knowledge.sync`로 전달한 source snapshot을
받을 수 있다. chunking, token statistics, embedding/retrieval/ranking, prompt context와
model inference는 Agent private derived data이며 재생성 가능해야 한다. Agent result는
`knowledge://` source reference와 chunk ID를
반환할 수 있지만 Platform 원본 또는 confirmation 상태를 변경할 권한은 없다.

검색 문서, tool result, model output, provider error는 모두 비신뢰 입력이다. 권한,
approval, target binding, PLC online identity를 만들거나 safety gate를 완화할 수
없다. API key/custom header와 실제 endpoint는 public contract, Platform audit/event,
Agent stdout protocol 및 public Git history에 기록하지 않는다.

## 공개 계약과 visibility

Platform `schemas/`가 HTTP/Event/Agent/MCP Product/Workspace/Audit v1의 source of
truth다. `controlstudio-contracts` Python/TypeScript package에는 versioned DTO와
validation만 둔다. private Agent prompt/RAG policy 또는 private MCP
source/profile/vendor native payload는 public package나 history에 넣지 않는다.

- **Agent Protocol v1 (`1.0`)**: Platform↔Agent NDJSON stdio. initialize manifest,
  health, run start/cancel/progress, tool request/result, knowledge sync/search,
  final/error가 version/capability handshake 아래 동작한다. `run.progress`는
  생존 heartbeat일 뿐 결과·checkpoint 또는 side effect 증거가 아니다.
- **MCP Product Contract v1 (`1.0`)**: MCP wire와 별도인 tool manifest/schema
  digest·capability·risk·side-effect·approval/target policy·evidence/timeout contract다.
  Platform은 exact wire implementation version이 아니라 required product-contract
  major와 digest compatibility를 검사한다.
- **HTTP/Event/Workspace/Audit v1**: OpenAPI snapshot, event envelope, immutable
  source/working-copy identity, append-only sanitized audit contract다. required HTTP
  field 제거 또는 추가는 major/snapshot 갱신 없이 허용되지 않는다.

Platform은 설치된 공식 Agent와 MCP를 child process로만 실행한다. private source를
import하지 않으며 target identity, approval secret과 model credential은 각 소유
경계를 넘어 전달하지 않는다. 호환성은 initialize manifest와 versioned protocol,
Product Contract digest로 판단한다.

## 현재 PLC evidence 경계

각 실행은 현재 workspace, exact PID/HWND, project hash, capability와 operation
journal을 새로 결속해야 한다. 과거 기록만으로 현재 target 성공을 추정하지 않는다.
strong controller identity, project/parameter readback, online write, force, mode
change, download와 run edit이 이 조건을 충족하지 못하면 계속 fail-closed다.
