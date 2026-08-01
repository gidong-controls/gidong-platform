# 로컬 3-repository 작업 흐름

최종 갱신: 2026-08-01

이 문서는 논리 경계가 통과한 뒤의 물리 분리와 개발·release 검증 절차다. 현재
monorepo 안에 nested Git 저장소를 만들지 않는다. 원본 monorepo를 삭제·history
rewrite하지 않으며 remote 생성과 push는 사용자 지시 전까지 하지 않는다.

## 목표 topology

```text
ControlStudio-workspace/           # Git repository가 아닌 상위 directory
├─ controlstudio-platform/         # public local Git, clean genesis
├─ controlstudio-agent/            # private local Git, clean genesis
└─ controlstudio-plc-mcp/          # private local Git, clean genesis
```

Platform은 `web/`, `host/`, `launcher/`, `schemas/`, public
`packages/*/controlstudio-contracts`, `mocks/`, `conformance/`, `packaging/`과
공개 문서를 가진다. Agent candidate는 현재 `agent/**`를 repository root로
평탄화한다. PLC MCP candidate도 `mcp/**`를 repository root로 평탄화한다. `agent/`
또는 `mcp/` wrapper를 남기지 않으며 Git submodule/네 번째 composition repository를
만들지 않는다.

## 생성 전 필수 gate

물리 복사는 아래 항목 모두가 증거로 통과한 뒤에만 한다.

1. 원본 HEAD/ref와 dirty state를 기록하고 bundle과 독립 mirror를 만든다. 두 archive가
   disposable restore에서 복구되는지 확인한다.
2. contracts guard, Platform/Agent/MCP boundary·safety tests, Mock E2E와 기존
   XG5000 regression diagnostic을 통과시킨다. current actual XG5000 replay가
   `NOT_EXECUTED`이면 그렇게 기록하며 성공으로 대체하지 않는다.
3. Platform에서 private source import와 production `pydantic_ai` dependency가 없고,
   Agent에서 raw MCP client/`xg5000_control` import/approval secret/raw target identity가
   없음을 검사한다.
4. target paths가 원본 밖의 비어 있거나 존재하지 않는 direct sibling이고, 상위
   workspace에 `.git`이 없음을 확인한다.
5. copy map, ignored generated output/cache/user `artifacts/`, source-path dependency
   0개를 dry run으로 확인한다.
6. public candidate의 reachable blob, branch/tag, remote-tracking ref 전부에서 private
   Agent/MCP code, prompt/RAG/eval, vendor fixture/profile, API key/header/endpoint,
   user project/document/artifact/SQLite DB가 없음을 검사한다. 불확실하면 public
   clean genesis만 사용한다.

실행 증거 형식과 fail-closed policy는
[REFACTOR_VALIDATION_EVIDENCE.md](REFACTOR_VALIDATION_EVIDENCE.md)를 따른다.

## local Git 생성 원칙

각 approved snapshot을 sibling에 복사한 후 각 디렉터리에서 독립적으로 `git init`과
clean genesis commit을 만든다. 기존 monorepo history는 private archive/bundle/mirror에
보존하며 public Platform remote로 재사용하지 않는다. component history가 정말 필요해도
별도 candidate clone에서 reviewed `git-filter-repo`를 사용한 뒤 history scan을 다시
통과해야 한다.

각 repository의 runtime/build dependency는 published/immutable artifact 또는
explicit local compose input을 사용한다. `../controlstudio-agent`, `../mcp` 같은
sibling source path를 package metadata에 커밋하지 않는다. local compose command와
sidecar paths는 ignored file 또는 command argument로 주입한다.

## 독립 개발과 test

| repository | 단독 개발 경로 | 최소 검증 |
| --- | --- | --- |
| Platform | Mock Agent + Mock MCP, 공개 schemas/SDK | `uv run --project .\\host pytest`, `uv run --project .\\mocks pytest`, `uv run --project .\\conformance pytest`, Web `npm ci`/build, contract guard |
| Agent | Fake Platform protocol command와 fake tool results | `uv sync --group dev`, `uv run pytest`, `uv run ruff check src tests`; Agent Protocol conformance |
| PLC MCP | vendor unit/integration/fake backend 및 명시적 실기 harness | `uv sync --extra dev`, `uv run pytest --basetemp .pytest-tmp`, `uv run ruff check src tests`; MCP Product Contract conformance |

실제 candidate의 command argv와 output은 run manifest evidence에 기록한다. Platform
mock launch는 private Agent/MCP artifact가 설치되지 않은 상태에서도 성공해야 한다.
editable sibling source 조합만으로 release gate를 통과시켜서는 안 된다.

## artifact-only 조립과 E2E

각 repository는 독립적으로 Platform, Agent, PLC MCP artifact를 build한다. release
candidate는 immutable artifact만 isolated directory에 넣고 BOM을 만든다. BOM은 정확히
`platform`, `agent`, `plc-mcp` 세 component의 version, SHA-256, API/Event/Agent/MCP
Product/Workspace/Audit contract version 및 signature metadata를 동일하게 결속한다.

```powershell
python packaging/scripts/build_bom.py --input <component-artifact-input.json> --output <controlstudio.bom.json>
python packaging/scripts/verify_bom.py --bom <controlstudio.bom.json> --artifacts <artifact-directory>
python packaging/scripts/assemble_portable.py --bom <controlstudio.bom.json> --artifacts <artifact-directory> --output <portable-directory>
```

portable start script는 hash/contract/component identity를 검증한 뒤 immutable Agent와
MCP paths를 Platform에 전달한다. Platform이 계속 supervisor와 transport owner다.
unsigned 또는 development HMAC은 `not-for-production`이며 production signature를
대체하지 않는다. production release는 verified Authenticode publisher/thumbprint를
별도 pipeline에서 확인해야 한다.

black-box E2E는 packaged artifacts만 install/launch하여 다음을 보인다.

- Platform mock mode가 private artifact 없이 실행된다.
- official Agent sidecar와 official MCP가 versioned contract handshake,
  tool proposal→ExecutionBroker→MCP result relay를 통과한다.
- mismatch hash/signature/contract는 launch 전에 거부된다.
- Agent/MCP 각각의 failure가 다른 process를 암묵적으로 종료시키지 않으며,
  unknown mutation은 MCP reconciliation 전 자동 재시도되지 않는다.

## PLC 실기와 종료 조건

packaged E2E는 hardware E2E가 아니다. historical XG5000 read/offline mutation/
rollback gate가 회귀하지 않아야 하지만 current diagnostic `NOT_EXECUTED`를 real
execution으로 기록하면 안 된다. real PLC online capabilities는 별도 safe environment와
evidence가 없으면 미검증·fail-closed로 유지한다.

세 local Git이 존재한다는 사실만으로 완료가 아니다. 독립 build/test, Platform mock
launch, artifact-only black-box E2E, public history audit, BOM/packaging boundary,
XG5000 regression-scope integrity가 모두 증거로 통과하고 하나의 launcher action으로
세 component가 조합될 때만 plan의 물리 분리 완료 조건을 충족한다. remote/push는 그
조건과도 별개이며 사용자 지시 전까지 수행하지 않는다.
