# Development handoff

이 문서는 새 세션이 현재 `dev` 상태에서 바로 이어가기 위한 운영 기준이다. 제품 요구의
최상위 권위는 [PRD](../PRD.md)이며, 이 문서는 구현 상태와 다음 검증 순서만 기록한다.

코딩 에이전트 제품과 PLC 커뮤니티 벤치마킹에서 도출한 작업·실기 평가 기준은
[코딩 에이전트·PLC 엔지니어링 벤치마크](CODING_AGENT_BENCHMARK.md)의 CA-01~CA-12를
사용한다. 일반 SWE-bench나 모델 점수는 XG5000 변경 성공 증거를 대신하지 않는다.

## 저장소와 브랜치

- 세 core 저장소 `gidong-platform`, `gidong-agent`, `gidong-plc-mcp`는 `dev`에서 작업한다.
  `6d06754`, `f70d30c`, `9edc7e5`는 이 handoff 작성 당시의 역사적 code checkpoint이며 최신
  `HEAD` 표기가 아니다. 현재 제품 checkpoint와 실행 상태는 아래 2026-09-01 절을 우선한다.
- 독립 XG5000 CLI는 `main`의 `6a21db7`이며 사용자 지정 원격 `origin/main`과 동기화돼 있다.
  위 세 core 저장소의 이번 변경은 local commit이며 push하지 않았다.

2026-08-16 10:20 KST의 GitHub 443 거부와 당시 push 재시도는 역사적 기록이다. 이후
push 여부는 사용자별 권한과 원격 상태를 확인한 뒤에만 결정하며, 다음 세션의 자동 작업이 아니다.

## 2026-09-03 실행 복구 — 전체 제품 완료 아님

- `elevated-resume28-certification-receipt.json`의 실패는 UAC 거부가 아니라 이미 종료된
  XG5000 PID를 wrapper가 요구한 것이었다. 현재 wrapper는 정확히 빈 XG5000 baseline을
  확인한다. 실행 전 현재 프로세스를 확인하며 과거 PID를 현재 상태로 간주하지 않는다.
- MCP `30a461e`와 Platform `b104e2d`는 launcher의 native-file proof와 official Open
  receipt proof를 각 소비자가 구분하도록 수정했다. official receipt를 native file-use
  증거라고 표시하지 않는다. 인증기의 attach는 별도로 native identity를 다시 수집한다.
- MCP `5eca15f`/`90e4340`는 인증기 최초 source의 bare 실행과 독립 fresh reopen의
  exact-path positional 실행을 구분한다. PID/HWND/create-time, 제목, 파일 사용, hash,
  workspace/marker/backup 검증은 유지한다.
- resume29는 승인 후 실행됐지만 vendor Open 명령 전 창 조회 중 8초 deadline을 소진했다.
  MCP `8d23686`는 Open 준비의 중복 창 조회를 제거했다. `83d3e19`/`52035ea`는 실제
  실패 단계와 vendor command dispatch 수를 오류에 남긴다. 과거의 `official Open`
  오류 문구만으로 실제 명령이 전달됐다고 판단해서는 안 된다.
- resume31은 vendor Open 뒤 `XG5000_OPEN_DIALOG_NOT_FOUND`로 중단됐다. bounded evidence의
  candidate 수는 189이고 선택된 창은 HWND `199222`, 빈 제목의 `BCGPShadowSideWnd`였다.
  이는 Open dialog가 아니라 장식 shadow 후보가 선택된 사례다. semantic dialog wait 수정은
  진행 중이며, 이 기록은 live 인증 성공이나 수정 검증 성공을 뜻하지 않는다. resume32는
  동일 scratch hash와 exact empty XG5000 baseline을 전제로 실행 없이 준비만 됐다.
- 2026-09-03의 resume33/34 certification wrapper 관측(예: pre-click
  `XG5000_SEND_INPUT_FOREGROUND_REQUIRED`)은 이전 인증 재시도 기록이며 현재 장애 상태가 아니다.
- 현재 정상 Development Host health는 ready다. active source-linked CLI의 일반 open/resume
  official Open 연결은 `0829d35`에 반영됐고, independent 경로 보존 후속만 진행 중이다.
- 실제 PWA resume은 아직 수행하지 않았다. 사용자에게 요청된 “이 작업 계속하기” 1회 클릭은
  UAC 요청이나 인증 wrapper 재실행이 아니다.
- 2026-09-03 사용자 지시로 프로젝트 테스트 코드는 삭제됐다. 이 문서의 날짜가 붙은 과거
  test/receipt 기록은 당시 사실일 뿐 현재 test suite의 존재나 통과 상태를 뜻하지 않는다.
  실제 LD 변경·검사·저장·독립 재열기와 PWA 사용자 흐름은 아직 성공으로 판정하지 않는다.
- 정상 개발 제품 경로는 `Start-ControlStudio-Development.ps1` → persistent elevated Host →
  Agent/MCP → CLI다. MNE certification wrapper와 root Computer Use는 이 Host/PWA 기동의
  선행조건이나 runtime 의존성이 아니다.
- UAC는 한 번 요청하고, Windows가 명시적으로 사용자 취소를 반환하면 사용자 재요청까지
  기다린다. 승인 후 내부 실행 실패는 권한 거부로 보고하지 않으며 원인을 수정한다.
  물리 PLC는 장비 확보 후 로드맵이고 현재 검증 대상이 아니다.

## 2026-09-01 필수 읽기 경로 — 현재 진행

- 실제 프로그램 전면화 vertical이 완료됐다. 독립 CLI `52a3130`은 `focus-program`의
  UTF-8 JSON wire, exact PID/HWND/process lifetime, 프로그램 ID/name, 프로젝트 경로와
  SHA 전후 불변을 소유하며 `origin/main`에 push됐다. MCP `df64ee7`은 `program.focus`와
  `program.focus_location`의 대상 선택을 이 CLI catalog에 결속했고, `54d7349`는 승인된 LD
  변경의 diff/apply/validate/rollback도 같은 CLI catalog/read/apply 경계로 통일했다. Agent
  `cd1b554`는 명시적인 현재 XG5000 LD 열기 요청을 모델 선택에 맡기지 않고
  `project.list_programs` → catalog의 유일한 canonical ID → `program.focus` → verified receipt
  완료로 진행한다. 튜토리얼과 수정·저장·실행 요청은 이 읽기 전용 경로에서 제외한다.
- 실제 제품 conversation `ed6a0765-4870-4d2a-b6b0-b7d8b5828f79`, run
  `b4d8fb70-99b1-4750-8980-1cd4991ed205`는 PID `16228`/HWND `723394`에서 프로그램 목록과
  포커스를 각각 한 번만 실행하고 `COMPLETED`됐다. focus evidence는
  `7913a038-ae8e-5227-abd0-7a290c943f35`이며 artifact는
  `artifacts/change-mutation-e2e-localappdata/ControlStudio Development/artifacts/mcp-sessions/541aa48b-fd96-4187-9a0c-ac02bf1accda/27099419eee3/result.json`이다.
  `MainProgram`/`ld:55e388e7aea7a8ce`, exact program active, exact foreground, read-only,
  project unchanged가 모두 true이고 SHA-256 전후는
  `2d55b3489f0f1a97666e79d00bd4ef6962d2e2e71fa44839193be04240308b48`로 동일하다.
  PWA 작업 기록에서 이 완료 run과 프로그램 1개를 실제로 다시 열어 확인했다. 작업 기록에서
  현재 target과 일치하는 engineering conversation을 선택해도 시작 화면에 남던 상태 버그도
  수정해 해당 작업 화면으로 즉시 복귀한다.
- 최신 deterministic receipt는
  `artifacts/plc-agent-acceptance-agent-focus-final/run-3i6hmlyv/receipt.json`이며 manifest SHA-256은
  `bfcb8f3528932e4d2b7d3e788da1da71334ebea886e387a1f8c5dea082f70269`다. 새 Agent focus
  regression을 단일 manifest에 포함한 상태에서 Agent·Host·MCP·독립 CLI·Web build 5개
  group이 모두 `PASS`다. 외부 live-gate 파일을 주입하지 않았으므로 overall은 의도대로
  `FAIL_CLOSED`이며, 물리 PLC는 장비 확보 뒤 PRD G3/G4 로드맵으로 유지한다. XG-SIM
  quarantine은 재시도하거나 해제하지 않았다.
- CLI `f2a096f`와 문서 `6a21db7`은 production `project.list_programs`의 vendor core를
  독립 저장소로 옮겼다. `list_programs_entry.py`는 shared canonical UTF-8 JSON wire,
  exact PID/HWND, XG5000 title과 process lifetime, project path/SHA를 시작·종료에 결속하고,
  `vendor/program_catalog.py`가 bounded XGWX gzip/XML에서 LD program과 stable ID를 읽는다.
  기존 8-pixel project-grid sweep은 이 production 경로에서 사용하지 않는다. MCP `b96586b`는
  in-process structure catalog 대신 이 CLI를 호출하고 source/digest/program identity를 다시
  검증한다. 실제 PID `16228`/HWND `723394`에서 `MainProgram`과
  `ld:55e388e7aea7a8ce`, unchanged SHA `2d55b348...08b48`를 반환했다. CLI 전체 121개,
  MCP CLI/server boundary 112개가 통과했다.
- 새 deterministic artifact는
  `artifacts/plc-agent-acceptance-cli-owned-catalog/run-7f9t2xt4/receipt.json`, manifest
  SHA-256은 `06503a913f3b0542cddbe0d361e721fcda79965c743c8b259bfe0ea6bd3bee15`다.
  Agent·Host·MCP·독립 CLI·Web build 5개 group이 모두 `PASS`이고 CLI group은 93 tests,
  MCP group은 126 tests를 실행했다. 장비/외부 live receipt는 공급하지 않았으므로 overall은
  의도대로 `FAIL_CLOSED`다.
- Agent `b7c063d`는 현재/실제 XG5000의 named LD read 요청을 모델의 임의 initial-tool
  선택에서 제거했다. runtime이 `list_project_programs({})`를 먼저 선택하고, verified catalog의
  stable ID를 기존 deterministic continuation이 `read_ladder_program`에 전달한다. 일반 LD
  tutorial은 이 경로에 들어오지 않고 특정 fixture 이름에도 고정되지 않는다. 전체 Agent 103 tests가
  통과했다. 이 수정 전 실제 run `db4d60d0-7896-4cb9-8ae8-486fd216401d`은 static
  workspace search/structure를 반복하고 `agent_output_contract`로 실패했으며 CLI/XG5000 실패가
  아니었다.
- 정상 Host restart 뒤 direct XG5000 session은 process-lifetime-bound라 자동 복원되지 않는다.
  persisted static workspace SHA `13251e...`와 혼동하지 않도록 exact PID `16228`/HWND `723394`,
  working path/SHA `2d55b348...08b48`로 read-only attach를 다시 검증했다. 새 conversation
  `5f3fc135-54a0-448a-aa7a-04864ef731e3`, run
  `ecb0ee66-560d-4640-a6aa-14a603d3fd34`는 27초 안에 `list_project_programs` 1회와
  `read_ladder_program` 1회만 실행하고 `COMPLETED`됐다. list artifact
  `52265d1f.../ec32ddb5c6b3/result.json`은 `catalog_source=sha_bound_project_xml`,
  `live_editor_catalog=false`, program `ld:55e388e7aea7a8ce`, project SHA 전후 동일을 기록한다.
  LD artifact `52265d1f.../e8a71258b2b3/result.json`과 evidence
  `70964455-e837-58c8-8a6f-7d39705bb07d`는 payload SHA
  `aca8fb5f154b49ae9431df075efee8971610fd83dca1ebba65821e6be7795bd7`와 같은 project SHA를
  결속한다. 최종 답변은 `조건식: M00001 AND (M00000 OR M00010), 출력: M00010`이다.
- Agent route 수정 뒤 통합 artifact는
  `artifacts/plc-agent-acceptance-exact-ld-routing/run-ar8dko26/receipt.json`이다. 같은 manifest
  SHA `06503a913f3b0542cddbe0d361e721fcda79965c743c8b259bfe0ea6bd3bee15`에서 5개 deterministic
  group이 모두 `PASS`이며 Agent group은 79, MCP group은 126, CLI group은 93 tests다.
  external live-gate receipt는 공급하지 않았으므로 overall은 계속 `FAIL_CLOSED`다.

- CLI `067f4bb`은 실제 `read-ld` 생산 진입점의 전체 runtime closure를 독립 저장소가
  소유하게 했다. `read_ld_entry.py`가 canonical UTF-8 request, exact PID/HWND, project
  path/SHA, stable program ID/name, operation-local private payload handoff를 검증하고,
  CLI-owned decoder가 native payload와 semantic rung을 만든다. editor-open, exact
  project-tree/MDI selection, cancel-only dialog guard, raw clipboard transaction/capture,
  UIA/Win32 input/message substrate도 CLI-owned다. subprocess import guard는 이 closure가
  `xg5000_control`을 하나도 load하지 않음을 검증한다. CLI 전체 112개와 MCP CLI/server
  boundary 112개가 통과했다. 다른 compatibility command의 vendor core까지 전부 추출됐다는
  뜻은 아니다. `wire.py` 한 곳이 legacy와 fast entry의 UTF-8 canonical JSON,
  duplicate-key/NaN 거절, 크기 제한을 모두 소유한다. 초기 entry/shared wire는 `7a456a1`,
  stable program ID는 `07d1944`, 전체 LD read workflow는 `067f4bb`로 `origin/main`에 push됐다.
- `067f4bb` 소스 상태에서 실제 제품 conversation
  `2ed7ebb5-344c-4a69-944e-7aa3a73b4fec`의 run
  `fb410c7f-6cda-4c03-adb7-f18ba42a61ae`가 `COMPLETED`됐다. Agent는
  `project.list_programs` 1회와 `program.read_ld` 1회만 호출했고 PID `16228`/HWND
  `723394`, program `ld:55e388e7aea7a8ce`에서 evidence
  `5deb704b-8167-57bb-a11c-7c4d343b600b`를 만들었다. payload SHA-256은
  `aca8fb5f154b49ae9431df075efee8971610fd83dca1ebba65821e6be7795bd7`, project SHA-256은
  전후 `2d55b3489f0f1a97666e79d00bd4ef6962d2e2e71fa44839193be04240308b48`로 동일하며
  답변은 `조건식: M00001 AND (M00000 OR M00010), 출력: M00010`이다.
