# XG5000 MCP 구현 인계서

> **역사 기록 — 현재 기준 아님**
>
> 이 문서는 2026-07-27 기준 LS/XG5000 구현 인계 기록이며 당시 도구 수와
> 우선순위를 보존한다. 새 작업은 먼저 [PRD.md](PRD.md)와
> [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md),
> [CONTROLSTUDIO_GUI_HANDOFF.md](CONTROLSTUDIO_GUI_HANDOFF.md),
> [CONTROLSTUDIO_AGENT_RUNTIME_HANDOFF.md](CONTROLSTUDIO_AGENT_RUNTIME_HANDOFF.md),
> [MCP_CAPABILITY_MATRIX.md](MCP_CAPABILITY_MATRIX.md)를 읽어야 한다.
> 현재 제품 범위·도구 수·멀티벤더 및 controller 요구사항을 이 문서로
> 판정하지 않는다. 아래의 “GUI는 나중”이라는 순서는 당시 구현 계획이며,
> 현재는 read-only 제품 vertical slice를 일찍 검증하는 아키텍처 결정으로
> 대체됐다. 현재 서버 `tools/list`는 38개이며 이 문서의 14개 목록은 역사
> snapshot일 뿐이다.

역사 snapshot: 2026-07-27

현행 연결 주석: 2026-07-30

대상 브랜치: `dev`

기준 커밋: `f4a2567` (`Correct XG5000 version compatibility notes`)

## 0. 2026-07-30 현행 연결 상태

현재 구현·증거의 권위는 [MCP_CAPABILITY_MATRIX.md](MCP_CAPABILITY_MATRIX.md)와
[VALIDATION.md](VALIDATION.md)다. 이 문서 이후 다음 경로가 추가됐다.

- Launcher→FastAPI Host→React Web/PWA→Pydantic AI→MCP 제품 slice
- OpenAI-compatible 사용자 모델 설정과 실제 read-only agent tool loop
- immutable source capture와 hash-pinned working copy
- XG5000 0개 상태의 exact PID cold start, native Open과
  `controlled_launch_read_only` attach
- 실제 대동 프로젝트의 모델→Pydantic AI→MCP `project.list_programs` read E2E
- `program.focus`와 Host/Web의 `XG5000에서 열기` 자동 검증 및 실제 대동
  프로젝트 live 전면화·프로젝트 hash 불변 read-only E2E
- 동일 길이 LD operand 한 개의 exact approval/precondition, durable journal,
  recovery owner-transition WAL, XG5000 check/save와 crash reconciliation
- workspace open·attach·native create·오프라인 change·reconcile을 직렬화하고
  stale lease 자동 해제를 금지하는 Host 전역 XG5000 target-operation lease
- exact XG5000 PID/HWND와 native dialog label/control을 사용하는
  `project.create_native`
- LS XGT FEnet TCP 2004 상태·device 읽기 전용 도구

현재 MCP `tools/list`는 **38개**다. 이는 사용자 기능 38개라는 뜻이 아니라
workspace, read/analyze, 변경 단계, crash recovery, simulator와 역사적 IL
하네스의 원자적 실패·권한 경계를 나눈 결과다. Host는 이 전체를 모델에
노출하지 않는다.

검증 수준을 섞지 않는다. 신규 project 생성은 fake backend에서 검증됐고 현재
실제 XG5000에서는 modal/elevation 불일치로 변경 전에 거부됐다. FEnet은 fake
TCP까지만 검증됐으며 실제 PLC에는 연결하지 않았다. 한 operand product
transaction은 자동화된 crash tests를 통과했지만 새 Host 경로의 실제 XG5000
전체 E2E와 외부 재열기·XG-SIM은 아직 없다.

현재 설치에서는 COM activation/ROT PID readback이 불가능해 strong identity는
확보되지 않았다. 실기 성공 범위는 controlled-launch Read·Analyze이며
mutation/save/simulation/online에는 사용할 수 없다.

PLC에서 프로젝트·파라미터 복원, online write/run edit/force/mode/download,
일반 LD 구성과 Mitsubishi/Rockwell adapter는 구현되지 않았다. 따라서 이
문서의 역사적 “MCP 완료 판정 게이트”나 일부 과거 계획을 현재 완료 선언으로
사용하지 않는다.

## 1. 이 문서의 목적

새 Codex 세션은 이 문서를 먼저 읽고 PRD의 XG5000 MCP 구현을 이어간다.
당장의 목표는 GUI가 아니라 **MCP 서버를 완성하는 것**이다.

