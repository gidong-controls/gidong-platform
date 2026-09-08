# 채팅 기반 PLC 개발 에이전트 구현 계획

갱신: 2026-09-08. 상태: P0a/P0b와 P1 소스 구현·정적 검증 완료. 실제 XG5000 기반
P1-control·대표 사용자 흐름 검증과 P2 CLI 독립화는 미실행.
제품 요구는 [PRD](../PRD.md), 재개 상태는 [HANDOFF](HANDOFF.md)를 따른다.
참조 대화 `6a9edf19-2e24-83e8-82ed-a3cfc2c41b35`의 최신 제품 방향을 로컬 코드와 대조했다.
그 대화의 이전 답변은 소스 접근 없이 작성됐으므로 구현 증거로 사용하지 않았다.

## 1. 제품 목표와 완료 기준

기본 화면은 채팅이다. 사용자의 PLC 개발·설명·수정 요청에 Agent가 도구를 선택하고,
실제 결과를 받은 뒤 다음 도구 또는 답변을 결정한다. 그 행동과 결과가 같은 대화에
즉시 보인다. 검증 관리 시스템이나 새 mission 프레임워크를 만드는 계획이 아니다.

- 설명 요청: 필요한 읽기와 설명으로 완료할 수 있다.
- 후보만 요청: 변경안을 작성하고 적용하지 않는다.
- 지정 작업 사본의 수정·저장 요청: 요청 범위 안에서 필요한 동작을 이어서 실행한다.
- 시뮬레이션 요청: XG-SIM을 사용하고 관측값·STOP·입력 복원을 설명한다.
- 신규 LD 개발: 요구를 파악하고 설계·생성·검사·저장까지 이어간다.
- 실패/중단/후속 지시: 실제 상태를 알려주고 가능한 다음 작업을 계속한다.

M30 변경은 기존 프로젝트 수정의 한 사례다. `COMMITTED`, 테스트 개수, CLI 저장소
분리만으로 제품 완료를 선언하지 않는다. 물리 PLC는 없으므로 현재 범위는 LS XG5000
오프라인 LD와 XG-SIM이며 실제 I/O는 후속 로드맵이다.

## 2. 점검 범위와 근거 수준

네 저장소의 HEAD·tracked 변경 상태, PRD/주요 설계/벤치마크/핸드오프, 모델/Host/event/
MCP/CLI 주요 실행 경로를 읽었다. 아래 행 번호는 문서 변경 전 HEAD 기준 탐색용이다.
함수명을 다시 검색하면 된다. 전체 파일의 모든 분기를 검증했다는 뜻은 아니다.

이번 구현에서는 소스 정적 검사와 focused contract smoke만 수행했고 정상 개발 앱·모델·
XG5000·XG-SIM을 실행하지 않았다. 실제 실행으로 로드된 모듈 경로와 현재 작업 사본의 LD
내용도 재확인하지 않았다. 따라서 소스 구현·정적 및 focused smoke, 과거 실기 기록, 현재
미확인을 구분한다. 오래된 실기 통과를 현재 코드의 전체 성공으로 확대하지 않는다.

| 영역 | 현재 소스 구현 | 남은 확인 |
| --- | --- | --- |
| Agent loop | Host catalog 기반 선택, 실제 결과·정제 실패 continuation, 대상 없는 안전 경로, 직전 실패 동일 호출 거부 | 실제 모델을 사용한 대표 end-to-end 요청 미검증 |
| Host/Web | selected와 실제 dispatch 시작 분리, SSE terminal 갱신, request-message/run 재결합, compact chat card, 완료·근거 원자 기록 | 정상 기동·장시간 실행·reconnect의 브라우저 실사용 미검증 |
| 요청 범위 | 새 메시지 strict scope matrix, permanent check/save 직접 실행, candidate 승인 대기, 과거 scope 조합 복구 호환 | 실제 XG5000 변경/후보 승인 흐름 미검증 |
| MCP→CLI | 표준 subprocess/JSON 경계, check/save-only 정상 경로, saved identity 회전 | CLI native 역의존과 P1-control 실기 남음 |
| 과거 실행 | 특정 LD의 과거 읽기·설명·생성·재열기 기록 보존 | 현재 작업 사본/timeout/대표 복수 흐름 미확인 |