- 실제 제품 conversation `2ed7ebb5-344c-4a69-944e-7aa3a73b4fec`의 새 run
  `f04421bf-b1e8-4a63-a9ad-125dda7a31a9`은 새 좁은 CLI entry를 통해
  `read_ladder_program`을 1회 실행하고 `COMPLETED`됐다. PID `16228`/HWND `723394`, program
  `ld:55e388e7aea7a8ce`에서 evidence `b1393d42-fb86-5e11-89c4-63f088036577`를 만들었고 답변은
  `조건식: M00001 AND (M00000 OR M00010), 출력: M00010`이다. 앞선 run
  `0d62ff8f-660d-49bc-8d5f-60db0c9b58f6`은 새 entry의 한글 경로 JSON canonicalization 차이로
  실패했으며, 어댑터와 같은 `ensure_ascii=false` 규약으로 수정한 뒤 위 run에서 해결했다.
  비상승 terminal 직접 호출의 `win32_error=5`는 재시도하지 않았고, 이미 권한을 유지한 Host에서
  실제 사용자 경로만 검증했다. 새 UAC 요청은 없었다.
  공용 wire 리팩터 뒤의 마지막 Agent run
  `c1434d49-068a-4f3c-a6c0-d01e54a06ddf`은 첫 read-only session-status 뒤 모델 제공자
  HTTP 502로 종료됐으며 `program.read_ld`를 호출하지 않았다. 따라서 CLI 실패나 XG5000 동작
  증거로 해석하지 않고 같은 실기 요청을 반복하지 않았다.
- 갱신된 단일 acceptance manifest는 CLI narrow-entry와 shared-wire 회귀까지 포함한다. deterministic 전체
  실행 artifact는 `artifacts/plc-agent-acceptance-shared-wire/run-xdarfcco/receipt.json`,
  manifest SHA-256은 `3a4cce5bccac943d05be4b50559201cf3dc40109b18d595ed75be366609581ef`다.
  다섯 command group이 모두 `PASS`이고 CLI group은 66 tests를 실행했다. 외부 live receipt를
  이 deterministic run에 주입하지 않았으므로 overall은 의도대로 `FAIL_CLOSED`이며, 위 실제 run을
  가짜 CA-02 gate 파일로 승격하지 않는다.
- 최신 완료: MCP `404848a`는 UIA worker의 중복 `native_menu` fallback 순환을 제거했고,
  CLI `ea18481`은 실제 UIA/MSAA helper child를 CLI-owned vendor namespace로 옮겼다. MCP
  `1cf37e4`/`ccb2fb2`는 helper 구현을 삭제하고 부모 subprocess transport/public wrapper만
  유지하며, 직접 MCP read도 configured CLI helper를 사용한다. CLI 전체 89개, MCP 전체 578개가
  통과했고 CLI helper probe가 PID `14284`/HWND `329768`에서 성공했다. 제품 재시작 후 정확한
  working-copy PID `16228`/HWND `723394`를 session
  `c21c301e-ac0b-450f-96ca-0e1d4b760772`로 다시 attach했다.
- Agent `6c186db`는 확인된 `project.list_programs` 결과에서 정확한 LD 하나를 고른 뒤
  `read_ladder_program`으로 넘어가는 read-only continuation과, 한 개의 Host-decoded semantic rung에서
  조건식/출력을 완료하는 경로를 deterministic하게 처리한다. 전체 Agent 102개와 64 subtests가
  통과했다. 실제 conversation `2ed7ebb5-344c-4a69-944e-7aa3a73b4fec`, run
  `40e95d4b-7a16-4ef8-acd9-35a179e47ae8`은 목록 evidence
  `da27787f-2727-5470-a70b-b59a00689923`, LD evidence
  `8dbea3f9-d4ed-52a6-915d-ca5c6e966caf`을 각각 한 번 생성하고 `COMPLETED`됐다. 답변은
  `조건식: M00001 AND (M00000 OR M00010), 출력: M00010`이며 프로젝트 SHA는 전후 동일하다.
  이전 `a49dc6c6`/`4d55d598`/`fae53c6f`의 `continuation_contract` 실패는 이 경로에서 해결됐다.
- 별도 CLI 문서 commit `6e6694b`/`64f1f75`도 지정 원격 main에 push됐다. 전체 vendor-core 추출은 아직
  완료가 아니며 남은 menu/resource/toolbar/input/dialog/workflow 구현은 command vertical별로
  이동한다. 물리 PLC I/O는 장비가 있을 때의 PRD G3/G4 로드맵으로 유지한다. 과거 XG-SIM
  `QUARANTINED` 상태는 재시도하거나 해제하지 않았다.
- 실제 PWA 데이터/Agent 경로는 위 run으로 완료됐다. 첫 수동 Launcher 진단의
  `launch challenge proof did not match`는 격리 Development `LOCALAPPDATA`를 지정하지 않고 기본
  사용자 루트의 다른 token을 읽은 호출자 오류였다. 정확한 격리 루트의 `--no-open` 검증은 즉시
  통과했다. 뒤의 UI 호출은 기존 ControlStudio 창을 복원했지만 Windows foreground 전환이 거부돼
  한 번만 실패했으며 재시도하지 않았다. 오래된 인앱 탭은 여전히 인증 만료 화면이므로 Agent/CLI
  성공과 구분하고, 이를 이유로 LD read를 반복하거나 UAC를 요청하지 않는다.
- 정상 Host 재시작은 UAC 없이 완료됐다. 동일 XG5000 PID `16228`/HWND `723394`, working project
  SHA `2d55b348...b48`에 read-only session
  `99b79306-8631-45bf-80d6-5ccd143874cc`이 연결돼 있다.
- 실제 conversation `fbfd967e-c5ef-4c71-a5f1-645e21eb4259`의 run
  `8b0b7c95-c991-4271-9fa1-d9803a131b48`은 `build_program_logic_graph`와 evidence
  `4a88026d-06e2-5a4f-a1db-879d3025c311`을 남기고 `COMPLETED`됐다. 모델이 답한 논리식은
  `M00001 AND (M00000 OR M00010)`이며, M00001 OFF일 때 자기유지가 중단되고 M00010 OFF까지의
  동작을 정확히 설명했다. 같은 대화의 자연 후속 질문 run
  `85fe3b2e-98fa-45ca-94e1-432d0d949234`은 `tool.program.read_ld`에서 실패했다. 원인은
  SHA-bound structure가 LD program을 해석하지 못한 것으로, interface가 executable LD ID 대신
  `code_unit_id`만 제시한 것이었다.
- 현재 canonical ID 경로는 MCP `2ad7b7c`의 `canonical_program_id` → Host 기존 receipt의
  `structureFacts[].attributes`에 있는 `{name: canonicalProgramId, value: ld:…}` → Agent다.
  중간 구현 `a153d39`/`83c1f1c`/`7dedf39`의 별도 structure modelContext 전달은 아래 cleanup에서
  제거됐다. analysis-only read attach와 완료 미션 뒤 같은 대화의 새 분석 objective는 구현됐으며
  mutation 권한을 열지 않는다.
- 자연 후속 검증 conversation `623562a2-3c41-4083-934e-f7ee289c70a3`의 run
  `06582be7-94e0-4acd-8170-c43f634def62`은 `programlist`와 `read_ladder_program`을 모두
  자율적으로 성공했다. artifact `7c5a031e.../ca6c24cfb9e8/result.json`의 decoded semantic rung은
  정확히 `M00001 AND (M00000 OR M00010)`이다. evidence
  `f2f43294-d19f-5bc8-a849-d810eb1837c0`을 남긴 뒤 `COMPLETED`됐고, 답변은 M00010이 출력이며
  M00001 ON일 때만 self-hold하고 M00001 OFF이면 M00010 OFF라고 정확히 설명했다. 이 run은
  `programlist`를 사용했으므로 canonical structure 경로의 실제 검증을 뜻하지는 않는다. session
  `7c5a031e-8e34-4f80-9b09-888b13c3971d`은 같은 PID/HWND와 project SHA를 사용한다.
- 같은 대화의 명시적 tool name/ID 없는 동일 질문은 run
  `a318e924-a1a9-4c7f-aebc-2b85c593e2b4`에서 `search_project_evidence`와
  `read_ladder_program`을 모두 성공했다. evidence
  `d8ec0c6e-337f-5540-9e09-4fb5a2471495`를 남긴 뒤 `COMPLETED`됐고, 답변은
  `M00001 AND (M00000 OR M00010)` 및 M00001 OFF이면 전체 AND가 false여서 M00010 OFF라고
  정확히 설명했다. artifact `f48e.../0dc77fef613a/result.json`은 `verified=true`,
  `project_unchanged=true`다.
- structure-only run `9f983318-fc0b-4ba5-b160-8e642cebabba`는
  `inspect_project_structure` 성공 뒤 `agent_runtime_runtime_invariant`로 실패했다. 기존 receipt의
  `structureFacts.canonicalProgramId`는 보존돼 있었지만, 중복 model context projection이 raw digest를
  projection digest와 비교한 것이 원인이었다. Platform `dc7661a`/Agent `9b0b53b`는 중복 structure
  modelContext/overlay를 제거하고 기존 receipt `structureFacts[].attributes.canonicalProgramId`만
  사용한다. graph context는 유지하며, 저장된 `9f983` receipt replay는 `has_model_context=false`로
  통과했다. structure-only 재시도 run `918fa80b-07ed-471a-90e3-d9d58b574ff8`은
  `inspect_project_structure`를 성공하고 evidence `8fed4083-1c87-5a13-a840-174ace315fd9`를 남긴
  뒤 `COMPLETED`됐다. 답변은 structure entity ID
  `xg5000:code-unit:1449fa43-e9fb-4799-981c-54a82bcbdb63`와 LD `canonicalProgramId`
  `ld:55e388e7aea7a8ce`를 구분하고 `program.read_ld`에는 canonical ID를 써야 함을 정확히 설명했다.
- 최신 focused regression 수치는 위 최신 완료 절을 우선한다. 이 아래의 Agent 80개, MCP 76개,
  CLI 74개 기록은 이전 체크포인트다.
- 물리 PLC를 현재 범위에서 제외한 뒤 CLI vendor-core 추출을 재개했다. CLI `74826a2`는 LD 읽기의
  cross-process OLE clipboard helper를 CLI-owned vendor namespace로 옮겼고 지정 원격 main에
  push했다. MCP `9171433`은 같은 helper 구현과 MCP server의 helper entry를 삭제하고 부모 JSON
  프로토콜만 유지한다. CLI 전체 78개와 MCP clipboard/server boundary 88개 테스트가 통과했다.
  실제 conversation `00691471-1de0-43d5-b0f4-d3f3f57679c2`의 run
  `f7bc2d96-235b-4924-b181-73c10930510c`은 새 helper 경로에서 LD 읽기를 성공하고 조건식
  `M00001 AND (M00000 OR M00010)`과 출력 `M00010`을 답해 `COMPLETED`됐다. 전체 vendor-core
  추출 완료는 아니다. 이후 UIA/MSAA helper child 추출과 중복 fallback 제거는 위 최신 완료 절에서
  끝났고, 남은 menu/resource/toolbar/input/dialog/workflow 구현은 계속 transitional이다.
- 별도 conversation `35fdea15-3dc2-4145-889b-063807425b31`의 일반 요청 `54a7482d`와 뒤의
  세 실제 run은 프로그램 목록 읽기 뒤 `continuation_contract`로 실패했다. 원인은 compact model
  continuation에 고정 read-only 다음 단계를 맡긴 것이었고 Agent `6c186db`와 위 실제 완료 run으로
  해결했다. event `2693`은 과거 실패 뒤 evidence 보존 checkpoint로만 남긴다.
- 과거 XG-SIM trial은 `QUARANTINED`, engineered readiness는 false로 보존된다. XG-SIM
  acceptance와 전체 CLI vendor-core 독립은 아직 완료되지 않았다. 물리 PLC I/O는 현재 장비가
  없어 현재 완료 범위와 blocker에서 제외하고 PRD G3/G4 로드맵으로 이관했다. 별도 복구 기능은
  사용자 지시로 제외한다.

## 2026-08-31 21:57 KST native resume: 독립 재열기 검증 완료, XG-SIM 실패 기록

- 22:29 KST 반영 확인: MCP `189330e`는 raw/wrapped UIA 전달 상태 중 하나라도 `unknown`이면
  무실행 실패로 분류하지 않는다. 경계 테스트 67개, runtime 관련 103개, Host 관련 28개가
  통과했다. 정상 Host restart 한 번이 UAC 없이 `ready`로 끝났고, 이후 GET에서 아래 reopen
  성공·SIM 격리·미완료 상태와 기존 두 XG5000 PID가 그대로 유지됨을 확인했다. 새 SIM 실행은 없다.
- 원래 native operation `1dc48ccb-a0d8-44d1-9665-bb101dc7c64c`의 LD 입력, MainRes command
  `876` Program Check(0 errors), 저장은 이미 `APPLIED_VERIFIED`로 완료됐다. 새 프로젝트 생성,
  IL 재입력, 원래 검사·저장을 반복하지 않는다. workspace
  `9674be8c-cbea-4dd1-8d4d-a603280d3569` source/working hash와 저장된 프로젝트 hash는 모두
  `2d55b3489f0f1a97666e79d00bd4ef6962d2e2e71fa44839193be04240308b48`다.
- `17e855a` 뒤 fresh working instance PID `16228`/HWND `723394`의 MainProgram editor HWND
  `8390818`를 열었다. CLI `reopen-readback`은 첫 6개 MNE와 expected source SHA-256
  `74ff83178d6aa0319631874eed5ffd1d973fa326d8e4c656172ecf6ec9f534d0`를 실제 확인했다. 원래
  PID `14284`/HWND `329768`와 작업 복사본은 계속 보존된다.
- CLI `23830c4`가 MNE 읽기 뒤 LD 보기로 복귀하도록 수정된 후 21:57:06에 독립 복사본의
  MNE·LD·Program Check·파일 해시 검증이 모두 통과했다. reopen은 `verified`이며 성공 artifact는
  `993f8d91-daa5-4d2c-a77d-112526ac8436/71219768c132`다. 원본을 닫거나 다시 저장하지 않았다.
- 이어진 첫 XG-SIM 시도는 21:57:45에 `XG5000_UIA_TIMEOUT`으로 실패했다. 같은 session의
  `b103aec2dba7`가 실패 artifact다. trial `1c0f2380-8e1b-545a-ae01-09ff8a8b6e83`는
  `QUARANTINED`, `safeRestorationVerified=false`이며 `engineeredProgramReady=false`,
  `workspaceBound=false`다. 실제 명령 전달 단계는 아직 확인 전이므로 임의 재시도나 DB 격리
  해제를 하지 않는다. 21:59 XimUtil/XGSim 프로세스가 없었다는 조회만으로 과거 무동작을
  단정하지 않는다. 물리 I/O·최종 PWA acceptance는 미실시이고 이 구간 UAC 요청은 없었다.
