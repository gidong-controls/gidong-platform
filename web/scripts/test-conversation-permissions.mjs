import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { ConversationPermissions } = await server.ssrLoadModule('/src/components/ConversationPermissions.tsx');
  for (const [scope, label] of [
    ['analysis_only', '권한: 변경 안 함'],
    ['review_before_apply', '권한: 적용 전 확인'],
    ['offline_candidate_revision', '권한: 적용 전 확인'],
    ['permanent_offline_revision', '이번 요청: 수정 허용'],
  ]) {
    const markup = renderToStaticMarkup(createElement(ConversationPermissions, {
      scope, busy: false, canChange: true, simulation: false, canSimulate: true,
      onSelect: () => assert.fail('Rendering must not grant permission'),
      onSimulation: () => assert.fail('Rendering must not start simulation'),
    }));
    assert.ok(markup.includes(label));
    assert.ok(markup.includes('aria-expanded="false"'));
    assert.ok(!markup.includes('role="group"'), 'options stay out of the closed UI');
  }
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const composer = app.slice(app.indexOf('<footer className="composer">'), app.indexOf('</footer>', app.indexOf('<footer className="composer">')));
  assert.ok(!composer.includes('change-request-scope'));
  assert.ok(!composer.includes('읽기·분석'));
  assert.ok(composer.includes('writeRequest={changeRequestScope === "permanent_offline_revision"'));
  assert.ok(
    app.includes('{debugMode && engineeringScopeVisible && !targetlessAnalysisActive && ('),
    'the permission override belongs to technical diagnostics, not the normal conversation UI',
  );
  assert.ok(app.includes('setChangeRequestScope("analysis_only")'), 'one-shot permission reset is retained');
  console.log('PASS: compact permissions, no render-time authorization, uncluttered composer, explicit write action');
} finally {
  await server.close();
}
