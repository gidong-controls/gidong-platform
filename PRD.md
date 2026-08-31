# 기동(GIDONG) PLC 엔지니어링 에이전트 제품 요구사항

| 항목 | 내용 |
| --- | --- |
| 문서 상태 | 제품 기준선 v1 · Living PRD |
| 최종 갱신 | 2026-08-31 |
| 제품 상태 | 개발 중 · production release 아님 |
| 제품 브랜드 | 기동 / GIDONG |
| 제품 형태 | 단일 설치·단일 실행 진입점, 내부적으로 격리된 멀티프로세스 sidecar |
| 소스 구조 | 공개 `gidong-platform`, 비공개 `gidong-agent`·`gidong-plc-mcp`, 별도 vendor repository `controlstudio-xg5000-cli` |

## 0. 이 문서의 목적과 판정 규칙

이 문서는 기동이 **누구의 어떤 문제를 해결하는 제품인지**, 사용자가 얻게 될
결과가 무엇인지, 제품이 반드시 지켜야 할 안전 원칙과 출시 조건이 무엇인지,
그리고 현재 어디까지 실제로 완료됐는지를 한곳에서 판단하기 위한 최상위
제품 문서다.

잘 만든 PRD는 기능 목록이나 개발 일지가 아니다. 제품 목표, 사용자 흐름,
요구사항, 비범위, 성공 기준과 검증 상태를 연결하여 다음 질문에 같은 답을 주어야
한다.

1. 왜 이 제품을 만드는가?
2. 누구를 위한 제품인가?
3. 사용자는 어떤 결과를 얻는가?
4. 제품은 무엇을 반드시 보장하고 무엇을 절대 해서는 안 되는가?
5. 무엇을 근거로 기능 또는 제품을 완료라고 판정하는가?
6. 현재 검증된 것과 아직 주장할 수 없는 것은 무엇인가?

이 문서와 세부 문서가 충돌하면 제품 범위와 완료 판정에는 이 문서가 우선한다.
다만 이 문서의 날짜가 있는 현황 기록은 최신의 명시적 제품 요구를 대체하지 않는다.
현재 구현·검증 상태는 source와 runtime evidence로 판단하되, 아직 구현되지 않았거나
검증되지 않은 범위를 제품 요구에서 제외하는 근거로 사용하지 않는다.
세부 사용자 경험은 [GUI 명세](docs/CONTROLSTUDIO_GUI_HANDOFF.md)와
[엔지니어링 작업 흐름](docs/ENGINEERING_COLLABORATION_WORKFLOWS.md), 실행 책임은
[제품 아키텍처](docs/PRODUCT_ARCHITECTURE.md), 배포와 버전 결속은
[제품 배포·버전 정책](docs/refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md)을
따른다.

### 상태 용어

| 상태 | 의미 |
| --- | --- |
| 요구됨 | 제품 범위에 포함되지만 구현 또는 검증이 끝나지 않음 |
| 구현됨 | 제품 runtime 코드와 사용자 입력·출력 경로가 존재함 |
| runtime 검증됨 | production 의존성으로 component load·schema·manifest·build를 확인함. 실제 장비 성공을 뜻하지 않음 |
| 실제 검증됨 | 지정한 실제 IDE·시뮬레이터·장비 환경에서 직접 실행한 증거가 있음 |
| 부분 완료 | 제한된 입력·버전·경로에서만 구현 또는 검증됨 |
| `NOT_EXECUTED` | 안전 조건이나 실행 환경이 없어 현재 재실행하지 않았음 |
| 미구현·미검증 | 완료로 주장하거나 사용자에게 활성화할 수 없음 |

코드, API 또는 버튼이 존재하는 것만으로 제품 기능을 완료로 판정하지 않는다.
PLC·IDE와 상호작용하는 기능은 지원 환경에서의 실제 증거가 있어야
`실제 검증됨`으로 승격한다.

## 1. 제품 정의

기동은 PLC 프로그램의 신규 구축, 기존 설비 이해, 고장 진단, 변경 계획,
검사·시뮬레이션과 승인된 실행을 하나의 작업공간에서 연결하는
**증거 중심 PLC 엔지니어링 에이전트**다.

기동의 핵심 약속은 다음과 같다.

> 에이전트는 분석하고 제안한다. Platform은 사용자·대상·승인·감사를 결속한다.
> PLC MCP는 실제 대상을 다시 검증하고 변경·readback·rollback을 독립 강제한다.

기동은 단순 챗봇, 범용 코드 생성기, XG5000 화면 자동화 도구가 아니다. 사용자가
어떤 근거로 무엇을 승인하고 실제 대상에 어떤 변화가 생겼는지 끝까지 확인할 수
있는 엔지니어링 제품이어야 한다.

### 제품이 해결하는 문제

PLC 유지보수와 신규 구축 정보는 벤더 IDE, 프로젝트 파일, 현장 문서, 장비 상태,
담당자 경험과 변경 이력에 흩어져 있다. 이 때문에 다음 문제가 반복된다.

- 기존 로직의 의도와 영향 범위를 파악하는 데 오래 걸린다.
- 프로젝트 파일과 실제 대상이 일치하는지 확신하기 어렵다.
- 변경 계획, 승인, 실제 적용, 검증과 원복 기록이 분리된다.
- 숙련자의 현장 지식이 개인에게 남고 재사용 가능한 근거가 되지 못한다.
- AI가 그럴듯한 답을 하더라도 실제 PLC 작업의 권한과 안전을 맡길 수 없다.
- 벤더가 달라질 때마다 사용자 경험과 안전 절차가 다시 만들어진다.

