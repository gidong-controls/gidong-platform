# 문서

최종 갱신: 2026-08-01

문서 권위는 다음 순서다.

1. [PRD.md](PRD.md): 기능·안전·출시 결과의 최상위 기준
2. [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md): 현재 확정된 컴포넌트와 실행 경계
3. [THREE_REPOSITORY_REFACTOR_PLAN.md](THREE_REPOSITORY_REFACTOR_PLAN.md): 세 repository로 가기 위한 전체 완료 조건
4. 책임/데이터/패키징/검증 문서: 구현과 release gate의 증거
5. 기존 handoff와 vendor 문서: 역사·adapter 세부사항

리팩터링 문서는 PRD 범위를 축소하지 않는다. 코드, mock 또는 API가 있다는 사실도
실제 PLC/online 성공 증거가 아니다.

## 3-repository 기준 문서

- [THREE_REPOSITORY_REFACTOR_PLAN.md](THREE_REPOSITORY_REFACTOR_PLAN.md): 공개
  Platform, 비공개 Agent, 비공개 PLC MCP의 분리 계획과 전체 완료 조건
- [RESPONSIBILITY_AND_DATA_OWNERSHIP.md](refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md):
  Platform/Agent/MCP 책임, ID·journal, approval/target binding, 원본·파생 데이터 경계
- [LOCAL_THREE_REPO_WORKFLOW.md](refactor/LOCAL_THREE_REPO_WORKFLOW.md): sibling
  local Git 생성 전제, 독립 개발/test, mock, artifact-only E2E와 remote 금지
- [PACKAGING.md](refactor/PACKAGING.md): 세 immutable artifact와 BOM·서명 경계
- [REFACTOR_VALIDATION_EVIDENCE.md](refactor/REFACTOR_VALIDATION_EVIDENCE.md):
  보존·분할·public history·independent delivery의 fail-closed evidence gate
- [XG5000_REGRESSION_EVIDENCE.md](refactor/XG5000_REGRESSION_EVIDENCE.md):
  historical real-XG5000 evidence와 current `NOT_EXECUTED`의 구분

## 제품 문서

- [PRD.md](PRD.md)
- [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md)
- [CONTROLSTUDIO_GUI_HANDOFF.md](CONTROLSTUDIO_GUI_HANDOFF.md)
- [CONTROLSTUDIO_AGENT_RUNTIME_HANDOFF.md](CONTROLSTUDIO_AGENT_RUNTIME_HANDOFF.md)
- [ENGINEERING_COLLABORATION_WORKFLOWS.md](ENGINEERING_COLLABORATION_WORKFLOWS.md)
- [KNOWLEDGE_MEMORY.md](KNOWLEDGE_MEMORY.md)
- [MCP_CAPABILITY_MATRIX.md](MCP_CAPABILITY_MATRIX.md)
- [XG5000_CONTROL.md](XG5000_CONTROL.md)
- [VALIDATION.md](VALIDATION.md)
- [MCP_IMPLEMENTATION_HANDOFF.md](MCP_IMPLEMENTATION_HANDOFF.md) — 역사 handoff이며
  현재 공개 경계 또는 제품 범위를 정의하지 않는다.

## 상태 표기

- **구현됨**: 코드와 해당 소프트웨어 test가 존재한다.
- **자동 검증됨**: mock/fake/합성 protocol test를 통과했다. 실기 성공은 아니다.
- **실제 검증됨**: 지정 환경에서 직접 성공한 evidence가 있다.
- **NOT_EXECUTED**: 안전 조건 또는 환경이 없어 현재 replay를 실행하지 않았다.
- **계획**: PRD 범위이지만 아직 제품 완료가 아니다.

현재 actual XG5000 replay가 `NOT_EXECUTED`여도 historical evidence가 사라지는
것은 아니다. 반대로 historical read/change evidence가 새 Platform→Agent→MCP
packaged E2E나 PLC online 기능을 증명하지도 않는다. actual PLC identity, write,
force, mode change, download와 run edit은 미검증·fail-closed다.
