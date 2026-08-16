# Development handoff

이 문서는 새 세션이 현재 `dev` 상태에서 바로 이어가기 위한 운영 기준이다. 제품 요구의
최상위 권위는 [PRD](../PRD.md)이며, 이 문서는 구현 상태와 다음 검증 순서만 기록한다.

## 저장소와 브랜치

- `gidong-platform`: `dev` (이 문서를 포함한 commit)
- `gidong-agent`: `dev` @ `70df51c` (`Teach agent static workspace search limits`)
- `gidong-plc-mcp`: `dev` @ `0188cad` (`Expose static workspace literal search`)
- 세 저장소의 `main`은 작업 시작 기준 commit에 그대로 두었다. 개발은 `dev`에서만 계속한다.

2026-08-16 09:43 KST에 세 저장소의 최신 `dev` push를 다시 시도했지만 GitHub 443 연결
거부로 모두 실패했다. 이 문서 commit 기준 Platform은 `origin/dev`보다 24 commit,
Agent는 9 commit, MCP는 9 commit
앞서 있다. 네트워크 가능한 세션에서 세 저장소의 `git push origin dev`를
먼저 실행한다.

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
직접 확인했지만 실제 Agent tool started/completed 흐름과 XG5000 창 제어는 사용자 세션에서
이어가야 한다.
Codex 실행 identity는 `CodexSandboxOffline`이며 이 identity에서 bare XG5000도 동일
`0xC0000409`로 종료됐다. 따라서 project argument나 ControlStudio binding 성공을 거짓으로
판정하지 않았고, 실제 Administrator desktop/UAC 세션에서 아래 절차로 다시 시작한다.

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

## 주요 남은 위험

- 자동 startup schema migration의 pair snapshot/restore는 구현됐다. 사용자가 임의 시점의
  전체 데이터를 백업·선택 복원하는 UI/운영 정책, backup retention과 release downgrade 지원은
  여전히 별도 production 배포 범위다.
- UAC 승인 뒤의 실제 UI/Agent/XG5000 end-to-end는 이 commit에서 아직 재실행되지 않았다.
- 대표 실제 project fixture와 실제 XG5000 버전 변화에 대한 acceptance evidence가 더 필요하다.
- 비교식의 정적 구문·operand order·signed width는 구조화됐지만 CPU별 device range,
  실제 runtime 값, scan 실행과 조건 참은 여전히 별도 관측 증거가 필요하다.
- 대량의 과거 mock/implementation-coupled test는 정리됐으므로, 남은 핵심 경계는 build,
  contract guard, 최소 실기 acceptance가 실제로 실패를 드러내는지 계속 확인해야 한다.
