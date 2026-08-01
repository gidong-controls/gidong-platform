# ControlStudio 사람·에이전트 엔지니어링 작업 흐름

최종 갱신: 2026-07-30

이 문서는 [PRD.md](PRD.md)의 두 핵심 결과인 `신규 구축`과
`기존 설비 엔지니어링`을 사람이 실제로 사용하는 화면과 상태 전이로 구체화한다.
기능·안전 범위는 PRD가 우선하고, GUI 및 런타임의 기술 경계는
[CONTROLSTUDIO_GUI_HANDOFF.md](CONTROLSTUDIO_GUI_HANDOFF.md)와
[CONTROLSTUDIO_AGENT_RUNTIME_HANDOFF.md](CONTROLSTUDIO_AGENT_RUNTIME_HANDOFF.md)를
따른다.

## 1. 공통 원칙

ControlStudio는 빈 채팅창에서 곧바로 코드를 생성하거나, 변경 요청 한 문장을
즉시 실행하는 제품이 아니다.

```text
사용자 의도
→ 구조화된 엔지니어링 artifact
→ 미정·가정·충돌 확인
→ 정확한 검토 대상 고정
→ 사람의 검토 또는 승인
→ 벤더 adapter의 최소 작업
→ readback·검사·시뮬레이션·관찰
→ 확정 또는 검증된 원복
```

채팅은 질문과 설명을 위한 인터페이스다. 요구사항, 설계, 변경안, 승인과 검증
결과는 채팅 말풍선이 아니라 서버가 소유하는 버전이 있는 artifact다.

모든 항목은 최소한 다음 상태 중 하나를 가진다.

| 상태 | 의미 |
|---|---|
| `confirmed` | 사용자·문서·실측 또는 결정론적 도구 증거로 확인됨 |
| `proposed` | 에이전트가 제안했으며 사람의 검토가 남음 |
| `unknown` | 아직 결정하거나 측정하지 못함 |
| `conflicting` | 출처 또는 현재 상태가 서로 일치하지 않음 |
| `not_applicable` | 근거와 함께 적용 대상이 아님을 확인함 |

`unknown`을 숨기기 위해 값을 추측하지 않는다. 대신 어느 단계까지 진행을
막는지 `blocking`으로 표시한다. 모델이 `완료`라고 표현해도 Host의 결정론적
완전성 gate와 사람의 digest-bound 검토가 없으면 다음 단계로 넘어가지 않는다.

### 1.1 엔지니어링 지식 사용

작업자는 필요할 때 `지식` 화면에서 제조사 매뉴얼, 표준과 프로젝트 문서를
가져온다. 에이전트는 Host의 read-only 검색으로 적용 가능한 근거를 찾고 사용한
페이지·섹션의 `knowledge_ref`를 남긴다.

- 매뉴얼은 제조사 명령 의미와 제한을 설명하지만 현재 프로젝트 상태의 증거는
  아니다.
- 프로젝트·현장 기억과 작성 선호는 먼저 `proposed`로 저장하며 사람이 범위와
  revision을 확인하기 전에는 Agent 근거로 쓰지 않는다.
- 제어 패턴은 모터·실린더·시퀀스·알람 등의 필수 질문과 시험 누락을 줄이는
  체크리스트다. I/O 주소, 신호 극성, 시간값과 위험 상태는 현장 증거 없이
  채우지 않는다.
- 벤더·제품군·버전·프로젝트 범위가 다른 결과는 조용히 혼합하지 않는다.
- 프로젝트 범위는 파일명이나 표시 이름이 아니라 immutable `workspace_id`로
  고정한다. 현재 `workspace:{workspace_id}` 또는 `project:{workspace_id}`에
  명시적으로 결합한 매뉴얼은 아직 product family와 version을 증명하지 못했어도
  그 workspace 안에서만 참고할 수 있다. 이 경우 applicability는 미확정으로
  표시하며 다른 프로젝트나 공유 표준으로 승격하지 않는다.
- `vendor:*` 또는 `global`의 공유 매뉴얼은 current target의 product family와
  version이 둘 다 증명되고 정확히 일치할 때만 사용한다. 둘 중 하나가 없거나
  다른 경우에는 유사한 내용이어도 fail-closed하고 추가 확인 질문으로 남긴다.

상세 계약은 [KNOWLEDGE_MEMORY.md](KNOWLEDGE_MEMORY.md)를 따른다.

## 2. 신규 프로젝트 협업

