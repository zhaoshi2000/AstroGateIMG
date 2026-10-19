const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../app/gallery_catalog.json'), 'utf8'));
const source = fs.readFileSync(path.join(__dirname, '../app/static/community.js'), 'utf8');
class Element {
  constructor(tag = 'div') { this.tag = tag; this.events = {}; this.dataset = {}; this.children = []; this.value = ''; this.attributes = {}; this.textContent = ''; this.disabled = false; const classes = new Set(); this.classList = { add: (name) => classes.add(name), remove: (name) => classes.delete(name), contains: (name) => classes.has(name), toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name) }; }
  get childNodes() { return this.children; }
  append(...nodes) { nodes.forEach((node) => { node.parent = this; this.children.push(node); }); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  addEventListener(name, handler) { this.events[name] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
  closest(selector) { const name = selector.slice(6, -1).replace(/-([a-z])/g, (_, char) => char.toUpperCase()); return this.dataset[name] !== undefined ? this : this.parent?.closest(selector); }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.events.close?.(); }
}
const flush = () => new Promise(setImmediate);
const find = (node, predicate) => predicate(node) ? node : node.children.map((child) => find(child, predicate)).find(Boolean);
async function page(user = { id: 1, display_name: 'test-reader', is_admin: false }) {
  const elements = new Map(); const events = {}; const requests = []; const drafts = new Map();
  const select = (selector) => { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); };
  const categories = ['all', 'illustration', 'collage', 'science'].map((category) => { const button = new Element('button'); button.dataset.category = category; return button; });
  const posts = catalog.map((post) => ({ ...post, image: '/assets/gallery/' + post.id + '.webp', thumbnail: '/assets/gallery/' + post.id + '-preview.webp', likes_count: 0, liked: false, comments_count: 2, last_commented_at: '', comment_preview: { ...post.comments[0], is_example: true } }));
  const comments = new Map(posts.map((post) => [post.id, post.comments.map((item, index) => ({ ...item, id: 'example-' + index, is_example: true, can_delete: false }))]));
  const location = { href: 'https://img.astrogate.sbs/', origin: 'https://img.astrogate.sbs', search: '', assign: (url) => { location.destination = url; } };
  const context = vm.createContext({
    document: { querySelector: select, querySelectorAll: () => categories, createElement: (tag) => new Element(tag), createTextNode: (text) => { const node = new Element('text'); node.textContent = text; return node; }, addEventListener: (name, handler) => { events[name] = handler; } },
    location, history: { replaceState: (_, __, url) => { location.href = String(url); location.search = new URL(url).search; } },
    URL, URLSearchParams, Intl, Date, Set, console, navigator: { clipboard: { writeText: async () => {} } },
    sessionStorage: { setItem: (key, value) => drafts.set(key, value) }, setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, options = {}) => {
      requests.push({ url, ...options }); let body;
      if (url === '/api/community/posts') body = { items: posts, user };
      else {
        const id = url.split('/')[4]; const post = posts.find((item) => item.id === id);
        if (url.endsWith('/like')) { post.liked = JSON.parse(options.body).liked; post.likes_count = Number(post.liked); body = { liked: post.liked, likes_count: post.likes_count }; }
        else if (url.endsWith('/comments')) { const comment = { id: 99, author: 'test-reader', content: JSON.parse(options.body).content, created_at: '2026-10-19T09:00:00Z', is_example: false, can_delete: true }; comments.get(id).push(comment); post.comments_count++; body = { comment, comments_count: post.comments_count }; }
        else body = { item: post, comments: comments.get(id) };
      }
      return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(body)) };
    },
  });
  select('#gallery-sort').value = 'featured';
  vm.runInContext(source, context); await flush();
  return { select, events, categories, requests, drafts, location, posts };
}
async function main() {
  const view = await page(); const grid = view.select('#gallery-grid');
  assert.equal(grid.children.length, 15, 'Every supplied image must have a card');
  view.select('#category-filters').events.click({ target: view.categories[3] });
  assert.equal(grid.children.length, 5, 'Science filter must show all five science works');
  view.select('#gallery-search').value = '不存在的关键词'; view.select('#gallery-search').events.input();
  assert.equal(grid.children.length, 0); assert.equal(view.select('#gallery-empty').classList.contains('is-hidden'), false);
  view.select('#clear-gallery-filters').events.click();
  const post = view.posts[0]; const card = grid.children[0];
  view.events.click({ target: find(card, (node) => node.dataset.openPost === post.id), preventDefault() {} }); await flush();
  assert.equal(view.select('#post-dialog').open, true);
  assert.equal(view.select('#detail-title').textContent, post.title);
  assert.equal(view.select('#detail-prompt').textContent, post.prompt);
  assert.equal(view.select('#detail-comments').children.length, 2);
  assert.equal(view.select('#submit-comment').disabled, false);
  view.select('#detail-like').events.click(); await flush();
  assert.equal(view.select('#detail-like').attributes['aria-pressed'], 'true');
  view.select('#comment-content').value = '<img src=x onerror=alert(1)> 这里是普通文本评论';
  await view.select('#comment-form').events.submit({ preventDefault() {} });
  assert.equal(view.select('#detail-comment-count').textContent, 3);
  const last = view.select('#detail-comments').children.at(-1);
  assert.equal(find(last, (node) => node.tag === 'p').textContent, '<img src=x onerror=alert(1)> 这里是普通文本评论');
  assert.equal(view.select('#comment-content').value, '');
  view.select('#use-prompt').events.click();
  assert.deepEqual(JSON.parse(view.drafts.get('astrogate.community-draft')), { prompt: post.prompt, size: post.size });
  assert.equal(view.location.destination, '/studio');
  view.select('#close-post-dialog').events.click();
  assert.equal(view.select('#post-dialog').open, false);
  assert.equal(view.location.search, '');
  const anonymous = await page(null); const first = anonymous.select('#gallery-grid').children[0];
  anonymous.events.click({ target: find(first, (node) => !!node.dataset.likePost) }); await flush();
  assert.equal(anonymous.location.destination, '/login?next=' + encodeURIComponent('/?post=sakura-after-school'));
  assert.equal(anonymous.requests.filter((request) => request.method === 'POST').length, 0);
  console.log('PASS: fifteen cards, category/search filters, matching image prompts, dialog comments, real-like state, safe comment text, studio draft, anonymous login return');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
