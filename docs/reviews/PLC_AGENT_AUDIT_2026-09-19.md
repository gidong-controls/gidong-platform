# PLC 에이전트 현상태 감사 및 다음 구현 인계

기록 상태: 2026-09-19 당시 감사. 현재 상태·실행 계약·개발 순서는
[PRD](../../PRD.md)와 [구현 계획 §4·§5](../AGENT_PRODUCT_PLAN.md)를 따른다.
아래 미완료·다음 구현 표현은 해당 날짜의 조사 결과다.

작성: 2026-09-19. 대상: 현재 소스, 개발 DB의 실제 실행 기록, 최종 저장된 대동인덱스 프로젝트.

후속 보완: [총괄 설계 검토·공식 웹 벤치마킹](PLC_AGENT_ARCHITECTURE_2026-09-19.md).
이 감사의 코드/산출물 근거와 함께 읽는다. 외부 사례 대조, 모델과 Host의 책임 경계,
실행 소스의 권위, 장기 작업·모델 호환성·구조 시험은 후속 문서에서 구체화했다.

## 1. 결론

**현재 상태를 “일반 사용자가 맡기면 쓸 만한 래더를 완성하는 PLC 에이전트”의 합격 상태로 볼 수 없다.**

실제 XG5000 프로젝트 생성·저장·재열기와 XG-SIM 8개 시나리오 통과 기록은 있다. 이를 가짜 실행이나 전부 미구현으로 취급하는 것도 틀리다. 그러나 그 최종 설계에도 수동 운전의 경계 조건 결함이 남아 있고, 신규 설계 에이전트의 완료 조건은 사용자 목표 달성이 아니라 **검사를 통과한 설계 발행**에 가깝다. 실제 프로젝트의 심볼 표도 비어 있다.

| 평가 대상 | 판단 | 이유 |
|---|---|---|
| XG5000 연결·생성·저장·재열기 기반 | 동작 증거 있음 | 최종 native operation, 파일 해시, 시뮬레이션 receipt 존재 |
| 영속 작업물·부분 편집·도구 실행 기반 | 구현되어 있음 | draft/revision, read/edit/check, Host broker와 실행 이력 존재 |
| 대동인덱스의 제한된 8개 시험 | 과거 통과 확인 | 2026-09-15 실제 receipt 확인. 오늘 다시 실행한 것은 아님 |
| 최종 래더의 요구 충족·유지보수성 | 불합격 | 수동 운전 경계 조건, 미사용 선언, 빈 심볼 표 |
| 신규 요청부터 실행·수정·완성하는 자율 작업 흐름 | 불합격 | 발행 강제 라우팅, 설계/실행 분리, 외부 상세 수리 지시에 의존한 이력 |
| 다른 장비·다른 모델에도 일반화되는 PLC 에이전트 | 입증되지 않음 | 특정 index 역할·ID·시험에 강하게 결합. 독립 장비 실사용 근거 부족 |
| 현 시점 화면의 시각적 완성도 | 이번 감사에서 판정 보류 | 현재 native 화면을 직접 관찰하지 못함. UI 소스와 저장 산출물은 검토함 |

문제의 중심은 모델 크기나 토큰 한도 하나가 아니다. **작업 제어권, 실제 실행과 수정의 연결, PLC 동작 의미, 사람이 읽을 산출물**이 함께 부족하다. 검사·승인 문구를 더 추가하거나 대동인덱스 전용 수리 함수를 계속 늘리는 방식으로는 해결되지 않는다.

## 2. 감사 범위와 근거 수준

사용자의 이번 요청은 현상태 진단·문서화와 다음 새 채팅의 구현 준비다. 이번에는 제품 코드, DB, 래더, 원본 프로젝트, PLC를 변경하지 않았다. SSH 개발 논의는 범위 밖이다.

### 기준 소스

| 저장소 | 브랜치 / HEAD |
|---|---|
| `gidong-platform` | `dev` / `9e2b7eb` |
| `gidong-agent` | `dev` / `0410ed1` |
| `gidong-plc-mcp` | `dev` / `dd6b43e` |
| `controlstudio-xg5000-cli` | `main` / `00567b5` |

