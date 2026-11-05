# 책임과 데이터 소유권

현행 실행 경계 갱신: 2026-10-11

이 문서는 [제품 아키텍처](../PRODUCT_ARCHITECTURE.md)와
[제품 배포·버전 정책](PRODUCT_DISTRIBUTION_AND_VERSIONING.md)의 실행 경계를
코드·test·release 검토에 적용하는 기준이다. Platform은 공개,
`gidong-agent`와 `gidong-plc-mcp`는 비공개다. 이 구분은 PRD의
기능·안전 범위를 축소하지 않는다.

## 책임 분리

실행 경로는 `Agent → Platform/ExecutionBroker → MCP → 벤더 CLI → 벤더 프로그램`이다.
별도 `controlstudio-xg5000-cli` 저장소는 LS XG5000의 프로세스·프로젝트·LD 조작을
명령별 계약으로 제공한다. 정상 호출은 canonical UTF-8 JSON을 stdin으로 전달한다.
MCP는 제품 도구 계약, 대상 결속 및 작업 기록을 소유하고, CLI는 전달받은 대상에서
벤더 작업을 수행한다. CLI의 분리 단위는 개별 PLC 프로젝트가 아니라 engineering IDE
계열이다. 이 분리는 후속 CLI를 추가해도 Agent가 창 클래스·native 좌표·입력 구현을
알 필요 없게 하려는 것이다. 현재 LS 실행 명령 설정과 향후 IDE/버전 매칭은 구분한다.

현재 CLI 실행 경계는 분리됐지만 벤더 코어 일부는 MCP 저장소에서 가져오는 이행
단계다. 아래 MCP의 벤더 구현 소유권은 이 남은 코드까지 포함한 현재 상태이며,
전체 소스 추출 완료를 뜻하지 않는다. CLI 추출이 사용자 승인·workspace 소유권을
CLI로 옮기거나 새 사용자 승인 단계를 추가하는 것은 아니다.
Agent에는 XG 전용 참조→primitive 계산이, Host에는 XGWX 저장 파일 codec이 남아 있다.
아래 표는 소유권 기준이며 이 잔여까지 분리 완료했다는 뜻은 아니다. 실제 변경 파일과
다음 action의 계약 정리는 [구현 계획](../AGENT_PRODUCT_PLAN.md)를 따른다.

| 경계 | Platform | Agent | PLC MCP |
| --- | --- | --- | --- |
| 사용자 경험 | Launcher, Web/PWA, HTTP/SSE와 실제 결과의 공개 표시 | 모델의 조사·설계·도구 선택·수정·최종 설명 | 없음 |
| 프로세스 | Agent/MCP 시작·manifest/health·종료 감독 | supervised NDJSON sidecar | supervised MCP stdio server |
| 실행 권위 | `ExecutionBroker`, allowlist, target binding, approval, audit | `ExternalToolRequest` 제안만 | 실제 tool 실행 전 재검증 |
| 작업 진행 | 요청 범위·작업본/IDE binding·run/event·native checkpoint 보존 | 캡처 지도·상세/참조·모델 계획·완료 도구 이력 | 실제 입력·실패·관측·저장 결과. legacy 거래는 별도 journal/rollback/quarantine |
| 벤더/PLC | 대상·파일 authority, native UI 실행 없음. 남은 저장 파일 codec은 vendor로 이관 | source/ref 의미와 모델 도구, raw MCP/PID/HWND/endpoint 없음. 남은 XG 위치·배선 계산은 adapter로 분리 | XG5000/XG-SIM/FEnet 및 향후 adapter 연결; native 구현은 CLI로 이관 중 |
| 모델/RAG | credential lifecycle와 원본 source 전달 정책 | Pydantic AI, prompt, model adapter, derived index/ranking/eval | 없음 |

Platform이 owning하는 `ExecutionBroker`는 model-facing alias를 raw MCP method로
매핑한다. Agent가 제안한 risk/capability 또는 “완료” 설명은 권위가 아니다.
bound native run은 `NativeWorkspaceExecution`의 catalog와 owner가 처리한다.
기존 manifest 도구에서는 Platform이 tool schema digest, required capability,
risk/side-effect, target-binding/approval requirement를 확인한다. MCP도 동일
precondition을 스스로 확인하므로 Platform만 통과해도 mutation이 허용되지 않는다.