완성하려는 MCP는 다음 순서로 동작해야 한다.

```text
XG5000 인스턴스와 프로젝트 식별
  → 전체 프로그램 열거
  → 프로그램별 실제 XG5000LADDER 추출
  → 접점·배선·병렬 분기 그래프 복원
  → 디바이스 참조와 STEP/타이머/인터록/출력/알람 모델 생성
  → 사람이 읽을 수 있는 동작 보고서 생성
  → 변경 계획·검사·저장·재추출 검증
  → XG-SIM 런타임 검증과 원상복구
```

실제 PLC 다운로드는 현재 범위가 아니다. 실제 PLC 관련 기능은 안전장치가
완성되기 전까지 기본 비활성화 상태를 유지한다.

## 2. 새 세션이 가장 먼저 읽을 파일

1. `docs/PRD.md`
2. `docs/XG5000_CONTROL.md`
3. `docs/VALIDATION.md`
4. `mcp/README.md`
5. `mcp/src/xg5000_control/server.py`
6. `mcp/src/xg5000_control/session_state.py`
7. `mcp/src/xg5000_control/ladder.py`
8. `mcp/src/xg5000_control/project/operations.py`
9. `mcp/src/xg5000_control/live/xg5000_ui.py`
10. `mcp/tests/test_server.py`
11. `mcp/tests/test_ladder.py`
12. private PLC MCP repository의 `scripts/run_product_e2e.py`

## 3. 검증된 사실

다음 항목은 추측이나 계획이 아니라 이 PC의 XG5000/XG-SIM에서 검증됐다.
아래 사실은 당시 격리 하네스 증거다. 여기서 말하는 E2E는 현재
Launcher→Host→Pydantic AI 제품 E2E나 strong PID-bound identity를 뜻하지 않는다.

- XG5000 실행 인스턴스를 PID와 HWND로 찾고 특정 세션에 고정할 수 있다.
- 프로젝트 경로와 SHA-256을 세션 신원으로 검증할 수 있다.
- `XG5000MNE` 형식으로 제한적인 IL 읽기와 빈 프로그램 작성이 가능하다.
- XG5000 프로그램 검사, 저장, 재오픈 검증 경로가 존재한다.
- XG-SIM을 시작하고 프로그램을 쓰고 RUN/STOP할 수 있다.
- x86 XimUtil 브리지로 입력을 쓰고 출력을 읽은 뒤 메모리를 복원할 수 있다.
- XG-SIM 공식 쓰기 창과 “쓰기를 정상적으로 완료했습니다” 결과를 통과했다.
- M0를 쓴 직후 래더가 M1을 변경하는 실제 논리 동작을 확인했다.
- XG5000 V4.81과 V4.82.1 모두 당시 정의된 IL/LD/XG-SIM 하네스 E2E가 성공했다.
- 익명화된 실제 프로젝트의 LD 프로그램 4개를 실제 `XG5000LADDER` payload로
  추출했다.
- 익명화된 실제 프로젝트에서 총 344개 visual row를 추출했다.
- 익명화된 실제 프로젝트 전체 프로그램 검사 결과는 오류 0, 경고 36이었다.
- LD 읽기 전후 프로젝트 파일 SHA-256이 동일했다.

V4.81이 지원되지 않는다는 결론은 잘못된 과거 진단이다. 버전 자체 문제가
아니었으며 V4.81과 V4.82.1 모두 통과했다. 아키텍처, 권한, 창 선택, 실행
컨텍스트 문제를 버전 호환성 문제로 오판하지 않는다.

## 4. 2026-07-27 당시 구현된 MCP 도구

2026-07-27 당시 `mcp/src/xg5000_control/server.py`가 노출한 도구는 다음
14개였다. 현재 목록은 38개이며 capability matrix에서 확인한다.

- `xg5000.instances`
- `xg5000.attach`
- `xg5000.session_status`
- `program.read_il`
- `program.insert_blank`
- `program.to_ld`
- `program.check`
- `project.commit_save`
- `sim.start`
- `sim.write_program`
- `sim.run`
- `sim.runtime_proof`
- `sim.stop`
- `evidence.get`

이 도구들은 제어와 XG-SIM 검증 기반이다. PRD의 주 기능인 전체 LD 분석을
제공하지는 않는다.

### 반드시 피해야 할 오해

`program.to_ld`는 `program.read_ld`가 아니다.

