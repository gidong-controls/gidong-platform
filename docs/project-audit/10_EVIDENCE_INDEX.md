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
