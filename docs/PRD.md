# ControlStudio PLC 엔지니어링 에이전트 제품 요구사항

최종 갱신: 2026-07-30

> **구현 상태 주석**
>
> 이 PRD의 제품 목표와 릴리스 합격 기준은 축소하지 않는다. 현재 저장소는
> 전체 PRD 완성품이 아니며, 구현·검증 수준은
> [MCP_CAPABILITY_MATRIX.md](MCP_CAPABILITY_MATRIX.md)와
> [VALIDATION.md](VALIDATION.md)를 기준으로 판정한다.
>
> 2026-07-30 현재 사용자 제품 경로에는 Windows 네이티브 XG5000 파일 선택,
> 원본 보존 working copy, Host 재시작 뒤 저장된 workspace 재개가 연결돼 있다.
> 재개 시 원본 또는 working-copy hash가 달라지면 XG5000을 열기 전에
> fail-closed하며, 성공해도 최근 대화를 다른 설비 증거처럼 재사용하지 않고
> 새 target 전용 conversation을 만든다. 현재 Actual V4.82.1 재개 실기 성공
> 범위는 fresh Host에서 fresh XG5000을 controlled launch한 경우다. 이미 실행
> 중인 PID의 반복 재획득은 strong ROT PID/path/hash readback을 확보하지 못해
> fail-closed하며, 성공했다고 주장하지 않는다. 일반 앱 재접속은 브라우저 저장값이
> 없더라도 Host의 최근 conversation을 복원한다. 모델이 호출한 MCP 도구 진행은
> run별 `tool.started/completed/failed`로 전달하되 인자·결과·원시 오류는
> 브라우저 이벤트에서 제거한다.
>
> 실제 XG5000 프로그램 목록은 Host와 Web의 읽기 전용 탐색 패널까지 연결됐다.
> `XG5000에서 열기`의 기반인 `program.focus`는 대상·hash·전면 창 검증 계약,
> 단위/MCP/Host/Web 통합 시험과 실제 대동 프로젝트의 프로그램 전면화·프로젝트
> hash 불변 read-only E2E까지 완료했다.
>
> 기존 추가 범위는 (1) 동일 길이 LD operand 한 개의
> exact approval/precondition·XG5000 check/save·durable crash reconciliation,
> (2) exact XG5000 PID/HWND와 native dialog label/control을 사용하는 LS
> 신규 프로젝트 생성 경로, (3) LS XGT FEnet TCP 2004의 상태·device 읽기
> 전용 경로다. 신규 생성은 fake backend만 통과했고 실제 XG5000에서는
> modal/elevation 불일치로 변경 전에 거부됐다. FEnet은 fake TCP만 통과했으며
> 실제 PLC에는 연결하지 않았다. Host의 feature readiness도 두 기능을
> `preview/fake_only/currentReady=false`로 반환하므로 일반 사용자 UI에서는
> 온라인 관찰을 활성화하지 않는다. 일반 제품 Host의 직접 API도 두 기능을
> MCP 호출 전에 fail-closed한다. fake backend 시험은 환경변수로 켤 수 없는
> 명시적 validation harness 설정에서만 허용한다.
>
> PLC에서 프로젝트·파라미터 복원, 강한 online identity와 전체 관찰,
> online write/run edit/force/mode/download, 일반 LD 구성과
> Mitsubishi/Rockwell adapter는 아직 구현되지 않았다.
>
> 경량 엔지니어링 지식 계층은 별도 SQLite/FTS5 저장소, PDF·Markdown·텍스트
> 가져오기, 제조사·제품군·버전 필터, 페이지 인용, 사람 확인 전 메모리 검색
> 차단, 12개 벤더 중립 제어 패턴과 Agent read-only 검색까지 구현됐다. 이는
> 멀티벤더 adapter나 실제 설비 안전 조건의 확인을 대신하지 않는다.

이 문서는 ControlStudio의 제품 범위와 완료 기준을 정의하는 최상위 기준이다.
`MCP_IMPLEMENTATION_HANDOFF.md`와 벤더별 기술 문서는 구현 이력과 증거이며,
이 문서의 제품 범위를 축소하거나 대체하지 않는다.

확정된 기술 계층은 [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md),
GUI·실행 계약은 [CONTROLSTUDIO_GUI_HANDOFF.md](CONTROLSTUDIO_GUI_HANDOFF.md),
에이전트 런타임 계약은
[CONTROLSTUDIO_AGENT_RUNTIME_HANDOFF.md](CONTROLSTUDIO_AGENT_RUNTIME_HANDOFF.md)를
따른다. 신규 구축과 기존 설비 부분 수정의 사용자 협업 흐름은
[ENGINEERING_COLLABORATION_WORKFLOWS.md](ENGINEERING_COLLABORATION_WORKFLOWS.md)를
따른다. 지식·기억·제어 패턴의 출처와 적용 규칙은
[KNOWLEDGE_MEMORY.md](KNOWLEDGE_MEMORY.md)를 따른다. 이 결정 문서들도 본
PRD의 기능·안전 요구를 축소하지 않는다.