현재 `program.to_ld`는 IL 화면에서 LD 화면으로 전환한 뒤 다시
`XG5000MNE` readback으로 변환 성공을 확인한다. 실제 `XG5000LADDER`
payload를 반환하지 않고 프로그램의 배선이나 병렬 분기를 분석하지 않는다.

`mcp/src/xg5000_control/ladder.py`의 `decode_ladder_payload()`도 완성된
LD 읽기 기능이 아니다. 이 함수는 이미 확보한 payload를 해석하는 순수
디코더일 뿐이다. 실행 중인 XG5000에서 payload를 가져오거나 전체 프로그램을
순회하는 제품 경로는 아직 없다.

## 5. 현재 LD 디코더가 하는 일과 못 하는 일

현재 가능한 항목:

- 프로그램명 추출
- visual row 추출
- NO/NC 접점
- 상승/하강 에지 접점
- 비교 접점
- OUT/SET/RST
- 일부 응용 명령
- 원본 좌표와 raw token 보존

현재 불가능한 항목:

- XG5000에서 live payload 획득
- 프로젝트 프로그램 전체 열거
- 분기 시작점과 합류점 복원
- 직렬 AND와 병렬 OR의 완전한 논리식 생성
- 분기 뒤 공통 인터록 판정
- 다중 출력 분기 복원
- 전체 rung 그래프 생성
- 디바이스 참조 인덱스
- STEP/타이머/카운터/래치 상태 모델
- 출력 및 알람 조건 설명

현재 파서는 관찰된 payload 형식에 관한 가정도 포함한다.

- 두 번째 `FF FE FF` 표식 이후 프로그램명
- 한 바이트 길이
- `FF FE FF FF` stream 표식
- opcode 67을 row 표식으로 사용
- `y % 1024`를 visual row 계산에 사용

이 가정은 실제 payload fixture를 추가해 검증해야 한다. 알 수 없는 형식이나
요소는 임의로 해석하지 말고 fail-closed 또는 `unknown`으로 반환한다.

## 6. 현재 구조의 중요한 기술 부채

### 6.1 IL 중심 단일 상태 머신

현재 상태는 다음처럼 한 방향으로 진행된다.

```text
DETACHED
  → ATTACHED
  → PROJECT_OPEN
  → EDITOR_IL
  → IL_READBACK_OK
  → LD_OK
  → CHECK_OK
  → SAVED_REOPENED_OK (역사적 명칭; 현재는 SAVED_REOPENED_UNVERIFIED)
  → SIM_READY
  → PROGRAM_WRITTEN
  → RUN
  → RUNTIME_PROVED
  → RESTORED
```

읽기 전용 LD 분석이 IL 편집, 저장, 시뮬레이터 단계를 통과해야 할 이유가 없다.
세션 신원과 기능 상태를 분리해야 한다.

권장 구조:

- identity: PID, HWND, executable, project path, project hash
- analysis capability: program list, LD snapshot, graph, semantic model
- mutation capability: backup, patch, check, save, reopen
- simulation capability: start, write, run, proof, restore

### 6.2 당시 명시적 동시 실행 잠금 부재 — 현재 해결된 범위

이 절의 원래 snapshot에는 operation lock이 없었다. 현재 Host는 전역 XG5000
target-operation lease로 workspace open, attach, native create, 오프라인
change와 reconcile을 직렬화한다. 이전 Host의 lease는 `STALE_BLOCKED`로 남기며
시간 경과만으로 자동 해제하지 않는다. 동일 `operation_id`와 exact target을
다시 증명한 recovery만 소유권을 이어받는다.

MCP change recovery도 lease owner 전환을 write-ahead record에 먼저 기록한다.
lease 교체와 journal 갱신 사이 hard crash는 다음 시작에서 repair하며 live owner
process가 존재하면 경쟁 recovery를 거부한다. 예상하지 못한 대화상자 탐지와
실제 변경·recovery 제품 E2E는 별도 검증 범위로 남는다.

### 6.3 진단 정보 부족

`xg5000.instances`와 세션 상태에 다음을 포함해야 한다.

- XG5000 프로세스 bitness
- MCP Python 프로세스 bitness
- 관리자 권한 여부와 integrity level
- 대상 창의 부모/자식 관계
- 활성 MDI child
- 프로젝트 경로와 해시
- 프로필/언어와 XG5000 버전

x86 XG5000과 x64 비관리자 Python 같은 실행 컨텍스트 불일치는 구체적인
진단 오류로 반환한다.

## 7. 구현할 MCP 표면

PRD에 명시된 읽기 전용 도구:

