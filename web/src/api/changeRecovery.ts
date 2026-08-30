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
    inventoryConfirmedEmpty,
    targetIdentity,
  }: {
    transactionState: string;
    recoverySupported: boolean;
    inventoryConfirmedEmpty: boolean;
    targetIdentity: unknown;
  },
): boolean {
  return (
    transactionState === "quarantined" &&
    recoverySupported &&
    inventoryConfirmedEmpty &&
    journaledRecoveryRelaunchTarget(targetIdentity) !== null
  );
}
