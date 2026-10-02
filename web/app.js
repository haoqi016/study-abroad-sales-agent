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
const dateText = value => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : '未设置';
const stageLabel = { NEW: '新线索', CONSULTING: '正在咨询', QUALIFIED: '已确认需求', PRICE_OBJECTION: '价格异议', LIKELY_TO_PAY: '可能付款', WAITING_STUDENT: '等待学生', WAITING_DECISION_MAKER: '等待决策人', READY_TO_SIGN: '待签约', WON: '已签约', LOST: '未成交', DO_NOT_CONTACT: '禁止联系' };
const sourceLabel = { XIAOHONGSHU: '小红书', WECHAT: '微信', REFERRAL: '转介绍', OTHER: '其他' };
const sourceClass = { CUSTOMER_STATED: 'fact', HUMAN_CONFIRMED: 'fact', SYSTEM_VERIFIED: 'fact', AGENT_HYPOTHESIS: 'hypothesis' };
const sourceName = { CUSTOMER_STATED: '学生明确说过', HUMAN_CONFIRMED: '人工确认', SYSTEM_VERIFIED: '系统核实', AGENT_HYPOTHESIS: '待验证假设' };
const memoryLabel = { 'budget.total_ceiling': '机构服务费预算上限', 'intent.bargaining': '可能在议价', payer: '付款人', decision_maker: '决策人' };
const progressLabel = { COMMITMENT_SIGNAL: '明确表达签约意向（未核实成交）', OBJECTION_CLARIFIED: '澄清了顾虑', ENGAGED_WITH_NEW_INFORMATION: '继续讨论新信息', NEUTRAL: '中性回复', DEFERRED: '暂缓', EXIT: '明确退出', DO_NOT_CONTACT: '明确要求停止联系', UNCLEAR: '无法从原话判断是否推进' };

function shell(content) {
  root.innerHTML = `
    <header class="topbar"><div class="brand"><span class="brand-mark">S</span><div><strong>Sales 工作台</strong><small>${localModel() ? '本机 Ollama Agent 实验 · 仅合成数据' : usingBridge ? '本地 Python 合成脚本 · 非真实 Agent' : '浏览器内合成演示 · 非真实 Agent'}</small></div></div><span class="offline-badge">${localModel() ? 'LOCAL MODEL' : usingBridge ? 'DEMO/OFFLINE' : 'DEMO'}</span></header>
    <main id="main-content" class="main-content">${state.error ? `<div class="alert error" role="alert">${text(state.error)} <button type="button" data-action="dismiss-error" aria-label="关闭错误">×</button></div>` : ''}${state.notice ? `<div class="alert success" role="status">${text(state.notice)}</div>` : ''}${content}</main>
    <nav class="bottom-nav" aria-label="主导航"><button type="button" data-action="nav-students" class="${state.page === 'students' || state.page === 'detail' ? 'active' : ''}" ${state.page === 'students' || state.page === 'detail' ? 'aria-current="page"' : ''}><span aria-hidden="true">▦</span>学生</button><button type="button" data-action="nav-reminders" class="${state.page === 'reminders' ? 'active' : ''}" ${state.page === 'reminders' ? 'aria-current="page"' : ''}><span aria-hidden="true">◷</span>待办提醒</button><button type="button" data-action="nav-me" class="${state.page === 'me' ? 'active' : ''}" ${state.page === 'me' ? 'aria-current="page"' : ''}><span aria-hidden="true">◎</span>我的</button></nav>`;
}

function render() {
  if (state.loading) return shell(`<section class="loading" role="status"><span class="spinner" aria-hidden="true"></span>${text(state.busyText || '正在读取演示数据…')}</section>`);
  if (state.page === 'students') return renderStudents();
  if (state.page === 'detail') return renderDetail();
  if (state.page === 'reminders') return renderReminders();
  return renderMe();
}

function renderStudents() {
  const students = state.students.filter(student => state.filter === 'ALL' || (state.filter === 'PRICE_OBJECTION' ? student.sales.stage === 'PRICE_OBJECTION' : state.filter === 'REVIEW_DUE' ? student.draft_status === 'PENDING_REVIEW' || student.has_pending_offer : state.filter === 'DO_NOT_CONTACT' ? student.sales.contact_permission === 'DO_NOT_CONTACT' : student.sales.stage === state.filter));
  shell(`<section class="page-heading"><div><p class="eyebrow">客户工作台</p><h1>学生</h1><p>先看状态，再进入一位学生处理当前动作。</p></div><button type="button" class="button primary" data-action="toggle-add">＋ 新增学生</button></section>
    <div class="demo-warning">仅供合成数据演示。不要录入真实学生信息；${localModel() ? '本机模型生成可能耗时数分钟，也可能被 V2 Gate 拒绝或调用失败；失败时不会生成脚本草稿。' : usingBridge ? '服务重启后会清空数据。' : '刷新页面会清空新增内容。'}</div>
    ${state.showAdd ? addStudentForm() : ''}
    <div class="filter-row" role="group" aria-label="筛选学生">${[['ALL','全部'], ['CONSULTING','正在咨询'], ['PRICE_OBJECTION','价格异议'], ['LIKELY_TO_PAY','可能付款'], ['REVIEW_DUE','待审核'], ['WAITING_STUDENT','等待学生'], ['DO_NOT_CONTACT','已停止联系']].map(([value,label]) => `<button type="button" data-action="filter" data-value="${value}" class="chip ${state.filter === value ? 'selected' : ''}" aria-pressed="${state.filter === value}">${label}</button>`).join('')}</div>
    ${students.length ? `<div class="student-list">${students.map(studentCard).join('')}</div>` : `<div class="empty-state"><span aria-hidden="true">◎</span><h2>这个筛选下还没有学生</h2><p>换个筛选条件，或新建一份合成学生档案。</p></div>`}`);
}

