# 10. 근거 색인과 변경 전 상태

감사일: 2026-09-20. 이 패킷은 제품 구현이 아니라 읽기 전용 감사·복구 작업 정의다. 최초 상태는 파일을 쓰기 전에 도구 출력에 기록했고 아래에 옮겼다. 기존 9월 19~20일 보고서는 재사용하되 사실·해석·설계 제안을 구별했다.

## 근거 등급

- **확인됨:** 이번에 읽은 소스/설정/Git/도구 결과로 확인. 동작 실행을 뜻하지 않는다.
- **확인됨(이전 감사):** 날짜·대상·조회 방법이 있는 기존 감사의 기록. 이번에 DB/실기를 다시 실행한 것은 아니다.
- **높은 가능성:** 직접 확인된 사실들로 지지되는 원인 해석. 인과 실험은 하지 않음.
- **가설 / 확인 불가 / 접근 권한 때문에 미확인:** 각각 따로 표시한다. 증거 부재를 결함이나 성공으로 바꾸지 않는다.
- 신뢰도 **높음/중간/낮음**은 판단 근거의 강도이며 통계적 성공 확률이 아니다.

## E01 — 최초 Git 상태

작업 루트 `C:/Users/Administrator/Downloads/workspace/controlstudio-platform-main` 자체는 Git 저장소가 아니다(`rev-parse --show-toplevel`, exit 128). 아래 네 하위 저장소가 독립 저장소다. git 명령은 해당 `-C`/working directory와 프로세스 한정 `-c safe.directory=<정확한 저장소 경로>`로 실행한다. 전역 Git 설정은 변경하지 않았다.

| 저장소 | 브랜치 / HEAD | 주요 다른 브랜치 | 최초 변경 / worktree |
|---|---|---|---|
| Platform | dev / `9e2b7eb52ee57f8527362efb67ab4a26b8353f8d` | main `22d744a` | staged 없음; 아래 문서 변경만; 해당 경로 worktree 1개 |
| Agent | dev / `0410ed14db258be81246890cfab1e7823951c12b` | main `fec902c` | staged/unstaged/untracked 없음; worktree 1개 |
| MCP | dev / `dd6b43e69da7a230d064bad2fbf7abd80ca38ef3` | main `cfb2063` | 동일, clean; worktree 1개 |
| CLI | main / `00567b55799e99fdf3485b82ab145c1f2166a284` | 로컬 main만 | 동일, clean; worktree 1개 |

각 worktree는 위 작업 루트 아래 해당 저장소 디렉터리다. upstream은 각각 `origin/dev`, `origin/main`; **기존 로컬 remote-tracking ref와 0/0**이다. fetch를 하지 않아 GitHub 서버 최신과 같다는 뜻은 아니다.

정확한 로컬 디렉터리와 remote:

| 로컬 디렉터리 | remote origin |
|---|---|
| `gidong-platform` | `git@github.com:gidong-controls/gidong-platform.git` |
| `gidong-agent` | `git@github.com:gidong-controls/gidong-agent.git` |
| `gidong-plc-mcp` | `git@github.com:gidong-controls/gidong-plc-mcp.git` |
| `controlstudio-xg5000-cli` | `https://github.com/gidong-controls/controlstudio-ls-xg5000-cli.git` |

CLI의 로컬 폴더명과 remote 이름은 다르다. 최종 감사 검사에서 remote 이름으로 로컬 경로를 구성한 묶음 명령은 `cannot change to ... No such file or directory`/exit128로 실패했다. 작업 루트의 실제 디렉터리를 읽어 새 근거를 얻은 뒤 정확한 경로에서 확인해 exit0·같은 HEAD·clean을 확인했다. 제품/과거 작업의 branch 오류로 집계하지 않는다.

최초 Platform unstaged: `docs/AGENT_PRODUCT_PLAN.md`, `docs/CODING_AGENT_BENCHMARK.md`, `docs/HANDOFF.md`, `docs/README.md`. untracked: `docs/reviews/PLC_AGENT_AUDIT_2026-09-19.md`, `PLC_AGENT_ARCHITECTURE_2026-09-19.md`, `PLC_AGENT_DESIGN_REVIEW_2026-09-20.md`. 이전 사용자의 작업으로 보존했다. 루트 `HANDOFF.md`는 상위 비저장소 파일이다.

