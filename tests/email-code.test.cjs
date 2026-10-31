const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8');
class Element {
  constructor() {
    this.events = {}; this.value = ''; this.disabled = false;
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute() {}
  append() {}
  replaceChildren() {}
  reportValidity() { return true; }
  focus() {}
}
const response = (status, body, retryAfter = null) => ({
  ok: status >= 200 && status < 300, status, json: async () => body,
  headers: { get: () => retryAfter },
});
async function page({ email = 'artist@example.com', storage = new Map(), clock = { now: 1000000 }, send } = {}) {
  const elements = new Map(); const events = {}; const intervals = new Map();
  const select = (selector) => { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); };
  select('#username').value = email;
  let calls = 0; let intervalId = 0;
  const context = vm.createContext({
    document: { querySelector: select, querySelectorAll: () => [], createElement: () => new Element(), addEventListener() {} },
    window: { addEventListener: (name, callback) => { events[name] = callback; } },
    location: { pathname: '/register' }, navigator: {}, URL, URLSearchParams, Intl,
    Date: class extends Date { static now() { return clock.now; } },
    localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    setTimeout: () => 0, clearTimeout() {},
    setInterval: (callback) => { intervals.set(++intervalId, callback); return intervalId; },
    clearInterval: (id) => intervals.delete(id),
    fetch: async (url) => {
      if (url === '/api/auth/me') return response(401, { detail: '请先登录' });
      assert.equal(url, '/api/auth/email-code'); calls++;
      return send ? send() : response(200, { resend_after: 60 });
    },
  });
  vm.runInContext(source, context);
  await new Promise(setImmediate);
  return {
    select, events, context, clock, storage, calls: () => calls,
    click: () => select('#send-email-code').events.click(),
    tick: (seconds) => { clock.now += seconds * 1000; [...intervals.values()].forEach((callback) => callback()); },
    email: (value) => { select('#username').value = value; select('#username').events.input(); },
  };
}
async function main() {
  const first = await page();
  await first.click();
  assert.equal(first.select('#send-email-code').textContent, '60 秒后重发');
  assert.equal(first.select('#send-email-code').disabled, true);
  first.tick(1);
  assert.equal(first.select('#send-email-code').textContent, '59 秒后重发');
  await first.click();
  assert.equal(first.calls(), 1, 'Repeated clicks during cooldown must not send another request');

  const refreshed = await page({ email: 'ARTIST@EXAMPLE.COM', storage: first.storage, clock: first.clock });
  assert.equal(refreshed.select('#send-email-code').textContent, '59 秒后重发', 'Reload must retain the email cooldown');
  refreshed.email('other@example.com');
  assert.equal(refreshed.select('#send-email-code').disabled, false);
  refreshed.email('artist@example.com');
  assert.equal(refreshed.select('#send-email-code').textContent, '59 秒后重发', 'Switching emails must not reset the original cooldown');
  refreshed.tick(59);
  assert.equal(refreshed.select('#send-email-code').textContent, '获取验证码');
  assert.equal(refreshed.select('#send-email-code').disabled, false);

  const limited = await page({ send: () => response(429, { detail: '请 37 秒后重新获取验证码' }, '37') });
  await limited.click();
  assert.equal(limited.select('#send-email-code').textContent, '37 秒后重发', 'Server rejection must start the actual remaining countdown');
  limited.tick(7);
  assert.equal(limited.select('#send-email-code').textContent, '30 秒后重发');

  const failed = await page({ send: () => response(502, { detail: '邮件暂时未能发送' }, '42') });
  await failed.click();
  assert.equal(failed.select('#send-email-code').textContent, '42 秒后重发');
  assert.equal(failed.select('#email-code-hint').textContent, '邮件暂时未能发送', 'SMTP errors must not claim delivery');

  const unavailable = await page({ send: () => { throw new Error('网络未连接'); } });
  await unavailable.click();
  assert.equal(unavailable.select('#send-email-code').disabled, false, 'A network error without a server cooldown must allow retrying');

  let resolveSend;
  const pending = await page({ send: () => new Promise((resolve) => { resolveSend = resolve; }) });
  const request = pending.click();
  pending.events.pageshow();
  assert.equal(pending.select('#send-email-code').textContent, '正在发送…');
  assert.equal(pending.select('#send-email-code').disabled, true);
  resolveSend(response(200, { resend_after: 55 }));
  await request;
  assert.equal(pending.select('#send-email-code').textContent, '55 秒后重发');
  console.log('PASS: live countdown, duplicate-click guard, reload persistence, email switching, server cooldown, SMTP and network errors');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