function studentCard(student) {
  return `<button type="button" data-action="open-student" data-id="${text(student.student_id)}" class="student-card"><span class="avatar" aria-hidden="true">${text(student.display_name.slice(0,1))}</span><span class="student-card-body"><span class="card-title"><strong>${text(student.display_name)}</strong><span class="stage">${text(stageLabel[student.sales.stage] || student.sales.stage)}</span></span><span class="muted">${text(sourceLabel[student.source.channel] || student.source.channel)} · ${text([...student.targets.countries, ...student.targets.universities, ...student.targets.programs_or_majors].join(' / ') || '目标方向未填')}</span><span class="card-objection">${text(student.sales.current_objection || '当前障碍未记录')}</span><span class="card-foot">${student.has_pending_offer ? '定制方案待审批' : student.draft_status === 'PENDING_REVIEW' ? '草稿待审核' : `下次动作时间：${dateText(student.sales.next_action_at)}`}</span></span><span class="chevron" aria-hidden="true">›</span></button>`;
}

function addStudentForm() {
  return `<form id="add-student" class="panel form-panel"><div class="section-heading"><h2>新增学生（演示）</h2><button type="button" class="text-button" data-action="toggle-add">取消</button></div><p class="muted">仅录入合成资料，备注名须包含“合成”或“DEMO”。下次动作时间只是内部建议，不代表学生同意联系；本页不会自动发送消息或推送。</p>${usingBridge ? '<p class="helper">离线桥接暂不支持平台 ID；这里只记录合成来源渠道。</p>' : ''}<div class="form-grid"><label>备注名 <span class="required">必填</span><input name="display_name" required maxlength="80" placeholder="${usingBridge ? '例如：DEMO 学生或合成同学' : '例如：X 同学（合成）'}"></label><label>来源<select name="channel"><option value="XIAOHONGSHU">小红书</option><option value="WECHAT">微信</option><option value="REFERRAL">转介绍</option><option value="OTHER">其他</option></select></label>${usingBridge ? '' : '<label>平台 ID（仅浏览器演示）<input name="platform_handle" maxlength="80" placeholder="合成演示 ID"></label>'}<label>本科院校<input name="university" maxlength="120"></label><label>本科专业<input name="major" maxlength="120"></label><label>均分<input name="score" inputmode="decimal" maxlength="20"></label><label>年级<select name="year"><option value="UNKNOWN">未知</option><option value="YEAR_1">大一</option><option value="YEAR_2">大二</option><option value="YEAR_3">大三</option><option value="YEAR_4">大四</option><option value="GRADUATED">已毕业</option></select></label><label>目标地区<select name="country"><option value="">未确定</option><option value="SG">新加坡</option><option value="HK">香港</option><option value="UK">英国</option><option value="AU">澳大利亚</option></select></label><label>目标院校<input name="target_universities" maxlength="240" placeholder="可用逗号分隔多所院校"></label><label>目标专业／项目<input name="target_programs" maxlength="240" placeholder="可用逗号分隔多个项目"></label><label>销售阶段<select name="stage">${Object.entries(stageLabel).map(([value,label]) => `<option value="${value}">${label}</option>`).join('')}</select></label><label>当前障碍<input name="current_objection" maxlength="240"></label><label>决策人<input name="decision_maker" maxlength="120" placeholder="例如：本人／家长（合成）"></label><label>联系许可<select name="contact_permission"><option value="UNKNOWN">未知</option><option value="ALLOWED">允许联系</option><option value="LIMITED">有限联系</option><option value="DO_NOT_CONTACT">禁止联系</option></select></label><label>下次动作时间<input name="next_action_at" type="datetime-local"></label><label>下次动作原因<input name="next_action_reason" maxlength="240" placeholder="例如：演示回访需求"></label></div><button type="submit" class="button primary wide">保存演示档案</button></form>`;
}

