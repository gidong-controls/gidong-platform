import { useEffect, useMemo, useState } from "react";
import { hostApi, HostApiError } from "../api/client";
import {
  hasSupportedChangeRecoveryContract,
} from "../api/changeContracts";
import {
  canOfferRecoveryRelaunch,
  journaledRecoveryRelaunchTarget,
} from "../api/changeRecovery";
import type {
  ChangeTransaction,
  XG5000Instance,
} from "../api/types";
import { Icon } from "./Icon";

interface ChangeRecoveryPanelProps {
  transaction: ChangeTransaction;
  busy: boolean;
  mcpReady: boolean;
  currentWorkspaceHash?: string;
  onReconcile: (
    pid: number,
    hwnd: number,
    options?: RecoveryReconcileOptions,
  ) => Promise<void>;
}

interface RecoveryReconcileOptions {
  relaunchIfUnavailable?: boolean;
  restoreVerifiedBackup?: boolean;
  expectedCurrentProjectHash?: string;
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
  const rollbackOperation = nestedRecord(reconciliation, "rollback_operation");
  return (
    nestedRecord(finalStatus, "transaction_checkpoint") ??
    nestedRecord(abort, "transaction_checkpoint") ??
    nestedRecord(lookup, "transaction_checkpoint") ??
    nestedRecord(status, "transaction_checkpoint") ??
    nestedRecord(rollbackOperation, "transaction_checkpoint")
  );
}

function hasSupportedRecoveryContract(transaction: ChangeTransaction): boolean {
  return hasSupportedChangeRecoveryContract(transaction.plan);
}

