# XG5000 제어 및 LD 분석 기술 명세

현행 계약 갱신: 2026-10-11. 제품 요구사항은 [PRD](../PRD.md), 구현 순서와
실제 호출자는 [구현 계획 §4·§5](AGENT_PRODUCT_PLAN.md)를 따른다.

대상 환경: Windows 11, XG5000 V4.81 및 V4.82.1, XG-SIM

이 문서는 첫 provider adapter의 기술 명세다. 초기 LS 제품에서 XG5000은
래더와 벤더 프로젝트의 시각적 원본이며, ControlStudio 웹 GUI는 이를 범용
래더 IDE로 복제하지 않는다. ControlStudio는 분석 근거와 프로그램·네트워크
위치를 구조화해 표시하고 XG5000의 해당 위치를 연다.

선정 구조의 대형 LD 읽기·부분 편집·보존·저장·재개 타당성은 인수됐다.
도구가 실행한 입력, 현재 LD 관측, 저장 파일, 프로그램 검사와 기능 시험은 각각
별개의 결과다. 모델은 요청과 실제 결과에 필요한 관측·검사·저장을 선택한다.

## 1. 결론

XG5000은 공개된 범용 자동화 API만으로 모든 편집 기능을 제공하지 않는다.
선정 경로는 다음 세 계층을 조합한다.

1. 실제 `XG5000LADDER` 캡처를 보관하고 작은 구조 지도·정확한 상세 조회를 제공한다.
2. 모델이 캡처에 속한 요소·행·포트 참조와 변경 순서를 선택한다.
3. CLI가 native 커서·Find/Go To·편집 명령·입력창으로 실행하고 실제 결과를 돌려준다.

LD 셀의 고정 화소·스크롤 계산은 이 경로에 없다. XG의 논리 행·셀과 native record
좌표는 실행기 내부 선택 수단이며 공통 요소 ID가 아니다. 창·입력창은 PID/HWND,
컨트롤 ID와 부모 관계로 다룬다. 모델은 LD 참조와 변경 내용을 지정한다.

XG5000 UI 자동화는 XG5000과 같은 로그인 사용자·interactive desktop·권한
수준에서만 수행한다. 잠금 화면, Session 0, 다른 RDP 세션 또는 integrity
level 불일치를 확실히 판별하지 못하면 UI 작업을 시작하지 않는다.

