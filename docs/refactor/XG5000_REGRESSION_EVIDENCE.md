# XG5000 회귀 게이트 증거

최종 갱신: 2026-08-01

이 문서는 three-repository refactor의 단계 0, 10, 14에서 보존해야 하는
XG5000 회귀 범위를 고정한다. 이는 Platform/Agent/MCP 분리의 합격 증거가
아니며, 현장 PLC 기능을 새로 검증했다는 주장도 아니다.

## 기존에 실제로 확인된 범위

| 경로 | 근거 | 확인된 결과 |
| --- | --- | --- |
| 실제 XG5000 V4.81의 대동 프로젝트 복사본 read/analysis | `artifacts/ld-read-e2e/result-capability-final.json` | 실제 JSONL MCP transport에서 LD 4개, visual row 344를 읽었다. 분석 profile에서 `project.backup`은 거부되고, 프로젝트 SHA-256 전후가 동일했다. |
| 동일 복사본의 독립 오프라인 LD 변경/원복 | `artifacts/ld-change-e2e/result-standalone-rollback.json` | 한 operand 변경, diff, visible XG5000 program check, `project.rollback_change`를 실행했고 최종 상태는 `rolled_back`, canonical payload와 파일 SHA-256은 원본과 일치했다. |
| 시험 프로젝트의 XG-SIM 경로 | `docs/VALIDATION.md` 1절 및 `docs/XG5000_CONTROL.md` 4–5절 | IL 삽입/readback, IL→LD, check, save/reopen readback, XG-SIM 공식 쓰기, RUN/STOP, 3회 M0→M1 추종과 복원이 이전 실기 기록에 있다. |

첫 두 JSON은 historical evidence다. 당시 working copy
익명화된 historical working copy와 IDE PID/HWND가 현재 존재한다는 뜻은 아니다.
특히 새 Host 승인 transaction 전체, 외부 재열기, XG-SIM runtime을 현재 실제
XG5000에서 다시 실행했다는 증거로 확대 해석하면 안 된다.

## 현재 refactor gate 결과

`scripts/refactor/xg5000_regression_diagnostics.py`는 IDE/PLC/MCP를 시작하지
않는 read-only 진단이다. 다음을 artifact에 기록한다.

- XG5000 process/PATH/common install directory와 historical working-copy의 존재 여부
- historical JSON evidence의 SHA-256 및 제한된 핵심 fact
- 각 기존 하네스가 read-only인지, offline mutation/XG-SIM mutation인지의 분류
- PLC write, force, mode change, download, online run edit이 실행되지 않았음을 명시

이 작업 시점의 진단 artifact는
`artifacts/refactor-xg5000/diagnostics-20260801T083144Z.json`이다. 결과는
`NOT_EXECUTED`다. XG5000 process/PATH, common install directory, historical
historical working copy가 없어서 current replay는 안전 조건을 충족하지 않았다.
`artifacts/`는 Git ignore 대상이므로 공개 Platform history에
실기 project, UI evidence 또는 민감 경로를 추가하지 않는다.

현재 real-XG5000 replay는 다음 조건을 모두 만족할 때만 별도 수행할 수 있다.

1. XG5000 실행 파일과 operator가 지정한 PID/HWND가 확인된다.
2. 사용자 원본이 아닌 hash-recorded working-copy 또는 전용 fixture가 확인된다.
3. read 경로는 `analysis` profile만 사용한다. `run_ld_change_e2e.py`와
   `run_ls_adapter_e2e.py`는 이 gate에서 실행하지 않는다.
4. PLC online write/force/mode/download/run-edit은 어떠한 경우에도 실행하지 않는다.

조건이 하나라도 없으면 진단과 기존 evidence 확인까지만 수행하고 결과는
`NOT_EXECUTED`로 남긴다. 이를 실패를 성공으로 바꾸는 면제가 아니라 안전한
실기 재현 보류로 취급한다.

## 계속 미검증인 실제 online 범위

실제 PLC 연결의 strong identity, PLC project/parameter readback, PLC write,
run edit, force, mode change, download는 검증하지 않았다. `xg5000.attach`는
IDE attach일 뿐 PLC 연결 증거가 아니며, 이 문서는 위 기능을 완료로 주장하지 않는다.
