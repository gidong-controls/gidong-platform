# Production open-source benchmarks

이 문서는 유명한 프로젝트를 나열하는 목록이 아니다. ControlStudio의 실제 위험에 대응하는 검증된 제품 패턴만 비교하고, 채택할 불변식과 의도적으로 제외할 범위를 고정한다.

벤치마크 포함 기준은 세 가지다.

1. Agent 실행, Windows 데스크톱 제어, PLC 엔지니어링, 배포 공급망 중 하나에 직접 대응한다.
2. 공개된 구현 또는 공식 기술 문서로 동작을 검증할 수 있다.
3. 현재 코드 결정이나 명시적인 후속 acceptance gate로 연결된다.

여기서 `모두`는 유명한 오픈소스를 무제한 수집한다는 뜻이 아니다. 현재 제품의
실패 위험과 직접 맞닿는 production 계열을 빠뜨리지 않는다는 뜻이다. 조사 범위는
다음 여섯 축으로 고정한다.

| 축 | 반드시 비교하는 계열 | 결과물 |
|---|---|---|
| Agent runtime | Pydantic AI, OpenHands, LangGraph, AutoGen, Semantic Kernel, CrewAI | 실행·중단·재개·승인·단일/다중 Agent 결정 |
| durable workflow | Temporal, DBOS, Restate, Prefect, Airflow, n8n | checkpoint, lease, retry, unknown outcome 결정 |
| UI·관측·평가 | AG-UI, Vercel AI stream, OpenTelemetry, Langfuse, Pydantic Evals | 사용자 Activity, debug projection, 회귀 평가 결정 |
| Windows automation | Microsoft UIA, FlaUI, pywinauto, WinAppDriver/Appium | selector, instance identity, version 재인증 결정 |
| release·공급망 | VS Code Insiders, Tauri updater, TUF, in-toto, SLSA, Sigstore, SPDX/CycloneDX, Syft/Grype | 채널 격리, 서명, updater, SBOM gate 결정 |
| PLC engineering | Beremiz/MatIEC, OpenPLC v4, Eclipse 4diac, RuSTy | typed IR, compiler/runtime/debug 분리와 vendor 검증 결정 |

평가는 `채택`, `패턴만 채택`, `후속 gate`, `제외` 네 값만 사용한다. 프레임워크를
추가하는 것 자체는 성과가 아니다. 현재 구현보다 더 작은 코드로 실제 acceptance
gate를 닫을 때만 dependency를 추가한다.

| 후보군 | 판정 | 이유 |
|---|---|---|
| Pydantic AI | 채택 | 현재 runtime이며 structured tool/output, deferred approval, deterministic test와 공식 durable integration을 제공 |
| OpenHands, LangGraph, OpenTelemetry, AG-UI | 패턴만 채택 | event/checkpoint/trace/UI protocol 불변식은 유용하지만 기존 권위 경계를 교체할 이유는 없음 |
| Temporal, DBOS, Restate, Prefect | 후속 gate | 다중 노드·장기 workflow 요구가 SQLite journal의 한계를 실제로 넘을 때 재평가 |
| AutoGen, Semantic Kernel, CrewAI, Airflow, n8n | 제외 | 현재 단일 Engineering Agent와 Host state machine 위에 두 번째 orchestration 계층만 만듦 |
| UIA, FlaUI, pywinauto | 패턴만 채택 | accessibility/control inspection 원칙을 취하되 XG5000 custom MFC는 별도 runtime certification 필요 |
| WinAppDriver/Appium Windows | 제외 | 유지 상태와 custom editor 한계 때문에 mutation authority가 될 수 없음 |
| TUF, in-toto, SLSA, SPDX/CycloneDX, Syft/Grype | 후속 gate | clean beta lifecycle과 외부 production signer가 준비된 뒤 updater·SBOM·provenance에 적용 |
| Beremiz, OpenPLC v4, 4diac, RuSTy | 패턴만 채택 | compiler/runtime/debug 분리는 참고하되 LS vendor semantics의 증거는 아님 |

