import test from 'node:test';
import assert from 'node:assert/strict';

test('failed bridge decision refreshes persisted workspace without a success notice', async () => {
  const handlers = new Map();
  const root = {
    innerHTML: '',
    addEventListener(name, handler) { handlers.set(name, handler); },
  };
  const original = { document: globalThis.document, location: globalThis.location,
    fetch: globalThis.fetch };
  const student = {
    student_id: 's1', display_name: 'DEMO Student', revision: 1,
    source: { channel: 'OTHER' }, education: {}, targets: {},
    sales: { stage: 'NEW', contact_permission: 'ALLOWED' },
    events: [{ event_id: 'inbound-1', event_type: 'INBOUND_RECEIVED',
      payload: { raw_text: 'DEMO: I want to know about applications' } }],
    memory: [], drafts: [], sent: [], decision: null, offer: null,
    draft_status: 'NONE', has_pending_offer: false,
  };
  let failedTurnRecorded = false;
  let workspaceReads = 0;
  globalThis.document = { querySelector: () => root };
  globalThis.location = { hostname: '127.0.0.1', search: '?bridge=offline' };
  globalThis.fetch = async (path, options) => {
    let payload;
    if (path === '/api/health') payload = { agent_mode: 'LOCAL_OLLAMA' };
    else if (path === '/api/students') payload = [student];
    else if (path === '/api/students/s1' && options.method === 'GET') {
      workspaceReads++;
      payload = { ...student, events: failedTurnRecorded ? [...student.events,
        { event_id: 'context-1', event_type: 'TURN_CONTEXT_BUILT', payload: {} }] : student.events };
    } else if (path === '/api/students/s1/decision') {
      failedTurnRecorded = true;
      payload = { pipeline: { status: 'HANDOFF', reason_codes: ['MODEL_PROVIDER_UNAVAILABLE_OR_INVALID'] },
        workspace: student };
    } else throw new Error(`unexpected request: ${path}`);
    return { ok: true, json: async () => payload };
  };

  try {
    await import('../app.js?bridge-outcome-test');
    await new Promise(resolve => setImmediate(resolve));
    const click = action => handlers.get('click')({ target: {
      closest: () => ({ dataset: { action, id: 's1' } }),
    } });
    await click('open-student');
    await click('request-decision');
    assert.equal(workspaceReads, 2);
    assert.match(root.innerHTML, /HANDOFF.*MODEL_PROVIDER_UNAVAILABLE_OR_INVALID/);
    assert.match(root.innerHTML, /TURN_CONTEXT_BUILT/);
    assert.doesNotMatch(root.innerHTML, /draft passed the current Gate/);
  } finally {
    globalThis.document = original.document;
    globalThis.location = original.location;
    globalThis.fetch = original.fetch;
  }
});

test('standard offer is shown as a standard quote without custom approval claim', async () => {
  const handlers = new Map();
  const root = { innerHTML: '', addEventListener(name, handler) { handlers.set(name, handler); } };
  const original = { document: globalThis.document, location: globalThis.location,
    fetch: globalThis.fetch };
  const student = {
    student_id: 's2', display_name: 'DEMO Student', revision: 1,
    source: { channel: 'OTHER' }, education: {}, targets: {},
    sales: { stage: 'CONSULTING', contact_permission: 'ALLOWED' },
    events: [], memory: [], drafts: [], sent: [], decision: null,
    offer: { state: 'STANDARD_OFFER', quote: {
      product_id: 'A', price: 1000, policy_version: 'portfolio-synthetic-pricing-v1',
    } },
    draft_status: 'NONE', has_pending_offer: false,
  };
  globalThis.document = { querySelector: () => root };
  globalThis.location = { hostname: '127.0.0.1', search: '?bridge=offline' };
  globalThis.fetch = async path => ({ ok: true, json: async () =>
    path === '/api/health' ? { agent_mode: 'LOCAL_OLLAMA' } :
      path === '/api/students' ? [student] : student });

  try {
    await import('../app.js?standard-offer-test');
    await new Promise(resolve => setImmediate(resolve));
    await handlers.get('click')({ target: { closest: () => ({
      dataset: { action: 'open-student', id: 's2' },
    }) } });
    assert.match(root.innerHTML, /<h2>Standard offer<\/h2>/);
    assert.match(root.innerHTML, /1000 CNY/);
    assert.match(root.innerHTML, /portfolio-synthetic-pricing-v1/);
    assert.doesNotMatch(root.innerHTML, /Custom offer · Approved by a human/);
    assert.doesNotMatch(root.innerHTML, /Demo: approve package and price/);
  } finally {
    globalThis.document = original.document;
    globalThis.location = original.location;
    globalThis.fetch = original.fetch;
  }
});

