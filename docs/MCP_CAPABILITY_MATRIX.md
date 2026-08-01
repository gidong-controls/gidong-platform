# ControlStudio MCP capability matrix

최종 갱신: 2026-07-30

이 문서는 [PRD.md](PRD.md)의 사용자 결과를 MCP capability와 실제 증거에
연결한다. 현재 저장소의 `xg5000_control`을 ControlStudio 전체 MCP로 부르지
않고 **LS XG5000 adapter 초기 구현**으로 분류한다.

## 1. 시스템 계층

| 계층 | 책임 | 현재 상태 |
|---|---|---|
| Windows Launcher | 사용자 클릭 시 Host 시작·검증, readiness 확인, UI 열기 | 구현·78 tests 통과. verified 소유 창 1개는 새 handoff 없이 재사용·전면화하고, 다중 소유 창은 하나로 복구하며 미검증 소유자는 fail-closed. medium→UAC→high Host·MCP→health/HMAC challenge 실제 smoke 통과; 이 최신 창 재사용 정책의 별도 visible live smoke는 미실행 |
| React Web/PWA GUI | 대화, 탐색, 시각화, 대안, 승인, 결과 설명 | 읽기·분석 UI와 제한된 change approval/recovery 구현. native project와 LS controller 화면/API는 `preview/fake_only/currentReady=false`이며 일반 실행 비활성 |
| Python/FastAPI Host | API, 서버 상태, agent·MCP 수명주기, client 재연결 | SQLite 상태, MCP supervisor, Pydantic AI, Host-owned change/native-project/controller API와 자동 테스트 구현 |
| Pydantic AI Engineering Agent | 요구 해석, 가설, 설계·수정안, 추천 | Pydantic AI 2.19 구현, 읽기·분석 MCP 최대 allowlist 12개. target이 없는 도구는 동적으로 숨기며 일반 Host의 controller 2개는 현재 비활성. 실제 모델→`project.list_programs`→XG5000 E2E와 대동 프로젝트의 `program.focus` read-only 전면화 확인 |
| OpenAI-compatible model adapter | 사용자 설정 모델의 streaming·tool calling 검증 | secret 저장·capability probe와 `nemotron-3-super` 실제 agent tool-call E2E 확인. endpoint·credential은 저장소에 기록하지 않음 |
| ControlStudio 공통 계약 | workspace, controller, logic, change, evidence의 벤더 중립 모델과 안전 정책 | workspace identity와 제한된 durable change 계약 구현. controller identity·vendor manifest·일반 logic 계약은 계속 필요 |
| LS XG5000 adapter | XG5000/XG-SIM native 읽기·분석·제한 변경 | 부분 구현·실기 증거 있음 |
| Mitsubishi GX Works adapter | Mitsubishi native project·controller·simulator | 미구현 |
| Rockwell Studio 5000 adapter | Logix native project·controller·emulator | 미구현 |

GUI는 MCP를 직접 호출하거나 XG5000을 제어하지 않는다. Host만 agent와 MCP
client를 소유하고, MCP 안전 코어가 실제 변경의 최종 권위를 가진다. 확정된
계층은 [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md)를 따른다.
활성 창·프로그램·클립보드를 공유하는 벤더 IDE UI 도구는 read라도
`engineering_session`별 단일 큐에서 직렬 실행하고, immutable artifact만
사용하는 순수 분석만 독립 병렬화한다.

공통 계약은 XG5000의 PID/HWND, 디바이스 주소와 클립보드 형식을 노출하지
않는다. 벤더 어댑터는 다음 port별 capability manifest를 반환해야 한다.

| Adapter port | 책임 |
|---|---|
| `EngineeringSystemAdapter` | 벤더 IDE 발견, 프로젝트 생성·열기·검사·저장 |
| `ProjectCodecAdapter` | 네이티브 artifact와 무손실 공통 IR 간 읽기·쓰기 |
| `ControllerObserverAdapter` | 읽기 전용 PLC 탐색·연결·identity·upload·관찰 |
| `ControllerModifierAdapter` | 별도 credential의 PLC 쓰기·런중 수정 |
| `DirectControlAdapter` | 별도 credential의 모드·값·force 제어 |
| `SimulatorAdapter` | 시뮬레이터 쓰기·실행·자극·trace·복구 |

