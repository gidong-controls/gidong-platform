# XG5000 제어 및 LD 분석 기술 명세

대상 환경: Windows 11, XG5000 V4.81 및 V4.82.1, XG-SIM

이 문서는 첫 provider adapter의 기술 명세다. 초기 LS 제품에서 XG5000은
래더와 벤더 프로젝트의 시각적 원본이며, ControlStudio 웹 GUI는 이를 범용
래더 IDE로 복제하지 않는다. ControlStudio는 분석 근거와 프로그램·네트워크
위치를 구조화해 표시하고 XG5000의 해당 위치를 연다.

화면에 원하는 래더가 보인다는 사실은 변경 성공 증거가 아니다. 성공 판정은
native payload·artifact hash, 구조 diff, 프로그램 검사, 저장·독립 재열기,
XG-SIM 또는 controller readback처럼 adapter가 반환한 독립 증거를 사용한다.

## 1. 결론

XG5000은 공개된 범용 자동화 API만으로 모든 편집 기능을 제공하지 않는다.
현재 adapter 제어는 다음 세 계층을 조합한다.

1. Win32 창·메뉴·툴바·대화상자 제어
2. XG5000 전용 클립보드 형식
   - IL: `XG5000MNE`
   - LD: `XG5000LADDER`
3. XG-SIM 런타임 메모리: x86 `XimUtil.DeviceInterface`

절대 화면좌표는 최후 수단으로만 사용한다. 기본 식별자는 PID/HWND, 창 클래스,
컨트롤 ID, MDI 부모 관계이며 좌표를 사용할 때도 확인된 자식 컨트롤 내부의
상대 위치만 사용한다.

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

구현은 비공개 `gidong-plc-mcp` 저장소의
`src/xg5000_control/ladder.py`에 있다.
해석하지 않은 배선 토큰도 각 행의 `raw_tokens`에 보존한다.

### 현재 해석 범위

현재 디코더는 프로그램명, 시각 행, 접점, 비교식, 출력 명령과 좌표를 복원한다.
각 record는 opcode 시작 offset과 별도로 실제 operand가 있는
`value_token_offset`을 제공한다. 논리 그래프는 관찰된 행 순서와 좌표가 지지하는
직렬 조건·일부 병렬 가지, network/rung identity와 canonical digest를 만들고
원본 source span으로 되돌아갈 수 있게 한다. 해석하지 못한 token, branch 또는
application instruction이 있으면 network/rung을 `partial`로 표시하며 모든 런을
완전한 불리언 식으로 변환했다고 주장하지 않는다.

## 3. XG5000MNE

`XG5000MNE`는 제한된 빈 프로그램 작성과 developer-certified 기존 프로그램 전체
교체 경로에 사용한다. CPU나 프로젝트 언어 설정에 따라 IL/MNE를 사용할 수 없으므로
일반 프로젝트 분석의 기본 경로로 사용하지 않는다. 일반 분석의 기준은 LD다.

안전 규칙:

- 빈 프로그램 삽입, 기존 프로그램 전체 교체, 한 action 기준 단일 명령 패치를
  서로 다른 승인 계약으로 분리
- 단일 명령 패치는 검토 evidence의 유일한 `OUT`/`SET`/`RST`/`TON` action만
  anchor로 허용하고 private baseline에서 한 행 insert/replace/delete를 컴파일
- 전체 교체는 typed 13-command IR, 최대 4,096개 instruction과 마지막 단일 `END` 검사
- 모델·공개 API에 raw MNE/LD 또는 private artifact 경로를 노출하지 않음
- exact PID/HWND·active program/editor/view·project path/hash·DPI·runtime identity 결속
- scratch-project에서 서명된 capability certificate와 사람의 일회성 plan 승인 요구
- 적용 전 raw MNE/LD baseline과 project backup을 MCP journal-private artifact로 보존
- 적용 후 MNE/LD exact readback, Program Check, 저장 hash, 별도 PID 재열기 검증
- 실패 시 durable baseline 또는 승인 target checkpoint일 때만 exact rollback하고,
  나머지는 `QUARANTINED`로 보존