- 알려진 제한: 현재 create/resume handler는 independent reopen과 XG-SIM acceptance를 함께
  요구한 뒤에만 active engineering session/workspace를 bind하므로, reopen이 검증됐어도
  quarantined SIM 중에는 Agent의 read/analyze 대상도 노출되지 않는다. 저장된 reopen session을
  그대로 bind하면 controlled-launch proof가 mutation-ready로도 해석될 수 있어 허용할 수 없다.
  향후에는 새 PID/HWND/lifetime을 다시 확인하는 명시적 analysis-only attach를 read-ready로만
  bind하고 mutation readiness는 닫는 경로로 분리한다. 이 문서상 quarantine은 해제하지 않으며
  GET/read 경로가 그 상태를 바꾸어서는 안 된다.
- 사용자 지시로 별도 복사본 초기화/복구 API는 만들지 않는다. 필수 작업은 기존 Agent의
  프로젝트 읽기 연결과 CLI/MCP 상태 조회, LD 작성·검사·저장·시뮬레이션이다. 실패 기록을
  삭제하거나 창 재열기를 과거 시뮬레이션 복원 성공으로 처리하지 않는다.
- MCP `c4ff276`은 XG-SIM 시작 상태 증명을 보완했고, `a16c137`/Platform `614d747`은 단계별
  native simulation 실패 증거를 분류했다. 해당 focused test는 통과했지만 실제 XG-SIM 재시도는
  아직 하지 않았다. `dd4582a`의 SHA-bound ID→exact-name 경로는 유지한다. semantic legacy
  catalog-sweep 교체 WIP는 폐기했으며, genuinely unnamed legacy sweep은 여전히 알려진 debt다.
  compact XGK 일반 수정 `476cdb9`/`b4738a8`은 완료됐지만 persisted actual plan은 이미
  full-width 주소를 사용하므로 이번 blocker가 아니다. root architecture README `d615174`도
  반영됐다. CLI `e735242`까지 지정 원격 main에 게시됐고 MCP·Platform checkpoint는 local이다.
  전체 CLI vendor-core 독립은 아직 완료되지 않았다.

## 2026-08-31 18:39 KST 재개 경로 구현·실행 중 — 이전 관측

- 기존 입력 LD/PID `14284`/HWND `329768`와 operation
  `1dc48ccb-a0d8-44d1-9665-bb101dc7c64c`를 계속 사용한다. 새 프로젝트 생성, IL 재입력,
  UAC 재요청, 물리 PLC 쓰기는 하지 않았다. 아래 18:14 상태의 최종 저장·재열기·XG-SIM
  완료는 아직 확인되지 않았다.
- Host `0ad8c12`/`15bba74`, MCP `f2379d2`, CLI `27c9046`으로 중단된 native build를
  재개하는 경로를 연결했고, Web `3fbfa84`로 **입력된 LD에서 검사·저장 재개**를 추가했다.
  API body는 `{pid, hwnd, expectedRequestDigest}`이며 저장된 계획을 사용한다.
- 실제 API 시도 두 건은 XG5000 명령 전달 전 거부됐다. 첫째는 resume에 다른 잠금 종류를
  지정한 오류였으며 Host `c26df66`이 기존 `native_project_create` 소유권을 그대로 인계한다.
  둘째는 plan digest 계산이 create-only 빈 폴더 조건을 재검사한 오류였다. Host `6ce7f7d`는
  기존 폴더에서도 동일 digest를 계산하며, 새 프로젝트 생성의 빈 폴더 조건은 유지한다.
  이 거부들을 native 동작 완료나 추가 UAC 실패로 기록하지 않는다.
- CLI `262445e`/`4f714f7`은 COM ROT가 없는 경우 기존 SHA-bound 창·파일 사용 관계와
  실제 XML CPU를 읽는다. 실제 `OperationResult` 반환형을 사용하는 pipeline 테스트는
  `1ff31ea`이며 평탄화한 가짜 검사 결과로 성공을 만들지 않는다.
- MCP `9422192`는 `LSPLC(XGB-XBCH)` 고정을 제거했다. 현재 파일의 configuration 이름을
  읽어 check/create/resume/reopen에 동일하게 전달한다. 실제 fixture 이름은 CPU suffix 없는
  `LSPLC`이며 사용자 변경 이름도 허용한다. CLI 문서 `1a77d01`까지 지정 원격 main에 push했다.
- 마지막 실행 담당은 `cli_runtime_fix`이며, MCP의 같은 plan-digest 수정 확인 뒤 정상 Host
  restart와 동일 operation의 resume를 한 번 실행한다. 중복 live 실행을 하지 않는다.
  결과가 나오면 이 절을 갱신한다. PWA 화면 acceptance는 여전히 수행하지 않았다.

## 2026-08-31 18:14 KST 새 LD 입력 완료·검사 실패 — 이전 관측

- 새 operation `1dc48ccb-a0d8-44d1-9665-bb101dc7c64c`는 실제 프로젝트 생성,
  compiled IL 입력 대조, LD 변환·roundtrip을 지난 뒤 Program Check 창 닫기에서 실패했다.
  현재 `quarantined`/`NATIVE_PROJECT_MCP_REJECTED`이며 전체 생성 완료가 아니다.
- PID `14284`/HWND `329768`에 `LD 오프라인 자기유지 개발 검증 20260831`이 그대로 열려
  있다. 입력된 LD를 닫거나 다시 만들지 않는다. 컴파일된 source SHA-256은
  `74ff83178d6aa0319631874eed5ffd1d973fa326d8e4c656172ecf6ec9f534d0`이다.
- 실패 원인은 `PROGRAM_CHECK_DOCK_CLOSE_LAYOUT_UNVERIFIED`였다. MCP `847a0e7`은
  close 영역의 고정 좌표 폭 대신 실제 native `HTCLOSE` 영역을 사용한다. 단위 테스트는
  통과했으며 이 수정의 새 프로젝트 실기 성공은 아직 확인하지 않았다.
- 기존 reconcile은 결과 확인만 할 뿐 중단된 검사·저장을 재개하지 못한다. 따라서
  Host → MCP → CLI `resume-native`를 연결 중이다. 기존 계획과 현재 프로그램을 대조한
  뒤 검사·저장·workspace capture·독립 재열기·XG-SIM을 잇고, 새 프로젝트 dialog나 IL
  재입력은 하지 않는다. 최초 생성 증거를 만들어 붙이지 않고 실제 resume provenance를 쓴다.
- 이 시점에 최종 저장·독립 재열기·실 XG-SIM·PWA 화면 acceptance·실 PLC I/O는 미완료다.
  이번 재개 작업에서 UAC를 새로 요청하지 않았다. 아래 복구 성공은 이전 프로젝트 기록이다.

## 2026-08-31 17:55 KST 이전 프로젝트 복구 성공

- 사용자의 계속 진행 지시에 따라 검증된 backup 복원 뒤 durable recovery를 정상 완료했다.
  같은 PID `14448`/HWND `329110`에서 reconcile은 `true`, 결과는
  `explicit_recovery_rolled_back`이며 final journal은 `ROLLED_BACK_SAVED`/terminal/
  lease released다. 이는 independent fresh reopen 증거가 아니다.
- journal의 실제 전이는 authorization 소비 뒤 `PLANNED → APPLYING`(seq 6),
  `QUARANTINED`(seq 7), `ROLLED_BACK_EDITOR`(seq 8)였다. 따라서 앞선
  “PLANNED-only/no mutation” 해석은 틀렸고, 안전 abort shortcut을 추가하지 않았다.
- `structural_diff`, `xg5000_program_check`, `project_save`, `project_reopen` 검사가
  모두 완료됐고, 현재 파일 SHA-256은
  `13251e001479190a16b9a88cf42d00b439b65d0c620222872e32bc5ec1ba7568`이다.
  정상 Host attach도 strong readback session
  `4a4ec200-62b2-4dee-a2df-c9b191d4010b`으로 성공했다.
- MCP `022a30e`은 duplicated-selection current envelope의 구조(duplicate selection,
  separator, 길이 28의 byte-significant intermediate header, decoded row stream)를
  확인한 경우에만 두 4-byte instance/process field를 canonicalize한다. 같은 decoded
  program의 raw snapshot 49개가 canonical SHA-256
  `75a1c0f0fd17273b613e1be0fece25d796d414a02348f57ce5bfc56e3a44bc97` 하나로 수렴했다.
  이 header 28 bytes는 zero라고 가정하지 않으며, program/row와 나머지 header는 계속
  byte-significant다.
- reviewed no-save close는 PID 14448에서 verified true로 끝났고 IDNO click/force kill은 없었다
  (receipt digest `1ec1dcd2…`). 이어 정상 Host → MCP → CLI idle launch는 PID 14284/HWND
  329768, lifetime `1788166678.7651005`, digest `a85e6a6ec72000b7b529ddde956fd1e8689c3b10cd51434efda324b97d4283c1`으로 성공했다.
  reviewed native create는 operation `1dc48ccb-a0d8-44d1-9665-bb101dc7c64c`, plan `62af347d…`,
  folder `artifacts/ld-native`로 제출됐다. 이후 실패와 현재 재개 상태는 위 18:14 절을 따른다.

## 2026-08-31 오후 제품 경로 재개 — 이전 관측 기록

- 정상 Launcher handoff → session → bootstrap API로 실행 중인 Development Host에
  인증했다. 브라우저 저장소/토큰 추출이나 차단된 페이지 렌더링은 사용하지 않았다.
  `/api/health`는 ready이며 제품 `/api/v1/xg5000/instances`가 MCP → 독립 CLI를 통해
  실제 XG5000 PID `7024`, HWND `67344`를 반환했다. Codex Computer Use의 실패는
  제품 런타임 실패로 취급하지 않는다. 이 관측은 PWA 화면 acceptance를 뜻하지 않는다.
- 제품 `POST /api/v1/host/restart`를 한 번 실행해 Host `5464`를 `13472`로 교체했고
  restart receipt `ready`, Host/MCP ready를 확인했다. UAC를 호출하지 않았고 XG5000
  PID/HWND는 그대로였다. 이 API는 호출한 세션만 승계하므로 다른 브라우저 세션의
  자동 유지를 주장하지 않는다.
- working copy SHA `f277c7ce606b7b902053b8fe732555288cc81e6c537ee7dafbc51a6bf68d91c2`와
  격리 transaction `f6ecf557-316b-4739-adde-247813330a18`는 유지했다.
  복원/재결속/이전 승인 재사용/PLC 쓰기는 수행하지 않았다.
- 별도 제품 대화 `6bb8a0a4-cf45-4e18-b402-d4b732216723`에 M0000 START,
  M0001 STOP_OK, M0002 RUN_CMD의 오프라인 자기유지 LD 개발 요청을 실제 제출했다.
  run `1ffc9dca-ceda-432e-a7ef-0f7af2fd0a0a`는 narrative 출력 계약 실패;
  초기 상태를 확정한 run `7692bf2d-1db4-4019-be12-227cd3274439`는 native-design
  provider 오류; 갱신 후 run `a76d5314-9674-4d27-a5c2-10436560c91d`는 native-design
  validator 오류였다. 세 실행 모두 실패이며 LD 생성 성공으로 계산하지 않는다.
- Agent `023ef77` / Platform `a7542e6`은 provider HTTP 4xx/5xx 상태만 안전한 오류 코드로
  보존한다. 본문·헤더·비밀값은 전달하지 않는다. 기존 두 실패의 버려진 HTTP 상태는
  복원할 수 없으며 특정 상태였다고 추측하지 않는다. 관련 Agent 4 + Host 2 테스트 통과.
- Agent `c8e4ea5`는 native validation의 원인과 모델 feedback을 보존하며 관련 3건 테스트가
  통과했다. Platform `5fb6a41`은 Web의 경과 시간 표시와 coalescing을 보완했고 Node 테스트
  2건, typecheck 및 build가 통과했다.
- Host `644131f`는 같은 단계의 15초 heartbeat를 Host가 버리던 stage-only 중복
  제거를 `(stage, elapsed_seconds)`로 고쳤다. fake transport 4건 통과 뒤 두 번째 정상
  Host restart에서 이 코드가 UAC 없이 실제로 load됐다. live SSE event `2290`/`2291`은
  `native_design`의 elapsed 15/30초를 각각 보냈으므로, 이 수정은 mock만의 결과가 아니다.
  긴 침묵이 sidecar event-loop 정지라는 초기 가설은 틀렸다. 로컬 fake-model 측정상 native
  schema는 14,106 bytes, 생성 약 19ms, Agent 생성 약 14ms였으며 과도한 schema 준비가
  원인이 아니다.
- MCP `55da905`는 LD source-not-visible의 안전한 숫자 geometry를 전달하고,
  CLI `30c46e3`는 code-prefixed `str(error)` 대신 구조화된 message를 반환한다.
  CLI commit은 사용자 지정 원격 `gidong-controls/controlstudio-ls-xg5000-cli`의
  `main`에 push 완료했다. 세 core 저장소 commit은 local이며 push하지 않았다.

제품 run `967941a3-16cf-42d8-8421-0b9587e3d37f`는 event 2290–2302의 15–195초
heartbeat 이후 event 2304에서 `FAILED/agent_runtime_provider_http_504`로 끝났다.
저장된 진단은 `planning_native_design/model_api_error/provider_http_504`이며, 사용자 메시지
API에서도 HTTP 504 표시를 확인했다. 제품 모델은 `nemotron-3-ultra`, 원격 서버는
`100.65.0.1:8978`, 제한 시간은 300초다. TCP 연결은 성공했고 무인증 `/v1/models` 조회는
401이었다. 이는 인증 필요 응답이며 저장된 자격 증명이 틀렸다는 증거가 아니다. 모델 생성
성공도 아니므로 504 이후 같은 모델 요청을 반복하지 않았다.

그 뒤 run `2930f0d8-24a9-438f-88d3-eaddab60e453`은 event 2306–2320의
`native_design` elapsed 0–150초 뒤 `FAILED/agent_output_contract`로 끝났다. safe 진단은
`planning_native_design/unexpected_model_behavior/schema_validation/<root>:json_invalid`이며,
앞선 provider HTTP 504와 다른 실패다. `/settings/model/test`의 저장하지 않는 15초
ephemeral timeout은 generic `all=false`을 돌려도 예외 시 aggregate false일 수 있으므로 모든
capability가 지원되지 않는다는 뜻이 아니며, sidecar protocol 오류 증거도 없다.

Agent `968e2ab`은 runtime 주소를 정규화하고, `0c9b4a7`은 fixture import를 바로잡았다.
최종 `87ad596`은 `nemotron-3-ultra`의 `supports_json_object_output=False`를 명시해 native
출력 요청에서 `response_format`을 보내지 않는다. mock wire 15건은 이를 고정하고 invalid JSON은
계속 거부한다. `artifacts/probe_native_output_20260831.py`의 controlled probe에서 강제 true는
input 114/output 47, finish `stop`, 29자 text와 `json_invalid`로 24,980ms 뒤 실패했고, 명시
false는 같은 prompt에서 input 114/output 30, finish `SUCCESS`로 10,621ms 뒤 성공했다. 중간
기본 profile(override 제거)도 5,660ms/output 45로 성공했으므로 강제 JSON 모드가 항상 실패한다고
단정하지 않으며, 이번 수정은 간헐적 호환성 위험을 제거한다.

