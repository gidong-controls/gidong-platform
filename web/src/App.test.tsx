import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const READY_STATUS = {
  schemaVersion: "1",
  host: { state: "ready", detail: "로컬 사용자 세션 Host" },
  agent: {
    state: "ready",
    detail: "읽기·분석 전용 에이전트가 준비되었습니다.",
    modelDisplayName: "engineering-model",
  },
  mcp: {
    state: "ready",
    serverId: "xg5000-mcp-server",
    toolCount: 30,
    agentToolCount: 9,
  },
  engineeringSystem: {
    state: "unavailable",
    vendor: "ls",
    application: "XG5000",
    sessionVerified: false,
    detail: "XG5000 실행 인스턴스를 선택하기 전입니다.",
  },
  controller: {
    state: "unavailable",
    vendor: "ls",
    protocol: "XGT_FENET",
    readOnly: true,
    strongEnoughForWrite: false,
    detail: "온라인 LS PLC endpoint가 선택되지 않았습니다.",
  },
  capabilities: ["read", "analyze"],
};

function bootstrap(status = READY_STATUS) {
  return {
    schemaVersion: "1",
    status,
    csrfToken: "browser-csrf",
    clientSessionId: "client-1",
    lastEventSequence: 0,
  };
}

function conversation(messages: unknown[] = []) {
  return {
    conversationId: "conversation-1",
    title: "PLC 분석 대화",
    workflow: "engineering",
    messages,
    updatedAt: "2026-07-28T10:00:00Z",
  };
}

function terminalChangeReceipt(receiptAcknowledgedAt: string | null = null) {
  return {
    transactionId: "change-terminal-1",
    operationId: "operation-terminal-1",
    conversationId: "conversation-1",
    state: "completed",
    targetIdentity: {
      workspaceId: "workspace-1",
      revisionId: "revision-1",
      artifactHash: "a".repeat(64),
      projectPath: "C:\\projects\\machine.xgwx",
      engineeringSessionId: "session-1",
      xg5000Pid: 4321,
    },
    mutationScope: "workspace:workspace-1",
    riskLevel: "medium",
    intent: "M100을 M101로 변경",
    plan: {
      target: { program: "MAIN", device: "M100" },
      current: { operand: "M100" },
      proposed: { operand: "M101" },
      impact: ["Y100 기동 조건"],
      risks: ["시퀀스 타이밍 변경"],
      verificationScope: "offline_native_check_only",
      validation: [
        "structural_diff",
        "xg5000_program_check",
        "project_save",
      ],
      manualFollowUp: ["실제 설비 동작은 현장에서 확인"],
      rollback: ["변경 전 payload 복원"],
    },
    planDigest: "b".repeat(64),
    requestedByActorId: "browser:reviewer",
    approvalExpiresAt: "2099-01-01T00:00:00Z",
    result: {
      structuralDiffVerified: true,
      programCheckPassed: true,
      projectSaved: true,
    },
    createdAt: "2026-07-30T00:00:00Z",
    updatedAt: "2026-07-30T00:01:00Z",
    completedAt: "2026-07-30T00:01:00Z",
    receiptAcknowledgedAt,
    receiptAcknowledgedBy: receiptAcknowledgedAt
      ? "browser:reviewer"
      : null,
  };
}

function authenticatedFetch(
  handler: (
    input: RequestInfo | URL,
    init?: RequestInit,
  ) => Promise<Response> | Response,
  recentChanges: unknown[] = [],
  recentConversations: unknown[] = [],
) {
  return vi.fn<typeof fetch>(async (input, init) => {
    const path = String(input);
    if (path.includes("/api/v1/events?")) {
      expect(new Headers(init?.headers).get("Authorization")).toMatch(
        /^Bearer /,
      );
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          init?.signal?.addEventListener(
            "abort",
            () =>
              controller.error(
                new DOMException("The operation was aborted.", "AbortError"),
              ),
            { once: true },
          );
        },
      });
      return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }
    if (path.includes("/api/v1/change-transactions?")) {
      return jsonResponse(recentChanges);
    }
    if (path.includes("/api/v1/conversations?")) {
      return jsonResponse({
        items: recentConversations,
        total: recentConversations.length,
        limit: 100,
        offset: 0,
      });
    }
    return handler(input, init);
  });
}

function installMemoryStorage(
  property: "localStorage" | "sessionStorage",
): void {
  const values = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
  Object.defineProperty(window, property, {
    configurable: true,
    value: storage,
  });
}

