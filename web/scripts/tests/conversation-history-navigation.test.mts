import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");

test("selecting matching engineering history returns to its workspace", () => {
  const start = app.indexOf("const selectConversationHistory = useCallback(");
  assert.notEqual(start, -1, "history selection callback must exist");
  const end = app.indexOf("\n\n  const ", start + 1);
  assert.notEqual(end, -1, "history callback must end before the next callback");
  const handler = app.slice(start, end);

  assert.match(
    handler,
    /const matchesCurrentTarget =\s*conversationMatchesCurrentTarget\(detail, status\) \|\|\s*conversationMissionMatchesCurrentTarget\(detail, status\);/,
  );
  assert.match(
    handler,
    /if \(detail\.workflow === "engineering" && matchesCurrentTarget\) \{[\s\S]*?setWorkChooserOpen\(false\);[\s\S]*?\}/,
  );
});

test("startup leaves a cross-target active operation in history", () => {
  const start = app.indexOf("const connectHost = useCallback(async () => {");
  assert.notEqual(start, -1, "Host startup callback must exist");
  const end = app.indexOf("const refreshStatus", start);
  assert.notEqual(end, -1, "Host startup callback must end before refreshStatus");
  const handler = app.slice(start, end);

  assert.match(handler, /const targetAtBootstrap = currentEngineeringTarget\(bootstrap\.status\);/);
  assert.match(handler, /operationConversation = await hostApi\.getConversation\(/);
  assert.match(
    handler,
    /restoreActiveOperation =\s*targetAtBootstrap === null \|\|\s*conversationMatchesCurrentTarget\([\s\S]*?\) \|\|\s*conversationMissionMatchesCurrentTarget\(/,
  );
  assert.match(handler, /if \(operationConversationId && restoreActiveOperation\) \{/);
  assert.match(handler, /if \(!restoreActiveOperation\) \{[\s\S]*?setActiveChange\(null\);/);
});

test("new work can defer an old recovery panel without changing its transaction", () => {
  const start = app.indexOf("const openWorkChooser = () => {");
  assert.notEqual(start, -1, "new-work chooser callback must exist");
  const end = app.indexOf("const returnToCurrentWork", start);
  assert.notEqual(end, -1, "new-work chooser callback must end before return action");
  const handler = app.slice(start, end);

  assert.match(handler, /setDeferredChangeTransactionId\(activeChange\.transactionId\);/);
  assert.doesNotMatch(handler, /acknowledgeChangeReceipt|decideChangeTransaction/);
  assert.match(
    app,
    /activeChange\.transactionId !== deferredChangeTransactionId/,
  );
});
