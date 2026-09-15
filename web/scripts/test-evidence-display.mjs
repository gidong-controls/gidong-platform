import assert from 'node:assert/strict';
import {
  displayAssistantText,
  displayEvidenceCitations,
  hasReadableEngineeringAnalysis,
  rowDetailList,
} from '../src/evidenceDisplay.ts';

const id = 'ae908574-828a-5098-a2be-d7cf346f4084';
const source = `프로그램을 확인했습니다. [근거: ${id}]`;
const refs = [{ evidenceId: id, title: '프로그램 목록' }];
assert.equal(displayEvidenceCitations(source, refs), `프로그램을 확인했습니다. [프로그램 목록](#evidence-${id})`);
assert.equal(displayEvidenceCitations(source, []), '프로그램을 확인했습니다. [참고 자료 미연결]');
assert.equal(displayEvidenceCitations('M0320A는 시작 조건입니다.', refs), 'M0320A는 시작 조건입니다.');
assert.ok(displayEvidenceCitations(source, [{ evidenceId: id, title: '[임의](링크)' }]).includes('\\[임의\\]'));
assert.ok(source.includes(id), 'stored source is not mutated');
assert.equal(displayAssistantText('초기동작 (programId: `ld:abc123`) · M00800', [], [{ programId: 'ld:abc123' }]), '초기동작 · M00800');
assert.equal(displayAssistantText('알 수 없는 (programId: `ld:abc123`)', [], []), '알 수 없는 (programId: `ld:abc123`)');
const list = (items) => ({type: 'element', tagName: 'ol', children: items.map(value => ({type: 'element', tagName: 'li', children: [{type: 'text', value}]}))});
const rows = list(['시작 조건 (Row 1): 조건을 읽습니다.', '행 4: 다음 단계입니다.', '행 13: 분기 조건 미해결, 단정할 수 없습니다.']);
const before = structuredClone(rows);
assert.deepEqual(rowDetailList(rows), {warnings: ['행 13: 분기 조건 미해결, 단정할 수 없습니다.']});
assert.deepEqual(rows, before, 'row source is not rewritten');
assert.equal(rowDetailList(list(['준비하세요.', '한 번 이동하세요.', '결과를 확인하세요.'])), null);
assert.equal(rowDetailList(list(['Row 1: 하나', 'Row 2: 둘'])), null);
assert.equal(rowDetailList(undefined), null);
const emptyAnalysis = {
  observations: [], inferences: [], hypotheses: [], alternatives: [],
  locations: [], relations: [], coverage: [], projectStructure: [],
  unknowns: [], nextActions: [],
};
assert.equal(hasReadableEngineeringAnalysis(emptyAnalysis), false);
assert.equal(
  hasReadableEngineeringAnalysis({ ...emptyAnalysis, evidenceRefs: [id] }),
  false,
  'an inline evidence link does not need a second empty details panel',
);
assert.equal(
  hasReadableEngineeringAnalysis({
    ...emptyAnalysis,
    observations: [{ summary: '프로그램을 확인했습니다.' }],
  }),
  true,
);
console.log('PASS: readable evidence links, unchanged source, optional row inventory with visible warnings');
