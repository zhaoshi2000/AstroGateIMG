const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

// A small DOM fixture exercises the production paste handler without a browser.
class Element {
  constructor() {
    this.events = {}; this.children = []; this.value = ''; this.dataset = {};
    const classes = new Set();
    this.classList = { add: (...names) => names.forEach((name) => classes.add(name)), remove: (...names) => names.forEach((name) => classes.delete(name)), contains: (name) => classes.has(name), toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name) };
  }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute() {}
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  focus() {}
  dispatchEvent(event) { this.events[event.type]?.(event); }
}
async function main() {
  const elements = new Map(); const documentEvents = {};
  const document = {
    body: new Element(),
    querySelector: (selector) => { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); },
    querySelectorAll: () => [], createElement: () => new Element(),
    addEventListener: (name, callback) => { documentEvents[name] = callback; },
  };
  const drafts = new Map([['astrogate.community-draft', JSON.stringify({ prompt: 'Showcase prompt carried into the studio', size: '864x1536' })]]);
  document.querySelector('#size').options = [{ value: '1024x1024' }, { value: '864x1536' }];
  const context = vm.createContext({ document, window: { addEventListener: () => {} }, location: { pathname: '/studio', search: '', hash: '' },
    URL, URLSearchParams, crypto: webcrypto, File, Event, navigator: {}, Intl, console,
    sessionStorage: { getItem: (key) => drafts.get(key), removeItem: (key) => drafts.delete(key) },
    setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {}, confirm: () => true,
    fetch: async (url) => ({ ok: true, status: 200, json: async () => url === '/api/auth/me' ? { user: { username: 'artist', credits: 2, is_admin: false } } : { items: [] } }),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8'), context);
  await new Promise(setImmediate);
  assert.equal(document.body.classList.contains('studio-theme'), true, 'Studio must use its dedicated dark workspace theme');
  assert.equal(elements.get('#prompt').value, 'Showcase prompt carried into the studio');
  assert.equal(elements.get('#size').value, '864x1536');
  assert.equal(drafts.size, 0, 'Applying a showcase draft must consume it once');
  context.location.search = '?next=' + encodeURIComponent('/?post=sakura-after-school');
  assert.equal(vm.runInContext('authDestination()', context), '/?post=sakura-after-school');
  context.location.search = '?next=' + encodeURIComponent('//untrusted.example');
  assert.equal(vm.runInContext('authDestination()', context), '/studio', 'Login must reject external redirect destinations');
  context.location.search = '';
  vm.runInContext('state.user.is_admin = true', context);
  assert.equal(vm.runInContext('authDestination()', context), '/admin', 'Administrators must land in the independent console');
  vm.runInContext('state.user.is_admin = false', context);
  vm.runInContext("authMode('register')", context);
  assert.equal(elements.get('#username').type, 'email', 'Registration must request an email address');
  assert.equal(elements.get('#email-code').required, true, 'Registration must require an email code');
  vm.runInContext("authMode('login')", context);
  assert.equal(elements.get('#email-code').disabled, true, 'A hidden code input must not block existing-account login');
  let prevented = false;
  const image = (name) => new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' });
  documentEvents.paste({ clipboardData: { items: [image('one.png'), image('two.png')].map((file) => ({ kind: 'file', type: file.type, getAsFile: () => file })) }, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true, 'Pasting images should not insert binary data into the prompt');
  assert.equal(vm.runInContext('state.references.length', context), 2, 'Both pasted reference images should be retained');
  assert.equal(elements.get('#reference-list').children.length, 2, 'Both references should get a preview');
  assert.equal(elements.get('#generation-mode').textContent, '图生图 · 2 张参考图');
  const firstPreview = elements.get('#reference-list').children[0];
  firstPreview.children[3].events.click();
  assert.equal(vm.runInContext('state.references.length', context), 1, 'A reference can be removed independently');
  context.moreFiles = Array.from({ length: 8 }, (_, index) => image(`extra-${index}.png`));
  await vm.runInContext('addReferences(moreFiles)', context);
  assert.equal(vm.runInContext('state.references.length', context), 1, 'Over-limit batches must leave existing references intact');
  context.largeFiles = [{ size: 51 * 1024 * 1024, type: 'image/png' }];
  await vm.runInContext('addReferences(largeFiles)', context);
  assert.equal(vm.runInContext('state.references.length', context), 1, 'Oversized references must be rejected');
  prevented = false;
  documentEvents.paste({ clipboardData: { items: [] }, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, false, 'Ordinary text pasting must keep working');
  vm.runInContext('clearReferences()', context);
  const historyId = 'a'.repeat(24);
  const historyImage = `/api/images/files/${historyId}`;
  context.historyFixture = { id: historyId, image_src: historyImage, prompt: '月光下的云海城堡', mode: 'text-to-image', size: '1024x1024', credits_cost: 1, created_at: '2026-10-19T10:00:00Z' };
  vm.runInContext('state.history = [historyFixture]; renderHistory()', context);
  elements.get('#history-grid').children[0].children[0].events.click();
  assert.equal(elements.get('#result-image').src, historyImage, 'Selecting a history thumbnail must show it on the central canvas');
  assert.equal(elements.get('#share-result').href, `/?compose=1&image=${historyId}`, 'Selected history retains community sharing');
  assert.equal(elements.get('#download-result').href, historyImage, 'Selected history retains private download');
  const zoom = elements.get('#image-zoom-dialog');
  let zoomOpened = false;
  zoom.showModal = () => { zoomOpened = true; };
  elements.get('#zoom-result').events.click();
  assert.equal(zoomOpened, true, 'Zoom button opens the enlarged artwork');
  assert.equal(elements.get('#zoom-image').src, historyImage);
  context.fetch = async (url) => ({ ok: url === historyImage, blob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: 'image/png' }) });
  await elements.get('#use-result-reference').events.click();
  assert.equal(vm.runInContext('state.references.length', context), 1, 'Current artwork can be used as the next generation reference');
  assert.equal(elements.get('#generation-mode').textContent, '图生图 · 1 张参考图');
  console.log('PASS: clipboard images, multiple previews, removal, size/count limits, normal text paste');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