### 2.1 화면과 단계

시작 화면에서 `새 프로젝트`를 선택한 뒤에만 신규 구축 작업공간을 연다.
시작 선택과 빈 대화창을 같은 화면에 동시에 표시하지 않는다.

```text
BRIEF
→ DESIGN
→ PLAN_REVIEW
→ TECHNICAL_SETUP
→ BUILD
→ VERIFY
→ READY
```

각 단계는 하나의 주 행동만 보여준다.

| 단계 | 사용자에게 보이는 주 작업 | 다음 단계 gate |
|---|---|---|
| `BRIEF` | 설비 목적과 알려진 제약 입력 | 이름·목적·안전/위험 상태 기록 |
| `DESIGN` | 미결 결정에 답하고 설계안 비교 | blocking unknown·conflict 정리 |
| `PLAN_REVIEW` | 살아 있는 Engineering Plan 검토 | 정확한 plan digest에 사람 검토 기록 |
| `TECHNICAL_SETUP` | 벤더·CPU·모듈·경로의 실제 호환성 확정 | adapter capability와 plan digest 일치 |
| `BUILD` | 승인된 묶음별 native project·로직 생성 | 각 묶음의 예상 diff와 readback 일치 |
| `VERIFY` | 검사·재열기·정상/경계/고장/복구 simulation | 모든 필수 시나리오와 원상복구 성공 |
| `READY` | release 후보와 미해결 제한 확인 | 별도 PLC 쓰기·온라인 승인 전 오프라인 완료 |

### 2.2 요구사항 brief

첫 brief는 길고 복잡한 CPU 설정 폼이 아니다. 다음 핵심만 먼저 받는다.

- 프로젝트 이름
- 설비 목적과 생산 결과
- 안전·인터록·위험 구역과 현재 모르는 위험
- 알려진 공정 순서
- 알려진 하드웨어·I/O·통신
- 자동·수동·보전·초기화·재기동 요구
- cycle time·품질·인수 기준
- 선택적 I/O 목록, 설비 문서와 기존 템플릿

모르는 항목은 비울 수 있지만 저장 시 명시적인 `unknown`이 된다. 빈 값이
확인된 `없음`으로 바뀌지 않는다.

### 2.3 살아 있는 Engineering Plan

에이전트는 다음 artifact를 단계적으로 만든다.

1. 요구사항과 설비 경계
2. 하드웨어·모듈·네트워크
3. 센서·액추에이터·I/O·tag
4. 자동·수동·보전·초기화·재기동·전원 복구 mode
5. STEP·상태 전이와 timing
6. 인터록·충돌·자원 점유·hazard
7. alarm·timeout·reset·복구
8. task·program·routine 구조
9. 정상·경계·고장·복구 시험과 acceptance trace
10. 결정 이력, 출처와 변경 이유

각 item은 안정적인 ID, 상태, 출처, 중요도, blocking 여부, 확인 방법과 revision을
가진다. 에이전트의 문장은 기본적으로 `proposed`이며 사용자 brief의 원문이나
도구 증거와 같은 것으로 표시하지 않는다.

설비 종류가 확인되면 에이전트는 벤더 중립 제어 패턴에서 필요한 신호 역할,
복구 질문과 시험 시나리오를 가져올 수 있다. 벤더·CPU·제품군·버전이 확정된
뒤에만 해당 매뉴얼을 사용하고, 패턴을 네이티브 로직으로 곧바로 변환하지 않는다.

### 2.4 결정 필요 목록

대화창보다 `결정 필요` 목록을 우선한다. 각 질문에는 다음을 함께 표시한다.

- 왜 지금 결정해야 하는가
- 선택 가능한 대안
- 에이전트 추천과 근거
- 답하지 않으면 막히는 단계
- 사용자의 결정, 결정자와 시각

예:

```text
결정 필요 · SAFETY · BUILD 차단

비상정지 해제 뒤 자동 운전을 자동으로 재개합니까?

추천
자동 재개하지 않고 초기화 확인과 작업자 재시작 명령을 요구합니다.

이유
정지 중 제품·축 위치가 달라질 수 있어 기존 STEP 재개를 안전하게
증명할 수 없습니다.
```

### 2.5 계획 검토

데스크톱 기본 구성은 다음 관계를 유지한다.

```text
상단  단계·project revision·전체 완전성
왼쪽 artifact 목록·미결 수
중앙 선택 artifact의 구조화 내용·revision diff
오른쪽 또는 하단 에이전트 대화·결정 질문
```

