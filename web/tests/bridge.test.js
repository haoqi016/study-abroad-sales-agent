import test from 'node:test';
import assert from 'node:assert/strict';
import { OfflineBridgeAdapter, bridgeSelected, workspaceView } from '../bridge_api.js';
import { studentCreatePayload, studentUpdatePayload } from '../api.js';

const local = { hostname: '127.0.0.1', search: '?bridge=offline' };

test('bridge selection requires explicit local URL; demo remains default', () => {
  assert.equal(bridgeSelected(local), true);
  assert.equal(bridgeSelected({ hostname: '127.0.0.1', search: '' }), false);
  assert.equal(bridgeSelected({ hostname: 'example.com', search: '?bridge=offline' }), false);
  assert.throws(() => new OfflineBridgeAdapter(async () => {}, { hostname: 'example.com', search: '?bridge=offline' }));
});

test('bridge profile create is one synthetic request without a platform identifier', async () => {
  const requests = [];
  const api = new OfflineBridgeAdapter(async (path, options) => {
    requests.push({ path, options });
    return { ok: true, json: async () => ({ student_id: 's1', source: { channel: 'OTHER' },
      targets: { universities: ['NUS'], programs_or_majors: ['统计'] },
      sales: { decision_maker: '本人', next_action_reason: '演示回访' }, events: [], drafts: [], sent: [] }) };
  }, local);
  const record = studentCreatePayload({ display_name: '合成同学', channel: 'OTHER', platform_handle: 'demo-only',
    target_universities: 'NUS', target_programs: '统计', decision_maker: '本人',
    next_action_at: '2026-10-05T15:30', next_action_reason: '演示回访' }, true);
  const workspace = await api.createStudent(record);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/students');
  const body = JSON.parse(requests[0].options.body);
  assert.deepEqual(body.source, { channel: 'OTHER' });
  assert.equal(JSON.stringify(body).includes('platform_handle'), false);
  assert.equal(body.demo_only, true);
  assert.deepEqual(body.targets.universities, ['NUS']);
  assert.equal(body.sales.decision_maker, '本人');
  assert.equal(workspace.sales.next_action_reason, '演示回访');

  const update = studentUpdatePayload({ target_countries: 'SG', target_universities: 'NTU',
    target_programs: '商科', stage: 'CONSULTING', contact_permission: 'DO_NOT_CONTACT',
    next_action_at: '', next_action_reason: '' }, 1);
  await api.updateStudent('s1', update);
  assert.equal(requests[1].path, '/api/students/s1');
  assert.equal(JSON.parse(requests[1].options.body).sales.contact_permission, 'DO_NOT_CONTACT');
  assert.equal(JSON.stringify(JSON.parse(requests[1].options.body)).includes('platform_handle'), false);
});

test('workspace mapping preserves source event and keeps draft out of customer history', () => {
  const w = workspaceView({
    student_id: 's1', source: {}, targets: {}, education: {}, sales: {}, memory: [], sent: [],
    draft_status: 'DRAFTED', drafts: [{ draft_id: 'd1', revision: 1, status: 'DRAFTED', messages: [] }],
    events: [{ event_id: 'e1', event_type: 'INBOUND_RECEIVED', payload: { raw_text: '合成原话' } },
      { event_id: 'd1', event_type: 'DRAFTED', payload: { rendered_text: '草稿' } }],
    decision: { input_event_ids: ['e1'] },
  });
  assert.equal(w.events[0].raw_content, '合成原话');
  assert.equal(w.events[1].raw_content, null);
  assert.deepEqual(w.decision.input_event_ids, ['e1']);
  assert.equal(w.decision.demo_only, true);
  assert.equal(w.drafts[0].status, 'PENDING_REVIEW');
});

test('bridge requires external send assertion and sends no HTTP request without it', async () => {
  const requests = [];
  const api = new OfflineBridgeAdapter(async (path, options) => {
    requests.push({ path, options });
    return { ok: true, json: async () => ({ events: [], drafts: [], sent: [] }) };
  }, local);
  await assert.rejects(api.recordActualSent('s1', { actual_sent_text: '合成已发' }), /确认已在外部渠道/);
  assert.equal(requests.length, 0);
  await api.recordActualSent('s1', { actual_sent_text: '合成已发', confirmed_external_send: true });
  assert.equal(requests[0].path, '/api/students/s1/actual-sent');
  assert.equal(requests[0].options.headers['X-Sales-Demo-Bridge'], '1');
  assert.equal(JSON.parse(requests[0].options.body).confirmed_external_send, true);
});

test('bridge errors are surfaced and never treated as success', async () => {
  const api = new OfflineBridgeAdapter(async () => ({ ok: false, status: 400,
    json: async () => ({ error: 'current_approved_draft_required' }) }), local);
  await assert.rejects(api.approveDraft('s1'), /current_approved_draft_required/);
  const handoff = new OfflineBridgeAdapter(async () => ({ ok: true, json: async () => ({
    pipeline: { status: 'HANDOFF', reason_codes: ['RUNTIME_CONTRACT_VIOLATION'] }, workspace: {},
  }) }), local);
  await assert.rejects(handoff.requestDecision('s1'), /HANDOFF/);
});

