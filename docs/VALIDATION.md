# LS XG5000 adapter 검증 기록

최종 갱신: 2026-07-30

## 1. 시험 프로젝트 XG-SIM E2E

환경: Windows 11, XG5000 V4.81 및 V4.82.1, XG-SIM

시험 프로그램:

```text
LOAD M00000
OUT M00001
END
```

결과:

| 항목 | 결과 |
|---|---|
| XG5000MNE 삽입/readback | 성공 |
| IL→LD 전환 | 성공 |
| 프로그램 검사 | 오류 0 |
| 저장 후 별도 프로세스 재열기·예상 IL readback | 성공 |
| XG-SIM 공식 쓰기 | 성공 문구 확인 |
| RUN/STOP | 성공 |
| M0 입력→M1 출력 추종 | 3회 성공 |
| 원래 메모리 복원 | 성공 |

기존 근거는 로컬 `artifacts/product-e2e-final`에 남아 있다. 디렉터리 이름과
달리 검증 대상은 ControlStudio 전체 제품이 아니라 LS XG5000/XG-SIM
adapter다. 현재 하네스의 기본 출력은 `artifacts/ls-adapter-e2e`이며
`artifacts/`는 Git에서 제외한다.

이 재열기 증거는 새 프로세스에서 예상 IL을 다시 읽었다는 기술 증거다. 현재
MCP가 선택 PID에 실제로 열린 project path를 독립적으로 확인한 증거는 아니므로
서버 단계도 `SAVED_REOPENED_UNVERIFIED`로 표시하며 제품 identity gate로
사용하지 않는다.

2026-07-19의 별도 E2E 기록은 XG5000 `4.81.0.0`에서 같은 시험 프로그램의
작성·저장·재개방·공식 XG-SIM 쓰기·RUN·3회 입출력 추종·복원을 모두 통과했다.
따라서 V4.82.1 업그레이드는 필수 조건으로 취급하지 않는다.

## 2. 익명화된 실제 프로젝트 LD 읽기 — 기존 adapter 검증

아래 SHA와 검사 결과는 기존 LS adapter 검증 시점의 기록이다. 현재 파일을
사용한 제품 전체 read-only E2E의 정체성·SHA·receipt는 4절의
2026-07-30 기록을 기준으로 한다.

원본:

`<redacted historical customer project>`

검증 결과:

| 항목 | 결과 |
|---|---|
| LD 프로그램 | `NewProgram`, `초기동작`, `알람`, `출력` |
| 클립보드 형식 | `XG5000LADDER` |
| 복원된 시각 행 | 344 |
| NO/NC/에지 접점 | 복원 성공 |
| OUT/SET/RST/응용명령 | 복원 성공 |
| 전체 프로그램 검사 | 오류 0, 경고 36 |
| 프로그램 크기 | 1,159 / 15,360 STEP (7.54%) |
| 읽기 전후 SHA-256 | 동일 |

원본 SHA-256:

`8f9d640ae4b7b5dbae349e083c8d87abc27e9151fa90bea80659ef63b97a71ca`

## 3. 소프트웨어 검증

- Ruff: 통과
- LD 디코더 단위 테스트: 통과
- LS MCP adapter: 266 passed, 10 skipped, 14 subtests와 Ruff 통과
- Windows Launcher: 78 tests와 Ruff 통과. elevated one-click 실제 smoke 통과
- Python/FastAPI Host와 agent/MCP 통합: 184 passed와 Ruff 통과. Codex의
  비대화형 Windows 로그온 세션에서 Credential Manager round-trip 1건은
  Win32 `ERROR_NO_SUCH_LOGON_SESSION`으로 제외했으며 실제 사용자 세션 Host는
  저장된 모델 자격 증명을 읽어 `agent=ready`를 확인했다.
- React/TypeScript Web/PWA: 74 tests 통과
- Web typecheck와 production build: 통과
- Web dependency audit: 취약점 0
- 전체 테스트는 쓰기 가능한 `--basetemp`를 지정해야 한다.
  기본 Windows 임시 폴더의 기존 권한 문제는 제품 코드 실패가 아니다.

현재 revision의 MCP 공개 surface는 38개이고 모델용 읽기·분석 최대
allowlist는 12개다. target이 없는 도구는 동적으로 숨기며, 아래 37/11 표기는
해당 실기 실행 당시의 역사 수치다.

## 4. 제품 read-only vertical slice

2026-07-28에 문서로만 고정돼 있던 첫 제품 경로를 실제 구현하고 다음 경로를
검증했다.

```text
Windows Launcher
→ ControlStudio Host
→ React Web/PWA
→ Pydantic AI runtime
→ MCP supervisor
→ LS XG5000 read-only adapter
```