- `project.list_programs`
- `program.read_ld`
- `program.logic_graph`
- `program.state_model`
- `device.references`
- `project.behavior_report`

안전한 변경까지 완성하려면 추가할 도구:

- `project.snapshot`
- `project.backup`
- `program.plan_change`
- `program.apply_ld_patch`
- `project.diff`
- `project.validate_change`

도구 이름은 구현 전에 스키마 테스트와 함께 확정한다. 기존 도구와 호환성을
깨는 이름 변경은 별도 마이그레이션 없이 하지 않는다.

## 8. 단계별 구현 계획

### P0. 기반 정리

목표: 이후 LD 작업이 기존 IL 상태 머신에 끌려가지 않게 만든다.

작업:

1. 세션 identity와 analysis/mutation/simulation capability를 분리한다.
2. PID별 operation lock을 추가한다.
3. bitness/elevation/version/active child 진단을 추가한다.
4. MCP 입출력에 `schema_version`을 추가한다.
5. 모든 읽기 도구도 PID/HWND/project path/project hash를 검증한다.
6. 프로젝트 해시 또는 창 신원이 달라지면 즉시 실패한다.

완료 조건:

- 기존 14개 도구 테스트가 그대로 통과한다.
- 읽기 전용 분석 세션을 IL 편집 없이 시작할 수 있다.
- 같은 PID에 대한 동시 UI 작업이 직렬화된다.

### P1. 프로그램 열거와 실제 LD 수집

가장 먼저 구현해야 할 제품 기능이다.

#### `project.list_programs`

요구사항:

- 프로젝트 트리 또는 안정적인 XG5000 내부 구조에서 모든 LD 프로그램을 찾는다.
- 화면 절대 좌표보다 HWND, class, control ID, parent/child 관계를 우선한다.
- 프로그램 표시명, 안정적인 내부 식별자, 순서, 언어를 반환한다.
- 이름이 중복돼도 구분할 수 있어야 한다.
- 실제 활성 프로그램과 반환한 식별자가 일치하는지 확인한다.

#### `program.read_ld`

요구사항:

1. 요청한 프로그램을 활성화한다.
2. LD 편집기인지 확인한다.
3. 실제 `XG5000LADDER` clipboard/native payload를 가져온다.
4. payload 안의 프로그램명과 활성 MDI child를 교차 검증한다.
5. 원본 payload SHA-256을 계산한다.
6. `decode_ladder_payload()` 결과를 JSON으로 반환한다.
7. 프로젝트 파일의 호출 전후 SHA-256을 비교한다.
8. payload와 스크린샷을 evidence로 남긴다.
9. 같은 프로그램을 두 번 읽어 payload가 안정적인지 확인한다.

읽기 경로는 프로젝트를 저장하거나 수정해서는 안 된다.

권장 내부 모듈:

```text
mcp/src/xg5000_control/live/xg5000_ladder.py
mcp/src/xg5000_control/project/catalog.py
mcp/src/xg5000_control/ladder_payload.py
```

완료 조건:

- 대동 프로젝트의 4개 프로그램이 모두 열거된다.
- MCP 호출만으로 총 344개 visual row를 재현한다.
- 읽기 전후 프로젝트 SHA-256이 동일하다.
- 수동 단발성 스크립트가 필요하지 않다.

### P2. 구조적 IR과 분기 그래프

목표: 평면 요소 목록을 실제 래더 논리로 변환한다.

필요한 중간 표현:

```text
Project
  Program
    Network/Rung
      Node(contact, compare, instruction, junction, rail)
      Edge(wire)
      Branch
      Output
      SourceSpan(program, row, x, y, token offset)
```

반드시 지원할 의미:

- 직렬 접점 = AND
- 병렬 가지 = OR
- NC 접점 = NOT
- 분기 합류 뒤 조건 = 전체 가지에 적용되는 공통 조건
- 하나의 조건에서 여러 출력으로 갈라지는 구조
- SET/RST와 타이머 같은 상태 보유 명령

예:

```text
M00001 AND (P00020 OR P00021) AND NOT M00103
    → SET M00200
```

그래프와 논리식의 모든 항목은 원본 좌표와 token offset으로 역추적 가능해야
한다. 지원하지 않는 토큰은 삭제하지 말고 `unknown` 노드로 보존한다.

테스트:

- 직렬
- 단일 병렬
- 중첩 병렬
- 분기 이후 공통 인터록
- 다중 출력
- NC와 비교 접점
- SET/RST
- TON/CTR/TRTG 등 관찰된 응용 명령
- 알 수 없는 opcode
- 잘린 payload와 손상된 길이

