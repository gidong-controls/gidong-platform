# ControlStudio 사람·에이전트 엔지니어링 작업 흐름

최종 갱신: 2026-09-04

이 문서는 [루트 PRD](../PRD.md)의 두 핵심 결과인 `신규 구축`과
`기존 설비 엔지니어링`을 사람이 실제로 사용하는 화면과 상태 전이로 구체화한다.
기능·안전 범위는 PRD가 우선하고, GUI 및 런타임의 기술 경계는
[CONTROLSTUDIO_GUI_HANDOFF.md](CONTROLSTUDIO_GUI_HANDOFF.md),
[PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md)와
[책임·데이터 소유권](refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md)을 따른다.

## 1. 공통 원칙

ControlStudio는 사용자의 요구를 PLC 설계와 실제 LD 개발 작업으로 이어 주는
에이전트다. 자연어 요청으로 분석·설계·변경안을 만들고, 사용자가 선택한 변경은
작업 복사본에 적용하여 검사·저장·재열기까지 진행한다. 문서 작성이나 검증 결과만
늘리는 것으로 이 개발 작업을 대신하지 않는다.

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

기존 프로젝트의 engineering composer는 자연어 동사를 추측해 권한을 열지 않는다.
사용자가 각 작업의 범위를 `읽기·분석` 또는 `승인할 오프라인 후보 준비`로
명시적으로 선택하고 Host가 그 값을 run metadata에 기록한다. 설계 초안과 변경
설명은 읽기 근거만으로도 진행할 수 있다. 실제 적용할 후보는 원본 보존 working
copy와 현재 XG5000 대상에 결속하고, 기존 승인 경로로 실행한다. COM으로 확인된
대상만을 유일한 방법으로 요구하지 않는다. Host가 저장·재열기 근거와 현재
프로세스·작업 복사본을 확인할 수 있으면 그 범위의 오프라인 작업에 사용한다.
읽기 세션이나 후보 준비 선택 자체를 PLC 쓰기 권한으로 바꾸지 않는다.

모든 항목은 최소한 다음 상태 중 하나를 가진다.

| 상태 | 의미 |
|---|---|
| `confirmed` | 사용자·문서·실측 또는 결정론적 도구 증거로 확인됨 |
| `proposed` | 에이전트가 제안했으며 사람의 검토가 남음 |
| `unknown` | 아직 결정하거나 측정하지 못함 |
| `conflicting` | 출처 또는 현재 상태가 서로 일치하지 않음 |
| `not_applicable` | 근거와 함께 적용 대상이 아님을 확인함 |

`unknown`은 미정으로 표시하고 그 결정이 필요한 작업만 보류한다. 다른 부분의
분석·설계·로컬 개발은 계속하며, 완료한 단계는 미정 항목 때문에 처음부터
반복하지 않는다. 프로젝트 적용 전에는 대상과 변경 내용을 확인하고, 적용 후에는
실제 readback·검사·저장 결과로 성공 여부를 판단한다. 모델의 완료 문구나 로컬
검사 통과만으로 실제 XG5000 작업까지 성공했다고 표시하지 않는다.

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
- 신규 프로젝트 브리프에는 `confirmed` 상태의 매뉴얼·표준·프로젝트 문서를
  최대 20개까지 명시적으로 첨부할 수 있다. Host는 실행 시작 시 source ID,
  SHA-256과 revision을 다시 확인하고, Agent는 첨부 자료를 사용할 때 그 source
  ID로 검색 범위를 제한한다. 첨부 사실은 사용자 확인 항목이지만 문서 내용은
  인용된 `knowledge_ref` 없이는 승인된 설계 사실이 아니다.
- 프로젝트 범위는 파일명이나 표시 이름이 아니라 immutable `workspace_id`로
  고정한다. 현재 `workspace:{workspace_id}` 또는 `project:{workspace_id}`에
  명시적으로 결합한 매뉴얼은 아직 product family와 version을 증명하지 못했어도
  그 workspace 안에서만 참고할 수 있다. 이 경우 applicability는 미확정으로
  표시하며 다른 프로젝트나 공유 표준으로 승격하지 않는다.
