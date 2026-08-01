import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NewProjectBriefPanel } from "./NewProjectBriefPanel";

describe("NewProjectBriefPanel", () => {
  it("requires an explicit safety or interlock statement before planning", () => {
    const submit = vi.fn();

    render(
      <NewProjectBriefPanel
        busy={false}
        onCancel={vi.fn()}
        onSubmit={submit}
      />,
    );

    fireEvent.change(screen.getByLabelText("프로젝트 이름"), {
      target: { value: "  포장 설비  " },
    });
    fireEvent.change(
      screen.getByLabelText(/이 설비가 무엇을 해야 하나요\?/),
      {
        target: { value: "  제품을 검사하고 포장한다  " },
      },
    );

    const safety = screen.getByLabelText("안전·인터록·운영 제약");
    const submitButton = screen.getByRole("button", {
      name: "계획서 초안 만들기",
    });
    expect(safety).toBeRequired();
    expect(submitButton).toBeDisabled();

    fireEvent.blur(safety);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "계획을 시작하려면 안전 조건이나 현재 확인되지 않은 위험",
    );

    fireEvent.change(safety, {
      target: { value: "  도어 개방 시 축을 정지한다  " },
    });
    fireEvent.click(screen.getByText("알고 있는 정보 추가"));
    fireEvent.change(screen.getByLabelText("운전·초기화·복구 모드"), {
      target: { value: "  자동·수동·보전, 재기동은 작업자 확인  " },
    });
    fireEvent.change(screen.getByLabelText("성능·품질·인수 기준"), {
      target: { value: "  사이클 6초, 고장·복구 시험 통과  " },
    });
    expect(submitButton).toBeEnabled();
    fireEvent.click(submitButton);

    expect(submit).toHaveBeenCalledWith({
      projectName: "포장 설비",
      machinePurpose: "제품을 검사하고 포장한다",
      sequence: "",
      hardwareAndIo: "",
      safetyAndInterlocks: "도어 개방 시 축을 정지한다",
      operatingModes: "자동·수동·보전, 재기동은 작업자 확인",
      performanceAndAcceptance: "사이클 6초, 고장·복구 시험 통과",
    });
  });
});
