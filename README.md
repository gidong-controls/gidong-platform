# 기동 Platform

기동(GIDONG)은 PLC 엔지니어링 제품이다. 이 저장소는 공개될 수 있는
`gidong-platform` 경계(웹/PWA, Launcher, FastAPI Host, 공개 contracts,
mocks, conformance, 패키징 조립 규칙)를 담는다. 전체 제품은 다음 세 컴포넌트를
함께 사용한다.

```text
gidong-platform  (public)  ← Platform: UI, Host, ExecutionBroker, data/audit authority
gidong-agent     (private) ← Pydantic AI sidecar: reasoning and derived RAG
gidong-plc-mcp   (private) ← PLC MCP: vendor execution, journal, readback, rollback
```

공개 Platform은 private Agent/MCP 소스나 기존 monorepo 이력을 포함하지 않는다.
공개 contracts의 원본은 이 저장소의 `schemas/`이며 Python/TypeScript SDK는
`packages/`에 있다. 제품·안전 범위는 [PRD](PRD.md)가 우선한다. 현재 분리
경계는 [responsibility/data ownership](docs/refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md),
소비자 조립·버전·업데이트 불변조건은
[product distribution/versioning policy](docs/refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md),
완료 증거는 [refactor validation evidence](docs/refactor/REFACTOR_VALIDATION_EVIDENCE.md)에
고정한다.

## 현재 상태와 안전한 해석

논리 경계(contracts, ExecutionBroker, Agent Protocol sidecar, MCP Product
Contract, public mocks, conformance, BOM)는 구현 대상이다. 그러나 이것은 PRD의
PLC 기능을 완료했다는 뜻이 아니다. 현재 실제 XG5000 evidence는 과거의 제한된
read/offline-change/rollback 기록이며, 이번 refactor에서의 current replay는
`NOT_EXECUTED`다. 실제 PLC online identity, write, force, mode change, download와
run edit은 검증되지 않았고 계속 fail-closed다. 자세한 근거는
[XG5000 regression evidence](docs/refactor/XG5000_REGRESSION_EVIDENCE.md)를
따른다.

## 개발 실행

Platform만 개발할 때는 private component 없이 public mock을 사용한다.

```powershell
uv sync --project .\host --group dev
uv sync --project .\mocks --group dev
uv run --project .\mocks controlstudio-mock-agent
uv run --project .\mocks controlstudio-mock-mcp
```

Host에 mock/official sidecar command를 전달하는 방법은 개발 환경에 따라 다르므로
제품 코드 또는 local ignored compose 설정에서 명시한다. sibling source path를
커밋하거나 Platform이 private source를 import해서는 안 된다. 계약·Platform test와
웹 build는 각각 아래처럼 독립 실행한다.

```powershell
uv run --project .\host pytest
uv run --project .\mocks pytest
uv run --project .\conformance pytest
npm ci --prefix .\web
npm run build --prefix .\web
```

private MCP의 직접 실행은 adapter 개발·진단용이며 제품 실행 방법이 아니다.
공식 세 컴포넌트 실행, artifact-only E2E와 BOM 조립은
[local three-repository workflow](docs/refactor/LOCAL_THREE_REPO_WORKFLOW.md)를
따른다.

## 문서

- [제품 요구사항](PRD.md)
- [제품 아키텍처](docs/PRODUCT_ARCHITECTURE.md)
- [GIDONG 저장소 이름과 제품 경계](docs/REPOSITORY_NAMING.md)
- [책임·데이터 소유권](docs/refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md)
- [로컬 3-repository 작업 흐름](docs/refactor/LOCAL_THREE_REPO_WORKFLOW.md)
- [패키징/BOM 경계](docs/refactor/PACKAGING.md)
- [단일 제품 배포·독립 버전·업데이트 정책](docs/refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md)
- [리팩터링 검증 증거](docs/refactor/REFACTOR_VALIDATION_EVIDENCE.md)
- [XG5000 회귀 범위](docs/refactor/XG5000_REGRESSION_EVIDENCE.md)

세 local Git과 검증된 artifact-only 조립 경계는 만들어졌다. remote 생성과 push는
사용자 지시 전까지 수행하지 않는다.