기동은 원본과 대상의 정체성을 먼저 고정하고, 분석 근거와 대안을 보여 주며,
사용자가 승인한 정확한 변경만 실행하고, 실제 readback과 journal로 결과를
확정하는 방식으로 이 문제를 해결한다.

## 2. 목표 사용자와 핵심 작업

| 사용자 | 해결하려는 핵심 작업 |
| --- | --- |
| 제어·자동화 엔지니어 | 요구사항을 PLC 구조와 로직으로 설계하고, 기존 프로그램의 동작과 영향 범위를 빠르게 이해한다. |
| 설비 유지보수 엔지니어 | 알람·정지·품질 문제의 원인을 신호, 인터록, 시퀀스와 연결해 진단하고 안전한 대안을 비교한다. |
| 시스템 통합 담당자 | 신규 프로젝트, 하드웨어·I/O, 운전 모드, 시퀀스, 인터록, 알람과 시험 시나리오를 일관된 산출물로 만든다. |
| 검토·승인 책임자 | 변경 대상, 계획, diff, 위험, 검증과 rollback 가능성을 확인하고 정확한 범위만 승인한다. |
| 제품·릴리스 운영자 | 독립 버전의 Platform·Agent·MCP를 검증된 하나의 제품 조합으로 배포하고 감사 가능한 상태로 유지한다. |

초기 제품의 주 사용 환경은 Windows와 LS ELECTRIC XG5000/XG-SIM이다.
Mitsubishi와 Rockwell 등 다른 벤더는 같은 상위 사용자 경험과 안전 계약을
유지하는 후속 adapter 범위다.

## 3. 제품 목표와 비범위

### 목표

1. 신규 구축과 기존 설비 유지보수를 같은 작업공간과 증거 모델로 지원한다.
2. 로컬 프로젝트 열기, PLC에서 복원하기, 새 프로젝트 시작하기를 동등한 진입
   경로로 제공한다.
3. 프로그램 구조, 신호, 인터록, 시퀀스, 알람과 인과관계를 근거와 함께 설명한다.
4. 진단 결과 하나를 단정하지 않고 가설, 반증 근거, 대안과 예상 영향을 비교한다.
5. 변경을 계획·diff·승인·적용·readback·검사·저장·재열기·시뮬레이션·rollback의
   닫힌 트랜잭션으로 만든다.
6. 사용자의 원본과 작업공간을 보존하면서 모델과 파생 인덱스는 교체 가능하게 한다.
7. 벤더가 달라도 사용자 흐름과 안전 규칙은 유지하고 실제 실행만 adapter로 분리한다.
8. 일반 사용자에게는 저장소나 sidecar가 아니라 하나의 기동 제품만 제공한다.

### 비범위

- XG5000과 같은 벤더 IDE의 범용 래더 편집 화면 전체를 복제하지 않는다.
- AI 판단을 사람의 승인, 설비 안전 절차, 하드웨어 인터록 또는 법정 검사의
  대체물로 취급하지 않는다.
- 근거가 없는 자동 PLC 쓰기, force, mode change, download 또는 run edit을
  활성화하지 않는다.
- 로컬 합성 결과나 정적 검사를 실제 PLC 검증이라고 부르지 않는다.
- 저장소 분리, 프로세스 기동 또는 패키징 성공을 전체 제품 완성으로 확대하지 않는다.
- 이번 제품에서 지원하지 않는 벤더·CPU·IDE 버전을 추정으로 호환 처리하지 않는다.

## 4. 핵심 사용자 흐름

### 4.1 시작과 작업공간 선택

사용자는 하나의 기동 아이콘으로 제품을 실행한다. Platform은 내부 Agent와 MCP의
버전·hash·계약을 확인하고 필요한 sidecar를 감독한다. 사용자는 다음 중 하나로
작업을 시작한다.

1. 새 PLC 프로젝트 구축
2. 로컬 프로젝트 파일 또는 기존 기동 작업공간 열기
3. 연결된 컨트롤러에서 프로젝트와 파라미터를 안전하게 읽어 작업공간 생성

원본은 불변으로 보존하고 모든 변경은 식별 가능한 working copy에서 수행한다.

### 4.2 신규 구축

사용자는 설비 목적, I/O, 운전 모드, 시퀀스, 인터록, 알람과 복구 조건을 대화와
구조화 화면으로 정리한다. 기동은 빠진 요구사항과 결정이 필요한 항목을 표시하고
다음 산출물을 연결한다.

- 하드웨어·I/O 구성
- 운전 모드와 상태 전이
- 시퀀스와 인터록
- 알람·복구 정책
- 프로그램 구조와 로직
- 검사·시뮬레이션 시나리오

네이티브 설계의 I/O, mode, control network, timer, sequence, alarm, recovery,
safety 묶음은 각각 `confirmed` 또는 근거가 있는 `not_applicable` 범위 결정을
가진다. 적용되지 않는 묶음을 가짜 항목으로 채우지 않으며, 빈 값·미확인은
`not_applicable`로 간주하지 않고 결정 필요 상태로 남긴다. 물리·위험 출력이 있으면
process interlock 또는 외부 안전 경계를 반드시 기록하며 safety 묶음을 N/A로 둘 수
없다. 이전 scope 없는 계획은 읽을 수 있지만 새 build 전 다시 계획해야 한다.

검토가 끝나지 않은 계획을 실제 프로젝트 생성이나 PLC 작업 승인으로 간주하지 않는다.

### 4.3 기존 프로젝트 이해와 유지보수

기동은 원본·working copy·IDE session을 식별한 뒤 프로그램 목록, 사용 신호,
cross-reference, 로직과 시퀀스를 추출한다. 사용자는 자연어 질문에서 시작하되
결과를 프로그램 위치, 원본 문서, 실제 readback과 연결해 확인할 수 있어야 한다.