Host `2a4f807`의 canonical compiler와 실제 Agent readiness → artifact 도출 → Host compile
경로는 M00000/M00001/M00002 출력을 증명했다. Agent 5건, Host fixture 3건, 상위 Host combined
7건과 Agent focused lint(F/E9/I)가 통과했다. fixture의 RUN_CMD만 M0010으로 둔 것은 XG-SIM
whole-byte write에서 입력과 출력을 분리하기 위한 test fixture이며 START M0000/STOP_OK M0001과
사용자의 기존 PLC 주소를 변경한 것이 아니다. `3167fc3`은 native-handoff fake attach에
PID/HWND/path/hash-bound `process_create_time` proof를 보완해 lineage 4/4를 통과시켰고 production
read-binding 검사를 약화하지 않는다. role-specific internal simulation의 구현·회귀 결과는
아래에 구분하며 실제 XG-SIM 성공을 대신하지 않는다.

- 별도 fake-bridge simulation 역할 검증에서 MCP `fa20d0a`와 Host `cb3540d`는 safe contact-only
  internal-M stimulus를 지원했다. sealed feedback은 `OFF → ON → released-held → STOP off`를
  검증했고 stimulus write는 M byte 0만, output M byte 2는 쓰지 않았다. 같은 byte write나 direct
  output forcing은 계속 거부한다. MCP targeted 43건, Host targeted 22건, 추가 Agent 24건과
  F/E9/I lint가 통과했다(`9f5e45d`는 formatting-only). 이는 fake bridge 증거이며 실제 XG-SIM
  실행이나 사용자 approval 결과가 아니다. Platform `820291e`는
  `onlyReviewedSimulationStimuli`에 TypeScript 계약과 Web 검사를 일치시켰다. contracts build/typecheck,
  Web typecheck/production build도 통과했지만 실제 PWA 화면 acceptance는 수행하지 않았다.

정상 Host restart는 ready/UAC 없음으로 끝났다. 기존 conversation
`6bb8a0a4-cf45-4e18-b402-d4b732216723`의 run
`7b89c506-6583-4ebb-a6ba-8af9c5986266`은 event 2342에서 `COMPLETED`가 되었고
`native_planning_finalize`까지 195초가 기록됐다. 제품 GET conversation의 latest plan message
`63277186-378f-40ca-96a1-e97542a78261`은 `ready_for_review`, `openDecisions=[]`, digest
`62af347dbb15fc358c20ca4fd48f2cf7e0daff6fe166fa81431e9b0480b1d0cd`다. read-only SQLite의
저장 metadata를 실제 Host compiler에 통과시켜 다음 source와 SHA-256을 확인했다.

```
LOAD M00001
LOAD M00000
OR M00010
AND LOAD
OUT M00010
END
```

`74ff83178d6aa0319631874eed5ffd1d973fa326d8e4c656172ecf6ec9f534d0`이며, 이는 실제
model → stored product artifact → compiler 경로의 증거다. model source는 M 내부 주소를 쓰되
START/STOP을 `digital_input`, RUN을 `digital_output`으로 typed 했다. 이는 native XG5000
create/build/save/reopen/simulation 또는 PLC write/new file write의 성공은 아니다. native create는
현재 plan의 human review와 global quarantined test transaction의 restore approval을 여전히
필요로 하며, 후자 async 질문은 사용자 응답을 기다린다. simulation 지원 수정은 커밋됐으며
실기 적용 전에는 변경된 Host/MCP 런타임을 함께 반영해야 한다. SSH 정보나 UAC 재요청은 하지 않았고 기존 LD operand 위치
선택의 scroll 단위도 아직 실증되지 않았다. Native Find 리소스 ID만으로 중복 피연산자 중 정확한
occurrence를 선택한다고 가정하거나 임의 좌표 식으로 바꾸지 않는다. 보호된 기존 working copy,
격리 transaction과 file quarantine은 변경하지 않았다.

## 현재 제품 경계

- 제품은 단발 chatbot이 아니라 Host가 mission, checkpoint, evidence, change transaction을
  소유하고 Agent가 bounded multi-cycle tool loop를 수행하는 엔지니어링 Agent다.
- Agent는 분석 evidence를 만들고 변경 후보를 제안한다. Host가 사용자 승인과 권한을
  소유하며 MCP만 XG5000/PLC 실행과 readback journal을 소유한다.
- 기존 LD 구조 변경은 typed MNE candidate, 전체 대상 digest, Program Check, save,
  independent reopen, rollback receipt에 결속된다. 선언된 XG-SIM 범위는 관찰 receipt 뒤
  사용자의 keep/revert 결정까지 같은 transaction에서 처리한다.
- Development/Beta/Production 실행·데이터 루트와 패키징 경계는 분리돼 있다. 개발
  launcher는 `scripts/Start-ControlStudio-Development.ps1`을 사용한다.
- Development 모델은 위 스크립트의 `nemotron-3-ultra` 설정을 사용한다. 2026-08-15에
  `/models`에서 해당 ID가 확인됐고 OpenAI-compatible completion 응답도 수신했다.

## 2026-08-31 PWA/native 현재 수직 상태

- 실제 PWA의 fresh search, hardware context, target binding, snapshot과 mission revision 5–8은
  통과했다. offline `P0020→P0021` candidate는 실제 PWA에서 승인되어 한 번 실행을 시작했다.
- execution은 `PLANNED` 뒤 native LD focus coordinate 오류
  `XG5000_LD_SOURCE_NOT_VISIBLE`로 피연산자 입력 전에 실패했다. apply, diff, Program Check,
  save, independent reopen/readback 또는 PLC 기능 성공은 아직 주장하지 않는다.
- 관련 Host transaction은 `f6ecf557-316b-4739-adde-247813330a18`, MCP transaction은
  `077d9ef5-c941-4607-9c1d-f0fc9af74a7d`이며, recovery check의 original-exact match는
  확인됐지만 recovery attach/rollback/save 성공은 확인되지 않았다.
- 역사적 2026-08-31 관측: 05:59 KST OS 재부팅 후 이전 Host/XG5000은 종료됐고, 06:16에
  normal Development launcher로 Host PID 5464/port 8767을 확인했다. 당시 XG5000 inventory는
  비어 있었다. 이는 현재 process 상태나 다음 작업 지시가 아니다. working copy `f277…`는 이전
  `b929…`와 static `NetworkModuleName` 하나만 다르고 ProgramData blob은 동일하지만, live
  equivalence나 hash 재결속의 권위는 없다.
- Browser control은 existing ControlStudio tab을 claim했고, scoped `LOCALAPPDATA`의 one-use
  launcher handoff로 Host PID 5464를 바꾸지 않고 PWA 인증과 conversation `8b3` 복원을
  완료했다. XG5000 재확인 inventory는 비어 있었고 recovery relaunch는 한 번만 시도됐다.
  그것은 transaction의 verified working copy가 현재 `f277`이고 retained artifact hash가
  `b929`라서 prelaunch에서 거부됐다. geometry/recovery resolution은 여전히 미완료이며,
  새 XG launch나 file write는 수행하지 않았다.
- Platform `b7d44aec0b6494701bebf74d932e2bc93683b306`은 reviewed-current hash와 transaction/
  Host plan/native checkpoint plan을 각각 결속한 opt-in backup restore request, Host-only MCP
  receipt validation, durable `change.working_copy_restored` event, 그리고 recovery UI의 exact
  review tuple을 추가했다. MCP counterpart `e3e69`는 private preservation과 journal-pinned
  exact backup restore를 소유하며 MCP regression 487건이 통과했다. PWA URL policy로
  실제 UI/restart acceptance는 수행하지 않았고 사용자의 file-restore approval도 아직 없다.
- Acceptance harness의 descendant-pipe fixture는 child PID를 temporary sibling에서 `os.replace`
  로 원자 게시하고 child exit 뒤 current-directory handle release를 bounded wait 하도록 고쳐,
  Windows의 빈 PID 파일/temporary cleanup retry-only race를 제거했다.
- Agent `5a707b0`을 포함한 consolidated deterministic-only acceptance는 session `50168`에서
  exit `0`으로 끝났다. receipt는
  `artifacts/plc-agent-acceptance/run-6rg7qore/receipt.json` (generated
  `2026-08-30T23:11:09Z`, manifest SHA-256
  `aba0b0d80046c7b9c66a6f3d0fb00cc82090564ccd565526633466e4f2640f7a`)이며,
  Agent/Host/MCP/CLI/Web 다섯 deterministic group과 255 test executions가 PASS다.
  CA-01, CA-02, CA-04--CA-07, CA-12는 모두 `NOT_RUN`이므로 overall은 계속
  `FAIL_CLOSED`; Browser URL policy의 PWA acceptance 미검증과 file-restore approval 대기도
  그대로다.
- 현재 운영 recovery 정책: 실패나 관측 timeout 뒤에는 live handle이 있으면 보존하고 결과와
  journal/identity evidence를 진단한다. 결과가 불명확한 non-idempotent side effect와 이미
  소비된 approval은 replay하지 않는다. 반면 safe inspection과 구현은 이미 승인된 exact
  plan/file/mutation 범위 안에서 계속할 수 있다. 실제 UAC 거부가 확인된 경우에만 새 explicit
  user request 전에는 elevation을 다시 요청하지 않는다. timeout, browser policy 거부, 또는
  과거 관측만으로 UAC 거부를 추정하지 않는다.

## 마지막 리팩터링

- Agent runtime의 canonical JSON 직렬화를 `_canonical_json` 한 곳으로 합쳤다. digest,
  checkpoint, model prompt의 JSON 바이트는 그대로이며 중복 옵션과 프로덕션 코드 72줄을
  제거했다. 마지막 mission objective prompt 두 곳도 같은 serializer를 사용해 별도 옵션
  조합을 남기지 않았다.
- MCP behavior report가 같은 프로그램의 LD graph를 state model과 reference index에서 다시
  만들던 경로를 명시적 내부 graph 전달로 바꿨다. 프로그램당 graph 생성은 3회에서 1회로
  줄었고 공개 함수 시그니처와 report JSON은 그대로다. write action의 condition device
  순회와 causal application parsing도 각각 한 번만 수행한다.
- Host Agent projection은 이미 structural evidence가 소유한 `canonical_digest`를 재사용해
  별도 hashlib/json dependency와 중복 직렬화를 삭제했다.
- Host의 네 종류 structural MNE patch에 흩어졌던 schema, compiler tool, evidence schema,
  plan model, MCP operation, XG-SIM 지원 규칙을 `MNE_PATCH_PLAN_CONTRACTS` 한 곳으로
  통합했다. 새 patch kind의 Host 변경 지점은 여러 분기에서 registry 한 곳으로 줄었다.
- Web의 다섯 structural MNE plan 판별을 `getStructuralMnePlan` discriminated union으로
  통합해 승인 화면의 반복 분기를 제거했다.
- Agent native-design validation은 nested model 전체 dump/parse를 없애고 `model_copy`로
  scope만 교체한다. validator의 retry 규칙은 유지하면서 run 종료 뒤 중복 검증과 mutable
  validator cache를 제거했다.
- MCP MNE plan/apply/verify/rollback/probe의 같은 active-project binding closure 다섯 개를
  기존 검증 함수에 대한 명시적 `partial`로 바꿨다. receipt와 journal 전이는 서로 의미가
  달라 억지로 공통화하지 않았다.
- Host의 operand/MNE executor 선택 규칙을 `CompositeChangeExecutionService._service` 한
  곳으로 모아 preflight/apply/verify/reconcile 네 경로의 중복 분기를 제거했다.
- Agent sidecar의 호출 지점이 하나뿐인 final-output forwarding wrapper를 제거하고 기존
  `asdict` 결과를 wire payload에 직접 사용한다. `run.final` contract는 바뀌지 않았다.
- LS 공식 XGK/XGB instruction contract에 맞춰 direct-D `INC/DEC` signed/unsigned
  16/32-bit 계열 16개를 하나의 in-place mutation spec으로 모델링했다. `P` variant는
  rising-edge 실행이고 double-word variant는 두 연속 D word를 read/write한다. Condition
  causal impact, state/behavior inventory와 Host evidence 검증이 같은 의미 경계를 공유하며
  non-D, indirect, 추가 operand 형식은 계속 `partial`이다.
- Structural raw 결과를 줄일 때 위 명령의 operation/execution/data type/read-write width가
  모델 입력에서 사라지던 경계를 닫았다. Host가 동일 raw state model을 독립 재검증해
  receipt digest에 결속된 bounded `modelContext`만 Agent에 전달하며, Agent는 이를
  observation이 아닌 inference에만 사용한다. HTTP/DB/evidence/event contract는 불변이다.
- 실제 대동 project에서 관측된 LOAD 비교식을 signed 16/32-bit 정적 의미로 구조화했다.
  `=`, `<>`, `>`, `<`, `>=`, `<=`와 정확한 `D` prefix만 지원하며 좌변·우변 순서를
  보존한다. MCP 한 파서가 graph/state/reference를 공급하고 Host는 raw expression을 독립
  재검증한다. 지원하지 않는 suffix·operand는 rung과 network를 `partial`로 유지한다.
  상세 의미는 receipt digest에 결속된 bounded `modelContext`로만 Agent 추론에 전달된다.
- 실제 보존 artifact에서 opcode 33이 raw `APPLICATION`으로 기록된 `>,C0001,6`도 확인했다.
  raw record/opcode는 보존하고 strict 비교 parser가 성공한 경우에만 logical `COMPARE`로
  정규화하는 판정을 한 함수로 모았다. 그 결과 같은 row의 `SET S050.01` condition,
  device reference, causal edge, state/behavior가 같은 의미를 공유하며 TON/MOV/DMOV/DINCP/
  CTR/TRTG/END 등 다른 application 경로는 바뀌지 않는다.
- Windows launcher가 소유한 Host stdout/stderr를 UTF-8로 고정했다. CP949와 UTF-8이 한
  startup log에 섞여 모델·Agent 실패 사유가 깨지던 실제 Development 진단 문제만
  제거했으며 실행 상태나 공개 계약은 바꾸지 않았다.
- Development 자동 모델 검사에서 이미 분류된 실패 사유를 로그에서 버리지 않고 기존
  `recent_model_verification` 한 곳에 성공·실패 모두 보존한다. 현재 저장 모델과 cache key가
  정확히 일치할 때만 기존 `status.agent.detail`과 `host.status` 이벤트로 투영하므로 새 상태,
  DB/API/event schema 또는 Web 분기를 만들지 않았다. 실패 결과는 설정 저장의 성공 cache로
  재사용하지 않아 재검사를 막지 않는다.
