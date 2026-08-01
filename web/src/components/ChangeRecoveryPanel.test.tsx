import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChangeTransaction } from "../api/types";
import { ChangeRecoveryPanel } from "./ChangeRecoveryPanel";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const QUARANTINED: ChangeTransaction = {
  transactionId: "transaction-recovery-1",
  operationId: "operation-recovery-1",
  state: "quarantined",
  targetIdentity: {
    workspaceId: "workspace-1",
    revisionId: "revision-1",
    artifactHash: "a".repeat(64),
    sourceArtifactHash: "s".repeat(64),
    projectPath: "C:\\work\\machine.xgwx",
    vendor: "ls",
    engineeringSystem: "XG5000",
  },
  mutationScope: "offline_working_copy",
  riskLevel: "high",
  intent: "M100을 M101로 변경",
  plan: {
    target: { program: "MAIN", tokenOffset: 18 },
    current: { operand: "M100" },
    proposed: { operand: "M101" },
    impact: [],
    risks: ["변경 결과가 불명확합니다."],
    verificationScope: "offline_native_check_only",
    validation: [
      "structural_diff",
      "xg5000_program_check",
      "project_save",
    ],
    manualFollowUp: ["복구 뒤 실제 설비 상태를 현장에서 확인합니다."],
    rollback: ["원래 payload 복구"],
  },
  planDigest: "b".repeat(64),
  requestedByActorId: "local-user",
  approvalExpiresAt: "2026-07-29T10:00:00Z",
  result: {},
  errorCode: "native_change_outcome_uncertain",
  errorDetail: "Host 응답이 중단되어 실제 상태를 확인해야 합니다.",
  createdAt: "2026-07-29T09:00:00Z",
  updatedAt: "2026-07-29T09:05:00Z",
};

describe("ChangeRecoveryPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requires visible PID/HWND confirmation even when one XG5000 window exists", async () => {
    const reconcile = vi.fn(async () => undefined);
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({
        status: "ready",
        instances: [{ pid: 4321, hwnd: 8765, title: "XG5000 - machine" }],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ChangeRecoveryPanel
        transaction={QUARANTINED}
        busy={false}
        onReconcile={reconcile}
      />,
    );

    const option = await screen.findByRole("option", {
      name: "XG5000 - machine · PID 4321 · HWND 8765",
    });
    const submit = screen.getByRole("button", {
      name: "선택한 창으로 격리 복구 검사",
    });
    expect(submit).toBeDisabled();
    expect(
      screen.getByText(/제안한 변경을 다시 적용하지 않습니다/),
    ).toBeInTheDocument();

    fireEvent.change(
      screen.getByLabelText("복구 전용으로 확인할 XG5000 창"),
      { target: { value: option.getAttribute("value") } },
    );
    fireEvent.click(submit);

    expect(reconcile).toHaveBeenCalledWith(4321, 8765);
    expect(
      screen.queryByRole("button", { name: /변경 재적용|자동 재시도/ }),
    ).not.toBeInTheDocument();
  });

  it("shows terminal rollback evidence without loading or offering recovery again", () => {
    const recovered: ChangeTransaction = {
      ...QUARANTINED,
      state: "rolled_back",
      result: {
        reconciliation: {
          native_mutation_performed: true,
          final_status: {
            transaction_checkpoint: {
              state: "ROLLED_BACK_SAVED",
              terminal: true,
              current_project_hash: "c".repeat(64),
            },
          },
        },
      },
      errorCode: "explicit_recovery_rolled_back",
      errorDetail:
        "The quarantined native transaction was explicitly rolled back.",
      completedAt: "2026-07-29T09:10:00Z",
    };
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ChangeRecoveryPanel
        transaction={recovered}
        busy={false}
        onReconcile={vi.fn(async () => undefined)}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "격리 복구 증거" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("원래 상태로 원복하고 저장한 사실을 확인했습니다."),
    ).toBeInTheDocument();
    expect(screen.getByText(/ROLLED_BACK_SAVED/).closest("dd")).toHaveTextContent(
      "terminal 확인",
    );
    expect(screen.getByText("변경 흔적 확인 후 원복")).toBeInTheDocument();
    expect(screen.getByText(`${"c".repeat(16)}…`)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "선택한 창으로 격리 복구 검사",
      }),
    ).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps the recovery action explicit when evidence is still insufficient", async () => {
    const unresolved: ChangeTransaction = {
      ...QUARANTINED,
      result: {
        reconciliation: {
          lookup: {
            transaction_checkpoint: {
              state: "APPLYING",
              terminal: false,
            },
          },
        },
      },
      errorCode: "explicit_recovery_unproven",
      errorDetail: "Explicit rollback could not be proven.",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValueOnce(
        jsonResponse({ status: "unavailable", instances: [] }),
      ),
    );

    render(
      <ChangeRecoveryPanel
        transaction={unresolved}
        busy={false}
        onReconcile={vi.fn(async () => undefined)}
      />,
    );

    expect(
      await screen.findByText(/복구 대상을 확인할 수 없습니다/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/격리가 해제되지 않았습니다/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "선택한 창으로 격리 복구 검사",
      }),
    ).toBeDisabled();
  });
});