### 4.4 온라인 읽기와 진단

연결 대상의 strong identity와 프로젝트 일치가 확인된 경우에만 device value,
상태 변화, trace와 알람을 읽는다. 기동은 관측 사실, 추론, 가설과 확인되지 않은
부분을 구분하고, 필요한 추가 관측과 안전한 시험 순서를 제안한다.

### 4.5 승인된 변경

모든 변경은 다음 순서를 닫힌 트랜잭션으로 따른다.

```text
대상 재확인 → 백업 → 변경 계획·diff → 사용자 승인
→ 최소 변경 → native readback → vendor check → 저장
→ 재열기·재추출 → 시뮬레이션/시험 → commit 또는 rollback → 감사 기록
```

대상, 계획, 인자 또는 권한이 승인 뒤 바뀌면 승인은 무효다. 결과가 불명확하면
성공으로 표시하거나 자동 재시도하지 않고 격리·reconciliation한다.

## 5. 제품 요구사항

### 5.1 작업공간과 증거

| ID | 요구사항 | 합격 기준 |
| --- | --- | --- |
| WS-01 | 원본과 working copy를 분리한다. | 원본 hash와 revision이 보존되고 모든 변경은 별도 working copy에서만 발생한다. |
| WS-02 | 프로젝트 출처와 현재 대상을 명시한다. | local, restored-from-controller, new-build 출처와 target identity를 모든 run·evidence에서 추적할 수 있다. |
| WS-03 | 작업을 재개할 수 있다. | 대화, run, 승인, evidence와 미완료 operation을 재시작 뒤 복원한다. |
| WS-04 | 근거를 원문으로 되돌아갈 수 있게 한다. | 분석 결과가 프로그램 위치, 문서 revision, capture 또는 readback reference를 제공한다. |
| WS-05 | 사용자 데이터 수명주기를 Platform이 소유한다. | Agent index를 지워도 원본, workspace, citation, confirmation과 audit가 보존된다. |

### 5.2 분석·설계·진단

| ID | 요구사항 | 합격 기준 |
| --- | --- | --- |
| ENG-01 | 프로그램 구조와 신호 관계를 설명한다. | 프로그램·network/rung·visual row/token 위치, cross-reference·full causal chain, 조사 coverage·truncation·limitations와 근거를 함께 표시한다. |
| ENG-02 | 신규 구축 요구를 구조화한다. | 하드웨어, I/O, mode, sequence, interlock, alarm, recovery와 acceptance 항목의 누락·충돌을 표시한다. |
| ENG-03 | 사실과 추론을 구분한다. | 관측 사실, 파생 해석, 가설, 불확실성과 추가 확인 항목이 분리된다. |
| ENG-04 | 대안을 비교한다. | 각 대안의 영향, 위험, 필요한 변경, 검증 방법과 rollback을 보여 준다. |
| ENG-05 | PLC 작업 지식에 출처를 연결한다. | 검색·기억·모델 답변이 권한이나 target identity를 만들지 않으며 사용한 source를 추적할 수 있다. |

### 5.3 실행·승인·감사

| ID | 요구사항 | 합격 기준 |
| --- | --- | --- |
| EXE-01 | Agent 제안과 실행 권한을 분리한다. | Agent는 `ExternalToolRequest`만 반환하고 raw MCP connection을 소유하지 않는다. |
| EXE-02 | Platform이 실행을 중계한다. | `ExecutionBroker`만 allowlist, schema, capability, target과 approval을 확인하고 MCP를 호출한다. |
| EXE-03 | 승인을 정확한 변경에 결속한다. | 승인에 actor, run, target digest, tool, arguments/plan digest, capability, expiry와 single-use nonce가 포함된다. |
| EXE-04 | MCP가 독립 검증한다. | MCP가 target·precondition·approval을 다시 확인하고 operation journal을 먼저 기록한다. |
| EXE-05 | 실제 결과를 readback으로 확정한다. | apply 응답만으로 성공 처리하지 않고 native readback, check, save/reopen과 evidence를 검증한다. |
| EXE-06 | 실패와 불명확한 결과를 안전하게 처리한다. | 자동 중복 실행 없이 rollback, `ABORTED_NO_MUTATION`, `ROLLED_BACK_SAVED`, `COMMITTED` 또는 `QUARANTINED`로 reconciliation한다. |
| EXE-07 | 감사 연결성을 보장한다. | conversation, run, tool call, operation, approval와 target ID를 Platform audit와 MCP journal 사이에서 추적한다. |
| EXE-08 | 신규 PLC 설계 제안과 build 권한을 분리한다. | Host가 canonical 설계에서 계산한 모든 scope·주소·상태·시간·관계 항목을 사람이 각각 확인하고, plan·최신 brief·첨부 source revision digest에 결속한 v2 review만 build authority가 된다. 자연어 해석, Agent 출력 또는 이전 section-only review는 실행 권한이 아니다. |

### 5.4 사용자 제품과 배포

| ID | 요구사항 | 합격 기준 |
| --- | --- | --- |
| UX-01 | 하나의 사용자 제품으로 제공한다. | 일반 사용자가 설치 파일, 실행 진입점과 시작 메뉴 항목을 각각 하나만 본다. |
| UX-02 | 내부 구성요소를 자동 관리한다. | Platform이 Agent/MCP를 시작·health check·종료하고 독립 실패를 사용자에게 설명한다. |
| REL-01 | private source를 소비자 설치와 분리한다. | installer가 private Git repository를 clone하거나 source-control credential을 요구하지 않는다. |
| REL-02 | 정확한 조합만 실행한다. | BOM이 Platform, Agent, MCP, contracts의 exact version, contract set, hash와 signature metadata를 고정한다. |
| REL-03 | 전체 조합을 검증·업데이트한다. | 컴포넌트 하나가 바뀌어도 새 product release와 BOM을 만들고 packaged runtime handshake·readback을 다시 확인한다. |
| REL-04 | 사용자 데이터를 binary와 분리한다. | 업데이트·rollback·제거가 원본 문서, workspace와 audit를 기본적으로 삭제하지 않는다. |
| REL-05 | 공개 Platform을 독립 개발할 수 있다. | 공개 contracts만으로 production build가 가능하고 private component는 versioned protocol artifact로 결합한다. |

