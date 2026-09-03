# 문서

문서 권위는 다음 순서다.

1. [루트 PRD](../PRD.md): 기능·안전·출시 결과의 최상위 기준
2. [제품 아키텍처](PRODUCT_ARCHITECTURE.md): 컴포넌트와 실행 경계
3. [엔지니어링 작업 흐름](ENGINEERING_COLLABORATION_WORKFLOWS.md): 사람·Agent 협업과 변경 transaction
4. [책임·데이터 소유권](refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md): 저장·실행 권한과 journal 경계
5. [배포·버전 정책](refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md): artifact 조립과 update 불변조건
6. [생산급 오픈소스 벤치마크](refactor/PRODUCTION_OPEN_SOURCE_BENCHMARKS.md): 채택한 제품 패턴과 의도적으로 제외한 범위
7. [코딩 에이전트·PLC 엔지니어링 벤치마크](CODING_AGENT_BENCHMARK.md): 상용 코딩 에이전트, 커뮤니티 실패 사례와 PLC 전용 acceptance suite
8. [개발 인계](HANDOFF.md): 현재 `dev` 구현 상태, 검증 결과와 다음 실기 검증 순서

## 제품·구현 문서

- [사용자 GUI 및 실행 아키텍처](CONTROLSTUDIO_GUI_HANDOFF.md)
- [XG5000 제어 및 LD 분석 기술 명세](XG5000_CONTROL.md)
- [저장소 이름과 제품 경계](REPOSITORY_NAMING.md)
- [패키징/BOM 경계](refactor/PACKAGING.md)

문서나 UI가 존재한다는 사실만으로 PLC 기능 성공을 선언하지 않는다. 실행 성공은
현재 대상 identity, 작업 journal, native readback과 저장된 evidence receipt로 판정한다.
