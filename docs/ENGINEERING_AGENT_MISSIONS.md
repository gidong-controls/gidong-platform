# Engineering Agent Missions

## 관측 사실과 Agent 판단

새 engineering analysis는 `schemaVersion: "4"`를 사용한다. `observations`는
모델이 작성한 자연어가 아니다. Host가 structural receipt에서 투영한
`evidenceId + factKind + factFingerprint + canonical statement` 가운데 하나를
Agent가 선택하고, Host가 원 receipt membership과 문장을 다시 확인한 값이다.
자유로운 해석은 `inferences`, 가능한 원인은 `hypotheses`에만 둔다.

가설은 확인 계획만 적어 둔 채 진단 완료로 닫지 않는다. 각 가설은 `open`,
`verified`, `rejected` 중 하나이며, 검증 또는 기각은 실제 결과와 현재 run의 Host
evidence를 함께 가진다. 그 evidence는 결과를 만든 mission step에도 남는다. 하나라도
`open`이면 mission은 완료가 아니며, 도구로 더 확인하거나 실제 사용자 입력·capability
blocker를 명시해야 한다.

대안이 실제로 필요한 작업에서 Agent가 `alternatives` 항목을 하나 만들면 그
항목에는 근거, 예상 영향, 위험, 필요한 변경, 검증 절차와 rollback이 모두
있어야 한다. 단순 조회처럼 대안 비교가 필요 없는 작업은 빈 배열을 유지한다.
기존 저장 대화의 schema v1/v2는 읽기 호환을 유지하지만 새 run의 사실 권위로
승격하지 않는다.

ControlStudio의 엔지니어링 화면은 챗봇 대화창이 아니다. 사용자가 한 번 제시한
엔지니어링 목표를 Agent가 작업 계획으로 만들고, 필요한 읽기·분석 도구를 연속으로
실행하며, 완료 조건이 닫힐 때까지 진행 상태와 근거를 관리하는 작업 공간이다.

## 실행 모델

한 사용자 요청은 하나의 `run`을 시작한다. 한 run 안에서 Agent는 최대 여덟 개의
자율 cycle을 수행하고, 각 cycle 안에서는 모델과 도구 결과를 이어 받아 여러 도구를
호출할 수 있다. 다음 중 하나가 될 때만 사용자에게 제어를 돌려준다.

- 목표의 모든 완료 조건이 근거와 함께 충족됨
- 계속하려면 사용자의 구체적인 정보가 필요함
- digest-bound 사람 승인이 필요함
- 필요한 capability가 실제로 없음
- 자율 cycle budget을 소진해 명시적인 재개가 필요함

도구 하나가 끝났거나 분석 문장 하나를 만들었다는 이유만으로 작업을 끝내지 않는다.

## Mission snapshot

모든 engineering cycle은 다음 상태를 완전한 snapshot으로 남긴다.

- `objective`: 현재 엔지니어링 목표
- `completionCriteria`: 목표 달성을 판정할 수 있는 조건
- `steps`: stable step ID, 상태, 결과 요약, 결속된 Host evidence ID
- `activeStepId`: 현재 실행 중인 단계
- `status`: `active`, `waiting_for_user`, `waiting_for_approval`, `completed`, `blocked`
- `blockingQuestions`: 계속하기 위해 사람에게 필요한 답변
- `nextAction`: Agent가 다음에 수행할 구체적 행동
- `stopReason`: 응답하거나 계속한 이유

Platform은 snapshot을 canonical JSON digest, conversation, durable target identity,
revision, run과 결속해 저장한다. workspace가 있으면 그 workspace/revision을 사용하고,
직접 연결한 XG5000은 원문 경로를 노출하지 않는 경로 digest와 exact project hash를
사용한다. ephemeral XG5000 session ID가 바뀌더라도 같은 대상을 다시 검증하면 mission을
새 run으로 이어갈 수 있다. XG5000 없이 LS PLC만 관찰하는 작업은 검증된 controller
fingerprint와 canonical endpoint를 durable target으로 사용한다. 이전 evidence ID는 작업
이력이며, 현재 사실의 freshness가 중요하면 Agent가 다시 관측해야 한다.

한 conversation의 mission objective와 완료 조건, 완료된 step 이력은 Agent가 임의로
바꾸거나 지울 수 없다. blocker에 대한 사용자 답변도 같은 mission을 계속하며, 별개의
목표는 새 engineering conversation에서 시작한다.