## 6. 제품·저장소·프로세스 경계

```mermaid
flowchart LR
    U["사용자"] --> P["gidong-platform (public)<br/>UI · Host · workspace · approval · audit"]
    P -->|"sanitized context / Agent Protocol"| A["gidong-agent (private)<br/>Pydantic AI · prompt · derived RAG"]
    A -->|"analysis / ExternalToolRequest"| P
    P -->|"validated target / approval / MCP contract"| M["gidong-plc-mcp (private)<br/>vendor action · journal · readback · rollback"]
    M -->|"migrated vendor commands"| C["controlstudio-xg5000-cli (separate vendor repository)<br/>deterministic subprocess · JSON receipt"]
    C --> V
    M -->|"receipt / evidence"| P
    M <--> V["XG5000 · XG-SIM · PLC"]
```

| 저장소 | 공개 범위 | 소유하는 권위 | 소유하지 않는 것 |
| --- | --- | --- | --- |
| `gidong-platform` | public | Launcher/UI/Host, 공개 contracts, 사용자 원본·workspace, conversation/run, target binding, approval, `ExecutionBroker`, audit, process supervision, packaging/BOM | private prompt·RAG 정책, vendor native 실행 구현, MCP operation truth |
| `gidong-agent` | private | 공식 Pydantic AI sidecar, 모델 adapter, prompt, 추론·계획, 파생 RAG index/ranking/eval, Agent checkpoint | raw MCP, PID/HWND/endpoint, approval secret, 원본 문서 authority, 실제 변경 성공 판정 |
| `gidong-plc-mcp` | private | vendor adapter, target/precondition 재검증, operation lock/journal, apply/check/readback/rollback/reconciliation | 모델·prompt, Platform UI/API, 사용자 전체 대화와 원본 문서 authority |
| `controlstudio-xg5000-cli` | separate vendor repository | 이관된 XG5000 명령의 deterministic subprocess/JSON request-result 경계와 vendor receipt | Agent/Platform approval·audit 권위, MCP target/precondition 재검증·operation journal, 아직 MCP 소유 `xg5000_control`에 남은 legacy-native backend 전체 |

공개 계약의 source of truth는 Platform의 `schemas/`다. Agent와 MCP는 공개 계약을
구현하지만 private 구현을 Platform source나 public Git history에 넣지 않는다.

### 데이터 권위

| 데이터 | 권위 소유자 | 원칙 |
| --- | --- | --- |
| 사용자 원본 문서·프로젝트 | Platform | 원본, revision, hash, citation과 삭제·보존 정책을 관리한다. |
| workspace·working copy | Platform | 현재 작업 대상과 사용자 workflow의 기준이다. |
| 파생 chunk·embedding·index·ranking | Agent | sanitize된 snapshot에서 재생성 가능해야 하며 원본을 대체하지 않는다. |
| 모델 대화 checkpoint | Agent | 분석 재개용이며 실제 side effect 성공의 근거가 아니다. |
| run·approval·audit | Platform | 사용자 결정과 tool 중계의 권위다. |
| 실제 변경·readback·rollback journal | PLC MCP | vendor mutation과 crash reconciliation의 최종 권위다. |

## 7. 안전·보안 불변조건

다음 조건은 편의, 모델 성능 또는 출시 일정 때문에 완화할 수 없다.

1. Agent는 raw MCP client, target identity, PID/HWND, controller endpoint 또는
   approval secret을 소유하지 않는다.
2. Platform 승인 없이 mutation을 실행하지 않는다.
3. Platform 승인이 있어도 MCP 재검증을 통과하지 못하면 실행하지 않는다.
4. target, project hash, plan, arguments, capability 또는 nonce가 다르면 fail closed한다.
5. 원본을 직접 변경하지 않고 확인된 working copy와 백업을 사용한다.
6. apply 응답만으로 성공을 선언하지 않고 native readback과 journal을 확인한다.
7. 결과가 불명확한 mutation을 자동 재시도하지 않는다.
8. 모델 출력, 검색 문서와 tool result는 비신뢰 입력이며 권한·승인·대상 정체성을
   만들 수 없다.
9. 공개 저장소의 reachable history에 private Agent/MCP 코드, prompt/eval,
   비공개 vendor payload, 사용자 데이터, 자격 증명이나 실제 endpoint를 넣지 않는다.
10. 실제 PLC에서 검증하지 않은 online capability는 비활성·fail-closed로 유지한다.

## 8. 단일 제품 배포와 버전 정책

일반 사용자는 세 저장소나 세 개의 독립 제품을 설치하지 않는다. 기동은 하나의
installer와 하나의 실행 진입점을 제공하고, Platform이 내부 runtime의 Agent와 MCP
sidecar를 자동으로 관리한다.

소비자 설치 시 private repository에서 코드를 내려받거나 빌드하지 않는다. private
release pipeline이 각 저장소를 독립 production build하여 만든 immutable artifact를
BOM으로 결속하고, 검증된 조합만 installer에 넣는다.

버전은 다음처럼 분리한다.

