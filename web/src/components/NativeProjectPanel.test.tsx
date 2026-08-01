import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { HostStatusResponse } from "../api/types";
import { NativeProjectPanel } from "./NativeProjectPanel";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const HOST_STATUS: HostStatusResponse = {
  schemaVersion: "1",
  host: { state: "ready" },
  agent: { state: "ready" },
  mcp: { state: "ready" },
  engineeringSystem: {
    state: "ready",
    vendor: "ls",
    application: "XG5000",
    processId: 120,
    sessionVerified: true,
  },
  controller: {
    state: "unavailable",
    vendor: "ls",
    protocol: "XGT_FENET",
    readOnly: true,
    strongEnoughForWrite: false,
  },
  workspace: {
    workspaceId: "workspace-native-1",
    displayName: "AssemblyLine01.xgwx",
    source: "local",
    vendor: "ls",
    artifactHash: "a".repeat(64),
  },
  capabilities: ["native_project_create"],
};

const INSTANCES = [
  { pid: 120, hwnd: 1001, title: "XG5000" },
  { pid: 121, hwnd: 1002, title: "XG5000 - 다른 창" },
];

const REVIEWED_PLAN = {
  conversationId: "planning-conversation",
  messageId: "assistant-plan",
  planDigest: "d".repeat(64),
  reviewedSectionIds: ["project-brief"],
};

const COMPLETED_OPERATION = {
  nativeProjectId: "native-project-1",
  operationId: "11111111-1111-4111-8111-111111111111",
  state: "completed",
  requestDigest: "r".repeat(64),
  actorId: "local-user",
  planDigest: "p".repeat(64),
  destinationDirectory:
    "C:\\ControlStudio\\workspaces\\AssemblyLine01",
  request: {
    operationId: "11111111-1111-4111-8111-111111111111",
    projectName: "AssemblyLine01",
    workspaceParent: "C:\\ControlStudio\\workspaces",
    plcSeries: "XGK",
    cpuModel: "XGK-CPUE",
    programName: "MAIN",
    language: "LD",
    description: "조립 1호기",
    xg5000Pid: 120,
    xg5000Hwnd: 1001,
    reviewedPlan: REVIEWED_PLAN,
  },
  result: {
    operation: "project.create_native",
    projectPath:
      "C:\\ControlStudio\\workspaces\\AssemblyLine01\\AssemblyLine01.xgwx",
    projectSha256: "a".repeat(64),
    projectSize: 4096,
    destinationDirectory:
      "C:\\ControlStudio\\workspaces\\AssemblyLine01",
    planDigest: "p".repeat(64),
    xg5000Pid: 120,
    xg5000Hwnd: 1001,
    identityVerified: true,
    identityEvidence: {},
    controllerConnected: false,
    plcWritePerformed: false,
    workspaceBound: false,
    workspaceBindingReason:
      "생성 원본과 보호 작업 복사본이 서로 다른 파일입니다.",
    workspaceRegistration: {
      state: "captured_not_bound",
      detail: "보호 복사본을 캡처했지만 활성 세션에 연결하지 않았습니다.",
    },
  },
  createdAt: "2026-07-29T01:00:00Z",
  updatedAt: "2026-07-29T01:00:05Z",
  completedAt: "2026-07-29T01:00:05Z",
};

const QUARANTINED_OPERATION = {
  ...COMPLETED_OPERATION,
  nativeProjectId: "11111111-1111-4111-8111-111111111111",
  state: "quarantined",
  result: {
    recovery: {
      expectedProjectPath:
        "C:\\ControlStudio\\workspaces\\AssemblyLine01\\AssemblyLine01.xgwx",
    },
  },
  errorCode: "HOST_RESTARTED_DURING_NATIVE_PROJECT_CREATION",
  errorDetail: "Host restarted after native UI execution began.",
};

