import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  HostStatusResponse,
  LSControllerObservation,
} from "../api/types";
import { OnlineControllerPanel } from "./OnlineControllerPanel";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const STATUS: HostStatusResponse = {
  schemaVersion: "1",
  host: { state: "ready" },
  agent: { state: "ready" },
  mcp: { state: "ready" },
  engineeringSystem: {
    state: "unavailable",
    vendor: "ls",
    application: "XG5000",
    sessionVerified: false,
  },
  controller: {
    state: "unavailable",
    vendor: "ls",
    protocol: "XGT_FENET",
    readOnly: true,
    strongEnoughForWrite: false,
  },
  capabilities: ["online_observe"],
};

const OBSERVATION: LSControllerObservation = {
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
    evidenceStrength: "protocol_status_without_serial_or_project_digest",
    strongEnoughForWrite: false,
    writeAuthorizationReason: "serial and project digest are unavailable",
  },
};

describe("OnlineControllerPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("connects read-only and reads only the requested device values", async () => {
    const connectedStatus: HostStatusResponse = {
      ...STATUS,
      controller: {
        ...STATUS.controller,
        state: "ready",
        endpoint: OBSERVATION.endpoint,
        timeoutSeconds: 2,
        observedAt: OBSERVATION.observedAt,
        provisionalIdentity: OBSERVATION.provisionalIdentity,
      },
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(OBSERVATION))
      .mockResolvedValueOnce(jsonResponse(connectedStatus))
      .mockResolvedValueOnce(
        jsonResponse({
          schemaVersion: "1",
          operation: "controller.ls.read",
          endpoint: OBSERVATION.endpoint,
          observedAt: "2026-07-29T00:00:01Z",
          dataType: "WORD",
          values: [
            { variable: "%MW0", rawHex: "3412", unsignedValue: 4660 },
            { variable: "%MW1", rawHex: "0100", unsignedValue: 1 },
          ],
          readOnly: true,
          strongEnoughForWrite: false,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <OnlineControllerPanel
        status={STATUS}
        onClose={vi.fn()}
        onStatusChanged={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("PLC IP"), {
      target: { value: "192.168.0.10" },
    });
    fireEvent.click(screen.getByText("읽기 전용 연결"));

    expect(await screen.findByText("XGK")).toBeInTheDocument();
    expect(
      screen.getByText("임시 식별 · 쓰기 권한 근거로 사용 불가"),
    ).toBeInTheDocument();
    expect(screen.getByText("환경 미검증")).toBeInTheDocument();
    expect(
      screen.getByText(
        "FEnet 상태 응답만 확인 · 일련번호와 프로젝트 digest 없음",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/실제 설비·프로젝트 일치와 쓰기 안전성은 검증하지 않았습니다/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/실기 환경 검증 완료를 의미하지 않습니다/),
    ).toBeInTheDocument();

    fireEvent.change(
      screen.getByLabelText("디바이스 주소 · 쉼표 또는 공백으로 최대 16개"),
      { target: { value: "%MW0, %MW1" } },
    );
    fireEvent.click(screen.getByText("값 읽기"));

    expect(await screen.findByText("4660")).toBeInTheDocument();
    expect(screen.getByText("0100")).toBeInTheDocument();
    expect(
      Array.from(document.querySelectorAll("button")).some((button) =>
        /PLC 쓰기|런중 수정/.test(button.textContent ?? ""),
      ),
    ).toBe(false);
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      host: "192.168.0.10",
      port: 2004,
      timeoutSeconds: 2,
    });
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toEqual({
      dataType: "WORD",
      variables: ["%MW0", "%MW1"],
    });
  });
});
