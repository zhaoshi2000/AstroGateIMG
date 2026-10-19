const $ = (selector) => document.querySelector(selector);
const state = { user: null, mode: 'login', references: [], history: [], activeImageItem: null, busy: false, reading: false };
if (['/studio', '/wallet'].includes(location.pathname)) {
  document.body?.classList.add('studio-theme');
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', '#0b1020');
}
const ideas = {
  fantasy: '一位旅人站在云海边的古老城堡前，巨龙从远处天空掠过，月光照亮披风与石阶，细腻奇幻插画，电影感构图。',
  city: '雨夜的未来都市街道，霓虹灯映在潮湿地面上，一位穿黑色外套的年轻人回头望向镜头，紫蓝色光影，精致动漫插画。',
  product: '一瓶透明玻璃香水置于浅紫色石台上，晨光从侧面穿过，细腻的玻璃折射，极简产品摄影，干净留白。',
  space: '一座面朝海面的暖色拱门建筑，柔和的午后阳光，天然石材与细腻肌理，地中海风格，建筑摄影。',
  abstract: '漂浮的紫色玻璃球体与金属圆环，柔和光线，浅薰衣草色背景，抽象艺术，精致的材质细节。',
};
const promptStyles = {
  none: '',
  anime: '动漫插画风格，细腻线条，柔和光影。',
  photo: '写实摄影风格，自然材质与真实光线。',
  fantasy: '奇幻插画风格，丰富层次与史诗氛围。',
  cyber: '赛博朋克风格，霓虹光影与未来都市氛围。',
  science: '科学图解风格，清晰布局与易辨认的视觉层级。',
  furry: '兽人角色插画风格，重视角色表情与毛发质感。',
};

class ApiError extends Error { constructor(message, status, retryAfter = 0) { super(message); this.status = status; this.retryAfter = retryAfter; } }
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
  let payload;
  try { payload = await response.json(); } catch { throw new ApiError('服务暂时没有响应，请稍后重试', response.status); }
  if (!response.ok) {
    let detail = payload.detail || payload.error || '请求未完成';
    if (Array.isArray(detail)) detail = detail.map((item) => String(item.msg || '')).join('；').replaceAll('Value error, ', '');
    if (typeof detail !== 'string') detail = detail.message || '请求未完成';
    throw new ApiError(detail, response.status, Math.max(0, Number(response.headers?.get('Retry-After')) || 0));
  }
  return payload;
}