지원 XG5000 실행 파일이 설치·구성된 환경에서는 새 vendor process도
`highestAvailable`로 시작되므로, Launcher는 Host를 만들기 전에 Windows
`ShellExecute(..., "runas", ...)`로 자신을 다시 실행하고 UAC 승인을 기다린다.
이미 실행 중인 XG5000이 있으면 그 process integrity도 함께 확인한다. 이미 검증된
Host가 필요한 integrity보다 낮으면 그 세션을 재사용하지 않고 재시작을 요구한다.
XG5000 실행 파일이 없는 planning/controller-observe 전용 환경만 기본
`asInvoker` 경로를 유지한다. UAC secure desktop의 승인 자체는 자동 입력하거나
우회하지 않는다. 이는 Microsoft의
[동일 권한 수준 원칙](https://learn.microsoft.com/en-us/troubleshoot/power-platform/power-automate/desktop-flows/cannot-interact-with-desktop-application)과
[최소 권한·별도 elevated helper 지침](https://learn.microsoft.com/en-us/windows/win32/secbp/running-with-administrator-privileges),
그리고 pywinauto의
[interactive desktop 요구사항](https://pywinauto.readthedocs.io/en/latest/remote_execution.html)에
맞춘 경계다. 현재 즉시 경로는 Launcher/Host 전체를 target과 같은 수준으로 맞추며,
설치형 제품에서는 XG5000 UI/네이티브 명령만 수행하는 작은 elevated helper로
분리하는 것이 후속 최소 권한 구조다.

## 2. XG5000LADDER

### 확인된 구조

네이티브 페이로드는 다음 정보를 포함한다.

- PLC명과 프로그램명 UTF-16LE 헤더
- 행 스트림 시작 마커 `FF FE FF FF`
- 탭으로 구분된 요소 레코드
- 시각 행 기준 좌표: 행마다 1024 증가
- 접점 코드
  - `6`: NO
  - `7`: NC
  - `8`: 상승 에지
  - `9`: 하강 에지
- 비교 요소 `33`
- 출력 하위형식
  - `14`: OUT
  - `16`: SET
  - `17`: RST
  - `33`: 응용명령

현재 생산 읽기·디코더는 `controlstudio-xg5000-cli`의
`src/controlstudio_xg5000_cli/vendor/live/read_ld.py`, `vendor/ladder.py`,
`vendor/ladder_graph.py`가 소유한다. MCP의 동명 모듈은 호환 코드와 구분한다.
해석하지 않은 배선 토큰도 각 행의 `raw_tokens`에 보존한다.

### 현재 해석 범위

현재 디코더는 프로그램명, 시각 행, 접점, 비교식, 출력 명령과 좌표를 복원한다.
각 record는 opcode 시작 offset과 별도로 실제 operand가 있는
`value_token_offset`을 제공한다. 논리 그래프는 관찰된 행 순서와 좌표가 지지하는
직렬 조건·일부 병렬 가지, network/rung identity와 canonical digest를 만들고
원본 source span으로 되돌아갈 수 있게 한다. 해석하지 못한 token, branch 또는
application instruction이 있으면 network/rung을 `partial`로 표시하며 모든 런을
완전한 불리언 식으로 변환했다고 주장하지 않는다.

## 3. 모델 도구와 부분 편집

Agent의 새 service catalog는 `references` 인터페이스를 사용한다. 캡처의
`result_id` 또는 `capture_step`+`program_name`에 `eN`/`rN`과 관측한 named port를
결합한다. 참조는 관측 당시의 배치이며 행 이동 뒤 현재 위치로 자동 재해석하지 않는다.

`edit_contact`, `remove_wire`, `remove_element`, `insert_contact_after`,
`insert_contact_on_wire`, `connect_ports`는 typed `editor_apply_edits.entries`로
제공한다. 한 항목 변경도 같은 batch 계약이다. 명령 편집·직렬/병렬 선삽입·행 삭제는
standalone이고, 작성에는 `insert_rung_before`, `populate_rung_at`, `insert_terminal_at`을 쓴다.
지원 동작·인자와 과거 대화 catalog의 구분은 구현 계획 §4에 있다.

batch는 비원자 순차 실행이다. 완료 prefix, 실패/결과불명 항목과 미시작 suffix를
모델에 반환하며 입력 완료만으로 회로 기능을 판정하지 않는다. 모델이 그 결과를 받아
추가 읽기·수정·저장·마무리를 결정한다. `connect_ports`는 현재 직선/한 번 꺾이는 배선이다.

`query_ld_snapshot`/`read_tool_result`는 보관된 관측의 조회로 추가 GUI 읽기를 하지 않는다.
현행 공개 `editor_read_ld_rows`는 full capture를 얻은 뒤 지정 행을 투영한다.
CLI 내부 `ld_cursor.read_ld_rows`의 실제 range Copy와 동작 비용을 혼동하지 않는다.
캡처와 실제 입력 결과·도구 이력은 다음 모델 호출 전에 보존하고 checkpoint로 재개한다.

## 4. 프로그램 검사와 저장

native 도구 `editor_program_check`는 검사 명령을 호출해 옵션 창을 연다. 이것만으로
검사가 끝난 것은 아니다. 모델이 실제 창을 처리하고 `editor_read_check`로 결과 pane의
오류/경고를 관찰한다. 별도 compatibility CLI `program-check`의 완전 검사 계약과 구분한다.
`save_project`는 연결된 실제 작업본을 저장하고, `read_saved_project`는 저장 파일을
관측한다. 현재 편집 buffer 읽기, 저장 파일 관측과 독립 재열기는 서로 다른 근거다.
일반 편집마다 MDI 닫기·전체 읽기·새 프로세스 재열기·SIM을 강제하지 않는다.

경고는 오류와 구분해 기록한다. 경고가 있으면 통과 여부와 별개로 개수와 메시지를
증거에 남긴다.

## 5. XG-SIM

프로그램 다운로드와 런타임 메모리 쓰기는 다른 작업이다.
현재 MCP에는 `sim.start/write_program/run/runtime_proof/stop` primitive가 있다.
CLI의 `sim-target-status`는 관찰 도구이며 SIM 실행 전체가 CLI로 추출된 상태는 아니다.
현행 native reference service catalog에도 SIM은 아직 연결되지 않았다.

다음 구현은 실제 입력·출력·실패를 같은 대화의 모델에 돌려주고, 모델이 부분 수정한
작업본의 적용·저장·readback과 그 버전의 재시험을 연결한다. 실패 원인이나 정답 회로를
장비별 실행기에 넣지 않는다. 런타임 입력 변경과 STOP/원복의 실제 결과도 반환한다.

### 기존 Host trial 경로의 호환 계약

아래는 기존 trial의 저장·복구 규칙이다. 새 native 대화의 고정 단계표나 모든 요청의
Keep 의무로 사용하지 않는다.

1. XG-SIM 시작
2. XimUtil 연결로 서버 준비 확인
3. XG5000 공식 `쓰기` 실행
4. 쓰기 완료 문구 확인
5. RUN 전환
6. 입력 비트만 변경하고 출력 추종 확인
7. 성공·실패와 관계없이 CPU STOP 확인
8. 원래 입력 메모리로 복원하고 exact readback 확인
9. 시나리오·STOP·원복 receipt를 digest-bound `OBSERVING` trial로 Host SQLite에 저장
10. 사용자가 `ACCEPTED` 또는 `REVERTED`를 결정하고, 채택한 경우에만 보호 working copy 결속

MCP 응답 유실이나 불완전 evidence로 CPU STOP·입력 원복을 끝까지 증명하지 못하면
Host는 trial을 `QUARANTINED`로 저장한다. 이 상태는 실패를 자동 재실행하거나
채택·원복으로 꾸미지 않으며, XG-SIM 상태를 명시적으로 복구하기 전까지 working
copy 결속을 막는다. `OBSERVING` 또는 `QUARANTINED` trial은 사용자가 Web 창을
닫거나 Host를 재시작해도 bootstrap의 active operation으로 다시 노출된다. 사용자는
미복구 경고를 확인해 `QUARANTINE_ACKNOWLEDGED`로 이력을 닫을 수 있지만, 이 결정은
CPU 정지·입력 원복을 증명하지 않으며 working copy를 활성 대상으로 만들지 않는다.

XimUtil 정보:

| 항목 | 값 |
|---|---|
| ProgID | `XimUtil.DeviceInterface` |
| bitness | x86 in-process COM |
| 브리지 | `gidong-plc-mcp/src/xg5000_control/live/xgsim_bridge_x86.cs` |

## 6. 별도 호환·관찰 경로

실제 도구 표면은 현재 MCP `tools/list`와 Host의 세션별 catalog를 따른다.
아래 기존 설계 compiler·한 operand 거래·COM save 경로는 선정한 native 참조 편집
대화와 별개다. 그 제한이나 전체 프로젝트 재읽기 절차를 일반 LD 편집에 이식하지 않는다.
물리 PLC write·run edit·force·mode·download 권한은 별도 범위다.

### 6.1 과거 native design v2 build 계약

이 절은 기존 compiler가 사용한 timer/restart 규칙의 기록이다. 현재 신규 target 준비는
다음 구현 항목이며 `workspace.open_native`는 기존 파일을 작업본으로 여는 기능이다.
신규 작성은 모델이 현재 CPU·I/O·timer parameter와 사용자 운전 조건을 조사하고 일반
LD 작성 도구로 설계하는 경로로 연결한다. 아래 compiler를 기본 설계자로 복원하지 않는다.

새 프로젝트의 timer area parameter는 변경하지 않는다. 따라서
[LS XGK/XGB Instructions Manual V2.2](https://www.ls-electric.com/upload/customer/download/f46ceeb8-094e-4a41-b623-7c9a60c7880b/XGK_XGB_Instructions_Manual_Eng_V2.2.pdf)의 기본
영역인 XGK `T0000-T0999=100ms`, `T1000-T1499=10ms`,
`T1500-T1999=1ms`와 XGB `T000-T191=100ms`, `T192-T200=10ms`,
`T201-T255=1ms`만 typed duration과 일치할 때 컴파일한다. XGK bit device는
`P/M` 뒤 5자리, XGB는 4자리이며 마지막 자리는 hexadecimal `0-F`다.

native design v2의 각 sequence는 `restartPolicy`를 가진다. 기본
`require_start_release`는 마지막 STEP 완료 조건, sequence reset 조건과 해당
recovery 조건에 `startAll` 중 적어도 한 조건의 반대 상태를 명시해야 한다.
따라서 TON 완료나 alarm reset 뒤 START가 계속 ON이라는 이유로 자동 재기동하지
않는다. `continuous`는 사용자가 연속 반복 운전을 명시적으로 검토한 경우에만
허용한다. 이 정책과 모든 STEP 완료 조건은 계획 검토 화면에 그대로 표시된다.

### 6.2 기존 오프라인 한 operand transaction

이 호환 거래에서 제품 Host는 동일 길이 LD operand 한 개에 한해 exact target·plan digest·
만료 사용자 승인과 MCP 일회성 HMAC authorization을 결합한다. MCP는
snapshot·backup·plan·apply·diff·XG5000 check·save checkpoint와 프로젝트
범위 durable lease를 기록한다.

변경 분석에 앞서 `project.hardware_context`는 세션·PID/HWND·경로와 SHA-256이
일치하는 `.xgwx` working copy의 첫 bounded gzip member에서 XG5000 project XML을
읽는다. 원본 파일은 쓰지 않는다. 반환 객체는
`xg5000.project.hardware-context/1.0`이며, `context_digest`는 해당 필드를 제외한
project binding·CPU·timer area·I/O 관측 전체의 canonical SHA-256이다. Host는 이를
`controlstudio-hardware-context-evidence/v1` receipt로 영속화하고 후속 target
analysis와 승인 계획이 exact digest를 다시 인용하게 한다.

2026-08-11 실제 대동 XGWX readback에서 CPU/main unit `XBC-DN/DP64H`, 활성 timer
area `T0-T499=100ms`, `T500-T999=10ms`, `T1000-T1023=1ms`, I/O module
`XBC-DN/DP64H`와 `XBE-DC08A`, BASIC PARAMETER의 전역 DI/DO/AI/AO device prefix와
offset을 관측했다. 100us `0..0` 값은 T0를 포함하는 활성 영역과 겹치는 비활성
sentinel로 보존했다. [LS XGK/XGB Instructions Manual V2.2 §2.6.5](https://www.ls-electric.com/upload/customer/download/f46ceeb8-094e-4a41-b623-7c9a60c7880b/XGK_XGB_Instructions_Manual_Eng_V2.2.pdf)도
XGB에서 0.1ms 설정을 지원하지 않는다고 명시한다.

프로젝트 XML 자체에는 module별 device address range, 외부 terminal wiring, module
health와 현재 PLC runtime이 없다. 다만 CPU가 exact `XBC-...H`이고 XML의 base/slot이
유일할 때에만 [LS XGB Basic Tutorial §3.1.2](https://www.ls-electric.com/upload/customer/download/b203572b-fb74-4d5a-90b1-20c2f93d5c8d/XBM_XBC_Basic_Tutorial.pdf)의
고정 4-word P-device 슬롯 배정을 versioned profile로 결합한다. 실제 샘플의 slot 1
`XBE-DC08A`에는 `P00040-P0007F`가 `observed`가 아닌 `documented` slot reservation으로
결속된다. 다른 CPU family나 범위 밖 slot에는 이 규칙을 적용하지 않는다.

`XBC-DN/DP64H`의 base 0/slot 0 CPU가 XML에서 정확히 관측된 경우에는 같은 vendor
profile로 built-in `P0000–P001F` digital input과 `P0020–P003F` digital output을
각각 channel 0–31에 결속한다. `program.change_target_analysis`는 물리 P operand의
LD 역할(contact=input, coil=output)과 direction, module rack/base, slot, module id,
terminal range, channel을 하나의
`xg5000.project.field-io-binding/1.0` digest receipt로 확인한다. 범위가 없거나,
중복되거나, channel을 계산할 수 없거나, LD 역할과 방향이 맞지 않으면 후보는
fail-closed한다. 따라서 Agent는 임의 `P` 주소를 제안할 수 없다.

이 binding은 **저장 가능한 offline LD 설계**만 허용한다. 외부 배선, field-device
identity, module health, independent safety hardware, controller identity와 runtime
state는 여전히 미관측이며 runtime 사용 전 별도 검증이 필요하다. force/write/download
및 PLC 운전 권한은 이 기능으로 생기지 않으며 계속 unsupported다. 내부 device는 CPU,
timer instruction은 CPU와 실제 timer area처럼 해당 분석의
`required_context`만 모두 `observed`일 때 후보가 될 수 있다.

변경 제안 전 `program.change_target_analysis`는 cache를 사용하지 않고 현재
프로젝트의 모든 LD 프로그램을 안정적으로 두 번씩 읽는다. 선택한 exact
`value_token_offset`을 program·record·visual row/column·network/rung
identity/digest, current/proposed semantics, project-wide reference, 상류·하류
causal path, multiple writer와 정적 물리 P 출력 후보에 결속하고 canonical
`analysis_digest`를 만든다. Platform은 여기서 다시 최소 Host receipt와 digest를
만들어 같은 conversation/run의 evidence event로 저장한다. Agent 계획은 이 exact
receipt를 target identity, value semantics, direct/indirect impact와 safety
coverage 모두에 인용해야 하며 승인·실행 시 서버가 필드 전체를 재대조한다.
MCP `program.plan_change`도 transaction snapshot에서 같은 분석을 다시 계산해
analysis/network digest가 달라지면 적용 전에 거부한다.

TON/TOF의 instruction, timer address와 literal preset은 읽을 수 있고 XGK/XGB의
timer area는 프로젝트마다 변경될 수 있다. 기존 프로젝트에서는 위
`project.hardware_context`가 관측한 활성 영역과 timer 주소를 결합해 duration을
계산하며 parameter가 없거나 겹치거나 주소가 범위를 벗어나면 후보를 막는다. 신규
프로젝트 생성 경로는 parameter를 건드리지 않은 exact build provenance 때문에
제한된 제조사 기본 영역을 사용한다.

중단 복구는 자동 재적용하지 않는다. apply 전이면 `ABORTED_NO_MUTATION`,
apply 이후이면 현재 payload가 exact original/planned/neither인지 확인해
no-op rollback, 한 번의 역변경 또는 `QUARANTINED`로 닫는다. 불명확한 상태에서
재적용하거나 성공으로 간주하지 않는다. 이 계약과 evidence gate가 구현됐더라도
승인된 실제 operand mutation의 end-to-end acceptance는 아직 수행하지 않았다.

### 6.3 LS FEnet 읽기 전용

`controller.ls.status`, `controller.ls.read`, `controller.ls.trace`는 XGT FEnet
TCP 2004의 상태, 1~16개 device와 최대 30초의 bounded 시계열을 읽는 별도
read-only 경로다. trace는 2~64개 샘플, 값 전이, 변수별 처음·마지막·최소·최대·
변화 횟수, 전후 CPU 상태와 provisional identity를 같은 receipt에 보존한다.
RFC1918 IPv4, CGNAT와 IPv6 ULA
숫자 주소만 허용하고 loopback, link-local, public address와 DNS hostname은
거부한다. 반환 identity는 provisional이며 write에 충분하지 않다.

이 read-only 경로는 실제 PLC identity나 project upload/복원을 만들지 않는다.
샘플은 순차 FEnet 요청이므로 동일 PLC scan의 동시 순서를 증명하지 않는다.
controller-native scan trace, alarm history 및 write/run edit/force/mode/download
명령은 제공하지 않는다.

#### 6.3.1 Controller restore surface discovery

`controller.ls.*` observations do not constitute a project upload. The missing
vendor-native path is XG5000 **Project > Open from PLC**, followed by **Save As**
to a new Host-owned staging file and an exact active-project path/hash readback.

`controlstudio-probe-controller-restore` is a separate developer command for
discovering that version-specific menu and dialog surface. `inspect` is
read-only. The two `open-*-dialog` actions open exactly one vendor dialog and
return its bounded control profile without confirming it; editable values are
not emitted. This command is not an MCP tool and is not visible to the Agent.

The product MCP catalog additionally exposes Host-only
`controller.restore_preflight`. It reads the exact selected PID/HWND process
lifetime, XG5000 executable version/hash, and current resolution of the Open from
PLC and Save As menu commands. Its digest-bound receipt explicitly records that
no command was invoked, no PLC was contacted, and no file or workspace was
created. The Agent cannot call this tool.

The product restore path is now a Host-owned preview rather than an Agent tool.
After exact preflight it invokes only the resolved **Open from PLC** menu and
hands the vendor connection/upload dialog to the user. Completion calls the
installed 32-bit `IApplication.SaveAsProject` on the same PID-bound ROT object,
requires a new XGWX file plus same-object absolute-path readback, hashes the
stable file, and captures `restored_from_controller` immutable source and
working-copy provenance. A retry with the same operation ID revalidates the
stored completion receipt instead of saving again. PLC write, force, mode,
download, and online edit remain outside this path. Real-controller acceptance
is still required before the preview can be marked verified.

The reusable post-capture comparison is implemented independently as
`workspace.compare`. It always compares exact immutable-source and working-copy
file hashes. For XGWX, it also builds both bounded structure indexes and returns
digest-only added, removed, and changed counts for configurations, tasks, code
units, symbols, devices, calls, parameters, modules, communications, and opaque
containers. Truncated or unsupported structure data is reported as partial; a
file hash mismatch is never promoted to identical because the visible structure
happened to match.

### 6.4 기존 controlled-launch/COM-save의 identity 계약

아래 보증과 미인수 표현은 이 별도 경로의 범위다. 현재 native owner에 연결한
LD 편집·저장·재개 타당성과 혼동하지 않는다. 선정 경로는 Host의 작업본·IDE binding과
native 명령 결과를 사용하며, COM/ROT 확보가 LD 편집의 선행 조건이 아니다.

기본 보증은 XG5000 0개 상태에서 ControlStudio가 새로 실행한 정확한 PID/HWND,
process creation time, native Open, exact title과 working-copy path/hash를 같은 MCP
수명 안에서 묶은 `controlled_launch_read_only`이다. 이 보증은 Read·Analyze에만
유효하며 mutation, save, simulation과 online capability를 열지 않는다.

Bound offline save는 toolbar 좌표 클릭을 사용하지 않는다. 설치된 32-bit
`IApplication.SaveProject`를 exact PID/HWND와 active absolute project path가 일치한
ROT 객체에서만 호출하고, vendor result code `0`, 호출 뒤 같은 PID/HWND/path,
stable disk hash와 independent reopen readback을 모두 요구한다. 어느 하나라도
불일치하면 저장 성공으로 승격하지 않는다.

이 strong 경로는 코드와 x86 정적 컴파일까지만 확인됐다. 지원 XG5000 버전과 실제
normal session에서의 acceptance evidence가 아직 없으므로 production 지원 완료로
표시하지 않으며, 증거를 얻지 못한 설치는 계속 `controlled_launch_read_only`로 남는다.

## 7. 확인된 한계

- 실제 PLC download/write/run edit/force/mode 변경은 현재 구현·활성화돼 있지
  않으며 현장 검증도 수행하지 않았다.
- XG5000 버전·언어·DPI에 따라 UI 구조가 달라질 수 있다.
- V4.81과 V4.82.1의 UI 구조 차이는 런타임 profile과 readback으로 구분하며,
  문서의 버전 목록을 필수 최소 버전으로 간주하지 않는다.
- 일부 대화상자는 64비트 Python에서 텍스트가 깨지므로 컨트롤 ID fallback이 필요하다.
- LD 전용 형식은 공식 공개 사양이 아니다. 미해석 자료는 보존하고, 해당 자료의 해석을
  필요로 하는 미지원 변경과 이미 지원하는 다른 요소의 변경을 구분한다.
- 프로그램의 기계적 의미는 I/O 주석이나 전장 도면이 없으면 주소 기준까지만 확정 가능하다.
