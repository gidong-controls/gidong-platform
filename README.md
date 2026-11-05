# 기동 Platform

저장소 간 공통 기준은 [루트 PRD](../PRD.md)와 [ARCHITECTURE](../ARCHITECTURE.md), 현재 작업 상태는 [HANDOFF](../HANDOFF.md)를 따른다.

기동(GIDONG) Platform은 Web/PWA, Windows Launcher, FastAPI Host, 공개 계약과
패키징 조립을 담당한다. Host는 SQLite 원본 기록, Evidence Store,
ExecutionBroker와 Agent sidecar 수명주기를 소유한다.

Platform은 private Agent/MCP 소스를 import하지 않으며 Agent Protocol과 MCP
Product Contract로 통신한다. CLI로 이관된 기능은 별도 프로세스의 JSON 계약을
사용한다. 아직 남은 MCP compatibility core 의존은
[CLI 구현 상태](../controlstudio-xg5000-cli/README.md#current-implementation-status)에 기록한다.

상세 데이터 소유권은 [책임 경계](docs/refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md),
조립·업데이트는 [배포·버전 정책](docs/refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md),
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

프런트엔드 수정도 즉시 반영하려면 `-WebDev`를 사용한다. 이 명령은 이 저장소의
Vite 개발 서버를 `127.0.0.1:5173`에 시작하거나 확인하여 재사용하고, 기존 Host에
정상 일회성 세션으로 연결한다. `/api` 요청과 실시간 작업 이벤트는 Host로 전달한다.
다른 Origin은 허용하지 않으며 Host 인증·CSRF 검증을 유지한다.

```powershell
.\scripts\Start-ControlStudio-Development.ps1 -WebDev
```

선택한 브라우저에서 개발 화면을 열려면 `-WebDev -WebUrl`을 사용한다. 이미 사용하는
작업·설정 디렉터리는 `-ProfileRoot`로 지정할 수 있다. 웹의 지식 화면에서는 브라우저
파일 선택기로 PDF·텍스트 자료를 가져올 수 있다.

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

Beta는 source checkout을 beta라고 표시해 실행하지 않는다. 네 immutable executable artifact와
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
개발 fallback으로 찾는다. 배포 조립은 이 fallback을 사용하지 않고 네 컴포넌트의
버전·SHA-256·계약 버전을 BOM 하나에 결속한 뒤 Launcher가 검증된 artifact만
시작하도록 구성한다.

## 문서

- [Beta 0.1.0-beta.20261020.18 내부 검증 지원표](docs/releases/0.1.0-beta.20261020.18.md)

- [제품 요구사항](PRD.md)
- [제품 아키텍처](docs/PRODUCT_ARCHITECTURE.md)
- [저장소 이름과 제품 경계](docs/REPOSITORY_NAMING.md)
- [책임·데이터 소유권](docs/refactor/RESPONSIBILITY_AND_DATA_OWNERSHIP.md)
- [패키징/BOM 경계](docs/refactor/PACKAGING.md)
- [배포·독립 버전·업데이트 정책](docs/refactor/PRODUCT_DISTRIBUTION_AND_VERSIONING.md)
