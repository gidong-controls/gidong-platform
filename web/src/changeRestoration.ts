import type { ChangeTransaction } from "./api/types";

export const TERMINAL_CHANGE_OUTCOME_STATES = new Set<ChangeTransaction["state"]>([
  "completed",
  "rolled_back",
  "failed",
  "rejected",
  "expired",
]);

export function selectRestorableChange(
  transactions: readonly ChangeTransaction[],
  isEligible: (transaction: ChangeTransaction) => boolean,
): ChangeTransaction | null {
  // Host returns creation order including acknowledged receipts. Reviewing the
  // latest attempt must not resurrect older terminal outcomes from history.
  // Unresolved execution and approval authority still remain selectable.
  return transactions.find((transaction, index) =>
    isEligible(transaction) &&
    (index === 0 || !TERMINAL_CHANGE_OUTCOME_STATES.has(transaction.state)),
  ) ?? null;
}
