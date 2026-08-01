import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChangeTransaction } from "./types";
import { HostApiClient } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("HostApiClient product API contract", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("records a new-project plan review against the exact message and digest", async () => {
    const planDigest = "d".repeat(64);
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        review: {
          messageId: "message-plan-1",
          planDigest,
          reviewedSectionIds: ["project-brief", "validation-plan"],
          actorId: "browser:reviewer",
          reviewedAt: "2026-07-30T01:00:00Z",
        },
        reviewRecorded: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    client.setSessionToken("browser-session");
    client.setCsrfToken("browser-csrf");

    const response = await client.reviewProjectPlan("conversation-1", {
      messageId: "message-plan-1",
      planDigest,
      reviewedSectionIds: ["project-brief", "validation-plan"],
    });

    expect(response.reviewRecorded).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/conversations/conversation-1/project-plan/review",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          messageId: "message-plan-1",
          planDigest,
          reviewedSectionIds: ["project-brief", "validation-plan"],
        }),
      }),
    );
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer browser-session");
    expect(headers.get("X-ControlStudio-CSRF")).toBe("browser-csrf");
  });

  it("confirms a memory with only the server revision precondition", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        sourceId: "memory/site:line-7",
        kind: "memory",
        title: "7호기 모터 기동 규칙",
        scope: "project:workspace-7",
        authority: "user-proposed:local-user",
        status: "confirmed",
        sha256: "a".repeat(64),
        revision: "revision-2",
        metadata: {},
        confirmedBy: "browser-session",
        createdAt: "2026-07-30T01:00:00Z",
        updatedAt: "2026-07-30T01:01:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    client.setSessionToken("browser-session");
    client.setCsrfToken("browser-csrf");

    await client.confirmKnowledgeMemory(
      "memory/site:line-7",
      "revision-2",
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/knowledge/memories/memory%2Fsite%3Aline-7/confirm",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ expectedRevision: "revision-2" }),
      }),
    );
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer browser-session");
    expect(headers.get("X-ControlStudio-CSRF")).toBe("browser-csrf");
  });

  it("uses only the versioned product endpoints and an idempotent message body", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          schemaVersion: "1",
          status: {},
          clientSessionId: "client-1",
          lastEventSequence: 3,
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ schemaVersion: "1" }))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            conversationId: "conversation-1",
            title: "PLC 분석 대화",
            messages: [],
            updatedAt: "2026-07-28T10:00:00Z",
          },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            runId: "run-1",
            operationId: "message:operation-123",
            state: "analyzing",
          },
          202,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          status: "ready",
          instances: [{ pid: 101, hwnd: 202, title: "XG5000" }],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          workspace: {
            workspaceId: "workspace-1",
            workingCopy: { path: "C:\\workspaces\\machine.xgwx" },
          },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          status: "attached",
          session: { session_id: "session-1" },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          runId: "run-1",
          state: "cancelled",
          terminal: true,
          idempotent: false,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    client.setSessionToken("browser-session");

    await client.getBootstrap();
    await client.getStatus();
    await client.createConversation({ title: "PLC 분석 대화" });
    await client.sendMessage("conversation-1", {
      content: "왜 느린지 확인해 줘",
      operationId: "message:operation-123",
    });
    await client.getXG5000Instances();
    await client.openLocalWorkspace({
      sourcePath: "C:\\projects\\machine.xgwx",
      openInXg5000: true,
      timeoutSeconds: 60,
    });
    await client.attachXG5000({ pid: 101, hwnd: 202 });
    await client.cancelRun("run-1");

    expect(fetchMock.mock.calls.map(([input]) => input)).toEqual([
      "/api/v1/bootstrap",
      "/api/v1/status",
      "/api/v1/conversations",
      "/api/v1/conversations/conversation-1/messages",
      "/api/v1/xg5000/instances",
      "/api/v1/workspaces/open-local",
      "/api/v1/xg5000/attach",
      "/api/v1/runs/run-1/cancel",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("Authorization")).toBe(
        "Bearer browser-session",
      );
    }

    const messageRequest = fetchMock.mock.calls[3]?.[1];
    expect(messageRequest).toMatchObject({
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      body: JSON.stringify({
        content: "왜 느린지 확인해 줘",
        operationId: "message:operation-123",
      }),
    });
    expect(new Headers(messageRequest?.headers).get("Idempotency-Key")).toBe(
      "message:operation-123",
    );
    expect(fetchMock.mock.calls[5]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        sourcePath: "C:\\projects\\machine.xgwx",
        openInXg5000: true,
        timeoutSeconds: 60,
      }),
    });
    expect(fetchMock.mock.calls[7]?.[1]?.method).toBe("POST");
  });

  it("never expects model secrets back from the Host", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({
        protocol: "openai_chat_completions",
        baseUrl: "https://models.example/v1",
        model: "engineering",
        customHeaderNames: ["X-Tenant"],
        timeoutSeconds: 60,
        hasApiKey: true,
        externalEndpoint: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const settings = await new HostApiClient().getModelSettings();

    expect(settings.customHeaderNames).toEqual(["X-Tenant"]);
    expect(settings.hasApiKey).toBe(true);
    expect(settings).not.toHaveProperty("apiKey");
    expect(settings).not.toHaveProperty("customHeaders");
  });

  it("uses CSRF-protected local UX endpoints for file selection and workspace resume", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          selected: true,
          path: "C:\\projects\\machine.xgwx",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          workspace: { workspaceId: "workspace-1" },
          session: { sessionId: "session-1" },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          transaction: {
            transactionId: "change-1",
            receiptAcknowledgedAt: "2026-07-30T00:00:00Z",
          },
          acknowledgementRecorded: true,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    client.setSessionToken("browser-session");
    client.setCsrfToken("browser-csrf");

    await expect(client.selectXG5000ProjectFile()).resolves.toEqual({
      selected: true,
      path: "C:\\projects\\machine.xgwx",
    });
    await client.resumeLocalWorkspace("workspace-1", {
      timeoutSeconds: 60,
    });
    await client.acknowledgeChangeReceipt("change-1");

    expect(fetchMock.mock.calls.map(([input]) => input)).toEqual([
      "/api/v1/files/select-xg5000-project",
      "/api/v1/workspaces/workspace-1/resume",
      "/api/v1/change-transactions/change-1/receipt-acknowledgement",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.method).toBe("POST");
      expect(new Headers(init?.headers).get("Authorization")).toBe(
        "Bearer browser-session",
      );
      expect(new Headers(init?.headers).get("X-ControlStudio-CSRF")).toBe(
        "browser-csrf",
      );
    }
    expect(fetchMock.mock.calls[1]?.[1]?.body).toBe(
      JSON.stringify({ timeoutSeconds: 60 }),
    );
  });

  it("lists programs read-only and protects XG5000 focus with the browser CSRF token", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          programs: [
            {
              programId: "program-main",
              name: "MAIN",
              language: "LD",
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          status: "focused",
          verified: true,
          readOnly: true,
          projectUnchanged: true,
          program: {
            programId: "program-main",
            name: "MAIN",
            language: "LD",
          },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    client.setSessionToken("browser-session");
    client.setCsrfToken("browser-csrf");

    await client.listEngineeringPrograms();
    await client.focusEngineeringProgram("program-main");

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/engineering/programs",
    );
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBeUndefined();
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/engineering/programs/program-main/focus",
    );
    expect(fetchMock.mock.calls[1]?.[1]?.method).toBe("POST");
    expect(
      new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get(
        "X-ControlStudio-CSRF",
      ),
    ).toBe("browser-csrf");
  });

  it("exchanges the handoff ticket once and uses the derived session bearer", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          sessionToken: "derived-browser-session",
          expiresAt: "2099-01-01T00:00:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ status: "shutting_down" }, 202),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();

    const session = await client.exchangeHandoffTicket(
      "one-time-handoff-ticket",
    );
    client.setSessionToken(session.sessionToken);
    await client.shutdownHost();

    expect(fetchMock.mock.calls.map(([input]) => input)).toEqual([
      "/api/session",
      "/api/v1/host/shutdown",
    ]);
    expect(
      new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization"),
    ).toBe("Bearer one-time-handoff-ticket");
    expect(
      new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get("Authorization"),
    ).toBe("Bearer derived-browser-session");
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
    });
  });

  it("rotates a browser session bearer without a new launcher window", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({
        sessionToken: "rotated-browser-session",
        expiresAt: "2099-01-02T00:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    client.setSessionToken("current-browser-session");
    client.setCsrfToken("current-csrf");

    const refreshed = await client.refreshBrowserSession();

    expect(refreshed.sessionToken).toBe("rotated-browser-session");
    expect(client.getAuthorizationHeader()).toBe(
      "Bearer rotated-browser-session",
    );
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/session/refresh");
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(
      new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization"),
    ).toBe("Bearer current-browser-session");
  });

  it("invalidates an in-memory session on any V1 unauthorized response", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse(
        {
          title: "Unauthorized",
          detail: "browser session expired",
          status: 401,
        },
        401,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    const onUnauthorized = vi.fn();
    client.setSessionToken("expired-browser-session");
    client.setUnauthorizedHandler(onUnauthorized);

    await expect(client.getStatus()).rejects.toMatchObject({ status: 401 });

    expect(onUnauthorized).toHaveBeenCalledOnce();
    expect(client.getAuthorizationHeader()).toBeUndefined();
  });

  it("unwraps the server-owned approval transaction across decision and execution", async () => {
    const transaction: ChangeTransaction = {
      transactionId: "transaction-1",
      operationId: "change:operation-1",
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
      intent: "피연산자 변경",
      plan: {
        target: { program: "MAIN" },
        current: { operand: "M100" },
        proposed: { operand: "M101" },
        impact: [],
        risks: [],
        verificationScope: "offline_native_check_only",
        validation: [
          "structural_diff",
          "xg5000_program_check",
          "project_save",
        ],
        manualFollowUp: [],
        rollback: ["restore backup"],
      },
      planDigest: "b".repeat(64),
      requestedByActorId: "local-user",
      approvalExpiresAt: "2099-01-01T00:00:00Z",
      result: {},
      createdAt: "2026-07-29T00:00:00Z",
      updatedAt: "2026-07-29T00:00:00Z",
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ transaction, created: true }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          transaction: { ...transaction, state: "approved" },
          decisionRecorded: true,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          transaction: { ...transaction, state: "executing" },
          executionStarted: true,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    client.setSessionToken("browser-session");

    const created = await client.createChangeTransaction({
      operationId: "change:operation-1",
      intent: "피연산자 변경",
      riskLevel: "medium",
      plan: transaction.plan,
      approvalTtlSeconds: 900,
    });
    const approved = await client.decideChangeTransaction(
      created.transactionId,
      {
        decision: "approve",
        expectedPlanDigest: created.planDigest,
      },
    );
    const executing = await client.executeChangeTransaction(
      approved.transactionId,
      { expectedPlanDigest: approved.planDigest },
    );

    expect(created.state).toBe("pending_approval");
    expect(approved.state).toBe("approved");
    expect(executing.state).toBe("executing");
    expect(
      new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get(
        "Idempotency-Key",
      ),
    ).toBe("change:operation-1");
  });

  it("submits an exact recovery-only PID/HWND for a quarantined change", async () => {
    const transaction: ChangeTransaction = {
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
      intent: "피연산자 변경 복구",
      plan: {
        target: { program: "MAIN" },
        current: { operand: "M100" },
        proposed: { operand: "M101" },
        impact: [],
        risks: [],
        verificationScope: "offline_native_check_only",
        validation: [
          "structural_diff",
          "xg5000_program_check",
          "project_save",
        ],
        manualFollowUp: ["복구 뒤 실제 설비 상태 확인"],
        rollback: ["exact rollback"],
      },
      planDigest: "b".repeat(64),
      requestedByActorId: "local-user",
      approvalExpiresAt: "2099-01-01T00:00:00Z",
      result: {},
      createdAt: "2026-07-29T00:00:00Z",
      updatedAt: "2026-07-29T00:00:00Z",
    };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({
        transaction: { ...transaction, state: "rolled_back" },
        reconciled: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    client.setSessionToken("browser-session");

    const response = await client.reconcileChangeTransaction(
      transaction.transactionId,
      {
        expectedPlanDigest: transaction.planDigest,
        pid: 4321,
        hwnd: 8765,
      },
    );

    expect(response.reconciled).toBe(true);
    expect(response.transaction.state).toBe("rolled_back");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/change-transactions/transaction-recovery-1/reconcile",
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        expectedPlanDigest: transaction.planDigest,
        pid: 4321,
        hwnd: 8765,
      }),
    });
  });

  it("uses the Host-owned read-only LS controller endpoints", async () => {
    const observation = {
      schemaVersion: "1",
      operation: "controller.ls.status",
      endpoint: {
        host: "192.168.0.10",
        port: 2004,
        transport: "tcp",
        protocol: "XGT_FENET",
      },
      observedAt: "2026-07-29T00:00:00Z",
      readOnly: true,
      status: {
        mode: "RUN",
        stateFlags: ["RUN"],
        systemStateRaw: 1,
        padtConnectionFlag: 1,
        configurationError: 0,
        configurationWarning: 0,
      },
      provisionalIdentity: {
        vendor: "LS ELECTRIC",
        protocol: "XGT_FENET",
        cpuFamily: "XGK",
        cpuInfoCode: 1,
        cpuTypeFlag: 1,
        osVersion: "1.0",
        module: { base: 0, slot: 0 },
        fingerprintSha256: "a".repeat(64),
        evidenceStrength: "provisional",
        strongEnoughForWrite: false,
        writeAuthorizationReason: "serial and project digest are unavailable",
      },
    };
    const read = {
      schemaVersion: "1",
      operation: "controller.ls.read",
      endpoint: observation.endpoint,
      observedAt: observation.observedAt,
      dataType: "WORD",
      values: [{ variable: "%MW0", rawHex: "3412", unsignedValue: 4660 }],
      readOnly: true,
      strongEnoughForWrite: false,
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(observation))
      .mockResolvedValueOnce(jsonResponse(observation))
      .mockResolvedValueOnce(jsonResponse(read))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    client.setSessionToken("browser-session");

    await client.connectLSController({
      host: "192.168.0.10",
      port: 2004,
      timeoutSeconds: 2,
    });
    await client.getLSControllerStatus();
    await client.readLSController({
      dataType: "WORD",
      variables: ["%MW0"],
    });
    await client.disconnectLSController();

    expect(fetchMock.mock.calls.map(([input]) => input)).toEqual([
      "/api/v1/controllers/ls/connect",
      "/api/v1/controllers/ls/status",
      "/api/v1/controllers/ls/read",
      "/api/v1/controllers/ls/connection",
    ]);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        host: "192.168.0.10",
        port: 2004,
        timeoutSeconds: 2,
      }),
    });
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        dataType: "WORD",
        variables: ["%MW0"],
      }),
    });
    expect(fetchMock.mock.calls[3]?.[1]?.method).toBe("DELETE");
  });

  it("creates and reads a server-owned native project operation", async () => {
    const request = {
      operationId: "11111111-1111-4111-8111-111111111111",
      projectName: "AssemblyLine01",
      workspaceParent: "C:\\ControlStudio\\workspaces",
      plcSeries: "XGK",
      cpuModel: "XGK-CPUE",
      programName: "MAIN",
      language: "LD",
      description: "",
      xg5000Pid: 120,
      xg5000Hwnd: 1001,
      reviewedPlan: {
        conversationId: "conversation-plan-1",
        messageId: "message-plan-1",
        planDigest: "a".repeat(64),
        reviewedSectionIds: ["project-brief", "validation-plan"],
      },
    };
    const operation = {
      nativeProjectId: "native-project-1",
      operationId: request.operationId,
      state: "executing",
      requestDigest: "r".repeat(64),
      actorId: "local-user",
      planDigest: "p".repeat(64),
      destinationDirectory:
        "C:\\ControlStudio\\workspaces\\AssemblyLine01",
      request,
      createdAt: "2026-07-29T00:00:00Z",
      updatedAt: "2026-07-29T00:00:00Z",
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ operation, created: true }, 202),
      )
      .mockResolvedValueOnce(jsonResponse(operation))
      .mockResolvedValueOnce(
        jsonResponse({
          operation: { ...operation, state: "quarantined" },
          reconciled: false,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HostApiClient();
    client.setSessionToken("browser-session");

    const created = await client.createNativeProject(request);
    const restored = await client.getNativeProject(created.nativeProjectId);
    const reconciled = await client.reconcileNativeProject(
      created.nativeProjectId,
      {
        pid: 120,
        hwnd: 1001,
        expectedProjectPath:
          "C:\\ControlStudio\\workspaces\\AssemblyLine01\\AssemblyLine01.xgwx",
      },
    );

    expect(restored).toEqual(operation);
    expect(reconciled.state).toBe("quarantined");
    expect(fetchMock.mock.calls.map(([input]) => input)).toEqual([
      "/api/v1/native-projects",
      "/api/v1/native-projects/native-project-1",
      "/api/v1/native-projects/native-project-1/reconcile",
    ]);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify(request),
    });
    expect(
      new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get(
        "Idempotency-Key",
      ),
    ).toBe(request.operationId);
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        pid: 120,
        hwnd: 1001,
        expectedProjectPath:
          "C:\\ControlStudio\\workspaces\\AssemblyLine01\\AssemblyLine01.xgwx",
      }),
    });
  });
});