현재 모든 읽기가 전역 격리에 막힌다고 단정하면 틀린다. `app.py`의 강한 offline 준비
조건은 `change_request_scope != analysis_only`에 적용된다. Keep/Revert도 특정 시뮬레이션
변경 흐름에 있다. 삭제 범위는 실제 연결된 분기를 따라 정하며, 이름에 verification이
있다는 이유만으로 정상 읽기나 사용자 데이터를 지우지 않는다.

## 3. 실제 호출 경로와 재사용할 코드

```text
Web sendMessage → Host send_message_v1 → execute_agent_run → Agent sidecar/runtime
Agent tool.request → Host ExecutionBroker → MCP tool → CLI → XG5000
MCP/CLI result → Host tool.result → Agent의 다음 모델 호출
Host tool event → SSE → 같은 대화의 tool timeline
```

| 위치 (workspace 상대 경로) | 확인한 함수/구간 | 수정 목적 |
| --- | --- | --- |
| `gidong-agent/src/controlstudio_agent/runtime.py` | `PydanticAIAgentRuntime.run` L13662, cycle L14708, `_engineering_execution_tool_name` L5109, `_engineering_continuation_analysis` L1431 | 기존 loop를 재사용하며 요청별 도구 선택과 완료 판단을 단순화. 초기 LD 특수 선택/mission 강제를 실제 사용처와 함께 정리 |
| `gidong-agent/src/controlstudio_agent/sidecar.py` | `_sidecar_toolset` L221, tool.request/result L866–904, `_cancel_run` L906 | 실제 결과를 다음 모델 입력으로 유지. ToolFailed의 일반화된 오류에 안전한 원인 코드/단계를 보존할지 Host 계약과 함께 수정 |
| `gidong-platform/host/src/controlstudio_host/app.py` | `send_message_v1` L9770, `execute_agent_run` L8082, `publish_tool_event` L8937, `cancel_run_v1` L10396, `replay_events_v1` L11531 | 요청 범위·도구 목록·결과·이벤트를 한 경로로 연결. 일반 채팅과 변경 제한의 실제 범위를 유지/단순화 |
| `gidong-platform/host/src/controlstudio_host/execution_broker.py` | `execute_external_tool_request` L562, started L569, completed L690, failed L1615 | Host client는 모델 선택 때 `tool.selected`를 기록하고, broker는 preflight 뒤 실제 MCP dispatch 직전에 `tool.started`를 기록한다. terminal projection을 선검증한 뒤 completed/failed를 전달 |
| `gidong-platform/web/src/App.tsx` | event reduction L1507, stream L1843, sendMessage L3775, Keep/Revert L3810, `showAgentWorkspace` L4139, timeline L5701 | 기존 timeline/SSE를 활용한 채팅 기본 화면. 실행 중 카드/경과 시간/실패/중단, 보조 패널 배치와 대상 없는 대화 진입 수정 |
| `gidong-platform/web/src/api/client.ts`, `api/sse.ts`, `api/types.ts`, `activityProgress.ts` | 현재 HTTP/SSE client·DTO·진행 표시 | 필요한 선택 이벤트/취소 상태만 기존 계약으로 전달. 새 이벤트 버스는 만들지 않음 |
| `gidong-platform/web/src/components/EngineeringMissionPanel.tsx`, `ChangeApprovalPanel.tsx`, `ChangeRecoveryPanel.tsx`, `NativeProjectPanel.tsx` | App에서 실제로 호출되는 panel | 기본 대화를 점유하는 강제 절차 제거. 대상/변경/오류에 필요한 정보는 대화의 보조 카드로 통합 |
| `gidong-plc-mcp/src/xg5000_control/cli_adapter.py` | `run_xg5000_cli` L1645, 명령 분류 L43–80 | 표준 CLI 실행/timeout/error 경계 재사용. 별도 경쟁 subprocess 경로 추가하지 않음 |
| `gidong-plc-mcp/src/xg5000_control/server.py` | `_cli_program_check` L13291, `_cli_project_save` L13585, `_cli_fresh_reopen_ld_approval_readback` L13781; open/reopen/simulation/save tool handlers | 편집·검사·저장·재열기·시뮬레이션을 요청 범위에 맞게 호출. 기존 거래 전용 gate와 일반 실행 도구를 구분 |
| `controlstudio-xg5000-cli/src/controlstudio_xg5000_cli/__main__.py`, `open_project_entry.py`, `legacy.py` | fast command dispatch, `_open_workspace` L290, legacy command dispatch | CLI-owned 명령을 유지하고 MCP native 역의존 폐쇄를 실제 명령별로 이관 |
| `gidong-plc-mcp/src/xg5000_control/project_launcher.py` | `open_workspace_in_xg5000` L55, `_open_workspace_in_xg5000` L117 | 현재 Open 실패의 실제 소유 위치. c5311c8 변경은 live 미확인; 별도 Open 재시도 wrapper를 추가하지 않음 |