최초 문서 SHA-256:

| 파일 | SHA-256 |
|---|---|
| 루트 HANDOFF | `71229696F345AFDDD80E96681E4D58F0373205A0A3417E6522B30A238BC56AAB` |
| docs/HANDOFF | `C1C40709B2383476A3E891C32DFE01C5D8D9653ECA62088246EAF5DF6D619D9E` |
| docs/AGENT_PRODUCT_PLAN | `32F2980676C71ACA8302945D8243E96C883C3B6A92F0ED302CECB8F0E7797B2D` |
| docs/CODING_AGENT_BENCHMARK | `CD6E7DB2B5B26A46A0F380685D2AAA89D903578EA5D59ADBC0E34278DB50AABD` |
| docs/README | `A5C58F20CF79BC9A9DC29368324096D0764226C98669ECC8B21D128D9A540F7B` |
| 이전 현상태 감사 | `0DE20344E9E726D1394FB921393066FFADB5BB773B91031B90D5551F6954E7E6` |
| 이전 총괄 설계 | `B35ECD70ED1BFC170F94D2D5F8A5FB78A86C965107E134F4BDCF52E6136B290F` |
| 이전 설계 반대 검토 | `931EF9B829EDB6D180066E00C14A4AA6C6C2EC878D7F0C905E1E93A2007B8B29` |

대표 최근 커밋: Platform `9e2b7eb`(09-15), `7b20349`·`6700f38`(09-09); Agent `0410ed1`(09-15), `71844ae`(09-09), `4ebdbe6`(09-08); MCP `dd6b43e`(09-15), `4698f74`·`efcbd20`(09-08); CLI `00567b5`(09-15), `518e389`(09-08), `0674f94`(09-04). 각 `log -12 --date=iso-strict` 원문은 감사 도구 기록에 있다. `Complete`라는 커밋 제목은 제품 인수 근거가 아니다.

## E02~E21 — 소스·문서·실행 근거

이 패킷의 코드 경로는 작업 루트 기준이다. 행 번호는 위 HEAD 기준 탐색점이며 이후 수정 시 함수명으로 다시 찾는다. 기존 감사 문서는 이번에 원문을 다시 수정하지 않는다.

