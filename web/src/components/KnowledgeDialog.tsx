import {
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { Icon } from "./Icon";

export type KnowledgeVendor =
  | "ls"
  | "mitsubishi"
  | "rockwell"
  | "generic";

export type KnowledgeDocumentKind =
  | "manual"
  | "standard"
  | "project_document";

export type KnowledgeMemoryKind = "memory" | "preference";

export type KnowledgeScope = "project" | "site" | "team" | "vendor";

export type KnowledgeStatus =
  | "proposed"
  | "confirmed"
  | "superseded"
  | "archived";

export interface KnowledgeCitation {
  reference: string;
  title: string;
  locator?: string | null;
  page?: number | null;
  section?: string | null;
}

export interface KnowledgeSearchHit {
  chunkId: string;
  sourceId: string;
  sourceTitle: string;
  content: string;
  score: number;
  kind:
    | KnowledgeDocumentKind
    | KnowledgeMemoryKind
    | "control_pattern";
  status: KnowledgeStatus;
  scope: string;
  vendor?: KnowledgeVendor | null;
  productFamily?: string | null;
  version?: string | null;
  authority?: string | null;
  citation: KnowledgeCitation;
}

export interface KnowledgeDocument {
  sourceId: string;
  title: string;
  kind: KnowledgeDocumentKind;
  status: KnowledgeStatus;
  scope?: string | null;
  vendor: KnowledgeVendor;
  productFamily?: string | null;
  version?: string | null;
  language?: string | null;
  locator?: string | null;
  updatedAt?: string | null;
  authority?: string | null;
  extractionStatus?: "ready" | "partial";
  pageCount?: number | null;
  indexedPageCount?: number | null;
  lowTextPages?: readonly number[];
}

export interface KnowledgeMemory {
  sourceId: string;
  title: string;
  content: string;
  kind: KnowledgeMemoryKind;
  status: KnowledgeStatus;
  scope: KnowledgeScope;
  scopeLabel?: string | null;
  vendor?: KnowledgeVendor | null;
  revision: string;
  confirmedBy?: string | null;
  updatedAt?: string | null;
}

export interface ControlPatternSummary {
  patternId: string;
  title: string;
  intent: string;
  revision: string;
  equipmentKind?: string | null;
  requiredQuestions: readonly string[];
  safetyBoundary: string;
}

export interface KnowledgeSearchRequest {
  query: string;
  vendor?: KnowledgeVendor;
  kind?:
    | KnowledgeDocumentKind
    | KnowledgeMemoryKind
    | "control_pattern";
  limit: number;
}

export interface ImportKnowledgeDocumentRequest {
  title?: string;
  kind: KnowledgeDocumentKind;
  scope: string;
  vendor: KnowledgeVendor;
  productFamily?: string;
  version?: string;
  language: string;
}

export interface ProposeKnowledgeMemoryRequest {
  title: string;
  content: string;
  kind: KnowledgeMemoryKind;
  scope: KnowledgeScope;
  project?: string;
  site?: string;
  team?: string;
  vendor?: KnowledgeVendor;
}

export interface ConfirmKnowledgeMemoryRequest {
  sourceId: string;
  expectedRevision: string;
}

export interface KnowledgeDialogService {
  search(
    request: KnowledgeSearchRequest,
  ): Promise<readonly KnowledgeSearchHit[]>;
  listDocuments(): Promise<readonly KnowledgeDocument[]>;
  importLocalDocument(
    request: ImportKnowledgeDocumentRequest,
  ): Promise<KnowledgeDocument | null>;
  listMemories(): Promise<readonly KnowledgeMemory[]>;
  proposeMemory(
    request: ProposeKnowledgeMemoryRequest,
  ): Promise<KnowledgeMemory>;
  confirmMemory(
    request: ConfirmKnowledgeMemoryRequest,
  ): Promise<KnowledgeMemory>;
  listControlPatterns(): Promise<readonly ControlPatternSummary[]>;
}

export interface KnowledgeDialogProps {
  open: boolean;
  service: KnowledgeDialogService;
  onClose: () => void;
  projectScopeHint?: string;
}

type KnowledgeTab = "search" | "documents" | "memories" | "patterns";

const TABS: readonly { id: KnowledgeTab; label: string }[] = [
  { id: "search", label: "검색" },
  { id: "documents", label: "문서" },
  { id: "memories", label: "현장 기억·선호" },
  { id: "patterns", label: "제어 패턴" },
];

const VENDOR_LABELS: Record<KnowledgeVendor, string> = {
  ls: "LS Electric",
  mitsubishi: "Mitsubishi",
  rockwell: "Rockwell",
  generic: "공통·미지정",
};

const KIND_LABELS: Record<
  KnowledgeSearchHit["kind"] | KnowledgeDocumentKind | KnowledgeMemoryKind,
  string
> = {
  manual: "매뉴얼",
  standard: "표준",
  project_document: "프로젝트 문서",
  memory: "현장 기억",
  preference: "작성 선호",
  control_pattern: "제어 패턴",
};

const STATUS_LABELS: Record<KnowledgeStatus, string> = {
  proposed: "제안됨",
  confirmed: "확인됨",
  superseded: "대체됨",
  archived: "보관됨",
};

type DocumentScopeType =
  | "global"
  | "vendor"
  | "project"
  | "site"
  | "team";

interface DocumentForm
  extends Omit<ImportKnowledgeDocumentRequest, "scope"> {
  scopeType: DocumentScopeType;
  scopeValue: string;
}

const DEFAULT_DOCUMENT_FORM: DocumentForm = {
  title: "",
  kind: "manual",
  vendor: "ls",
  productFamily: "",
  version: "",
  language: "ko",
  scopeType: "vendor",
  scopeValue: "",
};

interface MemoryForm {
  title: string;
  content: string;
  kind: KnowledgeMemoryKind;
  scope: KnowledgeScope;
  context: string;
  vendor: KnowledgeVendor;
}

const DEFAULT_MEMORY_FORM: MemoryForm = {
  title: "",
  content: "",
  kind: "memory",
  scope: "project",
  context: "",
  vendor: "generic",
};

function messageFromError(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  return "지식 저장소 작업을 완료하지 못했습니다.";
}

function citationLabel(citation: KnowledgeCitation): string {
  const location = [
    citation.section,
    citation.page ? `${citation.page}쪽` : null,
  ].filter(Boolean);
  return location.length > 0
    ? `${citation.title} · ${location.join(" · ")}`
    : citation.title;
}

function scopeContextLabel(scope: KnowledgeScope): string {
  switch (scope) {
    case "project":
      return "프로젝트";
    case "site":
      return "현장·설비";
    case "team":
      return "팀";
    case "vendor":
      return "벤더";
  }
}

function authorityLabel(authority: string): string {
  const labels: Record<string, string> = {
    vendor_official: "벤더 공식",
    organization_approved: "조직 승인",
    project_approved: "프로젝트 승인",
    user_imported_reference: "사용자 가져옴",
    "controlstudio-curated-design-guidance": "ControlStudio 검토",
  };
  if (authority.startsWith("user-proposed:")) {
    return "사용자 제안·확인";
  }
  return labels[authority] ?? authority;
}

function documentScopeLabel(scope: string): string {
  if (scope === "global") {
    return "전체 범위";
  }
  const [kind, ...parts] = scope.split(":");
  const value = parts.join(":");
  const kindLabels: Record<string, string> = {
    vendor: "벤더",
    project: "프로젝트",
    site: "현장",
    team: "팀",
  };
  const displayValue =
    kind === "vendor" && value in VENDOR_LABELS
      ? VENDOR_LABELS[value as KnowledgeVendor]
      : value;
  return `${kindLabels[kind ?? ""] ?? kind}${displayValue ? ` · ${displayValue}` : " · 이름 입력 필요"}`;
}

function buildDocumentScope(form: DocumentForm): string {
  if (form.kind === "manual") {
    return form.scopeType === "project"
      ? `project:${form.scopeValue.trim()}`
      : `vendor:${form.vendor}`;
  }
  if (form.kind === "project_document") {
    return `project:${form.scopeValue.trim()}`;
  }
  if (form.scopeType === "global") {
    return "global";
  }
  if (form.scopeType === "vendor") {
    return `vendor:${form.vendor}`;
  }
  return `${form.scopeType}:${form.scopeValue.trim()}`;
}

export function KnowledgeDialog({
  open,
  service,
  onClose,
  projectScopeHint,
}: KnowledgeDialogProps) {
  const titleId = useId();
  const searchInputRef = useRef<HTMLInputElement>(null);
  const lastProjectScopeHintRef = useRef<string | undefined>(undefined);
  const [activeTab, setActiveTab] = useState<KnowledgeTab>("search");
  const [query, setQuery] = useState("");
  const [searchVendor, setSearchVendor] = useState<KnowledgeVendor | "all">(
    "all",
  );
  const [searchKind, setSearchKind] = useState<
    KnowledgeSearchRequest["kind"] | "all"
  >("all");
  const [searchResults, setSearchResults] = useState<
    readonly KnowledgeSearchHit[]
  >([]);
  const [searchAttempted, setSearchAttempted] = useState(false);
  const [documents, setDocuments] = useState<readonly KnowledgeDocument[]>([]);
  const [memories, setMemories] = useState<readonly KnowledgeMemory[]>([]);
  const [patterns, setPatterns] = useState<
    readonly ControlPatternSummary[]
  >([]);
  const [selectedPatternId, setSelectedPatternId] = useState<string | null>(
    null,
  );
  const [documentForm, setDocumentForm] = useState(DEFAULT_DOCUMENT_FORM);
  const [memoryForm, setMemoryForm] = useState(DEFAULT_MEMORY_FORM);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeTone, setNoticeTone] = useState<"success" | "warning">(
    "success",
  );

  useEffect(() => {
    if (!open) {
      return;
    }
    setActiveTab("search");
    setError(null);
    setNotice(null);
    setNoticeTone("success");
    searchInputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    const hint = projectScopeHint?.trim();
    if (!open || !hint) {
      return;
    }
    const previousHint = lastProjectScopeHintRef.current;
    setDocumentForm((current) =>
      (current.kind === "project_document" ||
        (current.kind === "manual" &&
          current.scopeType === "project") ||
        (current.kind === "standard" &&
          current.scopeType === "project")) &&
      (!current.scopeValue.trim() || current.scopeValue === previousHint)
        ? { ...current, scopeValue: hint }
        : current,
    );
    setMemoryForm((current) =>
      current.scope === "project" &&
      (!current.context.trim() || current.context === previousHint)
        ? { ...current, context: hint }
        : current,
    );
    lastProjectScopeHintRef.current = hint;
  }, [open, projectScopeHint]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  useEffect(() => {
    if (!open || activeTab === "search") {
      return;
    }

    let active = true;
    setError(null);
    setNotice(null);
    setNoticeTone("success");

    const load = async () => {
      setBusy(`load-${activeTab}`);
      try {
        if (activeTab === "documents") {
          const loaded = await service.listDocuments();
          if (active) {
            setDocuments(loaded);
          }
        } else if (activeTab === "memories") {
          const loaded = await service.listMemories();
          if (active) {
            setMemories(loaded);
          }
        } else {
          const loaded = await service.listControlPatterns();
          if (active) {
            setPatterns(loaded);
            setSelectedPatternId(
              (current) => current ?? loaded[0]?.patternId ?? null,
            );
          }
        }
      } catch (loadError) {
        if (active) {
          setError(messageFromError(loadError));
        }
      } finally {
        if (active) {
          setBusy(null);
        }
      }
    };

    void load();
    return () => {
      active = false;
    };
  }, [activeTab, open, service]);

  if (!open) {
    return null;
  }

  const selectedPattern =
    patterns.find((pattern) => pattern.patternId === selectedPatternId) ??
    patterns[0];

  const changeTab = (tab: KnowledgeTab) => {
    setActiveTab(tab);
    setError(null);
    setNotice(null);
  };

  const handleTabKeyDown = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    let targetIndex: number | null = null;
    if (event.key === "ArrowRight") {
      targetIndex = (index + 1) % TABS.length;
    } else if (event.key === "ArrowLeft") {
      targetIndex = (index - 1 + TABS.length) % TABS.length;
    } else if (event.key === "Home") {
      targetIndex = 0;
    } else if (event.key === "End") {
      targetIndex = TABS.length - 1;
    }
    if (targetIndex === null) {
      return;
    }

    event.preventDefault();
    const targetTab = TABS[targetIndex];
    if (!targetTab) {
      return;
    }
    changeTab(targetTab.id);
    event.currentTarget.parentElement
      ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
      .item(targetIndex)
      .focus();
  };

  const submitSearch = async (event: FormEvent) => {
    event.preventDefault();
    const normalizedQuery = query.trim();
    if (!normalizedQuery) {
      setError("찾을 장치, 명령, 증상 또는 제어 의도를 입력하세요.");
      return;
    }

    setBusy("search");
    setError(null);
    setNotice(null);
    try {
      const results = await service.search({
        query: normalizedQuery,
        vendor: searchVendor === "all" ? undefined : searchVendor,
        kind: searchKind === "all" ? undefined : searchKind,
        limit: 12,
      });
      setSearchResults(results);
      setSearchAttempted(true);
    } catch (searchError) {
      setError(messageFromError(searchError));
    } finally {
      setBusy(null);
    }
  };

  const importDocument = async (event: FormEvent) => {
    event.preventDefault();
    const scopeValue = documentForm.scopeValue.trim();
    if (
      documentForm.kind === "manual" &&
      (!documentForm.productFamily?.trim() || !documentForm.version?.trim())
    ) {
      setError("벤더 매뉴얼은 적용할 제품군과 문서 버전을 입력해야 합니다.");
      return;
    }
    if (
      (documentForm.kind === "project_document" ||
        (documentForm.kind === "manual" &&
          documentForm.scopeType === "project") ||
        (documentForm.kind === "standard" &&
          !["global", "vendor"].includes(documentForm.scopeType))) &&
      !scopeValue
    ) {
      setError("프로젝트·현장·팀 문서는 실제 적용 범위를 입력해야 합니다.");
      return;
    }

    const scope = buildDocumentScope(documentForm);
    setBusy("document-import");
    setError(null);
    setNotice(null);
    try {
      const imported = await service.importLocalDocument({
        title: documentForm.title?.trim() || undefined,
        kind: documentForm.kind,
        scope,
        vendor: documentForm.vendor,
        productFamily: documentForm.productFamily?.trim() || undefined,
        version: documentForm.version?.trim() || undefined,
        language: documentForm.language.trim(),
      });
      if (!imported) {
        setNotice("파일 선택을 취소했습니다.");
        setNoticeTone("success");
        return;
      }
      setDocuments((current) => [
        imported,
        ...current.filter((item) => item.sourceId !== imported.sourceId),
      ]);
      setDocumentForm((current) => ({ ...current, title: "" }));
      if (imported.extractionStatus === "partial") {
        const lowTextDetail =
          imported.lowTextPages && imported.lowTextPages.length > 0
            ? ` 텍스트가 부족한 쪽: ${imported.lowTextPages.join(", ")}.`
            : "";
        setNotice(
          `"${imported.title}" 문서를 일부만 추출했습니다.${lowTextDetail} 원본과 인덱싱 범위를 확인하세요.`,
        );
        setNoticeTone("warning");
      } else {
        setNotice(`"${imported.title}" 문서를 로컬 지식에 추가했습니다.`);
        setNoticeTone("success");
      }
    } catch (importError) {
      setError(messageFromError(importError));
    } finally {
      setBusy(null);
    }
  };

  const proposeMemory = async (event: FormEvent) => {
    event.preventDefault();
    const title = memoryForm.title.trim();
    const content = memoryForm.content.trim();
    const context = memoryForm.context.trim();
    if (!title || !content || (memoryForm.scope !== "vendor" && !context)) {
      setError("제목, 내용, 적용 범위를 모두 입력하세요.");
      return;
    }

    const request: ProposeKnowledgeMemoryRequest = {
      title,
      content,
      kind: memoryForm.kind,
      scope: memoryForm.scope,
      vendor: memoryForm.vendor,
    };
    if (memoryForm.scope === "project") {
      request.project = context;
    } else if (memoryForm.scope === "site") {
      request.site = context;
    } else if (memoryForm.scope === "team") {
      request.team = context;
    }

    setBusy("memory-propose");
    setError(null);
    setNotice(null);
    try {
      const proposed = await service.proposeMemory(request);
      setMemories((current) => [
        proposed,
        ...current.filter((item) => item.sourceId !== proposed.sourceId),
      ]);
      setMemoryForm((current) => ({
        ...current,
        title: "",
        content: "",
      }));
      setNotice("기억을 제안 상태로 저장했습니다. 사용 전 사람이 확인해야 합니다.");
      setNoticeTone("success");
    } catch (proposeError) {
      setError(messageFromError(proposeError));
    } finally {
      setBusy(null);
    }
  };

  const confirmMemory = async (memory: KnowledgeMemory) => {
    setBusy(`memory-confirm-${memory.sourceId}`);
    setError(null);
    setNotice(null);
    try {
      const confirmed = await service.confirmMemory({
        sourceId: memory.sourceId,
        expectedRevision: memory.revision,
      });
      setMemories((current) =>
        current.map((item) =>
          item.sourceId === confirmed.sourceId ? confirmed : item,
        ),
      );
      setNotice(`"${confirmed.title}" 기억을 사람이 확인했습니다.`);
      setNoticeTone("success");
    } catch (confirmError) {
      setError(messageFromError(confirmError));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="knowledge-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="knowledge-dialog__header">
          <div>
            <p className="eyebrow">ENGINEERING KNOWLEDGE</p>
            <h2 id={titleId}>엔지니어링 지식</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label="엔지니어링 지식 닫기"
          >
            <Icon name="close" />
          </button>
        </header>

        <nav className="knowledge-tabs" role="tablist" aria-label="지식 보기">
          {TABS.map((tab, index) => (
            <button
              key={tab.id}
              id={`knowledge-tab-${tab.id}`}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={`knowledge-panel-${tab.id}`}
              onClick={() => changeTab(tab.id)}
              onKeyDown={(event) => handleTabKeyDown(event, index)}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        <div className="knowledge-dialog__body">
          {activeTab === "search" ? (
            <section
              id="knowledge-panel-search"
              role="tabpanel"
              aria-labelledby="knowledge-tab-search"
              className="knowledge-search-panel"
            >
              <form className="knowledge-search-form" onSubmit={submitSearch}>
                <label className="knowledge-search-input">
                  <span className="sr-only">엔지니어링 지식 검색</span>
                  <Icon name="evidence" size={17} />
                  <input
                    ref={searchInputRef}
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="장치, 명령, 증상 또는 제어 의도 검색"
                    autoComplete="off"
                  />
                </label>
                <select
                  aria-label="검색 벤더"
                  value={searchVendor}
                  onChange={(event) =>
                    setSearchVendor(
                      event.target.value as KnowledgeVendor | "all",
                    )
                  }
                >
                  <option value="all">모든 벤더</option>
                  {Object.entries(VENDOR_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="검색 지식 종류"
                  value={searchKind}
                  onChange={(event) =>
                    setSearchKind(
                      event.target.value as
                        | KnowledgeSearchRequest["kind"]
                        | "all",
                    )
                  }
                >
                  <option value="all">모든 근거</option>
                  {Object.entries(KIND_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <button
                  className="button primary-button"
                  type="submit"
                  disabled={busy === "search"}
                >
                  {busy === "search" ? "검색 중…" : "검색"}
                </button>
              </form>
              <p className="knowledge-search-note">
                확인된 문서·기억과 검토된 제어 패턴만 근거로 검색합니다.
              </p>

              <div
                className="knowledge-result-list"
                aria-live="polite"
                aria-busy={busy === "search"}
              >
                {searchResults.map((result) => (
                  <article
                    key={result.chunkId}
                    className="knowledge-result-card"
                  >
                    <header>
                      <div>
                        <span className="knowledge-kind">
                          {KIND_LABELS[result.kind]}
                        </span>
                        {result.vendor ? (
                          <span>{VENDOR_LABELS[result.vendor]}</span>
                        ) : null}
                        {result.productFamily ? (
                          <span>{result.productFamily}</span>
                        ) : null}
                        {result.version ? <span>v{result.version}</span> : null}
                        {result.authority ? (
                          <span>{authorityLabel(result.authority)}</span>
                        ) : null}
                      </div>
                      <strong>{result.sourceTitle}</strong>
                    </header>
                    <p>{result.content}</p>
                    <footer>
                      <Icon name="link" size={13} />
                      <span>{citationLabel(result.citation)}</span>
                      <code>{result.citation.reference}</code>
                    </footer>
                  </article>
                ))}
                {searchAttempted &&
                busy !== "search" &&
                searchResults.length === 0 ? (
                  <div className="knowledge-empty">
                    <strong>확인된 근거를 찾지 못했습니다.</strong>
                    <span>
                      벤더·종류 필터를 넓히거나 문서를 먼저 추가하세요.
                    </span>
                  </div>
                ) : null}
                {!searchAttempted ? (
                  <div className="knowledge-search-start">
                    <strong>근거부터 찾습니다.</strong>
                    <span>
                      예: M0320A, 모터 기동 인터록, TON 완료 조건
                    </span>
                  </div>
                ) : null}
              </div>
            </section>
          ) : null}

          {activeTab === "documents" ? (
            <section
              id="knowledge-panel-documents"
              role="tabpanel"
              aria-labelledby="knowledge-tab-documents"
              className="knowledge-management-panel"
            >
              <form
                className="knowledge-compact-form"
                onSubmit={importDocument}
                noValidate
              >
                <div className="knowledge-section-heading">
                  <div>
                    <h3>로컬 문서 가져오기</h3>
                    <p>
                      Host가 PDF·Markdown·텍스트와 CSV/TSV I/O 목록을 가져옵니다.
                      표 파일은 행·열 이름을 보존해 설계 근거로 사용합니다.
                    </p>
                  </div>
                  <button
                    className="button primary-button"
                    type="submit"
                    disabled={busy === "document-import"}
                  >
                    {busy === "document-import"
                      ? "가져오는 중…"
                      : "Host에서 파일 선택"}
                  </button>
                </div>
                <div className="knowledge-form-grid">
                  <label>
                    <span>제목 · 선택</span>
                    <input
                      value={documentForm.title}
                      onChange={(event) =>
                        setDocumentForm((current) => ({
                          ...current,
                          title: event.target.value,
                        }))
                      }
                      placeholder="비우면 파일명 사용"
                    />
                  </label>
                  <label>
                    <span>문서 종류</span>
                    <select
                      value={documentForm.kind}
                      onChange={(event) =>
                        setDocumentForm((current) => {
                          const kind = event.target
                            .value as KnowledgeDocumentKind;
                          return {
                            ...current,
                            kind,
                            scopeType:
                              kind === "manual"
                                ? "vendor"
                                : kind === "project_document"
                                  ? "project"
                                  : "global",
                            vendor:
                              kind === "standard" ? "generic" : current.vendor,
                            scopeValue:
                              kind === "project_document"
                                ? projectScopeHint?.trim() ?? ""
                                : "",
                          };
                        })
                      }
                    >
                      <option value="manual">매뉴얼</option>
                      <option value="standard">회사·현장 표준</option>
                      <option value="project_document">프로젝트 문서</option>
                    </select>
                  </label>
                  <label>
                    <span>벤더</span>
                    <select
                      value={documentForm.vendor}
                      onChange={(event) =>
                        setDocumentForm((current) => ({
                          ...current,
                          vendor: event.target.value as KnowledgeVendor,
                        }))
                      }
                    >
                      {Object.entries(VENDOR_LABELS).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>
                      제품군
                      {documentForm.kind === "manual" ? " · 필수" : ""}
                    </span>
                    <input
                      value={documentForm.productFamily}
                      onChange={(event) =>
                        setDocumentForm((current) => ({
                          ...current,
                          productFamily: event.target.value,
                        }))
                      }
                      placeholder="예: XGK, MELSEC iQ-R"
                      required={documentForm.kind === "manual"}
                    />
                  </label>
                  <label>
                    <span>
                      버전
                      {documentForm.kind === "manual" ? " · 필수" : ""}
                    </span>
                    <input
                      value={documentForm.version}
                      onChange={(event) =>
                        setDocumentForm((current) => ({
                          ...current,
                          version: event.target.value,
                        }))
                      }
                      placeholder="예: 2.5"
                      required={documentForm.kind === "manual"}
                    />
                  </label>
                  <label>
                    <span>언어</span>
                    <select
                      value={documentForm.language}
                      onChange={(event) =>
                        setDocumentForm((current) => ({
                          ...current,
                          language: event.target.value,
                        }))
                      }
                    >
                      <option value="ko">한국어</option>
                      <option value="en">English</option>
                      <option value="ja">日本語</option>
                    </select>
                  </label>
                  {documentForm.kind === "project_document" ? (
                    <label className="knowledge-wide-field">
                      <span>적용 프로젝트 · 필수</span>
                      <input
                        value={documentForm.scopeValue}
                        readOnly
                        placeholder="먼저 기존 프로젝트를 여세요"
                        required
                      />
                      <small className="knowledge-field-hint">
                        현재 열린 작업공간의 고유 ID에 연결해야 Agent가 자동
                        범위로 사용합니다.
                      </small>
                    </label>
                  ) : null}
                  {documentForm.kind === "manual" ? (
                    <>
                      <label>
                        <span>매뉴얼 적용 범위</span>
                        <select
                          value={documentForm.scopeType}
                          onChange={(event) =>
                            setDocumentForm((current) => ({
                              ...current,
                              scopeType: event.target
                                .value as DocumentScopeType,
                              scopeValue:
                                event.target.value === "project"
                                  ? projectScopeHint?.trim() ?? ""
                                  : "",
                            }))
                          }
                        >
                          <option value="vendor">벤더 공통 참고</option>
                          <option value="project">
                            현재 프로젝트에 명시적으로 연결
                          </option>
                        </select>
                      </label>
                      {documentForm.scopeType === "project" ? (
                        <label>
                          <span>현재 작업공간 ID · 필수</span>
                          <input
                            value={documentForm.scopeValue}
                            readOnly
                            placeholder="먼저 기존 프로젝트를 여세요"
                            required
                          />
                          <small className="knowledge-field-hint">
                            이 문서는 현재 프로젝트의 참고 자료로만 자동
                            검색됩니다. 제품군·버전 표시는 그대로 유지됩니다.
                          </small>
                        </label>
                      ) : null}
                    </>
                  ) : null}
                  {documentForm.kind === "standard" ? (
                    <>
                      <label>
                        <span>표준 적용 범위</span>
                        <select
                          value={documentForm.scopeType}
                          onChange={(event) =>
                            setDocumentForm((current) => ({
                              ...current,
                              scopeType: event.target
                                .value as DocumentScopeType,
                              vendor:
                                event.target.value === "global"
                                  ? "generic"
                                  : current.vendor,
                              scopeValue:
                                event.target.value === "project"
                                  ? projectScopeHint?.trim() ?? ""
                                  : "",
                            }))
                          }
                        >
                          <option value="global">전체 공통</option>
                          <option value="vendor">선택한 벤더</option>
                          <option value="site">현장·설비</option>
                          <option value="team">팀</option>
                          <option value="project">프로젝트</option>
                        </select>
                      </label>
                      {!["global", "vendor"].includes(
                        documentForm.scopeType,
                      ) ? (
                        <label>
                          <span>범위 이름 · 필수</span>
                          <input
                            value={documentForm.scopeValue}
                            onChange={(event) =>
                              setDocumentForm((current) => ({
                                ...current,
                                scopeValue: event.target.value,
                              }))
                            }
                            readOnly={
                              documentForm.scopeType === "project"
                            }
                            placeholder="현장, 팀 또는 프로젝트 이름"
                            required
                          />
                        </label>
                      ) : null}
                    </>
                  ) : null}
                  <div className="knowledge-document-scope-preview">
                    <Icon name="shield" size={14} />
                    <span>저장 범위</span>
                    <strong>
                      {documentScopeLabel(buildDocumentScope(documentForm))}
                    </strong>
                  </div>
                </div>
              </form>

              <div
                className="knowledge-source-list"
                aria-busy={busy === "load-documents"}
              >
                <h3>가져온 문서</h3>
                {documents.map((document) => (
                  <article key={document.sourceId}>
                    <Icon name="file" size={17} />
                    <div>
                      <strong>{document.title}</strong>
                      <span>
                        {KIND_LABELS[document.kind]} ·{" "}
                        {VENDOR_LABELS[document.vendor]}
                        {document.productFamily
                          ? ` · ${document.productFamily}`
                          : ""}
                        {document.version ? ` · v${document.version}` : ""}
                        {document.scope
                          ? ` · ${documentScopeLabel(document.scope)}`
                          : ""}
                        {document.authority
                          ? ` · ${authorityLabel(document.authority)}`
                          : ""}
                      </span>
                    </div>
                    <span
                      className={`knowledge-status knowledge-status--${document.status}`}
                    >
                      {STATUS_LABELS[document.status]}
                    </span>
                  </article>
                ))}
                {busy !== "load-documents" && documents.length === 0 ? (
                  <p className="knowledge-list-empty">가져온 문서가 없습니다.</p>
                ) : null}
                {busy === "load-documents" ? (
                  <p className="knowledge-list-loading" role="status">
                    문서 목록을 불러오는 중…
                  </p>
                ) : null}
              </div>
            </section>
          ) : null}

          {activeTab === "memories" ? (
            <section
              id="knowledge-panel-memories"
              role="tabpanel"
              aria-labelledby="knowledge-tab-memories"
              className="knowledge-management-panel"
            >
              <aside className="knowledge-learning-boundary">
                <Icon name="shield" size={17} />
                <span>
                  대화에서 자동 학습하지 않습니다. 모든 기억·선호는 제안으로
                  저장되고, 사람이 확인한 뒤에만 검색 근거가 됩니다. 현장·팀
                  범위는 현재 수동 검색·기록용이며 Agent가 자동 적용하지
                  않습니다.
                </span>
              </aside>
              <form
                className="knowledge-compact-form"
                onSubmit={proposeMemory}
                noValidate
              >
                <div className="knowledge-section-heading">
                  <div>
                    <h3>기억 제안</h3>
                    <p>검증된 현장 규칙과 작성 선호만 짧고 명확하게 기록하세요.</p>
                  </div>
                  <button
                    className="button primary-button"
                    type="submit"
                    disabled={busy === "memory-propose"}
                  >
                    {busy === "memory-propose" ? "저장 중…" : "제안으로 저장"}
                  </button>
                </div>
                <div className="knowledge-form-grid">
                  <label>
                    <span>종류</span>
                    <select
                      value={memoryForm.kind}
                      onChange={(event) =>
                        setMemoryForm((current) => ({
                          ...current,
                          kind: event.target.value as KnowledgeMemoryKind,
                        }))
                      }
                    >
                      <option value="memory">현장 기억</option>
                      <option value="preference">래더 작성 선호</option>
                    </select>
                  </label>
                  <label>
                    <span>제목</span>
                    <input
                      value={memoryForm.title}
                      onChange={(event) =>
                        setMemoryForm((current) => ({
                          ...current,
                          title: event.target.value,
                        }))
                      }
                      placeholder="예: 7호기 모터 기동 확인 규칙"
                      required
                    />
                  </label>
                  <label>
                    <span>적용 범위</span>
                    <select
                      value={memoryForm.scope}
                      onChange={(event) =>
                        setMemoryForm((current) => ({
                          ...current,
                          scope: event.target.value as KnowledgeScope,
                          context:
                            event.target.value === "project"
                              ? projectScopeHint?.trim() ?? ""
                              : "",
                        }))
                      }
                    >
                      <option value="project">프로젝트</option>
                      <option value="site">현장·설비</option>
                      <option value="team">팀</option>
                      <option value="vendor">벤더</option>
                    </select>
                  </label>
                  {memoryForm.scope === "vendor" ? (
                    <label>
                      <span>벤더</span>
                      <select
                        value={memoryForm.vendor}
                        onChange={(event) =>
                          setMemoryForm((current) => ({
                            ...current,
                            vendor: event.target.value as KnowledgeVendor,
                          }))
                        }
                      >
                        {Object.entries(VENDOR_LABELS).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <>
                      <label>
                        <span>{scopeContextLabel(memoryForm.scope)}</span>
                        <input
                          value={memoryForm.context}
                          onChange={(event) =>
                            setMemoryForm((current) => ({
                              ...current,
                              context: event.target.value,
                            }))
                          }
                          readOnly={memoryForm.scope === "project"}
                          placeholder={`${scopeContextLabel(memoryForm.scope)} 이름`}
                          required
                        />
                        {memoryForm.scope === "project" ? (
                          <small className="knowledge-field-hint">
                            현재 열린 작업공간의 고유 ID에 연결된 기억만 자동
                            적용됩니다.
                          </small>
                        ) : (
                          <small className="knowledge-field-hint">
                            현장·팀 범위는 현재 Agent 자동 검색에 포함되지
                            않습니다.
                          </small>
                        )}
                      </label>
                      <label>
                        <span>관련 벤더</span>
                        <select
                          value={memoryForm.vendor}
                          onChange={(event) =>
                            setMemoryForm((current) => ({
                              ...current,
                              vendor: event.target.value as KnowledgeVendor,
                            }))
                          }
                        >
                          {Object.entries(VENDOR_LABELS).map(
                            ([value, label]) => (
                              <option key={value} value={value}>
                                {label}
                              </option>
                            ),
                          )}
                        </select>
                      </label>
                    </>
                  )}
                  <label className="knowledge-wide-field">
                    <span>내용</span>
                    <textarea
                      value={memoryForm.content}
                      onChange={(event) =>
                        setMemoryForm((current) => ({
                          ...current,
                          content: event.target.value,
                        }))
                      }
                      placeholder="언제 적용하는지, 무엇을 의미하는지, 예외가 무엇인지 기록"
                      rows={3}
                      required
                    />
                  </label>
                </div>
              </form>

              <div
                className="knowledge-source-list"
                aria-busy={busy === "load-memories"}
              >
                <h3>기억·선호</h3>
                {memories.map((memory) => (
                  <article key={memory.sourceId}>
                    <Icon
                      name={memory.kind === "preference" ? "settings" : "history"}
                      size={17}
                    />
                    <div>
                      <strong>{memory.title}</strong>
                      <span>
                        {KIND_LABELS[memory.kind]} ·{" "}
                        {memory.scopeLabel ?? scopeContextLabel(memory.scope)}
                      </span>
                      {memory.content ? (
                        <p>{memory.content}</p>
                      ) : memory.status === "proposed" ? (
                        <p className="knowledge-memory-missing-content">
                          확인할 내용을 불러오지 못해 승인할 수 없습니다.
                        </p>
                      ) : null}
                    </div>
                    <div className="knowledge-memory-action">
                      <span
                        className={`knowledge-status knowledge-status--${memory.status}`}
                      >
                        {STATUS_LABELS[memory.status]}
                      </span>
                      {memory.status === "proposed" && memory.content ? (
                        <button
                          className="button secondary-button"
                          type="button"
                          disabled={busy === `memory-confirm-${memory.sourceId}`}
                          onClick={() => void confirmMemory(memory)}
                          aria-label={`"${memory.title}" 기억을 사람이 확인`}
                        >
                          {busy === `memory-confirm-${memory.sourceId}`
                            ? "확인 중…"
                            : "사람이 확인"}
                        </button>
                      ) : null}
                    </div>
                  </article>
                ))}
                {busy !== "load-memories" && memories.length === 0 ? (
                  <p className="knowledge-list-empty">
                    저장된 기억이나 선호가 없습니다.
                  </p>
                ) : null}
                {busy === "load-memories" ? (
                  <p className="knowledge-list-loading" role="status">
                    기억 목록을 불러오는 중…
                  </p>
                ) : null}
              </div>
            </section>
          ) : null}

          {activeTab === "patterns" ? (
            <section
              id="knowledge-panel-patterns"
              role="tabpanel"
              aria-labelledby="knowledge-tab-patterns"
              className="knowledge-pattern-panel"
            >
              <header className="knowledge-pattern-heading">
                <div>
                  <h3>검토된 제어 패턴</h3>
                  <p>코드를 복사하는 템플릿이 아니라 설계 누락을 막는 체크리스트입니다.</p>
                </div>
                <span>내장 · 읽기 전용</span>
              </header>
              <div className="knowledge-pattern-layout">
                <div className="knowledge-pattern-list" role="list">
                  {patterns.map((pattern) => (
                    <button
                      key={pattern.patternId}
                      type="button"
                      className={
                        selectedPattern?.patternId === pattern.patternId
                          ? "is-selected"
                          : undefined
                      }
                      onClick={() => setSelectedPatternId(pattern.patternId)}
                      aria-pressed={
                        selectedPattern?.patternId === pattern.patternId
                      }
                    >
                      <strong>{pattern.title}</strong>
                      <span>{pattern.intent}</span>
                    </button>
                  ))}
                  {busy !== "load-patterns" && patterns.length === 0 ? (
                    <p className="knowledge-list-empty">
                      등록된 제어 패턴이 없습니다.
                    </p>
                  ) : null}
                  {busy === "load-patterns" ? (
                    <p className="knowledge-list-loading" role="status">
                      제어 패턴을 불러오는 중…
                    </p>
                  ) : null}
                </div>
                {selectedPattern ? (
                  <article className="knowledge-pattern-detail">
                    <header>
                      <div>
                        <span>
                          {selectedPattern.equipmentKind ?? "공통 제어"} · rev{" "}
                          {selectedPattern.revision}
                        </span>
                        <h4>{selectedPattern.title}</h4>
                      </div>
                      <Icon name="shield" size={20} />
                    </header>
                    <p>{selectedPattern.intent}</p>
                    <section>
                      <h5>반드시 먼저 답할 질문</h5>
                      <ol>
                        {selectedPattern.requiredQuestions.map((question) => (
                          <li key={question}>{question}</li>
                        ))}
                      </ol>
                    </section>
                    <section className="knowledge-safety-boundary">
                      <h5>안전 경계</h5>
                      <p>{selectedPattern.safetyBoundary}</p>
                    </section>
                  </article>
                ) : null}
              </div>
            </section>
          ) : null}
        </div>

        {error || notice ? (
          <div
            className={
              error
                ? "knowledge-dialog__feedback is-error"
                : noticeTone === "warning"
                  ? "knowledge-dialog__feedback is-warning"
                  : "knowledge-dialog__feedback"
            }
            role={error ? "alert" : "status"}
          >
            {error ?? notice}
          </div>
        ) : null}
      </section>
    </div>
  );
}