모든 줄마다 승인 버튼을 요구하지 않는다. 하드웨어/I/O, 운전·복구,
안전·인터록, architecture·시험처럼 위험 경계가 다른 묶음별로 검토한다.

`계획 검토 완료`는 화면 전환 버튼이 아니다. 다음을 서버에 기록한다.

```text
plan_revision_id
plan_digest
actor_id
reviewed_at
blocking_unknown_count = 0
review_scope
```

계획 내용이 한 글자라도 바뀌면 digest가 달라지고 이전 검토는 자동 무효다.
계획 검토는 native project 생성, PLC 쓰기 또는 online 작업 승인이 아니다.

### 2.6 생성과 검증

전체 로직을 한 번에 생성하지 않는다.

```text
프로젝트 골격
→ 하드웨어·I/O
→ 공통 mode·초기화
→ 공정 STEP
→ 인터록·alarm·복구
→ program 로직
→ native check
→ 저장·별도 프로세스 재열기
→ 정상·경계·고장·복구 simulation
```

각 묶음은 승인된 plan revision, 예상 diff, 실제 native readback과 검증 receipt를
가진다. 한 묶음이 실패하거나 결과가 불명확하면 다음 묶음을 실행하지 않는다.

## 3. 기존 프로젝트의 부분 수정

### 3.1 “타이머를 잠깐 늘려 보자”의 의미

`잠깐`, `시험`, `일단`, `임시로`는 실행 명령이 아니라 범위가 모호한 의도다.
ControlStudio는 먼저 다음 중 무엇인지 확인한다.

| 시험 방식 | 의미 |
|---|---|
| XG-SIM에서만 시험 | 저장된 production revision을 바꾸지 않고 simulator에서 검증 |
| 오프라인 시험 revision | 별도 working copy revision으로 저장하고 비교 |
| 온라인 test edit 뒤 원복 | 실행 중 controller에서 제한된 시험 후 명시적 untest/rollback |
| 영구 변경 | 검증된 변경을 새 정식 revision 또는 controller에 확정 |

모호한 상태의 기본 추천은 `XG-SIM에서만 시험`이며 mutation을 실행하지 않는다.
현재 adapter가 해당 시험 방식을 검증하지 않았다면 다른 방식으로 조용히
대체하지 않고 지원 불가 이유와 필요한 준비를 표시한다.

### 3.2 부분 수정 전체 흐름

```text
의도·시험 범위 확인
→ 정확한 target 후보 식별
→ 현재값·단위·CPU 명령 의미 확인
→ 전체 영향과 현재 실행 상태 조사
→ 대안과 비코드 조치 비교
→ 변경 계약 검토
→ digest-bound 승인
→ read-before-write와 전체 edit set 재확인
→ 최소 변경
→ readback·검사
→ 지정 신호 관찰과 pass/fail 판정
→ 시험 유지·영구화 또는 원복
→ receipt와 감사 기록
```

사용자가 XG5000에서 보고 있는 위치를 활용할 수 있지만 현재 선택만으로 target을
확정하지 않는다. 같은 이름의 timer, alias, 간접 참조 또는 여러 쓰기가 있으면
프로그램·네트워크·현재값과 함께 후보를 보여 주고 하나를 선택하게 한다.

### 3.3 변경 계약

승인 화면은 최소한 다음을 한 계약으로 표시한다.

- 의도와 시험/영구 수명
- 정확한 workspace·controller·program·network·instruction identity
- 현재값, 제안값, 단위와 CPU별 의미
- 승인에 포함되는 **전체 edit set**
- 직접·간접 참조, 다중 쓰기와 실행 순서
- mode·초기화·재기동·전원 복구·fault 경로
- 영향을 받는 output·HMI·통신·외부 장치
- 현재 active STEP·timer 상태와 snapshot 시각
- 물리 actuator·hazard mapping과 미확인 전제
- 관찰 신호와 acceptance window
- pass/fail·즉시 중단 조건
- rollback manifest와 readback 기준
- 실제 자동 검증 범위와 사람이 수행할 현장 검증

기술 ID는 접힌 상세 정보에 두고 기본 화면은 다음처럼 읽혀야 한다.