- Platform version
- Agent version
- PLC MCP version
- API/Event/Agent/MCP Product/Workspace/Audit contract version
- 위 exact 조합을 나타내는 Product release version

각 저장소는 독립적으로 개발하고 버전을 올릴 수 있지만, 일반 사용자에게 전달되는
조합은 `latest`가 아니라 BOM의 exact version·SHA-256·contract set·signature로
고정한다. 한 컴포넌트만 바뀌어도 새 product release를 만들고 전체 compatibility와
packaged runtime handshake와 사용자 경로 readback을 다시 검증한다. 업데이트와 rollback도 BOM으로 결속된 전체 조합이
원자적 단위다.

현재 `controlstudio_*`, `controlstudio-*.exe`, `controlstudio-contracts`는 호환성을
위해 남아 있는 기술 식별자다. 제품 브랜드와 저장소 이름은 GIDONG이지만 설치 화면,
실행 파일과 package namespace의 최종 GIDONG 전환은 별도 호환성 마이그레이션과
회귀 검증을 거쳐야 한다.

## 9. 현재 상태 — 2026-08-31

### 9.1 저장소와 실행 경계

Platform·Agent·MCP의 core service 경계와 별도 vendor CLI repository가 존재한다.
CLI가 이관된 명령의 process/JSON 경계를 제공해도 native backend 전체가 MCP에서
물리적으로 분리됐다는 뜻은 아니다. 이 경계는 제품 전체나 실제
PLC online 기능이 완료됐다는 뜻이 아니다.

| 항목 | 상태 | 확인된 내용 |
| --- | --- | --- |
| 버전화된 공개 contracts | 완료·runtime 검증 | HTTP/Event/Agent/MCP Product/Workspace/Audit 계약과 Python/TypeScript DTO가 Platform에 있음 |
| Platform `ExecutionBroker` | 완료·runtime 검증 | tool proposal, allowlist/schema, target binding, approval, MCP relay와 audit 경계를 Platform이 소유함 |
| Agent의 raw MCP 직접 접근 제거 | 완료·runtime 검증 | Agent는 `ExternalToolRequest`만 제안하고 raw MCP/PID/HWND/approval secret을 갖지 않음 |
| Pydantic AI Agent sidecar | 완료·runtime 검증 | private Agent가 supervised NDJSON 프로세스로 동작하고 공개 Agent Protocol을 구현함 |
| MCP 독립 실행 경계 | 완료·runtime 검증 | Product Contract, operation journal, native readback과 quarantine 경계가 private MCP에 있으며, 이관된 명령은 별도 CLI subprocess/JSON 경계로 위임함 |
| 네 sibling local Git | 완료 | `gidong-platform`, `gidong-agent`, `gidong-plc-mcp`, `controlstudio-xg5000-cli`가 독립 Git source 경계다. CLI의 legacy-native backend 전체 추출·독립 release는 별도의 deferred 과제임 |
| 저장소 독립 production load/build | 부분 완료·runtime 검증 | Platform Web build와 Agent·Host·MCP·CLI의 결정론적 test/build 경로는 확인됐지만 전체 독립 release 증거는 아님 |
| packaged artifact 결합 | Beta 검증 완료 | 현재 Platform·Agent·MCP·contracts 조합을 exact BOM으로 조립하고 clean install 및 digest가 다른 이전 Beta에서의 in-place upgrade를 replay함. CLI 경계의 전체 release independence는 별도 gate임 |
| BOM과 단일 실행 조립 | Beta 검증 완료 | exact version/hash/contracts 조립, 단일 installer, current/previous 전체 조합 hash 및 사용자 SQLite byte 보존을 replay함. production signing은 남음 |
| 기준 revision | 확인됨 | core 세 repository의 기준 변경은 local commit으로 보존됐고, CLI 기준 revision `82ae877`은 push됨 |

이 기준일에 재현한 범위는 Agent·Host·MCP·CLI·Web의 다섯 deterministic command
group, 총 255 test execution과 Web build PASS다. 이 재현 범위는 현재 P0020
vertical의 실제 XG5000 UI edit·save·independent reopen을 증명하지 않는다. 아래에
기록한 XGK-CPUE와 29 mnemonic fixture의 과거 실제 evidence는 해당 fixture·시점에
한정해 유효하며, 현재 vertical이나 전체 제품 acceptance로 일반화하지 않는다.
PLC 연결·쓰기, 현재 controller runtime과 XG-SIM 시나리오는 이 재현 범위에
포함되지 않는다.

### 9.2 제품 기능

