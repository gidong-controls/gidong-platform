# ControlStudio 경량 엔지니어링 지식·메모리·제어 패턴

최종 갱신: 2026-07-30

이 문서는 현재 구현된 로컬 지식 검색, 사람 확인형 메모리, 벤더 중립 제어 패턴의
제품 계약을 설명한다. 목표는 PLC 엔지니어가 매번 매뉴얼을 처음부터 찾거나 현장
관례를 다시 설명하지 않아도 되게 하는 것이다. 범용 RAG 플랫폼, 자동 래더 생성
DSL, 문서 관리 시스템을 새로 만드는 것이 목표가 아니다.

```text
React 지식 화면
  → ControlStudio Host API
    → 로컬 SQLite 지식 저장소
      ├─ 매뉴얼·표준·프로젝트 문서
      ├─ 사람이 확인한 현장 기억·작성 선호
      └─ 내장 제어 패턴 12개
    → 읽기 전용 에이전트 지식 검색
      → 출처가 붙은 분석·질문·설계 제안
```

지식 저장소는 모델 클라이언트나 MCP/PLC 접근 권한을 소유하지 않는다. 검색 결과는
참고 근거이며, 프로젝트 변경이나 PLC 제어 권한이 아니다.

## 1. 현재 구현된 지식 계층

| 종류 | 용도 | 현재 상태 규칙 |
|---|---|---|
| `manual` | 벤더 명령, 장치, 설정, 제한사항 | 공유 매뉴얼은 제품군·버전 필수, 현재 workspace 전용 매뉴얼은 아래 예외 계약 적용 |
| `standard` | 회사·현장 엔지니어링 표준 | 출처와 적용 범위를 함께 저장 |
| `project_document` | I/O 목록, 설계서, 프로젝트별 설명 | 프로젝트 범위를 명시해 저장 |
| `memory` | 검증된 현장 동작 의미와 유지보수 관례 | 항상 `proposed`로 시작하고 사람이 확인 |
| `preference` | 네이밍, 네트워크 구성, 래더 작성 선호 | 항상 `proposed`로 시작하고 사람이 확인 |
| `control_pattern` | 반복 설비 설계의 질문·불변조건·검증 체크리스트 | 내장 검토본, 읽기 전용 |

각 출처에는 제목, 종류, 적용 범위, 벤더, 제품군, 버전, 언어, 권위, 상태,
revision, SHA-256, locator와 메타데이터가 저장된다. 텍스트는 인용 가능한 chunk로
나뉘며 원본 출처와의 연결을 유지한다.

### 권위와 적용 우선순위

검색 점수는 텍스트 적합도이지 권위 점수가 아니다. 에이전트는 다음 계약으로 결과를
판단한다.

1. 독립 안전 PLC·안전 릴레이·비상정지·가드·위험성 평가와 제조사 제한은 다른
   지식이나 선호로 무시할 수 없다.
2. 현재 프로젝트에서 다시 읽은 구조와 현재 런타임 증거는 과거 프로젝트 기억보다
   우선한다.
3. 현재 사용자가 명시한 요구와 결정은 과거의 작성 선호보다 우선한다. 단, 현재
   사실이나 안전 제한을 바꾸는 근거가 되지는 않는다.
4. 공유 벤더·전역 매뉴얼은 기록된 벤더·제품군·버전에서만 명령 의미의 근거가
   된다. 현재 workspace에 명시적으로 결합한 매뉴얼만 그 workspace 안에서
   제품군·버전 미확정 상태의 참고 근거가 될 수 있으며 다른 프로젝트로 전파하지
   않는다.
5. 확인된 현장 기억과 표준은 기록된 프로젝트·현장·팀·벤더 범위 안에서만 관례의
   근거가 된다.
6. 작성 선호는 표현과 구조 선택에만 영향을 주며 기계 사실로 취급하지 않는다.
7. 제어 패턴은 빠뜨리기 쉬운 질문과 검증 항목을 제공할 뿐, 적용 가능한 네이티브
   코드나 확정된 설비 조건이 아니다.

출처끼리 충돌하거나 적용 범위가 맞지 않으면 조용히 합치지 않고 불일치로 남겨야
한다. 검색 결과가 없으면 에이전트는 추측하지 않고 지식베이스가 답을 확립하지
못했다고 말해야 한다.

## 2. 벤더·제품군·버전 격리

