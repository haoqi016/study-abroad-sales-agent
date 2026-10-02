// No network requests or persistent storage. This adapter exists only to exercise the UI.
export class SalesWorkspaceApi {
  async listStudents() { throw new Error('Workspace backend is not connected'); }
  async createStudent(_record) { throw new Error('Workspace backend is not connected'); }
  async updateStudent(_id, _patch) { throw new Error('Workspace backend is not connected'); }
  async getWorkspace(_id) { throw new Error('Workspace backend is not connected'); }
  async recordInbound(_id, _message) { throw new Error('Workspace backend is not connected'); }
  async discussInternal(_id, _note) { throw new Error('Workspace backend is not connected'); }
  async requestDecision(_id) { throw new Error('Workspace backend is not connected'); }
  async reviewDraft(_id, _review) { throw new Error('Workspace backend is not connected'); }
  async approveDraft(_id) { throw new Error('Workspace backend is not connected'); }
  async approveCustomOffer(_id, _approval) { throw new Error('Workspace backend is not connected'); }
  async reviewCustomOffer(_id, _review) { throw new Error('Workspace backend is not connected'); }
  async recordActualSent(_id, _sent) { throw new Error('Workspace backend is not connected'); }
  async labelProgressAssessment(_id, _label) { throw new Error('Progress labels are supported only by the local offline bridge.'); }
  async listReminders() { throw new Error('Workspace backend is not connected'); }
}

