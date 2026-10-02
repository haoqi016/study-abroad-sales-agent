import test from 'node:test';
import assert from 'node:assert/strict';

test('progress panel keeps raw reply and prediction visible through a human correction', async () => {
  const handlers = new Map();
  const root = { innerHTML: '', addEventListener(name, handler) { handlers.set(name, handler); } };
  const original = { document: globalThis.document, location: globalThis.location, fetch: globalThis.fetch,
    FormData: globalThis.FormData };
  const prediction = { inbound_event_id: 'reply-1', predicted_signal: 'UNCLEAR',
    supporting_spans: [], confidence: 0.3 };
  let labeled = false;
  const workspace = () => ({
    student_id: 's1', display_name: 'DEMO Student', revision: labeled ? 3 : 2,
    source: { channel: 'OTHER' }, education: {}, targets: {}, memory: [], drafts: [], sent: [],
    sales: { stage: labeled ? 'DO_NOT_CONTACT' : 'CONSULTING',
      contact_permission: labeled ? 'DO_NOT_CONTACT' : 'ALLOWED' },
    events: [{ event_id: 'reply-1', event_type: 'INBOUND_RECEIVED',
      payload: { raw_text: 'DEMO: please do not contact me <exact words>' } }],
    progress_assessments: [{ event_id: 'assessment-1', payload: prediction }],
    progress_labels: labeled ? [{ event_id: 'label-1', occurred_at: '2026-10-02T00:00:00Z',
      payload: { assessment_event_id: 'assessment-1', human_label: 'DO_NOT_CONTACT',
        reason: 'The exact words request no further contact', labeled_by: 'demo-operator' } }] : [],
    effective_progress_labels: [{ assessment_event_id: 'assessment-1', predicted_signal: 'UNCLEAR',
      human_label: labeled ? 'DO_NOT_CONTACT' : null }],
    draft_status: 'NONE', offer: null, has_pending_offer: false,
  });
  const posts = [];
  globalThis.document = { querySelector: () => root };
  globalThis.location = { hostname: '127.0.0.1', search: '?bridge=offline' };
  globalThis.FormData = class { constructor() { return [
    ['assessment_event_id', 'assessment-1'], ['human_label', 'DO_NOT_CONTACT'],
    ['reason', 'The exact words request no further contact'],
  ]; } };
  globalThis.fetch = async (path, options) => {
    if (path === '/api/students/s1/progress-label') { posts.push(JSON.parse(options.body)); labeled = true; }
    return { ok: true, json: async () => path === '/api/health' ? { agent_mode: 'SCRIPTED' } :
      path === '/api/students' ? [workspace()] : workspace() };
  };
  try {
    await import('../app.js?progress-label-ui-test');
    await new Promise(resolve => setImmediate(resolve));
    await handlers.get('click')({ target: { closest: () => ({ dataset: { action: 'open-student', id: 's1' } }) } });
    assert.match(root.innerHTML, /DEMO: please do not contact me &lt;exact words&gt;/);
    assert.match(root.innerHTML, /Original model prediction[\s\S]*Cannot determine progress from the message/);
    assert.match(root.innerHTML, /id="progress-label-form"/);
    await handlers.get('submit')({ preventDefault() {}, target: { id: 'progress-label-form' } });
    assert.equal(posts.length, 1);
    assert.equal(posts[0].assessment_event_id, 'assessment-1');
    assert.match(root.innerHTML, /Original model prediction[\s\S]*Cannot determine progress from the message/);
    assert.match(root.innerHTML, /Latest valid human label[\s\S]*Explicit do not contact request/);
    assert.match(root.innerHTML, /The exact words request no further contact/);
    assert.match(root.innerHTML, /View label history \(1\)/);
    assert.doesNotMatch(root.innerHTML, /data-action="request-decision"|id="offer-form"|id="sent-form"/);
  } finally {
    Object.assign(globalThis, original);
  }
});
