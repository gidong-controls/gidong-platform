# 03. 실제 아키텍처와 핵심 코드 감사

2026-09-20. 기준 HEAD와 행 번호는 [E01~E13](10_EVIDENCE_INDEX.md). 코드 읽기 결과와 실행 증거를 구별한다. 이 감사는 구현을 변경하지 않는다.

## 현재 구성과 책임

| 구성 | 실제 역할 / 주요 위치 | 보존할 경계 |
|---|---|---|
| Platform Launcher | 앱 진입·sidecar 구성·BOM/배포 검사 | 사용자 실행 환경과 artifact 조합 |
| Web | React/TypeScript 대화, 프로젝트, 계획/실행 상태, 설정; `web/src/App.tsx`, `components/` | 사람이 읽는 진행/결과와 제어 |
| Agent | Pydantic AI 모델 호출·도구 선택·planning·retrieval; `gidong-agent/src/controlstudio_agent/runtime.py` | 모델 추론/제안. DB·MCP·native 직접 권한 없음 |
| PLC MCP | vendor 도구·대상 확인·operation journal·lock·실행 결과; `gidong-plc-mcp` | native 실행 truth와 대상/전제 확인 |
| XG5000 CLI | 이관된 명령의 process/JSON 경계; 로컬 `controlstudio-xg5000-cli`(remote 이름은 controlstudio-ls-xg5000-cli) | vendor transport. 일부 native backend는 MCP 역의존 |
| XG5000 / XG-SIM | 사용자에게 전달할 실제 프로젝트 편집/재열기·오프라인 실행 | UI에 성공이라고 표시하는 것과 별개의 실제 결과 |

근거: `docs/PRODUCT_ARCHITECTURE.md:17~96,132`, E03/E19. CLI 분리 완료 또는 모든 도구의 CLI 이관을 주장하지 않는다.

## 요청·데이터 흐름

```text
사용자 대화 → Web → Host: conversation / run / task scope
                         ↓ Agent protocol (NDJSON)
                    모델이 tool 선택
                         ↓ ExternalToolRequest
                    Host ExecutionBroker
                    ├─ workspace 조회·편집·검사
                    └─ MCP → CLI/기존 adapter → XG5000/XG-SIM
                         ↑ 결과·오류·receipt
                Host 기록 → Agent 다음 판단 / UI SSE
```

현재 신규 planning은 이 일반 루프와 별도 분기이고, design workspace valid 뒤 도구 선택을 publish로 제한한다. native 실행 서비스는 이미 있지만 설계 수정→같은 작업본에 새 source 적용→그 source 재시험의 연속 사용자 흐름은 미입증이다. 이 차이가 AF-01/03의 핵심이다.

### 주요 API·상태·영속성

- Host API: `app.py:7460` session, `7491` refresh, `8081` health, `8093` status, `8693` bootstrap, `13284` events(SSE), `13343/13356` model settings, `14569` XG5000 instances. 상세 계약은 `api_models.py`와 tool manifest가 결정한다. 모델에 HTTP API 전체를 권한으로 내주지 않는다.
- Store: SQLite의 conversations/messages/runs/events/evidence, workspace·mission/checkpoint, 변경 transaction·native receipt·lease·model connection. `store.py:244,354,400,430,559,618`; WAL/FK/busy timeout 및 `BEGIN IMMEDIATE`는 `936~955`. 영속 기반은 이미 있다. 모델 retrieval index는 원본 데이터의 권위가 아니다(E03/E11).
- 비동기 작업: sidecar 모델 호출과 Host broker 요청, run/event 기록, native operation과 GUI 단계. `app.py:7084` startup reconciliation; 중단/결과불명 복구는 작업 성공과 구별해야 한다.
- 캐시: Agent의 disposable retrieval/context와 runtime history compaction(E12). 모델 KV cache나 서버의 실제 memory 동작을 이번에 측정하지 않았다. 캐시가 source/receipt의 권위가 되어선 안 된다.
- 동시성: DB transaction/lease, native operation journal, UI/file-dialog 직렬화가 존재한다(`app.py:4187`). 같은 desktop target의 직렬화는 설계 의도일 수 있어 분산 서버처럼 평가하지 않는다.
- 관측: run/toolCallId/events/evidence/receipt가 있다. SSE는 제한된 batch 조회와 polling(`store.py:3090`, `app.py:13301`, config:54). 시간 지표·장문/장기 실행·로그 보존 인수는 부족하다.

### 인증·인가와 배포

Host loopback/session·launcher challenge 경계, endpoint 전송 동의, broker 대상 binding과 MCP precondition/journal을 유지한다(E11). 모델 prompt/문서가 실제 대상 권한이나 원본 수정 권한을 획득하지 않는다. 물리 PLC는 복구 MVP 밖이다. 로컬 credential 저장은 별도 결함(AF-08)이며 이 경계가 있다는 이유로 무시하지 않는다.

