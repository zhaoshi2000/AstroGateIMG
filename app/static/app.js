const $ = (selector) => document.querySelector(selector);
const state = { user: null, mode: 'login', references: [], history: [], activeImageItem: null, busy: false, reading: false,
  models: [{ id: 'gpt-image-2', label: 'GPT Image 2', available: true, credits_cost: 1, price_rmb: '0.05' },
    { id: 'gpt-image-2.5-flare', label: 'GPT Image 2.5 Flare', available: false, credits_cost: 5, price_rmb: '0.25' },
    { id: 'gpt-image-2.5-sunburst', label: 'GPT Image 2.5 Sunburst', available: false, credits_cost: 5, price_rmb: '0.25' }] };
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
const tagCategories = [
  ['scene', '场景'], ['character', '人物'], ['style', '风格'], ['commercial', '商业'], ['science', '科研'],
];
const defaultTags = {
  scene: ['云海古城', '樱花街道', '雨夜都市', '月光森林', '海边日落', '雪山晨曦', '星际港口', '水下宫殿', '沙漠绿洲', '空中花园', '未来地铁', '山间茶屋'],
  character: ['银发魔法师', '都市旅人', '花间少女', '机甲骑士', '猫耳店员', '森林精灵', '宇航员', '古风剑客', '赛博侦探', '海洋守护者', '龙族少年', '舞台歌手'],
  style: ['电影感摄影', '精致动漫', '水彩绘本', '油画笔触', '黑白漫画', '黏土定格', '像素艺术', '复古海报', '极简设计', '梦幻插画', '赛博朋克', '蒸汽波'],
  commercial: ['香水广告', '珠宝特写', '咖啡包装', '甜品陈列', '家具大片', '运动鞋海报', '护肤品静物', '潮玩展示', '汽车宣传', '时尚杂志', '花艺品牌', '耳机产品'],
  science: ['纳米药物', '细胞成像', '蛋白结构', '宇宙星云', '气候地图', '植物解剖', '深海生态', '量子芯片', '人体骨骼', '太阳能电池', '脑神经网络', '材料晶格'],
};
let activeTagCategory = 'scene';
let tagRequestId = 0;
const tagCache = new Map(Object.entries(defaultTags).map(([category, tags]) => [category, { tags, source: 'default' }]));
const promptStyles = {
  none: '',
  anime: '动漫插画风格，细腻线条，柔和光影。',
  watercolor: '水彩绘本风格，轻盈的色彩晕染与手绘纸张肌理。',
  oil: '古典油画风格，厚实笔触，丰富的明暗与颜料质感。',
  ink: '国风水墨画风格，浓淡墨色与自然留白，笔触流动。',
  gongbi: '工笔细画风格，精细线描与典雅设色，细节清晰。',
  comic: '黑白漫画风格，鲜明线稿与网点阴影，分镜感构图。',
  gouache: '厚涂数字插画风格，层次丰富的笔触与柔和体积光。',
  pencil: '彩铅手绘风格，细腻排线与纸张纹理，温柔配色。',
  pixel: '像素艺术风格，清晰像素边缘与有限色板。',
  photo: '写实摄影风格，自然材质与真实光线。',
  cinematic: '电影剧照风格，戏剧性布光与景深，叙事感构图。',
  film: '复古胶片摄影风格，细腻颗粒与柔和色彩，真实光影。',
  portrait: '人像写真风格，人物神态自然，柔和布光与细腻肤色。',
  product: '商业产品摄影风格，干净背景，精确布光与材质细节。',
  macro: '微距摄影风格，主体细节锐利，浅景深与柔和背景。',
  architecture: '建筑摄影风格，准确透视与空间尺度，自然光线。',
  minimal: '极简视觉设计，明确视觉中心、克制配色与充足留白。',
  poster: '复古海报设计风格，醒目的图形构成与印刷质感。',
  render3d: '精致三维渲染风格，可信材质与柔和全局光照。',
  isometric: '等距视角插画风格，整齐结构、清晰层级与精巧细节。',
  paper: '纸雕艺术风格，分层纸张边缘与柔和投影。',
  clay: '黏土定格动画风格，手工塑形质感与温暖布光。',
  fantasy: '奇幻插画风格，丰富层次与史诗氛围。',
  cyber: '赛博朋克风格，霓虹光影与未来都市氛围。',
  steampunk: '蒸汽朋克风格，黄铜机械细节与维多利亚时代氛围。',
  scifi: '科幻概念设计风格，可信的未来科技结构与电影感光影。',
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
function selectedImageModel() {
  return state.models.find((item) => item.id === $('#image-model').value) || state.models[0];
}
function updateModelPrice() {
  const model = selectedImageModel();
  $('#generation-cost').textContent = `${model.credits_cost} 积分 / 张 · ¥${model.price_rmb}`;
  $('#current-model-label').textContent = `当前模型 · ${model.label}`;
  $('#model-availability').textContent = model.available
    ? `已开放 · ${model.credits_cost} 积分 / 张 · ¥${model.price_rmb}`
    : `${model.label} 暂不可用，请选择已开放模型。`;
  $('#generate-button').disabled = !model.available || state.busy;
}
function renderImageModels() {
  const select = $('#image-model');
  const previous = select.value;
  select.replaceChildren(...state.models.map((item) => {
    const option = document.createElement('option');
    option.value = item.id;
    option.disabled = !item.available;
    option.textContent = `${item.label} · ${item.available ? `${item.credits_cost} 积分 / 张` : '暂未开放'}`;
    return option;
  }));
  select.value = state.models.some((item) => item.id === previous && item.available) ? previous :
    (state.models.find((item) => item.available)?.id || 'gpt-image-2');
  updateModelPrice();
}
async function loadImageModels() {
  try {
    const result = await api('/api/images/models');
    if (Array.isArray(result.items) && result.items.length) state.models = state.models.map((fallback) => {
      const item = result.items.find((candidate) => candidate.id === fallback.id);
      return item && Number.isInteger(item.credits_cost) && item.credits_cost === fallback.credits_cost
        ? { ...fallback, ...item, available: item.available === true } : { ...fallback, available: false };
    });
  } catch { state.models = state.models.map((item) => ({ ...item, available: item.id === 'gpt-image-2' })); }
  renderImageModels();
}
$('#image-model').addEventListener('change', updateModelPrice);
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
  const previewItems = () => state.references.map((reference, index) => {
    const container = document.createElement('div'); container.className = 'reference-item';
    const image = document.createElement('img'); image.src = reference.url; image.alt = `参考图 ${index + 1}`;
    const number = document.createElement('span'); number.className = 'reference-number'; number.textContent = index + 1;
    const name = document.createElement('small'); name.textContent = reference.file.name || '粘贴的图片';
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label', `移除参考图 ${index + 1}`);
    remove.addEventListener('click', () => { URL.revokeObjectURL(reference.url); state.references = state.references.filter((item) => item.id !== reference.id); renderReferences(); });
    container.append(image, number, name, remove); return container;
  });
  $('#reference-list').replaceChildren(...previewItems());
  $('#reference-dialog-list').replaceChildren(...previewItems());
  $('#reference-count').textContent = `${state.references.length} / 8`;
  $('#reference-dialog-count').textContent = `${state.references.length} / 8 张`;
  $('#reference-dialog-done').textContent = state.references.length ? `完成，使用 ${state.references.length} 张参考图` : '完成，稍后添加';
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
function openReferenceDialog() {
  if (state.busy) return toast('请等待当前生成完成后再修改参考图');
  const dialog = $('#reference-dialog');
  if (!dialog.open) dialog.showModal();
  renderReferences();
  $('#reference-paste-zone').focus();
}
function closeReferenceDialog() { $('#reference-dialog').close(); }
$('#reference-dropzone').addEventListener('click', openReferenceDialog);
$('#reference-dropzone').addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openReferenceDialog(); } });
$('#reference-image').addEventListener('change', async (event) => { await addReferences(event.target.files); event.target.value = ''; });
$('#clear-references').addEventListener('click', clearReferences);
$('#reference-dialog-clear').addEventListener('click', clearReferences);
$('#reference-dialog-close').addEventListener('click', closeReferenceDialog);
$('#reference-dialog-done').addEventListener('click', closeReferenceDialog);
$('#reference-dialog-upload').addEventListener('click', () => $('#reference-image').click());
$('#reference-paste-zone').addEventListener('click', () => $('#reference-paste-zone').focus());
$('#reference-dialog').addEventListener('click', (event) => { if (event.target === $('#reference-dialog')) closeReferenceDialog(); });
const dropzone = $('#reference-dropzone');
['dragenter', 'dragover'].forEach((type) => dropzone.addEventListener(type, (event) => { event.preventDefault(); dropzone.classList.add('dragging'); }));
['dragleave', 'drop'].forEach((type) => dropzone.addEventListener(type, (event) => { event.preventDefault(); dropzone.classList.remove('dragging'); }));
dropzone.addEventListener('drop', (event) => addReferences(event.dataTransfer.files));
const pasteZone = $('#reference-paste-zone');
['dragenter', 'dragover'].forEach((type) => pasteZone.addEventListener(type, (event) => { event.preventDefault(); pasteZone.classList.add('dragging'); }));
['dragleave', 'drop'].forEach((type) => pasteZone.addEventListener(type, (event) => { event.preventDefault(); pasteZone.classList.remove('dragging'); }));
pasteZone.addEventListener('drop', (event) => addReferences(event.dataTransfer.files));
document.addEventListener('paste', (event) => {
  if (!state.user || $('#studio-panel').classList.contains('is-hidden')) return;
  const files = Array.from(event.clipboardData?.items || []).filter((item) => item.kind === 'file' && item.type.startsWith('image/')).map((item) => item.getAsFile()).filter(Boolean);
  if (files.length) { event.preventDefault(); addReferences(files); }
  else if ($('#reference-dialog').open) { event.preventDefault(); toast('剪贴板里没有图片，请先复制图片', true); }
});
$('#paste-reference').addEventListener('click', openReferenceDialog);
$('#reference-dialog-read').addEventListener('click', async () => {
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
  const modifiers = [$('#prompt-prefix').value.trim() ? '前置词已启用' : '无前置词', $('#prompt-avoid').value.trim() ? '避免元素已设置' : '可设置避免元素'];
  $('#prompt-settings-state').textContent = modifiers.join(' · ');
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
function renderTagCategories() {
  $('#tag-categories').replaceChildren(...tagCategories.map(([id, label]) => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
    button.className = id === activeTagCategory ? 'active' : '';
    button.setAttribute('aria-pressed', String(id === activeTagCategory));
    button.addEventListener('click', () => {
      if (id === activeTagCategory) return;
      activeTagCategory = id; renderTagCategories();
      const cached = tagCache.get(id);
      renderPromptTags(cached.tags);
      $('#tag-hint').textContent = cached.source === 'ai' ? '这组标签由 AI 生成；点击可把完整提示词写入上方。' : '默认标签已就绪；点“换一批”可让 AI 生成新标签。';
    });
    return button;
  }));
}
function renderPromptTags(tags) {
  $('#inspiration-chips').replaceChildren(...tags.map((tag) => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = tag;
    button.addEventListener('click', async () => {
      button.disabled = true; button.classList.add('is-loading');
      $('#tag-hint').textContent = `正在为“${tag}”构思提示词…`;
      try {
        const category = tagCategories.find(([id]) => id === activeTagCategory)?.[1] || '场景';
        const result = await api('/api/prompts/assist', { method: 'POST', body: JSON.stringify({ idea: `${category}主题：${tag}` }) });
        $('#prompt').value = result.prompt;
        $('#prompt').dispatchEvent(new Event('input'));
        $('#prompt').focus();
        $('#assist-suggestion').classList.add('is-hidden');
        $('#tag-hint').textContent = result.source === 'fallback' ? '文字节点暂不可用，已将快速补全写入画面描述，可继续编辑。' : 'AI 提示词已写入画面描述，可继续编辑。';
      } catch (error) { $('#tag-hint').textContent = error.message; toast(error.message, true); }
      finally { button.disabled = false; button.classList.remove('is-loading'); }
    });
    return button;
  }));
}
async function loadPromptTags(category) {
  const requestId = ++tagRequestId;
  const previous = tagCache.get(category)?.tags || [];
  $('#refresh-tags').disabled = true; $('#refresh-tags').classList.add('is-loading');
  $('#tag-hint').textContent = `正在准备${tagCategories.find(([id]) => id === category)?.[1] || ''}灵感标签…`;
  try {
    const result = await api('/api/prompts/tags', { method: 'POST', body: JSON.stringify({ category, exclude: previous }) });
    if (!Array.isArray(result.tags) || result.tags.length < 10) throw new Error('标签暂时无法更新');
    tagCache.set(category, result);
    if (requestId === tagRequestId && category === activeTagCategory) {
      renderPromptTags(result.tags);
      $('#tag-hint').textContent = result.source === 'fallback' ? '文字节点暂不可用，现显示精选标签；点击后仍可快速补全提示词。' : '这组标签由 AI 生成；点击可把完整提示词写入上方。';
    }
  } catch (error) { if (requestId === tagRequestId) $('#tag-hint').textContent = `标签暂时无法更新：${error.message}`; }
  finally { if (requestId === tagRequestId) { $('#refresh-tags').disabled = false; $('#refresh-tags').classList.remove('is-loading'); } }
}
$('#refresh-tags').addEventListener('click', () => loadPromptTags(activeTagCategory));
renderTagCategories();
renderPromptTags(defaultTags.scene);