| 항목 | 결과 |
|---|---|
| Launcher→Host readiness→MCP 시작 | medium launcher→UAC→high launcher·Host→MCP readiness→HMAC challenge 실제 smoke 통과 |
| elevated XG5000 권한 일치 | XG5000 high integrity 감지, UAC handoff 후 Host high integrity 기동 확인 |
| Launcher 두 번째 실행 | 실행 중인 검증된 Host 재사용, exit 0 |
| Launcher 실패 시 Host·MCP 프로세스 트리 정리 | Windows venv redirector 경로 30회 반복, 잔존 프로세스 0 |
| Launcher 성공 후 인증 종료 | Host·MCP와 `8765` listener 종료 확인 |
| SSE 연결 중 인증 종료 | 두 SSE 경로 종료 후 listener·Host process tree 잔존 0 |
| Host가 발견한 MCP 도구 | 당시 `tools/list` 37개 |
| agent에 노출한 도구 | 당시 11개 읽기 전용 도구만 허용 |
| agent MCP 대상 identity | strong readback 또는 cold-start `controlled_launch_read_only`가 성립할 때만 Host가 5개 identity 필드를 강제 주입. 후자는 read/analyze 전용 |
| 브라우저 launch handoff | master launch secret을 URL/process argv에 노출하지 않고 30초 one-time ticket만 교환. 정상 교환, replay·expiry 거부와 동시 단일 사용 테스트 통과 |
| conversation/run/event 기본 저장 | SQLite 저장·재열기 테스트 통과 |
| SSE replay·재연결 | 테스트 통과 |
| desktop Edge render | 성공 |
| mobile viewport Edge render | 성공 |
| React typecheck·production build | 성공 |
| 서비스 워커 | 생성·등록하지 않으며 Host도 차단 |
| 신규 프로젝트·온라인 PLC·mutation | 당시 read-only vertical slice에서는 비활성. 아래 최신 검증 기록으로 일부 경로 추가 |

이 결과는 제품 실행 골격과 최소권한 MCP 경계를 검증한 것이다. 이후 실제
모델과 XG5000을 사용한 agent tool-call 경로는 아래 2026-07-30 기록으로
별도 검증했다. 2026-07-29에는 elevated XG5000 환경의 one-click 시작과
기존 Host 재사용도 위 표와 같이 추가 검증했다.

### 현재 revision의 cold-start 로컬 프로젝트 E2E

2026-07-30에 XG5000 프로세스와 ControlStudio Host가 모두 없는 상태에서
실제 XG5000 V4.82.1과 익명화된 historical working copy를 사용해 다음 제품 경로를
처음부터 다시 검증했다. 이전 PID 15872/3768 기록은 strict identity gate
적용 전 참고 기록이며 현재 revision의 완료 증거로 사용하지 않는다.

```text
ControlStudio Launcher
→ Host/MCP 준비
→ POST /api/v1/workspaces/open-local
→ immutable source + hash-pinned working copy
→ 빈 XG5000을 정확한 새 PID로 실행
→ native Open dialog로 working copy 열기
→ exact PID/HWND/title/workspace hash/process lifetime 결합
→ read/analyze engineering session
```

| 항목 | 실제 결과 |
|---|---|
| 원본 SHA-256 전/후 | `bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f` 동일 |
| workspace / revision | `e6e6b5f4-4674-493d-87be-16bee9397f50` / `6cbea9cd-2c97-46a9-8a83-c74c20da6be4` |
| hash-pinned working copy | 원본과 해시 일치, 원본 미변경 |
| XG5000 cold start | PID 16984, HWND 2231910 |
| main-window 판별 | 시작 중 나타나는 1x1 `GDI+ Hook Window`를 제외하고 실제 engineering shell 선택 |
| XG5000 title readback | 익명화된 expected project title exact match |
| engineering session | `9d6fba4e-74da-456c-bfd5-00568be64cfa`, `PROJECT_OPEN` |
| identity assurance | `controlled_launch_read_only` |
| Host 상태 | `engineeringSystem.state=ready`, `readReady=true`, `sessionVerified=false` |
| 당시 MCP 전체 / agent 도구 | 37 / 11 |
| 저장·mutation·PLC 접근 | 수행하지 않음 |

이 설치본의 XG5000 COM 서버는 `highestAvailable` manifest 때문에 medium-integrity
Host에서 COM activation/ROT readback이 불가능하다. 따라서 위 보증은 ControlStudio가
0개 인스턴스에서 시작한 정확한 PID/HWND, native Open 완료, exact title, workspace
경로·해시와 process creation time을 같은 MCP 수명 안에서 묶은 **읽기·분석 전용**
보증이다. `project_identity_verified_against_selected_pid`는 여전히 `false`이고,
offline mutation 시작, 저장, 시뮬레이션, online modify의 신원 증거로는 인정하지
않는다. 해당 기능을 제품 권한으로 열려면 제한된 elevated x86 broker의 COM
PID/HWND/path/hash readback을 별도로 구현·검증해야 한다.

### 현재 revision의 모델→Pydantic AI→MCP→XG5000 E2E