`on_tool_event=None`인 Agent sidecar는 현재 Host가 실행 이벤트를 소유한다는 의도다.
이를 UI 이벤트가 아예 없다는 증거로 해석하지 않는다. Host가 받은 실제 요청에서 선택
상태를 만들고 실제 dispatch/반환에서 실행·종료 상태를 기록한다.

## 4. 구현 순서와 단계별 완료 조건

아래 `완료:` 문장은 end-to-end 실기 완료 기준이다. 이번 갱신의 P0a/P0b와 P1 완료 표기는
소스 변경과 정적·focused smoke 완료만 뜻하며, 이 실기 기준을 통과했다는 뜻은 아니다.

### P0a — 기존 모델 루프와 결과 전달

Agent runtime/sidecar와 Host tool catalog·dispatch를 먼저 함께 읽고 수정한다. 허용된
도구 중 필요한 다음 도구를 모델이 선택하게 하며, 실행된 도구의 결과·오류를 같은 대화
문맥에 되돌린다. 조사 결과에 없는 새 engine/service를 추가하지 않는다.

완료: 실제 설명 요청에서 모델이 필요한 도구를 실행하고, 받은 내용으로 후속 도구/답변을
결정한다. 미확인 가설은 한계로 설명할 수 있으며 모든 가설 검증을 일반 답변의 관문으로
강제하지 않는다. 도구가 실패했는데 성공 문장으로 덮거나 같은 인자로 반복하지 않는다.

### P0b — 실시간 채팅 (P0a와 병렬 가능)

Host publish/broker, 기존 event schema/DTO, Web SSE reducer/timeline을 함께 수정한다.
선택·실행 시작·완료·실패가 실제 사건에 따라 전송되게 한다. 사용자에게 역할·대상·경과
시간·짧은 결과만 기본 표시한다. 모델 내부 추론이나 만들어낸 진행률을 실행 기록으로
표시하지 않는다. 기존 cancel의 변경 중 409 분기를 확인해 안전한 시점 중단/현재 상태를
설명하며, HTTP 취소를 실제 XG5000 동작 취소로 오인시키지 않는다.

완료: 오래 걸리는 실제 도구가 끝나기 전에 카드가 표시되고, 같은 toolCallId가 종료 상태로
바뀐다. 새로고침/reconnect에서도 중복 카드 없이 이어지고 일반 채팅이 설정 화면에 가려지지 않는다.

### P1 — 요청한 작업 사본 편집·검사·저장

Host의 요청 범위 및 tool catalog, Agent의 후보 전용 제약, MCP mutation handler를 함께
변경한다. 사용자의 명확한 요청 범위를 Host가 기록하고 내부 도구에 전달한다. 별도의 확인은
후보 전용 요청의 실행 전환, 대상/범위 변경, 되돌리기 어려운 작업 등에 필요한 경우만 받는다.
단순히 UI 버튼을 숨기거나 nonce 검사에 상수 true를 반환하는 수정은 하지 않는다.

fresh reopen/XG-SIM/Keep가 한 흐름에 묶인 API·UI·handler를 분리한다. 독립 재열기는
필요한 저장 결과 조사 도구로, 시뮬레이션은 요청한 시험 도구로 제공한다. 실패한 작업의
추가 변경 제한은 유지하되 일반 대화·다른 안전한 읽기를 막는 실제 분기는 제거한다.

