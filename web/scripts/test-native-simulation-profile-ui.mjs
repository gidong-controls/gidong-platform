import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(
  new URL('../src/components/NativeProjectPanel.tsx', import.meta.url),
  'utf8',
);
const appSource = await readFile(
  new URL('../src/App.tsx', import.meta.url),
  'utf8',
);
const reviewSource = await readFile(
  new URL('../src/components/NewProjectPlanReviewPanel.tsx', import.meta.url),
  'utf8',
);
const contracts = await readFile(
  new URL('../../packages/typescript/controlstudio-contracts/src/index.ts', import.meta.url),
  'utf8',
);

assert.match(
  source,
  /simulationProfilePreflight\?\.schemaVersion ===\s*"controlstudio-native-simulation-profile-observation\/v1"/,
  'the profile preflight has its own visible state',
);
assert.match(source, /프로젝트 생성·저장·독립 재열기 검증은 완료됐습니다/);
assert.match(source, /시뮬레이터에는\s*아무 명령도 보내지 않았습니다/);
assert.match(source, /다음 설계 검토에서 기존 신호와 모드에\s*맞는 시험 역할 바인딩/);
assert.match(source, /<summary>읽은 설계 ID 보기<\/summary>/);
assert.match(source, /simulationReviewedPlan: reviewedPlan/);
assert.match(source, /기존 프로젝트에서 새 시험 설정 사용/);
assert.match(source, /reviewedSimulationPlanAvailable/);
assert.match(
  source,
  /reviewedPlan\.planDigest !== operation\.request\.reviewedPlan\.planDigest/,
  'the choice is absent when the retained operation already represents the same reviewed plan',
);
assert.match(source, /새 설계로 별도 프로젝트 만들기/);
assert.match(
  source,
  /reviewedSimulationPlanAvailable && onCreateFromReviewedPlan[\s\S]*?onClick=\{onCreateFromReviewedPlan\}/,
  'a different reviewed plan exposes an explicit new immutable project choice',
);
assert.match(
  contracts,
  /controlstudio-native-simulation-profile-observation\/v1/,
  'wire contract retains the structured preflight observation',
);
assert.match(contracts, /simulationReviewedPlan\?: ReviewedProjectPlanBinding/);
assert.match(contracts, /simulationPlanRevision\?:/);
assert.match(
  appSource,
  /nativeProjectRestoreConversationId === conversation\.conversationId[\s\S]*?setNativeProjectOpen\(true\);[\s\S]*?return;/,
  'a reviewed follow-up in the same conversation reopens the retained native build before exact current-review recovery lookup',
);
assert.match(
  appSource,
  /initialNativeProjectId=\{currentNativeProjectRestoreId \?\? undefined\}/,
  'the retained same-conversation native operation is supplied to the simulation panel',
);
assert.match(
  appSource,
  /key=\{[\s\S]*?currentNativeProjectRestoreId \?\?[\s\S]*?latestPlanningReview\?\.planDigest/,
  'switching from a retained operation to a new reviewed build remounts clean request state',
);
assert.match(
  appSource,
  /onCreateFromReviewedPlan=\{\(\) => \{\s*setNativeProjectRestoreId\(null\);\s*setNativeProjectRestoreConversationId\(null\);\s*\}\}/,
  'choosing a new build clears only the UI recovery selection and preserves the stored operation',
);
assert.doesNotMatch(
  appSource,
  /onClose=\{\(\) => \{\s*setNativeProjectOpen\(false\);\s*if \([\s\S]*?setNativeProjectRestoreId\(null\)/,
  'closing the panel must not discard the completed build lineage needed by a later reviewed simulation profile',
);
assert.match(reviewSource, /native-simulation-profile/);
assert.match(reviewSource, /design\/indexAcceptanceBindings\/signals/);
assert.match(reviewSource, /자동 시작 버튼/);
assert.match(reviewSource, /5초 시간초과 타이머/);
assert.match(reviewSource, /<summary>기술 기록 보기<\/summary>/);
assert.match(
  reviewSource,
  /이 계획에는 시뮬레이션 신호 연결 검토 항목이 없습니다/,
  'an older profile artifact without semantic rows is blocked with a human-readable republish instruction',
);
assert.match(contracts, /indexAcceptanceBindings\?: EngineeringPlanIndexAcceptanceBindings/);

console.log('PASS: simulation profile preflight preserves build/reopen completion and exposes reviewed-binding next step');
