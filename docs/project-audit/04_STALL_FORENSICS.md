# 04. 정체 포렌식

판정: **일을 하지 않은 2주가 아니라, 실제 부분 성과를 사용자 목표의 완료로 연결하지 못한 장기 정체다.** 사용자 진술의 “2주 이상”과 일치하는 장기 작업 흔적은 있다. 단일 formal Goal이 2주 연속 실행됐다는 원문/상태는 복원하지 못했다. [근거 E01/E08/E14/E15](10_EVIDENCE_INDEX.md).

## 타임라인

| 시기 | 확인 가능한 사건 | 해석·한계 |
|---|---|---|
| 08-15~16 | 4 repo 합계 일자별3/44 commits; 이후 HANDOFF에 비대화형 기동/UAC 실패 기록 | 최초 작업 시작일이라고 단정하지 않음 |
| 08-31 | 190 commits; native 생성·재개·CLI 분리, 실제 저장/재열기와 SIM timeout/quarantine 기록 | 부분 native 성과 존재. 전체 인수 아님 |
| 09-08~09 | 15/4 commits; 채팅 중심 PRD·일반 루프/이벤트·실사용 수정 | 목표 재정렬은 있었지만 신규 planning 경로는 별도 |
| 09-15 | 네 repo 최종 HEAD commit4개; 이전 감사에서 8개 XG-SIM 시나리오 성공 확인 | 성공을 부정하지 않음. 누락된 반례/가독성/자율 완료까지 증명하지 않음 |
| 09-19~20 | 읽기 감사·설계/반대 검토3개 문서. 기능 코드는 09-15 그대로 | 이 시기 문서 작성은 사용자의 감사 요청. 그 자체를 무단 회피로 평가하지 않음 |

08-15~09-20 범위 Git count는 Platform311/Agent80/MCP241/CLI74, 합계706이다. 08-31~09-20은 20일의 달력 구간이며 **706을 이 20일만의 수치로 잘못 쓰지 않는다.** 작업 시간·비용·실패율은 이 수치로 계산할 수 없다.

이전 감사의 동일 설계 대화: 101run=완료12/취소68/실패21, 최신 revision774. check/state revision도 있으므로 774번 편집이 아니고, 12/101은 모델 성공률이 아니다. 외부 USER 메시지에 내부 수리 지시가 포함된 기록은 일반 사용자의 최초 지시만으로 완성했다는 주장을 약화한다(E08). 이번에 DB를 다시 읽지 않았다.

## 반복 패턴 판정

