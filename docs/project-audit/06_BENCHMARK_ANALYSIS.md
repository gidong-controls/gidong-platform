# 06. 제한된 외부 벤치마크

확인일2026-09-20. 제품 유형을 **IDE/실행 도구와 연결된 PLC 엔지니어링 Agent**로 먼저 고정했다. 비교는 PLC 관련3개와 사용자가 요청한 코딩 Agent UX/작업 수명주기2개, 공식 기술 문서2개, 공개 구현1개로 제한했다. 설치·성능 비교·비공개 내부 구현 추정은 하지 않았다.

“나은 점”은 공개적으로 문서화된 능력과 우리 확인된 공백의 비교다. 실제 정답률/속도 우월성을 측정했다는 뜻은 아니다. 아래 사실과 적용 판단을 분리한다.

## 제품/도구 비교

| 대상·선정 이유·출처 | 공개적으로 확인한 흐름/기능/UX·기술 | 우리 적용 판단: 장점·금지·비용 |
|---|---|---|
| Siemens Industrial Copilot for TIA Portal Engineering — PLC IDE에 직접 결과를 넣는 비교. [공식 발표, 2024-04-22](https://press.siemens.com/global/en/pressrelease/siemens-xcelerator-scaling-roll-out-generative-ai-siemens-industrial-copilot) | 자연어 SCL 생성·설명, 매뉴얼 검색, TIA Portal에 제안 직접 반영, WinCC 초기 시각화와 private Azure 옵션이 소개됨 | **장점/교훈:** 대화 결과가 IDE 산출물로 연결되는 명확한 흐름. **따르지 않음:** SCL 지원을 LS LD 지원으로 오인, 마케팅 품질 수치 인용, Azure 강제. **비용:** LS용 쓰기/readback 어댑터와 실제 동작 검증은 별도 구현 필요 |
| Beckhoff TwinCAT CoAgent — 같은 자동화 엔지니어 대상. [공식 제품 설명](https://www.beckhoff.com/en-en/products/automation/twincat-projects-with-ai-supported-engineering/) | 기존 프로젝트/라이브러리 문맥의 ST 제안, I/O·HMI·문서 조회, IDE 통합; 공개 설명에 local/cloud 모델·MCP와 전문 Agent 구조가 있음. Engineering/Operations 제품을 구분 | **장점/교훈:** 프로젝트 문맥과 기종 문서, Engineering/Operations의 범위 구분. **따르지 않음:** 하위 Agent 수를 능력으로 간주, 현 범위에 HMI/실기운영 확장. **비용:** 정확한 LS 명령·심볼·문서와 권한 경계를 연결해야 함 |
| CODESYS Development System MCP Server — 완성 AI 제품과는 다르지만 PLC 엔지니어링 도구 능력을 직접 비교. [공식 도구 명세](https://content.helpme-codesys.com/en/CODESYS%20Development%20System%20MCP%20Server/_idemcp_mcp_tools_and_ressources.html) | 프로젝트 tree·부분 ST 읽기·검색·참조를 갱신하는 rename·부분 편집·library/device 조회·source path/line 포함 compile 진단. 읽기와 변경 효과를 구분 | **장점/교훈:** 장비 정답 버튼 대신 조합 가능한 편집/관측 능력. **따르지 않음:** ST 편집 도구가 LS의 LD 실행 의미를 보장한다고 주장. **비용:** typed symbol/LD 편집과 위치 진단 매핑; 일관된 변경 단위 필요 |
| Codex — PLC 경쟁 제품이 아닌 장기 개발 작업/UX 인접 비교. [공식 harness 설명](https://developers.openai.com/blog/codex-as-a-platform) | 컨텍스트·도구·이벤트·대화 지속·중단/승인을 harness가 다루며 application이 데이터/경계를 소유하는 공개 통합 설명 | **장점/교훈:** 작업 수명주기와 제품 도메인 책임을 구분. **따르지 않음:** SDK 교체만으로 해결, OpenAI 모델 종속화, UI 브랜드 복제. **비용:** 기존 loop와 Host result/goal 통합. 공개 설명 밖 내부를 추정하지 않음 |
| Claude Code — 사용자 요청/관측에 따라 달라지는 작업 루프 비교. [공식 동작 문서](https://code.claude.com/docs/en/how-claude-code-works) | 맥락 수집·행동·검증이 반복되며 도구 결과가 다음 판단에 들어감. 질문에는 읽기만, 수정에는 실행/검증; 사용자가 중간 개입 가능 | **장점/교훈:** plan-only와 실제 수정 요청의 차이, 관측 기반 다음 행동. **따르지 않음:** 모든 PLC에 무제한 shell/쓰기 권한, 검증 없이 모델의 완료 선언 채택. **비용:** 요청 scope·safe cancellation·native 실제 상태 처리는 제품 책임 |

Siemens 최신 제품 페이지 검색 결과에는 Eigen Engineering Agent가 나타났지만 본문 열기는 `Unsupported content-type: text/markdown`으로 실패했다. 그래서 위 행의 기능 주장은 **본문을 읽은 2024 공식 발표 범위**로 제한한다. 현재 판매 상태·성능·최신 기능의 완전 목록은 미확인이다. 다른 제품도 공개 페이지에서 설명하는 능력과 실제 현장 성능을 구분했다.

## 기술 기준2개와 공개 구현1개

1. **CODESYS 공식 MCP 도구 명세:** 위 비교와 동일 자료를 재사용. 일반 읽기·편집·정확한 진단·변경 효과를 계약으로 제공할 수 있다는 근거다. 당사 compiler policy 분리의 정답 구현을 제공하지는 않는다.
2. **[MCP Tools 명세 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools):** schema 및 호출 결과, protocol error와 `isError` 실행 오류를 구별한다. 입력/결과 검증과 실행 경계를 유지하면서 모델에 수정 가능한 오류를 돌려주는 설계에 적용한다. MCP 준수가 PLC 의미 정확성을 보증하지는 않는다.
3. **[mini-SWE-agent 공식 소스](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py):** `step/query/execute_actions`, 이력 저장, format/error 처리와 step/cost/time 한도 종료를 확인했다. 얇은 루프와 종료 이유 분리가 교훈이다. shell 기반 환경이나 task 제출을 XG5000 인수로 복제하지 않는다. `main` 확인 시점 자료이며 고정 버전 성능을 측정하지 않았다.

AGENTS/설정 공식 문서2개는 제품 비교를 확장하기 위한 수집이 아니라 [지시 적용 감사 E18](10_EVIDENCE_INDEX.md)에 한정했다. OpenAI Docs 스킬은 이 출처 선정과 적용 순서 구분에 사용했다. 추가 제품 조사/새 프레임워크 도입은 이번 감사 범위가 아니다.

## 채택/보류 결정

- 채택 권고: 모델 주도 단일 주 작업 루프, 기존 프로젝트 문맥, 범용 편집, 소스 위치가 있는 진단, 실제 결과의 다음 판단 연결, 읽을 만한 IDE 산출물.
- 보류: 다중 Agent 오케스트레이터 전면 교체, 신규 workflow framework, ST-only 템플릿 전환, 멀티벤더/HMI/물리 설비 운영 확장.
- 우선 적용 비용: 기존 read/edit/broker/persistence를 유지하면서 planning/native 단절과 compiler policy 경계를 바꾸는 것. 새 SDK 설치가 선행 조건은 아니다.
- 결론의 성격: 벤치마크는 방향을 지지한다. 우리 소스의 구현 가능성은 E04~E07로, 실제 완성 여부는 후속 native 인수로 판단해야 한다.
