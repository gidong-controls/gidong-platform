import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("native project resume is explicit and bound to the stored request target", () => {
  const panel = readFileSync(
    new URL("../../src/components/NativeProjectPanel.tsx", import.meta.url),
    "utf8",
  );
  const client = readFileSync(new URL("../../src/api/client.ts", import.meta.url), "utf8");

  assert.match(panel, /const resumeNativeOperation = async/);
  assert.match(panel, /operation\.state !== "quarantined"/);
  assert.match(panel, /!recoveryTargetMatchesStored/);
  assert.match(panel, /expectedRequestDigest: operation\.requestDigest/);
  assert.match(panel, /입력된 LD에서 검사·저장 재개/);
  assert.match(panel, /새 프로젝트 생성이나\s*IL 삽입은 실행하지 않으며 자동 재시도도 하지 않습니다/);
  assert.match(client, /resumeNativeProject\(/);
  assert.match(client, /\/api\/v1\/native-projects\/\$\{encodeURIComponent\(nativeProjectId\)\}\/resume/);
});

test("completed saved native projects explicitly bind a distinct fresh reopen target", () => {
  const panel = readFileSync(
    new URL("../../src/components/NativeProjectPanel.tsx", import.meta.url),
    "utf8",
  );
  const contracts = readFileSync(
    new URL(
      "../../../packages/typescript/controlstudio-contracts/src/index.ts",
      import.meta.url,
    ),
    "utf8",
  );

  assert.match(panel, /const completedVerificationContinuation =/);
  assert.match(panel, /const reuseVerifiedReopen =/);
  assert.match(panel, /const continueCompletedVerification = async/);
  assert.match(panel, /pid: sourcePid,/);
  assert.match(panel, /hwnd: sourceHwnd,/);
  assert.match(panel, /!reuseVerifiedReopen && selectedReopenedInstance/);
  assert.match(panel, /reopenedPid: selectedReopenedInstance\.pid,/);
  assert.match(panel, /reopenedHwnd: selectedReopenedInstance\.hwnd,/);
  assert.match(panel, /instance\.pid !== operation\.request\.xg5000Pid/);
  assert.match(panel, /operation\.result\?\.simulationVerification\?\.state !== "verified"/);
  assert.match(panel, /검증된 재열기 session을 그대로 재사용/);
  assert.match(panel, /새 프로젝트 생성, IL 삽입, 창 종료, 자동 재시도는 하지/);
  assert.match(panel, /명시적 채택 전에는 엔지니어링 준비 완료가 아닙니다/);
  assert.match(
    contracts,
    /interface ResumeNativeProjectRequest \{[\s\S]*reopenedPid\?: number;[\s\S]*reopenedHwnd\?: number;/,
  );
});
