import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProgramNavigatorPanel } from "./ProgramNavigatorPanel";

const PROGRAMS = [
  {
    programId: "program-main",
    name: "MAIN",
    language: "LD",
    kind: "scan program",
  },
  {
    programId: "program-alarm",
    name: "ALARM",
    language: "LD",
  },
];

describe("ProgramNavigatorPanel", () => {
  it("lists programs and focuses the selected program in XG5000", () => {
    const onRefresh = vi.fn();
    const onFocus = vi.fn();

    render(
      <ProgramNavigatorPanel
        programs={PROGRAMS}
        loading={false}
        focusAvailable
        onRefresh={onRefresh}
        onFocus={onFocus}
      />,
    );

    expect(
      screen.getByRole("region", { name: "XG5000 프로그램" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "래더는 XG5000에서 확인합니다. 여기서는 열 위치만 선택합니다.",
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("LD · scan program")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "ALARM XG5000에서 열기" }),
    );
    expect(onFocus).toHaveBeenCalledWith("program-alarm");

    fireEvent.click(
      screen.getByRole("button", {
        name: "XG5000 프로그램 목록 새로 고침",
      }),
    );
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("shows loading, empty, and load-error states without inventing a ladder view", () => {
    const onRefresh = vi.fn();
    const onFocus = vi.fn();
    const { rerender } = render(
      <ProgramNavigatorPanel
        programs={[]}
        loading
        focusAvailable={false}
        onRefresh={onRefresh}
        onFocus={onFocus}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "XG5000에서 프로그램 목록을 불러오고 있습니다",
    );
    expect(
      screen.getByRole("button", {
        name: "XG5000 프로그램 목록 새로 고침",
      }),
    ).toBeDisabled();

    rerender(
      <ProgramNavigatorPanel
        programs={[]}
        loading={false}
        focusAvailable={false}
        onRefresh={onRefresh}
        onFocus={onFocus}
      />,
    );
    expect(screen.getByText("표시할 프로그램이 없습니다.")).toBeInTheDocument();

    rerender(
      <ProgramNavigatorPanel
        programs={[]}
        loading={false}
        focusAvailable={false}
        notice="XG5000 세션이 응답하지 않습니다."
        onRefresh={onRefresh}
        onFocus={onFocus}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("프로그램 목록을 불러오지 못했습니다.");
    expect(alert).toHaveTextContent("XG5000 세션이 응답하지 않습니다.");
    fireEvent.click(within(alert).getByRole("button", { name: "다시 시도" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("locks navigation while XG5000 is opening the requested program", () => {
    const onFocus = vi.fn();

    render(
      <ProgramNavigatorPanel
        programs={PROGRAMS}
        loading={false}
        focusAvailable
        focusBusyProgramId="program-main"
        onRefresh={vi.fn()}
        onFocus={onFocus}
      />,
    );

    const openingButton = screen.getByRole("button", {
      name: "MAIN XG5000에서 열기",
    });
    expect(openingButton).toBeDisabled();
    expect(openingButton).toHaveAttribute("aria-busy", "true");
    expect(openingButton).toHaveTextContent("여는 중…");
    expect(
      screen.getByRole("button", { name: "ALARM XG5000에서 열기" }),
    ).toBeDisabled();

    fireEvent.click(openingButton);
    expect(onFocus).not.toHaveBeenCalled();
  });

  it("keeps the read-only program list visible while focus is unverified", () => {
    const onFocus = vi.fn();

    render(
      <ProgramNavigatorPanel
        programs={PROGRAMS}
        loading={false}
        focusAvailable={false}
        focusUnavailableReason="현재 XG5000 버전에서는 위치 열기가 검증되지 않았습니다."
        onRefresh={vi.fn()}
        onFocus={onFocus}
      />,
    );

    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(
      screen.getByText(
        "현재 XG5000 버전에서는 위치 열기가 검증되지 않았습니다.",
      ),
    ).toBeInTheDocument();
    const focusButton = screen.getByRole("button", {
      name: "MAIN XG5000에서 열기",
    });
    expect(focusButton).toBeDisabled();
    fireEvent.click(focusButton);
    expect(onFocus).not.toHaveBeenCalled();
  });
});
