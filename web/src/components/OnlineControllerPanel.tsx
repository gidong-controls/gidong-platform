import { useState } from "react";
import { hostApi, HostApiError } from "../api/client";
import type {
  HostStatusResponse,
  LSControllerDataType,
  LSControllerObservation,
  LSControllerRead,
  LSControllerTrace,
} from "../api/types";
import { Icon } from "./Icon";

interface OnlineControllerPanelProps {
  status: HostStatusResponse;
  conversationId: string | null;
  onClose: () => void;
  onStatusChanged: (status: HostStatusResponse) => void | Promise<void>;
  onInvestigateEvidence: (evidenceId: string, prompt: string) => void;
  targetChangeDisabled: boolean;
}

function timeLabel(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof HostApiError ? error.message : fallback;
}

export function OnlineControllerPanel({
  status,
  conversationId,
  onClose,
  onStatusChanged,
  onInvestigateEvidence,
  targetChangeDisabled,
}: OnlineControllerPanelProps) {
  const [host, setHost] = useState(status.controller.endpoint?.host ?? "");
  const [timeout, setTimeout] = useState(
    String(status.controller.timeoutSeconds ?? 2),
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [observation, setObservation] =
    useState<LSControllerObservation | null>(null);
  const [dataType, setDataType] = useState<LSControllerDataType>("WORD");
  const [variables, setVariables] = useState("%MW0");
  const [readResult, setReadResult] = useState<LSControllerRead | null>(null);
  const [sampleCount, setSampleCount] = useState("10");
  const [intervalMs, setIntervalMs] = useState("500");
  const [traceResult, setTraceResult] = useState<LSControllerTrace | null>(null);

  const requestedVariables = () =>
    variables
      .split(/[\s,]+/)
      .map((value) => value.trim())
      .filter(Boolean);

  const refreshHostStatus = async () => {
    const next = await hostApi.getStatus();
    await onStatusChanged(next);
  };

  const connect = async () => {
    if (busy) {
      return;
    }
    const timeoutSeconds = Number(timeout);
    if (
      !host.trim() ||
      !Number.isFinite(timeoutSeconds) ||
      timeoutSeconds < 0.1 ||
      timeoutSeconds > 10
    ) {
      setNotice("PLC IP, 포트와 제한 시간을 정확히 입력해 주세요.");
      return;
    }
    setBusy(true);
    setNotice("");
    setReadResult(null);
    setTraceResult(null);
    try {
      const next = await hostApi.connectLSController({
        host: host.trim(),
        port: 2004,
        timeoutSeconds,
      });
      setObservation(next);
      setHost(next.endpoint.host);
      setNotice(
        `${next.status.mode} 응답을 받았습니다. 읽기 전용 통신 확인이며 실기 환경 검증 완료를 의미하지 않습니다.`,
      );
      await refreshHostStatus();
    } catch (error) {
      setObservation(null);
      setNotice(errorMessage(error, "LS PLC에 연결하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    if (busy) {
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const next = await hostApi.getLSControllerStatus();
      setObservation(next);
      setNotice(
        `${next.status.mode} 상태를 ${timeLabel(next.observedAt)}에 확인했습니다.`,
      );
      await refreshHostStatus();
    } catch (error) {
      setNotice(errorMessage(error, "LS PLC 상태를 확인하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const readDevices = async () => {
    if (busy || !conversationId) {
      return;
    }
    const requested = requestedVariables();
    if (requested.length < 1 || requested.length > 16) {
      setNotice("한 번에 1~16개의 디바이스 주소를 입력해 주세요.");
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const result = await hostApi.readLSController({
        conversationId,
        dataType,
        variables: requested,
      });
      setReadResult(result);
      setNotice(
        `${result.values.length}개 디바이스를 ${timeLabel(result.observedAt)}에 읽었습니다.`,
      );
    } catch (error) {
      setReadResult(null);
      setNotice(errorMessage(error, "LS PLC 디바이스 값을 읽지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const traceDevices = async () => {
    if (busy || !conversationId) {
      return;
    }
    const requested = requestedVariables();
    const requestedSampleCount = Number(sampleCount);
    const requestedIntervalMs = Number(intervalMs);
    if (requested.length < 1 || requested.length > 16) {
      setNotice("한 번에 1~16개의 디바이스 주소를 입력해 주세요.");
      return;
    }
    if (
      !Number.isInteger(requestedSampleCount) ||
      requestedSampleCount < 2 ||
      requestedSampleCount > 64 ||
      !Number.isInteger(requestedIntervalMs) ||
      requestedIntervalMs < 50 ||
      requestedIntervalMs > 5_000 ||
      (requestedSampleCount - 1) * requestedIntervalMs > 30_000
    ) {
      setNotice("Trace는 2~64회, 50~5000ms 간격, 최대 30초로 설정해 주세요.");
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const result = await hostApi.traceLSController({
        conversationId,
        dataType,
        variables: requested,
        sampleCount: requestedSampleCount,
        intervalMs: requestedIntervalMs,
      });
      setTraceResult(result);
      setNotice(
        `${result.sampleCount}회 관찰에서 ${result.transitionCount}개 값 전이를 확인했습니다.`,
      );
    } catch (error) {
      setTraceResult(null);
      setNotice(errorMessage(error, "LS PLC 디바이스 trace를 수집하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (busy) {
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      await hostApi.disconnectLSController();
      setObservation(null);
      setReadResult(null);
      setTraceResult(null);
      setNotice("LS PLC 읽기 전용 연결을 해제했습니다.");
      await refreshHostStatus();
    } catch (error) {
      setNotice(errorMessage(error, "LS PLC 연결을 해제하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const connected =
    observation !== null || status.controller.state === "ready";
  const identity =
    observation?.provisionalIdentity ??
    status.controller.provisionalIdentity;
  const endpoint = observation?.endpoint ?? status.controller.endpoint;
  const validationEvidence =
    identity?.evidenceStrength ===
    "protocol_status_without_serial_or_project_digest"
      ? "FEnet 상태 응답만 확인 · 일련번호와 프로젝트 digest 없음"
      : identity?.evidenceStrength
        ? `API 증거: ${identity.evidenceStrength}`
        : "검증 등급을 판정할 API 증거 없음";

  return (
    <section className="controller-panel" aria-labelledby="controller-panel-title">
      <div className="instance-picker-heading">
        <div>
          <p className="eyebrow">READ-ONLY · LS XGT FEnet</p>
          <h2 id="controller-panel-title">온라인 LS PLC 읽기 전용 관찰</h2>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label="온라인 PLC 관찰 닫기"
          disabled={busy}
          onClick={onClose}
        >
          <Icon name="close" size={18} />
        </button>
      </div>

      <p className="controller-explanation">
        현재 운전 상태, 지정 디바이스 값과 bounded 변화 trace를 읽습니다. PLC의
        전체 프로젝트를 새 작업공간으로 복원하는 기능과 PLC 쓰기·런중 수정은
        별도 경로이며 여기서는 허용되지 않습니다.
      </p>

      <form
        className="controller-connect-form"
        onSubmit={(event) => {
          event.preventDefault();
          void connect();
        }}
      >
        <label>
          <span>PLC IP</span>
          <input
            type="text"
            inputMode="decimal"
            value={host}
            placeholder="192.168.0.10"
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
            onChange={(event) => setHost(event.target.value)}
          />
        </label>
        <label>
          <span>FEnet 포트</span>
          <input
            type="number"
            value="2004"
            disabled
            aria-describedby="controller-port-note"
          />
          <small id="controller-port-note">LS XGT 전용 TCP 포트</small>
        </label>
        <label>
          <span>제한 시간(초)</span>
          <input
            type="number"
            min="0.1"
            max="10"
            step="0.1"
            value={timeout}
            disabled={busy}
            onChange={(event) => setTimeout(event.target.value)}
          />
        </label>
        <button
          className="button primary-button"
          type="submit"
          disabled={!host.trim() || busy || targetChangeDisabled}
        >
          {busy ? "확인 중…" : "읽기 전용 연결"}
        </button>
      </form>

      {connected && (
        <section className="controller-live-card">
          <div className="controller-live-heading">
            <div>
              <span className="status-dot" />
              <strong>{observation?.status.mode ?? "ONLINE"}</strong>
              <span>{identity?.cpuFamily ?? "LS XGT"}</span>
            </div>
            <button
              className="button secondary-button"
              type="button"
              disabled={busy}
              onClick={() => void refresh()}
            >
              상태 새로 읽기
            </button>
          </div>

          <dl className="controller-proof-grid">
            <div>
              <dt>Endpoint</dt>
              <dd>{endpoint ? `${endpoint.host}:${endpoint.port}` : "—"}</dd>
            </div>
            <div>
              <dt>OS</dt>
              <dd>{identity?.osVersion ?? "—"}</dd>
            </div>
            <div>
              <dt>Identity</dt>
              <dd>임시 식별 · 쓰기 권한 근거로 사용 불가</dd>
            </div>
            <div>
              <dt>권한</dt>
              <dd>Online Observe · 쓰기 차단</dd>
            </div>
            <div className="controller-proof-wide controller-validation-tier">
              <dt>실기 PLC 검증 등급</dt>
              <dd>
                <strong>환경 미검증</strong>
                <span>{validationEvidence}</span>
                <small>
                  현재 연결 응답과 읽기·trace 동작만 확인합니다. 실제 설비·프로젝트
                  일치와 쓰기 안전성은 검증하지 않았습니다.
                </small>
              </dd>
            </div>
          </dl>

          <form
            className="controller-read-form"
            onSubmit={(event) => {
              event.preventDefault();
              void readDevices();
            }}
          >
            <label>
              <span>데이터 형식</span>
              <select
                value={dataType}
                disabled={busy}
                onChange={(event) =>
                  setDataType(event.target.value as LSControllerDataType)
                }
              >
                {["BIT", "BYTE", "WORD", "DWORD", "LWORD"].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="controller-variables">
              <span>디바이스 주소 · 쉼표 또는 공백으로 최대 16개</span>
              <input
                type="text"
                value={variables}
                placeholder="%MW0, %MW1"
                autoComplete="off"
                spellCheck={false}
                disabled={busy}
                onChange={(event) => setVariables(event.target.value)}
              />
            </label>
            <button
              className="button primary-button"
              type="submit"
              disabled={!variables.trim() || busy || !conversationId}
            >
              값 읽기
            </button>
          </form>

          <form
            className="controller-read-form"
            onSubmit={(event) => {
              event.preventDefault();
              void traceDevices();
            }}
          >
            <label>
              <span>샘플 수</span>
              <input
                type="number"
                min="2"
                max="64"
                step="1"
                value={sampleCount}
                disabled={busy}
                onChange={(event) => setSampleCount(event.target.value)}
              />
            </label>
            <label>
              <span>샘플 간격(ms)</span>
              <input
                type="number"
                min="50"
                max="5000"
                step="50"
                value={intervalMs}
                disabled={busy}
                onChange={(event) => setIntervalMs(event.target.value)}
              />
            </label>
            <button
              className="button primary-button"
              type="submit"
              disabled={!variables.trim() || busy || !conversationId}
            >
              값 변화 추적
            </button>
          </form>

          {readResult && (
            <div className="controller-value-table">
              <table>
                <thead>
                  <tr>
                    <th>디바이스</th>
                    <th>값</th>
                    <th>Raw</th>
                  </tr>
                </thead>
                <tbody>
                  {readResult.values.map((value) => (
                    <tr key={value.variable}>
                      <td>
                        <code>{value.variable}</code>
                      </td>
                      <td>{value.unsignedValue}</td>
                      <td>
                        <code>{value.rawHex}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {readResult.evidenceId && (
                <div className="instance-picker-actions">
                  <span>
                    관측 근거 <code>{readResult.evidenceId}</code>
                  </span>
                  <button
                    className="button secondary-button"
                    type="button"
                    disabled={busy || targetChangeDisabled}
                    onClick={() =>
                      onInvestigateEvidence(
                        readResult.evidenceId!,
                        `LS PLC의 ${readResult.values
                          .map((item) => item.variable)
                          .join(", ")} 값을 ${readResult.dataType} 형식의 읽기 전용 도구로 다시 관찰하고, 현재 상태에서 의미와 이상 가능성을 근거와 함께 분석해 주세요.`,
                      )
                    }
                  >
                    Agent로 조사
                  </button>
                </div>
              )}
            </div>
          )}

          {traceResult && (
            <div className="controller-value-table">
              <div className="controller-live-heading">
                <div>
                  <strong>디바이스 변화 요약</strong>
                  <span>
                    {traceResult.sampleCount}회 · {traceResult.intervalMs}ms · 실제 {traceResult.captureDurationMs}ms
                  </span>
                </div>
                <span>
                  CPU mode {traceResult.modeStable ? "유지" : "변경"} · 전이 {traceResult.transitionCount}건
                </span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>디바이스</th>
                    <th>처음 → 마지막</th>
                    <th>최소 · 최대</th>
                    <th>변화 횟수</th>
                  </tr>
                </thead>
                <tbody>
                  {traceResult.summaries.map((summary) => (
                    <tr key={summary.variable}>
                      <td><code>{summary.variable}</code></td>
                      <td>{summary.firstUnsignedValue} → {summary.lastUnsignedValue}</td>
                      <td>{summary.minimumUnsignedValue} · {summary.maximumUnsignedValue}</td>
                      <td>{summary.changeCount}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {traceResult.transitions.length > 0 && (
                <details>
                  <summary>
                    관측된 전이 {traceResult.transitionReturnedCount}건
                    {traceResult.transitionsTruncated ? " · 일부만 표시" : ""}
                  </summary>
                  <ol>
                    {traceResult.transitions.map((transition, index) => (
                      <li key={`${transition.variable}-${transition.toSample}-${index}`}>
                        <code>{transition.variable}</code>{" "}
                        sample {transition.fromSample}→{transition.toSample}:{" "}
                        {transition.fromUnsignedValue}→{transition.toUnsignedValue}
                      </li>
                    ))}
                  </ol>
                </details>
              )}
              <p>
                순차 FEnet 읽기이므로 같은 PLC scan 안의 동시 전이 또는 현재 XG5000 프로젝트와의 일치를 뜻하지 않습니다.
              </p>
              {traceResult.evidenceId && (
                <div className="instance-picker-actions">
                  <span>
                    추적 근거 <code>{traceResult.evidenceId}</code>
                  </span>
                  <button
                    className="button secondary-button"
                    type="button"
                    disabled={busy || targetChangeDisabled}
                    onClick={() =>
                      onInvestigateEvidence(
                        traceResult.evidenceId!,
                        `LS PLC의 ${traceResult.variables.join(", ")} 값을 읽기 전용 trace 도구로 다시 추적하고, 전이 패턴과 가능한 원인을 근거와 함께 분석해 주세요.`,
                      )
                    }
                  >
                    Agent로 조사
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {notice && (
        <p className="instance-notice" role="status">
          {notice}
        </p>
      )}

      {status.controller.state === "ready" && (
        <div className="instance-picker-actions">
          <button
            className="button secondary-button"
            type="button"
            disabled={busy || targetChangeDisabled}
            onClick={() => void disconnect()}
          >
            연결 해제
          </button>
        </div>
      )}
    </section>
  );
}