function renderDetail() {
  const w = state.workspace;
  if (!w) return shell('<div class="empty-state"><h1>尚未选择学生</h1><button class="button" data-action="nav-students">返回学生列表</button></div>');
  const blocked = w.sales.contact_permission === 'DO_NOT_CONTACT';
  const sendAllowed = w.sales.contact_permission === 'ALLOWED';
  const latestDraft = w.drafts.at(-1);
  const offerPending = usingBridge ? w.has_pending_offer : w.offer?.state === 'CUSTOM_OFFER_PROPOSAL';
  const offerRejected = usingBridge && ['REQUEST_CHANGES', 'REJECT'].includes(w.offer?.review_action);
  shell(`<div class="detail-back"><button type="button" class="text-button" data-action="nav-students">← 全部学生</button><span class="revision">档案版本 ${w.revision}</span></div>
    <section class="profile-header"><div class="avatar large" aria-hidden="true">${text(w.display_name.slice(0,1))}</div><div><p class="eyebrow">${text(sourceLabel[w.source.channel] || w.source.channel)} · ${usingBridge ? '平台 ID 暂不支持' : text(w.source.platform_handle)}</p><h1>${text(w.display_name)}</h1><span class="stage">${text(stageLabel[w.sales.stage] || w.sales.stage)}</span></div><button type="button" class="button subtle edit-profile" data-action="toggle-edit">编辑</button></section>
    ${blocked ? '<div class="alert blocked" role="alert"><strong>已停止联系</strong>：不能生成销售草稿、批准草稿或产生发送提醒。内部记录仍可查看。</div>' : ''}
    ${!blocked && !sendAllowed ? '<div class="alert blocked" role="alert"><strong>联系许可未明确允许</strong>：可整理内部信息，但不能批准或记录销售发送。</div>' : ''}
    ${state.showEdit ? editStudentForm(w) : ''}
    <section class="panel status-panel"><div class="section-heading"><h2>现在的状态</h2><span class="section-kicker">决策前先核对</span></div><dl class="facts"><div><dt>当前障碍</dt><dd>${text(w.sales.current_objection || '尚未记录')}</dd></div><div><dt>目标地区</dt><dd>${text(w.targets.countries.join('、') || '未知')}</dd></div><div><dt>目标院校</dt><dd>${text(w.targets.universities.join('、') || '未确定')}</dd></div><div><dt>目标专业／项目</dt><dd>${text(w.targets.programs_or_majors.join('、') || '未确定')}</dd></div><div><dt>本科／均分</dt><dd>${text(w.education.undergraduate_university_raw || '未填')} · ${text(w.education.score_raw || '未填')}</dd></div><div><dt>决策人</dt><dd>${text(w.sales.decision_maker || '未知')}</dd></div><div><dt>联系许可</dt><dd>${blocked ? '禁止联系' : w.sales.contact_permission === 'ALLOWED' ? '允许联系' : w.sales.contact_permission === 'LIMITED' ? '有限联系' : '尚未确认'}</dd></div><div><dt>下次动作时间</dt><dd>${dateText(w.sales.next_action_at)}</dd></div><div><dt>下次动作原因</dt><dd>${text(w.sales.next_action_reason || '未设置')}</dd></div></dl></section>
    ${renderMemory(w)}
    ${renderTimeline(w)}
    ${renderProgress(w)}
    <section class="panel inputs-panel"><div class="section-heading"><h2>继续处理</h2><span class="section-kicker">两种输入，不能混用</span></div><div class="dual-input"><form id="inbound-form" class="input-card student-input"><h3>录入学生消息</h3><p>仅粘贴学生实际说的原话。原文保存后不由 Agent 改写。</p><label>学生原话<textarea name="raw_text" rows="3" required placeholder="粘贴学生实际发来的内容"></textarea></label><label>实际收到时间<input name="occurred_at" type="datetime-local"></label><button type="submit" class="button primary">记录学生原话</button></form><form id="internal-form" class="input-card internal-input"><h3>和 Agent 讨论</h3><p>给 Agent 的内部意见，不会作为学生消息或客户事实。</p><label>内部意见<textarea name="raw_text" rows="3" required placeholder="例如：这句太生硬，先解释价值"></textarea></label><button type="submit" class="button">记录内部意见</button></form></div></section>
    ${renderDecision(w, blocked, offerPending)}
    ${renderOffer(w, blocked)}
    ${renderDrafts(w, blocked, offerPending || offerRejected, latestDraft, sendAllowed && !offerRejected)}
    <div class="bottom-space"></div>`);
}