```text
T120 시험 변경 · XG-SIM에서만

현재       3.0 s
제안       5.0 s
시험 종료  시나리오 3회 뒤 자동 원복

확인된 영향
Y23 기동과 STEP 40 진입이 최대 2.0 s 늦어짐

아직 확인할 항목
수동 복구 mode의 동일 timer 사용 여부

관찰
T120 elapsed, X14, Y23, STEP 40

성공
3회 모두 X14 확인 뒤 Y23 기동, alarm·timeout 없음
```

버튼은 실제 행위를 말한다.

- `XG-SIM 시험 변경 적용`
- `온라인 시험 시작`
- `시험 종료·원복`
- `검증 결과를 새 revision으로 유지`

일반적인 `확인`이나 모호한 `적용` 버튼을 사용하지 않는다.

### 3.4 누락 방지 gate

다음 coverage를 계약에 구조화해 표시한다.

1. exact target identity와 before hash
2. 값 단위와 instruction/CPU 의미
3. direct·indirect reference와 multiple writer
4. task·call·execution order
5. auto·manual·maintenance·startup·power recovery·fault 경로
6. online/offline 비교와 관찰 시각
7. physical actuator·hazard mapping
8. 현재 active sequence·timer 상태
9. 관찰 신호·acceptance window·중단 조건
10. 전체 artifact rollback manifest와 readback
11. capability·권한·승인
12. 남은 unknown과 미지원 native 요소

적용 가능 판정은 단순한 항목 개수가 아니라 각 coverage의 상태와 근거를 사용한다.
영향 경로를 닫는 데 필요한 항목이 `partial`, `unknown` 또는 `conflicting`이면
승인 버튼을 비활성화한다.

화면은 `영향 분석 완료` 같은 포괄 문구 대신 범위를 보여 준다.

```text
프로그램 4/4 조사
직접 참조 12 · 간접 경로 3 · 다중 쓰기 1
미지원 명령 2 → 적용 차단
runtime snapshot 11:42:08 · 18초 전
```

### 3.5 시험 상태와 최종 결과

부분 변경은 다음 상태를 사용자에게 계속 표시한다.

```text
조사
→ 계획 검토
→ 승인 대기
→ 현재 상태 재검사
→ 적용
→ 관찰·검증
→ 유지 또는 원복
```

시험 변경의 `적용 성공`은 최종 완료가 아니다. acceptance window가 끝날 때까지
`관찰 중`이며, 사용자가 닫아도 Host가 상태와 lease를 소유한다. 응답 유실로
현재 적용 여부를 증명하지 못하면 자동 재실행하지 않고 `QUARANTINED`로 전환한다.

## 4. 멀티벤더 적용

공통 제품 상태는 LS의 메뉴 이름으로 정의하지 않는다.

| 공통 단계 | LS XG5000 예 | Rockwell 예 |
|---|---|---|
| 변경 준비 | Start Online Editing | Pending / Accept Edits |
| 시험 | 별도 관찰·검증 | Test Edits |
| 확정 | Write Modified Program / End | Assemble Edits |
| 시험 취소 | 검증된 원복 | Untest / Cancel Edits |

벤더마다 실제 transaction 경계가 다르므로 adapter가 capability manifest와
전체 edit set을 반환한다. 예를 들어 Rockwell의 test 범위가 한 rung가 아니라
program의 여러 edit zone일 수 있으면 ControlStudio는 사용자가 말한 한 변경만
표시하지 않고 실제 함께 시험되는 전체 범위를 승인 계약에 포함한다.

## 5. 현재 구현과 다음 increment

2026-07-30 현재 구현은 다음 수준이다.

- 설비 목적·안전과 함께 운전/복구 mode와 성능·인수 기준을 받을 수 있는
  신규 프로젝트 brief와 MCP·mutation 도구가 없는 계획 대화
- 계획·기존 엔지니어링 양쪽에서 사용할 수 있는 Host read-only 지식 검색,
  PDF·Markdown·텍스트 가져오기, 페이지 인용, 제조사·제품군·버전 격리
- 사람 확인 전에는 검색되지 않는 현장 기억·작성 선호와 12개 벤더 중립 제어
  패턴
- 사용자 확인 사실과 에이전트 제안, 미결 결정을 분리한 서버 소유
  `Engineering Plan v1`, canonical digest와 SQLite 영속 저장
- 최신 ready plan의 정확한 digest와 전체 section set에 묶인 사람 검토 기록,
  section별 명시적 확인과 구조화된 Web 검토
- hardware/I/O, 운전·복구 mode, 성능·인수 기준이 비어 있으면 이를 생략하지
  않고 `unknown/blocking`으로 올리는 Host 계획 gate