새 mission의 objective는 사용자가 보낸 요청을 기존 4,000자 mission 한도 안에서
그대로 고정한다. 모델이 더 쉬운 목표로 바꿔 완료 선언할 수 없다. `completed`는 모든
step의 결과가 닫히고, 완료 step 중 적어도 하나가 현재 run 또는 Host가 보존한 이전
step의 exact evidence ID를 유지할 때만 허용한다. 조율·정리 단계마다 억지 근거를
붙이지는 않지만 근거가 전혀 없는 자기 선언을 목표 달성으로 저장하지 않는다.

각 cycle 경계에는 mission과 별도로 versioned Agent checkpoint도 저장한다. 이
checkpoint는 Pydantic AI model message history와 그 안의 retrieval/tool
request·result를 보존하며, mission digest와 target-bound conversation에 결속된다.
Host 또는 sidecar가 재시작된 뒤 같은 target과 mission을 재검증하면 새 run은 최근
checkpoint의 message history에서 이어간다. checkpoint는 분석 연속성일 뿐 변경 성공
증거가 아니며, 중간에 끝나지 않은 tool call이나 mutation을 자동 재실행하지 않는다.
그 경우 Host evidence와 MCP journal 상태를 다시 확인한다.

## 권한 경계

Mission은 조사와 계획의 진행 권위이지 PLC 변경 권한이 아니다. Agent는 목표와 단계를
선택하고 read-only 도구를 연속 실행할 수 있지만, mutation은 기존과 동일하게 Platform의
사람 승인, target binding, lease와 MCP journal/readback 경계를 통과해야 한다. Mission이
`waiting_for_approval`이라고 기록해도 승인 자체를 만들거나 대신하지 않는다.
이미 유효한 승인 대기 transaction이 있으면 Host는 그 transaction의 ID·plan digest·
만료 시각만 Agent에 알려주고 proposal tool을 다시 열지 않는다. 따라서 재시작 뒤 같은
변경 제안을 중복 생성하지 않고 기존 사람 결정을 기다린다.

## 재개와 UI

Web은 최신 mission을 대화 메시지와 분리된 작업 패널로 표시한다. 목표, 단계별 상태,
완료 조건, blocker, 다음 행동과 evidence를 한눈에 보여준다. 단계의 evidence ID는
현재 대화에 로드된 exact 근거 카드로 이동하며, 근거가 아직 복원되지 않았으면
비활성 상태로 명시한다. `active` mission이 cycle
budget에서 멈추면 사용자는 `작업 계속 실행`으로 같은 목표의 첫 미완료 단계부터 새
run을 시작한다. 완료되거나 차단된 mission에서 별개의 목표로 넘어갈 때는 `새
엔지니어링 작업`이 새 conversation을 만들며 이전 mission 기록을 보존한다. 일반 텍스트
답변의 `nextActions` 목록은 mission 진행 상태를 대신하지 않는다.

헤더의 `작업 기록`은 최근 conversation을 다시 열고 해당 conversation의 event stream을
처음부터 재생한다. 저장된 답변·mission·evidence·승인 이력을 다시 검토할 수 있으며,
현재 엔지니어링 target과 다른 기록은 기존 target-match gate에 의해 읽기 전용이다.
기록을 연다고 XG5000이나 controller target을 전환하지 않는다.

## 디버깅 모드

기본 작업 화면은 목표, 단계, 근거와 중단 조건만 보여준다. 별도의 `디버깅`
토글을 켜면 현재 conversation/run, 검증된 target, mission revision·digest,
active step·stop reason, event sequence/type, selected-PID identity assurance,
Host capability와 오프라인 변경 준비 상태를 확인할 수 있다. 전체 프로그램 교체는
strong readback과 developer-certified capability bundle 중 어느 전제에서 막혔는지도
별도로 표시한다. 이 진단 표시는 권한을 열거나 안전 gate를 우회하지 않는다.
Agent checkpoint는 원문 message나 tool 인수를 펼치지 않고 저장 cycle과 digest만 event
activity에 표시한다.
이 화면은 재현과 상태 전이 추적을 위한 것이며 모델 프롬프트, 원문 도구
인수, 자격 증명, 프로젝트 원문 payload와 PLC 쓰기 권한은 표시하지 않는다.
