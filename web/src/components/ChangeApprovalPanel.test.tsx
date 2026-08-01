import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChangeTransaction } from "../api/types";
import { ChangeApprovalPanel } from "./ChangeApprovalPanel";

const COVERAGE_AREAS = [
  "target_identity",
  "value_semantics",
  "direct_impact",
  "indirect_impact",
  "modes",
  "runtime",
  "external_integrations",
  "safety",
  "validation",
  "rollback",
] as const;

const TRANSACTION: ChangeTransaction = {
  transactionId: "txn-123",
  operationId: "operation-123",
  state: "pending_approval",
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
  riskLevel: "medium",
  intent: "T120 대기시간 변경",
  plan: {
    changeMode: "offline_working_copy_revision",
    requestScope: "offline_candidate_revision",
    target: { program: "초기동작", network: "37", device: "T120" },
    current: { operand: "K30", duration: "3.0초" },
    proposed: { operand: "K15", duration: "1.5초" },
    impact: ["Y23 시작 시점이 최대 1.5초 빨라집니다."],
    risks: ["센서 안정화 시간이 부족할 수 있습니다."],
    assumptions: ["현재 작업 복사본이 검토 대상입니다."],
    blockingUnknowns: [],
    alternatives: ["타이머를 변경하지 않고 센서 응답을 먼저 측정합니다."],
    acceptanceCriteria: ["승인된 피연산자 외 구조 diff가 없어야 합니다."],
    observationSignals: ["T120, X14, Y23"],
    stopConditions: ["예상하지 않은 구조 diff가 발견되면 중단합니다."],
    coverage: COVERAGE_AREAS.map((area) => ({
      area,
      status: "confirmed",
      summary: `${area} 범위를 확인했습니다.`,
      evidenceRefs: [`evidence:${area}`],
    })),
    verificationScope: "offline_native_check_only",
    validation: [
      "structural_diff",
      "xg5000_program_check",
      "project_save",
    ],
    manualFollowUp: [
      "현장에서 센서 안정 시간과 실제 축 동작을 확인합니다.",
    ],
    rollback: ["검증 실패 시 백업 payload로 복구합니다."],
  },
  planDigest: "b".repeat(64),
  requestedByActorId: "local-user",
  approvalExpiresAt: "2026-07-29T10:00:00Z",
  result: {},
  createdAt: "2026-07-29T09:00:00Z",
  updatedAt: "2026-07-29T09:00:00Z",
};

describe("ChangeApprovalPanel", () => {
  it("shows the exact change contract and requires an explicit decision", () => {
    const approve = vi.fn();
    const reject = vi.fn();
    const openEvidence = vi.fn();

    render(
      <ChangeApprovalPanel
        transaction={TRANSACTION}
        busy={false}
        onApprove={approve}
        onExecuteApproved={vi.fn()}
        onReject={reject}
        onOpenEvidence={openEvidence}
        onRefresh={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    expect(screen.getByText("T120 대기시간 변경")).toBeInTheDocument();
    expect(screen.getByText("K30")).toBeInTheDocument();
    expect(screen.getByText("K15")).toBeInTheDocument();
    expect(screen.getByText(/센서 안정화 시간이 부족/)).toBeInTheDocument();
    expect(screen.getByText(TRANSACTION.planDigest)).toBeInTheDocument();
    expect(
      screen.getByText("ControlStudio가 자동으로 확인"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("XG5000 프로그램 검사"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("사람이 직접 확인할 현장·후속 작업"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/현장에서 센서 안정 시간과 실제 축 동작/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("offline_native_check_only"),
    ).toBeInTheDocument();
    expect(screen.getByText("10/10 영역")).toBeInTheDocument();
    expect(
      screen.getByText("승인된 피연산자 외 구조 diff가 없어야 합니다."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /사용자가 선택한 오프라인 후보 revision · 저장됨 · 자동 원복 없음/,
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getAllByRole("button", { name: /연결된 근거 1건 보기/ })[0],
    );

    fireEvent.click(screen.getByText("거절"));
    fireEvent.click(screen.getByText("이 변경 계약 승인"));

    expect(reject).toHaveBeenCalledOnce();
    expect(approve).toHaveBeenCalledOnce();
    expect(openEvidence).toHaveBeenCalledOnce();
  });

  it("resumes an already approved change without asking for a second decision", () => {
    const execute = vi.fn();
    const reject = vi.fn();

    render(
      <ChangeApprovalPanel
        transaction={{ ...TRANSACTION, state: "approved" }}
        busy={false}
        onApprove={vi.fn()}
        onExecuteApproved={execute}
        onReject={reject}
        onRefresh={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    expect(
      screen.getByText(/승인은 기록됐지만 아직 적용되지 않았습니다/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "거절" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /승인 취소/ }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", {
        name: "승인된 작업 복사본 변경 실행",
      }),
    );
    expect(execute).toHaveBeenCalledOnce();
    expect(reject).not.toHaveBeenCalled();
  });

  it("keeps the terminal receipt and manual follow-up visible until dismissed", () => {
    const dismiss = vi.fn();

    render(
      <ChangeApprovalPanel
        transaction={{
          ...TRANSACTION,
          state: "completed",
          result: {
            detail: "오프라인 검사와 저장이 완료되었습니다.",
          },
        }}
        busy={false}
        onApprove={vi.fn()}
        onExecuteApproved={vi.fn()}
        onReject={vi.fn()}
        onRefresh={vi.fn()}
        onDismiss={dismiss}
      />,
    );

    expect(
      screen.getByText(/현장에서 센서 안정 시간과 실제 축 동작/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "실제 실행 결과" }),
    ).toBeInTheDocument();
    const dismissButton = screen.getByRole("button", {
      name: "확인하고 대화로 돌아가기",
    });
    expect(dismissButton).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /자동 검증 범위를 확인했습니다/,
      }),
    );
    expect(dismissButton).toBeEnabled();
    fireEvent.click(dismissButton);
    expect(dismiss).toHaveBeenCalledOnce();
  });
});
