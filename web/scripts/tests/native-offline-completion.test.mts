import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(
  new URL("../../src/components/NativeProjectPanel.tsx", import.meta.url),
  "utf8",
);
const nativeProjects = readFileSync(
  new URL("../../../host/src/controlstudio_host/native_projects.py", import.meta.url),
  "utf8",
);

test("offline native completion is bound to Program Check and independent reopen, not XG-SIM", () => {
  const completionStart = panel.indexOf("function isVerifiedCompletion(");
  const completionEnd = panel.indexOf("function expectedRecoveryProjectPath", completionStart);
  assert.ok(completionStart >= 0 && completionEnd > completionStart);
  const completion = panel.slice(completionStart, completionEnd);

  assert.match(completion, /receipt\.programReadback\.programCheckPassed === true/);
  assert.match(completion, /freshProcessReopenVerified/);
  assert.doesNotMatch(completion, /&&\s*nativeSimulationVerified/);

  const tailStart = nativeProjects.indexOf("async def _complete_verified_native_tail(");
  const tailEnd = nativeProjects.indexOf("async def _continue_completed_verification", tailStart);
  assert.ok(tailStart >= 0 && tailEnd > tailStart);
  const tail = nativeProjects.slice(tailStart, tailEnd);
  assert.doesNotMatch(tail, /await self\._verify_native_simulation\(/);
  assert.match(tail, /reopen_verification\.get\("state"\) == "verified"/);
});