감사 시작 시 추적 파일의 미커밋 변경은 관찰되지 않았다. 일부 기존 임시 디렉터리는 ACL 때문에 열람하지 못했다. 원격 fetch나 push 상태 검증은 하지 않았다. 이 문서와 인계 문서 변경은 위 HEAD 이후의 문서 작업이다.

근거를 다음처럼 구분한다.

- **소스/산출물 확인:** 코드 분기, DB 상태, 실제 저장 파일에서 직접 확인한 사실.
- **정적 조건 재현:** 저장된 최종 래더 조건을 그대로 읽어 Boolean 값으로 계산한 반례. 새 XG-SIM 실행 결과와 혼동하지 않는다.
- **과거 실기 증거:** 기존 XG-SIM receipt와 파일 해시를 다시 확인한 것. 오늘 동일 시험을 재실행했다는 뜻이 아니다.
- **추가 확인 필요:** 실제 스캔·스텝 명령 의미나 새 화면 관찰을 요구하는 항목. 확정 결함으로 세지 않는다.

현재 브라우저 조회에서는 두 대상 탭이 연결 불가 제목을 반환했고, native 화면 확인용 computer-use helper는 복구 시도 후에도 응답하지 않았다. 따라서 **오늘 웹/XG5000 실사용을 다시 통과했다거나 현재 창이 어떻게 보인다고 주장하지 않는다.** 이 보조 도구 문제를 제품의 실행 불가능 증거로도 사용하지 않는다.

## 3. 실제로 완료된 것과 완료되지 않은 것

### 3.1 최종 프로젝트는 존재하며 과거 실기 통과도 존재한다

대상 대화는 `대동인덱스 실사용 재검증 · 신규 설계`다. 최종 native operation은 `COMPLETED`, `engineeredProgramReady=true`, `workspaceBound=true`다. 시뮬레이션 상태는 `verified`, trial은 `ACCEPTED`, `safeRestorationVerified=true`다.

최종 설계에는 IO 선언 36개, 래더 39개, control network 13개, 모드 3개, 시퀀스 2개, 알람 3개, 복구 규칙 3개, 안전 규칙 4개, 타이머 1개가 있다. 이 개수 자체가 과다하거나 틀렸다는 판정 근거는 아니다.

실제 `multi_mode_index/v1` receipt의 8개 결과:

| 시험 | 실제 확인한 범위 | 확인하지 않은 중요한 범위 |
|---|---|---|
| 자동 완료 6회 | 완료마다 카운트 증가, 완료 유지 시 추가 증가 방지, 반복 이동 | 준비 신호 소실, 시작 시 완료 센서가 이미 ON인 경우 등 |
| 모드 상호 배제 | **자동 운전 중** 수동 모드 요구를 거절하고 기존 모드 유지 | **수동 운전 중** 자동 모드 요구 |
| 운전자 정상 정지 | 자동 운전/이동 OFF | 수동 시작 버튼 유지 상태의 정지·해제 |
| 수동 버튼 유지 1회 이동 | 버튼 유지 중 1회 완료, 완료 해제 후에도 재이동 방지, 새 누름 시 두 번째 이동 시작 | 두 번째 이동 완료와 카운트 2까지의 검증, 짧은 누름 후 완료 |
| 정지 중 카운터 reset | stop/reset 후 카운터와 래치 상태 확인 | 수동 버튼 유지·다른 단계·다른 입력 순서와의 조합 |
| 비상정지 복구 | 자동 운전에서 원인·reset·새 시작 경로 | 수동 운전에서 같은 조합 |
| 도어 복구 | 자동 운전에서 원인·reset·새 시작 경로 | 수동 운전에서 같은 조합 |
| 타임아웃 복구 | 자동 운전에서 5초 미완료 및 복구 경로 | 수동 운전에서 같은 조합 |

receipt의 `coverage`에 선언된 signal/step/network ID가 나열되어 있다는 사실은 모든 분기와 입력 순서가 실행되었다는 뜻이 아니다. `outputBytesWritten=false`이며 입력 조작을 통해 시험했다. 물리 PLC 연결·쓰기는 없었다.

### 3.2 자율 완성의 증거로 쓰기 어려운 실행 이력