## 1. 제품 정의

ControlStudio는 특정 PLC 편집기의 단순 자동화 도구가 아니다.

> 사용자가 자연어로 신규 설비의 제어 요구사항이나 기존 설비의 문제를 설명하면,
> 프로젝트·컨트롤러·런타임 증거를 함께 분석하고, 설계안 또는 원인 가설과 여러
> 해결안을 제시하며, 사람의 승인 아래 검증 가능한 최소 변경만 실행하는
> 멀티벤더 PLC 프로그래밍·유지보수 에이전트다.

초기 구현 대상은 LS ELECTRIC XG5000/XG-SIM이다. 향후 Mitsubishi
GX Works 계열과 Rockwell Studio 5000 계열을 추가한다. 공통 제품 모델을
XG5000의 프로젝트 구조, 디바이스 주소 또는 UI 동작에 종속시키지 않는다.

제품은 동등한 두 핵심 결과를 제공해야 한다.

1. **신규 구축**: 요구사항에서 하드웨어·I/O·시퀀스·인터록·알람·프로그램을
   설계하고, 벤더 프로젝트를 생성해 검사·시뮬레이션한다.
2. **기존 설비 엔지니어링**: 로컬 프로젝트 또는 컨트롤러에서 읽은 프로젝트를
   이해하고, 진단·영향 분석·수정·검증·복구를 수행한다.

## 2. 제품 진입 경로와 온라인의 의미

첫 화면은 다음 세 작업을 제공한다.

| 진입 경로 | 결과 |
|---|---|
| 새 프로젝트 만들기 | 요구사항에서 새 엔지니어링 작업공간과 벤더 프로젝트를 생성 |
| 로컬 프로젝트 열기 | 기존 벤더 파일을 손실 없이 읽어 분석 작업공간 생성 |
| PLC에서 읽어 프로젝트 복원 | 컨트롤러의 프로그램·파라미터를 새 작업공간으로 읽고 누락 가능한 오프라인 메타데이터를 표시 |

로컬 프로젝트를 연 뒤 대상 PLC에 연결하는 것도 온라인 연결이다. 온라인은
별도 제품 종류가 아니라 작업공간의 연결 상태다.

```text
새 프로젝트
  → 검사·시뮬레이션
  → 승인된 PLC 쓰기
  → 온라인 관찰

로컬 프로젝트
  → PLC 연결
  → 로컬/컨트롤러 identity 및 내용 비교
  → 일치하면 온라인 관찰
  → 불일치하면 PLC에서 새 파일로 읽기 / 다른 파일 선택 / 승인된 PLC 쓰기

로컬 파일 없음
  → PLC 읽기 전용 연결
  → PLC에서 새 작업공간으로 읽기
  → 복원 범위와 누락 가능 정보 표시
  → 온라인 관찰
```

UI와 MCP 계약은 `upload`와 `download`만 단독으로 쓰지 않고 방향과 결과를
명시한다.

| 공통 의미 | 방향 | 벤더 표현 예 |
|---|---|---|
| PLC에서 읽기 | Controller → Workspace | LS/Mitsubishi `Read from PLC`, Rockwell `Upload` |
| PLC에 쓰기 | Workspace → Controller | LS/Mitsubishi `Write to PLC`, Rockwell `Download` |
| 온라인 관찰 | Controller → Workspace | Monitor, Go Online |
| 런중 수정 | Workspace → 실행 중 Controller | Run edit, Online Change, Online Edits |

연결만으로 로컬 프로젝트와 컨트롤러가 일치한다고 간주하지 않는다. 잘못된
대상에 대한 쓰기를 막기 위해 controller identity, CPU/firm웨어, 통신 경로,
프로젝트 상관관계와 내용 비교를 독립 증거로 확인한다.

## 3. 사용자 제품

기본 화면은 PID/HWND와 원시 JSON을 조작하는 개발 콘솔이 아니라 다음을 한
작업공간에 결합한 사용자 제품이어야 한다.

최초 진입의 핵심 선택은 `새 프로젝트`와 `기존 로컬 프로젝트` 두 가지다.
PLC에서 프로젝트 복원과 온라인 읽기 전용 관찰은 실제 capability가 준비된
경우에만 접힌 보조 연결 경로로 제공한다. target 또는 신규 계획 대화가 아직
없을 때 빈 에이전트 대화창을 동시에 표시하지 않으며, 사용자가 한 경로를
완료한 뒤 계획 또는 엔지니어링 작업공간으로 전환한다.