$('#assist-prompt').addEventListener('click', async () => {
  const idea = $('#prompt').value.trim();
  if (idea.length < 2) return toast('先写下至少两个字的画面想法', true);
  if (idea.length > 1200) return toast('AI 帮写支持 1200 字以内的想法，请先精简描述', true);
  const button = $('#assist-prompt'); button.disabled = true; button.classList.add('is-loading'); button.textContent = '正在帮你构思…';
  $('#assist-hint').textContent = '正在生成提示词建议，请稍候。';
  try {
    const result = await api('/api/prompts/assist', { method: 'POST', body: JSON.stringify({ idea }) });
    $('#assist-suggestion-text').textContent = result.prompt;
    $('#assist-suggestion').classList.remove('is-hidden');
    $('#assist-hint').textContent = result.source === 'fallback' ? '文字节点暂不可用，已生成快速补全建议；采用后可继续修改。' : 'AI 建议已生成，采用后仍能继续修改。';
  } catch (error) { $('#assist-hint').textContent = error.message; toast(error.message, true); }
  finally { button.disabled = false; button.classList.remove('is-loading'); button.textContent = '✧ AI 帮写提示词'; }
});
$('#apply-assist').addEventListener('click', () => {
  const suggestion = $('#assist-suggestion-text').textContent.trim();
  if (!suggestion) return;
  $('#prompt').value = suggestion;
  $('#prompt').dispatchEvent(new Event('input'));
  $('#assist-suggestion').classList.add('is-hidden');
  $('#assist-hint').textContent = '建议已带入画面描述，可继续编辑后生成。';
  $('#prompt').focus();
});
$('#dismiss-assist').addEventListener('click', () => $('#assist-suggestion').classList.add('is-hidden'));