현재 지원하는 검색 벤더 키는 `ls`, `mitsubishi`, `rockwell`, `generic`이다.
LS Electric, Mitsubishi Electric, Rockwell Automation/Allen-Bradley의 일반적인
표기 차이는 같은 벤더 키로 정규화된다.

- 매뉴얼 가져오기 API와 화면은 `vendor`, `productFamily`, `version`을 요구한다.
- 로컬 파일은 출처를 암호학적으로 증명할 수 없으므로 브라우저가
  `vendor_official`을 자칭할 수 없고 `user_imported_reference`로 기록한다.
- 검색 저장소는 벤더, 종류, 상태, 범위, 제품군, 버전 필터를 **랭킹 전에** 적용한다.
- 기존 프로젝트의 에이전트 검색은 현재 workspace 벤더를 기본 대상으로 삼고
  `generic` 제어 패턴을 함께 허용한다.
- 에이전트 도구는 제품군과 버전 필터를 받을 수 있으며 결과마다 실제 벤더·제품군·
  버전·범위를 다시 반환한다.
- 제품군이나 버전을 알 수 없을 때는 여러 버전의 내용을 하나의 사실처럼 합치지
  않는다. 결과에 표시된 적용 정보를 보고 추가 확인 대상으로 남긴다.
- `generic`은 벤더 중립 설계 체크리스트를 뜻한다. LS, Mitsubishi, Rockwell의
  명령이나 주소 체계가 서로 호환된다는 뜻이 아니다.

### workspace 결합과 공유 매뉴얼의 차이

프로젝트·workspace 범위는 사용자가 바꿀 수 있는 표시 이름이나 원본 파일명이
아니라 Host가 발급한 immutable `workspace_id` 하나로만 식별한다.

```text
workspace:{workspace_id}
project:{workspace_id}
```

파일명 변경, 같은 이름의 다른 설비, 복사본 생성으로 범위가 다시 결합되지 않는다.
현재 workspace 전용으로 명시해 가져온 `manual` 또는 `project_document`는 위 범위와
정확히 일치할 때만 Agent가 읽는다.

두 매뉴얼 적용 정책은 의도적으로 다르다.

- `workspace:{workspace_id}` 또는 `project:{workspace_id}`에 명시적으로 결합한
  현재 프로젝트 전용 매뉴얼은 실제 작업자가 그 프로젝트의 문서로 선택한
  근거이므로, 아직 product family와 version을 증명하지 못했더라도 **그 workspace
  안에서만** 참고할 수 있다. 결과에는 미확정 applicability를 그대로 표시하며
  명령 의미를 확정하거나 다른 workspace로 재사용하지 않는다.
- `vendor:*` 또는 `global` 공유 매뉴얼은 여러 프로젝트로 전파되므로 현재 target의
  product family와 version이 둘 다 증명되고 정확히 맞을 때만 사용한다. target이
  미확정이거나 매뉴얼이 unqualified이면 공유 매뉴얼 검색은 fail-closed한다.
- target이 확정된 경우에도 명시적으로 허용한 unqualified 현재-project 문서와
  정확히 맞는 공유 매뉴얼만 함께 검색한다. 다른 제품군·버전은 랭킹 전에
  제외한다.

현재 제품 어댑터는 LS 우선이다. Mitsubishi와 Rockwell 지식을 저장하고 검색할 수
있지만, 그것이 해당 벤더 IDE의 native binding이나 프로젝트 생성·수정 기능이
구현됐다는 뜻은 아니다.

## 3. 출처와 페이지 인용

Host의 로컬 파일 선택으로 다음 문서를 가져올 수 있다.

- PDF
- Markdown (`.md`, `.markdown`)
- 일반 텍스트 (`.txt`)

PDF는 실제 추출 순서의 페이지 번호를 보존한다. Markdown과 텍스트는 한 페이지
문서로 취급한다. 원본 source record에는 SHA-256이 저장되고, 각 검색 결과는 다음
정보를 가진다.

```text
knowledge_ref
source_id
chunk_id
title
locator
page
section
authority
vendor / product_family / version / scope
```

인용 식별자는 Host가 반환한 source/chunk identity로만 구성하며 다음과 같은
안정된 형태다. `page` 또는 `section`이 없는 경우 해당 query 항목은 생략하고,
section은 URL encoding한다.

```text
knowledge://{source_id}/{chunk_id}?page={page}&section={section}
```