완료 조건:

- 지원하는 기호는 모두 구조화된다.
- 미지원 토큰의 손실은 0이다.
- 그래프에서 원본 좌표로, 원본 좌표에서 그래프로 왕복 추적된다.

### P3. 디바이스 참조와 의미 분석

#### `device.references`

반환할 정보:

- 읽는 위치
- 쓰는 위치
- 접점/코일/명령 인수 역할
- 영향을 받는 출력과 상태
- 프로그램과 rung/source 좌표

#### `program.state_model`

모델링할 정보:

- SET/RST 래치
- S/STEP 전이
- 자기유지
- TON 및 타이머 완료 조건
- 카운터
- 인터록
- 출력 조건
- 알람 발생과 해제 조건

#### `project.behavior_report`

JSON과 Markdown을 모두 생성한다.

- 프로그램별 역할
- 초기화 흐름
- 자동/수동 운전 흐름
- 주요 STEP 전이
- 출력 구동 조건
- 알람과 복귀 조건
- 주요 인터록
- 미해석 요소와 신뢰도

모델이 추론한 내용과 payload에서 직접 확인된 사실을 구분한다.

완료 조건:

- 특정 출력 주소를 주면 전체 구동 조건과 차단 조건을 찾을 수 있다.
- 특정 입력 주소를 주면 영향을 받는 출력/스텝/알람을 찾을 수 있다.
- SET/RST 상태의 설정 조건과 해제 조건을 함께 설명할 수 있다.

### P4. 안전한 LD 변경

읽기와 분석이 안정화된 다음 진행한다.

원칙:

- IL/MNE를 기준 표현으로 사용하지 않는다.
- 대상 CPU가 지원하는 LD 명령만 사용한다.
- 요청을 바로 적용하지 않고 먼저 change plan과 impact diff를 만든다.

순서:

1. 프로젝트 백업과 원본 SHA-256
2. 변경 계획 생성
3. CPU/명령/디바이스 호환성 확인
4. 예상 영향 출력
5. LD 변경 적용
6. XG5000 프로그램 검사
7. 저장
8. 별도 XG5000 프로세스 또는 확실한 재오픈
9. LD payload 재추출
10. 계획한 변경과 실제 readback 비교
11. 불일치 시 실패하고 백업 복구

실제 LD 작성 방식은 P1에서 확보한 native payload 구조와 XG5000이 허용하는
공식 UI 동작을 검증한 뒤 결정한다. 검증되지 않은 바이너리를 프로젝트 파일에
직접 삽입하지 않는다.

### P5. XG-SIM 통합 검증

기존 시뮬레이터 도구를 변경 검증 파이프라인에 연결한다.

순서:

1. XG-SIM 시작
2. 공식 프로그램 쓰기
3. 성공 문구와 연결 상태 확인
4. RUN
5. 입력 자극
6. 관련 출력과 내부 상태 관찰
7. STOP
8. 모든 변경 메모리 원상복구
9. 증거 JSON과 스크린샷 저장

전체 바이트가 기대값과 완전히 같아야 한다는 식의 과도한 검증을 하지 않는다.
입력 M0를 쓴 결과 래더가 출력 M1을 자연스럽게 바꿀 수 있다. 요청한 비트,
관찰 대상 비트, 허용되는 프로그램 출력 변화를 구분한다.

### P6. 제품 안정화

- 대형 프로젝트 결과 pagination/filter
- 프로젝트/payload hash 기반 캐시
- 프로젝트 변경 시 캐시 무효화
- 진행률과 취소
- 예상하지 못한 창/대화상자 복구
- 다국어 프로필
- V4.81/V4.82.1 회귀 테스트
- 설치 및 실행 진단
- 민감한 프로젝트 payload의 artifact 보존 정책

## 9. MCP 완료 판정 게이트

다음 조건을 모두 만족해야 “MCP가 완성됐다”고 말한다.

1. 대동 프로젝트 모든 프로그램을 MCP만으로 열거한다.
2. 모든 LD payload를 수동 조작 없이 추출한다.
3. 대동 프로젝트에서 검증된 총 344개 visual row를 재현한다.
4. 모든 지원 기호와 병렬 분기를 구조적 그래프로 복원한다.
5. 미지원 토큰을 누락하지 않고 명시적으로 보고한다.
6. 디바이스별 읽기, 쓰기, 영향 관계를 조회할 수 있다.
7. 출력, 알람, STEP, 래치의 발생 및 해제 조건을 설명할 수 있다.
8. 읽기 작업 전후 프로젝트 SHA-256이 동일하다.
9. 변경 전 백업, 검사, 저장, 재오픈, LD 재추출 비교가 자동화된다.
10. XG-SIM 쓰기, RUN/STOP, 런타임 검증, 원상복구가 자동화된다.
11. XG5000 V4.81과 V4.82.1에서 반복 통과한다.
12. 모든 단위/통합/E2E 테스트가 통과하고 증거가 남는다.

