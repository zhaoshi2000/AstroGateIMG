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
  const studioHtml = fs.readFileSync(path.join(__dirname, '../app/static/index.html'), 'utf8');
  const sizeOptions = studioHtml.match(/<select id="size">([\s\S]*?)<\/select>/)?.[1];
  assert.ok(sizeOptions, 'Studio must include aspect ratio options');
  const selectedSizes = [...sizeOptions.matchAll(/<option value="([^"]+)" selected>/g)].map((match) => match[1]);
  assert.deepEqual(selectedSizes, ['1536x864'], 'A new creation defaults to 16:9 widescreen');
  const initialPrefix = studioHtml.match(/<textarea id="prompt-prefix"[^>]*>([\s\S]*?)<\/textarea>/)?.[1];
  assert.ok(initialPrefix, 'Studio must expose an editable prompt prefix');
  const elements = new Map(); const documentEvents = {};
  const document = {
    body: new Element(),
    querySelector: (selector) => { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); },
    querySelectorAll: () => [], createElement: () => new Element(),
    addEventListener: (name, callback) => { documentEvents[name] = callback; },
  };
  const drafts = new Map([['astrogate.community-draft', JSON.stringify({ prompt: 'Showcase prompt carried into the studio', size: '864x1536' })]]);
  document.querySelector('#size').options = [{ value: '1536x864' }, { value: '864x1536' }];
  document.querySelector('#size').value = selectedSizes[0];
  document.querySelector('#prompt-prefix').value = initialPrefix;
  document.querySelector('#prompt-style').value = 'none';
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
  assert.equal(elements.get('#prompt-prefix').value, '', 'Imported full prompt must not receive a second default prefix');
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
  assert.equal(elements.get('#prompt-prefix').value, '', 'Reused artwork prompt must not receive another prefix');
  vm.runInContext('clearReferences()', context);
  elements.get('#prompt-prefix').value = '主体清晰，构图完整。';
  elements.get('#prompt-style').value = 'anime';
  elements.get('#prompt-avoid').value = '水印';
  elements.get('#prompt').value = '雨夜的城堡';
  elements.get('#prompt').dispatchEvent(new Event('input'));
  const composed = '主体清晰，构图完整。\n动漫插画风格，细腻线条，柔和光影。\n雨夜的城堡\n请尽量避免：水印';
  assert.equal(vm.runInContext('composePrompt()', context), composed);
  assert.equal(elements.get('#prompt-final').textContent, composed, 'Preview must show the exact prompt to be submitted');
  elements.get('#prompt').value = '长'.repeat(4000);
  elements.get('#prompt').dispatchEvent(new Event('input'));
  assert.equal(elements.get('#prompt-final-error').classList.contains('is-hidden'), false, 'Overlength combined prompts must show an error');
  let generationCalls = 0;
  context.fetch = async (url, options = {}) => {
    if (options.method === 'POST' && url.startsWith('/api/images/')) generationCalls += 1;
    throw new Error('Overlength prompt must not reach the API');
  };
  await elements.get('#generation-form').events.submit({ preventDefault() {} });
  assert.equal(generationCalls, 0, 'Overlength prompt must be blocked before credits can be reserved');
  elements.get('#prompt').value = '雨夜的城堡';
  elements.get('#prompt').dispatchEvent(new Event('input'));
  elements.get('#size').value = '1536x864';
  let submitted;
  context.fetch = async (url, options = {}) => {
    if (url === '/api/images/generations') {
      submitted = JSON.parse(options.body);
      return { ok: true, status: 200, json: async () => ({ credits: 1, item: { id: historyId, image_src: historyImage, prompt: submitted.prompt, mode: 'text-to-image', size: submitted.size, credits_cost: 1, created_at: '2026-10-19T10:00:00Z' } }) };
    }
    if (url === '/api/auth/me') return { ok: true, status: 200, json: async () => ({ user: { username: 'artist', credits: 1, is_admin: false } }) };
    throw new Error('Unexpected request: ' + url);
  };
  await elements.get('#generation-form').events.submit({ preventDefault() {} });
  assert.equal(submitted.prompt, composed, 'The API must receive exactly what the preview showed');
  assert.equal(submitted.size, '1536x864');
  assert.equal(elements.get('#result-prompt').textContent, composed);
  vm.runInContext('setBalance(5)', context);
  context.fetch = async (url, options = {}) => {
    if (url === '/api/images/models') return { ok: true, status: 200, json: async () => ({ items: [
      { id: 'gpt-image-2', label: 'GPT Image 2', available: true, credits_cost: 1, price_rmb: '0.05' },
      { id: 'gpt-image-2.5-flare', label: 'GPT Image 2.5 Flare', available: true, credits_cost: 5, price_rmb: '0.25' },
      { id: 'gpt-image-2.5-sunburst', label: 'GPT Image 2.5 Sunburst', available: false, credits_cost: 5, price_rmb: '0.25' },
    ] }) };
    if (url === '/api/prompts/assist') return { ok: true, status: 200, json: async () => ({ prompt: '月光下的古城，宽画幅，石墙与云海，柔和冷色调。' }) };
    if (url === '/api/images/generations') {
      submitted = JSON.parse(options.body);
      return { ok: true, status: 200, json: async () => ({ credits: 0, item: { id: historyId, image_src: historyImage, prompt: submitted.prompt, model: submitted.model, mode: 'text-to-image', size: submitted.size, credits_cost: 5, created_at: '2026-10-19T10:00:00Z' } }) };
    }
    if (url === '/api/auth/me') return { ok: true, status: 200, json: async () => ({ user: { username: 'artist', credits: 0, is_admin: false } }) };
    throw new Error('Unexpected request: ' + url);
  };
  await vm.runInContext('loadImageModels()', context);
  assert.equal(elements.get('#image-model').children[1].disabled, false, 'Image 2.5 becomes selectable only when the image node reports it');
  assert.equal(elements.get('#image-model').children[2].disabled, true, 'Unlisted image variant remains disabled');
  elements.get('#prompt').value = '月光下的古城';
  await elements.get('#assist-prompt').events.click();
  assert.equal(elements.get('#assist-suggestion').classList.contains('is-hidden'), false, 'AI suggestion remains reviewable before applying');
  assert.equal(elements.get('#prompt').value, '月光下的古城', 'AI must not silently overwrite the idea');
  elements.get('#apply-assist').events.click();
  assert.equal(elements.get('#prompt').value, '月光下的古城，宽画幅，石墙与云海，柔和冷色调。');
  elements.get('#image-model').value = 'gpt-image-2.5-flare';
  elements.get('#image-model').dispatchEvent(new Event('change'));
  assert.match(elements.get('#generation-cost').textContent, /5 积分/);
  await elements.get('#generation-form').events.submit({ preventDefault() {} });
  assert.equal(submitted.model, 'gpt-image-2.5-flare', 'Selected paid model must reach the image API');
  assert.match(elements.get('#generation-status').textContent, /5 积分/);
  console.log('PASS: clipboard, prompt preview, AI suggestion, model availability, five-credit pricing, and default widescreen');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