- Development 모델 검증의 성공·불완전·예외 결과 기록과 `host.status` 발행을 각각 한 경로로
  합쳤다. 실패 capability 객체 생성도 `ModelCapabilityResult.failed` 한 곳으로 모아 Host와
  sidecar의 중복 필드 구성을 제거했다. 기존 `ServiceState.starting`은 실제 검사가 진행 중일
  때만 쓰고 terminal `unavailable`은 즉시 반환한다. `-RequireAgent` smoke도 문자열이 아니라
  이 상태 enum을 소비하므로 확정 실패를 deadline까지 기다리지 않는다.
- XG5000 controlled launch의 창 생성 대기와 project title 대기 루프를 하나로 합쳤다. PID
  lifetime과 exact title을 같은 순회에서 확인하며 vendor process가 먼저 종료되면 title
  deadline을 더 기다리지 않는다. 제거된 helper를 포함해 14줄 추가/36줄 삭제이고, 정상
  PID/HWND/path/hash 검증은 그대로다.
- Host `MCPGateway`의 동일한 MCP 성공 결과 decoding을 7개 호출 경로에서
  `_decode_tool_result` 한 곳으로 합쳤다. structured content, data, text JSON fallback과 오류
  우선순위는 그대로이며 wire-format 변경 지점은 7곳에서 1곳으로 줄었다. Evidence digest의
  단일 forwarding wrapper도 제거해 이번 변경은 31줄 추가/92줄 삭제, 순 61줄 감소다.
- 이 리팩터링에서 새 테스트는 추가하지 않았다. 임시 C# compile executable 네 개와
  잘못 생성된 빈 파일 하나를 제거했다.
## 마지막 수직 연결

- 보존된 local working copy는 XG5000 process가 없어도 `workspace.compare`,
  `workspace.hardware_context`, `workspace.structure_index` 세 read tool을 Agent에 노출한다.
  Host의 workspace-only binding은 exact workspace/revision을 숨겨 주입하며 PID/HWND/path/hash가
  필요한 live LD·behavior·mutation 도구는 catalog에서 계속 빠지고 직접 호출도 거부된다.
- 동일한 source/working SHA-256도 조기 반환하지 않고 두 path-bound structure index를 만든다.
  파일 동일성은 exact hash로, 부분 구조 inventory는 `partial`로 각각 표현하므로 partial parser를
  LD 의미나 runtime 증거로 오해하지 않는다.
- Web에는 기존 local-open API의 `openInXg5000:false`를 쓰는 `구조만 분석` 선택만 추가했다.
  XG5000 launch가 409로 실패해도 이미 보존된 workspace를 static target으로 계속 사용하며,
  Debug 화면은 authoritative workspace/revision ID와 `오프라인 구조 분석`을 표시한다.
- controller/XG/workspace 전환은 한 번에 하나의 Agent target binding만 남긴다. controller 선택은
  workspace binding을 지우고, 명시적인 workspace/XG 선택은 controller binding을 지운다.
- Host와 Knowledge SQLite 중 한쪽만 먼저 migration되어 durable Agent 작업과 지식 source가
  서로 다른 세대로 남던 시작 경계를 닫았다. 실제 schema 상승이 필요할 때만 두 DB를 같은
  operation의 `sqlite3.backup` snapshot으로 보존하고, 두 초기화와 builtin seed가 모두 끝나야
  marker를 지운다. 두 번째 DB 실패는 즉시 두 preimage를 복원하며 hard crash로 marker가 남으면
  다음 시작이 먼저 같은 pair를 복원한다. 현재 schema에서는 backup I/O가 없다.
- 같은 static workspace에서 기존 순수 XGWX hardware parser도 Agent가 사용할 수 있다.
  `workspace.hardware_context`는 Host가 숨겨서 주입하는 workspace/revision 두 identity와 source·
  working SHA, inner context digest를 한 receipt로 결속한다. XG5000 query와 live hardware cache는
  사용하지 않으며 이 evidence는 변경 승인 coverage에 들어가지 않는다. UI Activity에는
  `작업공간 하드웨어 확인`으로 started/completed가 표시된다.
- 같은 working copy의 기존 `ProjectStructureIndex`도 `workspace.structure_index`로 직접
  전달한다. 새 parser나 HTTP/DB schema는 없고 프로그램·태스크·심볼·device·module·통신의
  bounded facts를 기존 structure receipt와 Agent citation validator가 소비한다. top evidence는
  static tool 이름을 유지해 live 변경 evidence와 구분하고 UI Activity는 `작업공간 구조 분석`으로
  실행을 표시한다. compare evidence도 conversation에 결속된 revision과 다르면 거부한다.
- `project.search`가 검증한 프로그램명·LD 위치·구조 항목 후보는 더 이상 raw-result 제거와 함께
  사라지지 않는다. 기존 search receipt의 digest와 match ID에 결속된 inference-only 후보를 전체
  32개로 제한해 Agent가 exact program/device 후속 도구를 호출할 때만 사용한다. raw XML·path·
  hash·PID/HWND와 공개 evidence/event/DB schema는 늘리지 않았다.
- XG5000을 열지 않은 static workspace에서도 `workspace.search`로 프로그램·심볼·device의
  bounded literal lookup을 수행한다. 기존 structure index와 `build_project_search`, 기존
  `project.search` receipt/context validator를 그대로 재사용하며, workspace/revision/source·working
  SHA를 읽기 전후에 결속한다. Agent에는 raw XGWX 결과가 아니라 receipt-bound locator만 전달되고
  이 evidence는 LD/rung/runtime/PLC/change/MNE 근거로 승격되지 않는다.

## 2026-08-16 현재 수직 보강

- MCP 원격 거부는 `tool_error`, Host 검증·정책 거부는 `host_policy`, 실제 채널 상실만
  `transport`로 Host→Agent까지 보존한다. Agent tool은 Pydantic AI의 terminal `ToolFailed`로
  구조화된 실패를 모델에 돌려주고 재시도 횟수 0을 유지한다. 실제 Agent run 회귀에서 모델은
  실패 종류를 받은 뒤 다음 판단을 수행하며 같은 Host 요청은 한 번만 호출됐다.
- 신규 native build를 `local_file`로 축소하던 WS-02 단절을 닫았다. `new_build` provenance는
  reviewed plan, build bundle/receipt, 저장 project SHA와 receipt digest를 workspace
  manifest/status, Host target binding, resume authorization, HTTP/TS contract까지 결속하며
  stale·tamper를 거부한다. Native handoff 후보와 accepted rebind도 같은 단일 lineage validator를
  사용하므로 일반 `local_file` attach는 native 권한으로 승격되지 않는다. Production에 남은 legacy
  ACCEPTED 1건은 원본 SHA까지 변경돼 자동 승격하지 않고 역사 기록으로만 보존한다.
- signed native resume은 같은 PID/HWND만 신뢰하지 않고 selected-PID COM/ROT가 읽은 실제 project
  path/hash와 process lifetime까지 서명된 new-build workspace에 결속한다. Durable handoff와 accepted
  reuse는 기록 직전에 `workspace.status`와 `xg5000.session_status`를 다시 읽으며, Agent static evidence도
  workspace/revision뿐 아니라 source kind, provenance receipt, source/working SHA를 끝까지 고정한다.
- XG5000 UI 경로의 직접 `SendMessage` 24곳을 bounded `SendMessageTimeoutW`로 통합했다.
- Platform 일반 테스트 14건 중 12건이 통과했고 실제-XGWX 2건은 명시 gate에서 분리했다. 실제 보존
  XGWX의 MCP subprocess→Host broker→Agent wire gate는 두 case 모두 개별 통과했으며, MCP 경계
  테스트 12건, Agent 전체 테스트 7건, HTTP contract guard와 Web build가 통과했다.
  관리자/UAC 실기에서는 XG5000 V4.82.1 PID 12268 기동과 project 임시 index 생성을 확인했지만
  read acceptance는 exit 1이고 결과/traceback이 보존되지 않아 catalog/LD/graph/state 성공은
  주장하지 않는다. source/working SHA-256은 모두
  `bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`로 유지됐고 종료 뒤
  XG 관련 process는 남지 않았다.

## 검증된 것

- Agent, MCP, Host, Launcher, packaging, Python contract compile/import 성공
- Host/Agent/MCP 변경 Python에 Ruff `F,E9,I` 성공
- 실제 Host→sidecar wire projection에서 16개 명령의 exact semantics와 변조 거부 확인
- 실제 보존된 XG5000 ladder artifact에서 `D<>,0,M0320`, `>=,C0001,1`,
  `<,C0001,1`, 그리고 raw APPLICATION encoding의 `>,C0001,6`이 ordered
  operand·signed width와 함께 구조화되는지 확인
- 보존된 18개 파일의 6개 고유 LD payload에서 raw APPLICATION 비교는 action/output에서
  제외되고 TON/MOV/DMOV/DINCP/CTR/TRTG/END의 logical node kind가 유지되는지 확인
- `>,C0001,6`이 `SET S050.01`의 structured condition, `C0001` read reference와
  downstream causal edge로 연결되고 Host logic/state receipt 및 modelContext 독립 검증을
  통과하는지 확인
- 잘못된 P suffix, extra operand, dotted device, 폭 밖 literal이 `partial`로 남고 Host가
  변조된 comparison metadata를 거부하는지 확인
- Host→Agent wire에서 raw state model은 제외되고 receipt digest에 결속된 comparison
  modelContext만 전달되는지 확인
- 2026-08-16 격리 Development 기동에서 Host/DB/static UI/MCP가 모두 `ready`, MCP 67개와
  Agent-visible 23개 도구가 등록되는지 확인. 같은 재기동의 모델 연결 실패 사유가 UTF-8
  log bytes로 기록되는 것도 확인
- 대표 LD 입력에서 리팩터링 전 HEAD와 현재 behavior report canonical JSON 동일 확인
- 같은 입력에서 behavior report의 LD graph 생성 횟수 3회 → 1회 확인
- TypeScript contracts build 성공
- Web TypeScript + Vite production build 성공
- 실제 보존 XGWX(SHA-256 `bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`)
  를 workspace-only path로 실행해 `identical/partial`, configurations 1, tasks 1,
  code units 4, symbols 302, devices 495, modules 2, parameters 6, communications 6을 확인.
  Agent catalog에는 compare/hardware/structure 세 도구만 남고 `program.read_ld`가 숨겨지는 것도
  확인
- 새 Development Host와 launcher handoff로 인앱 UI를 다시 인증해 header/target proof의
  `오프라인 구조 분석`, exact workspace/revision ID, local chooser의 `구조만 분석` 버튼을 확인
- 임시 SQLite 18/1 pair를 19/2로 정상 upgrade하고 sentinel 보존을 확인. Knowledge 초기화 실패를
  주입했을 때 18/1과 양쪽 sentinel이 복원됐으며, Host만 19로 올라간 hard-crash subprocess 뒤
  다음 시작이 marker를 소비해 18/1 preimage부터 다시 19/2로 완료하는 것도 확인
- 실제 Development data root의 현재 19/2 DB로 최종 Host를 재기동해 Host/DB/static UI/MCP
  `ready`, migration marker·backup directory 미생성을 확인
- 실제 보존 XGWX의 Host→MCP stdio 경로에서 MCP 69 tools, static Agent tools 3개를 확인했다.
  `workspace.hardware_context`는 CPU `XBC-DN/DP64H`, timer areas 3, I/O modules 2, configured
  I/O channels 4를 SHA-bound receipt로 반환했고 external wiring은 false로 유지했다. LD tool은
  catalog에서 숨겨졌고, stale revision은 `tool.started` 뒤 `tool.failed`로 거부됐으며 live
  hardware cache는 비어 있었다.
- 같은 stdio 경로의 `workspace.structure_index`는 `tool.started → tool.completed`, project
  `대동인덱스-0917-2`, code-unit names `NewProgram/출력/알람/초기동작`, structure fact 817개를
  발급했다. Host model-safe projection은 bounded 188 facts를 Agent runtime citation validator까지
  전달했고 MCP와 Host 모두 stale revision을 거부했다.
- 실제 XGWX search 결과에서 program-name/structure/LD 후보를 같은 Agent frame에 유지하고,
  35개 결과를 세 종류가 굶지 않는 순서의 32개로 제한했다. receipt digest·coverage match IDs 결속,
  변조 거부와 raw-result 비노출을 inline smoke로 확인했다.
- 같은 보존 XGWX의 static `workspace.search`에서 `M00217`, `T0150`을 각각 exact structure item
  1건으로 찾고 Host→Agent raw-result omission, source receipt digest 결속, stale revision 거부를
  확인했다. Static Agent catalog는 compare/hardware/structure/search 정확히 4개이며 live LD 도구는
  계속 숨겨진다.
- HTTP contract guard 성공
- 세 저장소 staged diff whitespace check 성공
- 원격 모델 catalog와 짧은 completion 호출 성공
- 최종 재검증에서 canonical JSON/digest 바이트 동등성과 behavior report의 graph 생성
  1회/프로그램 불변조건을 별도 테스트 파일 없이 인라인 smoke로 확인
- MCP 결과 decoding의 structured-content/data/text/error 대표 5경로가 기존 우선순위와
  오류 문구를 유지하는지 별도 테스트 파일 없이 inline smoke로 확인
- 2026-08-16 종료 검증에서 Agent/Host/MCP 대상 Python compile과 Ruff `F,E9,I`,
  TypeScript contracts build, Web typecheck/Vite production build, 세 저장소 diff-check를
  각각 한 번 통과. `origin/dev..HEAD`에 새로 추가·수정된 test 파일은 0개
- 실제 Development Host를 workspace 격리 data root로 재기동해 Host/DB/static UI/MCP
  `ready`, MCP 67개·Agent-visible 23개 도구를 확인. private model socket이 정책상 차단된
  상태에서 API와 인앱 브라우저 AGENT DEBUG가 모두 generic 문구 대신 정확한 정제 사유
  `모델 엔드포인트에 연결할 수 없습니다.`를 표시하는지 확인
- 최종 Platform 코드로 Development Host를 다시 시작해 Host/DB/static UI `ready`를 확인하고,
  과거 development smoke 실행이 같은 terminal 모델 실패를 기존 약
  120초 대기 대신 0.44초에 정확한 정제 사유와 함께 반환하는지 확인. 새 launcher handoff로
  인앱 UI를 다시 인증해 `DEVELOPMENT source-dev`, `Host 준비됨`, AGENT DEBUG 자동 활성화와
  동일 Agent runtime 사유를 확인
