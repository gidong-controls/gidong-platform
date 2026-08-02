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

소비자 설치 프로그램은 다음 작업을 **절대 하지 않는다**.

- private Git 저장소 clone, pull 또는 checkout
- GitHub 등 source-control 자격 증명 요청
- private source tree 다운로드
- sibling checkout이나 개발자 PC 경로 탐색
- 설치 시점에 source로 Agent/MCP 빌드
- 검증되지 않은 “latest” Agent/MCP 자동 선택

현재 배포 방식은 검증된 세 executable과 public contracts wheel을 설치 파일 안에
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
private Agent source ───┼─ 독립 build/test ─ immutable artifacts
private MCP source ─────┘                         │
public contracts wheel ───────────────────────────┤
                                                  ▼
                                  compatibility + black-box E2E
                                                  │
                                       exact BOM + signatures
                                                  │
                                                  ▼
                                      ControlStudio-Setup.exe
                                                  │
                                                  ▼
                           ControlStudio.exe + private runtime/
```

private release pipeline만 세 저장소의 release artifact에 접근한다. 조립 단계는
source repository가 아니라 명시적으로 전달된 immutable artifact만 입력으로 받는다.

## 4. 버전 모델

네 종류의 버전을 서로 구분한다.

1. **Platform version** — `gidong-platform`의 독립 제품 코드 버전
2. **Agent version** — `gidong-agent`의 독립 구현 버전
3. **PLC MCP version** — `gidong-plc-mcp`의 독립 구현 버전
4. **Contract versions** — API, Event, Agent Protocol, MCP Product,
   Workspace, Audit 계약 버전
5. **Product release version** — 검증된 전체 조합에 부여하는 소비자 릴리스 버전

각 저장소는 독립적으로 버전을 올릴 수 있다. 그러나 일반 사용자에게 전달되는
조합은 BOM에 exact version과 SHA-256으로 고정한다. 같은 Platform version을
사용하더라도 Agent 또는 MCP가 바뀌면 새로운 product release와 새로운 BOM이
필요하다.

예:

```json
{
  "product_release": "2026.08.1",
  "components": [
    {"id": "platform", "version": "1.4.0", "sha256": "..."},
    {"id": "agent", "version": "0.9.3", "sha256": "..."},
    {"id": "plc-mcp", "version": "2.1.1", "sha256": "..."}
  ],
  "contracts": {
    "api_contract": "1.0",
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
set을 세 컴포넌트가 동일하게 가져야 한다. 현재 runtime 검사는 이 exact equality를
강제한다.

## 5. 릴리스 승격 규칙

컴포넌트 하나만 변경되어도 다음 절차 전체를 다시 거친다.

1. 변경된 저장소가 독립 build/test를 통과한다.
2. public contracts와의 compatibility를 검사한다.
3. 세 packaged artifact의 exact 후보 조합을 선택한다.
4. Platform mock 실행과 공식 Agent/MCP black-box E2E를 수행한다.
5. 기존 XG5000 검증 범위의 회귀 gate를 수행한다.
6. artifact version, contract set, SHA-256, 서명 정보를 BOM에 기록한다.
7. BOM에 고정된 파일만 사용해 하나의 installer를 생성한다.
8. installer 자체를 clean 환경에 설치해 사용자 진입점, 내부 sidecar 기동,
   무결성 실패 거부를 다시 검사한다.
9. 모든 gate가 통과한 product release만 배포 채널로 승격한다.

독립 저장소의 새 tag나 artifact가 존재한다는 사실만으로 소비자 설치본을 자동
갱신하지 않는다.

## 6. 설치 레이아웃과 runtime 결속

정상 설치의 논리 레이아웃은 다음과 같다.

```text
ControlStudio/
├─ ControlStudio.exe
└─ runtime/
   ├─ controlstudio-agent.exe
   ├─ controlstudio-plc-mcp.exe
   ├─ controlstudio_contracts-<version>-py3-none-any.whl
   └─ controlstudio.bom.json
```

설치 프로그램은 payload를 복사하기 전에 BOM과 모든 hash를 검증한다. 설치된
Platform은 시작할 때 다음을 다시 검증한다.

- BOM schema와 exact component ID 집합
- Platform 자신, Agent, MCP, contracts wheel의 SHA-256
- 세 컴포넌트가 선언한 exact contract set
- 안전한 artifact 경로

검증 후 Platform은 검증된 내부 경로로만 Agent/MCP를 기동한다. 설치된 제품에서
PATH, sibling repository 또는 임의의 “latest” sidecar로 fallback해서는 안 된다.
manifest/handshake나 hash가 맞지 않으면 부분 기능으로 계속하지 않고 fail closed한다.

## 7. 업데이트와 rollback

일반 사용자 업데이트의 단위는 개별 sidecar가 아니라 **BOM으로 결속된 전체 제품
release**다.

- 업데이트 클라이언트는 서명된 release manifest와 installer/package만 신뢰한다.
- 새 release는 별도 staging 위치에 완전히 다운로드하고 검증한 뒤 활성화한다.
- 실행 중인 Platform과 자식 프로세스를 정상 종료한 뒤 조합 전체를 교체한다.
- 서로 다른 release의 Platform·Agent·MCP를 섞은 상태를 정상 상태로 인정하지 않는다.
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
| ControlStudio product | 일반 사용자 | Platform과 내부 Agent/MCP가 결속된 단일 installer |
| ControlStudio pre-release | 내부 QA/승인 사용자 | production과 동일한 조립 방식의 사전 검증 release |
| PLC MCP standalone | MCP 독립 사용자 | MCP만을 위한 별도 설치·버전·지원 정책 |

standalone MCP를 설치했다고 해서 ControlStudio Platform이 그 임의 설치본을
자동 채택하지 않는다. 제품은 자기 BOM에 포함된 내부 MCP만 사용한다.

## 9. 소유권

- Platform 팀: product release, installer/updater, BOM 검증, 프로세스 supervision,
  승인·대상 결속·감사, 사용자 데이터 보존
- Agent 팀: Agent artifact와 manifest, 추론·프롬프트·파생 RAG, 지원 contract 범위
- MCP 팀: MCP artifact와 manifest, mutation/journal/readback/rollback 강제,
  지원 vendor/product 범위
- Release pipeline: exact 조합 선택, signing, compatibility/E2E evidence,
  배포 채널 승격과 rollback 대상 보존

## 10. 현재 구현 상태와 production 전 조건

현재 로컬 구현은 offline single-installer, exact BOM/hash 검증, 단일 사용자
진입점, 내부 sidecar 자동 기동과 black-box 검증을 제공한다.

현재 installer는 unsigned 개발 artifact이며 production 배포 완료를 의미하지
않는다. production 전에 최소한 다음이 추가로 필요하다.

- 신뢰 가능한 Windows code-signing 인증서와 WinVerifyTrust 기반 검증
- 접근 통제된 immutable artifact registry
- 서명된 release manifest와 안전한 업데이트/rollback 구현
- 사용자 데이터 migration·backup 복구 시험
- 지원 Windows 환경의 설치·업데이트·제거 행렬 검증
- 계획서가 요구하는 실제 XG5000 검증 gate의 충족

실제 PLC/online capability는 별도 검증 환경의 증거가 없으면 완료로 표시하지 않는다.