에이전트가 답변에 실제로 사용한 결과는 검색 도구가 돌려준 정확한
`knowledge_ref`를 그대로 인용해야 한다. 모델이 source ID, chunk ID, 페이지나
section을 새로 만들거나 비슷한 문자열로 고쳐 쓰면 유효한 인용이 아니다. 최종
응답의 인용은 해당 run에서 실제 반환된 ref 집합과 대조하며, 검색 결과마다 Host가
별도의 `evidence.added`를 남긴다. 검색 결과가 없거나 적용 범위가 맞지 않으면
인용을 만들어 채우지 않고 `unknown` 또는 추가 문서 확인으로 남긴다.

검색 쿼리, 적용 필터, 반환 chunk, 소요 시간, actor와 conversation도 검색 감사
기록에 저장된다. 이 기록과 인용은 모델의 설명과 독립된 hallucination guard다.

사용자가 출처를 삭제하면 물리적으로 지우지 않고 `archived`로 전환한다. archived
출처는 기본 검색과 Agent 검색에서 제외되지만 기존 source/chunk, SHA-256,
`knowledge://` 인용과 검색 감사 기록은 보존된다. 따라서 과거 분석에서 무엇을
근거로 사용했는지 재현할 수 있고, 명시적인 archived 조회에서만 다시 확인할 수
있다.

스캔 이미지만 있는 PDF는 텍스트가 없다는 이유로 거부한다. 잘못된 빈 인덱스를
만드는 대신 OCR이 필요하다고 알리는 fail-closed 동작이다. 잠긴 PDF도 먼저 잠금을
해제해야 한다.

## 4. 현장 기억과 작성 선호

ControlStudio는 대화 내용을 자동으로 장기 기억으로 승격하지 않는다.

```text
사람 또는 에이전트가 기록할 내용 제안
  → proposed
  → 적용 범위와 문장 검토
  → 사람이 revision을 확인하고 승인
  → confirmed
  → 기본 검색 대상
```

화면에서 선택 가능한 범위는 프로젝트, 현장·설비, 팀, 벤더다. 기억과 선호는
`proposed` 상태에서도 목록과 명시적 상태 검색으로 검토할 수 있지만, 기본 검색과
에이전트 근거에는 `confirmed`만 포함된다. 확인할 때 actor와 revision을 기록하며,
이미 상태가 바뀐 revision을 다시 확인하려 하면 충돌로 거부한다.

현재 Agent 자동 적용 범위는 `global`, 선택된 벤더와, 기존 프로젝트 작업에서
Host가 immutable `workspace_id`로 결합한 `workspace:{workspace_id}` 및
`project:{workspace_id}` 범위다. 원본 파일명이나 화면 표시 이름은 범위 권위로
사용하지 않는다. 현장·팀 기억은 기록하고 사람이 직접 검색할 수 있지만
workspace와 현장·팀의 명시적 연결이 아직 없으므로 Agent가 자동으로 읽지 않는다.
이 제한은 다른 현장 관례가 섞이는 것보다 안전한 fail-closed 기본값이다.

두 종류는 의미가 다르다.

- `memory`: “이 설비의 운전 피드백은 별도 보조접점으로 확인한다”처럼 확인된 현장
  의미와 예외
- `preference`: “모터 로직은 출력 전용 프로그램에 모은다”처럼 구조와 표현에 관한
  선호

선호는 I/O 주소, 신호 극성, 타이머 값, 실제 안전 상태를 확정하지 못한다. 기존
프로젝트에서 다른 증거가 발견되면 현재 증거를 따르고 충돌을 사용자에게 보여준다.

## 5. 내장 제어 패턴 12개

제어 패턴은 래더 조각이나 벤더 명령 템플릿이 아니다. 반복 설비를 설계하거나
진단할 때 필요한 신호 역할, 결정, 불변조건, 알람·복구, 질문과 검증 시나리오를
빠르게 꺼내는 vendor-neutral 체크리스트다.

