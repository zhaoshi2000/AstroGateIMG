(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const state = { posts: [], user: null, category: 'all', selected: null, comments: [], opening: 0, liking: new Set(), mine: false, page: 0, hasMore: false, loading: false, files: [], source: null, publishing: false };
  const icons = {
    heart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/></svg>',
    comment: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z"/></svg>',
  };
  function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
  function action(className, text, attribute, id) { const node = element('button', className, text); node.type = 'button'; if (attribute) node.dataset[attribute] = id; return node; }
  function socialButton(kind, count, attribute, post) {
    const button = action('social-button', '', attribute, post.id);
    button.innerHTML = icons[kind]; // Icons are fixed application markup; all content uses textContent.
    button.append(element('span', '', String(count)));
    button.setAttribute('aria-label', `${kind === 'heart' ? '点赞' : '评论'}：${post.title}`);
    if (kind === 'heart') button.setAttribute('aria-pressed', String(post.liked));
    return button;
  }
  let toastTimer;
  function toast(message, error = false) { const box = $('#toast'); const host = $('#compose-dialog').open ? $('#compose-dialog') : $('#post-dialog').open ? $('#post-dialog') : document.body; if (host && box.parentElement !== host) host.append(box); box.textContent = message; box.classList.toggle('error', error); box.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => box.classList.remove('show'), error ? 6000 : 3500); }
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    let data;
    try { data = await response.json(); } catch { throw new Error('服务暂时未响应，请稍后重试'); }
    if (!response.ok) { let detail = data.detail || '请求未完成'; if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；'); const error = new Error(String(detail)); error.status = response.status; throw error; }
    return data;
  }
  function tags(post) { const container = element('div', 'post-tags'); container.append(...post.tags.map((tag) => element('span', '', tag))); return container; }
  function card(post) {
    const article = element('article', 'gallery-card'); article.dataset.postId = post.id;
    const imageButton = action('gallery-image-button', '', 'openPost', post.id); imageButton.setAttribute('aria-label', `查看作品：${post.title}`);
    if (post.thumbnail) {
      const image = element('img'); image.src = post.thumbnail; image.alt = post.alt; image.width = post.width; image.height = post.height; image.loading = 'lazy'; image.decoding = 'async';
      imageButton.append(image, element('span', 'gallery-image-hint', '展开作品 ↗'));
      if (post.images?.length > 1) imageButton.append(element('span', 'post-image-count', `${post.images.length} 张图片`));
    } else { imageButton.classList.add('text-post-cover'); imageButton.append(element('span', 'text-post-mark', '✦'), element('p', '', post.content)); }
    const body = element('div', 'gallery-card-body'); const title = element('h3', 'gallery-card-title'); title.append(action('gallery-title-button', post.title, 'openPost', post.id));
    const creator = element('div', 'gallery-creator'); creator.append(element('span', 'creator-avatar', Array.from(post.author)[0] || '友'), element('span', '', post.author));
    creator.append(element('span', 'creator-curated', post.is_curated ? '精选' : post.is_mine ? '我的' : '社区'));
    const actions = element('div', 'gallery-card-actions'); actions.append(socialButton('heart', post.likes_count, 'likePost', post), socialButton('comment', post.comments_count, 'commentPost', post), action('social-button gallery-prompt-button', '提示词 ↗', 'openPost', post.id));
    body.append(title, creator, tags(post));
    if (post.content && post.thumbnail) body.append(element('p', 'post-content-preview', post.content));
    body.append(actions);
    if (post.example_likes_count) body.append(element('small', 'card-example-note', `含 ${post.example_likes_count} 示例赞`));
    const preview = action('gallery-comment-preview', '', 'commentPost', post.id); const line = element('span', 'gallery-comment-text');
    if (post.comment_preview) {
      line.append(element('strong', '', post.comment_preview.author + '：'), document.createTextNode(post.comment_preview.content));
    } else line.append(document.createTextNode('还没有评论，来聊聊你的想法 →'));
    preview.append(line);
    article.append(imageButton, body, preview); return article;
  }
  function renderGallery() {
    const keyword = $('#gallery-search').value.trim().toLocaleLowerCase();
    let posts = state.posts.filter((post) => (state.category === 'all' || post.category === state.category) && (!keyword || [post.title, post.content, post.author, post.prompt, ...post.tags].join(' ').toLocaleLowerCase().includes(keyword)));
    if ($('#gallery-sort').value === 'liked') posts = [...posts].sort((a, b) => b.likes_count - a.likes_count);
    if ($('#gallery-sort').value === 'discussed') posts = [...posts].sort((a, b) => b.last_commented_at.localeCompare(a.last_commented_at));
    if ($('#gallery-sort').value === 'newest') posts = [...posts].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    const focusedLike = document.activeElement?.dataset.likePost;
    $('#gallery-grid').replaceChildren(...posts.map(card)); $('#gallery-grid').setAttribute('aria-busy', 'false');
    $('#gallery-empty').classList.toggle('is-hidden', posts.length > 0); $('#gallery-count').textContent = `${posts.length} 件作品`;
    document.querySelectorAll('[data-category]').forEach((button) => { const active = button.dataset.category === state.category; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); const count = button.querySelector?.('span'); if (count) count.textContent = state.posts.filter((post) => button.dataset.category === 'all' || post.category === button.dataset.category).length; });
    $('#my-posts').setAttribute('aria-pressed', String(state.mine)); $('#my-posts').textContent = state.mine ? '返回全部帖子' : '我的帖子';
    $('#load-more-posts').classList.toggle('is-hidden', !state.hasMore); $('#load-more-posts').disabled = state.loading;
    if (focusedLike) document.querySelector(`[data-like-post="${focusedLike}"]`)?.focus({ preventScroll: true });
  }
  function loginURL(id) { return '/login?next=' + encodeURIComponent(id ? '/?post=' + id : '/?compose=1'); }
  function requireUser(id) { if (state.user) return true; location.assign(loginURL(id)); return false; }
  function updatePost(id, patch) { const post = state.posts.find((item) => item.id === id); if (post) Object.assign(post, patch); if (state.selected?.id === id) Object.assign(state.selected, patch); }
  function renderDetailLike() { if (!state.selected) return; const button = socialButton('heart', state.selected.likes_count, null, state.selected); $('#detail-like').replaceChildren(...button.childNodes); $('#detail-like').setAttribute('aria-pressed', String(state.selected.liked)); $('#detail-like').setAttribute('aria-label', `点赞：${state.selected.title}`); $('#detail-like').disabled = state.liking.has(state.selected.id); }
  async function likePost(id) {
    if (!requireUser(id) || state.liking.has(id)) return;
    const post = state.posts.find((item) => item.id === id); if (!post) return;
    state.liking.add(id); renderDetailLike();
    try { const result = await api(`/api/community/posts/${id}/like`, { method: 'POST', body: JSON.stringify({ liked: !post.liked }) }); updatePost(id, result); renderGallery(); }
    catch (error) { if (error.status === 401) { state.user = null; location.assign(loginURL(id)); } else toast(error.message, true); }
    finally { state.liking.delete(id); renderDetailLike(); }
  }
  function formatDate(value) { try { return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value)); } catch { return ''; } }
  function renderComments() {
    $('#detail-comment-count').textContent = state.selected.comments_count;
    $('#detail-comments').replaceChildren(...state.comments.map((comment) => {
      const item = element('article', 'comment-item'); const body = element('div', 'comment-body'); const byline = element('div', 'comment-byline'); byline.append(element('strong', '', comment.author));
      if (comment.created_at) { const time = element('time', '', formatDate(comment.created_at)); time.dateTime = comment.created_at; byline.append(time); }
      body.append(byline, element('p', '', comment.content));
      if (comment.can_delete) body.append(action('delete-comment', '删除评论', 'deleteComment', String(comment.id)));
      item.append(element('span', 'comment-avatar', Array.from(comment.author)[0] || '友'), body); return item;
    }));
    if (!state.comments.length) $('#detail-comments').append(element('p', 'discussion-loading', '还没有评论，成为第一个分享想法的人。'));
    if (state.selected.comments_count > state.comments.length) $('#detail-comments').append(element('p', 'discussion-loading', '目前展示最近 100 条用户评论。'));
  }
  function fillDetail(post) {
    $('#detail-title').textContent = post.title; $('#detail-image').src = post.image || '/assets/mark.svg'; $('#detail-image').alt = post.alt;
    $('#detail-full-image').href = post.image || '#'; $('#detail-original').href = post.image || '#'; $('#detail-author').textContent = post.author;
    $('#detail-image-pane').classList.toggle('is-hidden', !post.image); $('.post-dialog-layout').classList.toggle('text-only', !post.image);
    $('#detail-image-switcher').replaceChildren(...(post.images || []).map((image, index) => { const button = action('image-switch', String(index + 1)); button.setAttribute('aria-label', `查看第 ${index + 1} 张图片`); button.addEventListener('click', () => { $('#detail-image').src = image.image; $('#detail-full-image').href = image.image; $('#detail-original').href = image.image; }); return button; }));
    $('#detail-image-switcher').classList.toggle('is-hidden', (post.images || []).length < 2);
    $('#detail-avatar').textContent = Array.from(post.author)[0] || '友'; $('#detail-curated').classList.toggle('is-hidden', !post.is_curated);
    $('#detail-author-note').textContent = post.is_curated ? '精选作品发布者' : `社区创作者 · ${formatDate(post.created_at)}`;
    $('#detail-content').textContent = post.content || ''; $('#detail-content').classList.toggle('is-hidden', !post.content);
    $('#delete-post').classList.toggle('is-hidden', !post.can_delete); $('#delete-post').disabled = false;
    $('#detail-example-note').textContent = post.example_likes_count ? `这篇精选作品包含预置的展示互动；用户发布的点赞和评论会真实保存。` : '';
    $('#detail-example-note').classList.toggle('is-hidden', !post.example_likes_count);
    $('#detail-prompt-section').classList.toggle('is-hidden', !post.prompt); $('#prompt-explanation').textContent = post.is_curated ? '根据这张作品的主体、风格与构图编写' : '由发布者分享的创作提示词';
    $('#detail-tags').replaceChildren(...tags(post).childNodes); $('#detail-prompt').textContent = post.prompt;
    $('#science-note').classList.toggle('is-hidden', post.category !== 'science');
    $('#comment-form').classList.toggle('is-hidden', !state.user); $('#comment-login').classList.toggle('is-hidden', !!state.user); $('#comment-login a').href = loginURL(post.id);
    renderDetailLike();
  }
  async function openPost(id, focusComment = false) {
    let post = state.posts.find((item) => item.id === id);
    if (!post) { try { post = (await api(`/api/community/posts/${encodeURIComponent(id)}`)).item; state.posts.push(post); } catch (error) { return toast(error.message, true); } }
    const opening = ++state.opening; state.selected = post; state.comments = [];
    fillDetail(post); $('#comment-content').value = ''; $('#comment-char-count').textContent = '0'; $('#detail-comment-count').textContent = post.comments_count;
    $('#submit-comment').disabled = true;
    $('#detail-comments').replaceChildren(element('p', 'discussion-loading', '正在加载作品讨论…'));
    if (!$('#post-dialog').open) $('#post-dialog').showModal();
    $('.post-discussion-pane').scrollTop = 0; $('.post-dialog-layout').scrollTop = 0;
    const url = new URL(location.href); url.searchParams.set('post', id); history.replaceState(null, '', url);
    try {
      const result = await api(`/api/community/posts/${id}`); if (opening !== state.opening) return;
      updatePost(id, result.item); state.comments = result.comments; fillDetail(state.selected); renderComments();
      $('#submit-comment').disabled = false;
      if (focusComment && state.user) $('#comment-content').focus();
    } catch (error) { if (opening !== state.opening) return; $('#detail-comments').replaceChildren(element('p', 'discussion-loading', '讨论暂未加载成功，请关闭后重新打开作品。')); toast(error.message, true); }
  }
  async function refreshDiscussion(id) {
    const result = await api(`/api/community/posts/${id}`); updatePost(id, result.item);
    if (state.selected?.id === id) { state.comments = result.comments; renderComments(); renderDetailLike(); }
    renderGallery();
  }
  document.addEventListener('click', (event) => {
    const open = event.target.closest('[data-open-post]'); if (open) { event.preventDefault(); openPost(open.dataset.openPost); return; }
    const comment = event.target.closest('[data-comment-post]'); if (comment) { openPost(comment.dataset.commentPost, true); return; }
    const like = event.target.closest('[data-like-post]'); if (like) { likePost(like.dataset.likePost); return; }
    const remove = event.target.closest('[data-delete-comment]');
    if (remove && state.selected) { const id = state.selected.id; remove.disabled = true; api(`/api/community/comments/${remove.dataset.deleteComment}`, { method: 'DELETE' }).then(() => refreshDiscussion(id)).then(() => toast('评论已删除')).catch((error) => { remove.disabled = false; toast(error.message, true); }); }
  });
  $('#category-filters').addEventListener('click', (event) => { const button = event.target.closest('[data-category]'); if (button) { state.category = button.dataset.category; renderGallery(); } });
  $('#gallery-search').addEventListener('input', renderGallery); $('#gallery-sort').addEventListener('change', renderGallery);
  $('#clear-gallery-filters').addEventListener('click', () => { state.category = 'all'; $('#gallery-search').value = ''; renderGallery(); });
  $('#close-post-dialog').addEventListener('click', () => $('#post-dialog').close());
  $('#post-dialog').addEventListener('click', (event) => { if (event.target === $('#post-dialog')) { const bounds = $('#post-dialog').getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) $('#post-dialog').close(); } });
  $('#post-dialog').addEventListener('close', () => { ++state.opening; state.selected = null; const url = new URL(location.href); url.searchParams.delete('post'); history.replaceState(null, '', url); });
  $('#detail-like').addEventListener('click', () => { if (state.selected) likePost(state.selected.id); });
  $('#delete-post').addEventListener('click', async () => {
    if (!state.selected?.can_delete || !confirm('删除这篇帖子及其评论？发布的图片也会移除，个人作品库中的原图会保留。')) return;
    const id = state.selected.id; $('#delete-post').disabled = true;
    try { await api(`/api/community/posts/${id}`, { method: 'DELETE' }); $('#post-dialog').close(); state.posts = state.posts.filter((post) => post.id !== id); renderGallery(); toast('帖子已删除'); }
    catch (error) { toast(error.message, true); $('#delete-post').disabled = false; }
  });
  $('#copy-prompt').addEventListener('click', async () => { if (!state.selected) return; try { await navigator.clipboard.writeText(state.selected.prompt); toast('提示词已复制'); } catch { toast('复制未完成，可以选中提示词手动复制', true); } });
  $('#share-post').addEventListener('click', async () => { if (!state.selected) return; try { await navigator.clipboard.writeText(location.origin + '/?post=' + state.selected.id); toast('作品链接已复制，可以分享给朋友'); } catch { toast('可以复制地址栏中的作品链接进行分享', true); } });
  $('#use-prompt').addEventListener('click', () => {
    if (!state.selected) return;
    try { sessionStorage.setItem('astrogate.community-draft', JSON.stringify({ prompt: state.selected.prompt, size: state.selected.size })); location.assign('/studio'); }
    catch { toast('浏览器暂时无法保存草稿，请复制提示词后进入工作台', true); }
  });
  $('#comment-content').addEventListener('input', () => { $('#comment-char-count').textContent = $('#comment-content').value.length; });
  $('#comment-form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (!state.selected || !requireUser(state.selected.id)) return;
    const id = state.selected.id; const opening = state.opening; const content = $('#comment-content').value.trim(); if (!content) return toast('写一点你的想法再发布吧');
    const button = $('#submit-comment'); if (button.disabled) return; button.disabled = true;
    try {
      const result = await api(`/api/community/posts/${id}/comments`, { method: 'POST', body: JSON.stringify({ content }) });
      updatePost(id, { comments_count: result.comments_count, comment_preview: result.comment, last_commented_at: result.comment.created_at });
      if (state.selected?.id === id) { state.comments.push(result.comment); renderComments(); $('#comment-content').value = ''; $('#comment-char-count').textContent = '0'; }
      renderGallery(); toast('评论已发布');
    } catch (error) { if (error.status === 401) { state.user = null; location.assign(loginURL(id)); } else toast(error.message, true); }
    finally { if (state.opening === opening) button.disabled = false; }
  });
  async function loadPosts(reset = false) {
    if (state.loading) return;
    state.loading = true; $('#load-more-posts').disabled = true; $('#my-posts').disabled = true;
    try {
      const page = reset ? 0 : state.page + 1;
      const query = new URLSearchParams({ page, mine: String(state.mine) });
      const data = await api('/api/community/posts' + (page || state.mine ? '?' + query : ''));
      state.posts = reset ? data.items : [...state.posts, ...data.items.filter((post) => !state.posts.some((old) => old.id === post.id))];
      state.user = data.user; state.page = page; state.hasMore = !!data.has_more;
      if (state.user) { $('#community-login').textContent = state.user.is_admin ? '管理后台' : state.user.display_name; $('#community-login').href = state.user.is_admin ? '/admin' : '/studio'; }
    } finally { state.loading = false; $('#my-posts').disabled = false; $('#load-more-posts').disabled = false; }
    renderGallery();
  }
  $('#load-more-posts').addEventListener('click', () => loadPosts().catch((error) => toast(error.message, true)));
  $('#my-posts').addEventListener('click', async () => {
    if (!state.user) { location.assign('/login?next=' + encodeURIComponent('/?mine=1')); return; }
    state.mine = !state.mine; state.category = 'all'; $('#gallery-search').value = '';
    try { await loadPosts(true); } catch (error) { state.mine = !state.mine; toast(error.message, true); }
  });

  function renderPostFiles() {
    const container = $('#post-image-previews'); const entries = state.source ? [{ url: state.source.image_src, source: true }] : state.files;
    container.replaceChildren(...entries.map((file, index) => {
      const item = element('div', 'post-upload-preview'); const image = element('img'); image.src = file.url; image.alt = file.source ? '作品库图片' : `待发布图片 ${index + 1}`;
      const remove = action('post-remove-image', '×'); remove.setAttribute('aria-label', `移除第 ${index + 1} 张图片`); remove.disabled = state.publishing;
      remove.addEventListener('click', () => { if (state.publishing) return; if (file.source) state.source = null; else { URL.revokeObjectURL(file.url); state.files.splice(index, 1); } renderPostFiles(); });
      item.append(image, remove); return item;
    }));
    $('#post-upload-count').textContent = state.source ? '已选择作品库图片 · 发布后生成公开副本' : `${state.files.length} / 9 张图片`;
    $('#post-dropzone').classList.toggle('is-hidden', !!entries.length);
  }
  function addPostFiles(files) {
    if (state.publishing) return;
    const incoming = Array.from(files).filter((file) => file.size > 0); if (!incoming.length) return;
    if (state.source) return toast('请先移除作品库图片，再添加本地图片', true);
    if (state.files.length + incoming.length > 9) return toast('最多上传 9 张图片', true);
    if (incoming.some((file) => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type))) return toast('仅支持 PNG、JPG 和 WebP', true);
    if (incoming.some((file) => file.size > 10 * 1024 * 1024)) return toast('单张图片不能超过 10MB', true);
    if ([...state.files.map((entry) => entry.file), ...incoming].reduce((total, file) => total + file.size, 0) > 30 * 1024 * 1024) return toast('图片合计不能超过 30MB', true);
    state.files.push(...incoming.map((file) => ({ file, url: URL.createObjectURL(file) }))); renderPostFiles();
  }
  async function openComposer(sourceId) {
    if (!state.user) { location.assign('/login?next=' + encodeURIComponent(sourceId ? `/?compose=1&image=${sourceId}` : '/?compose=1')); return; }
    if (!$('#compose-dialog').open) $('#compose-dialog').showModal();
    if (sourceId) {
      try { const data = await api('/api/images/history'); const item = data.items.find((entry) => entry.id === sourceId); if (!item) return toast('这张作品不存在或不属于当前账号', true); selectHistoryImage(item); }
      catch (error) { toast(error.message, true); }
    }
    $('#post-title').focus();
  }
  document.addEventListener('paste', (event) => {
    if (!$('#compose-dialog').open || state.publishing) return;
    const files = Array.from(event.clipboardData?.items || []).filter((item) => item.kind === 'file' && item.type.startsWith('image/')).map((item) => item.getAsFile()).filter(Boolean);
    if (files.length) { event.preventDefault(); addPostFiles(files); }
  });
  document.querySelectorAll('[data-compose-post]').forEach((button) => button.addEventListener('click', () => openComposer()));
  $('#close-compose').addEventListener('click', () => { if (!state.publishing) $('#compose-dialog').close(); });
  $('#compose-dialog').addEventListener('cancel', (event) => { if (state.publishing) event.preventDefault(); });
  $('#choose-post-images').addEventListener('click', () => { if (!state.publishing) $('#post-images').click(); });
  $('#post-dropzone').addEventListener('click', () => { if (!state.publishing) $('#post-images').click(); });
  $('#post-dropzone').addEventListener('keydown', (event) => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); if (!state.publishing) $('#post-images').click(); } });
  $('#post-images').addEventListener('change', (event) => { addPostFiles(event.target.files); event.target.value = ''; });
  $('#compose-dialog').addEventListener('dragover', (event) => event.preventDefault());
  $('#compose-dialog').addEventListener('drop', (event) => { event.preventDefault(); addPostFiles(event.dataTransfer.files); });
  $('#paste-post-images').addEventListener('click', async () => {
    if (!navigator.clipboard?.read) return toast('复制图片后，按 Ctrl + V（Mac：⌘ + V）粘贴');
    try {
      const files = [];
      for (const item of await navigator.clipboard.read()) { const type = item.types.find((value) => ['image/png', 'image/jpeg', 'image/webp'].includes(value)); if (type) files.push(new File([await item.getType(type)], `clipboard-${files.length + 1}`, { type })); }
      if (files.length) addPostFiles(files); else toast('剪贴板里没有图片');
    } catch { toast('可以直接按 Ctrl + V（Mac：⌘ + V）粘贴图片'); }
  });
  function selectHistoryImage(item) {
    if (state.publishing) return;
    if (!/^\/api\/images\/files\/[0-9a-f]{24}$/.test(item.image_src)) return toast('作品图片地址无效', true);
    state.files.forEach((entry) => URL.revokeObjectURL(entry.url)); state.files = []; state.source = item;
    if (!$('#post-prompt').value) $('#post-prompt').value = item.prompt;
    $('#post-history-picker').classList.add('is-hidden'); renderPostFiles();
  }
  $('#choose-history').addEventListener('click', async () => {
    if (state.publishing) return;
    const picker = $('#post-history-picker'); picker.classList.remove('is-hidden'); picker.replaceChildren(element('p', 'tiny muted', '正在读取个人作品库…'));
    try {
      const data = await api('/api/images/history');
      picker.replaceChildren(...data.items.map((item) => { const button = action('history-pick-item', ''); const image = element('img'); image.src = item.image_src; image.alt = item.prompt; image.loading = 'lazy'; button.append(image); button.setAttribute('aria-label', `选择作品：${item.prompt}`); button.addEventListener('click', () => selectHistoryImage(item)); return button; }));
      if (!data.items.length) picker.append(element('p', 'tiny muted', '作品库还是空的，你可以先上传图片或前往工作台创作。'));
    } catch (error) { picker.replaceChildren(element('p', 'tiny muted', error.message)); }
  });
  function readFile(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('图片读取失败，请重新添加')); reader.readAsDataURL(file); }); }
  $('#post-form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (state.publishing) return;
    if (!state.user) { location.assign(loginURL()); return; }
    const title = $('#post-title').value.trim(), content = $('#post-content').value.trim();
    if (!title) return toast('请填写帖子标题');
    if (!content && !state.files.length && !state.source) return toast('添加图片或写一点正文再发布吧');
    const postTags = $('#post-tags').value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean);
    if (postTags.length > 5 || postTags.some((tag) => tag.length > 20)) return toast('最多 5 个标签，每个不超过 20 字', true);
    state.publishing = true; $('#publish-post').disabled = true; $('#close-compose').disabled = true; $('#publish-post').textContent = '正在发布…';
    $('#post-publish-status').textContent = '正在上传并发布，请保持页面打开…'; renderPostFiles();
    try {
      const body = { title, content, category: $('#post-category').value, tags: postTags, prompt: $('#post-prompt').value.trim(), image_data_urls: await Promise.all(state.files.map((entry) => readFile(entry.file))), source_history_id: state.source?.id || null };
      const data = await api('/api/community/posts', { method: 'POST', body: JSON.stringify(body) });
      state.files.forEach((entry) => URL.revokeObjectURL(entry.url)); state.files = []; state.source = null;
      $('#post-form').reset(); $('#compose-dialog').close(); state.category = 'all'; $('#gallery-search').value = ''; $('#gallery-sort').value = 'newest';
      state.posts.unshift(data.item); renderPostFiles(); renderGallery(); await openPost(data.item.id); toast('帖子已发布，大家现在可以看到并参与讨论');
    } catch (error) { toast(error.message, true); }
    finally { state.publishing = false; $('#publish-post').disabled = false; $('#close-compose').disabled = false; $('#publish-post').textContent = '发布帖子 ↗'; $('#post-publish-status').textContent = '发布免费。发布后，图片、正文和提示词将对所有访客公开。'; renderPostFiles(); }
  });
  async function initialize() {
    try {
      const query = new URLSearchParams(location.search); state.mine = query.get('mine') === '1';
      if (state.mine) { try { await loadPosts(true); } catch (error) { if (error.status === 401) { location.assign('/login?next=' + encodeURIComponent('/?mine=1')); return; } throw error; } } else await loadPosts(true);
      const id = query.get('post'); if (id) await openPost(id);
      if (query.get('compose') === '1') { const imageId = query.get('image'); await openComposer(/^[0-9a-f]{24}$/.test(imageId || '') ? imageId : undefined); }
    } catch (error) {
      const message = element('p', 'gallery-loading', '灵感画廊暂未加载成功。'); const retry = action('text-button', '重新加载'); retry.addEventListener('click', initialize); message.append(document.createTextNode(' '), retry); $('#gallery-grid').replaceChildren(message); $('#gallery-grid').setAttribute('aria-busy', 'false'); toast(error.message, true);
    }
  }
  initialize();
})();
