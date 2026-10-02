import test from 'node:test';
import assert from 'node:assert/strict';
import { DemoAdapter, customerVisibleHistory, localActionTime, studentCreatePayload, studentUpdatePayload } from '../api.js';

test('profile form maps target and next-action fields into a synthetic record', async () => {
  const data = { display_name: 'DEMO Test Student', channel: 'OTHER', platform_handle: 'demo-only',
    university: 'Demo University', major: 'Mathematics', score: '85', year: 'YEAR_4', country: 'SG',
    target_universities: 'NTU,NUS', target_programs: 'Statistics,Business', stage: 'CONSULTING',
    current_objection: 'Still comparing options', decision_maker: 'Student', contact_permission: 'UNKNOWN',
    next_action_at: '2026-10-05T15:30', next_action_reason: 'Confirm synthetic application plans' };
  const payload = studentCreatePayload(data);
  assert.deepEqual(payload.targets, { countries: ['SG'], universities: ['NTU', 'NUS'], programs_or_majors: ['Statistics', 'Business'] });
  assert.equal(payload.sales.next_action_at, new Date(data.next_action_at).toISOString());
  assert.equal(payload.sales.next_action_status, 'SUGGESTED');
  assert.equal(localActionTime(payload.sales.next_action_at), data.next_action_at);
  const api = new DemoAdapter();
  const created = await api.createStudent(payload);
  let workspace = await api.getWorkspace(created.student_id);
  assert.deepEqual(workspace.targets, payload.targets);
  assert.equal(workspace.sales.decision_maker, 'Student');
  assert.equal(workspace.sales.next_action_reason, 'Confirm synthetic application plans');
  assert.equal(customerVisibleHistory(workspace).length, 0);
  assert.equal((await api.listReminders()).some(item => item.student_id === created.student_id), false);

  const edit = studentUpdatePayload({ ...data, target_countries: 'SG,HK', target_universities: 'NUS',
    target_programs: 'Data Science', decision_maker: 'Demo guardian', contact_permission: 'DO_NOT_CONTACT',
    next_action_at: '', next_action_reason: '' }, workspace.revision);
  await api.updateStudent(created.student_id, edit);
  workspace = await api.getWorkspace(created.student_id);
  assert.deepEqual(workspace.targets, { countries: ['SG', 'HK'], universities: ['NUS'], programs_or_majors: ['Data Science'] });
  assert.equal(workspace.sales.decision_maker, 'Demo guardian');
  assert.equal(workspace.sales.next_action_at, null);
  assert.equal(workspace.sales.next_action_reason, null);
  assert.equal(workspace.sales.next_action_status, null);
  assert.equal((await api.listReminders()).some(item => item.student_id === created.student_id), false);
});

test('synthetic marker is required for new and edited demo names', async () => {
  const api = new DemoAdapter();
  await assert.rejects(api.createStudent({ display_name: 'Real Person' }), /Only synthetic records are allowed/);
  const before = await api.getWorkspace('student-demo-1');
  await assert.rejects(api.updateStudent('student-demo-1', {
    expected_revision: before.revision, display_name: 'Real Person',
  }), /Only synthetic records may be edited/);
});

test('editing unrelated fields preserves an existing confirmed action status', () => {
  const prior = { next_action_at: new Date('2026-10-05T15:30').toISOString(), next_action_status: 'CONFIRMED' };
  const unchanged = studentUpdatePayload({ next_action_at: '2026-10-05T15:30' }, 2, prior);
  assert.equal(unchanged.sales.next_action_status, 'CONFIRMED');
  const changed = studentUpdatePayload({ next_action_at: '2026-10-06T15:30' }, 2, prior);
  assert.equal(changed.sales.next_action_status, 'SUGGESTED');
});

test('student inbound and internal discussion remain different event types', async () => {
  const api = new DemoAdapter();
  await api.recordInbound('student-demo-1', { raw_text: 'Student said: I do not want installments' });
  await api.discussInternal('student-demo-1', { raw_text: 'Sales internal note: try a different question' });
  const workspace = await api.getWorkspace('student-demo-1');
  assert.equal(workspace.events.at(-2).event_type, 'INBOUND_RECEIVED');
  assert.equal(workspace.events.at(-1).event_type, 'INTERNAL_NOTE');
  assert.equal(customerVisibleHistory(workspace).some(event => event.raw_content.includes('Internal note')), false);
  assert.equal(customerVisibleHistory(workspace).at(-1).raw_content, 'Student said: I do not want installments');
});

test('unapproved draft is not customer history and cannot be marked sent', async () => {
  const api = new DemoAdapter();
  await api.requestDecision('student-demo-1');
  let workspace = await api.getWorkspace('student-demo-1');
  assert.equal(workspace.drafts.at(-1).status, 'PENDING_REVIEW');
  assert.equal(customerVisibleHistory(workspace).length, 1);
  await assert.rejects(api.recordActualSent('student-demo-1', { actual_sent_text: 'Actually sent' }), /There is no valid approved draft/);
});

