# Development handoff

## 2026-09-14 no-op·scoped edit 배포 후 기존 설계 계속

- root가 planning/projection 전체 회귀 **64 passed**(20.71초)를 확인한 뒤 run
  `7bd0de8f-f8c9-4ae3-92e7-e4c6bb53351d`를 정상 cancel했고 terminal=true를
  확인했다. Host restart를 scheduled로 요청하고 재인증까지 성공하여 no-op/진단
  freshness 및 invalid 초안의 광고된 scoped edit 허용 수정을 배포했다.
- event19331의 영속 진단은 기존
  `index_acceptance_alarm_roles_invalid`와 `repair_requires_scoped_edit_tool`을
  확인하게 했지만 raw arguments는 의도적으로 보존하지 않는다. 따라서 그 실제
  호출이 narrative와 item repair의 혼합 편집이었다고 단정하지 않는다.
- 같은 conversation `b0f18468-e072-4b4c-8455-7e2a722f41f3`에서 새 run
  `b4492e6e-d329-4a8f-80f2-6955c6094fb2`, operation
  `continue-index-edit-contract-20260914`을 시작했다. 사용자 문구는 “지금까지 만든
  설계에서 이어서 완성해 줘.”이며 기술 조건을 중복 추가하지 않았다. API는
  analyzing이고 기존 browser8 재인증 후 최신 요청·분석 중·대화 준비 상태를 실제
  접근성 트리에서 확인했다.
- 기존 draft/revision/native 결과는 보존했다. 새 run의 요구 출처 채택, 설계 완성,
  새 native 생성과 XG-SIM은 아직 확인하지 않았다.
- 위 배포 뒤 scoped-edit 거절 안내를 더 정확히 고쳐, 실행 collection 전체 교체만
  제한되고 narrative·기술 설정·acceptance bindings는 관련 item edit와 함께 바꿀
  수 있음을 명시했다. 이 문구 정리는 현재 Host 재시작 이후 작성되어 아직 실행
  서비스에는 미배포다. 서버나 실 run을 추가 조작하지 않았다.

## 2026-09-14 설계 no-op·진단 freshness 수정 — 배포 대기

- 실제 run `7bd0de8f-f8c9-4ae3-92e7-e4c6bb53351d`에서 rev621→622
  `design.edit`가 성공으로 기록됐지만 두 revision의 editable document와 digest가
  동일했고, 이전 `index_acceptance_alarm_roles_invalid` 진단도 그대로 승계됐다.
- 일반 `design.edit`가 실제 문서를 바꾸지 않으면 revision을 만들지 않고
  `edit_made_no_change`, `diagnosticsFreshness=retained_not_rechecked`,
  `nextAction=design.check`를 반환하도록 수정했다. 실제 변경이면 이전 검사를 새
  결과처럼 승계하지 않고 새 revision을 `working`/미검증 상태로 저장한다. 이전
  진단은 append-only 과거 revision에 보존된다.
- 최신 USER 요구 출처가 complete brief/legacy supplemental shortcut에서 빠지던
  문제도 함께 수정했다. Agent가 exact message source를 요구 변경인지 상태·계속
  메시지인지 판단하며, 채택 시 기존 Host 원문 검증과 review 결속을 거친다.
- root가 planning/projection 전체 회귀를 독립 실행해 **63 passed**
  (19.56초)를 확인했다. 테스트 이름도 새 진단 freshness 계약에 맞췄다.
- 이 no-op/freshness 수정은 아직 Host에 배포하지 않았다. 현재 실 run은 취소하거나
  재시작하지 않고 유지 중이다. 이 회귀 결과는 PLC 동작 또는 XG-SIM 통과 증거가
  아니다.

## 2026-09-14 요구 출처 채택·설계 실패 진단 배포 후 재실행

- 설계 도구 실패 이벤트에 request/tool-call 결속을 유지한 bounded 진단을
  영속하도록 배포했다. 최대 4개의 Host 고정 `code`/JSON-pointer `path`/고정
  `message`/허용된 `expectedType`만 남기며 raw arguments, submitted value,
  전체 설계와 credentials는 저장하지 않는다. 일반 UI는 기존 실패 요약만 표시한다.
- complete brief와 legacy supplemental이 있더라도 새
  `requirement_message_sources`가 있으면 deterministic narrative shortcut을
  사용하지 않고 Agent가 새 원문을 요구 변경/확인 또는 상태·계속 메시지로
  판단한다. 채택한 요구는 기존 exact quote/messageId 검증과 새 review artifact에
  결속하며 기존 승인본이나 DB brief에 소급 적용하지 않는다.
- root가 독립 실행한 Host 집중 회귀는 4 passed, Agent 묶음은 67 passed 및
  51 subtests passed다. 이 결과는 PLC 동작 또는 XG-SIM 통과 증거가 아니다.
- 이전 run `b52c1f37-0c12-4142-9caa-5e1f96d50153`는 정상 cancel API로
  terminal=true를 확인했다. 중단 사유는 속도가 아니라 정상 정지를 네 번째
  알람으로 생성(rev606), 삭제(rev616), 재생성(rev618)하고 acceptance의 네 번째
  알람으로 추가(rev619), 다시 삭제(rev621)한 의미 왕복 회귀다.
- Host restart API를 scheduled로 요청한 뒤 재인증까지 성공하여 위 두 패치를
  배포했다. 같은 conversation
  `b0f18468-e072-4b4c-8455-7e2a722f41f3`에서 새 run
  `7bd0de8f-f8c9-4ae3-92e7-e4c6bb53351d`, operation
  `complete-index-requirement-adoption-20260914`을 시작했다. 요청은 정상 정지를
  별도 버튼으로 두고 세 알람을 유지하며, 준비·정지 실제 연결과 자동·수동 모드
  리셋을 기존 설계에서 완성하는 내용이다. 원본 금지 등의 불필요한 반복 문구는
  추가하지 않았다.
- 현재 API 상태는 analyzing이다. 새 run의 실제 source 채택, 설계 완성,
  브라우저 재인증, 새 native 생성과 XG-SIM은 아직 확인하지 않았다. 기존 DB
  revision, native operation과 생성 파일은 모두 보존했다.

## 2026-09-14 변경 설계의 별도 native 생성 경로 복구

- 같은 대화의 기존 native operation 복원이 새 코드 변경 검토본에서도 신규 생성
  폼을 숨기는 UI 결함을 확인했다. Host의 binding-only 동일성 검사는 유지했다.
- 다른 검토 digest일 때 기존 프로젝트 시험 설정 사용과 새 설계의 별도 프로젝트
  생성을 구분했다. 후자는 UI 복원 선택만 해제하고 패널을 새로 마운트한다.
  기존 operation/프로젝트는 삭제하거나 변경하지 않는다. 동일 digest 복원은 유지한다.
- Sol의 typecheck/프로필 UI 회귀 통과 후 실제 production build 및 entry 검증 성공.
  서비스 HTML이 assets/index-BL4t9JA2.js를 제공함을 확인했다. Host 재시작 없음.
- 실제 새 검토본으로 클릭·생성·XG-SIM까지 통과한 증거는 아직 없다.
  run b52c1f37-0c12-4142-9caa-5e1f96d50153는 현재 계속 실행 중이다.

## 2026-09-14 반복 편집 중단 후 요구 출처·검토 UI 수정 배포

- run `bdc6b832-13f9-491a-b117-aca3b177052d`는 준비 연결을
  mode_stopped→emergency_stop→mode_stopped로 되돌렸다(rev600~603 비교).
  같은 invalid 설정의 왕복 편집을 확인해 취소 API를 호출했고 terminal=true,
  state=cancelled를 받았다. 속도/조회 timeout만으로 중단한 것이 아니다.
- 아래 요구 출처 보존, profile/requirement semantic review UI 묶음을 Host 재시작과
  실제 `npm run build`로 반영했다. production entry 검증도 통과했다.
- 새 run `b52c1f37-0c12-4142-9caa-5e1f96d50153`, operation
  `complete-index-missing-inputs-20260914`를 같은 대화에서 시작했다.
  준비 확인 입력과 정상 정지 버튼을 실제 로직에 추가하고 원래 요구 전체를
  완성하라는 자연어 요청이다. 새 요청 후 supplemental 길이는 13,601자로 불변,
  draft도603으로 유지되어 불필요한 brief_changed revision을 만들지 않았다.
- 기존 브라우저 tab8에서 재인증 후 실제 새 요청/처리 중 화면을 확인했다.
  기존 native 파일은 변경하지 않았다. XG-SIM 실제 인수 시험은 여전히 미완료다.

## 2026-09-14 후속 대화 요구 출처 보존 수정 — 배포 대기

- 일반 USER 후속 메시지의 supplemental 자동 append를 제거하는 코드 묶음이
  Host/Agent 집중 회귀를 마쳤다. 기존 DB/이력은 변경하지 않았다.
- 메시지 출처는 마지막 human review의 채택 출처, 현재 draft 참조,
  그 review 이후 USER 원문을 결합한다. 부분/질문 artifact만 발행됐다고
  미채택 요구를 버리지 않는다. messageId+exact quote는 동일 대화 DB에서 검증한다.
- 새 요구 출처는 새 artifact semantic review에 결속된다. 상태 질문만으로
  기존 review를 일괄 stale로 만드는 신규 코드는 제거했다.
- Host planning drafts 59, 출처 관련17, Agent65 회귀가 통과했다.
  새 requirement/message 항목의 웹 본문/기술 상세 연결도 완료했다.
  웹 exact review universe는 출처 항목의 누락/ID·quote 변조를 거절하고,
  본문에는 한국어 요구 분야와 사용자 원문만 표시한다. UI 회귀/typecheck 통과.
  root의 별도 Vite production build도 성공했다
  (`.test-tmp/requirement-review-build-20260914`, 실제 서비스에는 미반영).
- 이 묶음과 앞선 profile 검토 화면 수정은 아직 실행 서버에 미배포다.
  실제 run bdc6b832는 rev600 invalid에서 신호 연결 진단을 받았고 계속 실행 중이다.
  아직 설계 편집이나 XG-SIM 운전 완료를 확인한 것은 아니다.

## 2026-09-14 의미 검사 및 이력 조회 배포 후 실제 설계 보완 재개

- design.read의 revision/latest_published/published_plan_digest와 profile 의미 검사를
  배포했다. 미존재 digest에서 None을 역참조하던 배포 전 결함도 수정했다.
- rev598에는 준비/정상 정지 입력이 실제로 없다. 설정만 수정하는 임시 제약을
  해제하고 원래 요구를 구현하는 요청을 같은 대화에 전달했다. 실행 ID는
  `bdc6b832-13f9-491a-b117-aca3b177052d`, operation은
  `complete-functional-index-design-20260914`다. 기존 저장 파일 덮어쓰기는 요청하지 않았다.
- 브라우저 기존 tab8을 재인증해 실제 새 요청과 처리 중 화면을 확인했다.
  Sol은 Host/Agent/web의 시험 역할·신호 연결 검토 항목을 구현 완료했다.
  한국어 역할·신호 이름 본문과 기술 상세를 분리하고 구 artifact의 검토 누락을
  재발행 안내로 처리한다. 집중 Host/Agent/UI 회귀와 typecheck는 통과, 아직 배포 전이다.
  현재 run은 이전 Host/Agent 프로세스를 사용하므로 종료 전 재시작하지 않는다.
  새 검토 규칙은 Host/Agent/web을 함께 배포하고 기존 draft로 재발행해야 한다.
- 실제 시뮬레이션 완료 증거는 여전히 없다. 기존 native operation을 보존한다.

## 2026-09-14 시험 설정 발행 후 의미 검증 차이 확인

- 역사적 native 관찰 전달 및 금지 출력 유지관측을 배포했다. 실제 DB projection에서
  briefRelation=previous_project_brief인 native_simulation_profile 관찰이 전달됨을
  읽기 전용으로 확인했다(`.test-tmp/inspect-native-observation-lineage.py`).
- 같은 대화의 run `3b2ce590-5b7e-4749-9083-141e84d94441`은 07:09:08 UTC에
  COMPLETED, rev598 published로 종료했다. 최신 검토 message는
  `65733f30-3d33-4a83-931a-6c853df8aff8`이다. indexAcceptanceBindings만 추가했고
  요청한 이전 rung 순서 복원은 수행하지 않았다. 실제 프로젝트에는 미적용이다.
- Host design.check는 valid를 반환했지만 같은 발행 artifact를 production MCP
  IndexAcceptanceBindings.resolve에 넣으면 `acceptance signal role ready has no
  compatible reviewed signal`로 거절된다. ready=mode_stopped, stop=emergency_stop인
  설정이다. 역할 kind 불일치와 서로 다른 기능의 alias를 시험 설정 검사에서
  진단해야 한다. `.test-tmp/check-current-index-bindings.py`로 읽기 전용 재현 가능.
- 과거 초안은 DB에 있었지만 Agent가 조회할 도구는 없었다. Sol이 design.read의
  revision/latest_published 조회를 구현했으나 아직 배포하지 않았다. latest는
  현재598이므로 native base591은 관찰의 reviewedPlanDigest로 정확히 선택할 수
  있도록 보완 중이다. 설정 의미 검증 연결과 함께 완료 후 배포한다.
- 같은 대화의 새 검토본에서 기존 native operation 연결이 사라지는 웹 결함은
  수정하고 production build까지 반영했다. 다른 대화에는 기존 operation을
  재사용하지 않는다. 새로운 검토본의 실제 simulation resume는 아직 미검증이다.

## 2026-09-14 기존 build 재연결 및 profile 관찰 전달 확인

- supplemental/rebind 1차 묶음을 Host 재시작으로 배포하고, 기존 native operation
  `6f9b17bc-027d-4396-af06-049b325d8507`에 runSimulation=true로 재개했다.
  동일 독립 XG5000 PID15588/HWND199644에서 project.verify_native_reopen을
  실제 실행해 native_reopen_binding을 다시 만들었다. 결과는 COMPLETED,
  engineeredProgramReady=true, reopenVerification=verified다.
- simulationVerification은 아직 not_verified이며, 실제 structured preflight는
  EXPLICIT_INDEX_ACCEPTANCE_BINDINGS_REQUIRED다. auto/manual/stopped와 현재
  신호·알람·타이머 목록을 보존한다. XG-SIM 운전 시나리오는 아직 실행되지 않았다.
- 웹 production build를 반영하고 실제 화면에서 저장·재열기·프로그램 검사 완료를
  확인했다. 새로고침 후 동일 planning 대화와 마지막 취소 요청도 보존됐다.
- 후속 대화의 brief 변경 시 과거 build 관찰이 projection에서 없어지는 별도
  문제를 확인했다. Sol 작업자가 Host→Agent 역사적 관찰 전달을 수정 중이다.
  이 관찰은 실행 권한이나 현재 draft 검증 결과로 취급해서는 안 된다.
- 금지 출력의 단발 관측을 유지 관측으로 바꾸는 runner 수정은 소스 회귀를
  마쳤으나 위 Host 재시작 이후 작성되어 아직 실행 프로세스에 반영되지 않았다.

## 2026-09-14 실제 생성·재열기 이후 시뮬레이션 연결 미완료

- 대화 `b0f18468-e072-4b4c-8455-7e2a722f41f3`의 run
  `0750277e-855a-4ede-b6a7-f6da233bd033`은 rev591 검토 발행으로 종료했다.
  native operation `6f9b17bc-027d-4396-af06-049b325d8507`은 기존 작업을
  reconcile/resume하여 생성·저장·독립 재열기 검증을 완료했다. 새로 만들지 않는다.
- XG-SIM 인수 시험은 아직 실행되지 않았다. 첫 시도는 세 운전 모드에 대한
  indexAcceptanceBindings 부재로 profile preflight에서 거절됐다. Host 재시작 뒤
  같은 operation을 재개한 시도는 saved-build 세션이 analysis 권한으로만 복구되어
  simulate 권한 검사에서 차단됐다. 두 결과 모두 PLC 시나리오 실패 증거가 아니다.
- profile 관찰의 Host→Agent strict schema 전달 수정은 배포됐다. 별도 검토한
  binding-only 시험 설정을 기존 build에 연결하는 simulationPlanRevision과 재시작
  후 simulation 권위 복구는 Sol 작업자가 구현 중이다. 현재 작업 파일을 완성본으로
  간주하거나 검증 없이 Host를 재시작하지 않는다.
- 후속 실제 요청 `자동·수동 모두 시험할 수 있게 시뮬레이션 설정을 보완해줘.
  프로그램은 그대로 유지해줘.`의 run `db066e45-33de-4060-96f7-7f76ade8c000`은
  설정 작성 대신 timeout recovery rung 순서를 두 번 변경했다. rev594에서 이를
  확인하고 취소 API가 terminal=true/cancelled를 반환했다. 속도 때문에 취소한
  것이 아니다. rev591 대비 rung 내용은 같고 순서만 다르며 bindings는 아직 없다.
  rev594와 이력을 보존했고 실제 생성 파일에는 이 변경을 적용하지 않았다.
- 다음 작업은 미완성 권위 연결을 끝내고, 기존 검토 build 기준 시험 설정만
  작성·검토한 뒤 실제 XG-SIM을 실행하는 것이다. 운전 중 모드 유지와 timeout
  복구 등 로직 정확성도 아직 미완료이며 정적 진단 0건으로 대체할 수 없다.

## 2026-09-14 리셋 불가능 조건 재현 및 실제 실행 반영

- 이전 run b6872caf-c6a5-4cd4-8ed2-edefc54b9ab1은 rev578까지 작성했다.
  모드 선언에서 stopped 공통 조건을 제거했지만, stopped OUT에 any_alarm OFF를
  추가하여 구조 검사 0건 상태에서도 세 알람 복구가 막혔다. 모든 recovery가
  mode_stopped ON과 해당 alarm latch ON을 요구하기 때문이다.
- `.test-tmp/probe-retained-alarm-reset.py`는 원본 DB를 읽기 전용으로 열어
  세 복구 조건 모두 false인 settled Boolean trace를 재현한다. XG-SIM 증거가 아니다.
- Host recovery_conditions.py는 단일 OUT writer의 필수 Boolean 조건을 추적한다.
  OR 대안은 교집합만 사용하며 다중 writer를 임의 해석하지 않는다. 충돌을
  design.check/publish에 반환하고 Agent는 ladder/control_network/recovery 편집을
  선택할 수 있다. Host 관련 테스트 56 passed.
- binding-only design.edit는 invalid 초안에서도 가능하되 기존 진단과 나머지
  작업물을 보존한다. Agent의 invalid/working read도 해당 editor를 노출한다.
  상충 지시문 수정, no-op 뒤 read/check 우선 경로 적용. Agent 회귀 40 passed,
  binding 경로 4 passed. 이 수치는 실사용 완료 증거가 아니다.
- 위 변경과 앞선 forced inspect/check 추론 분리를 Host 재시작으로 반영했다.
  기존 초안을 보존하고 짧은 `이어서 완성해줘.`로 재개한 현재 run은
  3f135fda-a5a5-4324-af7c-58668541bac1, operation
  resume-recovery-dependency-fix-20260914. 실제 check로 rev579 invalid,
  recovery_settled_conditions_contradict 3건 전달 확인. task131116 생성 중 관찰.
- 브라우저 기존 tab8에 재인증하여 같은 대화 복원 확인. 새 정책 문구를 사용자에게
  반복 입력시키지 않았다. native 생성/저장/재열기/XG-SIM 전체 검증은 미완료.

## 2026-09-14 확정 조회 호출과 설계 추론 분리 (차기 실행)

- capability에서 허용 도구가 design.inspect 또는 design.check 하나뿐인 경우,
  Qwen 추론을 끄고300토큰 호출 준비. 선택/수정 판단은 추론 및16000예산 유지.
  base extra_body를 복사하므로 이후 수정 호출의 추론 설정은 바뀌지 않음.
- test_workspace_reasoning_profile 4 passed. 현재 runb687은 재시작하지 않았음.
  task120409 processing=true 관찰. 실제 수정 품질/전체 인수 시험은 미완료.

## 2026-09-14 연결 회귀 및 첫 실제 도구 진행

- runb687에서 design.inspect tool.selected 확인. task117139 종료 후
  task118675 processing=true. 새 예산으로 최초 도구 단계 진행, rev574 보존.
- 결합 회귀: Agent workspace/runtime/추론/실패 shape/artifact + MCP 역할 시험
  60 passed + 5 subtests. Host draft/직렬화/receipt ID/edit impact 64 passed.
- 실제 설계 조건 변경, 검토 발행, native 생성/XG-SIM은 여전히 미완료.

## 2026-09-14 추론 예산 도구별 덮어쓰기 수정

- run2eb3 FAILED agent_output_contract (02:38:52 UTC), rev574 유지. 첫 tool 이벤트 없음.
  앞서 관찰한 서버 n_predict300은 capability가 설계 추론 예산16000을 덮은 값.
- _RequireWorkspaceToolUntilPublished.minimum_output_tokens 추가, 추론 켠 workspace만
  전체 응답 예산을 하한으로 유지. 비추론 모델의 기존 도구별 한도는 유지.
  test_workspace_reasoning_profile 3 passed. 실패의 유일 원인이라는 증거는 아직 없음.
- terminal 실행이므로 같은 대화에 짧은 계속 요청으로 새 실행 시작
  operation resume-reasoning-budget-fix-20260914. 다음에 run/slots 실제 예산 확인 필요.

## 2026-09-14 설계 추론 프로파일 재개

- run61d4 정상 cancel terminal 확인 후 Host 재시작 없이 새 Agent 실행.
  run2eb3a346-99f5-4f62-b415-ae4e9cfe5324, operation resume-reasoning-workspace-20260914.
  같은 대화에 '이어서 완성해줘.'만 전송, rev574 invalid 3 보존.
- Qwen task116830 processing=true, generated179, outputLimit300 (초기 단계).
  아직 design tool 이벤트 없음. workspace 추론 실제 요청/품질 개선 확인 전.
- 이전과 같은 오류 반복을 근거로 설계 전용 프로파일 적용했으며 느림만을 이유로
  재시작하지 말 것. 새 실행에서 실제 조건 수정 및 전체 요구 완료 여부 확인 필요.

## 2026-09-14 설계 작업 추론 설정 분리 (미적용)

- run61d4 rev572→574에서 stopped permit 재추가 왕복 확인, 실제 모드 조건 그대로.
- _design_workspace_model_settings 추가. Qwen 설계 작업만 enable_thinking=True,
  max_tokens16000. 일반 대화/서술 설정은 그대로. 원래 context 크기를 줄이지 않음.
- 이것은 반복 수정에 대한 품질 개선 가설이며 실제 성공 증거 아님. 현재 실행은
  아직 이전 설정. 프로파일/기존 Qwen 회귀 11 passed + 3 subtests (session26572 exit0).

## 2026-09-14 설명 편집과 실행 편집 구분 검증

- 기존 _ladder_edit_impact 반환을 재사용. 조건만 바꾼 실제 draft 저장 및
  description/condition/action/순서 구분 테스트 5 passed.
- Agent 지침에 executableChanged=false를 동작 수정으로 보지 말도록 연결
  (현재 run에는 아직 미적용). 관련 저장 경로 자체는 조건을 버리지 않음.
- run61d4 유지, rev572 working diagnostic0, task115618 processing=true.
  rev569→572는 network permit 변경이며 mode OUT 실제 조건은 그대로임.
  cn_mode_stopped에 any_alarm OFF를 추가했으므로 알람 복구 정지 조건과 충돌
  여부도 확인할 것. '오류0'을 동작 완료로 취급 금지.

## 2026-09-14 실제 웹 복원 확인 / Windows 관찰 실패

- CUA browser1 tab8 실제 AX 확인: 대화 준비됨, retained 대동인덱스 대화,
  '이어서 완성해줘.' 및 분석 중/작업 중단 요청 버튼 표시. 웹 연결 증거 있음.
- computer-use SKILL 및 guidance/api/confirmations 전체 읽은 후 sky 초기화.
  list_windows timeout → 2초 후 retry timeout → node_repl reset/초기화 후
  retry timeout. 이번 복구 절차 소진. 이전 좌표나 임의 handle로 조작 금지.
- XG5000 화면/프로젝트/XG-SIM 실행은 이번에도 확인되지 않음. 웹 연결 확인을
  native 검증 완료로 대체하지 말 것. 코드 및 목적별 MCP 경로 작업은 계속 가능.

## 2026-09-14 역할 바인딩 변경 적용 후 일반 문장으로 재개

- run41e3 정상 cancel terminal 확인 후 Host restart scheduled 및 재인증 성공.
  새 run61d4e32b-7156-49b5-b621-f705f4b83fc3 ANALYZING,
  operation resume-index-acceptance-bindings-20260914.
- 같은 대화에 '이어서 완성해줘.'만 전송. brief/초안 보존, 최신 rev566 invalid 2.
  mode network permit와 OUT 조건 불일치가 남아 있음. 기술 repair prompt 미추가.
- MCP 개발 실행기 import가 현재 src/native_simulation.py 및 새 role mapper를
  읽는 것 확인. Host/Agent 재시작 적용 경로 확인; 실제 XG-SIM 호출은 아직 아님.
- 웹 재인증 URL을 Codex browser panel로 열도록 요청. 실제 렌더 확인은 별도 필요.

## 2026-09-14 Host 시험 ID 검증 회귀

- _index_acceptance_scenario_ids는 receipt가 아닌 검토 설계에서 기대 시험 ID를
  계산한다. 실제 알람 ID 지원 및 누락/중복/미검토 알람 연결 거부.
- test_index_acceptance_receipt_scope.py 7 passed. 전체 receipt/실제 PLC 통합
  증거가 아니라 기대 시험 집합의 회귀 테스트다. legacy 8개 시험 이름 유지.

## 2026-09-14 Agent 바인딩 작성 및 최종 artifact 연결 (미배포)

- 유효한 design.read(index_acceptance_bindings) 이후 design.edit 노출.
  신규 설계 workspace 지침에 해당 인덱스 인수 조건일 때만 기존 ID 역할 연결 작성
  안내 추가. 사용자에게 내부 역할 이름을 입력시키지 않는다.
- derive_new_project_plan_artifact가 수동 직렬화하므로 새 필드를 빠뜨리던 경로
  수정: design.indexAcceptanceBindings 보존. 없는 기존 artifact는 필드 생략 유지.
- Agent test_index_acceptance_artifact.py 2 passed: read→editor와 artifact 보존 및
  digest 변경 확인. 모의 의미 검증이나 실제 XG-SIM 시험 완료 아님.
- 아직 Host 의미 검증/receipt 통합 회귀 및 live 배포 필요. 현재 저장 초안에
  바인딩을 직접 덮어쓰지 않았고 실행 중 Agent는 구버전이다.

## 2026-09-14 인수 시험 runner 실제 ID 연결 (미배포)

- MultiModeIndexSimulationRunner가 design.indexAcceptanceBindings를 해석한다.
  신호/알람/타이머는 실제 ID를 사용하며, 수동 시험은 별도 manual_start 역할 사용.
  잘못된 명시 profile은 constructor에서 거절하며 generic 시험으로 묵살하지 않는다.
- legacy profile은 유지. 명시 profile의 stop은 motion/running 정지를 요구하되
  선택된 모드까지 반드시 해제하도록 강제하지 않는다 (선택 상태와 운전 상태 분리).
- Host multi-mode receipt의 fault scenario ID 집합은 명시 바인딩의 실제 알람과
  정확히 일치해야 함. 바인딩 세 역할/서로 다른 알람/전체 알람 커버리지 검사.
- MCP tests: 17 passed + 5 subtests. 새 테스트는 임의 ID/별도 수동 입력/정지 모드
  포함 전체 trial 흐름을 mock으로 실행; 실제 XG-SIM 성공 증거 아님.
- 남은 연결: Agent 작성 지침/선택 경로 및 Host 전체 의미 검사·receipt 회귀,
  live 배포 후 실제 생성/저장/재열기/XG-SIM. 기존 run은 아직 구버전 실행 중.

## 2026-09-14 역할 바인딩 draft 편집 연결

- planning_drafts의 read/replace_section에 index_acceptance_bindings 추가.
  기존 native_design 컬렉션을 유지하면서 해당 객체만 교체한다.
- 직렬화/기존 객체 보존 테스트 3 passed. catalog 기대 항목에 새 section 반영.
  전체 planning_drafts 재실행 49 passed (session79290 exit0).
- runner/receipt 연결과 Agent 도구 선택·지침 연결은 아직 남음. 현재 live run은
  이 미배포 변경을 사용하지 않음. /slots task113414 processing=true 관찰.

## 2026-09-14 역할 바인딩 설계 직렬화 연결 (실행 전)

- Agent NewProjectNativeDesign / Host ProjectPlanNativeDesignV1에 선택적
  index_acceptance_bindings 추가. nested profile은 schema/signals/modes/alarms/timer.
- None은 exclude_if로 생략하므로 기존 serialized design에 새 null 필드를
  넣지 않는다. Host serialization tests 2 passed (실행 의미 검증 아님).
- 아직 draft 편집 도구, runner, receipt 검증 연결 전이다. 실행 지원 완료로
  취급 금지. MCP IndexAcceptanceBindings.resolve가 전체 역할/참조 검사를 담당할
  예정이며 현재 API 모델은 전송 형식만 보존한다. 배포 전 통합 필요.

## 2026-09-14 인수 시험 역할 바인딩 구현 시작 (실행 연결 전)

- MCP live/index_acceptance_bindings.py 추가: 명시적인 검토용 역할→실제 ID
  바인딩을 원본 설계 변경 없이 해석. 자동/수동 시작 버튼 분리 또는 공용 지원,
  별도 정지 모드 허용, 실제 알람 latch 및 timeout timer 연결.
- tests/test_index_acceptance_bindings.py 8 passed. 임의 ID와 세 모드 설계 보존,
  버튼 분리/공용을 검사. 실제 XG-SIM 시험 결과 아님.
- 아직 production 호출자 없음. 다음 작업은 검토 아티팩트/다이제스트에 바인딩을
  포함시키고 MultiModeIndexSimulationRunner의 고정 문자열을 이 바인딩으로
  대체하는 것. Host receipt 검증도 실제 alarm ID를 사용해야 함.
  미검토 외부 별도 인자로 바인딩을 주입하거나 설계를 예제 이름으로 바꾸지 말 것.

## 2026-09-14 컴파일 경계 재검증 / 남은 실행 연결

- rev556 readonly replay: Host/MCP v4 컴파일 결과의 모든 필드 동일
  (`DIFFERENT_FIELDS []`). 내부 출력의 빈 permit 허용과 별개로 digital_output으로
  바꾼 음성 사례는 양쪽 모두 거부. 실제 XG5000 빌드/시뮬레이션 증거는 아님.
- Agent `.venv-dev`에서 wait_step_release_contract + native_held_state: 8 passed.
  Host 테스트 환경에는 Agent openai 의존성이 없으므로 이 결합 테스트에 쓰지 말 것.
- 최신 관찰: 동일 run41e3 ANALYZING, draft559 working diagnostics0.
  서버 /slots task112042 processing=true 확인. 느리다는 이유로 새 요청 시작 금지.
- 실제 다중 모드 시험기는 REQUIRED_MODES/ALARMS와 신호 이름에 의존한다.
  임의로 생성한 설계의 역할을 검토된 시험 계획에 연결하는 작업이 아직 필요하다.
  예제 이름으로 강제 변경하거나 mock 통과를 실제 인수 시험 완료로 보고하지 말 것.

## 2026-09-14 XG5000 실행 확인 / 일반 대화 원칙

- 기존 실행 요청 session24455 exit0. Get-Process에서 PID4384, MainWindowTitle XG5000
  확인. 창 내용/프로젝트/시뮬레이터 실행 증명 아님. 중복 실행하지 말 것.
- sky.list_windows timeout → 재시도 app-server exited → reset/초기화 후 동일 오류.
  Computer Use 화면 확인 실패. 과거 좌표나 가짜 window handle로 조작 금지.
- 사용자는 매번 원본/물리PLC 금지 등 내부 지시를 대화에 넣는 것에 명확히 반대.
  기존 brief는 append_planning_clarification에서 유지됨. plain followup 보존 테스트1passed.
  향후 기술적 repair prompt를 일반 사용자 메시지로 계속 쌓지 말 것.
- rev556 manual start_all에 버튼 재추가됐음. 현재 run41e3, Qwen task108976 생성중.
  release 및 MCP 내부 permit 수정은 아직 live 배포 전. 전체 검증 미완료.

## 2026-09-14 Host/MCP 컴파일 연결 비교 및 XG 실행 요청

- .test-tmp/compare-native-compilers.py readonly rev553 비교: XGK 주소 정규화 후
  Host pass/MCP empty internal permit 거부 확인. MCP network 조건에서 internal_bit만
  빈 permit 허용 및 conditions allow_empty 인자를 추가해 Host와 일치시킴.
- 재실행 Host COMPILE_PASS / MCP COMPILE_PASS (v4). 출력 전체 동일성/실기 검증 아님.
  release 변경과 이번 MCP 변경은 live 배포 및 regression 추가 확인 필요.
- XG5000 프로세스 없음 확인 후 C:\XG5000\XG5000.exe Start-Process 요청.
  exec session24455는 아직 반환 안 됨, 후속 process 조회도 없음. 승인창 추정 금지.
  사용자에게 승인창 여부 질문했으나 답변 아직 없음. 중복 launch 금지, 같은 handle 확인.

## 2026-09-14 첫 대기 단계 release 회귀 검증 (미배포)

- test_wait_step_release_contract.py 추가: 첫 대기 단계의 digital input ON과 final/reset
  OFF 허용, final 또는 reset 해제조건 누락 시 거부. 선언과 대응 advance rung 함께 변경.
  Agent 테스트 3 passed. 실제 XG-SIM 시험이 아닌 계약 회귀 검사.
- 회귀 중 recovery에도 start_all만 보던 동일 제한 발견. Agent/Host/MCP 세 계층의
  recovery release 후보도 첫 단계 digital input까지 일치시킴. 역조건 필요 자체는 유지.
- 전체 MCP/Host 통합 회귀 및 live 배포는 아직 필요. run41e3 현재 상태 재조회할 것.

## 2026-09-14 수동 시작 조건 왕복의 하위 검증 원인 수정 (미배포)

- rev553 실제 diagnostic: require_start_release final inverse start condition.
  첫 대기 단계에서 버튼을 받는 설계인데 하위 세 계약은 start_all만 역조건 후보로 삼음.
- Agent runtime / Host native_projects / MCP native_create 모두 첫 단계 complete 조건의
  digital_input도 release 후보로 포함. final 및 reset의 역조건 검사는 유지.
- 저장 rev553 Host 재검사 diagnostics0 확인. 실제 래더/모드 유지/XG-SIM 증명 아님.
- 새 회귀 fixture 테스트 및 MCP 계약 검증 추가 필요. 현재 live에는 미적용, run41e3 유지.

## 2026-09-14 왕복 수정 중단 후 Host 변경 적용 재개

- rev550 manual_move_btn start_all 재추가, rev551 재제거: rev549와 동일해지는
  왕복 수정 확인. run cfbac858 정상 cancel, Host restart 요청 후 새 인증 성공.
- run41e3d8c0-4bc4-4133-b5bd-7023a57a31a3 analyzing 시작,
  operation resume-host-dependency-patch-20260914. mode 조건 실제 변경 우선 요청.
- Host existing-item patch 범위 및 실패 이유 표시 변경 포함 재기동 요청함.
  웹 세션 재인증/실제 화면 확인은 다음에 필요. native 전체 미완료.

## 2026-09-14 편집 거부 원인 표시 구분 (미배포)

- agent_client가 design edit 거부 진단을 no-change/scope/schema/기타 고정 reason code로
  구분해 기록하고 app이 짧은 사람용 문구로 표시. 원문 JSON/경로/비밀값은 복사하지 않음.
- 이전 두 실패 event16690/16696은 generic code만 있어 정확한 원인 복구 불가.
- Host 기존 항목 patch 허용과 함께 아직 미배포. run cfbac858 유지, rev549 이후
  새 수정 확인 전. 전체 native/XG-SIM 완료 아님.

## 2026-09-14 장기 생성 반환, 수동 진입 선언 수정

- runcfbac858 Qwen task89497이 15k 이상 생성 후 실제 edit 반환.
  rev549 seq_manual.start_all에서 manual_move_btn ON만 제거. 첫 대기 단계의
  버튼 ON 조건은 유지. 아직 대응 래더/모드 유지/새 시작 요구 완료 아님.
- 다음 task105339 processing=true/generated0 확인. 기존 요청을 시간 이유로 중단하지 않음.
- Host 기존 항목 patch 허용 변경은 여전히 미배포. 전체 native/XG-SIM 미완료.

## 2026-09-14 Host 기존 항목 연관 수정 허용 (미배포)

- planning_drafts section edit에서 모두 기존 ID의 patch_item인 batch는 첫 diagnostic의
  section/item 제한을 넘어서 수정 가능. Agent read/editor 노출만 바꿔서는 Host가
  repair_section_mismatch로 거부하던 실제 연결 문제 수정.
- create/remove 제한, schema/revision/no-op 검사와 최종 check/publish 조건은 유지.
- test_planning_drafts 49 passed. 새 테스트는 오류 남은 상태의 다른 영역 실제 저장 및
  후속 check invalid 유지를 확인. Host 미재시작이므로 live에는 아직 미적용.
- 현재 runcfbac858 계속 생성. 마지막 Qwen task89497 processing=true/generated132.
- 시뮬레이터 explicit binding 구현은 아직 미완료, 전체 native 검증도 미완료.

## 2026-09-14 현재 native acceptance 연결 제약 재확인

- native_simulation.py MultiModeIndexSimulationRunner.supports는 모드/알람 ID 고정 집합을
  요구하고 수동 trial은 manual_mode_btn pulse 후 start_btn을 사용한다. 현재 별도
  manual_move_btn/select 입력 설계의 사용자 시나리오와 다름. 단순 이름 치환 금지.
- server.py verify_native_simulation는 supports false이면 단일 객체 NativeSimulationRunner로
  분기. Host receipt도 고정 alarm scenario ID를 검증하므로 MCP 한 곳만 바꾸면 안 됨.
- 기존 verify_mne_rung_patch_profile은 컴파일된 MNE 단일 프로그램/정상상태 bit 시나리오이며
  2..16 phase, bit 출력만 지원. 전체 신규 프로젝트의 계수/시간/복구 검증 대체물 아님.
- 다음 구현 범위: 검토된 plan에 시나리오별 drive/observe 역할 및 실제 signal/mode/alarm/
  timer ID를 명시 결합 → plan digest에 포함 → MCP 실행/Host receipt가 같은 binding 사용.
  input-only byte 쓰기, STOP-before-restore, 실제 native evidence는 유지할 것.
- 현재 runcfbac858 유지. 마지막 Qwen task89264 processing=true/generated29.

## 2026-09-14 연속 편집 경로 실제 적용 재개

- runad799 rev543/545 자동/수동 모두 stopped OR self 추가했지만 선택 입력이 바깥
  필수 조건으로 남아 모드 유지 해결 안 됨. rev546 초안 보존 후 정상 cancel.
- Host 재시작 없이 runcfbac858-0a23-4d65-8af4-264a2aee3407 analyzing 재개.
  operation resume-continuous-edit-mode-20260914. 직전 두 Agent routing 변경 적용.
  정지 선택/운전 유지 분기를 실제 분리하고 나머지 원래 요구 완료하도록 요청.
- CUA tab6에서 ControlStudio 실제 분석 중 화면 확인. native sky list_apps는 두 번
  timeout 후 kernel reset/reinitialize에도 app-server exited 오류. native 화면 미확인.
- 전체 XG5000/XG-SIM 완료 아님. 원본/물리 PLC 쓰기 금지 유지.

## 2026-09-14 오류 초안의 연관 영역 편집 경로 (다음 Agent 실행 적용)

- invalid/working design.read는 읽은 sections의 editor/read/check를 제공하도록 수정.
  이전에는 남은 첫 diagnostic이 조회한 연관 영역 편집기를 덮어써 다중 영역 수정을 막았음.
  게시 도구는 노출하지 않고 Host 검증/수정 권한은 변경하지 않음.
- runtime/compaction 테스트 40 passed. 현재 Agent는 재시작하지 않아 아직 미적용.
- 마지막 확인 Qwen task88820 processing=true/generated0, rev544까지. 전체 완료 아님.

## 2026-09-14 성공 편집 후 연속 작업 경로 (다음 Agent 실행 적용)

- runtime.py 성공한 section edit 이후 check만 강제하던 경로를 해당 editor/read/check로 변경.
  게시 도구는 제공하지 않으며 Host revision/검증 및 부분 실패 재검사 경로는 유지.
- 관련 runtime/compaction 테스트 39 passed. 현재 실행 보존, 아직 live Agent에는 미적용.
- run ad799d0a rev543 mode_auto 조건에 stopped OR auto를 추가했지만 auto_mode_select가
  여전히 바깥 필수 조건이라 운전 중 선택 해제 문제는 해결되지 않음. rev544 검사만 수행.
- 마지막 모델 task88781 processing=true/generated28. 전체 native/XG-SIM 미완료.

## 2026-09-14 모드 상태 로직으로 후속 작업 재개

- runfb4d5a95 rev531 자동 step_move_cmd.complete_all move_complete ON 변경,
  rev533 대응 rung_seq_auto_move_to_wait contact ON 변경. 설명엔 OFF 잔존.
- rev535 mode IO 설명만 변경, rev537 narrative 원문 인용/요구 문장 재작성,
  rev540 이미 alarm RST 앞인 도어 auto/manual reset 순서만 서로 교환.
  모드 전환 요구 실제 로직은 여전히 미수정. 반복 구간 중단 위해 정상 cancel.
- Host 재시작 없이 runad799d0a-869f-4189-8f27-37bd9fa5b4c1 analyzing,
  operation resume-mode-state-logic-20260914. mode OUT/network의 운전 중
  상태 유지+정지 선택을 실제 구현하도록 요청. 초기화 순서/계수/안전 보존.
- 전체 native/XG-SIM 및 fresh-start 동작은 여전히 미완료.

## 2026-09-14 다섯 복구 reset 순서 이동 실제 저장

- runfb4d5a95에서 rev522 도어 자동, rev523 도어 수동, rev525 타임아웃 자동,
  rev527 타임아웃 수동, rev529 비상정지 자동 sequence reset이 각 alarm RST
  앞으로 이동 저장됨. 조건/액션 변경이 아닌 실제 rung 순서 변경 확인.
- 여전히 모드 전환/새 시작/전체 reset 의미 및 실제 native XG-SIM 검증이 남음.
  순서 dependency 해소만으로 전체 복구 성공을 주장하지 말 것.
- 마지막 Qwen task83533 processing=true. 같은 실행 계속 중, 재시작 안 함.

## 2026-09-14 복구 dependency/순서 activity Host 배포 및 재개

- runfdae7441은 rev518까지 추가 순서 변경 없이 I/O 편집 실패/check/read.
  정상 cancel 후 Host restart로 dependency observation과 사람이 읽는 순서
  activity label 적용. 초안 보존 확인 후 같은 대화에서 재개.
- 새 runfb4d5a95-10d9-4920-9501-ca10cfbb3d74 analyzing,
  operation resume-reset-dependencies-20260914. 다섯 dependency 쌍을 읽고
  각 sequence reset을 해당 alarm RST 앞으로 옮긴 뒤 나머지 요구 완성 요청.
- 웹 재인증 표시 요청도 수행. 실제 native/XG-SIM 전체는 여전히 미완료.

## 2026-09-14 복구 순서 dependency 조회 정보 추가 (Host 미배포)

- executionObservations에 potentialResetOrderDependencies 추가: 앞 signal RST와
  뒤 sequence_reset의 필수 ON 참조 쌍을 제공. 중간 같은 신호 writer면 제외,
  OR는 모든 경로에서 필수인 경우만 포함. 조건 동시 실행 여부는 미판정이므로
  potential/static이며 새 publication gate나 자동 수정 아님.
- test_reset_order_observations 1 passed. rev517 대조에서 door auto/manual,
  timeout auto/manual, estop auto 다섯 쌍 확인. 현재 순서 편집 대상이 명확해짐.
- Host 재시작 전이라 live read에는 아직 미적용. app.py 순서 activity 표시도
  함께 미배포 상태. 기존 runfdae7441 상태는 다음에 확인할 것.

## 2026-09-14 순서 편집 실제 호출/저장 확인

- runfdae7441 event16090 design.move_ladder_rung 실제 호출. rev514에서
  rung_seq_manual_reset이 rung_recovery_estop 앞으로 이동함. 나머지
  알람별 reset rung은 아직 뒤에 있으므로 전체 복구 순서 완료 아님.
- compare-design-revisions.py에 RUNG_ORDER before/after 출력 추가.
- Host app.py의 순서 편집 이벤트에 사람용 label/result 추가(미배포).
  기존 이벤트에는 tool_name만 표시됐음. test_ladder_order_activity.py 추가.
  Host는 실행 보존을 위해 재시작하지 않았음.

## 2026-09-14 순서 편집/크기 기반 이력 실제 실행 적용

- runb708dbb0 rev511까지 계수 조건 수정 후 설명만 재수정하고 복구 순서는
  그대로. 순서 도구가 노출되지 않던 코드 교정을 적용하기 위해 정상 cancel.
- Host 재시작 없이 same draft runfdae7441-6d41-4589-9444-81cbc9fbf290
  analyzing 시작, operation resume-scan-order-tools-20260914.
- 정상 ladder read 후 move_ladder_rung 노출 및 크기 기반 이력 정책 적용됨.
  같은 alarm ON을 쓰는 sequence_reset을 alarm RST 이전으로 이동하는 실제
  작업부터 요청. 기존 계수/수동/이동 수정 보존, 나머지 전체 요구 유지.
- native XG-SIM 전체는 여전히 미완료. 현재 revision 및 실행 결과 재조회 필요.

## 2026-09-14 정상 래더 조회 후 순서 편집 도구 허용 (미배포)

- valid/published design.read가 ladder_rungs를 반환해도 move_ladder_rung이
  숨겨져 있었음. 이제 읽은 래더의 scan-order 수정 도구도 노출.
  Host의 기존 move_item/revision 검증은 유지, 새 native 권한은 없음.
- 정상 구조 검사 통과와 별개로 alarm RST 이전 sequence reset 배치를
  고칠 수 있게 함. narrative만 읽었을 때는 순서 편집 노출 안 함.
- 관련 테스트 기대 도구 집합 갱신 및 회귀 검사 추가. 현재 runb708dbb0에는
  아직 미적용; 크기 기반 history 변경과 함께 다음 Agent 실행부터 적용.

## 2026-09-14 계수 reset_btn 실제 저장 확인

- runb708dbb0 rev506에서 MOV0 D00000 조건 init_btn ON을 reset_btn ON으로 수정.
  rev507 INCP D00000 억제 조건도 init_btn OFF에서 reset_btn OFF로 변경.
- rev507 static check diagnostics 0 / unwritten internal 0 확인. 실제 PLC scan,
  복구 단계 reset 순서, 모드 전환 및 fresh start는 아직 검증 안 됨.
- 웹 open_in_codex 요청은 queued 반환이므로 화면 표시 확인으로 간주하지 말 것.

## 2026-09-14 도구 이력 네 번 고정 절단 제거 (다음 Agent 실행 적용)

- _compact_design_workspace_history가 문맥 크기와 무관하게 최근 4교환만
  남기던 정책 제거. 완료된 교환을 serialized UTF-8 byte allowance까지 유지하고
  넘으면 오래된 교환 단위로 제거. 기본 allowance는 context 상수의 3/4 bytes:
  tokenizer 측정이 아닌 보수적 크기 proxy이며 schema/output 여유를 남김.
- 사용자 요구 전부 및 최신 도구 교환은 크기 초과에도 조용히 버리지 않음.
  작은 이력 20교환 유지, 극소 allowance에서도 요구와 마지막 call/result
  대응 보존 검사 포함 관련 suite 38 passed.
- 현재 runb708dbb0는 이전 all-UserPrompt 보존 코드로 진행 중이며 이 새
  크기 기반 변경은 다음 Agent 실행에서 적용. 단순 배포 목적으로 취소 안 함.

## 2026-09-14 반복 수정 중단 후 계수 초기화/복구 실제 작업 재개

- run66d53fc1은 rev498 manual_start_allowed 참조 변경 후 rev500 원래
  manual_move_btn으로 되돌리고 rev501/502 추가 동작 변경 없이 check/read
  반복. 단순 지연이 아니라 저장된 왕복 수정 확인 후 정상 cancel.
- Host 재시작 없이 같은 초안에서 runb708dbb0-e118-41b3-b940-ceff6c4d12da
  analyzing 시작, operation resume-counter-reset-and-recovery-20260914.
  모든 UserPrompt 보존 Agent 수정도 이 새 실행부터 적용.
- reset_btn으로 MOV0/INCP 억제, alarm RST 이전 단계 reset 순서부터 실제
  수정하고 모드 전환/fresh start 나머지도 완성 지시. 기존 이동 출력 및
  수동 start_btn 제거 보존, 원본/물리 PLC 금지 유지. 아직 native 미완료.

## 2026-09-14 수동 held-command 시험의 완료 센서 혼동 제거

- 후속 관찰 rev497: seq_auto.start_all에서 세 alarm OFF 접점 제거,
  mode_auto ON/auto_start_allowed ON만 남음. check diagnostics 0이나
  fresh-start 구현 증거는 아님. actual rung 안전 조건은 다음 확인 필요.
  Qwen task77057 processing=true. 위 변경 방향 및 실제 초기화/복구가 남음.

- manual trial에서 첫 완료 후 버튼 ON을 유지하고 완료 센서만 OFF로
  바꾼 뒤 계수 1 유지/이동 OFF를 관찰하도록 추가. 기존에는 완료 센서
  ON이 재이동을 막는 것인지 command re-arm이 막는 것인지 구분 못 했음.
- mocked 입력/관찰 회귀 검사 추가 포함 8 passed, 5 subtests passed.
  실제 XG-SIM 검증은 아직 수행되지 않았음.
- run66d53fc1의 design.edit_sequences 형식 오류 event15913 확인.
  오류 후 동일 Qwen task76654 processing=true 관찰; 실패한 편집은
  저장되지 않았고 당시 draft495 valid. 모델의 복구 응답은 계속 확인 필요.

## 2026-09-14 fault recovery 시험의 held-start 누락 수정

- MultiModeIndexSimulationRunner의 세 fault trial에서 start_btn을 ON으로
  유지한 채 fault 발생/원인 제거/reset을 진행하고 이동 OFF를 확인하도록 수정.
  버튼 해제만으로도 이동 OFF인지 확인한 뒤 새 누름으로 재시작한다.
- 종전 시험은 start OFF인 상태의 reset만 보고 freshStartRequired=true를
  기록했으므로 held-start 자동 재출발 결함을 놓칠 수 있었다.
- 시험 입력/관찰 순서 회귀 검사 추가: 8 passed, 5 subtests passed.
  이는 mock 기반 시험 절차 검증이며 실제 XG-SIM 통과 증거가 아니다.
- 고정 profile signal/mode ID에 묶인 runner의 범용 binding 문제는 아직 남음.

## 2026-09-14 후속 요구 이력 보존 / 수동 시작 실제 수정 확인

- Agent `_compact_design_workspace_history`가 최초/마지막 UserPrompt만 남겨
  독립적인 중간 수정 요구를 유실하는 문제 수정. 모든 UserPrompt를 순서대로
  유지하며 최근 도구 교환 4쌍은 유지. 반복 압축에서도 세 후속 요구 및
  tool-call/result 대응을 보존하는 회귀 검사 추가. 관련 테스트 37 passed.
- 이 Agent 코드 수정은 현재 실행에 아직 미적용. 실행 중인 run66d53fc1을
  단지 코드 반영을 위해 취소하지 않았음. 다음 정상 실행에서 적용됨.
- DB rev492: cn_move_cmd.step_id=null, permit_all의 step_move_cmd 제거 확인.
  rev489 이동 OUT의 자동/수동 명령 및 완료대기 OR 분기는 유지됨.
- DB rev494: rung_seq_manual_start의 별도 start_btn 접점 제거 실제 저장 확인.
  Qwen task75625 processing=true 확인. 이후 상태는 다시 조회해야 함.
- 초기화/정지 중 모드전환/알람 복구 후 fresh start와 native XG-SIM 전체는
  아직 검증되지 않았음. 기존 테스트 통과나 draft valid를 전체 완료로 보지 말 것.

## 2026-09-14 자동/수동 공통 이동 선언 충돌 방향 교정

- runfc680... rev479에서 rung_move_cmd_out에 step_manual_move_cmd ON OR 경로
  추가. 안전 permit 보존. 완료대기 step은 아직 없음.
- rev481/482 cn_move_cmd permit에 any_alarm OFF 추가했으나 step_id 자동 고정
  유지. rev483에는 permit_all에도 step_move_cmd ON 추가해 충돌을 악화시킴.
  중간 래더 편집 형식 오류도 tool.failed로 미저장 확인.
- 잘못된 수정 방향 교정 위해 정상 cancel 후 Host restart 없이 같은 초안
  재개: run66d53fc1-d238-449e-be21-388d75eb7549 analyzing,
  operation resume-shared-motion-network-20260914. cn_move_cmd.step_id null,
  permit_all의 step_move_cmd만 제거(안전 보존), 기존 OR rung 보존, 이동대기
  출력 유지와 수동 별도 start_btn 제거 및 원래 나머지 요구 완성 지시.
- native/XG-SIM 전체 미완료. 최신 초안은 DB에서 다시 확인해야 함.

## 2026-09-14 인용 수정 run 완료 / 웹에서 실제 이동 로직 보완 재개

- run67f... COMPLETED, draft474 published. Agent 최종 계약 통과 후 UI에
  검토안 반환 확인. 실제 요구 동작 미완성이므로 검토 완료 확인란은 선택 안 함.
- 저장 래더 확인: rung_move_cmd_out은 step_move_cmd ON만 참조(수동 없음),
  rung_seq_manual_start에는 start_btn ON이 여전히 남아 있음.
- CUA currentControlTab(browser1/tab4) 실제 메시지 입력/Enter로 자동·수동
  이동 중 출력 유지, 완료/알람시 OFF, 별도 수동 시작 버튼 제거 및 나머지
  원래 reset/모드/복구 요구 완성을 요청. 기존 인용/안전 보존, 원본/PLC 금지.
- 새 run fc680a2c-bc47-4181-ab9b-a1e9f3caa3cc ANALYZING,
  operation message:6bfc8dc2-fb5d-42d8-b200-b2f076cafdb9.
  웹에 새 요청/분석 중 표시 확인, markHandoff. native/XG-SIM 미완료.

## 2026-09-14 rev472/473 인용 수정 저장 및 Agent 최종 계약 통과

- rune67f...에서 narrative.requirement_sources.sequence 중복 '한 칸 이동'
  제거 저장. operating_modes 인용 및 user_requirements도 기존 supplemental
  문장으로 변경. native_design의 운전 로직 변경은 없음.
- replay-published-contract.py 결과 AGENT_FINAL_CONTRACT_PASS 확인.
- compare-design-revisions.py가 native_design만 비교하던 관찰 누락 수정:
  narrative/technical_configuration의 변경 필드도 이제 출력함. 앞선
  native diff 없음만으로 인용 미수정을 단정하면 안 된다.
- 전체 실제 동작, 별도 XG5000 생성/XG-SIM은 여전히 미완료.

## 2026-09-14 정상 read 후 narrative 편집 허용 배포

- rune245... 정상 cancel 확인. Host 재시작 없이 Agent 재개해 source 변경 적용.
- 새 run67f9874b-6b8e-4c0e-a97d-7d849a4738a1 analyzing,
  operation resume-narrative-editor-20260914. 기존 인용 중복 수정부터 실제
  저장하고 남은 원래 동작을 완성하도록 요청. 원본/물리 PLC 금지 유지.
- 인용 수정 및 native/XG-SIM 완료 증거는 아직 없음.

## 2026-09-14 새 실행의 인용 편집 도구 차단 해제 (미배포)

- 현재 rune245...는 publish 전 check/read 반복. source_error 플래그는
  run-local이며 새 실행에서 false로 시작. valid design.read가 narrative를
  반환해도 generic design.edit를 의도적으로 제외해서 인용 수정이 막혔음.
- valid/published read 후 읽은 모든 section editor 노출하도록 변경.
  narrative/configuration도 수정 가능. 구조 검사 통과는 불변 의미가 아니다.
- workspace runtime + internal permits + compaction tests 58 passed.
  Agent 재개 필요(Host 재시작 불필요). 실제 인용/운전 로직 저장은 아직 없음.

## 2026-09-14 복구 지시 보존 및 오류 위치 안내 Agent 재개 적용

- compaction + workspace runtime + internal network tests 57 passed.
- run7893... 정상 cancel, Host 재시작 없이 새 Agent 실행:
  e245b2ee-5725-4d43-9595-b50f9d5b61de (resume-retained-repair-20260914),
  analyzing 확인. draft465 published 유지. 인용 sequence 중복 원문 수정과
  원래 남은 실제 동작 완성을 요청했다. 웹 세션/Host는 재시작 안 함.
- 마지막 /slots task67831 processing=false이므로 새 모델 요청 착수 여부는
  다음 턴에서 재확인할 것. native 생성/XG-SIM은 여전히 미완료.

## 2026-09-14 인용 복구 지시가 compaction에서 유실되는 문제 수정 (미배포)

- _compact_design_workspace_history는 최초 UserPromptPart와 최근 tool-only
  이력만 보존했다. validate_published_workspace가 enqueue한 원문 인용 수정
  지시도 UserPromptPart이므로 compaction 후 사라질 수 있었다.
- 최초 요청 외 최신 후속 UserPromptPart도 보존하도록 변경. 최근 도구 4쌍
  유지. test_workspace_compaction_repair 1 passed: 도구 8쌍 후에도 원래 요구와
  중간 삽입한 인용 수정 지시가 함께 남는지 확인.
- 앞서 추가한 인용 오류 위치 안내와 함께 Agent 재개 시 적용 필요. 현재
  run7893.../draft465 published 유지, Qwen task67708 processing true 확인.
  원문 인용 편집은 아직 저장 안 됐으며 native/XG-SIM 미완료.

## 2026-09-14 rev465 재확정 / 원문 인용 오류 복구 중

- run7893...가 실제 래더 수정 없이 inspect/check/read/publish_review 수행,
  rev465 published. rev462에서 수동 start_all에 manual_move_btn ON 재추가된
  상태는 남아 있음. static valid는 실제 요구 완료가 아니다.
- Agent final contract 읽기전용 재현에서 requirement_sources 원문 불일치:
  sequence 인용의 '한 칸 이동' 중복. validate_published_workspace는 해당
  실패시 repair_requirement_sources 플래그를 설정해 편집 루프로 돌아간다.
- Agent 오류 메시지에 narrative.requirement_sources[index]와 field 및 원문
  조회/정확 복사 안내 추가(설계 보존). 이 메시지 수정은 아직 미배포.
- live /slots task66773 processing=true, generated0 마지막 확인. rev465 유지.

## 2026-09-14 수동 inert-wait 검사 배포 / 동일 초안 재개

- run cee57c88-d093-4c23-9a8a-49ec040bd5c1 정상 cancel 확인, Host restart 후
  재인증 및 새 run 7893a161-91b8-4c6d-92f7-c5b4f28c6259 analyzing.
  operation resume-manual-idle-20260914. rev461의 오진 때문에 검사 갱신 및
  기존 idle 구조 보존, 실제 출력/reset/모드/복구 완성을 요청함.
- 수동 inert-wait Host 수정 배포. 기존 초안/원래 요구사항 보존.
- 브라우저4/browser1 currentControlTab에서 해당 새 요청과 분석 중 확인,
  markHandoff. 만료된 tab3 닫음. 실제 native 생성/XG-SIM은 아직 미완료.

## 2026-09-14 rev460 수동 idle 진입 검사 수정 (Host 미배포)

- 새 run에서 rev458 검사 갱신, rev459 scope 변경 후 rev460 수동 start_all의
  manual_move_btn ON도 제거. 첫 step_manual_wait.complete_all은 버튼 ON 유지.
- Host manual_sequence_missing_operator_start가 startAll 버튼만 인정하는
  문제에 대응: 첫 단계의 완료 조건이 명령 입력 ON이고 해당 단계에 의존하는
  signal writer 또는 control network step 연결이 없으면 inert wait 진입 허용.
  버튼 없는 무조건 이동이나 idle 의존 출력은 여전히 진단. 이는 전체 동작
  안전성을 증명하는 분석은 아니며 별도 native 실행 시험 필요.
- test_recovery_input_polarity 5 passed(기존 수동 버튼 테스트에 inert wait
  허용 및 idle OUT 거절 추가). 이 Host 변경 아직 미배포.
- 브라우저 재인증 후 tab3/browser1 resumedControlTab 정상 작업화면 확인,
  markHandoff. 만료된 이전 tab2는 닫음.

## 2026-09-14 rev457 수동 시작 버튼 오진 수정 배포 후 재개

- rev454 cn_auto_start_allowed 추가, rev455 정합 검사 valid. rev456 수동
  start_all에서 start_btn ON만 제거(수동 버튼 ON 보존). rev457은 실행 중인
  구 Host의 manual_sequence_missing_operator_start 오진 1개로 invalid.
- 이전 run03ef... 정상 cancel(state cancelled) 및 Host restart 요청 후 새
  인증 성공. 이미 테스트한 manual_move_btn 인식 수정과 Agent scoped repair
  design.read 허용 수정 적용을 위해 수행한 재시작이며 대기 시간 때문이 아님.
- 새 run cee57c88-d093-4c23-9a8a-49ec040bd5c1 analyzing,
  operation resume-manual-button-20260914. 기존 초안/요구 유지하여 check 갱신,
  수동 시작 조건 제거 보존 및 수동 출력/reset 계수/정지 모드전환/복구 후
  fresh-start 완성 요청. 원본/물리 PLC 쓰기 금지 유지.
- 실제 XG5000 생성 및 XG-SIM 미완료. 브라우저 인증은 재시작 후 재확인 필요.

## 2026-09-14 rev452 자동 시작 허용 writer 저장 / rev453 선언 누락

- task49874 완료 후 rev452에서 rung_auto_start_allowed 추가됨. OUT 조건은
  (mode_stopped ON OR mode_auto ON) AND any_alarm OFF. 내부 미작성 신호는 없어졌으나
  이것만으로 복구 후 fresh-start가 보장되는 것은 아니다.
- 다음 design.check가 rev453 invalid1 저장: 해당 OUT의 control_network 0개.
  현재 compiler native_projects.py require_exact_actions도 OUT 대상 집합과
  network output 집합의 정확한 일치를 요구한다. Host 진단만 없애면 안 됨.
- 현재 run 유지, 후속 task63448 생성 시작 확인. native 실행 미완료.
- 웹 추가 관찰: 진행 이벤트 수신 후 설계 중 문구와 70분45초 경과가 표시됨.
  이전 일반 처리 문구는 연결 직후였으며 지속 갱신 누락으로 단정하지 않는다.

## 2026-09-14 사용자 웹 화면 인증/표시 복구

- CUA getState가 다시 성공. browser1(Codex In-app Browser) 최초 tabs 비어 있음.
  Start-ControlStudio-Development.ps1 -Web 정상 종료 후 직접 주소 탭은 미인증.
- 기존 /api/launcher/handoff 일회용 티켓을 앱 open_in_codex로 전달해 정상
  인증된 탭2를 열었음(티켓 미출력). 미인증 임시 탭1은 닫음.
- CUA binding liveControlTab(tab2, browser1), markHandoff 적용. 실제 AX와
  screenshot에서 대동인덱스 실사용 재검증 / 분석 중 / 작업 중단 요청 확인.
  UUID 등 작업 상세는 접혀 있음. 긴 생성 중 화면은 여전히 일반적인
  '요청을 처리하고 있습니다'만 보여 생성/저장 상태 구분이 어려움.
- 네이티브 XG5000 화면 확인 또는 XG-SIM 실행 완료를 뜻하지 않음.

## 2026-09-14 Host 지정 수정 범위에서도 의존성 조회 허용 (Agent 미배포)

- Agent 라우터에서 design.check invalid + repairContext.section에 해당하는
  editor만 노출하던 분기에 design.read 추가. 오류 항목을 고치기 전에 다른
  섹션의 실제 신호/참조를 조회할 수 있어야 한다. Host 쓰기 권한은 변경 안 함.
- test_design_workspace_runtime + test_internal_network_permits: 56 passed.
  실행 중인 Agent subprocess에는 아직 미배포. 다른 진단별 단일 도구 강제
  분기들은 그대로이므로 전체 도구 제어 문제가 해결됐다고 주장하지 않는다.
- live task49874 processing=true/generated9985, draft451 유지 확인.

## 2026-09-14 rev451 / 시뮬레이션 추가 내부 신호 관찰 수정

- 현재 run 03ef1d5c-e25d-496c-ad78-8705587804ab 유지. Qwen /slots task49874
  processing=true, generated8764 확인. 대기 만료를 실패로 취급하거나 재시작하지 않음.
- rev444/446 자동 대기 완료 선언과 전환 래더의 start_btn을 auto_start_allowed로
  변경했고 rev448/450 자동 시작 선언/래더에도 추가됨. rev451 valid이지만 해당
  내부 신호 writer는 아직 없어 실행 완료를 의미하지 않음.
- MultiModeIndexSimulationRunner.output_refs가 REQUIRED_SIGNALS에 있는 이름만
  포함하던 것을 설계의 전체 비입력 M/P 신호로 변경. 추가 내부 신호를 실제
  관찰 대상으로 해석하고 입력 byte와의 겹침도 검출함. 단위 테스트7개 통과.
- 이 수정은 모델 설계 수정이나 실제 XG-SIM 실행 증거가 아님. runner의 고정
  mode/alarm/signal ID와 수동 start_btn 가정은 남아 있으며, 현재 설계에 대한
  명시적 역할 연결 및 실제 native 생성/시험은 여전히 미완료.

## 2026-09-14 rev443 수동 시작 재요구 / 버튼 검증 수정 미배포

- run 03ef1d5c-e25d-496c-ad78-8705587804ab 유지. rev439 계수 증가 조건은
  init_btn OFF AND (auto 완료 OR manual 해제대기)로 수정되어 valid.
- rev440 수동 start_all에서 두 버튼을 제거했지만 기존 검증이 start 이름만
  인정해 rev442에서 start_btn/manual_move_btn ON이 다시 추가됨. rev443 valid.
- Host manual_sequence_missing_operator_start의 명령 입력 후보를 digital_input만
  대상으로 하고 manual_move_btn/button 및 jog_btn/button도 인정하도록 수정.
  모드 선택만 있는 경우는 여전히 오류. test_recovery_input_polarity5 passed.
  이 변경은 아직 Host 미배포이며, idle 단계에서 명령을 기다리는 일반 경로까지
  해결한 것은 아니다. 실제 move_cmd OUT은 여전히 자동 단계에만 연결되어 있음.
- 자동 step_wait는 start_btn ON 유지(설명만 최초1회로 변경됐음). 실제 자동
  반복/수동 출력/reset 계수연결/복구순서 및 native XG-SIM 완료 미확인.

## 2026-09-14 시작/전환 양방향 편집 배포 후 재개

- rev427 자동 전환 start_btn 제거가 rev429 sync로 되돌아간 것을 확인.
  rev430 invalid2, 첫 진단 수동 시작 조건. Agent 라우팅이 start/transition
  sync만 허용하고 Host가 sequences 편집을 거절하는 동일 구조 결함이었다.
- start/transition도 reset과 같이 edit_sequences/edit_ladder_rungs/read 허용,
  Host diagnosed sequences 편집 허용. Agent55 passed, Host sequence_sync_tools1 passed.
- run c89457bb-1d1a-4f6e-8f58-8cd5e78f3b96 정상 취소 후 Host restart.
  새 run 03ef1d5c-e25d-496c-ad78-8705587804ab analyzing,
  operation resume-sequence-bidirectional-20260914. 위 수정 배포.
- 저장 모드 래더와 선언은 상대 모드 OFF로 정합 수정됨(rev421/423/425).
  제한 Boolean 스캔에서 진동/동시ON은 사라졌지만 정지 중 전환 요구는 미검증.
- 자동 초기/복구 후 새 시작을 보존하면서 반복 시작요구 제거, 수동 입력/출력/
  계수, reset/복구 순서 완성을 요청. 별도 native 프로젝트/XG-SIM은 미완료.

## 2026-09-14 거절된 편집 재조회 및 양방향 진단 안내 배포 / 재개

- 기존 run a16331f4-be38-451d-bf17-a422f249ac8d에서 rev418 valid 유지,
  design.edit_sequences 형식 오류로 미저장 확인. 정상 cancel(state=cancelled)
  후 Host restart(status=scheduled), 새 세션 bootstrap 성공.
- 새 run c89457bb-1d1a-4f6e-8f58-8cd5e78f3b96, operation
  resume-readable-repair-20260914, state=analyzing 확인.
- 앞선 Agent rejected-edit read/check 허용 및 Host 양방향 permit 진단 안내
  반영을 위한 재개다. 기존 설계/확정 요구 보존, 정지 선언 수정 보존,
  모드 진동·자동 반복·수동 출력/계수·복구 순서 해결 요청. 전체 완료 아님.

## 2026-09-14 rev417 정지 선언 변경 / 진단 안내 미배포

- 같은 run a16331f4-be38-451d-bf17-a422f249ac8d에서 cn_mode_stopped의
  permit_all 중 any_alarm OFF 제거가 저장됐다. 현재 rung도 auto/manual OFF
  AND이므로 선언 정합성은 후속 검사 대상. 모드 진동/전체 동작 해결은 아님.
- Host control_network_out_missing_effective_permits 안내를 양방향 선언/래더
  수정으로 변경했다. 잘못된 선언을 그대로 래더에 복사하지 말고 확정 요구와
  비교하며 safety-rule permits는 보존하도록 명시. 검증 조건은 그대로다.
- focused internal_or_rung 테스트 1 passed. 이 Host 변경 및 앞선 Agent의
  rejected-edit read/check 변경은 현재 실행에 미배포. 이번에는 실제 선언
  수정이 진행됐으므로 실행을 취소하지 않았다. Qwen task24935 live 확인.
- rev411에서는 seq_auto.start_all의 auto_running ON이 제거됐지만 첫 단계
  start_btn ON은 남아 있다. 연속운전 요구를 해결한 것으로 인정하지 않는다.

## 2026-09-14 rev407 재확인 및 거절된 편집 재조회 경로 수정 (Agent 미배포)

- 동일 run a16331f4-be38-451d-bf17-a422f249ac8d 유지. rev403→405에서
  mode_stopped rung의 any_alarm OFF를 제거했지만 선언 불일치 검사 후
  rev407에서 다시 추가했다. 이 왕복은 기능 해결이 아니다.
- rev407 valid이나 제한적인 mode OUT Boolean 순차 평가에서는 자동/정지
  진동, 동시 선택 시 두 모드 ON, 알람 발생 시 stopped OFF가 여전히 재현된다.
  이 평가는 XG-SIM/전체 PLC 실행 검증이 아니다. 실제 프로젝트 생성도 미완료.
- Agent runtime의 거절된 collection 편집은 같은 편집뿐 아니라 read/check를
  허용하도록 변경했다. 거절된 rung repair helper도 read/check 및 직접 rung
  편집으로 진단할 수 있다. Host 검증·revision·publication 경계는 변경하지 않았다.
- test_internal_network_permits.py + test_design_workspace_runtime.py: 51 passed.
  기존 reset 양방향 편집 변경을 반영하지 않았던 오래된 기대값 1개도 갱신했다.
- 이 Agent 수정은 현재 subprocess에는 아직 반영되지 않았다. 진행 중인
  모델 요청을 단순 대기 시간 때문에 취소하지 않았다. 전체 목표 완료 아님.

## 2026-09-14 rev395 초기화 선언 정지 조건 복구 확인

- run a16331f4-be38-451d-bf17-a422f249ac8d의 실제 edit_sequences 저장:
  seq_auto.reset_all=[mode_stopped ON] 복구. alarm3 OFF/seq_auto_step OFF 제거.
- rung_seq_auto_reset의 추가 alarm/step 조건 정리는 아직 미확인.
  모드/수동/연속/복구 및 XG-SIM 전체 동작 검증은 남아 있다. 실행 유지.

## 2026-09-14 초기화 진단 안내 반영 후 선언 우선 재개

- rev392 invalid1 및 기존 일방적 reset rung 추가 안내를 확인하고 정상 취소/restart.
  Host의 양방향 수정 안내 문구 반영. 저장 설계 보존.
- 새 run a16331f4-be38-451d-bf17-a422f249ac8d,
  operation resume-reset-declaration-first-20260914 시작(analyzing 확인).
- 먼저 seq_auto.reset_all=[mode_stopped ON] 전체 교체를 명시하고 이후
  rung_seq_auto_reset도 동일하게 수정하도록 요청. 별도 복구/자기유지 보존.
  모드/수동/연속/복구 순서 및 전체 기존 요구사항은 그대로 남아 있음.

## 2026-09-14 reset 진단의 일방적 래더 추가 지시 제거 (Host 미배포)

- rev389는 선언 수정 대신 rung_seq_auto_reset에 step_complete OFF를 다시 추가했다.
- sequence_reset_rung_missing_or_incomplete 메시지가 'Add only the missing explicit
  reset rung'으로 지시해 양방향 편집 라우팅과 모순됨을 확인.
  사용자 요구와 선언/래더를 비교하고 잘못된 선언은 edit_sequences로 고치라는
  안내로 변경. 검증 조건 자체는 완화하지 않았다. focused3 passed.
- Host 메시지 수정은 아직 미배포. 현재 run e861ed48-dc35-4eaa-ae07-6fb928399b38,
  Qwen task19257 processing=true 확인. 초기화 실제 복구 및 전체 동작 미완료.

## 2026-09-14 rev387 초기화 래더 정지 조건 복구

- 동일 run e861ed48-dc35-4eaa-ae07-6fb928399b38의 rev384→387에서
  rung_seq_auto_reset 조건의 step_complete OFF를 mode_stopped ON으로 교체.
  알람3개 OFF는 유지. 앞선 duplicate 생성/condition-action 오류는 저장되지 않았음.
- reset_all 선언은 아직 alarm3 OFF + seq_auto_step OFF이므로 후속 정합 수정 필요.
  실제 모드 진동/복구/수동/연속운전 해결은 여전히 미완료.
- Qwen task18919 processing=true 확인. 실행 유지.

## 2026-09-14 rev384 잘못된 초기화의 참조만 수정

- run e861ed48-dc35-4eaa-ae07-6fb928399b38 유지.
  rung_seq_auto_reset의 미선언 step seq_auto_step OFF를 step_complete OFF로 교체.
  alarm3개 OFF 조건은 그대로. 완료 단계가 아닌 이동 중에도 reset될 수 있어
  실제 초기화 요구 해결로 인정하지 않는다. reset_all 선언도 여전히 별도 수정 필요.
- Qwen task18070 processing=true 확인. 목표 완료 아님.

## 2026-09-14 rev380 초기화 조건 여전히 오수정

- 동일 run e861ed48-dc35-4eaa-ae07-6fb928399b38에서 seq_auto.reset_all은
  completion_count OFF 대신 alarm 3개 OFF + seq_auto_step OFF로 변경됐다.
  요청한 mode_stopped ON 복구가 아니며 성공으로 인정하지 않는다.
- rev380 invalid1 sequence_reset_rung_missing_or_incomplete. 실제 reset rung은
  아직 completion_count OFF. Qwen task17523 processing=true 확인, 실행 유지.

## 2026-09-14 reset 양방향 수정 반영 후 재개

- 취소 전 최신 rev376 valid 확인. rev371→376에서 rung_seq_auto_reset이
  completion_count OFF로 sync됐고 기존 estop 복구는 별도 rung_seq_auto_reset_estop 추가.
- 기존 run 정상 취소/Host restart로 reset 선언 편집 허용 반영.
  새 run e861ed48-dc35-4eaa-ae07-6fb928399b38,
  operation resume-reset-contract-repair-20260914 시작(analyzing 확인).
- seq_auto.reset_all과 rung_seq_auto_reset을 mode_stopped ON으로 복원,
  별도 estop 복구 및 자기유지는 보존하도록 요청. 남은 전체 동작 요구도 유지.
- 아직 실제 복원 저장/XG-SIM 완료 미확인. 원본/물리 PLC 쓰기 없음.

## 2026-09-14 초기화 선언/래더 양방향 수정 연결 (미배포)

- rev372 sequence_reset_rung_missing_or_incomplete에서 Agent가 sync만 허용했다.
  잘못된 reset_all 자체를 고치도록 check/read 라우팅을 edit_sequences/
  edit_ladder_rungs/read로 변경. Host도 해당 diagnostic의 sequences 부분 편집 허용.
- Agent focused10 passed, Host 실제 서비스 sequence 편집 회귀1 passed.
  Host 테스트는 최초 fixture의 published_digest 누락을 고친 뒤 통과했다.
- 두 소스 수정 모두 현재 실행에는 미배포. run c29a2775-d10c-4d1f-8d16-ab9af722c2ff.
  초기화 completion_count OFF 변경 원인이 모델 판단인지 변환인지 raw call 근거는
  아직 확보하지 못했으므로 단정하지 않는다. 실제 동작 완료 아님.

## 2026-09-14 rev371 자동 초기화 선언 의심 변경

- 동일 run c29a2775-d10c-4d1f-8d16-ab9af722c2ff에서 seq_auto.reset_all이
  mode_stopped ON → completion_count OFF로 변경됐다. 요청한 정지/리셋 동작과
  맞는 변경으로 인정하지 않는다. 다른 자동 steps(시작 버튼 반복 요구 등)는 그대로.
- read-only Host check는 sequence_reset_rung_missing_or_incomplete 1건.
  안내는 새 선언에 맞춘 reset rung 추가인데, 선언 자체가 잘못됐으면 이를 수정해야 한다.
- 자기유지는 rev369 상태 유지. Qwen task16121 processing=true. 전체 미완료.

## 2026-09-14 rev369 자기유지 실제 복원 및 선언 일치

- 동일 run c29a2775-d10c-4d1f-8d16-ab9af722c2ff의 rev368→369에서
  rung_auto_running에 (start_btn ON OR auto_running ON) 복원 저장.
  rev367에서 공통 start permit을 제거했으므로 이번에는 선언과 충돌하지 않는다.
- 저장 rev369 read-only Host check diagnostics0 / unwrittenInternalSignals[].
  이는 정적 확인이며 mode_auto 진동이 남아 있어 실제 연속운전 성공을 뜻하지 않는다.
- Qwen task15377 processing=true 확인. 모드/수동 출력 및 계수/복구 순서 등
  남은 실제 동작 수정과 XG5000/XG-SIM 검증 필요. 실행 유지.

## 2026-09-14 rev367 자동운전 공통 시작 조건 실제 제거

- run c29a2775-d10c-4d1f-8d16-ab9af722c2ff 유지 중.
  rev366→367 design.edit_control_networks로 cn_auto_running permit_all에서
  start_btn ON만 제거했다. mode_auto ON 및 알람 3개 OFF는 유지.
- 아직 rung_auto_running 자기유지 복원은 미확인. 전체 동작 완료 아님.
  Qwen task14946 processing=true, 후속 check/편집 대기.

## 2026-09-14 timer/source 최종 검증 수정 Agent로 재개

- 기존 run 6b167973-bc91-4fd5-9437-9cc03569c02a는 native 수정 없이 rev361
  published 후 ANALYZING 상태. 최종 계약 오류를 고친 Agent 사용을 위해 정상 취소.
- Host 재시작 없이 새 run c29a2775-d10c-4d1f-8d16-ab9af722c2ff,
  operation resume-actual-selfhold-edit-20260914 시작. timer 참조/source repair 소스 적용.
- followup은 먼저 cn_auto_running start_btn 공통 permit 제거 및 실제 rung 자기유지
  복원 두 변경을 수행하도록 명시했다. 이후 남은 기존 동작 요구사항도 유지.
- 실제 저장 결과/전체 기능 완료는 아직 미확인. 물리 PLC/원본 쓰기 없음.

## 2026-09-14 publication 인용문 수정 경로 연결 (Agent 미배포)

- read-only final contract replay로 narrative requirement_sources.sequence의
  '한 칸 이동→한 칸 이동' 중복 문구가 실제 brief corpus에 없음을 확인했다.
- 최종 ModelRetry 뒤 read는 허용됐지만 정상 published read 라우터가 design.edit를
  제외하므로 인용문 자체를 수정할 수 없었다. requirement_sources 오류에 한해
  후속 read 뒤 design.edit/read를 제공하도록 workspace 상태/라우팅 연결.
- tests/test_internal_network_permits.py 8 passed(타이머 참조 + source repair 포함).
  이 Agent 변경과 앞선 timer 참조 변경은 현재 시작된 run에는 아직 미반영.
  run 6b167973-bc91-4fd5-9437-9cc03569c02a의 실제 native 수정은 여전히 없음.

## 2026-09-14 Agent 최종 timer 참조 오진 수정 (현재 실행 미반영)

- run 6b167973-bc91-4fd5-9437-9cc03569c02a는 실제 native 수정 없이 rev361 publish.
  Qwen task12264 뒤 task13103으로 넘어갔고 Host는 여전히 ANALYZING.
- read-only replay에서 NewProjectNativeDesign이 실제 rung timer 접점을 무시하여
  every timer must drive... 오류를 냄. 선언 참조 외에 nested ladder condition의
  source_type=timer 참조도 수집하도록 수정했다. TON writer만으로는 소비로 세지 않음.
- tests/test_internal_network_permits.py 7 passed. 저장 draft replay는 timer 오진을
  통과한 뒤 requirement_sources exact resolved user quotes 불일치 ModelRetry로 이동.
  다음 작업: 그 인용문/현재 brief 불일치 처리 경로 확인. 실제 PLC 동작은 여전히 미완료.
- 변경은 Agent source에만 있고 현재 이미 시작된 실행에는 아직 미반영.

## 2026-09-14 모드 오진 수정 Host 반영 및 재개

- rev358 invalid6 상태를 확인하고 기존 run 정상 취소, Host 정상 restart로
  모드 중복 activation 오진 수정 반영. 초안은 보존했다.
- 새 run 6b167973-bc91-4fd5-9437-9cc03569c02a,
  operation resume-mode-check-correction-20260914 시작(analyzing 응답 확인).
  check 갱신 후 기존 실제 동작 수정 지시를 이어가도록 요청했다.
- 이번 변경은 검사 오진 제거이며 실제 모드/자동/수동/복구 동작 완료가 아니다.

## 2026-09-14 모드 상호배제 오진 수정 (Host 미배포)

- 새 run adf7537f-9769-499a-b9e9-e850a4a8f831의 rev358 check에서
  mutually_exclusive_modes_required_together 6건이 새로 나타났지만 native 변경은 없었다.
  강화된 followup의 '동시' 문구가 해당 검사 분기를 활성화했다.
- 검사기가 모든 모드 activation ID를 모아 stopped의 auto_mode_select OFF까지
  auto_mode_select ON으로 중복 계산했다. 자동/수동 모드만 선택하고 중복 ID 제거 수정.
- test_stopped_mode_does_not_duplicate_auto_activation: 자동 단독은 오진 없음,
  실제 auto+manual ON AND는 여전히 검출. 관련 파일 4 passed.
- Host 소스 변경은 아직 미배포. 다음 작업에서 배포하고 잘못된 진단을 새 check로 갱신 필요.
  실제 mode_auto/manual/stopped의 진동/상호배제 결함은 별개로 여전히 남아 있다.

## 2026-09-14 자기유지 회귀 후 수정 라우터로 재개

- rev355에서 sync_control_network_out_rung이 auto_running 자기유지 OR를 제거했다.
  실제 회귀 확인으로 기존 run 정상 취소. 관찰 timeout 때문에 취소한 것이 아니다.
- Host 재시작 없이 새 run adf7537f-9769-499a-b9e9-e850a4a8f831,
  operation resume-preserve-behavior-20260914 시작(analyzing 응답 확인).
  Agent source의 permit mismatch 직접 edit 라우팅 변경을 새 실행에 사용.
- followup은 cn_auto_running start_btn 공통 permit 제거 + 자기유지 복원,
  모드 선택과 운전 상태 분리/상호배제/정지 중 전환, 남은 기존 요구사항을 지시.
  새 실제 수정 저장과 전체 E2E 완료는 아직 미확인.

## 2026-09-14 permit mismatch 직접 래더 편집 라우팅 수정 (아직 미배포)

- rev351→353에서 sync가 mode_stopped를 기존 AND+any_alarm OFF로 되돌렸다.
  선언 일치는 개선했지만 기존 모드 진동/복구 차단 논리로 회귀한 것.
- Agent check/read의 control_network_out_missing_effective_permits 분기에서
  sync 대신 edit_ladder_rungs/edit_control_networks/read를 노출하도록 수정했다.
  앞선 분기는 실제 래더 직접 편집을 누락했고 기존 선언 강제 복원을 유도했다.
- focused 24 passed, 15 deselected. 현재 실행에는 미배포; 다음 Agent 실행에 적용 필요.
- 현재 run 56c74617-1640-45d1-98b3-5559908ef69f 유지. rev354 invalid1은
  cn_auto_running의 start_btn ON 공통 선언이 자기유지 OR와 충돌하는 진단이다.
  이 선언에서 start_btn을 제거해야 자기유지가 보존됨; 래더를 이전식으로 되돌리면 회귀.

## 2026-09-14 rev351 자기유지 추가 및 정지 조건 오수정 확인

- 동일 run의 rev350→351 실제 rung 수정: auto_running 조건에
  (start_btn ON OR auto_running ON) 추가. 자기유지 경로가 생겼으나 전체 동작은 미검증.
- mode_stopped 조건은 (mode_auto OFF OR mode_manual OFF)로 바뀌었다.
  limited Boolean scan 재현에서 자동 선택 시 auto/stopped 동시 ON,
  두 선택 입력 ON 시 auto/manual 동시 ON 및 진동은 여전하다. 완료가 아니다.
- Qwen task10692 processing=true 확인. 기존 run 유지하며 후속 check/수정 대기.

## 2026-09-14 rev350 모드 순차 Boolean 계산 재현

- `.test-tmp/probe-mode-scan.py`는 DB read-only로 실제 모드 OUT rung 3개를 순차
  계산하는 제한된 조사 스크립트다. PLC 전체 emulator/XG-SIM 증거가 아니다.
- 자동 선택 유지 시 (auto, stopped)=(0,1)→(1,0)→(0,1) 반복.
  두 선택 입력 ON 시 auto/manual 모두 ON이 되는 스캔 발생.
  any_alarm ON 시 stopped도 OFF라 stopped를 요구하는 복구가 막히는 구조 확인.
  따라서 rev350 valid/diagnostics0은 기능 완료가 아님.
- run 56c74617-1640-45d1-98b3-5559908ef69f는 재시작 없이 유지.
  design.read 후 Qwen task9882 processing=true 확인. 후속 동작 수정 저장 대기.

## 2026-09-14 실제 타이머 완료 writer 작성 확인

- run 56c74617-1640-45d1-98b3-5559908ef69f 유지 중, rev348→350에서
  rung_move_timeout_done 추가 확인: timer/move_timeout ON → OUT signal/move_timeout_done.
  빈 sync 반복 대신 design.read 후 실제 래더 작성으로 진행했다.
  부수적인 network mode_id/step_id null 직렬화 변화는 동작 개선으로 세지 않는다.
- Agent router 회귀 17 passed. 현재 Qwen task9826 processing=true 확인.
  자동 연속운전/수동/복구 및 실제 XG-SIM 완료는 여전히 미검증.

## 2026-09-14 빈 내부 network 자동 동기화 반복 수정

- rev340→345에서 cn_any_alarm permit_all=[] 및 cycle_complete/move_cmd_active/
  move_timeout_done network 추가 확인. 실제 OR rung은 보존됨.
- 이후 sync_control_network_out_rung이 빈 all_of를 생성해 schema_invalid를 반복했다.
  missing OUT 분기의 도구 선택에 edit_ladder_rungs/read를 열고, sync not_applied는
  동일 sync만 재시도하지 않고 직접 편집/read/check로 이동하게 수정했다.
  Host sync는 조건 없는 선언으로 계산식을 만들어내지 않고 직접 편집 안내 conflict 반환.
- Host focused 3 passed, Agent focused 6 passed. 전체 E2E 성공 의미 아님.
- 기존 run 정상 취소 후 Host 재시작, 저장 초안에서 run
  56c74617-1640-45d1-98b3-5559908ef69f 재개(analyzing 응답 확인).
  operation resume-authored-internal-rung-20260914. 실제 타이머 rung 저장은 아직 확인 전.
  XG5000 별도 프로젝트 생성 및 XG-SIM 실동작 검증은 미완료.

## 2026-09-14 내부 OR 표현 및 선언/래더 양방향 수정 반영

- Host/Agent/Provider network permit_all은 내부 계산일 때 []를 허용한다.
  digital_output은 여전히 명시적 permit을 요구한다. compiler conditions()의
  allow_empty는 내부 network 검증 호출에만 켰고 다른 조건/실제 rung/safety 검증은 유지.
- OR 두 입력의 실제 Host compiler 통과, digital_output empty 거부를 확인했다.
  Agent 계약에서도 동일 기준 확인. permit mismatch에서 correct OR를 wrong AND에
  강제 동기화하지 않도록 edit_control_networks/read/sync 중 선택 가능하게 변경했고,
  Host도 진단 대상 network 선언의 부분 수정을 허용한다. 실제 서비스 edit/check
  경로에서 wrong permit→[] 수정→valid를 확인했다. Host2/Agent4 focused 통과.
- 기존 run 정상 취소/Host 재시작 뒤 run 91e02e5f-14a7-49cf-82ce-eccb3405e53f,
  operation resume-internal-or-contract-20260914로 기존 초안에서 재개했다.
  Qwen task8636 processing=true 및 Launcher -Web exit0 확인. 실모델의 후속 수정 저장은
  아직 확인 전이다. 전체 동작 및 XG-SIM은 여전히 미완료다.

## 2026-09-14 내부 writer 실제 추가 및 OR 연산 표현 차단 확인

- run f1568fa1-c2c3-498b-8eba-62deb0191f8e의 rev334→335에서 실제 OUT rung 4개 추가:
  any_alarm(3 alarm latch OR), auto_running(mode_auto/alarms OFF/start_btn ON),
  cycle_complete(auto step_complete AND move_complete), move_cmd_active(move_cmd AND !complete).
  auto_running은 아직 자기유지가 아니고 cycle_complete는 수동 경로가 빠진 상태다.
- rev336의 첫 오류는 any_alarm control_network_out_cardinality_mismatch이며 총4개다.
  중요한 다음 수정: 모든 내부 OUT에도 control_network가 필요하지만 permit_all이 반드시
  nonempty AND여서 any_alarm의 3-way OR에는 합법적인 공통 permit이 없다.
  dummy always-true bit나 self-reference로 우회하지 말고 내부 계산용 network는
  명시적 추가 permit 없음([])을 표현하도록 Host/Agent schema, compiler, instructions를
  함께 수정할 것. 실제 digital_output의 permit/safety 및 rung condition 검증은 유지.
- 위치: Host api_models ProjectPlanControlNetworkV1.permit_all min1; Agent runtime
  NewProjectControlNetwork/ProviderControlNetwork min1 및 instructions non-empty 강제;
  native_projects compiler network loop의 not permits, conditions()의 empty 거부.
  conditions()는 다른 용도에도 쓰므로 network 전용 allow-empty 여부를 분리해야 한다.
- 마지막 관측 약1305초에서 같은 run이 유지 중. 취소/재시작하지 않았다.
  이 구조 문제를 고쳐 실제 내부 OR writer가 컴파일되는 테스트를 먼저 만들고 반영할 것.

## 2026-09-14 원인별 복구 시험 바인딩 및 실제 출력 permit 반영

- MultiModeIndexSimulationRunner의 one shared recovery 제한을 제거했다.
  reviewed resetAlarmIds의 대응으로 공유 규칙 또는 알람별 규칙을 바인딩한다.
  현재 profile은 각 알람에 하나의 복구 경로를 시험하며 누락/중복 경로는 거부한다.
  더 일반적인 복수 조건별 경로 및 역할 ID 매핑은 여전히 남은 작업이다.
  test_multimode_plan_bindings.py 5개 통과. 실제 XG-SIM 실행은 아직 아니다.
- 유지 중인 model run f1568fa1-c2c3-498b-8eba-62deb0191f8e의 rev332→334에서
  rung_move_cmd_out에 emergency_stop ON이 실제 추가되어 control network와 일치했다.
  rev334 valid는 전체 기능 완료가 아니며 내부 신호 writer/자동·수동/모드 진동이 남는다.

## 2026-09-14 단계·시퀀스 식별자 고정 제거

- MultiModeIndexSimulationRunner에서 네 개 예제 step ID/개수 조건을 제거했다.
  실제 선언된 단계들을 고유 ID로 검증하고 그 주소를 바인딩한다. 시퀀스 식별자도
  index_sequence/manual_sequence 고정 대신 modeId 연결로 선택한다.
  임의 시퀀스 이름과 7개 단계, D 주소/NC 정상 상태의 focused 회귀 4개 통과.
- 아직 REQUIRED_MODES/REQUIRED_SIGNALS/REQUIRED_ALARMS 및 단일 recovery 가정이
  남는다. 현재 3모드/원인별 recovery 초안을 위한 전체 시험 경로 완성은 아니다.
- 유지 중인 run f1568fa1-c2c3-498b-8eba-62deb0191f8e는 rev330→332에서
  cn_move_cmd permit_all에 emergency_stop ON을 실제 추가했다. 뒤의 OUT rung 동기화와
  미작성 내부 신호 로직은 이어서 확인할 것. 관측 당시 Qwen task7033 처리 중.

## 2026-09-14 실제 시험 경로의 고정 예제 의존성 확인

- MultiModeIndexSimulationRunner는 REQUIRED_SIGNALS/MODES/SEQUENCES/ALARMS와
  네 개 step ID, one shared recovery, D00001을 고정한다. 현재 초안은 auto/manual/stopped,
  seq_auto/seq_manual, 원인별 recovery, D00000이므로 이 runner를 현재 설계의 완전한
  시험 경로로 취급하면 안 된다. 남은 핵심은 명시적 설계/시험 바인딩으로 일반화하는 것.
- 이번에는 counter address를 INCP/MOV-0의 실제 일치 주소에서 바인딩하도록 고쳤고,
  E-stop/door 초기화 및 fault assert/clear도 normalState의 정상/반대로 구동하도록
  수정했다. MCP tests/test_multimode_plan_bindings.py의 주소/불일치/NC 복구 3개 통과.
  신호명·단계명·recovery 수 고정은 아직 남는다. XG-SIM 실운전 완료 증거는 아니다.
- 현재 model run f1568fa1-c2c3-498b-8eba-62deb0191f8e는 유지 중이다.
  rev324→330에서 timer.enable_all과 실제 TON rung이 move_cmd_active로 변경됐다.
  해당 신호 writer는 아직 확인되지 않았으므로 타이머 동작 해결로 판단하지 않는다.

## 2026-09-14 데이터 흐름 조회 실앱 반영 및 무변경 저장 수정

- rev320→321 design.edit_sequences는 native_design 변경 없이 remaining_work만
  바뀌었다. 무변경 비교가 이 검사 bookkeeping을 포함해 가짜 편집 revision을 만든
  버그였다. narrative/technical_configuration/native_design의 실제 편집 내용만
  비교하도록 수정했다. invalid 및 valid-check 뒤 동일 patch 회귀 2 passed.
- executionObservations와 이 수정은 정상 cancel/restart로 실 Host에 반영했다.
  현재 run f1568fa1-c2c3-498b-8eba-62deb0191f8e,
  operation resume-dataflow-workspace-20260914. 기존 rev322에서 재개했다.
  Qwen slot task5465 processing=true를 확인했다. Launcher -Web exit0도 확인했다.
- 내부 신호 생산 로직부터 실제 작성하도록 요청했으며 자동/수동/모드/복구 순서
  요구도 유지했다. 해당 기능 수정의 실제 저장 여부 및 XG-SIM 완료는 아직 미확인.

## 2026-09-14 정적 실행 의존성 조회 추가 — 현재 요청은 유지 중

- design.inspect/read 응답에 executionObservations를 추가했다. 읽기만 하고
  OUT/SET/RST writer가 없는 내부 신호와 소비 rung ID를 제공한다. 새 publication
  gate나 시뮬레이션 결과가 아니라, 에이전트의 실제 편집에 필요한 정적 관찰이다.
- 실제 rev320 read-only 검사에서는 기존 진단 0개였지만 any_alarm, auto_running,
  cycle_complete, move_cmd_active, move_timeout_done 5개가 writer 없이 사용됐다.
  이 정보는 코드 및 focused 회귀 3 passed와 실제 저장본 분석으로 확인했다.
- 이 executionObservations 변경은 현재 실행 Host에 아직 반영하지 않았다.
  현재 run 0c3598b1-fb8f-4a92-b687-d004b63c8d6d를 다시 취소하지 않고 유지한다.
  관측 시 Qwen slot task3832는 processing=true, n_decoded623이었고 run 약405초,
  rev320 상태였다. live 출력의 완료/진척을 먼저 확인하고 다음 정상 재시작 때 반영할 것.
- 이전 goal turn은 코드 수정/실제 초안 수정으로 progress, 이번 turn도 조회 기능
  구현 및 실제 저장본의 5개 미작성 신호 확인으로 progress다. 전체 목표 미완료.

## 2026-09-14 복구 래더 오판 수정 및 실제 자동 복구 접점 수정

- 후속 rev314→316에서 수동 reset rung도 실제 위험 접점이 제거됐다.
  rev316→318은 mode_auto description만 바뀌었고 실행 조건은 그대로다.
- 컬렉션 래더 편집 결과에 editImpact(executableChanged, changedFieldsByRung,
  실제 변경 설명)를 추가했다. 설명 수정은 허용하되 실행 변경으로 오인하지 않도록
  모델 observation을 제공한다. 조건/순서/삭제와 설명-only 구분 포함 회귀 2 passed.
- 이 변경 반영 후 현재 run은 0c3598b1-fb8f-4a92-b687-d004b63c8d6d,
  operation resume-executable-impact-20260914이다. 모드 순환 조건 및 실제 rung 순서
  문제를 명시해 기존 초안에서 재개했고 rev319 inspect 완료를 확인했다.
  editImpact를 실제 모델이 활용해 운전 조건을 고쳤는지는 아직 미확인이다.
- 실행 순서상 알람 RST가 해당 latch ON을 요구하는 sequence reset보다 앞에 있다.
  이것도 복구 동작 미완료 사유다. 자동/수동 모드 진동 및 내부 writer 문제도 남는다.

- rev302→306은 reset rung에 emergency_stop ON뿐 아니라 door_open ON 및
  move_timeout_done ON까지 AND로 추가했다. 기존 검사는 이를 valid로 처리했지만
  정상 복구 동작은 아니다. 단순 검사 통과를 완료로 판단하지 않는다.
- 작업자 recovery를 구현하는 sequence_reset rung이 활성 위험 입력을 추가 요구하는
  오류를 검출하도록 수정했다. 일반 trip에 의한 sequence reset은 이 검사의 대상이 아니다.
- 이름만으로 alarm_estop_latch를 위험 입력으로 오인하는 문제도 확인하여 hazard 판별을
  digital_input으로 한정했다. rev312 read-only 검사에서 자동 쪽 오탐은 사라지고
  수동 rung 실제 오류 하나만 남았다. 입력/래치/trip-reset 구분 회귀 1 passed.
- rev310→312에서 자동 rung의 door_open/move_timeout_done 접점이 실제 제거됐다.
  설명만 변경된 rev308→310과 구분한다. 수동 쪽 및 전체 동작은 미완료다.
- 현재 run e3c3c556-36ba-43c9-8fde-733c924a124b,
  operation resume-typed-input-repair-20260914. 정상 API cancel/restart 후 기존
  초안에서 재개했으며 rev314는 수동 rung 오류 한 건을 실 Host에서 반환했다.
  테스트 반복 대신 실제 저장 diff와 수정 완료 여부를 확인하며 이어갈 것.

## 2026-09-14 중단된 Host 복구 및 rev298 재개

- 시작 시 8767 listener와 Python 프로세스가 없었지만 이전 run은 DB에 ANALYZING으로
  남아 있었다. 정식 Start-ControlStudio-Development.ps1 -Web이 exit0으로 기동했고
  새 listener와 웹 API 요청을 확인했다. 화면 시각 검증으로 확대하지 않는다.
- Qwen 100.64.0.1:8978/v1 / qwen36 설정이 보존됐다. 기존 대화에 정상 메시지 API로
  run 9aa24eba-9806-4433-badf-020aa5165b79를 시작했다. rev298에는 recovery_estop
  emergency_stop ON 변경이 실제 보존돼 있다. 신규 실행은 rev302까지 진행했으며
  비상정지 recovery의 seq_auto/seq_manual reset rung 진단 2개가 남아 있다.
- continuous start 검사에서 이름에 start가 있는 내부 신호를 순간 버튼으로 오인하는
  판별을 digital_input으로 제한했다. 이 소스 변경은 현재 실행 Host에 아직 미반영이다.
  실제 저장본 rev298의 read-only 검사에서는 RST/sequence reset 불일치 3개를 확인했다.
- 자동/수동 전체 동작, 별도 파일 생성 및 XG-SIM은 여전히 미완료다.

## 2026-09-13 실행 설계 대신 설명문만 재작성하는 경로 차단

- Qwen run `5e974c18-ddfa-460f-a38d-29cfba6fc43c`의 긴 응답은 design.edit로
  narrative만 변경했고 rev283→285 native_design diff는 없었다. 이것을 기능
  수정 완료로 보지 않는다. valid read 후에도 narrative/technical_configuration의
  공통 design.edit를 노출하던 경로를 수정했다. 초기 작성/진단 기반 복구 경로는
  유지하고 valid 설계 의미 검토에서는 컬렉션별 실제 편집기를 노출한다.
- 정상 cancel/restart 후 run `4b5e1339-cbc5-41e9-a90c-28d2e716816c`, operation
  `resume-executable-edit-20260913-1810`으로 재개. alarm_estop OFF,
  recovery_estop ON을 명시하고 설명문을 다시 쓰지 않도록 전달했다.
- 사용자 요청으로 정식 launcher `-Web` 실행(정상 exit0)했다. 인앱 open 요청은
  queued이고 CUA timeout이 반복됐다. computer-use skill의 초기화/앱목록/복구
  절차 후에도 `codex app-server exited before returning response 1`로 실패했다.
  실제 화면 확인 완료라고 주장하지 않는다. 현재 재시작 후 -Web 재호출 중.

## 2026-09-13 NC 입력 정상 상태를 거절하던 검사기 수정

- 실모델이 rev278에서 이동 제어망에 emergency_stop ON을 올바르게 추가했으나
  rev279 검사가 `hazard_active_used_as_run_permit`로 OFF로 바꾸라고 요구했다.
  `_requirement_diagnostics`가 emergency/door 입력은 모두 ON=위험이라고
  하드코딩한 것이 원인이다. NC emergency_stop의 normalState=on과 충돌했다.
- 제어망/OUT rung 진단을 각 I/O의 normalState 기준으로 변경했다. 같은 실제
  저장본 rev279 read-only 재검사는 잘못된 극성 오류 대신 정상 ON 조건을
  OUT rung에도 반영하라는 `control_network_out_missing_effective_permits`만
  반환했다. 신규 단위 테스트 반복이 아니라 실제 실패 저장본 확인이다.
- 잘못된 진단을 받은 이전 run 정상 cancel/restart 후 기존 초안에서 재개:
  run `5e974c18-ddfa-460f-a38d-29cfba6fc43c`, operation
  `resume-nc-polarity-repair-20260913-1750`. 이동허용 ON/알람발동 OFF/복구 ON을
  정확히 요청했다. 동작 및 시뮬레이션 전체 완료는 아직 아니다.

## 2026-09-13 최신 수정 요청 전달 누락 복구

- 이전 Qwen run은 저장 초안 rev265에서 read를 반복하며 실제 편집 저장을
  확인하지 못했다. 단순 진행 heartbeat를 작업 진척으로 보지 않는다.
- `_run_design_workspace`는 project_brief와 narrative만 받아 현재 사용자
  prompt를 실행 모델에 전달하지 않았다. 호출부에서 user_request=prompt를
  전달하고 `_design_workspace_prompt`에서 확정 brief와 구분해 작업 지시를
  제공하도록 수정했다. valid인 기존 초안 재발행만으로 수정 요청을 충족했다고
  취급하지 않도록 명시했다. Agent 회귀 33 passed.
- 이전 run 정상 cancel/restart 후 기존 대화에서 정상 API로 재개:
  `e8a9a2e4-ab91-4cc6-9d4c-15def4bcff25`, operation
  `resume-user-directed-repair-20260913-1720`. 첫 작업을 NC E-stop의 알람/복구
  극성 수정으로 특정하고 나머지 자동/수동/모드/신호/timer 문제도 유지했다.
  실제 편집 저장 및 전체 시뮬레이션 완료는 아직 확인 전이다.
- 후속 실실행은 safety edit 형식 오류 후 재수정하여 안전 규칙을 저장했다.
  rev270→276 read-only 비교에서 원인별 알람 차단 규칙 추가와 이동 OUT rung
  조건 변경이 확인됐다. rev277은 구조 검사 valid이나 NC 알람/복구 극성,
  내부 writer/자동·수동 동작은 아직 수정 확인 전이다. 완료로 표현하지 않는다.
  `.test-tmp/compare-design-revisions.py`는 저장본을 read-only 비교한다.

## 2026-09-13 Qwen OpenAI 호환 연결 검사 복구 진행

- 사용자 로컬 서버는 `http://100.64.0.1:8978/v1`, 모델 `qwen36`이다.
  `/models` 응답과 서버 슬롯을 확인했다. 게이트웨이 인증정보를 전달하지 않는다.
- 이전 기능 검사 실패는 Qwen 사용 불가의 증거가 아니다. 실제 런타임 직접
  재현에서 structured probe의 1,000-token 제한으로 JSON이 절단되어
  `UnexpectedModelBehavior`/JSON EOF가 발생했다. Host는 이를 일반 실패로
  표시하며 이미 성공한 단계도 false로 축약했다.
- structured probe는 실제 native output profile을 재사용하고 streaming
  probe의 별도 64-token 제한을 제거했다. 추론 모델의 출력 예산을 tiny probe
  상수로 제한하지 않는다. 성공 기준/스키마 검증은 유지한다.
- HTTP 응답 hook은 SSE를 미리 `aread()`하지 않게 수정했다. 기존 코드는
  생성 종료까지 스트림 전체를 버퍼링했다. HTTP200 error envelope도 모두503으로
  바꾸지 않고 인증/요청/한도/서버 오류를 구분한다.
- 관련 Agent 회귀 31 passed. 정상 Host restart 요청 완료. 수정 후 Qwen 실서버
  직접 검사 5개 항목 모두 통과했고 정상 settings PUT의 재검사도 통과했다.
  앱 저장 모델은 Qwen 위 주소이며 hasApiKey=false/customHeaderNames=[]이다.
- 기존 대화에서 정상 messages API로 의미 수정 재개:
  run `93b309bd-78a6-4a96-88d6-8416ce40535b`,
  operation `resume-qwen-semantic-review-20260913-1640`, analyzing 응답 확인.
  브라우저 연결 도구는 반복 timeout 상태여서 UI 확인을 했다고 주장하지 않는다.
  설계 의미 수정 및 XG-SIM 완료는 아직 아니다.
- 위 run은 inspect/check/read까지 실제 완료했으나 이후 비스트리밍 모델 응답을
  오래 기다려 rev263에서 정상 cancel했다. workspace `agent.iter`가 모델 노드를
  비스트리밍 next로만 구동하던 것을 확인했다. `_advance_workspace_node`는
  모델 노드 스트림을 소비한 뒤 원래 graph transition으로 검증/도구 실행한다.
  partial JSON/thinking은 사용자 텍스트로 노출하지 않는다. streaming-only
  TestModel 회귀는 비스트리밍 호출 금지 및 도구 exactly once를 확인한다.
  Agent 32 passed, 정상 Host restart scheduled. 전체 운전 완료 아님.
- 재시작 뒤 재개 run `db288e7a-fb69-498d-ad25-06f1ffc764b9`은 모델 검증
  precondition으로 실패했다. `create_app`의 개발 모델 기본값이 기존 사용자
  선택을 매번 덮어쓰는 코드가 원인이었다. 개발 기본값을 **비어 있는 설치만**
  seed하도록 변경했다. 실제 create_app/repository 테스트 2 passed는 사용자
  endpoint/검증시각/빈 인증정보 보존 및 빈 설치 초기화를 확인한다.
  Qwen 재저장 후 실제 재시작 보존 확인 중이다.
- 실제 재시작 후 settings GET은 Qwen 주소/모델과 빈 인증정보 보존을 확인했다.
  재개 run `4aae3847-d092-4e3c-8860-d7f10695541d`
  (`resume-qwen-persisted-stream-20260913-1700`)은 precondition을 통과하여
  design.inspect를 실행했다. 서버 `/slots`에서 stream=true와 생성 중 상태를
  확인했다. Host 관련 합산 48 passed/5 subtests, Agent 32 passed.
- 같은 run은 약315초 시점에도 실행 중이며 inspect→check→read→read 도구 완료가
  확인됐다. 현재 rev265 valid, 이 run의 실제 편집 저장/설계 완주는 아직 확인하지
  못했다. 스트리밍 연결과 모델 선택 보존의 복구를 PLC 동작 완료로 해석하지 말 것.

## 2026-09-13 09:32 승인 후 재개 및 실제 카운터 수정 차단 복구

- 마지막 10:11 의미 검토 요청 run `1e19cab1-65f5-46c6-8db9-3f7711804471`은
  inspect/check 후 provider HTTP503으로 FAILED. rev261 valid가 보존돼 있으나
  위 의미 결함은 수정 완료되지 않았다. 새 read/review/edit 경로의 실모델 완주도
  미검증이다. 후속 짧은 design_read tool-call probe: Nemotron 30초 timeout,
  gemma-4-26b-a4b-it HTTP429. 앱 모델 설정은 바꾸지 않고 dialog를 닫았다.
  인앱 ControlStudio 탭은 표시/유지했다. native 파일 생성/물리 PLC 쓰기는 없음.

- 10:01 재개에서 unbound rung을 특정해 수정한 뒤 rev250 valid, 이후 published로
  갔으나 최종 응답 없이 publish/narrative 편집을 반복했다. 10:07 정상 UI cancel.
  실제 DB의 read-only Agent contract replay는 NewProjectNativeDesign의
  `every timer must drive a transition, alarm, recovery, or output` 거절을 확인했다.
  `.test-tmp/replay-published-contract.py`는 DB를 읽기만 한다.
- 검사가 통과하면 publish 도구만 강제하던 Agent 라우터를 변경했다.
  valid check→design.read→읽은 영역의 편집/read/check/publish 선택을 허용해
  모델이 검사기가 놓친 동작 오류도 검토·수정할 수 있다. 최종 publication 거절도
  narrative 전용 edit 대신 read로 돌아가며, pydantic 오류는 입력 값 없이
  구체적인 경로/메시지를 모델에 전달한다. Agent 관련 28 tests passed.
- 현재 구조/컴파일 pass와 별개로 초안의 의미상 결함을 직접 확인했다:
  NC emergency_stop 정상 ON인데 alarm trigger ON/recovery OFF로 반대;
  자동 step_wait가 매 사이클 start_btn ON을 요구; move_cmd OUT은 자동 이동
  단계만 포함; auto_running/cycle_complete/any_alarm/move_timeout_done 등에
  writer가 없고, 모드 OUT들의 상호 의존은 스캔 간 진동 우려가 있다.
  이 초안을 정상 완성품으로 승인하거나 native 생성하지 않았다.
  새로운 동작 검토 경로를 정상 restart로 반영 중이며 실모델 후속 검증 필요.

- 09:52 재개는 복구 rung 검사 6개→1개까지 진행 후 provider HTTP503으로
  실패했다. 짧은 Nemotron 생성 probe HTTP200/choices 있음/error 없음 확인 후
  UI 재개했다. 기존 SDK max_retries=2가 이미 있어 횟수를 더 늘리지 않았다.
- 다음 native 오류는 unbound SET rung인데 generic 메시지에 sequence가 들어가
  sequences 수정으로 오분류됐다. 정상 UI cancel 후 NativeProjectPreconditionError에
  선택적 구조화 repair_section/item_ids/diagnostic_code를 추가했다.
  unbound_sequence_rung은 실제 matching rung IDs를 전달하고 해당 rung의
  부분 수정/중복 제거만 허용한다. Host는 이 구조화 대상을 문자열 추정보다
  우선한다. timer+draft 테스트 46 passed/5 subtests. 정상 restart로 반영 중.
  비상정지 NC 해제 조건은 현재 초안에서 아직 의미 검토가 필요한 상태다.

- 09:43 재개에서 timer 진단은 통과했고 sequence S group 충돌도 수정했다.
  rev229에서 recovery_sequence_reset_rung_missing_or_incomplete가 recovery_rules로
  잘못 라우팅돼 실행이 다시 실패했다. Host의 summary/execute가 따로 가진
  ladder 소유 코드 목록을 `_LADDER_REPAIR_CODES`로 통합하고 복구 전용 rung
  추가를 허용했다. 일반 초기화 rung을 보존하는 실제 execute 회귀를 추가했다.
  timer+draft 45 passed/5 subtests. 정상 Host restart scheduled로 반영 중이다.
  native 프로젝트 생성/전체 제품 완료는 아니다.

- 후속 09:30 run `62ec3309-cbde-450a-89ce-bb0faf3b6e2f`는 rev204까지
  진행 후 `agent_runtime_provider_http_503`으로 실패했다. 마지막 진단은
  T0010/default timer area mismatch다. 목록 조회와 Nemotron 짧은 생성 probe는
  이후 각각 HTTP200으로 복구 확인했다. 성공한 전체 계획 생성이 아니다.
- timer preflight가 잘못됐다는 말만 반복하고 필요한 timeBaseMs를 알려주지 않아
  모델이 주소만 반복 변경했다. 기존 검증 조건은 유지하면서 주소별 기대 base,
  duration 유지와 presetTicks 계산식을 오류에 포함했다. 범위 밖 주소에는 base
  수정 대신 주소 선택 안내를 준다. timer+draft 테스트 44 passed/5 subtests.
  계획 UI/clarification 18 passed/5 subtests, revision handoff 2 passed다.
  처음 revision tests 2개는 공유 TEMP 접근 거부로 setup 실패했고, 새 고유
  basetemp 재실행에서 통과했다. 이를 제품 코드 실패/수정으로 오인하지 않는다.

- 기본 LOCALAPPDATA Development DB/Host 8767 ready를 확인하고 정상 Launcher
  `-WebUrl` handoff로 인앱 대동인덱스 대화를 복원했다. 기존 실행은
  `host_restarted` FAILED였으며 완료가 아니었다. 초안은 rev128에서 보존돼 있었다.
- 09:25 UI 재개에서 카운터 MOV 0 수정이 `repair_must_target_diagnosed_item`로
  다시 막혔다. missing reset 진단의 path가 INCP 인덱스를 가리켜, relatedIds를
  제외해도 인덱스 기반 수정 제한이 남는 것이 원인이었다. missing reset일 때는
  ladder 컬렉션 소유로 처리해 초기화 writer 추가/수정을 허용한다. 기존 rung 보존과
  진단 인덱스가 있는 상태의 실제 execute 회귀를 추가했다. Host 해당 모듈 42 passed.
- Agent의 복수 호출 축약은 design workspace 요청에만 적용하도록 범위를 제한했다.
  일반 engineering 호출을 조용히 버리지 않는 회귀 포함 해당 모듈 27 passed.
- UI cancel 완료 후 정상 `/api/v1/host/restart` scheduled→health ready,
  정상 Launcher 재인증으로 수정본 반영했다. 추가 UAC/강제 종료/DB 수동 변경 없음.
  09:30 UI 재개는 rev143까지 진행했고 카운터 차단 다음인 manual_move_pressed의
  control network 누락 진단으로 넘어갔다. 전체 설계/신규 파일/XG-SIM 완료 아님.
- Windows computer-use list_apps는 timeout→app-server exited, kernel reset 후에도
  app-server exited로 실패했다. 현재 native 창을 확인했다고 주장하지 않는다.
  브라우저 제품 실행은 가능하며 이 화면 도구 오류와 제품 native 실패를 구분한다.

## 2026-09-10 Qwen 196K 컨텍스트 기준 에이전트 예산 반영 및 배포

- 실제 llama.cpp slot의 `n_ctx=196608`을 기준으로 Qwen 네이티브 설계 1차 출력
  한도를 32,000토큰, JSON 절단 재시도 한도를 64,000토큰으로 확대했다. 이 값은
  목표 출력 길이가 아니라 상한이며, 프롬프트와 출력을 합친 컨텍스트 한도는 지킨다.
- 기존 100개 메시지 절단을 제거했다. Host는 약 180k, Agent는 시스템 지시와
  출력 여유를 둔 약 150k 토큰까지 대화 입력을 보수적으로 계산해 보존한다.
- UsageLimits의 누적 실행 예산을 요청별 context와 혼동하던 문제를 수정했다.
  네이티브/서술 교정은 최대 4요청 각각 196,608 컨텍스트를 사용할 수 있도록
  누적 예산을 786,432로 분리했다. Qwen 서술 출력은 16k/절단 재시도 32k,
  네이티브 출력은 32k/절단 재시도 64k이며 이것들은 강제 생성량이 아닌 상한이다.
- agent 전체 103tests, Host 전체 27tests 및 정적 오류 검사 통과. Host 정상
  restart202로 새 코드를 배포했다. 앞선 XG5000 PID10172/HWND462098 재attach200도
  확인했지만 재시작 후 다시 사용할 때는 세션을 재결속해야 한다.
- 실모델 재검증 직전 `100.64.0.1:8060`이 TCP 연결 거부로 바뀌었다. Tailscale의
  `sanghun-desktop` 노드와 SSH 22번 포트는 살아 있으나 이 Windows 환경에는 해당
  Linux 계정 인증서가 없어 원격으로 모델 서버를 재기동하지 못했다. 모델 서버가
  다시 listen하기 전에는 설계 요청을 중복 제출하지 않는다. 정상 API로 제출했던
  run4f94689e-3a13-43f8-9a76-544fd1505075는 무의미한 provider timeout을 기다리지
  않고 cancel200/CANCELLED 처리했다.
- 신규 프로젝트 생성/저장/재열기/전체 XG-SIM은 여전히 미완료이며 성공으로
  주장하지 않는다.

## 2026-09-10 Qwen 분석 도구 분리 실검증과 JSON 절단 수정 (미배포)

- 일반 Qwen 엔지니어링을 통합 대형 EngineeringAnalysisOutput 대신 기존의
  Host 선택 도구 루프와 compact continuation 경로로 분리했다. 프로그램 목록과
  초기동작 LD 읽기 2건, evidence 2건, checkpoint/mission checkpoint가 실제 생성됨.
- run705151a8-6b72-4e4d-b01f-a4f35052ac46 최종 FAILED. 시간 초과가 아니라
  continuation 2,000토큰 한도에서 unexpected_end_of_json/json_invalid로 종료.
  따라서 단순 토큰 축소 성공으로 주장하지 않는다.
- Qwen 보조 continuation에 enable_thinking=false를 유지하고 한도를 4,000으로
  조정. qwen sampling/program continuation 30tests 및 Ruff 통과. 아직 이 최종
  변경은 미배포/실모델 미검증. 읽기 자체와 projectUnchanged 근거는 성공 유지.
- 신규 프로젝트 생성/저장/재열기/전체 XG-SIM은 여전히 미완료.

## 2026-09-10 실제 대동 읽기 성공, 후속 모델 설명 시간 초과

- 정상 workspace resume 200으로 대동 작업 사본을 새 XG5000 PID10172/HWND462098에
  열고 controlled_launch_official_open_read_only 세션을 결속했다. 이전 빈 창
  PID8868에 attach409가 난 것을 XG5000 전체 연결 불가라고 확대하지 말 것.
- 앱 대화 a0e2657e-0d7d-4e94-80cb-8f92ff8dd3b7의 run
  e70b5502-f56d-4b1d-bb25-4128ea54b74a는 최종 FAILED/provider_timeout.
  그러나 tool.completed 2개: list_project_programs와 read_ladder_program 모두 성공.
  초기동작 program.read_ld receipt verified=true/stableRepeat=true/projectUnchanged=true.
  evidence fa3d5126-2aa6-53c5-95b2-d24666d607c8. 본문 읽기 실패로 표현하면 틀린다.
- 실패는 후속 engineering_analysis 모델 단계. 설정 timeoutSeconds=300.0,
  일반 Agent max_tokens6000. Qwen slot190226 idle 확인. 타임아웃의 세부 원인을
  토큰 수만으로 확정하지 않았고 시간 제한 증가/재요청/모델 변경은 하지 않았다.
- 사용자에게 잘못된 이전 설명을 정정했다. 신규 재생성/저장/전체 XG-SIM은 미완료.

## 2026-09-10 XG5000 실행은 Windows 사용자 승인 대기

- 설치된 C:\XG5000\XG5000.exe를 Computer Use launch_app으로 한 번 실행 요청.
  호출 timeout 뒤 재실행하지 않고 창 목록을 새로 조회했으며,
  consent.exe의 "XG5000 Application이(가) 사용자 권한을 요청하고 있습니다."
  창을 확인했다. 사용자에게 직접 승인을 요청했다.
- 다음 goal turn의 read-only 프로세스 확인에서도 consent PID7352/session1,
  동일 권한 창이 존재하고 XG5000 프로세스는 없다. 승인/취소를 대신 누르거나
  중복 실행하지 않았다. 사용자의 승인 후 새 프로세스/창을 재확인할 것.
- 이전 브라우저 URL 안전 차단은 우회하지 않았으며 원본/작업 파일 변경 없음.
  XG5000 본 화면/신규 프로젝트/전체 시뮬레이션 완료로 주장하지 않는다.

## 2026-09-10 실제 재시도는 추가 질문으로 종료, 실행 설계 없음

- run2a4a444b-cdb7-4ce8-8b74-72e97209bb2a COMPLETED, 그러나 최신 assistant
  metadata plan_status=needs_clarification. 마지막progress planning_finalize225초,
  task187122 is_processing=false. live 요청 없음.
- 질문은 기존 reset 조건으로 알람 해제 처리/추가 알람 reset 불필요 여부,
  추가 HMI/위치 표시 제외 여부, 자동6회 및 수동 ON/OFF 시험 순서의 계획 반영 여부.
  질문 자체에 이전 확정 답변을 괄호로 인용하면서 다시 필수 확인으로 취급했다.
  주소/극성 guard 실패가 아닌 별개의 범위 재확인 문제이며, COMPLETED를
  실행 설계 생성 성공으로 해석하지 않는다. 동일 질문을 사용자에게 재전달하지 않았다.
- 새 프로젝트 파일/저장/재열기/전체 XG-SIM 미완료. 다음은 기존 답변 재전송이나
  또 다른 개별 질문 정규식보다 요구 판단 구조를 재검토할 것.

## 2026-09-10 원인 구분 배포 후 실제 설계 재시도 진행 중

- Host active 없음/모델 idle 확인 후 정상 restart202. 재인증/bootstrap 이후
  정상 대화 API로 "기존에 답한 요구사항으로 설계 생성을 다시 진행해 줘.
  추가 요구사항이나 변경은 없어." 제출. 기존 답변 전문은 다시 누적하지 않았다.
- run2a4a444b-cdb7-4ce8-8b74-72e97209bb2a HTTP202/analyzing.
  기존 9a5ef 실패와 혼동하지 말고 이 실행을 추적할 것. 종료 전에 중복 요청 금지.
- 새 모델 결과/파일 생성/실제 전체 시뮬레이션 성공은 아직 미확인.

## 2026-09-10 반복 질문 실패 원인 구분 (미배포)

- 주소 위임 재질문과 모의 극성 재질문이 모두 planning_question_repeated로
  기록되어 실제 마지막 실패 원인을 구분할 수 없었다. Agent 진단 매핑 및
  Agent/Host 허용 목록에 planning_address_question_repeated,
  planning_polarity_question_repeated를 추가했다. 원문/비공개 사고를 기록하지 않는다.
- correction55tests 통과. 기존 모델 실패를 새 코드로 소급해 해석하지 말 것.
  다음 실제 실행에서 어떤 guard가 실패했는지 구분하기 위한 변경이며 해결 자체가 아니다.
  아직 미배포이고 신규 요청 없음. 단계 연동 카운트/다중 시퀀스 구현은 아직 없음.

## 2026-09-10 작성 누락 피드백 수정 정상 재로딩

- bootstrap activeOperation 없음 및 모델 slot task184635 is_processing=false 확인 후
  정상 /api/v1/host/restart 202. 이후 새 세션 bootstrap 200/activeOperation 없음,
  model=qwen36와 기존 endpoint 유지 확인. 새 모델 메시지는 제출하지 않았다.
- 전체 시뮬레이션 경로 재검토: NativeSimulationRunner는 여전히 각 모드/시퀀스/
  알람 1개, 2-step require_start_release만 지원한다. 별도 MNE rung profile은
  비트 관측과 최대16step 전용이며 대동 전체 카운트/다중모드 시험의 대체가 아니다.
  단순 개수 제한 삭제 또는 MNE profile 우회로 전체 PASS를 만들지 말 것.
- 실제 신규 프로젝트 생성/저장/재열기/전체 XG-SIM과 사용자 화면 확인 미완료.

## 2026-09-10 계획서 작성 누락과 사용자 미확정 구분 (미배포)

- _validate_new_project_narrative_readiness의 누락 항목 retry 문구가
  "Complete the narrative, or return needs_clarification"으로 작성 누락을
  질문 분기로 돌아갈 선택지처럼 제시했다. 작성 누락 자체는 미확정 사용자
  결정이 아니며 근거와 명시적 제안으로 보완하도록 수정. 실제 미확정 결정은
  그대로 질문하도록 유지하며 자동 ready/근거 보충/DB 변경은 없다.
- correction54tests 통과: 미작성 ready는 여전히 거부하고 원래 객체 불변,
  진짜 clarification 질문은 그대로 유지. 최신 실제 반복 질문 실패가 이 문구로
  유발됐다는 증거는 없으며 해결됐다고 주장하지 않는다. 미배포/새 요청 없음.
- Windows 브라우저 화면의 정책상 안전 중단을 우회하지 않는다. 전체 일반 사용자
  생성/저장/재열기/전체 XG-SIM 목표는 여전히 미완료.

## 2026-09-10 실제 탭 인증 오류 확인과 안내 정리

- 사용자에게 보이는 상태를 재확인: in-app 탭은 Launcher 세션 인증 필요 상태로
  시작 메뉴 3개가 모두 비활성화되어 있었다. 서버 8767 listener만으로 웹 사용 가능이라
  판단하지 말 것. 정상 Development 런처 -Web 실행은 종료 코드 0, Edge 창은 존재한다.
- Windows Computer Use 초기 timeout 후 kernel reset으로 앱 목록 조회는 복구됐으나
  Edge 관측에서 현재 브라우저 URL을 확신할 수 없다는 안전 중단 발생. 이 경로를
  다른 UI 조작 기술로 우회하지 않았다. 사용자 화면 확인 답변은 아직 없음.
- App.tsx: 세션 인증 필요 시 중복 연결 경고와 비활성 시작 메뉴를 숨기고,
  정상 앱으로 다시 여는 방법 및 저장 기록 보존을 안내한다. npm run build 통과.
  수정 후 실제 화면 검증은 아직 하지 않았다. XG5000 생성/저장/재열기와 전체
  XG-SIM 인수 시험은 여전히 미완료이며 새 모델 요청도 제출하지 않았다.

## 2026-09-10 중복 축소 실검증도 반복 질문으로 실패

- run9a5ef227-3582-461b-912d-a8e30e628af0 최종 FAILED/agent_output_contract,
  planning_narrative/validator_rejected/planning_question_repeated. 마지막progress495초,
  task184635 idle. live 요청 없음, 모델 설계/파일 채택 없음.
- 중복 축소만으로 반복 질문 해결되지 않음. 동일 입력 재제출/재시작 없음.
  기존 정확 요구는 보존되어 있다. 사용자에게 다시 같은 질문을 넘기거나
  단순 안내 추가를 해결로 주장하지 말 것. 전체 목표는 여전히 미완료.

## 2026-09-10 모델 전달 중복 축소 반영 후 실제 요청

- task179746 idle/Host activeOperation 없음 확인→정상 restart202→재인증과
  bootstrap/settings qwen36 기존 endpoint 확인.
- 마지막 후속 답변 그대로 정상 메시지 API로 제출:
  run9a5ef227-3582-461b-912d-a8e30e628af0 HTTP202/analyzing.
  다음은 이 실행 추적. e6512fdd는 FAILED이며 중복 제출하지 말 것.
- 원문DB/근거 검증 변경 없음. 실제 생성/저장/재열기/전체 XG-SIM 미완료.

## 2026-09-10 누적 동일 답변의 모델 전달 중복 축소 (미배포)

- 현재 DB brief readonly 측정: supplemental 8650자/46문단, 연속 동일 문단만
  접으면3099자/17문단. 저장 절단은 아니며 반복 재제출이 상당 부분을 차지함.
- _planning_brief_prompt_view는 shallow copy에서 연속으로 완전히 동일한 문단만
  한 번 표시. _trusted_context_instructions의 serialized model view에만 적용.
  원문DB/brief digest/source enum 및 validator는 그대로. 공백 차이/A→B→A는 보존.
- correction52tests/Ruff 통과. 미배포/새 모델 요청 없음. 실제 반복 질문 해결 여부
  및 전체 생성/시뮬레이션은 미검증. 최신 e6512fdd FAILED, live 요청 없음.

## 2026-09-10 ON/OFF 보존 수정 후에도 반복 질문 최종 실패

- e6512fdd-21a9-44f7-813c-3174a079327d FAILED/agent_output_contract:
  planning_narrative/validator_rejected/planning_question_repeated. 마지막progress375초,
  task179746 idle 확인. live 요청 없음. 계획/파일 채택 없음.
- Host append_planning_clarification은 후속 원문을 잘라내지 않고 누적한다.
  실제 실패 질문 원문은 보존되지 않았으므로 극성/주소 중 무엇인지 확정하지 말 것.
  같은 요청 추가 제출/재시작 없음. 반복 질문을 사용자에게 재질문하는 대신
  누적 요구 전달 및 판단 경로의 구조적 개선 필요. 전체 목표 미완료.

## 2026-09-10 ON/OFF 정의 보존 반영 후 실제 요청

- 서버 task175896 idle/Host activeOperation 없음 확인 후 정상 restart202.
  재인증/bootstrap/settings qwen36 및 기존 endpoint 확인.
- 마지막 시험 순서 포함 답변 그대로 정상 API로 재제출:
  run e6512fdd-21a9-44f7-813c-3174a079327d HTTP202/analyzing.
  다음은 이 요청부터 확인. 이전67dee000는 clarification 완료일 뿐 실행 설계 없음.
  중복 제출 금지. 전체 생성/저장/재열기/XG-SIM 미완료.

## 2026-09-10 ON/OFF 시험 언급이 기존 정의를 가리는 결함 수정 (미배포)

- 67dee000 COMPLETED/needs_clarification, design 빈 배열. ON/OFF와 알람표시,
  6회 후 추가동작, 초기화 질문을 다시 출력. task175896 idle, live 요청 없음.
- _stored_simulation_polarity_quote가 가장 나중의 ON/OFF 언급이면 무조건
  정의 후보로 삼아 "버튼 ON/OFF 시험 순서"가 이전 확정 정의를 가림.
  후보는 논리/의미/정의/변경 또는 미확정 언급으로 한정. 시험 순서 언급은
  기존 정의 유지, 실제 후속 논리 미정/변경은 기존 답을 재사용하지 않음.
- 시험 순서 후 기존 정의 보존/논리 재정의 시 미확정 회귀 포함51tests 통과,
  Ruff 통과. 미배포/새 요청 없음. 모델 생성/파일/전체 시뮬레이션 미완료.

## 2026-09-10 시험 순서 작성 선택 질문에 정상 후속 응답

- e690dc57 COMPLETED/needs_clarification, design 빈 배열. 자동6회 모의 입력
  단계 정리 여부/수동 버튼 ON-OFF 타임라인 여부를 필수 질문으로 출력.
  task172361 idle. native 단계 도달하지 않아 참조 수정 실검증은 아직 아님.
- Codex 실사용 테스트 답변으로 두 시험 순서 포함을 정상 API로 응답.
  원래 요청한 PC 검증 범위임을 설명하고 기존 안전/운전/리셋/원본보존 유지.
  사용자 새 발언이라고 주장하지 말 것. 다음 실행 ID는 최신 runs 조회로 확인.
  추가 필수 질문 남발 문제와 전체 파일 생성/XG-SIM은 미완료.

## 2026-09-10 선언 ID 재시도 전달 반영 후 실제 검증

- 서버 task169390 idle/Host activeOperation 없음 확인 후 정상 restart202.
  재인증/bootstrap/settings qwen36 기존 endpoint 확인.
- 마지막 저장 후속 답변을 정상 메시지 API로 재제출:
  run e690dc57-c07c-42f8-8235-755b2bba8597 HTTP202/analyzing.
  다음은 이 요청부터 확인. bf6b3e41는 FAILED. 중복 재제출 금지.
- 모델 설계 채택/파일 생성/전체 XG-SIM은 아직 미완료.

## 2026-09-10 참조 오류 재시도에 실제 선언 선택지 전달 (미배포)

- provider 참조 validator가 generic undeclared 오류만 주고 실제 선택지를 잃던
  부분 보강. typed source_sets의 선언 ID tuple을 별도 error context에 보존하고
  retry feedback에 종류별 선택지를 전달. 종류별64개/ID80자/식별자 정규식 제한,
  정확한 알려진 오류 문구만 처리. 임의 context/raw input은 출력하지 않음.
- 선언 추가/자동 치환/ready 자동 승격 없음. 전체 설계 생성 방식을 분리한 것은
  아니며 이 수정의 실제 모델 성공 여부는 아직 미확인.
- native unknown-reference 통합 검사에 실제 선언 선택지 전달, payload 불변,
  public diagnostic에 context 미노출 검사 추가. correction51tests/Ruff 통과.
  미배포, 새 요청 없음. 최신 bf6b3e41는 FAILED, live 모델 요청 없음.

## 2026-09-10 native 설계 최종 실패: 선언되지 않은 action 신호

- bf6b3e41-dbe9-4231-b79b-b411c05518f6 최종 FAILED/agent_output_contract.
  planning_native_design/schema_validation/native_ladder_unknown_reference.
  ladder_rungs 2..8 action.target_id가 undeclared signal_id. 마지막progress960초,
  서버 task169390 idle 확인. 새 파일/채택된 설계 없음. 더 기다릴 live 요청 없음.
- 현재 provider는 source_sets로 io_points/timers/sequences.steps/sequence_id를
  검증하며 retry feedback에도 올바른 선언 collection과 target_type 대응을 이미
  안내한다. 단순히 동일 안내를 다시 덧붙이는 것으로 해결됐다고 주장하지 말 것.
  다음은 실제 모델의 선언/참조 분리 생성 방안과 스키마 부담을 검토할 것.
  이 실패 뒤 아직 재시작/새 모델 요청/소스 수정 없음. 전체 목표 미완료.

## 2026-09-10 clarification provider 제약 반영 후 실제 요청

- 서버 task160389 idle 및 Host activeOperation 없음 확인 후 정상 restart202.
  재인증/bootstrap/settings qwen36/기존 endpoint 확인.
- 마지막 후속 답변 그대로 정상 메시지 API 재제출:
  run bf6b3e41-dbe9-4231-b79b-b411c05518f6 HTTP202/analyzing.
  다음은 이 요청부터 확인. 이전5ba34a05는 FAILED. 중복 제출 금지.
- 생성 성공/실제 XG5000 파일 및 XG-SIM 검증 완료 증거는 아직 없음.

## 2026-09-10 질문 없는 clarification 실패 / provider 분기 제약 보강

- run5ba34a05-b71f-466b-9b82-2f70930246bf 최종 FAILED:
  planning_narrative/validator_rejected/planning_question_missing. 서버 task160389
  idle, 마지막 heartbeat675초. 최종 설계나 파일 채택 없음. live 요청 없음.
- 동적 provider clarification 분기는 open_questions 기본 빈 배열을 허용했으나
  후속 readiness validator에서는 거절하고 있었음. provider schema도 필수/minItems1로
  맞춤. ready 분기는 maxItems0. 질문 자동 생성이나 상태 자동 승격 없음.
- provider schema 필수/min/max 검사 포함 correction50tests 통과. 미배포,
  새 모델 요청 없음. 전체 생성·저장·재열기 및 XG-SIM은 여전히 미완료.

## 2026-09-10 추가 질문 결과 확인 및 실제 후속 답변

- fe48870a COMPLETED지만 needs_clarification, design 전체 빈 배열.
  질문은 자동 6회 이후 동작, 별도 알람 리셋 입력, HMI/위치 표시 필요 여부.
  주소 안내 수정의 native 단계 검증까지 도달하지 않음. 서버 task154458 idle.
- 실사용 테스트 후속 답변을 정상 API로 보냄. 기존 자동 반복/6회는 시험 기준,
  기존 리셋 조건, 요청 없는 추가 HMI/리셋 입력을 필수 질문으로 만들지 말 것을
  명시. 사용자 새 답변으로 주장하지 말 것: Codex가 기존 요구를 근거로 보낸 테스트 입력.
- run 5ba34a05-b71f-466b-9b82-2f70930246bf HTTP202/analyzing.
  다음은 이 실행 추적. 중복 요청 금지. 전체 파일/시뮬레이션 목표 미완료.

## 2026-09-10 주소 표기 안내 반영 후 실제 재검증

- Qwen task150347 idle와 Host activeOperation 없음 확인 후 정상 restart202.
  재인증/bootstrap/settings qwen36 및 기존 endpoint 유지 확인.
- 기존 최신 사용자 메시지를 정상 API로 재제출, HTTP202:
  run fe48870a-c1bd-4748-92da-eb7aaa092ad8, state analyzing.
  다음은 이 요청부터 확인. 기존 d1ce7406은 FAILED이며 더 기다리지 말 것.
- 주소 규칙 완화/자동 변환 없음. 실제 설계 생성 성공 여부는 아직 미확인.
  별도 파일 생성/재열기 및 전체 XG-SIM 목표는 그대로 남아 있음.

## 2026-09-10 실제 실행 설계 주소 형식 실패 / 안내 보강 미배포

- d1ce7406-ee75-4674-b612-34bbe9f7e375 최종 FAILED(agent_output_contract),
  planning_native_design/schema_validation. io_points 0..7 digital_input.address
  string_pattern_mismatch. 마지막 progress1350초, 서버 task150347 idle 확인.
  최종 설계 채택/파일 생성 없음. 더 기다릴 live 요청 없음.
- Agent instructions와 provider input/writable address schema description에
  compact bit 표기(P/M + 3~4자리 word + hex bit) 및 예시, dotted 금지,
  모의 digital_input도 M 영역 가능, 사용자 주소 보존/위임 조건 명시.
  실제 거절값은 진단에 없으므로 dotted 표기를 출력했다고 단정하지 않는다.
  정규식 완화/주소 자동 변환/요구사항 변경 없음.
- 50 correction tests 및 Ruff 통과. 아직 미배포/새 요청 없음.
  다음은 정상 재시작 후 실제 검증. 전체 XG5000 생성·저장·재열기와
  자동/수동 전체 XG-SIM은 여전히 미완료.

## 2026-09-10 현재 요청 실행 설계 단계 진입

- run d1ce7406-ee75-4674-b612-34bbe9f7e375 ANALYZING 유지,
  elapsedSeconds360/stage planning_native_design 확인. 서버 task146249
  is_processing=true. 기존 narrative task143592에서 다음 설계 생성으로 진행했다.
- 아직 ready plan 채택/파일 생성/시뮬레이션 완료가 아니다. 이 run을 계속 확인하며
  중복 요청하지 말 것. 완료 시 설계 내용 및 전체 요구 coverage를 검토해야 한다.

## 2026-09-10 비질문 차단 반영 후 실제 요청 진행

- 이전 run terminal 및 Qwen slot idle 확인 후 정상 Host restart202.
  재인증/설정 qwen36, 100.64.0.1:8978/v1 확인, 기존 최신 메시지를 정상 API로 재제출.
- run d1ce7406-ee75-4674-b612-34bbe9f7e375 HTTP202, 이후 ANALYZING/
  planning_narrative 75초, 서버 task143592 is_processing=true 확인. 다음은 이 요청 확인.
  중복 요청/중단 없음. 아직 파일 생성이나 전체 설계 성공 증거 없음.
- 실제 사용자 화면 확인을 위해 computer-use 스킬 및 필수 참조를 다시 읽고
  sky.list_apps 수행: timeout, 2초 후 재시도 app-server exited, 커널 초기화 후에도
  app-server exited. 해당 도구의 입력은 중단. 화면 표시 성공으로 주장하지 말 것.

## 2026-09-10 최신 계획 완료는 실행 완료가 아님 / 비질문 차단 수정

- a8203138-65c8-45a0-9572-ea08355b1c04 COMPLETED이지만 plan_status는
  needs_clarification. design 배열은 모두 비어 있으며 파일 생성/시뮬레이션 없음.
- openDecisions에 실제 질문 대신 "모의 신호의 실제 물리 배선 및 NC 접점 정의는
  불필요 (모의 신호 의미만 적용)", "실시간 성능 측정 또는 외부 통신은 범위 밖"을
  넣어 필수 결정을 만들어 낸 실제 결함 확인. 사용자에게 다시 답변 요구하지 말 것.
- Agent narrative validator에 명확한 한국어 범위 제외 서술 탐지 추가. 모델에
  scope/assumptions로 재분류하고 실제 미결정을 재평가하도록 ModelRetry.
  질문을 자동 삭제하거나 ready/사용자 근거를 자동 보충하지 않음.
  실제 확인 요청과 다른 미해결 질문은 보존. 회귀 49 tests 통과.
- 이 수정은 아직 미배포. 새 모델 요청 없음. XG5000 미실행, Edge 창 존재만
  확인됐으며 사용자 화면 노출 증거는 아님. 전체 요구사항 미완료.

## 2026-09-10 주소 위임 최초 전달 반영 및 실제 요청

- 모델 idle 확인→정상 Host restart202→재인증/settings qwen36 확인.
- 기존 최신 사용자 내용을 정상 API로 재제출:
  run `a8203138-65c8-45a0-9572-ea08355b1c04` HTTP202/analyzing.
  다음 확인은 이 실행부터. 기존5625bb99는 FAILED, 중복 요청 금지.
- 전체 설계 생성/별도 파일 생성·재열기/전체 XG-SIM은 여전히 미완료.

## 2026-09-10 주소 위임 결정의 최초 요청 전달 보강 (미배포)

- 기존 반복 질문 거절용 주소 위임 탐지를 _stored_internal_address_delegation으로
  분리해 정확한 해당 문장을 반환. 동일 판단을 최초 resolved instructions에도 전달.
  CPU/내부 모의 조건 및 나중 위임 철회 처리 유지. 다른 결정/readiness 자동 보충 없음.
- correction47tests 및 resolved context3tests/Ruff 통과. 미배포/새 모델 요청 없음.
  최신5625bb99는 FAILED 유지. 이번 실패의 질문 원문은 보존되지 않았으므로
  주소 질문이 최종 실패 원인이라고 확정한 것은 아님.

## 2026-09-10 짧은 근거 참조 실제 실행 최종 실패

- run5625bb99-e4e6-4f19-af4b-dc1a0d8a70a0 최종 FAILED:
  planning_narrative/validator_rejected/planning_question_repeated.
  마지막 heartbeat675초. 서버 task138043 is_processing=false 확인.
- 이번 최종 오류는 enum이 아니라 이미 답한 사항을 반복 질문한 validator 거절.
  전체 narrative/native plan 채택 없음, 파일 생성/변경 없음.
  같은 요청 즉시 반복 대신 전달된 resolved context와 반복 질문 분기 확인 필요.
  현재 살아 있는 모델 요청 없음. 목표 전체 미완료.

## 2026-09-10 사용자에게 보이는 정상 웹 실행

- 사용자가 웹/XG5000이 보이지 않는다고 지적. 이전 headless QA를 사용자가 보는
  앱 실행과 명확히 구별할 것. XG5000 프로세스 없음 확인, 생성/시뮬레이션 미진행.
- open_in_codex는 queued였음. 이후 정상 개발 Launcher -Web 실행 exit0.
  실제 msedge PID8532 MainWindowTitle=ControlStudio - 프로필 1 - Microsoft Edge
  창 확인. 사용자 화면 위치/전경까지 시각적으로 확인한 것은 아님.
- 모델 요청 재제출/중단 없음. 최신 추적 run5625bb99 및 task135365 확인을 이어갈 것.

## 2026-09-10 짧은 근거 참조 반영 후 실제 실행

- 서버 idle 확인→정상 Host restart202→재인증/settings 확인.
  qwen36/100.64.0.1:8978/v1 유지. 짧은 근거 ID 출력/정확 원문 복원 반영.
- 기존 최신 사용자 내용을 정상 메시지 API로 재제출.
  run `5625bb99-e4e6-4f19-af4b-dc1a0d8a70a0` HTTP202/analyzing.
  다음 확인은 이 실행부터. 기존91faeb4a는 FAILED이며 중복 재제출 금지.
  전체 생성/저장·재열기/다중 모드 XG-SIM은 아직 미완료.

## 2026-09-10 긴 인용문 재출력을 짧은 근거 참조로 변경 (미배포)

- run91faeb4a 최종 FAILED 확인: planning_narrative/schema_validation,
  ready requirement_sources sequence/hardware_and_io/operating_modes/
  performance_and_acceptance enum 오류. 서버 task132169 idle. 더 기다리지 말 것.
- Qwen의 동적 StoredRequirementQuote enum을 q0/q1/... 참조로 변경.
  enum description에 해당 실행의 저장된 정확 원문 매핑을 제공하고,
  _StoredQuoteReference 값에 바인딩된 exact_quote만 normalization에서 복원.
  원문 재작성 허용/없는 근거 보충/brief 변경/모델 대신 source 선택 없음.
  Nemotron 기존 원문 enum은 유지. Qwen tool/prompted 둘 다 같은 타입 사용.
- 필수 근거 누락 거절, paraphrase 거절, 원문 exact 복원/brief 불변 포함
  correction47tests/Ruff 통과. instructions도 ID 선택 방식으로 갱신.
  미배포/실모델 미요청. 전체 생성/파일/시뮬레이션 완료로 보지 말 것.

## 2026-09-10 실제 진행 중 계획 요청의 새로고침 복원 수정

- run91faeb4a-2524-4284-9f58-545c022d2966 ANALYZING/서버 task122462 live 확인.
  live-restore.mjs 최초 검증에서 중지 버튼 timeout으로 실패 재현.
- bootstrap.activeOperation은 정확한 해당 run/conversation/analyzing을 반환했으나
  App의 currentEngineeringTarget 비교가 독립 planning conversation 복원을 거부.
  과거 이벤트 차단 이후 실제 run도 화면에 복원되지 않던 회귀 발견.
- restoreActiveOperation 조건에 현재 localStorage로 선택한 동일 new_project_planning
  대화인 경우 추가. 다른 대상 engineering 대화의 기존 격리 판단은 유지.
- npm build/typecheck/production-entry 성공. 실제 별도 headless Edge에서
  최초 열기 및 reload 모두 중지 버튼 visible/enabled, composer disabled 확인.
  중지 클릭/중복 요청 없음. live-restore.png 직접 확인. 모델 요청은 그대로 유지.

## 2026-09-10 Qwen narrative HTTP500 단일 prompted fallback 반영/실행

- Qwen3.6 이름/로컬 qwen36 alias의 narrative HTTP500에서만 동일 endpoint/model의
  PromptedOutput로 1회 fallback. 기존 tool 방식이 기본이며 반복 500은 전파.
  401/429/503은 fallback 없음. Nemotron502 정책 유지.
- exact stored quote schema, normalize/authority/readiness validator 모두 유지.
  Qwen prompted flag가 기존에 무시되던 분기 수정. 원인 확정이 아니라 제한된
  대체 응답 방식이며, 사용자 서버 오류 로그 답변은 아직 없음.
- FunctionModel 실 graph 테스트: tool500→prompted repeated delegated question
  거절→prompted genuine question 통과, brief 불변. 500 2요청 한도 및
  401/429/503 1요청 확인. correction47tests/Ruff 통과.
- 모델 idle 확인→Host restart202→정상 API 기존 최신 사용자 내용 재제출.
  새 run `91faeb4a-2524-4284-9f58-545c022d2966` HTTP202/analyzing.
  이 실행부터 이어서 확인. 기존6b597f1d는 FAILED, 중복 제출하지 말 것.
  전체 설계/생성/저장·재열기/전체 XG-SIM은 아직 미완료.

## 2026-09-10 작업 기록 왕복 실제 브라우저 검증

- 임시 inspect.mjs headless Edge 정상 UI에서 작업 기록→대동인덱스 재구성
  테스트 0909 선택→5초 관찰→현재 대동인덱스 대화형 모의 프로젝트로 복귀
  →5초 관찰 완료. 과거 대화 중지 버튼 없음, 복귀 후 composer enabled 확인.
  브라우저 finally 종료, 요청 전송/프로젝트 변경 없음.
- 최초복원/1440→390 최신 위치/과거 중간 위치 보존도 함께 재통과.
- 설계 서버 HTTP500 원인/전체 생성/저장·재열기/다중 모드 XG-SIM은 미완료.
  새 전체 모델 요청 없음. 서버 오류 줄에 대한 사용자 답변은 아직 없음.

## 2026-09-10 HTTP500 분리 진단 및 작업 기록 복원 보강

- 서버 idle 확인 후 qwen36 최소 도구 호출 및 native JSON 요청(128토큰,
  thinking off) 둘 다 HTTP200. tool_calls1/27tokens, native stop/6tokens.
  긴 narrative HTTP500 원인은 아직 미확인; 전체 모델 요청 재제출 없음.
  사용자에게 task116405의 마지막 서버 오류 줄을 비동기로 요청함.
- selectConversationHistory에서도 snapshot 없이 sequence0 replay하는 경로 확인.
  bootstrap→대화 조회 뒤 같은 epoch인지 확인하고, 일치하는 activeOperation만
  복원하며 snapshot.lastEventSequence를 replay 필터에 전달하도록 수정.
- isHistoricalRunReplay 추출: 과거 run/token/progress 제외, 새 이벤트 및 과거
  도구/근거/저장 메시지는 보존하는 회귀 테스트 통과. 실제 기록 선택 UI
  재검증은 아직 남아 있음. 기존 최초복원/resize 실기 증거는 아래 절 참조.

## 2026-09-10 안전한 native 응답 진단 Host 반영

- task116405 idle 확인 후 정상 Host restart202 및 재인증 settings200 확인.
  qwen36/100.64.0.1:8978/v1 유지. Host stderr numeric-shape allowlist 반영.
- Web composer/permissions/evidence/request-permissions 4 scripts 모두 통과.
  새 모델 요청은 제출하지 않음. 최신 전체 실행6b597f1d는 narrative HTTP500 FAILED.
  다음 단계는 이 HTTP500의 원인 분리 또는 전체 실행 설계 생성 경로 개선이며,
  진단 반영만으로 설계 생성 문제가 해결됐다고 보지 말 것.

## 2026-09-10 실제 브라우저 UI 검증 및 복원/resize 수정

- run6b597f1d-e452-4c71-9e08-738a35a0f149 최종 FAILED:
  agent_runtime_provider_http_500, stage=planning_narrative. task116405 idle 확인.
  더 기다리거나 중복 재제출하지 말 것. 새 계획/파일 없음.
- CUA 실패 대신 임시 개발용 Playwright+headless Edge에서 정상 launcher handoff로
  실제 localhost 앱 로그인/기존 대화 복원/렌더링 성공. 사용자 브라우저 탭 미변경.
  별도 브라우저는 스크립트 finally에서 종료한다. npm 임시 설치 위치:
  C:/Users/Administrator/AppData/Local/Temp/controlstudio-ui-qa-7f11b7f630284b38921ce8904668edda
  inspect.mjs 및 desktop.png/mobile.png가 있음. 원문 로그는 최소화할 것.
- 발견: bootstrap 최신 상태 복원 후 SSE sequence0 과거 run events가 다시
  activeRunState를 ANALYZING으로 만들고 입력창을 잠금. startEventStream에
  snapshotSequence 추가, bootstrap.lastEventSequence 이하 과거 run 상태/token/
  progress는 현재 UI에 재적용하지 않음. 도구 이력 replay는 보존.
  다른 대화 전환 경로의 snapshot watermark는 아직 추가 확인 필요.
- 발견: 1440→390 resize시 최신 대화에서 오래된 위치로 밀림. 최신 따라가기
  상태에서만 resize시 bottom 재정렬. 과거 읽는 상태는 강제 이동하지 않음.
- npm run build/typecheck/production-entry 통과. 실제 Edge 재검증:
  복원 후 5초 replay에도 composer enabled, 1440/390 가로 넘침 없음,
  resize 최신 위치 유지, 과거 중간 지점 읽기 시 latest 강제 이동 없음.
  최신 screenshot 직접 확인. 좁은 화면도 입력창 화면 내 유지.
- Host stderr 진단 수정은 여전히 미배포. 모델 유휴 상태이므로 정상 재시작
  후 반영 가능. 전체 설계/생성/저장·재열기/다중 모드 XG-SIM은 미완료.

## 2026-09-10 Host stderr 필터로 native 응답 진단 유실 확인 및 수정 (미배포)

- 현재 run `6b597f1d-e452-4c71-9e08-738a35a0f149` ANALYZING,
  planning_narrative elapsed285초, 서버 task116405 is_processing=true 확인.
  요청 중복 제출/재시작 없음. 이 실행을 먼저 이어서 확인할 것.
- 기존 native response shape logger는 Host AgentSupervisor._drain_stderr에서
  허용되지 않아 버려짐을 소스에서 확인. 이전 진단 보강만으로 Host 로그에
  남는다고 가정하지 말 것. 현재 진행 중 프로세스는 여전히 그 버전이다.
- Agent 출력은 고정 finish enum + 두 숫자의 한 줄 WARNING으로 변경하고,
  Host에는 타임스탬프/모듈/정확한 문법/enum/9자리 이하 숫자를 fullmatch하는
  allowlist만 추가. 임의 stderr 본문 전달 없음. Host 테스트 2개 및 Ruff 통과.
  현재 모델 실행을 중단하지 않기 위해 미배포 상태로 둠.
- 브라우저 cua.getState 두 번 모두 30초 timeout. 시각적 UI 검증은 미실행.
  전체 다중 모드 runner 제한과 나머지 제품 완료 조건은 여전히 남아 있다.

## 2026-09-10 잘린 native 응답의 같은 예산 재생성 제거 및 실제 재실행

- _BoundedNativeDesignRetry에서 ValidationError + 마지막 응답 finish_reason=length인
  경우 동일 예산 ModelRetry를 소모하지 않고 기존 상위 1회 확대 재시도로 전달한다.
  원래 ValidationError cause 유지. finish=stop 문법 오류는 기존 수정 재시도 유지.
  6000/12000 한도와 도메인/안전 검증은 변경하지 않았다.
- FunctionModel 실제 agent graph 회귀: length 1요청, stop+invalid 2요청 확인.
  native_output_correction 45 tests 및 Ruff 통과.
- 모델 idle 확인 후 정상 Host restart202, 재인증/settings200 확인.
  native 응답 진단 및 카운트 hold 수정도 함께 소스 런타임에 반영.
- 기존 최신 user content를 정상 API로 재제출, run
  `6b597f1d-e452-4c71-9e08-738a35a0f149` HTTP202/analyzing.
  다음 턴은 이 실행부터 확인하고 중복 제출하지 말 것.
  전체 설계 생성/저장·재열기/전체 다중 모드 XG-SIM은 아직 미완료.

## 2026-09-10 native 재시도 응답 진단 보강 (미배포)

- 인증된 settings 200/qwen36/100.64.0.1:8978/v1 및 모델 idle 확인.
  최신 전체 run은 여전히 47b0878c FAILED이며 신규 요청은 제출하지 않았다.
- native 최초/502 fallback/길이 확대 재시도에 공통 응답 shape 기록 연결.
  finally에서 최근 2개 응답의 finish_reason/output_tokens/text_chars만 기록한다.
  본문·추론은 기록하지 않는다. 모델 응답 형식 자체를 고친 것은 아니다.
- 진단 단위 테스트 2개 및 Ruff 통과. 이번 수정은 미배포이며 실제 대형 응답
  원인 분리/전체 설계 생성/저장·재열기/전체 XG-SIM 검증은 여전히 남아 있다.

## 2026-09-10 카운트 유지 관찰 기간 보강 (미배포)

- _verify_increments의 단순3회 읽기를 _hold_data_value 1초 반복 관찰로 연결.
  마지막 샘플까지 값 유지/최소2샘플/elapsed 기록, 중간 불일치 즉시 실패,
  레지스터 쓰기 없음. 기존 flat observation 필드 보존. sampled evidence이며
  sub-poll 순간 변화의 부재를 증명하지 않는다.
- 늦은 추가 카운트 실패 회귀 포함 MCP28 tests 통과.
  전체 다중 모드 runner/step-dependent counter 지원을 대체하지 않는다.
  Host 재시작/실기 미실행, 새 모델 요청 없음.

## 2026-09-10 JSON 실패 분류 배포

- 모델 idle 확인 후 정상 Host restart202 및 재인증/settings 조회 성공.
  qwen36/100.64.0.1:8978/v1, 키/추가 헤더 없음 유지.
- JSON 실패 분류 반영. 새 전체 모델 요청은 제출하지 않았다.
  최신 run47b0878c는 FAILED. 실제 큰 응답의 EOF/문법 구분은 아직 미확인이다.

## 2026-09-10 JSON 실패 분류 진단 추가 (미배포)

- _output_contract_diagnostic의 json_invalid를 정적 loc 코드
  unexpected_end_of_json / invalid_json_syntax로 구분. EOF parser prefix만 분류,
  자유형 오류 메시지/응답/추론/키는 기록하지 않는다. EOF 자체는 서버의 length
  종료 사유 증거와 다르므로 둘을 동일시하지 말 것.
- Agent81 tests 통과. 아직 배포/실기 재요청 없음. 이 변경은 원인 분리를 위한
  진단이며 생성 실패 해결이나 full runner 구현 완료가 아니다.

## 2026-09-10 서버 JSON/비추론 분리 진단

- idle 확인 후 동일 qwen36 endpoint에 작은 strict json_schema 요청(128토큰 한도,
  enable_thinking=false 및 native 샘플링)을 보냈다. finish=stop,
  content14chars/reasoning0chars/completion7tokens, 기대 객체 {ok:true} 일치.
  서버 전체의 JSON/비추론 미지원은 아니라는 증거. 전체 설계 지원/성공 증거는 아님.
- 큰 설계의 최종 json_invalid가 길이 제한인지 기타 형식 실패인지 구분할
  terminal metadata 확보가 다음 진단 과제. 기존 host-restart.log에서 지정된
  planning response shape/native truncated 로그 검색은 결과가 없었다.
  최신 전체 run47b0878c는 FAILED 유지. 새 전체 요청 없음.

## 2026-09-10 최신 실행 최종 JSON 실패

- run47b0878c-edd5-4baf-b519-1095117c4ed4 최종 FAILED 확인.
  stage=planning_native_design, schema_validation/structured_output_contract,
  validationErrors=<root>:json_invalid. 마지막 heartbeat2250초.
  서버 task104370/is_processing=false/n_prompt_tokens34499. 더 기다리지 말 것.
- native instruct 및 12000 확대 재시도로도 유효 최종 객체 미확보.
  실제 full plan 채택/파일 생성/모의 운전 없음. 같은 설정 반복 제출 대신
  출력 생성 구조/응답 형식과 예산 원인을 추가 확인해야 한다.

## 2026-09-10 現 native 출력 확대 재시도

- run47b0878c-edd5-4baf-b519-1095117c4ed4는 planning_native_design 진행 중.
  서버 task92354/is_processing=true/n_predict12000 확인(960초).
  기존 명시적 length 응답에 대한 확대 재시도 경로. 중복 제출 없음.
  최종 결과는 아직 없으며 같은 run부터 확인할 것.

## 2026-09-10 nested coverage 안내 배포 및 실기 시작

- 서버 idle 확인 후 정상 Host restart202, 재인증/settings 조회 성공.
  qwen36/100.64.0.1:8978/v1/키 없음/추가 헤더 없음 유지.
- 기존 최신 대화 답변을 정상 API로 재제출: run
  `47b0878c-edd5-4baf-b519-1095117c4ed4`, HTTP202/analyzing.
  이 요청 결과부터 확인. 이전 a2620311은 FAILED. 아직 실기 성공 없음.

## 2026-09-10 nested native 오류의 coverage 안내 누락 수정 (미배포)

- 실제 오류 loc는 native_design.io_points.10.signal_id인데 retry feedback의
  coverage index 수집은 길이3/io_points 시작만 처리했다. 알려진 native_design
  래퍼만 제거하여 직접/중첩 검증 모두 누락 network/safety 목록을 보완 안내에 반영.
  원문 input echo/설계 자동수정/검증 완화 없음.
- nested mixed 12 errors/뒤쪽 indices 누락 회귀 추가. Agent80 tests 통과.
  현재 앱 재시작/실모델 재실행 전. 최신 a2620311은 FAILED이며 새 요청 없음.

## 2026-09-10 native JSON 이후 안전 범위 검증 실패

- run a2620311-3a30-47de-8cac-cda0915496d6 최종 FAILED, 서버 task73815 idle.
  stage=planning_native_design, schema_validation/native_output_safety_coverage,
  native_design.io_points.10.signal_id.every_digital_output_requires_an_explicit_safety_boundary.
  마지막 heartbeat765초. JSON 파싱 이후 의미 검증 실패이며 설계 채택/파일 변경 없음.
- 이 run은 더 기다리지 말 것. 기존 retry feedback는 이미 누락 io_points index와
  affected_output_signal_ids 정확한 참조, 출력 삭제/재분류/가상 안전 하드웨어 금지를
  안내한다. 같은 안내를 중복 추가하거나 안전 검사를 제거하지 말 것.
  구조화된 의미 검증까지 진행됐지만 전체 설계/실사용 성공은 아니다.

## 2026-09-10 사전 맥락 수정 후 실제 native 단계 도달

- run a2620311-3a30-47de-8cac-cda0915496d6는 270초에 planning_native_design으로
  전환. 서버 task69913/is_processing=true. narrative 반복 질문 경로를 통과한
  이번 실행 증거이며 전체 설계 성공은 아니다. 같은 run 결과부터 확인할 것.

## 2026-09-10 반복 질문 사전 맥락 배포 및 재실행

- 모델 idle 확인 후 정상 /api/v1/host/restart HTTP202, 이후 인증된 settings 조회
  성공으로 앱 재기동 확인. qwen36/100.64.0.1:8978/v1/키 없음/헤더 없음 유지.
- 기존 최신 답변을 정상 대화 API로 재제출: HTTP202,
  run `a2620311-3a30-47de-8cac-cda0915496d6`, analyzing.
  이 요청을 먼저 확인하고 중복 제출하지 말 것. 아직 결과/설계 생성 성공 없음.

## 2026-09-10 반복 질문 사전 맥락 수정 (미배포)

- _stored_simulation_polarity_quote로 기존 근거 추출을 분리하고,
  _resolved_simulation_instructions를 narrative 첫 호출 instructions에 연결.
  저장된 명시적 PC ON/OFF 문장만 JSON 인용으로 전달하며 승인/실제 배선으로
  해석하지 않도록 명시. 다른 미정 사항 보존, 이후 미정 수정은 추출하지 않음.
  DB 보충/ready 강제/검증 완화 없음. 기존 반복 질문 validator 유지.
- Agent 전체 79 tests/Ruff 통과. 아직 재시작/실모델 검증 전이다.
  최신 실기 run086fabe7은 FAILED. 새 요청을 제출하지 않았다.

## 2026-09-10 再실행 narrative 반복 질문으로 최종 실패

- run `086fabe7-639e-4b7a-9a7e-0c9532c4fe6c` FAILED 확인.
  stage=planning_narrative, validatorCode=planning_question_repeated,
  reasonCode=validator_rejected. 마지막 heartbeat 495초, 서버 task64511 idle.
  이 요청은 더 기다리지 말 것. native 단계 미도달로 instruct 변경 효과 미확인.
- 최신 코드 전체 Agent 77 tests, MCP 27 tests 및 관련 Ruff 통과.
  이는 실모델 성공/전체 자동·수동 구현 완료가 아니다.

## 2026-09-10 앱 복구 및 native instruct 실기 재요청

- Start-ControlStudio-Development.ps1 -NoOpen 정상 종료. 인증된 settings/model
  조회 성공: http://100.64.0.1:8978/v1, qwen36, hasApiKey=false, customHeaderNames=[].
  주의: /health는 SPA HTML을 반환하므로 그것만으로 readiness를 주장하지 말 것.
- 서버 slot idle 확인 뒤 정상 대화 API로 기존 최신 사용자 메시지를 재제출했다.
  run `086fabe7-639e-4b7a-9a7e-0c9532c4fe6c`, HTTP202/analyzing.
  이 run 결과부터 확인. 이전 run13581695는 FAILED이며 다시 기다리지 말 것.
  새 run의 native 단계 옵션 실제 적용 및 결과는 아직 미확인.

## 2026-09-10 native Qwen instruct 설정 수정 — 실행 앱 연결 거부

- _native_design_model_settings의 Qwen3.6/qwen36만 공식 instruct 설정
  temperature=.7/top_p=.8/presence_penalty=1.5,
  chat_template_kwargs.enable_thinking=false로 변경. 일반/tool/narrative 설정과
  기존 6000/12000 한도 및 검증은 유지. 긴 reasoning이 유일 원인이라는 증거는 없으며
  실제 실패에 대한 검증할 가설이다. 공식 모델 카드의 non-thinking/best-practices 확인.
- test_qwen_sampling 5 tests 통과(일반 및 native 실제 MockTransport body 전달 포함),
  runtime/tests Ruff 통과. 실모델 성공은 아직 미확인.
- 정상 재시작을 위해 /api/launcher/handoff에 연결했으나 WinError10061.
  재시작 API는 호출되지 않았다. localhost8767 앱 가용 상태 확인/복구 후 배포 확인과
  실제 계획 재검증 필요. 이전 native 요청은 FAILED/서버 idle 확인된 상태다.

## 2026-09-10 Qwen 요청 종료 및 유지 상태 관찰 수정

- run `13581695-a136-448c-bb45-47a88fe43e84` 최종 FAILED 확인.
  `agent_output_contract`, stage=planning_native_design,
  reasonCode=schema_validation, validatorCode=structured_output_contract,
  validationErrors=<root>:json_invalid. /slots task 49799 is_processing=false.
  더 이상 이 요청을 기다리지 말 것. 설계/변경 채택 없음. 새 요청 제출 없음.
- MCP native_simulation.py에 _hold_values 반복 관찰을 추가하고 타이머 완료 후
  시작 유지 및 입력 완료 후 해제 전 경로에 연결했다. 관찰 중 mismatch는 즉시 실패,
  입력 쓰기 없음, 최소 2 samples/1초 및 경과 시간 기록. 서브 폴링 순간 변화를
  증명하지 않는다. 전체 다중 모드 runner 구현을 대체하지 않는다.
- test_native_held_state.py 추가: 정상 반복 관찰, 중간 재동작 실패, 잘못된 기간,
  실제 sequence 해제 전 연결 순서. 기존 STOP 복원 포함 7 tests 통과, Ruff 통과
  (마지막 연결 테스트 추가 전 Ruff). 아직 Host 재시작/배포/실기 미실행.
- 다음 작업은 native JSON 출력 실패의 원인 확인 및 수정, 전체 다중 모드 실행부,
  별도 파일 생성/저장/재열기와 실제 UI/XG-SIM 검증이다.

## 2026-09-09 native 출력 한도 확대 재시도

- run `13581695-a136-448c-bb45-47a88fe43e84`는 23:38:16 KST/2070초에
  ANALYZING/planning_native_design. 서버 task 37780/is_processing=true,
  n_predict=12000 확인. 이전 task 31762는 6000 한도였다.
- 현재 코드의 Qwen native 12000 한도는 마지막 response.finish_reason=length일 때만
  실행하는 한 번의 확대 재시도 경로다. schema/근거 검사는 유지된다.
  최종 성공/실패는 아직 없으며 이 동일 run부터 확인해야 한다. 중복 제출 없음.

## 2026-09-09 native 설계 후속 요청 진행 확인

- 같은 run `13581695-a136-448c-bb45-47a88fe43e84`가 23:27:16 KST/1410초에도
  ANALYZING/planning_native_design이었다. /slots task는 29001에서 31762로
  바뀌었고 is_processing=true/n_predict=6000이었다. 앱에서 중복 제출한 것이 아니다.
- 최종 결과나 검증 성공 기록은 아직 없다. 이 턴은 실제 서버 작업을 확인한
  verified wait이며 다음 턴도 동일 run의 terminal 결과부터 확인할 것.

## 2026-09-09 Qwen 실제 native 설계 단계 도달

- 같은 run `13581695-a136-448c-bb45-47a88fe43e84`가 23:20:45 KST/1020초에
  planning_native_design으로 전환됐다. /slots에서 task 29001/is_processing=true,
  n_predict 6000 확인. 이전 narrative task 23038과 다른 다음 요청이다.
- 기존 샘플링 변경 후 실제 narrative가 ready 경로를 통과해 native 단계로 넘어간
  증거다. 전체 native 설계 검증/프로젝트 생성/저장/재열기/XG-SIM 성공은 아니다.
  실행 중인 동일 run부터 확인하고 중복 제출/재시작하지 말 것.

## 2026-09-09 Qwen 재실행의 실제 진행 확인

- 이전 목표 턴은 샘플링 코드 수정/검증과 실제 실행 제출로 progress였다.
- 이번 턴은 run `13581695-a136-448c-bb45-47a88fe43e84`를 계속 확인한
  verified wait다. 23:09:15 KST planning_narrative/330초/ANALYZING,
  서버 /slots의 동일 task 23038이 is_processing=true였다. 서버 상태의
  n_prompt_tokens도 12657→13519→13943→14461로 변했다. terminal 아님.
  중복 실행/재시작하지 않았으며 다음 턴도 동일 run부터 확인할 것.
- 원본과 작업 사본의 SHA-256을 실제 재확인했다. 둘 다
  BEE3B1A6E69469C0E1384CB8D9D758FEF2F95A92F0CC7AC6486DEEA941A9A68F로
  이전 원본과 같았다. 이는 보존 증거이며 신규 파일 생성/실기 완료 증거는 아니다.

## 2026-09-09 Qwen3.6 호출 샘플링 정합성

- 이전 턴은 STOP 복원 결함 수정/검증/배포로 progress였다. 현재 서버 /props에서
  실제 모델이 unsloth Qwen3.6-35B-A3B-UD-IQ4_NL.gguf임을 확인했다.
  context 196608, slot 1, 서버 기본 temperature 0.6/top_p 0.95/top_k 20/min_p 0.
- 앱은 모든 일반 모델에 temperature=0을 덮어쓰고 있었다. 공식 Qwen 문서
  https://huggingface.co/Qwen/Qwen3.6-35B-A3B#best-practices 의 정밀 코딩 권장에
  맞춰 qwen36 및 Qwen3.6 이름만 temperature 0.6/top_p 0.95/presence_penalty 0,
  top_k 20/min_p 0으로 변경했다. 다른 모델/토큰 한도/근거 검사는 유지한다.
  이는 원인 가설에 따른 호출 설정 수정이며 실기 재질문 해결을 증명하지 않는다.
- capability의 도구/스트림 검사도 같은 샘플링 함수를 쓰게 했다. 별칭 구분,
  native/재시도 한도 유지, 실제 HTTP request body 전달 등 포함 Agent 77 tests/Ruff 통과.
- 정상 Host 재시작 후 첫 capability 검사는 22:59:04 KST success=false였으며
  계획 자동 제출은 실행되지 않았다(session 46371 종료). 같은 production runtime의
  별도 진단 재검사는 23:02:50 KST 다섯 기능 모두 통과했다(session 85278 종료).
  단독 성공으로 간헐 실패나 실제 계획 품질이 해결됐다고 보지 말 것.
- /slots 조회로 실제 진행 중 요청의 temperature 0.6/top_p 0.95/top_k 20/min_p 0
  적용도 확인했다. 원문/추론/키는 출력하지 않았다.
- 동일 기존 대화/최신 답변으로 정상 API run
  `13581695-a136-448c-bb45-47a88fe43e84` 제출(HTTP 202). 현재 결과부터 확인하고
  실행 중이면 중복 요청/재시작하지 말 것.

## 2026-09-09 신규 XG-SIM STOP 실패 시 입력 복원 금지

- 이전 목표 턴은 코드 변경과 실제 실패 원인 확인이 있어 progress였다.
  현재 최신 계획 run은 f545a59e의 FAILED이며 새 모델 요청은 하지 않았다.
- 다중 모드 시험 실행부를 점검하다 NativeSimulationRunner.restore_baseline이
  cpu_stopped 인자를 영수증에만 기록하고 실제 쓰기 차단에 사용하지 않음을 확인했다.
  STOP 미확인 시 읽기/쓰기 없이 attempted=false, matched=false를 반환하고
  캡처한 baseline을 보존하도록 수정했다. 명시적 True만 허용한다.
- server의 restore_attempted도 STOP 확인 여부와 일치시켰다. STOP 실패를
  입력 복원 성공 또는 복원 쓰기 시도로 표시하지 않는다. 정상 STOP 후 exact
  write/readback 검사는 유지한다. MCP 23 tests와 Ruff 통과, 정상 Host restart 요청.
- 이 수정은 전체 자동/수동 runner 구현·실기 성공을 대체하지 않는다.
  질문 루프, 별도 생성/재열기, 다중 모드 XG-SIM, 화면 도구 복구는 여전히 남는다.

## 2026-09-09 Qwen의 이미 답한 모의 극성 재질문 수정

- `6ff268a2-b29d-4ae6-8248-11a80dcd9876`는 22:09:15 KST/240초에
  COMPLETED지만 needs_clarification이었다. 바로 전 대화에서 명시한 모의
  ON/OFF 정의를 다시 질문했다. 저장 supplemental_requirements에는 답이 있다.
- explicit PC 모의 ON/OFF 정의에 한해서 동일 극성 선호 질문을 받으면
  저장 원문을 ModelRetry로 돌려주도록 했다. 물리 NC 배선 질문, 미정 조건,
  이후 극성 수정은 자동 해결하지 않는다. 질문 제거/ready 승격/brief 변경 없음.
  실제 저장 brief와 질문으로 탐지 확인, 회귀 72 tests/Ruff 통과.
- 정상 재시작 후 동일 최신 답변으로 `f545a59e-613a-4d86-832b-559495641ae5`
  실행은 FAILED / planning_narrative / validator_rejected /
  planning_question_repeated로 종료. 마지막 progress 22:45:43 KST/630초.
  원문 피드백 후에도 실기 재질문이 해소되지 않았으므로 수정 완료로 보지 말 것.
  기존 원본 및 작업 사본 변경, 신규 파일 생성, 전체 XG-SIM 없음.
- CUA getState 반복 timeout. Windows computer-use list_windows도 timeout,
  재시도에서는 app-server exited, 명시적 JS reset/재초기화 후에도 timeout.
  화면 점검 도구 복구가 필요하며 이번 시각/사용자 조작 검증은 수행하지 못했다.
  현재 live 계획 요청은 종료됐으므로 진행 중으로 보고하지 말 것.

## 2026-09-09 사용자 로컬 Qwen 연결 및 작업 재개

- 사용자가 `100.64.0.1` 로컬 모델 서버 연결과 작업 재개를 지시했다.
  앞선 세 모델 제한과 별개로 이 연결의 `qwen36` 사용이 승인됐다.
- `http://100.64.0.1:8978/v1/models` HTTP 200, 모델 `qwen36` 확인.
  단독 production capability 검사는 다섯 기능 모두 통과했다.
- 앱이 내려가 있어 기존 개발 실행기로 기동했다. 첫 Host 설정 저장 검사는
  HTTP 422로 거부됐으나, 재검사 2026-09-09 21:45:42 KST에 completion,
  streaming, toolCalling, toolResultContinuation, structuredOutput 모두 통과했다.
  정상 설정 API 저장 HTTP 200, 후속 GET으로 qwen36/새 endpoint를 확인했다.
  hasApiKey=false, customHeaderNames=[]: 이전 서버 인증을 전달하지 않았다.
- 브라우저 자동화는 두 번 timeout 후 명시적 reset 및 재시도도 timeout이었다.
  이번 화면 조작/시각 검증은 미완료. 정상 인증 Host 메시지 API로 기존 대화의
  저장 요구를 이어갔다. run `1350879d-b827-4206-9425-9ea7d96151e2`는
  planning_narrative에서 FAILED / validator_rejected / requirement_source_invalid로
  종료됐다. 마지막 progress 21:54:00 KST / 450초. 모델 연결 성공은 별도 파일
  생성·재열기·전체 XG-SIM 완료가 아니다.
- Qwen narrative ToolOutput에도 exact stored quote 선택형 스키마를 적용했다.
  기존 Nemotron만 선택형이고 Qwen은 자유 인용 문자열이던 차이를 수정한 것이다.
  근거/권한 검사와 질문 status를 유지하며 저장 brief를 자동 채우지 않는다.
  Qwen tool 응답의 의역 거부→정확 인용 재시도→정규화 회귀 검사 추가.
  ready 필수 근거 누락 거부 및 ToolOutput 유지 회귀 검사도 추가해 Agent 전체
  70 tests, Ruff 통과. Web 관련 4 scripts 및 typecheck 통과.
- 정상 Host restart HTTP 202 후 새 Qwen 설정 유지 확인. 같은 요구로
  `9d7682e5-4307-44e1-af45-4787ea253eae`는 22:03:57 KST / 300초에
  COMPLETED지만 needs_clarification이다. 원문 인용 오류는 재현되지 않았고
  모의 신호 ON/OFF 선호와 알람 표시 선호를 질문했다. native 설계 완료 아님.
- 사용자가 위임한 PC 모의 설계 범위에서 버튼/준비/완료/비상정지발생/도어열림
  각각 ON=해당 조건, OFF=조건 없음, 알람 문구는 이동 시간 초과/비상정지/도어 열림으로
  정하고 기존 운전·복구 조건을 유지하도록 정상 대화 API에 답했다. 물리 배선 정의 아님.
  run `6ff268a2-b29d-4ae6-8248-11a80dcd9876` 진행 중. 이 실행부터 확인할 것.

## 2026-09-09 근거 항목 존재와 세부 결정 완료의 구분

- 20:16 Ultra run `0034f385-e3c9-4775-970e-eb0a6b808dcc`는 FAILED:
  planning_narrative / validator_rejected / planning_question_repeated.
  마지막 fresh progress는 20:19:54/225초. 서버 오류가 아니라 출력 검사 실패다.
  두 질문 guard가 같은 diagnostic을 쓰므로 어느 guard였는지는 단정할 수 없다.
- 코드에서 다섯 필드의 exact source mapping이 있다는 이유만으로 모든
  needs_clarification을 거부하던 포괄적 guard를 제거했다. 항목에 근거가 있어도
  아직 미정인 복구/운전 세부 결정이 있을 수 있다. 알려진 내부 주소 위임을
  다시 질문하는 specific guard는 유지한다. source/authority/readiness/native
  검사나 승인 경계는 바꾸지 않으며 status를 자동 ready로 승격하지 않는다.
- 미정 복구 조건을 실제 runtime에서 질문으로 보존하고 native pass를 실행하지
  않는 회귀 테스트 추가. Agent 전체 68 tests/Ruff 통과, 정상 앱 재시작 반영.
- 반영 후 `7e570542-643a-4253-a267-9ffa558ec959`는 20:26 COMPLETED지만
  needs_clarification이다(전체 작업 완료 아님). 실제 화면에서 주소 배치 승인 방식,
  알람 표현 방식 선호, 시험 시나리오 문서화 여부를 질문했다. blanket guard 제거가
  질문을 숨기던 실패를 드러냈으나 불필요한 구현 선호 질문 문제는 남아 있다.
- UI에서 주소/알람 표현은 검토용 설계안으로 제안하고 기존 동작 조건을 유지하며
  시험 시나리오를 문서화하도록 답했다. 이 답변으로 이어진
  `2fd17554-00c0-4e25-b224-ced0d6998762`는 planning_narrative HTTP 502로
  FAILED. 마지막 progress 20:31:25/240초. 현재 실제 계획 run은 모두 종료됐다.
  승인된 native 설계나 새 프로젝트 파일은 아직 없으며 기존 파일/PLC 변경 없음.
- 이후 Gemma 31B와 26B의 production capability 검사도 다시 rate_limited로
  종료됐다. live probe/plan 실행 없음. 세 모델 외 사용은 사용자 지시로 제외한다.
  현재 blocker는 허용된 연결들의 실제 출력/사용량 제한이며 모델 변경 승인 부족이 아니다.

## 2026-09-09 저녁 사용자 지정 세 모델로 재개

- 사용자가 같은 서버에서 세 모델만 사용하도록 승인했다. 우선순위는
  nemotron-3-ultra → gemma-4-31b-it → gemma-4-26b-a4b-it. 나머지는 제외한다.
  이전의 '다른 모델 연결 승인 대기'는 더 이상 blocker가 아니다.
- 서버 /models에서 이 세 모델의 등록을 실제 확인했다. Gemma 31B/26B는
  현재 설정의 동일 endpoint/인증으로 production test_capabilities를 호출했지만
  둘 다 safe failure_kind=rate_limited였다. 설정 저장/검사 우회는 하지 않았다.
- Ultra의 짧은 실제 chat 요청은 HTTP 200이었다. 이는 계획 생성이나 도구 실행
  성공을 증명하지 않는다. 기존 UI 대화에서 20:16 다시 실행한
  `0034f385-e3c9-4775-970e-eb0a6b808dcc`는 planning_narrative/ANALYZING.
  이 run의 현재 상태를 확인하고 실행 중이면 끊거나 중복 요청하지 말 것.
- CUA 연결이 두 번 timeout이었으나 명시적 세션 reset 뒤 복구됐다.
  모델 설정 화면은 열고 닫았을 뿐 모델/키/endpoint/동의 설정을 바꾸지 않았다.
  실제 파일 생성/저장/재열기/전체 다중 모드 XG-SIM은 여전히 미완료다.

## 2026-09-09 계획 상태별 근거 스키마와 502 복구

- `2a7419a4-c1e3-4a66-acdc-e0cfb86a9730`는 14:34 FAILED:
  planning_narrative / validator_rejected / requirement_source_missing. native 참조
  보완 단계에 도달하지 못했다. 이전 절의 ANALYZING 기록은 이 결과로 대체한다.
- Nemotron narrative는 concrete object root의 decision 아래 status-discriminated
  ready/clarification 응답을 사용한다. ready는 저장 brief에서 비었거나 미정인
  다섯 필드의 exact stored quote를 non-null 필수로 요구한다. clarification은
  null/누락을 허용한다. 기존 확정 필드는 근거 재입력을 강요하지 않는다.
  응답 정규화 후 기존 domain/authority/readiness 검사를 그대로 수행한다.
  사용자 근거를 자동 선택하거나 brief/승인 상태를 채우지 않는다.
- 정상 재시작 후 14:46 `37c0b039-01bd-4d8e-99b2-c826f738d49c`는
  planning_narrative HTTP 502로 FAILED. 스키마 변경의 실제 성공 증거는 없다.
- narrative에도 Nemotron HTTP 502에 한해서 같은 model/endpoint의 PromptedOutput
  스키마 전달로 한 번 복구하도록 연결했다. 모든 validator/사용자 history/brief는
  동일하다. fallback은 request_limit=2이며 반복 502/401/503이나 schema/authority
  실패에 무한 재요청하지 않는다. 취소/명시적 truncation 경로는 유지한다.
- Agent 전체 66 tests/Ruff 통과. ready에서 각 missing/null 필드 거부, 확정 필드
  보존, envelope 정규화, 런타임 fallback 뒤 반복 주소 질문 거부/진짜 미정 조건
  보존, 연속 502 bounded retry와 다른 HTTP 오류 비복구를 검사했다.
- 정상 재시작 후 14:49 UI에서 같은 대화/요구로
  `fd055304-e277-4461-be14-3b96b3e018f6` 실행, 최종 FAILED:
  planning_narrative / model_api_error / provider_http_502. 마지막 progress는
  14:52:23/150초였다. 현재 추가 실행 없음. 동일 연결 반복 재요청하지 말 것.
  화면 대화/진행/중단 조작 확인;
  이번 절에서 Web 소스 변경은 없다.
- 후속 통합 회귀: blank brief + supplemental exact source → ready envelope →
  실제 runtime native pass → 검토 artifact 변환이 mock model로 통과하며 DB/brief
  backfill은 없다. 집중 37 tests 통과(전체 66 통과 뒤 이 테스트 1개 추가).
- 전체 목표 미완료. 신규 파일 생성/저장/재열기 및 전체 다중 모드 XG-SIM 미완료.
  기존 NativeSimulationRunner의 단일 mode/sequence 제한은 여전히 남아 있다.
  원본/작업 사본/모델 연결 설정/실제 PLC 변경 없음.

## 2026-09-09 native 참조 오류 위치 보완

- `87d59ef8-d080-48be-8ca9-c5829dd4f105`는 최종 FAILED.
  planning_native_design / schema_validation / native_ladder_unknown_reference,
  validationErrors=native_design:value_error. 마지막 진행은 14:20:58/645초였다.
  현재 모델 재요청 없음. 파일 생성/변경은 수락되지 않았다.
- provider→domain 변환은 signal/timer/sequence/step ID를 유지한다. 기존 domain 오류는
  생성된 rung ID와 unknown source type만 표시해 중첩 provider 필드 위치가 사라졌다.
  provider 검증에서 선언 집합과 contact/action 참조를 대조하고 ladder_rungs index,
  condition.terms 경로와 해당 source/target 필드에 오류를 연결했다.
  coverage 누락과 unknown reference를 함께 수집하며 어떤 선언/참조도 자동 수정하지 않는다.
- 재작성 안내는 signal_id/timer_id/step_id/sequence_reset_id의 선언 위치와 주소/상징 ID,
  sequence ID/step ID 차이를 설명한다. safety 오류가 먼저 있어도 참조 안내를 포함한다.
  diagnostics에는 기존 allowlisted native_ladder_unknown_reference와 안전한 field path만
  사용하며 rejected input/context 또는 실제 참조 문자열은 되풀이하지 않는다.
- Agent 62 tests/Ruff 통과. 중첩 경로·세 가지 동시 참조 오류·input 비노출·원문 불변,
  safety와 혼합된 보완 안내를 검사했다. 전체 기준 설계와 기존 동작 시험도 통과한다.
  이후 정상 앱 재시작으로 반영했다. 14:26 동일 대화/요구로
  `2a7419a4-c1e3-4a66-acdc-e0cfb86a9730` 실행. 마지막 확인 14:29:33,
  planning_narrative 150초 fresh progress, ANALYZING. 이 run부터 재확인할 것.
  참조 보완의 실기 효과는 아직 미확인이다.
- 전체 목표 미완료. 이번 턴의 초기 부분은 특정 live run의 fresh progress를 확인한
  verified wait였고, terminal 확인 뒤 위 소스 변경을 수행했다. 원본/작업 사본/모델
  설정/실제 PLC 변경 없음. 다른 모델 연결 비교는 계속 사용자 답변 대기다.

## 2026-09-09 모의 구현 선택 지침과 진행 내역 실화면 수정

- narrative 지침의 contact polarity 금지를 physical contact polarity로 구분하고,
  내부 PC 모의 신호의 활성 상태는 사용자 지정이 없을 때 검토할 설계 제안임을 명시했다.
  native 지침도 요청 동작을 구현하는 start-release/running 메모리와 새 설비 기능을
  구분했다. 물리 I/O/안전 조건 생성이나 출력 역할 재분류를 허용하지 않는다.
  이것이 이전 실패의 단일 원인이었다는 증거는 없다. Agent 60 tests/Ruff 통과.
- 브라우저 정상 재시작으로 위 지침과 이전 MNE hold/STOP/복원 변경을 반영했다.
  14:10 동일 요청 `87d59ef8-d080-48be-8ca9-c5829dd4f105` 실행.
  14:14 이후 planning_native_design 진입 확인: 이번에는 narrative 반복 질문을 넘었다.
  최종 결과는 아직 미확인이며 현재 실행 중이다. 동일 run의 최신 상태부터 확인할 것.
- 실제 좁은 창에서 진행 내역을 열자 대화와 패널이 병렬로 찌그러지고 페이지가 다시
  스크롤되는 문제 재현. conversation-split으로 배치를 분리하고 일반 대화는 패널을
  열어도 viewport 높이를 유지한다. <=860px에서 패널이 대화 위에 열리고 대화는
  visibility:hidden으로 뒤쪽 초점 접근도 막는다. 닫기/Escape 후 원래 버튼 초점 복원.
  넓은 창에서는 좌우 배치, 디버그 문서 layout은 별도로 유지한다(실 넓은 창 미검증).
- run.state 이벤트의 detail에 runId를 그대로 넣던 부분을 빈 보조 내용으로 바꿨다.
  식별자는 내부 metadata/debug에 남는다. message.appended 내부 저장 알림은 일반
  진행 내역에서 제외하고 debug에는 유지한다. 내역 글씨 10/8px를 13/12px로 조정.
- Web typecheck/build/production entry 통과. 실제 browser reload 후 패널 열기,
  식별자/저장 알림 기본 숨김, 읽을 수 있는 글씨, Escape 닫기와 입력창/중단 버튼 복원
  확인. bundle index-R0g4prGa.js / index-CmvlAmDo.css.
- 새 파일 생성/저장/재열기/전체 다중 모드 XG-SIM은 미완료. 모델/키/연결/원본/작업
  사본/실제 PLC 변경 없음. 다른 연결 비교 질문에도 아직 답변이 없다.

## 2026-09-09 실행 결과와 기존 MNE 시나리오의 입력 유지 관측 연결

- `959c9622-87ec-4af2-837f-dac412e084bf`는 13:52:44 FAILED.
  planning_narrative / validator_rejected / planning_question_repeated.
  현재 active run 0. 입력 중복 제거 후에도 계획은 완성되지 않았다. 질문 삭제나
  status 강제 승격은 적용하지 않았고, 모델 재요청도 추가하지 않았다.
- 기존 compile_rung_patch_candidate → 공개 candidate → Host/API → Web 검토 →
  verify_mne_rung_patch_profile 경로에 선택적 `hold_ms`를 연결했다. 각 단계 0..10000ms,
  전체 30000ms 이하. 미지정 기존 profile의 canonical shape/digest는 바꾸지 않는다.
  hold 자체는 compiler/candidate/profile digest에 포함된다. Web은 초 단위로 설명한다.
- 최초 기대 출력 일치 이후 입력을 다시 쓰지 않고 같은 출력을 반복 관측한다. 유지 중
  불일치를 한 번 관측하면 실패하며, 나중에 일치해도 성공으로 복구하지 않는다.
  holdMs/holdObservedMs/holdSamples/observationKind를 반환하고 Host가 승인된 hold와
  기간·최소 2회 표본을 대조한다. 과거 endpoint의 단일 matched 결과로 새 hold를 통과 못함.
  이 방식은 polled steady state이며 짧은 pulse 부재나 엄밀한 시간 경계 증명이 아니다.
- compiler의 입력 byte 격리를 관측 OUT뿐 아니라 전체 프로그램 OUT/SET/RST의 P/M
  writer bytes까지 검사한다. MNE 서버 finally 복원은 STOP ok+verified일 때만 실행한다.
  STOP 불확실 시 RUN 중 입력 복원을 하지 않고 기존 실패/격리 경로로 남긴다.
- MCP 20 tests, Host 24 tests, focused Ruff 통과. 실제 compiler candidate를 Host
  MneRungPatchCandidateV6로 검증해 hold와 digest 보존 확인(no device I/O).
  Contracts/Web typecheck/build/production entry 통과, index-wrqqGeyq.js.
- Python 운영 프로세스는 이 변경 이후 아직 재시작하지 않았다. 실 XG-SIM hold/STOP
  실패 경계와 실제 검토 화면은 미검증이다. 다음 사용 전 정상 재시작으로 반영 필요.
  이 경로는 기존 단일 MNE rung 후보용이며 신규 전체 다중 모드 NativeSimulationRunner,
  D count 관측, 대동인덱스 파일 생성/저장/재열기/전체 XG-SIM 완료를 뜻하지 않는다.

## 2026-09-09 계획 입력 중복 제거 후 실사용 요청 확인 중

- narrative 요청은 동일 사용자 대화를 message_history와 user_clarifications JSON으로
  중복 전송했다. 추가 JSON 사본을 제거하고 원문 history/current prompt 및 Host brief를
  유지한다. 검증기의 user_clarifications 근거와 저장 원문은 변경하지 않는다.
- _planning_user_history는 연속한 동일 사용자 메시지만 마지막 timestamp로 합친다.
  A→B→A 변경 복귀, 공백을 포함한 서로 다른 원문은 유지한다. DB 기록은 그대로다.
  100-message 한도 전에 합쳐 반복 재요청이 초기 조건을 밀어내는 것도 방지한다.
- 실제 저장 대화 읽기 전용 측정: user 20→13 turns, history 3338→2470 chars,
  중복 clarification JSON 2788 chars 제거. 모델 거절의 원인이 입력 길이였다는 증거는
  없으며 이 수치는 입력 중복 제거 효과만 나타낸다. Agent 전체 60 tests/Ruff 통과.
- Computer Use 지침에 따라 브라우저 정상 재시작으로 반영. 13:49 동일 요구 실행
  `959c9622-87ec-4af2-837f-dac412e084bf`; 마지막 확인 13:50:16 신선한
  planning_narrative progress 60초, ANALYZING. 실행 결과부터 확인하고 관측 timeout만으로
  중단/재시작하지 않는다. 연결/모델/키/프로젝트 파일/실제 PLC 변경 없음.
- 생성·저장·재열기·전체 다중 모드 XG-SIM은 여전히 미완료. 다른 모델 연결 비교는
  사용자 답변 대기다. 진행 중 요청을 완료나 연결 복구 증거로 표시하지 않는다.

## 2026-09-09 혼합 설계 오류 보완 안내와 좁은 대화 화면 수정

- Agent retry feedback이 첫 diagnostic code만 보고 safety 보완 안내를 선택하는 결함을
  수정했다. network+safety 누락이 함께 있으면 두 종류의 보완 안내와 각각의 전체
  io_points index를 전달한다. 일반 오류 문장 8개 제한 뒤의 누락도 bounded index로
  포함하며 rejected input/context는 노출하지 않는다. 검증·승인 기준은 그대로다.
  test_native_output_correction 28 tests 및 focused Ruff 통과.
- 정상 앱 재시작 뒤 동일 요구로 13:35 `f5e74847-bd10-41d6-abe2-2749c5ecfc6e`
  요청. 13:38 FAILED, planning_narrative / model_api_error / provider_rejected.
  native 단계 전 실패여서 위 안내 수정의 실기 효과는 아직 확인하지 못했다.
  현재 실행 중인 모델 요청 없음. 다른 모델 연결 비교 질문에 답변도 아직 없음.
- 실제 좁은 브라우저에서 바깥 페이지와 대화가 이중 스크롤되고 상단 조작부가 밀리는
  문제 확인. 일반 대화 화면에만 viewport flex layout을 적용해 상단/입력창을 유지하고
  대화 본문만 스크롤한다. 펼친 설정은 높이 제한 안에서 읽을 수 있다. 최신 요청보다
  오래된 계획은 삭제하지 않고 요구사항·설정 안으로 옮겼다. live status 중복 문구는
  화면 낭독기용으로 유지한다. 진행 내역/디버그 열린 별도 layout은 이번 수정 범위 아님.
- Web typecheck/build/production entry 통과. 실 브라우저 새로고침 후 상단/입력창 표시,
  이전 계획 기본 숨김, 설정 펼침/접힘 접근 확인. 배포 bundle index-BLLezhzu.js,
  index-BU4bXDly.css. Computer Use 지침에 따라 브라우저 UI로 확인했다.
- 전체 다중 모드 XG-SIM runner는 이번에 구현하지 않았다. 새 파일 생성·저장·재열기·
  전체 XG-SIM도 미완료다. 원본/작업 사본/실제 PLC 변경 없으며 개발용 기준 설계를
  사용자 승인이나 실제 모델 출력으로 대체하지 않았다.

## 2026-09-09 전체 기준 설계 동작 시험과 reset/count 결함 수정

- `test_daedong_reference_behavior.py`에 입력만 바꿀 수 있는 개발용 scan evaluator와
  독립 기대값 기반 8개 시험을 추가했다. 즉시 rung 쓰기, 상호 배타적인 S-group step,
  100ms tick을 가정한다. 이 가정과 실제 컴파일 mnemonic의 동작 일치는 XG-SIM에서
  별도로 검증해야 한다. 이 코드는 실제 시뮬레이터 또는 runtime 대체품이 아니다.
- 범위: 자동 6회와 DONE 유지, 수동 START 유지/해제/재입력, 5초 미완료 latch,
  자동·수동 각각 E-stop/door latch 및 원인 해소/reset/new start, 운전 중 모드 변경과
  reset 차단, STOP 해제만으로 재가동 금지, 안전 latch 제거 변이의 기대값 실패.
- 추가 시험에서 운전 중 reset 버튼을 누르면 INCP의 reset-off 접점이 DONE 카운트를
  누락시켰다(0 != 1로 실패 재현). `reset_permitted` OUT을 실제 정지·시작 해제·원인 해소
  조건으로 만들고 MOV 0 / INCP에 그 비트의 반대 접점을 사용해 수정했다. 운전 중
  무효 reset은 완료 횟수를 막지 않는다. 기준 설계는 이제 23 rungs다.
- Agent 전체 57 tests / focused ruff / Test-Daedong-Reference.ps1 통과.
  실제 artifact → Host → MCP 컴파일 성공, 기존 multi-mode runner는 여전히 미지원으로
  거절한다. 운영 앱/사용자 계획 DB/프로젝트 파일은 변경하지 않았다.
- 다음: 실제 전체 모드 시나리오를 입력/관측 단계로 연결하고 STOP·입력 복원을 유지할 것.
  세밀한 reset 유지/원인 해소 순서 등 추가 경계와 vendor S/TON/INCP 관측이 남는다.
  현재 모델 재요청은 실행하지 않았으며, 다른 연결 비교 질문에도 아직 답변이 없다.

## 2026-09-09 전체 인덱서 기준 설계의 표현·컴파일 확인

- `f294a1d3-7e32-4376-bc32-a11eab2ebfc6`는 13:05 FAILED. native 단계에서
  io_points 10/13/14의 network 및 11/12/13/14의 safety coverage가 누락됐다.
  이번은 내부 오류나 SDK 서버 오류가 아니라 모델의 불완전한 실행 설계다.
  현재 실행 중인 요청은 없다. 같은 요청을 즉시 반복하지 않았다.
- 다른 모델 연결로 비교 검증해도 되는지 비동기 질문을 보냈으나 아직 답변 없음.
  연결/키/모델/전송 동의는 변경하지 않았다. 사용자 승인 없이 다른 연결로 보내지 말 것.
- `gidong-agent/tests/daedong_index_reference.py`는 전체 사용자 동작을 대상으로 하는
  개발용 기준 제안이다. 2-mode / 2-sequence / 3 alarms sharing one fault latch /
  5-second timer / INCP+MOV 0 / stopped-only mode selection and reset /
  released-start memory / latched run logic, 총 22 rungs. 모의 입력 M0000x,
  프로그램 상태 M0010x, 이동 출력 M00200을 분리한다. 실제 계획 DB에 삽입하거나
  사용자 승인/모델 생성/실기 증거로 둔갑시키지 않았다.
- `gidong-platform/scripts/Test-Daedong-Reference.ps1`로 실제 artifact serializer와
  Host schema를 거쳐 MCP mnemonic compiler까지 통과했다. 처음에는 raw domain dump를
  넘겨 timer durationMs 파생값이 없었으므로 정식 artifact 경로로 수정했다.
  정상 artifact 경로는 22-rung 전체 설계를 수용한다. 따라서 전체 동작이 형식상
  표현 불가능하다고 단정할 근거는 없다. ruff 통과.
- 다음은 기준 설계의 전체 동작을 독립 기대값으로 시험하고 full multi-mode XG-SIM
  runner를 연결하는 일이다. 아직 기준 설계의 scan-level 동작도 검증 전이며 native
  생성/저장/재열기/실제 XG-SIM은 미완료다. 기존 단일-mode runner는 이 설계를
  의도대로 거부한다. 이 거부를 성공한 simulation으로 표시하지 말 것.

## 2026-09-09 SDK 스트림 오류의 내부 오류 오분류 재현

- `9f9954c4-96f4-486a-b029-419a9326f628`는 12:46 FAILED,
  planning_narrative / runtime_invariant. 새 안전 로그는 `_consume_planning_stream`
  line 14398, kind Exception이었다. 설계 검증 전 스트림 소비 위치다. 실제 예외가
  아래 SDK APIError였다고 단정할 수는 없다.
- 설치된 openai/_streaming.py는 HTTP 200 이후 error SSE를 받으면 SDK APIError를
  직접 발생시킨다. 기존 `_agent_runtime_diagnostic`은 PydanticAI ModelAPIError만
  인식해 이를 local runtime_invariant로 오분류했다. httpx.MockTransport로 실제
  AsyncOpenAI → OpenAIChatModel → Agent.run → _consume_planning_stream을 통과하는
  오류 SSE를 재현했고 SDK APIError가 직접 전파됨을 확인했다. 네트워크/키 사용 없음.
- SDK APIError/APIConnectionError/APITimeoutError를 기존 안전 provider 분류에 연결했다.
  HTTP 상태는 실제 정수 400–599만 전달하며 provider body/code/message는 계속 비공개다.
  재시도/설정/검증 권한은 바꾸지 않았다. 공식 OpenAI 오류 문서를 대조했다:
  https://developers.openai.com/api/docs/guides/error-codes
  현재 호환 모델 서버의 실제 실패 원인 확정이나 복구 완료를 뜻하지 않는다.
- Agent 49 tests / focused ruff 통과. 정상 UI 재시작으로 반영 후 동일 문구로
  **12:53 `f294a1d3-7e32-4376-bc32-a11eab2ebfc6`** 실행 중.
  이 실행 결과부터 확인한다. 원본/작업 사본/실제 PLC 변경 없음.

## 2026-09-09 native 단계 내부 오류 위치 진단 보완

- `33141b86-30d1-405f-a47c-5dff1b7c8853`는 12:37 FAILED,
  stage=planning_native_design / runtime_error / runtime_invariant.
  이전 output safety coverage와 다른 오류다. Host 로그는 종류만 남겨 실제 코드
  발생 위치를 특정할 수 없었다. native 성공/파일 생성 증거 없음.
- Agent의 `_agent_runtime_diagnostic`은 runtime invariant일 때 runtime.py 내부 함수명,
  행 번호, 고정 허용 목록의 예외 종류만 별도 경고로 남긴다. Host stderr 전달은 날짜/
  logger/고정 문구/함수 식별자/양의 행 번호/허용 예외 종류를 fullmatch한 줄만 수용한다.
  예외 메시지, provider 응답, 경로, local variables는 전달하지 않는다. 사용자 화면에
  진단 문자열을 추가하지 않았다. Agent 48 / Host 21 tests / focused ruff 통과.
- 정상 앱 재시작으로 반영 후 동일 문구로 **12:43
  `9f9954c4-96f4-486a-b029-419a9326f628`** 실행 중, planning_narrative 확인.
  이 실행 결과부터 확인하고 진행 중 재시작/중복 요청하지 말 것. 같은 실사용 흐름으로
  재현해 이 오류 위치부터 확인할 것.
  진단을 보완했을 뿐 오류 자체가 수정됐다는 증거는 아직 없다.

## 2026-09-09 재실행이 실행 설계 단계로 진행

- 현재 `33141b86-30d1-405f-a47c-5dff1b7c8853`는 12:34 관측에서
  planning_native_design으로 진행했다. 12:35 UI 새로고침 후에도 같은 요청의 진행과
  중단 버튼을 복원했다. narrative 통과 증거이며 native 완료나 파일 생성 증거가 아니다.
- NewProjectPlanReviewPanel의 기본 검토 완료 표시에서 actorId를 제거하고 기존 접힌
  '계획 식별 정보'로 옮겼다. 기본 표시에는 '설계 검토 완료'와 시간만 남긴다.
  승인 항목/권한 조건은 변경하지 않았다. Web build/typecheck/production entry 통과,
  bundle `index-d7jo3OKr.js`. 실제 ready 검토 완료 화면은 아직 도달하지 못했다.
- 현재 요청의 terminal 결과부터 확인할 것. 앱 프로세스는 재시작하지 않았다.

## 2026-09-09 주소 질문의 출처 누락 우회 수정

- `3f121018-8e28-4c82-b155-2455db2f76a9`는 12:25 COMPLETED지만
  plan_status=needs_clarification이다. 내부 주소를 위임받았음을 문장에 인정하면서도
  주소 매핑표 확정을 다시 요구했다. requirementSources에서 hardware_and_io를 빼고
  나머지 4개만 반환해 기존 all-sources-resolved 검사를 피했다. native 생성 성공 아님.
- Agent `_has_delegated_internal_address_question`으로 이 구체적인 반복을 출처 개수와
  별개로 판정한다. 저장 brief의 CPU/내부 모의 사용/주소 제안 위임이 있고 질문이
  내부 주소 확정을 되묻는 경우 ModelRetry. 나중의 주소 위임 철회는 존중한다.
  실제 현재 brief에 대한 읽기 전용 판정 True 확인. 주소를 DB에 채우거나 다른 미해결
  사용자 결정을 ready로 바꾸지 않는다. FunctionModel 전체 runtime에서 반복 질문 거절
  → 진짜 미해결 복구 질문 수용 확인. Agent 47 tests / focused ruff 통과.
- MCP 입력 바이트 격리는 network OUT/알람뿐 아니라 모든 signal SET/RST writer에도
  적용한다. 내부 상태 비트를 입력으로 덮어쓰는 경우를 막고 별도 바이트는 허용하는
  full-constructor 회귀 포함 MCP 14 tests / ruff 통과.
- 정상 앱 재시작으로 두 소스 변경을 반영했다. 기존 계획 대화 복원 뒤 동일 문구로
  **12:30 `33141b86-30d1-405f-a47c-5dff1b7c8853`** 실행 중이며 12:31
  planning_narrative 15초 관측. 이 실행의 결과부터 확인하고 관측 지연 때문에
  재시작하거나 중복 요청하지 말 것. 다음 실제 요청의 결과가 아직 필요하다.
  자동·수동 전체 시나리오, 별도 파일 생성/저장/재열기/XG-SIM은 계속 미완료다.

## 2026-09-09 실행 설계의 안전 조건 누락 확인 · 횟수 관측 구현

- 12:03 `a044550b-49d9-426a-9e7c-72563cc772f1`는 narrative 통과 후
  planning_native_design에서 12:16 FAILED. 이번 원인은 HTTP 오류가 아닌
  native_output_safety_coverage: io_points 10, 13의 명시적 안전 경계 누락이다.
  실제 UI에서도 실패와 후속 입력 복귀를 확인했다. 생성/저장 성공이 아니다.
- Agent 수정: 안전 누락 재시도 피드백에 각 누락 index의 signal_id를 해당
  safety_rules.affected_output_signal_ids에 포함하도록 구체화했다. 출력 삭제/
  internal_bit 재분류/안전 장치 날조로 우회하지 않도록 명시한다. 내부 모의 입력과
  프로그램이 쓰는 출력/래치는 바이트 쓰기 충돌을 피하도록 별도 M word 배치를 안내한다.
  원문 요구와 안전 검사는 유지한다. 이 변경의 실제 모델 성공은 아직 미검증.
- NativeSimulationRunner: 직접 모의 입력만으로 조건을 구성할 수 있는 INCP에 대해
  독립적인 idle/pulse/reset 상태를 결정하고 6회 증가, 유지 중 추가 증가 없음,
  6→0 reset을 실제 읽기로 관측하는 경로를 구현했다. 데이터 레지스터를 직접 써서
  결과를 만들지 않는다. reset MOV는 초기 0에 대입해 성공으로 보지 않고 카운트 후 확인한다.
  두 writer만 허용하고 겹치는 reset/증가, 독립 idle 없음, 계산된 step/timer 접점,
  12개 초과 입력은 여전히 not_verified. 전체 자동/수동 상태기 지원을 뜻하지 않는다.
  MCP 13 tests / ruff / Agent→Host→MCP 메모리 내 컴파일 파이프라인 통과.
  회귀 시험은 모의 bridge 대체이며 실제 XG-SIM 실행 증거가 아니다.
- Agent 45 tests / focused ruff 통과 후 정상 UI의 앱 다시 시작으로 반영했다.
  기존 계획 대화 복원 후 동일 요구로 **12:19 `3f121018-8e28-4c82-b155-2455db2f76a9`**
  실행 중. 12:20 관측 planning_narrative. 이 실행 결과부터 확인하고 진행 중에는
  재시작/중복 요청하지 말 것. 원본/작업 사본/실제 PLC 변경 없음.


## 2026-09-09 새 대화/계획 복원 실사용 확인 · 현재 요청

- 11:49 `533cc555-98fe-4f6c-a191-4170690071da`는 narrative에서 HTTP 502로 실패했다.
  그러므로 큰 native schema만의 문제라고 확정하지 않는다. native 502 fallback의 실제
  성공도 아직 없다. 다만 전체 runtime을 통과하는 FunctionModel 테스트에서 첫 native
  502 → prompted 전달 → 동일 검증 → ready 제안 반환을 확인했다. Agent 45 / ruff 통과.
- 모델 설정창은 열어 점검 버튼 상태만 확인하고 저장/전송 동의 변경 없이 닫았다.
  기본 응답은 정상 앱의 별도 일반 대화로 시험했다. conversation
  `3f38122e-4567-4fcd-bedd-5456ee4dfd1e`, run `3387bb6a-5d6e-4fc2-bde0-be6f803b9643`
  11:59:18 시작 → 11:59:59 COMPLETED, 짧은 인사 응답. PLC/파일 도구 사용 없는 대화다.
  모델 전체가 불통이라는 증거는 없고 긴 계획 요청의 문제가 남는다.
- 상단 '새 대화' 버튼 추가: 기존 기록 보존, targetScope=none, 실행/전송 중 비활성화.
  첫 실사용에서 startFreshConversation의 targetlessAnalysisOpen 동기화 누락으로 입력이
  막힌 것을 발견해 실제 생성된 conversation 기준으로 상태를 맞췄다. 좁은 화면에서는
  브랜드 문구 대신 로고를 유지해 새 대화/기록/설정 버튼이 들어가도록 했다.
- 새로고침 없는 새 대화 진입/입력, 실행 중 버튼 비활성화, 기존 계획 DB 보존과 작업 기록을
  통한 양방향 복원을 실제 UI에서 확인했다. 계획 복원 시 잘못 표시되던 '읽기 전용' 문구도
  planningSession을 별도로 판별해 수정했다. 다른 프로젝트의 실제 조회 전용 제한은 유지한다.
  Web build/typecheck/entry와 composer/권한/evidence 검사 통과. bundle `index-DX0aiiBf.js`.
- 원래 계획 conversation `4328c29c-a4a4-420b-8ed5-b53a834f49a0`로 복원 후 동일 문구로
  재요청했다. **현재 run `a044550b-49d9-426a-9e7c-72563cc772f1`**, 12:03 시작,
  planning_narrative 진행 중. 이 요청 결과부터 확인하며 관측 타임아웃만으로 재시작하지 말 것.
  같은 원문 재요청이라 기본 폼을 채우거나 요구조건을 축소하지 않았다. 파일 생성/저장/
  재열기/자동·수동 전체 XG-SIM 성공은 아직 없다.

## 2026-09-09 정상 대화가 실행 설계 단계까지 진입

- 11:39 `7eab5fd7-4ba7-48fc-92d3-826d0b9c214d`의 narrative는 통과했다.
  DB agent.progress에서 11:43:29 planning_native_design 진입, 11:44:08 FAILED를
  확인했다. 실패는 agent_runtime_provider_http_502 / stage=planning_native_design다.
  따라서 반복 질문/요구사항 정리와 실행 설계 실패를 구분할 것. 파일 생성 성공은 아니다.
- 실행 설계 요청은 약 40초 뒤 서버 502였다. 큰 constrained schema의 서버 처리 문제일
  가능성을 시험하기 위해 Nemotron native 요청의 502에만 PromptedOutput 한 번 재시도를
  추가했다. 동일 모델/endpoint/brief를 사용하며 `_make_native_planning_agent`가 두
  전송 방식에 같은 타입, 지시, 설정, capability를 부여한다. fallback에도 기존
  validate_native_design output validator를 다시 등록한다. 2차 실패는 전파하며 다른
  HTTP 오류/다른 모델에는 이 fallback을 적용하지 않는다. 원인 확정/실기 성공 아님.
- Agent 44 tests와 focused ruff 통과. native/prompted 양쪽에서 빈 설계를 거절함을
  실제 factory로 확인했다. 기존 스키마 검사나 승인 권한을 완화하지 않았다.
  정상 앱 UI 재시작으로 반영 중. 다음에는 같은 사용자 요청으로 이어갈 것.

## 2026-09-09 계획 응답 전송과 잘림 복구

- `596c7d5e-68fb-436f-aa57-785348cb6832`는 11:25 모델 서버 HTTP 502로 실패했다.
  기존 파일 변경 없음. 질문 모순 수정 후 ready/native 성공 증거는 아직 없다.
- 긴 non-stream 응답을 기다리는 경로를 변경했다. 설치된 PydanticAI Agent.run의
  event_stream_handler는 model request를 스트리밍하면서 기존 graph/validator/retry를
  유지한다는 로컬 구현을 확인했다. `_consume_planning_stream`으로 narrative 및
  native 첫 실행/길이 초과 재시도 모두 연결했다. 부분 JSON/Thinking 이벤트를 사용자
  화면이나 로그로 내보내지 않고 최종 검증된 plan만 공개한다.
- `_run_narrative_with_truncation_retry`는 UnexpectedModelBehavior일 때 마지막
  실제 ModelResponse.finish_reason이 length인 경우만 10000-token 예산으로 한 번
  재시도한다. stop/기타 invalid JSON은 이 복구로 재시도하지 않으며 두 번째 실패는
  전파한다. 첫 실패의 토큰 사용량도 합산한다. 앞선 root json_invalid가 실제 잘림인지
  확정한 것은 아니다. 일반 HTTP 오류도 이 복구로 무제한 재시도하지 않는다.
- Agent 42 tests 및 focused ruff 통과. 스트리밍에서도 semantic validator의 첫 응답
  거절 → 수정 재시도가 실제 작동하는 FunctionModel 테스트 포함. 실제 외부 모델은
  아직 미검증이며 정상 앱 재시작으로 반영 중. 다음 요청 결과부터 이어갈 것.
- 실제 반영 후 11:28 `f9b98651-b450-463d-a319-f33598ede21d`는 11:32
  planning_narrative / validator_rejected / structured_output_contract로 실패했다.
  502가 아닌 출력 검사 실패이며 native 단계로 넘어간 증거는 없다.
- 새로운 스트리밍 취소 테스트는 부분 JSON 수신 중 취소가 stream을 닫고 재시도하지
  않음을 확인했다. 전체 Agent 43 통과. 기존 파일/PLC를 사용하는 테스트가 아니다.
- NEW_PROJECT_PLANNING_INSTRUCTIONS를 짧은 단일 지침으로 재작성했다. 중복된
  '모르는 항목' 및 주소 차단 강조 대신 사용자 결정(환경/역할/행동/시간/안전/완료 기준)과
  구현 제안(위임된 내부 주소/명령 배치/시험 절차)을 분리했다. 원문 출처/ready coverage/
  native human review/실행 권한 검사는 유지한다. 질문 모순 오류는 이제 고정 safe code
  planning_question_repeated로 구별한다. Agent/Host allowlist만 확장, 원문 로그 없음.
  정상 UI 재시작으로 이 후속 수정까지 반영했다. 현재 11:39
  `7eab5fd7-4ba7-48fc-92d3-826d0b9c214d` ANALYZING이며 같은 conversation
  `4328c29c-a4a4-420b-8ed5-b53a834f49a0`이다. 이 요청 결과부터 이어갈 것.
  원문 출처, 수정된 짧은 지침, 스트리밍, 1회 길이 복구, 질문 모순 판정이 모두 반영됐다.
- 이후 소스에만 추가한 표시 문구 정리: 완성 폼용 deterministic narrative의
  'Host/typed native/collection/null' 설명을 사용자용 입출력·제어 로직 설명으로 바꿨다.
  현재 대화는 이 shortcut을 쓰지 않으므로 현재 실행 때문에 재시작하지 말 것.

## 2026-09-09 후속 답변의 권위 전달 누락 수정 · 기본 화면 정리

- `ff11ee96-a48d-4c1e-8189-f28ee3d60c62`는 질문 반환으로만 완료했다. 빈 폼에서
  서버가 하드웨어/운전/인수 기준 질문 3개를 추가하는 결함을 수정했다. 명시적 모델
  질문이 있으면 이 일반 질문을 추가하지 않으며 ready coverage 검사는 유지한다.
- 10:57 `268585c1-8650-4890-83d7-7355111001d5`도 11:02 주소를 다시 묻고 종료했다.
  주소 위임 지시와 source schema 설명만 보완한 것으로는 실제 문제가 해결되지 않았다.
- 추가 확인: `_trusted_context_instructions`의 현재 Host brief allowlist에 저장된
  `supplemental_requirements`가 빠져 있었다. 이를 포함하고 후속 답변의 순서/명시적
  정정 및 이전 모델 질문과 사용자 요구의 차이를 명시했다. 저장 brief를 임의로 채우거나
  주소/실행 권한을 조작하지 않았다. 회귀 포함 Agent 38 tests 및 focused ruff 통과.
- 정상 UI 재시작 후 같은 대화에서 11:04 `45b4779e-7358-488b-b7e9-ea53a28c41fe`
  실행도 11:07 기존 주소 질문을 그대로 반복했다. 권위 목록 누락 수정만으로는 실기 해결이
  아니다. 과거 assistant 질문/실패를 신규 narrative의 few-shot 응답으로 다시 전달하는
  경로도 분리했다. `_planning_user_history`는 USER 입력만 순서대로 유지한다. 일반
  engineering 대화는 기존 전체 이력을 유지하며 단순 동의를 확정 출처로 승격하지 않는다.
  회귀 포함 Agent 39 tests와 focused ruff 통과.
- 다시 정상 UI 재시작 후 11:09 `58757daa-420d-4389-bc8a-0e3bf90028c1`는 11:17 실패.
  safe diagnostic은 planning_narrative / schema_validation / root json_invalid다.
  토큰 잘림인지 여부는 현재 진단만으로 단정하지 않는다. 이력 분리의 실제 성공 증거 없음.
  사용자가 모르는 주소를 대신 폼에 채우지 않고 내부 주소안을 제안해 달라는 일반 대화로
  이어갔다.
- DB의 최종 artifact를 읽어 11:02와 11:07 응답은 5개 requirementSources를 전부
  해결됐다고 표시하면서도 주소 질문을 반복했음을 확인했다. `_planning_sources_resolve_narrative`
  검사로 같은 기존 완성 brief 모순 검사에 연결했다. 메모리에서만 source coverage를
  확인하며 persisted brief를 덮어쓰지 않는다. 모델은 정말 미답인 경우 해당 source를
  바로잡아 질문할 수 있고 자동 ready 승격은 하지 않는다. Agent 40 / Host 20 / MCP 7 통과.
  이 검사까지 정상 UI 재시작으로 반영했다. 11:20 현재 run
  `596c7d5e-68fb-436f-aa57-785348cb6832` ANALYZING. 같은 대화에서 주소를 직접
  입력하지 않고 저장된 요구사항으로 진행해 달라고 요청했다. 이 실행부터 확인하며
  관측 타임아웃만으로 재시작/중복 제출하지 말 것.
- 신규 계획의 큰 안내/설정 영역은 프로젝트 이름 + '요구사항 · 설정' summary로
  기본 접힘 처리했다. 브라우저 실제 펼침/닫힘과 실행 중 새로고침 복원을 확인했다.
  내부 단계/기존 설정 조작은 삭제하지 않았다. Web build/typecheck/entry 및 기존
  composer/권한/evidence/대화 권한 검사 통과. 이후 completed 표시를 '답변 완료'로
  바꾸고 재빌드/새로고침했다. bundle `index-Bi1nfmOu.js`. 파일 생성 완료 의미가 아니다.
- 프로젝트 생성/저장/재열기/전체 XG-SIM 성공은 아직 없다. 아래 INCP preflight 차단은
  실제 카운터 시험 구현이 아니다. 기존 runner는 모드/타이머/시퀀스/알람/복구 각 1개,
  2-step release-required 시퀀스만 지원하며 자동 continuous profile을 거절한다.
  이 제한을 단순 삭제하거나 수동 단일 예제를 자동·수동 6분할 성공으로 보고하지 말 것.

## 2026-09-09 첫 정상 질문 반환 — 주소 위임 처리와 시뮬레이션 공백

- 실제 run `82f595b4-26a4-4196-acc9-fec0c5578863`은 COMPLETED로 종료했다.
  이는 native 생성 성공이 아니라 needs_clarification 답변 성공이다. 실제 화면에서
  6분할/자동·수동/5초/정지·리셋/원본 보존을 정확히 요약했지만, 이미 위임한 M/T/D
  주소 번호를 사용자에게 확정하라고 다시 물었다. 기본 폼을 대신 채우지 않았다.
- NEW_PROJECT_PLANNING_INSTRUCTIONS 상단의 '미정 주소는 blocker'와 하단의
  '주소는 후속 구현 단계' 지시가 충돌했다. 실제 배선 매핑과 내부 모의 주소 배정을
  구분해, CPU와 모의 역할이 확인되면 내부 번호 누락만으로 질문하지 않게 수정했다.
  narrative에는 역할 이름을 유지하고 후속 native pass가 검토할 주소안을 작성한다.
  실제 배선/물리 I/O/기종/안전 동작을 임의로 확정하는 권한은 추가하지 않았다.
- 중요한 미완료 기능: NativeSimulationRunner는 MOV만 검증하고 INCP를 무시했다.
  증가 rung을 preflight에서 not_verified로 명시하고 verify_scenarios도 전체 preflight가
  supported일 때만 진입하도록 막았다. 기존 기능을 확인하지 않고 전체 시뮬레이션이
  통과한 것처럼 보고하는 경로를 차단한 것이지 카운터 시뮬레이션 구현 완료가 아니다.
  INCP의 실제 펄스 1회/입력 유지/반복 증가/리셋 관측, 특히 sequence step과 결합된
  완료 횟수 관측 구현이 남는다. 단순 입력 전용 예제를 전체 대동인덱스 검증으로 대체하지 말 것.
- Agent 36개 / MCP 7개 tests 통과, focused ruff 통과. 새 시뮬레이션 테스트는 bridge나
  장치 접근 없이 preflight 누락을 거절하는 테스트이며 실기/시뮬레이터 성공 증거가 아니다.
  정상 앱 UI 재시작으로 두 수정을 반영 중이다. 다음은 주소를 직접 제공하지 않고
  '모의 주소는 제안해 달라'는 정상 사용자 답변으로 이어갈 것.

## 2026-09-09 원문 선택형 출처와 증가/초기화 충돌 검사

- 실제 run `8943a709-9d5f-438a-b0e0-dad456c20260`은 planning_narrative의
  requirement_source_invalid로 실패했다. 새 진단이 원문 출처 문제를 확인했으며
  동일 오류를 허용하도록 검사를 완화하지 않았다.
- Nemotron narrative NativeOutput은 이제 현재 Host brief에서 추출한 실제 문장을
  공유 enum으로 제공한다. requirement_sources는 항목별 nullable 선택 객체로 받으므로
  모델이 원문을 다시 작성하거나 동일 필드를 여러 번 제출할 필요가 없다. 응답 validator가
  기존 field/quote 목록으로 변환한 뒤 종전 authority/readiness 검사를 그대로 적용한다.
  원래 저장 brief 필드는 변경하지 않는다. 다른 모델의 ToolOutput 계약도 유지한다.
  과거 원문/임의 paraphrase/알 수 없는 source 필드는 거절하는 회귀 테스트를 추가했다.
- 루트 Annotated ValidateAs는 PydanticAI에서 response wrapper를 만들기 때문에 사용하지
  않는다. NativeOutput에 provider BaseModel을 직접 넘기고 output validator에서 정규화한다.
  FunctionModel 회귀에서 실제 factory의 schema 검증과 정규화까지 확인했다.
- INCP와 MOV 0 reset은 같은 접점에 대해 반대 상태를 반드시 요구해야 한다. AND에서는
  보장 접점을 합치고 OR에서는 모든 분기의 공통 접점만 인정한다. Agent 및 MCP가 독립
  검사하며, 같은 조건의 증가/초기화와 OR 우회 조건을 거절한다. 일반적인 논리 동치
  전체를 판정하는 SAT solver가 아니라 명시적인 상호 배제 조건을 요구하는 보수적 검사다.
- Agent 35개 / MCP 6개 tests 통과, focused ruff 통과. 교차 검사 스크립트에 MCP 단독으로
  초기화 누락/비영 초기화/동시 성립/OR 우회/물리 주소를 거절하는 테스트도 포함했고 통과했다.
  native XG5000 실행/저장/재열기/시뮬레이션 증거는 아직 없다. 유한 횟수 범위의 동작도
  실제 검증이 남는다. 정상 앱 UI 재시작으로 반영 중이며 다음 실제 모델 결과를 확인해야 한다.
- 정상 UI 재시작 후 10:24 실제 run `4c0a3b5e-e1e2-4eb6-a211-dd3f7199f2c7`을 실행했다.
  약 5분 뒤 planning_narrative / invented_controller로 실패했다. 원문 선택 오류가 아닌
  다음 검사에서 실패한 것이며 실제 계획/생성 성공은 아니다.
- 추가로 재현한 검사 결함: 사용자 XGK/XGB/XGI 선택에서 해당 제조사 LS Electric을
  덧붙이는 것도 미정 기종으로 거절했다. 명시된 XG 계열 → 제조사 이름만 단방향으로
  허용한다. LS만 지정해서 XGK를 선택하거나 XGK를 XGB로 바꾸는 것은 여전히 거절한다.
  미지원 controller 이름은 고정 allowlist 이름으로만 모델 수정 피드백에 명시한다.
  실제 실패가 LS 이름 때문이었는지는 단정하지 않는다. 회귀 포함 Agent 36개 통과.
- 10:26 원본 파일 SHA256을 다시 읽어 기존
  BEE3B1A6E69469C0E1384CB8D9D758FEF2F95A92F0CC7AC6486DEEA941A9A68F와 일치 확인했다.
  working 사본은 이번 절에서 재해시하지 않았다.
- 제조사 판정 보완까지 정상 UI 재시작으로 반영하고 10:34 실제 run
  `82f595b4-26a4-4196-acc9-fec0c5578863`을 시작했다. 현재 실행은 이 run이며 같은
  conversation `4328c29c-a4a4-420b-8ed5-b53a834f49a0`이다. 다음에는 이 결과를 먼저
  확인하며 동일 요청을 중복 제출하거나 기존 실행을 관측 시간 초과만으로 재시작하지 않는다.

## 2026-09-09 횟수 증가 생성 경로 구현 — 실제 XG5000 확인은 미완료

- `17ff1227-8e93-4dd3-a3af-d67cca15e24a`는 planning_narrative의 validator_rejected /
  structured_output_contract로 실패했다. NativeOutput 변경 뒤 JSON 해석 오류가 아닌
  내용 검사에서 거절된 것이며 실제 계획 성공이 아니다. 요구사항 source 누락/잘못된 인용,
  기본 brief 미확정, narrative 누락/질문 누락을 safe code로 구분하도록 Agent와 Host
  allowlist를 확장했다. 원문이나 내부 추론은 로그에 추가하지 않았다.
- 사용자 요구인 완료 횟수를 문구로만 남기지 않도록 typed `INCP destinationAddress`
  동작을 Agent provider/domain → Host 계약 → TS 검토 화면 → MCP tool schema →
  독립 native compiler → MNE 파서 및 build receipt의 rung 집계까지 연결했다.
  내부 D+5자리 주소만 허용하며 INC 및 물리 P 출력 증가는 허용하지 않는다.
  Agent/domain과 MCP 양쪽에서 레지스터당 증가 writer 1개 및 정확히 하나의 MOV 0
  reset writer를 요구한다. UI에는 '횟수 +1 · 조건이 켜질 때 한 번'으로 표시한다.
- 명령 근거: LS ELECTRIC XGK/XGB Instruction Manual 4.16.1 INC/INCP.
  https://www.ls-electric.com/upload/customer/download/c4090ad3-f6a5-424a-a5fd-ef46a70eecd8/XGK_XGB_Instructions_Manual_Eng_V2.2.pdf
  이 구현은 signed 16-bit 범위의 증가이며 무한 횟수를 보장하지 않는다. 모델 지시에
  상승 조건/명시 reset/유한 범위 설명을 추가했다. 오버플로 정책과 증분·리셋 조건의
  동시 성립, 실제 스캔/재시작 시 동작은 별도 검토/실행 검증이 남는다.
- Agent 33개 / Host 20개 / MCP 5개 tests 통과. `scripts/Test-Increment-Pipeline.ps1`
  교차 검사도 통과: Agent의 typed fixture를 Host가 검증/직렬화하고 MCP가 실제
  `INCP D00100`와 `MOV 0 D00100`을 포함한 4-rung IL로 컴파일했다. model/PLC/UI 호출이나
  프로젝트 파일 쓰기가 없는 검사다. 같은 D의 중복 증가/초기화 누락/비영 초기화도 거절한다.
- TS contracts 빌드 후 웹 build/typecheck/entry 통과 (`index-NJiEmi8i.js`).
  contracts는 web/node_modules에서 junction으로 연결되지만 dist 선언을 사용하므로,
  shared TS 변경 시 packages/typescript/controlstudio-contracts에서 먼저 npm run build 필요.
- 실제 XG5000에 INCP를 입력/저장/재읽기/시뮬레이션한 것은 아니다. 기존 대동인덱스 원본,
  작업 사본 및 실제 PLC는 변경하지 않았다. imported COM identity 제약도 우회하지 않았다.
  정상 앱 UI 재시작으로 반영 후 10:12 실제 후속 run
  `8943a709-9d5f-438a-b0e0-dad456c20260`을 시작했다. 이 실행 결과부터 이어서 확인할 것.
  같은 conversation `4328c29c-a4a4-420b-8ed5-b53a834f49a0`이며 빈 폼을 대신 채우지 않았다.

## 2026-09-09 대화 요구사항 전달 2단계 — 출처 연결 및 새 실사용 진행

- 아래 1단계의 미완료 source coverage를 연결했다. Agent narrative의 requirement_sources는
  항목별로 Host에 저장된 사용자 원문을 정확히 인용해야 한다. 누락 필드, 중복 항목,
  만들어 낸 인용, 미확정 문장, 단순 동의는 ready 근거가 되지 않는다. 원래 폼은 그대로
  두고 native binding, authority 검사, artifact의 open decisions까지 같은 출처를 사용한다.
  인용을 항목으로 분류한 것은 계획 검토 대상이지 의미적 정확성/실행 승인의 증명이 아니다.
- Host도 출처를 독립 검증하며 새 출처는 plan digest에 포함된다. 비어 있는 새 필드는
  직렬화에서 제외해 기존 artifact digest 호환성을 유지한다. legacy source 계약이 없는
  기존 계획에 새 coverage 검사를 소급 적용하지 않지만 기존 scope 검사는 유지한다.
- Agent 29개 / Host 20개 tests 통과. Agent에서 생성한 테스트 artifact를 Host typed model과
  scope validator에 실제 전달하는 교차 검사도 통과했다. 이 fixture는 단순 모의 출력으로,
  실제 모델의 6분할 인덱스 설계/파일 생성 성공 증거가 아니다.
- 정상 앱 UI 재시작으로 1/2단계 Host와 Agent를 반영했다. 09:42 기본 입력 3개만으로
  '대동인덱스 대화형 모의 프로젝트' 신규 대화를 시작했다. 실제 run
  `79f943b4-e20e-4b43-af52-b3ba64561981`은 09:48 planning_narrative의
  invented_controller 진단으로 실패했다. 첫 요청 성공이 아니다.
  확인 과정에서 별도 재현한 결함: needs_clarification의 open_questions에 든 기종 예시도
  확정 하드웨어로 판정했다. 하드웨어 literal 검사에서 해당 질문 필드만 제외했다.
  다른 필드(본문/가정/제안) 검사, 전체 knowledge 출처 검사, ready/source 검사는 유지한다.
  회귀 테스트 포함 Agent 30개 통과. 이 실제 실패가 질문 예시 때문인지는 단정하지 않았다.
  정상 UI 재시작으로 질문 판정 수정까지 반영 후 09:50에 CPU/5초/자동·수동/리셋 조건을
  폼이 아닌 채팅으로 전달했다. 실제 후속 run `33bbdcc5-dc8d-438e-9bde-84e47bae7074`,
  conversation `4328c29c-a4a4-420b-8ed5-b53a834f49a0`이 진행 중이다.
  DB 읽기 확인: hardware_and_io/operating_modes/performance_and_acceptance/sequence는
  여전히 빈 값이며 supplemental_requirements에 사용자 원문 437자가 저장됐다.
  다음에는 이 실행 결과를 먼저 확인한다. 중복 요청 또는 새 초기 폼 입력으로 재시작하지 않는다.
- 위 후속 run은 09:55 planning_narrative / schema_validation / root json_invalid로
  실패했다. 저장 원문 전달 성공과 실제 계획 성공은 구분할 것. Nemotron의 narrative만
  PromptedOutput이 남아 있어 NativeOutput으로 변경했다. 다른 모델 ToolOutput과 기존
  authority/readiness 검사는 유지한다. 실제 production factory로 native output mode 및
  blocking question 응답을 확인하는 FunctionModel 회귀 포함 Agent 31개 통과.
  이는 실제 모델 성공을 의미하지 않는다. 정상 앱 재시작으로 반영 후 09:58 저장된 조건으로
  실제 run `17ff1227-8e93-4dd3-a3af-d67cca15e24a`를 시작했다. 이 실행이 현재 진행 중이다.
  다음은 이 run 결과 확인을 우선하며 동일 요청을 중복 제출하지 않는다.
- 별도 기능 공백: 현재 native rung action은 OUT/SET/RST/TON/MOV 상수 중심이다.
  요구한 완료 횟수 증가를 직접 표현하는 counter action/domain/generator 경로는 없다.
  읽기 ladder_graph가 CTR/INC를 해석한다고 새 프로그램 생성이 지원되는 것은 아니다.
  횟수 요구를 문구로만 남기거나 제거해서 완료 처리하지 말 것.
- 후속 실행 중 UI의 전체 단계 안내를 기본 접힘으로 바꾸고, 미완성 요구사항 대화를
  무조건 2단계 계획 검토로 표시하던 것을 수정했다. 최신 ready artifact가 있고 그 뒤
  새 요청이 없을 때만 계획 검토로 표시한다. 전체 4단계는 펼쳐볼 수 있다.
  웹 build/typecheck/entry 통과(`index-CkjswxdV.js`), 실제 400px 브라우저에서 접힘/펼침과
  진행 시간 갱신을 확인했다. Host 재시작 없이 reload해서 현재 실행을 유지했다.
- 실사용 중 진행 시간이 첫 0ms에 고정되는 UI 버그를 발견했다. coalescing이 이전 detail을
  우선해서 발생한 것으로, progress는 최신 detail을 쓰고 도구 활동은 기존 보존 규칙을 유지한다.
  1초 미만의 ms 표시는 숨겼다. 회귀 test-composer-action 및 build/typecheck 통과.
  웹 `index-DRPpz7g0.js`를 reload해 실행 유지 및 2분 30초 표시를 확인했다.
- 아직 실제 새 파일 생성/저장/재열기/XG-SIM, 기존 대동인덱스 수정 완료가 아니다.
  imported COM identity 제약을 우회하지 않았고 원본/작업 사본을 변경하지 않았다.

## 2026-09-09 대화 요구사항 전달 1단계 — 원문 저장 및 기존 폼 지름길 수정

- `ProjectBriefV1.supplemental_requirements`(웹 supplementalRequirements)를 추가했다.
  planning의 일반 USER 후속 요청만 메시지/run과 동일 트랜잭션에서 원문 그대로 누적한다.
  중복 operation, 실행 중 거절, SYSTEM 요청, structured form 요청은 중복 누적하지 않는다.
  기본 폼의 빈 필드에 답변을 임의로 복제하지 않는다. 20000자 초과는 조용히 자르거나
  일부만 저장하지 않고 전체 제출을 롤백한다. 웹 요구사항 편집에서 추가 내용을 펼쳐
  확인/정리할 수 있고 폼 재제출 때도 유지된다.
- 새 필드가 비어 있을 때 calculate_project_brief_digest는 해당 키를 제외해 기존 승인
  해시를 유지한다. 실제 추가 답변이 생기면 해시가 바뀌어 이전 계획을 그대로 승인할 수 없다.
- Agent native 입력은 Host의 저장 brief 전체이므로 추가 답변도 전달된다. Agent와 Host의
  collection scope 근거 허용 범위에 이 실제 원문을 포함했다. 임의 paraphrase는 허용하지
  않는다. 추가 답변이 있으면 `_complete_narrative_from_brief` 자동 확정 지름길과
  '폼이 완성됐으니 질문 금지' 재시도 강제를 적용하지 않는다. 최신 답변을 해석해야 한다.
- 미완료: native binding은 여전히 기본 폼 전체가 채워져야 진행된다. 다음 단계에서
  누락 필드별로 실제 저장 사용자 원문의 인용을 검증하는 요구사항 coverage를 연결해야 한다.
  단순히 complete 검사를 제거하거나 같은 답변을 모든 빈 필드에 넣는 방식은 사용하지 말 것.
  이 1단계만으로 최초 빈 폼→대화 답변→파일 생성이 되는 것은 아니다.
- Host 새 회귀 7개 포함 전체 17개 통과. Agent 전체 27개 통과. 두 저장소 focused ruff
  통과. 웹의 추가 요구사항 편집까지 포함한 최종 build/typecheck/entry 확인도 통과했다
  (`index-epPz8cxs.js`).
  소스 변경만 했으며 이 단계의 Host/Agent를 정상 재시작으로 실제 앱에 반영하지 않았다.
  실제 모델 요청/대동인덱스 파일 변경은 이 단계에서 하지 않았다. 기존 모델 실사용 실패,
  imported 수정 연결 제약, 처음부터 생성 미완료 상태는 아래 절 그대로 유지된다.
- 다음 단계 주의: `_bind_native_design_scope_to_project_brief`만 연결해서는 부족하다.
  `derive_new_project_plan_artifact`도 빈 `_CORE_BRIEF_DECISIONS` 필드마다 차단 질문을
  다시 생성하므로, 저장된 사용자 원문의 인용으로 해결된 항목을 이 단계에도 전달해야 한다.
  제안 구현은 missing field별 exact source quote를 narrative에서 제출받아 Agent/Host가
  실제 저장 brief/supplemental 원문과 대조하는 방식이다. 그 출처는 plan digest에 포함하고,
  이전 artifact에서 새 출처 필드가 비어 있으면 기존 digest를 유지해야 한다.

## 2026-09-09 자동 진행 후속 — 출력 누락 위치 수정, 실제 생성 비교 진행 중

- Agent provider 설계에 출력 coverage의 위치 진단을 추가했다. 누락된 출력마다
  `io_points.<index>.signal_id`를 수정 응답으로 전달한다. 입력 원문은 오류에 넣지 않으며,
  출력 역할 변경/인터록 자동 보충/기존 domain 검사 완화는 하지 않는다.
- 재현 시험에서 지시와 domain의 불일치를 확인했다. 지시는 M 모의 출력에서 안전 규칙을
  생략할 수 있는 것으로 읽혔지만 domain은 모든 digital_output 및 writable P에 안전
  규칙을 요구한다. 생성 지시/스키마 설명을 기존 domain 규칙과 일치시켰다.
- `tests/test_native_output_correction.py` 4개 추가: 누락 위치, 다중 누락/입력 불변,
  N/A 내부 비트와 출력 구분, NativeOutput의 잘못된 첫 응답→수정 응답→domain 통과.
  이 마지막 테스트는 FunctionModel 회귀 시험이며 실제 모델/파일 생성 증거가 아니다.
  Agent 전체 tests 26개 및 focused ruff 통과.
- 실제 run `ef1d576a-4066-4330-843b-0e088dcd7538`(08:36→08:40)은 실패했다.
  수정된 진단이 출력 index 10의 network와 safety 누락, index 11~13의 safety 누락을
  드러냈지만 모델이 수정 응답에서도 해결하지 못했다. 파일 생성 성공으로 보지 말 것.
- Native 설계 단계의 Nemotron-3-Ultra에만 제한된 reasoning 비교 설정을 적용했다:
  enable_thinking/medium_effort/force_nonempty_content, reasoning_budget 4096,
  max_tokens 18000(명시적 length에 한해 24000 재시도), 요청 timeout 600초.
  각 native run request_limit 2 / total_tokens_limit 100000. 일반 대화/선택 설정,
  저장된 모델 연결/인증 정보는 그대로다. 앞선 medium 시험은 timeout 300초로 실패했으므로
  이번 비교의 실제 결과를 확인한 후 유지 여부를 판단할 것. 내부 추론은 읽거나 노출하지 않는다.
  참고: https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b/modelcard
- 정상 UI 재시작 후 run `81253371-f416-4982-8f8d-b2e33fd1f77b`를 08:45에 시작했다.
  08:52에 `agent_runtime_provider_rejected`로 종료됐다. 안전 진단은 model_api_error이며
  정확한 HTTP 상태/응답 내용은 없다. timeout 또는 schema 성공으로 단정하지 말 것.
  동일 요청의 무변경 재시도는 하지 않았다. reasoning 비교 설정은 아직 소스에 남아 있으나
  성공 증거가 없으므로 제품 완료/개선 검증으로 취급하지 말 것.
- 일반 사용자 처음부터 생성 흐름의 별도 미완료 문제: narrative는 user_clarifications를
  읽지만 `_bind_native_design_scope_to_project_brief`는 여전히 모든 폼 필드가 채워진
  저장 brief만 받는다. `_complete_narrative_from_brief` 경로는 최신 대화 수정도 반영하지
  않는다. 사용자 답변을 출처가 보존되는 요구사항 갱신으로 연결해야 하며, 한 답변을 모든
  빈 필드에 복제하거나 native authority 검사를 우회해서 해결하지 말 것.
  05:31의 수동 폼 입력 이후 실행은 대화만으로 초기 요구사항을 완성한 증거가 아니다.
- 생성 대기 화면을 실제로 보고 composer 전송 버튼 자리에 중단 버튼을 옮겼다.
  `ComposerActionButton`은 running이면 입력이 비어 있어도 취소 가능하고, 취소 응답
  대기 중에는 중복 클릭을 막으며, 종료 뒤 전송/수정·저장 동작으로 돌아간다.
  진행 안내의 중복 문장을 없애고 경과 시간을 분/초로 표시한다. 실행 UUID는 넣지 않는다.
  Web test-composer-action / test-conversation-permissions 및 production build 통과
  (`index-BRmE0Cuo.js`). 09:04 run `50209084-1aa0-41fb-8600-d024a7173a56`에서 새
  composer 중단 버튼을 눌러 09:05 CANCELLED 및 입력창 복귀를 실제 확인했다.
  이는 취소 UI 시험이지 생성 완료 증거가 아니다. 08:58의 이전 버튼 취소 시험 run
  `1da90232-a164-438c-bc4f-d7e3fd3048c8`도 CANCELLED다. 진행 중 모델 run은 없다.
- 09:00 정상 재시작에서 웹의 준비 대기가 만료됐지만 새 Host PID 19212는
  09:00:47 시작 후 /api/health ready를 반환했다. 중복 실행하지 않고 tab.reload()로
  복구했다. 브라우저 pressKey(Control+r)는 이 환경에서 페이지 갱신 증거가 되지 않았다.
  CUA API 문서 복구 후 명시적 reload로 새 빌드를 실제 확인했다.
- 마지막 표시 수정: CANCELLED + 정확한 detail `run cancelled`만 한국어로 표시하도록
  바꿨다(Web state는 소문자 cancelled). 다른 종료 상세/실패 경고는 보존한다.
  마지막 production build/typecheck/entry 확인도 통과했고 `index-LE0Lsyhi.js`를
  tab.reload()로 반영했다. 문구만 재확인하려고 추가 모델 요청을 만들지는 않았다.

## 2026-09-09 후속 동작 수정 — 기본 변경안 요청, XG5000 연결 제약 조사 중

- Web 기본 권한을 `review_before_apply`로 분리했다. 대상이 변경 가능할 때만 기존
  `offline_candidate_revision` 요청으로 전환하고, 연결 미확인/대상 없는 대화는
  `analysis_only`로 제한한다. 기본값에서 직접 실행 권한을 만들지 않는다. 사용자가
  명시적으로 변경 금지를 선택하면 계속 분석만 한다. 직접 수정 후 기본 검토로 복귀한다.
  질문을 변경 요청으로 취급하지 않도록 Agent 지시와 일반 질문 완료 조건도 맞췄다.
- 재시작 후 저장된 파일 분석 상태에서 XG5000 연결을 복구할 버튼을 현재 프로젝트에
  추가했다. 기존 정확한 작업 사본 창 재사용과 원본/사본 일치 검사는 유지한다.
- 실제 T0026 설정값 5→6 변경안을 기본 대화로 요청한 run
  `e65ec5be-527c-42cb-aa19-8a43348e2879`(07:52)은 analysis_only로 접수됐다. 읽기 연결은
  복구됐으나 수정 대상 증명이 없었기 때문이다. 07:54 모델이 분석 대신 도구/인자 객체를
  반환하여 실패했다. 실제 변경안/파일 수정 성공이 아니다.
- 읽기 재연결 시 strong identity 실패 진단(stage/code만, 원문 제외)을 기록하도록 추가했다.
  07:59 실제 결과는 `vendor_project_identity / ACTIVE_OBJECT_NOT_FOUND`다.
  MCP에 XG5000 4.82가 /Automation으로 시작해도 ROT 객체를 제공하지 않을 수 있다는
  기존 기록이 있다. 원본 복사본의 native 읽기 증명을 COM 수정 증명으로 승격하지 않았다.
  현재 imported 프로젝트의 실제 수정은 여전히 막힌다. new_build 전용 binding을 임의로
  재사용하거나 검증용 unsafe profile을 활성화하지 않았다.
- 신규 설계 응답은 PromptedOutput 대신 NativeOutput을 실제 서버에서 비교했다.
  08:06 run `5ecf14f9-8b1f-4383-b090-15f71fe3a01d`는 08:10 unknown reference로 실패했다.
  참조 필드 설명 및 코드 소유 검증 규칙만 노출하는 진단을 추가한 뒤 08:17 run
  `f3d2f944-079d-4c0a-b6d6-243ab67396a1`도 08:21 출력 안전 조건 누락으로 실패했다
  (`every_digital_output_requires_an_explicit_safety_boundary`). 서버가 응답 형식을 즉시
  거부하지 않은 것과 유효한 설계 생성 성공을 혼동하지 말 것. 원본 입력이나 모델 내부
  추론은 진단에 보관하지 않는다. 새 프로젝트 파일 생성/저장/XG-SIM은 아직 없다.
  같은 요청을 변경 없이 반복하지 말 것. 내부 M 주소의 모의 출력도 digital_output 역할이면
  안전 규칙 집합이 confirmed인 설계에서 전체 출력의 안전 조건 검사를 받는다.
- 현재 회귀 검사: Agent focused 22개, Host 표시/진단 6개, MCP 진단 2개,
  Web request-permissions/permissions 표시 및 production build 통과. 로컬 미커밋.

## 2026-09-09 권한 UI 후속 — 하단 실행 방식 폼 제거

- Codex 공식 Agent approvals & security와 Claude Code Desktop의 permission mode 문서를
  비교했다. 두 제품에도 권한 선택은 있다. 요청 내용과 실행 권한을 구분하는 원칙을 적용했다.
  참고: https://learn.chatgpt.com/docs/agent-approvals-security 및
  https://code.claude.com/docs/en/desktop#choose-a-permission-mode
- 하단의 긴 요청 방식 radio/검증 선택 폼과 중복 읽기·분석 상태 문구를 제거했다.
  대화 상단의 작은 권한 버튼으로 분리하고 상세는 누를 때만 렌더링한다. 직접 수정
  선택 시 버튼은 수정·저장으로 바뀐다. Escape 닫기/트리거 포커스 복귀, 바깥 클릭 닫기 지원.
- 기존 analysis_only 기본값, 검토 후 변경안 적용, 한 번의 직접 수정 후 권한 초기화,
  작업 사본 연결 확인 및 Host/MCP 승인 제약은 변경하지 않았다. 자연어 의도에 맞춰
  변경 시점에 승인을 요청하는 전체 흐름은 아직 구현하지 않았다. UI 정리와 혼동하지 말 것.
- Web typecheck/production build 및 test-conversation-permissions.mjs 통과. 실제 대동인덱스
  기존 대화에서 메뉴 열기/닫기, 수정 불가 시 비활성 항목을 확인했다. 좁은 화면에서 메뉴가
  왼쪽으로 잘리는 문제를 발견해 대화 헤더 기준 위치로 고쳤고 재확인했다. 키보드/접근성
  포커스가 입력창으로 이동할 때도 닫히도록 focus-out 처리를 추가했다. 프로젝트 수정 요청은 보내지 않았다.

## 2026-09-09 후속 실사용 — LD 읽기 복구, 사용자 화면 정리, 신규 생성 미완료

아래 이전 절의 LD 읽기 실패와 재시작 뒤 연결 실패는 후속 수정으로 일부 복구됐다.
전체 제품 완료는 아니다. 이 절의 소스는 아직 로컬 미커밋이며 원격 push하지 않았다.

- 실제 대동인덱스 `초기동작` 읽기는 native clipboard 8192 bytes와 14 visual rows를
  두 번 독립 캡처해 일치했다. CLI는 신선한 sequence/native format 및 정확한 foreground
  physical copy에서만 ownerless clipboard를 수용한다. 다른 PID 소유, stale publication,
  postmessage ownerless는 거부한다. 대상 프로그램·editor·프로젝트 hash 불변 검사는 유지했다.
- Host가 정상 WIRE/opcode 0의 빈 operand를 거부하던 projection을 수정했다. 실제 웹에서
  목록→LD 읽기→답변이 완료됐다(04:34 run `023ead58-d808-4e71-b1ae-234426e6aed7`,
  04:46 자연어 첫 질문도 완료). 프로그램 목록 context 누락, 일반 검색의 전용 인자 gate,
  도구 이름을 영어 문장으로 잘못 판단하던 한국어 검사, 실패 후 다른 도구 선택 gate도 수정했다.
- 내용 정확성은 별도다. partial topology에서 마지막 행을 무조건 출력으로 단정한 답변을
  발견했다. Host는 partial topology를 전달하고 Agent는 행≠완전한 rung, timer preset≠시간을
  명시한다. partial만으로 무조건 출력을 단정하는 답변의 재작성 검사도 추가했다.
  05:51 새 대화의 동일 자연어 첫 질문(run `5b7f8c00-27f6-4042-8d7d-31b433328a5a`)은
  05:53 완료됐고 무조건 출력 단정과 timer preset=시간 오류가 재현되지 않았다.
  답변은 여전히 행별 목록이 길어, 일반 설명의 기본 answer는 짧은 개요·핵심 3개 이내와
  필요한 한계로 제한하고 상세 목록은 접힌 structured analysis에 두도록 지시를 추가했다.
  이 간결한 응답 지시 이후의 실제 답변은 아직 확인 전이다.
- 재시작 후 정확한 작업 파일의 유일한 검증된 소유 창을 기존 attach 경로로 재사용한다.
  새 창을 계속 만들지 않는다. 관측된 원본과 작업 사본 SHA는 `bee3b1a6…a68f`로 동일했다.
  기존 두 XG5000 창, 원본, 작업 사본과 오래된 격리·백업 기록은 보존했다.
- 기본 UI는 대화·결과 중심이다. UUID/해시/기술 영수증은 펼쳐 보는 상세로 이동하고,
  실제 근거 제목을 누르면 근거 패널이 열린다. 완료된 도구 과정은 한 줄로 접힌다.
  새 프로젝트 대화에는 Host 내부 지시문 대신 당시 입력한 요구사항만 표시한다.
  원본 메시지는 내부에 그대로 유지하며 현재 brief로 과거 입력을 덮어 표시하지 않는다.
  계획 중 기존 프로젝트의 프로그램 목록은 숨겼다. 좁은/넓은 화면에서 추가로 드러난
  개발 버전·디버깅 표시와 실패 안내도 정리했다. 최종 반응형 확인은 계속 필요하다.
- 일반 사용자처럼 `대동인덱스 재구성 테스트 0909`를 요구사항부터 시작했다.
  6분할 인덱스, 자동/수동/초기화, timeout alarm/reset, 내부 모의 신호만, 실제 PLC 연결 금지,
  원본/사본 덮어쓰기 금지 조건이다. 현재 planning conversation은
  `aebd609c-2771-4556-9281-01c70d088124`이며 새 파일은 아직 생성하지 않았다.
- 초기 계획은 JSON 출력 불완전으로 실패했다. 후속 응답 예산/최종 내용 설정 수정 뒤
  `58d97395-e92a-43a2-a395-e182810721e9`는 JSON 다음의 `invented_device_address`에서
  거부됐다. 사람이 읽는 narrative에 불필요했던 native 주소 예시·래더 지시를 제거하고
  별도 native design 단계와 분리했다. 승인·근거·실행 검사는 완화하지 않았다.
  05:10 run `9dedc43a-317b-41b9-a489-471466ea5b0a`는 05:14에 실제 계획 생성으로 완료됐다.
  계획은 확인 질문 5개가 남은 상태이며 실행 설계나 파일 생성 성공은 아니다.
- 실제 생성된 계획이 78개 항목 전체와 같은 내용의 탐색 패널을 중복 렌더링하는 문제를
  확인했다. 전체 중복 목록을 삭제하고 기존 섹션 탐색 패널 하나에 검토 기능을 옮겼다.
  기본 화면은 접힌 계획서와 대화의 요약·확인 질문이다. 빈 실행 설계/비활성 체크박스
  목록은 표시하지 않는다. 원래의 정확한 항목·섹션 검토 제출 조건은 유지한다.
- 신규 계획 선택 후 새로고침이 이전 프로젝트 대화로 돌아가는 오류를 실기로 재현했다.
  선택된 planning conversation을 startup 복원 대상으로 포함했고 새로고침 후 동일 계획과
  질문이 유지됨을 실제 확인했다. workspace activation의 대상 일치 gate는 변경하지 않았다.
  05:19에는 신호 수동 모의, 5초 timeout, 원샷 수동 이동, 정지 중 초기화와 모드 전환,
  XG5000/XG-SIM 사용을 일반 문장으로 답해 후속 계획을 요청했다.

- 05:25 run `605274d0-250f-4a5e-9999-dc80d6539007`도 완료됐지만 환경을 재질문했다.
  대화 후속 답을 별도로 narrative에 전달하고 빈 필드와 충돌을 구분하는 지시를 수정했다.
  양식 자동 반영은 아직 구현되지 않았다. 05:31에는 이미 답한 조건을 수동으로 양식에
  반영해 실행 설계 단계까지 진입했다. 이를 정상 채팅 자동화 성공으로 기록하지 않는다.
- `d91af238-5403-423f-a1e9-1345d0e9b355`는 native design의 단일 항목 all_of/any_of
  표현을 schema가 거부했다. provider의 단일 그룹을 같은 의미의 contact로 정규화하고
  executable domain의 기존 조건 제약은 유지했다. 빈 그룹은 계속 거부한다.
- 이후 run `9d049de8-4106-421e-b8cf-605f0f4a3a14`는 05:40에 provider HTTP 502로 실패했다.
  프로젝트 생성·쓰기 성공은 없다. 내부 전용 모의 프로젝트에서 사용자가 주소 배치를
  맡긴 경우 M/T/S/D 구현 배치를 검토할 제안으로 허용하도록 native 지시를 분리했다.
  물리 배선 추측이나 검토 우회 지시는 추가하지 않았다.
- MCP 비동기 시작 완료가 상태 이벤트로 전달되지 않아 생성 기능이 계속 비활성이던
  문제를 재현했다(수동 연결 새로고침 직후 정상 준비로 바뀜). 시작 완료/실패 때 기존
  host.status 이벤트를 보내도록 수정했고 정상 재시작 뒤 별도 새로고침 없이 준비 표시를
  확인했다. 과거 brief 메시지가 요구사항 수정 뒤 내부 프롬프트로 다시 보이던 문제는
  서버 템플릿의 정확한 round-trip에 한해서 당시 요구사항을 복원 표시하도록 수정했다.

- 05:42 run `d58c89ef-cd4f-49cd-825f-20e73ef438c9`는 05:46 native JSON 불완전으로
  실패했다. 명시적인 finish_reason=length일 때만 tool-free native 설계를 더 큰 출력
  예산으로 한 번 재요청하도록 수정했다. 잘린 JSON을 수용하거나 검사를 우회하지 않는다.
- 05:58 run `74d0b63f-9e4b-4631-8d31-e9145f5a27ae`는 06:02 `native_sequence_structure`
  검사로 실패했다. JSON 이후의 실행 설계 조건 검사 실패이며, 이 실행이 length 재요청을
  탔는지는 확인하지 않았다. Provider 단계 주소 schema에도 기존 .00 reset 예약 규칙을
  명시하고, start-release 조건의 상위 선언과 rung 양쪽 반영 지시를 구체화했다.
- 후속 요청이 있는 이전 계획을 최신 확인 질문처럼 표시하지 않도록 계획 헤더를 수정했다.
  실패 후 입력 보존 안내와 이전 계획 접기를 구분한다. 좁은 화면의 더 보기 메뉴에서
  연결 버튼 텍스트가 줄바꿈되던 문제도 font/폭으로 수정했다. Web production build 통과.
- 06:05 run `f044d87d-2d5f-45b1-9e7c-53bdc584c679`도 06:11 동일한
  `native_sequence_structure`로 거부됐다. 예약 step/상위 선언 지시 강화만으로 해결되지
  않았다. 실행 설계의 구체적인 실패 조건 식별과 생성 안정화가 남았다. 새 파일 생성,
  검사, 저장, 재열기, XG-SIM은 수행되지 않았다.
- 긴 사용자 입력(600자 초과)은 짧은 미리보기와 원문 펼치기로 표시한다. 실제 요구사항
  펼치기/접기에서 마지막 성능·완료 기준과 PLC 연결 금지 원문까지 보존됨을 확인했다.
  Assistant 실패·주의 문구는 이 접기 대상이 아니다. 계획의 사용자 안내에서 schema 필드
  이름과 stale 등 내부 용어도 사람용 표현으로 바꿨다. 최신 Web build 통과.
- 06:13 run `8e4613e9-ce9b-42ef-9b02-3b4be0e1ca6d`는 완료됐으나 여전히 긴 행별
  답변과 OUT을 토글로 부르는 오류가 있었다. 간결한 답변 지시만으로 개선됐다고 볼 수 없다.
  partial LD 행만으로 토글을 단정하지 않도록 검사와 테스트를 추가했다. 이후 06:20 run
  `4a7adad4-c306-45d7-8bd6-3b14cf3707a8`는 06:21 engineering_analysis root JSON
  형식 오류로 실패했다. 최신 토글 방지의 실기 통과나 기본 설명 품질 통과는 아직 없다.
  잠시 시험한 700자 길이 거부 검사는 제거했다. 긴 답변 자체를 실행 실패로 만들지 않는다.
- native sequence 오류를 한 코드로 묶던 진단은 예약 reset 단계, 그룹 불일치, 마지막 단계·
  reset·recovery의 시작 해제 조건 누락으로 구분했다. 알려진 제품 규칙 코드만 Agent/Host
  양쪽에서 허용하며 사용자 내용·모델 추론을 기록하지 않는다. 다음 실제 계획 시도의 구체
  원인 식별에 사용한다. 연결 모델은 nemotron-3-ultra이며 다른 연결로 바꾸지 않았다.
- 06:25 run `43d28116-d52d-469e-aa79-acdc8a62d8e0`는 06:34 실패했고 구체적인 원인은
  `native_sequence_final_release_missing`이다. require_start_release 선언과 달리 마지막
  단계 complete_all에 시작 신호의 역상 조건이 없다. Provider sequence의 restart_policy와
  steps schema 설명에도 마지막 complete_all에 역상 START를 명시하도록 추가했다.
  단순히 정책을 continuous로 바꾸거나 코드를 임의 추가해 검사를 우회하지 않는다.
- Pydantic AI의 기본 출력 검증 재시도가 rejected input 전체를 오류와 함께 재전달하는
  경로를 로컬 라이브러리 코드에서 확인했다. native Agent에만 오류 위치·규칙을 bounded
  ModelRetry로 전달하는 capability를 추가했다. 실제 Pydantic AI FunctionModel 재시도
  테스트에서 정상 교정과 rejected input 미포함을 확인했다. 43d run에는 미적용이며,
  종료 뒤 정상 재시작으로 적용했다. 검사를 억제하거나 결과를 수용하지 않는다.
- 넓은 화면에서 신규 설계 중에도 온라인 관찰 진입이 표시됨을 확인해 planning 중에는
  숨겼다. 실제 PLC 관련 기능을 새 프로젝트 계획과 혼동하지 않게 하는 표시 변경이다.
  Web production build 통과. viewport는 원래 크기로 복원했다.
- 06:36 run `22005c57-6ae7-46e4-97e6-cb9057eb2939`는 06:41
  `native_ladder_invalid_target` (`ladder_rungs.19.action.RST`)로 실패했다. 앞선 단계
  의미 검사의 전체 통과를 뜻하지 않는다. provider action schema가 명령 4개×대상 4개를
  모두 허용하고 domain에서만 잘못된 조합을 거부하던 불일치를 확인했다. provider schema를
  OUT/RST→signal, TON→timer, SET→signal/step/sequence_reset의 구별된 union으로 바꿨다.
  16개 조합을 기존 domain 결과와 비교해 동일 허용 집합임을 검사했다. 임의 명령 변환 없음.
- 대화가 페이지 전체를 늘리던 레이아웃에 독립된 스크롤 영역과 입력창을 두었다. 새 대화
  복원과 하단 열람 중에는 최신 메시지를 따라가고, 과거 기록으로 스크롤하면 자동 이동을
  멈춘다. 이때만 최근 대화 이동 버튼을 표시한다. Web build 통과. 실제 좁은 화면에서
  마지막 실패 답변·입력창 동시 표시, PageUp으로 과거 기록 읽기, 최근 대화 버튼으로 복귀와
  버튼 숨김을 확인했다. 현재는 새 결과 수신 중 과거 열람 위치 유지까지 시험 중이다.
- 06:47 run `2eb298f2-711f-4abb-979d-d0417b6483d9`는 06:56 control_networks 1,2의
  permit_all이 비어 있어 거부됐다. Agent/Host/MCP 모두 비어 있지 않은 permit를 요구한다.
  이를 완화하지 않았다. provider 설명에 OUT 구동 출력만 network로 만들고 SET/RST 내부
  latch에는 불필요한 OUT network를 만들지 않으며 실제 brief 신호 조건이 필요함을 추가했다.
  이 형식 안내 보완 이후의 실제 생성은 아직 확인 전이다.
  결과가 도착해도 스크롤해 두었던 06:41 실패 기록 위치와 최근 대화 버튼이 유지됨을
  스크린샷으로 확인했다. 원래 좁은 viewport 유지 중이다.
- 명시적인 행 번호를 가진 3개 이상의 기술 목록만 별도 행별 로직 상세로 접는다. 일반
  사용 지시는 접지 않는다. 목록 안의 미확인·미해결·주의 내용은 접힌 바깥에도 표시한다.
  상세/행별 요청에는 처음부터 펼친다. 원문·실행·근거는 변경하지 않으며 Web helper 검사와
  production build 통과. 06:16 실제 LD 답변에서 목록 접기/펼치기와 목록 밖의 분기 미해결
  안내 유지를 확인했다. 원문의 잘못된 토글 서술도 기록에는 남아 있으며 표시 변경이
  답변 정확성 수정이나 새로운 읽기 성공을 뜻하지 않는다.
- 07:05 run `6752cb83-e0b2-47d0-a313-7da5342b3af7`는 07:14 RST.target_type literal
  오류로 실패했다. schema가 허용 조합을 명시해도 현재 모델 출력이 이를 지키지 못했다.
  신규 파일 생성 성공은 없다.
- Native 설계에서도 기존에는 Nemotron 추론이 강제로 꺼져 있었다. [NVIDIA 모델 카드의
  medium-effort 설정](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b/modelcard)을
  확인하고 설계 Agent에만 enable_thinking/medium_effort/force_nonempty_content를 켰다.
  다른 모델, 일반 대화, 도구 선택의 설정은 유지했다. native 첫 출력 상한 16k와 요청 2회/
  총 75k, 명시적 length 때만 추가 24k 출력 1회/총 50k로 제한한다. 결과 검사는 그대로다.
  추론 내용은 사용자 UI나 진단에 출력하지 않는다. 이 설정은 아래 실기 실패 후 되돌렸다.

- 07:19 run `e4d40e65-00c1-42ce-affe-796d6b443061`은 07:24에 native 설계 응답
  `provider_timeout`으로 실패했다. 모델 연결의 기존 요청 제한은 300초이며 변경하지 않았다.
  추론 실험의 생성 성공 근거가 없어 기본 final-only 6k, 명시적 length 재요청만 12k,
  각 요청 묶음 2회/총 50k로 복원했다. 실제 신규 파일 생성은 여전히 없다.
  timeout 안내는 연결 자체 실패와 구분하여 응답 대기 시간 초과와 입력 보존을 설명한다.
  과거에 저장된 실패 메시지는 소급 덮어쓰지 않는다.
- 실제 화면에서 긴 입력 접기/펼치기, 행별 설명 접기/펼치기, 접힌 설명 밖의 미확인 경고,
  좁은 화면의 최근 대화 이동을 확인했다. 이전 메시지를 읽는 동안 실행이 종료돼도
  강제로 맨 아래로 이동하지 않았다. 도구 활동 갱신도 최근 대화 자동 추적에 포함했다.
  이것은 화면 확인이며 생성 실패나 과거 답변의 부정확한 설명이 해결됐다는 뜻이 아니다.

Focused 검사: Agent 19개, Host WIRE 4개와 요구사항/실패 표시 5개, CLI clipboard 5개,
Web evidence 표시 테스트 및 production typecheck/build 통과. 전체 suite나 실제 생성·검사·
저장·재열기·XG-SIM 완료를 뜻하지 않는다. 현재 실행은 종료됐다. 신규 설계 생성의 모델
응답 안정성과 대화 확인 답변의 요구사항 반영이 남아 있다. 같은 요청을 무작정 반복하지
말고 이를 해결한 뒤 실제 신규 생성과 기존 프로젝트 수정 흐름을 계속 수행해야 한다.

## 2026-09-09 실사용 검증 — 채팅·열기 부분 통과, native LD 읽기 실패

정상 개발 Launcher와 실제 모델(`nemotron-3-ultra`)을 실행하고 웹 화면에서 직접 질문·후속
대화·새로고침·프로젝트 열기·XG5000 연결·프로그램 읽기를 수행했다. 정적 검사만의 기록이
아니다. 다만 **래더 본문 읽기가 실패했으므로 실사용 완료가 아니다.** 후보 준비·직접 편집·
Program Check·저장·XG-SIM은 이번 실기에서 실행하지 않았다.

### 소스 체크포인트

- Platform `1893a50`: 대상 없는 대화의 명시적 `targetScope=none`, 일반 질문 진입·복원,
  접힌 분석 상세, XG5000 open receipt 계약 및 작업 사본 수동 attach 결속 수정.
  `6700f38`은 재연결 대기 중에도 새 작업 진입을 유지하며 Web 빌드 후 실제 진입을 확인했다.
- Agent `71844ae`: 실패한 `read_ladder_program` 반환을 정상 LD model context로 해석해
  예외를 내던 버그 수정. bounded Host 실패만 evidence 생성에서 제외하며 malformed success는
  계속 거부한다. 해당 실패 응답은 다음 판단용 history에 남는다.
- MCP `4698f74`, CLI `518e389`: 이번 실사용 검증에서 소스 변경 없음. 원격 push 없음.
- 정상 기동에 지정된 Python으로 Host/Agent/MCP/CLI import 경로가 각각 현재 네 저장소를
  가리킴을 확인했다. Host의 `코드 다시 불러오기 (UAC 없음)`으로 변경을 반영했으며 XG5000
  창을 종료하거나 기존 불확실 transaction을 복구·초기화하지 않았다.

### 실제 관측 (2026-09-09 KST, DB timestamp는 UTC)

| 사용자 흐름 | 관측 결과 |
| --- | --- |
| 저장된 작업에서 프로그램 목록 질문 | `inspect_workspace_structure` 완료(496ms), 4개 LD 프로그램 답변. 전체 응답 약 56초. 새로고침 뒤 요청·답변·도구 카드가 중복 없이 복원됨 |
| 대상이 이미 있을 때 일반 질문 시작 | 명시적 unbound conversation 생성, workspace/revision/session 모두 null. 빈 대화 및 메시지 있는 대화의 새로고침·작업 전환 복원 확인. 외부 도구/변경 거래 없음 |
| 일반 질문과 후속 정정 | 첫 응답 약 49초, 후속 응답 약 14초. 대화 연결은 통과했으나 첫 답변이 scan time과 주파수를 혼동했고 사용자의 정정 지적 뒤 올바르게 설명함. 모델 답변 품질 통과로 기록하지 않음 |
| 원본 프로젝트를 복사본으로 열기 | 새 작업 사본과 XG5000 창이 실제 생성됨. 최초에는 Host가 MCP의 controlled launch enum을 거부해 502. 계약 수정 후 같은 작업 사본 열기와 자동 strong attach 통과 |
| `초기동작 프로그램의 구조를 설명해 줘` | 실제 목록→필터 목록→구조 분석→래더 읽기 순서로 진행. 래더 읽기는 약 36초 뒤 실패, 전체 run 약 185초 뒤 FAILED. 선택/실행/완료/실패가 같은 채팅 카드로 갱신됨 |
| 코드 재로드 | 대화 기록 복원. native session은 자동 추측 결속하지 않고 offline 구조 대상으로 전환됨. 대상 재연결 대기 동안 새 작업 버튼이 숨던 분기를 수정함 |
| 기존 창에 수동 재연결 | 재시작 뒤 PID 15200/HWND 918690을 선택했지만 현재 프로젝트 경로 검증 실패로 거부됨. 창 보존 안내를 확인했으며 추가 창 생성으로 우회하지 않음 |

프로그램 목록 답변에도 근거가 증명하지 않은 “실행 순서대로”라는 표현이 있었다.
짧은 질문의 긴 모델 대기, 중복 목록 조회, 일부 영어 도구 요약 및 일반 설명 정확도는 남은
실사용 품질 문제다. 성공 terminal을 내용 정확성이나 전체 제품 완성의 근거로 삼지 않는다.

대표 run:

- 목록 질문: `db784cf7-e3b8-426b-8ef2-7586ff54499d` (`COMPLETED`).
- 대상 없는 질문: `d70ffeec-9125-4f08-bea9-332e1d3e557e` (`COMPLETED`).
- 후속 정정: `5609843e-1696-46f3-94fb-fe04d2560f88` (`COMPLETED`).
- native 읽기: `5711c2c9-10e6-4ec8-a970-9243874f4fbc` (`FAILED`), conversation
  `3ca75df4-386f-441c-aaee-b7505cee1f54`, session `82ef8893-53ab-4a4a-b678-a0f5ca29f128`.

### 수정한 실패와 남은 원인

1. MCP open은 `controlled_launch_official_open_read_only` 또는
   `controlled_launch_native_file_read_only`를 반환하지만 Host enum에 없었다. 정확한 producer
   값을 추가했으며 이를 strong identity로 승격하지 않았다. 수동 attach는 현재 작업 사본의
   exact path/hash가 일치할 때만 누락됐던 workspace ID를 전달한다.
2. 실패한 래더 반환도 `_checkpoint_program_read_ld_contexts`가 정상 데이터 검증에 넣어
   `ValueError: checkpoint LD result is not a verified native read`가 발생했다. 실제와 같은
   bounded 실패로 재현 후 Agent `71844ae`로 수정했다. 실패/기존 근거 유지, 정상 LD 수용,
   malformed 결과 5종 및 잘못된 프로그램 이름 거부를 in-memory smoke로 확인했다.
   **수정 후 동일 native 실패의 전체 모델 continuation은 아직 실기 재확인하지 않았다.**
3. 그보다 아래의 실제 CLI 실패는 `read-ld`의 `XG5000_LADDER_CLIPBOARD_MISSING`이다.
   `초기동작` LD 창은 열렸지만 copy 후 전용 `XG5000LADDER` payload를 얻지 못했다.
   실패 screenshot에 해당 프로그램이 보인다. 정확한 clipboard publication 조건 실패는
   아직 확정하지 못했으며 원인 변경 없이 전체 웹 과정을 반복하지 않았다.
4. Host 재시작 이후 기존 창의 수동 attach도 프로젝트 경로 증명 실패로 남아 있다. workspace
   ID 전달 누락은 고쳤지만 그것만으로 native 경로를 증명한 것은 아니다. 일회성 controlled
   open proof를 재생성하거나 이미 열린 창을 추측 결속하지 않았다. 자동 strong attach의
   성공을 재시작 후 수동 재연결의 성공으로 확대하지 않는다.

native 실패 진단은 `%LOCALAPPDATA%/ControlStudio Development/artifacts/mcp-sessions/`
`82ef8893-53ab-4a4a-b678-a0f5ca29f128/ba829a7259c3/`의 `error.json`, `manifest.json`,
`main_window.png`에 있다. manifest의 UIA 진단 수집 timeout을 본래 read-ld 실패와 혼동하지
않는다. Host/Agent에는 일반 `tool_error`로만 전달돼 원인 설명이 부족한 점도 남는다.

### 작업 사본과 재개 주의사항

- 원본: `C:/Users/Administrator/Downloads/대동인덱스-0917-2/대동인덱스-0917-2.xgwx`.
- 새 workspace: `f74fe2f5-5cc3-44d7-82b8-ce549e951dc6`, revision
  `42e009ce-efae-4b54-9cad-8966737517e9`. 작업 파일은 해당 개발 workspace의
  `working/대동인덱스-0917-2.xgwx`다.
- 원본·captured source·working copy의 실기 후 SHA-256 모두
  `bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`로 확인했다.
  열린 working copy는 공유 읽기 스트림으로 해시를 계산했다. 새 편집·저장은 하지 않았다.
- 관측 시 XG5000 PID `18348`/HWND `67174`(첫 open), PID `15200`/HWND `918690`
  (strong attach 후 읽기)이 같은 새 working copy를 열고 있었다. 두 창 모두 보존했다.
  다음 작업은 PID/lifetime을 다시 확인하고 동일 파일의 중복 창을 고려해야 한다.
- 오래된 격리 거래·백업·작업 사본은 건드리지 않았다. 테스트 파일을 대량 추가하지 않았다.
  Agent compile/Ruff F/E9/I, Host compile/Ruff 및 계약 smoke, contracts build, Web
  typecheck/build/production entry 검증은 통과했다. Web chunk 크기 경고와 이전 절의
  OpenAPI guard/기존 MCP lint 문제는 별개로 남아 있다.

다음은 기존 창 재연결의 native 경로 증명과 native read 실패의 최소 경로에서
publication/owner/format 관측을 분리하고,
실패 원인을 안전한 코드로 Agent와 화면까지 전달하는 것이다. 읽기를 복구한 뒤 후보만 작성,
직접 편집·검사·저장, 명시적 XG-SIM을 각각 검증한다. 지금 전체 제품 통과로 기록하지 않는다.

## 2026-09-08 구현 결과 — 채팅 루프와 직접 오프라인 저장

최신 제품 방향의 P0a/P0b와 P1 소스 변경을 `gidong-agent`, `gidong-platform`,
`gidong-plc-mcp`에 반영했다. 실제 XG5000·XG-SIM·UAC 실행은 하지 않았으므로 제품 완료나
실기 통과 기록이 아니다. 아래 “새 채팅 시작점”의 앱 수정 미실행 표기는 이 구현 직전 기록이다.

### 소스 체크포인트 (이 절의 문서 커밋 전)

| 저장소 | 브랜치 | 소스 HEAD | 상태 |
| --- | --- | --- | --- |
| `gidong-platform` | `dev` | `2c19f0f` | Host와 Web/contracts 코드 커밋 완료, 이 문서 변경만 대기 |
| `gidong-agent` | `dev` | `4ebdbe6` | clean |
| `gidong-plc-mcp` | `dev` | `4698f74` | clean |
| `controlstudio-xg5000-cli` | `main` | `518e389` | 이번 구현 변경 없음, clean |

### 반영한 동작

- Agent는 Host가 준 현재 tool catalog에서 다음 도구를 선택하고 실제 성공 또는 정제된 실패를
  다음 cycle에 전달한다. 실패 직후 같은 tool/args의 맹목 재시도를 거부하며, 일반 설명은 열린
  가설을 한계로 밝히고 완료할 수 있다. 대상 없는 일반 질문은 외부 도구와 변경 callback 없이
  별도 안전 경로에서 답한다.
- Host는 모델 선택 시 `tool.selected`, 실제 dispatch 직전에 `tool.started`, 반환 시
  `tool.completed`/`tool.failed`를 기존 event/SSE 경로로 기록한다. Web은 같은 tool call의 상태와
  경과 시간을 한 채팅 카드로 갱신하며, conversation의 `request_message_id`와 run을 다시 결합해
  새로고침/reconnect 뒤에도 해당 요청 메시지(Host 재개 SYSTEM 포함) 아래에 표시한다. 카드에는
  Host가 정한 역할·논리
  대상·짧은 결과만 보이며 raw 인자·경로·endpoint·모델 추론은 넣지 않는다. 모든 도구의 Agent 결과는
  민감 필드 제거, JSON 직렬화, wire 크기를 완료 기록 전에 검증하고, 구조 분석은 추가로 bounded
  projection만 전달한다. 구조 근거는 같은 conversation/run/session/project와 실제 요청의
  program/query/device/cache/pagination/path/scenario에 정확히 결속된 Host 저널 receipt만 사용하며,
  selector drift와 여분 필드를 거부한다. `tool.completed`와 `evidence.added`는 한 DB transaction에
  기록한다. Host/Agent protocol은 `NaN`/`±Infinity`를 양방향으로 거부하고 개행을 포함한 exact
  frame limit을 적용한다.
- 사용자가 `permanent_offline_revision`을 고르면 그 한 요청을 정확한 작업 사본의 수정 권한으로
  사용한다. Host가 동일 actor/run/target/plan digest를 확인하고 기존 transaction executor를
  호출한다. 성공 경로는 구조 diff→XG5000 Program Check→저장까지만 수행하며 독립 재열기,
  XG-SIM, Keep/Revert를 강제하지 않는다. 생략된 verification scope도 이 범위에서만
  `offline_native_check_only`로 정규화하고, 명시적으로 충돌하는 scope는 거부한다. 직접 권한은 같은
  actor의 승인으로 생성과 함께 원자 기록해 승인 대기 이벤트나 결정 race를 만들지 않는다. 실제
  실행 전 실패는 무변경 `FAILED` 영수증으로 닫고, 변경 거래가 mutation 단계인 동안 Agent run의
  HTTP 취소는 409로 거부한다. 검증 실패·원복은 UI에 `tool.failed`로 남긴다. Agent에는 재시도
  판단에 필요한 정식 terminal transaction을 `status=completed` protocol delivery로 반환하되,
  `state=FAILED`/`ROLLED_BACK`을 성공 변경으로 해석하지 않게 한다.
- `offline_candidate_revision`은 계속 승인 대기 후보만 만들며 callback에서 실행하지 않는다.
  `offline_native_reopen_xgsim`은 사용자가 별도로 선택한 V6 OUT rung 후보에만 허용하고, 승인 뒤
  기존 reopen/XG-SIM/Keep·Revert 경로를 사용한다. 다른 후보 형식은 이를 거부한다.
- SYSTEM mission 재개는 항상 `analysis_only`로 강등해 이전 요청의 직접 변경 권한을 재발급하지
  않는다. 이미 terminal이 된 정확한 candidate transaction과 재제안 조건이 함께 일치할 때만
  별도 candidate-only 재제안 권한을 복원하며, 새 직접 실행은 명시적인 USER 요청에서만 가능하다.
- 새 메시지는 위의 엄격한 범위 조합을 따르지만, 이미 저장된 과거 `permanent+reopen` 또는
  `candidate+check-only` 거래·미션은 승인·실행·재개 화면에서 계속 복구할 수 있다. 영구 변경을
  보낸 Web 선택은 성공·오류·응답 불명확 여부와 관계없이 즉시 읽기·분석으로 되돌아간다. 메시지
  전송·재개 성공 응답도 run id, operation id, state를 검사하므로 손상된 2xx를 성공으로 표시하거나
  자동 재시도하지 않는다.
- MCP의 operand/MNE 정상 검증은 check/save-only 계약에서 재열기 callback을 호출하지 않는다.
  단, 검증 실패 원복·명시적 reconciliation은 복구 증명을 위해 독립 재열기를 수행할 수 있다.
  Program Check와 저장 receipt를 확인한 뒤에만 transaction을 끝내며, 저장으로 바뀐 파일 hash는
  같은 PID/HWND/process lifetime/workspace/plan에 결속된 새 controlled-read identity로 회전한다.
  결과가 불명확한 변경은 격리하고 자동 재실행하지 않는다. 물리 I/O 분석이 hardware context
  digest를 재사용할 때는 첫 MCP dispatch 전 현재 conversation/run의 Host 저널 receipt와 project
  hash가 일치해야 하며, MCP process cache만 남은 관측은 근거 권한으로 사용하지 않는다.
  변경 receipt는 계획의 canonical target과 apply→diff→validation→save의 raw/canonical after hash,
  target/program identity를 연쇄 검증하며, 새 canonical 필드가 빠지거나 달라도 거부한다.

### 이번 검증과 남은 일

- Platform Python compile 및 Ruff F/E9/I, Agent compile 및 Ruff F/E9/I, MCP 전체 compile과 변경
  파일 Ruff, TypeScript contracts와 Web typecheck/build, 세 저장소 `git diff --check`를 통과했다.
  요청 범위, 원자 직접 승인/실행 전 실패, 단일 도구 terminal, 완료·근거 원자 기록을 별도 스모크로
  확인했다. oversized Agent 결과 선차단, camelCase 민감 필드 제거, 비저널 hardware context digest의
  MCP dispatch 전 거부도 스모크로 확인했다.
- MCP whole-repo Ruff F/E9/I에는 이번 변경 파일 밖의 기존 진단 12건이 남아 있다. 이번에 바꾼
  `server.py`와 `change_transactions.py`는 전체 Ruff 규칙을 통과했으며, 무관한 파일을 함께 고치지 않았다.
- `scripts/contracts/contract_guard.py`의 read-only 검사는 현재 FastAPI OpenAPI v1 snapshot drift와
  generated TypeScript operation marker drift로 실패한다. `--write`도 snapshot을 쓰기 전에, 이번
  diff 이전 HEAD에 이미 required였던 `EngineeringConversationHandoffV1.reopenReceiptDigest`와
  `reopenReceiptSchemaVersion`의 major 변경을 감지해 중단된다. 이 작업에서 API major를 임의로
  올리거나 guard를 우회하지 않았다.
- 실제 XG5000을 실행하지 않았으므로 P1-control의 Open/read/save 실기와 `c5311c8` timeout 수정은
  여전히 미확인이다. 과거 격리 transaction·백업·작업 사본은 건드리지 않았다.
- 다음은 정상 개발 기동에서 대상 없는 일반 설명과 후보만 작성 흐름을 먼저 확인한다. XG5000에
  의존하는 P1-control Open/read/save와 직접 수정·검사·저장 흐름은 같은 실기 작업자가 이어서
  확인한다. 그 뒤 V6 XG-SIM 대표 흐름과 P2 CLI native 역의존 폐쇄·신규 LD 흐름을 진행한다.

## 2026-09-08 새 채팅 시작점 — 제품 방향 재정리

이번 변경은 **코드/계획 점검과 문서 갱신**이다. 앱 수정·실행·UAC·XG5000 편집·복구는
수행하지 않았다. 제품 완료가 아니다. 아래 과거 날짜 절의 “현재/다음/최우선”은 당시 기록이며
새 작업 지시가 아니다. 다음 채팅은 이 절과 [구현 계획](AGENT_PRODUCT_PLAN.md)을 먼저 읽는다.

### 확정 제품 방향

간단한 채팅 UI에서 Agent가 요청을 받아 도구를 선택하고, 실제 결과/오류로 다음 행동을
결정하며 끝까지 일한다. 선택·실행·완료·실패를 같은 대화에 즉시 표시한다. 검증·복구·승인
관리 화면이 주 제품이 아니다. 모든 요청을 M30→재열기→XG-SIM→Keep→COMMITTED에 보내는
이전 계획을 폐기한다. M30은 대표 변경 사례 하나이고 전체 제품 완료선이 아니다.

대상 확인·작업 사본 보호·불명확한 변경의 중복 실행 방지는 도구 내부에 남긴다. 명확히
요청한 작업 사본 수정·저장은 요청 범위 안에서 이어간다. 불필요한 per-step approval,
전역 차단, 고정 mission/verification gate는 실제 호출 경로에서 단순화한다. 화면만 숨기지 않는다.
물리 PLC는 없으므로 online I/O는 로드맵이며 현재 blocker가 아니다.

### 코드 점검 결론

- 모델/도구 루프가 없는 상태는 아니다. Agent `runtime.py`의 `PydanticAIAgentRuntime.run`,
  `sidecar.py`의 `tool.request/tool.result`가 존재하지만 고정 선택·mission 완료 제한이 남는다.
- Host `execution_broker.py`는 started/completed/failed를 보내고 Web `App.tsx`는 SSE와
  timeline을 처리한다. 이 경로를 재사용하여 선택/실제 시작을 구분하고 채팅 기본 화면으로 정리한다.
- 현재 `analysis_only` 요청과 offline candidate 준비를 구분한다. 모든 읽기가 전역 격리에
  막힌다는 주장은 하지 않는다. Keep/Revert도 특정 XG-SIM 변경 흐름의 현재 구현이다.
- CLI Git/process/JSON 분리는 존재한다. process/app helper·wire·catalog·LD reader/editor는
  CLI 소유지만 `open_project_entry.py`/`legacy.py`의 MCP native 역의존이 남는다.
- 파일·함수·탐색 행 번호·변경 목적·완료 조건은 [구현 계획 §3–4](AGENT_PRODUCT_PLAN.md)에 있다.

### 점검 시점 Git 기준 (문서 커밋 전)

| 저장소 | 브랜치 | HEAD | 로컬 origin 추적 ref 대비 |
| --- | --- | --- | --- |
| gidong-platform | dev | a6037e1 | ahead 302 |
| gidong-agent | dev | efc42a9 | ahead 70 |
| gidong-plc-mcp | dev | c5311c8 | ahead 236 |
| controlstudio-xg5000-cli | main | 0674f94 | ahead 35 |

점검 시작 시 tracked 변경은 없었다. 위 ahead는 fetch 없이 로컬 tracking ref와 비교한 값으로,
현재 GitHub 서버 상태를 다시 확인한 수치가 아니다. 문서 커밋 후 HEAD/ahead는 달라진다.
CLI origin은 `https://github.com/gidong-controls/controlstudio-ls-xg5000-cli.git`다.
Git sandbox 소유권 경고는 해당 저장소에만 `git -c safe.directory=<absolute-repo> -C <repo> ...`
를 사용해 조회했다. 전역 trust 설정을 바꾸지 않았다.

### 실행 상태와 기존 미확정 작업

2026-09-08 읽기 전용 조회에서 XG5000 프로세스와 8767 LISTEN 항목은 반환되지 않았다.
사용자 데스크톱·브라우저 화면을 직접 관측하거나 앱의 현재 기능을 재실행한 것은 아니다.
실제 로드된 모듈 경로도 이번에는 확인하지 않았다.

아래는 **2026-09-04 기록**이며 이번에 저널/작업 사본을 재판독한 결과가 아니다.

- 마지막 저널: seq 57, `QUARANTINED`, session `0233917c-2793-4fd4-a511-f557bb886744`.
- recorded saved hash: `8d2d044037be7c915c1fc39e9b34a5e8835a9c33147b61f9aefdd03631cf4e66`.
- editor rebind 수정 후 독립 재열기까지 갔으나 `show_open_dialog`에서 timeout.
- 후속 MCP `c5311c8`의 중복 Open 30초 cap 제거는 커밋돼 있다. 실제 해결 여부는 미확인.
- M30 변경의 최종 확정/현재 XG-SIM/Keep 성공은 확인되지 않았다. 현재 파일을 읽지 않고
  “M30이 전혀 적용되지 않았다”, “원복 완료”라고 단정하지 않는다.

기존 사본·백업·저널을 보존하고 실제 내용을 확인해 해당 작업을 처리한다. 상태값 초기화나
강제 terminal 처리로 새 작업을 열지 않는다. 이 한 건의 복구를 모든 Agent/UI 개발의
선행 조건으로 삼지도 않는다.

### 다음 채팅에서 바로 할 일

1. 이 절 → [구현 계획](AGENT_PRODUCT_PLAN.md) → 필요한 코드부터 읽는다. 전체 과거 로그를
   다시 감사하거나 똑같은 계획 문서를 추가하는 것으로 시작하지 않는다.
2. P0a 모델 도구 선택/결과 전달과 P0b 채팅 이벤트/화면을 병렬 구현한다. 이미 있는 loop/SSE를 살린다.
3. P1에서 요청 범위의 편집·검사·저장을 연결하고 강제 승인/재열기/SIM/Keep 분기를 정리한다.
4. 실제 필요한 Open/read 제어 오류는 별도 담당자가 기본 명령에서 확인한다. XG5000 실동작은
   한 작업자만 수행한다. 완료하지 않은 최신 timeout 수정을 성공으로 가정하지 않는다.
5. 일반 웹에서 설명, 후보만 작성, 수정·저장, 요청한 XG-SIM, 신규 LD 생성의 대표 작업을 확인한다.
   이후 CLI native 추출·죽은 코드 정리·배포 작업을 이어간다.

개발 진입점: `gidong-platform/scripts/Start-ControlStudio-Development.ps1 -Web`.
`-WebUrl`은 기존 인증 handoff를 위한 옵션이며 발급 URL/token을 문서·로그에 복사하지 않는다.
PWA/웹과 Codex 내부 탭이 실제 사용자 화면에서 열린 것인지는 따로 관측한다.
필요한 UAC는 정상 호출하고, 실제 거부/취소일 때만 해당 승격을 멈춘다. 승인된 Host의 권한을
작업마다 새로 요구하지 않는다. 이번 문서 작업에서는 UAC를 요청하지 않았다.

작업자는 기존 사용자 선호인 Terra High를 사용하고 독립 파일 작업을 병렬 배정한다.
코드/문서 변경은 해당 저장소에 작게 커밋한다. 새로운 검증 시스템·테스트 파일 대량 생성·
전체 백업 절차를 개발 선행 조건으로 추가하지 않는다. 사용자 프로젝트/백업 삭제 권한으로
“과설계 코드 정리”를 해석하지 않는다.

### 문서 읽기 순서

- [PRD](../PRD.md): 현재 제품 요구·오프라인 완료 범위.
- [구현 계획](AGENT_PRODUCT_PLAN.md): 실제 파일/함수와 P0/P1/P2 작업.
- [GUI](CONTROLSTUDIO_GUI_HANDOFF.md), [사용자 흐름](ENGINEERING_COLLABORATION_WORKFLOWS.md),
  [아키텍처](PRODUCT_ARCHITECTURE.md): 목표와 현재 구현의 차이.
- [벤치마크](CODING_AGENT_BENCHMARK.md): 실제 사용자 시나리오와 과거 조사 구분.
- `docs/history/`와 아래 기록: 특정 실패를 추적할 때만 참고할 역사 자료.

## 아래는 2026-09-04까지의 역사 기록

당시의 다음 작업/승인/완료 판정 지시는 위 2026-09-08 방향을 대체하지 않는다.

이 문서는 새 세션이 현재 `dev` 상태에서 바로 이어가기 위한 운영 기준이다. 제품 요구의
최상위 권위는 [PRD](../PRD.md)이며, 이 문서는 구현 상태와 다음 검증 순서만 기록한다.

코딩 에이전트 제품과 PLC 커뮤니티 벤치마킹에서 도출한 작업·실기 평가 기준은
[코딩 에이전트·PLC 엔지니어링 벤치마크](CODING_AGENT_BENCHMARK.md)의 CA-01~CA-12를
사용한다. 일반 SWE-bench나 모델 점수는 XG5000 변경 성공 증거를 대신하지 않는다.

## 저장소와 브랜치

- 세 core 저장소 `gidong-platform`, `gidong-agent`, `gidong-plc-mcp`는 `dev`에서 작업한다.
  `6d06754`, `f70d30c`, `9edc7e5`는 이 handoff 작성 당시의 역사적 code checkpoint이며 최신
  `HEAD` 표기가 아니다. 현재 제품 checkpoint와 실행 상태는 아래 최신 날짜 절을 우선한다.
- 독립 XG5000 CLI는 `main`에서 작업한다. `6a21db7`의 원격 동기화는 과거 기록이며,
  현재 HEAD와 원격 동기화 여부는 Git에서 확인한다. 2026-09-03 변경은 local commit이다.

2026-08-16 10:20 KST의 GitHub 443 거부와 당시 push 재시도는 역사적 기록이다. 이후
push 여부는 사용자별 권한과 원격 상태를 확인한 뒤에만 결정하며, 다음 세션의 자동 작업이 아니다.

## 2026-09-04 LD 원복과 재열기 — 현재 이어갈 지점

- 20:54 최신: MCP `5942e12`는 IL 화면 전환 뒤 같은 program ID/name의 새 MDI/editor
  descriptor를 조회·활성화해 capture에 전달한다. 기존 PID `1808` 웹 복구에서 편집기
  단계를 넘어 새 PID `3752` 독립 재열기까지 진행했으나 `show_open_dialog` 시간 초과로
  종료됐다. 현재 XG5000은 없다. journal seq `57`, session
  `0233917c-2793-4fd4-a511-f557bb886744`, saved hash
  `8d2d044037be7c915c1fc39e9b34a5e8835a9c33147b61f9aefdd03631cf4e66`다.
  launcher의 caller deadline 외에 중복된 Open UI 30초 cap을 제거하고 기존 남은 예산과
  post-binding reserve를 사용하도록 수정 중이다. outer timeout/재시도는 늘리지 않는다.

- 20:44 최신: MCP `d20f099`의 표준 IDOK 버튼 선택을 정상 웹 재로딩으로 반영했다.
  재열기 PID `1808` / session `b65118de-80ba-42f0-9faf-95dc72f26310` 연결은 성공했으나
  원복에서 `MNE_EDITOR_NOT_OPEN`(hwnd `2361654`)로 종료됐다. journal seq `55`,
  `QUARANTINED`, saved hash는 `8527f8e6…` 그대로이며 PID `1808`은 살아 있다.
  같은 session의 `101c64a2bdb1/main_window.png`에는 MainProgram MNE 11행이 실제로
  열린 모습이 보인다. 프로그램 부재로 단정하지 않고 탐지 시점/선택 조건을 조사한다.
  기존 창을 유지하며 새 프로젝트·백업 복원·동일 요청 재실행을 하지 않았다.

- 20:36 최신: MCP `b99e6ae`는 외부 프로세스 최상위 창 제목을 `GetWindowText`로 읽어
  창별 WM_GETTEXT 대기를 제거했다(자식 입력 필드 읽기는 유지).
  정상 웹 실행에서 첫 재열기 PID `15684` / session
  `a090f090-b474-4435-85c5-99e874d1a1d1` 연결은 성공했다. 이후 원복 독립 재열기는
  `XG5000_OPEN_DIALOG_BUTTON_NOT_FOUND`, `stage=official_open_ui`로 종료됐다.
  현재 journal seq `53`, `QUARANTINED`, saved hash
  `8527f8e61841e995c465fb9f821d2b84f4ba22c16f1b11923f23ecc941fa6bd3`이며 XG5000은 없다.
  다음 조사 대상은 파일 대화상자의 Open 버튼 선택이다. 같은 요청은 재실행하지 않았다.
  최상위 caption API 근거: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowtextw

- 이후 실기에서 MCP `359c005` / CLI `0674f94`가 원인 단계
  `show_open_dialog`를 전달했다. 대화상자 선택 뒤 중복 검사 제거 `1d1b5d0`와
  실제 입력 필드 검사를 selector에 한 번 보존한 `16bd7e2`를 반영했으나 같은 단계에서
  시간 초과가 재현됐다. 해결 완료가 아니다. 다음 수정은 최상위 외부 창 제목 열거에서
  창마다 WM_GETTEXT 응답을 기다리지 않도록 하는 것이다. 자식 입력 필드 읽기와 구분한다.

- 20:12 후속: CLI `0a2bdcd`는 wrapper의 직접 원인 예외에서 고정 XG5000 오류 코드를
  전달한다. 정상 재로딩 후 백업 없는 재열기를 한 번 요청했으나 이번에는 첫
  `xg5000.open_project`가 HTTP 409로 종료됐다. 실제 원인은
  `XG5000_OPEN_PROJECT_TIMEOUT`, `stage=official_open_ui`다. PID `14792`는 종료됐고
  journal seq `51`은 그대로다. 권한 오류로 바꾸어 설명하거나 동일 요청을 반복하지 않는다.
  Open UI 내부 단계와 중복 창 탐색의 시간 소모를 조사 중이다.

- 최신 실행은 journal seq `51`, `QUARANTINED`, saved hash
  `a81e9e1b7d3dae92df9001d4ddc561533c916e295528f2aff8522f881546a86b`다.
  직전 seq `49`의 실제 파일/journal 일치 해시 `7ced7811…`로 첫 재열기는 성공했다.
  CLI `a98668b`는 `fresh-reopen-readback`의 `WorkspaceError` 원문을
  노출하지 않고 기존 고정 `{reason_code, stage}` 분류를 다음 실행에 전달하게 했다.
  이는 launcher 오류를 고쳤다는 주장이 아니며, 이전 session
  `9bf51bf1-d186-4632-9ba9-9624d01a5eda/94942f622d80/error.json`의 실제 종료 원인은
  구조화 정보가 없어서 아직 확정되지 않았다.
- Host `ff2d723`와 UI `0a361aa`는 검증된 현재 journal saved hash로 창 없는 복구 재열기를
  결속하고, UI가 직전 cached workspace hash 때문에 백업 복원만 요구하지 않게 정리했다.
  정상 웹 재로딩 뒤 `XG5000에서 작업 복사본 다시 열어 복구`를 백업 복원 없이 한 번
  실행했다. PID `15120`으로 프로젝트가 열리고 session
  `734dc4d1-854a-4284-a9f0-665bd7ffd548` 복구 연결까지 성공했다. 웹 해시도 `7ced7811…`로
  갱신됐다. 이후 원복의 독립 재열기가 `XG5000_OFFICIAL_OPEN_UI_FAILED`,
  `stage=official_open_ui`로 실패했다(`e2d4f1e53f68/error.json`). 현재 XG5000은 없다.
  첫 재열기와 독립 재열기의 차이를 조사 중이며 최종 원복·M30 적용·XG-SIM·Keep은 미완료다.
- 이미 반영된 MCP `e7cb417` / CLI `33f5c3b`의 plaintext/raw MNE 해시 분리와
  MCP `f8b2ab6`의 Windows 경로 비교 수정은 유지한다. 아래 19:12 해시 오류는 이 수정 전
  기록으로, 같은 원인을 다시 고치지 않는다.

- 19:12 KST 최신 실행: MCP `d65a6af`는 live baseline LD 일치가 확인된 복구에서 private
  baseline MNE 행 수를 전달해 중복 행별 탐색을 생략한다. 그 후 관측한 END-only 복사를
  고치기 위해 `f392716`은 범위 선택 뒤 Copy도 같은 SendInput 순서에 한 번만 넣는다.
  동기 Copy 및 후속 fallback은 없다. 실제 웹 복구에서 attach와 전체 읽기를 통과했고,
  원복의 Program Check·저장 뒤 CLI 독립 재열기까지 진행됐다. 이전 PID `16168`은 종료됐고
  현재 XG5000은 PID `15856`(생성 19:12:06.736), 같은 프로젝트 제목으로 실행 중이다.
- 최종 원복은 아직 실패다: `fresh-reopen-readback`이 `reopened mnemonic source digest
  differs from the authorized source`를 반환했다. artifact는 session
  `4ac09d88-5beb-4479-9090-78dd3b02307f/0d788384dfe0/error.json`이다.
  journal seq `45`, 상태 `QUARANTINED`, active PID/HWND는 이전 `16168/1377718`,
  current saved hash는 `e01575464a9fcc57d6f4aefca1dd5cfcd3ff6dbc09d88fc58bfe3ddaabd94e8c`다.
  실제 재열기 내용과 기대 source digest를 비교 중이며, 재생성·재복원·재실행하지 않는다.
  아래 seq `43`과 이전 오류는 이 실행 전 기록이다.

- Host `bd7b571`로 위 경로 비교 문제를 수정했다. 실제 차이는 작업 파일 경로의 `LD`/`ld`
  대소문자뿐이고 다른 stable identity 필드는 같았다. 기존 `_normalized_workspace_path`로
  경로만 비교한다. UAC 없는 반영 뒤 같은 PID `16168` 복구 요청은 Host 검사를 통과했지만
  `xg5000.attach`의 `MNE_EDITOR_READBACK_TIMEOUT` (`Timed out while reading mnemonic rows.`)으로
  종료됐다. 웹 증거 코드는 `mne_native_status_unavailable`; journal은 여전히 seq `43`이다.
  이는 `OpenClipboard` 오류 해결의 실기 완료 증거가 아니며, 새 행 읽기 실패를 조사한다.

- MCP `1b22ab2`는 정확 MNE 범위 선택 뒤 물리 Ctrl+C와 후속 native Copy를 중복 보내던
  경로를 단일 동기 `ID_EDIT_COPY`로 바꿨다(15줄 추가/182줄 삭제). MCP `2d77321`은 기존
  정확 모달 분류기를 통해 `OpenClipboard 실패입니다.` 안내만 확인하고 새 복구 읽기를
  시작하도록 연결했다. 두 변경을 UAC 없이 반영했다. 재시작 직후 MCP unavailable은 이후
  정상 창 조회로 회복됐고 PID `16168` / HWND `1377718`을 웹에서 다시 확인했다.
  같은 창의 복구 요청은 Host에서 HTTP 409 `active recovery workspace identity changed`로
  거부돼 네이티브 읽기에 도달하지 않았다. 복원 후 작업공간 identity의 실제 차이를 조사 중이다.
  따라서 단일 Copy 수정의 실기 성공은 아직 주장하지 않는다. seq `43`과 현재 창을 유지한다.

- 18:35 KST 실제 진행: Platform `2e74c5d`를 UAC 없이 반영한 뒤 같은 웹 종료 요청이
  성공했다. PID `13884`는 정상 종료됐고 종료 receipt digest는
  `caf152fe2865a94e5b7ef110d07d8c41090d967ae621df1fb34e5b790a4c081d`다.
  이어 웹의 `검토한 백업 복원 후 XG5000 복구 검증`을 한 번 실행해 canonical 작업 파일을
  백업 해시 `16248471…`로 복원했고 journal seq `41`에 기록됐다. 새 XG5000 PID `16168` /
  HWND `1377718`이 같은 프로젝트를 열었다. 현재 session은
  `1d8bcbd9-f489-4be3-bc3a-8aab2b3ddd0b`, seq `43`, 상태 `QUARANTINED`다.
  종료·복원·재열기까지는 실제 동작했지만 원복 최종 검사는 실패했다.
- 새 실패는 위 session의 `286f3a2366a8/error.json`:
  `MNE_REPLACEMENT_DIALOG_VISIBLE`, phase `capture_before`, dialog_count `1`이다.
  같은 artifact의 화면은 baseline 11행이 선택된 MNE editor와
  `XG5000` 제목의 `OpenClipboard 실패입니다.` / `확인` 오류창을 보여준다.
  Program Check 선택 modal과 구분한다. 원인을 확인하기 전 동일 복구를 반복하지 않는다.
  아래 seq `40` / PID `13884` 및 최초 종료 거부는 이 성공 이전의 기록이다.

- 최신 웹 실행: Platform `d421486`/`27987bc`, MCP `5f78a9d`/`dfab29a`, CLI `63197b1`을
  기존 Host의 `코드 다시 불러오기 (UAC 없음)`으로 반영했다. 내부 브라우저의 복구 화면에서
  PID `13884` / HWND `3803514` / 생성 시각 `1788501517.2690492`를 선택하고
  `선택한 창 저장 없이 닫기 검토` → `저장하지 않고 닫기`를 한 번 실행했다.
  Host가 `offline_change operation 588463c0-11fe-4748-a8e1-0b2639df1a05 is stale and
  requires explicit recovery`로 거부했다. CLI 종료에는 도달하지 않았으며 같은 창이 남아 있다.
  일반 target-switch lease가 복구용 종료를 별도 attach로 분류하는 문제를 수정 중이다.
  수동 lease 삭제나 파일 복원으로 우회하지 않는다.
- 위 커밋의 구현 내용: 선택한 실제 창의 PID/HWND/생성 시각으로 정상 종료를 요청할 수 있고,
  사라진 작업 파일은 기존 검증 백업 복원 기능에서 복원할 수 있다. 복원 시 원본 파일이
  없었다면 보존 receipt는 null이며, 실제 복원 해시를 journal에 기록하되 `QUARANTINED`를
  유지한다. 네이티브 종료·백업 복원·새 재열기 성공은 아직 확인되지 않았다.
- 마지막 읽기 확인에서 canonical working `.xgwx`는 없고, 보호 원본과 거래 백업은 모두
  SHA-256 `16248471e9a7131623b65ea6ad7b43f59fa6a78dcccf462d88687fbc8442f13e`로 보존됐다.
  사용자 바탕화면 Chrome/Edge를 열었다고 주장하지 않는다. 조작한 것은 Codex 내부 웹 탭이다.

- 같은 대화 `868e0126-a603-481a-9262-4ecef0023e9d`, run `e6259675-650a-48d7-b08a-7d726ef6bd4b`,
  Host transaction `588463c0-11fe-4748-a8e1-0b2639df1a05`, MCP transaction
  `1f4710c8-797e-476a-85f2-aa972ff137c7`를 유지한다. 상태는 `QUARANTINED`, journal seq `40`,
  active session `ea856ceb-60dc-4ee2-af2f-ffaa83b4ca36`, active PID `13884` / HWND `3803514`다.
  과거 session/lease 값을 다시 쓰지 말고 현재 journal을 읽는다.
  M30 적용·독립 재열기 최종 readback·XG-SIM·Keep은 미완료다.
- MCP `508fff0` / `9921075`의 collapsed dock 펼침을 반영한 정상 웹 복구는
  `f36ba0df-d4ce-4e76-b9dd-d0549cfa072b/7b2a576cbdd0/error.json`에서 끝났다.
  `cli_timeout_seconds=30.0`, `program_check_phase=check_fresh_transition`,
  `program_check_elapsed_ms=11031`이며, 펼침 함수까지 도달한 증거가 없다.
- 실제 원인 구분: 현재 UIA의 `프로그램 검사` 버튼은 MainRes mapping `876`을 기록하지만,
  그 버튼의 실제 command ID를 확인한 것이 아니다. 이 버튼 클릭을 검사 실행으로 취급하지 않는다.
  반면 과거 실제 artifact `5012a1ed-878a-4a99-895e-c3ece3baf26a/a9d9e275aab0/result.json`에는
  동일 PID `12492` / HWND `1901812`에서 `resource-proven-mainres-wm-command` `876` 호출 뒤
  새 검사 modal, 모든 프로그램 선택, MainProgram 11-step 검사 로그와 오류 없음이 기록되어 있다.
  메뉴의 `보기`라는 이름만으로 876도 단순 pane toggle이라고 단정하면 안 된다.
  MCP `99b3358`은 정상 실행을 이 실제 성공 명령에 연결하고 새 modal·실제 결과 판정을 유지한다.
- `99b3358`을 반영한 14:50 KST 정상 웹 복구 한 건도 아직 실패했다. artifact는
  `3c6409a1-5ce9-43bf-a796-0c3d9b64585a/41f77341fc22/error.json`, 오류는
  `PROGRAM_CHECK_FRESH_MODAL_UNVERIFIED`, backend는 `resource-proven-mainres-wm-command`다.
  최신 캡처는 MainProgram의 11개 IL 명령이 전체 선택된 상태다. 성공한 5012… 기록은
  LD roundtrip의 최종 LD 보기 직후였다. 기존 toolbar helper에 있던 LD 전환/포커스 준비가
  direct command 경로에는 없는 점을 조사 중이며, 이것을 아직 실증된 원인이나 해결로 기록하지 않는다.
- MCP `105e104`은 기존 LD 전환·정확한 editor focus 준비를 재사용한 뒤 MainRes 876을 보낸다.
  이를 반영한 14:57 KST 정상 웹 복구는 Program Check와 save의 반환 조건을 통과하고 독립 재열기로
  진행했다. `rollback_program_replacement`의 순서는 baseline readback → Program Check →
  post-check readback → save → `_verify_reopen`이다. 그러나 최종 예외로 단계별 반환 receipt와
  `ROLLED_BACK` 전이는 생성되지 않았으므로 전체 원복 성공으로 기록하지 않는다.
- 새 실패는 `3a589247-df07-4869-b67c-cb28cb1e8c60/c45e4646638f/error.json`의
  `XG5000_PROGRAMS_NOT_FOUND`다. 기존 PID `12492`는 정상 close 경로에서 사라졌고 새
  PID `13884` / HWND `3803514` 한 개가 같은 프로젝트 제목으로 열렸다. 현재 journal의
  active PID/HWND는 여전히 이전 값이다. 웹의 창 목록 갱신은 새 PID를 표시하며 미선택 상태다.
- 실제 실패 호출자는 CLI가 아니라 rollback에 남아 있던 MCP 경로다:
  `server._rollback_mne_executor` → `_mne_reopen_project` → `program_replacement._verify_reopen`
  → `resolve_exact_program_target` → `list_ld_programs`. apply는 이미 `_mne_fresh_reopen_project`로
  standalone CLI를 사용하지만 rollback은 아직 별도 catalog 좌표 탐색을 사용했다.
  CLI `_select_reopen_program`에도 중복 catalog 탐색이 있으나 그 수정만으로 현재 원복 오류를
  해결했다고 주장하면 안 된다. MCP `c132f5a`는 원복도 기존 CLI 재열기 경계로 통일하고
  baseline 기대값을 결속했다. CLI `7107d8d`까지 exact named editor 재사용을 반영했다.
- MCP `bb57cfa`와 CLI `b3cc0c8`은 toolbar 진단 버튼을 정상 Program Check 실행 증거로 수용하던
  분기를 제거했다. 임시 phase trace는 MCP `93b57ee`로 제거했고 별도 toolbar 진단만 남는다.
  Host `561fd8f`/`1714d67`은 reconciliation rollback receipt와 standalone-CLI reopen variant를
  기존 검증 경계에 연결했다. 이 변경들은 정상 웹 재로드로 반영됐지만 terminal 원복 성공은 아직 없다.
- MCP `11b61db`는 durable recovery에서 journaled target editor를 먼저 연 뒤 기존 catalog를
  읽게 했다. 15:29 KST 실제 복구는 `mne_native_status_unavailable`로 끝났으며 seq/lease는
  바뀌지 않았다. 이 오류를 과거 `XG5000_PROGRAMS_NOT_FOUND`로 추정하면 안 된다.
- Host `54d6759`는 recovery gateway도 기존 private MCP 오류 logger를 재사용하게 했다.
  15:45:23 KST 정상 웹 확인에서 실제 오류가 기록됐다:
  `xg5000.attach / ValueError / timeout must be between 0 and 30.0 seconds`.
  새 editor-open 호출의 60초가 MSAA worker의 최대 30초 계약을 위반한 것이다. UAC 실패가 아니다.
  MCP `7f52db5`는 동일한 editor-open 호출 세 곳을 기존 20초 범위로 고쳤다. 제한 확대나
  재시도 추가는 없다. CLI fresh-reopen은 이미 editor open을 최대 20초로 제한하므로 변경하지 않았다.
- 15:50:14 KST 이후 실제 복구는 `XG5000_PROJECT_SEARCH_RESULT_UNVERIFIED`로 끝났다.
  16:16 KST 실행의 전체 오류 자료
  `f7c0fb84-538e-4876-a934-4281b45cd37b/1086fb5a5957/error.json`에는 exact matching node가
  **0개**다. 캡처의 검색어는 MainProgram이 아니라 프로젝트 이름이며 활성 MDI는 시스템 변수다.
  실제 호출 순서는 `open_program_editor` → `_expand_bound_project_tree_for_program` →
  프로젝트 루트 exact 선택 → MainProgram 선택이다. 제목에서 얻은 `웹 LD 자기유지 20260903`와
  live MSAA 루트 표시 `웹 LD 자기유지 20260903 *`가 달라 첫 루트 선택에서 실패했다.
  앞서 MainProgram의 화면 밖 위치를 이번 오류의 원인으로 설명한 것은 잘못된 추론이었다.
  이 실행에서는 MainProgram 선택이나 accSelect까지 도달하지 않았으며, 캡처의 native/MSAA
  grid 사각형도 같으므로 DPI 불일치가 원인이라는 증거는 없다.
- MCP `8d10213`, CLI `0d98248`/`4c7814d`의 화면 밖 행 선택 변경과 MCP `282bfa5` /
  CLI `0615b54`의 Project 검색 Edit 포커스 후 Enter 입력 변경은 반영됐지만, 위 루트 표시
  불일치보다 뒤의 프로그램 선택 성공을 입증하지 못했다. 성공한 수정으로 기록하지 않는다.
  MCP `a4a75f1`은 durable attach의 기존 오류를 바꾸지 않고 origin session에 기존 오류 bundle을
  남겨 실제 실패 지점을 확인하게 했다.
- MCP `3e8e12e` / CLI `2dcddf4`는 live 루트의 표시 라벨을 제목 또는 정확히 `제목 + " *"`로
  해석한다. 첫 실행의 `050f8807ad5c`는 새 resolver가 경량 MSAA 결과에 없던 `role`을 요구해
  실패한 구현 오류였다. CLI `26e115a`가 공유 helper의 경량 결과에 실제 역할 값을 포함하게
  고쳤으며, MCP는 같은 helper의 노드를 그대로 받는다.
- 16:32:39 KST 실행 `f1c09f2be8e6`는 루트 선택·펼침을 통과했다. Enter를 보내기 전 이미
  MainProgram이 visible MSAA row `[68,265,100,281]`로 나타났지만 추가 검색 입력의 포커스
  검사에서 멈췄다(`1444554` Search Edit 대신 `1049978` workspace toolbar가 포커스 보유).
  이는 프로젝트 검색이 이미 적용된 뒤 불필요한 Enter를 요구한 문제다. MCP `8ce1d4a` /
  CLI `5a79efb`는 그 물리 클릭·Enter를 제거하고 기존 WM_SETTEXT readback·settle·실제 MSAA
  행 확인을 유지한다. 커서 원복이 클릭을 다른 곳으로 보냈다는 인과는 입증하지 못했으므로
  공용 SendInput을 추정으로 바꾸지 않았다.
- 16:42:26 KST 실행 `0bfdfdf726e3`는 마지막 프로그램 MSAA 읽기에 남은 시간이 0.05초여서
  실패했다. MCP `8f46ac4` / CLI `40f9689`는 정상 경로를 요청 프로그램 필터 → exact 행 확인
  → 편집기 열기로 바꿨다. 기존 결과가 실제 hidden-only exact 행을 증명할 때만 루트 펼침을
  한 번 수행하며, 전체 제한 시간은 늘리지 않았다. 해당 오류 manifest의
  `current_process_elevated=true`이므로 이 실패를 UAC 미승인으로 설명하지 않는다.
  16:51:53 KST 정상 웹 복구는 편집기 열기와 durable LD 식별을 통과해 seq36에서 현재 PID로
  recovery 대상을 재결속했다. 실제 `program.rollback_mne_replacement` 실행의
  `0e56fadb-eb35-4929-bb70-461845348cb9/56a36faf5c86`는 Program Check 전 dock-close 판정에서
  실패했고 seq37로 격리를 유지했다. 캡처에는 MainProgram의 기존 11개 명령이 보이지만,
  rollback 완료·save·fresh reopen receipt는 없다.
- 그 실패는 닫기 입력 실패가 아니다. 실제 Program Check 체인(pane `32840`, toolbar `1709`)은
  숨겨졌고 공유 dock에서 찾기 1(pane `32841`, toolbar `1397`)만 보인다. 기존 snapshot의 첫
  MSAA-label 분기가 남은 제목과 공유 dock 안의 임의 list를 검사 결과로 오인했다.
  MCP `952d420`은 이 잘못된 MSAA 분기와 반복 전체-MSAA 조회를 제거하고, 기존의 정확한
  active-tab/native-child 체인을 유일한 판정 경로로 사용한다. 닫기 재시도나 timeout 증가는 없다.
  반영 후 첫 instances 조회는 503이었으나 한 번의 읽기 전용 새로고침은 200과 동일 XG5000
  창을 반환했다. 이를 import 오류나 UAC 실패로 설명하지 않는다.
  17:06:32 KST 정상 웹 복구는 `PROGRAM_CHECK_FRESH_MODAL_UNVERIFIED`로 끝났다.
  artifact `f4da5b69-c70c-4565-94af-43ce04be6aef/75987b6eebdf`의 실제 캡처에는 LD 보기 위에
  `프로그램 검사 - LSPLC` 선택 대화상자가 열려 있다. 같은 자료의 `#32770` HWND `853350`은
  native text가 빈 문자열이다. 명령이 실행되지 않았다는 판정이 아니라 modal 관찰 실패다.
  이 대화상자를 그대로 둔 채 876 명령이나 editor 복구를 반복하지 않는다.
  MCP `d5934eb`가 native dialog caption 읽기와 exact predicate 대기를 수정했다.
  정확한 소유 Program Check modal 취소에는
  `recover_reviewed_save_modal`의 IDCANCEL 경로를 재사용할 수 있다. 이 함수는 XG5000을
  닫지 않으며, 뒤의 별도 `close_reviewed_xg5000_no_save`와 혼동하지 않는다.
  MCP `854d9f7`이 이 취소를 MNE durable recovery의 editor 읽기 전에 한정해 연결했다.
- 17:33:23 KST 정상 웹 복구는 UAC 없는 재로드 후 위 두 수정으로 modal 정리·LD 식별·
  Program Check를 통과해 `project-save`까지 진행했다. 최신 artifact는
  `ea856ceb-60dc-4ee2-af2f-ffaa83b4ca36/b5d82560232c`이며 오류는
  `PROJECT_SAVE_TARGET_IDENTITY_UNVERIFIED`다. 검사 창 오류로 다시 설명하지 않는다.
  실제 캡처의 Program Check는 오류 0개이고, active MDI는 editor commit 후 시스템 변수다.
  **현재 working의 원래 `.xgwx` 파일은 없다.** 17:34:41 저장 때 생성된
  `웹LD자~2.XGW_bkx0`와 `웹LD자~2.state`, `~tmp13884`가 남아 있고 XG5000은 여전히 살아 있다.
  공유 읽기 stream으로 읽은 short-name backup SHA256은
  `01ce9a5d843c11fbe3438d014ece100e1fac7e09a1b6b792aac60ee2589fc9fc`다. journal current hash
  `d803e78b85017942286ae05fd39fd3f1e4222f5e5edd2c6f749d0c3f8a394ed8`와 다르므로 임의로
  같은 파일이라고 수용하거나 복사하지 않는다. 저장 identity 검사 자체를 완화하지 않는다.
  MCP `85bf7cf`는 전체 파일명을 8.3 이름으로 바꾸던 helper를 삭제했다. CLI `0d05a68`은
  MNE/LD 독립 재열기와 compatibility open을 기존 one-shot 공식 Open UI로 통일했다.
  두 파일의 syntax/diff 확인만 끝났고, 이 launcher 수정 이후 native 재열기·저장은 아직 없다.
  원본/체크포인트 백업의 `162484…`는 보존되어 있다. 기존
  `change.restore_verified_quarantined_backup` 기능과 정상 no-save close를 통한 복구 경로를
  조사 중이며, 파일·journal·lease를 수동 수정하거나 추가 저장/재열기는 하지 않았다.
- 사용자 화면의 일반 Chrome/Edge에 ControlStudio 창을 열었다는 증거는 없다. 현재 조작 중인
  `http://127.0.0.1:8767/`은 Codex 내부 browser tab 5다. 내부 tab visibility 또는 queued 상태의
  `open_in_codex`를 외부 창 표시 성공으로 설명하지 않는다. Windows Computer Use helper의
  허용된 복구가 실패했고 외부 창 launch가 거부된 뒤 우회 실행이나 수동 UIA 입력은 하지 않았다.
- 보호된 source 파일 SHA256은 여전히
  `16248471e9a7131623b65ea6ad7b43f59fa6a78dcccf462d88687fbc8442f13e`다.
- 사용자 화면에 웹을 열었다고 주장하지 않는다. `127.0.0.1:8767` 서버와 내부 tab 5의 정상 웹은
  확인했지만, 표시 요청은 계속 `queued`다. 후속 조회에서 visibility는 `true`로 읽혔지만 이것만으로
  사용자 화면 표시를 증명하지 않는다. Windows 창 확인 도구는 timeout이고, read-only 프로세스
  조회에는 Firefox `세션 복원` 창만 있었다. 외부 정상 브라우저를 띄웠다고 주장하지 않는다.

## 2026-09-04 14:21 KST 경과 기록

- 같은 대화 `868e0126-a603-481a-9262-4ecef0023e9d`와 run
  `e6259675-650a-48d7-b08a-7d726ef6bd4b`를 유지한다. Host transaction
  `588463c0-11fe-4748-a8e1-0b2639df1a05`, MCP transaction
  `1f4710c8-797e-476a-85f2-aa972ff137c7`는 여전히 `QUARANTINED`다.
  M30 적용·저장·독립 재열기·XG-SIM·Keep 성공은 아직 없다.
- MCP `4370927`은 정상 Program Check에서 모든 툴바의 remote-memory 조사 대신
  exact toolbar 59435의 유일한 enabled UIA 버튼을 사용한다. MainRes mapping은
  `resource_command_id=876`이며 live button-ID를 읽었다고 주장하지 않는다. 이후 실제
  session `26169e13-dd63-422b-a01f-0ec422ee0ca0/f76101570239`에서도 `CLITimeout`이
  발생했다. 따라서 이 축소만으로 원인이 해결됐다고 기록하지 않는다.
- MCP `52b9b8f` / `4a80fbf` / `81e7916`은 다음 실제 timeout의 마지막 함수 단계·경과 ms·
  outer timeout 숫자만 기록하는 임시 측정이다. Windows CRLF를 처리하며 raw stderr는
  노출하지 않는다. 원인을 측정해 고친 뒤 이 임시 코드를 제거한다. 제한 시간을 늘리거나
  재시도 루프를 추가하지 않았다.
- 정상 웹의 UAC 없는 코드 재로드로 Host PID `9708`, Platform/Web `da4165e`,
  MCP `81e7916`, Agent `efc42a9`, CLI `8e69032`를 반영했다. XG5000은 기존 PID
  `12492` / HWND `1901812` 한 개 그대로다. `da4165e`의 MCP-ready 전환 후 read-only
  대상 목록 갱신은 실제 UI에서 확인했다. 자동 선택·복구는 없으며 수동 새로고침도
  필요하지 않았다. 외부 Chrome/Edge 창이 열린 증거는 없다. 확인된 웹은 Codex 내부
  browser tab 5의 `http://127.0.0.1:8767/`이다.
- 같은 PID로 한 번 실행한 측정 복구는 Program Check 전 `MNE_ROW_COPY_FAILED`로
  끝났다. 실제 artifact는 session
  `c9d4d29b-b19c-4fce-88cf-870b49db4032/7a7985977e82/error.json`이며 row index 1,
  view HWND `8063498`이다. 캡처에는 기존 11개 IL 명령과 선택된 두 번째 행
  `AND NOT M00020`이 보인다. journal seq `25`, active session은 위 c9d4…,
  lease `e1b75df0-eebf-453e-9eeb-c7aee7827008`이다. 이 시도에서는 Program Check
  측정 단계에 도달하지 않았다. 지금은 이 exact row-copy publication 경로를 조사한다.
  원인 변경 없이 다시 실행하거나 journal을 직접 수정하지 않는다.
- MCP `378f82c`의 row-copy publication 순서 수정 뒤 session
  `4d68677d-582b-4c6f-8aef-c1989b6c83f3/cbceccf2f7d3`는 별도의
  `XG5000_LADDER_CLIPBOARD_MISSING`에서 끝났다. 실제 실행 파일은 MCP ladder 복사본이
  아니라 standalone CLI `vendor/live/read_ld.py`다. CLI `efd2183` / `deb2078`,
  MCP `1792f26`은 copy 직후 clipboard를 열지 않고 sequence 변경·대상 PID 소유권·
  예상 native format의 `IsClipboardFormatAvailable`을 확인한 뒤 읽는다. 마지막 무조건
  format 열거/read와 쓰이지 않는 helper도 제거했다. Copy 횟수·기한은 늘리지 않았다.
- 위 변경을 정상 웹에서 UAC 없이 반영한 Host PID는 `8360`이다. 실행 코드 기준
  Platform/Web `0e6a8d9`, MCP `1792f26`, Agent `efc42a9`, CLI `deb2078`이며 XG5000
  PID/HWND는 여전히 `12492` / `1901812`다. Web `0e6a8d9`는 historical SSE 재생의
  대화 조회를 in-flight+trailing 방식으로 합친다. 실제 웹 reload 뒤 같은 대화/격리
  복원이 유지됐고, 관찰한 새 log 53줄 중 해당 대화 GET은 10개였다. 모든 재연결에서
  고정 2회라고 주장하지 않는다. 가장 최신 caller의 epoch와 allowSwitch를 함께 사용한다.
- 최신 실제 복구는 MNE·LD 읽기를 통과하고 Program Check result-read까지 도달했다.
  artifact는 `6034066e-ffd9-48f0-ba63-d8e5e7b1e75c/a609974c7900/error.json`이다.
  임시 측정 값은 `program_check_phase=check_result_read`, `program_check_elapsed_ms=14062`,
  `cli_timeout_seconds=30.0`이다. journal seq `29`, active session은 위 6034…,
  lease는 `33527b10-7323-445a-9a80-5c5c2c8f3959`이며 여전히 `QUARANTINED`다.
  이 측정은 result-read 도달만 증명하며 실제 검사 명령의 실행을 증명하지는 않는다.
  controls artifact에서 caption `854680` 높이 1, pane `1312976`(32840)와 list `723156`
  (32849) 높이가 0이다. parser가 hidden/stale 결과를 pass로 수용하지 않는 것은 맞다.
  다음 작업은 동일 결과 dock에 인접한 live horizontal BCGPSlider를 한 번 끌어 실제
  결과 면적을 확보하는 정상 UI 동작이다. 고정 HWND/좌표·전체 레이아웃 초기화·추가
  사용자 권한 조건을 도입하지 않는다. 실제 결과를 읽은 뒤 임시 phase trace를 제거한다.
- Codex 오른쪽 표시 도구를 최근 다시 호출해도 `status=queued`였다. 내부 browser의
  visibility=true는 사용자 화면에 패널이 실제 펼쳐졌다는 증거가 아니다. 외부 브라우저나
  사용자 화면에 웹을 띄웠다고 말하지 않는다. 내부 탭에서 정상 웹 조작은 계속 가능하다.

## 2026-09-04 13:26 KST 경과 기록

- **현재 우선 상태:** run `e6259675-650a-48d7-b08a-7d726ef6bd4b`의 Host transaction
  `588463c0-11fe-4748-a8e1-0b2639df1a05`, MCP transaction
  `1f4710c8-797e-476a-85f2-aa972ff137c7`는 `QUARANTINED`다. fresh candidate
  `57570bfa-d2b7-5286-9a79-98f1afc518b4`와 승인·snapshot·backup·plan은 성공했다.
  apply는 `MNE_SELECTOR_VIEW_CHANGED`로 실패했다. CLI legacy-core의 공용 replacement
  함수가 fresh capture 뒤 IL 보기를 다시 전환하여 certified HWND를 무효화한 것이
  원인이다. MCP `af119fd`는 이 중복 전환을 제거하며 exact selector 검사는 유지한다.
  두 호출자(apply/rollback) 모두 직전에 IL 전환·fresh capture를 이미 수행한다.
- 정상 Web의 격리 복구를 같은 PID/HWND로 1회 실행했다. 이후 오류는 selector가 아니라
  `PROGRAM_CHECK_FRESH_MODAL_UNVERIFIED`이며 artifact는 session
  `f7c0fb84-538e-4876-a934-4281b45cd37b/eb2f91b7cb2e/error.json`이다. 검사가 끝나지 않아
  저장·독립 재열기와 완전한 rollback receipt는 없다. journal을 직접 바꾸거나 새 제안을
  먼저 적용하지 않는다. 실제 controls artifact에서 Program Check bar(32840)가
  `visible=true`이지만 711×0, 결과 ListView(32849)가 709×0이었다. MCP `c4a9c6b`는
  접힌 zero-area dock을 열린 결과로 오인하지 않도록 공용 pane/snapshot/result selector를
  맞춘다. 실제 펼쳐진 reused dock의 ListView text hash도 기존 state hash에 포함하고,
  실패 시 기존 CLI details의 전후 상태와 command backend를 private artifact에 보존한다.
- 위 수정과 Agent `788a16c`를 정상 Web에서 UAC 없이 재로드했다. Agent 변경은 Host가
  허용한 정확한 pre-apply 실패/만료 재제안에서 기존 verified compiler intent를 재사용하여
  모델 인수 재생성 지연을 제거한다. 현재 compiler 호출·새 후보·새 승인은 그대로이며,
  기존 typed schema validator를 사용한다. 실제 e625 checkpoint의 read-only 확인에서
  동일 program과 10단계 scenario가 투영됐다. 실제 재개 성능은 아직 확인하지 않았다.
- 재로드 뒤 같은 PID로 정상 복구를 1회 더 요청했지만 `explicit_mne_recovery_unproven`이
  유지됐다. journal event 12는 소유권 이전이며 새 session은
  `d53272be-da32-4880-8d9d-4cddfa865010`, recovery action은 `rollback_required`다.
  이 session의 artifact 디렉터리는 생성되지 않았다. 따라서 이를 이전 ProgramCheck
  오류의 재발로 단정하지 않는다. 현재는 native 호출 전 복구 연결/자료 로딩 실패를
  조사했다. Host `60cc821`은 기존 bounded MCP reject type/message를 private Host log에
  보존하고 public reconciliation에는 기존 structured type/code만 전달한다. 이 변경 뒤
  1회 정상 복구에서는 `mne_recovery_identity_unavailable`가 발생했다. 원인은 initial
  authenticated journal lookup이 이전 lease `45ff…`와 정상 handoff lease `48030…`를
  비교해 거부한 것이다. 실제 DB+journal pure validation으로 literal 예외를 재현했다.
  Host `0b2328b`는 해당 lookup에서 기존 `allow_lease_rotation` 경로를 사용한다. 다른
  transaction/plan/project/hash/PID 검사는 유지한다.
- 위 lease 수정을 반영한 실제 정상 복구는 bootstrap/attach/status를 통과했다. 이후
  MCP의 정확한 거부는 `mutation requires a same-COM-object selected-PID project proof`다.
  `result.reconciliation.rollback_rejection`은 `recovery_identity_requires_same_com_proof`를
  기록한다. 실패 이후의 최신 복구 진단은 `result.reconciliation`에서 읽는다. 기존
  `verification.recovery` 14개 키는 과거 기준본이며, 최신 진단이 소실된 것이 아니다.
  MCP `7065bbe`는 기존 durable proof를 exact quarantined MNE 원복에 연결했다. private
  bundle·전체 LD catalog·baseline/target MNE readback을 결속하고 원복 executor에서도
  같은 proof를 사용한다. 일반 CLI/COM 원복 경로와 apply·물리 PLC 권한은 바꾸지 않는다.
  단순 operation allowlist 추가만으로 해결하지 않았다.
- 위 변경을 정상 Web에서 UAC 없이 반영한 뒤 같은 PID `12492`로 복구를 한 번 실행했다.
  연결 proof와 executor identity를 실제로 통과하고 Program Check까지 도달했다. 현재
  정확한 오류는 `PROGRAM_CHECK_DOCK_BASELINE_UNVERIFIED`이며 artifact는 session
  `de00132c-585c-4ebb-b404-566a25bcf7fa/c43ec15e62a7/error.json`이다. journal event는
  `15`, lease는 `ee8b04a3-469e-4ca6-831e-4ce709c158b2`이고 여전히 `QUARANTINED`다.
  실제 캡처에는 기존 11개 MNE 명령이 보인다. Program Check의 visible pane 목록과
  zero-area를 제외한 dock baseline 사이의 불일치를 조사한다. 저장·독립 재열기·M30
  적용 성공은 아직 없다. 새 native 복구는 원인이 수정된 뒤 한 번만 수행한다.
- MCP `42dbf09`는 제목 caption 높이 같은 예외값 대신 실제 결과 목록이 보이는 strict
  dock snapshot을 모든 pane selector가 공유하도록 했다. MSAA 후보도 visible 결과
  ListView 하나를 요구한다. 이 변경을 반영한 정상 복구는 baseline 판정을 통과했지만
  `PROGRAM_CHECK_FRESH_MODAL_UNVERIFIED`로 종료됐다. `before_pane_handles=[]`,
  `command_backend=resource-proven-mainres-wm-command`, `reused_dock=null`이며 최신
  artifact는 `6c1747f0-4a1e-4387-a397-0a6857337ab2/035527f59e2e/error.json`이다.
  현재 journal event는 `17`, lease는 `31f8470d-df0c-41cb-aebe-50a97f10db08`다. 이 결과는
  원복/저장 성공이 아니다. 메인 창에 WM_COMMAND 876만 보내는 경로 대신 이미 구현된
  exact primary-toolbar Program Check 버튼 클릭을 실제 실행 경로에 연결하는 중이다.
  다른 pane에 해당하는 active-MDI 4957을 추측으로 대체하지 않는다.
- MCP `211cc16`은 MainRes 876 mapping을 보존하면서 기존 primary-toolbar의 live UIA /
  runtime button 교차점을 한 번 클릭하도록 연결했다. 빈 native pane title은 strict
  결과 목록 snapshot 근거로 처리한다. CLI `8e69032`는 이 semantic backend에 잘못된
  `visualMenu` 필드를 붙이지 않는다. 실제 실행은 버튼 전 optional project-root search의
  `XG5000_PROJECT_SEARCH_RESULT_UNVERIFIED`로 실패했다. query readback은 맞지만
  semantic row가 0개였고 session은 `64db3c9b-aadc-47c8-b6c0-33b5b0aa7a4d`다.
- MCP `bfb7768`은 정상 검사에서 그 진단용 검색과 historical toolbar 좌표 조건을
  제거했다. 이미 열린 exact program과 unique live toolbar 59435, enabled UIA /
  runtime 876 교차점·창 소유권 검사는 유지한다. 그 뒤 정상 복구는 `CLITimeout`으로
  종료됐다. 최신 artifact는 `83a935a3-5bb6-42ea-bf8e-b497c868b689/c5c8f0073308/error.json`,
  lease는 `45291269-11e3-4e00-ac67-5d85cb227f93`다. 실제 LD 캡처는 기존 프로그램이며
  검사 결과 pane은 없다. primary helper가 아직 모든 툴바를 조사하는 diagnostic inventory를
  호출하고 있으므로 exact toolbar 한 개만 조회하는 실행 경로로 축소 중이다. timeout을
  늘리거나 같은 코드를 다시 실행하지 않는다. 저장·재열기·XG-SIM·Keep 성공은 아직 없다.
- Platform `e34ac97` / Agent `efc42a9`는 검증된 격리 원복 결과
  `ROLLED_BACK / explicit_mne_recovery_rolled_back`에서도 같은 대화의 fresh compiler와
  새 승인 제안을 허용한다. 기존 내부 M 범위 검사를 재사용하며 일반 사용자의 Revert나
  불확실한 원복은 포함하지 않는다. 이 경로는 아직 실제 원복 성공 이후 검증 전이다.

- 현재 대화는 `868e0126-a603-481a-9262-4ecef0023e9d`, workspace는
  `fccdf3ec-34e4-4dae-9009-1a8c1e58497d`다. Host PID `8212`, XG5000 PID `12492` /
  HWND `1901812`를 유지한다. 아래 경과 기록의 이전 PID·실패 run으로 돌아가지 않는다.
- Platform runtime `251abbb`, Web `6113faf`, MCP `bfb7768`, Agent `efc42a9`, CLI `8e69032`까지
  정상 웹의 UAC 없는 Host 재로드로 반영했다. 앞선 Platform `2dcb3b6` / Agent `503f2f1`는
  만료된 후보의 재개 허용 정보가 Host serializer와 Agent sidecar decoder 양쪽에서
  누락되던 문제를 수정했다. 앞선 `bf1295a1-b8e4-426e-873f-5f7abf836e6f`는 잘못된
  planner keyword 호출, `fe9e8f07-fdc6-4f01-92e1-6d1f69882088`는 누락된 허용 정보로
  실패한 별도 실행이다. 이 오류들을 권한 거절이나 native 적용 실패로 분류하지 않는다.
- 수정 뒤 실제 run `f50c184f-819b-44ed-bde6-f5e29996307d`에서 compiler(event `3749`,
  11초), 새 제안(event `3755`/`3756`)이 성공했다. 현재 run의 후보 근거는
  `7ddb7db0-b95e-5583-b24e-9ba6e1c437ac`이며 기존 11개 명령을 보존하는 13개 명령과
  START/STOP만 조작하는 10단계 XG-SIM profile이 실제 웹에 표시됐다.
- 새 transaction `166a93cd-cfb4-4f3d-9cd5-ab6d47b9f952`의 plan digest는
  `217c413011902e5ae055f904b8492430f88b5d1717c72322e904ee7c368f8141`다. 웹의 승인
  버튼으로 10:26:04에 APPROVED(event `3764`), 실행 버튼으로 10:26:32에
  EXECUTING(event `3766`)에 진입했다. 이어 event `3768`에서 QUARANTINED가 됐다.
  오류는 `native_change_outcome_uncertain`, 상세는
  `MNE preparation failed and a no-mutation abort was not proven`이다.
  저장된 recovery 진단은 `preparation_error_kind=ChangeExecutionPreconditionError`,
  `abort_error_kind=MCPToolError`, `known_mcp_transaction_id=null`이다. 이 기록만으로
  native 변경 또는 원복 성공을 단정하지 않는다. 정확한 준비 실패와 복구 경계를 조사한다.
- 준비 실패 원인은 MCP snapshot program receipt의 `canonical_sha256` 누락이었다.
  MCP `bbfaf48`는 공용 LD receipt/catalog producer에 기존 canonicalizer의 결과를
  포함한다. 실제 보존된 snapshot은 승인된 canonical digest `f3a044…`와 일치한다.
  Platform `5cf11f4`는 snapshot이 생성한 transaction UUID를 먼저 검증·보존하여
  뒤의 의미 비교 실패에서도 정확한 journal을 중단할 수 있게 했다. Web `29ffd65`는
  전체 구조 변경 종류에 공용 plan validator를 사용해 복구 gate 누락·중복을 없앴다.
  위 수정은 UAC 없는 정상 Host 재로드로 반영했다.
- 10:37에 정상 Web 복구 selector에서 기존 PID/HWND를 선택해 한 번 복구를 실행했다.
  MCP transaction `167a51a8-d243-47a1-8c21-a43bd656aef9` lookup, recovery attach,
  status 읽기는 성공했다. 상태는 `SNAPSHOT_CREATED`, `mutation_possible=false`,
  `recovery_action=abort_safe`이며 새 세션은 `449c3c40-eec0-40a3-b364-9cd9a1c812c7`다.
  이후 `change.abort`의 `MCPToolError`로 정상 중단은 실패했고 Host 오류는
  `mne_native_abort_unproven`으로 바뀌었다. 다음 Host `4eb0be6` 실행은 같은 거부를
  `SessionError/recovery_identity_requires_same_com_proof`로 정확히 기록했다.
- 실제 원인은 journal만 닫는 `change.abort`에 native 수정용 same-COM 검사를 적용한
  분류 오류였다. MCP `c386b52`는 이 live mutation 검사와 앞서 추가한 abort 전용
  durable/native-file 예외들을 제거했다. 인증된 세션·요청 PID/HWND, journal의 동일
  session/target/path/hash, 정확한 pre-apply 상태, 미소비 변경 승인, lease와 durable
  no-mutation evidence 검사는 유지한다. 실제 rollback/save의 권한 검사와 증명은 유지한다.
- 10:54:53의 정상 Web 복구에서 MCP `167a51a8…`가 `ABORTED_NO_MUTATION`으로
  종료됐고 lease는 `released`, `mutation_possible=false`다. 새 복구 세션은
  `a7b19b9e-a1e8-478e-82d3-5aec68ef1ba0`다. Host transaction `166a93cd…`는
  `ROLLED_BACK / mne_recovery_aborted_before_mutation`으로 확정됐다. 이는 실제 원복
  편집·저장을 수행했다는 뜻이 아니다. `native_mutation_performed=false`,
  `executed_checks=[]`이며 실제 적용·검사·저장·재열기·XG-SIM은 미실행이다.
  Web `364e4ed`는 이 결과를 원복·저장 성공과 구분하고, 실제 no-mutation 종료 근거로
  acknowledgement를 허용한다. 정상 Web 버튼으로 결과 확인까지 성공했다.
  Host `031a52a`도 이 결과에 존재할 수 없는 저장·독립 재열기 증명을 요구하지 않고
  같은 PID/경로/파일 해시의 새 분석 연결을 보존한다. 준비 작업 종료는 재실행하지 않는다.
- 새 엔지니어링 대화 `22465379-be2c-4ec5-b6f0-239ad5082752`가 event `3780`에서
  생성됐다. 기존 작업공간의 `기존 XG5000 창에 이 작업 연결`로 PID `12492`를 연결하고
  오프라인 후보·XG-SIM 범위를 정상 선택했다. 그런데 연결/후보 승격 과정 중 Web이
  새 대화 대신 이전 `ce6523b3…`로 돌아갔다. 새 사용자 요청의 실제 run
  `c543a380-3c11-40d5-8d2d-a12fd59382ea`가 이전 대화에 연결된 것을 확인하고 정상 취소
  버튼으로 중단했다(event `3791`, CANCELLED). 이 run은 progress만 있으며 tool 실행이
  없다. Host `6098968`은 이미 대화가 귀속된 과거 handoff를 진행 중 작업으로 다시
  광고하지 않는다. Web `6113faf`는 현재 명시 선택을 우선하며, 대상 연결 전 생성된 빈
  대화는 기존 create API로 target-bound 대화에 승격한다. 예전 label-specific 예외는
  제거했다. 원래 빈 대화와 기존 미션 기록은 삭제하거나 재활성화하지 않는다.
- 위 수정을 반영한 실제 웹에서 `22465379…`를 작업 기록으로 선택한 뒤 기존 PID `12492`
  연결, 오프라인 후보·XG-SIM 선택을 진행했다. target-bound 대화 `868e0126…`가
  event `3793`에서 생성됐으며 선택이 과거 `ce6523b3…`로 바뀌지 않았다. 정상 메시지
  전송의 새 run은 `8952faca-4ee6-44be-b2ef-fa9debf1f6fd`(event `3798`)다. 기존
  3개 rung을 보존하고 END 직전에 M00010→OUT M00030을 추가하며 10단계 XG-SIM을
  수행하는 제안을 요청했다. 실제 프로그램 목록(2.6초), LD 읽기(8.5초), 후보 컴파일
  (10초, event `3821`)과 새 제안(event `3827`)이 성공했다. 후보는
  `54abd1aa-3e14-5a66-939c-8c14fc2cb55d`이고 UI에서 11→13개 명령·END 앞 추가·10개
  시나리오를 검토한 뒤 정상 승인/실행 버튼을 눌렀다.
- 새 Host transaction `39d30b4c-168a-43a4-8716-fc56f3d4aa7a`는 준비 중 실패했다.
  상태는 `FAILED / native_change_not_applied`, 오류는
  `MNE preparation failed; MCP proved an exact no-mutation abort`다. 기존 `result_json`
  은 `{}`여서 원래 preparation 오류를 전달하지 못한다. 정상 UI에서 결과 확인을
  기록했고 같은 `868e0126…` 대화는 유지된다. 아직 실제 적용·저장·독립 재열기·XG-SIM·
  Keep은 미실행이다. 실패 결과를 미션 완료로 처리하거나 같은 승인으로 재실행하지 않는다.
- 정확한 원인은 private snapshot receipt의 producer가 `canonical_sha256`를 포함하지만
  MCP binder/loader가 이전 6필드 allowlist를 요구한 불일치다. MCP journal
  `8eb1ceb7-12c8-4530-a42f-5861808e628e`는 유효한 snapshot
  `b3e49e2b-3934-48e2-8710-f2513dccc02a` 생성 뒤 binder에서 실패했고,
  `ABORTED_NO_MUTATION`으로 종료됐다. backup/plan/apply는 시작되지 않았다.
  MCP `abc3d1c`가 모든 해당 binder/loader 필드 규약과 SHA 검사를 맞췄다.
  Host `9e381cb` / Agent `4b7465d`는 기존 만료 후 fresh compile 경로를 정확한
  `FAILED/native_change_not_applied`에도 적용한다. 동일한 내부 M 디바이스·후보·profile
  범위 검사를 유지하고 Host의 `internal_mne_rung_reproposal_permitted`를 양쪽
  serializer에 전달한다. 이전 transaction/승인은 되살리지 않는다. Host는 새로운 준비
  실패에서 원문 대신 단계·오류 종류·정형 MCP 오류 코드만 결과에 보존한다.
- 위 묶음을 정상 Web의 UAC 없는 재로드로 반영한 뒤, 같은 PID/HWND를 연결하고
  오프라인 후보·XG-SIM을 다시 선택했다. 대화 `868e0126…`가 유지된 상태에서
  `결과 확인하고 계속`을 눌러 run `27fbf9b2-5fbf-4448-945b-7ff2c0be8a25`
  (event `3848`)를 시작했다. 이 재개는 실제 fresh compiler(event `3864`, 10초)와
  새 제안(event `3871`)에 성공했다. 후보 `f716d973-6349-58d0-b1f0-9d3311fc78d7`의
  같은 11→13개 명령·10개 시나리오를 검토하고 정상 UI로 승인·실행했다.
- Host transaction `4ee621a8-832c-4360-8d21-44d651136733`는 snapshot과 backup을
  통과(event `3883`/`3884`)한 뒤 plan에서 `FAILED/native_change_not_applied`가 됐다.
  MCP transaction `7b12731a-c07b-4e4e-a113-48d0cc6dbc9b`도 BACKUP_CREATED에서
  no-mutation abort로 종료됐다. 실제 적용·저장·재열기는 미실행이다. Host의 안전한
  진단은 `verification.preparation.stage=plan`, `mcp_error_type=SessionError`다.
  오류 artifact는 `MNE replacement public plan fields are invalid`를 기록했다.
- 원인은 plan producer가 내보내는 `rollback_ld_canonical_sha256`를 공용 plan digest
  validator가 누락한 것이다. MCP `174ede3`가 해당 필드를 정확한 규약과 SHA 검사에
  포함한다. producer와 공용 validator의 16필드 집합, bind/load/승인/apply/rollback의
  공용 validator 사용, Host plan/apply/verify/reopen 수신 규약을 소스로 확인했다.
  추가 Host 변경은 없다. 실패 receipt는 정상 UI에서 확인했고 같은 대화가 유지된다.
- MCP `174ede3`를 UAC 없는 재로드로 반영하고 동일 PID/HWND를 다시 연결한 뒤,
  같은 대화의 `결과 확인하고 계속`으로 run
  `f1457eaf-2b84-4e12-9fe6-c6f66f50a5f7`을 시작했다. 새 후보·제안·승인 뒤 snapshot과
  backup까지 성공했지만 private plan 파일 저장에서 실패했다(event `3922`). Host
  `7344646a-399c-4557-b843-845a2e2bd4e2`는 `FAILED/native_change_not_applied`, MCP
  `f332971b-2864-421d-8849-294d09fb55df`는 `ABORTED_NO_MUTATION`이다. 오류는
  `unsupported private transaction artifact`이며 LD 적용은 시작하지 않았다.
  `adapter-binding.json`을 plan producer와 loader가 요구하지만 private artifact writer의
  파일명 목록에 빠져 있었다. MCP `f655f85`는 이 누락을 맞췄다. producer/writer/loader의
  4개 파일명 연결을 확인했고, 정상 Web 결과 확인(event `3924`) 뒤 UAC 없이 반영했다.
  같은 PID/HWND와 대화에 연결하고 XG-SIM 범위를 선택한 뒤 12:02에 정상 재개한 run은
  `e6259675-650a-48d7-b08a-7d726ef6bd4b`(event `3931`)다. 이후 실패·복구 상태는 위의
  현재 우선 상태를 따른다.
  실제 LD 적용·저장·독립 재열기·XG-SIM·Keep은 여전히 미실행이다.
  현재 모델은 compiler 인수 생성에 모델 호출을 사용해 앞선 재개에서 약 200초가
  걸렸다. 이는 재개 권한 누락이 아니며, 속도 최적화 코드는 아직 추가하지 않았다.
- MCP `8586ffe`는 MNE catalog guard에 남아 있던 target raw LD SHA 동등 비교를
  기존 필수 canonical SHA 동등 비교로 통일한다. raw SHA는 출처 값으로 보존하고,
  canonical 필드 누락/형식 검사는 유지한다. 이는 점검 중 찾은 잔여 비교 수정이며
  이 gate에서 실제 실패가 재현됐다는 뜻은 아니다.
- Platform `cd14e0e`는 Web Keep 판정을 고정 3단계 대신 승인된 scenario의 각 관측값에
  맞췄다. 실제 시뮬레이션 receipt는 아직 없어 실사용 성공 판정 전이다. 실행 중인
  작업·복구 handoff 우선순위는 유지한다.
- 작업 웹은 Codex 내부 `http://127.0.0.1:8767/`의 탭 5다. 선택 탭과 visible 상태는
  확인했지만 Codex 패널 열기는 현재 작업으로 이동한 뒤에도 `queued`였다. 별도
  Edge/Chrome 창이나 사용자 화면에 웹이 표시됐다고 보고하지 않는다.

## 2026-09-04 LD 변경 경과 기록

아래는 오류 원인과 수정 근거를 보존한 이력이다. 현재 상태는 위 이어갈 지점을 우선한다.

- 현재 반영 묶음은 Platform `2d866d3` / `d700ad1`, MCP `39b2bb0` / `86d7be0`,
  CLI `dd64c94`다. native LD의 확인된 가변 헤더는 공용 canonicalizer로 처리하고,
  raw SHA는 실제 캡처의 출처 값으로 보존한다. 후보 재컴파일, baseline/snapshot,
  focus, 적용·복구·독립 재열기 및 화면 결과 판정이 canonical SHA를 의미 비교에 쓴다.
  후보 자체의 digest 검증과 실제 MNE 내용·프로젝트 경로/파일 binding은 유지한다.
  Platform 웹 production build까지 완료했다. 정적 점검 통과가 실사용 변경 성공을
  뜻하지는 않으며, 아래 실제 run 실패 기록과 아직 미실행인 변경 단계는 그대로다.
- 위 묶음을 Host PID `15168`로 UAC 없이 리로드하고 기존 XG5000 PID `12492`를
  연결했다. 최신 정상 UI 대화 `ce6523b3-f02c-47ae-a81e-0a83cb4f6b8d`의 run
  `e7c8310b-ffb2-42f1-9980-9d3ac0aaef5e`는 event `3500` 프로그램 목록과 `3505` LD
  읽기가 성공했다. 이후 event `3509`에서 `agent_output_contract`로 실패했다.
  안전 진단은 `engineering_analysis / model_retry / structured_output_contract`이며
  이전의 `continuation_contract`와 동일한 오류라고 추정하지 않는다. mission revision 1은
  active로 남았고 후보·제안·변경 실행은 없었다. 이 대화와 원본 프로젝트를 보존한다.
- 해당 실행의 저장된 실제 LD 근거로 실패 원인을 재현했다. 내부 lifecycle factory가
  만든 영어 `next_actions`가 한국어 응답 검사에 거부됐다. Agent `af884ce`는 초기·계속·
  compact 경로가 공유하는 factory에서 사용자 언어에 맞는 표시 문장을 생성하며,
  도구명·근거 ID는 코드 표기로 유지한다. 검증된 LD나 변경 승인 조건은 바꾸지 않았다.
  원래 로그에는 ModelRetry 원문이 없었으므로 이 진단은 원문 로그를 읽었다는 주장이 아니다.
  웹의 `MainProgram XG5000에서 열기`도 실제 성공했고 프로젝트 내용은 바뀌지 않았다.
- `af884ce` 반영 뒤 같은 대화의 run `bb63f579-4ced-4a87-8336-2a65e2d46631`는
  LD 읽기(event `3519`)와 다음 compiler 단계 체크포인트(event `3521`)를 실제 통과했다.
  이후 세 compiler 호출(event `3526`, `3536`, `3542`)은 모두 동일한 시나리오의 단계
  키 `label` 때문에 거부됐다. 실제 형식은 `{name, drive, observe}`인데 등록 설명은
  키 이름 없이 display label이라고만 했다. 입력 keyset과 관측값은 이미 작성되어
  있었으며 시나리오 누락이 아니다. 동일 호출 반복을 막으려고 정상 웹 취소 버튼으로
  run을 취소(event `3552`)했고 mission revision 5는 active로 보존했다. 적용·저장·
  재열기·시뮬레이션 완료는 여전히 아니다. MCP 입력 형식/오류 설명과 Agent에 노출되는
  해당 설명을 일치시킨 뒤 이 대화에서 재개한다.
- MCP `b566193`과 Agent `7bd280f`는 단계 키를 정확히 `name / drive / observe`로
  설명하고 missing/unexpected 필드를 오류에 명시한다. 이후 run
  `cf7a139d-01e3-4b37-bf3f-5bae2528533e`의 event `3565`는 Host의
  `MNE patch compiler candidate is invalid`로 실패했다. 원래 candidate가 보존되지
  않아 원문 validation error를 확인했다고 주장하지 않는다. 소스 대조에서 Host와 웹만
  name의 ASCII 식별자 제한이 남아 있는 불일치를 확인했고 Platform `5289d2a`로
  MCP와 동일한 nonblank/unique 표시 이름 규약으로 맞췄다. profile digest 검증은 유지한다.
  그 반영 뒤 run `d910ddc6-4c04-4324-860f-dd1e2274287d`는 도구 실행 전에 모델 제공자의
  HTTP 502로 실패했다. 이를 새 코드/권한 문제로 오인하지 말고 해당 run과 구분한다.
- 같은 대화에서 재개한 run `7b91c430-cf87-4741-abe6-de30b7e78f2e`는 모델이 응답했고
  compiler를 실행했으나 event `3589`에서 native `XG5000Error`의
  `MNE_ROW_COPY_AMBIGUOUS: Expected exactly one copied mnemonic row.`로 실패했다.
  오류를 변경 후보 성공으로 전환하거나 무작정 반복하지 않는다. 기존 PID `12492`의
  공용 MNE 행 선택/복사 경로를 진단 중이며 실제 변경 제안·저장·XG-SIM은 여전히 미완료다.
- 보존된 `artifacts/mcp-sessions/3eee3e09-4070-4c68-96f5-7e466f4470c9/2382d75be6ed/error.json`
  (위 development LOCALAPPDATA 아래)에 row 0 복사가 실제 11개 전체 명령과 END를
  반환한 기록이 있다. MCP `9da450e`는 빠른 Ctrl+A 캡처가 fallback할 때 남은 전체
  선택을 기존 checked first-row primitive로 해제하고 행 읽기를 시작한다. 별도 재시도나
  지연 반복은 추가하지 않았다. 실제 후속 web run으로 확인한다.
- `9da450e`만으로는 해결되지 않았다. run `940256e6-022a-4f56-884c-0543e76176a9`의
  event `3603`도 같은 native 오류로 실패했고, 보존 디렉터리
  `artifacts/mcp-sessions/786821c4-f529-4d9d-be99-ff0030c1aa32/aece6a87c3c5`의
  `error.json`에는 row 0에서 전체 11행을 복사한 기록이 있다. MCP editable source와
  실제 caller (`derive_supported_mne_adapter_binding -> capture_full_mne ->
  _discover_exact_mne_row_count -> _read_il_rows_with_view`)까지 확인했다.
  MCP `a557424`에서 행 수 탐색의 speculative Ctrl+A 빠른 분기를 제거해 행별 읽기와
  뒤의 명시적 전체 범위 캡처를 분리했다. 후속 실제 run으로 확인했으며, 앞선 첫 행
  클릭 수정만을 해결 완료로 보고하지 않는다.
- 같은 대화의 run `190c8d56-2aee-4843-b50a-d433bc67d0ae`에서 event `3626` compiler가
  10초 만에 성공했다. event `3627`의 후보 근거 `71641432-7f05-590b-91bc-6f3356e27a3b`는
  기존 11개 명령을 보존한 13개 명령과 유효한 XG-SIM 10단계 profile을 포함한다.
  START/STOP만 drive하고 M00010/M00030을 observe하며, START 유지 중 STOP 해제 시
  재기동 금지와 START 해제·재누름 후 재시작까지 포함한다. 한글 단계 이름도 Host에
  정상 수용됐다. mission revision 6은 이 후보로 `propose_offline_rung_patch`를 호출하는
  next action을 active 상태로 저장했다. 그러나 다음 분석 응답이 event `3635`에서
  `agent_output_contract / engineering_analysis / model_retry / structured_output_contract`로
  실패했다. 아직 proposal 도구 실행·승인 transaction·변경 적용은 없으며, 성공한 후보와
  같은 프로젝트를 보존한 채 이 후속 연결 오류를 진단한다. XG5000 PID는 `12492`다.
- 후속 코드 대조에서 제안 경로의 실제 불일치 두 가지를 확인했다. exact local tool
  호출이 JSON의 coverage 객체를 typed 모델로 변환하지 않았고, 컴파일된 부분 변경에도
  compiler 근거로는 충족할 수 없는 전체 프로그램용 10개 coverage 행/7개 confirmed
  영역을 요구했다. Agent `58f8e3b`는 기존 함수 스키마로 인수를 변환하고,
  Agent `f0ae432` / Platform `12a5fae`는 compiler 후보의 `target_identity`와
  `direct_impact`를 현재 후보 근거에 결속한다. 추가 coverage는 선택 사항이며 근거가
  필요하고, 전체 프로그램 교체의 10개 영역 계약은 바꾸지 않았다. 저장·재열기·rollback·
  XG-SIM 실행 성공을 compiler만으로 확인했다고 표시하지 않는다. 기존 native plan,
  원본 보호, 승인 및 ordered verification 경계도 유지한다.
  저장된 과거 후보는 재개 문맥에 보존되지만 새 run의 제안에는 현재 run의 compiler
  근거가 필요하므로 기존 lifecycle action에서 한 번 새로 컴파일한 뒤 제안한다.
  generic `3635` 로그만으로 원래 실패한 세부 항목을 확정한 것은 아니며, 잘못 의심했던
  Nemotron 실행 분기 변경은 반영하지 않았다. 알려진 실패는 고정된 안전 진단 코드로
  구분하도록 했으며 원문 모델 응답이나 자격 증명은 기록하지 않는다.
  코드 반영은 웹의 UAC 없는 재로드(Host PID `13272`)로 수행했고, 같은 대화의
  run `b190fdb9-5a5a-4c11-a7cc-d5ce3056fc31`를 event `3642`에서 실제 재개했다.
- 이 재개의 compiler도 event `3646`에서 10초 만에 성공했고, 새 근거
  `36e61049-0dff-5e50-9a7b-551fbe8d06c1`의 후보와 10단계 profile은 이전 성공 후보와
  동일했다. 다음 local proposal 단계 전후의 event `3656`은 다시 generic
  `agent_output_contract`로 실패했으며, 앞선 수정만으로 해결됐다고 보지 않는다.
  Agent `7f87137` / `876eeb8`는 현재 run의 검증된 rung 후보에서 승인 제안을 직접
  조립하여 두 번째 생성형 인수 작성 단계를 없앴다. 한국어 목표에는 한국어 설명과
  실제 후보 대상이 표시되고, 승인 후 적용·검사·저장·독립 재열기·요청한 XG-SIM의
  성공은 미래 완료 조건으로만 기재한다. 실행 권한이나 결과를 만들어내지 않는다.
  Platform `8587bf5` / `8b3e17d`는 기존 Agent 오류 frame 로그의 정확한 production
  timestamp/logger prefix 및 함수명/행 번호만 Host에 전달한다. 임의 stderr나 모델
  원문은 계속 제외한다. 이 묶음을 UAC 없이 Host PID `10404`에 반영했다.
- 다음 run `d782f5c4-d1bd-4f2f-ab72-891cb8457cad`에서 compiler(event `3669`) 뒤
  실제 `change.propose_offline` 호출(event `3675`)까지 진행했으나 Host에서 거부됐다.
  실제 plan의 `baselineLdCanonicalSha256`가 raw LD payload 차단 규칙에도 걸린 것이
  원인이며 Platform `cfc928c`는 기존 raw SHA와 같이 canonical SHA identity만 허용한다.
  raw LD/MNE 내용 공개나 승인 조건 완화는 없다. UAC 없는 reload 후 Host PID는 `14820`이다.
- run `5c86aa46-398e-49b2-80cc-991d2e838ec5`의 compiler(event `3691`)와
  `change.propose_offline`(event `3699`)가 실제 성공했다. event `3698`에서 transaction
  `1dc65e00-9f9b-4ca3-9da1-29523c141538`이 생성됐고 mission revision 10은 정확히
  `waiting_for_approval / needs_human_approval`에 머문다. 후보만으로 완료 처리하지 않았다.
  plan digest는 `09665f27bb2ea9b18174188565a739aa4f36505e2bda2e3e13b1e0c9dd8fd8dc`이며
  승인 만료 시각은 2026-09-04 09:39:33 KST다. DB/만료/승인 권한을 수동 수정하지 않는다.
  아직 apply/save/fresh reopen/XG-SIM/Keep 실행 성공은 아니다.
- 승인 제안을 웹에 표시하는 경계에서 inner XG-SIM profile의 native snake_case를
  camelCase로 기대하던 불일치를 확인했다. Platform `0444e88`는 outer `simulationProfile`을
  유지하고 digest에 포함된 inner native key를 contracts/parser/panel 전부에서 보존한다.
  profile/candidate/plan의 실제 digest나 저장된 데이터를 재작성하지 않는다. production
  build만으로 해결됐다고 보지 않았으며 실제 browser-only reload로 남은 오류를 추적했다.
  Platform `3b13ee3`는 bootstrap 성공 뒤 응답 계약 파싱 실패를 Host 연결 장애로 표시하지 않는다.
  `ee31b84`는 END 삽입 분기 전에 END anchor를 거부하던 모순과 Host의 정확한 inert alias
  기본값을 처리한다. `f5b91ff`는 private payload guard가 baseline MNE/LD/canonical SHA까지
  거부하던 중복 규칙을 수정했다. 실제 event `3698`의 transaction을 production parser에
  직접 전달한 읽기 전용 진단에서 전체 transaction/plan/list가 통과했고, 웹에서도 승인
  카드·11→13 명령·M00010→OUT M00030·10단계 시나리오가 실제로 표시됐다.
  임시 진단 파일은 제거했고 테스트 파일/스위트는 추가·실행하지 않았다.
- 표시 후에도 별도 승인 버튼 gate에 전체 프로그램용 10개 coverage 조건이 남아 있었다.
  Platform `1842b27`는 compiler candidate의 공용 계약 검사 결과를 재사용하고 필수 2개
  coverage 및 실제 XG-SIM 단계 수를 표시한다. 전체 프로그램 교체의 10개 조건은 유지한다.
  실제 웹 승인 버튼이 활성화된 것을 확인했다. 09:51에 그 버튼을 사용했으나 이미
  유효 시간이 지나 서버가 event `3706`/`3707`에서 EXPIRED를 확정했다. transaction의
  execution/verification 시작 시각은 모두 null이며 적용·저장·재열기·시뮬레이션은 없었다.
  웹의 `결과 확인하고 계속`은 활성화됐다. 기존 mission에서 만료 후 새 baseline compiler
  근거로 다시 제안하는 연결과 10단계 시나리오 완료 receipt의 Web Keep 판정을 수정 중이다.
  Host/XG5000은 아직 PID `14820`/`12492`를 유지하며 보호 원본 SHA도 동일하다.
- 작업 중인 웹은 Codex 내부의 `http://127.0.0.1:8767/` 인증 탭이다. 별도 Edge/Chrome
  창은 확인하지 못했다. 초기 패널 표시 요청은 `queued`였고 선택 탭과 작업 탭도 달랐다.
  07:38에 정상 Launcher `--web-url` handoff로 기존 선택 탭 5를 인증했고, 작업 탭도 5로
  통일한 뒤 `browserVisible=true`와 선택 탭 일치를 확인했다. 당시 웹 화면 캡처도 제공했다.
- 기존 XG5000 PID `12492` / HWND `1901812`, workspace
  `fccdf3ec-34e4-4dae-9009-1a8c1e58497d`를 유지한다. 새 XG5000을 실행하거나 기존 창을
  종료하지 않았다. 코드 반영은 실행 중인 Host의 UAC 없는 재로드로 수행했다.
- 기존 3개 rung / 11개 명령을 보존하고 마지막 END 앞에 `M00010 NO -> OUT M00030`을
  추가한 13개 명령의 후보 컴파일은 실제 성공했다. 그러나 기존 대화
  `b155b206-04a8-4c60-821e-d84acd5cb2df`의 run
  `3970cb1b-d192-481c-8225-b397496a22b5`는 event `3435`의 후보만으로 revision 4를
  `completed`로 잘못 저장했다. 이 기록은 실제 변경 완료가 아니다. 승인 transaction,
  변경 적용·추가 rung 저장·독립 재열기·XG-SIM 실행은 아직 없었다.
- 해당 실제 compiler 인수에는 `simulation_scenario`가 있었다. Host/MCP 전달 누락이
  아니라 빈 drive 및 단계별로 다른 drive keyset을 가진 잘못된 시나리오였으며,
  compiler가 `ValueError`를 삼켜 `simulation_profile: null` 후보를 성공 반환한 것이
  원인이었다. Agent `6bf77b7`은 동일 외부 입력 keyset과 held-input 순서를 명시하고,
  MCP `8dbafb5` / `2d32d26`은 명시 시나리오의 오류를 숨기지 않는다. 시나리오를
  요청하지 않은 static/legacy 경로의 optional 동작은 유지한다.
- Agent `066e87c` / `6bf77b7`은 현재 후보만으로 완료하지 않고 실제 Host proposal과
  pending authority로 이어지게 한다. 실행 이후 terminal Host outcome은 오래된 후보보다
  우선한다. 한 번의 잘못된 완료 기록을 위한 별도 복구 예외는 `f7c70a5`로 제거했다.
  Host/UI에 복구 특례는 추가하지 않았다.
- 최근 대화는 정상 UI의 새 엔지니어링 작업으로 만든
  `62c20e54-b378-43fd-9916-ae6d19047785`다. 기존 프로젝트와 PID는 유지하고 원래 목표를
  재전달했다. run `bce62cf3-b7b7-424c-9670-3ab7f085cd48`에서 프로그램 목록(event `3454`),
  LD 읽기(event `3459`)가 성공했으나 이후 `continuation_contract`로 실패했다.
  정상 UI 재개의 run `e3b5ca1c-4218-4dfe-81af-009d51fbffd4`는 compiler event `3481`의
  시뮬레이션 이름 ValueError 후에도 revision 4를 잘못 완료 처리했다. 추가 rung 적용이나
  XG-SIM 완료가 아니다. 대화 기록은 보존한다.
- Agent `3514d89`는 LD/최신 compiler 오류→정정 컴파일→후보→실제 제안의 판단을 한
  함수로 공유한다. pending authority와 terminal Host outcome이 다음 단계를 결정한다.
  Host `5fb8af3`은 offline candidate 작업의 terminal outcome 없는 완료를 거부한다.
  MCP `a306270`은 화면 표시용 simulation phase label의 불필요한 영문 식별자 제한을
  compiler와 native simulator에서 함께 제거했다. 이 묶음은 코드 리로드했으나 아직
  이후 새 실사용 run을 통과한 결과는 없다.
- 해당 리로드 때 기존 인증 탭은 일시적 연결 거부 오류 페이지로 전환됐고, 이후 조작은
  도구의 `data:` 오류 페이지 URL 제한에 막혔다. 로컬 listener와 기존 XG5000은 살아 있다.
  그 오류 탭이 아니라 이미 열린 정상 HTTP 탭 5를 위 Launcher 경로로 인증해 복구했다.
  Launcher observation `2419c537-3591-487e-b5bb-75625742ecc7`는
  `elevation_relaunch_requested=false`, `elevated_host_retention_verified=true`다.
  localhost 자체가 금지됐다거나 새 UAC가 필요했다고 보고하지 않는다.
- MCP `7a9778c`는 END 인수에 `operand: null`을 명시한다. Agent `28cf0e4`와 Host
  `7599765` / `d3ac932`는 읽기 전용 후보 컴파일의 ValueError를 성공 근거가 아닌 오류
  피드백으로 전달한다. 권한·통신 오류나 변경 실행을 자동 재시도하는 기능은 아니다.

## 2026-09-04 이전 프로젝트 재열기 체크포인트

- 저장 작업 `4849692c-deb3-40e5-a500-3ce74080d9d5`를 계속 사용한다. 재생성하지 않는다.
  LD 생성·Program Check·저장과 독립 재열기·재읽기가 완료됐다. 아래 과거 실패 기록은
  현재 상태가 아니라 복구 경과다.
- MCP `9402f3a`/`4fb0e5b`, Host `bf6fb1d`는 saved-build inventory 전에 해당 보호
  workspace path/hash와 일치하는 dead-CLI/unverified lease만 정리한다. 살아 있는
  일반 XG5000 창을 종료하지 않으며, `xg5000.instances`는 읽기 전용으로 유지한다.
- MCP `ad76218`은 durable exact child가 있는 timeout 정리에서 파일 전체 hash와
  전체 프로세스 재탐색을 제거했다. 75초 CLI timeout 뒤 정상 오류 반환·exact cleanup이
  실제 확인됐고, 이전처럼 Host의 140초 timeout까지 넘어가 MCP가 끊기지 않았다.
- 75초 deadline 복구 확인 시도는 stale PID 540을 정리하고 PID 4872 하나를 시작했다.
  lease `bd8b87f6a58e4a178e9bb45d226efaea`의 마지막 단계는
  `official_open_ui_dialog_free_check_completed` / `dialog_free=true`다.
  파일 열기는 75초 timeout으로 실패했고 `launch_cleanup=terminated`로 반환됐다.
- 실제 Open 정체 스택은 `msaa_hit_test._location -> IAccessible.accLocation`이었다.
  MCP `523137b`는 빈 BCG menu-bar 경로를 기존 검증된 MainRes Open 명령으로 연결해
  MSAA 위치 탐색을 제거했다. 실제 웹 재열기로 PID 12492/HWND 1901812에
  `웹 LD 자기유지 20260903 - XG5000`이 열렸고 응답 중이다. 이 성공 창을 닫거나 다시 만들지 않는다.
- Host `80f9d30`은 official Open 성공 영수증의 assurance 계약을 정렬했다. MCP `027e49f`는
  Copy 전 clipboard sequence 0을 기준값으로 허용하되 Copy 뒤 sequence 변경과 exact XG
  clipboard owner 확인을 유지한다. 같은 PID 12492의 실제 재개가 성공해 웹이
  `프로젝트 생성 완료`로 전환됐다: 3개 rung, 11개 mnemonic 행 재확인, LD 왕복,
  Program Check, 파일 무변경, 현재 분석 세션 연결이 모두 확인됐다.
- 분석 연결은 `controlled_launch_read_only`이며 쓰기까지 완료됐다는 뜻이 아니다.
  Platform `b815a2c`는 성공 결과에 현재 연결된 프로젝트로 작업을 시작하는 명시적 버튼을
  추가했다. 최신 웹 reload 후 engineering 대화 `0828547d-ccc2-4797-9e3c-1b66510008a6`와
  현재 PID 12492 대상이 표시됐고, UI에서 MainProgram 실제 LD 읽기·정지 우선 자기유지
  분석을 요청했다. Run `0748e00a-abb8-41e5-9fc9-7a705a8505a2`는 초기 프로그램 목록 도구
  호출에서 `tool_error`로 실패했다(645ms). MCP `8fe94df`는 이미 exact identity를 확인한
  `saved_build_reopen_readback` 세션에 일반 읽기 분석을 허용했다. 쓰기 권한은 추가하지
  않는다. 이 수정 뒤 실제 에이전트 분석 성공은 아직 확인 전이다.
  UAC 없는 Host 재로딩으로 session이 사라진 뒤 generic attach는 저장된 생성 작업의
  signed read-only proof를 복원하지 못했다. 같은 기존 창을 이 증명으로 재연결하는
  경로는 Host `b30562c`로 연결했다. 최초 실제 클릭은 `request` 주입 누락으로 HTTP 500이
  발생했고 `e995800`에서 수정했다. 실제 웹의 일반 `분석 세션 연결`로 같은 PID 12492 /
  HWND 1901812의 read-ready 재연결에 성공했다. 추가 UAC나 새 XG5000 실행은 없었다.
  `이 작업 계속하기`로 새 프로세스를 열거나 생성 작업을 재실행하지 않는다.
  대화 `ab8f9615-922b-44f9-8703-3f079a476d01` / run
  `75d6381c-7e4b-40cf-b88d-f95a123230aa`에서 같은 실제 LD 읽기 질문을 재전송했다.
  프로그램 목록은 2.4초, 실제 래더 읽기는 11초에 완료됐다. Run은 완료로 표시됐지만
  답변은 실패다: 모델이 `AND NOT M00020`의 부정과 `LOAD NOT M00001`의 부정을 잘못
  해석하고 일반 `OUT M00020`을 SET으로 설명해 재기동 조건을 반대로 답했다.
  근거 artifact `95f95894-73c9-5a9a-9be7-370387e5487f`의 실제 표현과 모델 입력을
  확인한 결과 `semantic_rungs`가 `NOT` wrapper 안에 `DEVICE(contact:NC)`를 다시 넣어
  `NOT NOT M00020` / `NOT NOT M00001`로 전달했다. native row의 NC/OUT 디코딩 자체는
  맞다. MCP `20f4ae7`/`0d86917`, Agent `81b3340`에서 polarity-once semantic 표현과
  OUT의 scan-assignment 의미를 정렬했다. NC만 정규화하며 P_EDGE/N_EDGE는 보존한다.
  이 답변을 성공 근거로 쓰지 않는다.
- 그다음 run `d74c7525-63b7-4bad-92f0-cad058dd443c`는 CLI vendor의 이전 semantic
  projection과 MCP의 새 projection 전체를 비교하던 결함으로 읽기에 실패했다.
  MCP `f286dfb`는 private payload/hash/raw decoded rows를 strict 비교한 뒤 모델용 의미
  표현을 MCP에서 재생성한다. CLI의 독립 파생 projection은 검증 대상 원문도 모델 입력도 아니다.
  CLI `1d58d44`는 standalone 사용자용 documented projection도 같은 NC/OUT 의미로 고쳤다.
  대화 `63aebc28-88b2-4bb4-86af-932d3769678c`의 `작업 계속 실행`으로 재개한 run
  `d280496f-00d2-4a8f-918e-30378a68e101`은 실제 LD 읽기 8.5초 완료 뒤 답변까지 완료했다.
  새 근거 `cf0708c7-de22-50b1-ad05-d6ef22511834`의 실제 modelContext는 다음과 같다.
  - `M00000 AND NOT M00020` → `OUT M00021`, assignment=scan
  - `NOT M00001 AND (M00021 OR M00010)` → `OUT M00010`, assignment=scan
  - `M00000` → `OUT M00020`, assignment=scan
  이전 artifact나 이중 부정 projection은 재사용되지 않았다. 최종 답변의 START 유지 중
  STOP 해제만으로 재기동하지 않고 START 재상승이 필요하다는 결론은 맞다. 다만 M00020을
  `정지래치`라고 부른 설명과 `동일/다음 스캔`이라는 모호한 순서 표현은 부정확하다.
  M00020은 마지막 rung에서 갱신하는 이전 START 상태이며 실제 SET/RST 코일이 아니다.
  따라서 native 읽기·핵심 논리 판단 복구와 모델 설명 품질을 구분한다. 오프라인 편집,
  XG-SIM 실행 및 전체 제품 완료를 이 read-only 결과로 주장하지 않는다.
- CLI `e164ec0`은 fast program catalog가 사용하는 vendored window enumeration에도
  기존 bounded caption reader를 공유한다. 모든 top-level 창을 읽다가 무기한 막히는
  경로를 제거했으며, 전체 vendor 동기화나 테스트 코드 추가는 하지 않았다.
  이미 성공한 파일 열기/창 준비/UAC 단계로 돌아가지 않는다.
- 일회성 stack 수집 코드는 `d2c387f`로 제거했고 trace 원인 증거는 private lease 폴더에
  보존했다. CLI-only source 변경은 다음 CLI 실행에 바로 반영된다. Host 코드 변경만
  웹의 `코드 다시 불러오기 (UAC 없음)`으로 반영한다.

## 2026-09-04 실제 웹 LD 생성 — 저장 완료, 재열기 미완료

- 개발 웹을 정상 launcher `-WebUrl`로 열고 기존 계획
  `92e018bc-5228-4ec3-b343-2ef46fcff1b4`와 검토 완료 상태를 복원했다. 계획을 재생성하지 않았다.
- 웹의 생성 요청은 한 번이다. native operation
  `4849692c-deb3-40e5-a500-3ce74080d9d5`는 XGK-CPUH, `MainProgram`, LD 3개 rung을
  XG5000 PID `2432`/HWND `67174`에 생성했다. 11개 mnemonic 행 exact readback, LD 변환,
  Program Check, 저장이 확인됐고 saved SHA-256은
  `16248471e9a7131623b65ea6ad7b43f59fa6a78dcccf462d88687fbc8442f13e`다.
- 원본은 `artifacts/web-ld-projects/웹 LD 자기유지 20260903-4849692cdeb3/웹 LD 자기유지 20260903.xgwx`다.
  보호 작업 사본 캡처는 성공했지만 별도 PID `8980`의 재열기가 실패해 `workspaceBound=false`다.
  저장된 진단은 sanitized `WorkspaceError`뿐이므로 positional/DDE redirect를 관측 사실로 단정하지 않는다.
- CLI `cc4a325`는 독립 재열기도 빈 새 프로세스에서 공식 Project Open을 사용하게 했다.
  native file-use 증거를 꾸미지 않고 기존 official Open receipt 대안을 사용한다.
- Platform `94ceab0`/`6c63309`는 실패한 재열기 창이 남지 않았을 때 명시적 웹 동작으로
  저장된 작업 사본만 한 번 다시 열 수 있게 한다. 원본만 실행 중인지 확인하며 생성·IL 삽입·자동 반복은 없다.
- 정상 Host 재시작은 원본 PID `2432`를 보존했다. 재시작 뒤 기술 구성 화면이 저장된
  completed-but-unbound 작업 대신 빈 생성 폼으로 돌아가는 복원 결함을 추가로 확인했다.
  해당 복원 경로를 수정한 뒤 같은 operation의 재열기만 이어가야 한다. 새 생성 요청은 금지한다.
- 이 실행에서 실제 PLC와 XG-SIM은 사용하지 않았다. 생성·검사·저장 성공은 전체 제품 완료와 다르다.

## 2026-09-03 실행 복구 — 전체 제품 완료 아님

### 현재 웹 계획 체크포인트

- 실제 웹 계획 run `92e018bc-5228-4ec3-b343-2ef46fcff1b4`는 `웹 LD 자기유지20260903`의
  XGK-CPUH/`MainProgramLD` 계획과 START·STOP·RUN_STATE·PREVIOUS_START·START_PULSE의
  3-rung LD 설계를 생성했고, 웹 UI에서 81개 항목/23개 섹션 검토를 마쳐 기술 구성 단계에 도달했다.
- 이는 계획·검토 성공만 뜻한다. XG5000 파일 생성, Program Check, 저장, 독립 재열기는 아직 성공으로
  기록되지 않았다.
- 기존 source PID `3948`을 보존하기 위한 새 빈 XG5000 일반 실행은 2026-09-03 현재 Windows consent
  pending이다. 결과가 확인되기 전에는 생성 실행이나 실패를 단정하지 않는다.
- 현재 web capability 화면은 Platform `45f5117`과 최신 `web/dist` 빌드가 반영된 상태다. Agent
  `81aa8f9`/`da97a7c`의 계획 validator 변경도 실제 XG 작업 완료 증거를 대신하지 않는다.

- `elevated-resume28-certification-receipt.json`의 실패는 UAC 거부가 아니라 이미 종료된
  XG5000 PID를 wrapper가 요구한 것이었다. 현재 wrapper는 정확히 빈 XG5000 baseline을
  확인한다. 실행 전 현재 프로세스를 확인하며 과거 PID를 현재 상태로 간주하지 않는다.
- MCP `30a461e`와 Platform `b104e2d`는 launcher의 native-file proof와 official Open
  receipt proof를 각 소비자가 구분하도록 수정했다. official receipt를 native file-use
  증거라고 표시하지 않는다. 인증기의 attach는 별도로 native identity를 다시 수집한다.
- MCP `5eca15f`/`90e4340`는 인증기 최초 source의 bare 실행과 독립 fresh reopen의
  exact-path positional 실행을 구분한다. PID/HWND/create-time, 제목, 파일 사용, hash,
  workspace/marker/backup 검증은 유지한다.
- resume29는 승인 후 실행됐지만 vendor Open 명령 전 창 조회 중 8초 deadline을 소진했다.
  MCP `8d23686`는 Open 준비의 중복 창 조회를 제거했다. `83d3e19`/`52035ea`는 실제
  실패 단계와 vendor command dispatch 수를 오류에 남긴다. 과거의 `official Open`
  오류 문구만으로 실제 명령이 전달됐다고 판단해서는 안 된다.
- resume31은 vendor Open 뒤 `XG5000_OPEN_DIALOG_NOT_FOUND`로 중단됐다. bounded evidence의
  candidate 수는 189이고 선택된 창은 HWND `199222`, 빈 제목의 `BCGPShadowSideWnd`였다.
  이는 Open dialog가 아니라 장식 shadow 후보가 선택된 사례다. semantic dialog wait 수정은
  진행 중이며, 이 기록은 live 인증 성공이나 수정 검증 성공을 뜻하지 않는다. resume32는
  동일 scratch hash와 exact empty XG5000 baseline을 전제로 실행 없이 준비만 됐다.
- 2026-09-03의 resume33/34 certification wrapper 관측(예: pre-click
  `XG5000_SEND_INPUT_FOREGROUND_REQUIRED`)은 이전 인증 재시도 기록이며 현재 장애 상태가 아니다.
- 현재 정상 Development Host health는 ready다. CLI `0829d35`/`0611593`은 일반 open/resume에
  official Open을 연결하고 explicit independent PID 재열기는 기존 native-file 방식을 유지한다.
  매번 새 CLI가 현재 소스를 읽으므로 이 수정에 Host 재시작은 필요 없다.
- 2026-09-03 15:32~15:33 KST standalone CLI `open-project`는
  `artifacts/cli-open-live-20260903-153207/open-result.jsonl`에서 exit 0, PID `3948`/HWND `787958`,
  `UI 레더 수직검증 20260817`, working SHA `e0eab4…81db`, official passed/closed binding을 기록했다.
- 이 receipt는 `official_open_binding_verified:true`/`native_file_usage_verified:false`다. 같은 PID
  `list-programs`는 `MainProgram` `ld:55e388e7aea7a8ce`를 `sha_bound_project_xml`(live catalog false)로 반환했다.
- 2026-09-03 15:33 KST elevated read/check executor PID `7784`는 같은 XG PID `3948`/HWND
  `787958`에서 `read-ld` exit 0, `stable_repeat:true`, decoded SHA
  `77687de1bf98820ffefef8d5b354151ec48ef0a2fd120152bf62d3d8e1972989`를 기록했다.
- readback은 2 visual rows, `P0000 AND P0001 AND P0002 → P0020` 및 `END`이고 project SHA는
  전후 `e0eab4…81db`로 동일하다. Program Check는 resource command `876`의 actual dialog 뒤
  error/warning `0`, `status:passed`, `verified:true`를 반환했다.
- 원본/working copy는 수정·저장하지 않았고 새 XG5000도 실행하지 않았다. 이는 Agent/PWA 통합이나
  LD 생성 완료가 아니다. 앞선 medium read `Win32 error 5` 뒤 elevated pair만 성공했다.
- Platform `16a661f`는 복구 두 경로가 launch receipt를 attached-session의 옛 assurance 문자열과
  비교하던 오류를 수정했다. PID/HWND/hash 및 후속 독립 readback은 유지한다. 이 Host 수정은
  아직 실행 중 프로세스에 반영되지 않았으며, 해당 복구 실행 전 정상 Host 재시작이 필요하다.
- 실제 PWA resume은 아직 수행하지 않았다. 사용자에게 요청된 “이 작업 계속하기” 1회 클릭은
  UAC 요청이나 인증 wrapper 재실행이 아니다.
- 2026-09-03 사용자 지시로 프로젝트 테스트 코드는 삭제됐다. 이 문서의 날짜가 붙은 과거
  test/receipt 기록은 당시 사실일 뿐 현재 test suite의 존재나 통과 상태를 뜻하지 않는다.
  실제 LD 변경·검사·저장·독립 재열기와 PWA 사용자 흐름은 아직 성공으로 판정하지 않는다.
- 정상 개발 제품 경로는 `Start-ControlStudio-Development.ps1` → persistent elevated Host →
  Agent/MCP → CLI다. MNE certification wrapper와 root Computer Use는 이 Host/PWA 기동의
  선행조건이나 runtime 의존성이 아니다.
- UAC는 한 번 요청하고, Windows가 명시적으로 사용자 취소를 반환하면 사용자 재요청까지
  기다린다. 승인 후 내부 실행 실패는 권한 거부로 보고하지 않으며 원인을 수정한다.
  물리 PLC는 장비 확보 후 로드맵이고 현재 검증 대상이 아니다.

## 2026-09-01 필수 읽기 경로 — 현재 진행

- 실제 프로그램 전면화 vertical이 완료됐다. 독립 CLI `52a3130`은 `focus-program`의
  UTF-8 JSON wire, exact PID/HWND/process lifetime, 프로그램 ID/name, 프로젝트 경로와
  SHA 전후 불변을 소유하며 `origin/main`에 push됐다. MCP `df64ee7`은 `program.focus`와
  `program.focus_location`의 대상 선택을 이 CLI catalog에 결속했고, `54d7349`는 승인된 LD
  변경의 diff/apply/validate/rollback도 같은 CLI catalog/read/apply 경계로 통일했다. Agent
  `cd1b554`는 명시적인 현재 XG5000 LD 열기 요청을 모델 선택에 맡기지 않고
  `project.list_programs` → catalog의 유일한 canonical ID → `program.focus` → verified receipt
  완료로 진행한다. 튜토리얼과 수정·저장·실행 요청은 이 읽기 전용 경로에서 제외한다.
- 실제 제품 conversation `ed6a0765-4870-4d2a-b6b0-b7d8b5828f79`, run
  `b4d8fb70-99b1-4750-8980-1cd4991ed205`는 PID `16228`/HWND `723394`에서 프로그램 목록과
  포커스를 각각 한 번만 실행하고 `COMPLETED`됐다. focus evidence는
  `7913a038-ae8e-5227-abd0-7a290c943f35`이며 artifact는
  `artifacts/change-mutation-e2e-localappdata/ControlStudio Development/artifacts/mcp-sessions/541aa48b-fd96-4187-9a0c-ac02bf1accda/27099419eee3/result.json`이다.
  `MainProgram`/`ld:55e388e7aea7a8ce`, exact program active, exact foreground, read-only,
  project unchanged가 모두 true이고 SHA-256 전후는
  `2d55b3489f0f1a97666e79d00bd4ef6962d2e2e71fa44839193be04240308b48`로 동일하다.
  PWA 작업 기록에서 이 완료 run과 프로그램 1개를 실제로 다시 열어 확인했다. 작업 기록에서
  현재 target과 일치하는 engineering conversation을 선택해도 시작 화면에 남던 상태 버그도
  수정해 해당 작업 화면으로 즉시 복귀한다.
- 최신 deterministic receipt는
  `artifacts/plc-agent-acceptance-agent-focus-final/run-3i6hmlyv/receipt.json`이며 manifest SHA-256은
  `bfcb8f3528932e4d2b7d3e788da1da71334ebea886e387a1f8c5dea082f70269`다. 새 Agent focus
  regression을 단일 manifest에 포함한 상태에서 Agent·Host·MCP·독립 CLI·Web build 5개
  group이 모두 `PASS`다. 외부 live-gate 파일을 주입하지 않았으므로 overall은 의도대로
  `FAIL_CLOSED`이며, 물리 PLC는 장비 확보 뒤 PRD G3/G4 로드맵으로 유지한다. XG-SIM
  quarantine은 재시도하거나 해제하지 않았다.
- CLI `f2a096f`와 문서 `6a21db7`은 production `project.list_programs`의 vendor core를
  독립 저장소로 옮겼다. `list_programs_entry.py`는 shared canonical UTF-8 JSON wire,
  exact PID/HWND, XG5000 title과 process lifetime, project path/SHA를 시작·종료에 결속하고,
  `vendor/program_catalog.py`가 bounded XGWX gzip/XML에서 LD program과 stable ID를 읽는다.
  기존 8-pixel project-grid sweep은 이 production 경로에서 사용하지 않는다. MCP `b96586b`는
  in-process structure catalog 대신 이 CLI를 호출하고 source/digest/program identity를 다시
  검증한다. 실제 PID `16228`/HWND `723394`에서 `MainProgram`과
  `ld:55e388e7aea7a8ce`, unchanged SHA `2d55b348...08b48`를 반환했다. CLI 전체 121개,
  MCP CLI/server boundary 112개가 통과했다.
- 새 deterministic artifact는
  `artifacts/plc-agent-acceptance-cli-owned-catalog/run-7f9t2xt4/receipt.json`, manifest
  SHA-256은 `06503a913f3b0542cddbe0d361e721fcda79965c743c8b259bfe0ea6bd3bee15`다.
  Agent·Host·MCP·독립 CLI·Web build 5개 group이 모두 `PASS`이고 CLI group은 93 tests,
  MCP group은 126 tests를 실행했다. 장비/외부 live receipt는 공급하지 않았으므로 overall은
  의도대로 `FAIL_CLOSED`다.
- Agent `b7c063d`는 현재/실제 XG5000의 named LD read 요청을 모델의 임의 initial-tool
  선택에서 제거했다. runtime이 `list_project_programs({})`를 먼저 선택하고, verified catalog의
  stable ID를 기존 deterministic continuation이 `read_ladder_program`에 전달한다. 일반 LD
  tutorial은 이 경로에 들어오지 않고 특정 fixture 이름에도 고정되지 않는다. 전체 Agent 103 tests가
  통과했다. 이 수정 전 실제 run `db4d60d0-7896-4cb9-8ae8-486fd216401d`은 static
  workspace search/structure를 반복하고 `agent_output_contract`로 실패했으며 CLI/XG5000 실패가
  아니었다.
- 정상 Host restart 뒤 direct XG5000 session은 process-lifetime-bound라 자동 복원되지 않는다.
  persisted static workspace SHA `13251e...`와 혼동하지 않도록 exact PID `16228`/HWND `723394`,
  working path/SHA `2d55b348...08b48`로 read-only attach를 다시 검증했다. 새 conversation
  `5f3fc135-54a0-448a-aa7a-04864ef731e3`, run
  `ecb0ee66-560d-4640-a6aa-14a603d3fd34`는 27초 안에 `list_project_programs` 1회와
  `read_ladder_program` 1회만 실행하고 `COMPLETED`됐다. list artifact
  `52265d1f.../ec32ddb5c6b3/result.json`은 `catalog_source=sha_bound_project_xml`,
  `live_editor_catalog=false`, program `ld:55e388e7aea7a8ce`, project SHA 전후 동일을 기록한다.
  LD artifact `52265d1f.../e8a71258b2b3/result.json`과 evidence
  `70964455-e837-58c8-8a6f-7d39705bb07d`는 payload SHA
  `aca8fb5f154b49ae9431df075efee8971610fd83dca1ebba65821e6be7795bd7`와 같은 project SHA를
  결속한다. 최종 답변은 `조건식: M00001 AND (M00000 OR M00010), 출력: M00010`이다.
- Agent route 수정 뒤 통합 artifact는
  `artifacts/plc-agent-acceptance-exact-ld-routing/run-ar8dko26/receipt.json`이다. 같은 manifest
  SHA `06503a913f3b0542cddbe0d361e721fcda79965c743c8b259bfe0ea6bd3bee15`에서 5개 deterministic
  group이 모두 `PASS`이며 Agent group은 79, MCP group은 126, CLI group은 93 tests다.
  external live-gate receipt는 공급하지 않았으므로 overall은 계속 `FAIL_CLOSED`다.

- CLI `067f4bb`은 실제 `read-ld` 생산 진입점의 전체 runtime closure를 독립 저장소가
  소유하게 했다. `read_ld_entry.py`가 canonical UTF-8 request, exact PID/HWND, project
  path/SHA, stable program ID/name, operation-local private payload handoff를 검증하고,
  CLI-owned decoder가 native payload와 semantic rung을 만든다. editor-open, exact
  project-tree/MDI selection, cancel-only dialog guard, raw clipboard transaction/capture,
  UIA/Win32 input/message substrate도 CLI-owned다. subprocess import guard는 이 closure가
  `xg5000_control`을 하나도 load하지 않음을 검증한다. CLI 전체 112개와 MCP CLI/server
  boundary 112개가 통과했다. 다른 compatibility command의 vendor core까지 전부 추출됐다는
  뜻은 아니다. `wire.py` 한 곳이 legacy와 fast entry의 UTF-8 canonical JSON,
  duplicate-key/NaN 거절, 크기 제한을 모두 소유한다. 초기 entry/shared wire는 `7a456a1`,
  stable program ID는 `07d1944`, 전체 LD read workflow는 `067f4bb`로 `origin/main`에 push됐다.
- `067f4bb` 소스 상태에서 실제 제품 conversation
  `2ed7ebb5-344c-4a69-944e-7aa3a73b4fec`의 run
  `fb410c7f-6cda-4c03-adb7-f18ba42a61ae`가 `COMPLETED`됐다. Agent는
  `project.list_programs` 1회와 `program.read_ld` 1회만 호출했고 PID `16228`/HWND
  `723394`, program `ld:55e388e7aea7a8ce`에서 evidence
  `5deb704b-8167-57bb-a11c-7c4d343b600b`를 만들었다. payload SHA-256은
  `aca8fb5f154b49ae9431df075efee8971610fd83dca1ebba65821e6be7795bd7`, project SHA-256은
  전후 `2d55b3489f0f1a97666e79d00bd4ef6962d2e2e71fa44839193be04240308b48`로 동일하며
  답변은 `조건식: M00001 AND (M00000 OR M00010), 출력: M00010`이다.
- 실제 제품 conversation `2ed7ebb5-344c-4a69-944e-7aa3a73b4fec`의 새 run
  `f04421bf-b1e8-4a63-a9ad-125dda7a31a9`은 새 좁은 CLI entry를 통해
  `read_ladder_program`을 1회 실행하고 `COMPLETED`됐다. PID `16228`/HWND `723394`, program
  `ld:55e388e7aea7a8ce`에서 evidence `b1393d42-fb86-5e11-89c4-63f088036577`를 만들었고 답변은
  `조건식: M00001 AND (M00000 OR M00010), 출력: M00010`이다. 앞선 run
  `0d62ff8f-660d-49bc-8d5f-60db0c9b58f6`은 새 entry의 한글 경로 JSON canonicalization 차이로
  실패했으며, 어댑터와 같은 `ensure_ascii=false` 규약으로 수정한 뒤 위 run에서 해결했다.
  비상승 terminal 직접 호출의 `win32_error=5`는 재시도하지 않았고, 이미 권한을 유지한 Host에서
  실제 사용자 경로만 검증했다. 새 UAC 요청은 없었다.
  공용 wire 리팩터 뒤의 마지막 Agent run
  `c1434d49-068a-4f3c-a6c0-d01e54a06ddf`은 첫 read-only session-status 뒤 모델 제공자
  HTTP 502로 종료됐으며 `program.read_ld`를 호출하지 않았다. 따라서 CLI 실패나 XG5000 동작
  증거로 해석하지 않고 같은 실기 요청을 반복하지 않았다.
- 갱신된 단일 acceptance manifest는 CLI narrow-entry와 shared-wire 회귀까지 포함한다. deterministic 전체
  실행 artifact는 `artifacts/plc-agent-acceptance-shared-wire/run-xdarfcco/receipt.json`,
  manifest SHA-256은 `3a4cce5bccac943d05be4b50559201cf3dc40109b18d595ed75be366609581ef`다.
  다섯 command group이 모두 `PASS`이고 CLI group은 66 tests를 실행했다. 외부 live receipt를
  이 deterministic run에 주입하지 않았으므로 overall은 의도대로 `FAIL_CLOSED`이며, 위 실제 run을
  가짜 CA-02 gate 파일로 승격하지 않는다.
- 최신 완료: MCP `404848a`는 UIA worker의 중복 `native_menu` fallback 순환을 제거했고,
  CLI `ea18481`은 실제 UIA/MSAA helper child를 CLI-owned vendor namespace로 옮겼다. MCP
  `1cf37e4`/`ccb2fb2`는 helper 구현을 삭제하고 부모 subprocess transport/public wrapper만
  유지하며, 직접 MCP read도 configured CLI helper를 사용한다. CLI 전체 89개, MCP 전체 578개가
  통과했고 CLI helper probe가 PID `14284`/HWND `329768`에서 성공했다. 제품 재시작 후 정확한
  working-copy PID `16228`/HWND `723394`를 session
  `c21c301e-ac0b-450f-96ca-0e1d4b760772`로 다시 attach했다.
- Agent `6c186db`는 확인된 `project.list_programs` 결과에서 정확한 LD 하나를 고른 뒤
  `read_ladder_program`으로 넘어가는 read-only continuation과, 한 개의 Host-decoded semantic rung에서
  조건식/출력을 완료하는 경로를 deterministic하게 처리한다. 전체 Agent 102개와 64 subtests가
  통과했다. 실제 conversation `2ed7ebb5-344c-4a69-944e-7aa3a73b4fec`, run
  `40e95d4b-7a16-4ef8-acd9-35a179e47ae8`은 목록 evidence
  `da27787f-2727-5470-a70b-b59a00689923`, LD evidence
  `8dbea3f9-d4ed-52a6-915d-ca5c6e966caf`을 각각 한 번 생성하고 `COMPLETED`됐다. 답변은
  `조건식: M00001 AND (M00000 OR M00010), 출력: M00010`이며 프로젝트 SHA는 전후 동일하다.
  이전 `a49dc6c6`/`4d55d598`/`fae53c6f`의 `continuation_contract` 실패는 이 경로에서 해결됐다.
- 별도 CLI 문서 commit `6e6694b`/`64f1f75`도 지정 원격 main에 push됐다. 전체 vendor-core 추출은 아직
  완료가 아니며 남은 menu/resource/toolbar/input/dialog/workflow 구현은 command vertical별로
  이동한다. 물리 PLC I/O는 장비가 있을 때의 PRD G3/G4 로드맵으로 유지한다. 과거 XG-SIM
  `QUARANTINED` 상태는 재시도하거나 해제하지 않았다.
- 실제 PWA 데이터/Agent 경로는 위 run으로 완료됐다. 첫 수동 Launcher 진단의
  `launch challenge proof did not match`는 격리 Development `LOCALAPPDATA`를 지정하지 않고 기본
  사용자 루트의 다른 token을 읽은 호출자 오류였다. 정확한 격리 루트의 `--no-open` 검증은 즉시
  통과했다. 뒤의 UI 호출은 기존 ControlStudio 창을 복원했지만 Windows foreground 전환이 거부돼
  한 번만 실패했으며 재시도하지 않았다. 오래된 인앱 탭은 여전히 인증 만료 화면이므로 Agent/CLI
  성공과 구분하고, 이를 이유로 LD read를 반복하거나 UAC를 요청하지 않는다.
- 정상 Host 재시작은 UAC 없이 완료됐다. 동일 XG5000 PID `16228`/HWND `723394`, working project
  SHA `2d55b348...b48`에 read-only session
  `99b79306-8631-45bf-80d6-5ccd143874cc`이 연결돼 있다.
- 실제 conversation `fbfd967e-c5ef-4c71-a5f1-645e21eb4259`의 run
  `8b0b7c95-c991-4271-9fa1-d9803a131b48`은 `build_program_logic_graph`와 evidence
  `4a88026d-06e2-5a4f-a1db-879d3025c311`을 남기고 `COMPLETED`됐다. 모델이 답한 논리식은
  `M00001 AND (M00000 OR M00010)`이며, M00001 OFF일 때 자기유지가 중단되고 M00010 OFF까지의
  동작을 정확히 설명했다. 같은 대화의 자연 후속 질문 run
  `85fe3b2e-98fa-45ca-94e1-432d0d949234`은 `tool.program.read_ld`에서 실패했다. 원인은
  SHA-bound structure가 LD program을 해석하지 못한 것으로, interface가 executable LD ID 대신
  `code_unit_id`만 제시한 것이었다.
- 현재 canonical ID 경로는 MCP `2ad7b7c`의 `canonical_program_id` → Host 기존 receipt의
  `structureFacts[].attributes`에 있는 `{name: canonicalProgramId, value: ld:…}` → Agent다.
  중간 구현 `a153d39`/`83c1f1c`/`7dedf39`의 별도 structure modelContext 전달은 아래 cleanup에서
  제거됐다. analysis-only read attach와 완료 미션 뒤 같은 대화의 새 분석 objective는 구현됐으며
  mutation 권한을 열지 않는다.
- 자연 후속 검증 conversation `623562a2-3c41-4083-934e-f7ee289c70a3`의 run
  `06582be7-94e0-4acd-8170-c43f634def62`은 `programlist`와 `read_ladder_program`을 모두
  자율적으로 성공했다. artifact `7c5a031e.../ca6c24cfb9e8/result.json`의 decoded semantic rung은
  정확히 `M00001 AND (M00000 OR M00010)`이다. evidence
  `f2f43294-d19f-5bc8-a849-d810eb1837c0`을 남긴 뒤 `COMPLETED`됐고, 답변은 M00010이 출력이며
  M00001 ON일 때만 self-hold하고 M00001 OFF이면 M00010 OFF라고 정확히 설명했다. 이 run은
  `programlist`를 사용했으므로 canonical structure 경로의 실제 검증을 뜻하지는 않는다. session
  `7c5a031e-8e34-4f80-9b09-888b13c3971d`은 같은 PID/HWND와 project SHA를 사용한다.
- 같은 대화의 명시적 tool name/ID 없는 동일 질문은 run
  `a318e924-a1a9-4c7f-aebc-2b85c593e2b4`에서 `search_project_evidence`와
  `read_ladder_program`을 모두 성공했다. evidence
  `d8ec0c6e-337f-5540-9e09-4fb5a2471495`를 남긴 뒤 `COMPLETED`됐고, 답변은
  `M00001 AND (M00000 OR M00010)` 및 M00001 OFF이면 전체 AND가 false여서 M00010 OFF라고
  정확히 설명했다. artifact `f48e.../0dc77fef613a/result.json`은 `verified=true`,
  `project_unchanged=true`다.
- structure-only run `9f983318-fc0b-4ba5-b160-8e642cebabba`는
  `inspect_project_structure` 성공 뒤 `agent_runtime_runtime_invariant`로 실패했다. 기존 receipt의
  `structureFacts.canonicalProgramId`는 보존돼 있었지만, 중복 model context projection이 raw digest를
  projection digest와 비교한 것이 원인이었다. Platform `dc7661a`/Agent `9b0b53b`는 중복 structure
  modelContext/overlay를 제거하고 기존 receipt `structureFacts[].attributes.canonicalProgramId`만
  사용한다. graph context는 유지하며, 저장된 `9f983` receipt replay는 `has_model_context=false`로
  통과했다. structure-only 재시도 run `918fa80b-07ed-471a-90e3-d9d58b574ff8`은
  `inspect_project_structure`를 성공하고 evidence `8fed4083-1c87-5a13-a840-174ace315fd9`를 남긴
  뒤 `COMPLETED`됐다. 답변은 structure entity ID
  `xg5000:code-unit:1449fa43-e9fb-4799-981c-54a82bcbdb63`와 LD `canonicalProgramId`
  `ld:55e388e7aea7a8ce`를 구분하고 `program.read_ld`에는 canonical ID를 써야 함을 정확히 설명했다.
- 최신 focused regression 수치는 위 최신 완료 절을 우선한다. 이 아래의 Agent 80개, MCP 76개,
  CLI 74개 기록은 이전 체크포인트다.
- 물리 PLC를 현재 범위에서 제외한 뒤 CLI vendor-core 추출을 재개했다. CLI `74826a2`는 LD 읽기의
  cross-process OLE clipboard helper를 CLI-owned vendor namespace로 옮겼고 지정 원격 main에
  push했다. MCP `9171433`은 같은 helper 구현과 MCP server의 helper entry를 삭제하고 부모 JSON
  프로토콜만 유지한다. CLI 전체 78개와 MCP clipboard/server boundary 88개 테스트가 통과했다.
  실제 conversation `00691471-1de0-43d5-b0f4-d3f3f57679c2`의 run
  `f7bc2d96-235b-4924-b181-73c10930510c`은 새 helper 경로에서 LD 읽기를 성공하고 조건식
  `M00001 AND (M00000 OR M00010)`과 출력 `M00010`을 답해 `COMPLETED`됐다. 전체 vendor-core
  추출 완료는 아니다. 이후 UIA/MSAA helper child 추출과 중복 fallback 제거는 위 최신 완료 절에서
  끝났고, 남은 menu/resource/toolbar/input/dialog/workflow 구현은 계속 transitional이다.
- 별도 conversation `35fdea15-3dc2-4145-889b-063807425b31`의 일반 요청 `54a7482d`와 뒤의
  세 실제 run은 프로그램 목록 읽기 뒤 `continuation_contract`로 실패했다. 원인은 compact model
  continuation에 고정 read-only 다음 단계를 맡긴 것이었고 Agent `6c186db`와 위 실제 완료 run으로
  해결했다. event `2693`은 과거 실패 뒤 evidence 보존 checkpoint로만 남긴다.
- 과거 XG-SIM trial은 `QUARANTINED`, engineered readiness는 false로 보존된다. XG-SIM
  acceptance와 전체 CLI vendor-core 독립은 아직 완료되지 않았다. 물리 PLC I/O는 현재 장비가
  없어 현재 완료 범위와 blocker에서 제외하고 PRD G3/G4 로드맵으로 이관했다. 별도 복구 기능은
  사용자 지시로 제외한다.

## 2026-08-31 21:57 KST native resume: 독립 재열기 검증 완료, XG-SIM 실패 기록

- 22:29 KST 반영 확인: MCP `189330e`는 raw/wrapped UIA 전달 상태 중 하나라도 `unknown`이면
  무실행 실패로 분류하지 않는다. 경계 테스트 67개, runtime 관련 103개, Host 관련 28개가
  통과했다. 정상 Host restart 한 번이 UAC 없이 `ready`로 끝났고, 이후 GET에서 아래 reopen
  성공·SIM 격리·미완료 상태와 기존 두 XG5000 PID가 그대로 유지됨을 확인했다. 새 SIM 실행은 없다.
- 원래 native operation `1dc48ccb-a0d8-44d1-9665-bb101dc7c64c`의 LD 입력, MainRes command
  `876` Program Check(0 errors), 저장은 이미 `APPLIED_VERIFIED`로 완료됐다. 새 프로젝트 생성,
  IL 재입력, 원래 검사·저장을 반복하지 않는다. workspace
  `9674be8c-cbea-4dd1-8d4d-a603280d3569` source/working hash와 저장된 프로젝트 hash는 모두
  `2d55b3489f0f1a97666e79d00bd4ef6962d2e2e71fa44839193be04240308b48`다.
- `17e855a` 뒤 fresh working instance PID `16228`/HWND `723394`의 MainProgram editor HWND
  `8390818`를 열었다. CLI `reopen-readback`은 첫 6개 MNE와 expected source SHA-256
  `74ff83178d6aa0319631874eed5ffd1d973fa326d8e4c656172ecf6ec9f534d0`를 실제 확인했다. 원래
  PID `14284`/HWND `329768`와 작업 복사본은 계속 보존된다.
- CLI `23830c4`가 MNE 읽기 뒤 LD 보기로 복귀하도록 수정된 후 21:57:06에 독립 복사본의
  MNE·LD·Program Check·파일 해시 검증이 모두 통과했다. reopen은 `verified`이며 성공 artifact는
  `993f8d91-daa5-4d2c-a77d-112526ac8436/71219768c132`다. 원본을 닫거나 다시 저장하지 않았다.
- 이어진 첫 XG-SIM 시도는 21:57:45에 `XG5000_UIA_TIMEOUT`으로 실패했다. 같은 session의
  `b103aec2dba7`가 실패 artifact다. trial `1c0f2380-8e1b-545a-ae01-09ff8a8b6e83`는
  `QUARANTINED`, `safeRestorationVerified=false`이며 `engineeredProgramReady=false`,
  `workspaceBound=false`다. 실제 명령 전달 단계는 아직 확인 전이므로 임의 재시도나 DB 격리
  해제를 하지 않는다. 21:59 XimUtil/XGSim 프로세스가 없었다는 조회만으로 과거 무동작을
  단정하지 않는다. 물리 I/O·최종 PWA acceptance는 미실시이고 이 구간 UAC 요청은 없었다.
- 알려진 제한: 현재 create/resume handler는 independent reopen과 XG-SIM acceptance를 함께
  요구한 뒤에만 active engineering session/workspace를 bind하므로, reopen이 검증됐어도
  quarantined SIM 중에는 Agent의 read/analyze 대상도 노출되지 않는다. 저장된 reopen session을
  그대로 bind하면 controlled-launch proof가 mutation-ready로도 해석될 수 있어 허용할 수 없다.
  향후에는 새 PID/HWND/lifetime을 다시 확인하는 명시적 analysis-only attach를 read-ready로만
  bind하고 mutation readiness는 닫는 경로로 분리한다. 이 문서상 quarantine은 해제하지 않으며
  GET/read 경로가 그 상태를 바꾸어서는 안 된다.
- 사용자 지시로 별도 복사본 초기화/복구 API는 만들지 않는다. 필수 작업은 기존 Agent의
  프로젝트 읽기 연결과 CLI/MCP 상태 조회, LD 작성·검사·저장·시뮬레이션이다. 실패 기록을
  삭제하거나 창 재열기를 과거 시뮬레이션 복원 성공으로 처리하지 않는다.
- MCP `c4ff276`은 XG-SIM 시작 상태 증명을 보완했고, `a16c137`/Platform `614d747`은 단계별
  native simulation 실패 증거를 분류했다. 해당 focused test는 통과했지만 실제 XG-SIM 재시도는
  아직 하지 않았다. `dd4582a`의 SHA-bound ID→exact-name 경로는 유지한다. semantic legacy
  catalog-sweep 교체 WIP는 폐기했으며, genuinely unnamed legacy sweep은 여전히 알려진 debt다.
  compact XGK 일반 수정 `476cdb9`/`b4738a8`은 완료됐지만 persisted actual plan은 이미
  full-width 주소를 사용하므로 이번 blocker가 아니다. root architecture README `d615174`도
  반영됐다. CLI `e735242`까지 지정 원격 main에 게시됐고 MCP·Platform checkpoint는 local이다.
  전체 CLI vendor-core 독립은 아직 완료되지 않았다.

## 2026-08-31 18:39 KST 재개 경로 구현·실행 중 — 이전 관측

- 기존 입력 LD/PID `14284`/HWND `329768`와 operation
  `1dc48ccb-a0d8-44d1-9665-bb101dc7c64c`를 계속 사용한다. 새 프로젝트 생성, IL 재입력,
  UAC 재요청, 물리 PLC 쓰기는 하지 않았다. 아래 18:14 상태의 최종 저장·재열기·XG-SIM
  완료는 아직 확인되지 않았다.
- Host `0ad8c12`/`15bba74`, MCP `f2379d2`, CLI `27c9046`으로 중단된 native build를
  재개하는 경로를 연결했고, Web `3fbfa84`로 **입력된 LD에서 검사·저장 재개**를 추가했다.
  API body는 `{pid, hwnd, expectedRequestDigest}`이며 저장된 계획을 사용한다.
- 실제 API 시도 두 건은 XG5000 명령 전달 전 거부됐다. 첫째는 resume에 다른 잠금 종류를
  지정한 오류였으며 Host `c26df66`이 기존 `native_project_create` 소유권을 그대로 인계한다.
  둘째는 plan digest 계산이 create-only 빈 폴더 조건을 재검사한 오류였다. Host `6ce7f7d`는
  기존 폴더에서도 동일 digest를 계산하며, 새 프로젝트 생성의 빈 폴더 조건은 유지한다.
  이 거부들을 native 동작 완료나 추가 UAC 실패로 기록하지 않는다.
- CLI `262445e`/`4f714f7`은 COM ROT가 없는 경우 기존 SHA-bound 창·파일 사용 관계와
  실제 XML CPU를 읽는다. 실제 `OperationResult` 반환형을 사용하는 pipeline 테스트는
  `1ff31ea`이며 평탄화한 가짜 검사 결과로 성공을 만들지 않는다.
- MCP `9422192`는 `LSPLC(XGB-XBCH)` 고정을 제거했다. 현재 파일의 configuration 이름을
  읽어 check/create/resume/reopen에 동일하게 전달한다. 실제 fixture 이름은 CPU suffix 없는
  `LSPLC`이며 사용자 변경 이름도 허용한다. CLI 문서 `1a77d01`까지 지정 원격 main에 push했다.
- 마지막 실행 담당은 `cli_runtime_fix`이며, MCP의 같은 plan-digest 수정 확인 뒤 정상 Host
  restart와 동일 operation의 resume를 한 번 실행한다. 중복 live 실행을 하지 않는다.
  결과가 나오면 이 절을 갱신한다. PWA 화면 acceptance는 여전히 수행하지 않았다.

## 2026-08-31 18:14 KST 새 LD 입력 완료·검사 실패 — 이전 관측

- 새 operation `1dc48ccb-a0d8-44d1-9665-bb101dc7c64c`는 실제 프로젝트 생성,
  compiled IL 입력 대조, LD 변환·roundtrip을 지난 뒤 Program Check 창 닫기에서 실패했다.
  현재 `quarantined`/`NATIVE_PROJECT_MCP_REJECTED`이며 전체 생성 완료가 아니다.
- PID `14284`/HWND `329768`에 `LD 오프라인 자기유지 개발 검증 20260831`이 그대로 열려
  있다. 입력된 LD를 닫거나 다시 만들지 않는다. 컴파일된 source SHA-256은
  `74ff83178d6aa0319631874eed5ffd1d973fa326d8e4c656172ecf6ec9f534d0`이다.
- 실패 원인은 `PROGRAM_CHECK_DOCK_CLOSE_LAYOUT_UNVERIFIED`였다. MCP `847a0e7`은
  close 영역의 고정 좌표 폭 대신 실제 native `HTCLOSE` 영역을 사용한다. 단위 테스트는
  통과했으며 이 수정의 새 프로젝트 실기 성공은 아직 확인하지 않았다.
- 기존 reconcile은 결과 확인만 할 뿐 중단된 검사·저장을 재개하지 못한다. 따라서
  Host → MCP → CLI `resume-native`를 연결 중이다. 기존 계획과 현재 프로그램을 대조한
  뒤 검사·저장·workspace capture·독립 재열기·XG-SIM을 잇고, 새 프로젝트 dialog나 IL
  재입력은 하지 않는다. 최초 생성 증거를 만들어 붙이지 않고 실제 resume provenance를 쓴다.
- 이 시점에 최종 저장·독립 재열기·실 XG-SIM·PWA 화면 acceptance·실 PLC I/O는 미완료다.
  이번 재개 작업에서 UAC를 새로 요청하지 않았다. 아래 복구 성공은 이전 프로젝트 기록이다.

## 2026-08-31 17:55 KST 이전 프로젝트 복구 성공

- 사용자의 계속 진행 지시에 따라 검증된 backup 복원 뒤 durable recovery를 정상 완료했다.
  같은 PID `14448`/HWND `329110`에서 reconcile은 `true`, 결과는
  `explicit_recovery_rolled_back`이며 final journal은 `ROLLED_BACK_SAVED`/terminal/
  lease released다. 이는 independent fresh reopen 증거가 아니다.
- journal의 실제 전이는 authorization 소비 뒤 `PLANNED → APPLYING`(seq 6),
  `QUARANTINED`(seq 7), `ROLLED_BACK_EDITOR`(seq 8)였다. 따라서 앞선
  “PLANNED-only/no mutation” 해석은 틀렸고, 안전 abort shortcut을 추가하지 않았다.
- `structural_diff`, `xg5000_program_check`, `project_save`, `project_reopen` 검사가
  모두 완료됐고, 현재 파일 SHA-256은
  `13251e001479190a16b9a88cf42d00b439b65d0c620222872e32bc5ec1ba7568`이다.
  정상 Host attach도 strong readback session
  `4a4ec200-62b2-4dee-a2df-c9b191d4010b`으로 성공했다.
- MCP `022a30e`은 duplicated-selection current envelope의 구조(duplicate selection,
  separator, 길이 28의 byte-significant intermediate header, decoded row stream)를
  확인한 경우에만 두 4-byte instance/process field를 canonicalize한다. 같은 decoded
  program의 raw snapshot 49개가 canonical SHA-256
  `75a1c0f0fd17273b613e1be0fece25d796d414a02348f57ce5bfc56e3a44bc97` 하나로 수렴했다.
  이 header 28 bytes는 zero라고 가정하지 않으며, program/row와 나머지 header는 계속
  byte-significant다.
- reviewed no-save close는 PID 14448에서 verified true로 끝났고 IDNO click/force kill은 없었다
  (receipt digest `1ec1dcd2…`). 이어 정상 Host → MCP → CLI idle launch는 PID 14284/HWND
  329768, lifetime `1788166678.7651005`, digest `a85e6a6ec72000b7b529ddde956fd1e8689c3b10cd51434efda324b97d4283c1`으로 성공했다.
  reviewed native create는 operation `1dc48ccb-a0d8-44d1-9665-bb101dc7c64c`, plan `62af347d…`,
  folder `artifacts/ld-native`로 제출됐다. 이후 실패와 현재 재개 상태는 위 18:14 절을 따른다.

## 2026-08-31 오후 제품 경로 재개 — 이전 관측 기록

- 정상 Launcher handoff → session → bootstrap API로 실행 중인 Development Host에
  인증했다. 브라우저 저장소/토큰 추출이나 차단된 페이지 렌더링은 사용하지 않았다.
  `/api/health`는 ready이며 제품 `/api/v1/xg5000/instances`가 MCP → 독립 CLI를 통해
  실제 XG5000 PID `7024`, HWND `67344`를 반환했다. Codex Computer Use의 실패는
  제품 런타임 실패로 취급하지 않는다. 이 관측은 PWA 화면 acceptance를 뜻하지 않는다.
- 제품 `POST /api/v1/host/restart`를 한 번 실행해 Host `5464`를 `13472`로 교체했고
  restart receipt `ready`, Host/MCP ready를 확인했다. UAC를 호출하지 않았고 XG5000
  PID/HWND는 그대로였다. 이 API는 호출한 세션만 승계하므로 다른 브라우저 세션의
  자동 유지를 주장하지 않는다.
- working copy SHA `f277c7ce606b7b902053b8fe732555288cc81e6c537ee7dafbc51a6bf68d91c2`와
  격리 transaction `f6ecf557-316b-4739-adde-247813330a18`는 유지했다.
  복원/재결속/이전 승인 재사용/PLC 쓰기는 수행하지 않았다.
- 별도 제품 대화 `6bb8a0a4-cf45-4e18-b402-d4b732216723`에 M0000 START,
  M0001 STOP_OK, M0002 RUN_CMD의 오프라인 자기유지 LD 개발 요청을 실제 제출했다.
  run `1ffc9dca-ceda-432e-a7ef-0f7af2fd0a0a`는 narrative 출력 계약 실패;
  초기 상태를 확정한 run `7692bf2d-1db4-4019-be12-227cd3274439`는 native-design
  provider 오류; 갱신 후 run `a76d5314-9674-4d27-a5c2-10436560c91d`는 native-design
  validator 오류였다. 세 실행 모두 실패이며 LD 생성 성공으로 계산하지 않는다.
- Agent `023ef77` / Platform `a7542e6`은 provider HTTP 4xx/5xx 상태만 안전한 오류 코드로
  보존한다. 본문·헤더·비밀값은 전달하지 않는다. 기존 두 실패의 버려진 HTTP 상태는
  복원할 수 없으며 특정 상태였다고 추측하지 않는다. 관련 Agent 4 + Host 2 테스트 통과.
- Agent `c8e4ea5`는 native validation의 원인과 모델 feedback을 보존하며 관련 3건 테스트가
  통과했다. Platform `5fb6a41`은 Web의 경과 시간 표시와 coalescing을 보완했고 Node 테스트
  2건, typecheck 및 build가 통과했다.
- Host `644131f`는 같은 단계의 15초 heartbeat를 Host가 버리던 stage-only 중복
  제거를 `(stage, elapsed_seconds)`로 고쳤다. fake transport 4건 통과 뒤 두 번째 정상
  Host restart에서 이 코드가 UAC 없이 실제로 load됐다. live SSE event `2290`/`2291`은
  `native_design`의 elapsed 15/30초를 각각 보냈으므로, 이 수정은 mock만의 결과가 아니다.
  긴 침묵이 sidecar event-loop 정지라는 초기 가설은 틀렸다. 로컬 fake-model 측정상 native
  schema는 14,106 bytes, 생성 약 19ms, Agent 생성 약 14ms였으며 과도한 schema 준비가
  원인이 아니다.
- MCP `55da905`는 LD source-not-visible의 안전한 숫자 geometry를 전달하고,
  CLI `30c46e3`는 code-prefixed `str(error)` 대신 구조화된 message를 반환한다.
  CLI commit은 사용자 지정 원격 `gidong-controls/controlstudio-ls-xg5000-cli`의
  `main`에 push 완료했다. 세 core 저장소 commit은 local이며 push하지 않았다.

제품 run `967941a3-16cf-42d8-8421-0b9587e3d37f`는 event 2290–2302의 15–195초
heartbeat 이후 event 2304에서 `FAILED/agent_runtime_provider_http_504`로 끝났다.
저장된 진단은 `planning_native_design/model_api_error/provider_http_504`이며, 사용자 메시지
API에서도 HTTP 504 표시를 확인했다. 제품 모델은 `nemotron-3-ultra`, 원격 서버는
`100.65.0.1:8978`, 제한 시간은 300초다. TCP 연결은 성공했고 무인증 `/v1/models` 조회는
401이었다. 이는 인증 필요 응답이며 저장된 자격 증명이 틀렸다는 증거가 아니다. 모델 생성
성공도 아니므로 504 이후 같은 모델 요청을 반복하지 않았다.

그 뒤 run `2930f0d8-24a9-438f-88d3-eaddab60e453`은 event 2306–2320의
`native_design` elapsed 0–150초 뒤 `FAILED/agent_output_contract`로 끝났다. safe 진단은
`planning_native_design/unexpected_model_behavior/schema_validation/<root>:json_invalid`이며,
앞선 provider HTTP 504와 다른 실패다. `/settings/model/test`의 저장하지 않는 15초
ephemeral timeout은 generic `all=false`을 돌려도 예외 시 aggregate false일 수 있으므로 모든
capability가 지원되지 않는다는 뜻이 아니며, sidecar protocol 오류 증거도 없다.

Agent `968e2ab`은 runtime 주소를 정규화하고, `0c9b4a7`은 fixture import를 바로잡았다.
최종 `87ad596`은 `nemotron-3-ultra`의 `supports_json_object_output=False`를 명시해 native
출력 요청에서 `response_format`을 보내지 않는다. mock wire 15건은 이를 고정하고 invalid JSON은
계속 거부한다. `artifacts/probe_native_output_20260831.py`의 controlled probe에서 강제 true는
input 114/output 47, finish `stop`, 29자 text와 `json_invalid`로 24,980ms 뒤 실패했고, 명시
false는 같은 prompt에서 input 114/output 30, finish `SUCCESS`로 10,621ms 뒤 성공했다. 중간
기본 profile(override 제거)도 5,660ms/output 45로 성공했으므로 강제 JSON 모드가 항상 실패한다고
단정하지 않으며, 이번 수정은 간헐적 호환성 위험을 제거한다.

Host `2a4f807`의 canonical compiler와 실제 Agent readiness → artifact 도출 → Host compile
경로는 M00000/M00001/M00002 출력을 증명했다. Agent 5건, Host fixture 3건, 상위 Host combined
7건과 Agent focused lint(F/E9/I)가 통과했다. fixture의 RUN_CMD만 M0010으로 둔 것은 XG-SIM
whole-byte write에서 입력과 출력을 분리하기 위한 test fixture이며 START M0000/STOP_OK M0001과
사용자의 기존 PLC 주소를 변경한 것이 아니다. `3167fc3`은 native-handoff fake attach에
PID/HWND/path/hash-bound `process_create_time` proof를 보완해 lineage 4/4를 통과시켰고 production
read-binding 검사를 약화하지 않는다. role-specific internal simulation의 구현·회귀 결과는
아래에 구분하며 실제 XG-SIM 성공을 대신하지 않는다.

- 별도 fake-bridge simulation 역할 검증에서 MCP `fa20d0a`와 Host `cb3540d`는 safe contact-only
  internal-M stimulus를 지원했다. sealed feedback은 `OFF → ON → released-held → STOP off`를
  검증했고 stimulus write는 M byte 0만, output M byte 2는 쓰지 않았다. 같은 byte write나 direct
  output forcing은 계속 거부한다. MCP targeted 43건, Host targeted 22건, 추가 Agent 24건과
  F/E9/I lint가 통과했다(`9f5e45d`는 formatting-only). 이는 fake bridge 증거이며 실제 XG-SIM
  실행이나 사용자 approval 결과가 아니다. Platform `820291e`는
  `onlyReviewedSimulationStimuli`에 TypeScript 계약과 Web 검사를 일치시켰다. contracts build/typecheck,
  Web typecheck/production build도 통과했지만 실제 PWA 화면 acceptance는 수행하지 않았다.

정상 Host restart는 ready/UAC 없음으로 끝났다. 기존 conversation
`6bb8a0a4-cf45-4e18-b402-d4b732216723`의 run
`7b89c506-6583-4ebb-a6ba-8af9c5986266`은 event 2342에서 `COMPLETED`가 되었고
`native_planning_finalize`까지 195초가 기록됐다. 제품 GET conversation의 latest plan message
`63277186-378f-40ca-96a1-e97542a78261`은 `ready_for_review`, `openDecisions=[]`, digest
`62af347dbb15fc358c20ca4fd48f2cf7e0daff6fe166fa81431e9b0480b1d0cd`다. read-only SQLite의
저장 metadata를 실제 Host compiler에 통과시켜 다음 source와 SHA-256을 확인했다.

```
LOAD M00001
LOAD M00000
OR M00010
AND LOAD
OUT M00010
END
```

`74ff83178d6aa0319631874eed5ffd1d973fa326d8e4c656172ecf6ec9f534d0`이며, 이는 실제
model → stored product artifact → compiler 경로의 증거다. model source는 M 내부 주소를 쓰되
START/STOP을 `digital_input`, RUN을 `digital_output`으로 typed 했다. 이는 native XG5000
create/build/save/reopen/simulation 또는 PLC write/new file write의 성공은 아니다. native create는
현재 plan의 human review와 global quarantined test transaction의 restore approval을 여전히
필요로 하며, 후자 async 질문은 사용자 응답을 기다린다. simulation 지원 수정은 커밋됐으며
실기 적용 전에는 변경된 Host/MCP 런타임을 함께 반영해야 한다. SSH 정보나 UAC 재요청은 하지 않았고 기존 LD operand 위치
선택의 scroll 단위도 아직 실증되지 않았다. Native Find 리소스 ID만으로 중복 피연산자 중 정확한
occurrence를 선택한다고 가정하거나 임의 좌표 식으로 바꾸지 않는다. 보호된 기존 working copy,
격리 transaction과 file quarantine은 변경하지 않았다.

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
- 역사적 2026-08-31 관측: 05:59 KST OS 재부팅 후 이전 Host/XG5000은 종료됐고, 06:16에
  normal Development launcher로 Host PID 5464/port 8767을 확인했다. 당시 XG5000 inventory는
  비어 있었다. 이는 현재 process 상태나 다음 작업 지시가 아니다. working copy `f277…`는 이전
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
- Agent `5a707b0`을 포함한 consolidated deterministic-only acceptance는 session `50168`에서
  exit `0`으로 끝났다. receipt는
  `artifacts/plc-agent-acceptance/run-6rg7qore/receipt.json` (generated
  `2026-08-30T23:11:09Z`, manifest SHA-256
  `aba0b0d80046c7b9c66a6f3d0fb00cc82090564ccd565526633466e4f2640f7a`)이며,
  Agent/Host/MCP/CLI/Web 다섯 deterministic group과 255 test executions가 PASS다.
  CA-01, CA-02, CA-04--CA-07, CA-12는 모두 `NOT_RUN`이므로 overall은 계속
  `FAIL_CLOSED`; Browser URL policy의 PWA acceptance 미검증과 file-restore approval 대기도
  그대로다.
- 현재 운영 recovery 정책: 실패나 관측 timeout 뒤에는 live handle이 있으면 보존하고 결과와
  journal/identity evidence를 진단한다. 결과가 불명확한 non-idempotent side effect와 이미
  소비된 approval은 replay하지 않는다. 반면 safe inspection과 구현은 이미 승인된 exact
  plan/file/mutation 범위 안에서 계속할 수 있다. 실제 UAC 거부가 확인된 경우에만 새 explicit
  user request 전에는 elevation을 다시 요청하지 않는다. timeout, browser policy 거부, 또는
  과거 관측만으로 UAC 거부를 추정하지 않는다.

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
  과거 development smoke 실행이 같은 terminal 모델 실패를 기존 약
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

다음 세션은 항상 새 PowerShell/UAC/smoke에서 다시 시작하지 않고, 아래 evidence-based resume
순서로 현재 수직을 이어 간다.

1. 검증된 active Host, engineering session, PID/HWND와 live handle이 있으면 data root,
   workspace path/hash, transaction/journal identity를 다시 대조한 뒤 보존한다. 관측 timeout이나
   오류만으로 이 handle을 버리거나 새 launcher를 시작하지 않는다.
2. Host 프로세스가 실제로 없거나 종료됐음을 확인한 경우에만 같은 Development data root를
   쓰는 normal launcher로 cold start한다. XG5000 session이나 개별 tool handle이 없다는 이유로
   살아 있는 Host를 다시 시작하지 않는다. elevation이 실제로 거부됐으면 새 explicit user
   request 전까지 다시 요청하지 않으며, timeout이나 과거 관측은 거부로 간주하지 않는다.
3. 기존 quarantined recovery는 같은 transaction의 journal, target identity, recovery evidence를
   보존한 채 필요한 승인 아래 계속한다. `restoreVerifiedBackup`은 명시적 opt-in 요청이며,
   마지막 관측에서는 해당 파일 복원 승인이 미응답이었다. 재개 시 실제 응답과 승인 대상을
   확인하고, 응답 없는 상태를 승인으로 간주하지 않는다.
4. recovery와 target binding이 허용하는 동안 먼저 read-only scroll/focus 진단으로
   `XG5000_LD_SOURCE_NOT_VISIBLE`의 실제 표면/identity를 확인한다. 불명확한 side effect나 이미
   소비된 approval은 replay하지 않는다.
5. 그 뒤 exact target과 baseline이 다시 증명된 경우에만, 새 approval을 받은 LD
   `P0020→P0021` operand 변경을 apply → Program Check → save → distinct PID/HWND reopen/readback
   순서로 수행하고 fresh receipt를 남긴다. XG-SIM scope를 선택하면 receipt 뒤 keep 또는 revert
   결정을 같은 transaction에서 완료한다.

실기 증거 없이 native/XG-SIM/PLC 성공을 문서 상태만으로 선언하지 않는다. Browser URL policy의
PWA acceptance 미검증도, file-restore approval 대기도 이 절차로 해결된 것으로 주장하지 않는다.

### 2026-08-31 현재 continuation evidence

- MCP `0584ef5`는 LD horizontal/vertical scrollbar notification을 각 bar의 own parent로
  보낸다. `4d549ed`는 `focus_location`을 CLI 경계로 옮겼고, CLI `d230d96` 및 `fa0ed95`는
  public `focus-location` 명령을 제공한다. CLI 49건과 MCP focused 91건은 통과했다.
- 실제 subprocess에 malformed focus request를 한 번 보냈을 때 `InputError`로 끝났고 UI는
  열리지 않았다. 이는 live LD focus 성공 증거가 아니다.
- `C:\XG5000\XG5000.exe`의 single `sky.launch_app`만 consent PID `12916`을 만들었다.
  prompt는 뒤에 사라졌고 XG5000 process/window도 없었으며 원인은 아직 모른다. launch를
  이 관측만으로 반복하지 않으며, 이 관측을 approval 거부라고 쓰지 않는다.
- 이후 사용자 `다시`에 따라 한 번 실행한 XG5000은 PID `7024`, HWND `67344`로 실제
  열렸다(2026-08-31 10:05 KST 시작). 빈 XG5000 화면을 Computer Use로 확인했고 프로세스는
  응답 중이었다. 첫 launch의 `targetable window` 오류는 시작 중 관측 지연이었으므로 같은
  프로세스를 다시 관측해 창을 찾았으며 재실행하지 않았다. 이 PID/HWND는 다음 행동 전에
  현재 프로세스/창 목록으로 재확인한다.
- 새 프로젝트 `Ctrl+N` 입력은 `failed to activate captured window`로 실패했다. 현재 창을
  다시 선택한 뒤 활성화를 한 번 재시도해 같은 오류를 확인했다. 사용자에게 기존 XG5000
  창을 전면에 놓아 달라고 요청했다. 새 프로젝트 생성·LD 입력·저장·PLC 작업은 하지 않았고,
  창 활성화 실패를 UAC 실패로 바꾸어 부르거나 XG5000/Host를 재시작하지 않는다.
- PWA Browser URL policy block은 계속 남아 있으며 우회하지 않는다. working-copy restore
  approval은 아직 받지 못했고 quarantine도 유지한다.
- native geometry는 아직 고치지 않았다. payload `739c...` snapshot `bd2300fece86`, token 18 /
  row 0 / c94에서 modulo 33은 -1211을 내지만 raw 373은 증명되지 않았다. 1024 row stride만으로
  modulo를 삭제하지 않는다.
- 다음 UI 확인 경로는 고정 좌표 추정이 아니라 XG5000의 `찾기/바꾸기 → 디바이스 찾기`다.
  설치된 `C:\XG5000\l.kor\XG5000Help_kor.pdf` 8장(PDF 315–322쪽)은 `현재 창`, 디바이스
  종류/행 범위 필터, `다음 찾기`의 일치 셀 선택, `모두 찾기` 결과 선택 후 Enter로 해당
  프로그램 행 이동을 설명한다. `찾아가기 → 스텝`도 PDF 251쪽에 있다. 따라서 과거의
  “LD Find/Go To 기능이 없다”는 조사는 틀렸다. 명령 존재는 확인했지만 자동화 구현 및
  현재 버전의 실제 UI 동작 확인은 아직 하지 않았다.

### 내부 M 비트 변경 연결 — 실기 완료와 구분

- Agent `f5aa1a4`, MCP `9edc7e5`, Host `6cde9e0`/`c5480c5`는 실제 I/O 영향이 없는
  내부 M 접점·코일의 검색 → 대상 분석 → snapshot → 승인 제안 경로를 연결했다. Host의
  승인 후 실행도 이 경우에만 하드웨어 digest 없이 native 변경 계획을 전달한다.
- 이전에는 M 변경도 P 전용 미션 판별과 무조건적인 하드웨어 요구에 걸렸다. 이제 공통
  target/plan/coverage 조립을 재사용하고 하드웨어 의존 부분만 조건부로 처리한다. M이라도
  downstream P 출력이 있으면 이 예외를 쓰지 않으며, 이미 하드웨어 근거가 있는 M 계획도
  기존 경로를 유지한다.
- 이후 Host `6d06754`는 하드웨어 미관측으로 차단된 분석도 Agent에 전달하도록 수정했다.
  Agent `03aabec`는 M 접점이 P 출력에 영향을 주면 기존 대상/제안 단계를 유지하면서 현재
  실행의 하드웨어 조회 → 동일 대상 재분석으로 이어간다. 이전 실행의 근거는 재사용하지
  않으며, 새 하드웨어에 결속된 분석이 준비된 뒤에만 공통 승인 제안을 생성한다.
  exact named M/P 접점의 condition 역할도 지원하며 legacy OUT 경로는 유지한다.
  `f70d30c`는 중복 target/coverage 조립을 이미 있던 공통 함수로 합쳤다.
- 보존 payload `739c2be5…`의 메모리 복사본을 M으로 변환해 실제 MCP 분석 JSON을 실제 Host
  broker로 전달했다. `M0010→M0011`, `M00010→M00011` 모두 MainProgram/token 18에서
  offline candidate가 생성됐고 하드웨어 binding은 없었다. 원본 파일은 변경하지 않았다.
  이것은 native 데이터/계층 연결 검증이며 XG5000 입력창의 M 정규화 형식이나 GUI 저장 성공을
  증명하지 않는다.
- 통합 실행 `artifacts/plc-agent-acceptance/run-setz_406/receipt.json`은 Agent 68, Host 71,
  MCP 99, CLI 49 test executions와 Web build가 통과했다. manifest SHA-256은
  `2a6fde46a4ecae84d0bb9f9d4f24afb3c833c815ee492bf7557386369c7e2c80`이며 생성 시각은
  `2026-08-31T10:56:54+09:00`이다. 실기 네 항목은 `NOT_RUN`, 전체는 `FAIL_CLOSED`다.
  공통 함수 정리 후 Agent 68건도 다시 통과했다.
  별도 교차 검사에서 실제 MCP 의미 형식의 Agent 생성 M 계획을 Host 실행 계약이 받았고,
  4/5자리 M 피연산자의 native 전달값이 그대로 유지됐다.
- 기존 XG5000 PID 7024/HWND 67344를 재확인했지만 이번 Computer Use 캡처는 timeout이었다.
  새 창 목록으로 다시 선택한 뒤 한 번 더 캡처해도 같았다. 기존 창·Host를 종료하거나 UAC를
  다시 요청하지 않았다. 실제 LD 생성·편집·검사·저장·재열기와 PWA 실사용 확인은 미완료다.
  Host PID 5464는 유지되어 이번 소스 변경은 아직 실행 중인 PWA에 반영하지 않았다.
- 다음 continuation의 창 목록 재관측도 Computer Use 도구가 `unacceptable risk`로 거부했다.
  사유는 반복 캡처 실패 뒤 외부 화면 변화 없는 재시도였다. 다른 자동화 수단으로 우회하지
  않았고, 사용자에게 기존 XG5000 창을 전면으로 올린 뒤 알려 달라고 요청했다. UAC 거절이나
  프로그램 종료로 해석하지 않는다. Native Find/Go 구현은 실제 활성 LD 메뉴·대화상자 관측이
  필요하며, 실패 지점에 추가 guard를 붙이는 변경은 하지 않았다.
- 2026-08-31 12시대(KST) 읽기 전용 process/token 확인: 기존 XG5000 `7024`와 Host `5464`는
  모두 `high`, Codex 본체 `9452`는 `medium`이었다. executable path와 생성 시각도 확인했다.
  기존 Host/XG5000 승인이 풀린 상태가 아니므로 이 오류를 근거로 UAC를 재호출하지 않는다.
  Codex 본체와 별도 Computer Use 실행 주체는 구분하며, 이 관측만으로 UIPI가 최초 활성화
  실패의 원인이라고 확정하지 않는다. 원본 기록은 10:06 화면 읽기 성공 뒤 활성화 실패,
  10:11 화면 읽기 성공, 10:15 이후 캡처 timeout 순서이며 오늘 앱 로그 파일은 비어 있었다.
- MCP `d6f7f26`은 별도 재현된 프로젝트 결함을 수정했다. 활성화마다 `SW_RESTORE`를 호출해
  최대화된 편집창까지 복원하던 동작을 제거하고 최소화된 창만 복원한다. 이미 전면인 창은
  건드리지 않으며, `GetForegroundWindow` 반환값과 `IsChild` HWND 인자를 pointer 크기로
  처리한다. 수정 전 layout/focus 회귀 2건 실패, 수정 후 64-bit ctypes callback을 포함한
  5건 통과 및 Ruff 통과. 모두 fake API 검사이며 실제 창에 입력하지 않았다. 이 수정은
  Codex Computer Use 구현을 바꾸지 않고, 캡처/활성화 복구나 LD 실사용 성공 증거도 아니다.
- CLI `66f8494`/`22d44cf`는 기존 instance discovery의 User32 ctypes fallback 호출에
  HWND/HMENU/LPARAM 인자·반환 형식을 지정했다. 프로세스 재검증 계층을 새로 만들지 않고
  원래 발견 절차를 유지한다. 합성 ctypes callback으로 32-bit 초과 핸들을 검사했고 전체
  CLI 51건이 통과했다. 지정 원격 main에 `22d44cf`까지 push 완료. 창 열거·입력·UAC 등의
  live 동작은 실행하지 않았으며, CLI의 HWND 처리 수정도 외부 Computer Use 복구 증거가 아니다.

## 제품 실행과 개발 도구의 경계

2026-08-31 사용자 재확인: 제품 Agent가 Host/ExecutionBroker → MCP → 독립 CLI를 통해
XG5000을 다루는 것이 목표다. Codex Computer Use는 이 실행 경로에 포함되지 않는다.
앞 절의 Codex 창 활성화·캡처 실패는 개발 도구의 실패 기록일 뿐, 제품 Agent 작업 전체를
중단하거나 사용자에게 XG5000 전면 클릭을 반복 요구할 근거가 아니다. 정상 제품 실행은
Host의 run/event/transaction 및 CLI 결과를 기준으로 추적한다. 외부 도구가 거부한 동일
UI 동작을 우회하지 않으며, 그 제한과 별개로 가능한 제품 구현·검증은 계속한다.

제품 기록 재확인 결과:

- 마지막 Agent run `50275151-dbec-415c-a2e6-99f81a58eb25`는 승인 제안 생성 후 실패했다.
  현재 Agent `c4bce7f` 이후의 exact-operand 결정적 전환이 이미 그 경로를 대체했고,
  관련 fake-runtime 회귀 3건이 통과했다. 이 과거 기록을 새 미수정 장애로 간주하지 않는다.
- 실제 apply 오류는 보존 artifact `cf83b3a1b673/error.json`의
  `XG5000_LD_SOURCE_NOT_VISIBLE`이다. 이후 `native_status_unavailable`은 최초 apply
  원인이 아니라 recovery 단계의 오류다. 현재 코드로 보존 payload를 순수 재비교하면
  token 18의 `P0020 → P00021` 한 건과 저장된 두 SHA가 일치한다. 파일/격리는 변경하지 않았다.
- MCP `dd03833`은 `_open_operand_dialog`에서 창 활성화를 viewport 측정보다 먼저 하고,
  좌표 계산 후의 무조건 `SW_RESTORE`를 제거했다. 회귀가 수정 전 실패/후 통과했고
  focus/foreground 12건 및 Ruff가 통과했다. 실제 product apply 성공은 아직 미검증이다.
- 정적 설치 리소스: `C:\XG5000\XGPM\l.kor\PMLDEditorRes.dll`의 RT_MENU 38007,
  LANGID 1042, offset `0xC7F0`에서 `편집 → 디바이스 찾기` command 4845를 확인했다.
  `XGPM\l.kor\PMFindRes.dll` dialog 6631은 입력 6620/다음 6626/모두 6600을 갖는다.
  별도 `l.kor\FindRes.dll` dialog 7767은 입력 7604/현재 창 7753/다음 7626/모두 7600이다.
  두 variant 중 실제 LD handler가 여는 창은 아직 확인하지 않았으므로 정적 ID 발견을
  새 Find adapter 구현/실동작 성공으로 해석하지 않는다. 다음 확인도 제품 native 경로에서 한다.

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
