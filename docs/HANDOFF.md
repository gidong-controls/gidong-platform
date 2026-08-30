# Development handoff

이 문서는 새 세션이 현재 `dev` 상태에서 바로 이어가기 위한 운영 기준이다. 제품 요구의
최상위 권위는 [PRD](../PRD.md)이며, 이 문서는 구현 상태와 다음 검증 순서만 기록한다.

코딩 에이전트 제품과 PLC 커뮤니티 벤치마킹에서 도출한 작업·실기 평가 기준은
[코딩 에이전트·PLC 엔지니어링 벤치마크](CODING_AGENT_BENCHMARK.md)의 CA-01~CA-12를
사용한다. 일반 SWE-bench나 모델 점수는 XG5000 변경 성공 증거를 대신하지 않는다.
[PLC 엔지니어링 Agent acceptance 하네스](PLC_AGENT_ACCEPTANCE.md)는 Agent, Host, MCP,
독립 CLI와 Web build를 한 매니페스트로 실행하고 JSON 영수증을 만든다. 2026-08-25의
deterministic-only 실행은 모두 통과했지만 CA-01/02/04~07/12 실기 영수증이 없어 전체
상태는 의도대로 `FAIL_CLOSED`다.

## 저장소와 브랜치

- `gidong-platform`: `dev` @ `153f3e14757ba083185382fcfcf9b80e6fd87647` (local checkpoint)
- `gidong-agent`: `dev` @ `c4bce7f15d67cc936afa3088f88547870d43ad8a` (local checkpoint)
- `gidong-plc-mcp`: `dev` @ `80b8f70da4dda6f74f679a117c56ace3d54b3807` and
  `24e1085` (local checkpoints)
- 독립 XG5000 CLI는 사용자 지정 원격에 `6a78ac8a6e6bb7c41236aa32abef4f93b16bc10d`를
  게시했다. Platform, Agent, MCP는 이 checkpoint 시점에 push하지 않았다.
- 세 저장소의 `main`은 작업 시작 기준 commit에 그대로 두었다. 개발은 `dev`에서만 계속한다.

2026-08-16 10:20 KST의 GitHub 443 거부와 당시 push 재시도는 역사적 기록이다. 이후
push 여부는 사용자별 권한과 원격 상태를 확인한 뒤에만 결정하며, 다음 세션의 자동 작업이 아니다.

## 현재 제품 경계

- 제품은 단발 chatbot이 아니라 Host가 mission, checkpoint, evidence, change transaction을
  소유하고 Agent가 bounded multi-cycle tool loop를 수행하는 엔지니어링 Agent다.
- Agent는 분석 evidence를 만들고 변경 후보를 제안한다. Host가 사용자 승인과 권한을
  소유하며 MCP만 XG5000/PLC 실행과 readback journal을 소유한다.
- 기존 LD 구조 변경은 typed MNE candidate, 전체 대상 digest, Program Check, save,
  independent reopen, rollback receipt에 결속된다. 선언된 XG-SIM 범위는 관찰 receipt 뒤
  사용자의 keep/revert 결정까지 같은 transaction에서 처리한다.
- Development/Beta/Production 실행·데이터 루트와 패키징 경계는 분리돼 있다. 개발
  launcher는 `scripts/Start-ControlStudio-Development.ps1`을 사용한다.
- Development 모델은 위 스크립트의 `nemotron-3-ultra` 설정을 사용한다. 2026-08-15에
  `/models`에서 해당 ID가 확인됐고 OpenAI-compatible completion 응답도 수신했다.

## 2026-08-31 PWA/native 현재 수직 상태

- 실제 PWA의 fresh search, hardware context, target binding, snapshot과 mission revision 5–8은
  통과했다. offline `P0020→P0021` candidate는 실제 PWA에서 승인되어 한 번 실행을 시작했다.
- execution은 `PLANNED` 뒤 native LD focus coordinate 오류
  `XG5000_LD_SOURCE_NOT_VISIBLE`로 피연산자 입력 전에 실패했다. apply, diff, Program Check,
  save, independent reopen/readback 또는 PLC 기능 성공은 아직 주장하지 않는다.
- 관련 Host transaction은 `f6ecf557-316b-4739-adde-247813330a18`, MCP transaction은
  `077d9ef5-c941-4607-9c1d-f0fc9af74a7d`이며, recovery check의 original-exact match는
  확인됐지만 recovery attach/rollback/save 성공은 확인되지 않았다.
- 05:59 KST OS 재부팅 후 이전 Host/XG5000은 종료됐고, 06:16에 normal Development launcher로
  Host PID 5464/port 8767을 확인했다. XG5000은 현재 없다. working copy `f277…`는 이전
  `b929…`와 static `NetworkModuleName` 하나만 다르고 ProgramData blob은 동일하지만, live
  equivalence나 hash 재결속의 권위는 없다.
- Browser control은 existing ControlStudio tab을 claim했고, scoped `LOCALAPPDATA`의 one-use
  launcher handoff로 Host PID 5464를 바꾸지 않고 PWA 인증과 conversation `8b3` 복원을
  완료했다. XG5000 재확인 inventory는 비어 있었고 recovery relaunch는 한 번만 시도됐다.
  그것은 transaction의 verified working copy가 현재 `f277`이고 retained artifact hash가
  `b929`라서 prelaunch에서 거부됐다. geometry/recovery resolution은 여전히 미완료이며,
  새 XG launch나 file write는 수행하지 않았다.
- Platform `b7d44aec0b6494701bebf74d932e2bc93683b306`은 reviewed-current hash와 transaction/
  Host plan/native checkpoint plan을 각각 결속한 opt-in backup restore request, Host-only MCP
  receipt validation, durable `change.working_copy_restored` event, 그리고 recovery UI의 exact
  review tuple을 추가했다. MCP counterpart `e3e69`는 private preservation과 journal-pinned
  exact backup restore를 소유하며 MCP regression 487건이 통과했다. PWA URL policy로
  실제 UI/restart acceptance는 수행하지 않았고 사용자의 file-restore approval도 아직 없다.
- Acceptance harness의 descendant-pipe fixture는 child PID를 temporary sibling에서 `os.replace`
  로 원자 게시하고 child exit 뒤 current-directory handle release를 bounded wait 하도록 고쳐,
  Windows의 빈 PID 파일/temporary cleanup retry-only race를 제거했다.

## 마지막 리팩터링

- Agent runtime의 canonical JSON 직렬화를 `_canonical_json` 한 곳으로 합쳤다. digest,
  checkpoint, model prompt의 JSON 바이트는 그대로이며 중복 옵션과 프로덕션 코드 72줄을
  제거했다. 마지막 mission objective prompt 두 곳도 같은 serializer를 사용해 별도 옵션
  조합을 남기지 않았다.
- MCP behavior report가 같은 프로그램의 LD graph를 state model과 reference index에서 다시
  만들던 경로를 명시적 내부 graph 전달로 바꿨다. 프로그램당 graph 생성은 3회에서 1회로
  줄었고 공개 함수 시그니처와 report JSON은 그대로다. write action의 condition device
  순회와 causal application parsing도 각각 한 번만 수행한다.
- Host Agent projection은 이미 structural evidence가 소유한 `canonical_digest`를 재사용해
  별도 hashlib/json dependency와 중복 직렬화를 삭제했다.