- `vendor:*` 또는 `global`의 공유 매뉴얼은 vendor·product family·version의
  적용 범위를 함께 표시한다. 일치 여부가 미정이어도 비교·조사 자료로 사용할
  수 있지만, 현재 프로젝트의 사실이나 특정 명령의 호환성을 증명하는 자료로
  조용히 승격하지 않는다. 실제 적용에 영향을 주는 차이만 확인 항목으로 남긴다.

원본·파생 지식과 확인 상태의 현재 계약은
[책임·데이터 소유권](refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md)을 따른다.

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

실행 가능한 네이티브 설계 제안은 I/O, mode, control network, timer, sequence,
alarm, recovery, safety의 8개 collection마다 `collectionScope` 결정을 기록한다.
존재하는 collection은 `confirmed`, 실제로 적용되지 않는 collection은 구체적인
사유·현재 사용자 brief의 원문 근거·허용된 N/A basis를 가진 `not_applicable`이다.
N/A collection은 비어 있는 채 compiler가 건너뛴다. 따라서
timer·sequence·alarm·recovery를 실행 가능성만 맞추려고 만들어 넣지 않는다.
물리·위험 출력은 confirmed `process_interlock` 또는
`external_safety_boundary`가 필수다. 이 단계의 자연어 evidence와 설계 값은 모두
에이전트의 제안이며, 사람의 항목별 검토 전에는 실행 권한이 아니다.
`collectionScope`가 없는 이전 계획은 표시만 유지하며 새 실행 build에는 명시적
scope로 재계획해야 한다.

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

설명 문장은 하드웨어/I/O, 운전·복구, 안전·인터록, architecture·시험처럼 위험
경계가 다른 묶음별로 읽는다. 그러나 native build의 실행 권한이 되는 구조화
설계는 누락을 숨길 수 없도록 다음 값을 각각 독립 검토 항목으로 펼친다.

- 각 collection의 전체 개수·ID와 confirmed/N/A basis
- I/O mapping, kind, normal/safe state
- mode activation·transition과 network output·permit·mode/step binding
- timer address·time base·preset·유효 시간·enable 조건
- sequence restart policy·start/reset·각 STEP 주소와 완료 조건
- alarm latch·trigger·affected output, recovery condition·reset target
- safety classification·action·permit와 각 affected output

각 항목은 에이전트 제안임을 표시하며 자동 선택하지 않는다. 사용자가 화면에
표시된 정확한 값과 “이 목록이 전체 집합”이라는 scope 항목을 직접 확인해야 한다.

`계획 검토 완료`는 화면 전환 버튼이 아니다. 다음을 서버에 기록한다.

```text
plan_revision_id
plan_digest
project_brief_digest
attachment_revision_digest
actor_id
reviewed_at
blocking_unknown_count = 0
review_scope
reviewed_item_ids = Host가 설계에서 계산한 전체 실행 항목 집합
```

클라이언트나 모델은 검토 항목의 전체 집합을 정하지 못한다. Host가 canonical
설계에서 이름 공간이 있는 안정 ID를 다시 계산하고 제출된 ID 집합과 정확히
일치하는지 검사한다. 계획 내용, 최신 project brief 또는 첨부 source revision 중
하나라도 바뀌면 이전 검토는 자동 무효다. build 직전에도 Store의 최신 artifact를
다시 읽어 digest, 전체 항목 coverage, 참조·주소·상태·시간·안전 불변식을 재검증한다.
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
→ 같은 working copy의 CPU·timer area·I/O context readback
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

`device.causal_impact`는 exact device를 기준으로 모든 LD 프로그램을 읽어
상류 원인과 하류 영향을 각각 추적한다. 결과는 직접·간접 device chain, 접점
극성, AND/OR 문맥, 쓰기 동작, 프로그램·network·rung·token 위치와 cycle을
보존한다. `partial`, `truncated` 또는 미지원 application이 있으면 닫힌 영향
조사로 취급하지 않으며, runtime 현재값·task 순서·HMI·통신·외부 배선은 별도
근거가 필요하다.