- 보존 artifact에서 새 Development working copy를 만든 뒤 실제 XG5000 V4.82.1 controlled
  launch를 재현했다. 이 sandbox 사용자에서는 vendor process가 약 6초 뒤
  `0xC0000409`로 자체 종료했으며, 변경 전 약 19.6초 generic title failure 대신 변경 후
  6.16초에 `exit_code=3221226505, ntstatus=0xC0000409`가 startup log에 남는 것을 확인했다.
  working copy 원본 hash는 보존됐고 XG5000 PID는 남지 않았다. Vendor가 분리 생성한
  `XGProjectServer.exe`는 crash 뒤 남아 이번에 생성한 PID만 수동 정리했으므로, process-tree
  cleanup 완료를 아직 주장하지 않는다.

## 아직 실기 검증이 필요한 것

이 Windows 장비에는 XG5000 V4.82.1이 설치돼 있고 vendor integrity가 필요하다. 2026-08-16의
격리 smoke는 쓰기 가능한 workspace Development data root에서 Host/DB/static UI/MCP까지 실제
기동했다. Agent는 코드 실패가 아니라 Codex 격리 정책이 configured private model endpoint
`100.65.0.1:8978` 소켓을 차단해 unavailable이었고 `/models`와 최소 completion도 같은
소켓 거부를 재현했다. 인앱 브라우저로 Development/Debug 화면과 정제된 Agent 실패 사유는
직접 확인했다. 이후 Administrator desktop/UAC 세션에서 XG5000 V4.82.1이 project working
copy를 실제로 풀어 임시 index까지 생성했으나 acceptance wrapper가 exit 1로 끝났고 최종 JSON과
traceback이 보존되지 않았다. 따라서 live catalog/LD/graph/state와 실제 Agent tool
started/completed 흐름은 아래 절차로 이어서 확인한다.

다음 세션은 항상 새 PowerShell/UAC/smoke에서 다시 시작하지 않고, 아래 evidence-based resume
순서로 현재 수직을 이어 간다.

1. 검증된 active Host, engineering session, PID/HWND와 live handle이 있으면 data root,
   workspace path/hash, transaction/journal identity를 다시 대조한 뒤 보존한다. 관측 timeout이나
   오류만으로 이 handle을 버리거나 새 launcher를 시작하지 않는다.
2. Host 프로세스가 실제로 없거나 종료됐음을 확인한 경우에만 같은 Development data root를
   쓰는 normal launcher로 cold start한다. XG5000 session이나 개별 tool handle이 없다는 이유로
   살아 있는 Host를 다시 시작하지 않는다. elevation이 실제로 거부됐으면 새 explicit user
   request 전까지 다시 요청하지 않으며, timeout이나 과거 관측은 거부로 간주하지 않는다.
3. 기존 quarantined recovery는 같은 transaction의 journal, target identity, recovery evidence를
   보존한 채 필요한 승인 아래 계속한다. `restoreVerifiedBackup`은 명시적 opt-in 요청이며,
   마지막 관측에서는 해당 파일 복원 승인이 미응답이었다. 재개 시 실제 응답과 승인 대상을
   확인하고, 응답 없는 상태를 승인으로 간주하지 않는다.
4. recovery와 target binding이 허용하는 동안 먼저 read-only scroll/focus 진단으로
   `XG5000_LD_SOURCE_NOT_VISIBLE`의 실제 표면/identity를 확인한다. 불명확한 side effect나 이미
   소비된 approval은 replay하지 않는다.
5. 그 뒤 exact target과 baseline이 다시 증명된 경우에만, 새 approval을 받은 LD
   `P0020→P0021` operand 변경을 apply → Program Check → save → distinct PID/HWND reopen/readback
   순서로 수행하고 fresh receipt를 남긴다. XG-SIM scope를 선택하면 receipt 뒤 keep 또는 revert
   결정을 같은 transaction에서 완료한다.

실기 증거 없이 native/XG-SIM/PLC 성공을 문서 상태만으로 선언하지 않는다. Browser URL policy의
PWA acceptance 미검증도, file-restore approval 대기도 이 절차로 해결된 것으로 주장하지 않는다.

### 2026-08-31 현재 continuation evidence

- MCP `0584ef5`는 LD horizontal/vertical scrollbar notification을 각 bar의 own parent로
  보낸다. `4d549ed`는 `focus_location`을 CLI 경계로 옮겼고, CLI `d230d96` 및 `fa0ed95`는
  public `focus-location` 명령을 제공한다. CLI 49건과 MCP focused 91건은 통과했다.
- 실제 subprocess에 malformed focus request를 한 번 보냈을 때 `InputError`로 끝났고 UI는
  열리지 않았다. 이는 live LD focus 성공 증거가 아니다.
- `C:\XG5000\XG5000.exe`의 single `sky.launch_app`만 consent PID `12916`을 만들었다.
  prompt는 뒤에 사라졌고 XG5000 process/window도 없었으며 원인은 아직 모른다. launch를
  이 관측만으로 반복하지 않으며, 이 관측을 approval 거부라고 쓰지 않는다.
- 이후 사용자 `다시`에 따라 한 번 실행한 XG5000은 PID `7024`, HWND `67344`로 실제
  열렸다(2026-08-31 10:05 KST 시작). 빈 XG5000 화면을 Computer Use로 확인했고 프로세스는
  응답 중이었다. 첫 launch의 `targetable window` 오류는 시작 중 관측 지연이었으므로 같은
  프로세스를 다시 관측해 창을 찾았으며 재실행하지 않았다. 이 PID/HWND는 다음 행동 전에
  현재 프로세스/창 목록으로 재확인한다.
- 새 프로젝트 `Ctrl+N` 입력은 `failed to activate captured window`로 실패했다. 현재 창을
  다시 선택한 뒤 활성화를 한 번 재시도해 같은 오류를 확인했다. 사용자에게 기존 XG5000
  창을 전면에 놓아 달라고 요청했다. 새 프로젝트 생성·LD 입력·저장·PLC 작업은 하지 않았고,
  창 활성화 실패를 UAC 실패로 바꾸어 부르거나 XG5000/Host를 재시작하지 않는다.
- PWA Browser URL policy block은 계속 남아 있으며 우회하지 않는다. working-copy restore
  approval은 아직 받지 못했고 quarantine도 유지한다.
- native geometry는 아직 고치지 않았다. payload `739c...` snapshot `bd2300fece86`, token 18 /
  row 0 / c94에서 modulo 33은 -1211을 내지만 raw 373은 증명되지 않았다. 1024 row stride만으로
  modulo를 삭제하지 않는다.
- 다음 UI 확인 경로는 고정 좌표 추정이 아니라 XG5000의 `찾기/바꾸기 → 디바이스 찾기`다.
  설치된 `C:\XG5000\l.kor\XG5000Help_kor.pdf` 8장(PDF 315–322쪽)은 `현재 창`, 디바이스
  종류/행 범위 필터, `다음 찾기`의 일치 셀 선택, `모두 찾기` 결과 선택 후 Enter로 해당
  프로그램 행 이동을 설명한다. `찾아가기 → 스텝`도 PDF 251쪽에 있다. 따라서 과거의
  “LD Find/Go To 기능이 없다”는 조사는 틀렸다. 명령 존재는 확인했지만 자동화 구현 및
  현재 버전의 실제 UI 동작 확인은 아직 하지 않았다.

### 내부 M 비트 변경 연결 — 실기 완료와 구분

- Agent `f5aa1a4`, MCP `9edc7e5`, Host `6cde9e0`/`c5480c5`는 실제 I/O 영향이 없는
  내부 M 접점·코일의 검색 → 대상 분석 → snapshot → 승인 제안 경로를 연결했다. Host의
  승인 후 실행도 이 경우에만 하드웨어 digest 없이 native 변경 계획을 전달한다.
- 이전에는 M 변경도 P 전용 미션 판별과 무조건적인 하드웨어 요구에 걸렸다. 이제 공통
  target/plan/coverage 조립을 재사용하고 하드웨어 의존 부분만 조건부로 처리한다. M이라도
  downstream P 출력이 있으면 이 예외를 쓰지 않으며, 이미 하드웨어 근거가 있는 M 계획도
  기존 경로를 유지한다.
- 이후 Host `6d06754`는 하드웨어 미관측으로 차단된 분석도 Agent에 전달하도록 수정했다.
  Agent `03aabec`는 M 접점이 P 출력에 영향을 주면 기존 대상/제안 단계를 유지하면서 현재
  실행의 하드웨어 조회 → 동일 대상 재분석으로 이어간다. 이전 실행의 근거는 재사용하지
  않으며, 새 하드웨어에 결속된 분석이 준비된 뒤에만 공통 승인 제안을 생성한다.
  exact named M/P 접점의 condition 역할도 지원하며 legacy OUT 경로는 유지한다.
  `f70d30c`는 중복 target/coverage 조립을 이미 있던 공통 함수로 합쳤다.
- 보존 payload `739c2be5…`의 메모리 복사본을 M으로 변환해 실제 MCP 분석 JSON을 실제 Host
  broker로 전달했다. `M0010→M0011`, `M00010→M00011` 모두 MainProgram/token 18에서
  offline candidate가 생성됐고 하드웨어 binding은 없었다. 원본 파일은 변경하지 않았다.
  이것은 native 데이터/계층 연결 검증이며 XG5000 입력창의 M 정규화 형식이나 GUI 저장 성공을
  증명하지 않는다.
- 통합 실행 `artifacts/plc-agent-acceptance/run-setz_406/receipt.json`은 Agent 68, Host 71,
  MCP 99, CLI 49 test executions와 Web build가 통과했다. manifest SHA-256은
  `2a6fde46a4ecae84d0bb9f9d4f24afb3c833c815ee492bf7557386369c7e2c80`이며 생성 시각은
  `2026-08-31T10:56:54+09:00`이다. 실기 네 항목은 `NOT_RUN`, 전체는 `FAIL_CLOSED`다.
  공통 함수 정리 후 Agent 68건도 다시 통과했다.
  별도 교차 검사에서 실제 MCP 의미 형식의 Agent 생성 M 계획을 Host 실행 계약이 받았고,
  4/5자리 M 피연산자의 native 전달값이 그대로 유지됐다.
- 기존 XG5000 PID 7024/HWND 67344를 재확인했지만 이번 Computer Use 캡처는 timeout이었다.
  새 창 목록으로 다시 선택한 뒤 한 번 더 캡처해도 같았다. 기존 창·Host를 종료하거나 UAC를
  다시 요청하지 않았다. 실제 LD 생성·편집·검사·저장·재열기와 PWA 실사용 확인은 미완료다.
  Host PID 5464는 유지되어 이번 소스 변경은 아직 실행 중인 PWA에 반영하지 않았다.
- 다음 continuation의 창 목록 재관측도 Computer Use 도구가 `unacceptable risk`로 거부했다.
  사유는 반복 캡처 실패 뒤 외부 화면 변화 없는 재시도였다. 다른 자동화 수단으로 우회하지
  않았고, 사용자에게 기존 XG5000 창을 전면으로 올린 뒤 알려 달라고 요청했다. UAC 거절이나
  프로그램 종료로 해석하지 않는다. Native Find/Go 구현은 실제 활성 LD 메뉴·대화상자 관측이
  필요하며, 실패 지점에 추가 guard를 붙이는 변경은 하지 않았다.
- 2026-08-31 12시대(KST) 읽기 전용 process/token 확인: 기존 XG5000 `7024`와 Host `5464`는
  모두 `high`, Codex 본체 `9452`는 `medium`이었다. executable path와 생성 시각도 확인했다.
  기존 Host/XG5000 승인이 풀린 상태가 아니므로 이 오류를 근거로 UAC를 재호출하지 않는다.
  Codex 본체와 별도 Computer Use 실행 주체는 구분하며, 이 관측만으로 UIPI가 최초 활성화
  실패의 원인이라고 확정하지 않는다. 원본 기록은 10:06 화면 읽기 성공 뒤 활성화 실패,
  10:11 화면 읽기 성공, 10:15 이후 캡처 timeout 순서이며 오늘 앱 로그 파일은 비어 있었다.
- MCP `d6f7f26`은 별도 재현된 프로젝트 결함을 수정했다. 활성화마다 `SW_RESTORE`를 호출해
  최대화된 편집창까지 복원하던 동작을 제거하고 최소화된 창만 복원한다. 이미 전면인 창은
  건드리지 않으며, `GetForegroundWindow` 반환값과 `IsChild` HWND 인자를 pointer 크기로
  처리한다. 수정 전 layout/focus 회귀 2건 실패, 수정 후 64-bit ctypes callback을 포함한
  5건 통과 및 Ruff 통과. 모두 fake API 검사이며 실제 창에 입력하지 않았다. 이 수정은
  Codex Computer Use 구현을 바꾸지 않고, 캡처/활성화 복구나 LD 실사용 성공 증거도 아니다.
- CLI `66f8494`/`22d44cf`는 기존 instance discovery의 User32 ctypes fallback 호출에
  HWND/HMENU/LPARAM 인자·반환 형식을 지정했다. 프로세스 재검증 계층을 새로 만들지 않고
  원래 발견 절차를 유지한다. 합성 ctypes callback으로 32-bit 초과 핸들을 검사했고 전체
  CLI 51건이 통과했다. 지정 원격 main에 `22d44cf`까지 push 완료. 창 열거·입력·UAC 등의
  live 동작은 실행하지 않았으며, CLI의 HWND 처리 수정도 외부 Computer Use 복구 증거가 아니다.

## 제품 실행과 개발 도구의 경계

2026-08-31 사용자 재확인: 제품 Agent가 Host/ExecutionBroker → MCP → 독립 CLI를 통해
XG5000을 다루는 것이 목표다. Codex Computer Use는 이 실행 경로에 포함되지 않는다.
앞 절의 Codex 창 활성화·캡처 실패는 개발 도구의 실패 기록일 뿐, 제품 Agent 작업 전체를
중단하거나 사용자에게 XG5000 전면 클릭을 반복 요구할 근거가 아니다. 정상 제품 실행은
Host의 run/event/transaction 및 CLI 결과를 기준으로 추적한다. 외부 도구가 거부한 동일
UI 동작을 우회하지 않으며, 그 제한과 별개로 가능한 제품 구현·검증은 계속한다.

제품 기록 재확인 결과:

- 마지막 Agent run `50275151-dbec-415c-a2e6-99f81a58eb25`는 승인 제안 생성 후 실패했다.
  현재 Agent `c4bce7f` 이후의 exact-operand 결정적 전환이 이미 그 경로를 대체했고,
  관련 fake-runtime 회귀 3건이 통과했다. 이 과거 기록을 새 미수정 장애로 간주하지 않는다.