위 세션에서 사용자 설정 OpenAI-compatible 모델 `nemotron-3-super`,
Pydantic AI `2.19`, 실제 MCP stdio subprocess를 사용해 다음 읽기 전용
제품 경로를 검증했다. 모델 endpoint와 credential은 문서·artifact·저장소에
기록하지 않는다.

```text
Host conversation/run
→ OpenAI-compatible model
→ Pydantic AI Engineering Agent
→ MCP project.list_programs
→ PID 16984의 실제 XG5000
→ engineering session 아래 MCP receipt 저장
→ receipt의 프로그램 4개와 일치하는 최종 응답
```

| 항목 | 실제 결과 |
|---|---|
| conversation | `6fd234f6-6acb-4fbf-b9dd-4df7db61b267` |
| run | `1c83d8ee-b2f9-47e8-98d1-0e41ae2da93b` |
| 실제 MCP 도구 | `project.list_programs` |
| MCP operation | `a6867a3a-c079-4c18-9b28-3c1d6c9413fa` |
| MCP receipt | `artifacts/mcp-sessions/9d6fba4e-74da-456c-bfd5-00568be64cfa/20260730-002231-141378-project.list-programs/result.json` |
| receipt 판정 | `verified=true`, `project_unchanged=true`, `program_count=4` |
| 실제 LD 프로그램 | `NewProgram`, `알람`, `초기동작`, `출력` |
| agent 최종 응답 | receipt의 프로그램 4개와 정확히 일치 |
| 저장·mutation·시뮬레이션·PLC 접근 | 수행하지 않음 |

이는 과거 assistant 문장만으로 주장한 결과가 아니다. 이 실행에서 새로 생성된
MCP `result.json`이 같은 engineering session 아래 저장됐고 Pydantic AI 최종
응답의 프로그램 목록이 receipt와 일치했다. 검증 범위는 현재 revision의
cold-start 로컬 XG5000
`Read`·`Analyze` 경로이며 신규 생성, controller read, online observe,
offline mutation 승인·적용 또는 실제 PLC 쓰기 증거가 아니다.

### Host 승인 오프라인 변경 transaction

2026-07-29에 동일 길이 LD operand 한 개를 대상으로 다음 제품 계약을 구현하고
자동화된 Host/MCP 통합·중단 복구 테스트로 검증했다.

```text
Host change proposal
→ exact target·plan digest·만료 approval
→ MCP snapshot·backup·plan checkpoint
→ single-use HMAC authorization
→ apply·native readback
→ structural diff·XG5000 program check·save
→ commit 또는 explicit reconcile
```

검증된 안전 성질:

- `operation_id`, workspace/revision, source와 working-copy hash, XG5000
  PID/HWND, exact operand 위치·현재값·대체값과 plan digest가 묶임
- 승인 만료·재사용·target 전환·예상값 불일치·다른 PID/HWND·다른 경로·
  다른 hash를 실행 전에 거부
- snapshot 응답 유실 시 Host transaction ID로 같은 journal 재발견
- pre-apply crash는 XG5000 UI/save/PLC 동작 없이
  `ABORTED_NO_MUTATION`
- post-apply crash는 자동 재적용하지 않고 current payload가 exact original,
  exact planned, neither인지 구분해 no-op rollback, 역변경 또는
  `QUARANTINED`
- recovery lease 소유권 전환을 write-ahead record로 먼저 저장하고 journal
  갱신 사이 hard crash를 다음 recovery에서 repair
- live lease owner와 recovery claim 경쟁을 거부
- Host 전역 XG5000 target-operation lease가 workspace open, attach,
  native create, 오프라인 change와 reconcile을 직렬화
- 이전 Host의 stale lease는 자동 해제하지 않고 같은 `operation_id`와 exact
  target을 다시 증명한 recovery만 소유권을 이어받음
- Host 재시작 뒤 `QUARANTINED` 항목은 사용자가 exact PID/HWND를 다시 선택한
  명시적 reconcile만 허용

현재 verification scope는 `offline_native_check_only`다. 구조 diff,
XG5000 프로그램 검사와 save까지 실행하며 외부 프로세스 재열기, XG-SIM,
runtime 또는 실제 PLC readback은 `not_executed` follow-up으로 남긴다.
2026-07-28의 독립 한 operand 실편집·검사·원복 실기 증거는 아래 8절에 있지만,
새 Host 승인 transaction 전체를 현재 실제 XG5000에서 다시 실행한 증거는
아니다.

### LS 네이티브 신규 프로젝트 생성 경로

2026-07-29에 Host API→MCP `project.create_native`→XG5000 네이티브
`프로젝트 > 새 프로젝트` dialog 경로를 구현했다.

자동화된 fake backend에서 검증한 항목:

- caller가 임의 대상을 추정하지 않고 exact XG5000 PID/HWND를 요구
- project name, 빈 destination, PLC series, CPU model, 첫 program/language,
  description과 plan digest를 Host 승인에 결합
- screen absolute coordinate가 아니라 네이티브 dialog label/control identity
  사용
