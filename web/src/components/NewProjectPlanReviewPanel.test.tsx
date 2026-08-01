import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { NewProjectEngineeringPlan } from "../api/types";
import { NewProjectPlanReviewPanel } from "./NewProjectPlanReviewPanel";

const PLAN_DIGEST = "sha256:plan-v3";

const READY_PLAN: NewProjectEngineeringPlan = {
  schemaVersion: "1",
  projectName: "인덱스 조립기",
  status: "ready_for_review",
  summary:
    "작업자가 검토한 공정 요구사항을 기준으로 제어 구조와 검증 범위를 정리했습니다.",
  objective: "제품을 감지하고 안전 조건 안에서 인덱스를 1사이클 이동한다.",
  sections: [
    {
      sectionId: "sequence",
      title: "공정 순서",
      items: [
        {
          itemId: "sequence-1",
          text: "제품 감지 후 클램프를 닫고 인덱스를 이동한다.",
          status: "confirmed",
          source: "user",
          blocking: false,
          verificationMethod: "사용자 공정 설명과 I/O 목록 대조",
        },
      ],
    },
    {
      sectionId: "safety",
      title: "안전·인터록",
      items: [
        {
          itemId: "safety-1",
          text: "도어가 열리면 자동 운전 출력을 차단한다.",
          status: "proposed",
          source: "agent",
          blocking: false,
          verificationMethod: "전기도면 및 현장 안전 담당자 검토",
        },
      ],
    },
  ],
  openDecisions: [],
  completeness: {
    totalItems: 2,
    confirmed: 1,
    proposed: 1,
    unknown: 0,
    conflicting: 0,
    notApplicable: 0,
    blocking: 0,
  },
};

const BLOCKED_PLAN: NewProjectEngineeringPlan = {
  ...READY_PLAN,
  status: "needs_clarification",
  openDecisions: [
    {
      decisionId: "decision-safe-position",
      question: "공압 차단 시 실린더의 안전 위치를 확정해야 합니다.",
      status: "unknown",
      source: "agent",
      blocking: true,
      verificationMethod: "공압 도면과 실제 무전원 동작 확인",
    },
  ],
  completeness: {
    ...READY_PLAN.completeness,
    totalItems: 3,
    unknown: 1,
    blocking: 1,
  },
};

describe("NewProjectPlanReviewPanel", () => {
  it("presents readiness, coverage, and a navigable plan without exposing the digest by default", () => {
    render(
      <NewProjectPlanReviewPanel
        artifact={READY_PLAN}
        planDigest={PLAN_DIGEST}
        busy={false}
        onReview={vi.fn()}
        onRequestRevision={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("region", { name: "인덱스 조립기" }),
    ).toBeInTheDocument();
    expect(screen.getByText("검토 준비됨")).toBeInTheDocument();
    expect(screen.getByText("100% 작성")).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", { name: "계획 작성 범위" }),
    ).toHaveValue(100);

    const navigation = screen.getByRole("navigation", { name: "계획 섹션" });
    const sequenceButton = within(navigation).getByRole("button", {
      name: /공정 순서/,
    });
    const safetyButton = within(navigation).getByRole("button", {
      name: /안전·인터록/,
    });
    expect(sequenceButton).toHaveAttribute("aria-current", "true");
    expect(
      screen.getByText("제품 감지 후 클램프를 닫고 인덱스를 이동한다."),
    ).toBeInTheDocument();

    fireEvent.click(safetyButton);
    expect(safetyButton).toHaveAttribute("aria-current", "true");
    expect(
      screen.getByText("도어가 열리면 자동 운전 출력을 차단한다."),
    ).toBeInTheDocument();
    expect(screen.getByText("에이전트 작성")).toBeInTheDocument();

    expect(screen.getByText(PLAN_DIGEST)).not.toBeVisible();
    fireEvent.click(screen.getByText("계획 식별 정보"));
    expect(screen.getByText(PLAN_DIGEST)).toBeVisible();
  });

  it("blocks review until every blocking decision is resolved", () => {
    const onReview = vi.fn();
    const onRequestRevision = vi.fn();
    render(
      <NewProjectPlanReviewPanel
        artifact={BLOCKED_PLAN}
        planDigest={PLAN_DIGEST}
        busy={false}
        onReview={onReview}
        onRequestRevision={onRequestRevision}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "열린 결정" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("검토 차단 1건");
    expect(
      screen.getByText(
        "공압 차단 시 실린더의 안전 위치를 확정해야 합니다.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("확인 필요")).toBeInTheDocument();

    const reviewButton = screen.getByRole("button", {
      name: "이 계획 검토 완료",
    });
    expect(reviewButton).toBeDisabled();
    expect(reviewButton).toHaveAccessibleDescription(
      "미확인 사항을 먼저 해결해야 합니다.",
    );
    fireEvent.click(reviewButton);
    expect(onReview).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "수정 요청" }));
    expect(onRequestRevision).toHaveBeenCalledTimes(1);
  });

  it("binds review completion to the exact plan digest", () => {
    const onReview = vi.fn();
    render(
      <NewProjectPlanReviewPanel
        artifact={READY_PLAN}
        planDigest={PLAN_DIGEST}
        busy={false}
        onReview={onReview}
        onRequestRevision={vi.fn()}
      />,
    );

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "이 섹션의 항목·상태·확인 방법을 검토했습니다",
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: /안전·인터록/ }),
    );
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "이 섹션의 항목·상태·확인 방법을 검토했습니다",
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "이 계획 검토 완료" }),
    );
    expect(onReview).toHaveBeenCalledWith(PLAN_DIGEST, [
      "sequence",
      "safety",
    ]);
  });

  it("shows an immutable reviewed state and removes the review action", () => {
    render(
      <NewProjectPlanReviewPanel
        artifact={READY_PLAN}
        planDigest={PLAN_DIGEST}
        review={{
          messageId: "message-plan-v3",
          planDigest: PLAN_DIGEST,
          reviewedSectionIds: ["sequence", "safety"],
          actorId: "engineer-01",
          reviewedAt: "2026-07-30T01:20:00Z",
        }}
        busy={false}
        onReview={vi.fn()}
        onRequestRevision={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("계획 검토 완료");
    expect(screen.getByRole("status")).toHaveTextContent("engineer-01");
    expect(
      screen.queryByRole("button", { name: "이 계획 검토 완료" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "수정 요청" }),
    ).toBeEnabled();
  });

  it("invalidates a review when the plan digest changes", () => {
    render(
      <NewProjectPlanReviewPanel
        artifact={READY_PLAN}
        planDigest={PLAN_DIGEST}
        review={{
          messageId: "message-plan-v2",
          planDigest: "sha256:older-plan",
          reviewedSectionIds: ["sequence", "safety"],
          actorId: "engineer-01",
          reviewedAt: "2026-07-29T01:20:00Z",
        }}
        busy={false}
        onReview={vi.fn()}
        onRequestRevision={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "계획이 변경되어 이전 검토 완료 기록은 더 이상 유효하지 않습니다.",
    );
    expect(
      screen.getByRole("button", { name: "이 계획 검토 완료" }),
    ).toBeDisabled();
  });
});
