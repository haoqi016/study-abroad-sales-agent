import test from 'node:test';
import assert from 'node:assert/strict';
import { DemoAdapter, customerVisibleHistory, localActionTime, studentCreatePayload, studentUpdatePayload } from '../api.js';

test('profile form maps target and next-action fields into a synthetic record', async () => {
  const data = { display_name: '测试同学（合成）', channel: 'OTHER', platform_handle: 'demo-only',
    university: '示例大学', major: '数学', score: '85', year: 'YEAR_4', country: 'SG',
    target_universities: 'NTU，NUS', target_programs: '统计、商科', stage: 'CONSULTING',
    current_objection: '尚在比较', decision_maker: '本人', contact_permission: 'UNKNOWN',
    next_action_at: '2026-10-05T15:30', next_action_reason: '确认合成申请方向' };
  const payload = studentCreatePayload(data);
  assert.deepEqual(payload.targets, { countries: ['SG'], universities: ['NTU', 'NUS'], programs_or_majors: ['统计', '商科'] });
  assert.equal(payload.sales.next_action_at, new Date(data.next_action_at).toISOString());
  assert.equal(payload.sales.next_action_status, 'SUGGESTED');
  assert.equal(localActionTime(payload.sales.next_action_at), data.next_action_at);
  const api = new DemoAdapter();
  const created = await api.createStudent(payload);
  let workspace = await api.getWorkspace(created.student_id);
  assert.deepEqual(workspace.targets, payload.targets);
  assert.equal(workspace.sales.decision_maker, '本人');
  assert.equal(workspace.sales.next_action_reason, '确认合成申请方向');
  assert.equal(customerVisibleHistory(workspace).length, 0);
  assert.equal((await api.listReminders()).some(item => item.student_id === created.student_id), false);

  const edit = studentUpdatePayload({ ...data, target_countries: 'SG，HK', target_universities: 'NUS',
    target_programs: '数据科学', decision_maker: '合成监护人', contact_permission: 'DO_NOT_CONTACT',
    next_action_at: '', next_action_reason: '' }, workspace.revision);
  await api.updateStudent(created.student_id, edit);
  workspace = await api.getWorkspace(created.student_id);
  assert.deepEqual(workspace.targets, { countries: ['SG', 'HK'], universities: ['NUS'], programs_or_majors: ['数据科学'] });
  assert.equal(workspace.sales.decision_maker, '合成监护人');
  assert.equal(workspace.sales.next_action_at, null);
  assert.equal(workspace.sales.next_action_reason, null);
  assert.equal(workspace.sales.next_action_status, null);
  assert.equal((await api.listReminders()).some(item => item.student_id === created.student_id), false);
});

test('synthetic marker is required for new and edited demo names', async () => {
  const api = new DemoAdapter();
  await assert.rejects(api.createStudent({ display_name: '真实姓名' }), /仅可新增合成资料/);
  const before = await api.getWorkspace('student-demo-1');
  await assert.rejects(api.updateStudent('student-demo-1', {
    expected_revision: before.revision, display_name: '真实姓名',
  }), /仅可编辑合成资料/);
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
  await api.recordInbound('student-demo-1', { raw_text: '学生实际说：不想分期' });
  await api.discussInternal('student-demo-1', { raw_text: '销售内部意见：换个问法' });
  const workspace = await api.getWorkspace('student-demo-1');
  assert.equal(workspace.events.at(-2).event_type, 'INBOUND_RECEIVED');
  assert.equal(workspace.events.at(-1).event_type, 'INTERNAL_NOTE');
  assert.equal(customerVisibleHistory(workspace).some(event => event.raw_content.includes('内部意见')), false);
  assert.equal(customerVisibleHistory(workspace).at(-1).raw_content, '学生实际说：不想分期');
});

test('unapproved draft is not customer history and cannot be marked sent', async () => {
  const api = new DemoAdapter();
  await api.requestDecision('student-demo-1');
  let workspace = await api.getWorkspace('student-demo-1');
  assert.equal(workspace.drafts.at(-1).status, 'PENDING_REVIEW');
  assert.equal(customerVisibleHistory(workspace).length, 1);
  await assert.rejects(api.recordActualSent('student-demo-1', { actual_sent_text: '实际上已发' }), /没有仍有效的已批准草稿/);
});

test('custom offer is internal and requires owner-confirmed price before customer draft', async () => {
  const api = new DemoAdapter();
  await assert.rejects(api.requestDecision('student-demo-2'), /定制 Offer 尚未批准/);
  await assert.rejects(api.approveCustomOffer('student-demo-2', { price: '' }), /价格/);
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
  assert.match(workspace.drafts.at(-1).text, /9200 元/);
});