Observer handle에는 수정 메서드가 존재하지 않아야 한다. 하나의 vendor SDK
연결을 내부 공유하더라도 capability-scoped facade와 credential은 분리한다.

## 2. 권한 모델

| Capability | 제품 의미 | 현재 LS adapter |
|---|---|---|
| `read` | 로컬 파일·IDE·기존 artifact의 identity와 원본 읽기 | 로컬 IDE/project만 구현 |
| `analyze` | 구조·상태·참조·영향 분석 | LD 중심 부분 구현 |
| `workspace_write` | 새 workspace·revision·working copy·backup 생성 | 로컬 native source capture·working copy와 Host-owned native project 생성 경로 구현 |
| `offline_mutate` | 로컬 작업공간·벤더 프로젝트 변경 | 동일 길이 LD operand 한 개의 Host 승인 transaction·durable recovery 구현. 일반 구조 편집은 없고 현재 설치는 strong COM PID identity가 없어 live product mutation gate가 닫혀 있음 |
| `simulate` | simulator 쓰기·RUN·자극·trace·복구 | 제한된 XG-SIM 경로 구현, 일반 서버 grant 차단 |
| `online_observe` | 실제 PLC 탐색·연결·identity·프로젝트 읽기·모니터링·trace | LS XGT FEnet 상태와 1~16개 device 읽기만 부분 구현. fake TCP 검증, provisional identity, 실제 PLC·프로젝트 복원·trace 미검증 |
| `online_modify` | 실제 PLC 쓰기·런중 수정 | 미구현·grant 불가 |
| `direct_control` | 모드·값·force 등 실제 동작 제어 | 미구현·grant 불가 |
| `controller_admin` | 초기화·firmware·보안·영구 파라미터 | 미구현·grant 불가 |
| `safety_engineering` | safety 프로그램·설정 | 별도 인증 전 영구 차단 |

현재 `xg5000.attach`는 XG5000 Windows 프로세스와 로컬 프로젝트를 고정할
뿐 실제 PLC에 연결하지 않는다. 이름이 비슷하다는 이유로 `controller.connect`
또는 `online_observe` 증거로 사용하지 않는다.

정상 stdio 서버 세션은 `analysis` 최소권한으로 시작한다. 오프라인 변경은
Host가 만든 transaction에 대해 exact project identity, durable lease,
일회성 승인 authorization이 모두 맞을 때만 해당 도구를 연다. 로컬 실기
하네스의 unsafe profile은 격리된 복사본 검증용이며 제품 승인이나 안전한
운영 권한이 아니다. Host-owned controller 읽기와 native project 생성은
모델이 직접 endpoint·PID/HWND·경로를 정할 수 없는 별도 경계다.

현재 cold-start session의 `controlled_launch_read_only`는 위 exact project
identity 요건을 대신하지 않는다. read/analyze만 가능하며 offline
mutation·save·simulate와 online capability에는 사용할 수 없다.

현재 정책 엔진은 `workspace_write`를 독립적으로 강제하지만 한 도구의 복수
capability 요구는 아직 표현하지 못한다. `controller.read_project` 구현 전에
`online_observe + workspace_write`의 교집합을 강제하는 정책 계약과 테스트가
먼저 필요하다. 분석 결과 artifact 기록과 source/project 변경 권한도 별도
storage boundary로 구분해야 한다.

## 3. 제품 진입 경로 coverage