- 에이전트 대화와 요청 이력
- 현재 workspace·프로젝트·PLC·프로그램·태스크·I/O의 대상 상태와 탐색
- 분석 근거가 된 벤더 프로그램·네트워크·명령의 구조화된 위치
- 디바이스 또는 태그 참조, 인과 그래프와 상태 전이
- 온라인 값·STEP·타이머·스캔·통신의 시간축
- 확인된 사실, 가설, 불확실성과 추가 측정 항목
- 여러 설계·수정안의 효과, 위험, 변경 범위와 추천 근거
- 구조 diff, 영향받는 출력, 검사·시뮬레이션 계획
- 승인, 실행, readback, 복구와 감사 증거

프로젝트 경로, 해시, 벤더, CPU, 연결 상태와 권한은 사람이 이해할 수 있는
상태 표시로 항상 확인 가능해야 한다. PID/HWND, 네이티브 payload와 MCP JSON은
개발자 상세 정보에서만 표시한다.

초기 LS 제품에서 래더와 벤더 프로젝트의 시각적 원본은 XG5000이다.
ControlStudio는 XG5000의 범용 래더 IDE를 복제하지 않고 관련 위치와 의미를
설명하며 `XG5000에서 열기`를 제공한다. 신규 로직 생성과 변경도 공통 계약과
adapter를 거쳐 벤더 네이티브 프로젝트에 반영한다. 향후 벤더가 늘어나도 자체
범용 래더 편집기를 제품 완료 전제로 두지 않는다.

사용자 화면은 React·TypeScript 웹 애플리케이션이며 Windows에서는 브라우저
또는 설치형 PWA 창으로 사용할 수 있다. 사용자가 ControlStudio 아이콘을
누르면 Windows Launcher가 Python Host와 MCP를 시작한 뒤 같은 웹 UI를 연다.
로그인·부팅 시 자동 실행은 기본값이 아니며, 브라우저/PWA가 직접 MCP나
XG5000을 제어하지 않는다. 검증된 Host와 Launcher 소유 앱 창이 하나 있으면
Launcher는 새 창을 만들지 않고 기존 창을 복원·전면화한다. 새 Host 또는
다중 소유 창 복구 때만 인증된 앱 창 하나를 새로 만들며, 정확히 한 창이
준비될 때까지 단일 실행 mutex를 유지한다.

## 4. 기능 요구사항

### 4.1 작업공간과 프로젝트 출처

모든 작업공간은 다음 identity를 가진다.

- workspace ID와 출처: `new`, `local_file`, `controller_read`
- immutable project revision ID, 벤더, 엔지니어링 시스템과 버전
- 네이티브 프로젝트 artifact와 source hash
- CPU·firm웨어·프로젝트 또는 컨트롤러 식별값
- 읽은 시각, 최신성, 해석 완성도와 누락 가능 정보
- 연결된 컨트롤러가 있으면 통신 경로와 상관관계 상태

IDE 자동화 상태인 `engineering_session`, 실제 PLC 통신 상태인
`controller_session`, 특정 시점의 `online_snapshot`, 변경 수명주기인
`change_transaction`은 별도 identity와 상태 기계로 관리한다. 하나의 선형
세션 단계에 프로젝트 편집, simulator와 실제 controller 상태를 섞지 않는다.

대화, agent run, 승인, 이벤트 순서와 미완료 작업은 ControlStudio Host가
영속적으로 소유한다. 브라우저가 보낸 과거 assistant 메시지, tool result,
승인 플래그 또는 local storage를 권위 있는 기록으로 신뢰하지 않는다. 여러
브라우저가 같은 세션을 보더라도 동일 대상의 실제 변경 lease는 하나만 존재한다.
첫 로컬 제품은 서로 다른 대화 사이에서도 non-terminal agent run을 전역으로
하나만 허용해 하나의 XG5000/MCP 제어 권위와 UI의 `activeOperation`이
갈라지지 않게 한다.

PLC에서 읽은 프로젝트는 기존 로컬 파일을 자동으로 덮어쓰지 않는다. 새
작업공간과 새 artifact로 저장하고, 컨트롤러에 저장되지 않는 주석·문서·편집
정보가 있을 수 있음을 표시한다.

`local_file` 출처도 원본을 직접 편집하지 않는다. source hash가 고정된
immutable revision으로 보존하고, 오프라인 변경은 별도의 hash-pinned working
copy에서만 수행한다. 저장·재열기·native round-trip이 성공해야 새 revision으로
승격한다.

### 4.2 신규 프로젝트 구축

사용자는 자연어 요구사항, I/O 목록, 설비 문서 또는 템플릿에서 시작할 수 있다.
에이전트는 불명확한 안전·운전 요구를 질문하고 다음 engineering artifact를
단계적으로 만든다.

- 벤더·CPU·firm웨어·모듈·네트워크 구성
- 센서·액추에이터·I/O와 심볼 또는 태그
- 자동·수동·보전·초기화·재기동 모드
- 공정 STEP과 상태 전이
- 인터록·충돌·자원 점유 매트릭스
- 알람·타임아웃·복구 매트릭스
- 태스크·프로그램·루틴 구조
- LD와 벤더가 지원하는 다른 언어의 로직
- 정상·경계·고장·복구 시뮬레이션 시나리오