- one-time HMAC authorization과 idempotent Host operation
- 생성된 `.xgwx` path/hash, XG5000 title/project identity 확인
- 결과의 `controllerConnected=false`, `plcWritePerformed=false`
- existing/non-empty destination, modal, ambiguous control, identity/elevation
  불일치의 fail-closed

현재 실제 PID/HWND에 대한 시도는 기존 visible modal과 elevation 불일치 때문에
첫 UI mutation 전에 안전하게 거부됐다. 따라서 **실제 XG5000 신규 프로젝트
생성 성공은 미검증**이다. 또한 이 좁은 경로는 요구사항 기반 I/O·태스크·심볼·
일반 LD 생성이나 XG-SIM scenario를 구현한 것이 아니다.

응답 유실 뒤 native create가 `QUARANTINED`인 자동화 시험에서는 사용자 요청의
exact PID/HWND와 예상 project path를 다시 제출한 read-only reconcile만 현재
artifact identity를 판정했다. 일치하면 완료와 lease 해제를, 불일치하면
`QUARANTINED`와 `STALE_BLOCKED` lease 유지를 확인했다. 생성 호출은 자동
재적용하지 않는다.

### LS FEnet 읽기 전용 경로

2026-07-29에 Host/MCP/Web에 다음 읽기 전용 경로를 구현했다.

- LS XGT FEnet TCP **2004** 상태 조회
- 한 요청에서 같은 data type의 1~16개 device 단발 읽기
- RFC1918 IPv4, CGNAT와 IPv6 ULA의 숫자 주소만 허용
- loopback, link-local, public address와 DNS hostname 거부
- 짧은 observation freshness와 오류·disconnect 시 즉시 invalidation
- provisional identity의 `strongEnoughForWrite=false`

합성/fake TCP server에서 정상 frame, timeout, malformed response, endpoint
정책과 Host 응답 검증을 시험했다. **실제 PLC에는 연결하지 않았다.** 이 경로는
PLC에서 프로그램·파라미터를 읽어 workspace로 복원하는 upload 기능이 아니며
serial, CPU/firmware와 online project digest를 강하게 증명하지 않는다. write,
run edit, force, mode change, download 명령은 제공하지 않는다.

## 5. 검증되지 않은 범위

- 새 Host 승인 transaction의 실제 XG5000 end-to-end와 외부 재열기·XG-SIM
- 장기 설비 추세·유지보수 이력
- 원격 HTTPS·인증·원격 역할별 권한 분리
- 신규 프로젝트의 실제 XG5000 생성 성공과 하드웨어·I/O·태스크·심볼 구성
- 실제 PLC FEnet 연결과 strong identity, PLC에서 프로젝트·파라미터 읽기
- 로컬 프로젝트와 PLC 비교 및 온라인 관찰
- 실제 PLC 쓰기·런중 수정·직접 제어
- 벤더 중립 core와 Mitsubishi/Rockwell adapter
- 모든 XG5000 버전과 CPU 조합
- 모든 LD 기호의 완전한 분기 그래프
- 일반 LD 런·접점·분기 생성·삭제
- 현장 I/O의 기계적 명칭

따라서 현재 확정적으로 말할 수 있는 범위는 XG5000 프로젝트의 LD 읽기·구조
분석, 격리 복사본의 제한된 오프라인 실편집·검사·XG-SIM 기술 증거,
Host→Pydantic AI→read-only MCP→XG5000 agent vertical slice와 새
한 operand transaction의 자동 검증된 안전 계약이다. Launcher의 elevated
one-click 런타임 시작도 실제 smoke를 통과했다. 신규 생성과 FEnet 화면이
구현됐더라도 live XG5000/PLC 검증이나 전체 PRD 완료를 뜻하지 않는다.
`xg5000.attach`는 XG5000 IDE attachment이며 실제 PLC 연결 증거가 아니다.
SQLite conversation/run/event와 change transaction 저장은 구현됐지만 장기
유지보수 이력 완료를 의미하지 않는다. PLC project restore, online write/run
edit/force/mode/download는 계속 비활성이고 해당 MCP 명령도 없다.

## 6. 최소권한 capability E2E

2026-07-28에 실제 XG5000 V4.81 대동 프로젝트 복사본으로 `analysis` 프로필을
검증했다.

| 항목 | 결과 |
|---|---|
| 부여 capability | `read`, `analyze` |
| 전체 LD 프로그램 | 4개 |
| 전체 visual row | 344 |
| 구조 그래프 | 4개 모두 `structural` |
| `project.backup` 시도 | `offline_mutate` 미부여로 실행 전 거부 |
| 거부 감사 이벤트 | 기록됨 |
| 실제 PLC capability | `online_observe`, `online_modify`, `direct_control` 모두 부여 불가 |
| 읽기 전후 프로젝트 SHA-256 | 동일 |