| ID | 근거 위치 / 확인 내용 | 등급 |
|---|---|---|
| E02 | `gidong-platform/PRD.md:12,64,94,179,200,284,409,459,487,510,543`; 27개 ID, 채팅 Agent, 오프라인 범위, 완료/보안 기준 존재 | 확인됨·높음 |
| E03 | `gidong-platform/docs/PRODUCT_ARCHITECTURE.md:17,41,70,132`; `ENGINEERING_COLLABORATION_WORKFLOWS.md:6,21,61`; README 실행/책임 구조 | 확인됨·높음 |
| E04 | `gidong-agent/src/controlstudio_agent/runtime.py:183,255,3259,16686,19365,19411,19482,19568`; valid 뒤 publish만, planning 분기/종료. `tests/test_workspace_stable_workbench.py:31`은 그 제약을 기대값으로 고정 | 확인됨·높음 |
| E05 | `gidong-platform/host/src/controlstudio_host/native_projects.py:2394,2412,2477,2558,2579`; 언어 변환과 설계 정책 혼재. `api_models.py:402,424,713` 지원 표현/명령 범위 | 확인됨·높음 |
| E06 | `native_projects.py:3958,3993,4031,4076,4108,4406,4990`; immutable build resume, binding-only overlay, 동일 source 결속 | 확인됨·높음 |
| E08 | [이전 감사](../reviews/PLC_AGENT_AUDIT_2026-09-19.md):53,74,149,168,202,210,233,330; 과거 8개 XG-SIM 통과, 101 run, 정적 수동 반례, 심볼 0, 실행 식별자 | 확인됨(이전 감사)·높음; 새 실기 아님 |
| E09 | Platform `.github/workflows/quality.yml:15,34,40,73`; Agent `quality.yml:63,70`; MCP `quality.yml:34,37,40`; compile/lint/build는 있으나 행동 테스트 실행 step 없음 | 확인됨·높음 |
| E10 | `model_connection.py:83,154,173,186`, `store.py:559`; key/header 값을 JSON 평문으로 저장/복원. 실제 credential 값은 조회하지 않음 | 확인됨·높음 |
| E11 | `store.py:244,354,400,430,618,936,3090`; SQLite WAL/FK/transaction/lease. `app.py:7084,7260,7370,3211,3227` startup reconcile, loopback/auth/endpoint consent | 확인됨·높음; 침투 시험 아님 |
| E12 | `runtime.py:68,70,442,20435`; 196,608 context 상수·150,000 input budget·OpenAI client. `model_connection.py:107` URL/model 설정. 실제 장문/서버 품질 미시험 | 코드 확인됨; 능력 확인 불가 |
| E13 | `web/src/App.tsx:828`; `NewProjectPlanReviewPanel.tsx:1465,1488`; planning-only 주입과 일괄 review UI. 이전 감사 F9 | 확인됨·높음; 현재 시각적 UX 미확인 |
| E14 | 각 repo Git log/reflog, 아래 C01~C04; 706 cross-repo commits와 파일 touch 등 포렌식 집계 | 확인됨·높음; 낭비 시간 추정 아님 |
| E15 | 현재 작업 `01a07e3e-1723-7c71-afda-538900b4a8de`(핸드오프 계획 구현) 대화/접근 가능한 요약. 감사 등록 전 `get_goal={goal:null}` | 현재 null 확인됨; 과거 단일 formal Goal 원문 확인 불가 |
| E16 | `gh` 미설치, 인증된 GitHub connector 없음, 4 remote의 pulls/issues/actions 12개 공개 REST 조회 모두 도구 접근 실패; 첨부 artifact 목록 비어 있음 | 원격 PR/이슈/CI 접근 권한 때문에 미확인 |
| E17 | Node26.3.1/npm11.16.0/uv0.11.21/Python3.14.3; Host/MCP venv3.12.13, Agent venv3.14.3; CI Python3.12/Node22. 활성 runtime과 지원 범위는 별도 | 설치/설정 확인됨·높음 |
| E18 | global `.codex/AGENTS.md` 0byte, override 없음, ancestor/저장소 지시 검색 및 config allowlist 읽기; 아래 지시 감사 | 확인됨·높음, 런타임 전체 주입 로그 아님 |
| E19 | PRD543~551 미결 정책; `bom.py:69`, `packaging/scripts/Start-ControlStudio.ps1:31`, `Install-ControlStudio.ps1:17`; 서명/BOM 검증 코드. CLI pyproject license와 LICENSE 부재, Host author 메타데이터 | 소스 확인됨; 실제 production 서명/법적 적합성 미확인 |
| E20 | 기존 웹 탭 조회: localhost8767 탭 2개가 연결 불가 제목. 하나 선택 시 browser URL policy가 오류 페이지 접근 차단. 앱/모델/IDE 기동하지 않음 | 현재 UI 인수 확인 불가; 제품 고장 원인 단정 불가 |
| E21 | 기존 AGENT_PRODUCT_PLAN P0~P2, 감사 F1~F10, 설계 D1~D8, 반대 검토 R1~R7/A~D | 로컬 작업 중복 검토 확인됨; 원격 중복은 E16 |

## 수행 명령과 종료 결과

