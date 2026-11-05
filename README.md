# 기동 Platform

Host·Launcher와 공용 Python contracts의 소스 개발에는 Python 3.12 이상이 필요하다.

ControlStudio의 공개 Platform 구성요소다. React 웹 UI, Windows Launcher,
FastAPI Host, 공개 계약과 제품 패키징 도구를 제공한다. Agent·PLC MCP·XG5000 CLI는
별도 구성요소이며 비공개 소스는 이 저장소에 포함하지 않는다. 내부 제품 계획,
설계 검토와 실행 기록은 별도로 관리한다.

## 개발

Windows에서 이 저장소의 Host·Launcher·웹 의존성을 준비한다.

```powershell
uv sync --project .\host
uv sync --project .\launcher
npm ci --prefix .\web
npm run build --prefix .\web
```

전체 제품 실행에는 호환되는 Agent·MCP·CLI 런타임도 필요하다. 해당 구성요소를
설정한 소스 개발 작업공간에서는 `scripts\Start-ControlStudio-Development.cmd`를
사용한다. 소스 checkout을 설치된 Beta나 production 릴리스로 취급하지 않는다.

사용자용 설치 파일은 `ControlStudio-Setup.exe`, 설치 후 진입점은
`ControlStudio.exe`다. 제품 조립에는 명시적인 사전 빌드 artifact와 exact BOM을
사용한다. unsigned 개발 artifact는 production 서명 완료를 뜻하지 않는다.
공개 빌더의 인수는 저장소 루트에서 다음 명령으로 확인한다.

```powershell
python packaging/scripts/assemble_release.py --help
python packaging/scripts/build_windows_installer.py --help
```

## 저장소 문서

- [웹 개발](web/README.md)
- [패키징 도구](packaging/README.md)
- [공개 Python 계약과 계약 검사](packages/python/controlstudio-contracts/README.md)
- [개발 스크립트](scripts/README.md)
- [라이선스](LICENSE)와 [고지](NOTICE)
