/** Render retained evidence citations by title without changing stored answers. */
export function displayEvidenceCitations(
  content: string,
  evidence: readonly { evidenceId: string; title: string }[],
): string {
  const titles = new Map(evidence.map((item) => [item.evidenceId, item.title]));
  return content.replace(
    /\[근거:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]/gi,
    (_citation, id: string) => {
      const title = titles.get(id);
      if (!title) return "[참고 자료 미연결]";
      const label = title.replace(/\s+/g, " ").replace(/[\\`*_[\]<>]/g, "\\$&");
      return `[${label}](#evidence-${id})`;
    },
  );
}

export function displayAssistantText(
  content: string,
  evidence: readonly { evidenceId: string; title: string }[],
  programs: readonly { programId: string }[],
): string {
  const readable = displayEvidenceCitations(content, evidence);
  return readable.replace(/\s*\(programId:\s*`?(ld:[0-9a-f]+)`?\s*\)/gi,
    (label, id: string) => programs.some((program) => program.programId === id) ? "" : label);
}

/** Suppress empty internal envelopes; evidence-only answers already have titled links. */
export function hasReadableEngineeringAnalysis(
  analysis: {
    observations: readonly unknown[];
    inferences: readonly unknown[];
    hypotheses: readonly unknown[];
    alternatives: readonly unknown[];
    locations?: readonly unknown[];
    relations?: readonly unknown[];
    coverage?: readonly unknown[];
    projectStructure?: readonly unknown[];
    unknowns: readonly unknown[];
    nextActions: readonly unknown[];
  },
): boolean {
  return [
    analysis.observations,
    analysis.inferences,
    analysis.hypotheses,
    analysis.alternatives,
    analysis.locations ?? [],
    analysis.relations ?? [],
    analysis.coverage ?? [],
    analysis.projectStructure ?? [],
    analysis.unknowns,
    analysis.nextActions,
  ].some((items) => items.length > 0);
}

/** Only recognize explicit row inventories; ordinary instructions stay visible. */
export function rowDetailList(node: unknown): { warnings: string[] } | null {
  function textOf(value: unknown): string {
    if (!value || typeof value !== "object") return "";
    const item = value as { value?: unknown; children?: unknown[] };
    if (typeof item.value === "string") return item.value;
    return Array.isArray(item.children) ? item.children.map(textOf).join("") : "";
  }
  const list = node as { children?: { type?: string; tagName?: string }[] } | undefined;
  const rows = Array.isArray(list?.children)
    ? list.children.filter((item) => item.type === "element" && item.tagName === "li").map(textOf)
    : [];
  if (rows.length < 3 || !rows.every((row) => /(?:\brows?\s*\d|행\s*\d|\d+\s*행)/i.test(row.slice(0, 100)))) return null;
  return { warnings: rows.filter((row) => /미확인|미해결|불명|단정|주의|위험|확인.{0,12}필요|partial|unresolved|unknown|cannot/i.test(row)) };
}