정상 stdio 서버는 호출자가 `offline_engineering` 또는
`simulation_operator`를 요청해도 attach 단계에서 거부한다. 기존 실제 변경과
XG-SIM 증거는 명시적인 unsafe 로컬 검증 하네스에서 얻은 기술 증거이며 제품
권한 승인 증거가 아니다.

근거:

`artifacts/ld-read-e2e/result-capability-final.json`

## 7. 내부 MCP 개발 콘솔 E2E 이력

2026-07-28에 Tkinter 개발 콘솔을 실제 JSONL MCP stdio 서버와 XG5000 V4.81
대동 프로젝트 복사본에 연결했다. 이 결과는 transport·callback·렌더링 진단
증거이며 ControlStudio 제품 GUI 또는 사용자 workflow 완료 증거가 아니다.

| 항목 | 결과 |
|---|---|
| 콘솔 backend | 별도 MCP stdio subprocess |
| 권한 | `analysis`: `read`, `analyze` |
| 프로그램 목록 | 실제 LD 4개 |
| 선택 프로그램 | `NewProgram`, 183 visual row |
| 콘솔 버튼 경로 | IDE attach → 프로그램 읽기 → LD 원본 → 논리 그래프 |
| 콘솔에서 실행한 분석 | `program.read_ld`, `program.logic_graph` |
| 그래프 상태 | `structural` |
| 화면 렌더링·스크린샷 | 성공 |
| 프로젝트 SHA-256 | 전후 동일 |

E2E는 MCP를 선호출해 결과를 화면에 주입하지 않는다. 실제 Tk 버튼 callback,
background worker, 별도 MCP stdio subprocess, 결과 렌더링 경로를 순서대로
통과한다.

이후 콘솔을 read-only 분석·증거 화면으로 축소하면서 이전 UI screenshot과
result는 혼동을 막기 위해 로컬 artifact에서 제거했다. 현재 콘솔 증거는
private PLC MCP repository의 `scripts/run_dev_console_e2e.py`로 다시 생성해야 하며 LS adapter 자체의 합격
조건과는 분리한다.

## 8. 독립 LD 원복 E2E

2026-07-28에 실제 XG5000 V4.81 대동 프로젝트 복사본에서 별도 MCP stdio
프로세스로 다음 전체 경로를 실행했다.

```text
snapshot → backup → M0320A→M0320B 적용 → 구조 diff
→ 전체 프로그램 검사 → project.rollback_change → 재추출
```

| 항목 | 결과 |
|---|---|
| 적용 diff | 계획한 한 토큰만 일치 |
| XG5000 전체 프로그램 검사 | 오류 0 |
| 원복 논리 diff | 0, `M0320A` 복구 |
| 비휘발 native payload | 원본과 일치 |
| XG5000 선택좌표 헤더 | offset 54·58만 `22→10`, 증거에 명시 |
| 프로젝트 SHA-256 | 전후 동일 |
| 최종 상태 | `rolled_back` |

선택좌표 헤더는 편집기 paste 뒤 XG5000이 갱신하는 중복 4바이트 필드다.
MCP는 검증된 중복 필드만 canonicalize하며, 다른 header·row stream·padding
차이는 원복 성공으로 인정하지 않는다.

근거:

`artifacts/ld-change-e2e/result-standalone-rollback.json`

## 9. 신규 프로젝트 계획 전용 Agent 실모델 검증

2026-07-30에 저장된 OpenAI-compatible 모델 연결을 사용해
`new_project_planning` workflow를 실제 Pydantic AI runtime으로 실행했다.
테스트에는 합성 포장 셀 요구사항만 사용했고 MCP 자리는 호출할 수 없는 객체를
주입했다.

| 항목 | 결과 |
|---|---|
| workflow | `new_project_planning` |
| MCP/tool surface | 없음, 호출 없이 완료 |
| typed output | 성공 |
| 사용자 요구사항과 설계 제안 분리 | 성공 |
| validation plan | 포함 |
| 최종 계획 상태 | `needs_clarification` |
| 프로젝트·XG5000·PLC 변경 | 없음 |

Host API와 SQLite 시험에서는 workflow를 대화 생성 시 서버가 고정하고, 다른
workflow 메시지를 `409`로 거부하며, structured brief 수정과 user message/run을
같은 transaction에서 저장하는 것을 검증했다. 계획 대화는 MCP가 준비되지 않아도
모델 capability가 검증되면 실행되지만, engineering 대화는 계속 MCP와 읽기
가능한 target을 요구한다.

이 결과는 계획 전용 모델 경로의 증거다. 네이티브 XG5000 프로젝트 생성,
일반 LD 구현, program check, 저장·재열기, XG-SIM 또는 실제 PLC 적용이
완료됐다는 뜻은 아니다.

## 10. 단일 창 Launcher와 제품 Host 실기동 검증

2026-07-30에 기존 구버전 ControlStudio 창과 Host를 정상 종료한 뒤 새
Launcher를 한 번만 실행했다.