배포는 독립 artifact/BOM 조합과 launcher의 검증 경로를 사용한다. 개발 환경은 npm/uv/Python, CI는 Python3.12/Node22지만 설치 환경에 다른 버전도 있다(E17). 실제 서명·clean-machine 설치·업데이트 성공은 이번에 확인하지 않았다(AF-14).

## 핵심 사용자 흐름과 코드 연결

| 사용자 흐름 | 확인한 코드 경로 | 현재 한계 |
|---|---|---|
| 기존 프로젝트 이해 | Host/broker→Agent 일반 engineering loop→MCP 조회 | 기존 읽기 성공 이력 있음; 대표 corpus 설명 품질은 추가 인수 필요 |
| 신규 설계 | App.tsx:828 → runtime.py:16686 → planning workspace tools:19365 → router:183 | valid/published 상태에 의해 다음 행동 강제 |
| 래더 변환 | native_projects.py `_compile_native_program`:2394~2653 | 언어 lowering와 장비 설계 정책이 결합 |
| 생성/저장/재열기 | native project service + MCP journal/adapter | 과거 실제 성공 있음, 이것만으로 요구 동작 합격 아님 |
| 저장 build 재시험 | native_projects.py:3958~4108,4406,4990 | immutable source; binding-only 검토 overlay |
| 자연어 후속·중단·재개 | run/event/store/reconcile + Agent loop | 새 source 적용/중단 증거 조합을 끝까지 인수하지 못함 |

## Finding 상세 — 제품/구조

심각도: P0=복구/제품 합격을 먼저 막는 항목, P1=핵심 여정/신뢰에 중대한 영향, P2=출시·운영 개선. 발생 가능성은 관찰/정적 조건 기준이며 실측 확률이 아니다. 품질/보안 Finding은 [05](05_QUALITY_COVERAGE_MATRIX.md)에 이어진다.

### AF-01 — 신규 planning에서 모델 주도 작업 루프가 단절됨

- **문제·증거/위치:** runtime.py:183~256,3259~3335,16686,19365,19411,19482,19568. valid 상태에서 publish만 허용하며 planning은 native 실행을 금지하는 별도 경로다. `test_workspace_stable_workbench.py:31~35`도 이 기대를 고정(E04).
- **사용자/사업 영향:** 유효한 계획 제출 이후 실제 도구 결과로 판단·수정하는 경험이 이어지지 않아 에이전트의 핵심 가치가 약해진다. **기술 영향:** 일반 loop와 planning이 다른 완료/도구 제어를 갖는다.
- **심각도/가능성/신뢰도:** P0 / 해당 분기에서 높음 / 확인됨·높음.
- **근본 원인:** schema/workspace 유효성을 모델 행동 종료 조건으로 사용한 경로 설계. 역할이 많다는 문제가 아니다.
- **임시 대응:** planning publish를 제품 완료로 보고하지 않음. **근본 해결:** 기존 일반 loop에 설계 읽기·부분 편집·검사·발행을 도구로 연결하고 모델 선택과 Host 권한을 분리.
- **요구/작업:** EXE-01/02/05/08, U-AUT → RB-04/07/08. 새 framework나 장비별 수리 workflow 도입은 해결책이 아니다.

### AF-02 — compiler에 언어 규칙과 특정 설계 정책이 혼재

- **문제·증거/위치:** native_projects.py:2394~2422(출력 형태),2477~2495(permit/mode/step 형태),2558~2577(SET/RST 목적 제한),2579~2653(sequence). api_models.py:402~457의 제한된 action 집합(E05).
- **사용자/사업 영향:** 사용자의 정당한 설계 선택이 현재 틀에 맞지 않아 막힐 수 있다. **기술 영향:** compiler를 그대로 재사용하면 일반 Agent가 지원 표현 안에서도 특정 정책을 따라야 한다.
- **심각도/가능성/신뢰도:** P0 / 다른 제어 구조에서 높음 / 확인됨·높음.
- **근본 원인:** 표현 언어의 유효성과 권고 설계 형태를 한 함수에서 판정.
- **임시 대응:** 지원 표현·정책 차이를 명시하고 모든 reject를 모델 무능으로 취급하지 않음. **근본 해결:** 타입/참조/주소/명령 legality/lowering은 유지; 요구·위험 기반 진단은 별도 결과로 제공. 실제 불법 명령을 허용하는 변경은 금지.
- **요구/작업:** EXE-08,U-AUT → RB-02/04/06/16. 전 LS instruction set 구현은 이번 MVP와 별도.

### AF-03 — 수정된 소스의 적용·저장·재시험 연속성이 입증되지 않음

### AF-04 — 합격 보고 범위를 벗어나는 최종 래더 반례