- Host의 네 종류 structural MNE patch에 흩어졌던 schema, compiler tool, evidence schema,
  plan model, MCP operation, XG-SIM 지원 규칙을 `MNE_PATCH_PLAN_CONTRACTS` 한 곳으로
  통합했다. 새 patch kind의 Host 변경 지점은 여러 분기에서 registry 한 곳으로 줄었다.
- Web의 다섯 structural MNE plan 판별을 `getStructuralMnePlan` discriminated union으로
  통합해 승인 화면의 반복 분기를 제거했다.
- Agent native-design validation은 nested model 전체 dump/parse를 없애고 `model_copy`로
  scope만 교체한다. validator의 retry 규칙은 유지하면서 run 종료 뒤 중복 검증과 mutable
  validator cache를 제거했다.
- MCP MNE plan/apply/verify/rollback/probe의 같은 active-project binding closure 다섯 개를
  기존 검증 함수에 대한 명시적 `partial`로 바꿨다. receipt와 journal 전이는 서로 의미가
  달라 억지로 공통화하지 않았다.
- Host의 operand/MNE executor 선택 규칙을 `CompositeChangeExecutionService._service` 한
  곳으로 모아 preflight/apply/verify/reconcile 네 경로의 중복 분기를 제거했다.
- Agent sidecar의 호출 지점이 하나뿐인 final-output forwarding wrapper를 제거하고 기존
  `asdict` 결과를 wire payload에 직접 사용한다. `run.final` contract는 바뀌지 않았다.
- LS 공식 XGK/XGB instruction contract에 맞춰 direct-D `INC/DEC` signed/unsigned
  16/32-bit 계열 16개를 하나의 in-place mutation spec으로 모델링했다. `P` variant는
  rising-edge 실행이고 double-word variant는 두 연속 D word를 read/write한다. Condition
  causal impact, state/behavior inventory와 Host evidence 검증이 같은 의미 경계를 공유하며
  non-D, indirect, 추가 operand 형식은 계속 `partial`이다.
- Structural raw 결과를 줄일 때 위 명령의 operation/execution/data type/read-write width가
  모델 입력에서 사라지던 경계를 닫았다. Host가 동일 raw state model을 독립 재검증해
  receipt digest에 결속된 bounded `modelContext`만 Agent에 전달하며, Agent는 이를
  observation이 아닌 inference에만 사용한다. HTTP/DB/evidence/event contract는 불변이다.
- 실제 대동 project에서 관측된 LOAD 비교식을 signed 16/32-bit 정적 의미로 구조화했다.
  `=`, `<>`, `>`, `<`, `>=`, `<=`와 정확한 `D` prefix만 지원하며 좌변·우변 순서를
  보존한다. MCP 한 파서가 graph/state/reference를 공급하고 Host는 raw expression을 독립
  재검증한다. 지원하지 않는 suffix·operand는 rung과 network를 `partial`로 유지한다.
  상세 의미는 receipt digest에 결속된 bounded `modelContext`로만 Agent 추론에 전달된다.
- 실제 보존 artifact에서 opcode 33이 raw `APPLICATION`으로 기록된 `>,C0001,6`도 확인했다.
  raw record/opcode는 보존하고 strict 비교 parser가 성공한 경우에만 logical `COMPARE`로
  정규화하는 판정을 한 함수로 모았다. 그 결과 같은 row의 `SET S050.01` condition,
  device reference, causal edge, state/behavior가 같은 의미를 공유하며 TON/MOV/DMOV/DINCP/
  CTR/TRTG/END 등 다른 application 경로는 바뀌지 않는다.
- Windows launcher가 소유한 Host stdout/stderr를 UTF-8로 고정했다. CP949와 UTF-8이 한
  startup log에 섞여 모델·Agent 실패 사유가 깨지던 실제 Development 진단 문제만
  제거했으며 실행 상태나 공개 계약은 바꾸지 않았다.
- Development 자동 모델 검사에서 이미 분류된 실패 사유를 로그에서 버리지 않고 기존
  `recent_model_verification` 한 곳에 성공·실패 모두 보존한다. 현재 저장 모델과 cache key가
  정확히 일치할 때만 기존 `status.agent.detail`과 `host.status` 이벤트로 투영하므로 새 상태,
  DB/API/event schema 또는 Web 분기를 만들지 않았다. 실패 결과는 설정 저장의 성공 cache로
  재사용하지 않아 재검사를 막지 않는다.
- Development 모델 검증의 성공·불완전·예외 결과 기록과 `host.status` 발행을 각각 한 경로로
  합쳤다. 실패 capability 객체 생성도 `ModelCapabilityResult.failed` 한 곳으로 모아 Host와
  sidecar의 중복 필드 구성을 제거했다. 기존 `ServiceState.starting`은 실제 검사가 진행 중일
  때만 쓰고 terminal `unavailable`은 즉시 반환한다. `-RequireAgent` smoke도 문자열이 아니라
  이 상태 enum을 소비하므로 확정 실패를 deadline까지 기다리지 않는다.
- XG5000 controlled launch의 창 생성 대기와 project title 대기 루프를 하나로 합쳤다. PID
  lifetime과 exact title을 같은 순회에서 확인하며 vendor process가 먼저 종료되면 title
  deadline을 더 기다리지 않는다. 제거된 helper를 포함해 14줄 추가/36줄 삭제이고, 정상
  PID/HWND/path/hash 검증은 그대로다.
- Host `MCPGateway`의 동일한 MCP 성공 결과 decoding을 7개 호출 경로에서
  `_decode_tool_result` 한 곳으로 합쳤다. structured content, data, text JSON fallback과 오류
  우선순위는 그대로이며 wire-format 변경 지점은 7곳에서 1곳으로 줄었다. Evidence digest의
  단일 forwarding wrapper도 제거해 이번 변경은 31줄 추가/92줄 삭제, 순 61줄 감소다.
- 이 리팩터링에서 새 테스트는 추가하지 않았다. 임시 C# compile executable 네 개와
  잘못 생성된 빈 파일 하나를 제거했다.
## 마지막 수직 연결

- 보존된 local working copy는 XG5000 process가 없어도 `workspace.compare`,
  `workspace.hardware_context`, `workspace.structure_index` 세 read tool을 Agent에 노출한다.
  Host의 workspace-only binding은 exact workspace/revision을 숨겨 주입하며 PID/HWND/path/hash가
  필요한 live LD·behavior·mutation 도구는 catalog에서 계속 빠지고 직접 호출도 거부된다.
- 동일한 source/working SHA-256도 조기 반환하지 않고 두 path-bound structure index를 만든다.
  파일 동일성은 exact hash로, 부분 구조 inventory는 `partial`로 각각 표현하므로 partial parser를
  LD 의미나 runtime 증거로 오해하지 않는다.
- Web에는 기존 local-open API의 `openInXg5000:false`를 쓰는 `구조만 분석` 선택만 추가했다.
  XG5000 launch가 409로 실패해도 이미 보존된 workspace를 static target으로 계속 사용하며,
  Debug 화면은 authoritative workspace/revision ID와 `오프라인 구조 분석`을 표시한다.
