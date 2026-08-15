# Development handoff

이 문서는 새 세션이 현재 `dev` 상태에서 바로 이어가기 위한 운영 기준이다. 제품 요구의
최상위 권위는 [PRD](../PRD.md)이며, 이 문서는 구현 상태와 다음 검증 순서만 기록한다.

## 저장소와 브랜치

- `gidong-platform`: `dev` (이 문서를 포함한 commit)
- `gidong-agent`: `dev` @ `a6732c55246a8c2ffa4ab954c8ef949a22687eb2`
- `gidong-plc-mcp`: `dev` @ `6653a29ae7bc397fedb5981af6f77c4c66af3ed0`
- 세 저장소의 `main`은 작업 시작 기준 commit에 그대로 두었다. 개발은 `dev`에서만 계속한다.

2026-08-16 04:00 KST에 세 저장소 push를 다시 시도했지만 GitHub 443 연결이 모두
거부됐다. 이 문서 commit 뒤 Platform은 `origin/dev`보다 8 commit, Agent는 4 commit,
MCP는 3 commit 앞서 있다. 네트워크 가능한 세션에서 세 저장소의 `git push origin dev`를
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

- Agent runtime의 canonical JSON 직렬화 17곳을 `_canonical_json` 한 곳으로 합쳤다. digest,
  checkpoint, model prompt의 JSON 바이트는 그대로이며 중복 옵션과 프로덕션 코드 72줄을
  제거했다.
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
- Windows launcher가 소유한 Host stdout/stderr를 UTF-8로 고정했다. CP949와 UTF-8이 한
  startup log에 섞여 모델·Agent 실패 사유가 깨지던 실제 Development 진단 문제만
  제거했으며 실행 상태나 공개 계약은 바꾸지 않았다.
- 이 리팩터링에서 새 테스트는 추가하지 않았다. 임시 C# compile executable 네 개와
  잘못 생성된 빈 파일 하나를 제거했다.

## 검증된 것

- Agent, MCP, Host, Launcher, packaging, Python contract compile/import 성공
- Host/Agent/MCP 변경 Python에 Ruff `F,E9,I` 성공
- 실제 Host→sidecar wire projection에서 16개 명령의 exact semantics와 변조 거부 확인
- 실제 보존된 XG5000 ladder artifact에서 `D<>,0,M0320`, `>=,C0001,1`,
  `<,C0001,1`의 모든 관측 occurrence가 ordered operand·signed width와 함께 구조화되는지
  확인. `>,C0001,6`은 보존 artifact에 없어 exact synthetic 입력으로 동일 경로를 확인
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
- HTTP contract guard 성공
- 세 저장소 staged diff whitespace check 성공
- 원격 모델 catalog와 짧은 completion 호출 성공
- 최종 재검증에서 canonical JSON/digest 바이트 동등성과 behavior report의 graph 생성
  1회/프로그램 불변조건을 별도 테스트 파일 없이 인라인 smoke로 확인

## 아직 실기 검증이 필요한 것

이 Windows 장비에는 XG5000이 설치돼 있고 vendor integrity가 필요하다. 2026-08-16의
격리 smoke는 쓰기 가능한 임시 Development data root에서 Host/DB/static UI/MCP까지 실제
기동했다. Agent는 코드 실패가 아니라 Codex 격리 정책이 configured private model endpoint
`100.65.0.1:8978` 소켓을 차단해 unavailable이었고 `/models`와 최소 completion도 같은
소켓 거부를 재현했다. launcher UI root 요청은 도달했지만 격리 계정이 Windows app window
ownership을 열람하지 못해 시각 UI 검증은 완료되지 않았다. 임시 Host는 검증 뒤 종료했다.
실제 사용자 세션에서는 아래 절차로 다시 시작한다.

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

- UAC 승인 뒤의 실제 UI/Agent/XG5000 end-to-end는 이 commit에서 아직 재실행되지 않았다.
- 대표 실제 project fixture와 실제 XG5000 버전 변화에 대한 acceptance evidence가 더 필요하다.
- 비교식의 정적 구문·operand order·signed width는 구조화됐지만 CPU별 device range,
  실제 runtime 값, scan 실행과 조건 참은 여전히 별도 관측 증거가 필요하다.
  `>,C0001,6`은 현재 보존된 실제 artifact에는 없어 exact synthetic 경로만 검증됐다.
- 대량의 과거 mock/implementation-coupled test는 정리됐으므로, 남은 핵심 경계는 build,
  contract guard, 최소 실기 acceptance가 실제로 실패를 드러내는지 계속 확인해야 한다.
