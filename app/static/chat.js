const $ = (selector) => document.querySelector(selector);
const state = { user: null, references: [], busy: false, timer: null, startedAt: 0, history: [] };
const MAX_REFERENCES = 8;
const MAX_REFERENCE_BYTES = 50 * 1024 * 1024;

class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
  let body; try { body = await response.json(); } catch { throw new ApiError('服务没有返回可读取的内容，请稍后再试。', response.status); }
  if (!response.ok) {
    let detail = body.detail || body.error || '请求未完成';
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg || '').join('；').replaceAll('Value error, ', '');
    if (typeof detail !== 'string') detail = detail.message || '请求未完成';
    throw new ApiError(detail, response.status);
  }
  return body;
}
function escapeHtml(value) { const node = document.createElement('span'); node.textContent = String(value ?? ''); return node.innerHTML; }
function elapsed(ms) { const seconds = Math.max(0, Math.floor(ms / 1000)); return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`; }
function formatDate(value) { try { return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value)); } catch { return ''; } }
function toast(message, error = false) { const node = $('#chat-toast'); node.textContent = message; node.classList.toggle('error', error); node.classList.add('show'); clearTimeout(node.timer); node.timer = setTimeout(() => node.classList.remove('show'), error ? 7000 : 3200); }
function id() { return globalThis.crypto?.randomUUID?.() || `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function currentSettings() { return { model: 'gpt-image-2', size: $('#chat-size').value, quality: $('#chat-quality').value, resolution: $('#chat-resolution').value }; }
function setBusy(busy) { state.busy = busy; $('#chat-send').disabled = busy; $('#chat-files').disabled = busy; $('#chat-prompt').disabled = busy; $('#new-chat').disabled = busy; }

function autoResize() { const field = $('#chat-prompt'); field.style.height = 'auto'; field.style.height = `${Math.min(180, Math.max(42, field.scrollHeight))}px`; }
function updateAttachmentStrip() {
  const strip = $('#attachment-strip'); strip.classList.toggle('is-empty', !state.references.length); strip.replaceChildren(...state.references.map((reference, index) => {
    const card = document.createElement('div'); card.className = 'attachment-card';
    card.innerHTML = `<img src="${reference.preview}" alt="参考图 ${index + 1}"><span>${index + 1}</span><button type="button" aria-label="移除参考图 ${index + 1}">×</button>`;
    card.querySelector('button').addEventListener('click', () => { URL.revokeObjectURL(reference.preview); state.references = state.references.filter((item) => item.key !== reference.key); updateAttachmentStrip(); });
    return card;
  }));
  $('#attachment-note').textContent = state.references.length ? `已添加 ${state.references.length} / ${MAX_REFERENCES} 张参考图 · 生成时将进入图生图模式` : '支持 PNG、JPG、WebP；最多 8 张参考图，Ctrl + V 可连续粘贴';
}
function fileToReference(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onerror = () => reject(new Error('图片读取失败，请重新选择。')); reader.onload = () => resolve({ key: id(), file, data: reader.result, preview: URL.createObjectURL(file) }); reader.readAsDataURL(file); }); }
async function addFiles(files) {
  const candidates = Array.from(files || []).filter((file) => /^image\/(png|jpeg|webp)$/.test(file.type));
  if (!candidates.length) { toast('请选择 PNG、JPG 或 WebP 图片。', true); return; }
  const available = MAX_REFERENCES - state.references.length;
  if (available <= 0) { toast(`最多添加 ${MAX_REFERENCES} 张参考图。`, true); return; }
  const accepted = candidates.slice(0, available);
  const total = state.references.reduce((sum, item) => sum + item.file.size, 0) + accepted.reduce((sum, file) => sum + file.size, 0);
  if (total > MAX_REFERENCE_BYTES) { toast('参考图总大小不能超过 50MB。', true); return; }
  try { state.references.push(...await Promise.all(accepted.map(fileToReference))); updateAttachmentStrip(); $('#chat-prompt').focus(); if (candidates.length > accepted.length) toast(`已添加前 ${available} 张图片。`); } catch (error) { toast(error.message, true); }
}
function userMessage(prompt, references) {
  const article = document.createElement('article'); article.className = 'message user-message';
  const refs = references.length ? `<div class="message-reference-grid">${references.map((item, index) => `<img src="${item.data}" alt="参考图 ${index + 1}">`).join('')}</div>` : '';
  article.innerHTML = `<div class="message-body"><p class="message-name">你</p><div class="message-bubble">${refs}<p>${escapeHtml(prompt)}</p></div></div><div class="message-avatar">你</div>`;
  $('#chat-messages').append(article); article.scrollIntoView({ behavior: 'smooth', block: 'end' });
}
function pendingMessage() {
  const article = document.createElement('article'); article.className = 'message assistant-message';
  article.innerHTML = `<div class="message-avatar">✦</div><div class="message-body"><p class="message-name">AstroGate IMG</p><div class="message-bubble pending-card"><span class="progress-orbit" aria-hidden="true"></span><div class="pending-copy"><b>正在创作画面</b><span>已等待 <em>0 秒</em> · 上游最长等待约 12 分钟</span></div></div></div>`;
  $('#chat-messages').append(article); article.scrollIntoView({ behavior: 'smooth', block: 'end' });
  const time = article.querySelector('em'); state.startedAt = Date.now(); state.timer = setInterval(() => { time.textContent = elapsed(Date.now() - state.startedAt); }, 1000); return article;
}
function resultMessage(item, duration) {
  const article = document.createElement('article'); article.className = 'message assistant-message';
  const source = typeof item.image_src === 'string' ? item.image_src : '';
  article.innerHTML = `<div class="message-avatar">✦</div><div class="message-body"><p class="message-name">AstroGate IMG</p><div class="message-bubble"><h2>画面已经完成</h2><p>已保存到你的作品库。</p><div class="generation-card"><img src="${escapeHtml(source)}" alt="本次生成的作品" data-zoom><div class="generation-info"><span><b>✓ 已完成</b> · 用时 ${elapsed(duration)}</span><a href="${escapeHtml(source)}" target="_blank" rel="noopener">查看原图 ↗</a></div></div></div></div>`;
  article.querySelector('[data-zoom]').addEventListener('click', () => openImage(source)); $('#chat-messages').append(article); article.scrollIntoView({ behavior: 'smooth', block: 'end' });
}
function errorMessage(error, duration) {
  const article = document.createElement('article'); article.className = 'message assistant-message';
  article.innerHTML = `<div class="message-avatar">!</div><div class="message-body"><p class="message-name">AstroGate IMG</p><div class="message-bubble error-card"><h2>这次没有完成</h2><p>${escapeHtml(error.message)}${duration ? `\n已等待 ${elapsed(duration)}。如果积分已扣除，失败任务会自动退回。` : ''}</p></div></div>`;
  $('#chat-messages').append(article); article.scrollIntoView({ behavior: 'smooth', block: 'end' });
}
function renderRecentImages() {
  const root = $('#recent-images'); const items = state.history.slice(0, 6); root.replaceChildren(...(items.length ? items.map((item) => {
    const link = document.createElement('a'); link.className = 'recent-image'; link.href = item.image_src; link.target = '_blank'; link.rel = 'noopener';
    link.innerHTML = `<img src="${escapeHtml(item.image_src)}" alt=""><span>${escapeHtml(item.prompt || '未命名作品')}</span>`; return link;
  }) : [Object.assign(document.createElement('span'), { className: 'side-empty', textContent: '还没有作品，先发第一句话吧。' })]));
}
async function loadHistory() { try { const result = await api('/api/images/history'); state.history = Array.isArray(result.items) ? result.items : []; renderRecentImages(); } catch { $('#recent-images').innerHTML = '<span class="side-empty">作品库暂时无法读取。</span>'; } }
async function submit(event) {
  event.preventDefault(); if (state.busy) return;
  const prompt = $('#chat-prompt').value.trim(); if (!prompt) { $('#chat-prompt').focus(); return; }
  const references = [...state.references]; const settings = currentSettings();
  userMessage(prompt, references); $('#chat-prompt').value = ''; autoResize(); state.references = []; updateAttachmentStrip(); setBusy(true); const pending = pendingMessage();
  try {
    const body = { ...settings, prompt };
    if (references.length) body.image_data_urls = references.map((reference) => reference.data);
    const result = await api(references.length ? '/api/images/edits' : '/api/images/generations', { method: 'POST', headers: { 'Idempotency-Key': id() }, body: JSON.stringify(body) });
    const duration = Date.now() - state.startedAt; clearInterval(state.timer); pending.remove(); resultMessage(result.item, duration);
    state.user.credits = result.credits; $('#chat-credits').textContent = result.credits; state.history.unshift(result.item); renderRecentImages();
  } catch (error) { const duration = Date.now() - state.startedAt; clearInterval(state.timer); pending.remove(); errorMessage(error instanceof Error ? error : new Error('生成没有完成。'), duration); }
  finally { references.forEach((reference) => URL.revokeObjectURL(reference.preview)); setBusy(false); $('#chat-prompt').focus(); }
}
function newChat() { if (state.busy) return; $('#chat-messages').querySelectorAll('.message:not(.welcome-message)').forEach((node) => node.remove()); state.references.forEach((reference) => URL.revokeObjectURL(reference.preview)); state.references = []; updateAttachmentStrip(); $('#chat-prompt').value = ''; autoResize(); $('#chat-prompt').focus(); }
function openImage(source) { const dialog = $('#image-dialog'); const image = $('#image-dialog-image'); image.src = source; image.style.transform = 'translate(0,0) scale(1)'; dialog.dataset.scale = '1'; dialog.dataset.x = '0'; dialog.dataset.y = '0'; dialog.showModal(); }
function installImagePanZoom() { const dialog = $('#image-dialog'); const canvas = $('#image-dialog-canvas'); const image = $('#image-dialog-image'); let drag = null;
  const transform = () => { image.style.transform = `translate(${dialog.dataset.x || 0}px,${dialog.dataset.y || 0}px) scale(${dialog.dataset.scale || 1})`; };
  canvas.addEventListener('wheel', (event) => { event.preventDefault(); const next = Math.max(.5, Math.min(8, Number(dialog.dataset.scale || 1) * (event.deltaY < 0 ? 1.13 : .87))); dialog.dataset.scale = String(next); transform(); }, { passive: false });
  canvas.addEventListener('pointerdown', (event) => { drag = { x: event.clientX, y: event.clientY, left: Number(dialog.dataset.x || 0), top: Number(dialog.dataset.y || 0) }; canvas.setPointerCapture(event.pointerId); });
  canvas.addEventListener('pointermove', (event) => { if (!drag) return; dialog.dataset.x = String(drag.left + event.clientX - drag.x); dialog.dataset.y = String(drag.top + event.clientY - drag.y); transform(); });
  canvas.addEventListener('pointerup', () => { drag = null; }); $('#image-dialog-close').addEventListener('click', () => dialog.close());
}
async function boot() {
  try { const auth = await api('/api/auth/me'); state.user = auth.user; $('#chat-credits').textContent = auth.user.credits; $('#chat-avatar').textContent = (auth.user.display_name || auth.user.username || 'A').slice(0, 1).toUpperCase(); }
  catch { location.assign('/login?next=/chat'); return; }
  await loadHistory();
  $('#chat-form').addEventListener('submit', submit); $('#chat-prompt').addEventListener('input', autoResize); $('#chat-prompt').addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); $('#chat-form').requestSubmit(); } });
  $('#chat-files').addEventListener('change', (event) => { addFiles(event.target.files); event.target.value = ''; }); $('#new-chat').addEventListener('click', newChat);
  document.addEventListener('paste', (event) => { if (state.busy) return; const files = Array.from(event.clipboardData?.items || []).filter((item) => item.kind === 'file' && /^image\//.test(item.type)).map((item) => item.getAsFile()).filter(Boolean); if (files.length) { event.preventDefault(); addFiles(files); } });
  const zone = $('#composer-zone'); ['dragenter', 'dragover'].forEach((name) => zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.add('dragging'); })); ['dragleave', 'drop'].forEach((name) => zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.remove('dragging'); })); zone.addEventListener('drop', (event) => addFiles(event.dataTransfer.files));
  document.querySelectorAll('[data-suggestion]').forEach((button) => button.addEventListener('click', () => { $('#chat-prompt').value = button.dataset.suggestion; autoResize(); $('#chat-prompt').focus(); }));
  $('#chat-logout').addEventListener('click', async () => { try { await api('/api/auth/logout', { method: 'POST' }); } finally { location.assign('/'); } }); installImagePanZoom(); autoResize();
}
boot();