- controller/XG/workspace 전환은 한 번에 하나의 Agent target binding만 남긴다. controller 선택은
  workspace binding을 지우고, 명시적인 workspace/XG 선택은 controller binding을 지운다.
- Host와 Knowledge SQLite 중 한쪽만 먼저 migration되어 durable Agent 작업과 지식 source가
  서로 다른 세대로 남던 시작 경계를 닫았다. 실제 schema 상승이 필요할 때만 두 DB를 같은
  operation의 `sqlite3.backup` snapshot으로 보존하고, 두 초기화와 builtin seed가 모두 끝나야
  marker를 지운다. 두 번째 DB 실패는 즉시 두 preimage를 복원하며 hard crash로 marker가 남으면
  다음 시작이 먼저 같은 pair를 복원한다. 현재 schema에서는 backup I/O가 없다.
- 같은 static workspace에서 기존 순수 XGWX hardware parser도 Agent가 사용할 수 있다.
  `workspace.hardware_context`는 Host가 숨겨서 주입하는 workspace/revision 두 identity와 source·
  working SHA, inner context digest를 한 receipt로 결속한다. XG5000 query와 live hardware cache는
  사용하지 않으며 이 evidence는 변경 승인 coverage에 들어가지 않는다. UI Activity에는
  `작업공간 하드웨어 확인`으로 started/completed가 표시된다.
- 같은 working copy의 기존 `ProjectStructureIndex`도 `workspace.structure_index`로 직접
  전달한다. 새 parser나 HTTP/DB schema는 없고 프로그램·태스크·심볼·device·module·통신의
  bounded facts를 기존 structure receipt와 Agent citation validator가 소비한다. top evidence는
  static tool 이름을 유지해 live 변경 evidence와 구분하고 UI Activity는 `작업공간 구조 분석`으로
  실행을 표시한다. compare evidence도 conversation에 결속된 revision과 다르면 거부한다.
- `project.search`가 검증한 프로그램명·LD 위치·구조 항목 후보는 더 이상 raw-result 제거와 함께
  사라지지 않는다. 기존 search receipt의 digest와 match ID에 결속된 inference-only 후보를 전체
  32개로 제한해 Agent가 exact program/device 후속 도구를 호출할 때만 사용한다. raw XML·path·
  hash·PID/HWND와 공개 evidence/event/DB schema는 늘리지 않았다.
- XG5000을 열지 않은 static workspace에서도 `workspace.search`로 프로그램·심볼·device의
  bounded literal lookup을 수행한다. 기존 structure index와 `build_project_search`, 기존
  `project.search` receipt/context validator를 그대로 재사용하며, workspace/revision/source·working
  SHA를 읽기 전후에 결속한다. Agent에는 raw XGWX 결과가 아니라 receipt-bound locator만 전달되고
  이 evidence는 LD/rung/runtime/PLC/change/MNE 근거로 승격되지 않는다.

## 2026-08-16 현재 수직 보강

- MCP 원격 거부는 `tool_error`, Host 검증·정책 거부는 `host_policy`, 실제 채널 상실만
  `transport`로 Host→Agent까지 보존한다. Agent tool은 Pydantic AI의 terminal `ToolFailed`로
  구조화된 실패를 모델에 돌려주고 재시도 횟수 0을 유지한다. 실제 Agent run 회귀에서 모델은
  실패 종류를 받은 뒤 다음 판단을 수행하며 같은 Host 요청은 한 번만 호출됐다.
- 신규 native build를 `local_file`로 축소하던 WS-02 단절을 닫았다. `new_build` provenance는
  reviewed plan, build bundle/receipt, 저장 project SHA와 receipt digest를 workspace
  manifest/status, Host target binding, resume authorization, HTTP/TS contract까지 결속하며
  stale·tamper를 거부한다. Native handoff 후보와 accepted rebind도 같은 단일 lineage validator를
  사용하므로 일반 `local_file` attach는 native 권한으로 승격되지 않는다. Production에 남은 legacy
  ACCEPTED 1건은 원본 SHA까지 변경돼 자동 승격하지 않고 역사 기록으로만 보존한다.
- signed native resume은 같은 PID/HWND만 신뢰하지 않고 selected-PID COM/ROT가 읽은 실제 project
  path/hash와 process lifetime까지 서명된 new-build workspace에 결속한다. Durable handoff와 accepted
  reuse는 기록 직전에 `workspace.status`와 `xg5000.session_status`를 다시 읽으며, Agent static evidence도
  workspace/revision뿐 아니라 source kind, provenance receipt, source/working SHA를 끝까지 고정한다.
- XG5000 UI 경로의 직접 `SendMessage` 24곳을 bounded `SendMessageTimeoutW`로 통합했다.
- Platform 일반 테스트 14건 중 12건이 통과했고 실제-XGWX 2건은 명시 gate에서 분리했다. 실제 보존
  XGWX의 MCP subprocess→Host broker→Agent wire gate는 두 case 모두 개별 통과했으며, MCP 경계
  테스트 12건, Agent 전체 테스트 7건, HTTP contract guard와 Web build가 통과했다.
  관리자/UAC 실기에서는 XG5000 V4.82.1 PID 12268 기동과 project 임시 index 생성을 확인했지만
  read acceptance는 exit 1이고 결과/traceback이 보존되지 않아 catalog/LD/graph/state 성공은
  주장하지 않는다. source/working SHA-256은 모두
  `bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`로 유지됐고 종료 뒤
  XG 관련 process는 남지 않았다.

## 검증된 것

- Agent, MCP, Host, Launcher, packaging, Python contract compile/import 성공
- Host/Agent/MCP 변경 Python에 Ruff `F,E9,I` 성공
- 실제 Host→sidecar wire projection에서 16개 명령의 exact semantics와 변조 거부 확인
- 실제 보존된 XG5000 ladder artifact에서 `D<>,0,M0320`, `>=,C0001,1`,
  `<,C0001,1`, 그리고 raw APPLICATION encoding의 `>,C0001,6`이 ordered
  operand·signed width와 함께 구조화되는지 확인
- 보존된 18개 파일의 6개 고유 LD payload에서 raw APPLICATION 비교는 action/output에서
  제외되고 TON/MOV/DMOV/DINCP/CTR/TRTG/END의 logical node kind가 유지되는지 확인
- `>,C0001,6`이 `SET S050.01`의 structured condition, `C0001` read reference와
  downstream causal edge로 연결되고 Host logic/state receipt 및 modelContext 독립 검증을
  통과하는지 확인
- 잘못된 P suffix, extra operand, dotted device, 폭 밖 literal이 `partial`로 남고 Host가
  변조된 comparison metadata를 거부하는지 확인
- Host→Agent wire에서 raw state model은 제외되고 receipt digest에 결속된 comparison
  modelContext만 전달되는지 확인
- 2026-08-16 격리 Development 기동에서 Host/DB/static UI/MCP가 모두 `ready`, MCP 67개와
  Agent-visible 23개 도구가 등록되는지 확인. 같은 재기동의 모델 연결 실패 사유가 UTF-8
  log bytes로 기록되는 것도 확인