| 기록 | 읽기 명령/도구 | 결과 |
|---|---|---|
| C01 | 각 repo `git -c safe.directory=<repo> -C <repo> branch --show-current`, `rev-parse HEAD`, `status --short`, `diff --cached --name-status`, `diff --name-status`, `ls-files --others --exclude-standard`, `worktree list --porcelain`, `branch -avv`, `log -12` | 해당 Git 읽기 성공. 일부 pytest temp 디렉터리 권한 경고; 무시된 디렉터리 내부 완전 목록은 미확인 |
| C02 | `git ... rev-list --count --since=2026-08-15 --until=2026-09-21 HEAD` | repo별 exit0: 311/80/241/74. 합계706. 독립 시도 횟수 아님 |
| C03 | `git ... log --since=2026-08-15 --date=iso-strict --format=... --name-only`; `git ... reflog --since=2026-08-15` | 포렌식 작업자 exit0 집계; 상세 한계 [04](04_STALL_FORENSICS.md) |
| C04 | umbrella `git rev-parse --show-toplevel`; sandbox에서 safe.directory 없는 Git | 각각 exit128(non-repo/dubious ownership). 정확한 repo와 프로세스 한정 설정으로 읽기 성공. 제품 오류 아님 |
| C05 | `Get-Command gh`; 공개 GitHub REST의 pulls/issues/actions 읽기 | gh 없음. CommandNotFound 뒤 LASTEXITCODE는 gh 결과가 아니므로 폐기. REST도 접근 불가; 재시도 중단 |
| C06 | `node --version`, `npm --version`, `uv --version`, `python --version`; `pyvenv.cfg` 읽기 | 버전 명령 exit0, E17. Get-Command 여러 이름을 묶은 초기 명령은 gh 부재로 exit1; Git 실패로 해석하지 않음 |
| C07 | scoped `rg --files <tests> -g test_*.py` | 각 exit0, Host26/Agent19/MCP15/CLI2 파일. 테스트 개수·실행 통과율 아님 |
| C08 | PRD ID 추출; 코드/워크플로/manifest/테스트 본문 읽기 | canonical requirement27개; pytest 함수형 테스트 확인. `unittest discover`만으로 수집된다고 가정하면 안 됨 |
| C09 | `cua.getTab` 기존 URL → 일치한 기존 tab ID 선택 | 다중 탭 식별 후 오류 페이지 policy 거절. 우회/재기동하지 않고 UI 평가 한계 기록 |
| C10 | OpenAI/Siemens/Beckhoff/CODESYS/Claude/MCP/mini-SWE 공식 페이지 열기 | [06](06_BENCHMARK_ANALYSIS.md)에 성공/실패·확인일 기록. 직접 비교 성능 시험 아님 |

포괄 `rg`는 권한 없는 temp/artifacts 경고가 있었다. 이후 추적 파일 및 명시한 소스/문서 디렉터리로 좁혔다. 모든 디스크나 모든 삭제된 임시 파일을 조사했다는 주장은 하지 않는다. 테스트·빌드·모델 호출·DB 읽기/쓰기·배포·설치·삭제는 이번 감사에서 실행하지 않았다. 문서 링크/일관성 검사는 아래 완료 검증에 별도 기록한다.

## 지시 파일·에이전트 설정 감사

발견 결과: 사용자 전역 AGENTS는 빈 파일, 전역 override 없음. 작업 루트부터 상위 경로의 AGENTS/override에 유효 내용 없음. 네 저장소의 tracked AGENTS/override/CLAUDE/GEMINI/`.instructions.md` 및 프로젝트 `.codex/config.toml` 없음. 무시된 권한 거부 temp 디렉터리까지 부재를 증명한 것은 아니다.

전역 config에서 확인한 기본값은 `model=gpt-5.6-sol`, `model_reasoning_effort=high`, 이 작업 루트의 trust는 trusted다. `project_doc_max_bytes`, fallback filenames, model_instructions_file 별도 지정은 해당 검색에서 없었다. 이는 저장 기본값이며 현재 주 세션 모델/주입 내용을 확정하는 자료가 아니다. 이번 하위 감사자는 사용자 지시대로 Sol medium 3개, 읽기 전용이었다. B/D는 한 작업자, E/지시 감사는 주 에이전트가 담당했다.

