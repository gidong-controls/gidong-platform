import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

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

test("an enabled completed-mission start click reaches fresh conversation creation", () => {
  const handler = functionBody(app, "startNewEngineeringMission");

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