test('progress label posts a scoped synthetic event and requires reason', async () => {
  const requests = [];
  const api = new OfflineBridgeAdapter(async (path, options) => {
    requests.push({ path, body: JSON.parse(options.body) });
    return { ok: true, json: async () => ({ events: [], drafts: [], sent: [] }) };
  }, local);
  await assert.rejects(api.labelProgressAssessment('s1', {
    assessment_event_id: 'a1', human_label: 'NEUTRAL', reason: '   ',
  }), /填写依据/);
  assert.equal(requests.length, 0);
  await api.labelProgressAssessment('s1', {
    assessment_event_id: 'a1', human_label: 'NEUTRAL', reason: ' 学生没有明确承诺 ',
  });
  assert.deepEqual(requests, [{ path: '/api/students/s1/progress-label', body: {
    demo_only: true, assessment_event_id: 'a1', human_label: 'NEUTRAL', reason: '学生没有明确承诺',
  } }]);
});

test('decision outcomes keep internal offer and stop states without claiming a draft', async () => {
  for (const [status, reason] of [['APPROVAL_REQUIRED', 'CUSTOM_OFFER_PENDING'],
    ['STOPPED', 'STOP_REQUIRED'], ['REVIEW_REQUIRED', null]]) {
    const rawWorkspace = { student_id: 's1', events: [], drafts: [], sent: [],
      has_pending_offer: status === 'APPROVAL_REQUIRED',
      offer: status === 'APPROVAL_REQUIRED' ? { state: 'CUSTOM_OFFER_PROPOSAL',
        proposal: { proposed_scope: ['文书'] } } : null };
    const api = new OfflineBridgeAdapter(async () => ({ ok: true, json: async () => ({
      pipeline: { status, reason_codes: reason ? [reason] : [] }, workspace: rawWorkspace,
    }) }), local);
    const result = await api.requestDecision('s1');
    assert.equal(result.pipeline_outcome.status, status);
    assert.equal(result.drafts.length, 0);
    assert.equal(result.has_pending_offer, status === 'APPROVAL_REQUIRED');
  }
});

test('failed decision and rejected draft are never returned as success', async () => {
  for (const status of ['HANDOFF', 'REVISE_REQUIRED']) {
    const api = new OfflineBridgeAdapter(async () => ({ ok: true, json: async () => ({
      pipeline: { status, reason_codes: ['HARD_GATE'] },
      workspace: { events: [], drafts: [], sent: [] },
    }) }), local);
    await assert.rejects(api.requestDecision('s1'), new RegExp(`${status} HARD_GATE`));
  }
});

test('synthetic offer approval requires an explicit human assertion and sends scoped fields', async () => {
  const requests = [];
  const api = new OfflineBridgeAdapter(async (path, options) => {
    requests.push({ path, options });
    return { ok: true, json: async () => ({ events: [], drafts: [], sent: [] }) };
  }, local);
  const approval = { offer_id: 'demo-offer-s1', price: '9800', scope: ['文书'], exclusions: ['递交'],
    payment_terms: ['一次付清'], valid_until: null,
    confirmed_roles: ['PRODUCT', 'DELIVERY', 'PRICING'], confirmed_approval: true };
  await assert.rejects(api.approveCustomOffer('s1', { ...approval, confirmed_approval: false }), /人工核对/);
  assert.equal(requests.length, 0);
  await assert.rejects(api.approveCustomOffer('s1', { ...approval,
    confirmed_roles: ['PRODUCT', 'PRICING'] }), /三个审批角色/);
  assert.equal(requests.length, 0);
  await api.approveCustomOffer('s1', approval);
  assert.equal(requests[0].path, '/api/students/s1/offer-review');
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    action: 'APPROVE',
    offer_id: 'demo-offer-s1', price: 9800, scope: ['文书'], exclusions: ['递交'],
    payment_terms: ['一次付清'], valid_until: null,
    confirmed_roles: ['PRODUCT', 'DELIVERY', 'PRICING'], demo_only: true,
  });
});

test('custom offer change and reject reviews require reasons and carry no approval fields', async () => {
  const requests = [];
  const api = new OfflineBridgeAdapter(async (path, options) => {
    requests.push({ path, body: JSON.parse(options.body) });
    return { ok: true, json: async () => ({ events: [], drafts: [], sent: [],
      offer: { state: 'CUSTOM_OFFER_PROPOSAL', review_action: JSON.parse(options.body).action },
      has_pending_offer: false }) };
  }, local);
  await assert.rejects(api.reviewCustomOffer('s1', { action: 'REJECT', comment: '  ' }), /审核理由/);
  assert.equal(requests.length, 0);
  for (const action of ['REQUEST_CHANGES', 'REJECT']) {
    const result = await api.reviewCustomOffer('s1', { action, offer_id: 'offer-s1', comment: '交付条件需核实' });
    assert.equal(result.offer.review_action, action);
    assert.equal(result.has_pending_offer, false);
  }
  assert.deepEqual(requests.map(request => request.path), [
    '/api/students/s1/offer-review', '/api/students/s1/offer-review',
  ]);
  assert.deepEqual(requests[0].body, { action: 'REQUEST_CHANGES', offer_id: 'offer-s1',
    comment: '交付条件需核实', demo_only: true });
  assert.deepEqual(requests[1].body, { action: 'REJECT', offer_id: 'offer-s1',
    comment: '交付条件需核实', demo_only: true });
});