코드를 한 번에 생성해 확정하지 않는다. 요구사항 → 설계 artifact → 로직
계획 → 생성 → 검사 → 시뮬레이션 순서마다 검토 가능해야 한다.

신규 프로젝트가 ControlStudio 내부 화면에만 존재해서는 안 된다. 선택한
adapter가 지원하는 범위에서 실제 벤더 프로젝트를 생성하고 벤더 IDE의
프로그램 위치에서 확인 가능해야 하며, 네이티브 저장·재열기·검사·시뮬레이션
증거가 성공해야 확정한다.

### 4.3 기존 프로젝트 이해와 유지보수

- 네이티브 프로젝트와 실행 중인 엔지니어링 시스템 식별
- 모든 PLC·프로그램·태스크·지원 언어 열거
- 런, 접점, 분기, 코일, 명령, 함수블록과 원본 위치 복원
- 직접·간접 주소, 태그 alias, 호출, 점프와 다중 쓰기 분석
- 타이머·카운터·SET/RST·STEP·인터록·알람 모델
- 자동·수동·보전·재기동·전원 복구 모드 차이
- PLC 설정, I/O, 네트워크, HMI, 인버터, 서보와 원격 I/O 관계
- 프로젝트 전체 인과 경로와 영향받는 출력
- 해석하지 못한 네이티브 요소의 무손실 보존과 `partial` 표시

정적 코드만으로 런타임 원인을 확정하지 않는다. 사실, 추론, 가설과 필요한
추가 측정을 구분한다.

### 4.4 온라인 읽기와 관찰

온라인 읽기는 제품의 핵심 기능이며 온라인 쓰기와 분리한다.

- 컨트롤러 탐색과 읽기 전용 연결
- 벤더·모델·serial·firm웨어·RUN/STOP·보안 상태 확인
- PLC에서 프로그램·파라미터·지원 메타데이터 읽기
- 로컬 프로젝트와 컨트롤러 비교
- 현재 디바이스·태그 값과 품질·timestamp
- 활성 STEP, 타이머 진행, 알람과 fault
- 스캔 시간, 태스크 주기, 통신·원격 I/O 갱신
- 입력 도착과 출력 발생의 시간축 trace
- 연결 해제와 읽기 작업의 감사 증거

분석 전용 프로필은 실제 컨트롤러 또는 원본 로컬 프로젝트를 변경할 수 없다.
관찰 증거를 저장하거나 PLC에서 새 작업공간을 복원하려면 `Online Observe`와
별도의 `Workspace Write` 권한을 함께 요구한다.

### 4.5 진단과 대안 추천

증상에서 관련 정적·런타임 경로를 추적하고 원인 후보를 순위화한다. 각 후보는
근거, 반증 조건과 확인 방법을 가진다. 변경이 필요한 경우 최소한 다음을 비교한다.

1. 가장 작은 국소 변경
2. 기존 동작을 최대한 보존하는 변경
3. 구조적 개선
4. 센서·기계·운영 점검 등 비코드 대안

추천에는 기대 효과, 안전 위험, 사이드 이펙트, 변경 범위, 중단 시간, 검증
방법, 롤백 가능성, 불확실한 전제를 포함한다.

### 4.6 변경, 검사와 시뮬레이션

모든 변경은 다음 닫힌 트랜잭션을 사용한다.

```text
대상·최신 상태 재확인
→ snapshot과 durable backup
→ plan digest와 예상 diff
→ 영향·안전 전제
→ 사용자 승인
→ 최소 변경
→ 네이티브 readback과 구조 diff
→ 벤더 프로그램 검사
→ 저장
→ 별도 프로세스 재열기와 재추출
→ 시뮬레이션 시나리오
→ 확정 또는 검증된 롤백
```

MCP 또는 호스트가 중단돼도 재시작 후 미완료 트랜잭션을 발견하고 복구를
재개할 durable journal이 필요하다. 같은 컨트롤러 또는 엔지니어링 시스템을
여러 프로세스가 동시에 수정하지 못하게 전역 잠금을 사용한다.

### 4.7 PLC 쓰기, 런중 수정과 직접 제어

다음 capability는 각각 별도 위험 경계다.

- 전체 또는 부분 PLC 쓰기
- RUN/STOP과 운전 모드 변경
- 런중 프로그램 수정
- 디바이스·태그 값 쓰기
- 출력 force와 직접 제어
- 초기화·firmware·보안 설정 변경
- safety 프로그램·설정 변경

실제 대상 identity, 로컬/온라인 비교, 현재 운전 상태, 활성 시퀀스, 관련
출력, 안전 상태, 승인, readback, 관찰 신호와 중단·복구 조건을 모두 확인한
경우에만 해당 벤더 어댑터가 실행을 허용한다.

### 4.8 유지보수와 감사