| 제품 범위 | 현재 상태 | 완료로 주장할 수 있는 범위 |
| --- | --- | --- |
| 단일 Launcher→Host→Web/PWA 사용자 흐름 | 부분 완료·runtime 검증 | planning conversation, workspace 상태와 sidecar lifecycle의 vertical slice |
| 로컬 프로젝트·불변 원본·working copy | 부분 완료·과거 실제 증거 | 지정 XG5000 working copy 열기, 저장된 workspace 재개와 원본 hash 보존 |
| 프로그램 목록·LD 추출·제한 분석 | 부분 완료·과거 실제 증거 | 지정 실제 XG5000 프로젝트 복사본의 제한된 read/analysis |
| 제한된 오프라인 LD 변경·원복 | 구현 확장 완료·구조 변경 실기 수락 미완료 | 지정 복사본의 한 operand 변경과 typed 13-command 전체 교체 외에, 검토 evidence에서 유일한 action/contact를 anchor로 삼은 한 행 insert/replace/delete, 2–16개 batch, 완전 rung 삽입·조건 교체·조건과 shared action 전체 원자 교체·삭제를 구현했다. 완전 rung 경로는 operand 기반 `contact`/`all_of`/`any_of` AST와 private baseline의 stack-validated condition slice 및 연속 shared action 1–16개를 결속하며, shared action 그룹을 쪼개지 않는다. 구조 후보는 MCP가 private baseline에서 컴파일하고 anchor·bounded context/slice·count·digest만 공개하며, 적용 직전 같은 baseline에서 재현되지 않으면 중단한다. 모든 MNE 변경은 project backup·signed capability·사람의 일회성 승인·lease generation과 exact readback·Program Check·저장 hash·독립 XG5000 PID 재열기를 공유한다. PLC write·online edit·controller 검증은 포함하지 않는다. `mne_rung_patch` V6만 사용자가 미리 선택한 `offline_native_reopen_xgsim` 범위에서 compiler가 증명한 단일-program·단일-writer P/M 출력 profile을 XG-SIM에서 정상/deny/restore로 관찰하고, `SIM_STOPPED → OBSERVING → Keep(COMMITTED)/Revert(ROLLED_BACK_SAVED)` 결정을 같은 transaction에 결속한다. 이 구조 변경과 XG-SIM 결정 경로의 실제 production acceptance는 아직 남아 있다. 현재 P0020 vertical은 이 과거 fixture evidence를 재실행하거나 일반화하지 않았다. |
| XG-SIM 시운전 경로 | 구현 완료·다중 실기 수락 미완료 | 신규 build의 reviewed 전체 network profile과 제한된 기존-project V6 rung patch의 compiler-derived P/M profile을 각각 RUN/STOP·입력 원복 receipt로 검증한다. 기존-project 경로는 PLC 연결·쓰기를 수행하지 않고, STOP·입력 원복이 확인된 뒤에만 사람이 저장 변경 유지 또는 exact baseline 원복을 결정한다. |
| 계획·지식·RAG | 구현·실제 모델 수락 검증 | 실제 model capability 5종과 sidecar lifecycle을 검증했다. 완전한 신규 요구 브리프에서 narrative/native 계획과 progress event를 거쳐 55초 안에 I/O·mode·network·timer·sequence·alarm·recovery·safety 8개 설계 컬렉션, 미결정 0건의 reviewed-plan 후보를 생성했다. |
| 신규 XG5000 제어 프로그램 | 부분 완료·과거 실제 XG5000 fixture 검증 | 프로젝트 이름·PLC 시리즈·CPU·첫 프로그램·LD 언어·설명을 계획의 사람 검토 항목으로 고정해 기술 설정 화면에 exact 자동 인계하고, reviewed typed I/O·mode·STEP sequence·TON timer·latched alarm·recovery·process interlock을 signed `engineered_control_program` bundle로 컴파일해 실제 XGK-CPUE fixture의 mnemonic readback·LD 변환·Program Check·저장 receipt에 결합함. 단순 control network와 `TON 1개 → timer ON permit → OUT` 지연 출력 설계는 Agent adapter가 canonical TON/OUT rung을 review 전에 materialize하며, 사람에게 표시된 rung만 build authority가 된다. sequence·alarm·recovery, step-bound logic 또는 복수 timer는 scan-order 정책을 임의로 만들지 않고 계속 explicit ordered rung을 요구한다. 다중 named program/task 생성은 vendor program-add primitive가 없어 아직 지원하지 않는다. 이 과거 fixture evidence는 현재 P0020 vertical의 실제 acceptance를 대체하지 않는다. |
| 독립 재열기 | 완료·과거 실제 XG5000 fixture 검증 | 보호 working copy를 생성 PID와 다른 새 XG5000 PID에서 열고 exact 29개 mnemonic·LD 최종 화면·Program Check 0 error·파일 hash 불변을 `REOPEN_VERIFIED` receipt로 결합함. 이 증거는 해당 fixture의 과거 run에 한정하며, 현재 P0020 vertical은 재실행하지 않았다. |
| XG-SIM acceptance | 구현 완료·다중 실기 수락 미완료 | 신규 build의 simulation receipt v4는 reviewed ladder IR의 모든 control network와 출력에 대해 ordered `networkRungs`를 재계산하고, simulator mutation 전에 전체 network·process interlock profile과 보호할 출력 byte를 검증한다. 한 번의 RUN/STOP·baseline 수명주기 안에서 network별 정상·deny·restore와 적용 가능한 mode·sequence·alarm·recovery·interlock 시나리오를 순차 실행하며, 일부 network만 성공한 receipt는 승인하지 않는다. 실제 수락 증거는 XGB `XBC-DN/DP64H` 단일 control network build의 receipt v3 5개 시나리오, exact rung IR/program digest, 공식 program write, vendor STOP 상태 3회 연속 관측, 입력 baseline 원복과 Host SQLite의 digest-bound `OBSERVING → ACCEPTED` 결정까지다. 이 실제 plan의 mode·timer·sequence·alarm·recovery collection은 명시적 N/A였다. 기존 프로젝트에서는 V6 완전-rung patch 한정으로 승인 target·재열기 receipt·compiler profile과 XG-SIM 정상/deny/restore 관찰을 결속하고 사용자가 Keep/Revert를 결정하는 수명주기가 구현됐다. 실제 PLC는 연결하거나 쓰지 않았고, 대표 복수 control network build와 이 제한된 기존-project 경로를 포함한 실제 수락은 아직 남아 있다. |
| 일반 기존 프로젝트 분석·진단 | 부분 구현·실제 acceptance 미완료 | `Engineering Analysis v4`는 Host evidence에 연결된 exact observation, program·network/rung·visual row·token 위치, relation과 full causal device chain, 영역별 coverage·truncation·limitations를 구조화한다. 진단 가설은 확인 계획뿐 아니라 `open/verified/rejected`, 실제 outcome과 현재 run의 verification evidence를 가지며 open 가설이 남은 mission은 완료할 수 없다. Web은 위치의 프로그램명이 현재 catalog에서 정확히 하나와 일치할 때만 `XG5000에서 열기`를 제공한다. `project.behavior_report`는 bounded write·physical·sequence·timer/counter·multiple-writer·APPLICATION inventory를 반환하며 미지원 `APPLICATION` 의미론은 `partial`로 남긴다. 실제 XGWX에서 관측한 `MOV literal→D`와 `DMOV literal/direct-D→direct-D`는 source read와 1/2-word destination write로 구조화한다. Direct-D `INC/DEC` signed/unsigned 16/32-bit 계열은 in-place read/write로 구조화하고 `P` variant는 rising-edge 실행으로 기록하며 double-word variant는 연속 두 D word를 포함한다. Non-D·간접 형식과 non-D destination `DMOV`는 계속 `partial`이다. 이는 정적 관측 구조이며 현재 PLC runtime, field wiring 또는 프로그램 목적의 증명이 아니다. 다중 LD 프로그램의 exact value token·network/rung digest·multiple writer·물리 P 출력 후보와 `xg5000.project.hardware-context/1.0`의 canonical `context_digest`를 Host 영수증에 결속한다. 실제 대동 XGWX에서 XBC-DN/DP64H, `T0-T499=100ms`·`T500-T999=10ms`·`T1000-T1023=1ms`, 비활성 100us `0..0` sentinel, XBC-DN/DP64H·XBE-DC08A module inventory와 전역 DI/DO/AI/AO offset을 확인했다. 또한 exact XBC-H CPU와 XML slot에만 LS 공식 고정 4-word profile을 적용해 slot 1을 `P00040-P0007F`의 `documented` reservation으로 결속한다. 이는 project-observed point range가 아니며 다른 CPU family, terminal point, 외부 배선·module health·현재 PLC runtime은 미관측이므로 물리 P 입출력 영향 후보는 계속 fail-closed다. 내부/타이머 후보도 해당 `required_context`가 닫힐 때만 가능하고 대표 production 프로젝트의 분석·승인 변경 end-to-end acceptance는 아직 없음 |
| LS FEnet online observe | 구현·실장비 미검증 | 사용자 지정 사설망 endpoint의 status/device read와 최대 30초 bounded trace가 존재한다. trace는 1~16개 변수의 샘플·전이·최소/최대·변화 횟수와 전후 CPU 상태를 한 receipt로 반환하지만 실제 장비 identity와 online project 일치 증거는 없음 |
| 실제 PLC write·run edit·force·mode·download | 미구현 또는 미검증 | 활성화하거나 완료로 주장하지 않음 |
| Mitsubishi·Rockwell adapter | 미구현 | 멀티벤더 상위 계약만 존재함 |