const RECOVERED_OPERATION = {
  ...QUARANTINED_OPERATION,
  state: "completed",
  result: {
    recovery: {
      expectedProjectPath:
        "C:\\ControlStudio\\workspaces\\AssemblyLine01\\AssemblyLine01.xgwx",
    },
    projectPath:
      "C:\\ControlStudio\\workspaces\\AssemblyLine01\\AssemblyLine01.xgwx",
    projectSha256: "a".repeat(64),
    identityVerified: true,
    controllerConnected: false,
    plcWritePerformed: false,
  },
  errorCode: undefined,
  errorDetail: undefined,
};

describe("NativeProjectPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("binds the form to one exact XG5000 PID/HWND and shows verified offline result", async () => {
    const onStatusChanged = vi.fn();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ status: "ready", instances: INSTANCES }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ operation: COMPLETED_OPERATION, created: true }, 201),
      )
      .mockResolvedValueOnce(jsonResponse(HOST_STATUS));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <NativeProjectPanel
        onClose={vi.fn()}
        onStatusChanged={onStatusChanged}
        reviewedPlan={REVIEWED_PLAN}
      />,
    );

    expect(
      await screen.findByRole("option", {
        name: "XG5000 · PID 120 · HWND 1001",
      }),
    ).toBeInTheDocument();
    fireEvent.change(
      screen.getByLabelText("새 프로젝트를 만들 빈 XG5000 창"),
      { target: { value: "1001" } },
    );
    fireEvent.change(screen.getByLabelText("프로젝트 이름"), {
      target: { value: "AssemblyLine01" },
    });
    fireEvent.change(screen.getByLabelText(/^작업공간 상위 폴더/), {
      target: { value: "C:\\ControlStudio\\workspaces" },
    });
    fireEvent.change(screen.getByLabelText("CPU 모델"), {
      target: { value: "XGK-CPUE" },
    });
    fireEvent.change(screen.getByLabelText("설명 (선택)"), {
      target: { value: "조립 1호기" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "오프라인 프로젝트 만들기" }),
    );

    expect(
      await screen.findByRole("heading", { name: "프로젝트 생성 완료" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("XG5000 창과 생성 파일 일치 확인"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("연결 안 함 · 다운로드 안 함 · 쓰기 안 함"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "보호 복사본 캡처 완료 · 현재 XG5000 세션에는 자동 연결 안 함",
      ),
    ).toBeInTheDocument();
    expect(onStatusChanged).toHaveBeenCalledWith(HOST_STATUS);

    const post = fetchMock.mock.calls[1];
    expect(post?.[0]).toBe("/api/v1/native-projects");
    const requestBody = JSON.parse(String(post?.[1]?.body)) as Record<
      string,
      unknown
    >;
    expect(requestBody.operationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(new Headers(post?.[1]?.headers).get("Idempotency-Key")).toBe(
      requestBody.operationId,
    );
    expect(requestBody).toEqual({
      operationId: requestBody.operationId,
      projectName: "AssemblyLine01",
      workspaceParent: "C:\\ControlStudio\\workspaces",
      plcSeries: "XGK",
      cpuModel: "XGK-CPUE",
      programName: "MAIN",
      language: "LD",
      description: "조립 1호기",
      xg5000Pid: 120,
      xg5000Hwnd: 1001,
      reviewedPlan: REVIEWED_PLAN,
    });
  });

  it("fails closed until one PID/HWND is selected when multiple XG5000 windows exist", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ status: "ready", instances: INSTANCES }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <NativeProjectPanel
        onClose={vi.fn()}
        onStatusChanged={vi.fn()}
        reviewedPlan={REVIEWED_PLAN}
      />,
    );
    await screen.findByRole("option", {
      name: "XG5000 · PID 120 · HWND 1001",
    });
    fireEvent.change(screen.getByLabelText("프로젝트 이름"), {
      target: { value: "AssemblyLine01" },
    });
    fireEvent.change(screen.getByLabelText(/^작업공간 상위 폴더/), {
      target: { value: "C:\\ControlStudio\\workspaces" },
    });
    fireEvent.change(screen.getByLabelText("CPU 모델"), {
      target: { value: "XGK-CPUE" },
    });

    expect(
      screen.getByRole("button", { name: "오프라인 프로젝트 만들기" }),
    ).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not promise to launch XG5000 when no exact target exists", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ status: "ready", instances: [] }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <NativeProjectPanel
        onClose={vi.fn()}
        onStatusChanged={vi.fn()}
        reviewedPlan={REVIEWED_PLAN}
      />,
    );
    expect(
      await screen.findByText(
        "실행 중인 XG5000이 없습니다. XG5000을 직접 실행한 뒤 빈 창이 보이면 다시 확인해 주세요.",
      ),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("프로젝트 이름"), {
      target: { value: "AssemblyLine01" },
    });
    fireEvent.change(screen.getByLabelText(/^작업공간 상위 폴더/), {
      target: { value: "C:\\ControlStudio\\workspaces" },
    });
    fireEvent.change(screen.getByLabelText("CPU 모델"), {
      target: { value: "XGK-CPUE" },
    });

    expect(
      screen.getByRole("button", { name: "오프라인 프로젝트 만들기" }),
    ).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refreshes a non-terminal server-owned operation without re-posting it", async () => {
    const pending = {
      ...COMPLETED_OPERATION,
      state: "executing",
      result: undefined,
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ status: "ready", instances: [INSTANCES[0]] }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ operation: pending, created: true }, 202),
      )
      .mockResolvedValueOnce(jsonResponse(COMPLETED_OPERATION))
      .mockResolvedValueOnce(jsonResponse(HOST_STATUS));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <NativeProjectPanel
        onClose={vi.fn()}
        onStatusChanged={vi.fn()}
        reviewedPlan={REVIEWED_PLAN}
      />,
    );
    expect(
      await screen.findByText("XG5000 · PID 120 · HWND 1001"),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("프로젝트 이름"), {
      target: { value: "AssemblyLine01" },
    });
    fireEvent.change(screen.getByLabelText(/^작업공간 상위 폴더/), {
      target: { value: "C:\\ControlStudio\\workspaces" },
    });
    fireEvent.change(screen.getByLabelText("CPU 모델"), {
      target: { value: "XGK-CPUE" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "오프라인 프로젝트 만들기" }),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "생성 상태 다시 확인" }),
    );

    expect(
      await screen.findByRole("heading", { name: "프로젝트 생성 완료" }),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(
        ([input]) => String(input) === "/api/v1/native-projects",
      ),
    ).toHaveLength(1);
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "/api/v1/native-projects/native-project-1",
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
  });

  it("restores a quarantined operation and requires an explicit exact PID/HWND before read-only reconciliation", async () => {
    const onStatusChanged = vi.fn();
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/xg5000/instances") {
        return jsonResponse({ status: "ready", instances: INSTANCES });
      }
      if (
        path ===
          "/api/v1/native-projects/11111111-1111-4111-8111-111111111111" &&
        (!init?.method || init.method === "GET")
      ) {
        return jsonResponse(QUARANTINED_OPERATION);
      }
      if (
        path ===
        "/api/v1/native-projects/11111111-1111-4111-8111-111111111111/reconcile"
      ) {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
          pid: 120,
          hwnd: 1001,
          expectedProjectPath:
            "C:\\ControlStudio\\workspaces\\AssemblyLine01\\AssemblyLine01.xgwx",
        });
        return jsonResponse({
          operation: RECOVERED_OPERATION,
          reconciled: true,
        });
      }
      if (path === "/api/v1/status") {
        return jsonResponse(HOST_STATUS);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <NativeProjectPanel
        initialNativeProjectId="11111111-1111-4111-8111-111111111111"
        onClose={vi.fn()}
        onStatusChanged={onStatusChanged}
      />,
    );

    expect(
      await screen.findByRole("heading", {
        name: "새 프로젝트 생성 결과 복구",
      }),
    ).toBeInTheDocument();
    expect(
      await screen.findByDisplayValue(
        "C:\\ControlStudio\\workspaces\\AssemblyLine01\\AssemblyLine01.xgwx",
      ),
    ).toHaveAttribute("readonly");
    const reconcileButton = screen.getByRole("button", {
      name: "현재 상태 다시 검사",
    });
    expect(reconcileButton).toBeDisabled();
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/reconcile"),
      ),
    ).toHaveLength(0);

    fireEvent.change(
      screen.getByLabelText("현재 상태를 확인할 XG5000 창"),
      { target: { value: "1002" } },
    );
    expect(reconcileButton).toBeDisabled();
    expect(
      screen.getByText(/선택한 PID\/HWND가 저장된 생성 대상과 다릅니다/),
    ).toBeInTheDocument();

    fireEvent.change(
      screen.getByLabelText("현재 상태를 확인할 XG5000 창"),
      { target: { value: "1001" } },
    );
    expect(reconcileButton).toBeEnabled();
    fireEvent.click(reconcileButton);

    expect(
      await screen.findByRole("heading", { name: "프로젝트 생성 완료" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "현재 XG5000 창과 생성 파일을 다시 읽어 프로젝트 생성 완료를 확인했습니다. 생성 요청을 다시 실행하지 않았습니다.",
      ),
    ).toBeInTheDocument();
    expect(onStatusChanged).toHaveBeenCalledWith(HOST_STATUS);
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input) === "/api/v1/native-projects" &&
          init?.method === "POST",
      ),
    ).toHaveLength(0);
  });

  it.each([
    {
      state: "failed",
      heading: "프로젝트 미생성 확인",
      errorCode: "NATIVE_PROJECT_CONFIRMED_NOT_CREATED",
      errorDetail: "프로젝트가 생성되지 않은 것으로 확인되었습니다.",
    },
    {
      state: "quarantined",
      heading: "생성 결과 확인 필요",
      errorCode: "NATIVE_PROJECT_RECOVERY_OUTCOME_UNCERTAIN",
      errorDetail: "현재 증거만으로 결과를 확정할 수 없습니다.",
    },
  ])(
    "shows the reconciled $state state without retrying creation",
    async ({ state, heading, errorCode, errorDetail }) => {
      const resolved = {
        ...QUARANTINED_OPERATION,
        state,
        result: { recovery: { outcome: state } },
        errorCode,
        errorDetail,
      };
      const fetchMock = vi.fn<typeof fetch>(async (input) => {
        const path = String(input);
        if (path === "/api/v1/xg5000/instances") {
          return jsonResponse({ status: "ready", instances: [INSTANCES[0]] });
        }
        if (path.endsWith("/reconcile")) {
          return jsonResponse({
            operation: resolved,
            reconciled: state !== "quarantined",
          });
        }
        if (path === "/api/v1/status") {
          return jsonResponse(HOST_STATUS);
        }
        if (path.includes("/api/v1/native-projects/")) {
          return jsonResponse(QUARANTINED_OPERATION);
        }
        throw new Error(`unexpected request: ${path}`);
      });
      vi.stubGlobal("fetch", fetchMock);

      render(
        <NativeProjectPanel
          initialNativeProjectId="11111111-1111-4111-8111-111111111111"
          onClose={vi.fn()}
          onStatusChanged={vi.fn()}
        />,
      );
      const target = await screen.findByLabelText(
        "현재 상태를 확인할 XG5000 창",
      );
      expect(
        screen.getByRole("button", { name: "현재 상태 다시 검사" }),
      ).toBeDisabled();
      fireEvent.change(target, { target: { value: "1001" } });
      fireEvent.click(
        screen.getByRole("button", { name: "현재 상태 다시 검사" }),
      );

      expect(
        await screen.findByRole("heading", { name: heading }),
      ).toBeInTheDocument();
      expect(screen.getByText(errorDetail)).toBeInTheDocument();
      expect(
        fetchMock.mock.calls.filter(
          ([input, init]) =>
            String(input) === "/api/v1/native-projects" &&
            init?.method === "POST",
        ),
      ).toHaveLength(0);
    },
  );
});