같은 설계 대화에 run 101개가 저장되어 있다: `COMPLETED` 12, `CANCELLED` 68, `FAILED` 21. 최신 draft는 revision 774이며 revision 기록은 0을 포함해 775개다. 이 중 서로 다른 전체 문서 해시는 367개이고, 직전 문서와 같은 해시인 기록은 368개다. 검사·상태 기록도 revision에 포함되므로 **774번 실제 래더를 고쳤다는 뜻은 아니다.** 취소와 개발 중 재시작도 포함되므로 12/101을 모델 성공률로 계산하지 않는다.

도구 선택 1,093건 중 `design.check` 339, `design.read` 198, `design.inspect` 103으로 합계 640건이다. 조사·검사가 필요한 것은 정상이나, 이 기록을 목표 달성의 대체 지표로 쓸 수는 없다.

후반 USER 역할 메시지에는 실제 일반 사용자 요구를 넘어 다음과 같은 상세 수리 지시가 들어 있다.

- sequence 128: 이전 스캔의 timeout done 때문에 reset 후 latch가 남는 원인과 수정 내용.
- sequence 130: 남은 수동 step의 재기동 문제, `manual_running` 허가 추가, reset 경로 수정 내용.
- sequence 131: “현재 프로젝트가 XG-SIM에서 실제 사용자 시나리오까지 통과하도록 이어서 완성해 줘.”

이 USER 역할 메시지의 작성자가 실제 사용자였는지 개발 보조 에이전트였는지는 역할 필드만으로 단정하지 않는다. 다만 **제품 외부에서 상세 PLC 수정 설명이 주입된 실행 이력**이므로, 평범한 최초 요청만으로 제품이 독립 완성했다고 주장할 근거는 아니다. 마지막 planning run도 sequence 131의 요청에 대해 설계 계획 발행으로 끝났다. 이후 native 실행은 별도 operation이다.

## 4. 핵심 아키텍처 진단

### 현재 구조

```text
채팅 / 신규 프로젝트 입력
  → Host: 요구·대화·영속 draft·실행 권한 관리
  → Agent: new_project_planning 분기
      inspect/read/edit → check
        ├─ invalid → 진단별 편집 또는 Host 지정 수리 도구
        └─ valid   → publish_review만 허용 → planning run 종료
  → UI: 설계 검토 / native 프로젝트 생성·시험 API
  → Host → MCP/CLI → XG5000 저장·재열기·XG-SIM
  → 실패 관측을 후속 planning 요청에 공급 가능
```

일반 engineering 경로에는 별도의 도구 선택 실행 루프가 이미 있다. SQLite 영속 상태, 부분 편집, Host broker, compiler, native 실행 서비스도 있다. **없는 프레임워크를 처음부터 만들 문제가 아니라, 신규 설계의 작업 제어와 실제 실행을 기존 기반 위에서 연결해야 하는 문제다.**

### F1 / P1 — 검사를 통과하면 읽기·수정을 금지하는 라우터

근거: Agent `runtime.py`의 `_route_design_workspace_tool` 183행, 특히 254~256행; `_RequireWorkspaceToolUntilPublished.prepare_tools` 3315행.

```python
# 현재 실제 분기
if edit_applied:
    return {"design.check"}
if latest_tool == "design.check" and status == "valid":
    return {"design.publish_review"}
```

반면 같은 파일 19411행의 지시는 “valid는 구조/컴파일 일관성일 뿐이며, 발행 전에 design.read로 동작을 검토하고 진단이 없어도 design.edit로 수정하라”이다. 실제 도구 목록을 필터링하므로 프롬프트를 더 강하게 써도 이 지시를 실행할 수 없다. 실제 함수의 위 입력별 반환 집합도 확인했다.

**영향:** 검사기가 놓친 의미 오류를 에이전트가 알아도 valid 이후 조사·수정할 수 없다. 더 좋은 모델이나 더 긴 컨텍스트가 이 강제를 없애지 못한다.

**수정 방향:** check 결과는 관측과 진단이지 에이전트 사고의 종결 명령이 아니다. 읽기·비교·부분 수정은 유지하고, publication 여부는 요청 범위와 남은 작업을 근거로 판단하게 한다. revision 충돌·권한·일관성 검사 자체를 제거하자는 뜻이 아니다.