function showResult(item) {
  const src = imageSource(item.image_src); if (!src) return;
  state.activeImageItem = item;
  $('#generation-empty').classList.add('is-hidden'); $('#generation-result').classList.remove('is-hidden');
  $('#result-image').src = src; $('#result-full').href = src; $('#download-result').href = src;
  $('#result-prompt').textContent = item.prompt;
  $('#share-result').href = '/?compose=1&image=' + encodeURIComponent(item.id);
  const modelName = state.models.find((model) => model.id === item.model)?.label || item.model || 'GPT Image 2';
  $('#result-meta').replaceChildren(...[item.mode === 'image-to-image' ? `图生图 · ${item.reference_count || 1} 张参考图` : '文生图', modelName, item.size, `${item.credits_cost ?? 1} 积分`].map((value) => { const el = document.createElement('span'); el.textContent = value; return el; }));
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
  const model = selectedImageModel();
  if (!model.available) return toast('该模型尚未在图片节点开放，请选择可用模型', true);
  if (state.user.credits < model.credits_cost) { toast(`积分不足；${model.label} 每张需要 ${model.credits_cost} 积分`, true); return; }
  state.busy = true; const button = $('#generate-button'); button.disabled = true; button.classList.add('is-loading'); button.textContent = '正在创作，请稍候…';
  $('#generation-progress').classList.remove('is-hidden');
  $('#generation-status').textContent = `已预留 ${model.credits_cost} 积分。生成需要一些时间，请保持页面打开。`;
  $('#generation-empty').classList.add('generating');
  try {
    const references = await Promise.all(state.references.map((item) => asDataUrl(item.file)));
    const body = { prompt, model: model.id, size: $('#size').value, quality: $('#quality').value };
    if (references.length) body.image_data_urls = references;
    const data = await api(`/api/images/${references.length ? 'edits' : 'generations'}`, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(body) });
    setBalance(data.credits); showResult(data.item); state.history.unshift(data.item); renderHistory();
    const spent = data.item.credits_cost ?? model.credits_cost;
    $('#generation-status').textContent = `作品已完成，本次消耗 ${spent} 积分。`; toast(`新作品已保存，消耗 ${spent} 积分`);
  } catch (error) { $('#generation-status').textContent = '生成未完成，请查看积分余额和明细后重试。'; toast(error.message, true); }
  finally { state.busy = false; updateModelPrice(); button.classList.remove('is-loading'); button.textContent = '✦ 生成图像'; $('#generation-progress').classList.add('is-hidden'); $('#generation-empty').classList.remove('generating'); try { await refreshUser(); } catch {} }
});