- native XG5000 기술 구성 요청을 최신 reviewed plan의 conversation,
  message, digest와 reviewed section set에 결합하고 생성 직전 다시 검증하는 gate
- 동일 길이 LD operand 한 개의 제한된 오프라인 변경 계약
- 영향·위험·가정·대안·acceptance·관찰 신호·중단 조건과 10개 필수 coverage를
  검사하는 Host 완전성 gate
- `잠깐`, `시험`, `temporary`, `trial` 요청을 서버가 감지하고 사용자가
  `분석만` 또는 `저장되는 오프라인 후보 revision`을 명시적으로 선택하기 전
  실행 계약 생성을 차단하는 request-scope gate
- agent가 제안한 confirmed coverage의 evidence ID를 같은 conversation·run의
  실제 `evidence.added` 기록과 생성·승인·실행 시점마다 다시 결합하는 gate
- 대상·digest 승인, native diff·XG5000 check·save와 crash reconciliation
- 승인과 실제 작업 복사본 변경 실행을 분리하고 `수정 요청`,
  `XG5000에서 프로그램 열기`를 제공하는 Web 검토 흐름. 현재는 정확한
  network·token 이동이 아니라 검증된 프로그램 전면화까지 지원

아직 전체 작업 흐름이 구현된 것은 아니다.

- 신규 계획의 item별 결정 사유와 revision diff, 문서·I/O 목록 첨부 및
  확인된 기술 설정 전체의 자동 전달이 더 필요하다.
- I/O·mode·sequence·safety·acceptance를 실제 벤더 생성 묶음과 native
  readback에 연결하는 결정론적 build gate가 필요하다.
- 부분 변경은 현재 저장되는 오프라인 revision만 지원한다. XG-SIM/온라인 시험의
  `OBSERVING → ACCEPTED/REVERTED` 수명과 전체 벤더 edit set은 아직 없다.
- 현재 변경 target은 program ID·native token offset·동일 길이 operand로
  결정론적으로 고정되지만 timer instruction·CPU time base·실제 시간 단위,
  network identity와 물리 hazard mapping은 아직 typed native evidence로
  강제되지 않는다. 따라서 coverage gate가 전체 사이드이펙트 분석의 완성을
  보증한다고 주장하지 않는다.
- 일반 LD 생성, 독립 재열기, 전체 XG-SIM 시나리오, 실제 PLC 관찰·run edit는
  별도 capability가 검증되기 전까지 활성화하지 않는다.

구현된 순서와 다음 coherent increment는 다음이다.

1. 완료: 계획 결과를 서버 소유 structured artifact와 digest로 저장
2. 완료: exact plan revision과 전체 section scope에 대한 사람 검토 기록
3. 완료: 구조화된 계획 검토 화면, 결정 필요 목록과 빈 핵심 항목의 blocking
4. 완료: reviewed plan digest를 현재 native project 골격 생성 요청에 결합
5. 완료: 현재 오프라인 부분 변경의 coverage·acceptance·request-scope·evidence
   계약과 승인 gate
6. 다음: 계획의 typed I/O·mode·safety를 실제 생성 묶음으로 만들고 각 묶음의
   native readback·검사·원복 receipt 제공
7. 다음: timer/CPU 단위와 전체 영향 경로의 typed evidence contract
8. 다음: XG-SIM trial의 관찰·채택·원복 수명주기

## 6. 공식 제품에서 채택한 설계 근거

- [LS XG5000 User Manual](https://sol.ls-electric.com/uploads/document/16411767742780/XG5000_Manual_V2.5_202012_EN.pdf)
- [Mitsubishi GX Works3 Operating Manual](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh081215eng/sh081215engar.pdf)
- [Rockwell Test Accepted Edits](https://www.rockwellautomation.com/en-us/docs/studio-5000-logix-designer/38-01/contents-ditamap/studio-5000-logix-designer/navigating-the-application/-product_name_rsl5k--application-main-window/-product_name_rsl5k--application-commands/test-accepted-program-phase-edits-command.html)
- [Rockwell Online Import Considerations](https://www.rockwellautomation.com/en-ro/docs/studio-5000-logix-designer/38-02/contents-ditamap/studio-5000-logix-designer/import-and-export/import-online-considerations.html)
- [Siemens Project Comparison](https://support.industry.siemens.com/cs/attachments/98944323/Project_comparison_en-US.pdf)
- [GitHub Copilot research, plan and iterate](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/research-plan-iterate)