test('bridge custom offer form exposes terms and all three role confirmations', async () => {
  const handlers = new Map();
  const root = { innerHTML: '', addEventListener(name, handler) { handlers.set(name, handler); } };
  const original = { document: globalThis.document, location: globalThis.location,
    fetch: globalThis.fetch };
  const student = {
    student_id: 's3', display_name: 'DEMO Student', revision: 1,
    source: { channel: 'OTHER' }, education: {}, targets: {},
    sales: { stage: 'CONSULTING', contact_permission: 'ALLOWED' },
    events: [], memory: [], drafts: [], sent: [], decision: null,
    offer: { state: 'CUSTOM_OFFER_PROPOSAL', proposal: {
      proposed_scope: ['Essay support'], explicit_exclusions: ['Submission'],
      proposed_price: 9800, payment_terms: ['Single payment'],
      customer_need_evidence: ['Only essay support'], delivery_requirements: ['Check delivery capacity'], risks: [],
    } },
    draft_status: 'NONE', has_pending_offer: true,
  };
  globalThis.document = { querySelector: () => root };
  globalThis.location = { hostname: '127.0.0.1', search: '?bridge=offline' };
  globalThis.fetch = async path => ({ ok: true, json: async () =>
    path === '/api/health' ? { agent_mode: 'SCRIPTED' } :
      path === '/api/students' ? [student] : student });

  try {
    await import('../app.js?custom-offer-form-test');
    await new Promise(resolve => setImmediate(resolve));
    await handlers.get('click')({ target: { closest: () => ({
      dataset: { action: 'open-student', id: 's3' },
    }) } });
    for (const field of ['scope', 'exclusions', 'payment_terms', 'valid_until',
      'role_PRODUCT', 'role_DELIVERY', 'role_PRICING', 'confirmed_approval']) {
      assert.match(root.innerHTML, new RegExp(`name="${field}"`));
    }
    assert.match(root.innerHTML, /This is an internal proposal/);
    assert.match(root.innerHTML, /name="review_action" value="REQUEST_CHANGES"/);
    assert.match(root.innerHTML, /name="review_action" value="REJECT"/);
    assert.match(root.innerHTML, /name="comment" rows="3" required/);
  } finally {
    globalThis.document = original.document;
    globalThis.location = original.location;
    globalThis.fetch = original.fetch;
  }
});

test('reviewed custom offers stay internal and never show approval or send controls', async () => {
  for (const action of ['REQUEST_CHANGES', 'REJECT']) {
    const handlers = new Map();
    const root = { innerHTML: '', addEventListener(name, handler) { handlers.set(name, handler); } };
    const original = { document: globalThis.document, location: globalThis.location,
      fetch: globalThis.fetch };
    const student = {
      student_id: 's4', display_name: 'DEMO Student', revision: 1,
      source: { channel: 'OTHER' }, education: {}, targets: {},
      sales: { stage: 'CONSULTING', contact_permission: 'ALLOWED' },
      events: [{ event_id: 'review-1', event_type: 'CUSTOM_OFFER_REVIEW',
        payload: { action, comment: 'Delivery conditions cannot be confirmed' } }],
      memory: [], drafts: [], sent: [], decision: null,
      offer: { state: 'CUSTOM_OFFER_PROPOSAL', review_action: action,
        review_event_id: 'review-1', proposal: { proposed_scope: ['Essay support'],
          explicit_exclusions: ['Submission'], proposed_price: 9800,
          payment_terms: ['Single payment'], customer_need_evidence: ['Only essay support'],
          delivery_requirements: ['Check delivery capacity'], risks: ['Delivery unconfirmed'] } },
      draft_status: 'NONE', has_pending_offer: false,
    };
    globalThis.document = { querySelector: () => root };
    globalThis.location = { hostname: '127.0.0.1', search: '?bridge=offline' };
    globalThis.fetch = async path => ({ ok: true, json: async () =>
      path === '/api/health' ? { agent_mode: 'SCRIPTED' } :
        path === '/api/students' ? [student] : student });
    try {
      await import(`../app.js?reviewed-custom-offer-${action}`);
      await new Promise(resolve => setImmediate(resolve));
      await handlers.get('click')({ target: { closest: () => ({
        dataset: { action: 'open-student', id: 's4' },
      }) } });
      assert.match(root.innerHTML, new RegExp(action === 'REJECT' ? 'Custom offer · Rejected' : 'Custom offer · Returned for changes'));
      assert.match(root.innerHTML, /Delivery conditions cannot be confirmed/);
      assert.match(root.innerHTML, /9800 CNY \(internal, unapproved\)/);
      assert.doesNotMatch(root.innerHTML, /Custom offer · Approved by a human/);
      assert.doesNotMatch(root.innerHTML, /Approved price/);
      assert.doesNotMatch(root.innerHTML, /id="offer-form"|id="sent-form"|data-action="approve-draft"/);
    } finally {
      globalThis.document = original.document;
      globalThis.location = original.location;
      globalThis.fetch = original.fetch;
    }
  }
});