| 사용자 결과 | 필요한 공통 capability | 현재 상태 |
|---|---|---|
| 아이콘 한 번으로 제품 시작 | Launcher, Host readiness, MCP supervisor, canonical origin | 구현·elevated XG5000 환경 one-click 첫 실행 실제 smoke 통과. 두 번째 실행의 기존 Host·단일 창 재사용은 자동 테스트 통과, 최신 정책의 별도 visible live smoke는 미실행 |
| 브라우저/PWA 대화와 재연결 | Host API, server-owned conversation/run/event store | 구현·SQLite 저장, SSE replay/reconnect, browser token 교환과 desktop/mobile Edge render 확인 |
| 교체 가능한 LLM 연결 | OpenAI-compatible Chat Completions, streaming·tool calling probe | 실제 사용자 설정 모델로 completion·streaming·tool calling·tool-result continuation·structured output과 agent→MCP E2E 확인. 실행 중 run 취소는 별도 Host API 경로 |
| 다중 client·선택적 원격 접근 | 한 write lease, 인증, 권한 분리, HTTPS remote gateway | 로컬 browser token 교환과 복수 화면 render 구현. Host 전역 target-operation lease가 workspace open·attach·native create·오프라인 change·reconcile을 직렬화하고 stale lease의 자동 해제를 금지하는 통합 테스트 통과. 원격 HTTPS·원격 권한 분리는 미구현 |
| 새 프로젝트 만들기 | workspace 생성, CPU/I/O/태스크/프로그램/심볼/로직 생성 | XG5000 native 새 프로젝트 dialog와 최초 LD 프로그램 생성 경로만 구현. fake backend 검증; 실제 현재 창은 modal/elevation 불일치로 변경 전 거부. `QUARANTINED`는 사용자 exact PID/HWND 기반 read-only reconcile만 허용하고 자동 재적용하지 않음. 요구사항 기반 하드웨어·I/O·태스크·심볼·일반 LD 생성은 미구현 |
| 로컬 프로젝트 열기 | native codec, artifact identity, 전체 구성 열거 | 원본 보존 workspace와 0-instance cold start 실제 검증. `controlled_launch_read_only` 분석 세션까지 구현됐으며 strong mutation identity는 없음. 전체 구성은 LD 일부 |
| PLC에서 읽어 프로젝트 복원 | controller read-only 연결, PLC→workspace 읽기, 누락정보 표시 | 미구현. FEnet 상태/device 읽기를 project upload로 오해하지 않음 |
| 로컬 프로젝트와 PLC 연결 | controller identity, 상관관계, native compare | FEnet endpoint·provisional status만 구현. serial/online project digest를 증명하지 못해 compare·write에는 사용 불가 |
| 온라인 관찰 | 값·STEP·timer·scan·통신 trace | FEnet 상태와 1~16개 device 단발 읽기만 fake TCP 검증. 실제 PLC·STEP·timer·scan·trace 미구현 |
| 기존 프로젝트 전체 이해 | 태스크·설정·I/O·호출·간접주소·모드 모델 | LD 직접 관계만 부분 구현 |
| 여러 설계·수정안 추천 | 가설·대안·효과·위험·검증·복구 모델 | 실제 모델의 read-only MCP tool loop는 검증. 설계·수정안 품질, 위험 비교와 승인 제품 경로는 미검증 |
| 안전한 오프라인 변경 | durable backup, plan, approval, apply, diff, check, reopen, sim, rollback | 한 operand Host transaction과 crash reconciliation 구현·자동 검증. check/save까지만 verification scope이며 외부 재열기·XG-SIM은 미실행 follow-up. 이전 격리 프로젝트 실편집/원복 증거와 새 제품 E2E는 구분 |
| PLC 쓰기 | controller backup, identity, compare, mode, approval, write, readback | 미구현·차단 |
| 런중 수정 | 활성 상태·출력·중단 조건·online diff·복구 | 미구현·차단 |
| 유지보수 이력 | 영속 요청·가설·승인·증거·결과 | SQLite conversation/run/event와 change proposal·approval·checkpoint·reconciliation 기록 구현. 장기 추세·설비 이력은 미구현 |

## 4. 필요한 공통 MCP 계약

아래 이름은 capability 경계를 설명하기 위한 후보이며 최종 공개 도구 수를
미리 고정하지 않는다.

### Workspace와 프로젝트 출처

- `vendor.capabilities`
- `workspace.create`
- `workspace.open_native`
- `workspace.status`
- `engineering.create_native_project`
- `project.read_structure`
- `project.configure_hardware`
- `project.create_task`
- `project.create_program`
- `project.create_symbols`
- `logic.read`
- `logic.plan_change`
- `logic.apply_change`