`project.behavior_report`는 관측한 native LD에서 write target, 물리 출력 후보,
sequence target, timer/counter target, multiple writer와 `APPLICATION` inventory를
각각 bounded collection으로 반환한다. 개수·잘림과 partial program/network/rung/
action을 coverage에 남기고 각 target에는 관측된 source span을 함께 둔다. 명시적인
지원 집합 밖의 `APPLICATION`은 operand가 있어도 `partial`이다. 실제 XGWX에서
관측한 direct-D `MOV`/`DMOV`는 source read와 1/2-word destination write로
구조화한다. Direct-D `INC/DEC` signed/unsigned 16/32-bit 계열은 LS instruction
contract에 따라 in-place read/write로 구조화하며 `P` variant는 rising-edge 실행으로
기록한다. Double-word variant는 지정 D word와 다음 word를 모두 포함한다. Non-D·간접
형식과 non-D destination `DMOV`는 추측하지 않는다. Native LOAD 비교는 정확히 두
operand를 가진 comma 형식만 구조화한다. `=`, `<>`, `>`, `<`, `>=`, `<=`는 signed
16-bit, 동일 연산자의 `D` prefix는 signed 32-bit이며 좌변·우변 순서를 보존한다.
직접 `P/M/K/F/T/C/L/D` word device와 폭 안의 signed decimal literal 밖의 형식은
`partial`이다. 이 정적 구문은 CPU별 device range나 runtime 값·실행·조건 참을
증명하지 않는다. 이 보고서는
현재 PLC runtime, field wiring 또는 이름에서 추측한 프로그램 목적을 증명하지 않는다.

`project.structure_index`는 같은 Host-bound 세션·PID/HWND·정규화된 프로젝트
경로·SHA-256에 정확히 결속된 XGWX에서 configuration, task, code unit, symbol,
device, 명시적 call token, parameter/module, communication을 bounded index로 읽는다.
Host는 raw XML·binary·연결 blob·초기화 command·secret을 Agent에 전달하지 않고,
digest와 pagination을 검증한 대표 `structureFacts`만 투영한다. 이 근거는 정적
XML 관측이므로 task runtime 순서, 암시적 vendor call/FB 의미, 외부 동작을
증명하지 않으며 Engineering Analysis v4의 `projectStructure`와
`project_structure` coverage는 항상 해당 partial/limitation을 보존한다.

`project.hardware_context`는 같은 세션·PID/HWND·프로젝트 경로·SHA-256에 묶인
XGWX XML 관측을 `xg5000.project.hardware-context/1.0`과 canonical
`context_digest`로 반환한다. 후속 `program.change_target_analysis`는 내부 device에
CPU, timer 변경에 CPU와 실제 timer area, 물리 `P` 입출력 target이나 causal
impact에 I/O mapping·module별 address range·외부 wiring을 `required_context`로
요구한다. 해당 근거가 하나라도 없거나 충돌하면 후보를 만들지 않는다.

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

2026-08-11 현재 구현은 다음 수준이다.

- 설비 목적·안전과 함께 운전/복구 mode와 성능·인수 기준을 받을 수 있는
  신규 프로젝트 brief와 MCP·mutation 도구가 없는 계획 대화
- 계획·기존 엔지니어링 양쪽에서 사용할 수 있는 Host read-only 지식 검색,
  PDF·Markdown·텍스트와 CSV/TSV I/O 목록 가져오기, 페이지 또는 원본 행 인용,
  제조사·제품군·버전 격리
- 사람 확인 전에는 검색되지 않는 현장 기억·작성 선호와 12개 벤더 중립 제어
  패턴
- 사용자 확인 사실과 에이전트 제안, 미결 결정을 분리한 서버 소유
  `Engineering Plan v2`, canonical digest와 SQLite 영속 저장
- 최신 ready plan·project brief·첨부 source revision의 정확한 digest와 Host가
  계산한 전체 native 실행 항목 집합에 묶인 사람 검토 기록, 값별 명시적 확인과
  구조화된 Web 검토
- hardware/I/O, 운전·복구 mode, 성능·인수 기준이 비어 있으면 이를 생략하지
  않고 `unknown/blocking`으로 올리는 Host 계획 gate
- 네이티브 8개 collection을 `confirmed` 또는 근거가 검증된
  `not_applicable`로 구분하고 N/A collection의 가짜 filler 로직 생성을 건너뛰는
  Agent·Host·MCP 공통 `collectionScope` 계약
- 프로젝트 이름·PLC 시리즈·CPU 모델·첫 프로그램·LD 언어·설명을 계획의
  semantic 검토 항목으로 포함하고, 검토 뒤 기술 설정 화면에서는 해당 값을
  수정할 수 없게 한 exact 자동 인계
