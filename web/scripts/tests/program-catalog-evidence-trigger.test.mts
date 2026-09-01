import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("verified program-list evidence triggers the existing navigator refresh", () => {
  const app = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");
  const contracts = readFileSync(
    new URL(
      "../../../packages/typescript/controlstudio-contracts/src/index.ts",
      import.meta.url,
    ),
    "utf8",
  );

  assert.match(contracts, /interface EvidenceRef \{[\s\S]*toolName\?: string;/);
  assert.match(app, /toolName\?: string \}\)\.toolName ===[\s\S]*"project\.list_programs"/);
  assert.match(app, /catalogEvidence\?\.evidenceId \?\? locatedMessage\?\.messageId/);
  assert.match(app, /\n\s*evidence,\n\s*hostReady,/);
});
