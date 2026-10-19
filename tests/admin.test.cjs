const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
  constructor() { this.events = {}; this.children = []; this.dataset = {}; this.value = ''; const classes = new Set(); this.classList = { add: (value) => classes.add(value), remove: (value) => classes.delete(value), toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value), contains: (value) => classes.has(value) }; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, handler) { this.events[name] = handler; }
  setAttribute() {}
}
const flush = () => new Promise(setImmediate);
async function page(forbidden = false) {
  const elements = new Map(); const metrics = new Map(); const calls = [];
  const select = (selector) => { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); };
  const accounts = [{ id: 'first', email: 'first@example.com', plan_type: 'team', status: '正常', image_quota: 120, image_inflight: 0, success: 2, fail: 0, credential_records: 1 }, { id: 'second', email: 'second@example.com', plan_type: 'team', status: '限流', image_quota: 0, image_inflight: 0, success: 1, fail: 0, credential_records: 1 }];
  const document = { visibilityState: 'hidden', querySelector: select, createElement: () => new Element(), querySelectorAll: (selector) => { if (selector === '[data-page]') return []; if (!metrics.has(selector)) metrics.set(selector, [new Element()]); return metrics.get(selector); } };
  const context = vm.createContext({ document, location: { pathname: '/admin/accounts' }, history: { replaceState() {} }, URLSearchParams, Intl, Date, Set, console,
    setTimeout: (handler) => { handler(); return 0; }, clearTimeout() {}, setInterval: () => 0,
    fetch: async (url, options = {}) => { calls.push({ url, ...options }); let body;
      if (url === '/api/admin/me') body = { user: { username: 'operator', is_admin: true } };
      else if (url === '/api/admin/upstream/accounts') body = { items: accounts, stats: { accounts: 2, normal: 1, image_quota: 120, records: 2 }, queried_at: '2026-10-19T10:00:00Z' };
      else if (url === '/api/admin/upstream/refresh') body = { progress_id: 'progress-one' };
      else if (url.endsWith('/progress-one')) body = { done: true, total: 1, processed: 1, error_count: 0 };
      else throw new Error('Unexpected request: ' + url);
      return { ok: !forbidden, status: forbidden ? 403 : 200, json: async () => forbidden ? { detail: '需要管理员权限' } : body };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/admin.js'), 'utf8'), context); await flush();
  return { select, metrics, calls };
}
async function main() {
  const view = await page();
  assert.equal(view.select('#admin-shell').classList.contains('hidden'), false);
  assert.equal(view.select('#admin-page-title').textContent, '上游账号池');
  assert.equal(view.select('#upstream-accounts').children.length, 2);
  assert.equal(view.metrics.get('[data-metric="quota"]')[0].textContent, 120);
  assert.equal(view.select('#upstream-accounts').children[0].children[4].textContent, '120');
  view.select('#account-status').value = '正常'; view.select('#account-status').events.change();
  assert.equal(view.select('#upstream-accounts').children.length, 1);
  view.select('#select-all-accounts').checked = true; view.select('#select-all-accounts').events.change();
  assert.equal(view.select('#refresh-selected').disabled, false);
  view.select('#refresh-selected').events.click(); await flush();
  const refresh = view.calls.find((call) => call.url === '/api/admin/upstream/refresh');
  assert.deepEqual(JSON.parse(refresh.body), { account_ids: ['first'] });
  assert.equal(view.select('#refresh-progress').textContent, '额度已更新，表格显示最新查询结果。');
  assert.ok(view.calls.filter((call) => call.url === '/api/admin/upstream/accounts').length >= 2);
  const customer = await page(true);
  assert.equal(customer.select('#admin-login-view').classList.contains('hidden'), false);
  assert.equal(customer.select('#admin-shell').classList.contains('hidden'), true);
  assert.equal(customer.calls.length, 1, 'A non-admin session must never request the upstream pool');
  console.log('PASS: independent admin page, quota cells and totals, filtering, selected-account refresh, completion reload, non-admin login gate');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