### Controller 읽기·비교·관찰

- `controller.discover`
- `controller.connect_readonly`
- `controller.identity`
- `controller.read_project`
- `controller.compare_project`
- `controller.runtime_status`
- `controller.observe`
- `controller.trace`
- `controller.disconnect`

`controller.read_project`는 Controller → Workspace 방향이다. 기존 작업공간을
자동으로 덮어쓰지 않고 새 artifact를 만들며 복원되지 않는 메타데이터를
보고해야 한다. 실제 PLC 읽기에는 `online_observe`, 새 workspace·artifact
생성에는 `workspace_write`를 함께 요구한다.

`workspace.open_native`는 source artifact를 immutable revision으로 보존하고
hash-pinned working copy를 만든다. source 원본을 직접 수정하지 않으며,
저장·독립 재열기·native round-trip 이후에만 새 revision을 확정한다.

### 위험 변경

- `controller.plan_write`
- `controller.apply_write`
- `online_edit.plan`
- `online_edit.apply`
- `controller.write_values`
- `controller.change_mode`
- `controller.force`

위 도구는 공통 이름이 존재하더라도 vendor capability, CPU/firm웨어별 실기
증거와 별도 승인 정책이 없으면 호출 불가능해야 한다. 프로젝트·task 생성은
`workspace_write`와 필요한 경우 `offline_mutate`, PLC 쓰기·런중 수정은
`online_modify`, 값 쓰기·모드 변경·force는 `direct_control`을 요구한다.

### 변경 트랜잭션과 증거

- `change.snapshot`
- `change.backup`
- `change.plan`
- `change.approve`
- `change.diff`
- `change.validate`
- `change.commit`
- `change.rollback`
- `evidence.query`

plan digest, user identity, 만료와 일회성 승인을 결합하고, durable journal과
프로세스 간 잠금으로 중단 후 복구와 동시 변경 차단을 보장한다.

## 5. 현재 LS XG5000 adapter 38개 도구

`mcp/src/xg5000_control/server.py`의 `TOOLS`가 반환하는 현재
`tools/list` surface를 직접 읽으면 **38개**다. 숫자는 제품 기능 수가 아니라
읽기, workspace, 변경 단계, 복구, simulator와 호환 하네스의 서로 다른
실패·권한 경계를 나눈 원자적 surface다. Host/agent가 38개를 모두 모델에
노출하는 것은 아니며, 도구를 줄이거나 늘리는 것 자체가 목표도 아니다.

증거 표기:

- **XG5000**: 이 PC의 실제 XG5000 V4.81 프로젝트에서 확인
- **XG-SIM**: 실제 XG-SIM 공식 쓰기 또는 runtime proof에서 확인
- **단위/통합**: mock 또는 합성 payload만 사용
- **내부 콘솔**: 사용자 제품이 아닌 MCP 진단 클라이언트