전체 프로그램 교체와 일반 구조 패치는 오프라인 working copy만 수정한다. PLC write,
online edit 및 controller 검증은 포함하지 않으며 실제 XG5000 production acceptance는
아직 별도로 필요하다. 예외적으로 `mne_rung_patch` V6는 사용자가 요청 전에
`offline_native_reopen_xgsim`을 선택하고 compiler가 단일-program·단일-writer P/M
출력 profile을 만들 수 있을 때만 아래 XG-SIM 수명주기로 이어진다. 이 경로도 PLC에는
연결하거나 쓰지 않는다.

## 4. 프로그램 검사와 저장

- XG5000의 공식 `프로그램 검사` 명령 사용
- 성공 문구와 오류 개수 0을 모두 확인
- MDI 편집기 닫기로 프로젝트 모델에 변경 반영
- 저장 전후 프로젝트 SHA-256 기록
- 별도 XG5000 프로세스에서 재열기 후 readback

경고는 오류와 구분해 기록한다. 경고가 있으면 통과 여부와 별개로 개수와 메시지를
증거에 남긴다.

## 5. XG-SIM

프로그램 다운로드와 런타임 메모리 쓰기는 다른 작업이다.

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

## 6. 제한된 제품 작업 경로

현재 production MCP manifest는 52개 tool을 노출한다. 아래 read/analyze 경로가
추가됐다는 사실은 실제 PLC write·run edit·force·mode·download가 활성화됐다는
뜻이 아니다.

### 6.1 네이티브 신규 프로젝트

`project.create_native`는 Host가 선택한 exact XG5000 PID/HWND와 기존의 빈
workspace directory를 대상으로 네이티브 `프로젝트 > 새 프로젝트` dialog를
연다. 절대 화면 좌표가 아니라 dialog label, control identity와 결과
project title/path/hash를 검증한다. project name, PLC series, CPU model,
첫 program name/language와 description, typed I/O·mode·STEP sequence·TON timer·
latched alarm·recovery·control network·process interlock은 검토 완료된 Engineering
Plan 전체를 담은 `engineered_control_program` build bundle에 고정된다. 검토 전
native design은 `agent/proposed`일 뿐이다. Host가 collection의 전체 개수·ID와
I/O mapping/state, mode·network 관계, timer 시간, sequence 재기동·STEP, alarm,
recovery, safety의 모든 실행 값을 semantic item으로 재계산하고, 사용자가 각
항목을 확인한 v2 review만 build 권한이 된다. 이 review는 plan, 최신 project
brief와 첨부 source revision digest에 함께 결속된다. Host와 MCP가 item universe와
같은 XG5000 mnemonic을 각각 독립 재계산하며 Host authorization은 exact bundle ID,
reviewed plan authority, PID/HWND와 모든 dialog 값에 일회성으로 묶인다.

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