let toastTimer;
function toast(message, error = false) {
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error', error);
  $('#toast').classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').classList.remove('show'), error ? 6500 : 3500);
}
function cell(value, className) { const el = document.createElement('td'); el.textContent = String(value ?? ''); if (className) el.className = className; return el; }
function date(value) { try { return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value)); } catch { return ''; } }
function imageSource(value) { return typeof value === 'string' && (/^\/api\/images\/files\/[0-9a-f]{24}$/.test(value) || /^https?:\/\//.test(value) || /^data:image\/(png|jpeg|webp);base64,/.test(value)) ? value : ''; }
function setBalance(balance) { if (state.user) state.user.credits = balance; $('#top-credits').textContent = balance; $('#wallet-balance').textContent = balance; }
async function refreshUser() { const data = await api('/api/auth/me'); state.user = data.user; setBalance(data.user.credits); }
function authDestination() { const next = new URLSearchParams(location.search).get('next'); return /^\/\?(?:post=[a-z0-9-]+|mine=1|compose=1(?:&image=[0-9a-f]{24})?)$/.test(next || '') ? next : state.user?.is_admin ? '/admin' : '/studio'; }

function authMode(mode) {
  state.mode = mode;
  const registering = mode === 'register';
  $('#login-tab').classList.toggle('active', !registering);
  $('#register-tab').classList.toggle('active', registering);
  $('#login-tab').setAttribute('aria-selected', String(!registering));
  $('#register-tab').setAttribute('aria-selected', String(registering));
  $('#auth-title').textContent = registering ? '为灵感，创建一个空间' : '欢迎回来';
  $('#auth-kicker').textContent = registering ? '开启你的创作旅程' : '欢迎回来';
  $('#auth-subtitle').textContent = registering ? '注册账号，开启你的图像创作。' : '登录后，继续你的创作。';
  $('#auth-submit').textContent = registering ? '创建账号，开始创作 ↗' : '登录工作台 ↗';
  $('#password').autocomplete = registering ? 'new-password' : 'current-password';
  $('#identity-label').textContent = registering ? '邮箱地址' : '邮箱 / 账号';
  $('#username').type = registering ? 'email' : 'text';
  $('#username').autocomplete = registering ? 'email' : 'username';
  $('#username').placeholder = registering ? '请输入你的邮箱地址' : '请输入邮箱或已有账号';
  $('#email-code-field').classList.toggle('is-hidden', !registering);
  $('#email-code').required = registering;
  $('#email-code').disabled = !registering;
  $('#auth-subtitle').textContent = registering ? '验证邮箱后，开启你的图像创作。' : '使用邮箱和密码，继续你的创作。';
  if (registering) restoreCodeCountdown();
}
$('#login-tab').addEventListener('click', () => authMode('login'));
$('#register-tab').addEventListener('click', () => authMode('register'));
$('#auth-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = $('#auth-submit'); button.disabled = true;
  try {
    const identifier = $('#username').value.trim();
    const body = state.mode === 'register'
      ? { email: identifier, code: $('#email-code').value.trim(), password: $('#password').value }
      : { identifier, password: $('#password').value };
    const result = await api(`/api/auth/${state.mode}`, { method: 'POST', body: JSON.stringify(body) }); state.user = result.user;
    location.assign(authDestination());
  } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
});

let codeCountdown;
let codeReadyAt = 0;
let codeEmail = '';
let codeSending = false;
const codeStoragePrefix = 'astrogate.email-code-ready-at:';
function updateCodeButton() {
  const remaining = Math.max(0, Math.ceil((codeReadyAt - Date.now()) / 1000));
  $('#send-email-code').disabled = codeSending || remaining > 0;
  $('#send-email-code').textContent = codeSending ? '正在发送…' : remaining ? `${remaining} 秒后重发` : '获取验证码';
  if (!remaining) clearInterval(codeCountdown);
}
function restartCodeCountdown() {
  clearInterval(codeCountdown);
  updateCodeButton();
  if (codeReadyAt > Date.now()) codeCountdown = setInterval(updateCodeButton, 1000);
}
function restoreCodeCountdown() {
  const email = $('#username').value.trim().toLowerCase();
  if (email !== codeEmail) codeReadyAt = 0;
  codeEmail = email;
  try { codeReadyAt = Math.max(codeReadyAt, Number(localStorage.getItem(codeStoragePrefix + email)) || 0); } catch {}
  restartCodeCountdown();
}
function startCodeCountdown(email, seconds) {
  codeEmail = email;
  codeReadyAt = Date.now() + seconds * 1000;
  try { localStorage.setItem(codeStoragePrefix + email, String(codeReadyAt)); } catch {}
  restartCodeCountdown();
}
$('#username').addEventListener('input', () => {
  if ($('#username').value.trim().toLowerCase() !== codeEmail) {
    $('#email-code').value = '';
    $('#email-code-hint').textContent = '验证码 10 分钟内有效。';
  }
  restoreCodeCountdown();
});
$('#username').addEventListener('change', restoreCodeCountdown);
window.addEventListener('pageshow', restoreCodeCountdown);
window.addEventListener('storage', (event) => { if (event.key?.startsWith(codeStoragePrefix)) restoreCodeCountdown(); });
$('#send-email-code').addEventListener('click', async () => {
  if (codeSending || codeReadyAt > Date.now()) return;
  if (!$('#username').reportValidity()) return;
  const email = $('#username').value.trim().toLowerCase();
  codeSending = true; updateCodeButton();
  $('#username').disabled = true;
  try {
    const result = await api('/api/auth/email-code', { method: 'POST', body: JSON.stringify({ email }) });
    startCodeCountdown(email, result.resend_after);
    $('#email-code-hint').textContent = '验证码已发送，请查收邮箱，也可以检查垃圾邮件。';
    $('#email-code').focus(); toast('验证码已发送，10 分钟内有效');
  } catch (error) {
    if (error.retryAfter > 0) startCodeCountdown(email, error.retryAfter);
    $('#email-code-hint').textContent = error.message;
    toast(error.message, true);
  } finally { codeSending = false; $('#username').disabled = false; updateCodeButton(); }
});

function renderReferences() {
  $('#reference-list').replaceChildren(...state.references.map((reference, index) => {
    const container = document.createElement('div'); container.className = 'reference-item';
    const image = document.createElement('img'); image.src = reference.url; image.alt = `参考图 ${index + 1}`;
    const number = document.createElement('span'); number.className = 'reference-number'; number.textContent = index + 1;
    const name = document.createElement('small'); name.textContent = reference.file.name || '粘贴的图片';
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label', `移除参考图 ${index + 1}`);
    remove.addEventListener('click', () => { URL.revokeObjectURL(reference.url); state.references = state.references.filter((item) => item.id !== reference.id); renderReferences(); });
    container.append(image, number, name, remove); return container;
  }));
  $('#reference-count').textContent = `${state.references.length} / 8`;
  $('#clear-references').classList.toggle('is-hidden', !state.references.length);
  $('#generation-mode').textContent = state.references.length ? `图生图 · ${state.references.length} 张参考图` : '文字生图';
}
function clearReferences() { state.references.forEach((item) => URL.revokeObjectURL(item.url)); state.references = []; $('#reference-image').value = ''; renderReferences(); }
async function addReferences(files) {
  if (state.busy) return toast('请等待当前生成完成后再修改参考图');
  const incoming = Array.from(files).filter((file) => file.size > 0);
  if (!incoming.length) return;
  if (state.references.length + incoming.length > 8) return toast('最多添加 8 张参考图，请先移除部分图片', true);
  if (incoming.some((file) => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type))) return toast('参考图仅支持 PNG、JPG 和 WebP', true);
  const total = [...state.references.map((item) => item.file), ...incoming].reduce((sum, file) => sum + file.size, 0);
  if (total > 50 * 1024 * 1024) return toast('参考图总大小不能超过 50MB', true);
  state.references.push(...incoming.map((file) => ({ id: crypto.randomUUID(), file, url: URL.createObjectURL(file) })));
  renderReferences(); toast(`已添加 ${incoming.length} 张参考图`);
}
$('#reference-dropzone').addEventListener('click', () => { if (!state.busy) $('#reference-image').click(); });
$('#reference-dropzone').addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (!state.busy) $('#reference-image').click(); } });
$('#reference-image').addEventListener('change', async (event) => { await addReferences(event.target.files); event.target.value = ''; });
$('#clear-references').addEventListener('click', clearReferences);
const dropzone = $('#reference-dropzone');
['dragenter', 'dragover'].forEach((type) => dropzone.addEventListener(type, (event) => { event.preventDefault(); dropzone.classList.add('dragging'); }));
['dragleave', 'drop'].forEach((type) => dropzone.addEventListener(type, (event) => { event.preventDefault(); dropzone.classList.remove('dragging'); }));
dropzone.addEventListener('drop', (event) => addReferences(event.dataTransfer.files));
document.addEventListener('paste', (event) => {
  if (!state.user || $('#studio-panel').classList.contains('is-hidden')) return;
  const files = Array.from(event.clipboardData?.items || []).filter((item) => item.kind === 'file' && item.type.startsWith('image/')).map((item) => item.getAsFile()).filter(Boolean);
  if (files.length) { event.preventDefault(); addReferences(files); }
});
$('#paste-reference').addEventListener('click', async () => {
  if (!navigator.clipboard?.read) return toast('请复制图片后，按 Ctrl + V（Mac：⌘ + V）粘贴');
  try {
    const items = await navigator.clipboard.read(); const files = [];
    for (const item of items) {
      const type = item.types.find((value) => ['image/png', 'image/jpeg', 'image/webp'].includes(value));
      if (type) { const blob = await item.getType(type); files.push(new File([blob], `clipboard-${files.length + 1}.${type.split('/')[1]}`, { type })); }
    }
    if (!files.length) return toast('剪贴板中没有图片，请先复制图片');
    await addReferences(files);
  } catch { toast('可以直接按 Ctrl + V（Mac：⌘ + V）粘贴图片'); }
});
function asDataUrl(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('图片读取失败，请重新添加')); reader.readAsDataURL(file); }); }
function composePrompt() {
  const prefix = $('#prompt-prefix').value.trim();
  const style = promptStyles[$('#prompt-style').value] || '';
  const description = $('#prompt').value.trim();
  const avoid = $('#prompt-avoid').value.trim();
  return [prefix, style, description, avoid ? `请尽量避免：${avoid}` : ''].filter(Boolean).join('\n');
}
function updatePromptPreview() {
  const prompt = composePrompt();
  $('#prompt-count').textContent = `${$('#prompt').value.length} / 4000`;
  $('#prompt-final').textContent = prompt || '输入画面描述后，可在这里确认完整提示词。';
  $('#prompt-final-count').textContent = `${prompt.length} / 4000`;
  $('#prompt-final-count').classList.toggle('over-limit', prompt.length > 4000);
  $('#prompt-final-error').classList.toggle('is-hidden', prompt.length <= 4000);
}
function clearPromptModifiers() {
  $('#prompt-prefix').value = '';
  $('#prompt-style').value = 'none';
  $('#prompt-avoid').value = '';
  updatePromptPreview();
}
['#prompt', '#prompt-prefix', '#prompt-avoid'].forEach((selector) => $(selector).addEventListener('input', updatePromptPreview));
$('#prompt-style').addEventListener('change', updatePromptPreview);
updatePromptPreview();
$('#prompt').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    if (!state.busy) $('#generation-form').requestSubmit();
  }
});
document.querySelectorAll('[data-idea]').forEach((button) => button.addEventListener('click', () => { $('#prompt').value = ideas[button.dataset.idea]; $('#prompt').dispatchEvent(new Event('input')); $('#prompt').focus(); }));

