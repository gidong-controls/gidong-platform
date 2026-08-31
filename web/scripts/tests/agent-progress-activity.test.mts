import assert from "node:assert/strict";
import test from "node:test";

import {
  agentProgressDetail,
  formatReportedElapsedSeconds,
  prependCoalescedActivity,
} from "../../src/activityProgress.ts";

test("shows only finite, non-negative server elapsed time on agent progress", () => {
  assert.equal(formatReportedElapsedSeconds(0), "0ms");
  assert.equal(formatReportedElapsedSeconds(1.5), "1.5초");
  assert.equal(formatReportedElapsedSeconds(15), "15초");
  assert.equal(formatReportedElapsedSeconds(-1), null);
  assert.equal(formatReportedElapsedSeconds(Number.NaN), null);
  assert.equal(formatReportedElapsedSeconds("15"), null);
  assert.equal(agentProgressDetail("run-1", 15), "run-1 · 15초 경과");
  assert.equal(agentProgressDetail("run-1", undefined), "run-1");
});

test("coalesces repeated heartbeats for one run and stage without hiding transitions", () => {
  const first = {
    id: "progress-1",
    coalesceKey: "run-1\u0000planning_native_design",
    detail: "run-1 · 15초 경과",
  };
  const repeated = {
    id: first.id,
    coalesceKey: first.coalesceKey,
    detail: "run-1 · 30초 경과",
  };
  const transitioned = {
    id: "progress-2",
    coalesceKey: "run-1\u0000planning_finalize",
    detail: "run-1 · 45초 경과",
  };

  const afterRepeat = prependCoalescedActivity([first], repeated);
  assert.deepEqual(afterRepeat, [repeated]);

  const afterTransition = prependCoalescedActivity(afterRepeat, transitioned);
  assert.deepEqual(afterTransition, [transitioned, repeated]);
});