### F2 / P1 — “설계 발행 완료”와 “사용자 작업 완료”가 분리됨

근거: `runtime.py` 16686행 workflow 분기, `_run_design_workspace` 19299행, 19568행의 발행 후 중단; Host `app.py` 10100행 부근 planning 도구 결속, 14935행 native 생성 API, 16081행 native resume API; `NativeProjectPanel.tsx` 1901행 API 호출.

planning 내부에서는 design 도구를 사용하고, XG5000 생성·실행은 별도 UI/API 흐름이다. 이전 native 실패 관측을 다음 planning run에 넣는 경로는 있지만, 그것만으로 **생성→실행→관측→기존 작업물 수정→재실행**이 한 작업의 자율 루프로 닫히지는 않는다.

**수정 방향:** 기존 native 서비스와 일반 engineering 도구 루프를 재사용한다. 구현·시험을 요청받은 작업에서는 동일 작업물·revision·native operation을 이어서 다룰 수 있어야 한다. 설명만 요청하면 설명에서 끝나며, 모든 요청에 생성·시뮬레이션·승인을 강요하지 않는다. 실제 권한이나 중요한 선택이 필요한 경우만 사용자에게 묻는다.

### F3 / P1 — 도메인 수리 코드·진단·시험이 같은 잘못된 가정에 결합

근거: Host `planning_drafts.py` `_index_mode_latch_repair_changes` 4498행, `_index_acceptance_behavior_diagnostics` 3295행; native acceptance runner.

- 모드 수리 함수는 자동·수동 양쪽에 같은 `bindings.signals.running`을 사용한다. 최종 binding은 `auto_running`이다. 별도의 `manual_running`은 여기서 모드 전환 금지 조건으로 반영되지 않는다.
- 수동 운전 검사/수리 일부는 역할 binding 대신 정확히 `manual_running`이라는 ID를 찾는다(3531행, 5171행). 정상적으로 다른 이름을 사용한 설계의 의미를 다루기 어렵다.
- 라우터는 특정 진단에 `repair_index_*` 등 지정된 수리 도구만 허용하는 경우가 있다. 잘못된 전제를 일반 편집으로 교정할 재량이 줄어든다.
- acceptance runner는 한쪽 모드 전환만 시험하므로 같은 전제의 누락을 잡지 못한다.

전용 helper나 결정적 컴파일러 자체가 문제는 아니다. **제어 정책의 설계, 정답 판정, 자동 수정이 모두 특정 장비의 특정 명명·형태에 묶인 것**이 문제다. Agent가 래더를 작성할 책임을 빼고 고정 템플릿 생성기로 바꾸는 해결책도 적절하지 않다.

**수정 방향:** Agent는 제어 방식과 수정 내용을 판단하고, Host는 typed reference·원자적 편집·주소/명령 변환·실행 경계를 책임진다. 장비별 시험 profile은 선택 가능한 fixture로 유지하되 범용 설계 도구의 의미와 완료 기준을 지배하지 않게 한다.

## 5. 최종 래더의 구체적 문제

이 절의 rung 번호는 최종 native request의 `buildBundle.engineeringPlan.design.ladderRungs` 순서다. 현재 초안만 보고 오래된 실행본의 결함이라고 오인하지 않도록 **실제로 최종 생성에 사용한 설계**를 확인했다.

### F4 / P1 — 수동 이동 중 모드 전환 금지가 성립하지 않음

첫 두 rung의 실제 논리를 축약하면 다음과 같다.

```text
자동모드 = !알람 & !수동모드 &
           ((자동선택 & !자동운전) | (자동모드 & (자동운전 | !수동선택)))
수동모드 = !알람 & !자동모드 &
           ((수동선택 & !자동운전) | (수동모드 & (자동운전 | !자동선택)))
```

수동 이동 중에는 `manual_running=1`이어도 `auto_running=0`이다. 수동 선택 버튼은 이미 놓았고 자동 선택을 누르면 두 번째 식에서 수동 모드가 OFF가 된다. 저장된 조건식을 그대로 평가한 결과:

| 입력 상태 | 첫 두 rung 처리 후 |
|---|---|
| 수동모드=1, 자동모드=0, 수동운전=1, 자동운전=0, 알람=0, 수동선택=0, 자동선택=1 | 수동모드=0, 자동모드=0 |

