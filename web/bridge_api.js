import { SalesWorkspaceApi } from './api.js';

const status = { DRAFTED: 'PENDING_REVIEW', REVIEW_CHANGES_REQUESTED: 'CHANGES_REQUESTED', HUMAN_SENT: 'SENT' };
const array = value => Array.isArray(value) ? value : [];
const identifier = value => encodeURIComponent(value);

export function bridgeSelected(locationLike = globalThis.location) {
  return locationLike?.hostname === '127.0.0.1' &&
    new URLSearchParams(locationLike.search).get('bridge') === 'offline';
}

export function workspaceView(raw) {
  const events = array(raw.events).map(event => ({
    ...event, raw_content: event.payload?.raw_text ?? event.payload?.actual_sent_text ??
      event.payload?.comment ?? null,
    diff_from_approved: event.payload?.diff_from_approved ?? null,
  }));
  const reviews = events.filter(event => event.event_type === 'REVIEW' &&
    event.payload?.action === 'REQUEST_CHANGES');
  const sent = array(raw.sent).map(event => ({ ...event, ...event.payload }));
  return {
    ...raw,
    source: { channel: 'OTHER', ...raw.source },
    education: raw.education || {},
    targets: { countries: [], universities: [], programs_or_majors: [], ...raw.targets },
    sales: { stage: 'NEW', contact_permission: 'UNKNOWN', ...raw.sales },
    memory: array(raw.memory), events, sent,
    decision: raw.decision ? { ...raw.decision, demo_only: true } : null,
    offer: raw.offer?.state === 'NONE' ? null : raw.offer,
    drafts: array(raw.drafts).map(draft => ({
      ...draft, status: status[draft.status] || draft.status,
      messages: array(draft.messages),
      feedback: reviews.filter(review => review.payload?.draft_event_id === draft.draft_id)
        .map(review => review.event_id),
    })),
    draft_status: status[raw.draft_status] || raw.draft_status,
  };
}

export class OfflineBridgeAdapter extends SalesWorkspaceApi {
  constructor(fetchImpl = (...args) => globalThis.fetch(...args), locationLike = globalThis.location) {
    super();
    if (!bridgeSelected(locationLike)) throw new Error('本地合成桥接仅可在 127.0.0.1 显式启用。');
    this.fetchImpl = fetchImpl;
  }

  async #request(method, path, body) {
    const response = await this.fetchImpl(path, {
      method, headers: body === undefined ? {} : {
        'Content-Type': 'application/json', 'X-Sales-Demo-Bridge': '1',
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(`本地桥接拒绝操作：${payload.error || response.status}`);
    return payload;
  }

  #path(id, action = '') { return `/api/students/${identifier(id)}${action ? `/${action}` : ''}`; }
  async getMode() { return (await this.#request('GET', '/api/health')).agent_mode; }
  async listStudents() { return (await this.#request('GET', '/api/students')).map(workspaceView); }
  async createStudent(record) {
    return workspaceView(await this.#request('POST', '/api/students', {
      ...record, demo_only: true, source: { channel: record.source?.channel || 'OTHER' },
    }));
  }
  async updateStudent(id, patch) { return workspaceView(await this.#request('PUT', this.#path(id), { ...patch, demo_only: true })); }
  async getWorkspace(id) { return workspaceView(await this.#request('GET', this.#path(id))); }
  async recordInbound(id, message) { return workspaceView(await this.#request('POST', this.#path(id, 'inbound'), { ...message, demo_only: true })); }
  async discussInternal(id, note) { return workspaceView(await this.#request('POST', this.#path(id, 'internal'), { ...note, demo_only: true })); }
  async requestDecision(id) {
    const result = await this.#request('POST', this.#path(id, 'decision'), { demo_only: true });
    const outcome = result.pipeline;
    if (!['REVIEW_REQUIRED', 'APPROVAL_REQUIRED', 'STOPPED'].includes(outcome?.status)) {
      throw new Error(`本地合成决策未生成可审核草稿：${result.pipeline?.status || 'UNKNOWN'} ${array(result.pipeline?.reason_codes).join('、')}`);
    }
    return { ...workspaceView(result.workspace), pipeline_outcome: outcome };
  }
  async reviewDraft(id, review) { return workspaceView(await this.#request('POST', this.#path(id, 'review'), { ...review, demo_only: true })); }
  async approveDraft(id) { return workspaceView(await this.#request('POST', this.#path(id, 'approve'), { demo_only: true })); }
  async approveCustomOffer(id, approval) { return this.reviewCustomOffer(id, { ...approval, action: 'APPROVE' }); }
  async reviewCustomOffer(id, review) {
    if (['REQUEST_CHANGES', 'REJECT'].includes(review.action)) {
      const comment = String(review.comment || '').trim();
      if (!comment) throw new Error('请填写内部审核理由。');
      return workspaceView(await this.#request('POST', this.#path(id, 'offer-review'), {
        action: review.action, offer_id: review.offer_id, comment, demo_only: true,
      }));
    }
    if (review.action !== 'APPROVE') throw new Error('不支持的定制 Offer 审核动作。');
    const approval = review;
    if (approval.confirmed_approval !== true) throw new Error('请先确认已人工核对产品、交付与定价。');
    if (['PRODUCT', 'DELIVERY', 'PRICING'].some(role => !approval.confirmed_roles?.includes(role))) {
      throw new Error('产品、交付、定价三个审批角色均须确认。');
    }
    return workspaceView(await this.#request('POST', this.#path(id, 'offer-review'), {
      action: 'APPROVE',
      offer_id: approval.offer_id, scope: approval.scope, exclusions: approval.exclusions,
      payment_terms: approval.payment_terms, valid_until: approval.valid_until,
      confirmed_roles: approval.confirmed_roles,
      price: Number(approval.price), demo_only: true,
    }));
  }
  async recordActualSent(id, sent) {
    if (sent.confirmed_external_send !== true) throw new Error('请确认已在外部渠道人工发送；本页不会发送。');
    return workspaceView(await this.#request('POST', this.#path(id, 'actual-sent'), { ...sent, demo_only: true }));
  }
  async labelProgressAssessment(id, label) {
    const assessment_event_id = String(label.assessment_event_id || '').trim();
    const human_label = String(label.human_label || '').trim();
    const reason = String(label.reason || '').trim();
    if (!assessment_event_id || !human_label || !reason) throw new Error('请选择判断标签并填写依据。');
    return workspaceView(await this.#request('POST', this.#path(id, 'progress-label'), {
      demo_only: true, assessment_event_id, human_label, reason,
    }));
  }
  async listReminders() {
    const students = await this.listStudents();
    const items = [];
    for (const summary of students) {
      if (summary.sales.contact_permission === 'DO_NOT_CONTACT') continue;
      const w = await this.getWorkspace(summary.student_id);
      const type = w.approved_draft_id ? 'SEND_DUE' : w.draft_status === 'PENDING_REVIEW' ? 'REVIEW_DUE' :
        w.has_pending_offer ? 'OFFER_REVIEW' : w.latest_event_type === 'INBOUND_RECEIVED' ? 'NEW_INBOUND' : null;
      if (type) items.push({ student_id: w.student_id, display_name: w.display_name, type,
        label: { SEND_DUE: '已批准草稿，待人工在外部渠道发送', REVIEW_DUE: '草稿待审核，不能发送',
          OFFER_REVIEW: '定制方案待负责人审核', NEW_INBOUND: '学生有新消息，需重新判断' }[type] });
    }
    return items;
  }
}