async function loadWallet() {
  const data = await api('/api/wallet'); setBalance(data.credits);
  $('#ledger-table').replaceChildren(...data.ledger.map((item) => { const tr = document.createElement('tr'); tr.append(cell(date(item.created_at)), cell(item.description), cell(`${item.amount > 0 ? '+' : ''}${item.amount}`, item.amount > 0 ? 'positive' : 'negative'), cell(item.balance_after)); return tr; }));
  $('#ledger-empty').classList.toggle('is-hidden', !!data.ledger.length);
  document.querySelectorAll('[data-payment-tier]').forEach((button) => { button.disabled = !data.payment_enabled; });
  if (!data.payment_enabled) $('#payment-status').textContent = '支付宝充值暂未开放，请使用兑换码充值。';
  $('#payment-orders').replaceChildren(...(data.payment_orders || []).map((item) => {
    const row = document.createElement('div'); row.className = 'payment-order';
    const summary = document.createElement('span'); summary.textContent = `¥${(item.amount_cents / 100).toFixed(2)} · ${item.credits} 积分 · ${date(item.created_at)}`;
    const status = document.createElement('b'); status.className = item.status === 'paid' ? 'positive' : 'pending';
    status.textContent = item.status === 'paid' ? '已到账' : '待支付'; row.append(summary, status); return row;
  }));
  if (!data.payment_orders?.length) $('#payment-orders').textContent = '暂无充值订单';
}
document.querySelectorAll('[data-payment-tier]').forEach((button) => button.addEventListener('click', async () => {
  button.disabled = true; $('#payment-status').textContent = '正在创建支付宝订单…';
  try {
    const order = await api('/api/payments/orders', { method: 'POST', body: JSON.stringify({ tier: Number(button.dataset.paymentTier) }) });
    const form = document.createElement('form'); form.method = 'POST'; form.action = order.action_url;
    for (const [name, value] of Object.entries(order.fields)) {
      const input = document.createElement('input'); input.type = 'hidden'; input.name = name; input.value = value; form.append(input);
    }
    document.body.append(form); $('#payment-status').textContent = '正在跳转支付宝收银台…'; form.submit();
  } catch (error) { $('#payment-status').textContent = '创建订单失败，请稍后重试。'; toast(error.message, true); button.disabled = false; }
}));
$('#refresh-payments').addEventListener('click', async () => {
  try { await loadWallet(); toast('订单状态已刷新'); } catch (error) { toast(error.message, true); }
});
async function watchReturnedPayment() {
  const orderId = new URLSearchParams(location.search).get('order');
  if (!/^AGI[0-9A-F]{24}$/.test(orderId || '')) return;
  for (let attempt = 0; attempt < 12; attempt++) {
    try {
      const order = await api(`/api/payments/orders/${orderId}`);
      if (order.status === 'paid') { $('#payment-status').textContent = `${order.credits} 积分已到账。`; await loadWallet(); toast(`充值成功，到账 ${order.credits} 积分`); return; }
      $('#payment-status').textContent = '等待支付宝确认支付，确认后积分会自动到账。';
    } catch (error) { $('#payment-status').textContent = error.message; return; }
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  $('#payment-status').textContent = '尚未收到支付确认。可点击“刷新到账状态”再次查询。';
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
      await loadImageModels();
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
    if (route === 'wallet') { await loadWallet(); void watchReturnedPayment(); }
  } catch (error) {
    if (error.status === 401) { $('#loading-view').classList.add('is-hidden'); $('#auth-view').classList.remove('is-hidden'); authMode(location.pathname === '/register' ? 'register' : 'login'); }
    else { $('#loading-view span').textContent = '暂时无法加载，请刷新页面重试。'; toast(error.message, true); }
  }
})();
