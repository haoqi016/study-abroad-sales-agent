import test from 'node:test';
import assert from 'node:assert/strict';

test('remote API mode is visible and offers model decision action', async () => {
  const handlers = new Map();
  const root = { innerHTML: '', addEventListener(name, handler) { handlers.set(name, handler); } };
  const original = { document: globalThis.document, location: globalThis.location, fetch: globalThis.fetch };
  const student = {
    student_id: 'synthetic-1', display_name: 'DEMO Student', revision: 1,
    source: { channel: 'OTHER' }, education: {},
    targets: { countries: [], universities: [], programs_or_majors: [] },
    sales: { stage: 'NEW', contact_permission: 'ALLOWED' },
    events: [], memory: [], drafts: [], sent: [], decision: null, offer: null,
    draft_status: 'NONE', has_pending_offer: false,
  };
  globalThis.document = { querySelector: () => root };
  globalThis.location = { hostname: '127.0.0.1', search: '?bridge=offline' };
  globalThis.fetch = async path => ({ ok: true, json: async () =>
    path === '/api/health' ? { agent_mode: 'REMOTE_API' } :
      path === '/api/students' ? [student] : student });
  try {
    await import('../app.js?remote-api-ui-test');
    await new Promise(resolve => setImmediate(resolve));
    assert.match(root.innerHTML, /API TEST/);
    assert.match(root.innerHTML, /Remote API Agent experiment/);
    await handlers.get('click')({ target: { closest: () => ({ dataset: {
      action: 'open-student', id: 'synthetic-1',
    } }) } });
    assert.match(root.innerHTML, /Run remote API Agent/);
    assert.doesNotMatch(root.innerHTML, /Run synthetic demo decision/);
    await handlers.get('click')({ target: { closest: () => ({ dataset: {
      action: 'nav-me',
    } }) } });
    assert.match(root.innerHTML, /requires --ollama-model or --api-config/);
  } finally {
    globalThis.document = original.document;
    globalThis.location = original.location;
    globalThis.fetch = original.fetch;
  }
});