| 항목 | 결과 |
|---|---|
| 가시 ControlStudio Chromium 앱 창 | 정확히 1개 |
| Launcher HWND 소유 표식 | 정확히 1개 |
| Host | `ready`, 단일 process tree |
| MCP | `ready`, 당시 공개 도구 37개 |
| 추가 PWA·브라우저 탭 | 생성하지 않음 |

이 live 기록은 새 Host의 첫 Launcher 실행에서 창 하나가 열린 증거다. 이후
추가된 현재 정책은 72개 Launcher 자동 시험에서 다음을 검증했다.

- 검증된 Host와 Launcher 소유 창이 하나면 새 handoff·창 없이 기존 창 전면화
- 새 Host는 남은 검증된 과거 창을 정리한 뒤 새 인증 창 하나 생성
- 다중 검증 창은 하나로 복구
- 미검증 소유자 또는 불완전한 복구는 fail-closed

최신 “두 번째 실행 시 기존 창 재사용” 정책을 대상으로 별도 visible live-window
smoke를 다시 수행한 것은 아니다.

같은 새 Host에서 저장된 OpenAI-compatible 연결과 Pydantic AI runtime으로
합성 포장 셀 planning 요청을 실행했다. client 자유 텍스트와 structured brief를
의도적으로 다르게 보냈으며, SQLite에 저장된 user message와 실제 모델 prompt가
서버 canonical brief를 사용하고 충돌한 자유 텍스트를 포함하지 않는 것을
확인했다.

| 항목 | 결과 |
|---|---|
| workflow | `new_project_planning` |
| assistant 실행 | `complete` |
| typed plan 상태 | `needs_clarification` |
| assistant 계획 본문 | 생성 성공 |
| canonical brief 영속성 | 성공 |
| planning MCP/tool 호출 | 없음 |

이 검증은 Launcher→Host→React/PWA와 planning agent의 실제 실행 증거다.
XG5000 신규 프로젝트 생성, 일반 LD 구현, check·save·reopen·XG-SIM 또는 실제
PLC 쓰기·런중 수정의 완료 증거는 아니다.

## 11. XG5000 프로그램 전면화 도구 검증

2026-07-30에 분석 결과의 “XG5000에서 프로그램 열기”를 뒷받침할 독립 read
capability인 `program.focus`를 추가했다. 기존 `program.read_ld`의 내부
활성화를 UI 명령으로 오용하지 않고, 화면 전환 자체의 대상·실패·감사 경계를
분리한 것이다. 현재 보증하는 범위는 프로그램 전면화이며 network·token의
정확한 편집 위치 이동까지 보증하지 않는다.

| 항목 | 결과 |
|---|---|
| 현재 공개 MCP 도구 | 38개 |
| capability | `read` |
| identity precondition | exact session/PID/HWND/project path/project hash |
| program selector | fresh live catalog의 stable `program_id`, 정확히 1개 일치 |
| activation proof | bound MDI parent, editor identity, exact active child, exact foreground window |
| project file | 전후 SHA-256 동일 강제 |
| 금지 동작 | 편집·저장·simulator·PLC 접근 없음 |
| focused test | `88 passed` |
| MCP 전체 회귀 | `266 passed, 10 skipped, 14 subtests passed` |
| 실제 XG5000 전면화 | 대동 프로젝트 `초기동작` 프로그램으로 검증 |

실제 대동 프로젝트에서 fresh catalog의 `초기동작` stable program ID를 사용해
bound MDI child를 활성화했다. exact active editor와 XG5000 foreground가
확인됐고 프로젝트 SHA-256은 전후 동일했다.

근거:

`artifacts/mcp-sessions/f20c1e83-217b-44b8-95fb-201c53f9153f/20260730-112250-847771-program.focus/result.json`

Host에는 인증된 `GET /api/v1/engineering/programs`와 CSRF 보호
`POST /api/v1/engineering/programs/{program_id}/focus`를 연결했다. 목록 API는
MCP의 HWND·title·project path/hash·artifact 데이터를 버리고 검증된
`programId`, `name`, `language`만 반환한다. focus 응답도 stable program
identity와 `verified/readOnly/projectUnchanged`만 노출하며, exact active
program·foreground window 증거가 하나라도 없으면 `502`로 닫힌다. MCP
unavailable은 `503`, read-ready engineering target 부재는 `409`로 차단한다.

| Host 검증 | 결과 |
|---|---|
| 관련 API·gateway 집중 테스트 | `28 passed` |
| Host 전체 회귀 | `184 passed, 1 environment-specific credential test deselected` |
| Host Ruff | 통과 |

Web에는 `GET /api/v1/engineering/programs` 결과를 좁은 읽기 전용 탐색 패널로
표시하고, 선택한 stable `programId`를 CSRF 보호 focus API로 보내는 경로가
연결돼 있다. 빈 목록·로딩·실패·중복 클릭 차단은 컴포넌트와 App 통합 시험으로
검증했다. 실제 live evidence는 위 MCP receipt이며 Web은 이 검증된 좁은 Host
경로만 호출한다.

