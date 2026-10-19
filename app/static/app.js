const state = { mode: "login", user: null, history: [], referenceImage: "", referenceName: "" };
const $ = (selector) => document.querySelector(selector);

function errorText(payload) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (typeof payload?.error === "string") return payload.error;
  return "请求未完成，请稍后重试";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) throw new Error(errorText(payload));
  return payload;
}

let toastTimer;
function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 3200);
}

function setAuthMode(mode) {
  state.mode = mode;
  const registering = mode === "register";
  $("#login-tab").classList.toggle("active", !registering);
  $("#register-tab").classList.toggle("active", registering);
  $("#auth-kicker").textContent = registering ? "START YOUR STUDIO" : "WELCOME BACK";
  $("#auth-title").textContent = registering ? "创建你的工作台" : "继续创作";
  $("#auth-subtitle").textContent = registering ? "注册后即可拥有专属的图像创作空间。" : "登录后访问你的个人图像工作台。";
  $("#auth-submit-text").textContent = registering ? "创建账号" : "登录工作台";
  $("#auth-footnote").innerHTML = registering
    ? '已有账号？<button type="button" id="switch-to-login">返回登录</button>'
    : '还没有账号？<button type="button" id="switch-to-register">现在注册</button>';
  $(registering ? "#switch-to-login" : "#switch-to-register").addEventListener("click", () => setAuthMode(registering ? "login" : "register"));
  $("#password").autocomplete = registering ? "new-password" : "current-password";
}

function formattedDate(value) {
  try { return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
  catch { return value || "刚刚"; }
}

function safeImageSource(source) {
  return typeof source === "string" && (/^https?:\/\//.test(source) || /^data:image\//.test(source)) ? source : "";
}

function createHistoryCard(item) {
  const card = document.createElement("article");
  card.className = "history-card";
  const image = document.createElement("img");
  image.src = safeImageSource(item.image_src);
  image.alt = item.prompt;
  image.loading = "lazy";
  const info = document.createElement("div");
  info.className = "history-info";
  const prompt = document.createElement("p"); prompt.textContent = item.prompt;
  const date = document.createElement("span"); date.textContent = `${item.mode === "image-to-image" ? "图生图" : "文生图"} · ${item.model} · ${formattedDate(item.created_at)}`;
  info.append(prompt, date);
  const remove = document.createElement("button");
  remove.className = "delete-history"; remove.type = "button"; remove.textContent = "×"; remove.title = "删除记录";
  remove.addEventListener("click", () => deleteHistory(item.id));
  card.append(image, info, remove);
  return card;
}

function renderHistory() {
  const grid = $("#history-grid");
  grid.replaceChildren(...state.history.filter((item) => safeImageSource(item.image_src)).map(createHistoryCard));
  $("#history-empty").classList.toggle("is-hidden", state.history.length > 0);
  $("#history-count").textContent = `${state.history.length} 件作品`;
}

async function loadHistory() {
  const data = await api("/api/images/history");
  state.history = data.items || [];
  renderHistory();
}

function showResult(item) {
  const source = safeImageSource(item.image_src);
  if (!source) return;
  $("#generation-empty").classList.add("is-hidden");
  $("#generation-result").classList.remove("is-hidden");
  $("#result-image").src = source;
  $("#result-prompt").textContent = item.prompt;
  const metadata = $("#result-meta");
  metadata.replaceChildren(...[item.model, item.size, item.quality === "medium" ? "标准质量" : `${item.quality} 质量`].map((value) => {
    const badge = document.createElement("span"); badge.textContent = value; return badge;
  }));
  $("#download-result").href = source;
}

async function deleteHistory(id) {
  try {
    await api(`/api/images/history/${encodeURIComponent(id)}`, { method: "DELETE" });
    state.history = state.history.filter((item) => item.id !== id);
    renderHistory();
    showToast("已从图集中移除");
  } catch (error) { showToast(error.message, true); }
}

async function updateUpstreamStatus() {
  try {
    const config = await api("/api/config");
    const status = $("#upstream-status");
    status.classList.toggle("ready", Boolean(config.upstream_configured));
    status.classList.toggle("problem", !config.upstream_configured);
    status.innerHTML = config.upstream_configured ? "<i></i> 图片服务已连接" : "<i></i> 等待服务配置";
  } catch { /* The rest of the application can still load. */ }
}

async function enterStudio(user) {
  state.user = user;
  $("#auth-view").classList.add("is-hidden");
  $("#app-view").classList.remove("is-hidden");
  $("#user-name").textContent = user.username;
  $("#user-avatar").textContent = user.username.slice(0, 1).toUpperCase();
  $("#today-label").textContent = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "short" }).format(new Date());
  await Promise.all([loadHistory(), updateUpstreamStatus()]);
}

