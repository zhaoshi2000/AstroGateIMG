(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const pages = { overview: ['运营概览', '上游额度、用户与图片服务运行情况。'], accounts: ['上游账号池', '查看账号剩余图片次数、重置时间与运行状态。'], users: ['用户与积分', '查询客户账号，管理可用积分。'], jobs: ['出图审核', '查看完整提示词、生成图片和失败原因。'], api: ['API 与并发', '管理调用密钥，批量生成图片，逐项查看结果。'], codes: ['兑换码管理', '生成充值兑换码，查看使用情况。'] };
  const route = location.pathname.split('/')[2]; const page = pages[route] ? route : 'overview';
  const state = { accounts: [], selected: new Set(), refreshing: false, user: null, jobsPage: 1 };
  function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = String(text); return node; }
  function td(value, className = '') { return el('td', className, value ?? '—'); }
  function date(value) { if (!value) return '上游未提供'; try { const raw = value.includes('T') ? value : value.replace(' ', 'T') + 'Z'; return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(raw)); } catch { return '—'; } }
  const statusClass = { '正常': 'normal', '限流': 'limited', '异常': 'abnormal', '未知': 'abnormal', '禁用': 'disabled', succeeded: 'normal', pending: 'limited', failed: 'abnormal' };
  const statusText = { succeeded: '已完成', pending: '生成中', failed: '未完成' };
  function badge(value) { return el('span', 'status ' + (statusClass[value] || ''), statusText[value] || value); }
  function promptCell(job) { return el('div', 'review-prompt', job.prompt || '旧任务未保存提示词'); }
  function imageCell(job) {
    if (!job.image_url) return el('span', 'muted', job.status === 'succeeded' ? '图片已删除' : '未生成图片');
    const link = el('a', 'review-image-link'); link.href = job.image_url; link.target = '_blank'; link.rel = 'noopener'; link.setAttribute('aria-label', '打开生成图片原图');
    const image = el('img'); image.src = job.image_url; image.alt = '任务 ' + job.id + ' 生成的图片'; image.loading = 'lazy'; link.append(image); return link;
  }
  function reviewCard(job) {
    const card = el('article', 'review-card'); const info = el('div', 'review-card-info');
    const head = el('div', 'review-card-head'); head.append(el('strong', '', job.email || job.username), badge(job.status), el('span', 'muted', date(job.created_at)), el('code', '', job.id));
    const title = el('h3', '', '完整提示词'); info.append(head, title, promptCell(job));
    if (job.failure_reason) info.append(el('p', 'review-error', job.failure_reason + ' · 积分已退回'));
    info.append(el('small', 'muted', `本次计价 ${job.credits_cost} 积分`));
    const art = el('div', 'review-card-art'); art.append(imageCell(job)); card.append(info, art); return card;
  }
  let apiPanel = null;
  let toastTimer;
  function toast(message, error = false) { $('#admin-toast').textContent = message; $('#admin-toast').classList.toggle('error', error); $('#admin-toast').classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#admin-toast').classList.remove('show'), error ? 6500 : 3500); }
  function showLogin(message = '') { apiPanel?.stop(); state.user = null; $('#admin-loading').classList.add('hidden'); $('#admin-shell').classList.add('hidden'); $('#admin-login-view').classList.remove('hidden'); $('#admin-login-error').textContent = message; }
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    let body; try { body = await response.json(); } catch { throw new Error('服务暂时无法响应，请稍后重试'); }
    if (!response.ok) { let message = body.detail || '操作未完成'; if (Array.isArray(message)) message = message.map((item) => item.msg).join('；'); if ([401, 403].includes(response.status) && !path.endsWith('/login')) showLogin(response.status === 403 ? '请使用管理员账号登录此后台' : '请登录管理后台'); throw new Error(String(message)); }
    return body;
  }
  function metric(name, value) { document.querySelectorAll(`[data-metric="${name}"]`).forEach((node) => { node.textContent = value; }); }
  function updateSelection() { $('#refresh-selected').disabled = !state.selected.size || state.refreshing; $('#refresh-upstream').disabled = state.refreshing; $('#refresh-selected').textContent = state.selected.size ? `刷新选中 (${state.selected.size})` : '刷新选中额度'; const visible = filteredAccounts(); $('#select-all-accounts').checked = visible.length > 0 && visible.every((account) => state.selected.has(account.id)); $('#select-all-accounts').indeterminate = visible.some((account) => state.selected.has(account.id)) && !$('#select-all-accounts').checked; }
  function filteredAccounts() { const keyword = $('#account-search').value.trim().toLowerCase(); const status = $('#account-status').value; return state.accounts.filter((account) => (!keyword || account.email.toLowerCase().includes(keyword)) && (!status || account.status === status)); }
  function renderAccounts() {
    const accounts = filteredAccounts();
    $('#upstream-accounts').replaceChildren(...accounts.map((account) => {
      const row = el('tr'); const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.checked = state.selected.has(account.id); checkbox.setAttribute('aria-label', '选择账号 ' + account.email);
      checkbox.addEventListener('change', () => { checkbox.checked ? state.selected.add(account.id) : state.selected.delete(account.id); updateSelection(); });
      const select = td(); select.textContent = ''; select.append(checkbox); const email = td(account.email, 'account-email');
      if (account.credential_records > 1) email.append(el('small', '', `${account.credential_records} 条凭据已合并统计`));
      const status = td(); status.textContent = ''; status.append(badge(account.status));
      row.append(select, email, td(account.plan_type), status, td(account.image_quota, 'quota-number'), td(date(account.restore_at)), td(account.image_inflight), td(`${account.success} / ${account.fail}`), td(account.last_used_at ? date(account.last_used_at) : '尚未使用'));
      return row;
    }));
    $('#upstream-empty').classList.toggle('hidden', accounts.length > 0);
    $('#select-all-accounts').checked = accounts.length > 0 && accounts.every((account) => state.selected.has(account.id));
    $('#select-all-accounts').indeterminate = accounts.some((account) => state.selected.has(account.id)) && !$('#select-all-accounts').checked;
    updateSelection();
  }
  async function loadAccounts() {
    try {
      const result = await api('/api/admin/upstream/accounts'); state.accounts = result.items;
      state.selected = new Set([...state.selected].filter((id) => state.accounts.some((account) => account.id === id)));
      for (const [name, value] of Object.entries(result.stats)) metric(name === 'image_quota' ? 'quota' : name, value);
      $('#upstream-connection').textContent = '上游已连接'; $('#upstream-connection').className = 'status normal';
      $('#quota-read-time').textContent = `数据读取于 ${date(result.queried_at)}（北京时间）。点击“刷新额度”重新查询上游。`;
      renderAccounts();
    } catch (error) { $('#upstream-connection').textContent = '上游连接异常'; $('#upstream-connection').className = 'status abnormal'; $('#quota-read-time').textContent = error.message; throw error; }
  }
  async function loadOverview() {
    const result = await api('/api/admin/overview');
    $('#metric-users').textContent = result.customers.users; $('#metric-credits').textContent = result.customers.credits;
    $('#metric-today').textContent = result.jobs.generated_today; $('#metric-generated').textContent = result.jobs.generated; $('#metric-pending').textContent = result.jobs.pending;
    $('#metric-codes').textContent = result.codes.total; $('#metric-redeemed').textContent = result.codes.redeemed;
    $('#recent-jobs').replaceChildren(...result.recent_jobs.map((job) => { const row = el('tr'); const status = td(); status.textContent = ''; status.append(badge(job.status)); row.append(td(job.id.slice(0, 12)), td(job.username), status, td(job.failure_reason || '—', 'job-reason'), td(date(job.created_at))); return row; }));
    $('#recent-jobs-empty').classList.toggle('hidden', result.recent_jobs.length > 0);
  }
  async function refreshQuota(ids) {
    if (state.refreshing) return;
    state.refreshing = true; updateSelection(); $('#refresh-progress').classList.remove('hidden'); $('#refresh-progress').textContent = '正在提交上游额度刷新…';
    try {
      const result = await api('/api/admin/upstream/refresh', { method: 'POST', body: JSON.stringify({ account_ids: ids }) });
      const deadline = Date.now() + 120000;
      while (Date.now() < deadline && state.user) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const progress = await api('/api/admin/upstream/refresh/' + encodeURIComponent(result.progress_id));
        $('#refresh-progress').textContent = `正在查询上游额度：${progress.processed ?? 0} / ${progress.total ?? '—'}`;
        if (progress.done) {
          if (progress.error) throw new Error(progress.error);
          await loadAccounts(); $('#refresh-progress').textContent = progress.error_count ? `查询完成，${progress.error_count} 条凭据未能成功刷新，请查看账号状态。` : '额度已更新，表格显示最新查询结果。';
          toast(progress.error_count ? '部分账号刷新未成功，请查看状态' : '最新图片额度已更新', progress.error_count > 0); return;
        }
      }
      $('#refresh-progress').textContent = '上游仍在查询，稍后可点击“刷新面板”查看最新结果。';
    } catch (error) { $('#refresh-progress').textContent = error.message; toast(error.message, true); }
    finally { state.refreshing = false; updateSelection(); }
  }
  async function loadUsers() {
    const result = await api('/api/admin/users?q=' + encodeURIComponent($('#admin-user-search').value));
    $('#user-summary').textContent = `账号总数 ${result.stats.users} · 账户积分合计 ${result.stats.credits} · 显示最近匹配的 100 个账号`;
    $('#admin-users').replaceChildren(...result.items.map((user) => {
      const row = el('tr'); const action = td(); action.textContent = ''; const form = el('form', 'credit-form');
      const input = el('input'); input.type = 'number'; input.min = -1000000; input.max = 1000000; input.required = true; input.placeholder = '+100 / -10'; input.setAttribute('aria-label', user.username + ' 积分调整');
      const button = el('button', 'secondary', '调整'); button.type = 'submit'; form.append(input, button); action.append(form);
      form.addEventListener('submit', async (event) => { event.preventDefault(); const amount = Number(input.value); if (!amount || !Number.isInteger(amount)) return toast('请输入非零的整数积分', true); button.disabled = true; try { await api(`/api/admin/users/${user.id}/credits`, { method: 'POST', body: JSON.stringify({ amount, reason: '管理后台手动调整积分' }) }); await loadUsers(); toast('积分已调整，并记录到流水'); } catch (error) { toast(error.message, true); } finally { button.disabled = false; } });
      const records = td(); records.textContent = ''; const open = el('button', 'secondary', '查看记录'); open.type = 'button';
      open.addEventListener('click', () => loadUserRecords(user.id).catch((error) => toast(error.message, true))); records.append(open);
      row.append(td(user.email || user.username), td(user.is_admin ? '管理员' : '客户'), td(date(user.created_at)), td(user.credits), records, action); return row;
    })); $('#users-empty').classList.toggle('hidden', result.items.length > 0);
  }
  async function loadUserRecords(userId) {
    const result = await api(`/api/admin/users/${userId}/records`);
    $('#user-records-title').textContent = `${result.user.email || result.user.username} · 用户记录`;
    $('#user-records-summary').textContent = `当前余额 ${result.user.credits} 积分 · 最近 30 笔出图任务、30 笔积分流水、20 笔充值订单`;
    $('#user-all-jobs').href = `/admin/jobs?user_id=${result.user.id}`;
    $('#user-records-jobs').replaceChildren(...result.jobs.map((job) => {
      const row = el('tr'); const status = td(); status.textContent = ''; status.append(badge(job.status));
      const prompt = td(); prompt.textContent = ''; prompt.append(promptCell(job));
      const image = td(); image.textContent = ''; image.append(imageCell(job));
      row.append(td(job.id.slice(0, 12)), status, td(job.credits_cost), prompt, image, td(job.failure_reason || '—', 'job-reason'), td(date(job.created_at))); return row;
    }));
    $('#user-records-ledger').replaceChildren(...result.ledger.map((entry) => { const row = el('tr'); row.append(td(date(entry.created_at)), td(entry.amount > 0 ? `+${entry.amount}` : entry.amount), td(entry.balance_after), td(entry.description)); return row; }));
    $('#user-records-orders').replaceChildren(...result.payment_orders.map((order) => { const row = el('tr'); row.append(td(order.id), td(`¥${(order.amount_cents / 100).toFixed(2)}`), td(order.credits), td(order.status === 'paid' ? '已到账' : '待支付'), td(date(order.created_at))); return row; }));
    $('#user-records-panel').classList.remove('hidden'); $('#user-records-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  async function loadJobs() {
    const params = new URLSearchParams({ page: String(state.jobsPage) });
    const userId = $('#job-user-filter').value.trim(); const status = $('#job-status-filter').value;
    if (userId) params.set('user_id', userId);
    if (status) params.set('status', status);
    const result = await api('/api/admin/jobs?' + params.toString());
    $('#admin-jobs-list').replaceChildren(...result.items.map(reviewCard));
    $('#admin-jobs-empty').classList.toggle('hidden', result.items.length > 0);
    $('#jobs-page').textContent = `第 ${result.page} 页`;
    $('#jobs-prev').disabled = result.page <= 1;
    $('#jobs-next').disabled = !result.has_more;
  }
  async function loadCodes() {
    const result = await api('/api/admin/codes');
    $('#admin-codes').replaceChildren(...result.items.map((code) => { const row = el('tr'); const status = code.redeemed_username ? '已兑换' : new Date(code.expires_at) <= new Date() ? '已过期' : '未使用'; row.append(td(code.id), td('…' + code.suffix), td(code.credits), td(status), td(code.redeemed_username || '—'), td(date(code.expires_at))); return row; }));
    $('#codes-empty').classList.toggle('hidden', result.items.length > 0);
  }
  async function loadPage() { if (page === 'overview') { const results = await Promise.allSettled([loadOverview(), loadAccounts()]); results.forEach((result) => { if (result.status === 'rejected') toast(result.reason.message, true); }); } else if (page === 'accounts') await loadAccounts(); else if (page === 'users') await loadUsers(); else if (page === 'jobs') await loadJobs(); else if (page === 'api') { if (!apiPanel) apiPanel = AdminApiPanel.mount({ api, toast, el, date }); await apiPanel.load(); } else await loadCodes(); }
  async function enter(user) {
    state.user = user; $('#admin-loading').classList.add('hidden'); $('#admin-login-view').classList.add('hidden'); $('#admin-shell').classList.remove('hidden'); $('#admin-name').textContent = user.username;
    $('#admin-page-title').textContent = pages[page][0]; $('#admin-page-description').textContent = pages[page][1]; $('#admin-breadcrumb').textContent = pages[page][0];
    document.querySelectorAll('[data-page]').forEach((link) => link.classList.toggle('active', link.dataset.page === page));
    Object.keys(pages).forEach((name) => $('#' + name + '-section').classList.toggle('hidden', name !== page));
    if (page === 'jobs') $('#job-user-filter').value = new URLSearchParams(location.search).get('user_id') || '';
    if (location.pathname === '/admin/login') history.replaceState(null, '', '/admin');
    await loadPage();
  }
  $('#admin-login-form').addEventListener('submit', async (event) => { event.preventDefault(); const button = $('#admin-login-submit'); button.disabled = true; $('#admin-login-error').textContent = ''; try { const result = await api('/api/admin/login', { method: 'POST', body: JSON.stringify({ username: $('#admin-username').value.trim(), password: $('#admin-password').value }) }); $('#admin-password').value = ''; await enter(result.user); } catch (error) { $('#admin-login-error').textContent = error.message; } finally { button.disabled = false; } });
  $('#admin-logout').addEventListener('click', async () => { try { await api('/api/auth/logout', { method: 'POST' }); showLogin(); history.replaceState(null, '', '/admin/login'); } catch (error) { toast(error.message, true); } });
  $('#reload-dashboard').addEventListener('click', async () => { const button = $('#reload-dashboard'); button.disabled = true; try { await loadPage(); } catch (error) { toast(error.message, true); } finally { button.disabled = false; } });
  $('#account-search').addEventListener('input', renderAccounts); $('#account-status').addEventListener('change', renderAccounts);
  $('#select-all-accounts').addEventListener('change', () => { filteredAccounts().forEach((account) => $('#select-all-accounts').checked ? state.selected.add(account.id) : state.selected.delete(account.id)); renderAccounts(); });
  $('#refresh-selected').addEventListener('click', () => { if (state.selected.size) refreshQuota([...state.selected]); }); $('#refresh-upstream').addEventListener('click', () => refreshQuota([]));
  $('#admin-user-search-form').addEventListener('submit', (event) => { event.preventDefault(); loadUsers().catch((error) => toast(error.message, true)); });
  $('#close-user-records').addEventListener('click', () => $('#user-records-panel').classList.add('hidden'));
  $('#job-filter-form').addEventListener('submit', (event) => { event.preventDefault(); state.jobsPage = 1; loadJobs().catch((error) => toast(error.message, true)); });
  $('#job-filter-clear').addEventListener('click', () => { $('#job-user-filter').value = ''; $('#job-status-filter').value = ''; state.jobsPage = 1; loadJobs().catch((error) => toast(error.message, true)); });
  $('#jobs-prev').addEventListener('click', () => { if (state.jobsPage > 1) { state.jobsPage--; loadJobs().catch((error) => toast(error.message, true)); } });
  $('#jobs-next').addEventListener('click', () => { state.jobsPage++; loadJobs().catch((error) => toast(error.message, true)); });
  $('#admin-code-form').addEventListener('submit', async (event) => { event.preventDefault(); const button = event.target.querySelector('button'); button.disabled = true; try { const result = await api('/api/admin/codes', { method: 'POST', body: JSON.stringify({ credits: Number($('#code-credits').value), count: Number($('#code-count').value), expires_days: Number($('#code-days').value) }) }); $('#new-admin-code-text').value = result.codes.join('\n'); $('#new-admin-codes').classList.remove('hidden'); await loadCodes(); toast('兑换码已生成，请保存完整内容'); } catch (error) { toast(error.message, true); } finally { button.disabled = false; } });
  $('#copy-admin-codes').addEventListener('click', async () => { try { await navigator.clipboard.writeText($('#new-admin-code-text').value); toast('兑换码已复制'); } catch { $('#new-admin-code-text').focus(); $('#new-admin-code-text').select(); toast('请按 Ctrl + C 复制选中的兑换码'); } });
  setInterval(() => { if (state.user && !state.refreshing && document.visibilityState === 'visible' && ['overview', 'accounts'].includes(page)) loadAccounts().catch(() => {}); }, 30000);
  api('/api/admin/me').then((result) => enter(result.user)).catch((error) => { if (!state.user) showLogin(error.message); else toast(error.message, true); });
})();
