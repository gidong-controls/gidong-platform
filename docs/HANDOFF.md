# Development handoff

이 문서는 새 세션이 현재 `dev` 상태에서 바로 이어가기 위한 운영 기준이다. 제품 요구의
최상위 권위는 [PRD](../PRD.md)이며, 이 문서는 구현 상태와 다음 검증 순서만 기록한다.

## 저장소와 브랜치

- `gidong-platform`: `dev` (이 문서를 포함한 commit)
- `gidong-agent`: `dev` @ `8989c9800306b24b255ff6a42f537d2ac34811c4`
- `gidong-plc-mcp`: `dev` @ `d863634c9c649ff3ad549b8feb012513dfbb08d8`
- 세 저장소의 `main`은 작업 시작 기준 commit에 그대로 두었다. 개발은 `dev`에서만 계속한다.

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
- 이 리팩터링에서 새 테스트는 추가하지 않았다. 임시 C# compile executable 네 개와
  잘못 생성된 빈 파일 하나를 제거했다.

## 검증된 것

- Agent, MCP, Host, Launcher, packaging, Python contract compile/import 성공
- Host/Agent/MCP 변경 Python에 Ruff `F,E9,I` 성공
- TypeScript contracts build 성공
- Web TypeScript + Vite production build 성공
- HTTP contract guard 성공
- 세 저장소 staged diff whitespace check 성공
- 원격 모델 catalog와 짧은 completion 호출 성공

## 아직 실기 검증이 필요한 것

이 Windows 장비에는 XG5000이 설치돼 있고 vendor integrity가 필요하다. 숨겨진 자동 실행은
Windows UAC 승인 화면을 통과할 수 없어 Host 시작 전에 중단됐다. 이는 코드에서 우회할
문제가 아니다.

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
- 대량의 과거 mock/implementation-coupled test는 정리됐으므로, 남은 핵심 경계는 build,
  contract guard, 최소 실기 acceptance가 실제로 실패를 드러내는지 계속 확인해야 한다.