- 대표 LD 입력에서 리팩터링 전 HEAD와 현재 behavior report canonical JSON 동일 확인
- 같은 입력에서 behavior report의 LD graph 생성 횟수 3회 → 1회 확인
- TypeScript contracts build 성공
- Web TypeScript + Vite production build 성공
- 실제 보존 XGWX(SHA-256 `bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`)
  를 workspace-only path로 실행해 `identical/partial`, configurations 1, tasks 1,
  code units 4, symbols 302, devices 495, modules 2, parameters 6, communications 6을 확인.
  Agent catalog에는 compare/hardware/structure 세 도구만 남고 `program.read_ld`가 숨겨지는 것도
  확인
- 새 Development Host와 launcher handoff로 인앱 UI를 다시 인증해 header/target proof의
  `오프라인 구조 분석`, exact workspace/revision ID, local chooser의 `구조만 분석` 버튼을 확인
- 임시 SQLite 18/1 pair를 19/2로 정상 upgrade하고 sentinel 보존을 확인. Knowledge 초기화 실패를
  주입했을 때 18/1과 양쪽 sentinel이 복원됐으며, Host만 19로 올라간 hard-crash subprocess 뒤
  다음 시작이 marker를 소비해 18/1 preimage부터 다시 19/2로 완료하는 것도 확인
- 실제 Development data root의 현재 19/2 DB로 최종 Host를 재기동해 Host/DB/static UI/MCP
  `ready`, migration marker·backup directory 미생성을 확인
- 실제 보존 XGWX의 Host→MCP stdio 경로에서 MCP 69 tools, static Agent tools 3개를 확인했다.
  `workspace.hardware_context`는 CPU `XBC-DN/DP64H`, timer areas 3, I/O modules 2, configured
  I/O channels 4를 SHA-bound receipt로 반환했고 external wiring은 false로 유지했다. LD tool은
  catalog에서 숨겨졌고, stale revision은 `tool.started` 뒤 `tool.failed`로 거부됐으며 live
  hardware cache는 비어 있었다.
- 같은 stdio 경로의 `workspace.structure_index`는 `tool.started → tool.completed`, project
  `대동인덱스-0917-2`, code-unit names `NewProgram/출력/알람/초기동작`, structure fact 817개를
  발급했다. Host model-safe projection은 bounded 188 facts를 Agent runtime citation validator까지
  전달했고 MCP와 Host 모두 stale revision을 거부했다.
- 실제 XGWX search 결과에서 program-name/structure/LD 후보를 같은 Agent frame에 유지하고,
  35개 결과를 세 종류가 굶지 않는 순서의 32개로 제한했다. receipt digest·coverage match IDs 결속,
  변조 거부와 raw-result 비노출을 inline smoke로 확인했다.
- 같은 보존 XGWX의 static `workspace.search`에서 `M00217`, `T0150`을 각각 exact structure item
  1건으로 찾고 Host→Agent raw-result omission, source receipt digest 결속, stale revision 거부를
  확인했다. Static Agent catalog는 compare/hardware/structure/search 정확히 4개이며 live LD 도구는
  계속 숨겨진다.
- HTTP contract guard 성공
- 세 저장소 staged diff whitespace check 성공
- 원격 모델 catalog와 짧은 completion 호출 성공
- 최종 재검증에서 canonical JSON/digest 바이트 동등성과 behavior report의 graph 생성
  1회/프로그램 불변조건을 별도 테스트 파일 없이 인라인 smoke로 확인
- MCP 결과 decoding의 structured-content/data/text/error 대표 5경로가 기존 우선순위와
  오류 문구를 유지하는지 별도 테스트 파일 없이 inline smoke로 확인
- 2026-08-16 종료 검증에서 Agent/Host/MCP 대상 Python compile과 Ruff `F,E9,I`,
  TypeScript contracts build, Web typecheck/Vite production build, 세 저장소 diff-check를
  각각 한 번 통과. `origin/dev..HEAD`에 새로 추가·수정된 test 파일은 0개
- 실제 Development Host를 workspace 격리 data root로 재기동해 Host/DB/static UI/MCP
  `ready`, MCP 67개·Agent-visible 23개 도구를 확인. private model socket이 정책상 차단된
  상태에서 API와 인앱 브라우저 AGENT DEBUG가 모두 generic 문구 대신 정확한 정제 사유
  `모델 엔드포인트에 연결할 수 없습니다.`를 표시하는지 확인
- 최종 Platform 코드로 Development Host를 다시 시작해 Host/DB/static UI `ready`를 확인하고,
  `Test-ControlStudio-Development.ps1 -RequireAgent`가 같은 terminal 모델 실패를 기존 약
  120초 대기 대신 0.44초에 정확한 정제 사유와 함께 반환하는지 확인. 새 launcher handoff로
  인앱 UI를 다시 인증해 `DEVELOPMENT source-dev`, `Host 준비됨`, AGENT DEBUG 자동 활성화와
  동일 Agent runtime 사유를 확인
- 보존 artifact에서 새 Development working copy를 만든 뒤 실제 XG5000 V4.82.1 controlled
  launch를 재현했다. 이 sandbox 사용자에서는 vendor process가 약 6초 뒤
  `0xC0000409`로 자체 종료했으며, 변경 전 약 19.6초 generic title failure 대신 변경 후
  6.16초에 `exit_code=3221226505, ntstatus=0xC0000409`가 startup log에 남는 것을 확인했다.
  working copy 원본 hash는 보존됐고 XG5000 PID는 남지 않았다. Vendor가 분리 생성한
  `XGProjectServer.exe`는 crash 뒤 남아 이번에 생성한 PID만 수동 정리했으므로, process-tree
  cleanup 완료를 아직 주장하지 않는다.

## 아직 실기 검증이 필요한 것

이 Windows 장비에는 XG5000 V4.82.1이 설치돼 있고 vendor integrity가 필요하다. 2026-08-16의
격리 smoke는 쓰기 가능한 workspace Development data root에서 Host/DB/static UI/MCP까지 실제
기동했다. Agent는 코드 실패가 아니라 Codex 격리 정책이 configured private model endpoint
`100.65.0.1:8978` 소켓을 차단해 unavailable이었고 `/models`와 최소 completion도 같은
소켓 거부를 재현했다. 인앱 브라우저로 Development/Debug 화면과 정제된 Agent 실패 사유는
직접 확인했다. 이후 Administrator desktop/UAC 세션에서 XG5000 V4.82.1이 project working
copy를 실제로 풀어 임시 index까지 생성했으나 acceptance wrapper가 exit 1로 끝났고 최종 JSON과
traceback이 보존되지 않았다. 따라서 live catalog/LD/graph/state와 실제 Agent tool
started/completed 흐름은 아래 절차로 이어서 확인한다.

다음 세션의 첫 작업은 아래 한 번의 수직 검증이다.

1. 보이는 PowerShell에서 `gidong-platform\scripts\Start-ControlStudio-Development.ps1` 실행
2. Windows UAC 승인
3. `scripts\Test-ControlStudio-Development.cmd -RequireAgent` 실행. 이 smoke는 기존
   Development Host를 같은 launcher token으로 검증하고 자신이 시작하지 않은 Host는
   종료하지 않는다.
