import type { Conversation, ChangeTransaction } from "./api/types";

export function isCurrentConversationQuarantineDiagnostic(
  conversation: Conversation | null,
  transaction: ChangeTransaction | null,
): boolean {
  const mission = conversation?.engineeringMission;
  const transactionId = mission?.changeTransactionId;

  return Boolean(
    conversation?.workflow === "engineering" &&
      mission &&
      mission.conversationId === conversation.conversationId &&
      transactionId &&
      transaction?.transactionId === transactionId &&
      transaction.state === "quarantined" &&
      (!transaction.conversationId ||
        transaction.conversationId === conversation.conversationId) &&
      transaction.targetIdentity.workspaceId === mission.workspaceId &&
      transaction.targetIdentity.revisionId === mission.revisionId,
  );
}
