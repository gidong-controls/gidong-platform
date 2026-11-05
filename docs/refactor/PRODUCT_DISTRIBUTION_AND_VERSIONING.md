# 단일 제품 배포와 독립 컴포넌트 버전 정책

이 문서는 기동(GIDONG)의 소비자 배포, 컴포넌트 버전 조합, 업데이트 및
rollback에 대한 권위 기준이다. 구현이나 다른 문서가 이 정책과 충돌하면 릴리스를
중단하고 충돌을 해소한다.

## 1. 제품 불변조건

일반 사용자의 관점에서 ControlStudio는 하나의 제품이다.

- 일반 사용자에게 제공하는 설치 파일은 `ControlStudio-Setup.exe` 하나다.
- 설치 후 사용자에게 노출하는 실행 진입점과 시작 메뉴 항목은
  `ControlStudio.exe`와 `ControlStudio.lnk` 각각 하나다.
- Agent와 PLC MCP는 보안·장애 격리를 위한 별도 프로세스이지만 별도 제품이 아니다.
  Platform의 private `runtime/` 아래에 설치되고 Platform이 생명주기를 관리한다.
- 일반 사용자는 Agent나 MCP를 따로 선택·설치·업데이트하지 않는다.
- MCP를 다른 클라이언트에서 독립적으로 사용하려는 사용자를 위한 MCP 전용 배포는
  별도 제품·별도 채널로 취급한다. 이것은 일반 ControlStudio 설치 흐름이 아니다.

따라서 이 구조의 사용자 설명은 **단일 제품·단일 설치, 내부적으로 격리된
멀티프로세스(sidecar) 아키텍처**다.

## 2. 공개 소스와 설치의 경계

소스 저장소의 공개 범위와 소비자 설치 경로를 혼동하지 않는다.

| 대상 | 공개 여부 | 소비자 설치 시 동작 |
|---|---|---|
| `gidong-platform` | 공개 | 미리 빌드된 Platform artifact를 설치 |
| `gidong-agent` | 비공개 | private release pipeline이 미리 빌드한 artifact를 내부 runtime에 설치 |
| `gidong-plc-mcp` | 비공개 | private release pipeline이 미리 빌드한 artifact를 내부 runtime에 설치 |
| `controlstudio-xg5000-cli` | 별도 vendor 저장소 | 미리 빌드한 CLI artifact를 내부 runtime에 설치 |

소비자 설치 프로그램은 다음 작업을 **절대 하지 않는다**.

- private Git 저장소 clone, pull 또는 checkout
- GitHub 등 source-control 자격 증명 요청
- private source tree 다운로드
- sibling checkout이나 개발자 PC 경로 탐색
- 설치 시점에 source로 Agent/MCP 빌드
- 검증되지 않은 “latest” Agent/MCP 자동 선택

현재 배포 방식은 검증된 네 executable과 public contracts wheel을 설치 파일 안에
포함하는 offline bundle이다. 향후 작은 bootstrap installer를 도입하더라도
내려받을 수 있는 것은 서명된 release manifest가 지정한 immutable binary
artifact뿐이다. bootstrap installer 역시 private Git 저장소나 source archive에
접근해서는 안 된다.

Compiled artifact 배포는 private 저장소와 Git history를 공개하지 않는다는 뜻이지,
역공학이 절대 불가능하다는 보장은 아니다. 제품 비밀은 자격 증명을 binary에 넣지
않는 설계, 코드 서명, artifact 접근 통제 및 서버 측 비밀 관리로 보호한다.

## 3. 빌드와 조립 흐름

```text
public Platform source ─┐
private Agent source ───┼─ 독립 production build ─ immutable artifacts
private MCP source ─────┤                         │
vendor CLI source ──────┘                         │
public contracts wheel ───────────────────────────┤
                                                  ▼
                             runtime compatibility + artifact verification
                                                  │
                                       exact BOM + signatures
                                                  │
                                                  ▼
                                      ControlStudio-Setup.exe
                                                  │
                                                  ▼
                           ControlStudio.exe + private runtime/
```

release pipeline은 네 저장소의 release artifact를 조립한다. 조립 단계는
source repository가 아니라 명시적으로 전달된 immutable artifact만 입력으로 받는다.