수동 운전 중 기존 모드를 유지한다는 요구와 맞지 않는다. 이 계산은 **해당 두 조건의 정적 반례**이며 전체 PLC 스캔 재실행 결과가 아니다. 다음 구현에서 실제 XG-SIM의 역방향 전환 시험으로 확인해야 한다. 기존 시험은 자동 운전 중 수동 선택만 확인했다.

### F5 / P1 — 수동 버튼 유지 상태에서 stop 해제 시 재기동 가능

rung 8(`rung_manual_move_oneshot`)은 이름과 달리 단발 펄스가 아니라 다음 run 허가 래치다.

```text
수동운전 = !정상정지 & !reset & 수동모드 & !각알람 & !수동완료단계 &
           (수동버튼 | 수동운전)
```

rung 30의 이동 출력은 수동 이동 step과 이 허가를 사용한다. 수동 이동 중 버튼을 누른 채 정상정지를 켰다가 해제하면, 이동 step이 남고 완료는 OFF인 조건에서 다음 값이 나온다.

| 정상정지 | 수동버튼 | 수동운전 허가 | 이동 출력 |
|---|---|---|---|
| OFF | 계속 ON | ON | ON |
| ON | 계속 ON | OFF | OFF |
| 다시 OFF | 계속 ON | 다시 ON | 다시 ON |

새로운 버튼 누름을 확인하는 조건이 없다. 최종 설계 조건을 직접 평가해 재현했으며, 이번 감사에서 새 XG-SIM으로 관측한 것은 아니다. 기존 수동 시험은 두 번째 이동 시작 후 버튼을 먼저 놓고 stop/reset으로 넘어가므로 이 경우를 잡지 못한다.

**수정해야 할 개념:** 단발 시작 이벤트, 현재 운전 허가, 이동 중 상태, stop/fault 후 재시작 허가는 서로 다르다. 신호 하나를 추가하는 데 그치지 말고 자동·수동에서 요구되는 release/rearm와 reset 정책을 명시하고 일관되게 구현해야 한다.

### F6 / P2 — reset 중복·명칭 불일치 및 스캔 순서 검토 부족

다음 항목은 전부 “즉시 전체 복구 실패”라고 단정하지 않는다. 중복 경로가 일부 동작을 보완하고 있고 실제 통과한 경로도 있다.

- rung 10에서 비상정지 알람을 RST한 뒤 rung 11은 같은 알람 ON을 요구하여 수동 sequence reset을 수행하려 한다. 도어도 rung 12의 RST 뒤 rung 13·14가 같은 latch ON을 요구한다. 앞에서 해제한 값을 뒤 조건에 쓰는 순서 문제를 검토해야 한다.
- 자동에는 rung 24의 독립 reset이 있으나 수동 rung 31은 `reset_btn AND NOT manual_move_btn`이다. 설명은 “알람 유무와 관계없는 operator reset”이지만 버튼 유지 조건에서는 동일한 보장이 아니다.
- 수동 카운트는 rung 18의 완료 step 접점으로 증가하고, 완료 step 진입은 rung 27, 버튼이 이미 OFF일 때 다음 reset은 rung 28이다. 짧게 누른 뒤 완료되는 경로에서 완료 step이 카운터에 관측되는지 **XGK S-step 명령의 스캔 의미와 실제 시뮬레이션 확인이 필요**하다. 이 항목은 아직 확정된 카운트 오류로 분류하지 않는다.
- `rung_manual_move_oneshot`은 현재 run latch이며 이름·설명·control network 명칭이 편집 후 충분히 정리되지 않았다.

요점은 rung 수를 줄이는 것이 아니라, 작성된 순서에서 실제 상태 변화가 어떻게 관측되는지 검토하고 죽은/중복 경로를 제거하는 것이다.

## 6. 생성된 래더가 사람이 읽기 어려운 이유

### F7 / P1 — 최종 XG5000 파일에 심볼 표가 없음

보존된 최종 source `.xgwx`를 기존 bounded project XML reader로 읽었다. SHA-256이 native 기록과 일치하는 파일에서 **`<Symbols Count="0">`**를 확인했다. 설계 문서에는 signal symbol이 있지만 실제 파일의 변수/심볼 표는 비어 있다.

