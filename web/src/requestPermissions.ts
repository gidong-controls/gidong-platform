import type { EngineeringChangeRequestScope } from "./api/types";

export type ConversationPermission = EngineeringChangeRequestScope | "review_before_apply";

// The default permits proposals, never execution. Unbound conversations and
// unavailable targets retain the existing read-only Host contract.
export function resolveRequestScope(
  permission: ConversationPermission,
  changeReady: boolean,
  targetless: boolean,
): EngineeringChangeRequestScope {
  if (targetless) return "analysis_only";
  if (permission === "review_before_apply") {
    return changeReady ? "offline_candidate_revision" : "analysis_only";
  }
  return permission;
}
