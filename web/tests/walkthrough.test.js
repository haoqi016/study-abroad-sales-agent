import test from 'node:test';
import assert from 'node:assert/strict';
import { startScriptedWalkthrough, WALKTHROUGH_MESSAGE } from '../walkthrough.js';

test('one-click walkthrough follows the existing student, inbound, decision path and stops at review', async () => {
  const calls = [];
  const api = {
    async createStudent(record) {
      calls.push(['create', record]);
      return { student_id: 'fictional-1' };
    },
    async recordInbound(id, message) {
      calls.push(['inbound', id, message]);
    },
    async requestDecision(id) {
      calls.push(['decision', id]);
      return { student_id: id, pipeline_outcome: { status: 'REVIEW_REQUIRED' },
        drafts: [{ status: 'PENDING_REVIEW' }] };
    },
  };
  const result = await startScriptedWalkthrough(api);
  assert.equal(result.student_id, 'fictional-1');
  assert.deepEqual(calls.map(call => call[0]), ['create', 'inbound', 'decision']);
  assert.match(calls[0][1].display_name, /DEMO/);
  assert.equal(calls[0][1].sales.contact_permission, 'ALLOWED');
  assert.equal(calls[1][2].raw_text, WALKTHROUGH_MESSAGE);
});

test('walkthrough does not report success without a reviewable draft', async () => {
  const api = {
    async createStudent() { return { student_id: 'fictional-2' }; },
    async recordInbound() {},
    async requestDecision() { return { pipeline_outcome: { status: 'APPROVAL_REQUIRED' }, drafts: [] }; },
  };
  await assert.rejects(startScriptedWalkthrough(api), /reviewable draft/);
});

test('student list plays a labeled example and does not trigger approval or send', async () => {
  const handlers = new Map();
  const root = { innerHTML: '', addEventListener(name, handler) { handlers.set(name, handler); } };
  const original = { document: globalThis.document, location: globalThis.location, fetch: globalThis.fetch };
  const calls = [];
  let created = false;
  const workspace = () => ({
    student_id: 'fictional-3', display_name: 'DEMO Student Alex', revision: 1,
    source: { channel: 'OTHER' }, education: {},
    targets: { countries: ['SG'], universities: [], programs_or_majors: [] },
    sales: { stage: 'CONSULTING', contact_permission: 'ALLOWED' }, memory: [], sent: [],
    events: [{ event_id: 'inbound-3', event_type: 'INBOUND_RECEIVED',
      payload: { raw_text: WALKTHROUGH_MESSAGE } }],
    decision: { current_objective: { goal: 'Clarify service needs' },
      action_plan: { selected_action: 'Ask which service area matters' },
      content_contract: { semantic_draft: 'Which part needs help?' } },
    drafts: [{ draft_id: 'draft-3', status: 'DRAFTED', messages: [{ type: 'text', content: 'Which part needs help?' }] }],
    draft_status: 'DRAFTED', has_pending_offer: false,
  });
  globalThis.document = { querySelector: () => root };
  globalThis.location = { hostname: '127.0.0.1', search: '?bridge=offline' };
  globalThis.fetch = async (path, options = {}) => {
    calls.push([options.method || 'GET', path]);
    const payload = path === '/api/health' ? { agent_mode: 'SCRIPTED' } :
      path === '/api/students' && options.method === 'GET' ? (created ? [workspace()] : []) :
      path === '/api/students' && options.method === 'POST' ? (created = true, workspace()) :
      path.endsWith('/decision') ? { pipeline: { status: 'REVIEW_REQUIRED' }, workspace: workspace() } : workspace();
    return { ok: true, json: async () => payload };
  };
  try {
    await import('../app.js?walkthrough-ui-test');
    await new Promise(resolve => setImmediate(resolve));
    assert.match(root.innerHTML, /Play scripted walkthrough/);
    assert.doesNotMatch(root.innerHTML, /DEMO\/OFFLINE|Synthetic demo only\. Do not enter real student information/);
    const click = action => handlers.get('click')({ target: { closest: () => ({ dataset: { action } }) } });
    await click('play-walkthrough');
    assert.match(root.innerHTML, /Scripted example · Step 1 of 4/);
    assert.match(root.innerHTML, /Clarify service needs/);
    await click('walkthrough-toggle');
    assert.deepEqual(calls.filter(([method]) => method === 'POST').map(([, path]) => path), [
      '/api/students', '/api/students/fictional-3/inbound', '/api/students/fictional-3/decision',
    ]);
  } finally {
    globalThis.document = original.document;
    globalThis.location = original.location;
    globalThis.fetch = original.fetch;
  }
});
