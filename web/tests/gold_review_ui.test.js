import test from 'node:test';
import assert from 'node:assert/strict';
import { OfflineBridgeAdapter } from '../bridge_api.js';

const location = { hostname: '127.0.0.1', search: '?bridge=offline' };

test('pause outcomes and human resume use the synthetic bridge without claiming a Gate result', async () => {
  const calls = [];
  const api = new OfflineBridgeAdapter(async (path, options) => {
    calls.push({ path, options });
    return { ok: true, json: async () => ({
      pipeline: { status: path.endsWith('decision') ? 'ASK_HUMAN' : 'REVIEW_REQUIRED' },
      workspace: { events: [], drafts: [], sent: [] },
    }) };
  }, location);
  assert.equal((await api.requestDecision('demo')).pipeline_outcome.status, 'ASK_HUMAN');
  await assert.rejects(() => api.resumeGoldPause('demo', {
    pause_event_id: 'pause-1', action: 'SELECT_STRATEGY', strategy_choice: 'FIRST',
  }), /explain/);
  await assert.rejects(() => api.resumeGoldPause('demo', {
    pause_event_id: 'pause-1', action: 'SELECT_STRATEGY', strategy_choice: 'FINAL', reason: 'No distinct compared decision',
  }), /independent decision/);
  assert.equal(calls.length, 1);
  const result = await api.resumeGoldPause('demo', {
    pause_event_id: 'pause-1', action: 'SELECT_STRATEGY', strategy_choice: 'FIRST', reason: 'Synthetic evidence supports the first decision',
  });
  assert.equal(result.pipeline_outcome.status, 'REVIEW_REQUIRED');
  assert.equal(calls[1].path, '/api/students/demo/gold-resume');
  const body = JSON.parse(calls[1].options.body);
  assert.equal(body.demo_only, true);
  assert.equal(body.strategy_choice, 'FIRST');
  assert.equal(body.gate_event_id, undefined);
  assert.equal(body.passed, undefined);
});

async function showWorkspace(workspace, suffix) {
  const handlers = new Map();
  const root = { innerHTML: '', addEventListener(name, handler) { handlers.set(name, handler); } };
  const original = { document: globalThis.document, location: globalThis.location, fetch: globalThis.fetch };
  globalThis.document = { querySelector: () => root };
  globalThis.location = location;
  globalThis.fetch = async path => ({ ok: true, json: async () => path === '/api/health'
    ? { agent_mode: 'SCRIPTED' } : path === '/api/students' ? [workspace] : workspace });
  try {
    await import(`../app.js?gold-${suffix}`);
    await new Promise(resolve => setImmediate(resolve));
    await handlers.get('click')({ target: { closest: () => ({ dataset: { action: 'open-student', id: 'demo' } }) } });
    return root.innerHTML;
  } finally {
    globalThis.document = original.document;
    globalThis.location = original.location;
    globalThis.fetch = original.fetch;
  }
}

function workspace(pause) {
  const decision = { purchase_readiness: { assessment: 'Unverified' },
    selected_strategy: { purchase_blocker: 'Scope unclear' },
    current_objective: { goal: 'Clarify scope', why_now: 'Student asked' },
    action_plan: { selected_action: 'Invite clarification' },
    content_contract: { semantic_draft: 'Internal draft only' },
    input_snapshot: { hidden_reference: 'HIDDEN_SNAPSHOT_SENTINEL' } };
  const stages = [
    { event_id: 'd0', event_type: 'GOLD_STAGE_RECORDED', payload: { stage: 'D0', output: { first_decision: decision } } },
    { event_id: 'g0', event_type: 'GOLD_STAGE_RECORDED', payload: { stage: 'G0', output: {
      status: 'MATCH', gold_ids: ['PUBLIC-SCOPE-01'], reason: 'Synthetic development situation',
    } } },
    { event_id: 'd1', event_type: 'GOLD_STAGE_RECORDED', payload: { stage: 'D1', pause_reason: 'ASK_HUMAN', output: {
      comparison: { similarities: ['Same question'], key_differences: ['No verified price'], disposition: 'ASK_HUMAN', reason: 'Human choice required' },
      final_decision: decision,
    } } },
    { event_id: 'g1', event_type: 'GOLD_STAGE_RECORDED', payload: { stage: 'G1', pause_reason: 'NO_LANGUAGE_MATCH', output: {
      status: 'NO_LANGUAGE_MATCH', gold_id: null, reason: 'No matching example',
    } } },
  ];
  return { student_id: 'demo', display_name: 'DEMO Student', revision: 1, source: {}, education: {}, targets: {},
    sales: { stage: 'NEW', contact_permission: 'ALLOWED' }, events: [], drafts: [], sent: [], memory: [],
    gold_homework_enabled: true, gold_trace: stages,
    gold_pending: pause === 'ASK_HUMAN' ? stages[1] : stages[2], gold_pending_status: pause,
    gold_language_catalog: [{ gold_id: 'SYN-01', source_version: 'synthetic-v1', neutral_situation: 'Fictional scope question' }] };
}

for (const pause of ['ASK_HUMAN', 'NO_LANGUAGE_MATCH']) {
  test(`review panel renders ${pause} with a human reason and without hidden reference fields`, async () => {
    const html = await showWorkspace(workspace(pause), pause);
    assert.match(html, /Independent decision \(D0\)/);
    assert.match(html, /Decision example \(G0\): PUBLIC-SCOPE-01/);
    assert.match(html, /Decision after comparison \(D1\)/);
    assert.doesNotMatch(html, /HIDDEN_SNAPSHOT_SENTINEL/);
    assert.match(html, /data-action="request-decision" disabled/);
    if (pause === 'ASK_HUMAN') {
      assert.match(html, /id="gold-resolution-form"/);
      assert.match(html, /name="reason"[^>]*required/);
      assert.match(html, /name="strategy_choice" value="FIRST"/);
      assert.doesNotMatch(html, /Use the compared decision/);
    } else assert.doesNotMatch(html, /id="gold-resolution-form"/);
  });
}

test('failed resume shows a handoff and no repeat resolution control', async () => {
  const w = workspace('RESUME_HANDOFF');
  w.gold_pending = w.gold_trace[2];
  const html = await showWorkspace(w, 'resume-handoff');
  assert.match(html, /Reference review needs a fresh human-reviewed turn/);
  assert.match(html, /No customer draft is ready/);
  assert.doesNotMatch(html, /id="gold-resolution-form"/);
});

test('style result is described as a heuristic check that still needs human review', async () => {
  const w = workspace('ASK_HUMAN');
  w.gold_pending = null;
  w.gold_pending_status = null;
  w.gold_trace.push(
    { event_type: 'GOLD_STAGE_RECORDED', payload: { stage: 'C0', output: { status: 'RECORDED' } } },
    { event_type: 'GOLD_STAGE_RECORDED', payload: { stage: 'C0_STYLE_RECHECK', output: { status: 'PASS', issues: [] } } },
  );
  const html = await showWorkspace(w, 'style');
  assert.match(html, /Automated style risk check: Passed a limited heuristic check; human review is still required/);
  assert.doesNotMatch(html, /passed Gold similarity/i);
});
