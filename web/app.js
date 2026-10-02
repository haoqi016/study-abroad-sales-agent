import { DemoAdapter, customerVisibleHistory, localActionTime, studentCreatePayload, studentUpdatePayload } from './api.js';
import { OfflineBridgeAdapter, bridgeSelected } from './bridge_api.js';

const usingBridge = bridgeSelected();
const api = usingBridge ? new OfflineBridgeAdapter() : new DemoAdapter();
const root = document.querySelector('#app');
const state = { page: 'students', studentId: null, students: [], workspace: null, reminders: [], loading: false, error: '', notice: '', filter: 'ALL', requestId: 0, showAdd: false, showEdit: false, bridgeMode: 'SCRIPTED' };
const localModel = () => usingBridge && state.bridgeMode === 'LOCAL_OLLAMA';

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const text = value => escapeHtml(value || '—');
const fieldValue = value => escapeHtml(value ?? '');
const dateText = value => value ? new Intl.DateTimeFormat('en-GB', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : 'Not set';
const stageLabel = { NEW: 'New lead', CONSULTING: 'Consulting', QUALIFIED: 'Needs qualified', PRICE_OBJECTION: 'Price objection', LIKELY_TO_PAY: 'Likely to pay', WAITING_STUDENT: 'Waiting for student', WAITING_DECISION_MAKER: 'Waiting for decision maker', READY_TO_SIGN: 'Ready to sign', WON: 'Won', LOST: 'Lost', DO_NOT_CONTACT: 'Do not contact' };
const sourceLabel = { XIAOHONGSHU: 'Xiaohongshu', WECHAT: 'WeChat', REFERRAL: 'Referral', OTHER: 'Other' };
const sourceClass = { CUSTOMER_STATED: 'fact', HUMAN_CONFIRMED: 'fact', SYSTEM_VERIFIED: 'fact', AGENT_HYPOTHESIS: 'hypothesis' };
const sourceName = { CUSTOMER_STATED: 'Student stated', HUMAN_CONFIRMED: 'Human confirmed', SYSTEM_VERIFIED: 'System verified', AGENT_HYPOTHESIS: 'Unverified hypothesis' };
const memoryLabel = { 'budget.total_ceiling': 'Service fee budget ceiling', 'intent.bargaining': 'May be negotiating price', payer: 'Payer', decision_maker: 'Decision maker' };
const progressLabel = { COMMITMENT_SIGNAL: 'Expressed signing intent (no verified sale)', OBJECTION_CLARIFIED: 'Clarified an objection', ENGAGED_WITH_NEW_INFORMATION: 'Engaged with new information', NEUTRAL: 'Neutral reply', DEFERRED: 'Deferred', EXIT: 'Student explicitly exits', DO_NOT_CONTACT: 'Explicit do not contact request', UNCLEAR: 'Cannot determine progress from the message' };

function shell(content) {
  root.innerHTML = `
    <header class="topbar"><div class="brand"><span class="brand-mark">S</span><div><strong>Sales Workspace</strong><small>${localModel() ? 'Local Ollama Agent experiment · synthetic data only' : usingBridge ? 'Local Python synthetic script · not a real Agent' : 'Browser synthetic demo · not a real Agent'}</small></div></div><span class="offline-badge">${localModel() ? 'LOCAL MODEL' : usingBridge ? 'DEMO/OFFLINE' : 'DEMO'}</span></header>
    <main id="main-content" class="main-content">${state.error ? `<div class="alert error" role="alert">${text(state.error)} <button type="button" data-action="dismiss-error" aria-label="Dismiss error">×</button></div>` : ''}${state.notice ? `<div class="alert success" role="status">${text(state.notice)}</div>` : ''}${content}</main>
    <nav class="bottom-nav" aria-label="Main navigation"><button type="button" data-action="nav-students" class="${state.page === 'students' || state.page === 'detail' ? 'active' : ''}" ${state.page === 'students' || state.page === 'detail' ? 'aria-current="page"' : ''}><span aria-hidden="true">▦</span>Students</button><button type="button" data-action="nav-reminders" class="${state.page === 'reminders' ? 'active' : ''}" ${state.page === 'reminders' ? 'aria-current="page"' : ''}><span aria-hidden="true">◷</span>Reminders</button><button type="button" data-action="nav-me" class="${state.page === 'me' ? 'active' : ''}" ${state.page === 'me' ? 'aria-current="page"' : ''}><span aria-hidden="true">◎</span>Settings</button></nav>`;
}

function render() {
  if (state.loading) return shell(`<section class="loading" role="status"><span class="spinner" aria-hidden="true"></span>${text(state.busyText || 'Loading demo data…')}</section>`);
  if (state.page === 'students') return renderStudents();
  if (state.page === 'detail') return renderDetail();
  if (state.page === 'reminders') return renderReminders();
  return renderMe();
}

function renderStudents() {
  const students = state.students.filter(student => state.filter === 'ALL' || (state.filter === 'PRICE_OBJECTION' ? student.sales.stage === 'PRICE_OBJECTION' : state.filter === 'REVIEW_DUE' ? student.draft_status === 'PENDING_REVIEW' || student.has_pending_offer : state.filter === 'DO_NOT_CONTACT' ? student.sales.contact_permission === 'DO_NOT_CONTACT' : student.sales.stage === state.filter));
  shell(`<section class="page-heading"><div><p class="eyebrow">Customer workspace</p><h1>Students</h1><p>Review status, then open one student to handle the next action.</p></div><button type="button" class="button primary" data-action="toggle-add">+ Add student</button></section>
    <div class="demo-warning">Synthetic demo only. Do not enter real student information; ${localModel() ? 'local inference can take several minutes and may fail validation or the V2 Gate; failure will not create a scripted draft.' : usingBridge ? 'data resets when the server restarts.' : 'new content resets when the page reloads.'}</div>
    ${state.showAdd ? addStudentForm() : ''}
    <div class="filter-row" role="group" aria-label="Filter students">${[['ALL','All'], ['CONSULTING','Consulting'], ['PRICE_OBJECTION','Price objection'], ['LIKELY_TO_PAY','Likely to pay'], ['REVIEW_DUE','Review due'], ['WAITING_STUDENT','Waiting for student'], ['DO_NOT_CONTACT','Contact stopped']].map(([value,label]) => `<button type="button" data-action="filter" data-value="${value}" class="chip ${state.filter === value ? 'selected' : ''}" aria-pressed="${state.filter === value}">${label}</button>`).join('')}</div>
    ${students.length ? `<div class="student-list">${students.map(studentCard).join('')}</div>` : `<div class="empty-state"><span aria-hidden="true">◎</span><h2>No students match this filter</h2><p>Try another filter or add a synthetic student.</p></div>`}`);
}

function studentCard(student) {
  return `<button type="button" data-action="open-student" data-id="${text(student.student_id)}" class="student-card"><span class="avatar" aria-hidden="true">${text(student.display_name.slice(0,1))}</span><span class="student-card-body"><span class="card-title"><strong>${text(student.display_name)}</strong><span class="stage">${text(stageLabel[student.sales.stage] || student.sales.stage)}</span></span><span class="muted">${text(sourceLabel[student.source.channel] || student.source.channel)} · ${text([...student.targets.countries, ...student.targets.universities, ...student.targets.programs_or_majors].join(' / ') || 'Target not entered')}</span><span class="card-objection">${text(student.sales.current_objection || 'Current obstacle not recorded')}</span><span class="card-foot">${student.has_pending_offer ? 'Custom package awaits approval' : student.draft_status === 'PENDING_REVIEW' ? 'Draft awaits review' : `Next action: ${dateText(student.sales.next_action_at)}`}</span></span><span class="chevron" aria-hidden="true">›</span></button>`;
}

function addStudentForm() {
  return `<form id="add-student" class="panel form-panel"><div class="section-heading"><h2>Add student (demo)</h2><button type="button" class="text-button" data-action="toggle-add">Cancel</button></div><p class="muted">Use synthetic data only; display names must include DEMO. A next action time is an internal suggestion, not contact consent. This page does not send messages or push notifications.</p>${usingBridge ? '<p class="helper">The offline bridge does not support platform IDs; only the synthetic source channel is recorded.</p>' : ''}<div class="form-grid"><label>Display name <span class="required">Required</span><input name="display_name" required maxlength="80" placeholder="${usingBridge ? 'Example: DEMO Student' : 'Example: DEMO Student X'}"></label><label>Source<select name="channel"><option value="XIAOHONGSHU">Xiaohongshu</option><option value="WECHAT">WeChat</option><option value="REFERRAL">Referral</option><option value="OTHER">Other</option></select></label>${usingBridge ? '' : '<label>Platform ID (browser demo only)<input name="platform_handle" maxlength="80" placeholder="Synthetic demo ID"></label>'}<label>Undergraduate university<input name="university" maxlength="120"></label><label>Undergraduate major<input name="major" maxlength="120"></label><label>Average score<input name="score" inputmode="decimal" maxlength="20"></label><label>Year<select name="year"><option value="UNKNOWN">Unknown</option><option value="YEAR_1">Year 1</option><option value="YEAR_2">Year 2</option><option value="YEAR_3">Year 3</option><option value="YEAR_4">Year 4</option><option value="GRADUATED">Graduated</option></select></label><label>Target country or region<select name="country"><option value="">Undecided</option><option value="SG">Singapore</option><option value="HK">Hong Kong</option><option value="UK">United Kingdom</option><option value="AU">Australia</option></select></label><label>Target universities<input name="target_universities" maxlength="240" placeholder="Separate universities with commas"></label><label>Target majors or programs<input name="target_programs" maxlength="240" placeholder="Separate programs with commas"></label><label>Sales stage<select name="stage">${Object.entries(stageLabel).map(([value,label]) => `<option value="${value}">${label}</option>`).join('')}</select></label><label>Current obstacle<input name="current_objection" maxlength="240"></label><label>Decision maker<input name="decision_maker" maxlength="120" placeholder="Example: student or parent (DEMO)"></label><label>Contact permission<select name="contact_permission"><option value="UNKNOWN">Unknown</option><option value="ALLOWED">Contact allowed</option><option value="LIMITED">Limited contact</option><option value="DO_NOT_CONTACT">Do not contact</option></select></label><label>Next action time<input name="next_action_at" type="datetime-local"></label><label>Next action reason<input name="next_action_reason" maxlength="240" placeholder="Example: demo follow up on needs"></label></div><button type="submit" class="button primary wide">Save demo record</button></form>`;
}

function renderDetail() {
  const w = state.workspace;
  if (!w) return shell('<div class="empty-state"><h1>No student selected</h1><button class="button" data-action="nav-students">Back to student list</button></div>');
  const blocked = w.sales.contact_permission === 'DO_NOT_CONTACT';
  const sendAllowed = w.sales.contact_permission === 'ALLOWED';
  const latestDraft = w.drafts.at(-1);
  const offerPending = usingBridge ? w.has_pending_offer : w.offer?.state === 'CUSTOM_OFFER_PROPOSAL';
  const offerRejected = usingBridge && ['REQUEST_CHANGES', 'REJECT'].includes(w.offer?.review_action);
  shell(`<div class="detail-back"><button type="button" class="text-button" data-action="nav-students">← All students</button><span class="revision">Record revision ${w.revision}</span></div>
    <section class="profile-header"><div class="avatar large" aria-hidden="true">${text(w.display_name.slice(0,1))}</div><div><p class="eyebrow">${text(sourceLabel[w.source.channel] || w.source.channel)} · ${usingBridge ? 'Platform ID not supported' : text(w.source.platform_handle)}</p><h1>${text(w.display_name)}</h1><span class="stage">${text(stageLabel[w.sales.stage] || w.sales.stage)}</span></div><button type="button" class="button subtle edit-profile" data-action="toggle-edit">Edit</button></section>
    ${blocked ? '<div class="alert blocked" role="alert"><strong>Contact stopped</strong>: no sales draft, approval, or send reminder is permitted. Internal records remain visible.</div>' : ''}
    ${!blocked && !sendAllowed ? '<div class="alert blocked" role="alert"><strong>Contact permission is not explicitly allowed</strong>: internal information may be organized, but sales sends cannot be approved or recorded.</div>' : ''}
    ${state.showEdit ? editStudentForm(w) : ''}
    <section class="panel status-panel"><div class="section-heading"><h2>Current status</h2><span class="section-kicker">Check before deciding</span></div><dl class="facts"><div><dt>Current obstacle</dt><dd>${text(w.sales.current_objection || 'Not recorded')}</dd></div><div><dt>Target country or region</dt><dd>${text(w.targets.countries.join(', ') || 'Unknown')}</dd></div><div><dt>Target universities</dt><dd>${text(w.targets.universities.join(', ') || 'Undecided')}</dd></div><div><dt>Target majors or programs</dt><dd>${text(w.targets.programs_or_majors.join(', ') || 'Undecided')}</dd></div><div><dt>University / average score</dt><dd>${text(w.education.undergraduate_university_raw || 'Not entered')} · ${text(w.education.score_raw || 'Not entered')}</dd></div><div><dt>Decision maker</dt><dd>${text(w.sales.decision_maker || 'Unknown')}</dd></div><div><dt>Contact permission</dt><dd>${blocked ? 'Do not contact' : w.sales.contact_permission === 'ALLOWED' ? 'Contact allowed' : w.sales.contact_permission === 'LIMITED' ? 'Limited contact' : 'Unconfirmed'}</dd></div><div><dt>Next action time</dt><dd>${dateText(w.sales.next_action_at)}</dd></div><div><dt>Next action reason</dt><dd>${text(w.sales.next_action_reason || 'Not set')}</dd></div></dl></section>
    ${renderMemory(w)}
    ${renderTimeline(w)}
    ${renderProgress(w)}
    <section class="panel inputs-panel"><div class="section-heading"><h2>Continue workflow</h2><span class="section-kicker">Two input types must stay separate</span></div><div class="dual-input"><form id="inbound-form" class="input-card student-input"><h3>Record student message</h3><p>Paste only the exact student message. The Agent cannot rewrite the stored original.</p><label>Student exact words<textarea name="raw_text" rows="3" required placeholder="Paste the exact student message"></textarea></label><label>Time received<input name="occurred_at" type="datetime-local"></label><button type="submit" class="button primary">Record student message</button></form><form id="internal-form" class="input-card internal-input"><h3>Discuss with Agent</h3><p>Internal feedback for the Agent is not treated as a student message or customer fact.</p><label>Internal note<textarea name="raw_text" rows="3" required placeholder="Example: this sounds stiff; explain the value first"></textarea></label><button type="submit" class="button">Record internal note</button></form></div></section>
    ${renderDecision(w, blocked, offerPending)}
    ${renderOffer(w, blocked)}
    ${renderDrafts(w, blocked, offerPending || offerRejected, latestDraft, sendAllowed && !offerRejected)}
    <div class="bottom-space"></div>`);
}

function editStudentForm(w) {
  return `<form id="edit-student" class="panel form-panel"><h2>Edit current status</h2><p class="muted">Sales assessments and student messages are recorded separately. A next action is an internal suggestion, not contact consent, and causes no automatic send or notification.</p><div class="form-grid"><label>Display name<input name="display_name" required maxlength="80" value="${fieldValue(w.display_name)}"></label><label>Target country or region<input name="target_countries" maxlength="240" value="${fieldValue(w.targets.countries.join(', '))}" placeholder="Separate with commas"></label><label>Target universities<input name="target_universities" maxlength="240" value="${fieldValue(w.targets.universities.join(', '))}" placeholder="Separate with commas"></label><label>Target majors or programs<input name="target_programs" maxlength="240" value="${fieldValue(w.targets.programs_or_majors.join(', '))}" placeholder="Separate with commas"></label><label>Sales stage<select name="stage">${Object.entries(stageLabel).map(([value,label]) => `<option value="${value}" ${w.sales.stage === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label><label>Current obstacle<input name="current_objection" maxlength="240" value="${fieldValue(w.sales.current_objection)}"></label><label>Decision maker<input name="decision_maker" maxlength="120" value="${fieldValue(w.sales.decision_maker)}"></label><label>Contact permission<select name="contact_permission"><option value="UNKNOWN" ${w.sales.contact_permission === 'UNKNOWN' ? 'selected' : ''}>Unknown</option><option value="ALLOWED" ${w.sales.contact_permission === 'ALLOWED' ? 'selected' : ''}>Contact allowed</option><option value="LIMITED" ${w.sales.contact_permission === 'LIMITED' ? 'selected' : ''}>Limited contact</option><option value="DO_NOT_CONTACT" ${w.sales.contact_permission === 'DO_NOT_CONTACT' ? 'selected' : ''}>Do not contact</option></select></label><label>Next action time<input name="next_action_at" type="datetime-local" value="${localActionTime(w.sales.next_action_at)}"></label><label>Next action reason<input name="next_action_reason" maxlength="240" value="${fieldValue(w.sales.next_action_reason)}"></label></div><button type="submit" class="button primary">Save status</button></form>`;
}

function renderMemory(w) {
  return `<section class="panel"><div class="section-heading"><h2>Student long term memory</h2><span class="section-kicker">Structured information with provenance</span></div>${w.memory.length ? `<div class="memory-list">${w.memory.map(item => `<div class="memory-item ${sourceClass[item.epistemic_status] || ''}"><div><strong>${text(memoryLabel[item.key] || item.key)}</strong><span class="source-pill">${text(sourceName[item.epistemic_status] || item.epistemic_status)}</span></div><p>${text(item.value)}</p><small>Evidence: ${text(item.evidence_span || item.source_event_ids.join(', '))} · ${text(item.source_event_ids.join(', '))}</small></div>`).join('')}</div>` : '<p class="muted">No source attributed memory yet. Model hypotheses are not treated as facts.</p>'}</section>`;
}

function renderTimeline(w) {
  const visible = customerVisibleHistory(w);
  const internal = w.events.filter(event => !['INBOUND_RECEIVED', 'HUMAN_SENT'].includes(event.event_type));
  return `<section class="panel timeline-panel"><div class="section-heading"><h2>Actual student conversation</h2><span class="section-kicker">Only student messages and actual external sends</span></div>${visible.length ? `<ol class="timeline">${visible.map(event => `<li class="bubble ${event.event_type === 'INBOUND_RECEIVED' ? 'student' : 'sent'}"><span class="bubble-type">${event.event_type === 'INBOUND_RECEIVED' ? 'Student exact words' : 'Actually sent by a human'}</span><p>${text(event.raw_content)}</p><small>${dateText(event.occurred_at)}</small>${event.diff_from_approved ? `<small class="diff-note">${text(event.diff_from_approved)}; approved draft and actual sent text are stored separately</small>` : ''}</li>`).join('')}</ol>` : '<p class="muted">No student visible messages yet. Unsent drafts do not appear here.</p>'}<details class="internal-log"><summary>View internal events (${internal.length})</summary>${internal.length ? `<ul>${internal.map(event => `<li><strong>${text(event.event_type)}</strong> · ${text(event.raw_content || 'Status changed')}<small>${dateText(event.occurred_at)}</small></li>`).join('')}</ul>` : '<p>No internal events</p>'}</details></section>`;
}

function renderProgress(w) {
  if (!usingBridge) return '';
  const assessmentEvent = w.progress_assessments?.at(-1);
  if (!assessmentEvent) return '';
  const assessment = assessmentEvent.payload;
  const inbound = w.events.find(event => event.event_id === assessment.inbound_event_id);
  const labels = (w.progress_labels || []).filter(event => event.payload?.assessment_event_id === assessmentEvent.event_id);
  const effective = w.effective_progress_labels?.find(item => item.assessment_event_id === assessmentEvent.event_id);
  const latestLabel = labels.at(-1)?.payload;
  const reflection = w.case_reflections?.at(-1)?.payload;
  const signal = value => text(progressLabel[value] || value);
  return `<section class="panel progress-panel"><div class="section-heading"><h2>Assessment after student reply</h2><span class="section-kicker">Human labels · synthetic data only</span></div>
    <div class="progress-source"><strong>Student exact words (immutable)</strong><p>${text(inbound?.payload?.raw_text || inbound?.raw_content || 'Source message event not found; check the record')}</p><small>Source event: ${text(assessment.inbound_event_id)}</small></div>
    <dl class="facts"><div><dt>Original model prediction</dt><dd>${signal(assessment.predicted_signal)}</dd></div><div><dt>Prediction evidence spans</dt><dd>${text(assessment.supporting_spans?.join(', ') || 'No sufficiently explicit source message')}</dd></div><div><dt>Prediction confidence</dt><dd>${assessment.confidence == null ? 'Not provided' : text(assessment.confidence)}</dd></div><div><dt>Latest valid human label</dt><dd>${effective?.human_label ? signal(effective.human_label) : 'Not labeled'}</dd></div><div><dt>Latest label reason</dt><dd>${text(latestLabel?.reason || 'Not labeled')}</dd></div></dl>
    <p class="muted">Human labels are appended separately; the original prediction and student message remain visible. An assessment does not prove signing, payment, or strategy effectiveness.</p>
    <form id="progress-label-form" class="progress-label-form"><input type="hidden" name="assessment_event_id" value="${fieldValue(assessmentEvent.event_id)}"><label>Human assessment label<select name="human_label" required><option value="">Select</option>${Object.entries(progressLabel).map(([value, label]) => `<option value="${value}">${text(label)}</option>`).join('')}</select></label><label>Assessment reason <span class="required">Required</span><textarea name="reason" rows="3" required maxlength="1000" placeholder="Explain the label using the student message"></textarea></label><button type="submit" class="button primary">Save human label</button></form>
    <details class="progress-history"><summary>View label history (${labels.length})</summary>${labels.length ? `<ol>${labels.map(event => `<li><strong>${signal(event.payload.human_label)}</strong><p>${text(event.payload.reason)}</p><small>${dateText(event.occurred_at)} · ${text(event.payload.labeled_by)}</small></li>`).join('')}</ol>` : '<p class="muted">No human labels yet.</p>'}</details>
    ${reflection ? `<details><summary>View unverified reflection for this customer</summary><p>${text(reflection.observed_outcome)}</p><p>Possible explanation: ${text(reflection.possible_explanation)}</p><small>${text(reflection.learning_status)} · Not automatically added to sales knowledge</small></details>` : ''}</section>`;
}

function renderDecision(w, blocked, offerPending) {
  const d = w.decision;
  const offerStatus = w.offer?.review_action === 'REJECT' ? 'Rejected' :
    w.offer?.review_action === 'REQUEST_CHANGES' ? 'Returned for changes' : d?.offer?.state || 'NONE';
  return `<section class="panel decision-panel"><div class="section-heading"><h2>Internal decision</h2><span class="section-kicker">Not shown to the student</span></div>${d ? `<div class="objective"><span class="eyebrow">Current objective</span><strong>${text(d.current_objective?.goal)}</strong><p>${text(d.current_objective?.why_now)}</p><small>Observable signals: ${text(d.current_objective?.success_signals?.join(', '))}</small></div><dl class="facts"><div><dt>Selected action</dt><dd>${text(d.action_plan?.selected_action)}</dd></div><div><dt>Base reply</dt><dd>${text(d.content_contract?.semantic_draft)}</dd></div><div><dt>Offer Status</dt><dd>${text(offerStatus)}</dd></div>${usingBridge && d.input_event_ids?.length ? `<div><dt>Input source events</dt><dd>${text(d.input_event_ids.join(', '))}</dd></div>` : ''}</dl>${d.demo_only ? `<p class="demo-note">${localModel() ? 'Local model experiment; V2 Gate and human review are still required. This does not mean it passed business evaluation.' : 'This is fixed rule demo output, not a model judgment or a decision that passed business evaluation.'}</p>` : ''}` : '<div class="empty-inline">No decision yet. Record a student message, then request a demo decision.</div>'}${blocked ? '<p class="blocked-note">Do not contact: sales decision actions are unavailable.</p>' : `<button type="button" class="button" data-action="request-decision" ${offerPending || ['DRAFTED', 'APPROVED', 'PENDING_REVIEW'].includes(w.draft_status) ? 'disabled' : ''}>${localModel() ? 'Run local Ollama Agent' : 'Run synthetic demo decision'}</button>`}${localModel() ? '<p class="muted">Local inference may take several minutes. A failure or Gate rejection will not be replaced with a scripted reply.</p>' : ''}${offerPending ? '<p class="blocked-note">Custom package awaits approval; no sendable draft can be generated.</p>' : ''}</section>`;
}

function renderOffer(w, blocked) {
  if (!w.offer) return '';
  if (w.offer.state === 'STANDARD_OFFER') {
    const quote = w.offer.quote;
    return `<section class="panel offer-panel"><div class="section-heading"><h2>Standard offer</h2><span class="section-kicker">Customer draft still needs review</span></div><dl class="facts"><div><dt>Product</dt><dd>${text(quote?.product_id)}</dd></div><div><dt>Standard price</dt><dd>${quote?.price == null ? 'No quote in this turn' : `${text(quote.price)} CNY`}</dd></div><div><dt>Pricing policy</dt><dd>${text(quote?.policy_version)}</dd></div></dl><p class="muted">This shows only the standard price used in this decision. The draft is not approved or sent.</p></section>`;
  }
  if (!['CUSTOM_OFFER_PROPOSAL', 'APPROVED_CUSTOM_OFFER'].includes(w.offer.state)) {
    return `<section class="panel offer-panel"><h2>Offer status needs review</h2><p>${text(w.offer.state)}</p></section>`;
  }
  if (usingBridge && ['REQUEST_CHANGES', 'REJECT'].includes(w.offer.review_action)) {
    const rejected = w.offer.review_action === 'REJECT';
    const proposal = w.offer.proposal || {};
    const review = w.events.find(event => event.event_id === w.offer.review_event_id);
    return `<section class="panel offer-panel reviewed-offer"><div class="section-heading"><h2>Custom offer · ${rejected ? 'Rejected' : 'Returned for changes'}</h2><span class="section-kicker">Internal record · cannot be sent to customer</span></div><p class="approval-warning">${rejected ? 'This proposal was rejected.' : 'This proposal was returned for changes.'} There is no approved quote or sendable draft.</p><dl class="facts"><div><dt>Customer evidence</dt><dd>${text(proposal.customer_need_evidence?.join(', '))}</dd></div><div><dt>Originally proposed scope</dt><dd>${text(proposal.proposed_scope?.join(', '))}</dd></div><div><dt>Explicit exclusions</dt><dd>${text(proposal.explicit_exclusions?.join(', '))}</dd></div><div><dt>Original proposed price</dt><dd>${proposal.proposed_price == null ? 'Not entered' : `${text(proposal.proposed_price)} CNY (internal, unapproved)`}</dd></div><div><dt>Payment terms</dt><dd>${text(proposal.payment_terms?.join(', '))}</dd></div><div><dt>Delivery requirements / risks</dt><dd>${text([...(proposal.delivery_requirements || []), ...(proposal.risks || [])].join(', '))}</dd></div><div><dt>Review reason</dt><dd>${text(review?.payload?.comment)}</dd></div></dl></section>`;
  }
  const pending = usingBridge ? w.has_pending_offer : w.offer.state === 'CUSTOM_OFFER_PROPOSAL';
  const proposal = w.offer.proposal;
  const approval = w.events.find(event => event.event_id === w.offer.approval_event_id)?.payload;
  const scope = proposal?.proposed_scope || [];
  const price = approval?.approved_offer?.approved_price ?? w.offer.price;
  const approvers = approval?.approved_offer?.approved_by || w.offer.approved_by || [];
  const approverLabel = Array.isArray(approvers) ? approvers.map(item => `${item.role}: ${item.actor}`).join(', ') : approvers;
  const bridgeApprovalFields = usingBridge ? `<label>Approved scope (comma separated)<input name="scope" required value="${fieldValue(scope.join(', '))}"></label><label>Explicit exclusions (comma separated, optional)<input name="exclusions" value="${fieldValue((proposal?.explicit_exclusions || []).join(', '))}"></label><label>Approved payment terms (comma separated)<input name="payment_terms" required value="${fieldValue((proposal?.payment_terms || []).join(', '))}"></label><label>Valid until (optional)<input name="valid_until" type="datetime-local"></label><label><input name="role_PRODUCT" type="checkbox" value="true" required> I confirm product scope approval (synthetic exercise)</label><label><input name="role_DELIVERY" type="checkbox" value="true" required> I confirm delivery approval (synthetic exercise)</label><label><input name="role_PRICING" type="checkbox" value="true" required> I confirm pricing approval (synthetic exercise)</label><label><input name="confirmed_approval" type="checkbox" value="true" required> I have checked all terms above (synthetic exercise)</label>` : '';
  return `<section class="panel offer-panel ${pending ? 'needs-approval' : 'approved-offer'}"><div class="section-heading"><h2>Custom offer · ${pending ? 'Internal proposal' : 'Approved by a human'}</h2><span class="section-kicker">${pending ? 'Cannot be sent' : 'Message draft still needs review'}</span></div><p>${text(w.offer.summary || scope.join(', '))}</p>${proposal ? `<dl class="facts"><div><dt>Customer evidence</dt><dd>${text(proposal.customer_need_evidence?.join(', '))}</dd></div><div><dt>Proposed scope</dt><dd>${text(scope.join(', '))}</dd></div><div><dt>Explicit exclusions</dt><dd>${text(proposal.explicit_exclusions?.join(', '))}</dd></div><div><dt>Original proposed price</dt><dd>${text(proposal.proposed_price)} CNY (internal)</dd></div><div><dt>Payment terms</dt><dd>${text(proposal.payment_terms?.join(', '))}</dd></div><div><dt>Delivery requirements / risks</dt><dd>${text([...(proposal.delivery_requirements || []), ...(proposal.risks || [])].join(', '))}</dd></div><div><dt>Delivery cost / margin</dt><dd>${text(proposal.estimated_delivery_cost?.status)} / ${text(proposal.estimated_margin?.status)}</dd></div></dl>` : ''}<dl class="facts"><div><dt>Approved price</dt><dd>${price == null ? 'Unapproved; cannot quote' : `${text(price)} CNY`}</dd></div><div><dt>Approver</dt><dd>${text(approverLabel)}</dd></div></dl>${pending && !blocked ? `<div class="approval-warning">This is an internal proposal. An approver must check product, delivery, and pricing before any customer promise.</div><form id="offer-form"><label>Approver approved price (CNY) <span class="required">Required</span><input name="price" type="number" min="1" step="1" required inputmode="numeric"></label>${bridgeApprovalFields}<button type="submit" class="button primary">Demo: approve package and price</button></form>${usingBridge ? `<form id="offer-review-form" class="offer-review-form"><h3>Internal review</h3><label>Reason for revision or rejection <span class="required">Required</span><textarea name="comment" rows="3" required placeholder="Specify terms to change or the reason for rejection"></textarea></label><div class="button-row"><button type="submit" name="review_action" value="REQUEST_CHANGES" class="button">Request changes</button><button type="submit" name="review_action" value="REJECT" class="button danger-button">Reject proposal</button></div></form>` : ''}<small class="helper">This changes synthetic demo state only. A real backend must validate approval roles, permissions, and pricing.</small>` : ''}</section>`;
}

function renderDrafts(w, blocked, offerPending, latestDraft, sendAllowed) {
  return `<section class="panel draft-panel"><div class="section-heading"><h2>Customer reply drafts</h2><span class="section-kicker">Every version is retained</span></div>${w.drafts.length ? `<div class="draft-stack">${w.drafts.map((draft, index) => `<article class="draft-version ${draft === latestDraft ? 'latest' : ''}"><div class="draft-head"><strong>Draft ${index + 1}</strong><span class="status-pill ${draft.status.toLowerCase()}">${draftStatus(draft.status)}</span></div><div class="message-preview">${draft.messages.map(message => message.type === 'text' ? `<p>${text(message.content)}</p>` : `<small>Image suggestion: ${text(message.intent)}</small>`).join('')}</div>${draft.feedback.length ? `<small>Linked revision feedback: ${text(draft.feedback.join(', '))}</small>` : ''}</article>`).join('')}</div>` : `<div class="empty-inline">${offerPending ? 'The custom package is unapproved, so no reviewable customer draft can be created.' : 'No customer drafts yet.'}</div>`}
    ${latestDraft?.status === 'PENDING_REVIEW' && !offerPending && !blocked ? `<form id="review-form" class="review-form"><h3>Human review for this version</h3><label>Feedback category<select name="feedback_type"><option value="NATURALNESS">Tone / naturalness</option><option value="STRATEGY_ERROR">Strategy error</option><option value="FACT_ERROR">Fact error</option><option value="POLICY_ERROR">Permission / policy error</option></select></label><label>Revision feedback<textarea name="comment" rows="2" placeholder="Explain what is wrong and the desired change"></textarea></label><div class="button-row"><button type="submit" class="button">${usingBridge ? 'Request changes (then regenerate)' : 'Request changes and create demo revision'}</button><button type="button" class="button primary" data-action="approve-draft" ${sendAllowed ? '' : 'disabled'}>Approve draft</button></div></form>` : ''}
    ${latestDraft?.status === 'APPROVED' && sendAllowed ? `<div class="approved-actions"><p class="approved-callout">The draft is approved, but <strong>has not been sent</strong>. Send it manually through an external channel, then record the exact text sent.</p><form id="sent-form"><label>Actual sent text <span class="required">Required</span><textarea name="actual_sent_text" rows="4" required>${text(latestDraft.text)}</textarea></label><label>Actual sent time<input name="sent_at" type="datetime-local"></label>${usingBridge ? '<label><input name="confirmed_external_send" type="checkbox" value="true" required> I confirm a human sent this through an external channel (synthetic exercise)</label>' : ''}<button type="submit" class="button primary">Record human external send only</button></form></div>` : ''}
    ${blocked ? '<p class="blocked-note">Do not contact: no new sales send may be approved or recorded.</p>' : ''}</section>`;
}

function draftStatus(status) { return { PENDING_REVIEW: 'Awaiting human review', CHANGES_REQUESTED: 'Changes requested', APPROVED: 'Approved · unsent', SENT: 'Actual send recorded', STALE: 'New student message · stale' }[status] || status; }

function renderReminders() {
  shell(`<section class="page-heading"><div><p class="eyebrow">In app tasks</p><h1>Reminders</h1><p>Mobile push notifications are not implemented. This list shows synthetic demo tasks only.</p></div></section><div class="demo-warning">Unapproved drafts prompt review only. A new student reply makes an older approved draft stale. Do not contact records have no send reminders.</div>${state.reminders.length ? `<div class="reminder-list">${state.reminders.map(item => `<button type="button" class="reminder-card" data-action="open-student" data-id="${text(item.student_id)}"><span class="reminder-type">${text(({ SEND_DUE: 'Awaiting human send', REVIEW_DUE: 'Review due', OFFER_REVIEW: 'Package awaits approval', NEW_INBOUND: 'New message' })[item.type] || item.type)}</span><strong>${text(item.display_name)}</strong><span>${text(item.label)}</span><span class="chevron" aria-hidden="true">›</span></button>`).join('')}</div>` : '<div class="empty-state"><span aria-hidden="true">✓</span><h2>No in app tasks right now</h2><p>This page never sends messages to students.</p></div>'}`);
}

function renderMe() {
  shell(`<section class="page-heading"><div><p class="eyebrow">Demo settings</p><h1>Settings</h1></div></section><section class="panel"><h2>Data mode</h2><p>Current: ${localModel() ? 'Local Ollama Agent experiment; synthetic students pass through the V2 decision, conversation, and Gate' : usingBridge ? 'Local Python offline bridge; fixed synthetic script passes through the V2 Gate' : 'Browser DemoAdapter fixed rules'}</p>${location.hostname === '127.0.0.1' ? `<p><a class="button" href="/${usingBridge ? '' : '?bridge=offline'}">Switch to ${usingBridge ? 'browser demo' : 'local Python offline bridge'}</a></p>` : '<p>The bridge is available only on 127.0.0.1.</p>'}<p>Switching mode reloads the page. Bridge data is kept in Python process memory and resets on restart. The local model requires the server --ollama-model flag; requests may take time or fail.</p></section><section class="panel"><h2>Current identity</h2><p>Synthetic demo operator. There is no login or access to real customer records.</p><div class="facts"><div><dt>Message sending</dt><dd>A human sends through an external channel; this page only records the action afterward</dd></div><div><dt>Notifications</dt><dd>In app task demo; no system push notifications</dd></div><div><dt>Data storage</dt><dd>${usingBridge ? 'Local Python process memory; resets on server restart' : 'Current page memory; resets on reload'}</dd></div><div><dt>Real data</dt><dd>Do not enter</dd></div></div></section><section class="panel"><h2>Requirements for real integration</h2><p>Authentication, authorization, persistent events, real approvals, data retention and deletion, and mobile notifications are not implemented.</p></section>`);
}

async function loadStudents() {
  const request = ++state.requestId;
  state.loading = true; render();
  try { const result = await api.listStudents(); if (request !== state.requestId) return; state.students = result; state.error = ''; }
  catch (error) { if (request !== state.requestId) return; state.error = error.message; }
  finally { if (request === state.requestId) { state.loading = false; render(); } }
}

async function loadWorkspace(studentId) {
  const request = ++state.requestId;
  state.studentId = studentId; state.page = 'detail'; state.loading = true; render();
  try { const result = await api.getWorkspace(studentId); if (request !== state.requestId) return; state.workspace = result; state.error = ''; }
  catch (error) { if (request !== state.requestId) return; state.error = error.message; }
  finally { if (request === state.requestId) { state.loading = false; render(); } }
}

async function loadReminders() {
  const request = ++state.requestId;
  state.page = 'reminders'; state.loading = true; render();
  try { const result = await api.listReminders(); if (request !== state.requestId) return; state.reminders = result; state.error = ''; }
  catch (error) { if (request !== state.requestId) return; state.error = error.message; }
  finally { if (request === state.requestId) { state.loading = false; render(); } }
}

async function mutate(operation, success) {
  state.error = ''; state.notice = '';
  state.loading = true;
  state.busyText = localModel() ? 'The local model is running; this may take several minutes. Waiting for the Gate result…' : 'Processing synthetic demo action…';
  render();
  try {
    const result = await operation();
    state.notice = typeof success === 'function' ? success(result) : success;
    if (state.page === 'detail') state.workspace = await api.getWorkspace(state.studentId);
    else state.students = await api.listStudents();
  } catch (error) {
    state.notice = '';
    state.error = error.message;
    // The pipeline can persist context, decisions, or a rejected draft before
    // returning HANDOFF. Refresh the view even when the mutation fails.
    try {
      if (state.page === 'detail' && state.studentId) state.workspace = await api.getWorkspace(state.studentId);
      else state.students = await api.listStudents();
    } catch (refreshError) {
      state.error += `; workspace refresh failed: ${refreshError.message}`;
    }
  }
  finally { state.loading = false; state.busyText = ''; }
  render();
}

root.addEventListener('click', async event => {
  const target = event.target.closest('[data-action]');
  if (!target) return;
  const action = target.dataset.action;
  if (action === 'dismiss-error') { state.error = ''; render(); }
  else if (action === 'nav-students') { state.page = 'students'; state.notice = ''; await loadStudents(); }
  else if (action === 'nav-reminders') { state.notice = ''; await loadReminders(); }
  else if (action === 'nav-me') { ++state.requestId; state.page = 'me'; state.notice = ''; render(); }
  else if (action === 'open-student') { state.notice = ''; await loadWorkspace(target.dataset.id); }
  else if (action === 'toggle-add') { state.showAdd = !state.showAdd; render(); }
  else if (action === 'toggle-edit') { state.showEdit = !state.showEdit; render(); }
  else if (action === 'filter') { state.filter = target.dataset.value; render(); }
  else if (action === 'request-decision') await mutate(() => api.requestDecision(state.studentId), result => {
    if (result.pipeline_outcome?.status === 'APPROVAL_REQUIRED') return 'The model proposed a custom offer for internal review. A human must check and approve it; there is no customer draft yet.';
    if (result.pipeline_outcome?.status === 'STOPPED') return `This turn stopped (${result.pipeline_outcome.reason_codes?.join(', ') || 'stop condition'}); no customer draft was generated.`;
    return localModel() ? 'The local model draft passed the current Gate and awaits human review. It has not been sent.' : 'Synthetic demo decision generated; this is not a real Agent output.';
  });
  else if (action === 'approve-draft') await mutate(() => api.approveDraft(state.studentId), 'The draft was approved by a human and remains unsent.');
});

root.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form));
  if (form.id === 'add-student') {
    const created = await api.createStudent(studentCreatePayload(data, usingBridge)).catch(error => { state.error = error.message; render(); });
    if (created) { state.showAdd = false; state.notice = usingBridge ? 'Synthetic student saved in the local offline process; it resets on server restart.' : 'Synthetic student added to this page; it resets on reload.'; await loadWorkspace(created.student_id); }
  } else if (form.id === 'edit-student') {
    await mutate(() => api.updateStudent(state.studentId, studentUpdatePayload(data, state.workspace.revision, state.workspace.sales)), 'Record status updated.');
    if (!state.error) { state.showEdit = false; render(); }
  } else if (form.id === 'inbound-form') await mutate(() => api.recordInbound(state.studentId, { raw_text: data.raw_text, occurred_at: data.occurred_at ? new Date(data.occurred_at).toISOString() : undefined }), 'The exact student message was recorded separately; any older approved draft is now stale.');
  else if (form.id === 'internal-form') await mutate(() => api.discussInternal(state.studentId, { raw_text: data.raw_text }), 'The internal note was recorded and does not enter the student visible conversation.');
  else if (form.id === 'progress-label-form' && usingBridge) await mutate(() => api.labelProgressAssessment(state.studentId, {
    assessment_event_id: data.assessment_event_id, human_label: data.human_label, reason: data.reason,
  }), 'The human label was appended; the original prediction and student message remain unchanged.');
  else if (form.id === 'offer-form') await mutate(() => api.approveCustomOffer(state.studentId, usingBridge ? {
    offer_id: `demo-offer-${state.studentId}`, price: data.price,
    scope: String(data.scope || '').split(/[,，、]+/).map(item => item.trim()).filter(Boolean),
    exclusions: String(data.exclusions || '').split(/[,，、]+/).map(item => item.trim()).filter(Boolean),
    payment_terms: String(data.payment_terms || '').split(/[,，、]+/).map(item => item.trim()).filter(Boolean),
    valid_until: data.valid_until ? new Date(data.valid_until).toISOString() : null,
    confirmed_roles: ['PRODUCT', 'DELIVERY', 'PRICING'].filter(role => data[`role_${role}`] === 'true'),
    confirmed_approval: data.confirmed_approval === 'true',
  } : { price: data.price }), 'The synthetic custom package and price were approved by a human; a customer draft still needs generation and review.');
  else if (form.id === 'offer-review-form') {
    const action = event.submitter?.value;
    await mutate(() => api.reviewCustomOffer(state.studentId, {
      action, offer_id: `demo-offer-${state.studentId}`, comment: data.comment,
    }), action === 'REJECT' ? 'The synthetic proposal was rejected; there is no approved quote or sendable draft.' : 'The synthetic proposal was returned for changes; a new internal package is needed, and the old proposal cannot be sent.');
  }
  else if (form.id === 'review-form') await mutate(() => api.reviewDraft(state.studentId, { feedback_type: data.feedback_type, comment: data.comment }), usingBridge ? 'Revision feedback saved. Run the synthetic demo decision again for a revised draft.' : 'Revision feedback and a new version saved for the synthetic demo.');
  else if (form.id === 'sent-form') await mutate(() => api.recordActualSent(state.studentId, { actual_sent_text: data.actual_sent_text, sent_at: data.sent_at ? new Date(data.sent_at).toISOString() : undefined, confirmed_external_send: data.confirmed_external_send === 'true' }), 'The exact text sent by a human through an external channel was recorded. This page sent no message.');
});

if (usingBridge) {
  api.getMode().then(mode => { state.bridgeMode = mode; return loadStudents(); })
    .catch(error => { state.error = error.message; render(); });
} else loadStudents();
