import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { PlanItemRow, ReviewedPlanActions, projectPlanReviewItemIds } = await server.ssrLoadModule(
    '/src/components/NewProjectPlanReviewPanel.tsx',
  );
  const evidence = '과거 사용자 요구와 수정 지시 '.repeat(200);
  const value = {
    status: 'confirmed',
    reason: 'all required operating modes were inferred from prior instructions',
    evidence: [evidence],
    basis: null,
    count: 3,
    ids: ['automatic', 'manual', 'stopped'],
  };
  const item = {
    itemId: 'scope/operatingModes',
    text: JSON.stringify({ path: 'scope/operatingModes', value }),
    status: 'confirmed',
    source: 'agent',
    blocking: false,
  };
  const markup = renderToStaticMarkup(createElement(PlanItemRow, { item }));
  const technicalDetailAt = markup.indexOf('<details>');
  assert.ok(technicalDetailAt > 0, 'collection scope keeps an expandable technical record');
  const defaultView = markup.slice(0, technicalDetailAt);
  const technicalView = markup.slice(technicalDetailAt);
  assert.match(defaultView, /실행 운전 모드/);
  assert.match(defaultView, /확인됨/);
  assert.match(defaultView, /3개 항목/);
  for (const hidden of [evidence, value.reason, 'basis', 'ids']) {
    assert.ok(!defaultView.includes(hidden), `default collection summary hides ${hidden.slice(0, 20)}`);
  }
  assert.ok(technicalView.includes('기술 기록 보기'));
  assert.ok(technicalView.includes(evidence));
  assert.ok(technicalView.includes('&quot;basis&quot;'));
  assert.equal(JSON.parse(item.text).value.evidence[0], evidence, 'stored evidence remains intact');

  const ordinary = {
    ...item,
    itemId: 'design/ioPoints/start/mapping',
    text: JSON.stringify({
      path: 'design/ioPoints/start/mapping',
      value: { symbol: 'START', address: 'M00010', kind: 'digital_input' },
    }),
  };
  const ordinaryMarkup = renderToStaticMarkup(createElement(PlanItemRow, { item: ordinary }));
  assert.match(ordinaryMarkup.slice(0, ordinaryMarkup.indexOf('<details>')), /START/);
  assert.match(ordinaryMarkup.slice(0, ordinaryMarkup.indexOf('<details>')), /M00010/);

  const requirementMessageId = '65733f30-3d33-4a83-931a-6c853df8aff8';
  const requirementQuote = '시간 초과는 6초로 바꿔 줘.';
  const requirementItem = {
    itemId: `requirement/message/${requirementMessageId}/performance_and_acceptance`,
    text: JSON.stringify({
      path: `requirement/message/${requirementMessageId}/performance_and_acceptance`,
      value: {
        field: 'performance_and_acceptance',
        messageId: requirementMessageId,
        quote: requirementQuote,
      },
    }),
    status: 'proposed',
    source: 'agent',
    blocking: false,
  };
  const requirementMarkup = renderToStaticMarkup(
    createElement(PlanItemRow, { item: requirementItem }),
  );
  const requirementTechnicalAt = requirementMarkup.indexOf('<details>');
  const requirementDefault = requirementMarkup.slice(0, requirementTechnicalAt);
  const requirementTechnical = requirementMarkup.slice(requirementTechnicalAt);
  assert.match(requirementDefault, /사용자 요구 근거/);
  assert.match(requirementDefault, /성능·인수 조건/);
  assert.ok(requirementDefault.includes(requirementQuote));
  assert.ok(!requirementDefault.includes(requirementMessageId));
  assert.ok(requirementTechnical.includes(requirementMessageId));
  assert.ok(requirementTechnical.includes('기술 기록 보기'));

  const semanticItem = (itemId, value = null) => ({
    itemId,
    text: JSON.stringify({ path: itemId, value }),
    status: 'proposed',
    source: 'agent',
    blocking: false,
  });
  const permit = { signalId: 'permit', state: 'on' };
  const condition = {
    kind: 'contact', sourceType: 'signal', sourceId: 'permit', state: 'on',
  };
  const action = { instruction: 'OUT', targetType: 'signal', targetId: 'output' };
  const exactArtifact = {
    schemaVersion: '2', projectName: '검토', status: 'ready_for_review',
    summary: '검토', objective: '검토', attachments: [], openDecisions: [],
    requirementSources: [{
      field: 'performance_and_acceptance', quote: requirementQuote,
      messageId: requirementMessageId,
    }],
    technicalConfiguration: {
      plcSeries: 'XGK', cpuModel: 'XGK-CPUE', programName: 'MAIN',
      language: 'LD', description: '검토',
    },
    design: {
      collectionScope: Object.fromEntries([
        'ioPoints', 'operatingModes', 'controlNetworks', 'timers', 'sequences',
        'alarms', 'recoveryRules', 'safetyRules',
      ].map((name) => [name, { status: 'confirmed', reason: name, evidence: ['검토'] }])),
      ioPoints: [
        { signalId: 'permit', symbol: 'PERMIT', address: 'M00000', kind: 'digital_input', normalState: 'off', safeState: 'not_applicable' },
        { signalId: 'output', symbol: 'OUTPUT', address: 'M00001', kind: 'internal_bit', normalState: 'off', safeState: 'off' },
      ],
      operatingModes: [],
      controlNetworks: [{ networkId: 'run', permitAll: [permit], outputSignalId: 'output', modeId: null, stepId: null }],
      ladderRungs: [{ rungId: 'run-rung', description: '허가 출력', condition, action }],
      timers: [], sequences: [], alarms: [], recoveryRules: [], safetyRules: [],
    },
    completeness: { totalItems: 30, confirmed: 0, proposed: 30, unknown: 0, conflicting: 0, notApplicable: 0, blocking: 0 },
    sections: [],
  };
  const requirementPath = requirementItem.itemId;
  const technicalIds = ['cpuModel', 'description', 'language', 'plcSeries', 'programName', 'projectName'].map((name) => `technical/${name}`);
  const scopeIds = ['ioPoints', 'operatingModes', 'controlNetworks', 'ladderRungs', 'timers', 'sequences', 'alarms', 'recoveryRules', 'safetyRules'].map((name) => `scope/${name}`);
  const ioIds = ['permit', 'output'].flatMap((id) => ['mapping', 'normalState', 'safeState'].map((name) => `design/ioPoints/${id}/${name}`));
  const networkIds = ['output', 'permit/permit:on', 'mode', 'step'].map((name) => `design/controlNetworks/run/${name}`);
  const rungValues = new Map([
    ['scope/ladderRungs', { status: 'confirmed', count: 1, ids: ['run-rung'] }],
    ['design/ladderRungs/run-rung/position', 0],
    ['design/ladderRungs/run-rung/description', '허가 출력'],
    ['design/ladderRungs/run-rung/condition', condition],
    ['design/ladderRungs/run-rung/action', action],
  ]);
  exactArtifact.sections = [
    { sectionId: 'user-requirements', items: [requirementItem] },
    { sectionId: 'native-technical-configuration', items: technicalIds.map(semanticItem) },
    { sectionId: 'native-io-map', items: [...scopeIds.filter((id) => id === 'scope/ioPoints'), ...ioIds].map((id) => semanticItem(id, rungValues.get(id))) },
    { sectionId: 'native-mode-model', items: [semanticItem('scope/operatingModes')] },
    { sectionId: 'native-control-networks', items: [semanticItem('scope/controlNetworks'), ...networkIds.map(semanticItem)] },
    { sectionId: 'native-ladder-rungs', items: [semanticItem('scope/ladderRungs', rungValues.get('scope/ladderRungs')), ...[...rungValues.entries()].filter(([id]) => id.startsWith('design/')).map(([id, value]) => semanticItem(id, value))] },
    ...[
      ['timers', 'native-timers'],
      ['sequences', 'native-sequences'],
      ['alarms', 'native-alarms'],
      ['recoveryRules', 'native-recovery'],
      ['safetyRules', 'native-safety-rules'],
    ].map(([name, sectionId]) => ({ sectionId, items: [semanticItem(`scope/${name}`)] })),
  ];
  assert.ok(projectPlanReviewItemIds(exactArtifact)?.includes(requirementPath));
  const tamperedArtifact = structuredClone(exactArtifact);
  tamperedArtifact.sections[0].items[0].text = JSON.stringify({
    path: requirementPath,
    value: { field: 'performance_and_acceptance', messageId: 'other-message', quote: requirementQuote },
  });
  assert.equal(projectPlanReviewItemIds(tamperedArtifact), undefined);

  const open = () => {};
  const review = { reviewedAt: '2026-09-14T00:00:00Z' };
  const actions = ReviewedPlanActions({
    review,
    onOpenTechnicalSetup: open,
    technicalSetupDisabled: true,
    technicalSetupTitle: '기존 권한 조건',
  });
  const button = actions.props.children[1];
  assert.equal(button.props.onClick, open, 'visible CTA uses the supplied setup handler');
  assert.equal(button.props.disabled, true, 'visible CTA preserves the supplied gate');
  const actionsMarkup = renderToStaticMarkup(actions);
  assert.match(actionsMarkup, /설계 검토 완료/);
  assert.match(actionsMarkup, /다음 단계 · 프로젝트 설정 확인/);
  assert.match(actionsMarkup, /title="기존 권한 조건"/);
  console.log('PASS: concise scope and requirement provenance, expandable technical records, reviewed next-step CTA');
} finally {
  await server.close();
}