describe("ControlStudio web product shell", () => {
  beforeEach(() => {
    installMemoryStorage("localStorage");
    installMemoryStorage("sessionStorage");
    window.sessionStorage.setItem(
      "controlstudio.browserSession",
      JSON.stringify({
        sessionToken: "test-browser-session",
        expiresAt: "2099-01-01T00:00:00Z",
      }),
    );
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps unsupported product entry paths explicit when Host is offline", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Host unavailable")),
    );

    render(<App />);

    expect(
      screen.getByRole("heading", { name: "어디서 시작할까요?" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "새 프로젝트" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "기존 로컬 프로젝트" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("PLC에서 로컬 작업공간 만들기"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("온라인 LS PLC 읽기 전용 관찰"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "에이전트 작업 공간" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("증상이나 확인할 동작을 설명해 주세요"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "읽기 전용 연결" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "아직 사용할 수 없음" }),
    ).toHaveLength(2);

    expect(
      await screen.findByText(
        "Host에 연결할 수 없습니다. ControlStudio를 다시 시작해 주세요.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("시스템 상세")).toBeInTheDocument();
    expect(
      screen.getByRole("region", {
        name: "Host, 에이전트, MCP와 XG5000 상세 상태",
      }),
    ).toBeInTheDocument();
  });

  it("restores a terminal change receipt until the user durably acknowledges it", async () => {
    const terminal = terminalChangeReceipt();
    const acknowledged = {
      ...terminal,
      receiptAcknowledgedAt: "2026-07-30T00:02:00Z",
      receiptAcknowledgedBy: "browser:reviewer",
    };
    const fetchMock = authenticatedFetch(
      async (input, init) => {
        const path = String(input);
        if (path === "/api/v1/bootstrap") {
          return jsonResponse(bootstrap());
        }
        if (path === "/api/v1/conversations") {
          return jsonResponse(conversation(), 201);
        }
        if (
          path ===
          "/api/v1/change-transactions/change-terminal-1/receipt-acknowledgement"
        ) {
          expect(init?.method).toBe("POST");
          expect(
            new Headers(init?.headers).get("X-ControlStudio-CSRF"),
          ).toBe("browser-csrf");
          return jsonResponse({
            transaction: acknowledged,
            acknowledgementRecorded: true,
          });
        }
        throw new Error(`unexpected request: ${path}`);
      },
      [terminal],
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "M100을 M101로 변경" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /자동 검증 범위를 확인했습니다/,
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "확인하고 대화로 돌아가기" }),
    );

    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: "M100을 M101로 변경" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      fetchMock.mock.calls.filter(
        ([input]) =>
          String(input) ===
          "/api/v1/change-transactions/change-terminal-1/receipt-acknowledgement",
      ),
    ).toHaveLength(1);
  });

  it("does not reopen an already acknowledged terminal change receipt", async () => {
    const fetchMock = authenticatedFetch(
      async (input) => {
        const path = String(input);
        if (path === "/api/v1/bootstrap") {
          return jsonResponse(bootstrap());
        }
        if (path === "/api/v1/conversations") {
          return jsonResponse(conversation(), 201);
        }
        throw new Error(`unexpected request: ${path}`);
      },
      [terminalChangeReceipt("2026-07-30T00:02:00Z")],
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await screen.findByRole("heading", { name: "어디서 시작할까요?" });
    expect(
      screen.queryByRole("heading", { name: "M100을 M101로 변경" }),
    ).not.toBeInTheDocument();
  });

  it("starts a planning-only conversation before exposing XG5000 technical setup", async () => {
    const nativeStatus = {
      ...READY_STATUS,
      capabilities: [
        ...READY_STATUS.capabilities,
        "native_project_create",
        "online_observe",
      ],
    };
    let conversationCreates = 0;
    const planningMessages: unknown[] = [];
    let serverBrief = {
      projectName: "포장 설비",
      machinePurpose: "제품을 검사하고 포장한다",
      sequence: "",
      hardwareAndIo: "",
      safetyAndInterlocks: "도어 개방 시 축을 정지한다",
      operatingModes: "",
      performanceAndAcceptance: "",
    };
    const reviewedPlanDigest = "d".repeat(64);
    const reviewedPlanArtifact = {
      schemaVersion: "1",
      projectName: "포장 설비",
      status: "ready_for_review",
      summary: "요구사항과 안전 조건을 구조화한 설계 계획",
      objective: "제품을 안전하게 검사하고 포장한다.",
      sections: [
        {
          sectionId: "project-brief",
          title: "확인된 프로젝트 브리프",
          items: [
            {
              itemId: "item-purpose",
              text: "제품을 검사하고 포장한다.",
              status: "confirmed",
              source: "user",
              blocking: false,
              verificationMethod: "사용자 브리프 대조",
            },
          ],
        },
      ],
      openDecisions: [],
      completeness: {
        totalItems: 1,
        confirmed: 1,
        proposed: 0,
        unknown: 0,
        conflicting: 0,
        notApplicable: 0,
        blocking: 0,
      },
    };
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(nativeStatus));
      }
      if (path === "/api/v1/conversations" && init?.method === "POST") {
        conversationCreates += 1;
        if (conversationCreates === 2) {
          expect(JSON.parse(String(init.body))).toEqual({
            title: "포장 설비 · 신규 설계",
            workflow: "new_project_planning",
            projectBrief: {
              projectName: "포장 설비",
              machinePurpose: "제품을 검사하고 포장한다",
              sequence: "",
              hardwareAndIo: "",
              safetyAndInterlocks: "도어 개방 시 축을 정지한다",
              operatingModes: "",
              performanceAndAcceptance: "",
            },
          });
        }
        return jsonResponse(
          conversationCreates === 1
            ? conversation()
            : {
                ...conversation(),
                conversationId: "planning-conversation",
                title: "포장 설비 · 신규 설계",
                workflow: "new_project_planning",
                projectBrief: serverBrief,
              },
          201,
        );
      }
      if (
        path ===
          "/api/v1/conversations/planning-conversation/messages" &&
        init?.method === "POST"
      ) {
        const request = JSON.parse(String(init.body));
        planningMessages.push(request);
        serverBrief = request.projectBrief;
        return jsonResponse({
          runId: "planning-run",
          operationId: request.operationId,
          state: "completed",
        });
      }
      if (
        path === "/api/v1/conversations/planning-conversation" &&
        (!init?.method || init.method === "GET")
      ) {
        return jsonResponse({
          conversationId: "planning-conversation",
          title: "포장 설비 · 신규 설계",
          workflow: "new_project_planning",
          projectBrief: serverBrief,
          updatedAt: "2026-07-30T00:00:00Z",
          messages: [
            {
              messageId: "user-plan",
              role: "user",
              status: "complete",
              createdAt: "2026-07-30T00:00:00Z",
              workflow: "new_project_planning",
              projectName: "포장 설비",
              content: "프로젝트 이름: 포장 설비\n설비 목적: 제품을 검사하고 포장한다",
            },
            {
              messageId: "assistant-plan",
              role: "assistant",
              status: "complete",
              createdAt: "2026-07-30T00:00:01Z",
              workflow: "new_project_planning",
              planStatus: "ready_for_review",
              projectName: "포장 설비",
              planArtifact: reviewedPlanArtifact,
              planDigest: reviewedPlanDigest,
              planReview: {
                messageId: "assistant-plan",
                planDigest: reviewedPlanDigest,
                reviewedSectionIds: ["project-brief"],
                actorId: "browser:reviewer",
                reviewedAt: "2026-07-30T00:00:02Z",
              },
              content: "## 프로젝트 계획\n\n안전 조건과 검증 계획을 포함했습니다.",
            },
          ],
        });
      }
      if (path === "/api/v1/xg5000/instances") {
        return jsonResponse({ status: "ready", instances: [] });
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    expect(
      await screen.findByText("AI 최대 허용 9개 · MCP 등록 30개"),
    ).toBeInTheDocument();
    const alternativeEntry = await screen.findByText("다른 연결 방법");
    fireEvent.click(alternativeEntry);
    expect(
      screen.getByRole("button", { name: "읽기 전용 연결" }),
    ).toBeEnabled();
    fireEvent.click(
      screen.getByRole("button", { name: "요구사항부터 시작" }),
    );

    expect(
      await screen.findByRole("heading", {
        name: "어떤 설비를 만들까요?",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "에이전트 작업 공간" }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("프로젝트 이름"), {
      target: { value: "포장 설비" },
    });
    fireEvent.change(
      screen.getByLabelText(/이 설비가 무엇을 해야 하나요\?/),
      {
        target: { value: "제품을 검사하고 포장한다" },
      },
    );
    fireEvent.change(screen.getByLabelText("안전·인터록·운영 제약"), {
      target: { value: "도어 개방 시 축을 정지한다" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "계획서 초안 만들기" }),
    );

    expect(
      await screen.findByRole("region", { name: "에이전트 작업 공간" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "어디서 시작할까요?" }),
    ).not.toBeInTheDocument();
    expect(planningMessages).toHaveLength(1);
    expect(planningMessages[0]).toMatchObject({
      workflow: "new_project_planning",
    });
    expect(screen.getByText("계획 검토 가능")).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "기술 구성으로",
      }),
    ).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "요구사항 수정" }));
    fireEvent.change(screen.getByLabelText("안전·인터록·운영 제약"), {
      target: { value: "도어 개방 시 모든 출력을 정지한다" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "계획서 초안 만들기" }),
    );
    await waitFor(() => expect(planningMessages).toHaveLength(2));
    expect(conversationCreates).toBe(2);
    expect(planningMessages[1]).toMatchObject({
      workflow: "new_project_planning",
      projectBrief: {
        projectName: "포장 설비",
        safetyAndInterlocks: "도어 개방 시 모든 출력을 정지한다",
      },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "기술 구성으로",
      }),
    );
    expect(
      await screen.findByRole("heading", {
        name: "XG5000 프로젝트 골격 구성",
      }),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("포장 설비")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "에이전트 작업 공간" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "실행 중인 XG5000이 없습니다. XG5000을 직접 실행한 뒤 빈 창이 보이면 다시 확인해 주세요.",
      ),
    ).toBeInTheDocument();
  });

  it("restores a server-owned new-project planning scene after reload", async () => {
    window.localStorage.setItem(
      "controlstudio.activeConversationId",
      "restored-planning",
    );
    const planningStatus = {
      ...READY_STATUS,
      capabilities: [
        ...READY_STATUS.capabilities,
        "native_project_create",
      ],
      mcp: {
        ...READY_STATUS.mcp,
        state: "unavailable",
        detail: "MCP를 시작하지 않음",
      },
      agent: {
        ...READY_STATUS.agent,
        state: "degraded",
        planningReady: true,
        engineeringReady: false,
      },
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        readReady: true,
        sessionVerified: true,
      },
      workspace: {
        workspaceId: "older-workspace",
        displayName: "older-machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };
    const restoredConversation = {
      conversationId: "restored-planning",
      title: "검사기 · 신규 설계",
      workflow: "new_project_planning",
      projectBrief: {
        projectName: "검사기",
        machinePurpose: "카메라 검사 후 합격품을 배출한다",
        sequence: "감지 → 검사 → 배출",
        hardwareAndIo: "",
        safetyAndInterlocks: "도어 개방 시 정지",
      },
      updatedAt: "2026-07-30T00:00:02Z",
      messages: [
        {
          messageId: "restored-user",
          role: "user",
          status: "complete",
          createdAt: "2026-07-30T00:00:00Z",
          workflow: "new_project_planning",
          projectName: "검사기",
          content:
            "프로젝트 이름: 검사기\n설비 목적: 과거 요구사항\n알고 있는 동작 순서·공정: 과거 순서\n알고 있는 하드웨어·I/O·통신: 아직 모름\n안전·인터록·운영 제약: 과거 제약",
        },
        {
          messageId: "restored-old-plan",
          role: "assistant",
          status: "complete",
          createdAt: "2026-07-30T00:00:01Z",
          workflow: "new_project_planning",
          planStatus: "ready_for_review",
          projectName: "검사기",
          content: "## 이전 계획\n\n검토 가능",
        },
        {
          messageId: "restored-assistant",
          role: "assistant",
          status: "complete",
          createdAt: "2026-07-30T00:00:02Z",
          workflow: "new_project_planning",
          planStatus: "needs_clarification",
          projectName: "검사기",
          content: "## 확인할 내용\n\n카메라 통신 방식을 알려 주세요.",
        },
      ],
    };
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(planningStatus));
      }
      if (path === "/api/v1/conversations/restored-planning") {
        return jsonResponse(restoredConversation);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("region", { name: "에이전트 작업 공간" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "어디서 시작할까요?" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "검사기" }),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(
        "계획을 보완하거나 에이전트의 질문에 답해 주세요",
      ),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", {
        name: "기술 구성으로",
      }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "요구사항 수정" }));
    expect(
      await screen.findByDisplayValue(
        "카메라 검사 후 합격품을 배출한다",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "에이전트 작업 공간" }),
    ).not.toBeInTheDocument();
  });

  it("does not reuse an older ready plan after a newer planning turn fails", async () => {
    window.localStorage.setItem(
      "controlstudio.activeConversationId",
      "failed-followup-planning",
    );
    const failedConversation = {
      conversationId: "failed-followup-planning",
      title: "검사기 · 신규 설계",
      workflow: "new_project_planning",
      projectBrief: {
        projectName: "검사기",
        machinePurpose: "제품을 검사하고 분류한다",
        sequence: "",
        hardwareAndIo: "",
        safetyAndInterlocks: "도어 개방 시 정지",
      },
      updatedAt: "2026-07-30T00:00:03Z",
      messages: [
        {
          messageId: "ready-plan",
          role: "assistant",
          status: "complete",
          createdAt: "2026-07-30T00:00:01Z",
          workflow: "new_project_planning",
          planStatus: "ready_for_review",
          content: "## 이전 계획\n\n검토 가능",
        },
        {
          messageId: "followup-request",
          role: "user",
          status: "complete",
          createdAt: "2026-07-30T00:00:02Z",
          workflow: "new_project_planning",
          content: "안전 조건을 다시 검토해 주세요.",
        },
        {
          messageId: "failed-plan",
          role: "assistant",
          status: "failed",
          createdAt: "2026-07-30T00:00:03Z",
          content: "모델 응답을 완료하지 못했습니다.",
        },
      ],
    };
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap());
      }
      if (path === "/api/v1/conversations/failed-followup-planning") {
        return jsonResponse(failedConversation);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "검사기" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "기술 구성으로",
      }),
    ).toBeDisabled();
    expect(screen.queryByText("계획 검토 가능")).not.toBeInTheDocument();
  });

  it("reconciles a persisted brief update when only the HTTP response is lost", async () => {
    window.localStorage.setItem(
      "controlstudio.activeConversationId",
      "brief-update-planning",
    );
    let serverBrief = {
      projectName: "포장기",
      machinePurpose: "제품을 포장한다",
      sequence: "",
      hardwareAndIo: "",
      safetyAndInterlocks: "비상정지 입력 사용",
    };
    const messages: Record<string, unknown>[] = [
      {
        messageId: "initial-plan",
        role: "assistant",
        status: "complete",
        createdAt: "2026-07-30T00:00:01Z",
        workflow: "new_project_planning",
        planStatus: "ready_for_review",
        content: "## 초기 계획",
      },
    ];
    const detail = () => ({
      conversationId: "brief-update-planning",
      title: "포장기 · 신규 설계",
      workflow: "new_project_planning",
      projectBrief: serverBrief,
      updatedAt: "2026-07-30T00:00:02Z",
      messages,
    });
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap());
      }
      if (
        path === "/api/v1/conversations/brief-update-planning" &&
        (!init?.method || init.method === "GET")
      ) {
        return jsonResponse(detail());
      }
      if (
        path === "/api/v1/conversations/brief-update-planning/messages" &&
        init?.method === "POST"
      ) {
        const request = JSON.parse(String(init.body));
        serverBrief = request.projectBrief;
        messages.push({
          messageId: "persisted-update",
          role: "user",
          status: "complete",
          createdAt: "2026-07-30T00:00:02Z",
          workflow: "new_project_planning",
          operationId: request.operationId,
          content:
            "ControlStudio 서버가 저장한 구조화 브리프를 기준으로 계획을 갱신합니다.",
        });
        throw new TypeError("response lost after commit");
      }
      if (path === "/api/v1/status") {
        return jsonResponse(READY_STATUS);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "포장기" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "요구사항 수정" }));
    fireEvent.change(screen.getByLabelText("안전·인터록·운영 제약"), {
      target: { value: "도어 개방 시 위험 출력을 차단한다" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "계획서 초안 만들기" }),
    );

    expect(
      await screen.findByText(
        "응답 연결은 끊겼지만 서버가 계획 요청을 보관했습니다. 실행 상태를 다시 확인해 주세요.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "기술 구성으로",
      }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "요구사항 수정" }));
    expect(
      await screen.findByDisplayValue(
        "도어 개방 시 위험 출력을 차단한다",
      ),
    ).toBeInTheDocument();
  });

  it("prioritizes an active operation conversation and blocks duplicate planning actions", async () => {
    window.localStorage.setItem(
      "controlstudio.activeConversationId",
      "stale-conversation",
    );
    const activeStatus = {
      ...READY_STATUS,
      capabilities: [
        ...READY_STATUS.capabilities,
        "native_project_create",
      ],
      activeOperation: {
        runId: "active-planning-run",
        conversationId: "operation-planning",
        state: "analyzing",
        label: "신규 프로젝트 계획",
      },
    };
    const operationConversation = {
      conversationId: "operation-planning",
      title: "조립기 · 신규 설계",
      workflow: "new_project_planning",
      projectBrief: {
        projectName: "조립기",
        machinePurpose: "부품을 조립하고 검사한다",
        sequence: "",
        hardwareAndIo: "",
        safetyAndInterlocks: "도어 개방 시 정지",
      },
      updatedAt: "2026-07-30T00:00:02Z",
      messages: [
        {
          messageId: "previous-ready-plan",
          role: "assistant",
          status: "complete",
          createdAt: "2026-07-30T00:00:01Z",
          runId: "previous-run",
          workflow: "new_project_planning",
          planStatus: "ready_for_review",
          projectName: "조립기",
          content: "## 이전 계획",
        },
      ],
    };
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(activeStatus));
      }
      if (path === "/api/v1/conversations/operation-planning") {
        return jsonResponse(operationConversation);
      }
      if (path === "/api/v1/conversations/stale-conversation") {
        throw new Error("stale local conversation must not be restored");
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "조립기" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "요구사항 수정" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", {
        name: "기술 구성으로",
      }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "계획 닫기" }),
    ).toBeDisabled();
    expect(
      screen.getByPlaceholderText(
        "계획을 보완하거나 에이전트의 질문에 답해 주세요",
      ),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "실행 중 작업 취소" }),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("stale-conversation"),
      ),
    ).toBe(false);
  });

  it("returns to the brief instead of leaving a phantom planning workspace when the first send fails", async () => {
    const planningConversation = {
      conversationId: "failed-planning",
      title: "이송기 · 신규 설계",
      workflow: "new_project_planning",
      projectBrief: {
        projectName: "이송기",
        machinePurpose: "제품을 다음 공정으로 이송한다",
        sequence: "",
        hardwareAndIo: "",
        safetyAndInterlocks: "비상정지와 가드 인터록을 적용한다",
      },
      messages: [],
      updatedAt: "2026-07-30T00:00:00Z",
    };
    let conversationCreates = 0;
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap());
      }
      if (path === "/api/v1/conversations" && init?.method === "POST") {
        conversationCreates += 1;
        return jsonResponse(
          conversationCreates === 1
            ? conversation()
            : planningConversation,
          201,
        );
      }
      if (
        path === "/api/v1/conversations/failed-planning/messages" &&
        init?.method === "POST"
      ) {
        return jsonResponse(
          { title: "Planning request failed", detail: "모델 호출 실패" },
          503,
        );
      }
      if (path === "/api/v1/conversations/failed-planning") {
        return jsonResponse(planningConversation);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "요구사항부터 시작" }),
    );
    fireEvent.change(screen.getByLabelText("프로젝트 이름"), {
      target: { value: "이송기" },
    });
    fireEvent.change(
      screen.getByLabelText(/이 설비가 무엇을 해야 하나요\?/),
      {
        target: { value: "제품을 다음 공정으로 이송한다" },
      },
    );
    fireEvent.change(screen.getByLabelText("안전·인터록·운영 제약"), {
      target: { value: "비상정지와 가드 인터록을 적용한다" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "계획서 초안 만들기" }),
    );

    expect(
      await screen.findByRole("heading", {
        name: "어떤 설비를 만들까요?",
      }),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("이송기")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "에이전트 작업 공간" }),
    ).not.toBeInTheDocument();
    expect(await screen.findByText("모델 호출 실패")).toBeInTheDocument();
  });

  it("keeps read-only PLC observation available beside an active local workspace", async () => {
    const attachedStatus = {
      ...READY_STATUS,
      capabilities: [...READY_STATUS.capabilities, "online_observe"],
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        processId: 101,
        sessionVerified: false,
        readReady: true,
        identityAssurance: "controlled_launch_read_only" as const,
      },
      workspace: {
        workspaceId: "workspace-1",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };
    vi.stubGlobal(
      "fetch",
      authenticatedFetch(async (input) => {
        const path = String(input);
        if (path === "/api/v1/bootstrap") {
          return jsonResponse(bootstrap(attachedStatus));
        }
        if (path === "/api/v1/conversations") {
          return jsonResponse(conversation(), 201);
        }
        throw new Error(`unexpected request: ${path}`);
      }),
    );

    render(<App />);

    expect(
      within(
        await screen.findByRole("region", { name: "현재 엔지니어링 대상" }),
      ).getByText("machine.xgwx"),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "온라인 관찰" }),
    );

    expect(
      screen.getByRole("heading", {
        name: "온라인 LS PLC 읽기 전용 관찰",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "어디서 시작할까요?" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/PLC의 전체 프로젝트를 새 작업공간으로 복원하는 기능과/),
    ).toBeInTheDocument();
  });

  it("opens one exclusive new-work chooser from an active engineering target", async () => {
    const attachedStatus = {
      ...READY_STATUS,
      capabilities: [
        ...READY_STATUS.capabilities,
        "native_project_create",
      ],
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        processId: 101,
        sessionVerified: false,
        readReady: true,
        identityAssurance: "controlled_launch_read_only" as const,
      },
      workspace: {
        workspaceId: "workspace-1",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(attachedStatus));
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("region", { name: "에이전트 작업 공간" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "새 작업 시작" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "새 작업" }));

    expect(
      screen.getByRole("heading", { name: "어디서 시작할까요?" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "에이전트 작업 공간" }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "현재 작업으로 돌아가기" }),
    );
    expect(
      screen.getByRole("region", { name: "에이전트 작업 공간" }),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input) === "/api/v1/conversations" &&
          init?.method === "POST",
      ),
    ).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "새 작업" }));
    fireEvent.click(
      screen.getByRole("button", { name: "요구사항부터 시작" }),
    );
    expect(
      screen.getByRole("heading", { name: "어떤 설비를 만들까요?" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "에이전트 작업 공간" }),
    ).not.toBeInTheDocument();
  });

  it("returns from new-project planning to the original engineering conversation", async () => {
    const attachedStatus = {
      ...READY_STATUS,
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        processId: 101,
        sessionVerified: false,
        readReady: true,
        identityAssurance: "controlled_launch_read_only" as const,
      },
      workspace: {
        workspaceId: "workspace-1",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };
    let conversationCreates = 0;
    const originalConversation = {
      ...conversation([
        {
          messageId: "original-answer",
          role: "assistant",
          content: "기존 설비 분석 결과",
          status: "complete",
          createdAt: "2026-07-30T00:00:00Z",
        },
      ]),
      title: "machine.xgwx 분석",
    };
    const planningConversation = {
      conversationId: "planning-from-existing",
      title: "포장기 · 신규 설계",
      workflow: "new_project_planning",
      projectBrief: {
        projectName: "포장기",
        machinePurpose: "제품을 포장한다",
        sequence: "",
        hardwareAndIo: "",
        safetyAndInterlocks: "도어 개방 시 정지",
      },
      messages: [],
      updatedAt: "2026-07-30T00:00:01Z",
    };
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(attachedStatus));
      }
      if (path === "/api/v1/conversations" && init?.method === "POST") {
        conversationCreates += 1;
        return jsonResponse(
          conversationCreates === 1
            ? originalConversation
            : planningConversation,
          201,
        );
      }
      if (
        path ===
          "/api/v1/conversations/planning-from-existing/messages" &&
        init?.method === "POST"
      ) {
        return jsonResponse({
          runId: "planning-run",
          operationId: "planning-operation",
          state: "completed",
        });
      }
      if (path === "/api/v1/conversations/planning-from-existing") {
        return jsonResponse({
          ...planningConversation,
          messages: [
            {
              messageId: "planning-answer",
              role: "assistant",
              content: "검토 가능한 계획서",
              status: "complete",
              workflow: "new_project_planning",
              planStatus: "ready_for_review",
              projectName: "포장기",
              createdAt: "2026-07-30T00:00:02Z",
            },
          ],
        });
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "machine.xgwx 분석" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "새 작업" }));
    fireEvent.click(
      screen.getByRole("button", { name: "요구사항부터 시작" }),
    );
    fireEvent.change(screen.getByLabelText("프로젝트 이름"), {
      target: { value: "포장기" },
    });
    fireEvent.change(
      screen.getByLabelText(/이 설비가 무엇을 해야 하나요\?/),
      { target: { value: "제품을 포장한다" } },
    );
    fireEvent.change(screen.getByLabelText("안전·인터록·운영 제약"), {
      target: { value: "도어 개방 시 정지" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "계획서 초안 만들기" }),
    );

    expect(
      await screen.findByRole("heading", { name: "포장기" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "계획 닫기" }));

    expect(
      screen.getByRole("heading", { name: "machine.xgwx 분석" }),
    ).toBeInTheDocument();
    expect(screen.getByText("기존 설비 분석 결과")).toBeInTheDocument();
    expect(conversationCreates).toBe(2);
  });

  it("restores the Host-owned quarantined native project operation from bootstrap without recreating it", async () => {
    const nativeProjectId = "11111111-1111-4111-8111-111111111111";
    const nativeStatus = {
      ...READY_STATUS,
      capabilities: [
        ...READY_STATUS.capabilities,
        "native_project_create",
      ],
      activeOperation: {
        kind: "native_project_create",
        nativeProjectId,
        operationId: nativeProjectId,
        state: "quarantined",
      },
    };
    const quarantinedOperation = {
      nativeProjectId,
      operationId: nativeProjectId,
      state: "quarantined",
      requestDigest: "r".repeat(64),
      actorId: "local-user",
      planDigest: "p".repeat(64),
      destinationDirectory:
        "C:\\ControlStudio\\workspaces\\AssemblyLine01",
      request: {
        operationId: nativeProjectId,
        projectName: "AssemblyLine01",
        workspaceParent: "C:\\ControlStudio\\workspaces",
        plcSeries: "XGK",
        cpuModel: "XGK-CPUE",
        programName: "MAIN",
        language: "LD",
        description: "",
        xg5000Pid: 120,
        xg5000Hwnd: 1001,
      },
      result: {},
      errorCode: "HOST_RESTARTED_DURING_NATIVE_PROJECT_CREATION",
      errorDetail: "생성 도중 Host가 다시 시작되었습니다.",
      createdAt: "2026-07-29T00:00:00Z",
      updatedAt: "2026-07-29T00:01:00Z",
      completedAt: "2026-07-29T00:01:00Z",
    };
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(nativeStatus));
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === `/api/v1/native-projects/${nativeProjectId}`) {
        expect(init?.method).toBeUndefined();
        return jsonResponse(quarantinedOperation);
      }
      if (path === "/api/v1/xg5000/instances") {
        return jsonResponse({
          status: "ready",
          instances: [{ pid: 120, hwnd: 1001, title: "XG5000" }],
        });
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "새 프로젝트 생성 결과 복구",
      }),
    ).toBeInTheDocument();
    expect(
      await screen.findByDisplayValue(
        "C:\\ControlStudio\\workspaces\\AssemblyLine01\\AssemblyLine01.xgwx",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "현재 상태 다시 검사" }),
    ).toBeDisabled();
    expect(
      await screen.findByText(
        "중단되거나 격리된 새 프로젝트 생성 작업을 다시 불러왔습니다. 생성 요청은 재실행하지 않습니다.",
      ),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input) === "/api/v1/native-projects" &&
          init?.method === "POST",
      ),
    ).toHaveLength(0);
  });

  it("reconciles a quarantined change only through an explicitly selected XG5000 PID/HWND", async () => {
    const attachedStatus = {
      ...READY_STATUS,
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        processId: 4321,
        sessionVerified: true,
      },
      workspace: {
        workspaceId: "workspace-1",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };
    const quarantined = {
      transactionId: "transaction-recovery-1",
      operationId: "change:recovery-1",
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
      intent: "격리된 변경 복구",
      plan: {
        target: { program: "MAIN" },
        current: { operand: "M100" },
        proposed: { operand: "M101" },
        impact: [],
        risks: ["결과 불명확"],
        verificationScope: "offline_native_check_only",
        validation: [
          "structural_diff",
          "xg5000_program_check",
          "project_save",
        ],
        manualFollowUp: ["복구 뒤 실제 설비 상태를 현장에서 확인"],
        rollback: ["exact rollback"],
      },
      planDigest: "b".repeat(64),
      requestedByActorId: "local-user",
      approvalExpiresAt: "2099-01-01T00:00:00Z",
      result: {},
      errorCode: "native_change_outcome_uncertain",
      errorDetail: "실제 상태 확인 필요",
      createdAt: "2026-07-29T00:00:00Z",
      updatedAt: "2026-07-29T00:00:00Z",
    };
    const recovered = {
      ...quarantined,
      state: "rolled_back",
      result: {
        reconciliation: {
          native_mutation_performed: true,
          final_status: {
            transaction_checkpoint: {
              state: "ROLLED_BACK_SAVED",
              terminal: true,
            },
          },
        },
      },
      errorCode: "explicit_recovery_rolled_back",
      errorDetail: "원복과 저장 확인",
    };
    const fetchMock = authenticatedFetch(
      async (input, init) => {
        const path = String(input);
        if (path === "/api/v1/bootstrap") {
          return jsonResponse(bootstrap(attachedStatus));
        }
        if (path === "/api/v1/conversations") {
          return jsonResponse(conversation(), 201);
        }
        if (path === "/api/v1/xg5000/instances") {
          return jsonResponse({
            status: "ready",
            instances: [
              { pid: 4321, hwnd: 8765, title: "XG5000 - machine" },
            ],
          });
        }
        if (
          path ===
          "/api/v1/change-transactions/transaction-recovery-1/reconcile"
        ) {
          expect(JSON.parse(String(init?.body))).toEqual({
            expectedPlanDigest: quarantined.planDigest,
            pid: 4321,
            hwnd: 8765,
          });
          return jsonResponse({ transaction: recovered, reconciled: true });
        }
        if (path === "/api/v1/status") {
          return jsonResponse(attachedStatus);
        }
        throw new Error(`unexpected request: ${path}`);
      },
      [quarantined],
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    const target = await screen.findByLabelText(
      "복구 전용으로 확인할 XG5000 창",
    );
    await screen.findByRole("option", {
      name: "XG5000 - machine · PID 4321 · HWND 8765",
    });
    const recoveryButton = screen.getByRole("button", {
      name: "선택한 창으로 격리 복구 검사",
    });
    expect(recoveryButton).toBeDisabled();
    fireEvent.change(target, { target: { value: "8765" } });
    await waitFor(() => expect(recoveryButton).toBeEnabled());
    fireEvent.click(recoveryButton);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input]) =>
            String(input) ===
            "/api/v1/change-transactions/transaction-recovery-1/reconcile",
        ),
      ).toBe(true),
    );

    expect(
      await screen.findByRole("heading", { name: "격리 복구 증거" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "원래 상태로 원복하고 저장한 증거를 확인했습니다. 제안 변경을 다시 적용하지 않았습니다.",
      ),
    ).toBeInTheDocument();
  });

  it("selects a running XG5000 window as a read-only engineering session", async () => {
    const attachedStatus = {
      ...READY_STATUS,
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        processId: 101,
        sessionVerified: true,
        detail: "선택한 PID와 프로젝트 정체성이 검증되었습니다.",
      },
      workspace: {
        workspaceId: "session-1",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };

    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap());
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/xg5000/instances") {
        return jsonResponse({
          status: "ready",
          instances: [
            {
              pid: 101,
              hwnd: 202,
              title: "XG5000 - machine.xgwx",
              version: "4.70",
            },
          ],
        });
      }
      if (path === "/api/v1/xg5000/attach") {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({ pid: 101, hwnd: 202 });
        return jsonResponse({
          status: "attached",
          session: { session_id: "session-1" },
        });
      }
      if (path === "/api/v1/status") {
        return jsonResponse(attachedStatus);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const selectButton = await screen.findByRole("button", {
      name: "프로젝트 열기",
    });
    fireEvent.click(selectButton);
    fireEvent.click(
      screen.getByRole("button", { name: "이미 열린 XG5000 창 선택" }),
    );

    expect(
      await screen.findByRole("heading", { name: "XG5000 실행 창 선택" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/실제 온라인 PLC 연결이나 PLC 프로젝트 업로드가 아닙니다/),
    ).toBeInTheDocument();
    fireEvent.click(
      await screen.findByRole("button", { name: "분석 세션 연결" }),
    );

    expect(
      within(
        await screen.findByRole("region", { name: "현재 엔지니어링 대상" }),
      ).getByText("machine.xgwx"),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("증상이나 확인할 동작을 설명해 주세요"),
    ).toBeEnabled();
    expect(
      screen.queryByText("현재 연결에 확인이 필요한 항목이 있습니다"),
    ).not.toBeInTheDocument();
  });

  it("keeps the instance picker open when an existing XG5000 target is unverified", async () => {
    const unverifiedStatus = {
      ...READY_STATUS,
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "degraded" as const,
        processId: 101,
        sessionVerified: false,
        readReady: false,
        identityAssurance: "unverified" as const,
      },
    };
    vi.stubGlobal(
      "fetch",
      authenticatedFetch(async (input, init) => {
        const path = String(input);
        if (path === "/api/v1/bootstrap") {
          return jsonResponse(bootstrap());
        }
        if (path === "/api/v1/conversations") {
          return jsonResponse(conversation(), 201);
        }
        if (path === "/api/v1/xg5000/instances") {
          return jsonResponse({
            status: "ready",
            instances: [
              {
                pid: 101,
                hwnd: 202,
                title: "machine - XG5000",
                version: "4.82.1",
              },
            ],
          });
        }
        if (path === "/api/v1/xg5000/attach") {
          expect(init?.method).toBe("POST");
          return jsonResponse({
            status: "attached",
            session: { sessionId: "session-unverified" },
          });
        }
        if (path === "/api/v1/status") {
          return jsonResponse(unverifiedStatus);
        }
        throw new Error(`unexpected request: ${path}`);
      }),
    );

    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "프로젝트 열기" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "이미 열린 XG5000 창 선택" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "분석 세션 연결" }),
    );

    expect(
      screen.getByRole("heading", { name: "XG5000 실행 창 선택" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "이 실행 창에서는 프로젝트 정체성을 확인할 수 없어 분석을 시작하지 않았습니다. 창을 닫고 기존 로컬 프로젝트 열기에서 다시 열어 주세요.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "에이전트 작업 공간" }),
    ).not.toBeInTheDocument();
  });

  it("keeps analysis submission closed for an unverified project identity", async () => {
    const unverifiedStatus = {
      ...READY_STATUS,
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "degraded",
        processId: 101,
        sessionVerified: false,
        readReady: false,
        identityAssurance: "unverified",
      },
      workspace: {
        workspaceId: "session-unverified",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };
    vi.stubGlobal(
      "fetch",
      authenticatedFetch(async (input) => {
        const path = String(input);
        if (path === "/api/v1/bootstrap") {
          return jsonResponse(bootstrap(unverifiedStatus));
        }
        if (path === "/api/v1/conversations") {
          return jsonResponse(conversation(), 201);
        }
        throw new Error(`unexpected request: ${path}`);
      }),
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "어디서 시작할까요?" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "에이전트 작업 공간" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("프로젝트 확인 제한")).toBeInTheDocument();
    expect(
      screen.getByText("현재 연결에 확인이 필요한 항목이 있습니다"),
    ).toBeInTheDocument();
  });

  it("resumes a persisted workspace without asking for the source path again", async () => {
    const persistedStatus = {
      ...READY_STATUS,
      capabilities: [
        ...READY_STATUS.capabilities,
        "open_local_project",
      ],
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "degraded",
        readReady: false,
        identityAssurance: "unverified" as const,
        detail: "XG5000 분석 세션 연결을 기다립니다.",
      },
      workspace: {
        workspaceId: "workspace-resume",
        displayName: "SampleLine.xgwx",
        source: "local" as const,
        vendor: "ls",
        artifactHash: "a".repeat(64),
      },
    };
    const resumedStatus = {
      ...persistedStatus,
      engineeringSystem: {
        ...persistedStatus.engineeringSystem,
        state: "ready",
        processId: 202,
        readReady: true,
        identityAssurance: "controlled_launch_read_only" as const,
      },
    };
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(persistedStatus));
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (
        path ===
        "/api/v1/workspaces/workspace-resume/resume"
      ) {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
          timeoutSeconds: 120,
        });
        return jsonResponse({
          workspace: {
            workspaceId: "workspace-resume",
            revisionId: "revision-resume",
            sourceKind: "local_file",
            vendor: "ls",
            engineeringSystem: "XG5000",
            createdAt: "2026-07-30T00:00:00Z",
            source: {
              originalPath: "C:\\projects\\SampleLine.xgwx",
              capturedPath: "C:\\workspaces\\source\\SampleLine.xgwx",
              sha256: "a".repeat(64),
              originalState: "unchanged",
            },
            workingCopy: {
              path: "C:\\workspaces\\working\\SampleLine.xgwx",
              baselineSha256: "a".repeat(64),
              currentSha256: "a".repeat(64),
              state: "clean",
            },
          },
          session: { sessionId: "session-resume" },
        });
      }
      if (path === "/api/v1/status") {
        return jsonResponse(resumedStatus);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "SampleLine.xgwx" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("저장된 작업공간 · 재검증 필요"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("SampleLine.xgwx · 저장됨, 재검증 필요"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("SHA-256 aaaaaaaaaaaa…"),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "이 작업 계속하기" }),
    );

    expect(
      await screen.findByRole("region", { name: "에이전트 작업 공간" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("읽기 대상 확인").length).toBeGreaterThan(0);
    expect(
      screen.queryByRole("heading", { name: "어디서 시작할까요?" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("증상이나 확인할 동작을 설명해 주세요"),
    ).toBeEnabled();
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input) === "/api/v1/conversations" &&
          init?.method === "POST",
      ),
    ).toHaveLength(2);
  });

  it("opens a local project through a source-preserving MCP workspace", async () => {
    const sourcePath = "C:\\projects\\machine.xgwx";
    const attachedStatus = {
      ...READY_STATUS,
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        processId: 101,
        sessionVerified: false,
        readReady: true,
        identityAssurance: "controlled_launch_read_only" as const,
      },
      workspace: {
        workspaceId: "workspace-1",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap());
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/files/select-xg5000-project") {
        expect(init?.method).toBe("POST");
        return jsonResponse({
          selected: true,
          path: sourcePath,
        });
      }
      if (path === "/api/v1/workspaces/open-local") {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
          sourcePath,
          openInXg5000: true,
          timeoutSeconds: 120,
        });
        return jsonResponse({
          workspace: {
            workspaceId: "workspace-1",
            revisionId: "revision-1",
            sourceKind: "local_file",
            vendor: "ls",
            engineeringSystem: "XG5000",
            createdAt: "2026-07-28T12:00:00Z",
            source: {
              originalPath: sourcePath,
              capturedPath: "C:\\workspaces\\source\\machine.xgwx",
              sha256: "a".repeat(64),
              originalState: "unchanged",
            },
            workingCopy: {
              path: "C:\\workspaces\\working\\machine.xgwx",
              baselineSha256: "a".repeat(64),
              currentSha256: "a".repeat(64),
              state: "clean",
            },
          },
          launch: {
            workspaceId: "workspace-1",
            workingPath: "C:\\workspaces\\working\\machine.xgwx",
            workingHash: "a".repeat(64),
            launchPid: 101,
            windowDetected: true,
            projectIdentityVerifiedAgainstSelectedPid: false,
            detail: "opened",
          },
          session: { sessionId: "session-1" },
        });
      }
      if (path === "/api/v1/status") {
        return jsonResponse(attachedStatus);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "프로젝트 열기" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "찾아보기" }));
    expect(await screen.findByDisplayValue(sourcePath)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "복사본으로 열기" }));

    expect(
      within(
        await screen.findByRole("region", { name: "현재 엔지니어링 대상" }),
      ).getByText("machine.xgwx"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "에이전트 작업 공간" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("읽기 대상 확인").length).toBeGreaterThan(0);
    expect(
      screen.getByPlaceholderText("증상이나 확인할 동작을 설명해 주세요"),
    ).toBeEnabled();
    expect(screen.getByText("분석 모드 · 변경 차단")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "기존 로컬 프로젝트 열기" }),
    ).not.toBeInTheDocument();
  });

  it("lists verified project programs and opens the selected location in XG5000", async () => {
    const attachedStatus = {
      ...READY_STATUS,
      featureReadiness: [
        {
          featureId: "program_focus",
          label: "XG5000 프로그램 위치 열기",
          maturity: "supported",
          verification: "real_xg5000",
          currentReady: true,
          detail: "실제 XG5000에서 위치 열기를 검증했습니다.",
        },
      ],
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        processId: 101,
        sessionVerified: true,
        readReady: true,
        identityAssurance: "controlled_launch_read_only",
      },
      workspace: {
        workspaceId: "workspace-1",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
        artifactHash: "a".repeat(64),
      },
    };
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(attachedStatus));
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/engineering/programs") {
        return jsonResponse({
          programs: [
            {
              programId: "program-initial",
              name: "초기동작",
              language: "LD",
            },
          ],
        });
      }
      if (
        path ===
        "/api/v1/engineering/programs/program-initial/focus"
      ) {
        expect(init?.method).toBe("POST");
        expect(
          new Headers(init?.headers).get("X-ControlStudio-CSRF"),
        ).toBe("browser-csrf");
        return jsonResponse({
          status: "focused",
          verified: true,
          readOnly: true,
          projectUnchanged: true,
          program: {
            programId: "program-initial",
            name: "초기동작",
            language: "LD",
          },
        });
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "프로그램" }),
    );
    expect(
      await screen.findByRole("button", {
        name: "초기동작 XG5000에서 열기",
      }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: "초기동작 XG5000에서 열기",
      }),
    );

    expect(
      await screen.findByText(
        "초기동작 프로그램을 XG5000에서 열었습니다. 프로젝트 내용은 변경되지 않았습니다.",
      ),
    ).toBeInTheDocument();
  });

  it("lists read-ready programs while fail-closing unverified location focus", async () => {
    const attachedStatus = {
      ...READY_STATUS,
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        processId: 101,
        sessionVerified: false,
        readReady: true,
        identityAssurance: "controlled_launch_read_only",
      },
      workspace: {
        workspaceId: "workspace-1",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(attachedStatus));
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/engineering/programs") {
        return jsonResponse({
          programs: [
            {
              programId: "program-initial",
              name: "초기동작",
              language: "LD",
            },
          ],
        });
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "프로그램" }),
    );

    const focusButton = await screen.findByRole("button", {
      name: "초기동작 XG5000에서 열기",
    });
    expect(focusButton).toBeDisabled();
    expect(
      screen.getByText(
        "XG5000 위치 열기는 실제 환경 검증 전 비활성입니다.",
      ),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/program-initial/focus"),
      ),
    ).toBe(false);
  });

  it("restores server messages and submits an idempotent analysis request", async () => {
    const attachedStatus = {
      ...READY_STATUS,
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        processId: 101,
        sessionVerified: true,
      },
      workspace: {
        workspaceId: "session-1",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };
    const userMessage = {
      messageId: "message-1",
      role: "user",
      content: "이 동작이 왜 느린지 확인해 줘",
      createdAt: "2026-07-28T10:01:00Z",
      status: "complete",
    };
    let conversationReads = 0;

    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(attachedStatus));
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/conversations/conversation-1/messages") {
        const body = JSON.parse(String(init?.body)) as {
          content: string;
          operationId: string;
        };
        expect(body.content).toBe(userMessage.content);
        expect(body.operationId).toMatch(/^message:/);
        expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(
          body.operationId,
        );
        return jsonResponse(
          {
            runId: "run-1",
            operationId: body.operationId,
            state: "analyzing",
          },
          202,
        );
      }
      if (path === "/api/v1/conversations/conversation-1") {
        conversationReads += 1;
        return jsonResponse(conversation([userMessage]));
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const composer = await screen.findByPlaceholderText(
      "증상이나 확인할 동작을 설명해 주세요",
    );
    fireEvent.change(composer, { target: { value: userMessage.content } });
    fireEvent.click(screen.getByRole("button", { name: "메시지 보내기" }));

    expect(await screen.findByText(userMessage.content)).toBeInTheDocument();
    expect(conversationReads).toBeGreaterThan(0);
  });

  it.each([
    {
      content: "T120을 잠깐 늘려 시험해 보자",
      selectedScope: "analysis_only",
      radioName: /분석·변경안만.*프로젝트에 쓰지 않음/,
    },
    {
      content: "M100을 M101로 바꿔줘",
      selectedScope: "offline_candidate_revision",
      radioName: /오프라인 후보 revision.*작업 복사본에 저장/,
    },
    {
      content: "T120을 3초로 조정해줘",
      selectedScope: "analysis_only",
      radioName: /분석·변경안만.*프로젝트에 쓰지 않음/,
    },
  ])(
    "requires an explicit scope for every change request: $content",
    async ({ content, selectedScope, radioName }) => {
    const attachedStatus = {
      ...READY_STATUS,
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready",
        processId: 101,
        sessionVerified: true,
      },
      workspace: {
        workspaceId: "session-1",
        displayName: "machine.xgwx",
        source: "local",
        vendor: "ls",
      },
    };
    const userMessage = {
      messageId: "message-temporary-1",
      role: "user",
      content,
      createdAt: "2026-07-28T10:01:00Z",
      status: "complete",
    };
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(attachedStatus));
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/conversations/conversation-1/messages") {
        const body = JSON.parse(String(init?.body)) as {
          content: string;
          operationId: string;
          changeRequestScope?: string;
        };
        expect(body.content).toBe(content);
        expect(body.changeRequestScope).toBe(selectedScope);
        return jsonResponse(
          {
            runId: "run-temporary-1",
            operationId: body.operationId,
            state: "analyzing",
          },
          202,
        );
      }
      if (path === "/api/v1/conversations/conversation-1") {
        return jsonResponse(conversation([userMessage]));
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    const composer = await screen.findByPlaceholderText(
      "증상이나 확인할 동작을 설명해 주세요",
    );
    fireEvent.change(composer, { target: { value: content } });

    expect(
      screen.getByRole("group", { name: "이 변경 요청의 범위" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "XG-SIM 시험과 온라인·런중 시험은 아직 사용할 수 없습니다.",
      ),
    ).toBeInTheDocument();
    const sendButton = screen.getByRole("button", { name: "메시지 보내기" });
    expect(sendButton).toBeDisabled();

    fireEvent.click(
      screen.getByRole("radio", {
        name: radioName,
      }),
    );
    expect(sendButton).toBeEnabled();
    fireEvent.click(sendButton);

    expect(await screen.findByText(content)).toBeInTheDocument();
    },
  );

  it("restores only a conversation bound to the current XG5000 target", async () => {
    window.localStorage.setItem(
      "controlstudio.activeConversationId",
      "stale-conversation",
    );
    const currentTarget = {
      workspaceId: "workspace-current",
      revisionId: "revision-current",
      engineeringSessionId: "session-current",
    };
    const attachedStatus = {
      ...READY_STATUS,
      engineeringSystem: {
        ...READY_STATUS.engineeringSystem,
        state: "ready" as const,
        processId: 101,
        sessionId: currentTarget.engineeringSessionId,
        sessionVerified: false,
        readReady: true,
        identityAssurance: "controlled_launch_read_only" as const,
      },
      workspace: {
        workspaceId: currentTarget.workspaceId,
        displayName: "current-machine.xgwx",
        source: "local" as const,
        revision: currentTarget.revisionId,
        artifactHash: "a".repeat(64),
        vendor: "ls",
      },
    };
    const staleConversation = {
      ...conversation(),
      conversationId: "stale-conversation",
      title: "이전 설비 대화",
      engineeringTarget: {
        workspaceId: "workspace-old",
        revisionId: "revision-old",
        engineeringSessionId: "session-old",
      },
    };
    const matchingConversation = {
      ...conversation(),
      conversationId: "matching-conversation",
      title: "현재 설비 대화",
      engineeringTarget: currentTarget,
    };
    const matchingSummary = {
      conversationId: matchingConversation.conversationId,
      title: matchingConversation.title,
      workflow: matchingConversation.workflow,
      engineeringTarget: currentTarget,
      updatedAt: matchingConversation.updatedAt,
    };

    const fetchMock = authenticatedFetch(
      async (input, init) => {
        const path = String(input);
        if (path === "/api/v1/bootstrap") {
          return jsonResponse(bootstrap(attachedStatus));
        }
        if (path === "/api/v1/conversations/stale-conversation") {
          return jsonResponse(staleConversation);
        }
        if (path === "/api/v1/conversations/matching-conversation") {
          return jsonResponse(matchingConversation);
        }
        if (path === "/api/v1/conversations" && init?.method === "POST") {
          throw new Error("a matching target-bound conversation must be reused");
        }
        throw new Error(`unexpected request: ${path}`);
      },
      [],
      [matchingSummary],
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("region", { name: "에이전트 작업 공간" }),
    ).toBeInTheDocument();
    expect(
      window.localStorage.getItem("controlstudio.activeConversationId"),
    ).toBe("matching-conversation");
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input) === "/api/v1/conversations" &&
          init?.method === "POST",
      ),
    ).toBe(false);
  });

  it("does not replace a stale machine conversation with an unrelated old plan", async () => {
    window.localStorage.setItem(
      "controlstudio.activeConversationId",
      "stale-machine-conversation",
    );
    const staleConversation = {
      ...conversation(),
      conversationId: "stale-machine-conversation",
      engineeringTarget: {
        workspaceId: "workspace-old",
        revisionId: "revision-old",
        engineeringSessionId: "session-old",
      },
    };
    const oldPlanningSummary = {
      conversationId: "old-plan",
      title: "Old unrelated plan",
      workflow: "new_project_planning",
      updatedAt: "2026-07-29T01:00:00Z",
    };
    let created = false;
    const fetchMock = authenticatedFetch(
      async (input, init) => {
        const path = String(input);
        if (path === "/api/v1/bootstrap") {
          return jsonResponse(bootstrap());
        }
        if (path === "/api/v1/conversations/stale-machine-conversation") {
          return jsonResponse(staleConversation);
        }
        if (path === "/api/v1/conversations" && init?.method === "POST") {
          created = true;
          return jsonResponse(conversation(), 201);
        }
        throw new Error(`unexpected request: ${path}`);
      },
      [],
      [oldPlanningSummary],
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "어디서 시작할까요?" }),
    ).toBeInTheDocument();
    expect(created).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/v1/conversations?"),
      ),
    ).toBe(false);
    expect(screen.queryByText("Old unrelated plan")).not.toBeInTheDocument();
  });

  it("requires explicit consent before testing an external model endpoint", async () => {
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap());
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/settings/model") {
        return jsonResponse({ detail: "model is not configured" }, 404);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("button", { name: "프로젝트 열기" });
    fireEvent.click(
      screen.getByRole("button", { name: "모델 연결 설정 열기" }),
    );

    const dialog = await screen.findByRole("dialog", {
      name: "모델 연결 설정",
    });
    expect(dialog).toHaveTextContent(
      "저장된 API 키와 헤더 값은 브라우저로 다시 전송하거나 표시하지 않습니다.",
    );
    fireEvent.change(screen.getByLabelText(/Base URL/), {
      target: { value: "https://models.example/v1" },
    });
    fireEvent.change(screen.getByLabelText(/^모델$/), {
      target: { value: "engineering-model" },
    });

    const testButton = screen.getByRole("button", { name: "연결 테스트" });
    expect(testButton).toBeDisabled();
    const consent = screen.getByRole("checkbox", {
      name: /PLC 프로젝트 내용과 대화가 이 PC 밖의 모델 엔드포인트로 전송/,
    });
    fireEvent.click(consent);
    await waitFor(() => expect(testButton).toBeEnabled());

    const apiKey = screen.getByLabelText(/API Key/);
    fireEvent.change(apiKey, { target: { value: "temporary-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "설정 닫기" }));
    expect(document.body).not.toHaveTextContent("temporary-secret");

    fireEvent.click(
      screen.getByRole("button", { name: "모델 연결 설정 열기" }),
    );
    expect(await screen.findByLabelText(/API Key/)).toHaveValue("");
  });

  it("requires separate transport consent for a Tailscale HTTP model endpoint", async () => {
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap());
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/settings/model") {
        return jsonResponse({ detail: "model is not configured" }, 404);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("button", { name: "프로젝트 열기" });
    fireEvent.click(
      screen.getByRole("button", { name: "모델 연결 설정 열기" }),
    );
    fireEvent.change(await screen.findByLabelText(/Base URL/), {
      target: { value: "http://100.64.0.10:8080/v1" },
    });
    fireEvent.change(screen.getByLabelText(/^모델$/), {
      target: { value: "engineering-model" },
    });

    const testButton = screen.getByRole("button", { name: "연결 테스트" });
    expect(testButton).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /PLC 프로젝트 내용과 대화가 이 PC 밖의 모델 엔드포인트로 전송/,
      }),
    );
    expect(testButton).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /인증서가 없는 HTTP입니다/,
      }),
    );
    await waitFor(() => expect(testButton).toBeEnabled());
  });

  it("exchanges and removes a handoff ticket before rendering product data", async () => {
    const handoffTicket = "one-time-handoff-ticket-that-must-not-render";
    const browserSessionToken = "derived-browser-session";
    window.sessionStorage.clear();
    window.history.replaceState(
      {},
      "",
      `/#view=main&handoff_ticket=${handoffTicket}`,
    );
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/session") {
        expect(
          new Headers(init?.headers).get("Authorization"),
        ).toBe(`Bearer ${handoffTicket}`);
        expect(window.location.hash).toBe("#view=main");
        return jsonResponse({
          sessionToken: browserSessionToken,
          expiresAt: "2099-01-01T00:00:00Z",
        });
      }
      if (path === "/api/v1/bootstrap") {
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          `Bearer ${browserSessionToken}`,
        );
        return jsonResponse(bootstrap());
      }
      if (path === "/api/v1/conversations") {
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          `Bearer ${browserSessionToken}`,
        );
        return jsonResponse(conversation(), 201);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await screen.findByRole("button", { name: "프로젝트 열기" });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/session");
    expect(document.body).not.toHaveTextContent(handoffTicket);
    expect(document.body).not.toHaveTextContent(browserSessionToken);
    expect(window.location.hash).toBe("#view=main");
    expect(window.localStorage.getItem("access_token")).toBeNull();
    expect(window.localStorage.getItem("controlstudio.browserSession")).toBeNull();
    expect(
      window.sessionStorage.getItem("controlstudio.browserSession"),
    ).toBe(
      JSON.stringify({
        sessionToken: browserSessionToken,
        expiresAt: "2099-01-01T00:00:00Z",
      }),
    );
    expect(
      [...Array(window.localStorage.length)].some((_, index) =>
        {
          const key = window.localStorage.key(index);
          return Boolean(
              key?.includes(handoffTicket) ||
              (key && window.localStorage.getItem(key)?.includes(handoffTicket)),
          );
        },
      ),
    ).toBe(false);
    expect(
      window.sessionStorage
        .getItem("controlstudio.browserSession")
        ?.includes(handoffTicket),
    ).toBe(false);
  });

  it("refreshes a recently expired stored session before loading product data", async () => {
    window.sessionStorage.setItem(
      "controlstudio.browserSession",
      JSON.stringify({
        sessionToken: "expired-browser-session",
        expiresAt: "2026-01-01T00:00:00Z",
      }),
    );
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/session/refresh") {
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          "Bearer expired-browser-session",
        );
        return jsonResponse({
          sessionToken: "refreshed-browser-session",
          expiresAt: "2099-01-01T00:00:00Z",
        });
      }
      if (path === "/api/v1/bootstrap") {
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          "Bearer refreshed-browser-session",
        );
        return jsonResponse(bootstrap());
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await screen.findByText("Host 준비됨");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/session/refresh");
    expect(
      window.sessionStorage.getItem("controlstudio.browserSession"),
    ).toBe(
      JSON.stringify({
        sessionToken: "refreshed-browser-session",
        expiresAt: "2099-01-01T00:00:00Z",
      }),
    );
  });

  it("clears the browser session when a product API rejects it", async () => {
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(
          {
            title: "Unauthorized",
            detail: "browser session expired",
            status: 401,
          },
          401,
        );
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "Launcher 세션 인증이 필요합니다",
      }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "ControlStudio Launcher에서 다시 열어 세션을 인증해 주세요.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        "Host에 연결할 수 없습니다. ControlStudio를 다시 시작해 주세요.",
      ),
    ).not.toBeInTheDocument();
    expect(
      window.sessionStorage.getItem("controlstudio.browserSession"),
    ).toBeNull();
  });

  it("cancels an active run explicitly and reports the terminal result", async () => {
    const runningStatus = {
      ...READY_STATUS,
      activeOperation: {
        runId: "run-42",
        state: "analyzing" as const,
        label: "프로젝트 영향 분석",
      },
    };
    const fetchMock = authenticatedFetch(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(runningStatus));
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/runs/run-42/cancel") {
        expect(init?.method).toBe("POST");
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          "Bearer test-browser-session",
        );
        return jsonResponse({
          runId: "run-42",
          state: "cancelled",
          terminal: true,
          idempotent: false,
          detail: "작업을 취소했습니다.",
        });
      }
      if (path === "/api/v1/status") {
        return jsonResponse(READY_STATUS);
      }
      if (path === "/api/v1/conversations/conversation-1") {
        return jsonResponse(conversation());
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "실행 중 작업 취소" }),
    );

    expect(
      await screen.findAllByText("작업을 취소했습니다."),
    ).not.toHaveLength(0);
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "실행 중 작업 취소" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("treats an already-terminal cancel response as an idempotent success", async () => {
    const runningStatus = {
      ...READY_STATUS,
      activeOperation: {
        runId: "run-terminal",
        state: "executing" as const,
      },
    };
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap(runningStatus));
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/runs/run-terminal/cancel") {
        return jsonResponse({
          runId: "run-terminal",
          state: "completed",
          terminal: true,
          idempotent: true,
        });
      }
      if (path === "/api/v1/status") {
        return jsonResponse(READY_STATUS);
      }
      if (path === "/api/v1/conversations/conversation-1") {
        return jsonResponse(conversation());
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "실행 중 작업 취소" }),
    );

    expect(
      await screen.findAllByText("작업은 이미 종료 상태입니다: 완료"),
    ).not.toHaveLength(0);
    expect(
      screen.queryByRole("button", { name: "실행 중 작업 취소" }),
    ).not.toBeInTheDocument();
  });

  it("only shuts down from the explicit confirmation action", async () => {
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap());
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/host/shutdown") {
        return jsonResponse({ status: "shutting_down" }, 202);
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("button", { name: "프로젝트 열기" });

    window.dispatchEvent(new Event("beforeunload"));
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/host/shutdown"),
      ),
    ).toBe(false);

    fireEvent.click(
      screen.getByRole("button", { name: "ControlStudio 종료" }),
    );
    expect(
      screen.getByRole("dialog", { name: "ControlStudio 종료" }),
    ).toHaveTextContent(
      "브라우저나 PWA 창을 닫는 것만으로는 ControlStudio가 종료되지 않습니다.",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Host와 MCP 종료" }),
    );

    expect(
      await screen.findByRole("heading", {
        name: "ControlStudio를 종료했습니다",
      }),
    ).toBeInTheDocument();
    expect(
      window.sessionStorage.getItem("controlstudio.browserSession"),
    ).toBeNull();
  });

  it("shows the Host reason when an active operation blocks shutdown", async () => {
    const fetchMock = authenticatedFetch(async (input) => {
      const path = String(input);
      if (path === "/api/v1/bootstrap") {
        return jsonResponse(bootstrap());
      }
      if (path === "/api/v1/conversations") {
        return jsonResponse(conversation(), 201);
      }
      if (path === "/api/v1/host/shutdown") {
        return jsonResponse(
          { detail: "분석 작업 run-42가 아직 실행 중입니다." },
          409,
        );
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("button", { name: "프로젝트 열기" });
    fireEvent.click(
      screen.getByRole("button", { name: "ControlStudio 종료" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Host와 MCP 종료" }),
    );

    expect(
      await screen.findByText(
        "현재 작업 때문에 종료할 수 없습니다: 분석 작업 run-42가 아직 실행 중입니다. 실행 중인 작업을 먼저 취소하거나 완료될 때까지 기다려 주세요.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("dialog", { name: "ControlStudio 종료" }),
    ).toBeInTheDocument();
  });
});