4. UI에서 Development/Debug 표면과 `nemotron-3-ultra` 연결 확인
5. 실제 working-copy XG5000 project를 attach하고 Agent에 read/analyze 요청
6. tool started/completed/failed activity와 evidence가 UI에 보이는지 확인
7. 승인된 작은 MNE 변경 한 건을 candidate → approval → apply → check/save/reopen까지 실행
8. XG-SIM scope를 선택했다면 receipt를 확인한 뒤 keep 또는 revert까지 완료

실기 증거 없이 native/XG-SIM/PLC 성공을 문서 상태만으로 선언하지 않는다.

## 반복하지 말아야 할 것

- 기능마다 Host/Agent/MCP/Web에 서로 다른 상태 규칙을 복제하지 않는다. 권위가 있는
  한 계층에서 결정하고 나머지는 exact contract를 소비한다.
- UI 표시나 model 주장만으로 mutation/verification 성공을 만들지 않는다. 성공은 target
  identity, journal state, native readback, receipt digest로만 결정한다.
- XG5000 control을 좌표·키 입력 fallback으로 넓히지 않는다. 가능한 typed COM/Win32
  identity를 쓰고, 불가피한 UI adapter는 PID/HWND/path/hash와 bounded profile로 격리한다.
- 방어 문구와 wrapper를 늘리지 않는다. 실제 failure mode, authority boundary, rollback
  invariant 중 하나를 증명하지 못하는 validation은 추가하지 않는다.
- 테스트 수를 성과로 삼지 않는다. 공개 contract나 실제 장애를 잡는 최소 테스트만
  유지하고 type/build/lint/static check가 더 싼 문제는 그 수단으로 검증한다.
- Development 편의를 위해 Production/Beta 권한, 데이터 루트, 서명·BOM 검증을 약화하지
  않는다.

## 2026-08-16 최종 리팩터링 종료점

- 이번 종료 패스는 새 기능·API·DB schema·event payload를 추가하지 않았다. 이미 커밋된
  Host MCP 결과 decoder 통합으로 동일 wire 해석 지점을 7곳에서 1곳으로 줄였고
  `31 insertions / 92 deletions`, 순 61줄의 프로덕션 코드를 제거했다. Agent canonical JSON
  직렬화와 MCP behavior-report graph 재사용도 같은 의미 규칙을 각각 한 곳에 둔다.
- `origin/dev...dev` 범위에 새로 추가하거나 수정한 test 파일은 세 저장소 모두 0개다.
  Python compile, 변경 Python의 Ruff `F,E9,I`, TypeScript contracts build, Web typecheck/Vite
  production build, 세 저장소 diff-check가 통과했다. 실제 보존 XGWX에서 `M00217`, `T0150`
  static literal 조회가 각각 정확히 1건이며 project SHA와 index digest가 유지되는 것도 다시
  확인했다.
- XGWX `ProgramData`를 실제 같은 project의 네 live `program.read_ld` payload와 대조했다.
  direct/application record는 전체 live 1,446개 중 1,278개(88.38%)를 opcode·coordinate·value·
  순서까지 exact 복원하지만 wire 166개와 특수 row-header 2개의 vendor 문법은 확정되지 않았다.
  따라서 현재 static evidence는 structure/search의 `partial` 경계를 유지한다. offset 추측으로
  `workspace.logic_graph`나 offline LD를 만들지 않는다. 해당 기능은 여러 XG5000 버전의 paired
  XGWX↔clipboard corpus로 wire/branch/empty-row 문법까지 증명된 뒤에만 추가한다.
- 다음 실기 작업은 사용자 Windows/UAC 세션에서 최신 Development launcher와
  `nemotron-3-ultra` Agent tool activity, live XG5000 read/analyze의 최종 receipt를 한 번 수직
  검증하는 것이다. 현재 Codex sandbox의 private-model socket은 코드로 우회하지 않는다.

## 주요 남은 위험

- 자동 startup schema migration의 pair snapshot/restore는 구현됐다. 사용자가 임의 시점의
  전체 데이터를 백업·선택 복원하는 UI/운영 정책, backup retention과 release downgrade 지원은
  여전히 별도 production 배포 범위다.
- UAC 승인 뒤 XG5000 기동과 project unpack은 재실행됐지만, 실제 UI/Agent/XG5000 read/analyze
  end-to-end의 성공 receipt는 아직 없다.
- 대표 실제 project fixture와 실제 XG5000 버전 변화에 대한 acceptance evidence가 더 필요하다.
- 비교식의 정적 구문·operand order·signed width는 구조화됐지만 CPU별 device range,
  실제 runtime 값, scan 실행과 조건 참은 여전히 별도 관측 증거가 필요하다.
- 대량의 과거 mock/implementation-coupled test는 정리됐으므로, 남은 핵심 경계는 build,
  contract guard, 최소 실기 acceptance가 실제로 실패를 드러내는지 계속 확인해야 한다.

## 2026-08-16 Agent 수직 폐쇄 후 현재 권위 상태

이 절은 위의 과거 테스트 수와 `새 test 파일 0개` 같은 종료 시점 기록을 대체한다. XG5000
제어층을 다시 만들지 않았고, 기존 COM/ROT·PID/HWND·path/hash 결속과 보존 live evidence를
baseline으로 유지한 채 Agent가 같은 목표를 끝까지 이어 가는 단절만 닫았다.

- Engineering mission은 같은 run의 변경 제안 직후 반드시 exact transaction/digest에 결속된
  `waiting_for_approval`로 멈춘다. 승인·실행 뒤 terminal outcome은 mission의
  `changeTransactionId`로만 재개되며, DB schema 21이 안전하게 기존 대기 mission을 보강한다.
  실패한 continuation은 outcome evidence를 남기지 않고, 성공 checkpoint와 같은 SQLite
  transaction에서 전역 1회만 기록한다. 재시도와 동시 실행도 ID rebound를 만들지 않는다.
- 일반 LD 물리 I/O operand 제안은 같은 run의 `project.hardware_context`와
  `program.change_target_analysis` ready receipt를 모두 요구한다. Agent에는 Host-owned
  `project_hash`를 제거한 public hardware binding만 전달하고, Host는 retained raw receipt의
  project hash와 context/project/receipt digest를 독립 대조한 뒤에만 `PENDING_APPROVAL`을 만든다.
  이 단계에서는 apply, save, PLC connect/write가 실행되지 않는다.
- `COMPLETED`/`ROLLED_BACK` receipt만으로 mission을 완료할 수 없다. 현재 run의 fresh target
  readback evidence가 있어야 완료되며, historical outcome으로 넘어간 다음 run에도 같은 규칙이
  유지된다. 동일 proposal·tool callback은 자동 재호출하지 않는다.
- 재시작 시 durable mission의 과거 evidence는 citation·observation·inference에 재사용한다.
  evidence와 Agent checkpoint는 각각 1.5 MB 제한의 별도 digest-bound frame으로 동기화한 뒤
  consume-once 처리하고, `run.start`에는 작은 reference만 둔다. checkpoint가 있으면 중복 history를
  보내지 않는다. 과거 evidence는 새 mutation 권한이나 fresh-target evidence로 승격되지 않는다.
- Accepted new-build handoff는 reviewed plan/build/reopen/XG-SIM provenance를 Store에서 다시 증명한
  13-field status로 Agent에 전달한다. 첫 mission objective와 acceptance criteria는 이 권위 상태에
  고정되고, 이후 durable mission이 우선한다.