이번에는 native 화면을 직접 보지 못했으므로 모든 rung 주석이나 표시 옵션까지 없다고 단정하지 않는다. 다만 **실제 저장 파일의 symbol table이 0개라는 것은 확인 사실**이다. 웹 계획서의 긴 설명만으로 native 래더 가독성을 대체할 수 없다.

### F8 / P2 — 선언과 실제 프로그램 사이에 정리되지 않은 잔재

signal과 step 참조 타입을 구분해 최종 39개 rung을 조사했을 때, 36개 IO 선언 중 아래 10개 signal은 래더에서 signal로 읽히거나 쓰이지 않았다. 다른 메타데이터의 참조 여부와는 별개다.

```text
step_move_cmd         M02003   (같은 이름의 step 참조와는 다른 signal)
manual_move_pressed   M02005
completion_count      M00010   (실제 카운터 명령은 D00000에 씀)
seq_auto_step         M00014
seq_manual_step       M00015
mode_change_allowed   M00017
init_btn              M00018
init_active           M02009
manual_move_pending   M0200B
manual_start_allowed  M0200D
```

단순히 전부 자동 삭제하면 안 된다. 특히 `init_btn`은 사용자가 기대하는 초기화 동작이 누락된 것인지 중복 제안이 남은 것인지 요구사항과 대조해야 한다. `completion_count`는 word 카운터와 bit 선언이 어긋난 사례다.

**수정 방향:** typed symbol/reference, 실제 read/write 사용처, 상태 전이, 필요한 주소 타입을 일관되게 유지한다. 편집 후 이름·설명·참조·미사용 선언을 정리하고, 심볼/주석을 native 파일에 전달한 다음 재열기하여 확인한다. 선언만 늘리거나 검토 문서만 길게 만드는 것은 산출물 품질 개선이 아니다.

## 7. UI/UX와 운영 기록

### F9 / P2 — 기술 항목 일괄 검토는 동작 검토를 대신하지 못함

현재 프런트엔드는 이전 상태보다 달라졌다. 여러 상세를 `<details>`로 접고, 검토 확인 체크는 하나이며, 기본 대화에 모든 객체를 매번 나열하지 않는 코드가 있다. 따라서 “현재도 사용자가 516개 체크박스를 눌러야 한다”거나 “모든 UUID가 기본 화면에 나온다”는 평가는 하지 않는다.

하지만 최종 plan review는 24개 section/516개 item ID와 결속된다. 해당 build bundle의 completeness는 confirmed 7, proposed 509다. UI의 “제안 전체를 확정 실행 설계로 검토 완료” 한 번으로 이 집합이 검토 완료 처리된다. 이는 **정확히 어느 버전을 승인했는지 기록하는 기능**이지 516개 의미 항목의 정확성이나 동작을 입증하는 기능이 아니다.

근거: `NewProjectPlanReviewPanel.tsx`의 `projectPlanReviewItemIds`, `reviewConfirmed`, 1465행 확인 UI와 1488행 버튼. 상세 조건 표현에는 raw `sourceType:sourceId`도 사용된다.

사용자 전면에는 요청한 결과, 현재 변경, IO/모드/시퀀스의 읽기 쉬운 요약, 남은 결정, 실제 결과를 보여야 한다. 내부 ID·digest·receipt는 펼쳐 보기로 둔다. 이 문제를 해결하려고 또 다른 승인 단계나 수백 개의 필수 검토 항목을 추가하지 않는다.

신규 입력 prompt builder에는 “이 단계에서는 파일이나 XG5000을 변경하지 말고 … 계획서를 작성”하라는 문구가 들어 있다(`App.tsx` 828행). 계획만 요청한 경우에는 맞지만, 처음부터 완성을 요청한 사용자에게 계획 작성만을 전체 작업처럼 보이게 하면 F2의 단절이 UX에서도 반복된다.

### F10 / P2 — 문서의 현재 상태가 실제보다 오래됨

