(function (root, factory) {
  'use strict';
  const exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  else root.AdminApiPanel = exported;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function parsePrompts(value) {
    const prompts = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (!prompts.length) throw new Error('请填写至少一行提示词');
    if (prompts.length > 50) throw new Error('每批最多 50 条提示词，请分批提交');
    if (prompts.some((prompt) => prompt.length > 4000)) throw new Error('每条提示词不能超过 4000 个字符');
    return prompts;
  }
  function createBatch({ prompts, concurrency, request, onUpdate = () => {} }) {
    if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 8) throw new Error('并发数需为 1 至 8');
    const jobs = prompts.map((prompt, index) => ({ prompt, index, status: 'queued', result: null, error: '' }));
    let cursor = 0, stopped = false, completion;
    const notify = () => onUpdate(jobs);
    async function worker() {
      while (!stopped && cursor < jobs.length) {
        const job = jobs[cursor++]; job.status = 'running'; notify();
        try { job.result = await request(job); job.status = 'succeeded'; }
        catch (error) { job.error = error.message || '请求未完成'; job.status = 'failed'; }
        notify();
      }
    }
    return { jobs,
      start() {
        if (!completion) completion = Promise.resolve().then(() => Promise.all(Array.from({ length: Math.min(concurrency, jobs.length) }, worker)));
        return completion;
      },
      stop() { stopped = true; for (const job of jobs) if (job.status === 'queued') job.status = 'cancelled'; notify(); },
    };
  }

  function mount({ api, toast, el, date }) {
    const $ = (selector) => document.querySelector(selector);
    const state = { models: [], keys: [], batch: null, running: false, initialized: false };
    const labels = { queued: '等待提交', running: '生成中', succeeded: '已完成', failed: '未完成', cancelled: '已停止' };
    const classes = { queued: '', running: 'limited', succeeded: 'normal', failed: 'abnormal', cancelled: 'disabled' };
    function hideSecret() { $('#api-key-secret').value = ''; $('#api-key-secret').type = 'password'; $('#api-key-reveal').textContent = '显示'; $('#api-key-once').classList.add('hidden'); }
    function estimate() {
      const count = $('#api-batch-prompts').value.split(/\r?\n/).filter((line) => line.trim()).length;
      const model = state.models.find((item) => item.id === $('#api-batch-model').value);
      $('#api-batch-estimate').textContent = `${count} 张图片 · 预计 ${count * (model?.credits_cost || 0)} 积分 · 失败自动退回积分`;
      $('#api-model-hint').textContent = model ? `${model.label} · ${model.credits_cost} 积分 / 张 · API 模型 ID：${model.id}` : '暂无可用模型，请稍后刷新面板';
      $('#api-batch-start').disabled = state.running || !model?.available;
    }
    function renderKeys() {
      $('#api-key-list').replaceChildren(...state.keys.map((key) => {
        const card = el('article', 'api-key-row');
        const details = el('div', 'api-key-details');
        const title = el('div', 'api-key-title'); title.append(el('strong', '', key.name), el('span', 'status ' + (key.revoked_at ? 'disabled' : 'normal'), key.revoked_at ? '已停用' : '可使用'));
        details.append(title, el('code', '', key.prefix + '…'), el('small', '', `并发上限 ${key.max_concurrency} · 创建 ${date(key.created_at)} · 最近调用 ${key.last_used_at ? date(key.last_used_at) : '尚未使用'}`));
        card.append(details);
        if (!key.revoked_at) {
          const button = el('button', 'secondary api-revoke', '停用'); button.type = 'button';
          button.addEventListener('click', async () => {
            if (!confirm(`停用“${key.name}”后，该密钥将无法继续调用。确定停用？`)) return;
            button.disabled = true;
            try { await api('/api/admin/api-keys/' + encodeURIComponent(key.id), { method: 'DELETE' }); hideSecret(); await loadKeys(); toast('密钥已停用'); }
            catch (error) { toast(error.message, true); button.disabled = false; }
          }); card.append(button);
        }
        return card;
      }));
      $('#api-key-empty').classList.toggle('hidden', state.keys.length > 0);
    }
    async function loadKeys() { const result = await api('/api/admin/api-keys'); state.keys = result.items; renderKeys(); }
    async function loadModels() {
      const result = await api('/api/images/models'); state.models = result.items;
      const select = $('#api-batch-model'), previous = select.value;
      select.replaceChildren(...state.models.map((model) => {
        const option = el('option', '', `${model.label} · ${model.credits_cost} 积分 / 张${model.available ? '' : '（暂不可用）'}`);
        option.value = model.id; option.disabled = !model.available; return option;
      }));
      select.value = state.models.find((model) => model.id === previous && model.available)?.id || state.models.find((model) => model.available)?.id || '';
      estimate();
    }
    function renderJobs(jobs) {
      const counts = Object.fromEntries(Object.keys(labels).map((status) => [status, jobs.filter((job) => job.status === status).length]));
      const finished = counts.succeeded + counts.failed + counts.cancelled;
      $('#api-batch-status').textContent = `${finished} / ${jobs.length} 已处理 · ${counts.running} 生成中 · ${counts.queued} 等待 · ${counts.succeeded} 成功 · ${counts.failed} 未完成${counts.cancelled ? ` · ${counts.cancelled} 已停止` : ''}`;
      $('#api-batch-progress').value = finished; $('#api-batch-progress').max = jobs.length;
      $('#api-batch-spinner').classList.toggle('hidden', counts.running === 0);
      $('#api-batch-results').replaceChildren(...jobs.map((job) => {
        const card = el('article', 'api-result');
        const media = el('div', 'api-result-media');
        const item = job.result?.item;
        if (item && /^\/api\/images\/files\/[0-9a-f]{24}$/.test(item.image_src)) {
          const link = el('a'); link.href = item.image_src; link.target = '_blank'; link.rel = 'noopener'; link.setAttribute('aria-label', '查看第 ' + (job.index + 1) + ' 张原图');
          const image = el('img'); image.src = item.image_src; image.alt = job.prompt; image.loading = 'lazy'; link.append(image); media.append(link);
        } else if (job.status === 'running') { const spinner = el('span', 'api-spinner'); spinner.setAttribute('aria-hidden', 'true'); media.append(spinner); }
        else media.append(el('span', 'api-result-number', String(job.index + 1).padStart(2, '0')));
        const details = el('div', 'api-result-detail');
        const head = el('div', 'api-result-head'); head.append(el('b', '', `#${String(job.index + 1).padStart(2, '0')}`), el('span', 'status ' + classes[job.status], labels[job.status]));
        details.append(head, el('p', 'api-result-prompt', job.prompt));
        if (job.error) details.append(el('p', 'review-error', job.error));
        if (item) details.append(el('small', '', `${item.credits_cost} 积分 · ${item.output_size || item.size} · 任务 ${item.id}`));
        card.append(media, details); return card;
      }));
    }
    function setBusy(busy) {
      state.running = busy; $('#api-batch-settings').disabled = busy;
      $('#api-batch-stop').disabled = !busy; $('#admin-logout').disabled = busy;
      $('#api-batch-start').textContent = busy ? '正在并发生成…' : '开始批量生成';
      estimate();
    }
    async function startBatch(event) {
      event.preventDefault(); if (state.running) return;
      let prompts;
      try { prompts = parsePrompts($('#api-batch-prompts').value); }
      catch (error) { toast(error.message, true); return; }
      const model = state.models.find((item) => item.id === $('#api-batch-model').value);
      if (!model?.available) return toast('请选择当前可用的图片模型', true);
      const settings = { model: model.id, size: $('#api-batch-size').value, quality: $('#api-batch-quality').value, resolution: $('#api-batch-resolution').value };
      const concurrency = Number($('#api-batch-concurrency').value);
      try {
        const runId = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, '0')).join('');
        state.batch = createBatch({ prompts, concurrency, onUpdate: renderJobs,
          request: async (job) => {
            try { return await api('/api/admin/images/generations', { method: 'POST', headers: { 'Idempotency-Key': `admin-${runId}-${job.index}` }, body: JSON.stringify({ ...settings, prompt: job.prompt }) }); }
            catch (error) { if (error instanceof TypeError) throw new Error('网络连接中断，任务可能仍在生成。请先到出图审核确认结果，再决定是否重试。'); throw error; }
          },
        });
        setBusy(true); $('#api-batch-output').classList.remove('hidden'); renderJobs(state.batch.jobs);
        await state.batch.start();
        const failed = state.batch.jobs.filter((job) => job.status === 'failed').length;
        toast(failed ? `本批已结束，${failed} 张未完成，请查看各条原因` : '本批任务已处理完成', failed > 0);
      } catch (error) { toast(error.message, true); }
      finally { setBusy(false); }
    }
    function initialize() {
      if (state.initialized) return; state.initialized = true;
      $('#api-base-url').textContent = location.origin + '/v1';
      $('#api-curl-example').textContent = `curl '${location.origin}/v1/images/generations' \\\n  -H 'Authorization: Bearer YOUR_ADMIN_API_KEY' \\\n  -H 'Content-Type: application/json' \\\n  -H 'Idempotency-Key: image-job-001' \\\n  -d '{"model":"gpt-image-2","prompt":"海边日落，电影感摄影","size":"1536x864","n":1,"response_format":"b64_json"}'`;
      $('#api-key-form').addEventListener('submit', async (event) => {
        event.preventDefault(); const button = $('#api-key-create'); if (button.disabled) return; button.disabled = true;
        try {
          const result = await api('/api/admin/api-keys', { method: 'POST', body: JSON.stringify({ name: $('#api-key-name').value.trim(), max_concurrency: Number($('#api-key-concurrency').value) }) });
          hideSecret(); $('#api-key-secret').value = result.key; $('#api-key-once').classList.remove('hidden'); $('#api-key-name').value = '';
          state.keys.unshift(result.item); renderKeys(); toast('密钥已创建，完整内容仅显示本次');
        } catch (error) { toast(error.message, true); }
        finally { button.disabled = false; }
      });
      $('#api-key-copy').addEventListener('click', async () => {
        try { await navigator.clipboard.writeText($('#api-key-secret').value); toast('密钥已复制'); }
        catch { $('#api-key-secret').type = 'text'; $('#api-key-reveal').textContent = '隐藏'; $('#api-key-secret').focus(); $('#api-key-secret').select(); toast('请按 Ctrl + C 复制选中的密钥'); }
      });
      $('#api-key-reveal').addEventListener('click', () => { const field = $('#api-key-secret'); field.type = field.type === 'password' ? 'text' : 'password'; $('#api-key-reveal').textContent = field.type === 'password' ? '显示' : '隐藏'; });
      $('#api-key-dismiss').addEventListener('click', hideSecret);
      $('#api-batch-form').addEventListener('submit', startBatch);
      $('#api-batch-prompts').addEventListener('input', estimate); $('#api-batch-model').addEventListener('change', estimate);
      $('#api-batch-stop').addEventListener('click', () => { if (!state.running) return; state.batch.stop(); $('#api-batch-stop').disabled = true; toast('已停止后续提交，正在生成的图片将继续完成'); });
      window.addEventListener('beforeunload', (event) => { if (state.running) { event.preventDefault(); event.returnValue = ''; } });
      window.addEventListener('pagehide', hideSecret);
    }
    return { async load() { initialize(); await Promise.all([loadKeys(), loadModels()]); }, clearSecret: hideSecret,
      stop() { state.batch?.stop(); hideSecret(); }, isRunning() { return state.running; } };
  }
  return { parsePrompts, createBatch, mount };
});
