import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { renderToStaticMarkup } from 'react-dom/server';

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { ComposerActionButton } = await server.ssrLoadModule('/src/components/ComposerActionButton.tsx');
  const { agentProgressDetail, activityDetail, isHistoricalRunReplay } = await server.ssrLoadModule('/src/activityProgress.ts');
  for (const eventType of ['run.created', 'run.state', 'run.state_changed', 'agent.token', 'agent.progress']) {
    assert.equal(isHistoricalRunReplay({ eventType, sequence: 100 }, 100), true);
    assert.equal(isHistoricalRunReplay({ eventType, sequence: 101 }, 100), false,
      'live events after snapshot must still update the current request');
  }
  for (const eventType of ['tool.started', 'tool.completed', 'evidence.added', 'agent.message']) {
    assert.equal(isHistoricalRunReplay({ eventType, sequence: 90 }, 100), false,
      'historical evidence and stored messages remain available');
  }
  const calls = [];
  const base = { running: false, cancelBusy: false, sendDisabled: false, writeRequest: false,
    onSend: () => calls.push('send'), onCancel: () => calls.push('cancel') };
  const sending = ComposerActionButton(base);
  assert.equal(sending.props['aria-label'], '메시지 보내기');
  sending.props.onClick();
  const running = ComposerActionButton({ ...base, running: true, sendDisabled: true });
  assert.equal(running.props.disabled, false, 'blank/disabled composer must not disable cancellation');
  assert.equal(running.props['aria-label'], '작업 중단 요청');
  running.props.onClick();
  assert.deepEqual(calls, ['send', 'cancel'], 'stop cannot submit a second message');
  const pending = ComposerActionButton({ ...base, running: true, cancelBusy: true });
  assert.equal(pending.props.disabled, true);
  assert.equal(pending.props['aria-busy'], true);
  const write = ComposerActionButton({ ...base, writeRequest: true });
  assert.ok(renderToStaticMarkup(write).includes('수정·저장'));
  assert.equal(write.props['aria-label'], '요청한 수정 실행 및 저장');
  assert.equal(agentProgressDetail('private-run-id', 180), '3분 경과');
  assert.equal(agentProgressDetail('private-run-id', 195), '3분 15초 경과');
  assert.equal(agentProgressDetail('private-run-id', undefined), '');
  assert.equal(agentProgressDetail('private-run-id', NaN), '');
  assert.equal(agentProgressDetail('private-run-id', 0), '');
  assert.equal(agentProgressDetail('private-run-id', 0.5), '');
  assert.equal(activityDetail(true, undefined, '0ms 경과', '1분 경과'), '1분 경과');
  assert.equal(activityDetail(false, undefined, '도구 실행', '완료'), '도구 실행');
  console.log('PASS: composer send/stop lifecycle, explicit writes, readable elapsed time without IDs');
} finally {
  await server.close();
}