- 사용자 요청과 설비 증상
- 사용한 프로젝트·컨트롤러 identity
- 수집한 정적·런타임 증거
- 원인 가설과 대안
- 위험과 승인 주체
- 실행한 도구와 상태 전이
- 변경 전후 artifact
- 검사·시뮬레이션·온라인 관찰 결과
- 복구 상태와 장기 성능 변화

를 영속적이고 재현 가능한 기록으로 보존한다.

### 4.9 엔지니어링 지식, 현장 기억과 설계 패턴

에이전트는 현재 프로젝트 증거만으로 알 수 없는 제조사 명령 의미, 제품군별
제약과 검토된 현장 관례를 구조화된 지식 저장소에서 조회할 수 있어야 한다.

- 제조사 매뉴얼·표준·프로젝트 문서·현장 기억·팀 선호·제어 패턴을 종류별로
  분리한다.
- 모든 검색 결과는 원본 제목, 제조사, 제품군, 버전, 페이지 또는 섹션과
  안정적인 `knowledge_ref`를 제공한다.
- 제조사·제품군·버전이 다른 근거를 자동으로 섞지 않는다. 대상이 정해지지 않은
  신규 계획에는 벤더 중립 패턴만 기본 노출한다.
- 대화에서 내용을 자동 학습하지 않는다. 기억과 선호는 `proposed`로 저장하고
  사람이 정확한 범위와 내용을 확인한 뒤에만 Agent 검색 근거가 된다.
- 제어 패턴은 모터·실린더·시퀀스·알람 등에 필요한 질문, 불변조건과 검증
  시나리오를 빠뜨리지 않게 하는 체크리스트다. 실제 I/O 주소, 극성, 시간값,
  위험 상태나 벤더 네이티브 로직을 추측해 채우지 않는다.
- 지식 문서는 비신뢰 데이터다. 문서 안의 지시가 Agent 권한·승인·MCP 안전
  gate를 바꿀 수 없으며, 검색 결과만으로 현재 프로젝트나 실제 PLC 상태를
  확인했다고 주장하지 않는다.

## 5. 시스템 경계와 멀티벤더 구조

```text
ControlStudio Windows Launcher
  ├─ ControlStudio Host (Python/FastAPI) 시작·검증
  └─ React·TypeScript Web/PWA 열기
       ↔ ControlStudio Host
         ├─ 서버 권위 conversation/run/approval/event store
         ├─ Pydantic AI Engineering Agent
         ├─ OpenAI-compatible model adapter
         └─ MCP client·process supervisor
           → 벤더 중립 ControlStudio 도메인·안전 계약
             → LS XG5000 adapter
             → Mitsubishi GX Works adapter
             → Rockwell Studio 5000 adapter
```

Launcher는 사용자가 아이콘을 눌렀을 때만 기존 Host를 검증하거나 새 Host를
시작하고, readiness 확인 뒤 canonical local URL을 연다. Host는 XG5000과
같은 로그인 사용자·interactive desktop·권한 수준에서 실행한다. XG5000 UI
자동화가 필요한 구성요소를 Session 0 Windows Service로 실행하지 않는다.

벤더 IDE UI의 활성 창·프로그램·선택·클립보드를 만지는 작업은 읽기와 쓰기를
구분하지 않고 `engineering_session`별 단일 큐에서 직렬 실행한다. UI 상태에
의존하지 않는 immutable native artifact 분석만 별도로 병렬화할 수 있다.

GUI는 표시와 사용자 입력을 담당하는 비신뢰 클라이언트다. Host가 API, 대화,
실행 상태, 승인과 프로세스 수명주기를 소유하고, MCP 안전 코어가 대상 identity,
capability, read-before-write, diff, readback, rollback과 quarantine의 최종
실행 권위를 소유한다. Pydantic AI의 tool approval은 사용자 UX이지만 인증·인가
또는 PLC 안전 경계로 사용하지 않는다.

모델 연결은 사용자 설정의 OpenAI-compatible Chat Completions `base_url`,
`api_key`, `model`, 선택적 header와 timeout을 사용한다. 특정 공급자나 모델을
하드코딩하지 않으며, 일반 응답뿐 아니라 streaming, tool call, tool result 뒤
후속 응답과 구조화 출력 capability를 연결 시 검증한다. 외부 모델로 전송되는
프로젝트·설비·tool 데이터의 범위와 endpoint를 고지하고 workspace별 반출
정책을 적용한다. 프로젝트 내용과 모델 출력은 권한·승인·실행 증거가 될 수
없는 비신뢰 입력으로 취급한다.

기본 웹 bind는 고정된 loopback 주소와 canonical origin이며 원격 접근은
명시적으로 켜는 별도 기능이다. 원격 접근, 대화·분석, 변경안 승인과 실제 실행
권한을 각각 분리한다. 브라우저 연결이 끊겨도 작업 상태는 Host에 남고, 변경
결과가 불명확한 중단은 자동 재시도하지 않고 `QUARANTINED`로 전환한다.

