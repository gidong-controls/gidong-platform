import { useEffect, useId, useRef, useState } from "react";
import { hostApi, HostApiError } from "../api/client";
import type {
  ModelCapabilityResult,
  SaveModelSettingsRequest,
} from "../api/types";
import { Icon } from "./Icon";

interface ModelSettingsDialogProps {
  open: boolean;
  hostReady: boolean;
  onClose: () => void;
  onSaved: () => void;
}

interface ModelSettingsForm {
  baseUrl: string;
  model: string;
  apiKey: string;
  timeoutSeconds: number;
  externalModelConsent: boolean;
  insecureHttpConsent: boolean;
  newHeaderName: string;
  newHeaderValue: string;
}

const DEFAULT_FORM: ModelSettingsForm = {
  baseUrl: "",
  model: "",
  apiKey: "",
  timeoutSeconds: 60,
  externalModelConsent: false,
  insecureHttpConsent: false,
  newHeaderName: "",
  newHeaderValue: "",
};

const CAPABILITY_LABELS: Record<string, string> = {
  completion: "일반 응답",
  streaming: "스트리밍",
  toolCalling: "도구 호출",
  toolResultContinuation: "도구 결과 후속 응답",
  structuredOutput: "구조화 출력",
};

function errorMessage(error: unknown): string {
  if (error instanceof HostApiError) {
    return error.message;
  }
  return "ControlStudio Host에 연결할 수 없습니다.";
}

function isLoopbackHost(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return (
    normalized === "localhost" ||
    normalized === "::1" ||
    normalized === "0:0:0:0:0:0:0:1" ||
    normalized === "127.0.0.1" ||
    normalized.startsWith("127.")
  );
}

function isExternalEndpoint(value: string): boolean {
  try {
    return !isLoopbackHost(new URL(value).hostname);
  } catch {
    return false;
  }
}

function isInsecureExternalHttpEndpoint(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" && !isLoopbackHost(url.hostname);
  } catch {
    return false;
  }
}