- 세 저장소에 Windows quality workflow를 추가했다. Platform/MCP는 frozen uv lock을 사용하고,
  Agent는 pinned public contracts commit에서 만든 wheel을 direct requirement로 설치한 뒤
  `direct_url.json`까지 확인한다. Agent 전체 dependency constraint, GitHub action full commit SHA,
  setup-uv `0.11.21`도 고정했다.
- XG5000 UI의 직접 `SendMessage` 24곳은 bounded `SendMessageTimeoutW` 공용 경계로 통합했다.
  UIA helper process, clipboard broker, physical `SendInput` 확대는 실제 관측 실패 없이 선제 구현하지
  않는다.

현재 코드의 최종 자동 검증은 Platform 24건 중 22 pass/보존-XGWX 2 skip, 같은 XGWX gate를
명시적으로 켠 subprocess module 6/6, Agent 23/23, MCP 12/12이다. Platform compile·Ruff
`F,E9,I`·HTTP contract guard, Agent compile·Ruff, MCP compile·Ruff, TypeScript contracts
typecheck/build, Web typecheck/production build가 통과했다. Web에는 기존 약 690 KB chunk 경고만
남는다.

보존 XGWX gate는 실제 원본 SHA
`bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`를 MCP subprocess로 열어
Host broker→Agent wire의 search/evidence/receipt, stale revision, transport, tamper 경계를 통과했고
원본 파일은 변하지 않았다. 별도 Development smoke는 launcher/Host process가 시작됐지만 이
비대화형 세션에서 120초 안에 `127.0.0.1:8767` listener가 열리지 않아 exit 1이었다. 종료 후
ControlStudio/XG5000/MCP/Python 잔류 process는 0개다. 이를 live XG 성공으로 선언하지 않는다.

기존 같은 project의 네 live `program.read_ld` payload와 XGWX 1,446-record 대조, 1,278 exact
복원 evidence는 그대로 유효하다. 이번 delta의 유일한 운영 확인 항목은 사용자 Windows/UAC
desktop에서 최신 Development source로 Agent read/analyze→proposal→approval→apply→fresh
readback→mission complete receipt를 한 번 남기는 것이다. 그 전까지 XG5000 제어를 처음부터
재구축하거나 같은 정적 분석을 반복하지 않는다.

## 2026-08-16 실제 모델 workspace mission 완료

이 절이 Agent workspace-only 실행 상태와 자동 검증 수치의 최신 권위다. 기존 XG5000 제어층은
재구축하지 않았다. 보존 XGWX SHA
`bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`를 source/working 불변으로
유지한 채 실제 `nemotron-3-ultra`와 Host→Agent sidecar→Broker→MCP subprocess 수직 미션을
완료했다.

- 미션은 구조, 하드웨어, `M00217`, `T0150`, 최종 종합의 5 step으로 진행됐고 최종 revision 19가
  `completed/objective_achieved`로 저장됐다. 마지막 모델 run은 과거 evidence 4개를 durable sync로
  재사용했고 `tool.*` 및 `evidence.added` 이벤트를 0개 생성했다. Host를 완전히 재기동한 뒤에도
  DB revision 19와 completed 상태가 유지됐으며 Host/DB/static UI와 MCP가 ready, MCP 70 tools와
  Agent-visible 26 tools로 올라왔다.
- 수리 전 진단 run에는 `workspace.structure_index`가 2회 추가 실행됐다. 이 반복을 숨기지 않는다.
  exact tool cache, recovery checkpoint, stale-next-action reanalysis를 반영한 뒤의 연속 구간에서는
  structure evidence 1회부터 시작해 hardware 1회, `M00217` search 1회, `T0150` search 1회였고
  각 재개는 앞선 도구를 다시 호출하지 않았다.
- Nemotron 경로는 도구 실행과 구조화 분석을 두 phase로 분리하고, 매 cycle 현재 durable mission을
  다시 주입한다. 마지막 step은 모든 선행 step이 outcome+evidence로 완료된 경우에만 retained
  evidence를 합성하며 새 evidence인 것처럼 다른 step으로 복사하지 않는다.
- 모델 문장을 durable step outcome으로 저장하지 않는다. search outcome은 Host-검증 receipt의
  exact literal, coverage match count, evidence ID에서 결정적으로 생성한다. 실제 진단 중 모델이
  `M00217`을 반대로 서술했던 사례가 이 변경의 회귀가 됐다.
- `workspace.search`의 bounded `modelContext`는 이제 같은 tool completion에서 evidence journal에
  저장된다. `sourceReceiptDigest`, search digest, coverage keys, candidate count/content와 별도 context
  digest를 교차 검증한다. 0-match도 빈 candidates와 count 0으로 명시 보존하며, 변조된 context는
  historical projection에서 거부한다. 과거 journal에 context가 없던 실행은 digest-bound Agent
  checkpoint에서만 1회 복구하는 호환 경로를 유지한다.

최신 자동 검증은 Platform 28건 중 25 pass/보존-XGWX 3 skip, 보존-XGWX 명시 gate 3/3,
Agent 34/34, MCP 12/12다. Platform/Agent/MCP compile과 Ruff `F,E9,I`, HTTP contract guard,
TypeScript contracts typecheck/build, Web typecheck/production build, 세 저장소 `git diff --check`가
통과했다. Web에는 기존 약 690 KB chunk 경고, Platform에는 FastAPI TestClient deprecation 안내만
남고, Agent 격리 테스트에는 비실패 asyncio slow-callback 진단이 한 번 기록됐다. fresh Host smoke
종료 후 workspace 관련 Host/Agent/MCP/XG5000 잔류 process는 없다.

다음 수직 작업은 정적 workspace 조회를 반복하는 것이 아니다. 사용자 Windows/UAC desktop에서
동일 mission/lifecycle 계약을 실제 XG5000 read target에 연결한 뒤, 승인된 작은 변경 한 건을
proposal→approval→apply→fresh readback→save/reopen→keep 또는 rollback까지 완료하고 receipt를
남긴다. 실제 장비가 포함되면 controller identity/project match와 stop condition을 먼저 결속한다.

## 2026-08-16 실제 변경 경로 사전 검증과 현재 UAC 경계

정적 조회를 다시 수행하지 않고 위의 다음 작업을 그대로 시작했다. 실제 모델 DB를 복제한 격리
data root `artifacts/change-mutation-e2e-localappdata`에서 최신 Host를 8777에 기동했고 Host, DB,
Agent `nemotron-3-ultra`, MCP 70 tools/Agent-visible 26 tools가 모두 ready인 것을 확인했다. 보존
XGWX를 새 working copy로 캡처한 뒤 기존 XG5000 launch 경로를 호출했으며, Windows가 실제
`[WinError 740]`로 vendor integrity 승격을 요구해 409로 종료했다. 생성된 workspace
`b9ffa41e-3805-4f9c-b87e-76309fd08ed6`, revision
`3c11b6f2-f322-441a-8ee6-f89eea8dc70c`은 그대로 보존했다. source, working, original 파일은 모두
원본 SHA `bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`와 같아서 mutation은
일어나지 않았다.
2026-08-17 재확인에서도 manifest의 workspace/revision과 captured/working 두 파일 SHA가 모두
동일했고 working size는 45,226 bytes였다. 따라서 새 캡처 없이 이 exact revision을 계속 사용한다.

