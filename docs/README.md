# 문서

프로그램 생성·조회·수정은 LD 전용이다. 현재 직접 LD 구조 생성·부분 편집은 미구현이다.
`project-audit/`, `reviews/`, 이전 날짜의 구현 계획은 과거 기록이며 현재 실행 지침이 아니다.
현재 제품은 채팅에서 도구를 선택·실행하고 결과로 다음 행동을 결정하는 PLC 개발 Agent다.
과거 고정 mission/승인/검증/Keep 흐름은 일반 요청의 필수 순서가 아니다.

제품/기술 문서는 다음 순서로 참고한다.

1. [루트 PRD](../PRD.md): 기능·안전·출시 결과의 최상위 기준
2. [제품 아키텍처](PRODUCT_ARCHITECTURE.md): 컴포넌트와 실행 경계
3. [엔지니어링 작업 흐름](ENGINEERING_COLLABORATION_WORKFLOWS.md): 요청별 도구 선택과 채팅 사용자 경험
4. [책임·데이터 소유권](refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md): 저장·실행 권한과 journal 경계
5. [배포·버전 정책](refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md): artifact 조립과 update 불변조건
6. [생산급 오픈소스 벤치마크](refactor/PRODUCTION_OPEN_SOURCE_BENCHMARKS.md): 채택한 제품 패턴과 의도적으로 제외한 범위
7. [코딩 에이전트·PLC 엔지니어링 벤치마크](CODING_AGENT_BENCHMARK.md): 상용 코딩 에이전트, 커뮤니티 실패 사례와 PLC 전용 acceptance suite

## 제품·구현 문서

- [사용자 GUI 및 실행 아키텍처](CONTROLSTUDIO_GUI_HANDOFF.md)
- [XG5000 제어 및 LD 분석 기술 명세](XG5000_CONTROL.md)
- [저장소 이름과 제품 경계](REPOSITORY_NAMING.md)
- [패키징/BOM 경계](refactor/PACKAGING.md)

문서/코드 존재, 과거 fixture 성공, 현재 웹 실사용 성공을 구분한다. 실제로 수행한
행동과 결과만 완료로 설명한다. 독립 재열기·XG-SIM·Keep는 모든 요청의 필수 gate가 아니다.
`history/`는 참고 자료이며 최신 사용자 방향보다 우선하지 않는다.
