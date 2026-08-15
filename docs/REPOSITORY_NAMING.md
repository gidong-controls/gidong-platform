# GIDONG 저장소 이름과 제품 경계

제품 브랜드는 **기동 / GIDONG**으로 정하고, 세 저장소는 같은 제품군의 역할을
이름에 그대로 드러낸다.

| 저장소 | 공개 범위 | 역할 |
| --- | --- | --- |
| `gidong-platform` | public | 사용자 UI·Host·ExecutionBroker·원본/workspace·승인·감사·패키징의 권위 |
| `gidong-agent` | private | Pydantic AI sidecar, 추론·프롬프트·파생 RAG, `ExternalToolRequest` 제안 |
| `gidong-plc-mcp` | private | PLC/vendor 실행, journal·readback·rollback·reconciliation 강제 |

로컬 작업 공간은 다음처럼 구성한다.

```text
GIDONG-workspace/
├─ gidong-platform/
├─ gidong-agent/
└─ gidong-plc-mcp/
```

저장소 이름과 Python distribution/import/실행 파일 이름은 별도 namespace다. 이번
브랜드 변경에서는 호환성을 위해 기존 `controlstudio_*`, `controlstudio-*.exe`,
`controlstudio-contracts` 식별자를 유지하고, 다음 별도 마이그레이션에서만
package·artifact namespace를 바꾼다. 저장소 이름을 바꿨다고 해서 Platform이
private source를 설치 시 clone하거나 sibling checkout을 탐색하지 않는다.
단, 위 구조의 Git 개발 작업 공간에서는 Platform Host가 private source를 import하지
않고 각 sibling `.venv`에 이미 설치된 console entry point만 개발 fallback으로
해결한다. `.git`이 없는 배포 artifact에서는 이 경로가 비활성화된다.

각 저장소는 하나의 Git repository로 독립 production build를 수행하고, remote 생성과 push는
사용자 지시 전까지 하지 않는다. 일반 사용자에게는 세 저장소가 아니라 하나의
`기동` 설치 제품만 노출한다.

## 라이선스 경계

- `gidong-platform`: Apache License 2.0
- `gidong-agent`: private proprietary, All Rights Reserved
- `gidong-plc-mcp`: private proprietary, All Rights Reserved

현재 LICENSE/NOTICE의 `GIDONG Controls`는 확정된 제품 조직명을 반영한 작업용
저작권 표기다. 실제 법인명 또는 개인 저작권자가 다르면 public push 전에 그
법적 명칭으로 교체한다. 제3자 dependency의 라이선스와 NOTICE는 각 manifest와
lock file에 따라 별도로 보존한다.