## 4. 버전 모델

버전은 다음처럼 구분한다.

1. **Platform version** — `gidong-platform`의 독립 제품 코드 버전
2. **Agent version** — `gidong-agent`의 독립 구현 버전
3. **PLC MCP version** — `gidong-plc-mcp`의 독립 구현 버전
4. **XG5000 CLI version** — `controlstudio-xg5000-cli`의 독립 구현 버전
5. **Contract versions** — API, Event, Agent Protocol, MCP Product,
   Workspace, Audit 계약 버전
6. **Product release version** — 검증된 전체 조합에 부여하는 소비자 릴리스 버전

각 저장소는 독립적으로 버전을 올릴 수 있다. 그러나 일반 사용자에게 전달되는
조합은 BOM에 exact version과 SHA-256으로 고정한다. 같은 Platform version을
사용하더라도 Agent, MCP 또는 CLI가 바뀌면 새로운 product release와 새로운 BOM이
필요하다.

예:

```json
{
  "product_release": "2026.08.1",
  "components": [
    {"id": "platform", "version": "1.4.0", "sha256": "..."},
    {"id": "agent", "version": "0.9.3", "sha256": "..."},
    {"id": "plc-mcp", "version": "2.1.1", "sha256": "..."},
    {"id": "xg5000-cli", "version": "0.1.0", "sha256": "..."}
  ],
  "contracts": {
    "api_contract": "3.0",
    "event_contract": "1.0",
    "agent_contract": "1.0",
    "mcp_product_contract": "1.0",
    "workspace_contract": "1.0",
    "audit_contract": "1.0"
  }
}
```

컴포넌트 개발 단계에서는 지원 가능한 contract 범위를 선언할 수 있다. 릴리스
조립 후에는 모호한 범위나 “최신 호환 버전”을 사용하지 않고 BOM의 exact contract
set을 네 배포 artifact가 동일하게 가져야 한다. 현재 runtime 검사는 이 exact equality를
강제한다.

## 6. 설치 레이아웃과 runtime 결속

정상 설치의 논리 레이아웃은 다음과 같다.

```text
ControlStudio/
├─ ControlStudio.exe
└─ runtime/
   ├─ controlstudio-agent.exe
   ├─ controlstudio-plc-mcp.exe
   ├─ controlstudio-xg5000-cli.exe
   ├─ controlstudio_contracts-<version>-py3-none-any.whl
   └─ controlstudio.bom.json
```

설치 프로그램은 payload를 복사하기 전에 BOM과 모든 hash를 검증한다. 설치된
Platform은 시작할 때 다음을 다시 검증한다.

- BOM schema와 exact component ID 집합
- Platform 자신, Agent, MCP, XG5000 CLI, contracts wheel의 SHA-256
- 네 배포 artifact가 선언한 exact contract set
- 안전한 artifact 경로

검증 후 Platform은 검증된 내부 경로로만 Agent/MCP를 기동하고 MCP에 CLI 경로를 전달한다.
Agent Protocol과 MCP Product의 실제 handshake는 별도로 확인한다. 설치된 제품에서
PATH, sibling repository 또는 임의의 “latest” sidecar로 fallback해서는 안 된다.
manifest/handshake나 hash가 맞지 않으면 부분 기능으로 계속하지 않고 fail closed한다.

## 7. 업데이트와 rollback

일반 사용자 업데이트의 단위는 개별 sidecar가 아니라 **BOM으로 결속된 전체 제품
release**다.

- 업데이트 클라이언트는 서명된 release manifest와 installer/package만 신뢰한다.
- 새 release는 별도 staging 위치에 완전히 다운로드하고 검증한 뒤 활성화한다.
- 실행 중인 Platform과 자식 프로세스를 정상 종료한 뒤 조합 전체를 교체한다.
- 서로 다른 release의 Platform·Agent·MCP·CLI를 섞은 상태를 정상 상태로 인정하지 않는다.
- activation 실패 시 이전에 검증된 BOM 조합 전체로 rollback한다.
- 사용자 원본 문서, workspace 권위, 감사 데이터는 runtime binary 디렉터리 밖에
  보존하며 업데이트나 제거가 기본적으로 삭제하지 않는다.
