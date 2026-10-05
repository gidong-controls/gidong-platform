import type { EngineeringChangeRequestScope } from "./api/types";

export type ConversationPermission = EngineeringChangeRequestScope | "review_before_apply";

// Intent does not change when the editor needs reconnection. The caller prepares
// the working-copy session before submitting a proposal-capable request.
export function resolveRequestScope(
  permission: ConversationPermission,
  _changeReady: boolean,
  targetless: boolean,
): EngineeringChangeRequestScope {
  if (targetless) return "analysis_only";
  if (permission === "review_before_apply") {
    return "offline_candidate_revision";
  }
  return permission;
}
