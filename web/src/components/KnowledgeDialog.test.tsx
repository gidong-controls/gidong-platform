import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  KnowledgeDialog,
  type KnowledgeDialogService,
  type KnowledgeMemory,
} from "./KnowledgeDialog";

function createService(
  overrides: Partial<KnowledgeDialogService> = {},
): KnowledgeDialogService {
  return {
    search: vi.fn().mockResolvedValue([]),
    listDocuments: vi.fn().mockResolvedValue([]),
    importLocalDocument: vi.fn().mockResolvedValue(null),
    listMemories: vi.fn().mockResolvedValue([]),
    proposeMemory: vi.fn(),
    confirmMemory: vi.fn(),
    listControlPatterns: vi.fn().mockResolvedValue([]),
    ...overrides,
  };
}

describe("KnowledgeDialog", () => {
  it("starts with evidence search and renders a precise citation", async () => {
    const search = vi.fn().mockResolvedValue([
      {
        chunkId: "chunk-1",
        sourceId: "source-1",
        sourceTitle: "XG5000 사용자 매뉴얼",
        content: "모터 기동 허가에는 과부하 정상 조건이 포함됩니다.",
        score: 1,
        kind: "manual",
        status: "confirmed",
        scope: "vendor:ls",
        vendor: "ls",
        productFamily: "XGK",
        version: "2.5",
        authority: "vendor_official",
        citation: {
          reference: "knowledge://source-1/chunk-1?page=41",
          title: "XG5000 사용자 매뉴얼",
          page: 41,
          section: "모터 제어",
        },
      },
    ]);
    const service = createService({ search });

    render(
      <KnowledgeDialog open service={service} onClose={vi.fn()} />,
    );

    expect(
      screen.getByRole("textbox", { name: "엔지니어링 지식 검색" }),
    ).toHaveFocus();
    fireEvent.change(
      screen.getByRole("textbox", { name: "엔지니어링 지식 검색" }),
      { target: { value: "모터 과부하 인터록" } },
    );
    fireEvent.change(screen.getByLabelText("검색 벤더"), {
      target: { value: "ls" },
    });
    fireEvent.click(screen.getByRole("button", { name: "검색" }));

    expect(
      await screen.findByText("모터 기동 허가에는 과부하 정상 조건이 포함됩니다."),
    ).toBeInTheDocument();
    expect(screen.getByText("XG5000 사용자 매뉴얼 · 모터 제어 · 41쪽")).toBeInTheDocument();
    expect(
      screen.getByText("knowledge://source-1/chunk-1?page=41"),
    ).toBeInTheDocument();
    expect(screen.getByText("벤더 공식")).toBeInTheDocument();
    expect(search).toHaveBeenCalledWith({
      query: "모터 과부하 인터록",
      vendor: "ls",
      kind: undefined,
      limit: 12,
    });
  });

  it("keeps API errors visible and closes with Escape", async () => {
    const onClose = vi.fn();
    const service = createService({
      search: vi.fn().mockRejectedValue(new Error("지식 인덱스를 읽을 수 없습니다.")),
    });

    render(
      <KnowledgeDialog open service={service} onClose={onClose} />,
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "엔지니어링 지식 검색" }),
      { target: { value: "TON" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "검색" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "지식 인덱스를 읽을 수 없습니다.",
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("uses the Host native picker and sends document metadata only", async () => {
    const importLocalDocument = vi.fn().mockResolvedValue({
      sourceId: "document-1",
      title: "GX Works3 교육 매뉴얼",
      kind: "manual",
      status: "confirmed",
      vendor: "mitsubishi",
      productFamily: "MELSEC iQ-R",
      version: "1.0",
      language: "ko",
      extractionStatus: "partial",
      pageCount: 54,
      indexedPageCount: 52,
      lowTextPages: [17, 42],
    });
    const service = createService({ importLocalDocument });

    render(
      <KnowledgeDialog open service={service} onClose={vi.fn()} />,
    );
    const searchTab = screen.getByRole("tab", { name: "검색" });
    searchTab.focus();
    fireEvent.keyDown(searchTab, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "문서" })).toHaveFocus();
    await waitFor(() => expect(service.listDocuments).toHaveBeenCalled());
    expect(screen.getByText("가져온 문서가 없습니다.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("제목 · 선택"), {
      target: { value: " GX Works3 교육 매뉴얼 " },
    });
    fireEvent.change(screen.getByLabelText("벤더"), {
      target: { value: "mitsubishi" },
    });
    fireEvent.change(screen.getByLabelText("제품군 · 필수"), {
      target: { value: " MELSEC iQ-R " },
    });
    fireEvent.change(screen.getByLabelText("버전 · 필수"), {
      target: { value: " 1.0 " },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Host에서 파일 선택" }),
    );

    await waitFor(() =>
      expect(importLocalDocument).toHaveBeenCalledWith({
        title: "GX Works3 교육 매뉴얼",
        kind: "manual",
        scope: "vendor:mitsubishi",
        vendor: "mitsubishi",
        productFamily: "MELSEC iQ-R",
        version: "1.0",
        language: "ko",
      }),
    );
    expect(await screen.findByText("GX Works3 교육 매뉴얼")).toBeInTheDocument();
    expect(
      screen.getByText("Host가 파일 선택 창을 엽니다. 경로를 직접 받지 않습니다."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/일부만 추출했습니다.*17, 42/),
    ).toBeInTheDocument();
  });

  it("requires a project scope instead of leaking project documents across a vendor", async () => {
    const importLocalDocument = vi.fn().mockResolvedValue({
      sourceId: "project-document-1",
      title: "7호기 I/O 목록",
      kind: "project_document",
      status: "confirmed",
      scope: "project:sample-line-7",
      vendor: "ls",
      language: "ko",
    });
    const service = createService({ importLocalDocument });

    const { rerender } = render(
      <KnowledgeDialog open service={service} onClose={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "문서" }));
    await waitFor(() => expect(service.listDocuments).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("문서 종류"), {
      target: { value: "project_document" },
    });
    fireEvent.change(screen.getByLabelText("제목 · 선택"), {
      target: { value: "7호기 I/O 목록" },
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Host에서 파일 선택" }),
    );
    expect(importLocalDocument).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("실제 적용 범위");

    rerender(
      <KnowledgeDialog
        open
        service={service}
        onClose={vi.fn()}
        projectScopeHint="sample-line-7"
      />,
    );
    expect(screen.getByLabelText(/적용 프로젝트 · 필수/)).toHaveValue(
      "sample-line-7",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Host에서 파일 선택" }),
    );

    await waitFor(() =>
      expect(importLocalDocument).toHaveBeenCalledWith({
        title: "7호기 I/O 목록",
        kind: "project_document",
        scope: "project:sample-line-7",
        vendor: "ls",
        productFamily: undefined,
        version: undefined,
        language: "ko",
      }),
    );
  });

  it("locks project-scoped knowledge to the verified workspace id", async () => {
    const service = createService();

    render(
      <KnowledgeDialog
        open
        service={service}
        onClose={vi.fn()}
        projectScopeHint="샘플라인-001"
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "문서" }));
    await waitFor(() => expect(service.listDocuments).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("문서 종류"), {
      target: { value: "project_document" },
    });

    const projectScope = screen.getByLabelText(/적용 프로젝트 · 필수/);
    expect(projectScope).toHaveValue("샘플라인-001");
    expect(projectScope).toHaveAttribute("readonly");
    expect(
      screen.getByText(/현재 열린 작업공간의 고유 ID/),
    ).toBeInTheDocument();
  });

  it("can explicitly bind a versioned manual to the current project", async () => {
    const importLocalDocument = vi.fn().mockResolvedValue(null);
    const service = createService({ importLocalDocument });

    render(
      <KnowledgeDialog
        open
        service={service}
        onClose={vi.fn()}
        projectScopeHint="workspace-7"
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "문서" }));
    await waitFor(() => expect(service.listDocuments).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("제품군 · 필수"), {
      target: { value: "XGI" },
    });
    fireEvent.change(screen.getByLabelText("버전 · 필수"), {
      target: { value: "V3.2" },
    });
    fireEvent.change(screen.getByLabelText("매뉴얼 적용 범위"), {
      target: { value: "project" },
    });
    expect(screen.getByLabelText(/^현재 작업공간 ID · 필수/)).toHaveValue(
      "workspace-7",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Host에서 파일 선택" }),
    );

    await waitFor(() =>
      expect(importLocalDocument).toHaveBeenCalledWith({
        title: undefined,
        kind: "manual",
        scope: "project:workspace-7",
        vendor: "ls",
        productFamily: "XGI",
        version: "V3.2",
        language: "ko",
      }),
    );
  });

  it("keeps memories proposed until a separate human confirmation", async () => {
    const proposed: KnowledgeMemory = {
      sourceId: "memory-1",
      title: "7호기 모터 기동 규칙",
      content: "운전 출력 후 보조접점 피드백을 확인한다.",
      kind: "memory",
      status: "proposed",
      scope: "project",
      scopeLabel: "프로젝트 · 7호기",
      vendor: "ls",
      revision: "1",
    };
    const proposeMemory = vi.fn().mockResolvedValue(proposed);
    const confirmMemory = vi.fn().mockResolvedValue({
      ...proposed,
      status: "confirmed",
      confirmedBy: "local-user",
    });
    const service = createService({ proposeMemory, confirmMemory });

    render(
      <KnowledgeDialog
        open
        service={service}
        onClose={vi.fn()}
        projectScopeHint="workspace-7"
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "현장 기억·선호" }));
    await waitFor(() => expect(service.listMemories).toHaveBeenCalled());

    expect(
      screen.getByText(/대화에서 자동 학습하지 않습니다/),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("제목"), {
      target: { value: "7호기 모터 기동 규칙" },
    });
    expect(screen.getByLabelText(/^프로젝트/)).toHaveValue("workspace-7");
    fireEvent.change(screen.getByLabelText("관련 벤더"), {
      target: { value: "ls" },
    });
    fireEvent.change(screen.getByLabelText("내용"), {
      target: { value: "운전 출력 후 보조접점 피드백을 확인한다." },
    });
    fireEvent.click(screen.getByRole("button", { name: "제안으로 저장" }));

    expect(
      await screen.findByRole("button", {
        name: "\"7호기 모터 기동 규칙\" 기억을 사람이 확인",
      }),
    ).toBeInTheDocument();
    expect(proposeMemory).toHaveBeenCalledWith({
      title: "7호기 모터 기동 규칙",
      content: "운전 출력 후 보조접점 피드백을 확인한다.",
      kind: "memory",
      scope: "project",
      project: "workspace-7",
      vendor: "ls",
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "\"7호기 모터 기동 규칙\" 기억을 사람이 확인",
      }),
    );
    await waitFor(() =>
      expect(confirmMemory).toHaveBeenCalledWith({
        sourceId: "memory-1",
        expectedRevision: "1",
      }),
    );
    expect(await screen.findByText("확인됨")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "\"7호기 모터 기동 규칙\" 기억을 사람이 확인",
      }),
    ).not.toBeInTheDocument();
  });

  it("does not confirm a proposed memory when the API omits its content", async () => {
    const service = createService({
      listMemories: vi.fn().mockResolvedValue([
        {
          sourceId: "memory-without-preview",
          title: "확인할 수 없는 기억",
          content: "",
          kind: "memory",
          status: "proposed",
          scope: "site",
          scopeLabel: "1공장",
          vendor: "ls",
          revision: "1",
        },
      ]),
    });

    render(
      <KnowledgeDialog open service={service} onClose={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "현장 기억·선호" }));

    expect(
      await screen.findByText(
        "확인할 내용을 불러오지 못해 승인할 수 없습니다.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /기억을 사람이 확인/ }),
    ).not.toBeInTheDocument();
  });

  it("shows built-in patterns as read-only questions and a safety boundary", async () => {
    const service = createService({
      listControlPatterns: vi.fn().mockResolvedValue([
        {
          patternId: "motor-dol",
          title: "직입 기동 모터",
          intent: "명령, 허가, 피드백, 고장과 복구를 분리합니다.",
          revision: "1",
          equipmentKind: "motor",
          requiredQuestions: [
            "자동·수동에서 기동 권한은 누가 갖습니까?",
            "운전 피드백이 없을 때 제한 시간은 무엇입니까?",
          ],
          safetyBoundary:
            "비상정지, 안전 릴레이와 전기적 보호는 이 패턴으로 대체하지 않습니다.",
        },
      ]),
    });

    render(
      <KnowledgeDialog open service={service} onClose={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "제어 패턴" }));

    expect(
      (await screen.findAllByText("직입 기동 모터")).length,
    ).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByText("자동·수동에서 기동 권한은 누가 갖습니까?"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "비상정지, 안전 릴레이와 전기적 보호는 이 패턴으로 대체하지 않습니다.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("내장 · 읽기 전용")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /직입 기동 모터/ }),
    ).toHaveAttribute("aria-pressed", "true");
  });
});