공식 `Start-ControlStudio-Development.ps1 -NoOpen`도 같은 격리 root로 호출했다. launcher는
Session 1의 `consent.exe`를 정상 기동했지만 이 비대화형 작업에서는 보안 데스크톱 승인을 받을 수
없어 두 번 모두 약 120초 뒤 명시적인 elevation-not-approved 상태로 끝났다. 이후 Host, Agent,
MCP, XG5000, consent와 8767/8777 listener 잔류는 0개다. 다음 실행은 workspace 캡처나 정적 분석을
반복하지 않고, 사용자가 UAC를 누를 준비가 된 한 번의 창에서 위 workspace를 이어 쓴다.

UAC 대기 중 실제 mutation service를 수직 검토해 저장 전 검증 실패의 복구 결함을 수정했다.
기존 코드는 `project.diff` 또는 Program Check가 실패해 native journal이 `APPLIED`,
`DIFF_VERIFIED`, `CHECKED` 또는 `QUARANTINED`인 경우에도 성공 경로의 `SAVED` 상태를 요구해,
이미 마련된 `project.rollback_change`에 도달하지 못하고 Host를 격리했다. 이제 실패 경로는
`change.transaction_status`를 다시 읽고 transaction/session/PID/HWND/project path/current hash와
recovery checkpoint, immutable plan ID/digest를 exact 재결속한다. native journal이 명시적으로
`rollback_required`인 rollback-capable 상태일 때만 editor rollback과 baseline save를 실행하며,
다른 plan으로 바뀐 journal은 rollback하지 않고 격리한다. 정상 경로의 SAVED→commit 검증도 같은
공용 권위 scope를 사용한다.

MNE terminal 경로의 저장·재열기 뒤 세션 결속도 닫았다. MCP는 전체 MNE verification 또는
rollback이 성공한 뒤에만 독립적으로 재오픈해 exact readback한 XG5000 PID/HWND, process lifetime,
workspace, canonical project path/hash를 1회성 controlled-open read proof로 등록한다. Host는 terminal
event와 mission continuation 전에 저장된 verification/rollback receipt의 transaction/plan/digest와
그 proof를 exact 검증해 `xg5000.attach`로 즉시 소비하고, 재오픈된 세션을 같은 workspace와 Agent
target에 바인딩한다. receipt, path, hash 또는 session이 어긋나면 완료 사실을 꾸미지 않고 live
target을 비워 static workspace만 유지한다. XG-SIM `OBSERVING` 중에는 명시적인 keep/revert 결정을
마칠 때까지 원래 mutation session을 유지하며, terminal accept/revert에서만 각각 verification 또는
rollback reopen을 결속한다. 이 read proof는 후속 mutation의 strong identity 권위를 대신하지 않는다.

같은 실기 경로에서 MNE replacement strategy의 물리 select/clear/paste 입력도 구식 개별
`keybd_event` 호출에서 공용 `SendInput(INPUT[])` 경계로 교체했다. 입력 직전에 root/editor HWND의
exact PID, foreground frame, GUI thread의 실제 focused descendant와 chord key-up 상태를 확인하고,
down/up 전체 배열을 한 호출로 넣는다. Windows가 전송 개수를 전부 반환하지 않으면 성공으로
진행하지 않고 요청 키의 release 배열을 best-effort로 보낸 뒤 명시적인 오류를 반환한다. 기존
editor liveness/dialog 검사와 단계별 MNE payload readback은 그대로 뒤에서 실제 결과를 확정한다.
2026-08-17에는 이 경계를 MNE read/capture/focus 행 이동, project-open shortcut, LD surface
focus/copy·operand dialog와 native dialog paste까지 확대했다. HOME/DOWN 반복 이동도 한 배열로
보내며, mouse fallback은 exact PID/foreground/target rect뿐 아니라 `WindowFromPoint`가 같은 target
descendant인지 확인해 다른 창에 가린 좌표를 클릭하지 않는다. cursor는 호출 뒤 원위치로 돌리고
partial mouse 전송은 button-up을 보낸 뒤 실패한다. production 전체에서 직접 `keybd_event`,
`mouse_event` 호출은 더 이상 남아 있지 않다.

MNE read/capture/focus/new-program/replacement가 사용자 clipboard를 text 하나로 축소하던 경계도
교체했다. 각 최상위 작업은 별도 short-lived STA helper가 `OleGetClipboard`로 원래 `IDataObject`를
보유하고, 전역 Windows named mutex 아래 nested MNE 호출 전체를 하나의 transaction으로 묶는다.
MCP는 자신이나 XG5000이 실제 clipboard를 바꾼 마지막 sequence를 기록하고, helper는 종료 시점의
`GetClipboardSequenceNumber`가 그 값과 exact 일치할 때만 `OleSetClipboard`와
`OleFlushClipboard`로 HTML·bitmap·custom·delayed format을 포함한 원본을 materialize한다. 사용자가
중간에 새로 복사해 sequence가 달라졌으면 복원하지 않는다. helper startup/restore에는 hard deadline이
있고 hang 시 child만 종료한다. 실제 Windows `snapshot→cancel` smoke를 연속 세 번 실행해 mutex,
STA/OLE 획득과 정상 종료를 확인했으며 잔류 helper는 없다.

같은 수직의 UIA 호출도 장기 MCP 프로세스에서 전부 분리했다. attach probe, project tree/diagnostic
inventory, toolbar command, program-check 결과 text, RUN/STOP/Write button 상태와 owner-drawn dialog
read/set/click fallback은 호출마다 별도 non-UI MTA helper에서 실행되고, parent가 PID/HWND를 호출
전·child 내부·응답 후 exact 검증한다. production의 `Desktop(backend="uia")` 호출은 이 worker에만
남아 있다.
helper가 UI Automation element 검색이나 `click_input`에서 멈추면 4~6초 hard deadline 뒤 child만
종료하며 MCP/transaction journal은 계속 살아 있다. 닫히는 modal dialog는 정상 postcondition으로
허용하되 같은 HWND가 다른 PID에 재사용되면 실패한다. UIA timeout 뒤 native HMENU/win32 fallback으로
갈 때도 PID ownership을 다시 확인한다. 실제 Windows foreground window를 대상으로 격리 helper의
attach probe와 button inventory를 연속 실행한 read-only smoke는 2.2초 안에 button 3개를 반환했고
helper 잔류는 없었다. [Microsoft UI Automation threading 지침](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)의
non-UI MTA 권고를 따르며, XG5000 제어 primitive나 selector를 새로 만들지 않고 이미 검증된 호출의
장애 경계만 바꿨다.