| 도구 | Capability | 실제 책임 | 증거·판정 |
|---|---|---|---|
| `project.create_native` | `workspace_write` | exact XG5000 PID/HWND와 네이티브 dialog label/control로 새 `.xgwx`와 최초 프로그램 생성 | unit/integration fake backend. 현재 실제 XG5000은 visible modal/elevation 불일치로 UI 변경 전에 안전 거부; live 성공 미검증 |
| `controller.ls.status` | `online_observe` | LS XGT FEnet TCP 2004 상태 읽기와 provisional identity | fake TCP. RFC1918·CGNAT·IPv6 ULA 숫자 주소만 허용; 실제 PLC 미검증 |
| `controller.ls.read` | `online_observe` | 1~16개 device의 같은 data type 단발 읽기 | fake TCP. write/force/mode/download 명령 없음, 실제 PLC 미검증 |
| `workspace.open_native` | `workspace_write` | `.xgw`/`.xgwx` 원본을 immutable capture와 hash-pinned working copy로 분리 | 실제 파일·단위/통합. 원본 전후 SHA-256 일치 |
| `workspace.status` | `read` | source·capture·working copy 경로와 현재 해시·변경 상태 재검증 | 실제 파일·단위/통합. UUID·경로 이탈·source tamper 차단 |
| `xg5000.open_project` | `workspace_write` | 검증된 working copy를 exact PID의 XG5000에 native Open하고 title/hash/lifetime을 확인 | XG5000 V4.82.1 cold-start 실기. 1x1 GDI+ helper window 제외, 0개면 exact 새 PID 실행, 2개 이상은 fail-closed. 일회성 read-only binding receipt 생성 |
| `xg5000.instances` | 세션 전 | XG5000 프로세스별 실제 main engineering window 하나만 열거 | XG5000. floating pane·popup과 1x1 GDI+ startup helper 제외 |
| `xg5000.attach` | 세션 전 | strong COM identity 또는 같은 MCP 수명의 controlled-open receipt로 세션 결합 | 현재 설치 실기는 `controlled_launch_read_only`; strong flag는 false. read/analyze만 허용하고 mutation/save/sim/online은 차단 |
| `xg5000.session_status` | `read` | 프로세스 내 세션·권한·단계·이벤트 조회 | 내부 콘솔. 영속 상태 아님 |
| `project.list_programs` | `read` | 현재 XG5000의 LD 프로그램 열거 | 실제 대동 프로젝트의 같은 engineering session에서 모델→Pydantic AI 호출로 새 MCP receipt 생성; 최종 응답과 receipt의 `NewProgram`, `알람`, `초기동작`, `출력` 일치. 다른 언어·태스크 제외 |
| `program.focus` | `read` | exact bound PID/HWND와 fresh catalog의 stable `program_id`로 해당 LD MDI child를 활성화하고 같은 XG5000 창만 전면화 | unit/server integration과 실제 대동 프로젝트 live E2E. PID/HWND ownership·MDI parent·editor identity·active child·foreground·project hash 불변을 모두 확인하며 편집·저장·simulator·PLC 접근 없음 |
| `program.read_ld` | `read` | native LD payload와 source 좌표 안정 readback | XG5000. 알려지지 않은 형식은 fail-closed |
| `program.logic_graph` | `analyze` | 지원 LD 배선의 구조 그래프 | XG5000, 일부 `partial` |
| `program.state_model` | `analyze` | 출력·SET/RST·일부 timer 상태 조건 | XG5000, 전체 상태전이 아님 |
| `device.references` | `analyze` | 직접 device 읽기·쓰기 위치 | XG5000. 간접주소·프로그램 간 전파 미완성 |
| `project.behavior_report` | `analyze` | 현재 LD 사실·추론 보고 | XG5000, `partial` |
| `project.snapshot` | `analyze` | LD payload 기준선 | XG5000. 설정·I/O·태스크 snapshot 아님 |
| `project.backup` | `offline_mutate` | 로컬 project file 사본과 해시 | XG5000. unsaved editor와 durable journal 제외 |
| `program.plan_change` | `analyze` | 동일 길이 operand 변경 precondition과 예상 diff | XG5000, 좁은 기술 증명 |
| `program.apply_ld_patch` | `offline_mutate` | XG5000 UI를 통한 계획된 operand 변경과 readback | XG5000. 일반 구조 편집 아님 |
| `project.diff` | `analyze` | snapshot·plan·현재 LD 구조 diff | XG5000. 설정·동작 diff 제외 |
| `project.validate_change` | `offline_mutate` | 프로그램 검사와 선택적 즉시 역변경 | XG5000 오류 0. 저장·외부 재열기·sim과 분리 |
| `project.rollback_change` | `offline_mutate` | 적용 직후 payload와 exact 일치할 때 독립 역변경 후 논리·비휘발 payload 확인 | XG5000. 선택좌표 차이만 별도 보고 |
| `change.transaction_lookup` | Host-owned recovery | snapshot 응답 유실 시 `host_transaction_id`로 durable transaction 재발견 | unit/integration·cross-process crash tests. 모델 비노출 |
| `change.transaction_status` | `read` | journal checkpoint·state·lease와 recovery action 조회 | unit/integration·cross-process crash tests. recovery owner-transition WAL repair와 live-owner 경쟁 거부 포함 |
| `change.abort` | `offline_mutate` | mutation 전 중단을 `ABORTED_NO_MUTATION`으로 확정하고 lease 해제 | unit/integration·crash tests. apply 이후 abort 금지 |
| `change.commit` | `offline_mutate` | 검증·저장 checkpoint가 맞는 transaction만 terminal commit | unit/integration. 새 Host path의 실제 XG5000 전체 E2E 미검증 |
| `program.read_il` | `read` | 빈 시험 프로젝트용 제한 MNE readback | XG5000 보조 하네스, 일반 LD 핵심 아님 |
| `program.insert_blank` | `offline_mutate` | 빈 MAIN에 허용된 시험 IL 삽입 | XG5000 보조 하네스 |
| `program.to_ld` | `offline_mutate` | 시험 IL→LD 전환과 readback | XG5000 보조 하네스 |
| `program.check` | `offline_mutate` | XG5000 공식 프로그램 검사 | XG5000. CPU compatibility matrix 미완성 |
| `project.commit_save` | `offline_mutate` | 현재 IDE 저장과 파일 해시 전이 | XG5000. `SAVED_OK`까지만 전이하며 새 프로세스 association도 현재 `SAVED_REOPENED_UNVERIFIED` |
| `sim.start` | `simulate` | XG-SIM 시작 | XG-SIM |
| `sim.write_program` | `simulate` | XG5000 공식 simulator 쓰기 | XG-SIM. controller 쓰기와 무관 |
| `sim.run` | `simulate` | simulator RUN | XG-SIM |
| `sim.runtime_proof` | `simulate` | 제한된 입력 자극·출력 추종·복구 | XG-SIM 3회. 일반 scenario runner 아님 |
| `sim.stop` | `simulate` | 정상 STOP과 실패 뒤 비상 STOP | XG-SIM/단위. 실패 경로는 `STOPPED_UNVERIFIED` |
| `evidence.get` | `read` | 현재 세션 이벤트·artifact 경로 | 프로세스 내 증거. 비영속·비서명 |