## 요청 권한과 대상 결속

기존 작업본의 native 실행은 Host의 `engineering + permanent_offline_revision +
offline_native_check_only` 분기에서 이미 연결돼 있다. Host가 descriptor와 실제 대상을
결속하고 broker·sidecar·Agent의 참조 도구 실행을 잇는다. raw 대상은 사용자가 작성하지 않는다.

제품에서는 명확히 요청한 작업 사본 수정·저장의 범위를 Host가 보존하고 후속
도구 실행에 전달한다. 후보만 요청하면 적용하지 않는다. 새로운 범위나 되돌리기 어려운
작업에 필요한 추가 확인과 기존 범위의 반복 승인 요청을 구분한다. 정상 대화·읽기는
변경 거래/Keep/복구의 선행 조건을 갖지 않는다. 실제 전환 파일은
[구현 계획의 실행·변경 경계·§5](../AGENT_PRODUCT_PLAN.md)를 따른다. 현재 Web의 scope reset과
mission 전용 재개 버튼은 native 후속 요청·재개로 연결할 잔여다.

아래는 **현재 legacy mutation 구현의 내부 계약**이다. 호환 경로를 다룰 때 참조하되
모든 요청에 새 사용자 승인 화면을 요구하라는 제품 지시로 사용하지 않는다.

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

기존 legacy approval 뒤 target·plan·arguments가 바뀌면 그 기존 claim을 재사용할 수 없다.
요청 범위 내 후속 동작의 내부 권한 전달과 사용자에게 새로 확인할 상황은 전환 구현에서
구분한다. Agent는 secret/signature를 보유하거나 권한을 임의 생성하지 않는다.
`confirm: true` 같은 caller flag로 실제 권한을 대신하지 않는다.

## journal과 상관관계 ID

### 현재 native 대화

Host는 요청·작업본·IDE owner, run/event, 저장 파일 관측과 native checkpoint의
연결을 소유한다. Agent checkpoint는 모델 계획·메시지, 보관된 capture·source 참조,
실제 도구 입력 결과와 reader-only 진행까지 포함한다. 저장된 관측은 당시 상태이며
현재 buffer나 저장 파일 상태를 대신하지 않는다.

실행기는 완료 prefix·실패/unknown·미시작 suffix를 반환한다. 모델로 보내기 전에 이
결과를 보존하고, 재개 시 이미 완료한 결과를 재주입한다. 미완료 입력을 맹목적으로
재발행하거나 참조를 새 행 위치로 자동 rebasing하지 않는다. 실제 현재 상태가 필요하면
모델이 native 읽기를 선택하고, 받은 자료의 상세는 추가 GUI 읽기 없이 조회한다.

`request_id`/tool-call ID는 요청과 실제 결과를 연결한다. 입력 완료, 회로 기능,
저장 성공과 사용자 작업 완료는 서로 다른 사실이다. Web에는 실제 결과의 공개 요약을
투영하고 private native payload·credential·대상 내부 정보를 그대로 출력하지 않는다.

### 기존 승인 거래의 journal

아래 mission/approval/MCP operation journal 규칙은 기존 거래를 다루는 계약이다.
native 참조 대화의 모든 도구 결과를 이 거래 상태로 변환하는 지침이 아니다.

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

native 실행은 현재 workspace/IDE에 결속한 owner를 재사용하며 요청 범위와 실제
결과를 기록한다. 저장으로 hash가 달라지는 정상 변경과 대상 교체를 구분한다.
기존 거래의 operation journal과 물리 PLC capability는 각각 해당 경로의 계약이다.
과거 기록만으로 현재 target 성공을 추정하지 않는다.
strong controller identity, project/parameter readback, online write, force, mode
change, download와 run edit이 이 조건을 충족하지 못하면 계속 fail-closed다.