이 경로는 visible modal, elevation 불일치, ambiguous control과 결과 identity
불일치를 첫 UI mutation 전후의 해당 단계에서 거부한다. 생성 성공은 exact native
project path/title/hash와 dialog field readback, exact named blank program, exact mnemonic
readback, LD 왕복, XG5000 Program Check와 저장 hash가 모두 일치하고
`APPLIED_VERIFIED` build receipt가 반환될 때만 확정한다. 현재 적용 범위는
XGK/XGB의 `LOAD`, `LOAD NOT`, `AND`, `AND NOT`, `OUT`, `SET`, `RST`, `TON`과
signed 16-bit 상수를 `Dxxxxx`에 쓰는 typed `MOV`로 구성한 순차 제어, timeout
alarm, operator recovery와 출력 interlock이다. 이
작업은 PLC에 연결하거나 쓰지 않는다. Host는 생성 파일을 불변 source와 별도
working copy로 캡처한 뒤 생성 PID와 다른 새 XG5000 프로세스에서 working copy를
연다. `project.verify_native_reopen`은 exact named program을 Project Tree Search로
열고 mnemonic 완전 일치, LD 왕복, Program Check 0 error와 검증 전후 disk hash
불변을 확인해 `REOPEN_VERIFIED` receipt를 반환한다. 이 전체 경로는 실제
XG5000 V4.81/XGK-CPUE에서 확인됐다. 또한 XG5000 V4.82.1과 XGB
`XBC-DN/DP64H`의 단일 control network build에서 실제 XG-SIM 5개 시나리오,
공식 program write, vendor STOP 상태 3회 연속 관측, CPU STOP 뒤 입력 baseline
원복을 v3 `SIMULATION_VERIFIED` receipt로 확인하고 digest-bound trial을
`ACCEPTED`로 결속했다. 현재 v4 simulation 계약은 reviewed ladder IR의 모든
control network/output과 ordered `networkRungs`를 Host와 MCP가 독립 재계산하고,
각 network의 정상·deny·restore 및 적용 가능한 mode·sequence·alarm·recovery·
process interlock 시나리오를 하나의 RUN/STOP·baseline 복원 수명주기에서 모두
확인한다. 전체 network profile과 보호 출력 byte를 simulator 동작 전에 검증하며
부분 network receipt는 승인하지 않는다. 기존-project V6 rung patch는 승인된 target,
독립 재열기, compiler-derived 정상/deny/restore P/M profile과 STOP·입력 원복을 같은
MNE journal에 결속하고 `SIM_STOPPED`에서 사람의 Keep 또는 exact Revert 결정을 기다린다.
다만 대표 복수 control network build와 이 제한된 기존-project 경로의 실제 XG-SIM
수락, 실제 PLC 연결·쓰기는 아직 이 증거 범위에 포함되지 않는다.

### 6.2 오프라인 한 operand transaction

제품 Host는 동일 길이 LD operand 한 개에 한해 exact target·plan digest·
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
결속된다. 다른 CPU family나 범위 밖 slot에는 이 규칙을 적용하지 않는다. exact
target이나 causal path가 물리 `P` 입력·출력에 닿으면 이 slot 귀속만으로 terminal
point와 외부 배선을 증명할 수 없으므로 변경 후보는 계속 fail-closed한다. 내부 device는
CPU, timer instruction은 CPU와 실제 timer area처럼 해당 분석의
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

### 6.4 XG5000 project identity 보증

기본 보증은 XG5000 0개 상태에서 ControlStudio가 새로 실행한 정확한 PID/HWND,
process creation time, native Open, exact title과 working-copy path/hash를 같은 MCP
수명 안에서 묶은 `controlled_launch_read_only`이다. 이 보증은 Read·Analyze에만
유효하며 mutation, save, simulation과 online capability를 열지 않는다.

추가된 x86 STA identity helper는 임의 COM 명령을 받지 않는다. 하나의 ROT COM
객체에서 `GetProjectTreeWnd`와 `GetProjectFileName`을 안정적으로 두 번 읽고,
project-tree HWND의 owner PID와 main HWND 계층, process creation time, exact
working-copy path/hash와 title을 MCP가 대조한다. 이 직접 증거가 모두 일치할 때만
`project_identity_verified_against_selected_pid=true`와 `strong_readback`을 만든다.
XG5000 인스턴스가 하나라는 사실이나 사용자가 준 path/hash만으로 strong identity를
추론하지 않는다. 일반 mutation과 MNE 실행 직전에도 같은 증거를 다시 읽는다.

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
- LD 전용 형식은 공식 공개 사양이 아니므로 관찰되지 않은 요소는 보수적으로 거부해야 한다.
- 프로그램의 기계적 의미는 I/O 주석이나 전장 도면이 없으면 주소 기준까지만 확정 가능하다.
