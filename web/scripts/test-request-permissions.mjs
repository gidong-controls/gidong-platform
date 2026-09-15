import assert from 'node:assert/strict';
import { resolveRequestScope } from '../src/requestPermissions.ts';

assert.equal(resolveRequestScope('review_before_apply', true, false), 'offline_candidate_revision');
assert.equal(resolveRequestScope('review_before_apply', false, false), 'analysis_only');
assert.equal(resolveRequestScope('review_before_apply', true, true), 'analysis_only');
assert.equal(resolveRequestScope('analysis_only', true, false), 'analysis_only');
assert.equal(resolveRequestScope('permanent_offline_revision', true, false), 'permanent_offline_revision');
assert.equal(resolveRequestScope('permanent_offline_revision', true, true), 'analysis_only');
for (const ready of [true, false]) for (const targetless of [true, false]) {
  assert.notEqual(resolveRequestScope('review_before_apply', ready, targetless), 'permanent_offline_revision');
}
console.log('PASS: default review never grants direct execution; unbound and unavailable targets remain analysis-only');