| Pattern ID | 엔지니어링 역할 |
|---|---|
| `motor.dol` | 직입 기동 모터의 요구·퍼미시브·보호·출력·피드백·재기동 |
| `motor.reversing` | 정·역 명령 상호 배타, 정지 증명, 물리 인터록, 전환 복구 |
| `cylinder.double_solenoid` | 복동 실린더의 전진·후진 출력, 종단 센서, 동작 감시 |
| `drive.vfd` | VFD run·속도 지령·ready/fault·통신 상실·정지와 reset |
| `axis.servo` | 서보 축 소유권·ready·home·motion·limit·완료·복구 |
| `alarm.lifecycle` | 발생·활성·acknowledge·return-to-normal·reset·이력 |
| `operation.mode_manager` | 자동·수동·보전·로컬 모드 소유권과 전환 |
| `initialization_recovery` | 기동·중단·전원 복귀 후 물리 상태 재확립 |
| `sequence.step_machine` | STEP, 진입/유지/전이, stop·hold·abort·재개 |
| `interlock.resource_owner` | 공용 구역·자원에 대한 단일 점유와 stale owner 복구 |
| `command.request_ack_done` | request·ack·done·failed handshake와 중복 실행 방지 |
| `analog.scaled_measurement` | raw scaling·단위·quality·fault·filter·alarm·대체값 |

모든 패턴에는 다음 공통 blocker가 들어 있다.

- 실제 프로젝트 symbol과 물리 I/O 매핑
- 신호의 전기·논리 극성, 정상·단선·고장 상태
- 실제 측정과 제조사 근거에 따른 delay, filter, debounce, timeout
- stop, fault, 통신 상실, restart, power recovery의 위험성 평가된 safe state
- 안전 PLC·안전 릴레이·drive·기계 장치가 소유하는 독립 안전 기능

이 항목이 확인되지 않으면 패턴은 적용 준비가 된 것이 아니다. 패턴에는 장치 주소나
일반 타이머 기본값이 없으며, 각 패턴은 최소 세 가지 정상·경계·고장/복구 검증
시나리오를 제공한다.

패턴은 LS Electric, Mitsubishi Electric, Rockwell Automation의 native ladder나
명령 binding을 생성하거나 보증하지 않는다. 또한 안전 PLC, 안전 릴레이, 기계 가드,
전기 설계, 제조사 지침, 현장 commissioning과 자격 있는 사람의 승인을 대체하지
않는다.

## 6. 새 프로젝트에서의 사용 흐름

1. 사용할 벤더·제품군·버전의 매뉴얼, 회사 표준, I/O 목록과 설비 문서를 로컬
   지식에 가져온다.
2. 새 프로젝트 planning 대화가 설비 종류에 맞는 매뉴얼과 제어 패턴을 읽기 전용으로
   검색한다.
3. 패턴의 필수 신호 역할과 질문을 설계 artifact의 후보 항목으로 사용한다.
4. 제공되지 않은 I/O, polarity, timing, safe state와 기계 제약은 값을 만들지 않고
   명시적 unknown/blocker로 남긴다.
5. 확인된 회사·현장 선호가 있으면 적용 범위 안에서 네이밍과 구조 선택에 반영한다.
6. 사용자가 요구사항과 계획을 검토한 뒤에만 별도 기술 설정·build 단계로 넘어간다.

새 프로젝트 planning 에이전트에는 현재 읽기 전용 지식 검색만 제공된다. 제어
패턴을 찾았다는 이유로 MCP 도구를 호출하거나 프로젝트를 생성·변경했다고 주장할 수
없다.

## 7. 기존 프로젝트에서의 사용 흐름

1. 현재 workspace와 engineering session을 먼저 식별한다.
2. 대상 CPU·제품군·버전의 매뉴얼과 현재 프로젝트 문서를 선택하거나 가져온다.
3. “이 모터가 왜 늦게 켜지는가” 같은 요청에서 에이전트는 현재 프로젝트 read/analyze
   증거와 지식 검색을 분리해 수집한다.
4. 매뉴얼은 명령 의미, 확인된 memory는 현장 관례, pattern은 누락 점검에만 사용한다.
5. 실제 프로젝트 구조·참조·현재 관찰과 충돌하면 현재 증거를 우선하고 충돌을
   표시한다.
6. 사용한 지식은 페이지·section이 포함된 `knowledge_ref`로 근거 목록에 남긴다.
7. 수정이 필요하면 기존 change plan, 사람 승인, read-before-write, native check와
   검증 경로를 그대로 거친다.

지식 검색은 변경 승인, 온라인 연결 증명, 현재 PLC 값, XG5000 검사 또는 rollback을
대체하지 않는다.

## 8. 경량 검색과 벤치마크

현재 검색은 로컬 SQLite FTS5만 사용한다.

1. `unicode61` word BM25 채널
2. 한글·일본어·영문 식별자의 부분 일치를 위한 결정론적 2/3-gram 채널
3. 두 채널 순위를 Reciprocal Rank Fusion으로 결합한 `word_gram_rrf`

