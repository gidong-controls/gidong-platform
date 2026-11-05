import { useEffect, useMemo, useState } from "react";
import { hostApi, HostApiError } from "../api/client";
import {
  hasSupportedChangeRecoveryContract,
} from "../api/changeContracts";
import {
  canOfferRecoveryRelaunch,
  journaledRecoveryRelaunchTarget,
  executionTargetIdentity,
} from "../api/changeRecovery";
import type {
  ChangeTransaction,
  ReviewedNoSaveCloseRequest,
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
  onReviewClose: (target: Omit<ReviewedNoSaveCloseRequest, "confirmNoSave">, title?: string) => void;
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
    transaction.errorCode === "explicit_recovery_aborted_before_mutation"
  ) {
    return "변경 전에 중단된 사실을 확인하고 작업 잠금을 해제했습니다.";
  }
  if (
    transaction.errorCode === "explicit_recovery_rolled_back"
  ) {
    return "원래 프로젝트를 복원했습니다.";
  }
  if (transaction.state === "completed") {
    return "이미 커밋된 네이티브 상태임을 확인했습니다.";
  }
  if (transaction.state === "rolled_back") {
    return "네이티브 트랜잭션이 원복 또는 무변경 종료 상태임을 확인했습니다.";
  }
  return "복구가 완료되지 않았습니다.";
}

function instanceLabel(instance: XG5000Instance): string {
  const title = instance.title?.trim() || "XG5000";
  return `${title} · PID ${instance.pid} · HWND ${instance.hwnd}`;
}

function shortHash(value: string): string {
  return `${value.slice(0, 16)}…`;
}

export function recoveryReviewedCloseTarget(
  transaction: ChangeTransaction,
  instance: XG5000Instance | null,
  currentWorkspaceHash?: string,
): Omit<ReviewedNoSaveCloseRequest, "confirmNoSave"> | null {
  if (!instance || !Number.isSafeInteger(instance.pid) || instance.pid <= 0 ||
      !Number.isSafeInteger(instance.hwnd) || instance.hwnd <= 0 ||
      typeof instance.process_create_time !== "number" ||
      !Number.isFinite(instance.process_create_time) || instance.process_create_time <= 0) {
    return null;
  }
  const projectPath = executionTargetIdentity(transaction).projectPath;
  // Workspace metadata is the reviewed expected file, not live evidence that
  // this selected window owns it. The Host must authenticate its native receipt.
  const hasFileContext = typeof projectPath === "string" && projectPath.trim() !== "" &&
    typeof currentWorkspaceHash === "string" && /^[0-9a-f]{64}$/i.test(currentWorkspaceHash);
  return {
    pid: instance.pid,
    hwnd: instance.hwnd,
    processCreateTime: instance.process_create_time,
    ...(hasFileContext ? { projectPath, projectHash: currentWorkspaceHash.toLowerCase() } : {}),
  };
}

export function ChangeRecoveryPanel({
  transaction,
  busy,
  mcpReady,
  currentWorkspaceHash,
  onReconcile,
  onReviewClose,
}: ChangeRecoveryPanelProps) {
  const recoverySupported = hasSupportedRecoveryContract(transaction);
  const [instances, setInstances] = useState<XG5000Instance[]>([]);
  const [selectedHwnd, setSelectedHwnd] = useState("");
  const [instancesLoading, setInstancesLoading] = useState(
    transaction.state === "quarantined" && recoverySupported,
  );
  const [inventoryConfirmed, setInventoryConfirmed] = useState(false);
  const [instanceNotice, setInstanceNotice] = useState("");

  const selectedInstance = useMemo(
    () =>
      instances.find((instance) => String(instance.hwnd) === selectedHwnd) ??
      null,
    [instances, selectedHwnd],
  );

  const loadInstances = async () => {
    setInstancesLoading(true);
    setInventoryConfirmed(false);
    setInstanceNotice("");
    setSelectedHwnd("");
    try {
      const response = await hostApi.getXG5000Instances();
      setInstances(response.instances);
      setInventoryConfirmed(response.status === "ready");
      if (response.status !== "ready" || response.instances.length === 0) {
        setInstanceNotice(
          "복구 대상을 확인할 수 없습니다. 해당 작업 복사본을 연 XG5000 창을 준비한 뒤 다시 확인해 주세요.",
        );
      }
    } catch (error) {
      setInstances([]);
      setInventoryConfirmed(false);
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
  const journaledCurrentHash =
    typeof projectHash === "string" && /^[0-9a-f]{64}$/i.test(projectHash)
      ? projectHash.toLowerCase()
      : undefined;
  const relaunchTarget = journaledRecoveryRelaunchTarget(
    executionTargetIdentity(transaction),
  );
  const closeTarget = recoveryReviewedCloseTarget(transaction, selectedInstance, currentWorkspaceHash);
  const relaunchAvailable = canOfferRecoveryRelaunch({
    transactionState: transaction.state,
    recoverySupported,
    inventoryConfirmed,
    instances,
    targetIdentity: executionTargetIdentity(transaction),
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
          <p className="panel-kicker">중단된 작업 확인</p>
          <h3 id="change-recovery-title">
            {recovered ? "격리 복구 증거" : "격리된 변경 상태 확인"}
          </h3>
          <p>
            중단된 작업의 상태와 복구 증거를 확인합니다.
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
          <button
            className="button secondary-button"
            type="button"
            disabled={busy || instancesLoading || !closeTarget}
            onClick={() => {
              if (closeTarget && selectedInstance) {
                onReviewClose(closeTarget, selectedInstance.title);
              }
            }}
          >
            선택한 창 저장 없이 닫기 검토
          </button>
          {relaunchAvailable && relaunchTarget && (
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
              {journaledCurrentHash
                ? "기록된 작업 복사본 다시 열어 복구"
                : "XG5000에서 작업 복사본 다시 열어 복구"}
            </button>
          )}
          {hasCurrentHashMismatch && relaunchAvailable && relaunchTarget && (
            <div className="change-recovery-restore" role="alert">
              <p>
                현재 파일이 백업과 다릅니다. 현재 파일을 보관하고 백업을 복원할 수 있습니다.
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
                  현재 파일을 보관한 뒤 백업을 복원합니다.
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
            </div>
          )}
        </form>
      )}

      {transaction.state === "quarantined" && !recoverySupported && (
        <p className="change-recovery-hold" role="alert">
          변경 정보를 확인할 수 없어 복구를 실행할 수 없습니다. 상태를 새로고침한 뒤 다시 확인하세요.
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

    </section>
  );
}