test('custom offer is internal and requires owner-confirmed price before customer draft', async () => {
  const api = new DemoAdapter();
  await assert.rejects(api.requestDecision('student-demo-2'), /The custom offer awaits internal approval/);
  await assert.rejects(api.approveCustomOffer('student-demo-2', { price: '' }), /price/);
  let workspace = await api.getWorkspace('student-demo-2');
  assert.equal(workspace.offer.state, 'CUSTOM_OFFER_PROPOSAL');
  assert.equal(workspace.drafts.length, 0);
  await api.approveCustomOffer('student-demo-2', { price: 9200 });
  workspace = await api.getWorkspace('student-demo-2');
  assert.equal(workspace.offer.state, 'APPROVED_CUSTOM_OFFER');
  assert.equal(workspace.offer.price, 9200);
  await api.requestDecision('student-demo-2');
  workspace = await api.getWorkspace('student-demo-2');
  assert.equal(workspace.decision.offer.state, 'APPROVED_CUSTOM_OFFER');
  assert.match(workspace.drafts.at(-1).text, /CNY 9200/);
});

test('review revisions preserve feedback and actual sent text differs from approved draft', async () => {
  const api = new DemoAdapter();
  await api.requestDecision('student-demo-1');
  await api.reviewDraft('student-demo-1', { feedback_type: 'NATURALNESS', comment: 'Make it sound more natural for chat' });
  let workspace = await api.getWorkspace('student-demo-1');
  assert.equal(workspace.drafts.length, 2);
  assert.equal(workspace.drafts[0].status, 'CHANGES_REQUESTED');
  assert.equal(workspace.drafts[1].revision, 2);
  assert.equal(workspace.events.some(event => event.raw_content === 'Make it sound more natural for chat'), true);
  await api.approveDraft('student-demo-1');
  workspace = await api.getWorkspace('student-demo-1');
  const approved = workspace.drafts.at(-1).text;
  assert.equal(customerVisibleHistory(workspace).length, 1);
  await api.recordActualSent('student-demo-1', { actual_sent_text: 'I changed the sentence before sending it' });
  workspace = await api.getWorkspace('student-demo-1');
  assert.equal(workspace.sent[0].approved_text, approved);
  assert.equal(workspace.sent[0].actual_sent_text, 'I changed the sentence before sending it');
  assert.equal(customerVisibleHistory(workspace).at(-1).raw_content, 'I changed the sentence before sending it');
  assert.equal(workspace.sent[0].diff_from_approved !== null, true);
});

test('new student reply invalidates an approved unsent draft and old send reminder', async () => {
  const api = new DemoAdapter();
  await api.requestDecision('student-demo-1');
  await api.approveDraft('student-demo-1');
  assert.equal((await api.listReminders()).some(item => item.student_id === 'student-demo-1' && item.type === 'SEND_DUE'), true);
  await api.recordInbound('student-demo-1', { raw_text: 'Wait, I have another question' });
  const workspace = await api.getWorkspace('student-demo-1');
  assert.equal(workspace.drafts.at(-1).status, 'STALE');
  assert.equal(workspace.approved_draft_id, null);
  assert.equal((await api.listReminders()).some(item => item.student_id === 'student-demo-1' && item.type === 'SEND_DUE'), false);
});

test('do-not-contact blocks sales actions and reminders', async () => {
  const api = new DemoAdapter();
  const before = await api.getWorkspace('student-demo-1');
  await api.updateStudent('student-demo-1', { expected_revision: before.revision, sales: { contact_permission: 'DO_NOT_CONTACT', stage: 'DO_NOT_CONTACT' } });
  await assert.rejects(api.requestDecision('student-demo-1'), /Do not contact/);
  assert.equal((await api.listReminders()).some(item => item.student_id === 'student-demo-1'), false);
});

test('unknown contact permission cannot approve a customer-facing draft', async () => {
  const api = new DemoAdapter();
  const before = await api.getWorkspace('student-demo-1');
  await api.updateStudent('student-demo-1', { expected_revision: before.revision, sales: { contact_permission: 'UNKNOWN' } });
  await api.requestDecision('student-demo-1');
  await assert.rejects(api.approveDraft('student-demo-1'), /Contact permission is not explicitly allowed/);
});

test('pending draft cannot be silently replaced by another generated draft', async () => {
  const api = new DemoAdapter();
  await api.requestDecision('student-demo-1');
  await assert.rejects(api.requestDecision('student-demo-1'), /A draft is pending/);
  assert.equal((await api.getWorkspace('student-demo-1')).drafts.length, 1);
});

test('revision conflict is visible and profile facts do not mutate silently', async () => {
  const api = new DemoAdapter();
  const before = await api.getWorkspace('student-demo-1');
  await api.updateStudent('student-demo-1', { expected_revision: before.revision, display_name: 'DEMO Updated Student' });
  await assert.rejects(api.updateStudent('student-demo-1', { expected_revision: before.revision, display_name: 'DEMO Overwrite Attempt' }), /The record has changed/);
  assert.equal((await api.getWorkspace('student-demo-1')).display_name, 'DEMO Updated Student');
});

test('returned workspace is an isolated copy and synthetic memory keeps provenance', async () => {
  const api = new DemoAdapter();
  const workspace = await api.getWorkspace('student-demo-1');
  const hypothesis = workspace.memory.find(item => item.epistemic_status === 'AGENT_HYPOTHESIS');
  const confirmed = workspace.memory.find(item => item.epistemic_status === 'CUSTOMER_STATED');
  assert.ok(hypothesis.source_event_ids.length);
  assert.ok(confirmed.evidence_span);
  workspace.events[0].raw_content = 'Tampered in the frontend';
  assert.notEqual((await api.getWorkspace('student-demo-1')).events[0].raw_content, 'Tampered in the frontend');
});
