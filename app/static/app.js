const $ = (selector) => document.querySelector(selector);
const state = { user: null, mode: 'login', references: [], history: [], busy: false, reading: false };
const ideas = {
  product: '一瓶透明玻璃香水置于浅紫色石台上，晨光从侧面穿过，细腻的玻璃折射，极简产品摄影，干净留白。',
  space: '一座面朝海面的暖色拱门建筑，柔和的午后阳光，天然石材与细腻肌理，地中海风格，建筑摄影。',
  abstract: '漂浮的紫色玻璃球体与金属圆环，柔和光线，浅薰衣草色背景，抽象艺术，精致的材质细节。',
};

class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
  let payload;
  try { payload = await response.json(); } catch { throw new ApiError('服务暂时没有响应，请稍后重试', response.status); }
  if (!response.ok) {
    let detail = payload.detail || payload.error || '请求未完成';
    if (Array.isArray(detail)) detail = detail.map((item) => String(item.msg || '')).join('；').replaceAll('Value error, ', '');
    if (typeof detail !== 'string') detail = detail.message || '请求未完成';
    throw new ApiError(detail, response.status);
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

function authMode(mode) {
  state.mode = mode;
  const registering = mode === 'register';
  $('#login-tab').classList.toggle('active', !registering);
  $('#register-tab').classList.toggle('active', registering);
  $('#login-tab').setAttribute('aria-selected', String(!registering));
  $('#register-tab').setAttribute('aria-selected', String(registering));
  $('#auth-title').textContent = registering ? '为灵感，创建一个空间' : '欢迎回来';
  $('#auth-kicker').textContent = registering ? 'START YOUR CREATIVE JOURNEY' : 'WELCOME BACK';
  $('#auth-subtitle').textContent = registering ? '注册账号，开启你的图像创作。' : '登录后，继续你的创作。';
  $('#auth-submit').textContent = registering ? '创建账号，开始创作 ↗' : '登录工作台 ↗';
  $('#password').autocomplete = registering ? 'new-password' : 'current-password';
}
$('#login-tab').addEventListener('click', () => authMode('login'));
$('#register-tab').addEventListener('click', () => authMode('register'));
$('#auth-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = $('#auth-submit'); button.disabled = true;
  try {
    await api(`/api/auth/${state.mode}`, { method: 'POST', body: JSON.stringify({ username: $('#username').value.trim(), password: $('#password').value }) });
    location.assign('/studio');
  } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
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
$('#prompt').addEventListener('input', () => $('#prompt-count').textContent = `${$('#prompt').value.length} / 4000`);
document.querySelectorAll('[data-idea]').forEach((button) => button.addEventListener('click', () => { $('#prompt').value = ideas[button.dataset.idea]; $('#prompt').dispatchEvent(new Event('input')); $('#prompt').focus(); }));

function showResult(item) {
  const src = imageSource(item.image_src); if (!src) return;
  $('#generation-empty').classList.add('is-hidden'); $('#generation-result').classList.remove('is-hidden');
  $('#result-image').src = src; $('#result-full').href = src; $('#download-result').href = src;
  $('#result-prompt').textContent = item.prompt;
  $('#result-meta').replaceChildren(...[item.mode === 'image-to-image' ? `图生图 · ${item.reference_count || 1} 张参考图` : '文生图', item.size, `${item.credits_cost ?? 1} 积分`].map((value) => { const el = document.createElement('span'); el.textContent = value; return el; }));
}
function renderHistory() {
  $('#history-grid').replaceChildren(...state.history.filter((item) => imageSource(item.image_src)).map((item) => {
    const card = document.createElement('article'); card.className = 'history-card';
    const link = document.createElement('a'); link.href = imageSource(item.image_src); link.target = '_blank'; link.rel = 'noopener';
    const image = document.createElement('img'); image.src = link.href; image.alt = item.prompt; image.loading = 'lazy'; link.append(image);
    const info = document.createElement('div'); info.className = 'history-info';
    const prompt = document.createElement('p'); prompt.textContent = item.prompt;
    const meta = document.createElement('small'); meta.textContent = `${item.mode === 'image-to-image' ? '图生图' : '文生图'} · ${date(item.created_at)}`; info.append(prompt, meta);
    const remove = document.createElement('button'); remove.className = 'delete-history'; remove.textContent = '×'; remove.title = '删除作品';
    remove.addEventListener('click', async () => {
      if (!confirm('删除这张作品？删除后无法恢复，已消耗积分不会退回。')) return;
      try { await api(`/api/images/history/${encodeURIComponent(item.id)}`, { method: 'DELETE' }); state.history = state.history.filter((value) => value.id !== item.id); renderHistory(); toast('作品已删除'); } catch (error) { toast(error.message, true); }
    }); card.append(link, info, remove); return card;
  }));
  $('#history-count').textContent = `${state.history.length} 件作品`; $('#history-empty').classList.toggle('is-hidden', !!state.history.length);
}
async function loadHistory() { const data = await api('/api/images/history'); state.history = data.items; renderHistory(); }
$('#generation-form').addEventListener('submit', async (event) => {
  event.preventDefault(); if (state.busy) return;
  if (state.user.credits < 1) { toast('积分不足，请先前往积分中心兑换充值', true); return; }
  if (!$('#prompt').value.trim()) return toast('请填写画面描述', true);
  state.busy = true; const button = $('#generate-button'); button.disabled = true; button.textContent = '✦ 正在创作，请稍候…';
  $('#generation-status').textContent = '已预留 1 积分。生成需要一些时间，请保持页面打开。';
  $('#generation-empty').classList.add('generating');
  try {
    const references = await Promise.all(state.references.map((item) => asDataUrl(item.file)));
    const body = { prompt: $('#prompt').value.trim(), model: 'gpt-image-2', size: $('#size').value, quality: $('#quality').value };
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

async function loadUsers() {
  const data = await api(`/api/admin/users?q=${encodeURIComponent($('#user-search').value)}`);
  $('#admin-user-count').textContent = data.stats.users; $('#admin-credit-count').textContent = data.stats.credits;
  $('#admin-users-table').replaceChildren(...data.items.map((user) => {
    const tr = document.createElement('tr'); const action = document.createElement('td');
    const form = document.createElement('form'); form.className = 'credit-adjust-form';
    const input = document.createElement('input'); input.type = 'number'; input.placeholder = '+100 / -10'; input.min = -1000000; input.max = 1000000; input.required = true; input.setAttribute('aria-label', `${user.username} 积分调整`);
    const button = document.createElement('button'); button.type = 'submit'; button.className = 'button small secondary'; button.textContent = '调整';
    form.append(input, button); action.append(form);
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); button.disabled = true;
      try { await api(`/api/admin/users/${user.id}/credits`, { method: 'POST', body: JSON.stringify({ amount: Number(input.value), reason: '管理员手动调整' }) }); await loadUsers(); await refreshUser(); toast('积分已调整并记录流水'); }
      catch (error) { toast(error.message, true); } finally { button.disabled = false; }
    });
    tr.append(cell(user.username + (user.is_admin ? ' · 管理员' : '')), cell(date(user.created_at)), cell(user.credits), action); return tr;
  }));
}
async function loadCodes() {
  const data = await api('/api/admin/codes');
  $('#admin-codes-table').replaceChildren(...data.items.map((item) => { const tr = document.createElement('tr'); const status = item.redeemed_username ? `已兑换 · ${item.redeemed_username}` : new Date(item.expires_at) < new Date() ? '已过期' : '未使用'; tr.append(cell(`…${item.suffix}`), cell(item.credits), cell(status)); return tr; }));
}
$('#user-search-form').addEventListener('submit', (event) => { event.preventDefault(); loadUsers().catch((error) => toast(error.message, true)); });
$('#code-create-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = event.target.querySelector('button'); button.disabled = true;
  try { const data = await api('/api/admin/codes', { method: 'POST', body: JSON.stringify({ credits: Number($('#code-credits').value), count: Number($('#code-count').value), expires_days: Number($('#code-days').value) }) }); $('#new-code-text').value = data.codes.join('\n'); $('#new-codes').classList.remove('is-hidden'); await loadCodes(); toast('兑换码已生成，请及时复制保存'); }
  catch (error) { toast(error.message, true); } finally { button.disabled = false; }
});
$('#copy-codes').addEventListener('click', async () => { try { await navigator.clipboard.writeText($('#new-code-text').value); toast('兑换码已复制'); } catch { $('#new-code-text').focus(); $('#new-code-text').select(); toast('请按 Ctrl + C 复制选中的兑换码'); } });
$('#logout-button').addEventListener('click', async () => { if (state.busy) return toast('请等待生成结束后再退出'); try { await api('/api/auth/logout', { method: 'POST' }); clearReferences(); location.assign('/login'); } catch (error) { toast(error.message, true); } });

(async function initialize() {
  try {
    const data = await api('/api/auth/me'); state.user = data.user;
    if (['/login', '/register'].includes(location.pathname)) return location.replace('/studio');
    if (location.pathname === '/admin' && !state.user.is_admin) return location.replace('/studio');
    $('#user-name').textContent = state.user.username; $('#user-avatar').textContent = state.user.username[0].toUpperCase(); setBalance(state.user.credits);
    $('#admin-link').classList.toggle('is-hidden', !state.user.is_admin);
    const route = location.pathname === '/wallet' ? 'wallet' : location.pathname === '/admin' ? 'admin' : 'studio';
    ['studio', 'wallet', 'admin'].forEach((name) => $(`#${name}-panel`).classList.toggle('is-hidden', name !== route));
    document.querySelector(`[data-route="${route}"]`).classList.add('active');
    $('#app-view').classList.remove('is-hidden'); $('#loading-view').classList.add('is-hidden');
    if (route === 'studio') { await loadHistory(); const idea = new URLSearchParams(location.search).get('idea'); if (ideas[idea]) { $('#prompt').value = ideas[idea]; $('#prompt').dispatchEvent(new Event('input')); } if (location.hash === '#history') $('#history').scrollIntoView(); }
    if (route === 'wallet') await loadWallet();
    if (route === 'admin') await Promise.all([loadUsers(), loadCodes()]);
  } catch (error) {
    if (error.status === 401) { $('#loading-view').classList.add('is-hidden'); $('#auth-view').classList.remove('is-hidden'); authMode(location.pathname === '/register' ? 'register' : 'login'); }
    else { $('#loading-view span').textContent = '暂时无法加载，请刷新页面重试。'; toast(error.message, true); }
  }
})();
