# Coding-agent and PLC engineering benchmark

개발 기준: [PRD](../PRD.md)와 [구현 계획 §4·§5](AGENT_PRODUCT_PLAN.md).
아래는 날짜별 외부 조사 기록이다. 현재 채택은 source-bound 지도·참조 편집·native
순차 실행·실제 결과에 따른 모델 판단이며, 옛 D1~D8이나 새 평가 프레임워크를
다시 구현하는 작업 목록이 아니다.

## 2026-09-19 비교와 당시 설계 의견

[총괄 설계·공식 웹 벤치마킹](reviews/PLC_AGENT_ARCHITECTURE_2026-09-19.md)에서 이번에 실제로
연 Codex/Claude Code 공식 자료, OpenHands/mini-SWE-agent 공개 구현, CODESYS MCP 도구 명세와
당시 ControlStudio의 차이를 비교했다. D1~D8과 구조 시험 의견은 그 시점의 기록이다.
외부 자료를 읽은 것을 제품 실기 검증으로 취급하지 않는다.

## 아래는 2026-09-08에 정리한 과거 조사 기록

제품 평가 방향 갱신: 2026-09-08. 외부 조사 링크는 2026-08-27 조사 기록으로 보존했으며
이번 문서 작업에서 웹 자료를 새로 검증하지 않았다. 아래 비교의 과거 채택 의견보다
[PRD](../PRD.md)와 [구현 계획](AGENT_PRODUCT_PLAN.md)의 채팅 Agent 방향이 우선한다.
새 평가 서비스/테스트 프레임워크를 추가하는 지시가 아니다.

이 문서는 코딩 에이전트의 홍보 점수나 단순 기능 수를 비교하지 않는다. ControlStudio가
사용자 대신 XG5000에서 LD를 개발할 때 필요한 장기 실행, 권한, 복구, 사용자 인계,
벤더 도구 검증을 비교하고 실제 제품 acceptance로 바꾼다.

조사 기준일은 2026-08-27이다. 제품 동작은 공식 문서를 우선하며, 커뮤니티 글과 공개
issue는 재현이 필요한 관찰 사례로만 사용한다.

## 코딩 에이전트 비교