### 9.3 출시 준비

| 항목 | 상태 |
| --- | --- |
| offline portable/Beta installer 조립 | 구현·현재 변경 조합 clean install/upgrade 재검증 |
| 일반 사용자에게 하나의 설치·실행 단위 제공 | Beta 완료·production 서명/배포 미완료 |
| production Authenticode code signing | 미완료 |
| 접근 통제된 immutable artifact registry | 미완료 |
| 서명된 update manifest와 안전한 전체 조합 rollback | 미완료 |
| 사용자 데이터 migration·backup·복구 | 미완료 |
| 지원 Windows 설치·업데이트·제거 행렬 | 단일 Windows Beta clean install/in-place upgrade 검증·전체 행렬 및 제거 미검증 |
| production release | 준비되지 않음 |

따라서 현재 정확한 판정은 다음과 같다.

- **Platform·Agent·MCP 경계와 separate CLI process/JSON boundary:** 구현·runtime 검증; CLI native backend 전체 추출과 독립 release는 미완료
- **reviewed plan 기반 XGK/XGB 시퀀스·타이머·알람·복구 프로그램 계약:** 구현·runtime 검증
- **일반 신규 구축과 실제 XG5000 적용:** 미완료
- **production 배포:** 미완료
- **실제 PLC online 기능:** 미검증

## 10. 남은 제품 로드맵과 승격 게이트

로드맵의 순서는 기능을 켜는 순서이며 날짜 약속이 아니다. 앞 단계의 증거가 없는
고위험 기능을 뒤 단계에서 먼저 활성화하지 않는다.

### G0 — 공개·배포 기반 확정

- 실제 법적 저작권자 명칭 확정 및 Platform `LICENSE`/`NOTICE` 최종 확인
- GitHub 조직 `gidong-controls`의 세 repository visibility와 branch protection 확정
- GIDONG 제품 표시명과 기존 `controlstudio_*` 기술 식별자의 호환성 마이그레이션 결정
- production code signing, immutable artifact registry, signed manifest, updater와 전체 BOM rollback
- clean Windows 설치·업데이트·제거 및 사용자 데이터 보존 행렬 통과

### G1 — LS read-only 엔지니어링 release

- 지원 XG5000·XG-SIM·Windows·CPU 조합을 명시
- 안전한 실제 XG5000 working copy에서 packaged product read 경로 재검증
- 실제 XGWX timer-area·I/O inventory readback을 대표 프로젝트로 확대하고
  다른 CPU family의 module/point address profile·외부 배선·module health·현재 PLC runtime 근거를 보강
- 컨트롤러 strong identity와 restore/compare 구현·실기 검증
- 프로그램 구조·cross-reference·logic graph·근거 탐색의 대표 프로젝트 acceptance 통과

### G2 — LS 신규 구축과 일반 오프라인 편집

