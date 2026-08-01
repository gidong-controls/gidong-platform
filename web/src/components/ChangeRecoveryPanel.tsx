import { useEffect, useMemo, useState } from "react";
import { hostApi, HostApiError } from "../api/client";
import type {
  ChangeTransaction,
  XG5000Instance,
} from "../api/types";
import { Icon } from "./Icon";

interface ChangeRecoveryPanelProps {
  transaction: ChangeTransaction;
  busy: boolean;
  onReconcile: (pid: number, hwnd: number) => Promise<void>;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function nestedRecord(
  value: Record<string, unknown> | null,
  key: string,
): Record<string, unknown> | null {
  return value ? asRecord(value[key]) : null;
}

function recoveryCheckpoint(
  transaction: ChangeTransaction,
): Record<string, unknown> | null {
  const reconciliation = asRecord(transaction.result.reconciliation);
  const finalStatus = nestedRecord(reconciliation, "final_status");
  const abort = nestedRecord(reconciliation, "abort");
  const lookup = nestedRecord(reconciliation, "lookup");
  const status = nestedRecord(reconciliation, "status");
  return (
    nestedRecord(finalStatus, "transaction_checkpoint") ??
    nestedRecord(abort, "transaction_checkpoint") ??
    nestedRecord(lookup, "transaction_checkpoint") ??
    nestedRecord(status, "transaction_checkpoint")
  );
}

function recoveryOutcome(transaction: ChangeTransaction): string {
  if (transaction.errorCode === "explicit_recovery_aborted_before_mutation") {
    return "변경 전에 중단된 사실을 확인하고 작업 잠금을 해제했습니다.";
  }
  if (transaction.errorCode === "explicit_recovery_rolled_back") {
    return "원래 상태로 원복하고 저장한 사실을 확인했습니다.";
  }
  if (transaction.state === "completed") {
    return "이미 커밋된 네이티브 상태임을 확인했습니다.";
  }
  if (transaction.state === "rolled_back") {
    return "네이티브 트랜잭션이 원복 또는 무변경 종료 상태임을 확인했습니다.";
  }
  return "복구 증거가 충분하지 않아 격리를 유지합니다.";
}

function instanceLabel(instance: XG5000Instance): string {
  const title = instance.title?.trim() || "XG5000";
  return `${title} · PID ${instance.pid} · HWND ${instance.hwnd}`;
}

export function ChangeRecoveryPanel({
  transaction,
  busy,
  onReconcile,
}: ChangeRecoveryPanelProps) {
  const [instances, setInstances] = useState<XG5000Instance[]>([]);
  const [selectedHwnd, setSelectedHwnd] = useState("");
  const [instancesLoading, setInstancesLoading] = useState(
    transaction.state === "quarantined",
  );
  const [instanceNotice, setInstanceNotice] = useState("");

  const selectedInstance = useMemo(
    () =>
      instances.find((instance) => String(instance.hwnd) === selectedHwnd) ??
      null,
    [instances, selectedHwnd],
  );

  const loadInstances = async () => {
    setInstancesLoading(true);
    setInstanceNotice("");
    setSelectedHwnd("");
    try {
      const response = await hostApi.getXG5000Instances();
      setInstances(response.instances);
      if (response.status !== "ready" || response.instances.length === 0) {
        setInstanceNotice(
          "복구 대상을 확인할 수 없습니다. 해당 작업 복사본을 연 XG5000 창을 준비한 뒤 다시 확인해 주세요.",
        );
      }
    } catch (error) {
      setInstances([]);
      setInstanceNotice(
        error instanceof HostApiError
          ? error.message
          : "XG5000 복구 대상을 확인하지 못했습니다.",
      );
    } finally {
      setInstancesLoading(false);
    }
  };

  useEffect(() => {
    if (transaction.state === "quarantined") {
      void loadInstances();
    }
    // Reloading is explicit after the first recovery-only target scan.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transaction.transactionId, transaction.state]);

  const checkpoint = recoveryCheckpoint(transaction);
  const reconciliation = asRecord(transaction.result.reconciliation);
  const recovered = transaction.state !== "quarantined";
  const nativeMutation = reconciliation?.native_mutation_performed;
  const nativeState =
    typeof checkpoint?.state === "string" ? checkpoint.state : undefined;
  const terminalProven = checkpoint?.terminal === true;
  const projectHash =
    typeof checkpoint?.current_project_hash === "string"
      ? checkpoint.current_project_hash
      : undefined;

  return (
    <section
      className={`change-recovery ${
        recovered ? "change-recovery-resolved" : ""
      }`}
      aria-labelledby="change-recovery-title"
    >
      <header className="change-recovery-header">
        <span>
          <Icon name={recovered ? "check" : "warning"} size={20} />
        </span>
        <div>
          <p className="panel-kicker">RECOVERY ONLY</p>
          <h3 id="change-recovery-title">
            {recovered ? "격리 복구 증거" : "격리된 변경 상태 확인"}
          </h3>
          <p>
            제안한 변경을 다시 적용하지 않습니다. MCP 체크포인트를 검사해
            변경 전 중단이 증명되면 종료하고, 변경 가능성이 증명된 경우에만
            원래 상태로 원복합니다.
          </p>
        </div>
      </header>

      {transaction.state === "quarantined" && (
        <form
          className="change-recovery-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (selectedInstance && !busy) {
              void onReconcile(selectedInstance.pid, selectedInstance.hwnd);
            }
          }}
        >
          <label>
            <span>복구 전용으로 확인할 XG5000 창</span>
            <select
              value={selectedHwnd}
              disabled={busy || instancesLoading}
              onChange={(event) => setSelectedHwnd(event.target.value)}
            >
              <option value="">PID/HWND를 직접 확인해 선택해 주세요</option>
              {instances.map((instance) => (
                <option key={instance.hwnd} value={instance.hwnd}>
                  {instanceLabel(instance)}
                </option>
              ))}
            </select>
          </label>
          <button
            className="button secondary-button"
            type="button"
            disabled={busy || instancesLoading}
            onClick={() => void loadInstances()}
          >
            XG5000 다시 확인
          </button>
          <button
            className="button danger-button"
            type="submit"
            disabled={busy || instancesLoading || !selectedInstance}
          >
            {busy ? "복구 증거 확인 중…" : "선택한 창으로 격리 복구 검사"}
          </button>
        </form>
      )}

      {instanceNotice && transaction.state === "quarantined" && (
        <p className="change-recovery-notice" role="status">
          {instanceNotice}
        </p>
      )}

      {reconciliation && (
        <dl className="change-recovery-evidence">
          <div>
            <dt>판정</dt>
            <dd>{recoveryOutcome(transaction)}</dd>
          </div>
          <div>
            <dt>Host 상태</dt>
            <dd>{transaction.state}</dd>
          </div>
          {transaction.errorCode && (
            <div>
              <dt>증거 코드</dt>
              <dd>
                <code>{transaction.errorCode}</code>
              </dd>
            </div>
          )}
          {nativeState && (
            <div>
              <dt>네이티브 상태</dt>
              <dd>
                <code>{nativeState}</code>
                {terminalProven ? " · terminal 확인" : ""}
              </dd>
            </div>
          )}
          {typeof nativeMutation === "boolean" && (
            <div>
              <dt>네이티브 변경</dt>
              <dd>{nativeMutation ? "변경 흔적 확인 후 원복" : "변경 없음 확인"}</dd>
            </div>
          )}
          {projectHash && (
            <div>
              <dt>프로젝트 해시</dt>
              <dd>
                <code>{projectHash.slice(0, 16)}…</code>
              </dd>
            </div>
          )}
        </dl>
      )}

      {transaction.state === "quarantined" && reconciliation && (
        <p className="change-recovery-hold" role="alert">
          격리가 해제되지 않았습니다. 자동 재시도하거나 변경을 다시 적용하지
          마세요. 위 오류와 XG5000 대상을 확인한 뒤에만 복구 검사를 다시
          요청할 수 있습니다.
        </p>
      )}
    </section>
  );
}