현재 즉시 합칠 명백한 중복은 없다. 그러나 이 38개가 ControlStudio 전체 도구
목록이라는 의미도 아니다. IL 3개는 LS 시험 하네스 보조 기능으로 분류하고,
공통 production 경로가 대체한 뒤 내부화 여부를 판단한다.

## 6. LS adapter 합격 전 차단 항목

선택 PID/HWND·working-copy hash 결합, Host transaction ID, durable journal,
MCP recovery owner-transition WAL, 프로젝트 범위 lease, Host 전역
target-operation lease, 일회성 plan 승인과 pre/post-apply crash
reconciliation은 좁은 한 operand 경로에 구현됐다. Host lease는 stale이라는
이유만으로 자동 해제하지 않고 동일 operation·exact target recovery만
허용한다. 다음은 여전히 release 차단 항목이다.

1. 제한된 elevated x86 broker로 COM PID/HWND/path/hash readback을 확보하고
   `project_identity_verified_against_selected_pid=true`를 실제 검증한다.
2. 새 Host transaction을 격리된 실제 XG5000 working copy에서 처음부터 끝까지
   실행하고 check/save/rollback/reconcile 증거를 하나의 product E2E로 남긴다.
3. patch → 검사 → 저장 뒤 외부 재열기 → 재추출 → XG-SIM scenario를 검증된
   상태 기계로 연결한다. 현재 결과는 이 단계를 `not_executed`로 표시한다.
4. editor의 unsaved 변경을 포함해 분석 cache 최신성을 증명한다.
5. 전체 프로그램·태스크·설정·I/O·심볼·간접주소·실행 순서와 일반 LD
   구조 편집을 구현한다.
6. controller serial·CPU·firmware·online project identity를 강하게 증명하고
   PLC→workspace 복원과 local/controller compare를 구현한다.
7. 실제 LS PLC에서 read-only FEnet status/device 관찰과 reconnect·freshness를
   검증한다. 현재 fake TCP 증거를 현장 PLC 증거로 승격하지 않는다.
8. 신규 프로젝트를 실제 XG5000에서 생성·검사·저장·독립 재열기하고,
   요구사항 기반 CPU/I/O·태스크·심볼·일반 LD 구성으로 확장한다.