공통 코어는 다음 port를 정의하고 벤더 어댑터가 capability manifest로 지원
범위와 증거 수준을 보고한다.

1. `EngineeringSystemAdapter`: 벤더 IDE 발견, 프로젝트 열기·생성·검사·저장
2. `ProjectCodecAdapter`: 네이티브 프로젝트와 무손실 IR 읽기·쓰기
3. `ControllerObserverAdapter`: 읽기 전용 탐색·연결·identity·upload·관찰
4. `ControllerModifierAdapter`: 별도 credential을 요구하는 PLC 쓰기·런중 수정
5. `DirectControlAdapter`: 별도 credential을 요구하는 모드·값·force 제어
6. `SimulatorAdapter`: 쓰기, 실행, 자극, trace와 원상복구

읽기 전용 어댑터 또는 handle은 수정·직접제어 메서드를 아예 갖지 않는다.
구현상 하나의 SDK 연결을 공유하더라도 capability-scoped facade와 credential을
분리한다.

공통 IR은 프로젝트, 컨트롤러, 하드웨어, 태스크, 프로그램, 루틴, 네트워크,
명령, operand, device/tag, 상태와 증거를 표현한다. 벤더 네이티브 artifact와
해석하지 못한 요소를 함께 보존하며 최소 공통분모로 손실 변환하지 않는다.
또한 device/tag를 센서·액추에이터·축·설비 자원·점유 구역·hazard zone에
매핑하고, 충돌 관계·선후행 조건·safety invariant와 근거 출처를 표현한다.

capability manifest는 최소한 vendor, engineering-system/version, controller
family/model, CPU·firm웨어 범위, project format, language, operation별 지원
수준, identity strength, evidence level/date, native round-trip 결과와 online
정책을 버전화한다. manifest에 없는 조합은 지원으로 추론하지 않는다.

에이전트와 GUI는 벤더별 UI 좌표·메뉴·클립보드 형식을 직접 사용하지 않는다.
모든 실행은 해당 어댑터의 MCP 계약을 통한다.

## 6. 권한과 안전 정책

| Capability | 의미 |
|---|---|
| `Read` | 로컬 파일·IDE·기존 artifact의 identity와 원본 읽기 |
| `Analyze` | 구조·상태·영향·진단 분석 |
| `Workspace Write` | 새 작업공간·revision·working copy·backup 생성 |
| `Offline Mutate` | 로컬 작업공간과 벤더 프로젝트 변경 |
| `Simulate` | 시뮬레이터 쓰기·실행·자극·복구 |
| `Online Observe` | 실제 컨트롤러 탐색·연결·identity·프로젝트 읽기·모니터링·trace |
| `Online Modify` | PLC 쓰기와 런중 수정 |
| `Direct Control` | 모드·값·force 등 실제 동작에 직접 영향 |
| `Controller Admin` | 초기화·firmware·보안·영구 파라미터 변경 |
| `Safety Engineering` | safety 프로그램·설정 변경; 별도 인증 전 영구 차단 |

현재 구현이 없다는 이유로 제품 요구사항을 제거하지 않는다. 반대로 PRD에 있다는
이유로 검증되지 않은 capability를 활성화하지 않는다. 실제 PLC 관련 capability는
벤더·CPU별 테스트벤치와 현장 안전 정책을 통과한 범위만 부여한다.

호출자가 권한 이름이나 `confirm: true`를 전달하는 것만으로 승인으로 인정하지
않는다. 사용자 identity, 대상, plan digest, 위험, 만료와 일회성 승인을 묶는다.

모델 endpoint 인증과 데이터 반출 동의도 MCP capability를 대체하지 않는다.
프로젝트 주석·문서·tool result에 포함된 지시는 system policy, 승인 또는
권한을 변경할 수 없으며, 모델이 만든 모든 실행 요청은 같은 안전 gate를
통과해야 한다.

이 제품은 safety PLC, safety relay, 비상정지, 기계적 인터록과 현장 안전
절차를 대체하지 않는다.

다음 중 하나라도 성립하면 모든 release 경로는 fail-closed 한다.

- 대상 identity가 불명확하거나 선택 세션과 결합되지 않음
- snapshot 또는 online 관찰이 오래됐거나 중간에 상태가 바뀜
- 의미 해석이 `partial`인데 변경 영향 경로를 닫을 수 없음
- 실제 diff가 계획과 다르거나 검사·readback이 실패함
- rollback artifact 또는 복구 결과가 불확실함
- 요구 capability·승인·현장 안전 조건 중 하나라도 없음

## 7. 현재 저장소의 구현 상태

현재 저장소에는 LS XG5000 adapter 위에 Launcher·Host·Web/PWA·단일 agent를
연결한 첫 제품 vertical slice와 로컬 프로젝트 작업공간 경로가 존재한다.
전체 PRD의 완성품은 아니다.

