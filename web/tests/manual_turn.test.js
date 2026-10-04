import test from 'node:test';
import assert from 'node:assert/strict';

function rawWorkspace({ inbound = false, drafted = false, stopped = false } = {}) {
  return {
    student_id: 's1', display_name: 'DEMO Student', revision: 1,
    source: { channel: 'OTHER' }, education: {},
    targets: { countries: [], universities: [], programs_or_majors: [] },
    sales: { stage: 'NEW', contact_permission: stopped ? 'DO_NOT_CONTACT' : 'ALLOWED' },
    events: inbound ? [{ event_id: 'e1', event_type: 'INBOUND_RECEIVED',
      payload: { raw_text: 'I need help planning my application.' } }] : [],
    memory: [], sent: [], offer: null, has_pending_offer: false,
    decision: drafted ? { current_objective: { goal: 'Clarify service needs' },
      action_plan: { selected_action: 'Ask which service area matters' } } : null,
    drafts: drafted ? [{ draft_id: 'd1', status: 'DRAFTED',
      messages: [{ type: 'text', content: 'Which part of your application needs help?' }] }] : [],
    draft_status: drafted ? 'DRAFTED' : 'NONE',
  };
}

async function runCase(caseName, { stopped = false, failDecision = false } = {}) {
  const original = { document: globalThis.document, location: globalThis.location,
    fetch: globalThis.fetch, FormData: globalThis.FormData };
  const handlers = new Map();
  const root = { innerHTML: '', addEventListener(name, handler) { handlers.set(name, handler); } };
  const calls = [];
  let inbound = false;
  let drafted = false;
  globalThis.document = { querySelector: () => root };
  globalThis.location = { hostname: '127.0.0.1', search: '?bridge=offline' };
  globalThis.FormData = class { *[Symbol.iterator]() {
    yield ['raw_text', 'I need help planning my application.'];
    yield ['occurred_at', ''];
  } };
  globalThis.fetch = async (path, options = {}) => {
    const method = options.method || 'GET';
    calls.push([method, path]);
    let payload;
    if (path === '/api/health') payload = { agent_mode: 'SCRIPTED' };
    else if (path === '/api/students') payload = [rawWorkspace({ inbound, drafted, stopped })];
    else if (path === '/api/students/s1/inbound') {
      inbound = true;
      payload = rawWorkspace({ inbound, drafted, stopped });
    } else if (path === '/api/students/s1/decision') {
      if (failDecision) payload = { pipeline: { status: 'HANDOFF', reason_codes: ['MODEL_PROVIDER_UNAVAILABLE'] },
        workspace: rawWorkspace({ inbound, drafted, stopped }) };
      else {
        drafted = true;
        payload = { pipeline: { status: 'REVIEW_REQUIRED' }, workspace: rawWorkspace({ inbound, drafted, stopped }) };
      }
    } else if (path === '/api/students/s1') payload = rawWorkspace({ inbound, drafted, stopped });
    else throw new Error(`Unexpected path: ${path}`);
    return { ok: true, json: async () => payload };
  };
  try {
    await import(`../app.js?manual-turn-${caseName}`);
    await new Promise(resolve => setImmediate(resolve));
    await handlers.get('click')({ target: { closest: () => ({ dataset: { action: 'open-student', id: 's1' } }) } });
    assert.match(root.innerHTML, stopped ? /<button type="submit" class="button primary">Record student message<\/button>/ : /Record message and generate reply/);
    await handlers.get('submit')({ preventDefault() {}, target: { id: 'inbound-form' } });
    return { html: root.innerHTML, calls };
  } finally {
    globalThis.document = original.document;
    globalThis.location = original.location;
    globalThis.fetch = original.fetch;
    globalThis.FormData = original.FormData;
  }
}

test('one student entry records exact words, generates a draft, and updates the visible workflow status', async () => {
  const { html, calls } = await runCase('success');
  assert.deepEqual(calls.filter(([method]) => method === 'POST').map(([, path]) => path), [
    '/api/students/s1/inbound', '/api/students/s1/decision',
  ]);
  assert.match(html, /Reply draft awaiting human review/);
  assert.match(html, /Sales stage: New lead/);
  assert.match(html, /Workflow: Reply draft awaiting human review/);
  assert.match(html, /Which part of your application needs help\?/);
  assert.match(html, /Scripted sample reply/);
  assert.match(html, /Draft only · not sent to the student/);
  assert.doesNotMatch(html, /Approved · unsent|Actual send recorded/);
});

test('a stopped contact records the exact message but does not request a sales reply', async () => {
  const { html, calls } = await runCase('stopped', { stopped: true });
  assert.deepEqual(calls.filter(([method]) => method === 'POST').map(([, path]) => path), [
    '/api/students/s1/inbound',
  ]);
  assert.match(html, /Contact stopped/);
  assert.match(html, /no sales reply was generated/);
});

test('a failed decision keeps the recorded message and does not claim a reply', async () => {
  const { html, calls } = await runCase('handoff', { failDecision: true });
  assert.deepEqual(calls.filter(([method]) => method === 'POST').map(([, path]) => path), [
    '/api/students/s1/inbound', '/api/students/s1/decision',
  ]);
  assert.match(html, /student message was recorded, but no reply draft was produced/i);
  assert.match(html, /MODEL_PROVIDER_UNAVAILABLE/);
  assert.doesNotMatch(html, /Agent reply drafted and workflow status updated/);
});
