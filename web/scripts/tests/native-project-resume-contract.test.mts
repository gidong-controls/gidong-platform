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