- 이 native XG5000 기술 구성 요청을 최신 reviewed plan 전체, conversation,
  message, plan/brief/source digest와 reviewed section·item 전체 집합에 결합한
  `engineered_control_program`
  build bundle, 일회성 승인, XGK/XGB mnemonic exact readback·LD 변환·Program
  Check·저장 hash receipt
- 동일 길이 LD operand 한 개의 제한된 변경 계약과, raw payload를 공개하지 않는
  developer-certified 기존 프로그램 전체 MNE 교체 계약
- 영향·위험·가정·대안·acceptance·관찰 신호·중단 조건과 10개 필수 coverage를
  검사하는 Host 완전성 gate
- `잠깐`, `시험`, `temporary`, `trial` 요청을 서버가 감지하고 사용자가
  `분석만` 또는 `저장되는 오프라인 후보 revision`을 명시적으로 선택하기 전
  실행 계약 생성을 차단하는 request-scope gate
- agent가 제안한 confirmed coverage의 evidence ID를 같은 conversation·run의
  실제 `evidence.added` 기록과 생성·승인·실행 시점마다 다시 결합하는 gate
- 기존 프로젝트 답변의 `Engineering Analysis v4`에 Host evidence와 결속된 exact
  program·network/rung·visual row·token 위치, relation의 full causal device chain,
  영역별 status·count·truncation·limitations를 담고 이를 `근거 위치`, `신호 관계`,
  `조사 범위`로 표시하는 Agent·Host·Web 계약. 가설은 확인 계획과 별도로
  open/verified/rejected 상태, 실제 outcome과 검증 evidence를 기록하며 open 가설은
  mission 완료를 막는다. 현재 프로그램 catalog에서 exact
  이름이 하나만 일치하는 위치에만 프로그램 전면화용 `XG5000에서 열기`를 제공
- bounded write·physical·sequence·timer/counter·multiple-writer·`APPLICATION`
  inventory와 정적 coverage를 반환하고 미지원 `APPLICATION`을 `partial`로
  보존하는 `project.behavior_report`
- cache를 우회한 전체 LD 재읽기에서 exact value token을 program·network·rung
  identity/digest, 현재·제안 의미, project-wide reference·causal path·multiple
  writer·물리 P 출력 후보와 한 receipt로 결속하고, 같은 receipt를 변경 계획의
  target/value/direct/indirect/safety coverage에 강제하는 gate
- 대상·digest 승인, native diff·XG5000 check·save와 crash reconciliation
- 승인과 실제 작업 복사본 변경 실행을 분리하고 `수정 요청`,
  `XG5000에서 프로그램 열기`를 제공하는 Web 검토 흐름. 현재는 정확한
  network·token 이동이 아니라 검증된 프로그램 전면화까지 지원
- Host가 선택한 LS FEnet endpoint에서 단발 status/device read뿐 아니라
  1~16개 exact direct variable을 최대 30초 동안 읽어 샘플·전이·변수별 변화 요약과
  전후 CPU 상태를 하나의 read-only runtime evidence로 만드는 bounded trace

아직 전체 작업 흐름이 구현된 것은 아니다.

- 신규 계획의 item별 결정 사유, 직전 유효 계획 기준 revision diff와 확인된
  문서 첨부는 Host 대화 이력·digest에 결합해 구현했다. 별도 CSV/TSV I/O 목록은
  헤더·열 위치·원본 행 locator를 보존한 project document로 첨부할 수 있다.
  목록의 각 행을 typed I/O 설계로 자동 확정하지는 않으며, 제안된 mapping 전체를
  사람 검토 v2 항목으로 확인한 뒤에만 실행 권한이 된다. 확인된 기술 설정은
  생성 화면에 exact 값으로 자동 전달되며 그 화면에서 다시 수정할 수 없다.
- typed I/O·mode·STEP sequence·TON timer·latched alarm·recovery·control
  network·process interlock은 하나의 deterministic LD 생성 phase와 native
  readback 계약에 연결됐다. 외부 안전장치의 실제 배선 확인과 현장 acceptance는
  별도 evidence로 남아 있다.
