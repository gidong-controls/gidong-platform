# 문서

개발 기준 갱신: 2026-10-11. 실제 대형 XG5000 LD의 읽기·구조 편집·저장·재개를
인수했으며, 선정한 구조로 제품 개발을 진행한다. 실제 LD 캡처에서 작은 구조 지도와
부분 조회를 제공하고, 모델이 요소·포트 참조로 변경을 선택하면 벤더 실행기가 편집 명령을
적용한다. 모델은 그 결과를 받아 다음 작업을 판단한다.

기존 작업본과 신규 프로젝트의 native 대화는 Host·Agent sidecar·broker에 연결돼 있다.
신규 작성·저장·SIM과 설치본 인수의 실행별 범위와 한계는 [HANDOFF](../../HANDOFF.md)를 따른다.
현재 개발 지침은 아래 PRD·구현 계획·제품 아키텍처다. 구현 계획의 표는 작업별
담당 경계·동작·완료 근거를 담는다. 날짜가 있는 실행 기록과
`project-audit/`, `reviews/`, `history/`는 판단 근거로 보존하며, 당시의 미완료·완료 철회와
고정 절차를 현재 실행 지침으로 재사용하지 않는다.
기존 audit Goal/백로그·review 설계안과 benchmark 채택 의견은 날짜별 기록으로 구분한다.

제품/기술 문서는 다음 순서로 참고한다.

1. [루트 PRD](../PRD.md): 기능·안전·출시 결과의 최상위 기준
2. [구현 계획](AGENT_PRODUCT_PLAN.md): 선정 방식, 인수 근거, 현행 계약과 다음 구현 작업
3. [제품 아키텍처](PRODUCT_ARCHITECTURE.md): 현재 실행 경로, 확장할 경계와 컴포넌트 책임
4. [엔지니어링 작업 흐름](ENGINEERING_COLLABORATION_WORKFLOWS.md): 요청별 도구 선택과 채팅 사용자 경험
5. [책임·데이터 소유권](refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md): 저장·실행 권한과 journal 경계
6. [배포·버전 정책](refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md): artifact 조립과 update 불변조건
7. [생산급 오픈소스 벤치마크](refactor/PRODUCTION_OPEN_SOURCE_BENCHMARKS.md)와 [코딩 에이전트·PLC 엔지니어링 벤치마크](CODING_AGENT_BENCHMARK.md): 비교 조사 기록. 현재 채택은 구현 계획을 따른다.

## 제품·구현 문서

- [사용자 GUI 및 실행 아키텍처](CONTROLSTUDIO_GUI_HANDOFF.md)
- [XG5000 제어 및 LD 분석 기술 명세](XG5000_CONTROL.md)
- [저장소 이름과 제품 경계](REPOSITORY_NAMING.md)
- [패키징/BOM 경계](refactor/PACKAGING.md)

문서/코드 존재, 과거 fixture 성공, 현재 웹 실사용 성공을 구분한다. 실제로 수행한
행동과 결과만 완료로 설명한다. 독립 재열기·XG-SIM·Keep는 모든 요청의 필수 gate가 아니다.
`history/`는 참고 자료이며 최신 사용자 방향보다 우선하지 않는다.
