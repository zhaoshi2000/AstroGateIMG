(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const state = { posts: [], user: null, category: 'all', selected: null, comments: [], opening: 0, liking: new Set() };
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
  function toast(message, error = false) { const box = $('#toast'); box.textContent = message; box.classList.toggle('error', error); box.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => box.classList.remove('show'), error ? 6000 : 3500); }
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
    const image = element('img'); image.src = post.thumbnail; image.alt = post.alt; image.width = post.width; image.height = post.height; image.loading = 'lazy'; image.decoding = 'async';
    imageButton.append(image, element('span', 'gallery-image-hint', '展开作品 ↗'));
    const body = element('div', 'gallery-card-body'); const title = element('h3', 'gallery-card-title'); title.append(action('gallery-title-button', post.title, 'openPost', post.id));
    const creator = element('div', 'gallery-creator'); creator.append(element('span', 'creator-avatar', '站'), element('span', '', post.author), element('span', 'creator-curated', '精选'));
    const actions = element('div', 'gallery-card-actions'); actions.append(socialButton('heart', post.likes_count, 'likePost', post), socialButton('comment', post.comments_count, 'commentPost', post), action('social-button gallery-prompt-button', '提示词 ↗', 'openPost', post.id));
    body.append(title, creator, tags(post), actions);
    const preview = action('gallery-comment-preview', '', 'commentPost', post.id); const line = element('span', 'gallery-comment-text');
    if (post.comment_preview.is_example) line.append(element('span', 'example-tag', '示例评论'));
    line.append(element('strong', '', post.comment_preview.author + '：'), document.createTextNode(post.comment_preview.content)); preview.append(line);
    article.append(imageButton, body, preview); return article;
  }
  function renderGallery() {
    const keyword = $('#gallery-search').value.trim().toLocaleLowerCase();
    let posts = state.posts.filter((post) => (state.category === 'all' || post.category === state.category) && (!keyword || [post.title, post.prompt, ...post.tags].join(' ').toLocaleLowerCase().includes(keyword)));
    if ($('#gallery-sort').value === 'liked') posts = [...posts].sort((a, b) => b.likes_count - a.likes_count);
    if ($('#gallery-sort').value === 'discussed') posts = [...posts].sort((a, b) => b.last_commented_at.localeCompare(a.last_commented_at));
    const focusedLike = document.activeElement?.dataset.likePost;
    $('#gallery-grid').replaceChildren(...posts.map(card)); $('#gallery-grid').setAttribute('aria-busy', 'false');
    $('#gallery-empty').classList.toggle('is-hidden', posts.length > 0); $('#gallery-count').textContent = `${posts.length} 件作品`;
    document.querySelectorAll('[data-category]').forEach((button) => { const active = button.dataset.category === state.category; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
    if (focusedLike) document.querySelector(`[data-like-post="${focusedLike}"]`)?.focus({ preventScroll: true });
  }
  function loginURL(id) { return '/login?next=' + encodeURIComponent('/?post=' + id); }
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
      if (comment.is_example) byline.append(element('span', 'example-tag', '示例评论'));
      else { const time = element('time', '', formatDate(comment.created_at)); time.dateTime = comment.created_at; byline.append(time); }
      body.append(byline, element('p', '', comment.content));
      if (comment.can_delete) body.append(action('delete-comment', '删除评论', 'deleteComment', String(comment.id)));
      item.append(element('span', 'comment-avatar', Array.from(comment.author)[0] || '友'), body); return item;
    }));
    if (state.selected.comments_count > state.comments.length) $('#detail-comments').append(element('p', 'discussion-loading', '展示最近 100 条用户评论与示例讨论。'));
  }
  function fillDetail(post) {
    $('#detail-title').textContent = post.title; $('#detail-image').src = post.image; $('#detail-image').alt = post.alt;
    $('#detail-full-image').href = post.image; $('#detail-original').href = post.image; $('#detail-author').textContent = post.author;
    $('#detail-tags').replaceChildren(...tags(post).childNodes); $('#detail-prompt').textContent = post.prompt;
    $('#science-note').classList.toggle('is-hidden', post.category !== 'science');
    $('#comment-form').classList.toggle('is-hidden', !state.user); $('#comment-login').classList.toggle('is-hidden', !!state.user); $('#comment-login a').href = loginURL(post.id);
    renderDetailLike();
  }
  async function openPost(id, focusComment = false) {
    const post = state.posts.find((item) => item.id === id); if (!post) return toast('这张作品暂时无法打开', true);
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
  async function initialize() {
    try {
      const data = await api('/api/community/posts'); state.posts = data.items; state.user = data.user;
      if (state.user) { $('#community-login').textContent = state.user.display_name; $('#community-login').href = '/studio'; }
      renderGallery(); const id = new URLSearchParams(location.search).get('post'); if (id) await openPost(id);
    } catch (error) {
      const message = element('p', 'gallery-loading', '灵感画廊暂未加载成功。'); const retry = action('text-button', '重新加载'); retry.addEventListener('click', initialize); message.append(document.createTextNode(' '), retry); $('#gallery-grid').replaceChildren(message); $('#gallery-grid').setAttribute('aria-busy', 'false'); toast(error.message, true);
    }
  }
  initialize();
})();