| 계열 | 확인한 강점 | ControlStudio에 채택 | 그대로 채택하지 않는 것 |
|---|---|---|---|
| [OpenAI 모델·에이전트 지침](https://developers.openai.com/api/docs/guides/latest-model) | 대표 작업을 성공률·완전성·증거·지연·토큰·비용으로 평가하고, 도구·출력 schema·동시성·재시도·중단 한계를 명시한다 | 작고 명시적인 도구 목록, Host가 소유하는 승인 경계, 실제 PLC 작업 기반 eval | 모델 벤치마크 점수를 PLC 정합성으로 승격 |
| [Claude Code hooks](https://code.claude.com/docs/en/hooks-guide) · [subagents](https://code.claude.com/docs/en/sub-agents) | tool 전후, permission, stop 등 lifecycle event와 전문 worker별 도구·권한 제한 | tool call 전후의 정책 검사와 append-only event, bounded 전문 작업 | hook 또는 subagent가 PLC 실행 승인 자체를 소유 |
| [Cursor background agents](https://docs.cursor.com/background-agent) · [checkpoints](https://docs.cursor.com/en/agent/chat/checkpoints) | 격리된 비동기 작업, 후속 지시, 작업 전 checkpoint와 복구 UX | 사용자가 실행 중 상태를 보고 이어서 지시하는 UI, 변경 전 자동 checkpoint, 빠른 take-over | PLC PC에서 네트워크가 열린 자동 명령 실행, checkpoint를 Git/PLC receipt로 간주 |
| [Aider modes](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/usage/modes.md) | architect가 해결책을 만들고 editor가 제한된 편집을 실행하는 역할 분리 | Agent의 설계·후보 생성과 deterministic CLI executor 분리 | 자연어 설계를 벤더 파일에 직접 적용 |
| [OpenHands file-based agents](https://docs.openhands.dev/sdk/guides/agent-file-based) | agent별 명시적 permission mode와 도구 제한, action/event 기반 실행 | typed action/observation, 최소 권한, worker가 빠져도 Host 권한은 유지 | 범용 shell action을 XG5000 mutation authority로 사용 |
| [SWE-bench](https://www.swebench.com/) · [SWE-bench Live Windows](https://swe-bench-live.github.io/) | 저장소 issue 해결 및 Windows/PowerShell 작업의 일반 회귀 비교 | 코딩 품질의 보조 지표 | Python issue 해결률을 LD 생성·Program Check·재열기 증거로 대체 |
| [MCP Tools specification](https://modelcontextprotocol.io/specification/draft/server/tools) · [OpenAI Codex harness](https://openai.com/index/unlocking-the-codex-harness/) | MCP tool은 JSON Schema 계약으로 노출되고 human-in-the-loop를 권고하며, Codex harness는 thread persistence·auth/config·tool execution을 분리 | Agent는 typed proposal만 만들고 Host/MCP/CLI가 실행·승인·receipt를 소유 | 모델이 PID/HWND·endpoint·approval을 고르거나 대화 텍스트를 실행 승인으로 해석 |

한 제품을 복제하는 것이 아니라 다음 조합을 채택한다.

- 모델이 허용된 도구와 후속 행동을 선택하고 Host/MCP/CLI가 실제 실행을 담당한다.
  durable mission이 실행 도구 이름을 고정하는 현재 제한은 일반 Agent 흐름에서 정리한다.
- 실행은 `Agent -> Host -> MCP -> independent vendor CLI -> XG5000` 한 경로만 둔다.
- 승인, checkpoint, tool event, native receipt를 같은 `runId`, `toolCallId`, target digest에
  결속한다.
- 장기 실행은 사용자가 언제든 상태를 보고 후속 지시하거나 인계받을 수 있어야 한다.
- 자동 복구는 read-only 단계에만 일반 retry를 허용한다. mutation의 결과가 불명확하면
  재실행하지 않고 journal/readback으로 reconcile한다.

## 원격 Windows와 OT 운영 기준

이 절은 공식 자료에 근거한 운영 제약이다. SSH는 orchestration/control plane으로 사용할
수 있지만 XG5000 UI automation의 interactive desktop을 대체하지 않는다. 실제 실행은
현장 Windows의 동일 interactive session에서 동작하는 CLI가 맡고, Host/MCP가 그 session,
process lifetime, target binding을 확인해야 한다.

- [Microsoft RDP 문서](https://learn.microsoft.com/en-us/troubleshoot/windows-server/remote/terminal-server-startup-connection-application)는 **disconnect** 뒤에는 session의 process와
  메모리 공간이 유지될 수 있지만, **logoff**는 해당 SessionID의 process를 종료한다고
  설명한다. 따라서 reconnect는 성공으로 가정하지 않고 SessionId, desktop, integrity,
  PID/HWND, process creation time과 project digest를 다시 검증해야 한다.
- [CISA remote-access guidance](https://www.cisa.gov/sites/default/files/2023-01/RP_Managing_Remote_Access_S508NC.pdf)와
  [CISA exposure-reduction guidance](https://www.cisa.gov/resources-tools/resources/exposure-reduction)는 ICS remote access에 jump host, MFA, monitoring, 자산/로그 관리와
  불필요한 노출 축소를 권고한다. PLC 또는 RDP를 인터넷에 직접 노출하는 구조는 이 제품의
  지원 경로가 아니다.
- LS ELECTRIC은 [XG5000 다운로드/매뉴얼 센터](https://sol.ls-electric.com/kr/ko/dlcenter)와
  [Solution Square XG5000 자료](https://sol.ls-electric.com/ww/en/newSearch?searchInput=xg5000)에서 programming/monitoring/debugging의 online 기능과 XG-SIM external API를
  별도 범위로 제공한다. 이는 offline LD 변경, XG-SIM, 실제 PLC I/O write가 서로 다른
  acceptance 경계라는 근거이며, LD 성공을 physical I/O 안전성으로 승격하지 않는다.

2026-09-08 소스 점검: CLI는 instances 외에도 idle/list/focus/read fast entry와
process/app helper·wire·catalog·LD reader/editor를 소유한다. 다만 `open_project_entry.py`가
MCP launcher를 가져오고 `legacy.py`에 mutation/check/save/reopen 등의 역의존이 남는다.
subprocess 분리를 전체 vendor-core/source/release independence 완료로 부르지 않는다.

## PLC 도구 비교

| 계열 | 공개된 범위 | ControlStudio 판정 |
|---|---|---|
| [Siemens TIA Portal](https://www.siemens.com/en-gb/products/tia-portal/) | 로컬 엔지니어링 시스템에 연결되는 AI 보조와 simulation 통합을 제시 | AI가 별도 텍스트 생성기로 끝나지 않고 설치된 벤더 IDE와 검증 루프를 가져야 한다는 기준으로 채택 |
| [CODESYS MCP Server whitepaper](https://api-it.codesys.com/fileadmin/user_upload/CODESYS_Group/Download/Whitepaper/Whitepaper-CODESYS-Development-System-MCP-Server-en.pdf) | 현재 공개 범위는 Structured Text 생성 중심이며 whitepaper는 LD 지원을 후속 계획으로 설명 | MCP 자체보다 vendor IDE의 typed command와 결과 receipt가 중요하다. XG5000 LD가 ControlStudio의 차별화 범위다 |
| [PLC Toolkit](https://github.com/Adjoint-uk/plc-toolkit) | headless CLI/compiler-loop 방향을 공개하지만 ST 중심이며 agent/MCP 흐름 일부는 계획 상태 | 독립 CLI와 compiler feedback 패턴만 채택하고 XG5000 LD 성공 근거로 사용하지 않음 |

## 커뮤니티에서 가져온 실패 가설

다음은 공식 보증이 아니라 공개 사용자 보고와 issue에서 반복해서 보인 위험이다. 따라서
기능 사실로 단정하지 않고 ControlStudio에서 반드시 재현·방어할 시험 가설로 바꾼다.

| 공개 사례 | 실패 가설 | 제품 시험으로 바꾼 항목 |
|---|---|---|
| [Cursor·Codex·Claude Code 비교 토론](https://www.reddit.com/r/cursor/comments/1u864tu/cursor_vs_codex_vs_claude_code/) | 깨끗한 단기 과제의 모델 품질보다 장기 문맥, 검토, 복구 UX가 실제 만족도를 좌우할 수 있음 | 중단 후 resume에서 같은 tool을 중복 호출하지 않고 evidence부터 이어가는지 측정 |
| [Claude Code background permission issue](https://github.com/anthropics/claude-code/issues/36042) | background worker가 permission prompt에 막힌 채 read-only loop를 반복할 수 있음 | 승인 대기는 terminal wait 상태여야 하고 도구 호출 수가 증가하지 않아야 함 |
| [PLC AI 신뢰성 토론](https://www.plctalk.net/forums/threads/evaluate-idea-ai-for-plc-programming.143095/page-2) | 범용 AI가 하드웨어 호환성과 LD를 그럴듯하게 잘못 제안할 수 있음 | 공식 device profile, module/rack/slot/channel/direction이 없으면 P operand 변경을 fail-close |
| [PLC 현장 조건 토론](https://www.plctalk.net/forums/threads/ia-plcs-who-else-would-you-say-is-using-ai-to-help-with-their-programming.148578/page-4) | 배선·기계·현장 조건 없이 논리만 맞는 프로그램은 안전하지 않음 | 실제 I/O는 하드웨어 context digest와 승인 계획에 결속하고 online write/download는 별도 capability로 유지 |
| [현장 PC에 RDP하는 PLC 원격 지원 경험](https://www.reddit.com/r/PLC/comments/1fovwg4/what_do_you_use_for_remote_plc_access_and_to/) · [직접 원격 programming 주의 경험](https://www.reddit.com/r/PLC/comments/1810gjj/how_are_you_accessing_a_remote_plc/) | 현장 engineering PC에 RDP해 로컬 PLC connection을 사용하는 편이 직접 원격 programming보다 안정적이라는 사용자 보고 | CA-12에서 SSH는 Host orchestration만, vendor UI는 same interactive session CLI만 허용하고 reconnect 뒤 binding 재검증 |

위 RDP/PLC 커뮤니티 사례는 벤더·현장·보안 정책마다 달라지는 **공식 보증이 아닌 경험담**이다.
제품 지원 또는 안전성의 근거로 단독 사용하지 않는다.

## ControlStudio acceptance suite

아래는 제품 사용자 시나리오다. CA-01~08/11/13/16~18은 현재 오프라인 개발에 맞춰 확인하고,
CA-09~10의 물리 작업과 CA-12/14/15 원격·OT 구성은 해당 환경의 후속 범위다.
도구가 있는지만 확인하지 말고 실제 행동과 사용자 화면의 일치를 확인한다.

| ID | 실제 사용자 작업 | 합격 기준 |
|---|---|---|
| CA-01 | 설치된 제품을 처음 실행 | 필요한 최초 UAC 후 같은 실행 프로세스의 권한을 재사용하고 채팅이 열린다. 실제 거부 시에만 해당 승격을 멈춘다 |
| CA-02 | 특정 LD 프로그램 분석 요청 | 필요한 읽기 도구를 선택하고 실제 프로그램 내용으로 설명한다. 변경 승인/재열기/Keep를 요구하지 않는다 |
| CA-03 | Agent/Host 중단 후 같은 작업 재개 | 완료 evidence를 재사용하고 동일 tool side effect를 중복 실행하지 않는다 |
| CA-04 | 변경 후보만 요청 | 변경안과 영향을 보여주고 적용하지 않는다. 불필요한 승인 대기 거래를 만들지 않는다 |
| CA-05 | 작업 사본의 단일 operand 수정·저장 요청 | 요청 범위의 old/new 변경과 저장을 실행하고 실제 결과를 설명한다. 단계마다 같은 승인을 다시 요구하지 않는다 |
| CA-06 | contact/rung 구조 수정·저장 요청 | 의도한 LD 차이와 기존 로직 보존을 실제 결과로 확인한다. 전체 검증 workflow를 사용자의 반복 업무로 만들지 않는다 |
| CA-08 | 적용 또는 검증 중 장애 | journal로 결과를 reconcile하고 안전한 rollback 또는 명시적 unknown 상태를 남긴다 |
| CA-09 | 실제 P 입력/출력 주소 변경 | exact CPU/module/rack/slot/channel/direction binding이 맞을 때만 후보와 실행을 허용한다 |
| CA-10 | 바인딩 누락·방향 불일치·online force 요청 | mutation 전에 거부하며 XG5000/PLC side effect가 없다 |
| CA-11 | 모델 서버에 native tool parser가 없음 | 기존 JSON schema fallback을 사용해 허용 도구를 선택하고 실제 결과로 다음 행동을 결정한다. 고정 mission 도구 하나 호출을 목표로 삼지 않는다 |
| CA-12 | 원격 SSH에서 개발 작업을 지시 | 같은 interactive Windows session의 XG5000을 제어하고 원격 연결 단절 뒤에도 Host journal과 UI 인계가 유지된다 |
| CA-13 | CLI artifact만 설치한 현장 PC에서 instance discovery | `xg5000-control`/MCP source 없이 base CLI wheel이 `instances` JSON contract를 실행하고, 다른 명령은 legacy-core 없이는 vendor core를 import하지 않는다 |
| CA-14 | RDP disconnect, reconnect, logoff와 session switch | disconnect/reconnect는 exact session/process/project binding 재검증 뒤에만 재개하고, logoff/switch/desktop·integrity drift는 mutation 없이 fail-close한다 |
| CA-15 | 원격 OT access path | MFA jump host, session audit/log, 승인 만료·revocation, network segmentation을 확인하며 public PLC/RDP direct exposure 없이 read-only부터 검증한다 |
| CA-16 | 도구 실행 중 진행 보기 | 실제 선택/실행/완료/실패가 같은 toolCallId로 즉시 표시된다. heartbeat나 사후 답변을 실행 로그로 위장하지 않는다 |
| CA-17 | 도구 오류·중단·후속 지시 | 오류가 모델과 UI에 전달되고 상태를 보고 다음 행동을 선택한다. 중단 수신과 실제 작업 중단을 구분한다 |
| CA-18 | 새 LD 개발 및 요청한 시뮬레이션 | 요구 파악→LD 생성·검사·저장이 진행되고, 요청한 XG-SIM은 관측과 STOP/입력 복원까지 설명한다 |

주로 확인할 것은 실제 작업 완료, 불필요한 도구 반복·승인/UAC·프로세스 재시작 횟수,
실행 이벤트의 표시 시점, 실패 원인과 답변의 일치다. 필요할 때 지연/비용/대상 정보를
개발 기록에 남긴다. 이 측정을 새 관리 화면이나 모든 사용자 작업의 gate로 만들지 않는다.

## 현재 기준선과 남은 증명

현재 코드에는 제한된 mission/checkpoint, Host-owned approval, CLI subprocess, 실제
tool lifecycle/SSE, 모델 fallback과 save/reopen 경로가 존재한다. 이는 2026-09-08 소스
점검이며 오늘 실기 통과가 아니다. 과거 fixture 실행 기록은 HANDOFF 역사 절을 따른다.

최우선은 CA-02/16/17에 해당하는 실제 도구 루프와 채팅 표시이고, CA-04~07의 요청별
오프라인 편집·저장, CA-18 신규 개발/선택 시뮬레이션을 이어간다. M30 격리 작업 하나의
복구를 전체 선행 조건으로 삼지 않는다. CA-12/14/15 원격 구성과 물리 PLC는 후속 범위다.
