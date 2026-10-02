// No network requests or persistent storage. This adapter exists only to exercise the UI.
export class SalesWorkspaceApi {
  async listStudents() { throw new Error('未接入工作台后端'); }
  async createStudent(_record) { throw new Error('未接入工作台后端'); }
  async updateStudent(_id, _patch) { throw new Error('未接入工作台后端'); }
  async getWorkspace(_id) { throw new Error('未接入工作台后端'); }
  async recordInbound(_id, _message) { throw new Error('未接入工作台后端'); }
  async discussInternal(_id, _note) { throw new Error('未接入工作台后端'); }
  async requestDecision(_id) { throw new Error('未接入工作台后端'); }
  async reviewDraft(_id, _review) { throw new Error('未接入工作台后端'); }
  async approveDraft(_id) { throw new Error('未接入工作台后端'); }
  async approveCustomOffer(_id, _approval) { throw new Error('未接入工作台后端'); }
  async reviewCustomOffer(_id, _review) { throw new Error('未接入工作台后端'); }
  async recordActualSent(_id, _sent) { throw new Error('未接入工作台后端'); }
  async labelProgressAssessment(_id, _label) { throw new Error('仅本地离线桥接支持推进标签。'); }
  async listReminders() { throw new Error('未接入工作台后端'); }
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
    student_id: 'student-demo-1', display_name: '林同学（合成）', source: { channel: 'XIAOHONGSHU', platform_handle: 'demo-lin' },
    education: { undergraduate_university_raw: '示例大学', undergraduate_tier: '211', major_raw: '金融', score_raw: '83', current_year: 'YEAR_4' },
    targets: { countries: ['SG'], universities: ['NTU'], programs_or_majors: ['商科'] },
    sales: { stage: 'PRICE_OBJECTION', current_objection: '全套服务超出预算', decision_maker: '本人', contact_permission: 'ALLOWED', next_action_at: null, next_action_reason: null, next_action_status: null },
    revision: 1,
    memory: [
      { memory_id: 'mem-demo-1', key: 'budget.total_ceiling', value: '15000 元', epistemic_status: 'CUSTOMER_STATED', evidence_span: '总共最多一万五', source_event_ids: ['inbound-demo-1'], active: true },
      { memory_id: 'mem-demo-2', key: 'intent.bargaining', value: '可能在议价', epistemic_status: 'AGENT_HYPOTHESIS', evidence_span: '能不能再便宜一点', source_event_ids: ['inbound-demo-1'], active: true }
    ],
    events: [{ event_id: 'inbound-demo-1', event_type: 'INBOUND_RECEIVED', actor: 'STUDENT', raw_content: '总共最多一万五，能不能再便宜一点？', occurred_at: '2026-09-19T11:30:00+08:00' }],
    decision: null, offer: null, drafts: [], approved_draft_id: null, sent: []
  },
  {
    student_id: 'student-demo-2', display_name: '周同学（合成）', source: { channel: 'WECHAT', platform_handle: 'demo-zhou' },
    education: { undergraduate_university_raw: '示例学院', undergraduate_tier: 'UNKNOWN', major_raw: '传播', score_raw: '86', current_year: 'GRADUATED' },
    targets: { countries: ['HK'], universities: [], programs_or_majors: ['传媒'] },
    sales: { stage: 'CONSULTING', current_objection: '只想购买文书和提醒的组合', decision_maker: '本人', contact_permission: 'ALLOWED', next_action_at: null, next_action_reason: null, next_action_status: null },
    revision: 1, memory: [],
    events: [{ event_id: 'inbound-demo-2', event_type: 'INBOUND_RECEIVED', actor: 'STUDENT', raw_content: '我只需要一篇 PS 和递交之后的提醒，可以单独买吗？', occurred_at: '2026-09-19T12:00:00+08:00' }],
    decision: { current_objective: { goal: '确认所需服务范围并提交负责人定价', why_now: '客户提出非现售组合', success_signals: ['负责人批准或拒绝'], status: 'ACTIVE' }, action_plan: { selected_action: '内部提交定制 Offer' }, content_contract: { semantic_draft: '这个组合我先按你的需求整理给负责人核一下。' }, offer: { state: 'CUSTOM_OFFER_PROPOSAL' } },
    offer: { offer_id: 'offer-demo-2', state: 'CUSTOM_OFFER_PROPOSAL', summary: '一篇 PS ＋递交后节点提醒', price: null, approved_by: null },
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
    if (!student) throw new Error('找不到该学生；请返回列表刷新。');
    return student;
  }

  async listStudents() {
    return copy([...this.students.values()].map(({ memory, events, decision, offer, drafts, sent, ...profile }) => ({
      ...profile, has_pending_offer: offer?.state === 'CUSTOM_OFFER_PROPOSAL',
      draft_status: drafts.at(-1)?.status ?? 'NONE', latest_event_type: events.at(-1)?.event_type ?? null
    })));
  }

  async createStudent(record) {
    if (!clean(record.display_name)) throw new Error('请填写学生备注名。');
    if (!clean(record.display_name).includes('合成') && !clean(record.display_name).toUpperCase().includes('DEMO')) {
      throw new Error('仅可新增合成资料；备注名须包含“合成”或“DEMO”。');
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
    if (patch.expected_revision !== student.revision) throw new Error('档案已变化，请刷新后重试，避免覆盖他人的修改。');
    if ('display_name' in patch && !clean(patch.display_name).includes('合成') && !clean(patch.display_name).toUpperCase().includes('DEMO')) {
      throw new Error('仅可编辑合成资料；备注名须包含“合成”或“DEMO”。');
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
    if (!clean(message.raw_text)) throw new Error('请粘贴学生实际发来的原话。');
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
    if (!clean(note.raw_text)) throw new Error('请填写内部讨论内容。');
    student.events.push({ event_id: id('internal'), event_type: 'INTERNAL_NOTE', actor: 'SALESPERSON', raw_content: clean(note.raw_text), occurred_at: now() });
    student.revision++;
    return copy(student);
  }

  async requestDecision(studentId) {
    const student = this.#get(studentId);
    if (student.sales.contact_permission === 'DO_NOT_CONTACT') throw new Error('该学生已禁止联系；不能生成销售草稿。');
    if (student.offer?.state === 'CUSTOM_OFFER_PROPOSAL') throw new Error('定制 Offer 尚未批准，只能先进行内部方案审批。');
    if (['PENDING_REVIEW', 'APPROVED'].includes(student.drafts.at(-1)?.status)) throw new Error('已有待处理草稿；请先完成审核或记录实际发送。');
    const latest = [...student.events].reverse().find(event => event.event_type === 'INBOUND_RECEIVED');
    if (!latest) throw new Error('请先录入一条学生真实消息。');
    const approvedCustomOffer = student.offer?.state === 'APPROVED_CUSTOM_OFFER';
    const objective = approvedCustomOffer ? '向客户说明已批准的定制组合并确认意愿'
      : latest.raw_content.includes('预算') || latest.raw_content.includes('一万五')
        ? '确认预算上限与最需要的服务范围' : '澄清当前最重要的申请障碍';
    const content = approvedCustomOffer
      ? `你提的${student.offer.summary}，负责人已经确认可以做，价格是 ${student.offer.price} 元。这个组合是你想要的吗？`
      : latest.raw_content.includes('一万五')
        ? '理解你的预算上限。你更希望我们帮你接手选校、文书，还是递交跟进中的哪一块？'
        : '你现在最希望有人帮你处理申请里的哪一块？';
    student.decision = {
      demo_only: true, current_objective: { goal: objective, why_now: '仅供界面演示的固定规则，非模型判断', success_signals: ['客户澄清具体需求'], status: 'ACTIVE' },
      action_plan: { selected_action: '询问一个具体需求问题', fallback_if_blocked: '交由人工判断是否转向单项服务' },
      content_contract: { semantic_draft: content }, offer: { state: approvedCustomOffer ? 'APPROVED_CUSTOM_OFFER' : 'NONE', offer_id: student.offer?.offer_id ?? null }
    };
    const revision = student.drafts.length + 1;
    student.drafts.push({ draft_id: id('draft'), revision, messages: [{ type: 'text', content }], text: content, status: 'PENDING_REVIEW', decision_demo_only: true, feedback: [] });
    student.revision++;
    return copy(student);
  }

  async approveCustomOffer(studentId, approval) {
    const student = this.#get(studentId);
    if (!student.offer || student.offer.state !== 'CUSTOM_OFFER_PROPOSAL') throw new Error('没有待审核的定制 Offer。');
    if (student.sales.contact_permission === 'DO_NOT_CONTACT') throw new Error('禁止联系状态下不能批准对客方案。');
    const price = Number(approval?.price);
    if (!Number.isFinite(price) || price <= 0 || !Number.isInteger(price)) throw new Error('负责人必须先确认定制方案的人民币整数价格。');
    student.offer.state = 'APPROVED_CUSTOM_OFFER';
    student.offer.price = price;
    student.offer.approved_by = 'demo-operator';
    student.offer.approved_at = now();
    student.events.push({ event_id: id('review'), event_type: 'OFFER_APPROVED', actor: 'SALESPERSON', raw_content: `演示：人工批准服务组合及价格 ${price} 元`, occurred_at: now() });
    student.revision++;
    return copy(student);
  }

  async reviewDraft(studentId, review) {
    const student = this.#get(studentId);
    const draft = student.drafts.at(-1);
    if (!draft || draft.status !== 'PENDING_REVIEW') throw new Error('没有可打回的待审核草稿。');
    if (!clean(review.comment)) throw new Error('请写明打回意见。');
    const feedbackType = review.feedback_type || 'NATURALNESS';
    const event = { event_id: id('review'), event_type: 'REVIEW_CHANGES_REQUESTED', actor: 'SALESPERSON', raw_content: clean(review.comment), feedback_type: feedbackType, occurred_at: now(), draft_id: draft.draft_id };
    student.events.push(event);
    draft.status = 'CHANGES_REQUESTED';
    draft.feedback.push(event.event_id);
    // This is a visible demo revision, not a claim that the decision or Conversation Agent ran.
    const revisedText = feedbackType === 'NATURALNESS' || feedbackType === 'TONE'
      ? `${draft.text.replace(/[。！？]+$/, '')}～`
      : draft.text;
    student.drafts.push({ draft_id: id('draft'), revision: draft.revision + 1, messages: [{ type: 'text', content: revisedText }], text: revisedText, status: 'PENDING_REVIEW', decision_demo_only: true, feedback: [event.event_id] });
    student.revision++;
    return copy(student);
  }

  async approveDraft(studentId) {
    const student = this.#get(studentId);
    if (student.sales.contact_permission !== 'ALLOWED') throw new Error('联系许可未明确允许；不能批准对客草稿。');
    if (student.offer?.state === 'CUSTOM_OFFER_PROPOSAL') throw new Error('定制 Offer 尚未获批。');
    const draft = student.drafts.at(-1);
    if (!draft || draft.status !== 'PENDING_REVIEW') throw new Error('没有可批准的待审核草稿。');
    draft.status = 'APPROVED';
    student.approved_draft_id = draft.draft_id;
    student.events.push({ event_id: id('review'), event_type: 'APPROVED', actor: 'SALESPERSON', raw_content: null, draft_id: draft.draft_id, occurred_at: now() });
    student.revision++;
    return copy(student);
  }

  async recordActualSent(studentId, sent) {
    const student = this.#get(studentId);
    const draft = student.drafts.find(item => item.draft_id === student.approved_draft_id);
    if (!draft || draft.status !== 'APPROVED') throw new Error('没有仍有效的已批准草稿；不能标记已发送。');
    if (student.sales.contact_permission !== 'ALLOWED') throw new Error('联系许可未明确允许；不能记录销售发送。');
    const actualText = clean(sent.actual_sent_text);
    if (!actualText) throw new Error('必须录入在外部渠道实际发送的原文。');
    const event = {
      event_id: id('sent'), event_type: 'HUMAN_SENT', actor: 'SALESPERSON', raw_content: actualText,
      actual_sent_text: actualText, approved_text: draft.text, approved_draft_id: draft.draft_id,
      diff_from_approved: actualText === draft.text ? null : '人工实际发送内容与批准草稿不同',
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
      if (draft?.status === 'APPROVED' && latest !== 'INBOUND_RECEIVED') items.push({ student_id: student.student_id, display_name: student.display_name, type: 'SEND_DUE', label: '已批准草稿，待人工在外部渠道发送' });
      else if (student.drafts.at(-1)?.status === 'PENDING_REVIEW') items.push({ student_id: student.student_id, display_name: student.display_name, type: 'REVIEW_DUE', label: '草稿待审核，不能发送' });
      else if (student.offer?.state === 'CUSTOM_OFFER_PROPOSAL') items.push({ student_id: student.student_id, display_name: student.display_name, type: 'OFFER_REVIEW', label: '定制方案待负责人审核' });
      else if (latest === 'INBOUND_RECEIVED') items.push({ student_id: student.student_id, display_name: student.display_name, type: 'NEW_INBOUND', label: '学生有新消息，需重新判断' });
    }
    return copy(items);
  }
}

export function customerVisibleHistory(workspace) {
  return workspace.events.filter(event => event.event_type === 'INBOUND_RECEIVED' || event.event_type === 'HUMAN_SENT');
}