감사 전 루트 HANDOFF는 9월 9일 미완료·미커밋 상태를, 추적 HANDOFF 최상단은 9월 14일 진행 중 run을 안내했다. 실제 소스는 9월 15일 commit이고 이후 native 수락 기록도 있다. 이전 문서만 읽으면 이미 끝난 경로를 다시 고치거나, 반대로 commit 제목의 “Complete”만 보고 제품 합격으로 오판할 수 있다.

이번 감사에서 새 시작점을 추가한다. 이전 기록은 삭제하지 않고 당시의 이력으로 보존한다. **최신 진단과 다음 작업 지시, 과거 진행 중 상태를 분리**해야 한다.

## 8. 보존할 기반과 구조적으로 바꿀 경계

| 부분 | 보존할 것 | 바꿀 것 |
|---|---|---|
| Agent | 일반 도구 루프, 영속 draft의 읽기·부분 편집, 수정 이력 | check-valid 후 발행 강제, 설계 발행을 사용자 작업 완료로 보는 종료 기준 |
| Host | 실행 권한, revision 결속, 원자적 변경, 저장/재열기 서비스 | 특정 장비의 제어 전략을 강제하는 수리 라우팅과 ID 의존 |
| Native 실행 | 기존 XG5000·XG-SIM 경로와 관측 receipt | Agent가 동일 작업의 다음 행동에 사용할 수 있는 호출·관측·재개 연결 |
| 검증 | 구조 검사, 과거 성공 시나리오, 파일/실행 결속 | 검사기와 동일한 가정을 되풀이하는 판정, 한쪽 모드·한 입력 순서만 확인하는 범위 |
| UI | 접히는 상세, 대화 중심 표현, 버전 결속 검토 | 계획/실행 분절, 원시 ID 중심 조건 표현, 사용자 목표와 무관한 기술 항목 강조 |
| native 산출물 | 별도 작업 사본과 보존 source | 심볼·주석·단위/타입·명확한 네트워크 구성과 재열기 품질 확인 |

CLI가 여전히 `xg5000_control`에 의존하는 독립 배포 문제도 남아 있다. 이는 별도 부채이며 이번 수동 모드/재기동 결함의 직접 원인과 혼동하지 않는다. 먼저 에이전트와 생성물의 의미 결함을 해결한다.

## 10. 다음 완료 판정표

| 범위 | 필요한 관측/결과 |
|---|---|
| 자율 작업 | 최초 자연어 요청 → 필요한 질문 → 설계/부분 수정 → 실제 생성/실행 → 결과 기반 수정. 외부 사람이 내부 수정 지시를 대신 작성하지 않음 |
| valid 이후 제어권 | 구조 검사가 통과해도 Agent가 의미 오류를 읽고 수정할 수 있음 |
| 양방향 모드 전환 | 자동 이동 중 수동 요구, 수동 이동 중 자동 요구 모두 기존 모드 유지. 정지 후 전환은 정상 |
| 수동 조작 | 짧은 누름/길게 유지/완료 전에 해제/완료 후 유지/새 누름을 구분. 두 번째 완료까지 실제 카운트 검증 |
| 정상 정지 | 자동·수동 각각 시작 버튼 유지/해제 조합. stop 해제로 원치 않는 재기동이 없어야 함 |
| 알람·복구 | 자동·수동 각각 비상정지/도어/타임아웃. 원인 유지 reset, 원인 해소 reset, 버튼 유지, 새 시작 순서 확인 |
| reset·초기 상태 | 시작/완료 입력이 ON인 초기 상태와 중간 step에서 reset. 출력·step·카운터 정책이 일관됨 |
| 카운터·타이머 | 두 모드에서 실제 완료당 1회 증가. reset과 충돌하지 않음. 지연 시작/해제/재시작 의미 확인 |
| native 가독성 | 저장 후 재열기한 XG5000에서 IO 심볼·설명·네트워크 의미를 읽을 수 있음. 계획서에만 이름이 있는 상태는 실패 |
| 후속 대화 | 완성된 프로젝트에 자연어 변경/설명 요청을 주고 같은 작업물을 조사·편집·재검증. 불필요한 새 계획/새 프로젝트로 되돌아가지 않음 |
| 일반화 | 예: 컨베이어 또는 2실린더 순차 제어. index 전용 내부 ID·repair 없이 필요한 명령과 상태를 작성/수정 |
| 모델 호환성 | 현재 OpenAI-compatible 서버와 다른 실제 호환 endpoint에서 도구 왕복/오류 수정/재개를 확인. API 모양의 호환과 모델의 PLC 품질은 구분 |
| 사용자 경험 | 진행/결과/필요한 결정이 대화에 보이고 상세는 펼침. 내부 검증 문서 전체를 읽어야만 다음 행동을 알 수 있는 구조가 아님 |