## Agent 실행과 사용자 가시성

| 기준 제품 | 검증된 production 패턴 | ControlStudio 결정 | 가져오지 않는 것 |
|---|---|---|---|
| [Pydantic AI durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/) | 현재 사용 중인 Agent runtime이 Temporal·DBOS·Prefect·Restate durable adapter와 streaming/MCP를 공식 지원 | 현 custom Host/Agent checkpoint를 공식 message·tool lifecycle과 계속 대조하고, 실제 재시작 acceptance가 부족할 때만 adapter 도입 검토 | 현재 Host/MCP journal을 버리고 workflow engine으로 mutation 권위를 이전 |
| [Pydantic AI deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/) | approval과 외부 실행을 model run에서 분리하고, 새 run은 message history와 deferred result로 이어감. client 승인만으로는 권한 경계가 아님 | Agent 제안, Host의 durable approval, MCP 실행 권위를 분리하고 `conversationId`로 후속 run을 연결 | UI가 보낸 승인 boolean을 직접 mutation authorization으로 사용 |
| [Pydantic AI step persistence](https://pydantic.dev/docs/ai/harness/step-persistence/) | settled boundary snapshot과 `(run_id, tool_call_id)` tool-effect ledger; 시작 후 terminal 결과가 없으면 `unknown_after_crash` | 현재 cycle checkpoint와 tool event를 같은 identity로 결속하고 unsettled mutation은 MCP journal로만 reconcile | interrupted model snapshot을 side-effect 성공 증거로 사용하거나 무제한 time travel 저장 |
| [Pydantic AI UI streams](https://pydantic.dev/docs/ai/integrations/ui/overview/) | AG-UI/Vercel stream adapter가 text·tool event를 UI에 실시간 전달 | 현재 SSE 계약을 유지하되 Activity의 의미를 `runId/toolCallId/evidenceId`로 고정. 필요 시 protocol adapter만 교체 가능하게 유지 | frontend가 보낸 complete message history를 신뢰하거나 raw thought를 표시 |
| [OpenHands events](https://docs.openhands.dev/sdk/arch/events) | immutable typed event와 action/observation 분리 | `tool.started/completed/failed`를 `runId + toolCallId`로 한 Activity 항목에 결합하고 Host evidence와 연결 | model thought 또는 chain-of-thought 노출 |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | step checkpoint, pending write, human interrupt, thread resume | Agent checkpoint와 engineering mission을 run/conversation/target digest에 결속 | LangGraph runtime 도입, mutation time travel |
| [AutoGen state and teams](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/state.html) | versioned agent/team state 저장·복원. 공식 지침도 단일 Agent가 충분하면 multi-agent team을 추가하지 말라고 권고 | 기본 제품은 한 Engineering Agent를 유지하고, 독립 전문성이 실제 acceptance를 개선할 때만 bounded worker 사용 | 역할 이름만 여러 개인 round-robin chat, 실행 중 일관되지 않은 team snapshot |
| [Semantic Kernel Agent Framework](https://learn.microsoft.com/en-us/semantic-kernel/frameworks/agent/) | tool·process orchestration과 human-agent collaboration을 명시적으로 분리 | Host가 process/approval을 소유하고 Agent는 typed proposal만 생성하는 현재 경계를 유지 | 범용 plugin/kernel memory를 PLC authority 저장소로 사용 |
| [CrewAI Flows](https://docs.crewai.com/en/concepts/flows) | event-driven flow, persisted state, human feedback | 비교 대상으로만 유지. 현재 bounded mission loop보다 명확한 acceptance 이득이 없으므로 제외 | crew hierarchy와 flow DSL 이중 orchestration |
| [Temporal](https://docs.temporal.io/) | durable event history와 crash 후 continuation | Host event/change/native journal을 재시작 복구 권위로 유지 | PLC mutation의 자동 replay 또는 일반 retry |
| [DBOS](https://docs.dbos.dev/) · [Restate](https://docs.restate.dev/) · [Prefect](https://docs.prefect.io/) | durable step, idempotency, recovery를 application workflow에 결합 | 장기·다중 노드 운영이 실제 필요해질 때 비교할 후속 후보. 현재 단일 Windows Host에는 SQLite journal이 더 작고 감사 가능 | 제품 규모 근거 없이 세 orchestration runtime 중 하나를 추가 |
| [Apache Airflow task states](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html) | queued/running/success/failed/deferred/awaiting-input를 구분하고 zombie run을 정리 | Agent run, mission wait, approval wait, tool activity를 서로 다른 상태로 표현 | DAG scheduler와 worker fleet 도입 |
| [n8n](https://github.com/n8n-io/n8n) | 실행 단위 상태, elapsed time, 과거 실행 확인 | Activity에 실행 중/완료/실패와 duration을 표시 | 범용 node editor, 사용자 정의 arbitrary executor |
| [OpenTelemetry tracing](https://opentelemetry.io/docs/specs/otel/trace/api/) | root span, child span, attributes, events, status의 일관된 상관관계 | conversation/run/toolCall/evidence ID를 상관 키로 사용하고 debug UI에는 bounded projection만 표시 | 외부 telemetry 기본 전송, raw tool arguments, secrets |
| [Langfuse](https://github.com/langfuse/langfuse/blob/main/README.md) | LLM, retrieval, tool invocation을 한 계층형 trace로 관찰 | local Agent debug에서 cycle/tool/evidence 순서를 한 화면에 표시 | 프롬프트·PLC 데이터·비밀의 SaaS 업로드 |

현재 채택한 최소 UI는 채팅 말풍선 옆의 장식이 아니라 실행 상태다. 사용자가 요청을 보내면 Activity가 즉시 열리고, 각 도구는 시작→완료/실패 한 행으로 갱신되며, 소요 시간과 생성된 evidence를 따라갈 수 있어야 한다. Debug mode는 같은 event stream의 식별자·상태·digest만 더 보여주고 payload나 모델 내부 추론은 보여주지 않는다.

## Agent 테스트와 회귀 평가

| 기준 제품 | 검증된 패턴 | ControlStudio 결정 | 가져오지 않는 것 |
|---|---|---|---|
| [Pydantic AI testing](https://pydantic.dev/docs/ai/guides/testing/) | `TestModel`/`FunctionModel`, dependency override, `ALLOW_MODEL_REQUESTS=False`로 모델 호출 없는 결정론적 테스트 | protocol·tool routing·mission/checkpoint는 실제 모델 없이 먼저 검증하고, 별도 acceptance에서만 승인된 모델을 사용 | 모든 CI를 확률적 live-model 호출에 의존 |
| [Pydantic Evals](https://pydantic.dev/docs/evals/) | dataset, evaluator, run comparison을 분리 | 대표 XGWX/요구사항 fixture에 tool choice·evidence coverage·terminal authority를 평가하는 후속 dataset을 둔다 | LLM judge 단독 점수로 PLC correctness 판정 |
| [OpenTelemetry trace API](https://opentelemetry.io/docs/specs/otel/trace/api/) | trace/span/event/status 상관 구조 | 로컬 debug projection의 identity vocabulary로만 우선 채택 | raw prompt·tool argument·PLC source를 외부 collector로 기본 전송 |
| [Langfuse](https://github.com/langfuse/langfuse) | model/retrieval/tool trace와 eval 관찰 | self-hosted 관측 요구가 생기면 export 후보로만 유지 | 제품의 authoritative run/change journal 대체 또는 SaaS 필수화 |

테스트 피라미드는 네 층이다. (1) pure compiler/schema/property test, (2) fake
model·fake MCP protocol test, (3) packaged development/beta process smoke, (4) 지정된
XG5000/XG-SIM/PLC 조합의 실기 acceptance다. 앞 단계 통과를 뒤 단계의 성공으로
승격하지 않는다.

## Windows/XG5000 자동화

| 기준 | 검증된 패턴 | ControlStudio 결정 | 가져오지 않는 것 |
|---|---|---|---|
| [Microsoft Windows UI Automation](https://learn.microsoft.com/en-nz/windows/apps/dev-tools/winapp-cli/ui-automation) | AutomationId/UIA pattern 우선, input injection은 pattern이 없는 경우에만 사용; inject 직전 target 재확인 | COM/readback → UIA/Win32/MFC command → control-relative input 순서. 모든 단계에서 exact PID/HWND/editor/view와 결과 readback 확인 | 화면 절대 좌표를 지속 가능한 제품 계약으로 사용 |
| [FlaUI](https://github.com/FlaUI/FlaUI) | UIA2/UIA3를 대상 control 특성에 맞게 선택하고 automation tree로 상호작용 | UIA로 노출되는 dialog/control은 pattern 기반으로 처리하고 custom MFC surface만 별도 certified fallback 사용 | 라이브러리 교체만으로 custom-drawn XG5000 editor가 안정화된다고 가정 |
| [pywinauto](https://pywinauto.readthedocs.io/en/latest/getting_started.html) | legacy MFC에는 Win32 backend, modern control에는 UIA backend를 선택 | selector profile에 backend/class/control hierarchy/language/DPI를 포함 | fuzzy title 한 개만으로 instance 선택 |
| [WinAppDriver](https://github.com/microsoft/WinAppDriver) · [Appium Windows Driver](https://github.com/appium/appium-windows-driver) | WebDriver 형태의 Windows UI test와 element inspection | 일반 앱 회귀 테스트의 비교 대상으로만 유지 | 2020년 이후 release가 없는 WinAppDriver service를 XG5000 mutation 권위 또는 custom MFC 해결책으로 도입 |

XG5000 변경은 update-proof라고 주장하지 않는다. 대신 새 버전에서 조용히 잘못 동작하지 않게 한다. 실행 권위는 `XG5000.exe` 절대 경로, SHA-256, file/product version, PE machine, Authenticode signer, process lifetime, exact PID/HWND, same-COM active-project path/hash, selector profile/language/DPI에 묶인다. 하나라도 바뀌면 mutation capability는 닫히며, disposable scratch project의 A→B→A readback/check/save/reopen 인증을 다시 통과해야 열린다.

[LS ELECTRIC 공식 검색](https://sol.ls-electric.com/ww/en/newSearch?searchInput=xg5000)은 XG-SIM 내부 상태 변경용 interface API는 공개하지만 XG5000 editor/project mutation API는 공개하지 않는다. 그러므로 현재 MFC/UI fallback은 불가피하지만, 절대좌표가 아니라 exact runtime/selector에 묶인 재인증 가능한 fallback이어야 한다. 공식 2026년 문서는 일부 장치 기능에 XG5000 V4.82 이상을 요구하므로, V4.81에서 얻은 evidence를 V4.82 지원 증명으로 승격하지 않는다.

## 릴리스와 공급망

| 기준 제품 | 검증된 production 패턴 | ControlStudio 결정 | 가져오지 않는 것 |
|---|---|---|---|
| [VS Code Insiders](https://code.visualstudio.com/insiders) | Stable과 Insiders의 side-by-side 설치·데이터 분리 | development/beta/production의 install path, data dir, port, mutex, executable identity를 분리 | branch 이름만으로 release channel 판정 |
| [Tauri updater](https://v2.tauri.app/plugin/updater/) | production update signature를 비활성화할 수 없는 설치 권위로 취급 | production BOM과 component Authenticode 검증 실패 시 설치·실행 거부 | unsigned production fallback |
| [The Update Framework](https://theupdateframework.github.io/specification/latest/) | root/targets/snapshot/timestamp 역할, threshold key, expiry, rollback·freeze·mix-and-match 방어 | updater 구현 때 signature 하나가 아니라 version/expiry/snapshot 일관성과 rollback 방어를 acceptance로 요구 | 현재 local dev launcher에 PKI/updater를 먼저 얹기 |
| [Dagger](https://docs.dagger.io/) | local/CI에서 같은 typed, content-addressed pipeline과 trace 사용 | 향후 clean-machine release replay의 기준으로 사용 | 현재 Windows signing pipeline을 Linux container 안으로 억지 이전 |
| [in-toto](https://github.com/in-toto/docs/blob/master/in-toto-spec.md) | build/test/package step과 material/product digest를 signed metadata로 연결 | BOM에 source revision, component digest, signer와 build evidence를 결속하는 후속 gate | 서명되지 않은 JSON을 provenance로 주장 |
| [SLSA build levels](https://slsa.dev/spec/v1.0/levels) | provenance 존재와 tamper-resistant build를 서로 다른 성숙도로 구분 | 현재 local BOM 검증과 외부 production signer를 구분하고, 생산 준비를 단계적으로 표시 | 현재 상태를 높은 SLSA level로 과장 |
| [Sigstore/Cosign](https://docs.sigstore.dev/) | artifact/SBOM signature와 투명한 signing record | 향후 public release attestation 후보. Windows 실행 권위는 당분간 Authenticode 유지 | 네트워크가 필요한 공개 transparency log를 PLC 현장 실행의 필수 의존성으로 사용 |
| [SPDX](https://spdx.dev/use/specifications/) · [CycloneDX](https://cyclonedx.org/capabilities/sbom/) | 표준 component·dependency·license·vulnerability evidence 표현 | release artifact에서 생성한 SBOM을 BOM digest와 서명 provenance에 결속하는 후속 gate | source lockfile 목록만으로 실제 packaged artifact SBOM이라고 주장 |
| [Syft](https://github.com/anchore/syft) · [Grype](https://github.com/anchore/grype) | filesystem/archive artifact에서 SBOM 생성 후 취약점 scan | 세 Windows artifact와 조립 결과를 대상으로 clean build에서 실행할 후보 | scanner 결과를 signer·provenance·runtime acceptance의 대체물로 사용 |

`dev`는 개발 작업 브랜치이지 배포 권위가 아니다. development는 로컬 개발 실행, beta는 별도 설치·상태 저장소를 쓰는 사용자 시험판, production은 외부 signing pipeline이 만든 서명 artifact만 허용한다. 세 채널이 같은 DB/token/port/mutex를 공유하면 side-by-side가 아니므로 모두 분리한다.

## PLC 엔지니어링

| 기준 제품 | 검증된 production 패턴 | ControlStudio 결정 | 가져오지 않는 것 |
|---|---|---|---|
| [Beremiz](https://beremiz.readthedocs.io/en/latest/overview.html) | IEC 61131-3 project model, typed compiler pipeline, IDE/runtime 분리 | Agent는 typed candidate를 제안하고 private compiler가 exact vendor target과 digest를 생성 | XGWX를 근거 없이 PLCopen XML로 변환 |
| [MatIEC via Beremiz](https://github.com/beremiz/beremiz) | textual IEC input을 검증된 중간 표현과 target code로 컴파일 | LD/MNE compiler의 parser·canonicalization·target digest 경계를 유지 | MatIEC semantics를 XGK/XGB vendor semantics로 간주 |
| [OpenPLC Runtime v4](https://github.com/Autonomy-Logic/openplc-runtime) | editor-side compile, validated package upload, runtime compile/load, 별도 debug channel | Agent/Host/MCP 권위와 설계→compile→deploy→runtime observation receipt를 분리 | OpenPLC runtime/API를 LS PLC 실행 증거나 adapter로 사용 |
| [Eclipse 4diac](https://eclipse.dev/4diac/doc/) | model validation, Problems/Console/Debug, deployment/monitoring을 분리 | project structure/task/call evidence와 target execution evidence를 구분 | IEC 61499 function-block model을 XG5000 LD 의미로 간주 |
| [RuSTy](https://plc-lang.github.io/rusty/) | IEC 61131-3 ST를 parser/semantic/LLVM backend로 분리한 compiler architecture | typed parser·semantic validation·target backend 경계를 참고 | ST compiler 결과를 XG5000 LD/MNE vendor semantics로 간주 |

공통 결론은 모델이 vendor 파일 전체를 자유 형식으로 다시 쓰게 하지 않는 것이다. Agent는 좁은 typed change candidate를 만들고, MCP가 private baseline을 다시 읽어 candidate를 deterministic compile한다. 인간은 complete target/digest/영향을 확인하고, Host의 일회성 승인을 거친 뒤에만 적용한다. 적용 후 Program Check, 저장된 파일 hash, 독립 reopen, 필요 시 XG-SIM 결과는 서로 다른 receipt로 남긴다.

## 지금 채택한 것과 다음 acceptance gate

지금 코드에 반영된 것:

- append-only tool activity와 evidence 연결
- mission/checkpoint와 human approval을 일반 대화와 분리
- development/beta/production의 실행·데이터 격리
- UIA/COM/readback 우선과 runtime fingerprint 기반 fail-close
- typed PLC candidate → private compile → approval → apply/check/save/reopen

아직 production 완료로 주장하지 않는 것:

- XG5000 V4.82 disposable scratch certification 및 실제 회귀 evidence
- clean Windows machine에서 beta 설치/upgrade/uninstall replay
- 외부 Authenticode production signer와 immutable artifact registry
- signed build provenance/SBOM 및 updater
- 실제 PLC에서의 restore/compare와 download authority

벤치마크로부터 추가된 바로 다음 acceptance 순서는 다음과 같다.

1. fake model/MCP 기반 Agent resume·approval·unknown-tool-outcome 회귀 suite
2. XG5000 runtime fingerprint별 disposable scratch A→B→A certification
3. clean Windows에서 beta install→upgrade→rollback→uninstall과 production 데이터 비간섭
4. packaged artifact SBOM, 외부 Authenticode, signed provenance, updater rollback 방어
5. 실제 controller restore/compare 후에만 download authority 설계

새 프레임워크 도입은 이 목록의 미해결 acceptance gate를 현재 코드보다 더 작고 명확하게 닫을 때만 허용한다. 이름값 때문에 Temporal, LangGraph, OpenTelemetry, Dagger를 통째로 넣지 않는다.

## 2026-08-13 실제 구현 검증 반영

- Temporal과 LangGraph는 durable 실행의 비교 기준으로 유지한다. 다중 머신 worker나
  며칠 단위 queue가 현재 SQLite lease/checkpoint의 한계를 실제로 넘기 전에는 도입하지 않는다.
- OpenHands의 typed action/observation event와 OpenTelemetry의 correlation vocabulary만
  채택한다. 어느 쪽도 PLC mutation authority나 원문 telemetry 저장소가 아니다.
- Beremiz, OpenPLC, Eclipse 4diac에서는 engineering model/compiler/runtime/deployment/debug
  경계만 채택한다. 이들의 PLC semantics를 LS/XG5000 semantics로 간주하지 않는다.
- pywinauto/UIA는 adapter일 뿐이다. 실제 권위는 PID, process creation time, HWND,
  working-copy path/hash, active-project proof, result readback, certified runtime profile의 결속이다.
- OpenFeature는 beta adapter를 점진 배포하거나 capability를 낮추는 데만 사용한다.
  approval, identity, verification을 우회하는 flag는 허용하지 않는다.

실제 `대동인덱스-0917-2.xgwx`를 immutable-source working copy로 열어 네 LD 프로그램의
catalog/read/logic/state 경로를 검증했고 source SHA-256은 변하지 않았다. 서로 다른 두
XG5000 프로세스의 첫 프로그램 raw clipboard payload는 header 4바이트가 달랐지만,
디코딩한 183-row ladder model은 완전히 같았다. 따라서 분석 receipt에는 별도
`decoded_program_sha256`을 추가한다. raw hash는 mutation/rollback 증거로 계속 유지하며,
decoded digest는 논리 identity와 XG5000 버전 회귀 판정에만 사용한다.

같은 acceptance에서 project title이 보이지만 Open modal이 남은 vendor 상태도 재현했다.
launch verified 조건은 이제 exact main HWND enabled와 PID-owned modal 부재까지 요구한다.
알 수 없는 dialog를 자동 클릭하거나 성공으로 승격하지 않는다.
