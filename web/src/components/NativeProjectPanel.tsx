import { useEffect, useMemo, useState } from "react";
import { hostApi, HostApiError } from "../api/client";
import type {
  CreateNativeProjectRequest,
  HostStatusResponse,
  NativeProjectOperation,
  ReviewedProjectPlanBinding,
  XG5000Instance,
} from "../api/types";
import { Icon } from "./Icon";

interface NativeProjectPanelProps {
  onClose: () => void;
  onStatusChanged: (status: HostStatusResponse) => void;
  initialNativeProjectId?: string;
  initialProjectName?: string;
  initialDescription?: string;
  reviewedPlan?: ReviewedProjectPlanBinding;
}

const TERMINAL_STATES = new Set(["completed", "failed", "quarantined"]);

type VerifiedNativeProjectOperation = NativeProjectOperation & {
  state: "completed";
  result: NonNullable<NativeProjectOperation["result"]> & {
    projectPath: string;
    projectSha256: string;
    identityVerified: true;
    controllerConnected: false;
    plcWritePerformed: false;
  };
};

function newOperationId(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10).join(""),
  ].join("-");
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof HostApiError ? error.message : fallback;
}

function isAbsoluteWindowsPath(value: string): boolean {
  return /^[a-zA-Z]:[\\/]/.test(value) || /^\\\\[^\\]+\\[^\\]+/.test(value);
}

function displayInstance(instance: XG5000Instance): string {
  const title = instance.title?.trim();
  const target = `PID ${instance.pid} · HWND ${instance.hwnd}`;
  return title ? `${title} · ${target}` : `XG5000 · ${target}`;
}

function isVerifiedCompletion(
  operation: NativeProjectOperation,
): operation is VerifiedNativeProjectOperation {
  return (
    operation.state === "completed" &&
    operation.result?.identityVerified === true &&
    operation.result.controllerConnected === false &&
    operation.result.plcWritePerformed === false &&
    typeof operation.result.projectPath === "string" &&
    operation.result.projectPath.length > 0 &&
    typeof operation.result.projectSha256 === "string" &&
    /^[0-9a-f]{64}$/i.test(operation.result.projectSha256)
  );
}

function expectedRecoveryProjectPath(
  operation: NativeProjectOperation,
): string {
  const destination = operation.destinationDirectory.replace(/[\\/]+$/, "");
  const projectName = operation.request.projectName.trim();
  return destination && projectName
    ? `${destination}\\${projectName}.xgwx`
    : "";
}

function verifiedCompletionNotice(operation: NativeProjectOperation): string {
  const registration = operation.result?.workspaceRegistration;
  if (registration?.state === "captured_not_bound") {
    return "새 오프라인 프로젝트를 만들고 정체성을 확인했습니다. 보호 복사본은 캡처했지만 XG5000에 열린 생성 원본과 별개이므로 분석 세션에는 자동 연결하지 않았습니다.";
  }
  if (registration?.state === "capture_failed") {
    return "새 오프라인 프로젝트 생성은 확인했지만 분석용 보호 복사본 캡처에 실패했습니다. 기존 로컬 프로젝트 열기에서 생성 파일을 다시 여세요.";
  }
  if (operation.result?.recovery) {
    return "현재 XG5000 창과 생성 파일을 다시 읽어 프로젝트 생성 완료를 확인했습니다. 생성 요청을 다시 실행하지 않았습니다.";
  }
  return "XG5000에서 새 오프라인 프로젝트를 만들고 정체성을 확인했습니다.";
}