### 현재 구현 범위와 증거 수준

아래 목록에는 실제 XG5000/XG-SIM 실기 증거와 제품 자동 검증이 함께 있다.
실기·자동·미검증 판정은 [VALIDATION.md](VALIDATION.md)와 capability matrix의
항목별 표기를 우선하며, 목록에 있다는 사실만으로 live 합격을 뜻하지 않는다.

- 실행 중인 XG5000 프로세스와 로컬 프로젝트 attachment
- 네이티브 `XG5000LADDER` 추출과 지원 토큰 구조화
- 프로그램 목록, 일부 논리 그래프·상태 모델·직접 참조
- 동일 길이 피연산자 한 토큰 변경 계획·적용·diff
- XG5000 프로그램 검사와 제한된 네이티브 롤백
- 저장·재열기 시험 경로
- XG-SIM 공식 쓰기, RUN/STOP, 제한된 I/O 추종과 메모리 복구
- 로컬 `.xgw`/`.xgwx` 원본의 immutable capture와 hash-pinned working copy
- 인증·CSRF 보호 Windows 네이티브 `.xgw`/`.xgwx` 파일 선택과 취소·오류 처리
  (자동 검증, 실제 사용자 dialog smoke 미기록)
- 저장된 로컬 workspace의 원본/working-copy hash 재검증 후 재개와, drift 시
  XG5000 호출 전 fail-closed
- 재개 대상마다 새 engineering conversation 생성, 일반 재접속 시 Host 최근
  conversation 복원
- working copy를 실제 XG5000 V4.82.1에서 열고 Host 분석 세션에 연결
- 실제 XG5000 프로그램 목록을 Host/Web에서 탐색하는 읽기 전용 경로
- Launcher→Host→React/PWA→Pydantic AI→MCP 실행 골격과 서버 권위 상태
- run별로 범위를 고정하고 인자·결과·원시 오류를 제거한 MCP tool 진행 이벤트
- target 없이 요구사항을 구조화하되 MCP·mutation 도구를 받지 않는
  `new_project_planning` 대화와 typed Project Plan
- 별도 SQLite FTS5 지식 저장소, PDF·Markdown·텍스트 가져오기, SHA·페이지·
  섹션이 붙은 인용과 제조사·제품군·버전·상태·범위 선필터
- Agent의 read-only 지식 검색과 `knowledge_ref` 근거 이벤트
- 사람 확인 전 검색되지 않는 현장 기억·작성 선호, 12개 벤더 중립 제어 패턴

위 신규 계획 경로는 요구사항·가정·미결 질문·설계 초안을 만드는 범위다.
네이티브 프로젝트 생성, 일반 LD 구현, 검사·재열기·시뮬레이션 또는 PLC
적용이 완료됐다는 뜻이 아니다.

일반 MCP stdio 서버는 현재 `Read`·`Analyze`와 source-preserving
`workspace_write`만 허용한다. 동일 길이 operand 변경과 XG-SIM 경로는 격리된
로컬 검증 하네스의 기술 증거다. Host 승인·durable journal·recovery WAL과
crash reconciliation 계약은 구현·자동 검증됐지만, 현재 설치에서는 strong
COM PID identity를 확보하지 못했으므로 live mutation·save·simulation gate를
제품 권한으로 열지 않는다.

### 아직 구현되지 않은 핵심 제품 범위

- 벤더 중립 ControlStudio core와 adapter manifest
- 신규 XG5000 프로젝트의 일반 생성과 하드웨어 구성
- 일반 LD 런·접점·코일·명령·분기 생성·삭제·이동
- 실제 LS PLC 탐색, 읽기 전용 연결과 controller identity
- PLC에서 프로젝트 읽기와 로컬/온라인 비교
- 실제 PLC 온라인 값·상태·시간축 관찰
- 실제 PLC 쓰기, 런중 수정과 직접 제어
- 프로젝트 전체 설정·I/O·통신·물리 설비 모델
- 범용 controller/workspace transaction 계약과 실제 controller identity
- 장기 유지보수 이력과 제품 전체 범위의 감사·승인 정책
- 실제 현장 문서 corpus의 relevance 판정과 선택적 dense 검색 도입 여부 검증
- Mitsubishi와 Rockwell adapter
- 설치 프로그램·서명된 배포물·선택적 원격 접근

`program.focus`와 Web의 `XG5000에서 열기` 연결은 구현·자동 검증됐고 실제
대동 프로젝트의 프로그램 전면화·프로젝트 hash 불변 read-only E2E도 확인했다.
다만 위 프로그램 탐색은
프로젝트 목록과 위치 이동일 뿐 전체 PRD의 I/O·태스크·물리 설비 모델,
일반 신규 LD 로직 생성 또는 실제 PLC 온라인 관찰 완료를 의미하지 않는다.

