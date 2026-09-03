# 기동 Platform

기동(GIDONG)은 근거 중심 PLC 엔지니어링 제품이다. 이 저장소는 공개 가능한
Platform 경계인 Web/PWA, Windows Launcher, FastAPI Host, 공개 계약과 패키징
조립 규칙을 담는다.

```text
gidong-platform  ← UI, Host, ExecutionBroker, 데이터·감사 권한
gidong-agent     ← 모델 추론, 계획, 파생 지식
gidong-plc-mcp   ← XG5000/XG-SIM 실행, readback, journal, rollback
controlstudio-xg5000-cli ← 별도 실행 파일과 JSON 계약을 가진 LS XG5000 벤더 CLI
```

Platform은 private Agent/MCP 소스를 import하지 않는다. Agent Protocol과 MCP
Product Contract로 통신하며, Host가 세션·승인·감사 기록과 외부 도구 실행 경계를
소유한다. 실행 경로는 `Agent → Host/ExecutionBroker → MCP → 벤더 CLI → XG5000`이다.
CLI로 이관된 경로에서 MCP는 별도 CLI 프로세스의 JSON 계약을 사용한다.

CLI 분리는 벤더별 창·COM·프로젝트 형식의 구현을 Agent와 공통 MCP 계약에서
분리하고, 다른 브랜드를 추가할 때 해당 벤더 CLI를 독립적으로 개발·시험하기
위한 것이다. 현재는 저장소·실행 파일·호출 경계가 분리된 단계다. 일부 벤더 코어는
아직 `gidong-plc-mcp`에 있어 CLI에서 의존하므로, 완전한 소스·배포 독립이나 전체
실사용 검증이 끝났다는 뜻은 아니다. 상세 이관 상태는
[CLI README](https://github.com/gidong-controls/controlstudio-ls-xg5000-cli/blob/main/README.md)에 기록한다.

## 현재 제품 경계

- Web은 엔지니어링 대화, 구조화 분석, 계획 검토, 변경 승인과 복구 상태를 표시한다.
- Host는 SQLite 원본 기록, Evidence Store, ExecutionBroker와 sidecar 수명주기를 소유한다.
- Agent는 도구 호출을 제안할 수 있지만 MCP나 PLC에 직접 연결하지 않는다.
- PLC MCP는 XG5000/XG-SIM 작업을 수행하고 exact readback과 변경 journal을 반환한다.
- PLC 쓰기·강제·모드 변경·다운로드는 명시적으로 구현되고 검증된 capability가
  아니면 fail-closed다.

제품·안전 요구사항은 [PRD](PRD.md)가 우선한다. 컴포넌트 책임과 데이터 소유권은
[책임 경계](docs/refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md), 조립과 업데이트
불변조건은 [배포·버전 정책](docs/refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md),
패키지 BOM은 [패키징 경계](docs/refactor/PACKAGING.md)를 따른다.

## 개발과 조립

위 네 sibling 저장소의 개발 환경이 준비된 Windows에서는 Platform 저장소에서 다음
명령 하나로 source development 채널을 실행한다.

```bat
.\scripts\Start-ControlStudio-Development.cmd
```

이 진입점은 Agent·MCP·독립 CLI의 현재 개발 executable을 명시적으로 결속하고
`ControlStudio Development` 데이터 디렉터리, 8767 포트와 development 전용
launcher mutex를 사용한다. production 또는 beta의 설정·토큰·DB를 재사용하지 않는다.
개발 모델 프로필도 이 source-development 진입점만 소유하며 Host가 시작할 때 한 번
전달한다. 공용 Host 모듈과 beta/production 조립물에는 개발 endpoint·key 값을 넣지
않는다.
브라우저를 열지 않는 기동 점검은 `-NoOpen`을 사용한다. `.cmd` 진입점은
Windows의 사용자별 PowerShell 실행 정책을 변경하지 않고, 저장소에 포함된 정확한
개발 스크립트 한 번에만 `ExecutionPolicy Bypass`를 적용한다.

개발 중 일반 웹 탭을 사용하려면 다음과 같이 실행한다. 기본 PWA 실행과 같은 Host와
일회성 세션 연결을 사용하며, 이미 준비된 Host에 새 UAC를 요청하지 않는다.

```powershell
.\scripts\Start-ControlStudio-Development.ps1 -Web
```

선택한 개발 브라우저로 연결하려면 `-WebUrl`로 발급된 일회성 URL을 그 브라우저에서
연다. Launcher는 웹의 인증·초기 연결 완료까지 기다린다. 발급 URL은 인증 정보이므로
문서·로그·커밋에 저장하지 않는다. `-Web`, `-WebUrl`, `-NoOpen`은 동시에 사용할 수 없다.
일반 주소 `http://127.0.0.1:8767/`만 새 브라우저에서 여는 것은 세션 발급을 대신하지 않는다.

개발 코드를 반영하려면 실행 중인 앱 헤더의 `종료` 확인 화면에서 `코드 다시 불러오기
(UAC 없음)`을 누른다. 현재 elevated Host가 고정된 self-restart bootstrap을 실행하므로
추가 `RunAs`/UAC 확인이 없다. bootstrap은 기존 Host가 8767을 반납한 후에만 동일 Python,
working directory, Agent/MCP/independent XG5000 CLI 환경으로 replacement Host를 시작한다.
XG5000 프로세스·창을 자동으로 종료하거나 재시작하지 않는다. Agent run, mutation, native XG5000
create 또는 ACTIVE target-operation lease가 있으면 재시작은 `409`으로 거부된다. workspace,
conversation과 두 SQLite DB는 기존 경로를 그대로 사용한다.

검증을 마친 XG5000은 Host/MCP stdio 자식 수명과 분리된 외부 engineering session이다.
따라서 replacement MCP의 stdin 종료도 그 PID/HWND/create-time을 닫거나 새로 열지 않으며,
새 Host는 같은 exact identity를 명시적으로 재-attach한다. 기존 named launcher Job에서
시작한 Host도 reload 진입 시 breakaway 권한을 idempotent하게 갱신하므로 이 수명 분리에
추가 UAC가 필요하지 않다.

프로세스 생존과 프로젝트 재연결 성공은 별개다. 특히 별도 CLI로 연 창은 해당 실행의
연결 근거가 제품 MCP에 없을 수 있다. 기존 창 연결이 거절되면 PID/HWND·프로젝트 경로·해시의
실제 증명 실패를 확인해야 하며, 이를 UAC 거부로 표시하거나 자동으로 IDE를 재실행하지 않는다.

source-development 보조 기동도 동일한 named launcher Job을 사용한다. 따라서 검증을
마친 XG5000만 문서화된 breakaway 경로를 사용하며, Host/MCP 재시작 후의 exact identity
재연결은 기존 reconcile 절차가 fail-closed로 처리한다.

Beta는 source checkout을 beta라고 표시해 실행하지 않는다. 세 immutable artifact와
public contracts wheel을 `assemble_release.py --channel beta`로 조립하고 생성된
`ControlStudio Beta` 설치본을 실행해야 한다. 실제 채널 권위는 branch 이름이 아니라
BOM의 `release_channel`이다.

```powershell
uv sync --project .\host
uv sync --project .\launcher
npm ci --prefix .\web
npm run build --prefix .\web
```

Host에는 설치된 공식 Agent와 PLC MCP 실행 명령을 명시적으로 전달한다. sibling
소스 경로를 패키지 메타데이터에 넣거나 Platform이 private 소스를 직접 import해서는
안 된다. 위 세 폴더가 모두 Git 개발 checkout인 경우에만 Host가 각 sibling의
`.venv`에 설치된 `controlstudio-agent`와 `controlstudio-plc-mcp` 실행 파일을 로컬
개발 fallback으로 찾는다. 배포 조립은 이 fallback을 사용하지 않고 세 컴포넌트의
버전·SHA-256·계약 버전을 BOM 하나에 결속한 뒤 Launcher가 검증된 artifact만
시작하도록 구성한다.

## 문서

- [제품 요구사항](PRD.md)
- [제품 아키텍처](docs/PRODUCT_ARCHITECTURE.md)
- [저장소 이름과 제품 경계](docs/REPOSITORY_NAMING.md)
- [책임·데이터 소유권](docs/refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md)
- [패키징/BOM 경계](docs/refactor/PACKAGING.md)
- [배포·독립 버전·업데이트 정책](docs/refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md)
