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
