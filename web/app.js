const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const shapes = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  settings: '<path d="m9 3-.8 2.4-2.5.5-1.5 2.6 1.5 2-.1 2.9-1.3 2 1.5 2.6 2.4.5L9 21h6l.8-2.5 2.4-.5 1.5-2.6-1.3-2v-2.9l1.5-2-1.5-2.6-2.5-.5L15 3Z"/><circle cx="12" cy="12" r="3"/>',
  shield: '<path d="M12 3 4 6v6c0 4 8 9 8 9s8-5 8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  chat: '<path d="M4 4h16v12H9l-5 4Z"/>',
  sparkles: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/><path d="m20 2 1 2 2 1-2 1-1 2-1-2-2-1 2-1Z"/>',
  code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18"/>',
  document: '<path d="M14 3H5v18h14V8Z"/><path d="M14 3v5h5M8 12h8m-8 4h6"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  folder: '<path d="M3 5h6l2 3h10v12H3Z"/>',
  paperclip: '<path d="m8 14 7-7a3 3 0 0 1 4 4L9 21a5 5 0 0 1-7-7L13 3a3 3 0 0 1 4 4L7 17"/>',
  send: '<path d="m3 4 18 8-18 8 3-8Zm3 8h15"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
  panel: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M15 3v18"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  plug: '<path d="M8 3v5m8-5v5M5 8h14v3a7 7 0 0 1-14 0Zm7 10v4"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
};
function icon(name) { return `<svg viewBox="0 0 24 24" aria-hidden="true">${shapes[name] || shapes.document}</svg>`; }
$$('[data-icon]').forEach(node => { node.innerHTML = icon(node.dataset.icon); });
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
const state = {sessions: [], sid: null, snapshot: null, events: [], files: [], attachments: [], drafts: new Map(), source: null, config: null, busy: false, loading: 0};
const statusNames = {queued: '等待执行', running: '正在执行', completed: '已完成', failed: '执行失败', cancelled: '已停止', interrupted: '执行中断'};
let toastTimer, refreshTimer;
function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error', error);
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, error ? 6500 : 3500);
}
async function api(path, options = {}) {
  const headers = {...options.headers};
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  let response;
  try { response = await fetch('/api' + path, {...options, headers}); }
  catch { throw new Error('暂时无法连接工作台，请检查服务是否正在运行。已有工作会保留。'); }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `请求失败（HTTP ${response.status}）`);
  }
  return response.status === 204 ? null : response.json();
}
function timeLabel(value) { return new Date(value * 1000).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'}); }
function dateLabel(value) {
  const date = new Date(value * 1000), now = new Date();
  return date.toDateString() === now.toDateString() ? '今天 ' + timeLabel(value) : date.toLocaleDateString('zh-CN', {month: 'short', day: 'numeric'});
}
function sizeLabel(bytes) { return bytes < 1024 ? bytes + ' B' : bytes < 1024 ** 2 ? (bytes / 1024).toFixed(1) + ' KB' : (bytes / 1024 ** 2).toFixed(1) + ' MB'; }
function activeRun() { return state.snapshot?.runs.findLast(run => ['queued', 'running'].includes(run.status)); }
function markdown(text) {
  const node = el('div', 'markdown');
  node.innerHTML = DOMPurify.sanitize(marked.parse(text || '', {breaks: true}), {FORBID_TAGS: ['style', 'iframe', 'form', 'input', 'button'], FORBID_ATTR: ['style']});
  $$('a', node).forEach(link => { link.rel = 'noopener noreferrer'; link.target = '_blank'; });
  return node;
}
function saveDraft() {
  const key = state.sid || '_new';
  state.drafts.set(key, {content: $('#prompt').value, attachments: [...state.attachments]});
  sessionStorage.setItem('orbit-drafts', JSON.stringify([...state.drafts]));
}
function restoreDraft() {
  const draft = state.drafts.get(state.sid || '_new');
  $('#prompt').value = draft?.content || '';
  state.attachments = draft?.attachments || [];
  renderAttachments(); resizePrompt();
}
function resizePrompt() {
  const field = $('#prompt');
  field.style.height = 'auto'; field.style.height = Math.min(180, Math.max(58, field.scrollHeight)) + 'px';
  $('#send-button').disabled = state.busy || !!activeRun() || !field.value.trim();
}
async function refreshSettings() {
  state.config = await api('/settings');
  $('#setup-banner').hidden = state.config.configured;
  $('#model-label').textContent = state.config.model || 'OpenCode Agent';
  $('#model-label').title = 'OpenCode ' + state.config.agent_version + ' · ' + (state.config.model || '尚未配置模型');
  $('#connection-label').textContent = state.config.configured ? '模型连接已配置' : '等待连接模型';
}
async function refreshSessions() {
  state.sessions = await api('/sessions'); renderSessions();
}
function renderSessions() {
  const root = $('#session-list'); root.replaceChildren();
  const query = $('#search').value.trim().toLowerCase();
  const filtered = state.sessions.filter(s => s.title.toLowerCase().includes(query));
  $('#session-count').textContent = state.sessions.length;
  if (!filtered.length) { root.append(el('div', 'session-empty', query ? '没有找到相关会话' : '还没有工作记录\n从一件小事开始吧')); return; }
  filtered.forEach(session => {
    const button = el('button', 'session-item' + (session.id === state.sid ? ' active' : ''));
    button.dataset.sessionId = session.id;
    button.innerHTML = icon('chat');
    const copy = el('span', 'session-item-copy');
    copy.append(el('strong', '', session.title), el('small', '', `${dateLabel(session.updated_at)}${session.status ? ' · ' + statusNames[session.status] : ''}`));
    button.append(copy);
    if (['queued', 'running'].includes(session.status)) button.append(el('i', 'session-running'));
    button.addEventListener('click', () => selectSession(session.id).catch(showError));
    root.append(button);
  });
}
function resetSource() { if (state.source) { state.source.close(); state.source = null; } }
async function selectSession(sid, {keepDraft = false} = {}) {
  if (!keepDraft) saveDraft();
  resetSource();
  const ticket = ++state.loading;
  state.sid = sid; state.snapshot = null; state.events = []; state.files = [];
  $('#sidebar').classList.remove('open');
  if (!keepDraft) restoreDraft();
  renderSessions();
  if (!sid) { history.replaceState(null, '', '/'); renderAll(); return; }
  history.replaceState(null, '', '#' + sid);
  localStorage.setItem('orbit-session', sid);
  $('#message-list').replaceChildren(el('div', 'empty-conversation', '正在恢复这项工作…'));
  $('#welcome').hidden = true;
  const snapshot = await api('/sessions/' + sid);
  if (ticket !== state.loading) return;
  state.snapshot = snapshot; state.events = snapshot.events;
  renderAll(); await refreshFiles(sid);
  if (ticket !== state.loading) return;
  connectEvents(sid, snapshot.last_event_id);
  $('#messages-scroll').scrollTop = $('#messages-scroll').scrollHeight;
}
function connectEvents(sid, cursor) {
  resetSource();
  const source = new EventSource(`/api/sessions/${sid}/events?after=${cursor}`);
  state.source = source;
  source.addEventListener('update', event => {
    if (state.sid !== sid) return;
    const data = JSON.parse(event.data);
    if (state.events.some(e => e.id === data.id)) return;
    const scroll = $('#messages-scroll'), nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 160;
    state.events.push(data);
    if (state.events.length > 500) state.events.shift();
    if (data.kind === 'status') {
      const run = state.snapshot?.runs.find(r => r.id === data.run_id);
      if (run) Object.assign(run, data.data);
      refreshSessions().catch(() => {});
    }
    if (['files', 'assistant'].includes(data.kind) || (data.kind === 'status' && !['running', 'queued'].includes(data.data.status))) {
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => refreshSnapshot(sid).catch(showError), 180);
    }
    renderMessages(); renderRunState(); renderActivity();
    if (nearBottom) scroll.scrollTop = scroll.scrollHeight;
  });
  source.onerror = () => { $('#connection-label').textContent = '连接恢复中，任务继续执行'; };
  source.onopen = () => { $('#connection-label').textContent = state.config?.configured ? '模型连接已配置' : '等待连接模型'; };
}
async function refreshSnapshot(sid) {
  if (state.sid !== sid) return;
  const snapshot = await api('/sessions/' + sid);
  if (state.sid !== sid) return;
  state.snapshot = snapshot;
  // Preserve newer SSE events if a response overlapped an incoming update.
  const newer = state.events.filter(e => e.id > snapshot.last_event_id);
  state.events = [...snapshot.events, ...newer];
  renderAll(); await refreshFiles(sid);
}
function renderAll() {
  $('#session-title').textContent = state.snapshot?.title || '新的开始';
  $('#delete-session').hidden = !state.sid;
  $('#welcome').hidden = !!state.sid;
  renderMessages(); renderRunState(); renderActivity(); renderFiles(); renderAttachments(); resizePrompt();
}
function messageNode(message, live = false) {
  const node = el('article', `message ${message.role}${live ? ' live' : ''}`);
  const heading = el('div', 'message-heading');
  const avatar = el('span', 'message-avatar');
  if (message.role === 'assistant') avatar.innerHTML = icon('sparkles'); else avatar.textContent = '我';
  heading.append(avatar, el('strong', '', message.role === 'assistant' ? 'Orbit' : '你'));
  if (live) heading.append(el('small', '', '工作进行中'));
  const time = el('time', '', timeLabel(message.created_at)); heading.append(time); node.append(heading);
  const body = el('div', 'message-body');
  if (message.role === 'assistant') body.append(markdown(message.content)); else body.textContent = message.content;
  node.append(body);
  if (message.attachments?.length) {
    const attachments = el('div', 'message-attachments');
    message.attachments.forEach(path => {
      const chip = el('button', 'attachment-chip'); chip.innerHTML = icon('document'); chip.append(document.createTextNode(path.split('/').pop()));
      chip.onclick = () => openFile(path).catch(showError); attachments.append(chip);
    });
    node.append(attachments);
  }
  return node;
}
function renderMessages() {
  const root = $('#message-list'); root.replaceChildren();
  const snapshot = state.snapshot;
  if (!snapshot) return;
  if (!snapshot.messages.length) {
    const empty = el('div', 'empty-conversation'); empty.innerHTML = icon('sparkles');
    empty.append(el('div', '', '这是一片新的工作空间。'), el('div', '', '描述你的目标，或上传材料，让我们开始。')); root.append(empty);
  }
  snapshot.messages.forEach((message, i) => {
    root.append(messageNode(message));
    const run = snapshot.runs.find(r => r.id === message.run_id);
    const nextSame = snapshot.messages[i + 1]?.run_id === message.run_id;
    if (!nextSame && run && ['failed', 'interrupted', 'cancelled'].includes(run.status)) {
      root.append(el('div', 'run-notice ' + run.status, run.error || '任务已停止，已完成的文件仍然保留。发送新消息可以继续。'));
    }
  });
  const run = activeRun();
  if (run) {
    const text = state.events.filter(e => e.run_id === run.id && e.kind === 'text').map(e => e.data.text).join('\n\n');
    if (text) root.append(messageNode({role: 'assistant', content: text, created_at: run.created_at}, true));
  }
}
function renderRunState() {
  const run = activeRun(), row = $('#run-state');
  row.replaceChildren(); row.className = 'run-state'; row.hidden = !run;
  if (run) {
    row.append(el('span', 'pulse'));
    const tools = state.events.filter(e => e.run_id === run.id && e.kind === 'tool');
    const latest = tools.at(-1)?.data;
    row.append(el('span', '', run.status === 'queued' ? '正在排队，轮到后会自动开始。' : latest ? `已完成 ${tools.length} 项操作 · ${latest.tool}，继续处理中…` : '助手正在处理任务，进展将显示在执行记录中…'));
  }
  $('#stop-button').hidden = !run;
  $('#send-button').hidden = !!run;
  $('#prompt').placeholder = run ? '可先写好下一条消息，当前任务结束后发送…' : '描述任务，或上传材料开始…';
  $('#attach-button').disabled = !!run || state.busy;
  resizePrompt();
}
function fileBadge(name) {
  const extension = name.split('.').at(-1).toLowerCase();
  const type = ['csv', 'xlsx', 'json'].includes(extension) ? ' data' : ['py', 'js', 'ts', 'html', 'css', 'sh'].includes(extension) ? ' code' : '';
  return el('span', 'file-type' + type, extension.length < 7 ? extension.toUpperCase() : 'FILE');
}
async function refreshFiles(sid = state.sid) {
  if (!sid) return;
  const data = await api(`/sessions/${sid}/files`);
  if (state.sid !== sid) return;
  state.files = data.files; renderFiles();
  if (data.truncated) $('#file-count').title = '仅展示前 1000 个文件';
}
function renderFiles() {
  const root = $('#file-list'); root.replaceChildren();
  $('#file-count').textContent = state.files.length;
  $('#file-empty').hidden = !!state.files.length;
  $('#download-all').disabled = !state.sid || !state.files.length;
  for (const [type, label] of [['output', '工作成果'], ['upload', '上传材料']]) {
    const files = state.files.filter(file => file.source === type);
    if (!files.length) continue;
    root.append(el('div', 'file-group-label', label));
    files.forEach(file => {
      const item = el('button', 'file-item'); item.dataset.filePath = file.path;
      item.append(fileBadge(file.name));
      const copy = el('span', 'file-item-copy'); copy.append(el('strong', '', file.name), el('small', '', `${sizeLabel(file.size)} · ${file.path.includes('/') ? file.path.slice(0, file.path.lastIndexOf('/')) : '工作区'}`));
      item.append(copy); const smallIcon = el('span'); smallIcon.innerHTML = icon('document'); item.append(smallIcon);
      item.title = file.path; item.onclick = () => openFile(file.path).catch(showError); root.append(item);
    });
  }
}
function renderActivity() {
  const root = $('#activity-list'); root.replaceChildren();
  const events = state.events.filter(e => ['tool', 'error', 'status', 'log'].includes(e.kind));
  $('#activity-count').textContent = events.length;
  if (!events.length) { root.append(el('div', 'activity-empty', '开始一项任务后，\n这里会显示真实的执行过程。')); return; }
  events.slice(-100).reverse().forEach(event => {
    const item = el('div', 'activity-event' + (event.kind === 'error' || event.data.status === 'failed' ? ' error' : ''));
    if (event.kind === 'tool') {
      const details = el('details');
      const summary = el('summary', '', event.data.title || event.data.input?.command || `${event.data.tool} · ${event.data.status === 'error' ? '失败' : '完成'}`);
      const pre = el('pre', '', JSON.stringify(event.data.input, null, 2) + '\n\n' + event.data.output);
      details.append(summary, pre); item.append(details);
    } else if (event.kind === 'status') {
      item.append(el('strong', '', statusNames[event.data.status] || event.data.status));
      if (event.data.error) item.append(el('pre', '', event.data.error));
    } else {
      const details = el('details'); details.append(el('summary', '', event.kind === 'error' ? 'Agent 返回错误' : 'CLI 日志'), el('pre', '', event.data.message || event.data.text)); item.append(details);
    }
    item.append(el('time', '', timeLabel(event.created_at))); root.append(item);
  });
}
function renderAttachments() {
  const root = $('#attachments'); root.replaceChildren();
  state.attachments.forEach(path => {
    const chip = el('span', 'attachment-chip'); chip.innerHTML = icon('document'); chip.append(document.createTextNode(path.split('/').pop()));
    const button = el('button', '', '×'); button.title = '移除本次附件（保留文件）'; button.onclick = () => { state.attachments = state.attachments.filter(p => p !== path); saveDraft(); renderAttachments(); };
    chip.append(button); root.append(chip);
  });
}
async function newSession(title = '新会话') {
  const session = await api('/sessions', {method: 'POST', body: {title}});
  await refreshSessions(); await selectSession(session.id); $('#prompt').focus(); return session.id;
}
async function send() {
  if (state.busy || activeRun()) return;
  const content = $('#prompt').value.trim();
  if (!content) return;
  if (!state.config?.configured) { await openSettings(); return; }
  state.busy = true; resizePrompt();
  try {
    if (!state.sid) {
      const draft = {content, attachments: [...state.attachments]};
      const session = await api('/sessions', {method: 'POST', body: {title: '新会话'}});
      state.drafts.set(session.id, draft);
      state.drafts.delete('_new');
      await selectSession(session.id);
    }
    const sid = state.sid;
    await api(`/sessions/${sid}/messages`, {method: 'POST', body: {content, attachments: state.attachments}});
    $('#prompt').value = ''; state.attachments = []; saveDraft();
    await refreshSessions(); await selectSession(sid, {keepDraft: true});
    $('#messages-scroll').scrollTop = $('#messages-scroll').scrollHeight;
  } finally { state.busy = false; renderRunState(); renderAttachments(); }
}
async function upload(files) {
  if (!files.length) return;
  if (activeRun()) throw new Error('任务执行中，请先等待完成再上传材料。');
  if (files.length > 20 || [...files].some(file => file.size > 25 * 1024 * 1024)) throw new Error('每次最多 20 个文件，单个不超过 25 MB。');
  state.busy = true; resizePrompt();
  try {
    if (!state.sid) {
      const draft = {content: $('#prompt').value, attachments: [...state.attachments]};
      const session = await api('/sessions', {method: 'POST', body: {title: '新会话'}});
      state.drafts.set(session.id, draft); await selectSession(session.id);
    }
    const sid = state.sid, form = new FormData();
    [...files].forEach(file => form.append('files', file));
    const response = await api(`/sessions/${sid}/files`, {method: 'POST', body: form});
    if (state.sid === sid) { state.attachments.push(...response.paths); renderAttachments(); saveDraft(); await refreshFiles(sid); }
    toast(`已上传 ${response.paths.length} 个文件，可以发送分析要求。`);
  } finally { state.busy = false; resizePrompt(); $('#file-input').value = ''; }
}
async function openSettings() {
  await refreshSettings();
  $('#setting-base').value = state.config.base_url;
  $('#setting-model').value = state.config.model;
  $('#setting-key').value = '';
  $('#setting-key').placeholder = state.config.has_key ? '已保存，留空保持不变' : '输入临时凭据';
  $('#settings-result').textContent = '';
  $('#settings-dialog').showModal();
}
$('#settings-form').addEventListener('submit', async event => {
  event.preventDefault(); $('#save-settings').disabled = true;
  $('#settings-result').classList.remove('error'); $('#settings-result').textContent = '正在保存并检测连接…';
  try {
    await api('/settings', {method: 'PUT', body: {base_url: $('#setting-base').value.trim(), model: $('#setting-model').value.trim(), api_key: $('#setting-key').value || null}});
    $('#setting-key').value = '';
    const result = await api('/models');
    $('#model-options').replaceChildren(...result.models.map(name => { const option = el('option'); option.value = name; return option; }));
    $('#setting-model').value = result.selected;
    $('#settings-result').textContent = '连接成功 · 可用模型：' + result.models.join('、');
    await refreshSettings();
  } catch (error) { $('#settings-result').textContent = error.message; $('#settings-result').classList.add('error'); }
  finally { $('#save-settings').disabled = false; }
});
let previewData, previewPath, previewSid, livePreview = false;
async function openFile(path) {
  const sid = state.sid;
  const data = await api(`/sessions/${sid}/files/content?path=${encodeURIComponent(path)}`);
  if (state.sid !== sid) return;
  previewData = data; previewPath = path; previewSid = sid; livePreview = false;
  $('#preview-title').textContent = path.split('/').pop();
  $('#preview-meta').textContent = `${path} · ${sizeLabel(data.size)}`;
  $('#preview-file-icon').replaceChildren(fileBadge(path));
  $('#preview-download').href = `/api/sessions/${sid}/files/download?path=${encodeURIComponent(path)}`;
  $('#preview-toggle').hidden = data.kind !== 'html';
  renderPreview(); if (!$('#file-dialog').open) $('#file-dialog').showModal();
}
function renderPreview() {
  const data = previewData, root = $('#preview-content'); root.replaceChildren(); root.className = 'preview-content';
  if (data.truncated) root.append(el('div', 'preview-note', '此处仅展示部分内容，请下载查看完整文件。'));
  if (data.kind === 'html' && livePreview) {
    root.classList.add('live');
    const frame = document.createElement('iframe'); frame.title = previewPath + ' 隔离预览';
    frame.setAttribute('sandbox', 'allow-scripts allow-downloads'); frame.referrerPolicy = 'no-referrer';
    frame.src = `/api/sessions/${previewSid}/files/preview?path=${encodeURIComponent(previewPath)}`;
    root.append(frame); $('#preview-toggle').textContent = '查看源码';
  } else if (data.kind === 'image') {
    const image = document.createElement('img'); image.alt = previewPath;
    image.src = `/api/sessions/${previewSid}/files/download?path=${encodeURIComponent(previewPath)}`; root.append(image);
  } else if (data.kind === 'markdown') root.append(markdown(data.content));
  else if (data.kind === 'csv') {
    const table = el('table', 'csv-preview');
    data.rows.forEach((row, index) => { const tr = el('tr'); row.forEach(value => tr.append(el(index === 0 ? 'th' : 'td', '', value))); table.append(tr); }); root.append(table);
  } else root.append(el('pre', '', data.content));
  if (!livePreview) $('#preview-toggle').textContent = '运行预览';
}
$('#preview-toggle').onclick = () => { livePreview = !livePreview; renderPreview(); };
document.addEventListener('click', event => {
  const link = event.target.closest('.markdown a');
  if (!link || !state.sid) return;
  let pathname;
  try { pathname = decodeURIComponent(new URL(link.getAttribute('href'), location.href).pathname).replace(/^\//, ''); } catch { return; }
  const file = state.files.find(f => f.path === pathname || pathname.endsWith('/' + f.path));
  if (file) { event.preventDefault(); openFile(file.path).catch(showError); }
});
function inspectorTab(name) {
  $('#files-panel').hidden = name !== 'files'; $('#activity-panel').hidden = name !== 'activity';
  for (const key of ['files', 'activity']) { $('#' + key + '-tab').classList.toggle('active', key === name); $('#' + key + '-tab').setAttribute('aria-selected', key === name); }
}
$('#files-tab').onclick = () => inspectorTab('files');
$('#activity-tab').onclick = () => inspectorTab('activity');
$('#new-session').onclick = () => newSession().catch(showError);
$('#send-button').onclick = () => send().catch(showError);
$('#prompt').addEventListener('input', () => { resizePrompt(); saveDraft(); });
$('#prompt').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send().catch(showError); } });
$('#search').oninput = renderSessions;
$('#attach-button').onclick = $('#empty-upload').onclick = () => $('#file-input').click();
$('#file-input').onchange = () => upload($('#file-input').files).catch(showError);
$('#open-settings').onclick = $('#setup-button').onclick = () => openSettings().catch(showError);
$('#mobile-menu').onclick = () => $('#sidebar').classList.toggle('open');
$('#toggle-files').onclick = () => {
  $('#inspector').classList.toggle(matchMedia('(max-width:1000px)').matches ? 'open' : 'collapsed');
};
$('#stop-button').onclick = async () => {
  const run = activeRun(), sid = state.sid;
  if (!run) return;
  $('#stop-button').disabled = true;
  try { await api(`/sessions/${sid}/runs/${run.id}/cancel`, {method: 'POST'}); await refreshSnapshot(sid); toast('任务已停止，已生成的文件会保留。'); }
  catch (error) { showError(error); } finally { $('#stop-button').disabled = false; }
};
$('#download-all').onclick = () => { if (state.sid) window.location.assign(`/api/sessions/${state.sid}/archive`); };
$('#session-title').onclick = async () => {
  if (!state.sid) return;
  const title = window.prompt('重命名会话', state.snapshot?.title);
  if (!title?.trim()) return;
  try { await api('/sessions/' + state.sid, {method: 'PATCH', body: {title: title.trim()}}); await refreshSessions(); await refreshSnapshot(state.sid); }
  catch (error) { showError(error); }
};
$('#delete-session').onclick = async () => {
  if (!state.sid || !window.confirm('删除这个会话及其中的所有文件？此操作无法撤销。')) return;
  try { await api('/sessions/' + state.sid, {method: 'DELETE'}); resetSource(); localStorage.removeItem('orbit-session'); await refreshSessions(); await selectSession(state.sessions[0]?.id || null); }
  catch (error) { showError(error); }
};
$$('.close-dialog').forEach(button => { button.onclick = () => button.closest('dialog').close(); });
$$('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } }));
$$('[data-starter]').forEach(button => { button.onclick = () => { $('#prompt').value = button.dataset.starter === 'code' ? '请开发一个可独立打开的番茄钟网页工具，支持开始、暂停和重置，并实际验证功能。' : '请分析我上传的工作材料，整理关键发现和行动建议，并生成一份 Markdown 报告。'; saveDraft(); resizePrompt(); $('#prompt').focus(); if (button.dataset.starter === 'work') $('#file-input').click(); }; });
for (const name of ['dragenter', 'dragover']) $('#composer').addEventListener(name, event => { event.preventDefault(); $('#composer').classList.add('dragging'); });
for (const name of ['dragleave', 'drop']) $('#composer').addEventListener(name, event => { event.preventDefault(); $('#composer').classList.remove('dragging'); if (name === 'drop') upload(event.dataTransfer.files).catch(showError); });
window.addEventListener('beforeunload', () => { saveDraft(); resetSource(); });
window.addEventListener('hashchange', () => {
  const sid = location.hash.slice(1) || null;
  if (sid !== state.sid) selectSession(sid).catch(showError);
});
document.addEventListener('keydown', event => {
  if (event.key.toLowerCase() !== 'n' || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
  if (event.target.closest('input,textarea,[contenteditable="true"]') || $('dialog[open]')) return;
  event.preventDefault();
  newSession().catch(showError);
});
function showError(error) { toast(error.message || '操作失败，请重试。', true); }
async function boot() {
  try {
    state.drafts = new Map(JSON.parse(sessionStorage.getItem('orbit-drafts') || '[]'));
  } catch { state.drafts = new Map(); }
  try {
    await Promise.all([refreshSettings(), refreshSessions()]);
    const candidate = location.hash.slice(1) || localStorage.getItem('orbit-session');
    const sid = state.sessions.some(s => s.id === candidate) ? candidate : null;
    await selectSession(sid, {keepDraft: true}); restoreDraft();
  } catch (error) { showError(error); }
}
setInterval(() => { if (!document.hidden) refreshSessions().catch(() => {}); }, 5000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { refreshSessions().catch(() => {}); if (state.sid) refreshSnapshot(state.sid).catch(() => {}); } });
boot();
