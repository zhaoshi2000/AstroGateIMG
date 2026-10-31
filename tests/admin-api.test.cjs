const assert = require('node:assert/strict');
const { parsePrompts, createBatch, mount } = require('../app/static/admin-api.js');
const tick = () => new Promise(setImmediate);

class Element {
  constructor(tag = '') { this.tag = tag; this.events = {}; this.children = []; this.value = ''; this.type = ''; this.textContent = ''; const classes = new Set(); this.classList = { add: (value) => classes.add(value), remove: (value) => classes.delete(value), toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value), contains: (value) => classes.has(value) }; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, handler) { this.events[name] = handler; }
  setAttribute() {}
}

async function panelTests() {
  const elements = new Map(), requests = [], notices = [], windowEvents = {}, pending = [];
  const select = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  global.document = { querySelector: select };
  global.window = { addEventListener: (name, handler) => { windowEvents[name] = handler; } };
  global.location = { origin: 'https://example.test' };
  global.confirm = () => true;
  let nonce = 0;
  // HTTP IP pages expose getRandomValues, but do not expose randomUUID.
  Object.defineProperty(global, 'crypto', { configurable: true, value: { getRandomValues(bytes) { bytes.fill(++nonce); return bytes; } } });
  const model = { id: 'gpt-image-2', label: '标准', credits_cost: 1, available: true };
  const key = { id: 'key-1', name: 'Python', prefix: 'ag_secret', max_concurrency: 3, created_at: 'today' };
  let keys = [];
  const panel = mount({
    api: async (path, options = {}) => {
      requests.push({ path, ...options });
      if (path === '/api/images/models') return { items: [model, { id: 'unavailable', label: '未开放', credits_cost: 5, available: false }] };
      if (path === '/api/admin/api-keys' && options.method === 'POST') { keys = [key]; return { key: 'one-time-only-secret', item: key }; }
      if (path === '/api/admin/api-keys') return { items: [...keys] };
      if (options.method === 'DELETE') { keys = [{ ...key, revoked_at: 'now' }]; return { ok: true }; }
      if (path === '/api/admin/images/generations') return new Promise((resolve) => pending.push(resolve));
      throw new Error('Unexpected ' + path);
    }, toast: (message) => notices.push(message), date: (value) => value,
    el: (tag, className, text) => { const node = new Element(tag); node.className = className || ''; if (text !== undefined) node.textContent = text; return node; },
  });
  select('#api-batch-concurrency').value = '2'; select('#api-batch-size').value = '1536x864'; select('#api-batch-quality').value = 'medium'; select('#api-batch-resolution').value = '2K';
  await panel.load();
  assert.equal(select('#api-batch-model').value, model.id);
  assert.equal(select('#api-batch-model').children[1].disabled, true);
  select('#api-key-name').value = 'Python'; select('#api-key-concurrency').value = '3';
  await select('#api-key-form').events.submit({ preventDefault() {} });
  assert.equal(select('#api-key-secret').value, 'one-time-only-secret');
  assert.equal(select('#api-key-secret').type, 'password');
  select('#api-key-reveal').events.click(); assert.equal(select('#api-key-secret').type, 'text');
  select('#api-key-dismiss').events.click(); assert.equal(select('#api-key-secret').value, '');
  assert.equal(select('#api-key-once').classList.contains('hidden'), true);
  assert.ok(!JSON.stringify(select('#api-key-list').children).includes('one-time-only-secret'));
  await select('#api-key-list').children[0].children[1].events.click();
  assert.ok(requests.some((request) => request.method === 'DELETE' && request.path.endsWith('/key-1')));
  select('#api-batch-prompts').value = '图一\n图二\n图三';
  const run = select('#api-batch-form').events.submit({ preventDefault() {} }); await tick();
  assert.equal(pending.length, 2); assert.equal(select('#api-batch-settings').disabled, true);
  const calls = requests.filter((request) => request.path.endsWith('/images/generations'));
  assert.deepEqual(JSON.parse(calls[0].body), { model: model.id, size: '1536x864', quality: 'medium', resolution: '2K', prompt: '图一' });
  assert.notEqual(calls[0].headers['Idempotency-Key'], calls[1].headers['Idempotency-Key']);
  let prevented = false; windowEvents.beforeunload({ preventDefault() { prevented = true; } }); assert.equal(prevented, true);
  select('#api-batch-stop').events.click();
  pending.forEach((resolve) => resolve({ item: { id: '0'.repeat(24), image_src: '/api/images/files/' + '0'.repeat(24), credits_cost: 1, output_size: '2048x1152' } })); await run;
  assert.equal(select('#api-batch-settings').disabled, false); assert.equal(select('#admin-logout').disabled, false);
  assert.equal(select('#api-batch-results').children.length, 3);
  assert.ok(select('#api-batch-status').textContent.includes('2 成功'));
  assert.ok(select('#api-batch-status').textContent.includes('1 已停止'));
  assert.equal(requests.filter((request) => request.path.endsWith('/images/generations')).length, 2);
  console.log('PASS: key lifecycle, one-time secret clearing, unavailable model disabled, authenticated payloads, unique request IDs, navigation warning and batch UI results');
}

async function main() {
  assert.deepEqual(parsePrompts(' 海边日落 \n\n雨夜都市\r\n'), ['海边日落', '雨夜都市']);
  assert.throws(() => parsePrompts(' \n'), /提示词/);
  assert.throws(() => parsePrompts(Array(51).fill('test').join('\n')), /50/);
  assert.throws(() => parsePrompts('a'.repeat(4001)), /4000/);
  const releases = [], updates = [], sent = [];
  let active = 0, peak = 0;
  const batch = createBatch({ prompts: ['one', 'two', 'three', 'four', 'five'], concurrency: 2,
    request: async (job) => {
      active++; peak = Math.max(peak, active); sent.push(job.prompt);
      await new Promise((resolve) => releases.push(resolve)); active--;
      if (job.prompt === 'two') throw new Error('服务繁忙');
      return { item: { id: job.prompt, credits_cost: 1 } };
    }, onUpdate: (jobs) => updates.push(jobs.map((job) => job.status)) });
  const complete = batch.start();
  assert.equal(batch.start(), complete, 'Double start must reuse the same run');
  await tick(); assert.deepEqual(sent, ['one', 'two']); assert.equal(peak, 2);
  releases.shift()(); await tick(); assert.deepEqual(sent, ['one', 'two', 'three']);
  batch.stop(); releases.splice(0).forEach((release) => release()); await complete;
  assert.equal(peak, 2); assert.deepEqual(sent, ['one', 'two', 'three']);
  assert.deepEqual(batch.jobs.map((job) => job.status), ['succeeded', 'failed', 'succeeded', 'cancelled', 'cancelled']);
  assert.equal(batch.jobs[1].error, '服务繁忙');
  assert.ok(updates.some((states) => states.includes('running')));
  assert.equal(sent.filter((prompt) => prompt === 'two').length, 1, 'Failures must not be retried automatically');
  assert.throws(() => createBatch({ prompts: ['x'], concurrency: 9, request() {} }), /并发/);
  console.log('PASS: prompt validation, bounded parallel workers, double-start prevention, partial failures, stop queued work without cancelling in-flight requests');
  await panelTests();
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