function editStudentForm(w) {
  return `<form id="edit-student" class="panel form-panel"><h2>编辑当前状态</h2><p class="muted">销售判断与学生原话分开记录；下次动作只是内部建议，不代表学生同意联系，也不会自动发送或推送。</p><div class="form-grid"><label>备注名<input name="display_name" required maxlength="80" value="${fieldValue(w.display_name)}"></label><label>目标地区<input name="target_countries" maxlength="240" value="${fieldValue(w.targets.countries.join('，'))}" placeholder="可用逗号分隔"></label><label>目标院校<input name="target_universities" maxlength="240" value="${fieldValue(w.targets.universities.join('，'))}" placeholder="可用逗号分隔"></label><label>目标专业／项目<input name="target_programs" maxlength="240" value="${fieldValue(w.targets.programs_or_majors.join('，'))}" placeholder="可用逗号分隔"></label><label>销售阶段<select name="stage">${Object.entries(stageLabel).map(([value,label]) => `<option value="${value}" ${w.sales.stage === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label><label>当前障碍<input name="current_objection" maxlength="240" value="${fieldValue(w.sales.current_objection)}"></label><label>决策人<input name="decision_maker" maxlength="120" value="${fieldValue(w.sales.decision_maker)}"></label><label>联系许可<select name="contact_permission"><option value="UNKNOWN" ${w.sales.contact_permission === 'UNKNOWN' ? 'selected' : ''}>未知</option><option value="ALLOWED" ${w.sales.contact_permission === 'ALLOWED' ? 'selected' : ''}>允许联系</option><option value="LIMITED" ${w.sales.contact_permission === 'LIMITED' ? 'selected' : ''}>有限联系</option><option value="DO_NOT_CONTACT" ${w.sales.contact_permission === 'DO_NOT_CONTACT' ? 'selected' : ''}>禁止联系</option></select></label><label>下次动作时间<input name="next_action_at" type="datetime-local" value="${localActionTime(w.sales.next_action_at)}"></label><label>下次动作原因<input name="next_action_reason" maxlength="240" value="${fieldValue(w.sales.next_action_reason)}"></label></div><button type="submit" class="button primary">保存状态</button></form>`;
}

function renderMemory(w) {
  return `<section class="panel"><div class="section-heading"><h2>学生长期记忆</h2><span class="section-kicker">有来源的结构化信息</span></div>${w.memory.length ? `<div class="memory-list">${w.memory.map(item => `<div class="memory-item ${sourceClass[item.epistemic_status] || ''}"><div><strong>${text(memoryLabel[item.key] || item.key)}</strong><span class="source-pill">${text(sourceName[item.epistemic_status] || item.epistemic_status)}</span></div><p>${text(item.value)}</p><small>依据：${text(item.evidence_span || item.source_event_ids.join('、'))} · ${text(item.source_event_ids.join('、'))}</small></div>`).join('')}</div>` : '<p class="muted">还没有经过来源区分的长期记忆。不会把模型猜测当成事实。</p>'}</section>`;
}

function renderTimeline(w) {
  const visible = customerVisibleHistory(w);
  const internal = w.events.filter(event => !['INBOUND_RECEIVED', 'HUMAN_SENT'].includes(event.event_type));
  return `<section class="panel timeline-panel"><div class="section-heading"><h2>学生实际对话</h2><span class="section-kicker">只含学生原话与真实已发送内容</span></div>${visible.length ? `<ol class="timeline">${visible.map(event => `<li class="bubble ${event.event_type === 'INBOUND_RECEIVED' ? 'student' : 'sent'}"><span class="bubble-type">${event.event_type === 'INBOUND_RECEIVED' ? '学生原话' : '人工实际发送'}</span><p>${text(event.raw_content)}</p><small>${dateText(event.occurred_at)}</small>${event.diff_from_approved ? `<small class="diff-note">${text(event.diff_from_approved)}；批准草稿和实际发送内容分别保存</small>` : ''}</li>`).join('')}</ol>` : '<p class="muted">还没有学生可见消息。未发送草稿不会出现在这里。</p>'}<details class="internal-log"><summary>查看内部事件（${internal.length}）</summary>${internal.length ? `<ul>${internal.map(event => `<li><strong>${text(event.event_type)}</strong> · ${text(event.raw_content || '状态变更')}<small>${dateText(event.occurred_at)}</small></li>`).join('')}</ul>` : '<p>暂无内部事件</p>'}</details></section>`;
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
  return `<section class="panel progress-panel"><div class="section-heading"><h2>学生回复后的判断</h2><span class="section-kicker">内部人工标注 · 仅合成数据</span></div>
    <div class="progress-source"><strong>学生原话（不可改写）</strong><p>${text(inbound?.payload?.raw_text || inbound?.raw_content || '原话事件未找到，请核对记录')}</p><small>来源事件：${text(assessment.inbound_event_id)}</small></div>
    <dl class="facts"><div><dt>原始模型预测</dt><dd>${signal(assessment.predicted_signal)}</dd></div><div><dt>预测依据片段</dt><dd>${text(assessment.supporting_spans?.join('、') || '没有足够的明确原话')}</dd></div><div><dt>预测置信度</dt><dd>${assessment.confidence == null ? '未提供' : text(assessment.confidence)}</dd></div><div><dt>最新有效人工标签</dt><dd>${effective?.human_label ? signal(effective.human_label) : '尚未标注'}</dd></div><div><dt>最新标注理由</dt><dd>${text(latestLabel?.reason || '尚未标注')}</dd></div></dl>
    <p class="muted">人工标签单独追加，原始预测与学生原话保持可见。判断不等于签约、付款或策略有效。</p>
    <form id="progress-label-form" class="progress-label-form"><input type="hidden" name="assessment_event_id" value="${fieldValue(assessmentEvent.event_id)}"><label>人工判断标签<select name="human_label" required><option value="">请选择</option>${Object.entries(progressLabel).map(([value, label]) => `<option value="${value}">${text(label)}</option>`).join('')}</select></label><label>判断依据 <span class="required">必填</span><textarea name="reason" rows="3" required maxlength="1000" placeholder="根据学生原话说明为何这样标注"></textarea></label><button type="submit" class="button primary">保存人工标签</button></form>
    <details class="progress-history"><summary>查看标注历史（${labels.length}）</summary>${labels.length ? `<ol>${labels.map(event => `<li><strong>${signal(event.payload.human_label)}</strong><p>${text(event.payload.reason)}</p><small>${dateText(event.occurred_at)} · ${text(event.payload.labeled_by)}</small></li>`).join('')}</ol>` : '<p class="muted">还没有人工标注。</p>'}</details>
    ${reflection ? `<details><summary>查看本客户的待验证复盘</summary><p>${text(reflection.observed_outcome)}</p><p>可能解释：${text(reflection.possible_explanation)}</p><small>${text(reflection.learning_status)} · 不自动写入销售知识库</small></details>` : ''}</section>`;
}

function renderDecision(w, blocked, offerPending) {
  const d = w.decision;
  const offerStatus = w.offer?.review_action === 'REJECT' ? '已拒绝' :
    w.offer?.review_action === 'REQUEST_CHANGES' ? '已打回修改' : d?.offer?.state || 'NONE';
  return `<section class="panel decision-panel"><div class="section-heading"><h2>内部决策</h2><span class="section-kicker">不对学生展示</span></div>${d ? `<div class="objective"><span class="eyebrow">当前小目标</span><strong>${text(d.current_objective?.goal)}</strong><p>${text(d.current_objective?.why_now)}</p><small>可观察信号：${text(d.current_objective?.success_signals?.join('、'))}</small></div><dl class="facts"><div><dt>本轮动作</dt><dd>${text(d.action_plan?.selected_action)}</dd></div><div><dt>基础回复</dt><dd>${text(d.content_contract?.semantic_draft)}</dd></div><div><dt>Offer 状态</dt><dd>${text(offerStatus)}</dd></div>${usingBridge && d.input_event_ids?.length ? `<div><dt>输入来源事件</dt><dd>${text(d.input_event_ids.join('、'))}</dd></div>` : ''}</dl>${d.demo_only ? `<p class="demo-note">${localModel() ? '本机模型实验输出；仍需 V2 Gate 和人工审核，不代表 Eval 通过。' : '这里是固定规则模拟输出，不是模型判断或通过 Eval 的决策。'}</p>` : ''}` : '<div class="empty-inline">尚无本轮决策；先录入学生原话，再请求演示决策。</div>'}${blocked ? '<p class="blocked-note">禁止联系：不提供销售决策操作。</p>' : `<button type="button" class="button" data-action="request-decision" ${offerPending || ['DRAFTED', 'APPROVED', 'PENDING_REVIEW'].includes(w.draft_status) ? 'disabled' : ''}>${localModel() ? '运行本机 Ollama Agent' : '运行合成演示决策'}</button>`}${localModel() ? '<p class="muted">本机推理可能耗时数分钟；失败或未过 Gate 不会替换成脚本回复。</p>' : ''}${offerPending ? '<p class="blocked-note">定制方案待审批，不能生成可发送草稿。</p>' : ''}</section>`;
}

function renderOffer(w, blocked) {
  if (!w.offer) return '';
  if (w.offer.state === 'STANDARD_OFFER') {
    const quote = w.offer.quote;
    return `<section class="panel offer-panel"><div class="section-heading"><h2>标准 Offer</h2><span class="section-kicker">仍需审核对客草稿</span></div><dl class="facts"><div><dt>产品</dt><dd>${text(quote?.product_id)}</dd></div><div><dt>标准报价</dt><dd>${quote?.price == null ? '本轮未提供报价' : `${text(quote.price)} 元`}</dd></div><div><dt>定价政策</dt><dd>${text(quote?.policy_version)}</dd></div></dl><p class="muted">仅展示本轮决策采用的标准报价；不代表草稿已批准或已发送。</p></section>`;
  }
  if (!['CUSTOM_OFFER_PROPOSAL', 'APPROVED_CUSTOM_OFFER'].includes(w.offer.state)) {
    return `<section class="panel offer-panel"><h2>Offer 状态待核对</h2><p>${text(w.offer.state)}</p></section>`;
  }
  if (usingBridge && ['REQUEST_CHANGES', 'REJECT'].includes(w.offer.review_action)) {
    const rejected = w.offer.review_action === 'REJECT';
    const proposal = w.offer.proposal || {};
    const review = w.events.find(event => event.event_id === w.offer.review_event_id);
    return `<section class="panel offer-panel reviewed-offer"><div class="section-heading"><h2>定制 Offer · ${rejected ? '已拒绝' : '已打回修改'}</h2><span class="section-kicker">内部记录 · 不可对客发送</span></div><p class="approval-warning">${rejected ? '此提案已被拒绝。' : '此提案已打回修改。'}没有获批的报价或可发送草稿。</p><dl class="facts"><div><dt>客户依据</dt><dd>${text(proposal.customer_need_evidence?.join('、'))}</dd></div><div><dt>原拟议范围</dt><dd>${text(proposal.proposed_scope?.join('、'))}</dd></div><div><dt>明确排除</dt><dd>${text(proposal.explicit_exclusions?.join('、'))}</dd></div><div><dt>原提案价格</dt><dd>${proposal.proposed_price == null ? '未填写' : `${text(proposal.proposed_price)} 元（内部，未批准）`}</dd></div><div><dt>付款条件</dt><dd>${text(proposal.payment_terms?.join('、'))}</dd></div><div><dt>交付要求／风险</dt><dd>${text([...(proposal.delivery_requirements || []), ...(proposal.risks || [])].join('、'))}</dd></div><div><dt>审核理由</dt><dd>${text(review?.payload?.comment)}</dd></div></dl></section>`;
  }
  const pending = usingBridge ? w.has_pending_offer : w.offer.state === 'CUSTOM_OFFER_PROPOSAL';
  const proposal = w.offer.proposal;
  const approval = w.events.find(event => event.event_id === w.offer.approval_event_id)?.payload;
  const scope = proposal?.proposed_scope || [];
  const price = approval?.approved_offer?.approved_price ?? w.offer.price;
  const approvers = approval?.approved_offer?.approved_by || w.offer.approved_by || [];
  const approverLabel = Array.isArray(approvers) ? approvers.map(item => `${item.role}：${item.actor}`).join('、') : approvers;
  const bridgeApprovalFields = usingBridge ? `<label>批准的服务范围（用逗号分隔）<input name="scope" required value="${fieldValue(scope.join('，'))}"></label><label>明确不包含的服务（用逗号分隔，可留空）<input name="exclusions" value="${fieldValue((proposal?.explicit_exclusions || []).join('，'))}"></label><label>批准的付款条件（用逗号分隔）<input name="payment_terms" required value="${fieldValue((proposal?.payment_terms || []).join('，'))}"></label><label>有效截止时间（可留空）<input name="valid_until" type="datetime-local"></label><label><input name="role_PRODUCT" type="checkbox" value="true" required> 我确认产品范围审批（合成演练）</label><label><input name="role_DELIVERY" type="checkbox" value="true" required> 我确认交付能力审批（合成演练）</label><label><input name="role_PRICING" type="checkbox" value="true" required> 我确认定价审批（合成演练）</label><label><input name="confirmed_approval" type="checkbox" value="true" required> 我已核对上述全部条款（合成演练）</label>` : '';
  return `<section class="panel offer-panel ${pending ? 'needs-approval' : 'approved-offer'}"><div class="section-heading"><h2>定制 Offer · ${pending ? '内部提案' : '已由人工批准'}</h2><span class="section-kicker">${pending ? '不可发送' : '仍需审核话术草稿'}</span></div><p>${text(w.offer.summary || scope.join('、'))}</p>${proposal ? `<dl class="facts"><div><dt>客户依据</dt><dd>${text(proposal.customer_need_evidence?.join('、'))}</dd></div><div><dt>拟议范围</dt><dd>${text(scope.join('、'))}</dd></div><div><dt>明确排除</dt><dd>${text(proposal.explicit_exclusions?.join('、'))}</dd></div><div><dt>原提案价格</dt><dd>${text(proposal.proposed_price)} 元（内部）</dd></div><div><dt>付款条件</dt><dd>${text(proposal.payment_terms?.join('、'))}</dd></div><div><dt>交付要求／风险</dt><dd>${text([...(proposal.delivery_requirements || []), ...(proposal.risks || [])].join('、'))}</dd></div><div><dt>成本／毛利</dt><dd>${text(proposal.estimated_delivery_cost?.status)}／${text(proposal.estimated_margin?.status)}</dd></div></dl>` : ''}<dl class="facts"><div><dt>已批准价格</dt><dd>${price == null ? '未批准，不能报价' : `${text(price)} 元`}</dd></div><div><dt>审批人</dt><dd>${text(approverLabel)}</dd></div></dl>${pending && !blocked ? `<div class="approval-warning">这只是内部提案。负责人需核对产品、交付与定价；批准前不能对客承诺。</div><form id="offer-form"><label>负责人批准的价格（元） <span class="required">必填</span><input name="price" type="number" min="1" step="1" required inputmode="numeric"></label>${bridgeApprovalFields}<button type="submit" class="button primary">演示：人工批准组合与价格</button></form>${usingBridge ? `<form id="offer-review-form" class="offer-review-form"><h3>内部审核处理</h3><label>打回或拒绝理由 <span class="required">必填</span><textarea name="comment" rows="3" required placeholder="说明需要修改的条款或拒绝原因"></textarea></label><div class="button-row"><button type="submit" name="review_action" value="REQUEST_CHANGES" class="button">打回修改</button><button type="submit" name="review_action" value="REJECT" class="button danger-button">拒绝提案</button></div></form>` : ''}<small class="helper">仅在合成演示中变更状态；实际后端必须核验审批角色、权限及定价。</small>` : ''}</section>`;
}

function renderDrafts(w, blocked, offerPending, latestDraft, sendAllowed) {
  return `<section class="panel draft-panel"><div class="section-heading"><h2>客户回复草稿</h2><span class="section-kicker">每一版都保留</span></div>${w.drafts.length ? `<div class="draft-stack">${w.drafts.map((draft, index) => `<article class="draft-version ${draft === latestDraft ? 'latest' : ''}"><div class="draft-head"><strong>第 ${index + 1} 份草稿</strong><span class="status-pill ${draft.status.toLowerCase()}">${draftStatus(draft.status)}</span></div><div class="message-preview">${draft.messages.map(message => message.type === 'text' ? `<p>${text(message.content)}</p>` : `<small>贴图建议：${text(message.intent)}</small>`).join('')}</div>${draft.feedback.length ? `<small>关联打回意见：${text(draft.feedback.join('、'))}</small>` : ''}</article>`).join('')}</div>` : `<div class="empty-inline">${offerPending ? '定制方案未获批，不能生成可审批的客户草稿。' : '还没有对客草稿。'}</div>`}
    ${latestDraft?.status === 'PENDING_REVIEW' && !offerPending && !blocked ? `<form id="review-form" class="review-form"><h3>人工审核这一版</h3><label>打回类别<select name="feedback_type"><option value="NATURALNESS">语气／自然度</option><option value="STRATEGY_ERROR">策略错误</option><option value="FACT_ERROR">事实错误</option><option value="POLICY_ERROR">权限／政策错误</option></select></label><label>修改意见<textarea name="comment" rows="2" placeholder="打回时填写：哪里不对、希望怎么改"></textarea></label><div class="button-row"><button type="submit" class="button">${usingBridge ? '打回草稿（随后重新生成）' : '打回并生成演示修订'}</button><button type="button" class="button primary" data-action="approve-draft" ${sendAllowed ? '' : 'disabled'}>人工批准草稿</button></div></form>` : ''}
    ${latestDraft?.status === 'APPROVED' && sendAllowed ? `<div class="approved-actions"><p class="approved-callout">草稿已批准，但<strong>尚未发送</strong>。请先在外部渠道人工发送，再记录实际发出的原文。</p><form id="sent-form"><label>实际发送原文 <span class="required">必填</span><textarea name="actual_sent_text" rows="4" required>${text(latestDraft.text)}</textarea></label><label>实际发送时间<input name="sent_at" type="datetime-local"></label>${usingBridge ? '<label><input name="confirmed_external_send" type="checkbox" value="true" required> 我确认已在外部渠道人工发送（合成演练）</label>' : ''}<button type="submit" class="button primary">仅标记“人工已在外部渠道发送”</button></form></div>` : ''}
    ${blocked ? '<p class="blocked-note">禁止联系：不能批准或记录新的销售发送。</p>' : ''}</section>`;
}

function draftStatus(status) { return { PENDING_REVIEW: '待人工审核', CHANGES_REQUESTED: '已打回', APPROVED: '已批准 · 未发送', SENT: '已记录实际发送', STALE: '有新学生消息 · 已失效' }[status] || status; }

function renderReminders() {
  shell(`<section class="page-heading"><div><p class="eyebrow">站内待办</p><h1>待办提醒</h1><p>手机推送尚未实现。这里仅根据合成演示状态显示待办。</p></div></section><div class="demo-warning">未批准草稿只提醒审核；有学生新回复会使旧批准草稿失效；禁止联系者不会出现发送提醒。</div>${state.reminders.length ? `<div class="reminder-list">${state.reminders.map(item => `<button type="button" class="reminder-card" data-action="open-student" data-id="${text(item.student_id)}"><span class="reminder-type">${text(({ SEND_DUE: '待人工发送', REVIEW_DUE: '待审核', OFFER_REVIEW: '方案待审批', NEW_INBOUND: '新消息' })[item.type] || item.type)}</span><strong>${text(item.display_name)}</strong><span>${text(item.label)}</span><span class="chevron" aria-hidden="true">›</span></button>`).join('')}</div>` : '<div class="empty-state"><span aria-hidden="true">✓</span><h2>目前没有站内待办</h2><p>这里不会自动给学生发送消息。</p></div>'}`);
}

function renderMe() {
  shell(`<section class="page-heading"><div><p class="eyebrow">演示设置</p><h1>我的</h1></div></section><section class="panel"><h2>数据模式</h2><p>当前：${localModel() ? '本机 Ollama Agent 实验；合成学生经 V2 决策、话术与 Gate' : usingBridge ? '本地 Python 离线桥接；固定合成脚本经过 V2 Gate' : '浏览器内 DemoAdapter 固定规则'}</p>${location.hostname === '127.0.0.1' ? `<p><a class="button" href="/${usingBridge ? '' : '?bridge=offline'}">切换为${usingBridge ? '浏览器内演示' : '本地 Python 离线桥接'}</a></p>` : '<p>桥接仅在 127.0.0.1 可选。</p>'}<p>切换模式会重新载入页面。桥接数据在 Python 进程内存中，服务重启即清空。本机模型模式需用服务端 --ollama-model 显式启动；请求可能耗时或失败。</p></section><section class="panel"><h2>当前身份</h2><p>合成演示操作员。没有登录或真实客户访问权限。</p><div class="facts"><div><dt>消息发送</dt><dd>仅人工在外部渠道完成；本页只作事后记录</dd></div><div><dt>通知</dt><dd>站内待办演示；没有系统推送</dd></div><div><dt>数据保存</dt><dd>${usingBridge ? '本地 Python 进程内存；服务重启即重置' : '当前页面内存；刷新即重置'}</dd></div><div><dt>真实数据</dt><dd>请勿输入</dd></div></div></section><section class="panel"><h2>接入前缺什么</h2><p>身份和授权、事件持久化、真实审批、数据保留与删除，以及移动通知均未接入。</p></section>`);
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
  state.busyText = localModel() ? '本机模型正在运行，可能需要数分钟；请等待 Gate 结果…' : '正在处理合成演示操作…';
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
      state.error += `；工作台刷新失败：${refreshError.message}`;
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
    if (result.pipeline_outcome?.status === 'APPROVAL_REQUIRED') return '模型提出定制 Offer 内部提案；须人工核对和审批，目前没有对客草稿。';
    if (result.pipeline_outcome?.status === 'STOPPED') return `本轮已停止（${result.pipeline_outcome.reason_codes?.join('、') || '停止条件'}）；没有生成对客草稿。`;
    return localModel() ? '本机模型草稿已通过当前 Gate，等待人工审核；尚未发送。' : '已生成合成演示决策；这不是实际 Agent 输出。';
  });
  else if (action === 'approve-draft') await mutate(() => api.approveDraft(state.studentId), '草稿已人工批准，仍未发送。');
});

root.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form));
  if (form.id === 'add-student') {
    const created = await api.createStudent(studentCreatePayload(data, usingBridge)).catch(error => { state.error = error.message; render(); });
    if (created) { state.showAdd = false; state.notice = usingBridge ? '合成学生档案已写入本地离线进程；服务重启后清空。' : '合成学生档案已加入当前页面；刷新后会清空。'; await loadWorkspace(created.student_id); }
  } else if (form.id === 'edit-student') {
    await mutate(() => api.updateStudent(state.studentId, studentUpdatePayload(data, state.workspace.revision, state.workspace.sales)), '档案状态已更新。');
    if (!state.error) { state.showEdit = false; render(); }
  } else if (form.id === 'inbound-form') await mutate(() => api.recordInbound(state.studentId, { raw_text: data.raw_text, occurred_at: data.occurred_at ? new Date(data.occurred_at).toISOString() : undefined }), '学生原话已单独记录；旧批准草稿如有则已失效。');
  else if (form.id === 'internal-form') await mutate(() => api.discussInternal(state.studentId, { raw_text: data.raw_text }), '内部意见已记录，不进入学生可见对话。');
  else if (form.id === 'progress-label-form' && usingBridge) await mutate(() => api.labelProgressAssessment(state.studentId, {
    assessment_event_id: data.assessment_event_id, human_label: data.human_label, reason: data.reason,
  }), '人工标签已追加；原始预测与学生原话未改写。');
  else if (form.id === 'offer-form') await mutate(() => api.approveCustomOffer(state.studentId, usingBridge ? {
    offer_id: `demo-offer-${state.studentId}`, price: data.price,
    scope: String(data.scope || '').split(/[,，、]+/).map(item => item.trim()).filter(Boolean),
    exclusions: String(data.exclusions || '').split(/[,，、]+/).map(item => item.trim()).filter(Boolean),
    payment_terms: String(data.payment_terms || '').split(/[,，、]+/).map(item => item.trim()).filter(Boolean),
    valid_until: data.valid_until ? new Date(data.valid_until).toISOString() : null,
    confirmed_roles: ['PRODUCT', 'DELIVERY', 'PRICING'].filter(role => data[`role_${role}`] === 'true'),
    confirmed_approval: data.confirmed_approval === 'true',
  } : { price: data.price }), '合成演示定制组合与价格已人工批准；仍需生成和审核对客草稿。');
  else if (form.id === 'offer-review-form') {
    const action = event.submitter?.value;
    await mutate(() => api.reviewCustomOffer(state.studentId, {
      action, offer_id: `demo-offer-${state.studentId}`, comment: data.comment,
    }), action === 'REJECT' ? '合成提案已被拒绝；没有批准报价或可发送草稿。' : '合成提案已打回修改；需产生新的内部方案，原提案不可发送。');
  }
  else if (form.id === 'review-form') await mutate(() => api.reviewDraft(state.studentId, { feedback_type: data.feedback_type, comment: data.comment }), usingBridge ? '已保留打回意见；请重新运行合成演示决策以生成修订版。' : '已保留打回意见和新版本；修订仅供合成演示。');
  else if (form.id === 'sent-form') await mutate(() => api.recordActualSent(state.studentId, { actual_sent_text: data.actual_sent_text, sent_at: data.sent_at ? new Date(data.sent_at).toISOString() : undefined, confirmed_external_send: data.confirmed_external_send === 'true' }), '已记录人工在外部渠道实际发送的原文；本页没有发送消息。');
});

if (usingBridge) {
  api.getMode().then(mode => { state.bridgeMode = mode; return loadStudents(); })
    .catch(error => { state.error = error.message; render(); });
} else loadStudents();