- sequence는 `require_start_release` 또는 명시적으로 검토된 `continuous`
  재기동 정책을 가진다. 기본 정책에서는 terminal STEP·reset·recovery가 START
  해제 조건을 포함하므로 TON 완료나 fault reset 뒤 level START로 재기동하지 않는다.
- 신규 생성 XG-SIM receipt는 Host SQLite가 digest-bound
  `OBSERVING → ACCEPTED/REVERTED` 상태로 소유하고, 명시적 채택 뒤에만 보호
  working copy를 활성 대상으로 결속한다. 기존 프로젝트에서는 V6 완전-rung patch
  하나가 승인된 target·독립 재열기·compiler-derived 단일-program/단일-writer P/M
  profile의 정상/deny/restore 관찰을 거쳐 `SIM_STOPPED → OBSERVING`에 머문다. 사용자가
  Keep하면 같은 journal을 commit하고 Revert하면 exact MNE/LD baseline을 복원한다.
  다른 부분 변경과 전체 벤더 edit set의 동일 수명주기는 아직 없다.
- 현재 변경 target은 두 경로로 분리된다. 단일 operand 경로는 program ID·native
  value token·network/rung identity와 digest·동일 길이 operand로 고정한다. 전체
  프로그램 경로는 exact program ID/name, typed instruction IR, baseline MNE/LD digest,
  runtime/capability/lease generation과 승인 target digest로 고정하며, Program Check·
  저장·별도 PID 재열기를 모두 통과해야 commit한다. 두 경로 모두 선택 network/rung이 partial,
  causal path가 제한을 넘어 잘렸거나 새 device가 현재 프로젝트에서 관찰되지 않으면
  후보 생성을 막는다. multiple writer와 정적 경로의 물리 P 출력 후보는 승인 화면에
  그대로 노출한다. 실제 대동 XGWX에서 CPU `XBC-DN/DP64H`, 활성 timer area
  `T0-T499=100ms`·`T500-T999=10ms`·`T1000-T1023=1ms`, 비활성 100us
  `0..0` sentinel, `XBC-DN/DP64H`·`XBE-DC08A` module inventory와 전역
  DI/DO/AI/AO offset을 읽었다. exact XBC-H CPU와 XML slot에는 LS 공식 고정
  4-word profile을 결합해 slot 1 `XBE-DC08A`의 `P00040-P0007F` reservation을
  `documented`로 결속한다. 이는 project-observed terminal point가 아니다. 다른 CPU
  family의 module/point range·외부 배선·module health·현재 PLC runtime은 미관측이므로
  물리 P 입출력 영향 변경은 계속 fail-closed다.
  내부/타이머 변경도 분석별 `required_context`가 모두 관측된 경우에만 후보가 된다.
- 신규 생성의 독립 재열기와 exact 재추출은 별도 XG5000 PID에서 검증됐다.
  XGB `XBC-DN/DP64H` 단일 control network build는 실제 XG-SIM에서 초기 정상,
  network 정상·deny·restore, process interlock 정상·상실·복원의 5개 scope-bound
  시나리오, vendor STOP 상태 3회 연속 관측과 입력 원복을
  v3 `SIMULATION_VERIFIED` receipt로 닫고 사용자가 digest-bound trial을 채택했다.
  신규 v4 경로는 reviewed ladder IR의 모든 control network/output을 ordered
  `networkRungs`로 재계산하고, simulator 동작 전에 전체 network·process interlock과
  보호 출력 byte를 검증한다. 각 network의 정상·deny·restore와 적용 가능한 선택
  시나리오가 모두 통과해야 하나의 receipt가 되며 부분 성공은 허용하지 않는다.
  대표 복수 control network build와 제한된 기존-project V6 rung patch의 실제 XG-SIM
  수락, 그 밖의 일반 기존-project simulation 및 실제 PLC 관찰·run edit는 별도 증거가
  생기기 전까지 완료로 주장하지 않는다.

구현된 순서와 다음 coherent increment는 다음이다.

1. 완료: 계획 결과를 서버 소유 structured artifact와 digest로 저장
2. 완료: exact plan·brief·source revision과 Host가 계산한 전체 semantic item
   universe에 대한 v2 사람 검토 기록
3. 완료: 구조화된 계획 검토 화면, item별 설계 사유, 직전 계획 대비
   변경 필드·섹션·추가·삭제 항목, 결정 필요 목록과 빈 핵심 항목의 blocking