- 실제 apply 오류는 보존 artifact `cf83b3a1b673/error.json`의
  `XG5000_LD_SOURCE_NOT_VISIBLE`이다. 이후 `native_status_unavailable`은 최초 apply
  원인이 아니라 recovery 단계의 오류다. 현재 코드로 보존 payload를 순수 재비교하면
  token 18의 `P0020 → P00021` 한 건과 저장된 두 SHA가 일치한다. 파일/격리는 변경하지 않았다.
- MCP `dd03833`은 `_open_operand_dialog`에서 창 활성화를 viewport 측정보다 먼저 하고,
  좌표 계산 후의 무조건 `SW_RESTORE`를 제거했다. 회귀가 수정 전 실패/후 통과했고
  focus/foreground 12건 및 Ruff가 통과했다. 실제 product apply 성공은 아직 미검증이다.
- 정적 설치 리소스: `C:\XG5000\XGPM\l.kor\PMLDEditorRes.dll`의 RT_MENU 38007,
  LANGID 1042, offset `0xC7F0`에서 `편집 → 디바이스 찾기` command 4845를 확인했다.
  `XGPM\l.kor\PMFindRes.dll` dialog 6631은 입력 6620/다음 6626/모두 6600을 갖는다.
  별도 `l.kor\FindRes.dll` dialog 7767은 입력 7604/현재 창 7753/다음 7626/모두 7600이다.
  두 variant 중 실제 LD handler가 여는 창은 아직 확인하지 않았으므로 정적 ID 발견을
  새 Find adapter 구현/실동작 성공으로 해석하지 않는다. 다음 확인도 제품 native 경로에서 한다.

## 반복하지 말아야 할 것

- 기능마다 Host/Agent/MCP/Web에 서로 다른 상태 규칙을 복제하지 않는다. 권위가 있는
  한 계층에서 결정하고 나머지는 exact contract를 소비한다.
- UI 표시나 model 주장만으로 mutation/verification 성공을 만들지 않는다. 성공은 target
  identity, journal state, native readback, receipt digest로만 결정한다.
- XG5000 control을 좌표·키 입력 fallback으로 넓히지 않는다. 가능한 typed COM/Win32
  identity를 쓰고, 불가피한 UI adapter는 PID/HWND/path/hash와 bounded profile로 격리한다.
- 방어 문구와 wrapper를 늘리지 않는다. 실제 failure mode, authority boundary, rollback
  invariant 중 하나를 증명하지 못하는 validation은 추가하지 않는다.
- 테스트 수를 성과로 삼지 않는다. 공개 contract나 실제 장애를 잡는 최소 테스트만
  유지하고 type/build/lint/static check가 더 싼 문제는 그 수단으로 검증한다.
- Development 편의를 위해 Production/Beta 권한, 데이터 루트, 서명·BOM 검증을 약화하지
  않는다.

## 2026-08-16 최종 리팩터링 종료점

- 이번 종료 패스는 새 기능·API·DB schema·event payload를 추가하지 않았다. 이미 커밋된
  Host MCP 결과 decoder 통합으로 동일 wire 해석 지점을 7곳에서 1곳으로 줄였고
  `31 insertions / 92 deletions`, 순 61줄의 프로덕션 코드를 제거했다. Agent canonical JSON
  직렬화와 MCP behavior-report graph 재사용도 같은 의미 규칙을 각각 한 곳에 둔다.
- `origin/dev...dev` 범위에 새로 추가하거나 수정한 test 파일은 세 저장소 모두 0개다.
  Python compile, 변경 Python의 Ruff `F,E9,I`, TypeScript contracts build, Web typecheck/Vite
  production build, 세 저장소 diff-check가 통과했다. 실제 보존 XGWX에서 `M00217`, `T0150`
  static literal 조회가 각각 정확히 1건이며 project SHA와 index digest가 유지되는 것도 다시
  확인했다.
- XGWX `ProgramData`를 실제 같은 project의 네 live `program.read_ld` payload와 대조했다.
  direct/application record는 전체 live 1,446개 중 1,278개(88.38%)를 opcode·coordinate·value·
  순서까지 exact 복원하지만 wire 166개와 특수 row-header 2개의 vendor 문법은 확정되지 않았다.
  따라서 현재 static evidence는 structure/search의 `partial` 경계를 유지한다. offset 추측으로
  `workspace.logic_graph`나 offline LD를 만들지 않는다. 해당 기능은 여러 XG5000 버전의 paired
  XGWX↔clipboard corpus로 wire/branch/empty-row 문법까지 증명된 뒤에만 추가한다.
- 다음 실기 작업은 사용자 Windows/UAC 세션에서 최신 Development launcher와
  `nemotron-3-ultra` Agent tool activity, live XG5000 read/analyze의 최종 receipt를 한 번 수직
  검증하는 것이다. 현재 Codex sandbox의 private-model socket은 코드로 우회하지 않는다.

## 주요 남은 위험

- 자동 startup schema migration의 pair snapshot/restore는 구현됐다. 사용자가 임의 시점의
  전체 데이터를 백업·선택 복원하는 UI/운영 정책, backup retention과 release downgrade 지원은
  여전히 별도 production 배포 범위다.
- UAC 승인 뒤 XG5000 기동과 project unpack은 재실행됐지만, 실제 UI/Agent/XG5000 read/analyze
  end-to-end의 성공 receipt는 아직 없다.
- 대표 실제 project fixture와 실제 XG5000 버전 변화에 대한 acceptance evidence가 더 필요하다.
- 비교식의 정적 구문·operand order·signed width는 구조화됐지만 CPU별 device range,
  실제 runtime 값, scan 실행과 조건 참은 여전히 별도 관측 증거가 필요하다.
- 대량의 과거 mock/implementation-coupled test는 정리됐으므로, 남은 핵심 경계는 build,
  contract guard, 최소 실기 acceptance가 실제로 실패를 드러내는지 계속 확인해야 한다.

## 2026-08-16 Agent 수직 폐쇄 후 현재 권위 상태

이 절은 위의 과거 테스트 수와 `새 test 파일 0개` 같은 종료 시점 기록을 대체한다. XG5000
제어층을 다시 만들지 않았고, 기존 COM/ROT·PID/HWND·path/hash 결속과 보존 live evidence를
baseline으로 유지한 채 Agent가 같은 목표를 끝까지 이어 가는 단절만 닫았다.

- Engineering mission은 같은 run의 변경 제안 직후 반드시 exact transaction/digest에 결속된
  `waiting_for_approval`로 멈춘다. 승인·실행 뒤 terminal outcome은 mission의
  `changeTransactionId`로만 재개되며, DB schema 21이 안전하게 기존 대기 mission을 보강한다.
  실패한 continuation은 outcome evidence를 남기지 않고, 성공 checkpoint와 같은 SQLite
  transaction에서 전역 1회만 기록한다. 재시도와 동시 실행도 ID rebound를 만들지 않는다.
- 일반 LD 물리 I/O operand 제안은 같은 run의 `project.hardware_context`와
  `program.change_target_analysis` ready receipt를 모두 요구한다. Agent에는 Host-owned
  `project_hash`를 제거한 public hardware binding만 전달하고, Host는 retained raw receipt의
  project hash와 context/project/receipt digest를 독립 대조한 뒤에만 `PENDING_APPROVAL`을 만든다.
  이 단계에서는 apply, save, PLC connect/write가 실행되지 않는다.
- `COMPLETED`/`ROLLED_BACK` receipt만으로 mission을 완료할 수 없다. 현재 run의 fresh target
  readback evidence가 있어야 완료되며, historical outcome으로 넘어간 다음 run에도 같은 규칙이
  유지된다. 동일 proposal·tool callback은 자동 재호출하지 않는다.
- 재시작 시 durable mission의 과거 evidence는 citation·observation·inference에 재사용한다.
  evidence와 Agent checkpoint는 각각 1.5 MB 제한의 별도 digest-bound frame으로 동기화한 뒤
  consume-once 처리하고, `run.start`에는 작은 reference만 둔다. checkpoint가 있으면 중복 history를
  보내지 않는다. 과거 evidence는 새 mutation 권한이나 fresh-target evidence로 승격되지 않는다.
- Accepted new-build handoff는 reviewed plan/build/reopen/XG-SIM provenance를 Store에서 다시 증명한
  13-field status로 Agent에 전달한다. 첫 mission objective와 acceptance criteria는 이 권위 상태에
  고정되고, 이후 durable mission이 우선한다.
- 세 저장소에 Windows quality workflow를 추가했다. Platform/MCP는 frozen uv lock을 사용하고,
  Agent는 pinned public contracts commit에서 만든 wheel을 direct requirement로 설치한 뒤
  `direct_url.json`까지 확인한다. Agent 전체 dependency constraint, GitHub action full commit SHA,
  setup-uv `0.11.21`도 고정했다.
- XG5000 UI의 직접 `SendMessage` 24곳은 bounded `SendMessageTimeoutW` 공용 경계로 통합했다.
  UIA helper process, clipboard broker, physical `SendInput` 확대는 실제 관측 실패 없이 선제 구현하지
  않는다.

현재 코드의 최종 자동 검증은 Platform 24건 중 22 pass/보존-XGWX 2 skip, 같은 XGWX gate를
명시적으로 켠 subprocess module 6/6, Agent 23/23, MCP 12/12이다. Platform compile·Ruff
`F,E9,I`·HTTP contract guard, Agent compile·Ruff, MCP compile·Ruff, TypeScript contracts
typecheck/build, Web typecheck/production build가 통과했다. Web에는 기존 약 690 KB chunk 경고만
남는다.

보존 XGWX gate는 실제 원본 SHA
`bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`를 MCP subprocess로 열어
Host broker→Agent wire의 search/evidence/receipt, stale revision, transport, tamper 경계를 통과했고
원본 파일은 변하지 않았다. 별도 Development smoke는 launcher/Host process가 시작됐지만 이
비대화형 세션에서 120초 안에 `127.0.0.1:8767` listener가 열리지 않아 exit 1이었다. 종료 후
ControlStudio/XG5000/MCP/Python 잔류 process는 0개다. 이를 live XG 성공으로 선언하지 않는다.

기존 같은 project의 네 live `program.read_ld` payload와 XGWX 1,446-record 대조, 1,278 exact
복원 evidence는 그대로 유효하다. 이번 delta의 유일한 운영 확인 항목은 사용자 Windows/UAC
desktop에서 최신 Development source로 Agent read/analyze→proposal→approval→apply→fresh
readback→mission complete receipt를 한 번 남기는 것이다. 그 전까지 XG5000 제어를 처음부터
재구축하거나 같은 정적 분석을 반복하지 않는다.

## 2026-08-16 실제 모델 workspace mission 완료

이 절이 Agent workspace-only 실행 상태와 자동 검증 수치의 최신 권위다. 기존 XG5000 제어층은
재구축하지 않았다. 보존 XGWX SHA
`bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`를 source/working 불변으로
유지한 채 실제 `nemotron-3-ultra`와 Host→Agent sidecar→Broker→MCP subprocess 수직 미션을
완료했다.

- 미션은 구조, 하드웨어, `M00217`, `T0150`, 최종 종합의 5 step으로 진행됐고 최종 revision 19가
  `completed/objective_achieved`로 저장됐다. 마지막 모델 run은 과거 evidence 4개를 durable sync로
  재사용했고 `tool.*` 및 `evidence.added` 이벤트를 0개 생성했다. Host를 완전히 재기동한 뒤에도
  DB revision 19와 completed 상태가 유지됐으며 Host/DB/static UI와 MCP가 ready, MCP 70 tools와
  Agent-visible 26 tools로 올라왔다.
- 수리 전 진단 run에는 `workspace.structure_index`가 2회 추가 실행됐다. 이 반복을 숨기지 않는다.
  exact tool cache, recovery checkpoint, stale-next-action reanalysis를 반영한 뒤의 연속 구간에서는
  structure evidence 1회부터 시작해 hardware 1회, `M00217` search 1회, `T0150` search 1회였고
  각 재개는 앞선 도구를 다시 호출하지 않았다.
- Nemotron 경로는 도구 실행과 구조화 분석을 두 phase로 분리하고, 매 cycle 현재 durable mission을
  다시 주입한다. 마지막 step은 모든 선행 step이 outcome+evidence로 완료된 경우에만 retained
  evidence를 합성하며 새 evidence인 것처럼 다른 step으로 복사하지 않는다.
- 모델 문장을 durable step outcome으로 저장하지 않는다. search outcome은 Host-검증 receipt의
  exact literal, coverage match count, evidence ID에서 결정적으로 생성한다. 실제 진단 중 모델이
  `M00217`을 반대로 서술했던 사례가 이 변경의 회귀가 됐다.
- `workspace.search`의 bounded `modelContext`는 이제 같은 tool completion에서 evidence journal에
  저장된다. `sourceReceiptDigest`, search digest, coverage keys, candidate count/content와 별도 context
  digest를 교차 검증한다. 0-match도 빈 candidates와 count 0으로 명시 보존하며, 변조된 context는
  historical projection에서 거부한다. 과거 journal에 context가 없던 실행은 digest-bound Agent
  checkpoint에서만 1회 복구하는 호환 경로를 유지한다.

최신 자동 검증은 Platform 28건 중 25 pass/보존-XGWX 3 skip, 보존-XGWX 명시 gate 3/3,
Agent 34/34, MCP 12/12다. Platform/Agent/MCP compile과 Ruff `F,E9,I`, HTTP contract guard,
TypeScript contracts typecheck/build, Web typecheck/production build, 세 저장소 `git diff --check`가
통과했다. Web에는 기존 약 690 KB chunk 경고, Platform에는 FastAPI TestClient deprecation 안내만
남고, Agent 격리 테스트에는 비실패 asyncio slow-callback 진단이 한 번 기록됐다. fresh Host smoke
종료 후 workspace 관련 Host/Agent/MCP/XG5000 잔류 process는 없다.

다음 수직 작업은 정적 workspace 조회를 반복하는 것이 아니다. 사용자 Windows/UAC desktop에서
동일 mission/lifecycle 계약을 실제 XG5000 read target에 연결한 뒤, 승인된 작은 변경 한 건을
proposal→approval→apply→fresh readback→save/reopen→keep 또는 rollback까지 완료하고 receipt를
남긴다. 실제 장비가 포함되면 controller identity/project match와 stop condition을 먼저 결속한다.

## 2026-08-16 실제 변경 경로 사전 검증과 현재 UAC 경계