완료: 웹에서 작업 사본의 수정·저장을 맡기면 불필요한 반복 확인 없이 실제 변경·검사·저장이
끝나고 결과를 설명한다. 후보만 요청했을 때는 변경하지 않는다. 정확한 LD 차이와 대상
보존을 확인하며 M30 11→13개라는 개수만으로 로직 보존을 판정하지 않는다.

### P1-control — 기본 제어 오류 (P0와 독립 진행 가능)

현재 Open/reopen 실패를 해당 CLI/MCP 기본 명령에서 재현하고 원인을 수정한다.
작업 사본·기존 백업은 보존한다. 오래된 격리 거래를 초기화하거나 원복 완료로 가정하지 않는다.
c5311c8의 실제 효과는 현재 코드를 정상 기동한 뒤 확인한다. 이미 해결한 UTF-8/경로 대소문자/
raw·decoded 해시 문제를 근거 없이 다시 수정하지 않는다. 기존 불확실한 변경이 있는 사본은
현재 디스크 내용·백업·저널 관계를 확인한 뒤 구현된 복구 경로로 처리한다.

완료: 같은 원인으로 전체 웹 과정을 반복하지 않고 기본 Open/read/save의 실제 결과 또는
고정 원인 코드·실패 단계가 반환된다. 제어 한 건의 실패로 P0 개발을 모두 멈추지 않는다.

### P2 — 일반화·CLI 독립·정리

서로 다른 설명/변경 요청과 새 LD 생성을 웹에서 수행한다. XG-SIM은 요청한 입력과 관측,
STOP/복원까지 확인한다. CLI는 이미 이관한 process/app helper·wire·catalog·LD reader/editor를
재사용한다. 아직 `open_project_entry.py`와 `legacy.py`가 가져오는 MCP native 코드를 명령별
의존 폐쇄로 옮긴다. 실패 원인이 module 경로 혼선이면 필요한 추출만 앞당기고, 무관한 전체
재배치는 P0/P1을 막지 않는다. 명령별 한 소유자를 남긴 후 죽은 wrapper/gate/UI 소비자를
함께 삭제한다. private 구현을 public Platform에 복사하지 않는다.

완료: MCP 소스/PYTHONPATH가 없는 환경에서 지원한다고 명시한 CLI 명령이 실행되며,
대표 기존/신규 PLC 개발 요청을 채팅에서 수행한다. production 서명·배포와 물리 PLC는
별도 릴리스/장비 로드맵이다.

## 5. 검증과 작업 운영

새 검증 시스템이나 테스트 개수 목표는 없다. 문서/정적 검사는 UI 실사용 성공을 대신하지
않는다. 이번 소스 구현은 Python compile/Ruff, TypeScript typecheck/build, `git diff --check`,
focused contract smoke까지만 수행했다. 후속 확인은 실제 실패 재현과 정상 웹 사용자 흐름을
중심으로 진행하고, 회귀 검사가 필요하면 현재 문제를 확인하는 최소 범위만 사용한다. 사용자
요청 없이 테스트 파일을 다시 대량 생성하지 않는다.

개발 실행 진입점은 Platform `scripts/Start-ControlStudio-Development.ps1 -Web` 또는
`.cmd`이며 이번 구현 검증에서는 실행하지 않았다. 일반 웹 사용은 기존 Launcher 인증 경로를
따른다. token을 문서·URL 예시·커밋에 저장하지 않는다. Codex의 화면 제어 기능이 제품의
필수 의존성이 되어서는 안 된다. 제품 자체 Agent→Host→MCP→CLI 경로로 동작해야 한다.

P0a/P0b와 P1의 소스 구현·정적 검증은 완료했다. 다음 작업은 정상 개발 기동에서 대상 없는
설명과 후보만 작성 흐름을 먼저 확인하고, P1-control Open/read/save와 직접 수정·검사·저장을
같은 실기 작업자가 이어서 확인하는 것이다. 같은 파일과 실제 XG5000 동작의 소유자는 하나로
유지한다. 그 뒤 V6 XG-SIM 대표 흐름과 P2 CLI 역의존 폐쇄·신규 LD 흐름을 진행한다. 새 채팅에서
이 문서 작성 과정을 반복하지 않는다.
