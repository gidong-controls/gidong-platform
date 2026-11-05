# 동작 기준선 보존과 정리 검토 · 2026-11-05

사용자는 현재 코드가 그나마 제대로 동작하는 상태임을 재확인했다. 기존 웹→Host→Agent→MCP→CLI→XG5000 경로와 설치본 인수 기록을 기준선으로 보존한다. 이번 커밋은 누적된 로컬 개발 소스를 원격에 보존하는 개발 체크포인트이며 새 제품 릴리스나 전체 회귀 합격을 뜻하지 않는다. 실행 DB·프로필·설치본·인증 정보·테스트 임시 폴더는 포함하지 않는다.

## 근거가 있는 정리

### AI 슬롭의 뜻과 이 검토의 적용 범위

사용자 요청으로 사전·연구·커뮤니티 원문을 확인했다. [Merriam-Webster](https://www.merriam-webster.com/dictionary/slop)는 AI로 대체로 대량 생산되는 저품질 디지털 콘텐츠로 정의한다. [Why Slop Matters](https://arxiv.org/abs/2601.06060)는 겉보기 능숙함과 내용의 빈약함, 생성 노력의 비대칭, 대량 생산성을 특징으로 논의한다. [Measuring AI Slop in Text](https://arxiv.org/abs/2509.19163)는 판정의 주관성과 자동 측정의 한계를 다룬다. 따라서 긴 코드·AI 사용·린트 경고만으로 슬롭이라고 판정하지 않는다.

커뮤니티의 사례도 대조했다. [Reddit의 리뷰 부담 토론](https://www.reddit.com/r/ExperiencedDevs/comments/1nfpb14/reviewing_someone_elses_ai_slop/)은 미검토 대량 변경·반복 코드·불필요한 API 호출을, [복잡성 사례](https://www.reddit.com/r/ExperiencedDevs/comments/1gmfw9g/struggling_with_ai_generated_slop/)는 불필요한 추상화·의존성과 기존 프레임워크에 맞지 않는 설계를 문제로 제시한다. [HN의 문서 논쟁](https://news.ycombinator.com/item?id=44034134)은 AI 문서의 효용을 인정하는 의견과 코드의 문장화만으로 독자에게 유용한 설명이 되지 않는다는 의견을 함께 담고 있다. 이는 경험담과 의견이며 산업 전체의 빈도나 이 저장소의 AI 작성 여부를 증명하지 않는다.

이 검토의 적용 기준은 실제 요구와 호출·실행 결과를 연결할 수 있는가, 기존 구조에 맞는가, 작성 비용을 줄이는 대신 사용자·다음 작업자의 검토·복구 부담을 늘리지 않는가다. 아래의 오래된 문서 안내는 구체적인 오도 사례이며, 검색 통계 재사용은 측정된 비용 개선이다. 나머지 린트·export 정리는 일반 유지보수로 구분한다. 네 계층 분리나 반복 검증도 각 소유자의 별도 책임이 있으면 유지하며, 미사용처럼 보이는 vendor 호환 경로를 추정으로 삭제하지 않는다.

| 관측 | 비교와 선택 | 확인 범위 |
| --- | --- | --- |
| 문서 목록이 이미 끝난 신규 프로젝트 연결 작업과 사라진 구현 계획 §4/§5를 다음 작업으로 안내했다. | 현재 진입 경로를 설명하고 실행별 인수 범위는 루트 HANDOFF로 연결한다. | Host의 준비 실행 분기와 Agent planning 문맥 전달을 대조했다. |
| Agent 지식 검색이 질의마다 모든 적격 chunk를 재토큰화했다. 반대로 동기화 때 선계산하면 검색하지 않는 실행에도 비용을 추가한다. | 현재 필터를 통과한 chunk의 첫 검색에서만 Counter와 길이를 계산한다. 동기화는 여전히 존재하는 chunk ID의 통계만 유지한다. 필터·점수·정렬·출처 형식은 유지한다. | 검색 없는 sync의 본문 토큰화 0회, 선택한 chunk만 최초 계산, 반복 검색 재사용, 메타데이터 필터 재적용·문서 교체·삭제·빈 sync를 검사한다. |
| native `_build_messages`가 이미 만든 provider context를 사용할 때 public dialogue를 다시 계산한 뒤 폐기했다. | context 없는 분기에서만 해당 계산을 수행한다. `messages`/`full_history`의 외부 deepcopy는 유지한다. | 두 분기의 전체 메시지·호출 수·외부 mutation 격리와 capture 복원을 검사한다. |
| Host 계획 검토 함수에 무조건 예외 뒤의 도달 불가능한 검토·기록 코드가 남아 있었다. | 도달 불가능한 꼬리만 제거하고 기존 거절 동작을 유지한다. | 문서의 검토 요구와 구현 완료를 혼동하지 않는다. 계획서 제품 흐름 변경은 별도 결정이다. |
| 삭제된 IL 명령명이 MCP `project.operations.__all__`에 남아 있었다. | 존재하지 않는 export만 제거한다. | 전체 소스 compile과 undefined-name 검사. |
| Host import·미사용 지역 변수의 린트 오류, 테스트의 오래된 예약 규칙·패키지 없는 runpy 로딩이 있었다. | import 정리와 미사용 바인딩 정리에서는 실행 호출을 보존한다. 테스트는 현재 예약·패키지 경계를 따른다. | Platform CI와 같은 F/E9/I 검사 및 해당 핵심 회귀 검사. |

검색 비용의 초기 비교는 합성 500 chunk, 질의 20회, 3개 표본의 중앙값으로, 본문 재토큰화 5.1511초와 통계 재사용 0.1424초에서 같은 결과를 반환했다. 그러나 이는 선계산의 무검색 비용을 다루지 않았다. 후속 비교는 같은 검색을 사용하는 eager/lazy 두 방식, 500 chunk 중 20개 적격, 7개 표본의 중앙값으로 수행했다. eager/lazy 각각 sync 88.011/19.981ms, 최초 필터 검색 0.528/1.543ms, 반복 검색 0.360/0.329ms였다. 반환 ranking·provenance는 같고 보관 통계는 500/20개였다. 최종 선택은 lazy다. 최초 검색의 비용을 인정하며 검색하지 않는 문서를 미리 계산하지 않는다.

public dialogue의 폐기 계산은 합성 200개 메시지·본문 240만 문자에서 약 2.148ms/호출이었다. 이를 건너뛰는 것은 불필요한 CPU 작업을 없애는 국소 개선이다. 외부 deepcopy를 제거하면 native provider context의 공유 dict/list가 노출되므로, 그 대안은 채택하지 않았다. 두 합성 비교 모두 모델 응답이나 XG5000 실행 전체의 속도 개선 증거가 아니다.

## 책임과 처리 비용 대조

| 현행 경로와 실제 소유자 | 더 단순한 대안 | 판단 |
| --- | --- | --- |
| Host `NativeWorkspaceExecution`이 작업 사본·세대·저장본을 결속하고, MCP `cli_adapter`가 실행 프로세스·deadline을 관리하며 CLI `simulator_write_sequence`가 실제 입력과 관측을 수행한다. | 계층을 합치거나 callback마다 실행하는 subprocess를 없앤다. | 현재 sequence는 batch당 bridge와 reader를 재사용한다. 자식 영수증 수는 CLI 기동 수가 아니다. 서로 다른 권위 경계의 검증을 지울 근거가 없으므로 유지한다. |
| `SavedToolResults`는 canonical text를 보존하고 reader가 소유할 값을 decode한다. native provider context는 보관 이력과 현재 요청을 연결한다. | 직렬화·deepcopy를 일괄 제거한다. | 등록 시 검증, 영속 증거, reader별 mutation 격리가 다르다. 실제로 폐기되는 public dialogue 계산만 제거한다. |
| Host checkpoint 수신은 transfer 검증→durable callback→ACK다. cold restore는 immutable node SHA·연결·counts를 확인하고 최종 checkpoint를 한 번 검증한다. | 검증 단계 합치기, 추가 범용 cache. | 이미 validated snapshot과 동일 durable restore reference를 재사용한다. 실행에서 기록된 검증·보존 비용을 먼저 분리하며 추가 cache는 도입하지 않는다. |
| 복구 전 저장본 관측과 normal close 직전의 fresh SHA 재검사는 다른 시점의 사실이다. | 이전 SHA를 그대로 재사용한다. | 그 사이 외부 편집 가능성을 배제할 수 없어 freshness 검사는 유지한다. 0.3초 수준의 이 구간을 79초 START 실패의 원인으로 설명하지 않는다. |
| MCP의 decoder·owned-modal shim은 CLI 구현에 위임하며, CLI의 open/check/save 일부는 MCP source에 역의존한다. | shim·legacy 경로를 한꺼번에 제거하고 완전 독립 CLI로 재작성한다. | 호환 import와 설치본 실행 경로가 실제 사용 중이다. 소스 소유권 잔여는 사실대로 기록하되 이번 실사용 근거 없이 재작성하지 않는다. |

근거 호출처: Agent `native_workspace.py`의 `_PublicConversationContext._build_messages`·`run_native_workspace`, `working_history.py`의 `SavedToolResults`; Host `agent_client.py`의 `sync_knowledge`·`_sync_native_checkpoint_locked`·`complete_native_checkpoint`, `native_workspace_host.py`의 sequence 실행; MCP `cli_adapter.py`; CLI `simulator_write_sequence.py` 및 `open_project_entry.py`.

활성 문서의 원래 목적도 분리했다. GUI 문서는 사용자 흐름, 협업 문서는 요청별 행동, 소유권 문서는 실행 권위, 아키텍처 문서는 구성·배포 설명을 담당한다. 이 문서들에서 신규 Web 제작·scope 유지·native 재개를 다시 구현하라는 오래된 지시와 CLI BOM 통합이 pending이라는 문장만 현재 producer/consumer에 맞췄다. 당시 실행·역사 문서를 현재 상태로 덮어쓰지 않았다.

## 실제 기준선 실행

정상 Launcher의 일회 handoff→브라우저 세션→기존 두 펌프 대화에서 이전 설치본 시험과 동일한 한국어 요청을 제출했다. 모델은 실제 `gpt-6.1-terra / medium`, 기존 32k 입력 설정을 유지했다. 주 에이전트는 실행 중 힌트·코드 재기동·XG5000 입력을 넣지 않았고 DB는 읽기 전용으로 관찰했다.

- run `b6d3c0be-6ceb-40a0-91ab-e36618760fe2`: 10:09:39.587Z→10:17:53.218Z, COMPLETED, 493.631초.
- 현재 요청의 provider 기록 15회/79.710초, 준비 4.582초. checkpoint 18회/3.585초 중 transfer 검증 0.706초, callback 보존 2.879초. 과거 continuation의 전체 checkpoint를 현재 run으로 합산하지 않고 마지막 `request_continued` 뒤의 기록만 집계했다.
- Host 도구 구간은 scenario 2회/158.000초, native editor 3회/26.086초, simulator 4회/44.517초, recovery 2회/146.700초다. 이는 각 상위 호출의 경과 시간이며 순수 IDE 키 입력이나 CLI 기동 시간으로 환산하지 않는다. 위 시간의 합과 총 실행 시간 사이 나머지를 임의로 모델·native에 배분하지 않는다.
- 최초 START는 약 79초 뒤 XGSIM_NOT_READY/UIA 관측 timeout으로 실패했다. 그 scenario의 8단계 suffix는 미시작이다. 불확실 START→실제 열린 모달 취소→원래 대상 종료 대기→fresh 대상 결속은 복구 작업이며 동일 조회의 낭비로 세지 않는다. 일부 close_pending/rebound 영수증은 같은 callback의 중간/최종 저장이고 별도 모델 호출·XG 재실행이 아니다.
- 새 대상의 8단계 기대값은 전부 일치했고 STOP은 완료됐다. 최초 END는 메뉴 탐색 결과가 unknown이어서 scenario wrapper 자체는 `lifecycle_end_failed`다. 후속 원본 영수증 33에서 `ended=true`, `current_session_inactive_verified=true`, `end_reconciled_without_simulator_control=true`, `simulator_control_action_attempted=false`로 종료를 확인했다. 원래 END unknown도 보존돼 있다.
- 최종 파일 5368B/SHA `3405f94fc75fd969a316c5322619d5280937cd571bebd00d25b1e37018dbd089`, 안정 읽기 확인. canonical source SHA `e5bccc2b438bcbf76fa2aad280effd0f634ecce1a3a9e329383714299e64651a`로 기존 LD와 같다. 메타데이터 전환의 파일 hash 변화와 LD 변경을 구분한다.
- 현재 요청의 지식 검색은 0회, 보관 결과 조회는 1회였다. 이 실행을 지식 검색 캐시의 속도 개선으로 제시하지 않는다. 주된 지연은 모델 재조회보다 실제 native 실행과 복구에 있었고, 통과한 recovery/END 보호를 제거해 시간을 줄이지 않았다.

소스 재시험은 기존 설치 파일을 교체하지 않고 공식 개발 Launcher와 같은 프로필·대화·작업본을 사용한다. 기존 Host는 UI에서 정상 종료했다. 개발 기동의 첫 UAC는 Windows에서 취소됐고 사용자 재요청 후 정상 Launcher를 다시 실행했다. 단일 요청이 끝나기 전에 새 시험을 중복 시작하지 않는다.

### 수정 소스의 실제 재시험

- run `2593e373-5ae6-43d9-ba59-adc73570765d`: 10:28:08.413Z→10:30:52.693Z, COMPLETED, 164.280초. 공식 개발 Launcher→Host 8767→source Agent/MCP/CLI→기존 XG5000 작업본 경로다. 새 설치본을 만들거나 설치 EXE를 바꾸지 않았다. 선택된 개발 Agent interpreter에서 두 수정 모듈이 현재 checkout의 `gidong-agent/src`에서 로드되는 것을 확인했다.
- 현재 요청의 provider 기록 4회/48.058초, 준비 1.130초. Host의 저장본 조회 1회/1.005초, SIM scenario 1회/52.722초. checkpoint 5회/0.657초 중 transfer 검증 0.322초·callback 보존 0.335초. 나머지 시간을 이 국소 최적화의 이득으로 해석하지 않는다.
- 8단계 모두 완료·기대값 일치·미시작 suffix 0·unknown 없음. 실제 출력 P0010/P0011 ON을 읽고 STOP 입력 후 둘 다 OFF를 읽었다. 마지막 시험 입력 P0000–P000C와 출력 P0010/P0011/P0012 OFF를 확인했다. 테스트 입력만 쓰며 펌프 출력 P0010/P0011에 값을 강제로 쓰지 않았다.
- STOP과 END를 모두 실제 완료했다. END는 `ended=true`, `current_session_inactive_verified=true`, `simulator_control_action_attempted=true`, `input_state_unknown=false`다. 이번 시나리오는 wrapper도 `ok=true`이고 종료 실패를 남기지 않았다.
- 실제 모델은 같은 Terra/medium, 기존 입력 한도 유지. 추가 LD 수정은 없었으며 최종 파일 5368B/SHA `906e82ddd23a465b9de669bd1551f76e397132893f5b1fd6238900a86e427aba`, 안정 읽기와 동일 canonical source `e5bccc2b438bcbf76fa2aad280effd0f634ecce1a3a9e329383714299e64651a`를 확인했다.
- 두 실행은 모델 선택과 IDE 복구 상태가 다르다. **493.631초→164.280초를 코드 최적화의 성능 개선율로 제시하지 않는다.** 실제 동작 보존을 검증했고, 변경 자체의 CPU 비용은 위의 제한된 비교로 제시한다. 이 요청의 지식 검색도 0회이므로 검색 캐시의 실사용 효과를 주장하지 않는다.

이 단계의 결론은 활성 문서의 잘못된 작업 지시 교정, 폐기되는 계산과 반복 토큰화의 국소 제거, 실제 두 펌프 동작 보존이다. 긴 native 대기·불확실 입력 정산을 무조건 없애거나 구조를 재작성할 근거는 얻지 못했다. 더 큰 최적화를 위해 현재 잘 작동하는 경로를 변경하지 않는다.

## 검증과 한계

- 웹 production build·타입 검사·production entry 검증 통과.
- Platform F/E9/I 검사, 네 저장소 F821/F822/E9 검사와 Python compile 통과.
- 공개 HTTP/schema contracts guard 통과.
- MCP 회귀 12개 통과. 프로젝트 생성 예약 테스트는 현재 내부 이름 `project`와 사용자 제목 분리를 사용한다.
- 최종 직접 영향 범위 핵심 회귀 20개 통과(65.35초): 지식 통계 lifecycle·provider context 두 분기·작업 메모리·capture 복원, Host 지식 동기화·재시작·복구 취소, CLI 닫기·순서 편집·저장본 source 보존.
- 전체 회귀 탐색: Agent 30 통과/8 실패, Host 27 통과/4 실패, CLI 53 통과/5 실패. 같은 의존성을 설치한 별도 Python 3.12 검사 환경을 사용했다. 일부 테스트는 내부에서 기존 Python 3.14 `.venv-dev`/Host `.venv` subprocess를 직접 선택하므로 격리 환경만으로 전부 통일되지는 않는다.
- 남은 실패에는 SIM projection의 기대 구조, fake HWND에 실제 Win32 검증이 도달하는 fixture, schema 필드 추가·reader 인자 변경을 반영하지 않은 mock, continuation 검토 기대값, 모델 응답 mock 소진, subprocess timeout이 있다. 어느 쪽이 실제 결함인지 추가 판정이 필요한 항목도 있으므로 전부 테스트 문제라고 단정하지 않는다. 이들을 통과시키기 위해 동작 기준선의 SIM·복구·완료 판단을 변경하지 않았다.
- 설치본을 재빌드·업데이트하지 않았다. 이번 실제 시험은 기존 LD의 SIM 재시험이며 신규 LD writer의 성능·제품 전체 인수를 뜻하지 않는다. 기존 HANDOFF의 신규 작성 인수와 구분한다.

전체 검사 로그는 작업공간 루트 `tmp/review-20261105-{agent,host,mcp,cli}.log`에 보존하며 Git에는 포함하지 않는다. 다음 변경은 실제 재현과 해당 작업의 소유 계층을 확인한 뒤 결정한다.
