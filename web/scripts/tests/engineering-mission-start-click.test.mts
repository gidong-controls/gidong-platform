import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";

const app = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");
const panel = readFileSync(
  new URL("../../src/components/EngineeringMissionPanel.tsx", import.meta.url),
  "utf8",
);

function functionBody(source: string, name: string): string {
  const start = source.indexOf(`const ${name} = async () => {`);
  assert.notEqual(start, -1, `${name} must be defined`);
  const end = source.indexOf("\n  const ", start + 1);
  assert.notEqual(end, -1, `${name} must end before the next callback`);
  return source.slice(start, end);
}

async function enabledStartButton(): Promise<{
  props: { disabled: boolean; onClick: () => void };
}> {
  const server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL("../..", import.meta.url)),
    server: { middlewareMode: true },
  });
  try {
    const { EngineeringMissionPanel } = await server.ssrLoadModule<{
      EngineeringMissionPanel: (props: Record<string, unknown>) => unknown;
    }>("/src/components/EngineeringMissionPanel.tsx");
    let startCalls = 0;
    const panel = EngineeringMissionPanel({
      mission: {
        workspaceId: "workspace-1",
        revisionId: "revision-1",
        revision: 1,
        digest: "d".repeat(16),
        snapshot: {
          objective: "read the verified project",
          status: "completed",
          steps: [],
          blockingQuestions: [],
          completionCriteria: [],
          nextAction: null,
          stopReason: null,
        },
      },
      availableEvidenceIds: [],
      onOpenEvidence: () => {},
      resumeAvailable: false,
      resumeBusy: false,
      onResume: () => {},
      reverifyAvailable: false,
      reverifyBusy: false,
      onReverifyBlockedStep: () => {},
      startNewAvailable: true,
      startNewBusy: false,
      onStartNew: () => {
        startCalls += 1;
      },
    });
    const findStartButton = (node: unknown): {
      props?: { children?: unknown; disabled?: boolean; onClick?: () => void };
    } | null => {
      if (!node || typeof node !== "object") return null;
      const props = (node as { props?: { children?: unknown; disabled?: boolean; onClick?: () => void } }).props;
      if (props?.children === "새 엔지니어링 작업") {
        return node as { props: { disabled: boolean; onClick: () => void } };
      }
      const children = props?.children;
      for (const child of Array.isArray(children) ? children : [children]) {
        const found = findStartButton(child);
        if (found) return found;
      }
      return null;
    };
    const button = findStartButton(panel);
    assert.ok(button?.props?.onClick, "completed mission must render a start button callback");
    button.props.onClick();
    assert.equal(startCalls, 1, "clicking the enabled button must call the parent start action");
    return button as { props: { disabled: boolean; onClick: () => void } };
  } finally {
    await server.close();
  }
}

test("an enabled completed-mission start click reaches fresh conversation creation", async () => {
  const handler = functionBody(app, "startNewEngineeringMission");

  const button = await enabledStartButton();
  assert.equal(button.props.disabled, false);

  // Eligibility remains the render-time button gate. The click handler may
  // suppress a duplicate request, but must not silently recheck that derived
  // value and return before the POST path.
  assert.match(panel, /disabled=\{!startNewAvailable \|\| startNewBusy\}/);
  assert.match(handler, /if \(missionStartBusy\) \{\s*return;\s*\}/);
  assert.doesNotMatch(handler, /!canStartNewEngineeringMission/);
  assert.match(
    handler,
    /await startFreshConversation\("새 PLC 엔지니어링 작업"\)/,
  );
  assert.match(app, /hostApi\.createConversation\(\{ title \}\)/);
});