- 데이터 schema migration에는 사전 백업, 명시적 migration version, 실패 복구 또는
  이전 버전 실행 불가 표시가 필요하다. binary rollback만으로 데이터 호환성을
  가정하지 않는다.

긴급 Agent/MCP 수정도 동일하다. 수정된 컴포넌트만 몰래 교체하지 않고 새 product
release를 만들고 전체 compatibility 및 black-box gate를 통과시킨다.

## 8. 채널 정책

최소한 다음 배포 채널을 분리한다.

| 채널 | 대상 | 내용 |
|---|---|---|
| ControlStudio product | 일반 사용자 | Platform과 내부 Agent/MCP/CLI가 결속된 단일 installer |
| ControlStudio pre-release | 내부 QA/승인 사용자 | production과 동일한 조립 방식의 사전 검증 release |
| PLC MCP standalone | MCP 독립 사용자 | MCP만을 위한 별도 설치·버전·지원 정책 |

standalone MCP를 설치했다고 해서 ControlStudio Platform이 그 임의 설치본을
자동 채택하지 않는다. 제품은 자기 BOM에 포함된 내부 MCP만 사용한다.

`development` BOM은 portable 실행만,
`beta` BOM은 `ControlStudio Beta` 설치 경로·바로가기로 production과 나란히 설치하며,
`production` BOM은 Authenticode 검증 없이는 조립·설치·실행하지 않는다. branch 이름은
배포 권위가 아니며 실제 채널은 immutable artifact의 BOM `release_channel`이 결정한다.
세 채널은 설치 경로뿐 아니라 local data/token/log, launcher mutex와 loopback port도
분리한다: production `ControlStudio`/8765, beta `ControlStudio Beta`/8766,
development `ControlStudio Development`/8767. 따라서 beta 검증이 production DB나
실행 세션을 재사용하지 않는다.

## 9. 소유권

- Platform 팀: product release, installer/updater, BOM 검증, 프로세스 supervision,
  승인·대상 결속·감사, 사용자 데이터 보존
- Agent 팀: Agent artifact와 manifest, 추론·프롬프트·파생 RAG, 지원 contract 범위
- MCP 팀: MCP artifact와 manifest, mutation/journal/readback/rollback 강제,
  지원 vendor/product 범위
- Release pipeline: exact 조합 선택, signing, runtime compatibility/readback evidence,
  배포 채널 승격과 rollback 대상 보존

## 10. 현재 구현 상태와 production 전 조건

현재 로컬 구현은 offline single-installer, exact BOM/hash 검증, 단일 사용자
진입점, 내부 sidecar 자동 기동과 black-box 검증을 제공한다. Beta installer는
같은 채널의 기존 설치 전체를 검증한 뒤 sibling staging에서 새 BOM 조합을 만들고,
실행 중인 구성요소가 없을 때만 전체 runtime을 교체한다. 교체 실패 시 검증된 transaction backup으로 복원하고 commit 후에는 hash-owned 과거 파일만 정리하며, current/previous 고정 slot의 unknown 잔여물은 검증된 경계 안의 GUID 경로에 보존하고 별도 local data 디렉터리는 건드리지 않는다.
실제 digest가 다른 두 Beta 조합의 연속 설치와 SQLite 사용자 데이터 byte 보존을
한 Windows 환경에서 replay했다.

현재 installer는 unsigned 개발 artifact이며 production 배포 완료를 의미하지
않는다. production 전에 최소한 다음이 추가로 필요하다.

- 신뢰 가능한 Windows code-signing 인증서와 WinVerifyTrust 기반 검증
- 접근 통제된 immutable artifact registry
- 서명된 release manifest와 원격 배포·복구까지 포함한 production updater
- 사용자 데이터 migration·backup 복구 시험
- 지원 Windows 전체 환경의 설치·업데이트·제거 행렬 검증
- 계획서가 요구하는 실제 XG5000 검증 gate의 충족

실제 PLC/online capability는 별도 검증 환경의 증거가 없으면 완료로 표시하지 않는다.