$("#login-tab").addEventListener("click", () => setAuthMode("login"));
$("#register-tab").addEventListener("click", () => setAuthMode("register"));
$("#switch-to-register").addEventListener("click", () => setAuthMode("register"));

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $(".auth-submit");
  const username = $("#username").value.trim();
  const password = $("#password").value;
  button.disabled = true;
  try {
    const data = await api(`/api/auth/${state.mode === "register" ? "register" : "login"}`, {
      method: "POST", body: JSON.stringify({ username, password }),
    });
    await enterStudio(data.user);
    showToast(state.mode === "register" ? "账号创建完成，欢迎开始创作" : "欢迎回来");
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
});

$("#prompt").addEventListener("input", (event) => { $("#prompt-count").textContent = `${event.target.value.length} / 4000`; });
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => {
  $("#prompt").value = button.dataset.prompt;
  $("#prompt").dispatchEvent(new Event("input"));
  $("#prompt").focus();
}));

$("#reference-image").addEventListener("change", () => {
  const file = $("#reference-image").files?.[0];
  if (!file) return;
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) return showToast("仅支持 PNG、JPG 或 WebP 图片", true);
  if (file.size > 50 * 1024 * 1024) return showToast("参考图不能超过 50MB", true);
  const reader = new FileReader();
  reader.onload = () => {
    state.referenceImage = String(reader.result || "");
    state.referenceName = file.name;
    $("#reference-preview-image").src = state.referenceImage;
    $("#reference-name").textContent = file.name;
    $("#reference-preview").classList.remove("is-hidden");
    $(".reference-picker").classList.add("is-hidden");
    $("#generate-button span:last-child").textContent = "开始图生图";
  };
  reader.readAsDataURL(file);
});

$("#remove-reference").addEventListener("click", () => {
  state.referenceImage = ""; state.referenceName = "";
  $("#reference-image").value = "";
  $("#reference-preview").classList.add("is-hidden");
  $(".reference-picker").classList.remove("is-hidden");
  $("#generate-button span:last-child").textContent = "开始生成";
});

$("#generation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#generate-button");
  const prompt = $("#prompt").value.trim();
  if (!prompt) return showToast("先描述你想生成的画面", true);
  button.disabled = true; button.innerHTML = '<span class="spark">✦</span><span>正在生成…</span>';
  try {
    const generationPayload = {
      prompt, model: $("#model").value, size: $("#size").value, quality: $("#quality").value,
    };
    const endpoint = state.referenceImage ? "/api/images/edits" : "/api/images/generations";
    if (state.referenceImage) generationPayload.image_data_url = state.referenceImage;
    const data = await api(endpoint, { method: "POST", body: JSON.stringify(generationPayload) });
    showResult(data.item);
    state.history.unshift(data.item); renderHistory();
    showToast("作品已生成并保存到图集");
    $("#generation-result").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = `<span class="spark">✦</span><span>${state.referenceImage ? "开始图生图" : "开始生成"}</span>`; }
});

$("#logout-button").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  state.user = null; state.history = [];
  $("#app-view").classList.add("is-hidden"); $("#auth-view").classList.remove("is-hidden");
  $("#auth-form").reset(); setAuthMode("login");
});

(async () => {
  try { const data = await api("/api/auth/me"); await enterStudio(data.user); }
  catch { setAuthMode("login"); }
})();