원문 쿼리는 FTS 연산자로 실행하지 않고 안전한 token으로 다시 구성한다. 벤더·종류·
상태·범위·제품군·버전 조건은 검색 랭킹 전에 적용한다.

재현 가능한 benchmark는 다음 명령으로 실행한다.

```powershell
cd <private-controlstudio-agent-repository>
uv run python scripts/benchmark_knowledge_retrieval.py
```

현재 저장소의 authored fixture는 21개 PLC 유사 문서와 24개 다국어·명령어·식별자
쿼리로 구성된다. 이 중 answerable query는 21개, no-answer query는 3개다. 실제
벤더 매뉴얼 원문을 복제한 corpus는 아니다. ranking 지표는 answerable query만
계산하고 no-answer는 별도 오근거율로 계산한 현재 실행 결과다.

| 검색 방식 | Recall@5 | MRR@5 | nDCG@5 | no-answer 오근거율 |
|---|---:|---:|---:|---:|
| word BM25 | 0.9048 | 0.9048 | 0.9048 | 0.0000 |
| CJK/identifier n-gram | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| hybrid RRF | 1.0000 | 1.0000 | 1.0000 | 0.0000 |

3개 no-answer query의 false-evidence는 모두 0이었고 벤더 누출 query도 0건이었다.
CJK/identifier n-gram과 hybrid RRF의 지표는 동률이다. hybrid 선택은 benchmark가
단독 우승했기 때문이 아니라, exact word와 CJK/identifier 채널을 함께 보존하는
**engineering tie policy**다. 이 fixture는 결정론적 회귀 기준일 뿐 실제 대형
매뉴얼 corpus의 relevance나 제품 검색 성능을 보증하지 않는다.

benchmark가 출력하는 latency는 개발 PC의 작은 fixture에서 관찰한 진단값이다.
hardware, corpus 크기, cold/warm cache와 동시 부하 조건이 고정되지 않았으므로
문서에 제품 지연 보장이나 acceptance threshold로 사용하지 않는다.

### Dense embedding 도입 gate

Dense/vector retrieval은 현재 기본 의존성이 아니며 실제 벤더 corpus 평가 전까지
deferred다. 다음이 모두 확인될 때만 후보로 추가한다.

- 실제 LS·Mitsubishi·Rockwell 다국어 매뉴얼 corpus와 별도 relevance 판정 쿼리
- lexical hybrid보다 실질적으로 개선된 Recall@5/MRR@5
- 벤더·제품군·버전 누출 증가 없음
- no-answer에서 그럴듯한 오근거 증가 없음
- page/section/source 인용 보존
- 현장 Windows 환경에서 허용 가능한 index 크기, 초기화 시간, 검색 지연
- embedding 모델과 버전의 고정·재색인·오프라인 배포·라이선스 방안

이 gate를 통과하기 전에는 외부 vector DB, LangChain/LlamaIndex, 별도 embedding
서비스를 추가하지 않는다. 의미 검색이 그럴듯해 보인다는 이유만으로 결정론성과
출처 격리를 희생하지 않는다.

## 9. 현재 미지원 범위

- 스캔 전용 PDF OCR
- 암호가 잠긴 PDF의 자동 해제
- 웹 크롤링, 매뉴얼 자동 다운로드와 자동 최신 버전 교체
- dense embedding, vector database와 클라우드 지식 동기화
- 대화에서 memory/preference를 자동 생성하거나 자동 확인하는 기능
- 서로 충돌하는 출처의 자동 판정·병합
- 제어 패턴에서 LS/Mitsubishi/Rockwell native ladder·ST·AOI를 자동 생성하는 기능
- 패턴만으로 장치 주소, I/O polarity, time value, safe state를 채우는 기능
- 지식 문서를 현재 PLC/runtime 사실로 승격하는 기능
- 실제 PLC 온라인 변경·출력 강제·다운로드 권한 부여
- 안전 기능의 설계 인증 또는 현장 검증 대체

현재 구현은 필요한 출처를 로컬에 저장하고, 범위를 붙여 검색하며, 사람이 확인한
기억과 검토된 패턴을 에이전트가 인용해 사용하는 최소 제품 경로다. 기능을 늘릴 때도
먼저 실제 PLC 엔지니어 작업에서 검색 실패나 반복 비용이 확인되어야 하며, 범용
지식 플랫폼을 미리 만드는 과설계를 피한다.