4. 완료: reviewed 프로젝트 이름·PLC·CPU·프로그램·언어·설명과 typed
   I/O·mode·control network·process interlock을 build bundle로 만들고 bundle
   ID·승인 digest·XG5000 description provenance·mnemonic/LD/check/save receipt에 결합
5. 완료: 현재 오프라인 부분 변경의 coverage·acceptance·request-scope·evidence
   계약과 승인 gate. 구현 완료·실제 acceptance 미완료: typed 13-command IR 기반
   기존 프로그램 전체 교체, private baseline, signed capability, 일회성 승인,
   exact MNE/LD readback·Program Check·저장·독립 재열기·checkpoint 한정 rollback
6. 완료·runtime 검증: STEP·TON timer·latched alarm·recovery를
   `engineered_control_program` 생성 phase와 exact native readback·검사·저장
   receipt에 결합
7. 완료·실제 XG5000 검증: 보호 working copy를 새 PID에서 재열기·재추출하고
   exact mnemonic·LD·Program Check·disk hash를 `REOPEN_VERIFIED` receipt에 결합
8. 구현 완료·다중 실기 수락 미완료: 신규 build의 모든 control network를 다루는
   XG-SIM receipt v4, Host 독립 재계산과 trial 영속화. 실제 증거는 단일 network
   v3 trial의 `OBSERVING → ACCEPTED` 결정과 채택 후 작업공간 결속까지이며,
   대표 다중 network build의 실기 수락은 남아 있다.
9. 구현 완료·실제 acceptance 미완료: 임의 기존 프로젝트의 exact program·network/rung·
   visual row·token 위치, relation/full causal chain, explicit coverage·truncation·
   limitations와 bounded behavior inventory를 구조화한다. Host가 원시 분석 결과에서
   canonical location/relation/coverage receipt를 만들고 Agent 출력과 필드 단위로
   대조하며, 고카디널리티 원문 대신 512 KB 이하의 receipt projection만 Agent wire로
   전달한다. 실제 대표 XG5000 프로젝트에서 이 end-to-end 분석 계약을 수락하는 절차는
   아직 남아 있다.
10. 완료·실제 XGWX 검증: `project.hardware_context`의 schema/context digest,
    CPU·timer-area parameter·I/O module inventory·전역 device offset readback
11. 구현 완료·실기 수락 미완료: 기존-project V6 완전-rung patch의 승인 target과
    독립 재열기 뒤 compiler-derived P/M 정상/deny/restore profile을 XG-SIM에서 검증하고,
    STOP·입력 원복 receipt를 `OBSERVING`에 저장해 Keep 또는 exact Revert로 종료
12. 다음: 다른 CPU family의 module/point address profile·외부 배선·module health·현재 PLC runtime 근거를
    확보하고 승인된 실제 변경의 end-to-end acceptance 수행

## 6. 공식 제품에서 채택한 설계 근거

- [LS XG5000 User Manual](https://sol.ls-electric.com/uploads/document/16411767742780/XG5000_Manual_V2.5_202012_EN.pdf)
- [LS XGK/XGB Instructions Manual V2.2 §2.6.5](https://www.ls-electric.com/upload/customer/download/f46ceeb8-094e-4a41-b623-7c9a60c7880b/XGK_XGB_Instructions_Manual_Eng_V2.2.pdf)
- [Mitsubishi GX Works3 Operating Manual](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh081215eng/sh081215engar.pdf)
- [Rockwell Test Accepted Edits](https://www.rockwellautomation.com/en-us/docs/studio-5000-logix-designer/38-01/contents-ditamap/studio-5000-logix-designer/navigating-the-application/-product_name_rsl5k--application-main-window/-product_name_rsl5k--application-commands/test-accepted-program-phase-edits-command.html)
- [Rockwell Online Import Considerations](https://www.rockwellautomation.com/en-ro/docs/studio-5000-logix-designer/38-02/contents-ditamap/studio-5000-logix-designer/import-and-export/import-online-considerations.html)
- [Siemens Project Comparison](https://support.industry.siemens.com/cs/attachments/98944323/Project_comparison_en-US.pdf)
- [GitHub Copilot research, plan and iterate](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/research-plan-iterate)