`xg5000_control.gui`는 현재 MCP 호출과 증거 흐름을 시험하는 내부 개발 콘솔이다.
최종 사용자 GUI 또는 GUI MVP 완료 증거로 간주하지 않는다.

## 8. 전달 단계

### M0 — 범위와 기반 정리

- 이 PRD와 capability matrix를 제품 기준으로 고정
- 기존 MCP를 LS XG5000 adapter로 재분류
- 내부 개발 콘솔과 제품 GUI를 명확히 분리
- 실제 검증과 mock·합성 테스트를 구분

### M1 — 공통 계약과 LS 온라인 읽기

- 벤더 capability manifest와 공통 identity·artifact·evidence 계약
- Launcher→Host→웹/PWA→단일 agent→현재 LS read-only MCP의 얇은 제품 경로
- immutable local source → working copy → native round-trip revision 경로
- LS 컨트롤러 탐색·읽기 전용 연결·identity
- PLC에서 새 작업공간으로 읽기
- 로컬 프로젝트/PLC compare
- 온라인 모니터링·trace의 안전한 읽기 기반

M1의 제품 경로는 위험 변경을 열지 않는 read-only vertical slice다. M3은 이
경로 위에 전체 신규 구축·진단·대안·승인·복구 UX를 완성하는 milestone이므로
두 단계의 순서가 충돌하지 않는다.

### M2 — LS 신규 구축과 일반 편집

- 새 프로젝트·PLC·CPU·I/O·태스크·프로그램 생성
- 변수·설명·심볼과 일반 LD 구조 편집
- CPU별 명령·디바이스 호환성
- 저장·외부 재열기·XG-SIM 시나리오의 닫힌 트랜잭션

### M3 — 에이전트와 사용자 제품

- Windows Launcher의 one-click Host·MCP 시작과 종료 수명주기
- React·TypeScript 웹/PWA와 Python/FastAPI Host
- 서버 권위 conversation·run·approval·event store와 다중 client 재연결
- Pydantic AI 단일 Engineering Agent와 OpenAI-compatible model adapter
- 신규 요구사항·기존 진단·대안 추천
- 벤더 IDE 위치 열기, 인과 그래프·상태·시간축과 구조화된 증거
- diff·승인·검증·복구와 감사 UX

### M4 — LS 실제 쓰기

- 전용 테스트 PLC에서 전체 쓰기·재업로드 비교
- 제한된 런중 수정·중단·복구
- CPU/firm웨어/버전별 호환성 증거
- 별도 현장 승인 뒤에만 production capability 부여

### M5 — 추가 벤더

- Mitsubishi GX Works adapter
- Rockwell Studio 5000 adapter
- 동일 사용자 결과와 벤더별 native round-trip·online 정책 검증

## 9. 제품 릴리스 합격 기준

첫 LS 제품 릴리스는 다음을 모두 실제 경로에서 증명해야 한다.

### 신규 구축

- 요구사항에서 CPU·I/O·시퀀스·인터록 설계 artifact 생성
- 새 XG5000 프로젝트와 일반 LD 생성
- XG5000 검사 오류 0
- 저장 후 별도 프로세스의 동일 구조 readback
- 정상·경계·고장·복구 XG-SIM 시나리오와 원상복구

### 기존 프로젝트

- 전체 프로그램·태스크·설정·심볼·I/O를 손실 없이 열거
- 지원 구조의 원본 위치 추적과 미지원 요소 손실 0
- 직접·간접 참조, 실행 순서와 프로젝트 전체 영향 경로
- 최소 변경, 검사, 외부 재열기, 시뮬레이션과 복구

### 컨트롤러 읽기와 온라인 관찰

- 정확한 LS PLC identity와 읽기 전용 연결
- PLC에서 새 작업공간으로 프로그램·파라미터 읽기
- 누락 가능한 오프라인 정보 표시
- 로컬/컨트롤러 일치·불일치 판정
- 온라인 값·상태·trace 수집과 연결 해제
- 분석 권한에서 컨트롤러 쓰기 구조적 차단

### 사용자 제품

- ControlStudio 아이콘 한 번으로 Host·MCP·UI가 준비되고 별도 터미널이 불필요
- 원시 MCP JSON 없이 자연어 요청에서 실제 도구 실행까지 연결
- 관련 벤더 IDE 위치·증거·가설·대안·위험을 함께 표시
- OpenAI-compatible 모델의 streaming·tool calling capability를 설정에서 검증
- 새로고침·PWA 재실행·다중 client에서도 서버 권위 작업 상태를 복원
- 같은 대상의 중복 mutation을 차단하고 원격 접근은 기본 비활성
- 지원하지 않는 기능을 숨기거나 비활성 이유를 명시
- 모든 변경의 대상·승인·검사·복구 증거 조회

실제 PLC 쓰기와 런중 수정은 위 릴리스와 별도의 테스트벤치·현장 합격 기준을
통과하기 전까지 제품 완료로 주장하거나 활성화하지 않는다.