- 하드웨어·I/O·mode·sequence·interlock·alarm 산출물의 닫힌 workflow
- 일반 LD 생성·편집, vendor check, save/reopen, diff와 rollback
- representative project와 XG-SIM 시나리오에서 요구사항→프로젝트→실제 시운전 흐름 완료

### G3 — 실제 online observe

- 실제 장비 identity와 권한을 결속한 device value, trace, alarm read
- disconnect·stale target·project mismatch·통신 오류 fail-closed 검증
- 관측 evidence와 진단 가설을 UI·audit에서 구분

### G4 — 승인된 실제 변경

- 실제 PLC write, download, mode change, run edit와 force를 capability별로 별도 승인
- 현장 안전 절차, 백업·readback·reopen·rollback·crash recovery 실기 gate 통과
- 지원 vendor/CPU/firmware 조합별 enablement; 미검증 조합은 계속 비활성

### G5 — 추가 벤더

- Mitsubishi·Rockwell adapter를 같은 Product Contract와 안전 불변조건 아래 구현
- 벤더별 실제 읽기·오프라인 변경·시뮬레이션·online capability를 독립 증거로 승격

## 11. 성공 지표

### 절대 통과 지표

- 승인되지 않았거나 stale/mismatched target인 mutation의 실행: **0건**
- 결과 불명확 상태를 성공으로 표시하거나 자동 재실행한 mutation: **0건**
- 승인된 mutation의 Platform audit와 MCP journal 연결 누락: **0건**
- public reachable history의 private 코드·secret·사용자 데이터: **0건**
- BOM과 다른 component·contract·hash를 실행한 release: **0건**
- 지원한다고 표시한 PLC capability 중 지정 실기 gate를 통과하지 않은 항목: **0건**

### 제품 효과 지표

pilot 전에 대표 LS 프로젝트와 실제 사용자 작업 집합을 고정하고 다음을 측정한다.

- 기존 프로젝트의 프로그램·신호·원인 위치를 근거와 함께 찾는 성공률과 소요 시간
- 신규 구축 요구사항의 누락·충돌 탐지율과 검토 완료까지의 시간
- 진단 답변의 원문·program location·readback citation 연결률
- 제안된 변경의 diff 정확도, vendor check·save/reopen·simulation 통과율
- rollback 성공률과 불명확 operation의 안전한 quarantine 비율
- 사용자가 AI 답변을 수정·거절한 이유와 반복 오류율

목표 수치는 임의로 정하지 않고 대표 corpus와 pilot baseline을 만든 뒤 release별로
고정한다. 단, 위 절대 통과 지표는 pilot 결과와 무관하게 완화하지 않는다.

## 12. 릴리스 완료 기준

어떤 capability든 다음 증거가 모두 있어야 지원 완료로 표시할 수 있다.

1. 지원 vendor·제품·버전·환경과 비지원 범위가 명시돼 있다.
2. 공개 계약과 production 구현이 일치하고 각 component가 실제 runtime으로 로드된다.
3. 세 packaged artifact의 exact BOM 조합으로 사용자 경로와 readback이 끝까지 동작한다.
4. IDE·simulator·PLC 기능이면 지정 실제 환경의 replay 가능한 evidence가 있다.
5. target mismatch, stale state, crash, timeout, tamper와 denial 경로가 fail-closed다.
6. mutation이면 승인, journal, readback, save/reopen, rollback과 reconciliation이
   끝까지 증명된다.
7. 사용자에게 구현·runtime 검증·실제 검증·미검증 상태가 정확히 표시된다.
8. documentation, audit, migration과 support matrix가 해당 release와 함께 고정된다.

전체 제품 완료는 신규 구축, 기존 프로젝트 이해, online observe, 승인된 변경과
단일 production 배포가 지원 범위에서 위 조건을 모두 통과했을 때만 선언한다.
현재는 이 조건을 충족하지 않았다.

## 13. 확정 결정과 남은 결정

### 확정됨

- 제품 브랜드: 기동 / GIDONG
- GitHub 조직 후보와 repository namespace: `gidong-controls`
- repository: `gidong-platform` public, `gidong-agent` private,
  `gidong-plc-mcp` private
- 일반 사용자 제품: 단일 설치·단일 진입점, 내부 sidecar architecture
- public license: Apache License 2.0
- Agent/MCP license: private proprietary, All Rights Reserved
- installer는 private repository를 clone하지 않고 미리 빌드된 artifact만 조립
- component 독립 버전 + exact product BOM 정책

### 공개·production 전에 확정해야 함

- `GIDONG Controls`가 아닌 경우 실제 개인 또는 법인 저작권자 명칭
- GIDONG 상표·도메인·publisher 표시명
- 소비자 실행 파일과 package/artifact namespace의 최종 명칭 및 migration 방식
- 최초 지원 XG5000·XG-SIM·Windows·PLC CPU/firmware matrix
- 모델 provider, credential, telemetry와 사용자 데이터 보존 정책
- standalone PLC MCP의 별도 배포·라이선스·지원 정책
- production signing certificate, artifact registry와 release channel 운영 주체

## 14. 근거 문서

- [사용자 GUI와 실행 경험](docs/CONTROLSTUDIO_GUI_HANDOFF.md)
- [사람·에이전트 엔지니어링 작업 흐름](docs/ENGINEERING_COLLABORATION_WORKFLOWS.md)
- [제품 아키텍처](docs/PRODUCT_ARCHITECTURE.md)
- [책임과 데이터 소유권](docs/refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md)
- [단일 제품 배포와 독립 버전 정책](docs/refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md)
- [패키징과 BOM 경계](docs/refactor/PACKAGING.md)
- [GIDONG 저장소 이름과 라이선스 경계](docs/REPOSITORY_NAMING.md)