export function NativeProjectPanel({
  onClose,
  onStatusChanged,
  initialNativeProjectId,
  initialProjectName = "",
  initialDescription = "",
  reviewedPlan,
}: NativeProjectPanelProps) {
  const [projectName, setProjectName] = useState(initialProjectName);
  const [workspaceParent, setWorkspaceParent] = useState("");
  const [plcSeries, setPlcSeries] = useState("XGK");
  const [cpuModel, setCpuModel] = useState("");
  const [programName, setProgramName] = useState("MAIN");
  const [language, setLanguage] = useState("LD");
  const [description, setDescription] = useState(initialDescription);
  const [instances, setInstances] = useState<XG5000Instance[]>([]);
  const [selectedHwnd, setSelectedHwnd] = useState("");
  const [instancesLoading, setInstancesLoading] = useState(true);
  const [operationLoading, setOperationLoading] = useState(
    Boolean(initialNativeProjectId),
  );
  const [recoveryHwnd, setRecoveryHwnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [operation, setOperation] = useState<NativeProjectOperation | null>(
    null,
  );

  const multipleInstances = instances.length > 1;
  const selectedInstance = useMemo(
    () =>
      instances.find((instance) => String(instance.hwnd) === selectedHwnd) ??
      null,
    [instances, selectedHwnd],
  );
  const selectedRecoveryInstance = useMemo(
    () =>
      instances.find((instance) => String(instance.hwnd) === recoveryHwnd) ??
      null,
    [instances, recoveryHwnd],
  );
  const recoveryMode = Boolean(initialNativeProjectId);
  const recoveryTargetMatchesStored =
    Boolean(selectedRecoveryInstance) &&
    selectedRecoveryInstance?.pid === operation?.request.xg5000Pid &&
    selectedRecoveryInstance?.hwnd === operation?.request.xg5000Hwnd;

  const loadInstances = async () => {
    setInstancesLoading(true);
    setNotice("");
    try {
      const response = await hostApi.getXG5000Instances();
      setInstances(response.instances);
      setRecoveryHwnd((current) =>
        response.instances.some((item) => String(item.hwnd) === current)
          ? current
          : "",
      );
      setSelectedHwnd((current) => {
        if (response.instances.some((item) => String(item.hwnd) === current)) {
          return current;
        }
        return response.instances.length === 1
          ? String(response.instances[0]?.hwnd ?? "")
          : "";
      });
    } catch (error) {
      setInstances([]);
      setSelectedHwnd("");
      setNotice(
        errorMessage(error, "XG5000 실행 상태를 확인하지 못했습니다."),
      );
    } finally {
      setInstancesLoading(false);
    }
  };

  useEffect(() => {
    void loadInstances();
    // The target list is intentionally captured once when the panel opens.
    // Users can request an explicit refresh before creating the project.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!initialNativeProjectId) {
      setOperationLoading(false);
      return;
    }
    const controller = new AbortController();
    setOperationLoading(true);
    setNotice("");
    void hostApi
      .getNativeProject(initialNativeProjectId, controller.signal)
      .then((restored) => {
        setOperation(restored);
        if (restored.state === "quarantined") {
          setNotice(
            "생성 결과가 불명확해 격리된 작업입니다. 생성 요청은 다시 실행하지 않습니다. 정확한 XG5000 창을 직접 선택한 뒤 현재 상태만 다시 검사하세요.",
          );
        } else if (isVerifiedCompletion(restored)) {
          setNotice(verifiedCompletionNotice(restored));
        } else if (restored.state === "failed") {
          setNotice(
            restored.errorDetail ??
              "현재 증거로 프로젝트가 생성되지 않은 것이 확인되었습니다.",
          );
        } else {
          setNotice(
            "Host가 소유한 생성 작업을 복원했습니다. 새 생성 요청을 보내지 않고 저장된 작업 상태만 확인합니다.",
          );
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setNotice(
            errorMessage(
              error,
              "중단된 새 프로젝트 생성 작업을 불러오지 못했습니다.",
            ),
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setOperationLoading(false);
        }
      });
    return () => controller.abort();
  }, [initialNativeProjectId]);

  const validate = (): string | null => {
    if (!projectName.trim()) {
      return "프로젝트 이름을 입력해 주세요.";
    }
    if (!workspaceParent.trim() || !isAbsoluteWindowsPath(workspaceParent.trim())) {
      return "작업공간 상위 폴더를 Windows 절대 경로로 입력해 주세요.";
    }
    if (!plcSeries.trim() || !cpuModel.trim()) {
      return "XG5000에 표시되는 PLC 시리즈와 CPU 모델을 입력해 주세요.";
    }
    if (!programName.trim()) {
      return "첫 프로그램 이름을 입력해 주세요.";
    }
    if (!recoveryMode && !reviewedPlan) {
      return "최신 Engineering Plan의 전체 섹션 검토 기록이 필요합니다.";
    }
    if (instances.length === 0) {
      return "XG5000을 실행한 뒤 빈 창을 다시 확인해 주세요.";
    }
    if (multipleInstances && !selectedInstance) {
      return "여러 XG5000 창 중 새 프로젝트를 만들 정확한 PID/HWND를 선택해 주세요.";
    }
    return null;
  };

  const createProject = async () => {
    if (busy) {
      return;
    }
    const validationError = validate();
    if (validationError) {
      setNotice(validationError);
      return;
    }
    if (!reviewedPlan) {
      setNotice("최신 Engineering Plan 검토 기록을 확인하지 못했습니다.");
      return;
    }

    const request: CreateNativeProjectRequest = {
      operationId: newOperationId(),
      projectName: projectName.trim(),
      workspaceParent: workspaceParent.trim(),
      plcSeries: plcSeries.trim(),
      cpuModel: cpuModel.trim(),
      programName: programName.trim(),
      language,
      description: description.trim(),
      reviewedPlan,
      ...(selectedInstance
        ? {
            xg5000Pid: selectedInstance.pid,
            xg5000Hwnd: selectedInstance.hwnd,
          }
        : {}),
    };

    setBusy(true);
    setNotice("");
    setOperation(null);
    try {
      const created = await hostApi.createNativeProject(request);
      setOperation(created);
      if (isVerifiedCompletion(created)) {
        setNotice(verifiedCompletionNotice(created));
        onStatusChanged(await hostApi.getStatus());
      } else if (created.state === "completed") {
        setNotice(
          "Host가 완료를 보고했지만 XG5000 정체성 또는 PLC 무영향 증거가 불완전합니다. 이 프로젝트를 사용하지 마세요.",
        );
      } else if (created.state === "failed") {
        setNotice(
          created.errorDetail ??
            "새 프로젝트 생성이 실패했습니다. XG5000 상태를 확인해 주세요.",
        );
      } else if (created.state === "quarantined") {
        setNotice(
          created.errorDetail ??
            "생성 결과를 확정할 수 없어 작업을 격리했습니다. 다시 실행하지 마세요.",
        );
      } else {
        setNotice(
          "Host가 새 프로젝트를 만드는 중입니다. 완료 전에는 같은 요청을 다시 보내지 마세요.",
        );
      }
    } catch (error) {
      setNotice(errorMessage(error, "새 프로젝트를 만들지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const refreshOperation = async () => {
    if (!operation || busy) {
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const next = await hostApi.getNativeProject(operation.nativeProjectId);
      setOperation(next);
      if (isVerifiedCompletion(next)) {
        setNotice(verifiedCompletionNotice(next));
        onStatusChanged(await hostApi.getStatus());
      } else if (next.state === "completed") {
        setNotice(
          "Host가 완료를 보고했지만 XG5000 정체성 또는 PLC 무영향 증거가 불완전합니다. 이 프로젝트를 사용하지 마세요.",
        );
      } else if (next.state === "failed" || next.state === "quarantined") {
        setNotice(
          next.errorDetail ??
            (next.state === "quarantined"
              ? "결과를 확정할 수 없어 작업을 격리했습니다."
              : "새 프로젝트 생성이 실패했습니다."),
        );
      } else {
        setNotice("아직 생성 중입니다. 같은 요청을 다시 보내지 마세요.");
      }
    } catch (error) {
      setNotice(
        errorMessage(error, "새 프로젝트 생성 상태를 확인하지 못했습니다."),
      );
    } finally {
      setBusy(false);
    }
  };

  const reconcileOperation = async () => {
    if (
      !operation ||
      operation.state !== "quarantined" ||
      !selectedRecoveryInstance ||
      !recoveryTargetMatchesStored ||
      busy
    ) {
      return;
    }
    setBusy(true);
    setNotice("");
    const expectedProjectPath = expectedRecoveryProjectPath(operation);
    try {
      const next = await hostApi.reconcileNativeProject(
        operation.nativeProjectId,
        {
          pid: selectedRecoveryInstance.pid,
          hwnd: selectedRecoveryInstance.hwnd,
          expectedProjectPath,
          ...(operation.result?.projectSha256
            ? { expectedProjectSha256: operation.result.projectSha256 }
            : {}),
        },
      );
      setOperation(next);
      setRecoveryHwnd("");
      if (isVerifiedCompletion(next)) {
        setNotice(verifiedCompletionNotice(next));
      } else if (next.state === "failed") {
        setNotice(
          next.errorDetail ??
            "할당된 폴더가 비어 있고 선택한 XG5000 창에는 다른 프로젝트가 열려 있어, 프로젝트가 생성되지 않은 것으로 확인되었습니다.",
        );
      } else {
        setNotice(
          next.errorDetail ??
            "현재 증거만으로 결과를 확정할 수 없어 격리를 유지합니다.",
        );
      }
      try {
        onStatusChanged(await hostApi.getStatus());
      } catch {
        // Reconciliation evidence remains authoritative even if status refresh fails.
      }
    } catch (error) {
      setNotice(errorMessage(error, "현재 XG5000 상태를 다시 검사하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const terminal = operation ? TERMINAL_STATES.has(operation.state) : false;
  const failure =
    operation?.state === "failed" ||
    operation?.state === "quarantined" ||
    (operation?.state === "completed" && !isVerifiedCompletion(operation));
  const canEditRequest =
    !recoveryMode &&
    (!operation ||
      (operation.state === "failed" && operation.result === undefined));

  return (
    <section
      className="native-project-panel"
      aria-labelledby="native-project-title"
    >
      <div className="instance-picker-heading">
        <div>
          <p className="eyebrow">
            {recoveryMode
              ? "LS XG5000 · QUARANTINE RECOVERY"
              : "STEP 3 · LS XG5000 TECHNICAL SETUP"}
          </p>
          <h2 id="native-project-title">
            {recoveryMode
              ? "새 프로젝트 생성 결과 복구"
              : "XG5000 프로젝트 골격 구성"}
          </h2>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label={
            recoveryMode ? "새 프로젝트 생성 복구 닫기" : "새 프로젝트 만들기 닫기"
          }
          disabled={busy || operationLoading}
          onClick={onClose}
        >
          <Icon name="close" size={18} />
        </button>
      </div>

      <p className="native-project-explanation">
        {recoveryMode
          ? "Host에 저장된 동일 작업의 파일과 XG5000 현재 상태만 다시 읽습니다. 생성 요청 재실행, 자동 재적용, PLC 연결·다운로드·쓰기는 수행하지 않습니다."
          : "검토한 계획을 바탕으로 XG5000의 빈 오프라인 프로젝트 골격만 만듭니다. 일반 로직 생성·프로그램 검사·재열기·시뮬레이션 단계는 아직 이 화면에서 실행되지 않으며, PLC 연결·다운로드·쓰기는 수행하지 않습니다."}
      </p>

      {operationLoading && (
        <p className="instance-notice" role="status">
          저장된 생성 작업을 복원하고 있습니다…
        </p>
      )}

      {canEditRequest && (
        <form
          className="native-project-form"
          onSubmit={(event) => {
            event.preventDefault();
            void createProject();
          }}
        >
          <label>
            <span>프로젝트 이름</span>
            <input
              type="text"
              value={projectName}
              maxLength={80}
              autoComplete="off"
              disabled={busy}
              placeholder="AssemblyLine01"
              onChange={(event) => setProjectName(event.target.value)}
            />
          </label>

          <label className="native-project-wide">
            <span>작업공간 상위 폴더</span>
            <input
              type="text"
              value={workspaceParent}
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
              placeholder="C:\ControlStudio\workspaces"
              onChange={(event) => setWorkspaceParent(event.target.value)}
            />
            <small>
              기존 파일을 덮어쓰지 않습니다. Host가 이 폴더 아래에 새 전용
              폴더를 만듭니다.
            </small>
          </label>

          <label>
            <span>PLC 시리즈</span>
            <input
              type="text"
              list="native-plc-series"
              value={plcSeries}
              maxLength={32}
              autoComplete="off"
              disabled={busy}
              onChange={(event) => setPlcSeries(event.target.value)}
            />
            <datalist id="native-plc-series">
              <option value="XGK" />
              <option value="XGI" />
              <option value="XGR" />
              <option value="XGB" />
            </datalist>
          </label>

          <label>
            <span>CPU 모델</span>
            <input
              type="text"
              value={cpuModel}
              maxLength={64}
              autoComplete="off"
              disabled={busy}
              placeholder="XGK-CPUE"
              onChange={(event) => setCpuModel(event.target.value)}
            />
          </label>

          <label>
            <span>첫 프로그램 이름</span>
            <input
              type="text"
              value={programName}
              maxLength={80}
              autoComplete="off"
              disabled={busy}
              onChange={(event) => setProgramName(event.target.value)}
            />
          </label>

          <label>
            <span>언어</span>
            <select
              value={language}
              disabled={busy}
              onChange={(event) => setLanguage(event.target.value)}
            >
              <option value="LD">LD</option>
              <option value="SFC">SFC</option>
              <option value="ST">ST</option>
              <option value="IL">IL</option>
            </select>
          </label>

          <label className="native-project-wide">
            <span>설명 (선택)</span>
            <textarea
              value={description}
              maxLength={1024}
              rows={3}
              disabled={busy}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>

          <fieldset className="native-project-target">
            <legend>XG5000 대상</legend>
            <div>
              {instancesLoading ? (
                <p>실행 중인 XG5000을 확인하고 있습니다…</p>
              ) : instances.length === 0 ? (
                <p>
                  실행 중인 XG5000이 없습니다. XG5000을 직접 실행한 뒤 빈
                  창이 보이면 다시 확인해 주세요.
                </p>
              ) : instances.length === 1 ? (
                <p>
                  <strong>{displayInstance(instances[0]!)}</strong>
                  <span>
                    이 창이 비어 있지 않거나 대화상자가 열려 있으면 Host가
                    생성을 거부합니다.
                  </span>
                </p>
              ) : (
                <label>
                  <span>새 프로젝트를 만들 빈 XG5000 창</span>
                  <select
                    value={selectedHwnd}
                    disabled={busy}
                    onChange={(event) => setSelectedHwnd(event.target.value)}
                  >
                    <option value="">정확한 PID/HWND를 선택해 주세요</option>
                    {instances.map((instance) => (
                      <option key={instance.hwnd} value={instance.hwnd}>
                        {displayInstance(instance)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <button
                className="button secondary-button"
                type="button"
                disabled={busy || instancesLoading}
                onClick={() => void loadInstances()}
              >
                XG5000 다시 확인
              </button>
            </div>
          </fieldset>

          <div className="native-project-safety">
            <Icon name="shield" size={17} />
            <span>
              열린 프로젝트, 예상하지 않은 대화상자, 권한 불일치, 기존 파일
              충돌이 있으면 변경 없이 중단합니다.
            </span>
          </div>

          <div className="native-project-actions">
            <button
              className="button primary-button"
              type="submit"
              disabled={
                busy ||
                instancesLoading ||
                !projectName.trim() ||
                !workspaceParent.trim() ||
                !plcSeries.trim() ||
                !cpuModel.trim() ||
                instances.length === 0 ||
                (multipleInstances && !selectedInstance)
              }
            >
              {busy ? "XG5000 확인 및 생성 중…" : "오프라인 프로젝트 만들기"}
            </button>
          </div>
        </form>
      )}

      {operation && (
        <section
          className={`native-project-result ${
            failure ? "native-project-result-failed" : ""
          }`}
          aria-labelledby="native-project-result-title"
        >
          <div className="native-project-result-heading">
            <span>
              <Icon
                name={failure ? "warning" : terminal ? "check" : "activity"}
                size={18}
              />
            </span>
            <div>
              <p className="eyebrow">작업 {operation.nativeProjectId}</p>
              <h3 id="native-project-result-title">
                {isVerifiedCompletion(operation)
                  ? "프로젝트 생성 완료"
                  : operation.state === "failed"
                    ? recoveryMode
                      ? "프로젝트 미생성 확인"
                      : "프로젝트 생성 실패"
                    : operation.state === "quarantined"
                      ? "생성 결과 확인 필요"
                      : operation.state === "completed"
                        ? "생성 결과 확인 필요"
                        : "프로젝트 생성 중"}
              </h3>
            </div>
          </div>

          {typeof operation.result?.projectPath === "string" &&
            operation.result.projectPath && (
            <dl className="native-project-proof">
              <div>
                <dt>프로젝트</dt>
                <dd>{operation.request.projectName}</dd>
              </div>
              <div>
                <dt>파일</dt>
                <dd>{operation.result.projectPath}</dd>
              </div>
              <div>
                <dt>구성</dt>
                <dd>
                  {operation.request.plcSeries} · {operation.request.cpuModel} ·{" "}
                  {operation.request.language}
                </dd>
              </div>
              <div>
                <dt>정체성</dt>
                <dd>
                  {operation.result.identityVerified
                    ? "XG5000 창과 생성 파일 일치 확인"
                    : "확인되지 않음"}
                </dd>
              </div>
              <div className="native-project-proof-wide">
                <dt>SHA-256</dt>
                <dd>
                  {typeof operation.result.projectSha256 === "string" ? (
                    <code>{operation.result.projectSha256}</code>
                  ) : (
                    "확인되지 않음"
                  )}
                </dd>
              </div>
              <div className="native-project-proof-wide">
                <dt>PLC 영향</dt>
                <dd>
                  {operation.result.controllerConnected === false &&
                  operation.result.plcWritePerformed === false
                    ? "연결 안 함 · 다운로드 안 함 · 쓰기 안 함"
                    : operation.result.controllerConnected === true ||
                        operation.result.plcWritePerformed === true
                      ? "예상 밖의 PLC 영향 보고됨 · 사용 중지"
                      : "PLC 무영향 증거가 아직 확인되지 않음"}
                </dd>
              </div>
              {operation.result.workspaceRegistration && (
                <div className="native-project-proof-wide">
                  <dt>분석 작업공간</dt>
                  <dd>
                    {operation.result.workspaceRegistration.state ===
                    "captured_not_bound"
                      ? "보호 복사본 캡처 완료 · 현재 XG5000 세션에는 자동 연결 안 함"
                      : "보호 복사본 캡처 실패 · 기존 로컬 프로젝트 열기 필요"}
                  </dd>
                </div>
              )}
            </dl>
            )}

          {!terminal && (
            <button
              className="button secondary-button"
              type="button"
              disabled={busy}
              onClick={() => void refreshOperation()}
            >
              생성 상태 다시 확인
            </button>
          )}
          {operation.state === "quarantined" && (
            <section
              className="native-project-recovery"
              aria-labelledby="native-project-recovery-title"
            >
              <div>
                <p className="eyebrow">READ-ONLY RECONCILIATION</p>
                <h4 id="native-project-recovery-title">
                  격리된 생성 결과 확인
                </h4>
                <p>
                  저장된 대상과 파일 경로를 현재 XG5000에서 다시 읽어 생성 여부만
                  판정합니다. 생성 명령은 다시 보내지 않습니다.
                </p>
              </div>

              <label>
                <span>검사할 예상 프로젝트 파일</span>
                <input
                  type="text"
                  readOnly
                  spellCheck={false}
                  value={expectedRecoveryProjectPath(operation)}
                />
              </label>

              <div className="native-project-recovery-target">
                <span>저장된 XG5000 대상</span>
                <strong>
                  PID {operation.request.xg5000Pid ?? "—"} · HWND{" "}
                  {operation.request.xg5000Hwnd ?? "—"}
                </strong>
              </div>

              <label>
                <span>현재 상태를 확인할 XG5000 창</span>
                <select
                  value={recoveryHwnd}
                  disabled={busy || instancesLoading}
                  onChange={(event) => setRecoveryHwnd(event.target.value)}
                >
                  <option value="">
                    정확한 PID/HWND를 직접 선택해 주세요
                  </option>
                  {instances.map((instance) => (
                    <option key={instance.hwnd} value={instance.hwnd}>
                      {displayInstance(instance)}
                    </option>
                  ))}
                </select>
              </label>

              {!instancesLoading && instances.length === 0 && (
                <p className="native-project-recovery-warning">
                  실행 중인 XG5000 창이 없습니다. 원래 작업 창을 연 뒤 목록을 다시
                  확인해 주세요.
                </p>
              )}
              {selectedRecoveryInstance && !recoveryTargetMatchesStored && (
                <p className="native-project-recovery-warning">
                  선택한 PID/HWND가 저장된 생성 대상과 다릅니다. 재검사를 실행하지
                  않습니다.
                </p>
              )}

              <div className="native-project-recovery-actions">
                <button
                  className="button secondary-button"
                  type="button"
                  disabled={busy || instancesLoading}
                  onClick={() => void loadInstances()}
                >
                  XG5000 목록 다시 확인
                </button>
                <button
                  className="button primary-button"
                  type="button"
                  disabled={
                    busy ||
                    instancesLoading ||
                    !recoveryTargetMatchesStored ||
                    !expectedRecoveryProjectPath(operation)
                  }
                  onClick={() => void reconcileOperation()}
                >
                  {busy ? "현재 상태 검사 중…" : "현재 상태 다시 검사"}
                </button>
              </div>
            </section>
          )}
        </section>
      )}

      {notice && (
        <p
          className="instance-notice"
          role={failure ? "alert" : "status"}
        >
          {notice}
        </p>
      )}
    </section>
  );
}