정적 조회를 다시 수행하지 않고 위의 다음 작업을 그대로 시작했다. 실제 모델 DB를 복제한 격리
data root `artifacts/change-mutation-e2e-localappdata`에서 최신 Host를 8777에 기동했고 Host, DB,
Agent `nemotron-3-ultra`, MCP 70 tools/Agent-visible 26 tools가 모두 ready인 것을 확인했다. 보존
XGWX를 새 working copy로 캡처한 뒤 기존 XG5000 launch 경로를 호출했으며, Windows가 실제
`[WinError 740]`로 vendor integrity 승격을 요구해 409로 종료했다. 생성된 workspace
`b9ffa41e-3805-4f9c-b87e-76309fd08ed6`, revision
`3c11b6f2-f322-441a-8ee6-f89eea8dc70c`은 그대로 보존했다. source, working, original 파일은 모두
원본 SHA `bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`와 같아서 mutation은
일어나지 않았다.
2026-08-17 재확인에서도 manifest의 workspace/revision과 captured/working 두 파일 SHA가 모두
동일했고 working size는 45,226 bytes였다. 따라서 새 캡처 없이 이 exact revision을 계속 사용한다.

공식 `Start-ControlStudio-Development.ps1 -NoOpen`도 같은 격리 root로 호출했다. launcher는
Session 1의 `consent.exe`를 정상 기동했지만 이 비대화형 작업에서는 보안 데스크톱 승인을 받을 수
없어 두 번 모두 약 120초 뒤 명시적인 elevation-not-approved 상태로 끝났다. 이후 Host, Agent,
MCP, XG5000, consent와 8767/8777 listener 잔류는 0개다. 다음 실행은 workspace 캡처나 정적 분석을
반복하지 않고, 사용자가 UAC를 누를 준비가 된 한 번의 창에서 위 workspace를 이어 쓴다.

UAC 대기 중 실제 mutation service를 수직 검토해 저장 전 검증 실패의 복구 결함을 수정했다.
기존 코드는 `project.diff` 또는 Program Check가 실패해 native journal이 `APPLIED`,
`DIFF_VERIFIED`, `CHECKED` 또는 `QUARANTINED`인 경우에도 성공 경로의 `SAVED` 상태를 요구해,
이미 마련된 `project.rollback_change`에 도달하지 못하고 Host를 격리했다. 이제 실패 경로는
`change.transaction_status`를 다시 읽고 transaction/session/PID/HWND/project path/current hash와
recovery checkpoint, immutable plan ID/digest를 exact 재결속한다. native journal이 명시적으로
`rollback_required`인 rollback-capable 상태일 때만 editor rollback과 baseline save를 실행하며,
다른 plan으로 바뀐 journal은 rollback하지 않고 격리한다. 정상 경로의 SAVED→commit 검증도 같은
공용 권위 scope를 사용한다.

MNE terminal 경로의 저장·재열기 뒤 세션 결속도 닫았다. MCP는 전체 MNE verification 또는
rollback이 성공한 뒤에만 독립적으로 재오픈해 exact readback한 XG5000 PID/HWND, process lifetime,
workspace, canonical project path/hash를 1회성 controlled-open read proof로 등록한다. Host는 terminal
event와 mission continuation 전에 저장된 verification/rollback receipt의 transaction/plan/digest와
그 proof를 exact 검증해 `xg5000.attach`로 즉시 소비하고, 재오픈된 세션을 같은 workspace와 Agent
target에 바인딩한다. receipt, path, hash 또는 session이 어긋나면 완료 사실을 꾸미지 않고 live
target을 비워 static workspace만 유지한다. XG-SIM `OBSERVING` 중에는 명시적인 keep/revert 결정을
마칠 때까지 원래 mutation session을 유지하며, terminal accept/revert에서만 각각 verification 또는
rollback reopen을 결속한다. 이 read proof는 후속 mutation의 strong identity 권위를 대신하지 않는다.

같은 실기 경로에서 MNE replacement strategy의 물리 select/clear/paste 입력도 구식 개별
`keybd_event` 호출에서 공용 `SendInput(INPUT[])` 경계로 교체했다. 입력 직전에 root/editor HWND의
exact PID, foreground frame, GUI thread의 실제 focused descendant와 chord key-up 상태를 확인하고,
down/up 전체 배열을 한 호출로 넣는다. Windows가 전송 개수를 전부 반환하지 않으면 성공으로
진행하지 않고 요청 키의 release 배열을 best-effort로 보낸 뒤 명시적인 오류를 반환한다. 기존
editor liveness/dialog 검사와 단계별 MNE payload readback은 그대로 뒤에서 실제 결과를 확정한다.
2026-08-17에는 이 경계를 MNE read/capture/focus 행 이동, project-open shortcut, LD surface
focus/copy·operand dialog와 native dialog paste까지 확대했다. HOME/DOWN 반복 이동도 한 배열로
보내며, mouse fallback은 exact PID/foreground/target rect뿐 아니라 `WindowFromPoint`가 같은 target
descendant인지 확인해 다른 창에 가린 좌표를 클릭하지 않는다. cursor는 호출 뒤 원위치로 돌리고
partial mouse 전송은 button-up을 보낸 뒤 실패한다. production 전체에서 직접 `keybd_event`,
`mouse_event` 호출은 더 이상 남아 있지 않다.

MNE read/capture/focus/new-program/replacement가 사용자 clipboard를 text 하나로 축소하던 경계도
교체했다. 각 최상위 작업은 별도 short-lived STA helper가 `OleGetClipboard`로 원래 `IDataObject`를
보유하고, 전역 Windows named mutex 아래 nested MNE 호출 전체를 하나의 transaction으로 묶는다.
MCP는 자신이나 XG5000이 실제 clipboard를 바꾼 마지막 sequence를 기록하고, helper는 종료 시점의
`GetClipboardSequenceNumber`가 그 값과 exact 일치할 때만 `OleSetClipboard`와
`OleFlushClipboard`로 HTML·bitmap·custom·delayed format을 포함한 원본을 materialize한다. 사용자가
중간에 새로 복사해 sequence가 달라졌으면 복원하지 않는다. helper startup/restore에는 hard deadline이
있고 hang 시 child만 종료한다. 실제 Windows `snapshot→cancel` smoke를 연속 세 번 실행해 mutex,
STA/OLE 획득과 정상 종료를 확인했으며 잔류 helper는 없다.

같은 수직의 UIA 호출도 장기 MCP 프로세스에서 전부 분리했다. attach probe, project tree/diagnostic
inventory, toolbar command, program-check 결과 text, RUN/STOP/Write button 상태와 owner-drawn dialog
read/set/click fallback은 호출마다 별도 non-UI MTA helper에서 실행되고, parent가 PID/HWND를 호출
전·child 내부·응답 후 exact 검증한다. production의 `Desktop(backend="uia")` 호출은 이 worker에만
남아 있다.
helper가 UI Automation element 검색이나 `click_input`에서 멈추면 4~6초 hard deadline 뒤 child만
종료하며 MCP/transaction journal은 계속 살아 있다. 닫히는 modal dialog는 정상 postcondition으로
허용하되 같은 HWND가 다른 PID에 재사용되면 실패한다. UIA timeout 뒤 native HMENU/win32 fallback으로
갈 때도 PID ownership을 다시 확인한다. 실제 Windows foreground window를 대상으로 격리 helper의
attach probe와 button inventory를 연속 실행한 read-only smoke는 2.2초 안에 button 3개를 반환했고
helper 잔류는 없었다. [Microsoft UI Automation threading 지침](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)의
non-UI MTA 권고를 따르며, XG5000 제어 primitive나 selector를 새로 만들지 않고 이미 검증된 호출의
장애 경계만 바꿨다.

최신 자동 검증은 Platform 34건 중 31 pass/보존-XGWX 3 skip, 보존-XGWX 명시 subprocess module
8/8(실제 artifact acceptance 3/3), Agent 34/34, MCP 52/52이다. 새 change execution 수직 3건은 정상 apply→diff→check→save→commit,
저장 전 native rejection→rollback→baseline save, plan identity 변조→no rollback/quarantine을 각각
검증한다. 추가 MNE terminal rebind 수직은 Host 3건과 MCP 2건으로 정상 completed handoff,
다른 파일/tampered receipt 거절, exact controlled-open binding 등록과 unbound reopen 무등록을
검증한다. 물리 입력 수직 14건은 exact down/up ordering, repeated navigation, focus·key-state 차단,
가려진 mouse target 거절, cursor 복원, partial-send release와 MNE/LD/dialog 연결을 검증한다.
clipboard 수직 7건은 nested snapshot 1회, exact last-sequence restore,
사용자 clipboard 변경 시 no-overwrite, OLE materialization과 destructive partial-write 복원을 검증한다.
UIA 격리 수직 17건은 MTA 초기화, hard timeout, PID/HWND drift·reuse 차단, exact attach와
toolbar/dialog 선택, Program Check result, XG-SIM·tree·diagnostic inventory 연결을 검증한다.
세 Python 제품 compile과 Ruff `F,E9,I`, HTTP contract guard, TypeScript contracts
typecheck/build, Web typecheck/production build가 통과했다. Web에는 기존 약 690 KB chunk 경고,
Platform에는 FastAPI TestClient deprecation 안내만 남는다.

남은 유일한 이 수직의 외부 동작은 사용자 보안 데스크톱에서 UAC를 승인한 뒤 같은 working copy의
작은 MNE candidate를 approval→apply→readback/check→save/reopen→keep 또는 rollback으로 닫고,
같은 Agent mission에 terminal outcome과 fresh evidence receipt를 남기는 것이다.

## 2026-08-17 실제 Agent 한국어 수직 완료와 XG5000 Automation 수정

격리 Development data root의 기존 workspace/revision을 그대로 사용해 최신 source Host를 8767에
기동하고 실제 `nemotron-3-ultra`→Agent sidecar→Host broker→MCP subprocess 경로를 다시 실행했다.
요청은 저장된 XGWX의 프로그램명과 하드웨어 구성을 한국어로 보고하고 파일/PLC를 변경하지 않는
분석-only 작업이었다. 첫 run에서 `inspect_workspace_structure`와
`inspect_workspace_hardware_context`를 각각 정확히 1회 실행해 evidence를 durable checkpoint에
저장했다. 후속 resume은 MCP tool 호출 0회로 두 evidence를 재사용해 mission revision 3을
`completed/objective_achieved`로 끝냈고, 최종 답변은 프로그램 `NewProgram`, `출력`, `알람`,
`초기동작`, CPU `XBC-DN/DP64H`, 확장 입력 `XBE-DC08A`, 관측된 I/O 시작 영역과 timer 영역을
한국어로 직접 보고했다. source/working SHA는 계속
`bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f`이며 mutation은 없었다.

이 실행에서 드러난 continuation의 영어 메타데이터 복창은 수정했다. 한국어 원 요청을 continuation
instructions와 prompt에 다시 결속하고, 사용자 표시 answer/structured prose가 영어 우세이면
validator가 수락하지 않는다. 내부 mission/workspace/revision/schema/receipt/digest 나열 대신 원래
engineering objective에 필요한 구체적 evidence fact를 직접 답하도록 했다. 영어 우세 answer,
structured prose, continuation 언어 결속의 회귀 3건을 추가했다.

XG5000 실기 attach의 별도 원인도 확인했다. 설치된 32-bit Automation server 등록은 ProgID
`XG5000App.Application`, CLSID `{25337192-7138-48AE-9712-99107F6E2522}`이고 LocalServer32 명령은
`C:\XG5000\XG5000.exe /Automation`이다. 기존 launcher는 project path만 전달해 창은 열어도 ROT에
등록되지 않았다. 이제 launcher는 exact executable에 `/Automation`과 exact project path를 전달하며
명령 회귀 테스트가 이를 고정한다. 실제 elevated ROT probe는 UAC 승인이 닫혀 실행되지 않았으므로
live acceptance로 기록하지 않는다. 다음 실기에서는 새 제어 primitive를 만들지 않고 이 corrected
launch command로 exact PID/ROT/path/hash proof만 확인한다.

Workspace resume의 controlled open이 실패한 뒤 XG5000 창이 늦게 준비되는 경우에도 다시
launch하지 않는다. Host는 최대 15초 동안 read-only instances만 확인하고, 후보가 정확히 하나일
때만 persisted workspace의 exact path/hash와 그 PID/HWND로 기존 MCP attach를 실행한다. strong
readback 또는 controlled read binding이 아니거나 후보가 없거나 복수이면 기존 409를 유지한다.
UI가 보내는 120초는 launcher 내부 deadline이며 fetch 자체에는 120초 Abort가 없다. MCP operation
계약과 독립 CLI subprocess outer는 cleanup grace를 포함해 135초이고, Host FastMCP transport는
failure envelope 전달 여유 5초를 더한 140초로 open-project에만 고정한다. 다른 workspace tool의
timeout 동작은 바꾸지 않는다.

최신 자동 검증은 Platform 35건 중 32 pass/보존-XGWX 3 skip, Agent 38/38, MCP 53/53이다.
세 Python 제품 compile과 Ruff `F,E9,I`, HTTP contract guard, TypeScript contracts typecheck/build,
Web typecheck/production build가 모두 통과했다. Web에는 기존 약 690 KB chunk 경고만 남는다.

## 2026-08-24 Production UI 흰 화면 수정

Development Host 8767의 HTML, JavaScript, CSS 응답과 파일 SHA는 정상이었지만 Vite가 생성한
`type="module" crossorigin` entry와 `crossorigin` stylesheet는 로컬 앱 브라우저에서 실행·파싱되지
않았다. 같은 소스의 Vite development UI는 정상 렌더링됐고, production asset은 self-contained
bundle임을 확인했다. production HTML에 한해서 JavaScript를 deferred classic entry로 바꾸고
stylesheet의 불필요한 `crossorigin`을 제거했다. development module entry는 그대로 유지한다.

흰 화면이 다시 조용히 남지 않도록 외부 `boot-guard.js`가 시작 예외 또는 3초 내 root 미생성을
안전한 복구 화면으로 표시한다. `npm run build`는 이제 typecheck와 Vite build 뒤
`verify-production-entry`를 실행해 deferred script, same-origin stylesheet, boot guard가 실제
`dist/index.html`에 남았는지 검사한다. 실제 인앱 브라우저에서 production DOM 렌더링,
header 배경 `rgb(17, 29, 27)`, CSSOM 968 rules를 확인했다. 직접 연 URL은 의도대로 Launcher 세션
인증 필요 화면에 머물렀으며, 인증 토큰을 브라우저에 수동 주입하지 않았다.

별도 Edge 151 app-mode acceptance에서는 페이지가 정상이어도 UI Automation에 `RootWebArea`가
노출되지 않아 Launcher의 fail-closed rendered-content probe를 통과하지 못했다. renderer
accessibility 강제 플래그도 실제 UIA tree를 바꾸지 않아 코드에는 남기지 않았다. 브라우저 프레임
제목이나 빈 창만으로 성공 처리하는 완화도 하지 않았다. 후속 작업은 일회성 handoff가 실제
`POST /api/session`에서 소비되고 bootstrap까지 성공했음을 Launcher가 안전하게 관측하는 별도
readiness receipt를 추가하는 것이다.