test('review revisions preserve feedback and actual sent text differs from approved draft', async () => {
  const api = new DemoAdapter();
  await api.requestDecision('student-demo-1');
  await api.reviewDraft('student-demo-1', { feedback_type: 'NATURALNESS', comment: '更像微信聊天' });
  let workspace = await api.getWorkspace('student-demo-1');
  assert.equal(workspace.drafts.length, 2);
  assert.equal(workspace.drafts[0].status, 'CHANGES_REQUESTED');
  assert.equal(workspace.drafts[1].revision, 2);
  assert.equal(workspace.events.some(event => event.raw_content === '更像微信聊天'), true);
  await api.approveDraft('student-demo-1');
  workspace = await api.getWorkspace('student-demo-1');
  const approved = workspace.drafts.at(-1).text;
  assert.equal(customerVisibleHistory(workspace).length, 1);
  await api.recordActualSent('student-demo-1', { actual_sent_text: '我在微信里改成了这一句' });
  workspace = await api.getWorkspace('student-demo-1');
  assert.equal(workspace.sent[0].approved_text, approved);
  assert.equal(workspace.sent[0].actual_sent_text, '我在微信里改成了这一句');
  assert.equal(customerVisibleHistory(workspace).at(-1).raw_content, '我在微信里改成了这一句');
  assert.equal(workspace.sent[0].diff_from_approved !== null, true);
});

test('new student reply invalidates an approved unsent draft and old send reminder', async () => {
  const api = new DemoAdapter();
  await api.requestDecision('student-demo-1');
  await api.approveDraft('student-demo-1');
  assert.equal((await api.listReminders()).some(item => item.student_id === 'student-demo-1' && item.type === 'SEND_DUE'), true);
  await api.recordInbound('student-demo-1', { raw_text: '等一下，我又想到一个问题' });
  const workspace = await api.getWorkspace('student-demo-1');
  assert.equal(workspace.drafts.at(-1).status, 'STALE');
  assert.equal(workspace.approved_draft_id, null);
  assert.equal((await api.listReminders()).some(item => item.student_id === 'student-demo-1' && item.type === 'SEND_DUE'), false);
});

test('do-not-contact blocks sales actions and reminders', async () => {
  const api = new DemoAdapter();
  const before = await api.getWorkspace('student-demo-1');
  await api.updateStudent('student-demo-1', { expected_revision: before.revision, sales: { contact_permission: 'DO_NOT_CONTACT', stage: 'DO_NOT_CONTACT' } });
  await assert.rejects(api.requestDecision('student-demo-1'), /禁止联系/);
  assert.equal((await api.listReminders()).some(item => item.student_id === 'student-demo-1'), false);
});

test('unknown contact permission cannot approve a customer-facing draft', async () => {
  const api = new DemoAdapter();
  const before = await api.getWorkspace('student-demo-1');
  await api.updateStudent('student-demo-1', { expected_revision: before.revision, sales: { contact_permission: 'UNKNOWN' } });
  await api.requestDecision('student-demo-1');
  await assert.rejects(api.approveDraft('student-demo-1'), /联系许可未明确允许/);
});

test('pending draft cannot be silently replaced by another generated draft', async () => {
  const api = new DemoAdapter();
  await api.requestDecision('student-demo-1');
  await assert.rejects(api.requestDecision('student-demo-1'), /已有待处理草稿/);
  assert.equal((await api.getWorkspace('student-demo-1')).drafts.length, 1);
});

test('revision conflict is visible and profile facts do not mutate silently', async () => {
  const api = new DemoAdapter();
  const before = await api.getWorkspace('student-demo-1');
  await api.updateStudent('student-demo-1', { expected_revision: before.revision, display_name: '新备注（合成）' });
  await assert.rejects(api.updateStudent('student-demo-1', { expected_revision: before.revision, display_name: '覆盖（合成）' }), /档案已变化/);
  assert.equal((await api.getWorkspace('student-demo-1')).display_name, '新备注（合成）');
});

test('returned workspace is an isolated copy and synthetic memory keeps provenance', async () => {
  const api = new DemoAdapter();
  const workspace = await api.getWorkspace('student-demo-1');
  const hypothesis = workspace.memory.find(item => item.epistemic_status === 'AGENT_HYPOTHESIS');
  const confirmed = workspace.memory.find(item => item.epistemic_status === 'CUSTOMER_STATED');
  assert.ok(hypothesis.source_event_ids.length);
  assert.ok(confirmed.evidence_span);
  workspace.events[0].raw_content = '被前端篡改';
  assert.notEqual((await api.getWorkspace('student-demo-1')).events[0].raw_content, '被前端篡改');
});