최신 자동 검증은 Platform 34건 중 31 pass/보존-XGWX 3 skip, 보존-XGWX 명시 subprocess module
8/8(실제 artifact acceptance 3/3), Agent 34/34, MCP 52/52이다. 새 change execution 수직 3건은 정상 apply→diff→check→save→commit,
저장 전 native rejection→rollback→baseline save, plan identity 변조→no rollback/quarantine을 각각
검증한다. 추가 MNE terminal rebind 수직은 Host 3건과 MCP 2건으로 정상 completed handoff,
다른 파일/tampered receipt 거절, exact controlled-open binding 등록과 unbound reopen 무등록을
검증한다. 물리 입력 수직 14건은 exact down/up ordering, repeated navigation, focus·key-state 차단,
가려진 mouse target 거절, cursor 복원, partial-send release와 MNE/LD/dialog 연결을 검증한다.
clipboard 수직 7건은 nested snapshot 1회, exact last-sequence restore,
사용자 clipboard 변경 시 no-overwrite, OLE materialization과 destructive partial-write 복원을 검증한다.
UIA 격리 수직 17건은 MTA 초기화, hard timeout, PID/HWND drift·reuse 차단, exact attach와
toolbar/dialog 선택, Program Check result, XG-SIM·tree·diagnostic inventory 연결을 검증한다.
세 Python 제품 compile과 Ruff `F,E9,I`, HTTP contract guard, TypeScript contracts
typecheck/build, Web typecheck/production build가 통과했다. Web에는 기존 약 690 KB chunk 경고,
Platform에는 FastAPI TestClient deprecation 안내만 남는다.

남은 유일한 이 수직의 외부 동작은 사용자 보안 데스크톱에서 UAC를 승인한 뒤 같은 working copy의
작은 MNE candidate를 approval→apply→readback/check→save/reopen→keep 또는 rollback으로 닫고,
같은 Agent mission에 terminal outcome과 fresh evidence receipt를 남기는 것이다.

## 2026-08-17 실제 Agent 한국어 수직 완료와 XG5000 Automation 수정

격리 Development data root의 기존 workspace/revision을 그대로 사용해 최신 source Host를 8767에
기동하고 실제 `nemotron-3-ultra`→Agent sidecar→Host broker→MCP subprocess 경로를 다시 실행했다.
요청은 저장된 XGWX의 프로그램명과 하드웨어 구성을 한국어로 보고하고 파일/PLC를 변경하지 않는
분석-only 작업이었다. 첫 run에서 `inspect_workspace_structure`와
`inspect_workspace_hardware_context`를 각각 정확히 1회 실행해 evidence를 durable checkpoint에
저장했다. 후속 resume은 MCP tool 호출 0회로 두 evidence를 재사용해 mission revision 3을
`completed/objective_achieved`로 끝냈고, 최종 답변은 프로그램 `NewProgram`, `출력`, `알람`,
`초기동작`, CPU `XBC-DN/DP64H`, 확장 입력 `XBE-DC08A`, 관측된 I/O 시작 영역과 timer 영역을
한국어로 직접 보고했다. source/working SHA는 계속
`bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`이며 mutation은 없었다.

이 실행에서 드러난 continuation의 영어 메타데이터 복창은 수정했다. 한국어 원 요청을 continuation
instructions와 prompt에 다시 결속하고, 사용자 표시 answer/structured prose가 영어 우세이면
validator가 수락하지 않는다. 내부 mission/workspace/revision/schema/receipt/digest 나열 대신 원래
engineering objective에 필요한 구체적 evidence fact를 직접 답하도록 했다. 영어 우세 answer,
structured prose, continuation 언어 결속의 회귀 3건을 추가했다.

XG5000 실기 attach의 별도 원인도 확인했다. 설치된 32-bit Automation server 등록은 ProgID
`XG5000App.Application`, CLSID `{25337192-7138-48AE-9712-99107F6E2522}`이고 LocalServer32 명령은
`C:\XG5000\XG5000.exe /Automation`이다. 기존 launcher는 project path만 전달해 창은 열어도 ROT에
등록되지 않았다. 이제 launcher는 exact executable에 `/Automation`과 exact project path를 전달하며
명령 회귀 테스트가 이를 고정한다. 실제 elevated ROT probe는 UAC 승인이 닫혀 실행되지 않았으므로
live acceptance로 기록하지 않는다. 다음 실기에서는 새 제어 primitive를 만들지 않고 이 corrected
launch command로 exact PID/ROT/path/hash proof만 확인한다.

Workspace resume의 controlled open이 실패한 뒤 XG5000 창이 늦게 준비되는 경우에도 다시
launch하지 않는다. Host는 최대 15초 동안 read-only instances만 확인하고, 후보가 정확히 하나일
때만 persisted workspace의 exact path/hash와 그 PID/HWND로 기존 MCP attach를 실행한다. strong
readback 또는 controlled read binding이 아니거나 후보가 없거나 복수이면 기존 409를 유지한다.
UI가 보내는 120초는 launcher 내부 deadline이며 fetch 자체에는 120초 Abort가 없다. MCP operation
계약과 독립 CLI subprocess outer는 cleanup grace를 포함해 135초이고, Host FastMCP transport는
failure envelope 전달 여유 5초를 더한 140초로 open-project에만 고정한다. 다른 workspace tool의
timeout 동작은 바꾸지 않는다.

최신 자동 검증은 Platform 35건 중 32 pass/보존-XGWX 3 skip, Agent 38/38, MCP 53/53이다.
세 Python 제품 compile과 Ruff `F,E9,I`, HTTP contract guard, TypeScript contracts typecheck/build,
Web typecheck/production build가 모두 통과했다. Web에는 기존 약 690 KB chunk 경고만 남는다.

## 2026-08-24 Production UI 흰 화면 수정

Development Host 8767의 HTML, JavaScript, CSS 응답과 파일 SHA는 정상이었지만 Vite가 생성한
`type="module" crossorigin` entry와 `crossorigin` stylesheet는 로컬 앱 브라우저에서 실행·파싱되지
않았다. 같은 소스의 Vite development UI는 정상 렌더링됐고, production asset은 self-contained
bundle임을 확인했다. production HTML에 한해서 JavaScript를 deferred classic entry로 바꾸고
stylesheet의 불필요한 `crossorigin`을 제거했다. development module entry는 그대로 유지한다.

흰 화면이 다시 조용히 남지 않도록 외부 `boot-guard.js`가 시작 예외 또는 3초 내 root 미생성을
안전한 복구 화면으로 표시한다. `npm run build`는 이제 typecheck와 Vite build 뒤
`verify-production-entry`를 실행해 deferred script, same-origin stylesheet, boot guard가 실제
`dist/index.html`에 남았는지 검사한다. 실제 인앱 브라우저에서 production DOM 렌더링,
header 배경 `rgb(17, 29, 27)`, CSSOM 968 rules를 확인했다. 직접 연 URL은 의도대로 Launcher 세션
인증 필요 화면에 머물렀으며, 인증 토큰을 브라우저에 수동 주입하지 않았다.

별도 Edge 151 app-mode acceptance에서는 페이지가 정상이어도 UI Automation에 `RootWebArea`가
노출되지 않아 Launcher의 fail-closed rendered-content probe를 통과하지 못했다. renderer
accessibility 강제 플래그도 실제 UIA tree를 바꾸지 않아 코드에는 남기지 않았다. 브라우저 프레임
제목이나 빈 창만으로 성공 처리하는 완화도 하지 않았다. 후속 작업은 일회성 handoff가 실제
`POST /api/session`에서 소비되고 bootstrap까지 성공했음을 Launcher가 안전하게 관측하는 별도
readiness receipt를 추가하는 것이다.