물리 PLC 투입이나 실제 기계 안전 인증은 이 표의 범위가 아니다. 현재 작업 범위는 오프라인 XG5000과 XG-SIM이다.

## 부록 A. 다음 작업자가 재조회할 실행 식별자

식별자는 사용자 기본 UI가 아니라 이 기술 부록에 둔다.

보존 source 파일:

```text
C:\Users\Administrator\AppData\Local\ControlStudio Development\workspaces\a5a999b5-0205-45d1-aca2-9dbc9758e8e6\source\대동인덱스 실사용 재검증.xgwx
10825 bytes
SHA-256 9bb733b40e12407a01b631d539a7a2f270691915e38abb8eca2fb888232308d7
```

같은 workspace의 `working` 파일은 11251 bytes / SHA-256 `13ed381e6c91537a96ea7914009df983a1727d19de867158d0b8f3ce3046be51`이다. source와 working은 구분된 산출물이므로 해시가 다르다는 이유만으로 손상/오염이라고 단정하지 않는다. draft digest와 reviewed plan digest도 서로 다른 객체의 결속값이다.

실제 조건은 `native_project_operations.request_json → buildBundle.engineeringPlan.design`에서, 실제 시험은 `result_json → simulationVerification.receipt.scenarioReceipt`에서 읽는다. `project_design_drafts`만 조회해 최종 실행본과 같다고 가정하지 않는다.

## 부록 B. 소스 탐색 지도

행 번호는 감사 당시 HEAD의 탐색 힌트다. 현재 삭제된 파일은 아래에서 당시 경로를
텍스트로 보존한다. 현행 개발 위치는 구현 계획 §4·§5를 사용한다.

- [Agent runtime](../../../gidong-agent/src/controlstudio_agent/runtime.py): `_route_design_workspace_tool` 183, `prepare_tools` 3315, planning 분기 16686, `_run_design_workspace` 19299, review 지시 19411, 발행 후 종료 19568.
- 당시 `host/src/controlstudio_host/planning_drafts.py` (현재 삭제): `_index_acceptance_behavior_diagnostics` 3295, `manual_running` ID 검색 3531, `_index_mode_latch_repair_changes` 4498, manual stop/reset repair 5166 부근.
- 당시 `host/src/controlstudio_host/native_projects.py` (현재 삭제): acceptance scenario 집합 93, `planning_native_failure_observation` 339, `_compile_native_program` 1569, symbol 확인 1628, rung 설명/명령 생성 2360.
- [Host API](../../host/src/controlstudio_host/app.py): planning 도구 결속 10100 부근, native 생성 14935, 시험 수락 15291, resume 16081.
- 당시 `gidong-plc-mcp/src/xg5000_control/live/native_simulation.py` (현재 삭제): 한쪽 모드 시험 2921, 수동 시험 2956, 수동 두 번째 시작 후 stop/reset 2989, 자동 fault 시험 3018.
- 당시 `web/src/components/NewProjectPlanReviewPanel.tsx` (현재 삭제): `projectPlanReviewItemIds` 31, `LadderConditionView` 353, `reviewConfirmed` 879, 검토 UI 1465.
- 당시 `web/src/components/NativeProjectPanel.tsx` (현재 삭제): `NativeProjectPanel` 1484, `createNativeProject` 호출 1901.
- [App](../../web/src/App.tsx): 신규 planning prompt 828.
- [Native XML reader](../../../controlstudio-xg5000-cli/src/controlstudio_xg5000_cli/vendor/project_xml.py): `read_project_xml` 74. 해시로 결속한 저장 파일의 심볼 표 확인에 사용.

이 감사의 완료는 **문제 파악과 다음 구현 인계의 완료**다. 제품 결함 수정이나 새로운 실기 합격을 의미하지 않는다.