function showResult(item) {
  const src = imageSource(item.image_src); if (!src) return;
  state.activeImageItem = item;
  $('#generation-empty').classList.add('is-hidden'); $('#generation-result').classList.remove('is-hidden');
  $('#result-image').src = src; $('#result-full').href = src; $('#download-result').href = src;
  $('#result-prompt').textContent = item.prompt;
  $('#share-result').href = '/?compose=1&image=' + encodeURIComponent(item.id);
  $('#result-meta').replaceChildren(...[item.mode === 'image-to-image' ? `图生图 · ${item.reference_count || 1} 张参考图` : '文生图', item.size, `${item.credits_cost ?? 1} 积分`].map((value) => { const el = document.createElement('span'); el.textContent = value; return el; }));
}
$('#zoom-result').addEventListener('click', () => {
  const source = imageSource(state.activeImageItem?.image_src);
  if (!source) return;
  $('#zoom-image').src = source;
  $('#image-zoom-dialog').showModal();
});
$('#close-image-zoom').addEventListener('click', () => $('#image-zoom-dialog').close());
$('#image-zoom-dialog').addEventListener('click', (event) => { if (event.target === $('#image-zoom-dialog')) $('#image-zoom-dialog').close(); });
$('#use-result-reference').addEventListener('click', async () => {
  const item = state.activeImageItem;
  if (!item || state.busy) return;
  const source = imageSource(item.image_src);
  if (!source) return;
  const button = $('#use-result-reference'); button.disabled = true;
  try {
    const response = await fetch(source, { credentials: 'same-origin' });
    if (!response.ok) throw new Error('暂时无法读取这张作品');
    const blob = await response.blob();
    const type = ['image/png', 'image/jpeg', 'image/webp'].includes(blob.type) ? blob.type : 'image/png';
    const before = state.references.length;
    await addReferences([new File([blob], `作品参考图.${type.split('/')[1]}`, { type })]);
    if (state.references.length === before) return;
    if (!$('#prompt').value.trim()) {
      clearPromptModifiers();
      $('#prompt').value = item.prompt || '';
      updatePromptPreview();
    }
    $('#prompt').focus();
    $('.composer').scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  } catch (error) { toast(error.message || '无法将作品加入参考图', true); }
  finally { button.disabled = false; }
});
function renderHistory() {
  $('#history-grid').replaceChildren(...state.history.filter((item) => imageSource(item.image_src)).map((item) => {
    const card = document.createElement('article'); card.className = 'history-card';
    card.classList.toggle('selected', state.activeImageItem?.id === item.id);
    const view = document.createElement('button'); view.type = 'button'; view.className = 'history-open'; view.setAttribute('aria-label', '查看作品：' + item.prompt);
    const image = document.createElement('img'); image.src = imageSource(item.image_src); image.alt = item.prompt; image.loading = 'lazy'; view.append(image);
    view.addEventListener('click', () => { showResult(item); renderHistory(); $('#generation-result').scrollIntoView?.({ behavior: 'smooth', block: 'nearest' }); });
    const info = document.createElement('div'); info.className = 'history-info';
    const prompt = document.createElement('p'); prompt.textContent = item.prompt;
    const meta = document.createElement('small'); meta.textContent = `${item.mode === 'image-to-image' ? '图生图' : '文生图'} · ${date(item.created_at)}`;
    const share = document.createElement('a'); share.className = 'history-share'; share.href = '/?compose=1&image=' + encodeURIComponent(item.id); share.textContent = '分享到社区 ↗'; info.append(prompt, meta, share);
    const remove = document.createElement('button'); remove.className = 'delete-history'; remove.textContent = '×'; remove.title = '删除作品';
    remove.addEventListener('click', async () => {
      if (!confirm('删除这张作品？删除后无法恢复，已消耗积分不会退回。')) return;
      try {
        await api(`/api/images/history/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
        state.history = state.history.filter((value) => value.id !== item.id);
        if (state.activeImageItem?.id === item.id) {
          state.activeImageItem = null;
          $('#generation-result').classList.add('is-hidden');
          $('#generation-empty').classList.remove('is-hidden');
        }
        renderHistory(); toast('作品已删除');
      } catch (error) { toast(error.message, true); }
    }); card.append(view, info, remove); return card;
  }));
  $('#history-count').textContent = `${state.history.length} 件作品`; $('#history-empty').classList.toggle('is-hidden', !!state.history.length);
}
async function loadHistory() { const data = await api('/api/images/history'); state.history = data.items; renderHistory(); }
$('#generation-form').addEventListener('submit', async (event) => {
  event.preventDefault(); if (state.busy) return;
  if (!$('#prompt').value.trim()) return toast('请填写画面描述', true);
  const prompt = composePrompt();
  if (prompt.length > 4000) { $('#prompt-settings').open = true; toast('完整提示词超过 4000 字，请缩短前置词、描述或避免元素', true); return; }
  if (state.user.credits < 1) { toast('积分不足，请先前往积分中心兑换充值', true); return; }
  state.busy = true; const button = $('#generate-button'); button.disabled = true; button.textContent = '✦ 正在创作，请稍候…';
  $('#generation-status').textContent = '已预留 1 积分。生成需要一些时间，请保持页面打开。';
  $('#generation-empty').classList.add('generating');
  try {
    const references = await Promise.all(state.references.map((item) => asDataUrl(item.file)));
    const body = { prompt, model: 'gpt-image-2', size: $('#size').value, quality: $('#quality').value };
    if (references.length) body.image_data_urls = references;
    const data = await api(`/api/images/${references.length ? 'edits' : 'generations'}`, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(body) });
    setBalance(data.credits); showResult(data.item); state.history.unshift(data.item); renderHistory();
    $('#generation-status').textContent = '作品已完成，本次消耗 1 积分。'; toast('新作品已保存，消耗 1 积分');
  } catch (error) { $('#generation-status').textContent = '生成未完成，请查看积分余额和明细后重试。'; toast(error.message, true); }
  finally { state.busy = false; button.disabled = false; button.textContent = '✦ 开始生成'; $('#generation-empty').classList.remove('generating'); try { await refreshUser(); } catch {} }
});

async function loadWallet() {
  const data = await api('/api/wallet'); setBalance(data.credits);
  $('#ledger-table').replaceChildren(...data.ledger.map((item) => { const tr = document.createElement('tr'); tr.append(cell(date(item.created_at)), cell(item.description), cell(`${item.amount > 0 ? '+' : ''}${item.amount}`, item.amount > 0 ? 'positive' : 'negative'), cell(item.balance_after)); return tr; }));
  $('#ledger-empty').classList.toggle('is-hidden', !!data.ledger.length);
}
$('#redeem-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = event.target.querySelector('button'); button.disabled = true;
  try { const data = await api('/api/wallet/redeem', { method: 'POST', body: JSON.stringify({ code: $('#redeem-code').value.trim() }) }); setBalance(data.credits); $('#redeem-code').value = ''; await loadWallet(); toast(`充值成功，到账 ${data.added} 积分`); }
  catch (error) { toast(error.message, true); } finally { button.disabled = false; }
});

$('#logout-button').addEventListener('click', async () => { if (state.busy) return toast('请等待生成结束后再退出'); try { await api('/api/auth/logout', { method: 'POST' }); clearReferences(); location.assign('/login'); } catch (error) { toast(error.message, true); } });

(async function initialize() {
  try {
    const data = await api('/api/auth/me'); state.user = data.user;
    if (['/login', '/register'].includes(location.pathname)) return location.replace(authDestination());
    const displayName = state.user.display_name || state.user.username;
    $('#user-name').textContent = displayName; $('#user-avatar').textContent = displayName[0].toUpperCase(); setBalance(state.user.credits);
    $('#admin-link').classList.toggle('is-hidden', !state.user.is_admin);
    const route = location.pathname === '/wallet' ? 'wallet' : 'studio';
    ['studio', 'wallet'].forEach((name) => $(`#${name}-panel`).classList.toggle('is-hidden', name !== route));
    document.querySelector(`[data-route="${route}"]`).classList.add('active');
    $('#app-view').classList.remove('is-hidden'); $('#loading-view').classList.add('is-hidden');
    if (route === 'studio') {
      await loadHistory();
      try {
        const draft = JSON.parse(sessionStorage.getItem('astrogate.community-draft') || 'null');
        if (draft && typeof draft.prompt === 'string' && draft.prompt.length <= 4000) {
          clearPromptModifiers();
          $('#prompt').value = draft.prompt;
          if (Array.from($('#size').options).some((option) => option.value === draft.size)) $('#size').value = draft.size;
          $('#prompt').dispatchEvent(new Event('input')); toast('参考提示词已带入，调整参数后即可创作');
        }
        sessionStorage.removeItem('astrogate.community-draft');
      } catch {}
      const idea = new URLSearchParams(location.search).get('idea'); if (ideas[idea]) { $('#prompt').value = ideas[idea]; $('#prompt').dispatchEvent(new Event('input')); }
      if (location.hash === '#history') $('#history').scrollIntoView();
    }
    if (route === 'wallet') await loadWallet();
  } catch (error) {
    if (error.status === 401) { $('#loading-view').classList.add('is-hidden'); $('#auth-view').classList.remove('is-hidden'); authMode(location.pathname === '/register' ? 'register' : 'login'); }
    else { $('#loading-view span').textContent = '暂时无法加载，请刷新页面重试。'; toast(error.message, true); }
  }
})();