9. 복수 capability 조건을 정책 엔진에서 강제한다.
10. actuator·sensor·axis·resource·hazard zone·safety invariant 물리 모델을
   공통 IR과 영향 경로에 추가한다.

vendor capability manifest는 vendor, IDE/version, controller family/model,
CPU·firm웨어 범위, project format, language, operation별 support, identity
strength, evidence level/date, native round-trip과 online policy를 버전화하고
provider conformance test로 검증해야 한다.

## 7. 벤더별 상태

| 벤더 | Engineering system | 프로젝트 | Controller | Simulator | 상태 |
|---|---|---|---|---|---|
| LS ELECTRIC | XG5000 | `.xgwx`, native clipboard 일부 | FEnet status/device read-only fake TCP까지 | XG-SIM 일부 | 첫 adapter, 부분 구현 |
| Mitsubishi | GX Works2/3 | vendor native | 미구현 | vendor simulator | 계획, 코드 없음 |
| Rockwell | Studio 5000 | `.ACD` 등 vendor native | 미구현 | Logix Echo/Emulate 계열 | 계획, 코드 없음 |

벤더별 태스크·프로그램·routine, device/tag, online edit와 upload/download
의미 차이는 capability manifest와 native extension으로 보존한다. LS 구현을
복사해 공통 표준으로 삼지 않는다.

## 8. 다음 구현 순서

1. **부분 완료** — Launcher→Host→React/PWA→Pydantic AI→현재 LS
   read-only MCP와 실제 XG5000의 얇은 제품 vertical slice
2. **부분 완료** — immutable local source·working copy·XG5000 열기 경로
3. **부분 완료** — 한 operand durable transaction과 승인·crash reconciliation
4. **자동 검증만 완료** — native LS project 생성 path와 FEnet 상태/device 읽기
5. 새 transaction의 실제 XG5000 E2E와 외부 재열기·XG-SIM scenario 닫기
6. 실제 LS PLC 읽기 전용 관찰, strong identity, PLC→workspace 읽기와 compare
7. LS 신규 프로젝트의 하드웨어·I/O·태스크·심볼·일반 LD 구조 생성
8. 공통 workspace·artifact·controller identity·capability manifest와
   native round-trip revision
9. 온라인 STEP·timer·scan·통신 trace와 장기 유지보수 증거
10. 전용 테스트 PLC의 LS 쓰기·재업로드·제한 런중 수정
11. Mitsubishi adapter
12. Rockwell adapter

1번 vertical slice는 Host 권위 상태, browser token 교환, 당시 MCP 전체 37개 도구
발견과 당시 agent용 11개 읽기 전용 도구 제한, SSE 재연결과 기존 desktop/mobile
Edge render를 검증했다. 실제 XG5000 V4.82.1과 대동 프로젝트로 원본 해시
보존, working copy 생성, XG5000 열기와 Host 분석 세션 연결을 확인했고,
2026-07-29에는 실제 `nemotron-3-super`→Pydantic AI 2.19→MCP
`project.list_programs`→XG5000의 네 LD 프로그램 read-only E2E도 통과했다.
이 E2E의 assurance는 `controlled_launch_read_only`이고 strong COM identity는
아니다. 저장·mutation·시뮬레이션·PLC 접근은 수행하지 않았다.
모델 endpoint와 credential은 저장소에 기록하지 않는다. 외부 모델 오류가
credential 값을 반사하는 hostile test에서도 로그·SQLite run 오류·API/event에
민감 값이 남지 않는 것을 확인했다.

한 operand product transaction, native project와 FEnet read-only path가 코드와
UI에 추가됐지만 검증 수준은 위 표대로다. 특히 신규 프로젝트의 실제 생성과
실제 PLC FEnet은 성공 검증되지 않았고, FEnet status/device 읽기는 PLC project
upload·복원이나 write capability가 아니다.

실제 PLC 쓰기, 런중 수정과 직접 제어는 해당 벤더·CPU·firm웨어별 테스트벤치와
현장 안전 절차를 통과하기 전까지 완료로 선언하거나 활성화하지 않는다.