function recoveryOutcome(transaction: ChangeTransaction): string {
  if (
    transaction.errorCode === "explicit_recovery_aborted_before_mutation" ||
    transaction.errorCode === "mne_recovery_aborted_before_mutation"
  ) {
    return "변경 전에 중단된 사실을 확인하고 작업 잠금을 해제했습니다.";
  }
  if (
    transaction.errorCode === "explicit_recovery_rolled_back" ||
    transaction.errorCode === "explicit_mne_recovery_rolled_back" ||
    transaction.errorCode === "mne_verification_failed_rolled_back"
  ) {
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

function shortHash(value: string): string {
  return `${value.slice(0, 16)}…`;
}

export function ChangeRecoveryPanel({
  transaction,
  busy,
  mcpReady,
  currentWorkspaceHash,
  onReconcile,
}: ChangeRecoveryPanelProps) {
  const recoverySupported = hasSupportedRecoveryContract(transaction);
  const [instances, setInstances] = useState<XG5000Instance[]>([]);
  const [selectedHwnd, setSelectedHwnd] = useState("");
  const [instancesLoading, setInstancesLoading] = useState(
    transaction.state === "quarantined" && recoverySupported,
  );
  const [inventoryConfirmedEmpty, setInventoryConfirmedEmpty] = useState(false);
  const [instanceNotice, setInstanceNotice] = useState("");

  const selectedInstance = useMemo(
    () =>
      instances.find((instance) => String(instance.hwnd) === selectedHwnd) ??
      null,
    [instances, selectedHwnd],
  );

  const loadInstances = async () => {
    setInstancesLoading(true);
    setInventoryConfirmedEmpty(false);
    setInstanceNotice("");
    setSelectedHwnd("");
    try {
      const response = await hostApi.getXG5000Instances();
      setInstances(response.instances);
      setInventoryConfirmedEmpty(
        response.status === "ready" && response.instances.length === 0,
      );
      if (response.status !== "ready" || response.instances.length === 0) {
        setInstanceNotice(
          "복구 대상을 확인할 수 없습니다. 해당 작업 복사본을 연 XG5000 창을 준비한 뒤 다시 확인해 주세요.",
        );
      }
    } catch (error) {
      setInstances([]);
      setInventoryConfirmedEmpty(false);
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
    if (
      transaction.state === "quarantined" &&
      recoverySupported &&
      mcpReady
    ) {
      void loadInstances();
    } else {
      setInstancesLoading(false);
    }
    // A newly ready MCP may refresh this read-only inventory once. Selection
    // and reconciliation remain explicit user actions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mcpReady, recoverySupported, transaction.transactionId, transaction.state]);

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
  const relaunchTarget = journaledRecoveryRelaunchTarget(
    transaction.targetIdentity,
  );
  const relaunchAvailable = canOfferRecoveryRelaunch({
    transactionState: transaction.state,
    recoverySupported,
    inventoryConfirmedEmpty,
    targetIdentity: transaction.targetIdentity,
  });
  const hasCurrentHashMismatch =
    transaction.state === "quarantined" &&
    typeof currentWorkspaceHash === "string" &&
    /^[0-9a-f]{64}$/i.test(currentWorkspaceHash) &&
    currentWorkspaceHash.toLowerCase() !==
      transaction.targetIdentity.artifactHash.toLowerCase();
  const restoreReviewKey =
    hasCurrentHashMismatch && relaunchTarget
      ? [
          transaction.transactionId,
          transaction.planDigest,
          currentWorkspaceHash!.toLowerCase(),
          transaction.targetIdentity.artifactHash.toLowerCase(),
          relaunchTarget.pid,
          relaunchTarget.hwnd,
        ].join(":")
      : null;
  const [confirmedRestoreReviewKey, setConfirmedRestoreReviewKey] = useState<
    string | null
  >(null);
  const restoreConfirmed = confirmedRestoreReviewKey === restoreReviewKey;

  useEffect(() => {
    setConfirmedRestoreReviewKey(null);
  }, [restoreReviewKey]);
  const restoreReceipt = nestedRecord(reconciliation, "working_copy_restore");
  const preservationRef =
    typeof restoreReceipt?.preservation_ref === "string"
      ? restoreReceipt.preservation_ref
      : null;
  const preservationDigest =
    typeof restoreReceipt?.preservation_digest === "string"
      ? restoreReceipt.preservation_digest
      : null;

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

      {transaction.state === "quarantined" && recoverySupported && (
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
          {!hasCurrentHashMismatch && relaunchAvailable && relaunchTarget && (
            <button
              className="button secondary-button"
              type="button"
              disabled={busy || instancesLoading}
              onClick={() =>
                void onReconcile(relaunchTarget.pid, relaunchTarget.hwnd, {
                  relaunchIfUnavailable: true,
                })
              }
            >
              XG5000에서 작업 복사본 다시 열어 복구
            </button>
          )}
          {hasCurrentHashMismatch && relaunchAvailable && relaunchTarget && (
            <div className="change-recovery-restore" role="alert">
              <p>
                현재 작업 복사본 해시가 이 거래가 검증한 원본과 다릅니다. 이는
                이전 네이티브 오류와 별개의 현재 복구 차단 사유입니다.
              </p>
              <dl className="change-recovery-evidence">
                <div>
                  <dt>현재 파일</dt>
                  <dd><code>{shortHash(currentWorkspaceHash!)}</code></dd>
                </div>
                <div>
                  <dt>검증된 거래 백업</dt>
                  <dd><code>{shortHash(transaction.targetIdentity.artifactHash)}</code></dd>
                </div>
              </dl>
              <label className="change-recovery-confirm">
                <input
                  type="checkbox"
                  checked={restoreConfirmed}
                  disabled={busy || instancesLoading}
                  onChange={(event) =>
                    setConfirmedRestoreReviewKey(
                      event.target.checked ? restoreReviewKey : null,
                    )
                  }
                />
                <span>
                  현재 파일 전체를 MCP 보존 증거로 먼저 저장한 뒤, 검증된 거래
                  백업으로만 복원하고 실제 XG5000 재열기 검증을 요청합니다.
                </span>
              </label>
              <button
                className="button danger-button"
                type="button"
                disabled={busy || instancesLoading || !restoreConfirmed}
                onClick={() =>
                  void onReconcile(relaunchTarget.pid, relaunchTarget.hwnd, {
                    relaunchIfUnavailable: true,
                    restoreVerifiedBackup: true,
                    expectedCurrentProjectHash: currentWorkspaceHash!,
                  })
                }
              >
                검토한 백업 복원 후 XG5000 복구 검증
              </button>
              <p className="change-recovery-hold">
                이 요청은 PLC 다운로드·온라인 쓰기·제안 변경 재적용을 포함하지
                않습니다. 보존 증거 식별자는 성공 영수증이 기록된 뒤에만 표시됩니다.
              </p>
            </div>
          )}
        </form>
      )}

      {transaction.state === "quarantined" && !recoverySupported && (
        <p className="change-recovery-hold" role="alert">
          알 수 없거나 잘못된 변경 계약에는 복구 명령을 보낼 수 없습니다. Host에서
          planKind와 검증 범위를 다시 확인하세요.
        </p>
      )}

      {instanceNotice &&
        transaction.state === "quarantined" &&
        recoverySupported && (
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
          {preservationRef && preservationDigest && (
            <div>
              <dt>현재 파일 보존 영수증</dt>
              <dd>
                <code>{preservationRef}</code> · <code>{shortHash(preservationDigest)}</code>
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