## 10. 테스트 전략

### 단위 테스트

- payload 경계와 문자열 디코딩
- 모든 알려진 LD 기호
- 분기 그래프
- source coordinate 추적
- unknown 보존
- 손상 payload의 fail-closed 처리
- device reference index
- 상태 모델

실제 프로젝트 원본을 Git에 넣지 않는다. 기밀 정보를 제거한 작은 binary
fixture 또는 합성 fixture를 사용한다.

### 통합 테스트

- 프로그램 열거 mock
- active child 불일치
- 프로젝트 hash 변경
- clipboard 형식 부재
- 중복 프로그램명
- operation lock
- 대화상자 발생
- evidence 생성

### 실제 E2E

- 단순 테스트 프로젝트
- 익명화된 실제 프로젝트
- V4.81
- V4.82.1
- 프로그램 전체 읽기
- 변경 후 재오픈 readback
- XG-SIM runtime proof와 restore

## 11. 실행 명령

저장소 루트:

```powershell
cd C:\path\to\controlstudio-platform
```

환경 동기화:

```powershell
cd mcp
uv sync --extra dev
```

테스트:

```powershell
python -m pytest --basetemp .pytest-tmp
python -m ruff check src tests
uv lock --check
```

MCP 서버:

```powershell
uv run python -m xg5000_control.server
```

CLI:

```powershell
xg5000-cli instances
```

실제 E2E 스크립트는 private PLC MCP repository의 `scripts/run_product_e2e.py`를 사용한다. 실행 전 현재
PID, HWND, 프로젝트 경로와 해시를 다시 조회한다. 이전 세션의 PID/HWND를
재사용하지 않는다.

## 12. 작업 규칙

- 먼저 `git status --short --branch`로 사용자의 변경을 확인한다.
- 사용자 변경을 지우거나 덮어쓰지 않는다.
- 화면 좌표는 마지막 수단이다. HWND/class/control ID/parent 관계를 우선한다.
- 성공 문구 또는 readback 없이 성공 처리하지 않는다.
- 읽기 작업은 프로젝트 해시 불변을 증명한다.
- 변경 작업은 항상 백업과 readback을 남긴다.
- 실제 PLC 도구는 명시적 승인과 안전장치가 생기기 전까지 구현/활성화하지 않는다.
- GUI 작업은 MCP 완료 게이트를 통과한 뒤 시작한다.
- 임시 진단 스크립트에서 성공한 기능은 정식 MCP 경로와 테스트로 옮기기 전까지
  제품 기능으로 간주하지 않는다.

## 13. 2026-07-27 당시 새 세션 시작 지시문

아래 내용은 당시 기록이며 현재 세션에 그대로 전달하지 않는다.

```text
docs/MCP_IMPLEMENTATION_HANDOFF.md를 처음부터 끝까지 읽고,
docs/PRD.md, docs/XG5000_CONTROL.md, docs/VALIDATION.md와 현재 코드를
대조해 작업을 이어가라.

목표는 GUI가 아니라 XG5000 MCP 완성이다. 현재 제어/XG-SIM 기반은 있으나
전체 LD 분석 MCP는 미완성이다. program.to_ld를 실제 program.read_ld로
오해하지 마라.

우선 git 상태와 기존 테스트를 확인한 뒤 P0 기반 정리와 P1
project.list_programs/program.read_ld 구현부터 진행하라. 실제 실행 중인
XG5000에서 XG5000LADDER payload를 읽고 대동 프로젝트 모든 프로그램을
순회하며, 호출 전후 프로젝트 SHA-256 불변과 evidence를 증명해야 한다.

그 다음에만 분기 그래프, device.references, state_model,
behavior_report, 안전한 LD 변경 순서로 진행하라. IL/MNE 의존 분석을 새로
만들지 말고 LD를 기준으로 삼아라. 알 수 없는 토큰은 버리지 말고 보존하라.
각 단계에서 테스트와 실제 E2E 증거 없이 완료라고 보고하지 마라.
```