## 12. 사용자 재진입·진행 표시 자동 검증

2026-07-30에 실제 사용자가 프로젝트를 다시 선택하고 Host 재시작 뒤 작업을
이어 가는 경로를 다음 범위에서 자동 검증했다.

| 경로 | 자동 검증 결과 | 실제 환경 판정 |
|---|---|---|
| Windows 네이티브 파일 선택 | `GetOpenFileNameW`, `.xgw/.xgwx` 필터, canonical existing path, 취소, native 오류, 인증·CSRF와 동시 dialog 차단 | Host/Web 연결 구현. 실제 사용자 dialog smoke는 별도 기록 없음 |
| 저장 workspace 재개 | 저장 identity와 MCP `workspace.status` 재비교, 원본/working-copy hash drift 및 전역 target lease에서 XG5000 attach 전 `409` | 자동 통합 검증 |
| 재개 후 conversation | 성공한 target 재개마다 새 engineering conversation 생성 | Web 자동 검증. workspace cryptographic binding 전까지 의도적 정책 |
| 일반 conversation 복원 | 브라우저 저장 ID가 없거나 유효하지 않을 때 Host 최근 conversation 조회·복원 | Web 자동 검증 |
| MCP tool 진행 | run별 started/completed/failed, tool name·call ID·failure kind만 노출, 인자·결과·원시 오류 제거, 동시 run sink 격리 | Host gateway 자동 검증 |
| feature readiness | native create와 online observe가 `preview/fake_only/currentReady=false`, `online_observe` capability 미부여, 일반 제품 Host의 직접 create/connect/status/read API가 MCP 호출 전 `409` | Host API 자동 검증, 일반 UI·직접 API 모두 비활성. fake 경로는 환경변수로 켤 수 없는 validation harness 설정에서만 시험 |
| offline mutation readiness | verified workspace와 exact strong XG5000 session이 없으면 `offline_mutate/currentReady=false`이며 실행용 transaction 생성도 `409`; terminal 변경 뒤 identity가 무효화되면 재검증 전 다음 transaction 거부 | Host API 자동 검증 |

이 표는 재진입 UX와 정보 노출 경계를 검증한 것이다. 실제 PLC 연결·프로젝트
복원·온라인 관찰, 새 XG5000 프로젝트의 실제 생성 성공, 일반 I/O·태스크·심볼·
LD 로직 생성, 검사·재열기·XG-SIM으로 닫힌 신규 구축 또는 전체 PRD 완료
증거가 아니다.

## 13. 엔지니어링 지식·메모리·패턴 회귀와 실모델 계획 검증

2026-07-30에 경량 지식 저장소, 문서 ingestion, 범위 격리, 사람 확인형 기억,
12개 벤더 중립 제어 패턴, Agent read-only 검색과 Web 지식 화면을 포함한 최신
전체 회귀를 다시 실행했다. 아래 수치는 앞 절의 역사적 시점별 결과를 덮어쓰는
것이 아니라 현재 working tree 전체의 추가 검증 결과다.

| suite | 최신 결과 |
|---|---|
| Host | `242 passed` |
| MCP | `276 passed, 1 skipped` |
| Launcher | `78 passed` |
| Web | `86 passed` |
| Python 정적 검사 | Host·MCP·Launcher Ruff 통과 |
| Web 정적·배포 검사 | TypeScript typecheck 통과, production build 성공 |

지식 집중 회귀에는 다음이 포함된다.

- 삭제 요청을 `archived` 전환으로 처리하고 과거 source/chunk, SHA-256,
  `knowledge://` 인용과 search audit를 보존
- vendor, product family, version과 immutable workspace/project scope를 랭킹
  전에 독립적으로 격리
- target 미정 시 qualified 공유 문서를 제외하고, 명시적 current-project-bound
  문서와 공유 vendor/global 매뉴얼의 applicability 정책을 분리
- 실제 authored PDF의 다중 페이지·partial page 번호, scan-only와 encrypted
  fail-closed, CP949 및 Markdown heading citation
- proposed memory의 기본 검색 차단, 사람이 확인한 exact revision만 활성화
- final answer가 검색 도구가 반환한 exact `knowledge://` ref만 인용하도록 하는
  근거 대조

같은 날 저장된 OpenAI-compatible 연결의 `nemotron-3-super`로 합성 신규 프로젝트
요구사항을 `new_project_planning` workflow에서 실제 실행했다. 이 검증은 이전
9·10절의 “당시 tool 호출 없음” 실행을 무효화하지 않는다. 현재 planning
workflow에는 MCP·mutation 도구 없이 Host read-only 지식 검색만 추가된 뒤의
별도 실행이다. 최종 성공 기록은 conversation
`14355dc2-df54-4c4f-a42c-e1a2e196f2b4`, run
`a6d3e64c-cee6-4977-b250-c5d823eccd47`이다.

