import type { NewProjectBrief } from "./components/NewProjectBriefPanel";

// Public UI draft metadata only. Never a native reservation/session authority.
export const INTAKE_DRAFT_STORAGE_KEY = "controlstudio.projectIntakeDrafts.v1";
type DraftStorage = Pick<Storage, "getItem" | "setItem">;
export interface ProjectIntakeDraft {
  scopeKey: string;
  context: string;
  brief: NewProjectBrief;
  updatedAt: string;
}
interface DraftCatalog {
  version: 1;
  active: Record<string, string>;
  drafts: ProjectIntakeDraft[];
}
const fields = ["projectName", "machinePurpose", "sequence", "hardwareAndIo",
  "safetyAndInterlocks", "operatingModes", "performanceAndAcceptance"] as const;
export function blankIntakeBrief(): NewProjectBrief {
  return { projectName: "", machinePurpose: "", sequence: "", hardwareAndIo: "",
    safetyAndInterlocks: "", operatingModes: "", performanceAndAcceptance: "", attachmentSourceIds: [] };
}
function readCatalog(storage: DraftStorage): DraftCatalog {
  const raw = storage.getItem(INTAKE_DRAFT_STORAGE_KEY);
  if (!raw) return { version: 1, active: {}, drafts: [] };
  const value = JSON.parse(raw) as DraftCatalog;
  if (value?.version !== 1 || !value.active || typeof value.active !== "object" ||
      Array.isArray(value.active) || !Array.isArray(value.drafts) ||
      value.drafts.some((draft) => !draft || !/^intake-[0-9a-f-]{36}$/.test(draft.scopeKey) ||
        typeof draft.context !== "string" || typeof draft.updatedAt !== "string" ||
        !draft.brief || fields.some((key) => typeof draft.brief[key] !== "string") ||
        !Array.isArray(draft.brief.attachmentSourceIds) ||
        draft.brief.attachmentSourceIds.some((id) => typeof id !== "string"))) {
    throw new Error("저장된 신규 작성 초안을 읽지 못했습니다.");
  }
  return value;
}
export function listIntakeDrafts(storage: DraftStorage, context: string): ProjectIntakeDraft[] {
  return readCatalog(storage).drafts.filter((draft) => draft.context === context);
}
export function selectIntakeDraft(storage: DraftStorage, context: string, scopeKey: string): ProjectIntakeDraft {
  const catalog = readCatalog(storage);
  const draft = catalog.drafts.find((item) => item.scopeKey === scopeKey && item.context === context);
  if (!draft) throw new Error("선택한 신규 작성 초안이 없습니다.");
  catalog.active[context] = scopeKey;
  storage.setItem(INTAKE_DRAFT_STORAGE_KEY, JSON.stringify(catalog));
  return draft;
}
export function createIntakeDraft(storage: DraftStorage, context: string,
  brief: NewProjectBrief = blankIntakeBrief()): ProjectIntakeDraft {
  const catalog = readCatalog(storage);
  const draft = { scopeKey: `intake-${crypto.randomUUID()}`, context,
    brief: structuredClone(brief), updatedAt: new Date().toISOString() };
  catalog.drafts.push(draft);
  catalog.active[context] = draft.scopeKey;
  storage.setItem(INTAKE_DRAFT_STORAGE_KEY, JSON.stringify(catalog));
  return draft;
}
export function restoreIntakeDraft(storage: DraftStorage, context: string,
  initialBrief?: NewProjectBrief): ProjectIntakeDraft {
  const catalog = readCatalog(storage);
  const found = catalog.drafts.find((draft) => draft.scopeKey === catalog.active[context] && draft.context === context);
  return found ?? createIntakeDraft(storage, context, initialBrief);
}
export function saveIntakeDraft(storage: DraftStorage, draft: ProjectIntakeDraft): void {
  const catalog = readCatalog(storage);
  const index = catalog.drafts.findIndex((item) => item.scopeKey === draft.scopeKey && item.context === draft.context);
  if (index < 0) throw new Error("신규 작성 초안의 자료 연결이 변경되었습니다.");
  catalog.drafts[index] = { ...draft, brief: structuredClone(draft.brief), updatedAt: new Date().toISOString() };
  catalog.active[draft.context] = draft.scopeKey;
  storage.setItem(INTAKE_DRAFT_STORAGE_KEY, JSON.stringify(catalog));
}
export function bindIntakeDraftConversation(storage: DraftStorage, scopeKey: string, conversationId: string): void {
  const catalog = readCatalog(storage);
  const draft = catalog.drafts.find((item) => item.scopeKey === scopeKey);
  if (!draft) throw new Error("작성 대화에 연결할 초안이 없습니다.");
  if (catalog.active[draft.context] === scopeKey) delete catalog.active[draft.context];
  draft.context = `conversation:${conversationId}`;
  catalog.active[draft.context] = scopeKey;
  storage.setItem(INTAKE_DRAFT_STORAGE_KEY, JSON.stringify(catalog));
}

export interface InitialIntakeDraftState {
  draft: ProjectIntakeDraft | null;
  drafts: ProjectIntakeDraft[];
  error: string;
}
export function initializeIntakeDraftState(storageFactory: () => DraftStorage, context: string,
  initialBrief?: NewProjectBrief): InitialIntakeDraftState {
  try {
    // Include access to the browser storage property in this guarded boundary.
    const storage = storageFactory();
    const draft = restoreIntakeDraft(storage, context, initialBrief);
    return { draft, drafts: listIntakeDrafts(storage, context), error: "" };
  } catch {
    // Never rewrite a broken catalog, generate a fallback key, or publish raw errors.
    return { draft: null, drafts: [], error: "신규 작성 초안을 복구하지 못했습니다. 저장소를 사용할 수 있을 때 다시 열어 주세요. 기존 자료는 보존합니다." };
  }
}