공식 [AGENTS 발견 규칙](https://learn.chatgpt.com/docs/agent-configuration/agents-md)에 따르면 전역/프로젝트 각 경로에서 override가 우선하고 가까운 경로의 지시가 뒤에 결합되며 기본 총량 한도가 있다. [설정 우선순위](https://learn.chatgpt.com/docs/config-file/config-basic)는 명령 재정의·신뢰 프로젝트·선택 프로필·사용자 기본값 등을 구별한다. 확인일2026-09-20. 따라서 **현재 발견한 AGENTS 충돌이 정체 원인이라는 근거는 없다.** 앱/세션이 실제 주입한 전체 로그를 재구성하지 못했으므로 “활성 지시를 전부 복원했다”고 하지 않는다.

이번 감사는 최신 사용자 audit-only 범위를 따른다. 이전 “계속 완성하라”는 구현 지시를 계속 실행할 권한으로 쓰지 않는다. 긴 HANDOFF는 자동 AGENTS가 아니라 명시적으로 읽는 자료이며, 457,548byte/5천행 이상이라 최신 결정 찾기와 맥락 비용이 크다. 파일 크기가 AGENTS 기본 한도를 넘는다고 실제 잘림을 입증한 것은 아니다. 새 시작점은 짧은 현재 상태+백로그 링크, 과거는 그대로 보존한다.

## Finding 연결

| Finding | 주 근거 | 상태 / 신뢰도 |
|---|---|---|
| AF-01 planning 제어/완료 단절 | E02,E04 | 확인됨 / 높음 |
| AF-02 설계 정책과 compiler 혼재 | E05 | 확인됨 / 높음 |
| AF-03 수정본 실행 identity 연결 부족 | E06,E07 | 계약 확인됨, 통합 미입증 / 높음 |
| AF-04 동작 반례와 검증 누락 | E08 | 이전 정적 감사 확인됨 / 높음 |
| AF-05 native/UI 결과 품질 | E08,E13,E20 | 산출물 확인됨, 현재 화면 미확인 / 높음 |
| AF-06 결과와 활동·완료 범위 불일치 | E02,E15,E21 | 높은 가능성 / 높음 |
| AF-07 CI 행동 검증과 기대값 정합성 | E04,E09,C07,C08 | 확인됨 / 높음 |
| AF-08 provider credential 평문 저장 | E10 | 확인됨 / 높음; 실제 유출 주장 아님 |
| AF-09 runtime/외부 접근 불확실성 | E16,E17,E20 | 확인됨/미확인 구분 / 높음 |
| AF-10 반복 통합 수정·긴 handoff | E14,E15,E18,E21 | 반복 확인됨, 인과는 높은 가능성 |
| AF-11 provider/장문 지원 주장 미검증 | E12 | 설정 확인됨, 실제 능력 미확인 |
| AF-12 데이터 수명주기 정책 미결 | E02,E11,E19 | 문서 미결 확인됨 / 중간 |
| AF-13 IR/native 외부 변경 일치 경계 | E06,E07,이전 반대 검토R4 | 설계 위험 / 중간; 손실 사고 미입증 |
| AF-14 release/CLI/라이선스 미완료 | E03,E19 | 일부 코드·미결 문서 확인됨 / 높음 |
| AF-15 성능/복구 인수 범위 부족 | E11,E12,E17 | 측정 부족 / 중간; 성능 결함 단정 아님 |

## 감사 완료 검증

2026-09-20 주 감사자의 **최초 감사 종료 시점** 검증. 아래 수치와 상태는 그 시점 기록이며 후속 문서 변경은 아래 별도 절에 기록한다. 제품 행동 테스트를 새로 통과했다는 의미가 아니다.

| 검사 | 방법 / 결과 |
|---|---|
| 필수 산출물 | PowerShell 파일 존재 검사 exit0; 00~10 필수11개 모두 존재 |
| 요구 추적 | PRD의 정규식 ID 추출27개 전부 요구 표 A/B에 각각1회, 사용자 추가6개; 총33개 |
| 평가/판정 범위 | 품질24영역, 원인A~P 16분류, 핵심 질문12개, 감사 완료 조건17개 명시 |
| Finding | 03/05 상세 카드15개, 고유 ID15개, evidence 연결 표15개 |
| 작업 필드 | 24개 카드의 우선순위/크기·문제/근거/결과·포함/제외·모듈·선행/후속·병렬·완료/검증·rollback·사용자 결정 존재 검사 exit0 |
| 작업 규모/수 | P0=5/P1=14/P2=3/P3=2; L/XL 없음, 미정의 RB ID 없음 |
| 의존성 | 카드의 선행 조건을 명시 graph로 옮겨 위상 정렬,24개 모두 정렬됨; 순환 없음. 최종 인수 gate와 초기 실행 선행 조건은 구분 |
| 로컬 링크 | 새 패킷37개 + 수정한 handoff/계획/인덱스의 새 시작 절24개, 합계61개 경로 존재; 깨진 링크0. 과거 보존 절 전체의 외부 URL 생존 검사는 아님 |
| 문서 형식 | 새11개 파일 표 열 수/행말 공백 검사 exit0, 오류0 |
| Git diff | 정확한 네 repo에서 `git -c safe.directory=<repo> -C <repo> diff --check`, 각 exit0. Platform CRLF 전환 경고는 있음, 파일을 변환하지 않음 |
| HEAD/변경 범위 | E01의 네 HEAD/브랜치 동일. Platform tracked 변경은 최초와 같은 docs4개, 새 audit11개. 다른3개 repo clean. 루트 HANDOFF는 non-Git 문서로 갱신 |
| 사용자 작업 보존 | 기존 review3개 SHA-256이 E01과 일치. 초기 CODING_AGENT_BENCHMARK 변경은 그대로 두고 이번에 편집하지 않음 |
| 제품 코드 표본 | 아래4개 SHA-256 최초/최종 일치. tracked source diff 없음 |
| 수행하지 않은 것 | 테스트 suite/빌드/모델·native 기동/DB 변경/의존성/삭제/배포/commit/push/외부 이슈 생성/새 구현 없음 |

표본 hash:

| 파일 | SHA-256 |
|---|---|
| `gidong-platform/host/src/controlstudio_host/model_connection.py` | `ED81C12CFAA178EDF7EEE45CD7441113CD26A7F360BDB048AA20B83BADA0611C` |
| `gidong-platform/.github/workflows/quality.yml` | `63A3C46F712F87B9C6C22634155E323B922CB909BE80A174169B2A43C357BC08` |
| `gidong-agent/src/controlstudio_agent/runtime.py` | `CE92ED33225BA5CDDA8804D392382C0859349490992C11E45A6404C2C7FC4D67` |
| `gidong-agent/tests/test_workspace_stable_workbench.py` | `91A45806894D9841FBE7890899472B8210A316FA330E5773EDB9A1B455DC3EB9` |

최종 status에도 기존 pytest 임시 디렉터리 권한 경고가 있어 그 내부는 미확인이다. 이것을 해소하려 권한을 변경하거나 삭제하지 않았다. 원격 중복/PR/CI, 현재 UI/native 실행, 과거 전체 formal Goal에 관한 한계도 그대로 남는다. 이 한계를 숨기지 않은 상태에서 **감사 문서·복구 백로그·핸드오프 작성 Goal만 종료**한다. 백로그를 실행하기 위한 제품 구현 Goal은 별도다.

## 후속 지시 반영 — 구조 결함 제거를 필수화

사용자: “애매하게 남기지 말고 구조적으로 문제 있는 것은 확실히 쳐내게.” 기존 E04~E10 근거를 바꾸지 않고, 07에 SC-01~06의 제거 대상/대체 구조/잔존 금지/완료 증거를 명시했다. 08/09 및 HANDOFF/계획/인덱스에 같은 종료 조건을 연결했다. RB-06/07/08/10/11을 P0로 올려 현재24개 작업은 **P0 10/P1 9/P2 3/P3 2**다. 최초 감사의 우선순위 수치는 위에 역사 기록으로 보존했다.

미입증 통합·실기 상태를 확인됨으로 바꾸지 않았다. 특히 SC-03은 수정 source의 실제 적용·시험 연결을 요구하며, immutable resume을 삭제하거나 이미 receipt 위조 사고가 있었다고 주장하지 않는다. 기존 데이터·권한·vendor legality 보호와 결함 코드 보존을 분리했다. 현재 요청도 문서 수정에 한정하며 프로덕션 코드는 수정하지 않는다.

후속 검사: SC 항목6개, 작업24개와 우선순위10/9/3/2 일치. 감사 문서의 로컬 링크40개와 fragment, 표 열 수/행말 공백 오류0. Platform `git diff --check` exit0, tracked 변경은 기존 docs4개뿐이다. model_connection.py/quality.yml/runtime.py의 hash도 위 기준과 일치했다. 새 native/모델/테스트 suite 실행은 없으며, 필수 제거의 **구현 완료**를 주장하지 않는다.