첫 시도는 모델 제한 시간을 120초로 둔 상태에서 `ModelAPIError`로 종료됐다.
실패한 run은 프로젝트·XG5000·PLC를 생성하거나 변경하지 않았고 완료로 기록하지
않았다. 모델 제한을 300초로 명시해 다시 실행한 두 번째 run은 약 90초에 정상
완료됐다. 따라서 120초 실패를 숨기거나 첫 시도까지 성공으로 합산하지 않는다.

| 실제 run 항목 | 결과 |
|---|---|
| conversation | `14355dc2-df54-4c4f-a42c-e1a2e196f2b4` |
| run | `a6d3e64c-cee6-4977-b250-c5d823eccd47` |
| workflow | `new_project_planning` |
| 모델 | `nemotron-3-super` |
| 성공 run | 약 90초에 완료, 모델 제한 300초 |
| 최종 출력 언어 | 한국어 |
| MCP·mutation tool | 노출·호출 없음 |
| Host read-only knowledge search | 16회 |
| `evidence.added` | 80건, unique `knowledge_ref` 25개 |
| 최종 응답의 exact `knowledge://` ref | 4개, 모두 해당 run 검색 반환값과 일치 |
| typed plan 상태 | `needs_clarification` |
| completeness | `total=62`, `confirmed=7`, `proposed=50`, `unknown=5`, `blocking=5` |
| 벤더·I/O 주소·신호 극성·시간값의 미제공 값 생성 | 0건 |
| 프로젝트·XG5000·PLC 변경 | 없음 |

80건의 evidence event는 16회 검색에서 반환된 candidate chunk 기록이며 서로 다른
80개 최종 인용을 뜻하지 않는다. 중복 제거된 검색 근거는 25개이고, 최종 응답이
실제로 채택한 exact ref는 4개다. 또한
미제공 값 생성 0건은 이 한 합성 run의 출력 검토 결과이지 모든 모델·요청에 대한
일반적 무환각 보증이 아니다. 결과가 `needs_clarification`인 것도 의도한
fail-closed 동작이며, 네이티브 프로젝트 생성 준비가 완료됐다는 뜻이 아니다.

현재 lexical benchmark는 authored 21문서, answerable 21 query와 no-answer
3 query의 회귀다. word BM25 Recall@5는 `0.9048`, CJK/identifier n-gram과
hybrid RRF는 각각 `1.0000`이며 no-answer false-evidence와 vendor leak은
각각 0건이다. hybrid는 두 단일 채널과 동률인 구간에서 exact word와
CJK/identifier 채널을 함께 보존하기 위한 engineering tie policy로 선택했다.
실제 LS·Mitsubishi·Rockwell 매뉴얼 corpus의 relevance 평가가 끝나기 전에는
dense 검색이 더 낫다고 주장하지 않으며 도입도 deferred 상태다. 작은 fixture의
latency 측정값은 제품 성능 보장이 아니다.

## 14. 저장 workspace의 fresh-launch 재개 실기 검증

2026-07-30에 Actual XG5000 V4.82.1을 대상으로 Host와 XG5000이 모두 없는
상태에서 저장 workspace 재개를 다시 검증했다. 이 결과는 4절의 과거 cold-start
증거를 덮어쓰지 않고, 현재 구현의 별도 실기 증거로 추가한다.

| 항목 | 결과 |
|---|---|
| workspace | `e6e6b5f4-4674-493d-87be-16bee9397f50` |
| source SHA-256 | `bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f` |
| working-copy SHA-256 | `bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f` |
| XG5000 PID | `744` (첫 재개), `10912` (최종 복원 재검증) |
| target 확인 | UI read target confirmed |
| 허용 범위 | read-analysis only |
| API resume | HTTP `200` |

fresh launch 경로에서는 XG5000이 파일을 연 직후 경로 readback을 아직 제공하지
않는 짧은 구간이 있었다. 현재의 bounded path-readback retry 안에서 대상 경로와
hash가 다시 확인돼 재개가 성공했다. 이는 제한 없는 대기나 identity 우회를
추가했다는 뜻이 아니다. 아래 existing-PID 제한 시험 뒤 변경 없는 XG5000을 정상
종료하고 같은 fresh controlled launch를 한 번 더 수행했으며 PID `10912`에서
동일한 read target confirmed/read-analysis only 상태로 다시 복원됐다.

같은 workspace를 이미 실행 중인 PID에 다시 재개하려는 반복 검증은 ROT에서
PID·경로·hash의 strong readback을 확보할 수 없어 의도대로 fail-closed 및
unverified로 끝났다. 따라서 이 결과를 기존 PID 재획득 성공으로 주장하지 않는다.
현재 실기 재개 성공 범위는 fresh XG5000 controlled launch이며, 기존 PID
재획득은 제한된 elevated x86 broker 또는 동등한 strong ROT PID/path/hash
readback이 마련되기 전까지 actionable limit로 남는다.
