import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { hasSupportedChangeRecoveryContract } from "../../src/api/changeContracts.ts";
import {
  canOfferRecoveryRelaunch,
  journaledRecoveryRelaunchTarget,
} from "../../src/api/changeRecovery.ts";
import {
  appendStreamingDraft,
  clearStreamingDraftForRun,
  visibleStreamingDraftText,
} from "../../src/streamingDraft.ts";

function legacyLdPlan(): Record<string, unknown> {
  return {
    changeMode: "offline_working_copy_revision",
    requestScope: "permanent_offline_revision",
    target: { programId: "program:MAIN", tokenOffset: 14 },
    current: { value: "M0001" },
    proposed: { value: "M0002" },
    impact: ["One reviewed operand changes."],
    risks: ["The working copy must be reopened before release."],
    assumptions: [],
    blockingUnknowns: [],
    alternatives: [],
    acceptanceCriteria: [],
    observationSignals: [],
    stopConditions: [],
    coverage: [],
    verificationScope: "offline_native_reopen",
    validation: [
      "structural_diff",
      "xg5000_program_check",
      "project_save",
      "project_reopen",
    ],
    manualFollowUp: ["Review the native reopen receipt."],
    rollback: ["Restore the transaction backup."],
  };
}

test("accepts an exact legacy LD receipt without inventing planKind", () => {
  const plan = legacyLdPlan();

  assert.equal("planKind" in plan, false);
  assert.equal(hasSupportedChangeRecoveryContract(plan), true);
});

test("fails closed for stale three-step or explicit-null plan-kind records", () => {
  const staleScope = {
    ...legacyLdPlan(),
    verificationScope: "offline_native_check_only",
    validation: ["structural_diff", "xg5000_program_check", "project_save"],
  };
  const explicitNullKind = { ...legacyLdPlan(), planKind: null };

  assert.equal(hasSupportedChangeRecoveryContract(staleScope), false);
  assert.equal(hasSupportedChangeRecoveryContract(explicitNullKind), false);
});

test("offers an explicit relaunch only after an empty inventory using journaled identity", () => {
  const targetIdentity = {
    engineeringProcessId: 1234,
    engineeringWindowHandle: 5678,
  };

  assert.deepEqual(journaledRecoveryRelaunchTarget(targetIdentity), {
    pid: 1234,
    hwnd: 5678,
  });
  assert.equal(
    canOfferRecoveryRelaunch({
      transactionState: "quarantined",
      recoverySupported: true,
      inventoryConfirmedEmpty: false,
      targetIdentity,
    }),
    false,
  );
  assert.equal(
    canOfferRecoveryRelaunch({
      transactionState: "quarantined",
      recoverySupported: true,
      inventoryConfirmedEmpty: true,
      targetIdentity,
    }),
    true,
  );
});

test("fails closed when the journal has no exact PID/HWND for relaunch", () => {
  assert.equal(
    journaledRecoveryRelaunchTarget({
      engineeringProcessId: 1234,
      engineeringWindowHandle: null,
    }),
    null,
  );
  assert.equal(
    canOfferRecoveryRelaunch({
      transactionState: "quarantined",
      recoverySupported: true,
      inventoryConfirmedEmpty: true,
      targetIdentity: { engineeringProcessId: 0, engineeringWindowHandle: 5678 },
    }),
    false,
  );
});

test("saved workspace offers an explicit workspace-bound existing-XG attach", () => {
  const source = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");
  const cardStart = source.indexOf('className="workspace-resume-actions"');
  const cardEnd = source.indexOf("</section>", cardStart);

  assert.ok(cardStart >= 0, "saved workspace action card is present");
  assert.ok(cardEnd > cardStart, "saved workspace action card closes");
  const card = source.slice(cardStart, cardEnd);
  assert.match(card, /기존 XG5000 창에 이 작업 연결/);
  assert.match(card, /openInstancePicker\(false\)/);
  assert.doesNotMatch(card, /openInstancePicker\(true\)/);
});

test("keeps the conversation-history action accessible as a compact mobile icon", () => {
  const appSource = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../../src/styles.css", import.meta.url), "utf8");

  assert.match(appSource, /className="header-text-button header-history-button"/);
  assert.match(appSource, /aria-label="작업 기록"/);
  assert.match(appSource, /header-history-button__label">작업 기록/);
  assert.match(
    styles,
    /@media \(max-width: 860px\)[\s\S]*?\.header-history-button \{[\s\S]*?display: inline-grid;[\s\S]*?width: 36px;[\s\S]*?\.header-history-button__label \{[\s\S]*?display: none;/,
  );
});

test("terminal replay clears only the draft owned by that terminal run", () => {
  const oldRunId = "run-old-terminal";
  const currentRunId = "run-current";
  const oldDraft = appendStreamingDraft(null, oldRunId, "old approval text");

  assert.deepEqual(clearStreamingDraftForRun(oldDraft, oldRunId), null);

  const currentDraft = appendStreamingDraft(null, currentRunId, "current text");
  assert.deepEqual(
    clearStreamingDraftForRun(currentDraft, oldRunId),
    currentDraft,
  );
  assert.equal(visibleStreamingDraftText(oldDraft, currentRunId, false), "");
  assert.equal(
    visibleStreamingDraftText(oldDraft, currentRunId, true),
    "",
  );
  assert.equal(
    visibleStreamingDraftText(oldDraft, oldRunId, true),
    "old approval text",
  );
});

test("App binds replayed token drafts to their run and clears a terminal owner", () => {
  const source = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");

  assert.match(source, /appendStreamingDraft\(\s*streamingDraftRef\.current,\s*event\.runId/);
  assert.match(source, /TERMINAL_RUN_STATES\.has\(runState\)/);
  assert.match(source, /clearStreamingDraftForRun\(\s*streamingDraftRef\.current,\s*event\.runId/);
  assert.match(source, /visibleStreamingDraftText\(\s*streamingDraftRef\.current,\s*activeRunId/);
});

test("offline candidates retain signed runtime blockers while permanent revisions require resolution", () => {
  const source = readFileSync(
    new URL("../../src/components/ChangeApprovalPanel.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    source,
    /const requiresResolvedHardware =\s*plan\.requestScope === "permanent_offline_revision"/,
  );
  assert.match(
    source,
    /const hardwareReadyForScope =\s*!requiresResolvedHardware \|\|[\s\S]*?requiredHardwareObserved[\s\S]*?relevantHardwareBlockers\.length === 0/,
  );
  assert.match(source, /hardwareReadyForScope &&/);
  assert.doesNotMatch(
    source,
    /stringList\(hardwareBinding\.blocking_reasons\)\.length === 0 &&\s*requiredHardwareObserved/,
  );
});
