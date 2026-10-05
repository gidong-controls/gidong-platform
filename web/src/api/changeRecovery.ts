import type { ChangeTransaction } from "./types";

export function executionTargetIdentity(transaction: ChangeTransaction) {
  return transaction.executionTargetIdentity ?? transaction.targetIdentity;
}

export interface RecoveryRelaunchTarget {
  pid: number;
  hwnd: number;
}

/**
 * The relaunch path may use only the transaction's journaled target identity.
 * It never selects a current process or invents a missing target.
 */
export function journaledRecoveryRelaunchTarget(
  targetIdentity: unknown,
): RecoveryRelaunchTarget | null {
  if (
    targetIdentity === null ||
    typeof targetIdentity !== "object" ||
    Array.isArray(targetIdentity)
  ) {
    return null;
  }
  const target = targetIdentity as Record<string, unknown>;
  const pid = target.engineeringProcessId;
  const hwnd = target.engineeringWindowHandle;
  if (
    typeof pid !== "number" ||
    typeof hwnd !== "number" ||
    !Number.isSafeInteger(pid) ||
    !Number.isSafeInteger(hwnd) ||
    pid <= 0 ||
    hwnd <= 0
  ) {
    return null;
  }
  return { pid, hwnd };
}

export function canOfferRecoveryRelaunch(
  {
    transactionState,
    recoverySupported,
    inventoryConfirmed,
    instances,
    targetIdentity,
  }: {
    transactionState: string;
    recoverySupported: boolean;
    inventoryConfirmed: boolean;
    instances: ReadonlyArray<{ pid: number; hwnd: number }>;
    targetIdentity: unknown;
  },
): boolean {
  const target = journaledRecoveryRelaunchTarget(targetIdentity);
  return (
    transactionState === "quarantined" &&
    recoverySupported &&
    inventoryConfirmed &&
    target !== null &&
    !instances.some((instance) => instance.pid === target.pid && instance.hwnd === target.hwnd)
  );
}