export function ModelSettingsDialog({
  open,
  hostReady,
  onClose,
  onSaved,
}: ModelSettingsDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const firstInputRef = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [savedHeaderNames, setSavedHeaderNames] = useState<string[]>([]);
  const [hasSavedApiKey, setHasSavedApiKey] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [notice, setNotice] = useState("");
  const [testResult, setTestResult] =
    useState<ModelCapabilityResult | null>(null);

  useEffect(() => {
    if (!open) {
      setForm((current) =>
        current.apiKey || current.newHeaderValue
          ? {
              ...current,
              apiKey: "",
              newHeaderValue: "",
            }
          : current,
      );
      return;
    }

    const controller = new AbortController();
    setNotice("");
    setTestResult(null);
    firstInputRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);

    if (hostReady) {
      setLoading(true);
      void hostApi
        .getModelSettings(controller.signal)
        .then((settings) => {
          setForm({
            baseUrl: settings.baseUrl,
            model: settings.model,
            apiKey: "",
            timeoutSeconds: settings.timeoutSeconds,
            externalModelConsent: false,
            insecureHttpConsent: false,
            newHeaderName: "",
            newHeaderValue: "",
          });
          setSavedHeaderNames(settings.customHeaderNames);
          setHasSavedApiKey(settings.hasApiKey);
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) {
            return;
          }
          if (error instanceof HostApiError && error.status === 404) {
            setForm(DEFAULT_FORM);
            setSavedHeaderNames([]);
            setHasSavedApiKey(false);
            return;
          }
          setNotice(errorMessage(error));
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setLoading(false);
          }
        });
    }

    return () => {
      controller.abort();
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [hostReady, onClose, open]);

  if (!open) {
    return null;
  }

  const externalEndpoint = isExternalEndpoint(form.baseUrl);
  const insecureExternalHttp = isInsecureExternalHttpEndpoint(form.baseUrl);
  const headerPartiallyEntered =
    Boolean(form.newHeaderName.trim()) !== Boolean(form.newHeaderValue);
  const isValid =
    Boolean(form.baseUrl.trim() && form.model.trim()) &&
    !headerPartiallyEntered &&
    (!externalEndpoint || form.externalModelConsent) &&
    (!insecureExternalHttp || form.insecureHttpConsent);
  const unavailableReason = !hostReady
    ? "Host가 준비된 뒤 연결을 테스트하고 저장할 수 있습니다."
    : "";

  const requestPayload = (): SaveModelSettingsRequest => {
    const headerName = form.newHeaderName.trim();
    return {
      protocol: "openai_chat_completions",
      baseUrl: form.baseUrl.trim(),
      model: form.model.trim(),
      apiKey: form.apiKey.trim() || undefined,
      customHeaders: headerName
        ? { [headerName]: form.newHeaderValue }
        : undefined,
      timeoutSeconds: form.timeoutSeconds,
      externalModelConsent: form.externalModelConsent,
      insecureHttpConsent: form.insecureHttpConsent,
    };
  };

  const testConnection = async () => {
    setBusy("test");
    setNotice("");
    setTestResult(null);
    try {
      const result = await hostApi.testModelSettings(requestPayload());
      setTestResult(result);
      setNotice(result.message);
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(null);
    }
  };

  const saveSettings = async () => {
    setBusy("save");
    setNotice("");
    try {
      const saved = await hostApi.saveModelSettings(requestPayload());
      setSavedHeaderNames(saved.customHeaderNames);
      setHasSavedApiKey(saved.hasApiKey);
      setForm((current) => ({
        ...current,
        apiKey: "",
        newHeaderName: "",
        newHeaderValue: "",
      }));
      setNotice("모델 연결 설정을 Windows 보안 저장소에 저장했습니다.");
      onSaved();
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dialog-header">
          <div className="dialog-title-group">
            <span className="dialog-icon">
              <Icon name="settings" size={19} />
            </span>
            <div>
              <p className="eyebrow">MODEL CONNECTION</p>
              <h2 id={titleId}>모델 연결 설정</h2>
            </div>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label="설정 닫기"
          >
            <Icon name="close" />
          </button>
        </header>

        <p id={descriptionId} className="dialog-description">
          OpenAI 호환 Chat Completions 엔드포인트를 연결합니다. 저장된 API
          키와 헤더 값은 브라우저로 다시 전송하거나 표시하지 않습니다.
        </p>

        <form
          className="settings-form"
          onSubmit={(event) => event.preventDefault()}
        >
          <label className="field full-field">
            <span className="field-label">API 형식</span>
            <span className="select-shell">
              <select value="openai_chat_completions" disabled>
                <option value="openai_chat_completions">
                  OpenAI Compatible · Chat Completions
                </option>
              </select>
              <Icon name="chevron" size={16} />
            </span>
          </label>

          <label className="field full-field">
            <span className="field-label">Base URL</span>
            <input
              ref={firstInputRef}
              type="url"
              inputMode="url"
              autoComplete="url"
              value={form.baseUrl}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  baseUrl: event.target.value,
                  externalModelConsent: false,
                  insecureHttpConsent: false,
                }))
              }
              placeholder="https://gateway.example.com/v1"
            />
            <span className="field-hint">
              localhost 외의 주소에는 HTTPS가 기본입니다. 신뢰하는 사설 또는
              Tailscale IP의 HTTP는 별도 동의 후 사용할 수 있습니다.
            </span>
          </label>

          <label className="field">
            <span className="field-label">모델</span>
            <input
              type="text"
              autoComplete="off"
              value={form.model}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  model: event.target.value,
                }))
              }
              placeholder="gateway-model-alias"
            />
          </label>

          <label className="field">
            <span className="field-label">API Key</span>
            <input
              type="password"
              autoComplete="new-password"
              value={form.apiKey}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  apiKey: event.target.value,
                }))
              }
              placeholder={
                hasSavedApiKey
                  ? "저장된 키 유지 또는 새 키 입력"
                  : "필요한 경우 API 키 입력"
              }
            />
            <span className="field-hint">
              {hasSavedApiKey
                ? "Windows 보안 저장소에 키가 있습니다. 값은 표시하지 않습니다."
                : "저장된 API 키가 없습니다."}
            </span>
          </label>

          <label className="field compact-field">
            <span className="field-label">요청 제한 시간</span>
            <span className="input-with-unit">
              <input
                type="number"
                min={10}
                max={300}
                value={form.timeoutSeconds}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    timeoutSeconds: Number(event.target.value),
                  }))
                }
              />
              <span>초</span>
            </span>
          </label>

          <div className="field full-field">
            <span className="field-label">저장된 사용자 지정 헤더</span>
            {savedHeaderNames.length > 0 ? (
              <ul className="secret-name-list" aria-label="저장된 헤더 이름">
                {savedHeaderNames.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            ) : (
              <span className="field-hint">저장된 사용자 지정 헤더가 없습니다.</span>
            )}
            <span className="field-hint">
              보안을 위해 헤더 이름만 표시하며 저장된 값은 Host 밖으로 나오지
              않습니다.
            </span>
          </div>

          <label className="field">
            <span className="field-label">교체할 헤더 이름 (선택)</span>
            <input
              type="text"
              autoComplete="off"
              value={form.newHeaderName}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  newHeaderName: event.target.value,
                }))
              }
              placeholder="X-Gateway-Token"
            />
          </label>

          <label className="field">
            <span className="field-label">교체할 헤더 값 (선택)</span>
            <input
              type="password"
              autoComplete="new-password"
              value={form.newHeaderValue}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  newHeaderValue: event.target.value,
                }))
              }
              placeholder="저장할 새 값"
            />
          </label>

          {externalEndpoint && (
            <label className="consent-field full-field">
              <input
                type="checkbox"
                checked={form.externalModelConsent}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    externalModelConsent: event.target.checked,
                  }))
                }
              />
              <span>
                PLC 프로젝트 내용과 대화가 이 PC 밖의 모델 엔드포인트로 전송될
                수 있음을 확인하고 동의합니다.
              </span>
            </label>
          )}
          {insecureExternalHttp && (
            <label className="consent-field full-field">
              <input
                type="checkbox"
                checked={form.insecureHttpConsent}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    insecureHttpConsent: event.target.checked,
                  }))
                }
              />
              <span>
                이 주소는 인증서가 없는 HTTP입니다. 신뢰하는 사설 또는 Tailscale
                네트워크의 IP 주소임을 확인하고 사용에 동의합니다.
              </span>
            </label>
          )}
        </form>

        {headerPartiallyEntered && (
          <div className="inline-notice warning-notice">
            <Icon name="warning" size={17} />
            <span>새 헤더를 저장하려면 이름과 값을 모두 입력해 주세요.</span>
          </div>
        )}
        {form.newHeaderName.trim() && form.newHeaderValue && (
          <div className="inline-notice warning-notice">
            <Icon name="warning" size={17} />
            <span>
              저장하면 기존 사용자 지정 헤더 묶음을 지금 입력한 헤더로
              교체합니다.
            </span>
          </div>
        )}

        <div className="capability-box">
          <div className="capability-heading">
            <Icon name="shield" size={17} />
            <span>에이전트 준비 검사</span>
          </div>
          <p>
            일반 응답뿐 아니라 스트리밍, 도구 호출, 도구 결과 후속 응답,
            구조화 출력을 실제 모델 요청으로 확인합니다. 실행 중 작업 취소는
            모델 연결 능력이 아니라 Host run 경로에서 별도로 검증합니다.
          </p>
          {testResult && (
            <ul className="capability-list" aria-label="모델 기능 검사 결과">
              {Object.entries(testResult.capabilities).map(([name, passed]) => (
                <li key={name} className={passed ? "passed" : "failed"}>
                  <Icon name={passed ? "check" : "warning"} size={14} />
                  <span>{CAPABILITY_LABELS[name] ?? name}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {!hostReady && (
          <div className="inline-notice warning-notice">
            <Icon name="warning" size={17} />
            <span>{unavailableReason}</span>
          </div>
        )}

        {loading && <p className="form-status">저장된 설정 확인 중…</p>}
        {notice && (
          <p className="form-status" role="status" aria-live="polite">
            {notice}
          </p>
        )}

        <footer className="dialog-actions">
          <button
            className="button secondary-button"
            type="button"
            onClick={onClose}
          >
            닫기
          </button>
          <button
            className="button secondary-button"
            type="button"
            disabled={!hostReady || !isValid || busy !== null}
            onClick={() => void testConnection()}
          >
            {busy === "test" ? "확인 중…" : "연결 테스트"}
          </button>
          <button
            className="button primary-button"
            type="button"
            disabled={!hostReady || !isValid || busy !== null}
            onClick={() => void saveSettings()}
          >
            {busy === "save" ? "저장 중…" : "설정 저장"}
          </button>
        </footer>
      </section>
    </div>
  );
}