- **문제·증거/위치:** 이전 감사:149,168(E08)에서 최종 39-rung 산출물의 수동 운전→자동 선택 및 수동 버튼 유지 상태의 정지 해제 반례를 정적 추적. 09-15 실제 8개 XG-SIM 시나리오가 이 조합을 포괄하지 않는다.
- **사용자/사업 영향:** 동작은 해도 운전 요구에 맞는 PLC인지 신뢰할 수 없다. **기술 영향:** schema/build/SIM 일부 성공은 상태 전이와 입력 조합의 correctness를 보증하지 않음.
- **심각도/가능성/신뢰도:** P0 / 기술된 입력 조합에서 높음 / 이전 정적 감사 확인됨·높음. 이번 실기 재현 아님.
- **근본 원인:** 요구–시나리오–oracle–최종 source 연결이 충분하지 않음. LLM 품질만으로 환원 불가.
- **임시 대응:** 현재 산출물을 설비 적용 합격품으로 취급하지 않음. **근본 해결:** 합의한 운전 의미를 독립 oracle로 만들고 실패 관측→일반 편집→수정본 재시험을 입증. 정답 rung을 Host가 자동 삽입하는 대체는 금지.
- **요구/작업:** ENG-02,EXE-08,U-NAT → RB-01/04/11/15/16.

### AF-05 — native 산출물 가독성과 사용자 화면 정보 계층 부족

- **문제·증거/위치:** 이전 감사:202/210의 `SymbolsCount=0`, 불필요한 신호/주소 타입 검토 지적. App.tsx:828, `components/NewProjectPlanReviewPanel.tsx:1465,1488`의 planning 중심 흐름(E08/E13). 현재 화면은 E20으로 미확인.
- **사용자/사업 영향:** 엔지니어가 코드 의미·변경 내용을 읽고 유지보수하기 어렵다. **기술 영향:** 생성 객체/검증 목록과 최종 native 설명 정보 사이의 추적이 부족.
- **심각도/가능성/신뢰도:** P1 / 과거 산출물에서 관찰 / native 증거 높음, 현재 시각적 UX는 미확인.
- **근본 원인:** 내부 상태/검증 객체 중심 인수와 사용자 산출물 품질 기준의 불일치.
- **임시 대응:** 완료 답변에 파일·의미·검증 범위를 먼저 표시. **근본 해결:** native 심볼/주석/타입을 저장·재열기로 검증, UI는 요약·결과 중심과 선택적 technical details.
- **요구/작업:** UX-03,U-NAT → RB-13/14/15.

### AF-06 — 결과 단위와 활동 단위의 완료 판정 불일치

- **문제·증거/위치:** PRD:487/510에는 지표/기준이 있으나 planning publish, build, 8개 시나리오, 문서 수정이 전체 사용자 목적 완료를 대신할 수 없다(E02/E04/E08/E15/E21).
- **사용자/사업 영향:** “완료” 뒤에도 동일 사용자 질문과 품질 불만이 계속된다. **기술 영향:** 경로마다 다른 success 의미를 상위 목표에 합성할 계약이 약함.
- **심각도/가능성/신뢰도:** P0 / 기록에서 반복 / 높은 가능성·높음. 전체 과거 formal Goal은 미확인.
- **근본 원인:** 범위·oracle·native 증거를 고정하기 전에 활동을 완료로 보고하고 지적마다 범위를 다시 열기.
- **임시 대응:** 감사 완료/코드 완료/실기 일부/제품 인수를 분리 표기. **근본 해결:** 고정 corpus·요청별 evidence contract·명시적 중단 조건, 다음 Goal 사용.
- **요구/작업:** EXE-05,U-DONE → RB-01/08/15 및 [09](09_REPLACEMENT_GOAL.md).

### AF-13 — 설계 IR와 외부 native 변경의 권위·drift 경계 미확정

- **문제·증거/위치:** E06/E07 및 이전 설계 반대 검토 R4. desired design과 실제 native working copy는 독립 revision이며 지원 명령 전체의 lossless import는 증명되지 않았다.
- **사용자/사업 영향:** 외부에서 고친 작업물을 오래된 IR로 덮을 위험. **기술 영향:** baseline/source identity와 import/merge 계약 없이는 일반 후속 편집이 안전하게 정의되지 않음.
- **심각도/가능성/신뢰도:** P1 / 외부 변경 시 조건부 / 설계 위험·중간. 실제 데이터 손실 사고 확인 아님.
- **근본 원인:** 기존 신규 설계와 기존 프로젝트 변경 경로의 source of truth가 다름.
- **임시 대응:** 불일치 시 자동 덮어쓰기 대신 상태 조회/사용자 선택. **근본 해결:** baseline 검증, 지원 subset의 lossless 편집 범위, native readback과 desired state의 분리 계약. 모든 native를 새 IR로 재작성하지 않음.
- **요구/작업:** WS-01/02,EXE-03/06 → RB-02/09/10.

## 판단

**조건부 실현 가능, 일부 핵심 경로의 구조 변경 필요(E).** 일반 Agent loop, Host 권위, 영속 상태, broker, 실제 native 실행 기반은 유효하다. 반대로 publish 강제, compiler의 일괄 설계 정책, 과거 build resume을 수정 실행으로 사용하는 가정은 재검토 대상이다. 코드를 모두 삭제하거나 완성품을 deterministic template로 바꾸는 방안은 증거로 지지되지 않는다. [복구 결정](07_RECOVERY_DECISION.md)과 [백로그](08_TASK_BACKLOG.md)가 구현 범위를 고정한다.