const copy = value => structuredClone(value);
const now = () => new Date().toISOString();
const clean = value => String(value ?? '').trim();
const cleanList = value => clean(value).split(/[,，、\n]+/).map(item => item.trim()).filter(Boolean);
export const formActionTime = value => value ? new Date(value).toISOString() : null;
export const localActionTime = value => {
  if (!value) return '';
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
export function studentCreatePayload(data, usingBridge = false) {
  return {
    display_name: data.display_name,
    source: usingBridge ? { channel: data.channel } : { channel: data.channel, platform_handle: data.platform_handle },
    education: { undergraduate_university_raw: data.university, major_raw: data.major, score_raw: data.score, current_year: data.year },
    targets: { countries: data.country ? [data.country] : [], universities: cleanList(data.target_universities), programs_or_majors: cleanList(data.target_programs) },
    sales: { stage: data.stage, current_objection: data.current_objection, decision_maker: data.decision_maker,
      contact_permission: data.contact_permission, next_action_at: formActionTime(data.next_action_at),
      next_action_reason: clean(data.next_action_reason) || null,
      next_action_status: data.next_action_at ? 'SUGGESTED' : null },
  };
}
export function studentUpdatePayload(data, revision, previousSales = {}) {
  const nextActionAt = formActionTime(data.next_action_at);
  return {
    expected_revision: revision, display_name: data.display_name,
    targets: { countries: cleanList(data.target_countries), universities: cleanList(data.target_universities), programs_or_majors: cleanList(data.target_programs) },
    sales: { stage: data.stage, current_objection: data.current_objection, decision_maker: data.decision_maker,
      contact_permission: data.contact_permission, next_action_at: nextActionAt,
      next_action_reason: clean(data.next_action_reason) || null,
      next_action_status: nextActionAt ? nextActionAt === previousSales.next_action_at && previousSales.next_action_status
        ? previousSales.next_action_status : 'SUGGESTED' : null },
  };
}
let sequence = 10;
const id = prefix => `${prefix}-demo-${++sequence}`;

const demoStudents = [
  {
    student_id: 'student-demo-1', display_name: 'Demo Student Lin', source: { channel: 'XIAOHONGSHU', platform_handle: 'demo-lin' },
    education: { undergraduate_university_raw: 'Demo University', undergraduate_tier: '211', major_raw: 'Finance', score_raw: '83', current_year: 'YEAR_4' },
    targets: { countries: ['SG'], universities: ['NTU'], programs_or_majors: ['Business'] },
    sales: { stage: 'PRICE_OBJECTION', current_objection: 'Full service package exceeds the budget', decision_maker: 'Student', contact_permission: 'ALLOWED', next_action_at: null, next_action_reason: null, next_action_status: null },
    revision: 1,
    memory: [
      { memory_id: 'mem-demo-1', key: 'budget.total_ceiling', value: 'CNY 15,000', epistemic_status: 'CUSTOMER_STATED', evidence_span: 'My total budget is CNY 15,000', source_event_ids: ['inbound-demo-1'], active: true },
      { memory_id: 'mem-demo-2', key: 'intent.bargaining', value: 'May be negotiating price', epistemic_status: 'AGENT_HYPOTHESIS', evidence_span: 'Could the price be lower?', source_event_ids: ['inbound-demo-1'], active: true }
    ],
    events: [{ event_id: 'inbound-demo-1', event_type: 'INBOUND_RECEIVED', actor: 'STUDENT', raw_content: 'My total budget is CNY 15,000. Could the price be lower?', occurred_at: '2026-09-19T11:30:00+08:00' }],
    decision: null, offer: null, drafts: [], approved_draft_id: null, sent: []
  },
  {
    student_id: 'student-demo-2', display_name: 'Demo Student Zhou', source: { channel: 'WECHAT', platform_handle: 'demo-zhou' },
    education: { undergraduate_university_raw: 'Demo College', undergraduate_tier: 'UNKNOWN', major_raw: 'Communications', score_raw: '86', current_year: 'GRADUATED' },
    targets: { countries: ['HK'], universities: [], programs_or_majors: ['Media Studies'] },
    sales: { stage: 'CONSULTING', current_objection: 'Wants only essay support and deadline reminders', decision_maker: 'Student', contact_permission: 'ALLOWED', next_action_at: null, next_action_reason: null, next_action_status: null },
    revision: 1, memory: [],
    events: [{ event_id: 'inbound-demo-2', event_type: 'INBOUND_RECEIVED', actor: 'STUDENT', raw_content: 'I only need one personal statement and reminders after submission. Can I buy those separately?', occurred_at: '2026-09-19T12:00:00+08:00' }],
    decision: { current_objective: { goal: 'Confirm the service scope and submit pricing for internal approval', why_now: 'The student requested a package outside the current catalog', success_signals: ['The approver accepts or rejects the package'], status: 'ACTIVE' }, action_plan: { selected_action: 'Submit a custom offer for internal review' }, content_contract: { semantic_draft: 'I will put this package together and ask our team to review it.' }, offer: { state: 'CUSTOM_OFFER_PROPOSAL' } },
    offer: { offer_id: 'offer-demo-2', state: 'CUSTOM_OFFER_PROPOSAL', summary: 'One personal statement plus post submission reminders', price: null, approved_by: null },
    drafts: [], approved_draft_id: null, sent: []
  }
];

export class DemoAdapter extends SalesWorkspaceApi {
  constructor(seed = demoStudents) {
    super();
    this.students = new Map(copy(seed).map(student => [student.student_id, student]));
  }

  #get(studentId) {
    const student = this.students.get(studentId);
    if (!student) throw new Error('Student not found; return to the list and refresh.');
    return student;
  }

  async listStudents() {
    return copy([...this.students.values()].map(({ memory, events, decision, offer, drafts, sent, ...profile }) => ({
      ...profile, has_pending_offer: offer?.state === 'CUSTOM_OFFER_PROPOSAL',
      draft_status: drafts.at(-1)?.status ?? 'NONE', latest_event_type: events.at(-1)?.event_type ?? null
    })));
  }

  async createStudent(record) {
    if (!clean(record.display_name)) throw new Error('Enter a student display name.');
    if (!clean(record.display_name).toUpperCase().includes('DEMO')) {
      throw new Error('Only synthetic records are allowed; the display name must contain DEMO.');
    }
    const student = {
      student_id: id('student'), revision: 1, display_name: clean(record.display_name),
      source: { channel: record.source?.channel || 'OTHER', platform_handle: clean(record.source?.platform_handle) },
      education: {
        undergraduate_university_raw: clean(record.education?.undergraduate_university_raw),
        undergraduate_tier: 'UNKNOWN', major_raw: clean(record.education?.major_raw),
        score_raw: clean(record.education?.score_raw), current_year: record.education?.current_year || 'UNKNOWN'
      },
      targets: { countries: record.targets?.countries || [], universities: record.targets?.universities || [], programs_or_majors: record.targets?.programs_or_majors || [] },
      sales: {
        stage: record.sales?.stage || 'NEW', current_objection: clean(record.sales?.current_objection),
        decision_maker: clean(record.sales?.decision_maker), contact_permission: record.sales?.contact_permission || 'UNKNOWN',
        next_action_at: record.sales?.next_action_at || null, next_action_reason: clean(record.sales?.next_action_reason) || null,
        next_action_status: record.sales?.next_action_status || null
      },
      memory: [], events: [], decision: null, offer: null, drafts: [], approved_draft_id: null, sent: []
    };
    this.students.set(student.student_id, student);
    return copy(student);
  }

  async updateStudent(studentId, patch) {
    const student = this.#get(studentId);
    if (patch.expected_revision !== student.revision) throw new Error('The record has changed. Refresh before editing to avoid overwriting another update.');
    if ('display_name' in patch && !clean(patch.display_name).toUpperCase().includes('DEMO')) {
      throw new Error('Only synthetic records may be edited; the display name must contain DEMO.');
    }
    for (const field of ['display_name']) if (field in patch) student[field] = clean(patch[field]);
    if (patch.targets) Object.assign(student.targets, copy(patch.targets));
    if (patch.sales) Object.assign(student.sales, patch.sales);
    student.revision++;
    return copy(student);
  }

  async getWorkspace(studentId) { return copy(this.#get(studentId)); }

  async recordInbound(studentId, message) {
    const student = this.#get(studentId);
    if (!clean(message.raw_text)) throw new Error('Paste the exact message received from the student.');
    student.events.push({ event_id: id('inbound'), event_type: 'INBOUND_RECEIVED', actor: 'STUDENT', raw_content: clean(message.raw_text), occurred_at: message.occurred_at || now(), recorded_by: 'demo-operator' });
    student.sales.stage = student.sales.contact_permission === 'DO_NOT_CONTACT' ? 'DO_NOT_CONTACT' : 'CONSULTING';
    // A new observation invalidates a previously approved but unsent suggestion.
    student.approved_draft_id = null;
    for (const draft of student.drafts) if (draft.status === 'APPROVED') draft.status = 'STALE';
    student.decision = null;
    student.revision++;
    return copy(student);
  }

  async discussInternal(studentId, note) {
    const student = this.#get(studentId);
    if (!clean(note.raw_text)) throw new Error('Enter an internal discussion note.');
    student.events.push({ event_id: id('internal'), event_type: 'INTERNAL_NOTE', actor: 'SALESPERSON', raw_content: clean(note.raw_text), occurred_at: now() });
    student.revision++;
    return copy(student);
  }

  async requestDecision(studentId) {
    const student = this.#get(studentId);
    if (student.sales.contact_permission === 'DO_NOT_CONTACT') throw new Error('Do not contact is active; a sales draft cannot be generated.');
    if (student.offer?.state === 'CUSTOM_OFFER_PROPOSAL') throw new Error('The custom offer awaits internal approval; a customer draft cannot be generated.');
    if (['PENDING_REVIEW', 'APPROVED'].includes(student.drafts.at(-1)?.status)) throw new Error('A draft is pending; review it or record the actual external send first.');
    const latest = [...student.events].reverse().find(event => event.event_type === 'INBOUND_RECEIVED');
    if (!latest) throw new Error('Record a student message first.');
    const approvedCustomOffer = student.offer?.state === 'APPROVED_CUSTOM_OFFER';
    const objective = approvedCustomOffer ? 'Explain the approved custom package and confirm interest'
      : /budget|15,?000/i.test(latest.raw_content)
        ? 'Confirm the budget ceiling and the most needed services' : 'Clarify the main application obstacle';
    const content = approvedCustomOffer
      ? `The package you asked about, ${student.offer.summary} has been approved at CNY ${student.offer.price}. Is that the package you want?`
      : /15,?000/i.test(latest.raw_content)
        ? 'I understand your budget limit. Which part would you most like help with: school selection, essays, or submission follow up?'
        : 'Which part of your application would you most like help with now?';
    student.decision = {
      demo_only: true, current_objective: { goal: objective, why_now: 'Fixed rule for the interface demo; not a model decision', success_signals: ['The student clarifies a specific need'], status: 'ACTIVE' },
      action_plan: { selected_action: 'Ask one specific question about the need', fallback_if_blocked: 'Ask a human to assess whether to offer a single service' },
      content_contract: { semantic_draft: content }, offer: { state: approvedCustomOffer ? 'APPROVED_CUSTOM_OFFER' : 'NONE', offer_id: student.offer?.offer_id ?? null }
    };
    const revision = student.drafts.length + 1;
    student.drafts.push({ draft_id: id('draft'), revision, messages: [{ type: 'text', content }], text: content, status: 'PENDING_REVIEW', decision_demo_only: true, feedback: [] });
    student.revision++;
    return copy(student);
  }

  async approveCustomOffer(studentId, approval) {
    const student = this.#get(studentId);
    if (!student.offer || student.offer.state !== 'CUSTOM_OFFER_PROPOSAL') throw new Error('There is no custom offer awaiting review.');
    if (student.sales.contact_permission === 'DO_NOT_CONTACT') throw new Error('A customer offer cannot be approved while do not contact is active.');
    const price = Number(approval?.price);
    if (!Number.isFinite(price) || price <= 0 || !Number.isInteger(price)) throw new Error('The approver must confirm a positive whole CNY price for the custom package.');
    student.offer.state = 'APPROVED_CUSTOM_OFFER';
    student.offer.price = price;
    student.offer.approved_by = 'demo-operator';
    student.offer.approved_at = now();
    student.events.push({ event_id: id('review'), event_type: 'OFFER_APPROVED', actor: 'SALESPERSON', raw_content: `Demo: human approved the package and price of CNY ${price}`, occurred_at: now() });
    student.revision++;
    return copy(student);
  }

  async reviewDraft(studentId, review) {
    const student = this.#get(studentId);
    const draft = student.drafts.at(-1);
    if (!draft || draft.status !== 'PENDING_REVIEW') throw new Error('There is no pending draft to return for changes.');
    if (!clean(review.comment)) throw new Error('Explain the requested changes.');
    const feedbackType = review.feedback_type || 'NATURALNESS';
    const event = { event_id: id('review'), event_type: 'REVIEW_CHANGES_REQUESTED', actor: 'SALESPERSON', raw_content: clean(review.comment), feedback_type: feedbackType, occurred_at: now(), draft_id: draft.draft_id };
    student.events.push(event);
    draft.status = 'CHANGES_REQUESTED';
    draft.feedback.push(event.event_id);
    // This is a visible demo revision, not a claim that the decision or Conversation Agent ran.
    const revisedText = feedbackType === 'NATURALNESS' || feedbackType === 'TONE'
      ? `${draft.text} No rush—tell me which part matters most to you.`
      : draft.text;
    student.drafts.push({ draft_id: id('draft'), revision: draft.revision + 1, messages: [{ type: 'text', content: revisedText }], text: revisedText, status: 'PENDING_REVIEW', decision_demo_only: true, feedback: [event.event_id] });
    student.revision++;
    return copy(student);
  }

  async approveDraft(studentId) {
    const student = this.#get(studentId);
    if (student.sales.contact_permission !== 'ALLOWED') throw new Error('Contact permission is not explicitly allowed; the customer draft cannot be approved.');
    if (student.offer?.state === 'CUSTOM_OFFER_PROPOSAL') throw new Error('The custom offer has not been approved.');
    const draft = student.drafts.at(-1);
    if (!draft || draft.status !== 'PENDING_REVIEW') throw new Error('There is no pending draft to approve.');
    draft.status = 'APPROVED';
    student.approved_draft_id = draft.draft_id;
    student.events.push({ event_id: id('review'), event_type: 'APPROVED', actor: 'SALESPERSON', raw_content: null, draft_id: draft.draft_id, occurred_at: now() });
    student.revision++;
    return copy(student);
  }

  async recordActualSent(studentId, sent) {
    const student = this.#get(studentId);
    const draft = student.drafts.find(item => item.draft_id === student.approved_draft_id);
    if (!draft || draft.status !== 'APPROVED') throw new Error('There is no valid approved draft; the message cannot be marked as sent.');
    if (student.sales.contact_permission !== 'ALLOWED') throw new Error('Contact permission is not explicitly allowed; a sales send cannot be recorded.');
    const actualText = clean(sent.actual_sent_text);
    if (!actualText) throw new Error('Enter the exact text sent through the external channel.');
    const event = {
      event_id: id('sent'), event_type: 'HUMAN_SENT', actor: 'SALESPERSON', raw_content: actualText,
      actual_sent_text: actualText, approved_text: draft.text, approved_draft_id: draft.draft_id,
      diff_from_approved: actualText === draft.text ? null : 'The actual human sent text differs from the approved draft',
      occurred_at: sent.sent_at || now()
    };
    student.events.push(event);
    student.sent.push(event);
    draft.status = 'SENT';
    student.approved_draft_id = null;
    student.sales.stage = 'WAITING_STUDENT';
    student.revision++;
    return copy(student);
  }

  async listReminders() {
    const items = [];
    for (const student of this.students.values()) {
      if (student.sales.contact_permission === 'DO_NOT_CONTACT') continue;
      const latest = student.events.at(-1)?.event_type;
      const draft = student.drafts.find(item => item.draft_id === student.approved_draft_id);
      if (draft?.status === 'APPROVED' && latest !== 'INBOUND_RECEIVED') items.push({ student_id: student.student_id, display_name: student.display_name, type: 'SEND_DUE', label: 'Approved draft awaits human external send' });
      else if (student.drafts.at(-1)?.status === 'PENDING_REVIEW') items.push({ student_id: student.student_id, display_name: student.display_name, type: 'REVIEW_DUE', label: 'Draft awaits review and cannot be sent' });
      else if (student.offer?.state === 'CUSTOM_OFFER_PROPOSAL') items.push({ student_id: student.student_id, display_name: student.display_name, type: 'OFFER_REVIEW', label: 'Custom package awaits approver review' });
      else if (latest === 'INBOUND_RECEIVED') items.push({ student_id: student.student_id, display_name: student.display_name, type: 'NEW_INBOUND', label: 'New student message requires a new assessment' });
    }
    return copy(items);
  }
}

export function customerVisibleHistory(workspace) {
  return workspace.events.filter(event => event.event_type === 'INBOUND_RECEIVED' || event.event_type === 'HUMAN_SENT');
}
