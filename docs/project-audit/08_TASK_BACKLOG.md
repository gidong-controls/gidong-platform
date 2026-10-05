# 08. 복구 실행 백로그

2026-09-20 후속 구조 제거 지시 반영. **문서만 갱신했으며 구현/외부 이슈 발행은 하지 않았다.** 총24개: **P0 10 / P1 9 / P2 3 / P3 2**. 핵심 구조 교체 RB-06/07/08/10/11을 P0로 올렸다. 모두 XS/S/M 크기이며, M을 넘으면 시작 전에 검토 가능한 하위 작업으로 나눈다.

## 공통 필수 제거 조건

[복구 결정의 SC-01~06](07_RECOVERY_DECISION.md#필수-제거교체-목록)은 선택적 권고가 아니다. 연결된 작업은 **기존 결함 경로/호출자/flag/fallback 제거 증거 + 대체 경로의 회귀 및 실행 증거**가 모두 있어야 완료다. 새 경로만 추가하거나 문제를 다른 helper로 옮기면 미완료다. 호환 읽기/이전 코드는 신규 실행 경로와 격리하고 담당자·제거 시점을 명시한다. 실제 vendor legality/권한/원본 보호/immutable journal은 보존한다.

SC 연결: SC-01→RB-04/07, SC-02→RB-04/06, SC-03→RB-10/11, SC-04→RB-08/15, SC-05→RB-04/05, SC-06→RB-19. **관련 작업 카드의 완료 조건에 이 규칙이 추가로 적용**된다. 작업 수나 카드만 늘려 이 결함의 처리를 미루지 않는다.

## 중복 확인과 현재 상태

기존 `AGENT_PRODUCT_PLAN.md`의 P0~P2, 이전 감사 F1~F10, 설계 D1~D8, 반대 검토 R1~R7/A~D를 확인했다(E21). 이 작업들은 기존 로컬 계획을 아래 ID로 **통합한 목록**이지 병렬로 다시 수행할 별도 목표가 아니다. remote PR/issues/actions는 E16 접근 불가이므로 **원격 중복 없음은 보증하지 않는다.** 외부 Task URL은 없음. RB-03에서 접근되는 기존 issue가 있으면 그 ID에 연결하고 새 issue를 만들지 않는다.

| 기존 항목 | 이 목록에서의 연결 |
|---|---|
| 감사 F1/설계 D1·반대 검토 A(일반 loop) | RB-04/07/08 |
| 반대 검토 R1/A(compiler) | RB-02/06 |
| 감사 실행 경계/반대 검토 B·R2/R4 | RB-02/09/10/11/12 |
| 이전 최종 동작 판정표·반대 검토 C | RB-01/04/13/14/15 |
| 반대 검토 D(provider/일반화) | RB-16/17/18 |
| PRD release/미결 정책·기존 P2 | RB-19/20/21/22/23/24 |

공통 rollback 원칙: 해당 작업의 변경만 검토 후 역변경하고, 사용자 파일·DB·secret·native receipt는 별도 보존한다. `reset --hard`, clean, broad delete는 방법이 아니다. 이전 성공 source/receipt를 새 source로 재결속하지 않는다.

## 순서·critical path·병렬화

| Wave | 작업 | 시작/완료 관계 |
|---|---|---|
| 0 결정·blocker | RB-01/02/03 | 지금은 문서만 준비됨. 결정/접근 확인 후 구현 승인 필요 |
| 1 검증 가능한 기반 | RB-04/05/06/07/08 | 회귀 oracle 먼저; CI·compiler·runtime는 파일 소유권 분리 시 병렬 |
| 2 핵심 MVP | RB-09/10/11/12/13/14/15/16 | scope→실제 수정본 적용→재시험→사용자 인수. 심볼·UI는 계약 확정 뒤 병렬 |
| 3 안정성·보안·성능 | RB-17/18/19/20/21/22 | 17~19는 복구 MVP 필수 gate이며 core 경로와 병렬 가능. 20~22는 정책/출시/운영 별도 |
| 4 선택 개선 | RB-23/24 | core 완료를 지연시키지 않는 별도 계획 |

핵심 경로: **RB-01/02 → RB-04 → RB-06/07/08/09 → RB-10 → RB-11 → RB-12 → RB-15 → RB-16 → 최종 인수**. RB-03의 native 환경은 실제 실행 단계의 선행 조건. **RB-05와 RB-17/18/19도 최종 인수 전 필수**이며 UI/native 가독성은 RB-13/14로 병렬 준비한다. Wave는 사업 우선순위 묶음이지 모든 번호를 직렬 실행하라는 뜻이 아니다.

공유 파일 소유권: runtime.py(RB-07/08/17/18), native_projects.py(RB-06/10/11), app.py(RB-09/12), App.tsx(RB-14)는 동시 쓰기 금지. 서로의 승인된 commit 위에서 이어 작업한다. 읽기 검토·독립 테스트 준비는 병렬 가능.

### RB-01 — MVP·지원 프로필·합격 oracle 결정

- **유형/상태:** decision / 결정 대기. **Wave/우선순위/크기/위험도:** 0 / P0 / S / 중간(잘못된 범위 고정).
- **Finding / 해결 문제 / 근거:** AF-04/06/09/11. “완벽/모든 모델/200k”와 native 합격 의미가 수치·범위로 고정되지 않음. E02/E08/E12/E15, 요구 C05/C07/C09/C10.
- **기대 결과:** 승인할 PRD delta와 한 개의 인수 manifest. **포함 범위:** 네 사용자 여정, 장비 I/O·모드/정지 의미, CPU/명령 지원, 모델 endpoint/version, 200000 vs 196608 및 input/total, 반복 수·시간/토큰/개입 예산, 검토자. **제외 범위:** 제품 코드·전체 PRD 재작성·추가 기능.
- **관련 파일/모듈:** PRD.md, 이 패킷01/02/09, 기존 최종 동작 판정표. **선행 조건:** 감사 읽기·제품 소유자 확인. **후속 작업:** RB-04/06/13/15/16/17/18. **병렬:** RB-02/03과 가능, 결정 충돌은 한 담당자가 조정.
- **완료 조건:** 모든 필수 여정에 입력·기대 결과·증거·검토자 존재, 미결 수치 0 또는 의존 여정의 명시적 blocked 상태. Agent 목표/200k 요구를 몰래 축소하지 않음.
- **검증 절차:** PRD27개와 추가6개 요구 ID를 manifest에 매핑; 두 검토자가 동일 사례의 pass/fail을 같은 기준으로 판단하는 예시 대조.
- **실패 시 롤백:** 제안 delta만 취소, 기존 PRD/제품 결과 보존. **사용자 결정:** 범위·200k 의미·지원 조합·인수자·예산 승인.

### RB-02 — task·source revision·attempt·권한 계약 결정

### RB-03 — 다음 실행 환경과 원격 중복 상태 확정

- **유형/상태:** bounded verification / 즉시 읽기 가능, native 기동은 다음 구현 승인 후. **Wave/우선순위/크기/위험도:** 0 / P0 / S / 낮음.
- **Finding / 문제 / 근거:** AF-09/14. 현재 active 환경·remote PR/issues/CI를 확인하지 못함(E01/E16/E17/E20).
- **기대 결과:** Git/환경/접근 manifest와 기존 issue 연결 표. **포함:** 네 repo HEAD/diff, 사용 interpreter·Node·XG5000·모델 서버 버전/능력, 대화형 세션, remote 작업 목록. **제외:** 무단 설치·권한 우회·작업 스케줄러·SSH 해결·secret 출력.
- **관련 파일/모듈:** manifests/lock/quality.yml, 실행 README, 이 백로그. **선행:** 담당자 접근 권한·native 단계는 실행 승인. **후속:** RB-05/10/15/17/18/22. **병렬:** 모든 독립 소스 작업과 가능.
- **완료 조건:** 각 검증의 사용 환경과 접근 가능/불가가 명시됨. remote 접근 가능 시 중복 issue 연결, 불가 시 로컬 ID 유지하고 발행만 blocked. native 실행 프로필은 실제 실행 전 확인되어야 함.
- **검증 절차:** repo별 branch/HEAD/status, 버전 명령, 승인된 읽기 API/UI 상태 확인; 두 번 실패한 동일 접근 반복 금지. 결과를 단일 manifest에 기록.
- **롤백:** 읽기 결과만 보관, 설정 변경 없음. **사용자 결정:** 필요한 접근·실행 환경 제공; 원격 접근이 없다고 로컬 구현 전체를 중단하지 않음.

### RB-04 — 알려진 제어·래더 반례를 제품 회귀 fixture로 고정

- **유형/상태:** test implementation / RB-01/02 결정 대기. **Wave/우선순위/크기/위험도:** 1 / P0 / M / 중간.
- **Finding / 문제 / 근거:** AF-01/02/04/07. 현재 router test는 제품과 반대, 최종 래더 반례가 8개 시나리오 밖(E04/E05/E08).
- **기대 결과:** 기존 실패를 탐지하는 작고 독립적인 회귀 fixtures. **포함:** valid 후 edit 선택, 합법 설계와 정책 진단 분리, 수동→자동/정지 해제 입력 전이 oracle. **제외:** Agent 대신 정답 rung 삽입·제품 코드 수정·모든 장비 fixture.
- **관련 파일/모듈:** Agent tests/test_workspace_stable_workbench.py, Host tests/native compiler tests, 합의 시나리오 fixture. **선행:** RB-01/02. **후속:** RB-05/06/07/11/15. **병렬:** 테스트 파일 소유권 분리 시 가능.
- **완료 조건:** 현재 baseline에서 알려진 결함이 탐지됨을 기록, 기대 oracle가 PRD/확정 요구와 일치. 수정 전 red를 감추는 무조건 skip/xfail이나 기대값 약화 없음.
- **검증 절차:** 고정 interpreter에서 `python -m pytest <해당 fixture>` 수집 수·실패 assertion 기록. 알려진 잘못된 샘플과 올바른 샘플 각각 판별. 초기 red는 결함 재현이며 제품 합격 아님.
- **롤백:** fixture 변경만 역변경, 원본 native artifact 보존. **사용자 결정:** 운전 의미/안전 기대값은 RB-01에서 승인.

### RB-05 — 실제 행동 회귀를 실행하는 CI gate 구성

- **유형/상태:** implementation / RB-04 후. **Wave/우선순위/크기/위험도:** 1 / P0 / M / 중간.
- **Finding / 문제 / 근거:** AF-07/09. quality workflow가 행동 test를 실행하지 않음(E09/C07/C08).
- **기대 결과:** CPU-only 행동 회귀의 수집·실행·실패가 CI에 반영됨. **포함:** lock된 test 의존성/runner, Host/Agent/MCP/CLI 실제 pytest 테스트, 명시적 native gate 분리. **제외:** GUI native를 mock으로 합격 처리·CI만 위해 요구 약화.
- **관련 파일/모듈:** 각 quality.yml/pyproject/lock/tests, web의 기존 검증 scripts. **선행:** RB-03/04. **후속:** RB-15/16 최종 인수 gate. **병렬:** RB-06/07과 독립 CI 파일 소유권으로 가능.
- **완료 조건:** collect-only가 0개가 아님, 실제 테스트 실행 기록, 의도한 실패 assertion에서 CI 실패, 수정된 통과 fixture에서 성공. native 비실행을 명시. 구현 결함이 남아 red면 릴리스 gate를 녹색으로 표시하지 않음.
- **검증 절차:** 각 repo의 고정 환경에서 `python -m pytest --collect-only`와 선택된 suite, 실패 주입 branch의 CI 결과를 확인. native gate는 실제 runner에서만 별도 실행. 원격 접근 부재 시 CI 적용 증거는 blocked.
- **롤백:** workflow/test-tool 의존 변경만 PR 단위 역변경, 기존 lock 복구. **사용자 결정:** 지원 runner/profile는 RB-01/03, 신규 CI 권한이 필요하면 별도 확인.

### RB-06 — compiler legality와 설계 정책 진단 분리

### RB-07 — 설계 도구를 일반 Agent 실행 루프에 통합

- **유형/상태:** mandatory replacement / SC-01 필수, 구현 착수는 별도. **Wave/우선순위/크기/위험도:** 1 / P0 / M / 높음.
- **Finding / 문제 / 근거:** AF-01. planning branch·router·tool projection가 모델 선택을 발행으로 강제(E04).
- **기대 결과:** Agent가 design 조회/부분 편집/검사/발행 뒤에도 목표에 필요한 도구를 선택. **포함:** 기존 broker/ExternalToolRequest 사용, 실제 오류 다음 호출 전달, planning 종료 의미 수정. **제외:** 새 planner/mission 서비스·native 권한 우회·장비 정답 코드.
- **관련 파일/모듈:** runtime.py router/projection/new_project_planning, 관련 Agent tests. **선행:** RB-02/04. **후속:** RB-08/10/15. **병렬:** RB-06/05 가능, runtime 편집은 단일 소유자.
- **완료 조건:** valid 후 일반 편집/조회 선택을 허용하는 test, 도구 실패 이후 수정 선택, 읽기 요청은 무변경 완료, publish만으로 loop 강제 성공 없음.
- **검증 절차:** mocked model은 도구 선택 계약만 시험; native integration은 RB-10/11에서 별도 증명. tool request/result transcript와 RB-04 regression 대조.
- **롤백:** runtime 경로 변경만 역변경, 이전 작업공간 데이터 유지. **사용자 결정:** 없음(승인된 D-02 안의 구현).

### RB-08 — 요청 목표·산출물·완료 증거의 상태 계약 구현

- **유형/상태:** mandatory replacement / SC-04 필수, 명세 연결 필요. **Wave/우선순위/크기/위험도:** 1 / P0 / S / 중간.
- **Finding / 문제 / 근거:** AF-03/06. publish/저장/시험과 사용자 목표의 success 의미가 혼합(E04/E06/E08).
- **기대 결과:** 요청별 완료 범위와 해당 artifact revision의 근거 연결. **포함:** read/design/save/test 결과 구별, 미완료/blocked 표시, stale receipt 거부. **제외:** 모든 저장에 SIM 강제·새 범용 mission DB.
- **관련 파일/모듈:** runtime finalization, Host run/result 모델·native receipt 연결. **선행:** RB-02/04/07. **후속:** RB-10/11/14/15. **병렬:** runtime 작업 완료 후 Host/API 소비자와 계약 기반 병렬.
- **완료 조건:** 읽기만 요청한 경우 실행 없이 완료 가능; save만 요청하면 save 증거만 주장; 전체 제작 요청은 publish로 완료 불가; source가 달라지면 옛 test가 합격 근거가 되지 않음.
- **검증 절차:** 요청 유형×artifact revision 표의 positive/negative 테스트, final answer와 Host 상태 대조.
- **롤백:** 상태 출력/계약 변경만 역변경, 기존 receipt 삭제/변조 없음. **사용자 결정:** 완료 범위 D-01/02 외 추가 없음.

### RB-09 — 작업 범위와 native baseline 변경 검증 연결

### RB-11 — 수정 source의 실패 관측·재시험을 Agent에 반환

- **유형/상태:** mandatory integration / SC-03 필수, 선행 구현 필요. **Wave/우선순위/크기/위험도:** 2 / P0 / M / 높음.
- **Finding / 문제 / 근거:** AF-03/04/06. 이전 build 시험과 새 설계 성공을 섞지 않아야 함(E06/E08).
- **기대 결과:** 실패→모델 일반 편집→RB-10 적용→새 source 시험의 닫힌 루프. **포함:** 시험 입력/oracle/source identity, structured failure/trace, 새 결과 다음 모델 호출. **제외:** Host의 정답 rung 주입·기존8개 결과 재사용·고정 장비 repair.
- **관련 파일/모듈:** native_projects.py SIM plan/receipts, Agent tool result, scenario fixtures. **선행:** RB-04/08/10. **후속:** RB-12/15/16. **병렬:** native/runtime 소유권 확인 후 RB-13/14 가능.
- **완료 조건:** 고정 반례가 잘못된 source에서 실패하고 Agent가 수정한 다른 source에서 기대 결과 충족. 시험 manifest가 실제 저장/readback source와 일치, stale evidence 거부.
- **검증 절차:** 실패/수정 transcript·source delta·실행 receipt·XG-SIM 입력/관측/판정 대조. 동일 가설 두 번 실패 시 중단 규칙 적용.
- **롤백:** 결과 adapter 변경만 역변경; 두 source와 모든 시험 증거 보존. **사용자 결정:** 이미 승인한 oracle를 새 결과에 맞춰 바꾸지 않음; 변경 필요 시 RB-01 재결정.

### RB-12 — 중단·재시작·경합에서 중복 적용 방지 인수

- **유형/상태:** bounded hardening / 선행 대기. **Wave/우선순위/크기/위험도:** 2 / P1 / M / 높음.
- **Finding / 문제 / 근거:** AF-03/09/15. lease/reconcile는 있지만 수정본 end-to-end 복구 증거 부족(E06/E11).
- **기대 결과:** 사용자 중단/Host 재시작/모델 timeout 이후 진실한 상태와 안전한 재개. **포함:** 요청 수신 vs 실제 정지 구별, 기존 reconciliation, 결과불명 읽기·격리, 단일 target 경합. **제외:** distributed scheduler·모든 crash point fault campaign.
- **관련 파일/모듈:** Host run/store/reconcile/app.py, MCP operation journal, Agent cancellation. **선행:** RB-03/09/10/11. **후속:** RB-15/21. **병렬:** RB-13/14 가능, app.py 단일 소유.
- **완료 조건:** 사전 고정한 모델 대기/적용 결과불명/저장 후 재시작 세 지점에서 중복 apply 0, 원본 불변, 확정 요구 보존, 상태와 receipt 일치. 안전한 다른 읽기는 계속 가능.
- **검증 절차:** 격리 작업본과 fixed interruption fixtures, lease/run/toolCallId 로그 비교. 프로세스 종료는 해당 인수 범위 승인 후 수행.
- **롤백:** lifecycle 연동만 역변경, DB/journal 삭제 없이 기존 recovery 사용. **사용자 결정:** 테스트 프로세스 중단 시점·보존 위치 승인.

### RB-13 — native 심볼·주석·주소 타입을 저장 산출물에 유지

- **유형/상태:** implementation / 요구 확정 대기. **Wave/우선순위/크기/위험도:** 2 / P1 / M / 중간.
- **Finding / 문제 / 근거:** AF-05. 이전 결과 SymbolsCount=0, 의미/주소 타입 품질 부족(E08).
- **기대 결과:** XG5000에서 다시 열어 읽을 수 있는 래더. **포함:** 지원 native 심볼/주석 전송·저장/readback, 수치/bit 타입 적합성, unused 선언 구별. **제외:** 단순 예쁜 웹 preview만·전체 UI 재디자인·미지원 symbol API 추측.
- **관련 파일/모듈:** native compiler/symbol mapping, MCP/CLI 지원 symbol API, Host readback. **선행:** RB-01/06/10. **후속:** RB-15/16. **병렬:** RB-11/14와 파일 소유권 분리 시 가능.
- **완료 조건:** 합의 프로젝트의 I/O·내부 상태·타이머/카운터에 의미 있는 이름/설명과 맞는 address type, 저장/재열기 뒤 보존. unsupported 기능은 인수 전에 드러냄.
- **검증 절차:** native 심볼 수·이름/주소/주석 대응과 rung 위치를 재열기 readback 및 엔지니어 검토로 확인. schema 통과만으로 닫지 않음.
- **롤백:** metadata 연동만 역변경, 사용자 프로젝트·의미 있는 주석 보존. **사용자 결정:** 명명/설명 기준과 지원 CPU는 RB-01.

### RB-14 — 채팅에 목표·진행·산출물을 우선 표시

- **유형/상태:** implementation / 상태 계약 대기. **Wave/우선순위/크기/위험도:** 2 / P1 / M / 중간.
- **Finding / 문제 / 근거:** AF-05/06. planning 중심 상세 검토가 사람의 핵심 흐름을 가림(E13,사용자 UX 지시).
- **기대 결과:** 일반 대화에서 이해·개입할 수 있는 실행 UX. **포함:** 목표/현재 작업/짧은 결과/산출물 열기, tool 선택 vs 시작, 취소 수신 vs 정지, UUID/JSON 접기, 필요한 추가 확인. **제외:** 모든 상세 삭제·권한 버튼 숨겨 자동 승인·새 관리 dashboard.
- **관련 파일/모듈:** App.tsx, NewProjectPlanReviewPanel.tsx, 실행 카드/technical disclosure tests. **선행:** RB-08/09 계약. **후속:** RB-15. **병렬:** native/core와 가능, Web 파일 단일 소유.
- **완료 조건:** 기본 화면에 내부 ID·긴 raw 객체가 불필요 노출되지 않으며 상세 접근 가능. keyboard로 결과 열기/중단/후속 입력 가능. 실제 Host 상태와 문구 일치.
- **검증 절차:** typecheck/build/disclosure test 후 실제 브라우저에서 빈 시작·진행·실패·완료·중단 상태의 화면/키보드 여정 검토. 현재 화면 접근 안 되면 시각적 인수 blocked.
- **롤백:** UI 변경만 역변경, 대화/설정 데이터 보존. **사용자 결정:** 대표 화면 승인; backend 권한 범위는 D-02 유지.

### RB-15 — 자연어 대동인덱스와 기존 프로젝트 여정 인수

- **유형/상태:** acceptance / 선행 대기. **Wave/우선순위/크기/위험도:** 2 / P1 / M / 높음(거짓 합격 방지).
- **Finding / 문제 / 근거:** AF-04/05/06/07. 내부 성공과 실제 사용자 완료가 다름(E08/E15).
- **기대 결과:** RB-01 corpus의 신규·실패수정·기존설명·후속/복구에 대한 재현 가능한 결과 manifest. **포함:** 일반 요청·질문·작성·native 적용·재열기·합의시험·가독성·중단 확인. **제외:** 개발자가 몰래 작업물 수리·합격시키기 위한 요구 변경·범위 밖 새 기능.
- **관련 파일/모듈:** 제품 전체 사용자 경로, 인수 fixture/증거 디렉터리. **선행:** RB-01/03/05/06~14; 최종 승인에는 RB-17/18/19도 필요. **후속:** RB-16/24, 최종 Goal 판정. **병렬:** provider/security 결과 수집과 독립 가능.
- **완료 조건:** 정한 반복 수에서 모든 필수 oracle 만족, 원본/물리 쓰기 0, 수동 정책 문구/ID 입력 0, 개입은 허용 예산 안에서 공개, 소스-증거 일치. 실패가 있으면 pass로 닫지 않음.
- **검증 절차:** 실제 Web/XG5000/XG-SIM에서 처음 요청부터 실행; model/version/시간·transcript·source/receipt·기대/실측·native 검토를 한 manifest로 비교. 확인된 결함은 별도 bounded 작업으로 분리.
- **롤백:** 시험 작업본/이력은 보존, 원본에 영향 주지 않음; 인수 실패를 코드 revert로 숨기지 않음. **사용자 결정:** 결과와 품질 최종 확인.

### RB-16 — 비인덱스 장비에서 일반 도구 루프 인수

- **유형/상태:** acceptance / 선행 대기. **Wave/우선순위/크기/위험도:** 2 / P1 / S / 중간.
- **Finding / 문제 / 근거:** AF-01/02/04. 장비 전용 rule/template가 성공 원인인지 분리해야 함(E04/E05).
- **기대 결과:** RB-01에서 고른 다른 제어 구조 사례의 동일 루프 성공/실패 증거. **포함:** 지원 명령 subset 내 새로운 요구와 독립 oracle, 최소 한 후속 수정. **제외:** 새 장비 수리 분기·새 vendor·성공할 때까지 사례 바꾸기.
- **관련 파일/모듈:** 기존 tool loop/인수 corpus만. **선행:** RB-01/06/07/11/13/15. **후속:** 최종 Goal 인수. **병렬:** RB-15 환경 공유 시 직렬, 독립 분석은 가능.
- **완료 조건:** 같은 도구·권한/검사 경계로 작성→적용→검증·후속 수정 완료. 과정에 전용 rung 생성/repair 예외 없음.
- **검증 절차:** 선택 사례·실제 transcript/소스·판정 manifest와 코드 diff를 대조. 실패는 원인/지원 범위로 기록하고 임의 사례 교체 금지.
- **롤백:** 인수 산출물 보존, 제품 원본/공유 설정 변경 없음. **사용자 결정:** 다른 사례는 RB-01에서 확정.

### RB-17 — OpenAI-compatible 지원 프로필의 도구 왕복 검증

- **유형/상태:** compatibility verification 및 확인된 계약 결함 수정 / 지원 결정 대기. **Wave/우선순위/크기/위험도:** 3 / P1 / S / 중간.
- **Finding / 문제 / 근거:** AF-11/09. URL/client 존재가 모든 provider 호환 증거는 아님(E12).
- **기대 결과:** 지원 endpoint별 transport/tool/error/cancel 능력 표. **포함:** RB-01 선정 서버, `100.64.0.1`의 승인된 실제 설정 포함 여부 확인, streaming/tool-call/result/오류/취소. **제외:** 모든 모델 동일 PLC 성능·새 모델 설치/유료 호출 무단 추가.
- **관련 파일/모듈:** model_connection.py, runtime model adapter, provider contract tests. **선행:** RB-01/03/07. **후속:** RB-18/최종 인수. **병렬:** native 작업과 가능, 같은 모델 서버 동시 부하는 피함.
- **완료 조건:** 지원 표의 필수 프로필에서 도구 왕복·오류 전달·취소 성공, unsupported는 사전에 명시. PLC 설계 품질은 RB-15/16과 구별.
- **검증 절차:** 고정 최소 도구 작업과 stream/nonstream 지원 모드, 알려진 오류·중단 fixture를 각 서버에서 실행해 request/result shape 대조. secret는 redaction.
- **롤백:** 확인된 adapter 수정만 역변경, 사용자 endpoint/key 설정 보존. **사용자 결정:** 실제 시험할 endpoint/모델·비용 범위.

### RB-18 — 200k 문맥 경계와 작업 정보 보존 검증

- **유형/상태:** technical verification / 의미·서버 능력 결정 대기. **Wave/우선순위/크기/위험도:** 3 / P1 / S / 중간(시간/메모리).
- **Finding / 문제 / 근거:** AF-11/15. 196608/150000 상수가 사용자 200k 요구를 증명하지 않음(E12).
- **기대 결과:** 실제 capacity와 장문/compaction 후 작업 정보 보존 결과. **포함:** 합의 input/total·출력 reserve·초중후반 요구/참조 조회, 경계 초과 처리, 복구. **제외:** 8k로 요구 대체·200k 한 번 출력·불가능 HW 지원 주장.
- **관련 파일/모듈:** runtime context/history/retrieval, model profile·인수 fixture. **선행:** RB-01/03/17. **후속:** 최종 인수/RB-21. **병렬:** 다른 모델 부하와 직렬, 독립 code review 가능.
- **완료 조건:** 합의한 정확한 크기에서 요청 수용, 정보 보존 oracle 만족, 예산 내 처리 또는 요구 미충족 명시. context를 조용히 버려 통과 금지. 서버 capacity가 부족하면 이 작업은 blocked/실패이지 완료 아님.
- **검증 절차:** tokenizer 기준 길이·실제 server capacity·input/output usage, 위치별 조회 결과, compaction 전후 요구와 source reference를 기록. 새 증거 없는 재시도 최대 두 번.
- **롤백:** 시험 프로필 변경만 복구, 사용자 모델 설치/서버 설정은 별도 승인 없이 변경하지 않음. **사용자 결정:** 200k 단위/정의·예산·필요 시 서버 profile 변경.

### RB-19 — 모델 key/header를 OS-backed secret 저장으로 분리

- **유형/상태:** security implementation / 저장 정책 확인 대기. **Wave/우선순위/크기/위험도:** 3 / P1 / M / 높음(credential 이전).
- **Finding / 문제 / 근거:** AF-08. model_connection/store의 평문 secret_payload_json(E10).
- **기대 결과:** DB가 secret의 opaque reference만 보유하고 필요한 실행 경로만 복호/조회. **포함:** 지원 Windows secret 보호, 기존 credential 이전/실패 보존·재입력 절차, API/log/export redaction. **제외:** 실제 사용자 key 읽기·공유·무단 회전·백업 무단 삭제.
- **관련 파일/모듈:** model_connection.py, store settings schema 접근부, credential tests. **선행:** RB-02 및 보호/이전 방식 결정. **후속:** 최종 인수/RB-20. **병렬:** core와 별도 모듈 가능.
- **완료 조건:** 가짜 key/header로 저장·재시작·사용·삭제·이전실패 시험 통과, DB/API/log/export에 평문 dummy secret 없음, 접근 주체/복구 절차 명확. 실제 데이터 이전은 승인 후만.
- **검증 절차:** 테스트 secret로 at-rest/응답/로그 검증, vault 실패/잠김 fixture, 이전 전후 rollback rehearsal. 사용자 실제 secret 출력 금지.
- **롤백:** 데이터 보존 계획에 따라 설정/참조를 복원하거나 사용자 재입력, 평문 재노출 fallback 금지. **사용자 결정:** OS 저장 방식과 실제 credential 이전 시점/백업 처리 승인.

### RB-20 — 데이터별 보존·내보내기·복구 정책 결정

- **유형/상태:** decision / 담당자 결정 대기. **Wave/우선순위/크기/위험도:** 3 / P2 / S / 중간.
- **Finding / 문제 / 근거:** AF-12/08. PRD 미결 정책과 서로 다른 원본/DB/cache/secret 수명(E11/E19).
- **기대 결과:** 데이터 분류별 소유자·보존·export/delete/backup·업데이트 보존 계약. **포함:** Agent cache 삭제가 사용자 원본/근거에 영향 없다는 oracle, 로그 redaction, 실제 구현 gap 목록. **제외:** 이번 결정 작업에서 데이터 삭제·모든 lifecycle 구현.
- **관련 파일/모듈:** PRD privacy delta, store/workspace/retrieval/packaging 계약. **선행:** RB-02/19의 설계. **후속:** 별도 lifecycle 구현 작업, RB-22 출시 기준. **병렬:** 기능 작업과 가능.
- **완료 조건:** 각 데이터 종류의 책임/기간/삭제/내보내기/복구/secret 포함 여부가 결정, 미결은 출시 blocker로 표시. 구현 필요 사항은 증거 기반 후속 작업으로만 생성.
- **검증 절차:** 데이터 flow table과 실제 저장 위치 대조, backup/restore/Agent index 폐기 시나리오의 기대 결과를 검토.
- **롤백:** 정책 제안만 취소, 실제 데이터 무변경. **사용자 결정:** 보존 기간·backup/export·telemetry 기본값.

### RB-21 — desktop 장기 실행의 시간·이벤트·복구 측정

- **유형/상태:** bounded measurement / 예산 대기. **Wave/우선순위/크기/위험도:** 3 / P2 / S / 낮음~중간.
- **Finding / 문제 / 근거:** AF-15/11. event polling/lease 기반은 있으나 구간별 지연과 backlog 영향 미측정(E11/E12).
- **기대 결과:** 모델·도구·대기·UI 이벤트 구간의 병목 및 예산 준수 결과. **포함:** 고정 desktop/run 크기, event history batch, 중단 후 backlog, redacted 로그 보존량. **제외:** 미확인 병목의 선제 리팩터링·분산 서비스.
- **관련 파일/모듈:** Host events/store/config, runtime timing·인수 report. **선행:** RB-01/12/18. **후속:** 근거가 생긴 성능 작업/RB-24. **병렬:** UI/native 정밀 인수와 부하 공유 금지.
- **완료 조건:** 동일 profile에서 측정 재현, budget 대비 결과와 병목 근거 존재; 성능 미달을 성공으로 바꾸지 않고 후속 수정 범위를 특정.
- **검증 절차:** 승인된 고정 duration/workload로 event latency·run 단계 시간·memory/log 크기·중단 후 상태 측정. 관측 변경은 기능과 분리.
- **롤백:** 테스트 부하 중단·진단 설정 복구, 사용자 이력 삭제 없음. **사용자 결정:** 성능/보존 예산은 RB-01/20.

### RB-22 — 오프라인 복구와 production release gate 분리 확정

- **유형/상태:** release decision / 접근·소유자 결정 대기. **Wave/우선순위/크기/위험도:** 3 / P2 / S / 중간.
- **Finding / 문제 / 근거:** AF-14/09. BOM/서명 검증은 있으나 실제 배포·CLI 공급·license 책임 미확인(E19).
- **기대 결과:** 정확한 artifact 조합, 서명·라이선스/notice 담당, clean-machine/update/rollback 인수 목록. **포함:** 지원 runtime, CLI backend 공급 관계, package handshake/readback, 사용자 데이터 보존, remote CI evidence 필요 목록. **제외:** production 서명·배포 자체·법적 적합성 단정.
- **관련 파일/모듈:** PRODUCT_ARCHITECTURE, distribution docs, BOM/packaging scripts/manifests. **선행:** RB-03/20. **후속:** 별도 출시 Goal/RB-23. **병렬:** offline core와 가능.
- **완료 조건:** 개발 MVP와 production 합격 항목이 분리되고 artifact/버전/hash/contract/서명·소유 책임에 미결 상태가 드러남. 미확인은 출시 blocked로 기록.
- **검증 절차:** 실제 manifests·BOM 검증 함수·license/notice·PRD REL-01~05를 항목별 대조. remote 증거는 접근 가능 시 연결하고 아니면 미확인 유지.
- **롤백:** 출시 정책 문서만 역변경, 기존 artifact 무변경. **사용자 결정:** 배포·서명 주체/저작·라이선스·출시 범위.

### RB-23 — CLI 역의존 추출의 첫 두 경계만 계획

- **유형/상태:** optional design / core 이후. **Wave/우선순위/크기/위험도:** 4 / P3 / S / 낮음.
- **Finding / 문제 / 근거:** AF-14. legacy/backend 역의존으로 release independence 미완료(E03/E19).
- **기대 결과:** 대표 두 command의 소유·입출력·import dependency와 bounded extraction 작업안. **포함:** 호출 graph와 contract fixture 위치. **제외:** CLI 전체 이관·MCP 전면 리팩터링·현 core 복구 선행 강제.
- **관련 파일/모듈:** CLI legacy.py/open_project_entry.py, MCP native backend, architecture docs. **선행:** RB-22와 core 인수. **후속:** 별도 작은 extraction PR 결정. **병렬:** 독립 읽기 가능.
- **완료 조건:** 두 경계에 중복 책임·BOM 영향·회귀 oracle·rollback 정의; 큰 작업은 실행 전에 추가 분할.
- **검증 절차:** source import/call과 실제 계약 fixture 대조, release 담당 리뷰.
- **롤백:** 설계안만 취소. **사용자 결정:** 해당 독립성 작업의 사업 우선순위.

### RB-24 — 고정 pilot 결과를 제품 지표로 요약

- **유형/상태:** optional measurement / 인수 후. **Wave/우선순위/크기/위험도:** 4 / P3 / S / 낮음.
- **Finding / 문제 / 근거:** AF-06/10/15. commit/test/run 수가 사용자 가치 지표를 대신해 왔음(E08/E14).
- **기대 결과:** 고정 pilot의 목표 완료·사용자 개입·재시도·구간 지연/품질 요약. **포함:** 이미 동의된 인수 기록을 집계, 실패도 포함. **제외:** 새 telemetry 수집·민감 데이터 업로드·성공 사례만 선택.
- **관련 파일/모듈:** 인수 manifest/제품 지표 문서. **선행:** RB-15/16/21 및 데이터 사용 동의. **후속:** 다음 제품 우선순위 결정. **병렬:** 기능 코드와 독립.
- **완료 조건:** 분모/대상/버전/수동 개입/미확인이 명확하고 내부 test 수와 구별; 새로운 기능 완료 주장 없음.
- **검증 절차:** 원 manifest와 표의 수치/판정 샘플 대조, 동의 범위 확인.
- **롤백:** 집계 문서만 수정, 원본 기록 보존. **사용자 결정:** pilot 대상/결과 공유 범위.

## 시작 가능한 작업과 멈출 조건

다음 구현 요청이 오면 RB-01의 미결 제품 조건, RB-02의 **확정된 구조 원칙을 위한 인터페이스 명세**, RB-03 환경을 짧게 정리한다. 제거할지 다시 감사하는 작업은 없다. 선행 조건은 해당 하위 작업에만 적용한다. 예를 들어 장비 운전 의미가 미결이어도 일반 도구 선택 계약 명세/회귀는 작성할 수 있고, remote issue 접근 부재가 로컬 구조 교체를 막지 않는다. 미확인 가설만으로 삭제 대상을 늘리지는 않는다.

동일 명령·수정 방향·가설이 새로운 정보 없이 두 번 실패하면 세 번째 반복을 하지 않는다. 실패 source/명령/exit/증거·새 가설을 기록하고, scope 변경이나 외부 권한이 필요하면 사용자에게 결정만 요청한다. 이번 Goal의 마지막 행동은 이 백로그 구현이 아니라 감사 종료다.