| 패턴 | 최초 확인·횟수/범위·근거 | 왜 완료로 이어지지 않았는가 / 분류 | 재발 방지 |
|---|---|---|---|
| 수정 후 원복 반복 | 09-01 Platform `c64079c`가 직전 native status commit revert. reflog의 실제 revert1; reset/rebase/checkout/cherry-pick 없음 | 광범위 코드 왕복은 **미입증**. 복구 동작과 Git 원복을 혼동하면 잘못 진단 / 기술·행동 | source/attempt별 변경·되돌림 기록. 무차별 reset 금지 |
| 실제 통합 재시도 | 08-31~09-04 명시 retry subject Agent1/MCP5; 09-04 Platform failure43/timeout5/blocker12, MCP failure13/timeout4 | 통합 오류 수정은 있었으나 최종 목표보다 국소 계약 통과에 집중 / 기술·행동, 높은 가능성 | 동일 원인 두 번 뒤 새 증거 없으면 중단, 실패 artifact를 고정하고 수정 가설을 바꾼 뒤 재실행 |
| 같은 테스트 무분석 반복 | 반복 live 기록은 확인. 완전히 동일한 명령·가설·출력이 몇 회였는지는 **확인 불가** | 횟수만으로 모든 재시험을 낭비라고 판단하지 않음 | 재시험마다 changed code/가설/기대 관측을 기록 |
| 요구 재해석/범위 확대 | 08-31 autonomous, 09-04 변경/복구, 09-08 chat-first, 09-15 persistent workspace 등 최소4개 초점. SSH 논의는 후에 사용자 제외 | 사용자 목표 변화와 구현 우회가 혼재; 정확한 증분 전부 복원 불가 / 요구·행동 | 현재 MVP와 미래 release 분리, 변경 요청은 delta로 기록 |
| 무관한 리팩터링 | CLI 분리/패키징 작업 흔적은 확인. PRD에도 요구되어 무관하다고 단정 불가 | 핵심 수동 동작 오류 해결의 선행 조건은 아님 / 우선순위 | 핵심 경로와 독립인 release 부채는 후속 Wave로 둠 |
| 테스트를 맞추려고 요구 약화 | Agent stable-workbench 테스트31~35행이 valid→publish-only를 기대값으로 고정(E04) | 원래 제품의 도구 선택 요구와 맞지 않는 동작을 회귀로 보호. 고의적 테스트 조작은 미입증 / 기술·요구 정합성 | PRD 기반 반례·외부 관측을 oracle로 사용. 원하는 코드 모양에서 기대값 생성 금지 |
| mock/fallback 누적 | mock 테스트 존재. 생산 MockTransport 대체 동작은 조사 범위에서 미발견; MCP fallback subject6 | mock이 실기를 대체했다는 주장은 **미입증**. 문서는 오히려 둘을 구분 | evidence tier를 분리, 실제 native 대상/소스와 시험 결과 결속 |
| 내부 코드에 집중·사용 흐름 미완료 | E04~E08: 내부 publish/check 성공과 실제 사용자 완료 단절 | 가장 강한 직접 근거 / 구조·행동 | 먼저 수정본 실제 적용/재시험, 이후 자연 최초 요청/후속 변경 |
| 문서 반복 | Platform HANDOFF157 file touches, docs/status 성격 subject151/311. 09-20 시작 전 추적 docs+87/-2, untracked review3개 | 기록 보존 가치가 있지만 인수 완료의 대체물이 됨 / 운영, 높은 가능성 | 짧은 현재 handoff, 안정된 Task ID, 상태 전이 때만 갱신. 이번 감사 후 구현 승인 없이는 구현하지 않음 |
| 임시 진단 코드 | 09-04 Program Check phase tracing 추가/제거 쌍, MCP diagnostic/trace/temp subject16 | 특정 지연 원인 조사 흔적; 임시 스크립트 전체 증식은 미입증 | 진단 소유·제거 조건을 같은 PR에 명시 |
| 의존성 설치/제거 왕복 | manifest touch: Platform launcher1/Host2/Web9; Agent1; MCP pyproject2/lock2; CLI1 | 설치·제거 반복이나 lock oscillation 증거 부족 | 필요 시 lock diff/사유 검토; 근본 원인으로 올리지 않음 |
| 여러 Agent 동시 수정 | 현재/이전 읽기 병렬 검토는 확인. 과거 같은 파일의 동시 쓰기 충돌은 미입증 | subagent 수가 정체의 원인이라는 결론 불가 | 쓰기는 단일 통합 책임자/파일 소유 범위, 읽기 검토 병렬 |
| 잘못된 branch/worktree | umbrella non-repo와 sandbox dubious ownership은 확인. 각 repo worktree1/기대 branch, reflog 전환 흔적 없음 | 실제 다른 checkout에 구현했다는 증거 없음 | 명령마다 repo/branch/HEAD 명시. 설정 오류를 제품 실패로 세지 않음 |
| 승인/권한을 실패로 오인 | 08-16~17 UAC/WinError740, 약120초 대기2건 기록. 이후 UAC 없는 reload와 내부 계약 오류도 기록 | 일부 실기 blocker이나 후속 설계 정책 문제를 설명하지 못함 / 외부+행동 | desktop/session/integrity와 계약 오류 분리, 거절을 무한 재요청하지 않음 |
| 재현 불가 환경 검증 | SSH Session0/GUI Session1 논의와 철회된 Task Scheduler 제안, 현재 UI 접근 불가(E20) | 사용자 환경과 관찰 도구의 차이를 제품 자체 결함으로 과대 해석할 위험 | 지원 interactive Windows 경로를 명시. 이번 감사는 앱을 켜서 재현하지 않음 |

키워드 집계는 commit 제목 기준이며 실행 횟수가 아니다. 동일 커밋이 여러 키워드에 중복될 수 있다. Top touched paths: Platform `docs/HANDOFF.md`157, `host/.../app.py`58, `web/src/App.tsx`31; Agent `runtime.py`69; MCP `server.py`78; CLI `legacy.py`33. 크기나 빈도만으로 나쁜 코드를 판정하지 않는다.

표의 날짜는 확보된 자료에서 확인한 시점이지 시스템 최초 발생일의 보증이 아니다. 개별 날짜가 없는 패턴의 최초 발생은 확인 불가이며 조사 범위는 08-15~09-20 Git/접근 가능한 이전 기록이다. 횟수를 특정할 수 없는 행은 확인한 흔적의 범위만 제시했다.

## 기존 Goal 품질

formal Goal 원문: **확인 불가**. 감사 시작 시 현재 Goal은 null이었다. 접근 가능한 대화에는 “핸드오프·계획 읽고 구현”, “일반 사람처럼 대동인덱스”, “완벽하게 끝내라”, 모델/장문/UX 요구와 이후 구현 중지·감사 요청이 있다(E15). 이를 과거 단일 Goal의 정확한 원문으로 재구성하지 않는다.

판정: PRD에는 완료 기준이 **있다**(12절). 문제는 자연어의 절대적 표현, 단위 run의 종료, native 시나리오 통과, 제품 전체 인수를 하나로 묶지 못한 것이다. “계속”을 새 작업/새 검사 수를 늘리는 활동 명령으로 처리하면 끝이 없다. 새 Goal은 고정된 결과·검증 집합·비범위·실패 중단 조건을 갖춰야 한다([09](09_REPLACEMENT_GOAL.md)).

## A~P 근본 원인 분류

| 분류 | 근본 원인 여부 / 심각도·신뢰 | 증거·관계·미제거 영향 | 제거 방법 |
|---|---|---|---|
| A 목표 불명확 | 일부; P2/중간 | PRD 제품 방향은 명확. 장문/모든 endpoint/완벽의 수치 미정→C,D | 요청 유형·지원 범위 확정 |
| B PRD 모순/누락 | 기여; P1/높음 | E02와 E04 충돌, native 가독성/200k 추가 요구의 정식 ID 부족 | 요구 추적표·결정 delta |
| C Goal 완료 기준 부재 | **부재는 아님**, 적용 단절이 핵심; P1/높음 | PRD12절 vs publish terminal→E,G,M | 결과별 완료 계약 |
| D 범위 과다/확장 | 기여; P2/중간 | offline/production/CLI/SSH 구분 미흡한 시기→J,L | 현 MVP/배포/미래 분리 |
| E 구조 불일치 | **주원인**; P1/높음 | planning silo, policy compiler, native revision 경계→C,G | 관련 모듈 재설계 |
| F 기술 부채 | 기여; P2/높음 | giant runtime/Host, CLI 역의존, 중복 의미→E | 필요한 경계부터 분리, 전면 재작성 안 함 |
| G 테스트/검증 부재 | **전체 부재 아님**, coverage/oracle/CI 공백이 주원인; P1/높음 | E04,E08,E09 | 요구 반례·소스 결속·CI 동작 gate |
| H 외부 서비스 | 일부; P2/중간 | 모델 속도/서버 능력 한계. 현재 서버 상태 미확인 | capability probe/성능 측정, 모델 탓으로 구조 오류 대체 금지 |
| I 권한/네트워크 | 일부; P2/높음 | UAC/세션·이번 원격 접근 불가 | 한정 preflight·blocker 기록; 우회 금지 |
| J 빌드/CI/환경 | 기여; P1/높음 | 실제 CI 상태 미확인, workflow 행동 gate 없음, 버전 차이 | 검증 가능한 동일 baseline |
| K branch/worktree | 근본 원인 근거 부족; 심각도 미평가/낮음 | E01/E14는 오히려 단일 정상 checkout 지지 | 현재 guard 유지 |
| L 문서/코드 불일치 | 기여; P1/높음 | E02/E04/E21, 큰 handoff→C,M | 현재 상태/결정/과거 분리 |
| M Agent 반복 전략 | **주원인 후보**; P1/높은 가능성 | 국소 repair/내부 gate 회복 중심, 외부 수리 지시→E,G,L | 실패 artifact·가설·완료 가치 중심 작업 |
| N 동시 작업 충돌 | 근본 원인 근거 부족; 심각도 미평가/낮음 | 과거 동시 쓰기 causality 미확인 | 소유권/통합 검토만 유지 |
| O 사실상 완료 | 원인 아님; 심각도 해당 없음/높음 | 실제8개 성공은 있지만 F4/F5/F7/F1 남음 | 불완전 성과 인정하고 구체 누락 해결 |
| P 기타 | 정체 원인 미입증; 보안 P1·release P2/높음 | 평문 credential은 정체 원인으로 입증되지 않음 | AF-08 보호 작업; 기능 정체 원인과 구분 |

영향도 순위: **① 신규 설계가 실행·수정으로 이어지지 않는 제어 구조 ② 일반 설계와 충돌하는 compiler/장비 정책 ③ 요구별 인수·실행 소스와 완료 판정의 단절 ④ 국소 오류 수리/재시험 중심 전략 ⑤ 잘못된 기대값과 CI 동작 검증 공백·현재 문서 혼재.** 이 순위는 감사 판단이며 소요 시간의 비율 추정이 아니다.
