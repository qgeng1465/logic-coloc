// 后端地址。默认为空 = 与页面同源（推荐：前后端部署在同一个域名下，
// 这样静态资源、/api、CORS 全部天然打通）。
// 若必须前后端分离部署，在 index.html 的 <script src="/static/app.js"> 之前插入：
//   <script>window.LC_API_BASE = "https://你的后端域名";</script>
// 所有请求都经 apiUrl() 拼地址，且都必须走 authFetch（它会补 Authorization 头并
// 统一处理 401）——直接调 fetch 会拿到一串 401 且不跳登录页。
const API_BASE = window.LC_API_BASE || "";
// 黑客松演示模式：核心体验即插即用，不把笔记、头像、历史写入服务器。
// 关闭页面后数据自然消失；正式产品再将它切换为 false 并启用持久化。
const DEMO_MODE = true;
const apiUrl = (path) => `${API_BASE}${path}`;
let dueEndpointUnavailable = false;
const state = { explainSessionId: null, discoverSessionId: null, activeKnowledgePanel: "discoverPanel", importTarget: "discoverText", importPanelTarget: "discoverPanel", explainConversation: [], zhihuMode: "quick", zhihuResearchItems: [], researchNoteDraft: null, discoverAbortController: null, discoverRequestId: null };
// 草稿也按账号隔离。这里先给未登录时的默认值，登录后由 applyStorageScope 按 uid
// 重建 key 并重读文本（见下方「账号与登录态」）。
let draftStorageKeys = {
  explainPanel: "logic_coloc_draft_explain_v2",
  discoverPanel: "logic_coloc_draft_discover_v2",
};
let explainDraftText = "";
let discoverDraftText = "";
// V1 曾在两个功能间复用草稿；不再迁移这些值，避免污染继续复活。
["draft_explain", "draft_discover", "globalInputText", "currentText", "draft", "inputText"].forEach((key) => localStorage.removeItem(key));
let historyFilterType = "跨学科理解", pendingHistoryDeleteId = null;

/* ============================ 账号与登录态 ============================
   后端所有涉及用户数据与 LLM 的路由都要求 `Authorization: Bearer <token>`。
   token 是无状态的 HMAC 签名串，服务端**无法吊销** —— 退出登录只能删掉本地这一份，
   这是刻意的取舍（换来的好处是服务重启后已登录的人不用重新登）。 */
const AUTH_TOKEN_KEY = "logic_coloc_auth_v1";
// 原生 fetch 的引用。authFetch 内部必须用它，用回 fetch 就是无限递归。
const rawFetch = window.fetch.bind(window);
const getToken = () => localStorage.getItem(AUTH_TOKEN_KEY) || "";
const setToken = (token) => { if (token) localStorage.setItem(AUTH_TOKEN_KEY, token); else localStorage.removeItem(AUTH_TOKEN_KEY); };

/* ---- 匿名身份 ----
   打开网站就能用，不需要登录。首次访问静默向 /api/auth/guest 要一个匿名身份 ——
   服务端签发的是和正式账号一模一样的无状态 token，数据照常按 uid 隔离，所以
   「我的知识」「卡片」「能量」一样能用。

   产品上**没有任何登录入口**（2026-09-13 起）：设置里的绑定/登录/退出三个按钮和
   兜底层那张登录注册表单都去掉了。后端那套注册/登录/绑定接口没删，只是前端不再
   暴露 —— 删后端只会连累测试和文档，对用户看到的东西毫无影响。

   GUEST_RETRY_KEY 是**一次性**重试标记：防止「建了身份 → /api/auth/me 仍然 401 →
   再建」这种无限刷新。token 失效时我们既解不出 uid、也没有密码可问用户要（产品上
   根本没有登录），所以唯一的出路就是再建一个。 */
const GUEST_RETRY_KEY = "logic_coloc_guest_retry_v1";

async function createGuestSession() {
  try {
    const response = await rawFetch(apiUrl("/api/auth/guest"), { method: "POST" });
    if (!response.ok) return false;
    const data = await response.json().catch(() => ({}));
    if (!data.token) return false;
    setToken(data.token);
    return true;
  } catch {
    return false;
  }
}

async function authFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await rawFetch(url, { ...options, headers });
  // 401 = 没带 token / 签名不对 / 已过期 / 身份已不存在。四种情况前端处理完全一样：
  // 清掉本地 token，弹兜底层让用户刷新（刷新即重新建一个匿名身份）。后端也刻意只回
  // 同一句话，不细分是哪种。
  if (response.status === 401 && !options.skipAuthRedirect) handleUnauthorized();
  return response;
}

let unauthorizedHandled = false;
function handleUnauthorized() {
  // 一屏之内可能同时飞着十几个请求，只跳一次就够了。
  if (unauthorizedHandled) return;
  unauthorizedHandled = true;
  setToken("");
  showAuthPage("连接已中断，请刷新页面重试。");
}

/* ---- 本地存储按账号分命名空间 ----
   换个身份就不该看到上一个人的书架/草稿。所有 key 后面拼 `::<uid>`；还没有身份时
   为空串（= 旧的全局 key，只用于拿到身份前那一小段时间）。
   2026-09-13 起产品上只有匿名身份，没有「换个人登录」这回事了 —— 但同一台浏览器
   里 token 失效后会重建一个**新 uid**，命名空间仍然照常起作用，所以这层没动。 */
const buildStorageKeys = (uid) => {
  const scope = uid ? `::${uid}` : "";
  return { books: `logic_coloc_books_v1${scope}`, notes: `logic_coloc_notes_v1${scope}`, history: `logic_coloc_history_v1${scope}`, explainHistory: `explain_history${scope}`, discoverHistory: `discover_history${scope}`, researchHistory: `research_history${scope}`, points: `logic_coloc_points_v1${scope}`, awards: `logic_coloc_review_awarded_date_v1${scope}`, user: `logic_coloc_user_v1${scope}`, firstLogin: `logic_coloc_first_login_v1${scope}`, categories: `logic_coloc_note_categories_v1${scope}`, attachmentDrafts: `logic_coloc_attachment_drafts_v1${scope}`, reviewSession: `logic_coloc_review_session_v1${scope}` };
};
const buildDraftKeys = (uid) => {
  const scope = uid ? `::${uid}` : "";
  return { explainPanel: `logic_coloc_draft_explain_v2${scope}`, discoverPanel: `logic_coloc_draft_discover_v2${scope}` };
};

function applyStorageScope(uid) {
  storageKeys = buildStorageKeys(uid);
  draftStorageKeys = buildDraftKeys(uid);
  // 草稿是读进内存的普通变量，换了命名空间必须重读，否则会继续拿着上一个人的文本。
  explainDraftText = localStorage.getItem(draftStorageKeys.explainPanel) || "";
  discoverDraftText = localStorage.getItem(draftStorageKeys.discoverPanel) || "";
}

// /api/auth/me 返回后灌进来，之后是资料的服务端权威副本（本地只做乐观显示）。
let authProfile = null;
let currentUser = null;

const DEFAULT_USER = { nickname: "学术萌新", signature: "记录每一次深度思考，留给未来的自己。", avatarUrl: "" };

/* 兜底提示层：只在**拿不到匿名身份**（断网 / 服务端没起来 / 身份失效）时盖住整页，
   给一句说明和一个「刷新重试」。正常打开网站它永远不出现。
   2026-09-13 之前这里是一张登录/注册表单，现在整层里一个字都不提登录。 */
function showAuthPage(message = "") {
  $("authPage").hidden = false;
  $("authNotice").textContent = message || "服务暂时不可用，请刷新页面重试。";
  document.body.classList.add("auth-open");
}

function hideAuthPage() {
  $("authPage").hidden = true;
  document.body.classList.remove("auth-open");
}

async function checkBackendHealth() {
  try {
    const response = await authFetch(apiUrl("/api/health"), { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.code !== 0 || !data.llm_bridge?.ok) {
      console.warn("Logic-Coloc backend dependency is unavailable", data);
    }
    return data;
  } catch (error) {
    console.error("Logic-Coloc health check failed", error);
    return null;
  }
}

const $ = (id) => document.getElementById(id) || { id, hidden: true, textContent: "", value: "", replaceChildren() {}, classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, addEventListener() {}, append() {}, appendChild() {}, closest() { return null; }, querySelector() { return null; } };
const profileLabels = {
  system_closure: "系统封闭性",
  causal_chain_length: "因果链长度",
  negative_feedback_strength: "负反馈强度",
  randomness_entropy: "随机性 / 熵",
  zero_sum_resource_level: "资源零和性",
};

let loadingTimer = null;
let loadingStartedAt = 0;
function setLoading(active, text = "正在分析…", trigger = null) {
  // 提示拆成上下两行：这一行是任务说明，下面 loadingTimer 那行是「已等待 N 秒…」。
  // 原先两段拼在同一个 span 里，字串一长就是超宽的一整条，窄屏上还会从中间断开、
  // 把「18 秒」拆到两行去。现在各占一行，宽度也由 CSS 收住了。
  $("loadingText").textContent = text;
  $("loadingTimer").textContent = "";
  $("loading").hidden = !active;
  $("cancelLoading").hidden = !(active && trigger?.id === "discoverButton");
  if (trigger) trigger.disabled = active;
  window.clearInterval(loadingTimer);
  if (active) {
    loadingStartedAt = Date.now();
    const tick = () => {
      const seconds = Math.floor((Date.now() - loadingStartedAt) / 1000);
      $("loadingTimer").textContent = `已等待 ${seconds} 秒，请不要重复提交…`;
    };
    tick();   // 立刻显示：等第一次 interval 才写的话，提示会先窄后高地跳一下
    loadingTimer = window.setInterval(tick, 1000);
  }
}

function showError(id, message) {
  const element = $(id);
  element.textContent = message || "请求失败，请稍后重试。";
  element.hidden = !message;
}

function attachImageFallback(image, container, fallbackText = "") {
  image.addEventListener("error", () => {
    image.remove();
    container.classList.add("image-fallback");
    if (fallbackText && !container.textContent.trim()) container.textContent = fallbackText;
  }, { once: true });
  return image;
}

async function request(path, payload, options = {}) {
  const response = await authFetch(apiUrl(path), {
    method: options.method || "POST",
    headers: payload == null ? {} : { "Content-Type": "application/json" },
    body: payload == null ? undefined : JSON.stringify(payload),
    signal: options.signal,
  });
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const detail = data.error?.message || (typeof data.detail === "string" ? data.detail : "服务暂时不可用");
    console.error("Logic-Coloc API request failed", { path, status: response.status, data });
    const error = new Error(`${detail}（HTTP ${response.status}）`); error.status = response.status; throw error;
  }
  if (data.code && data.code !== 0) console.error("Logic-Coloc API business error", { path, status: response.status, data });
  return data;
}

// 知乎内容不再抓取网页正文；当前统一经 /api/zhihu/search 调用官方开放平台搜索。

// 复习排期，单位秒，**一律以「天」为粒度**。必须与后端 api/card_store.py 的
// EBBINGHAUS_INTERVALS + EBBINGHAUS_LONG_INTERVALS 首尾相接后逐项一致 —— 两边各算一次
// 排期（前端离线先落盘、后端 review_card 也会算），表不一致就会出现「同一次复习两个到期日」。
// 页面上那几个「按天」的地方（曲线图 x 轴、学习日志那五栏的分界）也都是照这张表写的 ——
// 这张表是排期唯一的事实来源，别再另抄一份。
const REVIEW_INTERVAL_SECONDS = [86400, 2 * 86400, 4 * 86400, 7 * 86400, 15 * 86400, 30 * 86400, 90 * 86400, 180 * 86400, 365 * 86400];
const newReviewFields = () => ({ status: "unmastered", last_reviewed_at: null, next_review_due: new Date().toISOString(), review_stage: 0, ease_factor: 2.5 });
// 到期与否**只看时间**，不看存下来的 status（status 是下面 cardStatusOf 派生的结果）。
function isCardDue(card, now = Date.now()) { const due = Date.parse(card.next_review_due || card.createdAt || 0); return !Number.isFinite(due) || due <= now; }
// 卡片状态是**派生**的：到期 = 未掌握（该复习了），没到期 = 已掌握（这一轮已经巩固过）。
// 以前「已掌握」要求连续 8 次「认识」，于是刚复习完的卡既不算已掌握、又还没到期，
// 三个 tab 的徽章会出现「未掌握 0 + 已掌握 0 ≠ 全部 1」。现在复习完就是已掌握，
// 到点自动变回未掌握 —— 不需要任何定时任务，每次读卡片时重算一遍即可（见 getBooks）。
function cardStatusOf(card, now = Date.now()) { return isCardDue(card, now) ? "unmastered" : "mastered"; }
// 「还有多久到期」的人话，只用在复习空态那一行，粗粒度就够。
function formatReviewEta(ms) { const minutes = Math.max(1, Math.round(ms / 60000)); if (minutes < 60) return `${minutes} 分钟`; const hours = Math.round(minutes / 60); if (hours < 24) return `${hours} 小时`; return `${Math.max(1, Math.round(hours / 24))} 天`; }
// 复习队列空了时的那句话。队列空了只可能是两种：一张卡都没有，或者全都复习过了
// （未掌握 ⟺ 到期，所以「有卡但没到期」就等于「已掌握」）。后一种要顺带说清下次什么时候来 ——
// 用户会问「我都复习完了，它什么时候再考我」，这句就是回答。
function reviewTestEmptyMessage() { const cards = allReviewCards(); if (!cards.length) return "还没有卡片哦 —— 去「卡片」里新建，或从一次同源发现里把结果存成卡片吧！"; const upcoming = cards.map((card) => Date.parse(card.next_review_due || 0)).filter((value) => Number.isFinite(value)).sort((a, b) => a - b)[0]; const eta = Number.isFinite(upcoming) ? `最近一张 ${formatReviewEta(upcoming - Date.now())}后到期` : "下一张的到期时间还没排"; return `今天的复习都完成啦！${cards.length} 张都已掌握，${eta}，到点会自动回到未掌握。`; }
function scheduleCardLocally(card, quality) {
  const now = new Date(); card.last_reviewed_at = now.toISOString();
  if (quality === "忘记了") { card.review_stage = 0; card.next_review_due = now.toISOString(); }
  // 模糊：算复习过（所以是已掌握），但排期拉回最短一档，明天再来一次。
  else if (quality === "模糊") card.next_review_due = new Date(now.getTime() + 86400 * 1000).toISOString();
  else { const stage = Math.max(0, Number(card.review_stage) || 0); card.review_stage = stage + 1; const index = Math.min(card.review_stage - 1, REVIEW_INTERVAL_SECONDS.length - 1); card.next_review_due = new Date(now.getTime() + REVIEW_INTERVAL_SECONDS[index] * 1000).toISOString(); }
  // 状态一律由「排到什么时候」推出来，不再单算：复习完（除了「忘记了」= 立刻到期）就是已掌握。
  card.status = cardStatusOf(card, now.getTime());
  card.ease_factor = Number(card.ease_factor) || 2.5; return card;
}

let toastTimer;
function showToast(message) { window.clearTimeout(toastTimer); $("toast").textContent = message; $("toast").hidden = false; toastTimer = window.setTimeout(() => { $("toast").hidden = true; }, 2000); }
let lastDueNotificationCount = 0, currentDueNotificationCount = 0;
function renderNotificationBadge(count) { const badge = $("notificationBadge"), value = Math.max(0, Number(count) || 0); badge.textContent = value > 99 ? "99+" : String(value); badge.hidden = value === 0; // 铃铛数的是「今天到期的」，跟复习页 tab 徽章的「未掌握（状态）」不是一回事，措辞上分开。
  $("notificationButton").setAttribute("aria-label", value ? `查看 ${value} 张今天到期的卡片` : "今天没有到期的卡片"); }
async function checkDueCards({ notify = false } = {}) {
  const localDue = allReviewCards().filter((card) => isCardDue(card));
  // 本地卡片是复习页的权威数据源；后端只做可用性探测，避免两套存储重复计数。
  if (!dueEndpointUnavailable) { try { const response = await authFetch(apiUrl("/api/cards/due"), { cache: "no-store" }); if (response.status === 404) dueEndpointUnavailable = true; } catch { dueEndpointUnavailable = true; } }
  const count = localDue.length; currentDueNotificationCount = count; renderNotificationBadge(count);
  if (notify && count > lastDueNotificationCount) showToast(`📚 刘看山提醒你，有 ${count} 张卡片该复习啦！`);
  lastDueNotificationCount = count; return count;
}
function draftForPanel(panelId) { return panelId === "discoverPanel" ? discoverDraftText : explainDraftText; }
function setDraftForPanel(panelId, value) {
  const text = String(value ?? "");
  if (panelId === "discoverPanel") discoverDraftText = text;
  else explainDraftText = text;
  if (text) localStorage.setItem(draftStorageKeys[panelId], text);
  else localStorage.removeItem(draftStorageKeys[panelId]);
  const inputId = panelId === "discoverPanel" ? "discoverText" : "explainText";
  $(inputId).value = text;
}
function openImportSheet(id) { const target = state.activeKnowledgePanel === "discoverPanel" ? "discoverText" : "explainText"; state.importPanelTarget = state.activeKnowledgePanel; $("manualImportText").value = state.activeKnowledgePanel === "discoverPanel" ? discoverDraftText : explainDraftText; $("sheetBackdrop").hidden = false; $(id).hidden = false; document.body.classList.add("sheet-open"); }
function closeImportSheet() { $("importSheet").hidden = true; $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); showImportPanel(null); }
function showImportPanel(panelId) { $("importOptions").hidden = Boolean(panelId); document.querySelectorAll(".import-panel").forEach((panel) => { panel.hidden = panel.id !== panelId; }); }
function targetInput() { return $(state.importTarget); }
function revealKnowledgeEditor() { $("knowledgeLauncher").hidden = true; ["explainPanel", "discoverPanel"].forEach((id) => { $(id).hidden = id !== state.activeKnowledgePanel; }); }
function placeImportedText(text, message, panelId = state.importPanelTarget) { const inputId = panelId === "discoverPanel" ? "discoverText" : "explainText"; setDraftForPanel(panelId, text); closeImportSheet(); state.activeKnowledgePanel = panelId; state.importTarget = inputId; revealKnowledgeEditor(); showToast(message); $(inputId).focus(); }
function showKnowledgeLauncher(panelId) {
  state.activeKnowledgePanel = panelId; state.importTarget = panelId === "discoverPanel" ? "discoverText" : "explainText";
  // 只恢复当前功能的草稿，切换功能时绝不改写另一个输入框。
  $(state.importTarget).value = draftForPanel(panelId);
  document.querySelectorAll(".tab").forEach((item) => { const active = item.dataset.panel === panelId; item.classList.toggle("active", active); item.setAttribute("aria-selected", String(active)); });
  const discovering = panelId === "discoverPanel";
  $("pageTitle").textContent = discovering ? "探索它在跨学科领域的逻辑同源" : "把专业知识翻译成你能理解的语言";
  $("pageSubtitle").textContent = discovering ? "输入一个概念，看看其他学科里有没有相似的运行机制。" : "粘贴一段专业内容，先理解它，再寻找其他领域中结构相似的概念。";
  // #launcherEyebrow 已从 index.html 删除（用户要求去掉这行小字）。
  // 删元素就必须同时删掉这里的赋值：$ 是个不会抛错的桩，给不存在的 id 赋值会静默吞掉，
  // 留下一行永远不会报错的死代码。
  $("launcherTitle").textContent = discovering ? "导入内容或概念，寻找跨学科同源" : "选择一种方式开始";
  $("knowledgeLauncher").hidden = false; $("explainPanel").hidden = true; $("discoverPanel").hidden = true;
  $("historyBackButton").hidden = true; $("historyMemory").classList.remove("has-result-back");
  renderHistoryMemory();
  window.scrollTo({ top: document.querySelector(".mode-tabs").offsetTop, behavior: "smooth" });
}
function clearDraftForPanel(panelId) {
  setDraftForPanel(panelId, "");
}
async function searchZhihu() {
  const input = $("zhihuSearchQuery"), feedback = $("zhihuSearchFeedback"), results = $("zhihuSearchResults");
  const query = input.value.trim();
  if (!query) { feedback.textContent = "请先输入想搜索的主题或问题。"; return; }
  feedback.textContent = state.zhihuMode === "research" ? "正在从核心概念、机制原理和争议边界三个角度搜索…" : "正在搜索知乎真实讨论…"; results.replaceChildren(); $("zhihuSetupHelp").hidden = true;
  const button = $("confirmZhihuSearch"); button.disabled = true;
  try {
    // 研究模式也只消耗一次知乎 API 调用。开放平台的 30001 同时代表频率或日配额，
    // 三个扩展词即使串行也会把一次研究放大成三份额度；角度归类改在本地完成。
    const queries = [query];
    const responses = [];
    const researchErrors = [];
    // 知乎开放平台对瞬时并发很敏感。研究模式必须排队请求，不能 Promise.all 三连发；
    // 某个扩展角度触发限流时，保留前面已经取得的资料，而不是整批清空。
    for (let queryIndex = 0; queryIndex < queries.length; queryIndex += 1) {
      if (state.zhihuMode === "research") feedback.textContent = "正在获取知乎资料并在本地整理研究角度…";
      try {
        responses.push({ data: await request("/api/zhihu/search", { query: queries[queryIndex], count: state.zhihuMode === "research" ? 6 : 5 }), queryIndex });
      } catch (error) {
        researchErrors.push(error);
        if (state.zhihuMode !== "research" || !responses.length) throw error;
      }
    }
    const angleOf = (item, index) => {
      const content = `${item.title || ""} ${item.summary || ""}`;
      if (/争议|局限|缺点|问题|风险|反对|误区|失败|质疑/.test(content)) return "争议边界";
      if (/原理|机制|如何|为什么|过程|作用|反馈|因果/.test(content)) return "机制原理";
      return index % 3 === 2 ? "争议边界" : index % 3 === 1 ? "机制原理" : "核心概念";
    };
    const merged = responses.flatMap(({ data }) => (Array.isArray(data.items) ? data.items : []).map((item, index) => ({ ...item, research_angle: angleOf(item, index) })));
    const items = [...new Map(merged.map((item) => [item.url || `${item.title}:${item.summary}`, item])).values()];
    state.zhihuResearchItems = [];
    const partial = researchErrors.length ? "（知乎当前受到频率或额度限制）" : "";
    feedback.textContent = items.length ? (state.zhihuMode === "research" ? `找到 ${items.length} 条并已按研究角度整理${partial}；请选择 2–6 条形成材料包` : `找到 ${items.length} 条内容，选择一条继续`) : "没有找到相关内容，换个关键词试试。";
    items.forEach((item) => {
      const card = document.createElement("article"); card.className = "zhihu-result-card";
      if (state.zhihuMode === "research") {
        const select = document.createElement("input"); select.type = "checkbox"; select.className = "zhihu-research-check"; select.setAttribute("aria-label", `选择${item.title || "这条内容"}`);
        select.addEventListener("change", () => {
          if (select.checked && state.zhihuResearchItems.length >= 6) { select.checked = false; showToast("研究材料最多选择 6 条"); return; }
          state.zhihuResearchItems = select.checked ? [...state.zhihuResearchItems, item] : state.zhihuResearchItems.filter((entry) => entry !== item);
          $("zhihuSelectionCount").textContent = `已选 ${state.zhihuResearchItems.length} 条（最多 6 条）`;
          $("useZhihuResearch").disabled = state.zhihuResearchItems.length < 2;
          card.classList.toggle("selected", select.checked);
        });
        const angle = document.createElement("span"); angle.className = "zhihu-angle"; angle.textContent = item.research_angle;
        card.append(select, angle);
      }
      const title = document.createElement("strong"); title.textContent = item.title || "知乎内容";
      const meta = document.createElement("small"); meta.textContent = [item.author_name, `${item.vote_up_count || 0} 赞同`, `${item.comment_count || 0} 评论`].filter(Boolean).join(" · ");
      const summary = document.createElement("p"); summary.textContent = item.summary || "暂无摘要";
      const actions = document.createElement("div"); actions.className = "zhihu-result-actions";
      const use = document.createElement("button"); use.type = "button"; use.className = "primary compact"; use.textContent = "用这条发现同源"; use.hidden = state.zhihuMode === "research";
      use.addEventListener("click", () => {
        const text = [item.title, item.summary, item.url ? `来源：${item.url}` : ""].filter(Boolean).join("\n\n");
        placeImportedText(text, "知乎摘要已放入输入框，可以开始寻找同源啦", state.importPanelTarget);
      });
      const source = document.createElement("a"); source.href = item.url || "#"; source.target = "_blank"; source.rel = "noopener noreferrer"; source.textContent = "查看知乎原文 ↗";
      actions.append(use, source); card.append(title, meta, summary, actions); results.append(card);
    });
    $("zhihuResearchToolbar").hidden = state.zhihuMode !== "research" || !items.length;
  } catch (error) {
    const missingSecret = error.status === 503 || String(error.message || "").includes("Access Secret");
    feedback.textContent = missingSecret ? "知乎搜索尚未启用：后端还没有配置 Access Secret。" : (error.message || "知乎搜索暂时不可用。");
    $("zhihuSetupHelp").hidden = !missingSecret;
  } finally { button.disabled = false; }
}

async function importImage(file) {
  if (!file) return; const feedback = $("imageImportFeedback"); feedback.textContent = "正在识别图片…";
  try {
    const form = new FormData(); form.append("file", file);
    // FastAPI 使用 prefix="/api" 的 router 时，后端应注册 @router.post("/ocr")，最终地址即 /api/ocr。
    const response = await authFetch(apiUrl("/api/ocr"), { method: "POST", body: form });
    if (response.status === 404) { closeImportSheet(); revealKnowledgeEditor(); showToast("后端识图接口尚未开启，请直接手动粘贴文本。"); $(state.importPanelTarget === "discoverPanel" ? "discoverText" : "explainText").focus(); return; }
    const data = await response.json().catch(() => ({})); if (!response.ok || !data.text) throw new Error("ocr failed");
    placeImportedText(data.text, state.importPanelTarget === "discoverPanel" ? "识别成功，可以开始寻找同源啦" : "识别成功，可以开始解读啦", state.importPanelTarget);
  }
  catch { feedback.textContent = "图片识别暂时不可用，请尝试手动复制文字。"; showToast("图片识别失败，请尝试手动复制文本。"); }
  finally { $("imageImportFile").value = ""; }
}

function renderRichText(element, content) {
  const escape = (value) => value.replace(/[&<>"']/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
  // 模型偶尔会把中文编号标题连在上一段后面（“……。二、核心概念”），
  // 先补换行，再按 Markdown/中文标题渲染，避免整篇笔记挤成一大段。
  const normalized = String(content || "").replace(/```(?:markdown|text)?/gi, "").replace(/```/g, "")
    .replace(/\s+(?=(?:一|二|三|四|五|六|七|八|九|十)[、.．])/g, "\n")
    .replace(/([。！？；])\s*(?=(?:一|二|三|四|五|六|七|八|九|十)[、.．])/g, "$1\n");
  const lines = normalized.split(/\r?\n/);
  const html = [];
  let listOpen = null;
  const closeList = () => { if (listOpen) { html.push(`</${listOpen}>`); listOpen = null; } };
  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line || /^[-*_]{3,}$/.test(line)) { closeList(); return; }
    const inline = escape(line).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/__(.+?)__/g, "<strong>$1</strong>");
    if (/^#{1,3}\s+/.test(line) || /^(?:一|二|三|四|五|六|七|八|九|十)[、.．]\s*/.test(line)) {
      closeList();
      const heading = inline.replace(/^#{1,3}\s+/, "");
      html.push(`<h3>${heading}</h3>`); return;
    }
    const unordered = /^[-*•]\s+/.test(line), ordered = /^\d+[.)]\s+/.test(line);
    if (unordered || ordered) {
      const kind = ordered ? "ol" : "ul";
      if (listOpen !== kind) { closeList(); html.push(`<${kind}>`); listOpen = kind; }
      html.push(`<li>${inline.replace(ordered ? /^\d+[.)]\s+/ : /^[-*•]\s+/, "")}</li>`); return;
    }
    closeList(); html.push(`<p>${inline}</p>`);
  });
  closeList(); element.innerHTML = html.join("");
}

function renderCoreAnswer(content) {
  const clean = String(content || "").replace(/```(?:markdown|text)?/gi, "").replace(/```/g, "");
  const blocks = clean.split(/\n\s*\n/).filter((item) => item.trim());
  const preferred = blocks.filter((item) => /一句话|核心|关键机制/i.test(item)).slice(0, 3);
  const selected = preferred.length ? preferred : blocks.slice(0, 2);
  renderRichText($("explanationCore"), selected.join("\n\n") || clean.slice(0, 500));
  renderRichText($("explanation"), clean);
  $("answerDisclosure").open = false;
}

function renderLogicProfile(profile) {
  const container = $("profileRows"); container.replaceChildren();
  if (!profile) { $("logicProfile").hidden = true; return; }
  Object.entries(profileLabels).forEach(([key, label]) => {
    const value = Math.max(0, Math.min(1, Number(profile[key] ?? 0)));
    const row = document.createElement("div"); row.className = "profile-row";
    const name = document.createElement("span"); name.textContent = label;
    const track = document.createElement("div"); track.className = "track";
    const fill = document.createElement("div"); fill.className = "fill"; fill.style.width = `${Math.round(value * 100)}%`;
    const number = document.createElement("span"); number.className = "profile-value"; number.textContent = `${Math.round(value * 100)}%`;
    track.append(fill); row.append(name, track, number); container.append(row);
  });
  $("logicProfile").hidden = true; $("profileToggle").setAttribute("aria-expanded", "false"); $("profileToggle").textContent = "查看逻辑画像 ›";
}

function openMappingSheet(candidateName, mappings) {
  $("mappingSheetTitle").textContent = `术语对照 · ${candidateName}`;
  const container = $("mappingSheetContent"); container.replaceChildren();
  mappings.forEach((mapping) => {
    const row = document.createElement("article"); row.className = "mapping-sheet-card";
    const pair = document.createElement("strong"); pair.textContent = `${mapping.source_term} ↔ ${mapping.target_term}`;
    const source = document.createElement("p"); source.textContent = `原概念角色：${mapping.source_role}`;
    const target = document.createElement("p"); target.textContent = `候选角色：${mapping.target_role}`;
    const reason = document.createElement("p"); reason.textContent = `对应依据：${mapping.correspondence_reason}`;
    row.append(pair, source, target, reason);
    if (mapping.limitations?.length) { const limit = document.createElement("p"); limit.className = "mapping-limit"; limit.textContent = `局限：${mapping.limitations.join("；")}`; row.append(limit); }
    if (mapping.evidence_refs?.length) { const refs = document.createElement("small"); refs.textContent = `依据编号：${mapping.evidence_refs.join("、")}`; row.append(refs); }
    container.append(row);
  });
  $("sheetBackdrop").hidden = false; $("mappingSheet").hidden = false; document.body.classList.add("sheet-open");
}

function closeMappingSheet() {
  $("sheetBackdrop").hidden = true; $("mappingSheet").hidden = true; document.body.classList.remove("sheet-open");
}

/* 下拉末位那条「＋ 新建书本…」的哨兵值。它不是真实的书 id，绝不能留在 select 的 value 上：
   selectedShelfBook() 拿它 find 不到书会悄悄退化成第一本，用户以为在新建、结果存进了旧书。
   所以选中它由 change 监听立刻接手并拨回去，见下面 shelfSelect 的监听。 */
const NEW_BOOK_OPTION = "__new_book__";
/** 下拉里当前真正指向那本书的 id（哨兵值不算）。 */
let lastShelfBookId = "";

// 存卡目标书架：下拉里列出「卡片」页的真实书架，卡片存进用户选中的那一个
function renderShelfOptions() {
  const select = $("shelfSelect"), books = getBooks(), previous = select.value;
  select.replaceChildren();
  books.forEach((book) => { const option = document.createElement("option"); option.value = book.id; option.textContent = `${book.name}（${(book.cards || []).length} 张卡片）`; select.append(option); });
  // 有书架时只剩「往已有的书里存」这一条路，想新建得先跑去「卡片」tab 再绕回来。补上这条，
  // 存卡这一步就能直接开一本新书。
  const create = document.createElement("option"); create.value = NEW_BOOK_OPTION; create.textContent = "＋ 新建书本…"; select.append(create);
  // 沿用上一次选的那本，省得每次都重选。默认值以前写死成种子书「跨学科机制」——
  // 种子删掉后书架可能是空的，所以退化成「上次选的 → 第一本 → 空（没得选）」。
  lastShelfBookId = books.some((book) => book.id === previous) ? previous : books[0]?.id || "";
  select.value = lastShelfBookId;
}
/** 当前选中的书架；书架为空时是 null。调用点必须判空 —— 这里以前会兜底成种子书 book_cross。 */
function selectedShelfBook() { const books = getBooks(); return books.find((book) => book.id === $("shelfSelect").value) || books[0] || null; }

/* 「存为卡片」需要先有本书的那一步棋：不再弹个 toast 就完事（那是个死胡同，点了跟没点
   一样），改成转去「新建书本」，建完自动把这张卡存进新书 —— 见 saveBookButton 的 handler。
   两条路共用这里：**一本书都没有**时点「存为卡片」，以及**有书架但在下拉里选了「＋ 新建书本…」**。
   标记位在 closeBookSheet() 里清掉，所以中途取消（× / 取消 / 点遮罩）不会留下
   「下次建书时突然冒出一张卡片」的雷。 */
let pendingSaveCardAfterNewBook = false;

function startNewBookForSaveCard() {
  pendingSaveCardAfterNewBook = true;
  openBookSheet(null);
  showToast("给书架起个名字，这张卡片就存进去");
}

function openSaveCardSheet() {
  // 没有书架时这个弹层本来就是空的：下拉里一个选项都没有，"卡片将存入所选书架"也是句假话。
  // 所以不开它，直接去建书。
  if (!getBooks().length) { startNewBookForSaveCard(); return; }
  $("saveCardConcept").textContent = $("conceptName").textContent || "当前解释";
  renderShelfOptions();
  $("saveCardStatus").textContent = "卡片将存入所选书架，并同步到复习。";
  $("confirmSaveCard").disabled = false;
  $("sheetBackdrop").hidden = false; $("saveCardSheet").hidden = false; document.body.classList.add("sheet-open");
}

function closeSaveCardSheet() {
  $("sheetBackdrop").hidden = true; $("saveCardSheet").hidden = true; document.body.classList.remove("sheet-open");
}

async function saveKnowledgeCard() {
  // 书架为空时中止。**不自动替用户建一本书** —— 那又变成"凭空冒出来的默认数据"了。
  const book = selectedShelfBook();
  if (!book) { showToast("先建一本书，再把卡片存进去"); return; }
  const button = $("confirmSaveCard");
  button.disabled = true; button.textContent = "正在保存…"; $("saveCardStatus").textContent = "正在写入后端卡片库…";
  const source = state.activeKnowledgePanel === "discoverPanel" ? "跨学科理解" : "读懂它";
  const sessionId = source === "跨学科理解" ? state.discoverSessionId : state.explainSessionId;
  const card = { id: crypto.randomUUID ? crypto.randomUUID() : makeId("card"), front: $("conceptName").textContent || "知识卡片", back: $("explanationCore").textContent || $("explanation").textContent || "", source: source === "读懂它" ? "explain" : "discover", sessionId, createdAt: new Date().toISOString(), ...newReviewFields() };
  try {
    const response = await authFetch(apiUrl("/api/cards/save"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(card) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const saved = await response.json(); Object.assign(card, saved.card || {}); card.syncStatus = "synced"; book.cards = book.cards.filter((item) => item.front !== card.front || item.sessionId !== card.sessionId); book.cards.push(card); saveBook(book); renderBooks(); buildReviewQueue(); checkDueCards(); $("saveCardStatus").textContent = `已存入「${book.name}」并同步到复习。`; showToast(`✅ 已存入「${book.name}」，可在「卡片」Tab 查看。`);
  } catch {
    card.status = "unmastered"; card.localOnly = true; book.cards = book.cards.filter((item) => item.front !== card.front || item.sessionId !== card.sessionId); book.cards.push(card); saveBook(book); renderBooks(); buildReviewQueue();
    $("saveCardStatus").textContent = "后端暂时不可用，已本地保存并将在下次自动同步。"; showToast("后端暂时不可用，卡片已本地保存");
  }
  button.disabled = false; button.textContent = "确认存入卡片";
  closeSaveCardSheet();
}

async function syncLocalCards() {
  let changed = false;
  for (const book of getBooks()) {
    for (const card of book.cards.filter((item) => item.localOnly)) {
      try {
        const response = await authFetch(apiUrl("/api/cards/save"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(card) });
        if (!response.ok) continue;
        delete card.localOnly; card.syncStatus = "synced"; changed = true;
      } catch { /* keep local fallback until the API is available */ }
    }
    if (changed) saveBook(book);
  }
  if (changed) { renderBooks(); buildReviewQueue(); }
}

function createDisclosure(title, className = "") {
  const details = document.createElement("details"); details.className = `nested-disclosure ${className}`.trim();
  const summary = document.createElement("summary"); summary.textContent = title;
  const content = document.createElement("div"); content.className = "nested-content";
  details.append(summary, content);
  return { details, content };
}

function addMessage(role, content) {
  const item = document.createElement("div");
  item.className = `message ${role}`;
  if (role === "assistant") {
    const sender = document.createElement("strong"); sender.className = "message-sender"; sender.textContent = "Logic-Coloc"; item.append(sender);
    const body = document.createElement("div"); renderRichText(body, content); item.append(body);
  } else {
    item.textContent = `你：${content}`;
  }
  $("conversation").append(item);
  if (state.activeKnowledgePanel === "explainPanel") state.explainConversation.push({ role, content });
}

async function explain() {
  const text = $("explainText").value.trim();
  showError("explainError", "");
  if (!text) { showError("explainError", "请先输入需要解释的内容。"); return; }
  state.explainConversation = [];
  $("conversation").replaceChildren();
  setLoading(true, "正在提取逻辑结构并请求模型…", $("explainButton"));
  try {
    const data = await request("/api/explain", { text });
    setDraftForPanel("explainPanel", text);
    state.explainSessionId = data.session_id;
    addHistory("读懂它", text, state.explainSessionId, data);
    $("conceptName").textContent = data.concept?.name || "分析结果";
    $("explainUserMessage").textContent = text;
    renderCoreAnswer(data.explanation || "暂时没有生成解释。");
    renderLogicProfile(data.logic_profile);
    document.querySelector("#explainPanel > .input-area").hidden = true;
    $("explainResult").hidden = false;
    $("historyBackButton").hidden = false; $("historyMemory").classList.add("has-result-back");
    $("followUp").hidden = false;
    if ((data.explanation || "").includes("LLM 调用失败") || (data.explanation || "").includes("无法完成特征提取")) {
      showError("explainError", "解释服务当前无法连接模型后端，请确认 LLM 网关已经启动。");
    }
  } catch (error) { console.error("Explain request failed", error); showError("explainError", error.message); }
  finally { setLoading(false, "", $("explainButton")); }
}

async function chat() {
  const message = $("chatText").value.trim();
  showError("chatError", "");
  if (!state.explainSessionId) { showError("chatError", "请先完成一次“读懂它”。"); return; }
  if (!message) { showToast("请先输入一个具体问题"); showError("chatError", "请先输入一个具体问题"); return; }
  addMessage("user", message); $("chatText").value = ""; setLoading(true, "正在思考…", $("chatButton"));
  try {
    const data = await request("/api/chat", { session_id: state.explainSessionId, message });
    addMessage("assistant", data.answer || "暂时没有生成回答。");
    const items = getHistory(); const saved = items.find((item) => item.sessionId === state.explainSessionId);
    if (saved) { saved.fullResponse = { ...(saved.fullResponse || {}), conversation: state.explainConversation }; saveHistory(items); }
  } catch (error) { showError("chatError", error.message); }
  finally { setLoading(false, "", $("chatButton")); }
}

function valueOrUnavailable(value) {
  return Number.isFinite(Number(value)) ? `${Math.round(Number(value) * 100)}%` : "API 未提供";
}

const verdictLabels = {
  RELIABLE_WITH_LIMITS: "有限成立",
  NEEDS_REVIEW: "待核验",
  REJECTED: "不成立",
  INSUFFICIENT_EVIDENCE: "证据不足",
};

const verdictPriority = { RELIABLE_WITH_LIMITS: 0, NEEDS_REVIEW: 1, INSUFFICIENT_EVIDENCE: 2, REJECTED: 3 };

function effectiveVerdict(record, report, homology) {
  const raw = report?.verdict || ({ "有限成立": "RELIABLE_WITH_LIMITS", "待核验": "NEEDS_REVIEW", "证据不足": "INSUFFICIENT_EVIDENCE", "不成立": "REJECTED" }[record.reliability]) || "NEEDS_REVIEW";
  // Compatibility for historical responses produced before the verdict fix:
  // a high structural score cannot be labelled “not established” merely
  // because the generated mapping failed schema/type validation.
  return raw === "REJECTED" && Number(homology) >= 0.72 ? "NEEDS_REVIEW" : raw;
}

function appendSection(parent, title, text) {
  if (!text) return;
  const section = document.createElement("section"); section.className = "report-section";
  const heading = document.createElement("h4"); heading.textContent = title;
  const body = document.createElement("p"); body.textContent = text;
  section.append(heading, body); parent.append(section);
}

function appendListSection(parent, title, values, className = "") {
  if (!values?.length) return;
  const section = document.createElement("section"); section.className = `report-section ${className}`.trim();
  const heading = document.createElement("h4"); heading.textContent = title;
  const list = document.createElement("ul");
  values.forEach((value) => { const item = document.createElement("li"); item.textContent = value; list.append(item); });
  section.append(heading, list); parent.append(section);
}

function renderLearningReport(card, report, candidateName) {
  const verdict = document.createElement("div"); verdict.className = `verdict verdict-${report.verdict?.toLowerCase()}`;
  const verdictTitle = document.createElement("strong"); verdictTitle.textContent = verdictLabels[report.verdict] || "待核验";
  const verdictReason = document.createElement("span"); verdictReason.textContent = report.verdict_reason || "";
  verdict.append(verdictTitle, verdictReason); card.append(verdict);

  const primers = document.createElement("div"); primers.className = "primer-grid";
  [["先读懂原概念", report.source_primer], ["再认识候选概念", report.candidate_primer]].forEach(([title, text]) => {
    if (!text) return;
    const block = document.createElement("section"); block.className = "primer";
    const heading = document.createElement("h4"); heading.textContent = title;
    const body = document.createElement("p"); body.textContent = text;
    block.append(heading, body); primers.append(block);
  });
  if (primers.childElementCount) card.append(primers);

  appendSection(card, "共同的机制骨架", report.mechanism_summary);
  appendSection(card, "从交集进入第二领域", report.intersection_lesson);

  if (report.target_domain_lessons?.length) {
    const section = document.createElement("section"); section.className = "report-section course";
    const heading = document.createElement("h4"); heading.textContent = "第二领域微型课程"; section.append(heading);
    report.target_domain_lessons.forEach((lesson, index) => {
      const block = document.createElement("details"); block.className = "lesson";
      const title = document.createElement("summary"); title.textContent = `${index + 1}. ${lesson.title}`;
      const lessonContent = document.createElement("div"); lessonContent.className = "lesson-content";
      const explanation = document.createElement("p"); explanation.textContent = lesson.explanation;
      const connection = document.createElement("p"); connection.className = "lesson-bridge"; connection.textContent = `和你已知内容的连接：${lesson.connection_to_source}`;
      const example = document.createElement("p"); example.textContent = `例子：${lesson.example}`;
      const check = document.createElement("p"); check.className = "lesson-check"; check.textContent = `学完自测：${lesson.check_question}`;
      lessonContent.append(explanation, connection, example, check);
      const answer = document.createElement("button"); answer.type = "button"; answer.className = "quiz-answer-button"; answer.textContent = "查看答案";
      const answerText = document.createElement("p"); answerText.className = "quiz-answer"; answerText.hidden = true;
      answerText.textContent = `答案：${lesson.check_answer || "请先用自己的话回答，再对照本节解释和例子核对。"}`;
      answer.addEventListener("click", () => { answerText.hidden = !answerText.hidden; answer.textContent = answerText.hidden ? "查看答案" : "收起答案"; });
      lessonContent.append(answer, answerText); block.append(title, lessonContent);
      block.addEventListener("toggle", () => {
        if (!block.open) return;
        section.querySelectorAll(".lesson").forEach((item) => { if (item !== block) item.open = false; });
      });
      section.append(block);
    });
    card.append(section);
  }

  const transferGrid = document.createElement("div"); transferGrid.className = "transfer-grid";
  appendListSection(transferGrid, "可以带过去的直觉", report.transferable_knowledge, "transferable");
  appendListSection(transferGrid, "进入第二领域必须新学", report.new_knowledge, "new-knowledge");
  if (transferGrid.childElementCount) card.append(transferGrid);
  appendListSection(card, "理解检查", report.understanding_checks, "understanding-checks");

  const technical = createDisclosure("查看结构指标 ↓", "technical-details");
  if (report.dimension_comparisons?.length) {
    const section = document.createElement("section"); section.className = "report-section";
    const heading = document.createElement("h4"); heading.textContent = "五维结构证据"; section.append(heading);
    const dimensions = document.createElement("div"); dimensions.className = "dimension-list";
    report.dimension_comparisons.forEach((dimension) => {
      const row = document.createElement("div"); row.className = "dimension-row";
      const name = document.createElement("strong"); name.textContent = profileLabels[dimension.dimension] || dimension.dimension;
      const values = document.createElement("span");
      values.textContent = `原概念 ${Math.round(dimension.source_value * 100)} · 候选 ${Math.round(dimension.candidate_value * 100)}`;
      const bars = document.createElement("div"); bars.className = "dimension-bars";
      [dimension.source_value, dimension.candidate_value].forEach((value, index) => {
        const track = document.createElement("div"); track.className = "track";
        const fill = document.createElement("div"); fill.className = `fill ${index ? "candidate-fill" : ""}`; fill.style.width = `${Math.round(value * 100)}%`;
        track.append(fill); bars.append(track);
      });
      const reason = document.createElement("p"); reason.textContent = dimension.plain_language_reason;
      row.append(name, values, bars, reason); dimensions.append(row);
    });
    section.append(dimensions); technical.content.append(section);
  }

  if (technical.content.childElementCount) card.append(technical.details);

  if (report.mapping_evidence?.length) {
    const mappingButton = document.createElement("button");
    mappingButton.type = "button"; mappingButton.className = "secondary mapping-sheet-button";
    mappingButton.textContent = "单独查看术语与机制角色对照 →";
    mappingButton.addEventListener("click", () => openMappingSheet(candidateName, report.mapping_evidence));
    card.append(mappingButton);
  }

  const academic = createDisclosure("学术严谨性说明 ↓", "academic-details rigor-details");
  const boundaryGrid = document.createElement("div"); boundaryGrid.className = "boundary-grid";
  appendListSection(boundaryGrid, "成立条件", report.valid_conditions, "conditions");
  appendListSection(boundaryGrid, "关键差异", report.known_differences, "differences");
  appendListSection(boundaryGrid, "失效边界", report.failure_boundaries, "boundaries");
  appendListSection(boundaryGrid, "不能这样说", report.prohibited_claims, "prohibited");
  if (boundaryGrid.childElementCount) academic.content.append(boundaryGrid);
  if (academic.content.childElementCount) card.append(academic.details);

  const learning = createDisclosure("学习路径指导 ↓", "learning-details");

  if (report.source_references?.length) {
    const section = document.createElement("section"); section.className = "report-section sources";
    const heading = document.createElement("h4"); heading.textContent = "来源与可核验依据"; section.append(heading);
    const list = document.createElement("ol");
    report.source_references.forEach((source) => {
      const item = document.createElement("li");
      const label = `${source.title} · ${source.publisher_or_author}${source.locator ? ` · ${source.locator}` : ""}`;
      if (/^https?:\/\//.test(source.url_or_identifier || "")) {
        const link = document.createElement("a"); link.href = source.url_or_identifier; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = label; item.append(link);
      } else { item.textContent = `${label} · ${source.url_or_identifier}`; }
      if (source.supports?.length) { const supports = document.createElement("p"); supports.textContent = `支持：${source.supports.join("；")}`; item.append(supports); }
      list.append(item);
    });
    section.append(list); learning.content.append(section);
  }
  appendListSection(learning.content, "下一步怎么学", report.learning_next_steps, "next-steps");
  if (report.recommended_books?.length) {
    const section = document.createElement("section"); section.className = "report-section books";
    const heading = document.createElement("h4"); heading.textContent = "推荐书籍"; section.append(heading);
    const list = document.createElement("ul");
    report.recommended_books.forEach((book) => { const item = document.createElement("li"); item.textContent = `${book.title}${book.author ? ` · ${book.author}` : ""}：${book.reason}${book.scope ? `（${book.scope}）` : ""}`; list.append(item); });
    section.append(list); learning.content.append(section);
  }
  if (learning.content.childElementCount) card.append(learning.details);
}

function renderCandidates(data) {
  const container = $("candidateList"); container.replaceChildren();
  const retrievalScores = data.retrieval_scores || {};
  const records = [...(data.candidates?.length ? data.candidates : (data.results || []).map((candidate) => ({ candidate })))];
  const reportFor = (record) => {
    const candidate = record.candidate || record;
    const id = candidate.id || candidate.candidate_id || "未知候选";
    return record.learning_report || data.learning_reports?.find((item) => item.candidate_id === id);
  };
  const homologyFor = (record) => record.homonomy_score ?? (record.candidate || record).score;
  records.sort((left, right) => {
    const leftVerdict = effectiveVerdict(left, reportFor(left), homologyFor(left));
    const rightVerdict = effectiveVerdict(right, reportFor(right), homologyFor(right));
    return (verdictPriority[leftVerdict] ?? 9) - (verdictPriority[rightVerdict] ?? 9)
      || (Number(homologyFor(right)) || 0) - (Number(homologyFor(left)) || 0);
  });
  records.forEach((record, index) => {
    const candidate = record.candidate || record;
    const id = candidate.id || candidate.candidate_id || "未知候选";
    const candidateName = candidate.concept?.name || candidate.concept || record.concept || id;
    const homology = record.homonomy_score ?? candidate.score;
    const retrieval = candidate.retrieval_score ?? retrievalScores[id];
    const mappingResult = record.mapping || data.mappings?.find((item) => item.candidate_id === id);
    const mapping = mappingResult?.mapping || {};
    const critique = record.critique || data.critiques?.find((item) => item.candidate_id === id);
    const report = record.learning_report || data.learning_reports?.find((item) => item.candidate_id === id);
    const verdict = effectiveVerdict(record, report, homology);
    const reliable = verdict === "RELIABLE_WITH_LIMITS";
    const reliabilityLabel = verdictLabels[verdict] || "待核验";
    const card = document.createElement("details"); card.className = "candidate";
    const summary = document.createElement("summary"); summary.className = "candidate-summary";
    const head = document.createElement("div"); head.className = "candidate-head";
    const title = document.createElement("h3"); title.textContent = candidateName;
    const badge = document.createElement("span"); badge.className = `reliability ${reliable ? "yes" : ""}`; badge.textContent = reliabilityLabel;
    head.append(title, badge);
    const domain = document.createElement("span"); domain.className = "domain-pill"; domain.textContent = candidate.domain || "未标注";
    const scores = document.createElement("div"); scores.className = "scores";
    [["检索相关性（召回）", retrieval], ["逻辑同源度（结构）", homology]].forEach(([label, value]) => {
      const score = document.createElement("div"); score.className = "score";
      const name = document.createElement("span"); name.textContent = label;
      const strong = document.createElement("strong"); strong.textContent = valueOrUnavailable(value);
      const track = document.createElement("div"); track.className = "track";
      const fill = document.createElement("div"); fill.className = `fill ${label.startsWith("逻辑同源度") ? "candidate-fill" : ""}`; fill.style.width = Number.isFinite(Number(value)) ? `${Math.round(Number(value) * 100)}%` : "0%";
      track.append(fill); score.append(name, strong, track); scores.append(score);
    });
    const whySummary = document.createElement("p"); whySummary.className = "candidate-why";
    whySummary.textContent = `为什么相似：${report?.mechanism_summary || record.summary || candidate.mechanism || candidate.description || "具有可比较的底层机制结构。"}`;
    const expandLabel = document.createElement("span"); expandLabel.className = "expand-label"; expandLabel.textContent = "展开详细对应关系 ↓";
    summary.append(head, domain, scores, whySummary, expandLabel); card.append(summary);
    const body = document.createElement("div"); body.className = "candidate-content";
    if (Number(homology) < 0.5) {
      const warning = document.createElement("p"); warning.className = "homology-warning";
      warning.textContent = "⚠️ 底层逻辑相似度较低，不建议作为同源映射，请谨慎参考。";
      body.append(warning);
    }
    if (record.homonomy_score_reason || record.retrieval_score_reason) {
      const reasons = document.createElement("div"); reasons.className = "score-reasons";
      if (record.retrieval_score_reason) { const p = document.createElement("p"); p.textContent = `检索依据：${record.retrieval_score_reason}`; reasons.append(p); }
      if (record.homonomy_score_reason) { const p = document.createElement("p"); p.textContent = `同源依据：${record.homonomy_score_reason}`; reasons.append(p); }
      body.append(reasons);
    }
    if (!report && (candidate.description || candidate.mechanism)) {
      const why = document.createElement("p"); why.className = "critique";
      why.textContent = `为什么认为相似：${candidate.mechanism || candidate.description}`; body.append(why);
    }
    if (!report && Object.keys(mapping).length) {
      const list = document.createElement("ul"); list.className = "mapping";
      Object.entries(mapping).forEach(([from, to]) => { const item = document.createElement("li"); item.textContent = `${from} → ${to}`; list.append(item); });
      body.append(list);
    }
    if (!report && critique) {
      const note = document.createElement("p"); note.className = "critique";
      note.textContent = critique.summary || critique.reason || (critique.issues || critique.warnings || []).join("；"); body.append(note);
    }
    if (report) renderLearningReport(body, report, candidateName);
    if (report) {
      const ask = document.createElement("button"); ask.type = "button"; ask.className = "secondary candidate-ask"; ask.textContent = `询问关于「${candidateName}」的问题`;
      const chatBox = document.createElement("div"); chatBox.className = "candidate-chat"; chatBox.hidden = true;
      const row = document.createElement("div"); row.className = "chat-row";
      const input = document.createElement("textarea"); input.rows = 2; input.placeholder = "输入问题，例如：为什么成立？"; input.setAttribute("aria-label", `询问关于${candidateName}的问题`);
      const send = document.createElement("button"); send.type = "button"; send.className = "secondary"; send.textContent = "发送";
      const conversation = document.createElement("div"); conversation.className = "conversation"; conversation.setAttribute("aria-live", "polite");
      const chatStatus = document.createElement("div"); chatStatus.className = "chat-status"; chatStatus.textContent = "正在思考…"; chatStatus.hidden = true;
      const submit = async () => {
        const question = input.value.trim();
        if (!question) {
          showToast("请先输入一个具体问题");
          const errorMessage = document.createElement("div"); errorMessage.className = "message chat-error"; errorMessage.textContent = "请先输入一个具体问题"; conversation.append(errorMessage); input.focus(); return;
        }
        if (!state.discoverSessionId) {
          const errorMessage = document.createElement("div"); errorMessage.className = "message chat-error"; errorMessage.textContent = "当前会话已失效，请重新执行一次“发现同源”。"; conversation.append(errorMessage); return;
        }
        const message = `关于「${candidateName}」：${question}`;
        const userMessage = document.createElement("div"); userMessage.className = "message user"; userMessage.textContent = message; conversation.append(userMessage);
        input.value = ""; chatStatus.hidden = false; send.disabled = true; setLoading(true, "正在思考…", send);
        try {
          const data = await request("/api/chat", { session_id: state.discoverSessionId, message });
          const answerMessage = document.createElement("div"); answerMessage.className = "message assistant"; renderRichText(answerMessage, data.answer || "暂时没有生成回答。"); conversation.append(answerMessage);
        } catch (error) {
          const errorMessage = document.createElement("div"); errorMessage.className = "message chat-error"; errorMessage.textContent = error.message; conversation.append(errorMessage);
        } finally { chatStatus.hidden = true; setLoading(false, "", send); send.disabled = false; }
      };
      send.addEventListener("click", submit);
      input.addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(); } });
      ask.addEventListener("click", () => { chatBox.hidden = !chatBox.hidden; ask.textContent = chatBox.hidden ? `询问关于「${candidateName}」的问题` : "收起对话"; if (!chatBox.hidden) input.focus(); });
      row.append(input, send); chatBox.append(chatStatus, row, conversation); body.append(ask, chatBox);
    }
    card.append(body);
    container.append(card);
  });
  $("discoverEmpty").hidden = records.length > 0;
}

async function discover() {
  const text = $("discoverText").value.trim();
  showError("discoverError", "");
  if (!text) { showError("discoverError", "请先输入需要寻找同源结构的内容。"); return; }
  const requestId = crypto.randomUUID ? crypto.randomUUID() : makeId("discover");
  const controller = new AbortController(); state.discoverRequestId = requestId; state.discoverAbortController = controller;
  setLoading(true, "正在分析逻辑结构、检索候选并生成学习报告…", $("discoverButton"));
  try {
    // Five candidates prevent same-domain results from crowding out useful
    // cross-disciplinary matches such as immune or ecological feedback.
    const payload = { text, top_k: 5, request_id: requestId };
    const data = await request("/api/discover", payload, { signal: controller.signal });
    if (data.code !== 0) {
      state.discoverSessionId = null;
      $("discoverResult").hidden = true;
      showError("discoverError", data.message || "模型服务异常，请稍后重试");
      return;
    }
    state.discoverSessionId = data.session_id;
    setDraftForPanel("discoverPanel", text);
    addHistory("跨学科理解", text, state.discoverSessionId, data);
    clearDraftForPanel("discoverPanel");
    $("discoverTitle").textContent = `发现同源 · ${data.concept?.name || text.slice(0, 30)}`;
    renderRichText($("discoverReport"), data.report || "同源分析已完成。");
    renderCandidates(data); $("discoverResult").hidden = false; $("historyBackButton").hidden = false; $("historyMemory").classList.add("has-result-back");
    if ((data.report || "").includes("LLM 调用失败") || (data.report || "").includes("无法完成特征提取")) {
      showError("discoverError", "同源分析当前无法连接模型后端，请确认 LLM 网关已经启动。");
    }
  } catch (error) { if (error.name !== "AbortError") { console.error("Discover request failed", error); showError("discoverError", error.message); } }
  finally { if (state.discoverRequestId === requestId) { state.discoverAbortController = null; state.discoverRequestId = null; setLoading(false, "", $("discoverButton")); } }
}

function cancelDiscover() {
  const requestId = state.discoverRequestId;
  if (!requestId) return;
  state.discoverAbortController?.abort();
  state.discoverAbortController = null; state.discoverRequestId = null;
  setLoading(false, "", $("discoverButton"));
  showError("discoverError", "本次分析已取消，输入内容已保留。");
  authFetch(apiUrl(`/api/discover/${encodeURIComponent(requestId)}/cancel`), { method: "POST" }).catch(() => {});
}

function useZhihuResearch() {
  const items = state.zhihuResearchItems;
  if (items.length < 2) { showToast("请至少选择 2 条知乎内容"); return; }
  const topic = $("zhihuSearchQuery").value.trim();
  const button = $("useZhihuResearch"); button.disabled = true; button.textContent = "正在整合…";
  const sourceItems = items.map(({ research_angle, ...item }) => item);
  request("/api/zhihu/research", { topic, items: sourceItems }).then((data) => {
    state.researchNoteDraft = { title: data.title || `${topic}：知乎资料整合笔记`, content: data.content || "" };
    const researchHistoryItem = { id: makeId("research"), type: "知乎研究", input: topic, sessionId: `zhihu_research_${Date.now()}`, timestamp: new Date().toISOString(), fullResponse: { title: state.researchNoteDraft.title, content: state.researchNoteDraft.content, sourceCount: data.source_count || items.length } };
    saveHistory([researchHistoryItem, ...getHistory()]);
    const preview = document.createElement("article"); preview.className = "zhihu-research-note-preview";
    const heading = document.createElement("div"); heading.className = "zhihu-note-heading"; const back = document.createElement("button"); back.type = "button"; back.className = "secondary compact"; back.textContent = "← 返回搜索结果"; back.addEventListener("click", () => { preview.remove(); $("zhihuResearchToolbar").hidden = false; }); const headingText = document.createElement("h4"); headingText.textContent = "整合后的学习笔记（置顶）"; heading.append(back, headingText);
    const title = document.createElement("h3"); title.textContent = state.researchNoteDraft.title;
    const body = document.createElement("div"); body.className = "zhihu-research-note-content"; renderRichText(body, state.researchNoteDraft.content);
    const save = document.createElement("button"); save.type = "button"; save.className = "primary"; save.textContent = "保存到笔记";
    save.addEventListener("click", () => { closeImportSheet(); openNoteSheet(null); $("noteTitle").value = state.researchNoteDraft.title; renderRichText($("noteBody"), state.researchNoteDraft.content); showToast(`已生成整合笔记（${data.source_count || items.length} 条参考来源），请选择分类后保存`); });
    preview.append(heading, title, body, save); $("zhihuSearchResults").prepend(preview);
    $("zhihuResearchToolbar").hidden = true;
    showToast("整合笔记已生成，下面保留原始知乎文章供核对");
  }).catch((error) => showToast(error.message || "知乎资料整合失败，请稍后重试")).finally(() => { button.disabled = false; button.textContent = "整合为学习笔记"; });
}

function toggleZhihuSelectAll() {
  const checks = [...document.querySelectorAll(".zhihu-research-check")];
  const shouldSelect = state.zhihuResearchItems.length < Math.min(6, checks.length);
  checks.forEach((check) => { if (check.checked !== shouldSelect) check.click(); });
  $("toggleZhihuSelectAll").textContent = shouldSelect ? "取消全选" : "全选";
}

function activatePanel(panelId) {
  showKnowledgeLauncher(panelId);
}

function activateAppPage(pageId) {
  const knowledgeActive = pageId === "knowledge";
  document.querySelectorAll(".knowledge-shell").forEach((item) => { item.hidden = !knowledgeActive; });
  document.querySelectorAll(".app-page").forEach((item) => { item.hidden = item.id !== pageId; });
  const tabs = document.querySelectorAll(".bottom-tab");
  tabs.forEach((item) => { item.classList.remove("active"); item.setAttribute("aria-selected", "false"); });
  const activeTab = document.querySelector(`.bottom-tab[data-app-page="${pageId}"]`);
  if (activeTab) { activeTab.classList.add("active"); activeTab.setAttribute("aria-selected", "true"); }
  const settingsButton = $("settingsButton");
  if (settingsButton) settingsButton.hidden = pageId !== "petPage";
  if (pageId === "reviewPage") switchReviewFilter("unmastered");
  else if (knowledgeActive) showKnowledgeLauncher(state.activeKnowledgePanel);
  else window.scrollTo({ top: 0, behavior: "smooth" });
}

// 由 applyStorageScope(uid) 重建；未登录时是空 scope 的默认值。
let storageKeys = buildStorageKeys("");
function readHistoryKey(key) { try { const items = JSON.parse(localStorage.getItem(key)); return Array.isArray(items) ? items : []; } catch { return []; } }
function normalizeHistoryItem(item) { const rawType = String(item.type || item.mode || ""); const type = rawType.includes("知乎") || rawType.toLowerCase().includes("research") ? "知乎研究" : rawType.includes("跨") || rawType.toLowerCase().includes("discover") ? "跨学科理解" : "读懂它"; const timestamp = typeof item.timestamp === "number" ? new Date(item.timestamp < 100000000000 ? item.timestamp * 1000 : item.timestamp).toISOString() : (item.timestamp || new Date().toISOString()); return { id: item.id || makeId("history"), type, input: item.input || item.text || "", sessionId: item.sessionId || item.session_id || "", timestamp, fullResponse: item.fullResponse || item.response || null }; }
function migrateHistory() { const legacy = readHistoryKey(storageKeys.history); const explain = readHistoryKey(storageKeys.explainHistory); const discover = readHistoryKey(storageKeys.discoverHistory); const research = readHistoryKey(storageKeys.researchHistory); if (legacy.length || explain.length || discover.length || research.length) { const merged = [...legacy, ...explain, ...discover, ...research].map(normalizeHistoryItem); const unique = [...new Map(merged.map((item) => [item.sessionId || item.id, item])).values()]; localStorage.setItem(storageKeys.explainHistory, JSON.stringify(unique.filter((item) => item.type === "读懂它"))); localStorage.setItem(storageKeys.discoverHistory, JSON.stringify(unique.filter((item) => item.type === "跨学科理解"))); localStorage.setItem(storageKeys.researchHistory, JSON.stringify(unique.filter((item) => item.type === "知乎研究"))); if (legacy.length) localStorage.removeItem(storageKeys.history); } }
function getHistory() { migrateHistory(); return [...readHistoryKey(storageKeys.explainHistory), ...readHistoryKey(storageKeys.discoverHistory), ...readHistoryKey(storageKeys.researchHistory)].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp)); }
function saveHistory(items) { localStorage.setItem(storageKeys.explainHistory, JSON.stringify(items.filter((item) => item.type === "读懂它").slice(0, 100))); localStorage.setItem(storageKeys.discoverHistory, JSON.stringify(items.filter((item) => item.type === "跨学科理解").slice(0, 100))); localStorage.setItem(storageKeys.researchHistory, JSON.stringify(items.filter((item) => item.type === "知乎研究").slice(0, 100))); }
function historyType() { return state.activeKnowledgePanel === "discoverPanel" ? "跨学科理解" : "读懂它"; }
function addHistory(type, input, sessionId, fullResponse = null) { if (!sessionId) return; const items = getHistory().filter((item) => item.sessionId !== sessionId); items.unshift({ id: makeId("history"), type, input, sessionId, timestamp: new Date().toISOString(), fullResponse }); saveHistory(items); renderHistoryMemory(); }
function renderHistoryMemory() { const box = $("historyMemoryList"); if (!box) return; box.replaceChildren(); const type = historyType(); const items = getHistory().filter((item) => item.type === type).slice(0, 5); if (!items.length) { box.textContent = "暂无历史记忆，去探索第一个概念吧～"; return; } items.forEach((item) => { const row = document.createElement("button"); row.type = "button"; row.className = `history-memory-item ${type === "读懂它" ? "explain" : "discover"}`; row.textContent = `[${type}] ${String(item.input).slice(0, 34)} · ${new Date(item.timestamp).toLocaleDateString("zh-CN")}`; row.addEventListener("click", () => restoreHistory(item)); box.append(row); }); }
function restoreHistory(item) { closeHistory(); if (item.type === "知乎研究") { const response = item.fullResponse || {}; openNoteSheet(null); $("noteTitle").value = response.title || item.input || "知乎研究笔记"; $("noteBody").innerHTML = response.content || ""; showToast("已恢复这次知乎研究，可选择分类后保存"); return; } const discover = item.type === "跨学科理解"; state.activeKnowledgePanel = discover ? "discoverPanel" : "explainPanel"; showKnowledgeLauncher(state.activeKnowledgePanel); revealKnowledgeEditor(); const panel = $(state.activeKnowledgePanel); const inputArea = panel?.querySelector(".input-area"); if (inputArea) inputArea.hidden = true; if (discover) { state.discoverSessionId = item.sessionId; discoverDraftText = item.input; $("discoverText").value = item.input; if (item.fullResponse) { const response = item.fullResponse; $("discoverTitle").textContent = `发现同源 · ${response.concept?.name || item.input}`; renderRichText($("discoverReport"), response.report || "同源分析已完成。"); renderCandidates(response); $("discoverResult").hidden = false; } else { $("discoverResult").hidden = true; showToast("这条历史没有保存完整结果，请重新发起查询"); } } else { state.explainSessionId = item.sessionId; explainDraftText = item.input; $("explainText").value = item.input; state.explainConversation = []; $("conversation").replaceChildren(); if (item.fullResponse) { const response = item.fullResponse; $("conceptName").textContent = response.concept?.name || "分析结果"; $("explainUserMessage").textContent = item.input; renderCoreAnswer(response.explanation || ""); renderLogicProfile(response.logic_profile); (response.conversation || []).forEach((message) => addMessage(message.role, message.content)); $("explainResult").hidden = false; $("followUp").hidden = false; } else { $("explainResult").hidden = true; $("followUp").hidden = true; showToast("这条历史没有保存完整结果，请重新发起查询"); } } window.scrollTo({ top: 0, behavior: "smooth" }); }
function renderHistory() { const box = $("historyList"); box.replaceChildren(); document.querySelectorAll(".history-filter").forEach((button) => { const active = button.dataset.historyType === historyFilterType; button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active)); }); const items = getHistory().filter((item) => item.type === historyFilterType); if (!items.length) { box.textContent = `暂无${historyFilterType}历史记录`; return; } items.forEach((item) => { const row = document.createElement("article"); row.className = "history-item"; const open = document.createElement("button"); open.className = "history-open"; open.type = "button"; const type = document.createElement("strong"); type.className = "history-type"; type.textContent = `[${item.type}]`; const text = document.createElement("span"); text.className = "history-summary"; text.textContent = String(item.input).slice(0, 80); open.append(type, text); open.addEventListener("click", () => restoreHistory(item)); const remove = document.createElement("button"); remove.type = "button"; remove.className = "history-remove"; remove.textContent = "⋮"; remove.setAttribute("aria-label", "更多操作"); remove.addEventListener("click", (event) => { event.stopPropagation(); pendingHistoryDeleteId = item.id; $("sheetBackdrop").hidden = false; $("historyDeleteConfirm").hidden = false; document.body.classList.add("sheet-open"); }); const time = document.createElement("small"); time.textContent = new Date(item.timestamp).toLocaleString("zh-CN"); row.append(open, remove, time); box.append(row); }); }
function openHistory() { historyFilterType = "跨学科理解"; renderHistory(); $("sheetBackdrop").hidden = false; $("historySheet").hidden = false; document.body.classList.add("sheet-open"); }
function closeHistory() { $("historySheet").hidden = true; if (!document.querySelector(".bottom-sheet:not([hidden])")) { $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); } }
/* ---- 种子数据只用于「清理」，不再用于「播种」----
   这里原本有 seedBooks / seedNotes / seedCategories 三个常量，localStorage 里没有数据时
   被 readStore() 克隆进去、再写回 localStorage —— 于是每个新账号一进来就有 3 本书、
   5 张卡、2 篇笔记、2 个笔记本，看起来像系统预置的数据。现在新账号必须是全空的，
   所以三个常量删掉，只留下 id —— 它们唯一的用途是 purgeSeedData() 精确匹配老账号
   里已经写进去的那一份。**不要再把任何 id 写回 localStorage。** */
const SEED_BOOK_IDS = ["book_agent", "book_cross", "book_system"];
const SEED_CARD_IDS = ["card_mcp", "card_state", "card_feedback", "card_homology", "card_closure"];
const SEED_NOTE_IDS = ["note_mcp", "note_agent"];
const SEED_CATEGORY_IDS = ["learning", "research"];
const clone = (value) => JSON.parse(JSON.stringify(value));
const makeId = (prefix) => `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
function readStore(key, fallback) { try { const value = JSON.parse(localStorage.getItem(key)); return Array.isArray(value) ? value : clone(fallback); } catch { return clone(fallback); } }
function getBooks() { const books = readStore(storageKeys.books, []); let changed = !localStorage.getItem(storageKeys.books); books.forEach((book) => (book.cards || []).forEach((card) => { const defaults = newReviewFields(); Object.keys(defaults).forEach((key) => { if (card[key] === undefined) { card[key] = defaults[key]; changed = true; } }); const created = card.created_at || card.createdAt || card.created || new Date().toISOString(); if (!card.created_at) { card.created_at = created; changed = true; } if (!card.createdAt) { card.createdAt = created; changed = true; } if (card.last_reviewed_at === undefined) { card.last_reviewed_at = null; changed = true; } const stage = Number(card.review_stage) || 0; if (card.status !== "mastered" && stage > 0 && stage <= 5) { const due = new Date(new Date(created).getTime() + [86400000, 2 * 86400000, 4 * 86400000, 7 * 86400000, 15 * 86400000][stage - 1]); if (!card.next_review_due || Number.isNaN(due.getTime())) { card.next_review_due = due.toISOString(); changed = true; } } const derived = cardStatusOf(card); if (card.status !== derived) { card.status = derived; changed = true; } })); if (changed) localStorage.setItem(storageKeys.books, JSON.stringify(books)); return books; }
function saveBook(book) { const books = getBooks(); const index = books.findIndex((item) => item.id === book.id); if (index >= 0) books[index] = book; else books.push(book); localStorage.setItem(storageKeys.books, JSON.stringify(books)); pushLibrary({ books }); return book; }
function getNotes() { const notes = readStore(storageKeys.notes, []), seen = new Set(); let changed = false; notes.forEach((note) => { note.attachments = (note.attachments || []).filter((file) => { const key = file.id || file.url || `${file.name}:${file.size}`; const owner = file.noteId || note.id; if (owner !== note.id || seen.has(key)) { changed = true; return false; } if (!file.noteId) { file.noteId = note.id; changed = true; } seen.add(key); return true; }); }); if (!localStorage.getItem(storageKeys.notes) || changed) localStorage.setItem(storageKeys.notes, JSON.stringify(notes)); return notes.sort((a, b) => new Date(b.date) - new Date(a.date)); }
function saveNote(note) { const notes = getNotes(); const index = notes.findIndex((item) => item.id === note.id); if (index >= 0) notes[index] = note; else notes.unshift(note); localStorage.setItem(storageKeys.notes, JSON.stringify(notes)); return note; }
function deleteNote(noteId) { localStorage.setItem(storageKeys.notes, JSON.stringify(getNotes().filter((item) => item.id !== noteId))); }
async function hydrateNotesFromServer() { if (DEMO_MODE) { renderNotes($("noteSearch")?.value || ""); return; } try { const response = await authFetch(apiUrl("/api/notes")); if (!response.ok) return; const data = await response.json(); if (Array.isArray(data.notes) && data.notes.length) { const merged = new Map(getNotes().map((note) => [note.id, note])); data.notes.forEach((note) => merged.set(note.id, note)); localStorage.setItem(storageKeys.notes, JSON.stringify([...merged.values()])); } } catch { /* Offline mode keeps local data. */ } renderNotes($("noteSearch")?.value || ""); }
function migrateRootNotes() { const notes = readStore(storageKeys.notes, []); let changed = false; notes.forEach((note) => { if (note.folderId === "default" || note.categoryId === "default") { note.folderId = null; delete note.categoryId; changed = true; } }); if (changed) localStorage.setItem(storageKeys.notes, JSON.stringify(notes)); }
function getCategories() { const items = readStore(storageKeys.categories, []).filter((folder) => folder.id !== "default"); let changed = !localStorage.getItem(storageKeys.categories); items.forEach((folder) => { if (!("coverUrl" in folder)) { folder.coverUrl = ""; changed = true; } if (!("syncStatus" in folder)) { folder.syncStatus = folder.coverUrl?.startsWith("blob:") || folder.coverUrl?.startsWith("data:") ? "local" : "default"; changed = true; } }); if (changed || readStore(storageKeys.categories, []).some((folder) => folder.id === "default")) localStorage.setItem(storageKeys.categories, JSON.stringify(items)); migrateRootNotes(); return items; }
function saveCategories(folders) { localStorage.setItem(storageKeys.categories, JSON.stringify(folders)); pushLibrary({ categories: folders }); }

/* ---- 书架 / 笔记本的服务端同步 ----
   这两样以前只活在 localStorage 里，换台设备登录同一个账号就没了。现在服务端是权威
   副本（`data/users/<uid>/library.json`），localStorage 退化成离线缓存：写的时候本地
   先写（离线照样能用），随后防抖推一次服务端。

   ⚠️ 卡片的服务端位置有**两处**：这里推的 library.json 内嵌一份（前端模型就是「书里
   装着卡」），`/api/cards/save` 在 card_store 的 cards.json 里另存一份。这是改动前就
   存在的双轨（本地书架 vs 服务端卡片本来就在各存各的），本次**有意不合并** —— 两边都
   别当成脏数据删掉，真要合并时以哪边为准得先想清楚。 */
let libraryPushTimer = null, libraryPushPending = {}, libraryPushFailures = 0;

function pushLibrary(partial) {
  if (DEMO_MODE) return;
  Object.assign(libraryPushPending, partial);
  libraryPushFailures = 0;   // 用户又动手了，重试预算重置
  window.clearTimeout(libraryPushTimer);
  libraryPushTimer = window.setTimeout(flushLibraryPush, 600);
}

async function flushLibraryPush() {
  window.clearTimeout(libraryPushTimer); libraryPushTimer = null;
  const payload = libraryPushPending;
  if (!Object.keys(payload).length) return;
  libraryPushPending = {};
  try {
    const response = await authFetch(apiUrl("/api/library"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    libraryPushFailures = 0;
  } catch {
    // 离线/服务端挂了：不回滚 —— 本地那一份本来就是完整的。退避重试几次，仍失败就
    // 放弃，下次任何一处改动都会带上全量重新推（宁可少推一次，也不要死循环打接口）。
    libraryPushPending = { ...payload, ...libraryPushPending };
    if (libraryPushFailures < 3) { libraryPushFailures += 1; libraryPushTimer = window.setTimeout(flushLibraryPush, 2000 * libraryPushFailures); }
  }
}

/* 按 id 精确摘掉种子数据，返回新对象（不改入参）。
   书本只有在「里面的卡全部是种子卡」时才整本删 —— 万一往种子里加过自己的卡，
   宁可留一点脏数据，也不能误删用户自己写的东西。 */
function stripSeeds(library) {
  const bookIds = new Set(SEED_BOOK_IDS), cardIds = new Set(SEED_CARD_IDS), categoryIds = new Set(SEED_CATEGORY_IDS);
  const books = Array.isArray(library.books) ? library.books : [];
  const categories = Array.isArray(library.categories) ? library.categories : [];
  const keptBooks = books.filter((book) => !bookIds.has(book.id) || (book.cards || []).some((card) => !cardIds.has(card.id)));
  const keptCategories = categories.filter((folder) => !categoryIds.has(folder.id));
  return {
    books: keptBooks,
    categories: keptCategories,
    droppedCategories: categories.filter((folder) => categoryIds.has(folder.id)).map((folder) => folder.id),
    changed: keptBooks.length !== books.length || keptCategories.length !== categories.length,
  };
}

/* 认领「账号体系之前」留在浏览器里的书架/笔记本（一次性，登录后跑一次）。
   为什么需要它：账号体系是 2026-09-13 才加的，在那之前应用是匿名的，书架和笔记本
   写在没有命名空间的 key 上（= buildStorageKeys("") 那一份）。登录之后
   applyStorageScope(uid) 只认带 `::<uid>` 的 key，旧数据就再也读不到了 ——
   表现出来是「笔记还在（笔记在服务端），书架却一片空白」，看着像数据丢了。
   只搬书架和笔记本：笔记本来就存在服务端（hydrateNotesFromServer 会拉回来），
   再搬一份本地旧笔记只会凭空多出幽灵笔记。
   ⚠️ 标记位**必须不带命名空间**：带上 uid 的话，同一浏览器登录第二个账号时会把它
   再认领一遍，等于把第一个人的书架送给了第二个人。除此之外只在「这个账号自己那份
   还是空的」时才搬，且搬完就走 purgeSeedData()，老版本种进去的种子不会被搬活。 */
function adoptLegacyLocalData() {
  const marker = "logic_coloc_legacy_adopted_v1";
  if (localStorage.getItem(marker)) return;
  localStorage.setItem(marker, "1");
  const legacy = buildStorageKeys("");
  ["books", "categories"].forEach((field) => {
    if (localStorage.getItem(storageKeys[field])) return;   // 这个账号已经有自己的数据了，不动
    const raw = localStorage.getItem(legacy[field]);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed) || !parsed.length) return;  // 空数组不值得搬
    } catch { return; }                                      // 坏数据不搬
    localStorage.setItem(storageKeys[field], raw);
  });
}

/* 擦掉老版本写进 localStorage 的种子数据（幂等，登录后跑一次）。
   老版本把种子克隆进 localStorage 并真的存了下来，所以光删常量不够，中招的账号得清一次。
   凡是指向被删笔记本的笔记都要把 folderId 置空，否则那些笔记会因为父目录消失而在
   所有视图里人间蒸发（笔记页只在分类匹配时才显示它）。
   ⚠️ 必须跑在第一次 pushLibrary 之前：顺序反了就是把种子推到服务端，那就不是清一下
   浏览器缓存能解决的了。 */
function purgeSeedData() {
  const local = stripSeeds({ books: getBooks(), categories: getCategories() });
  if (local.changed) {
    localStorage.setItem(storageKeys.books, JSON.stringify(local.books));
    localStorage.setItem(storageKeys.categories, JSON.stringify(local.categories));
  }
  const seedNoteIds = new Set(SEED_NOTE_IDS);
  const notes = getNotes();
  const keptNotes = notes.filter((note) => !seedNoteIds.has(note.id));
  const orphaned = [];
  keptNotes.forEach((note) => { if (local.droppedCategories.includes(noteFolderId(note))) { note.folderId = null; delete note.categoryId; orphaned.push(note); } });
  if (keptNotes.length !== notes.length || orphaned.length) localStorage.setItem(storageKeys.notes, JSON.stringify(keptNotes));
  // 服务端的笔记也存着 folderId，只改本地的话下次 hydrateNotesFromServer() 会把它带回来。
  orphaned.forEach((note) => { authFetch(apiUrl("/api/notes/move"), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: note.id, folderId: null }) }).catch(() => {}); });
  if (currentCategory !== "all" && local.droppedCategories.includes(currentCategory)) currentCategory = "all";
}

/* 登录后拉一次服务端书架/笔记本，和本地对账。规则：
   - 服务端有数据 → 它是权威副本，覆盖本地（本地那一份从此只是离线缓存）；
   - 服务端空、本地有 → 把本地推上去，这就是老账号的迁移路径，自动、一次性；
   - 两边都空 → 什么都不做，页面直接走空状态。
   拉不到（离线/接口 401）就直接返回，本地照旧可用。 */
async function syncLibrary() {
  let remote;
  try {
    const response = await authFetch(apiUrl("/api/library"), { cache: "no-store" });
    if (!response.ok) return;
    remote = await response.json();
  } catch { return; }

  const server = stripSeeds(remote);
  const payload = {};
  // 服务端那一半自己也带着种子时（历史遗留），摘干净后再推回去 —— 否则每次登录都会
  // 重新灌进来，用户会以为"删不掉"。
  if (server.books.length) localStorage.setItem(storageKeys.books, JSON.stringify(server.books));
  else { const local = getBooks(); payload.books = local.length ? local : server.books; }
  if (server.categories.length) localStorage.setItem(storageKeys.categories, JSON.stringify(server.categories));
  else { const local = getCategories(); payload.categories = local.length ? local : server.categories; }
  // 注意 payload 的某一半可能是 undefined（服务端那一半非空、无需回推），读长度要先兜底。
  // 两边都空、服务端也没有种子残留时不推 —— 没必要为一次空账号登录凭空造个空文件。
  const needsPush = server.changed || (payload.books || []).length > 0 || (payload.categories || []).length > 0;
  if (needsPush) { Object.assign(libraryPushPending, payload); flushLibraryPush(); }
}
// 昵称/签名/头像/能量都以服务端为准：登录时 /api/auth/me 把 profile 灌进 authProfile，
// localStorage 那份只当缓存和乐观显示用（换台设备登录才不会看到两份不一样的数据）。
function getUser() { if (authProfile) return authProfile; try { return JSON.parse(localStorage.getItem(storageKeys.user)) || { ...DEFAULT_USER }; } catch { return { ...DEFAULT_USER }; } }
function setUser(user) { authProfile = { ...getUser(), ...user }; localStorage.setItem(storageKeys.user, JSON.stringify(authProfile)); return authProfile; }
function fileDataUrl(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); }); }
async function uploadFile(file, purpose) { const form = new FormData(); form.append("file", file); form.append("purpose", purpose); const response = await authFetch(apiUrl("/api/upload"), { method: "POST", body: form }); if (!response.ok) throw new Error("upload unavailable"); const data = await response.json(); if (!data.url) throw new Error("missing url"); return data.url; }
let noteReviewSession = null, noteReviewNote = null, noteReviewBoot = null;
// 正文为空时写入的占位串（saveNote 落库用的是同一个常量）。读的时候必须认出来：
// 不认的话 notePlainText 返回的是这句占位串（非空），下面「回退到标题」的兜底就永远
// 不触发 —— 导师收到的「笔记原文」成了「暂未填写正文。」，于是回你「笔记内容尚未填写」，
// 哪怕附件里全是干货也救不回来。这正是 PDF 导入的笔记复盘不动的根因。
const NOTE_EMPTY_BODY = "暂未填写正文。";
// 笔记正文存的是富文本 HTML，发给 AI 前先转成纯文本（标签会干扰模型、也影响回答质量）
function notePlainText(html) { const holder = document.createElement("div"); holder.innerHTML = String(html || ""); return holder.textContent.replace(/\s+/g, " ").trim(); }
// 实时复盘与历史回看共用这一个气泡生成器，两处观感才一致（.chat-message* 样式见 style.css）
function reviewMessageRow(role, text) { const row = document.createElement("div"); row.className = `chat-message ${role === "user" ? "chat-message-user" : "chat-message-ai"}`; const bubble = document.createElement("div"); bubble.className = "chat-message-content"; bubble.textContent = String(text ?? ""); row.append(bubble); return row; }
function renderNoteReviewList(query = "") {
  const box = $("noteReviewList"); if (!box) return;
  box.replaceChildren();
  const notes = getNotes().filter((note) => `${note.title} ${note.content}`.toLowerCase().includes(query.toLowerCase()));
  if (!notes.length) { box.innerHTML = "<p class=\"empty-state\">没有找到相关笔记</p>"; return; }
  // 与笔记页共用 noteCardParts，所以这里的小卡片和「笔记」页长得一样
  notes.forEach((note) => {
    const item = document.createElement("button"); item.type = "button"; item.className = "note-card";
    const { thumbnail, copy } = noteCardParts(note);
    item.append(thumbnail, copy);
    item.addEventListener("click", () => { noteReviewNote = note; $("noteReviewConfirmText").textContent = `确定要根据《${note.title}》进行AI复盘吗？`; $("noteReviewConfirm").hidden = false; });
    box.append(item);
  });
}
async function startNoteReview(note) {
  // 点击「开始复盘」的瞬间就切到聊天界面，不等 AI 返回：所有 DOM 切换都在下面的 await 之前同步完成。
  // 正文为空时回退到标题（图片/PDF 导入的笔记没有正文，空串会让后端 404）。
  // 占位串也算「空」——见 NOTE_EMPTY_BODY。注意兜底必须留着：产出空串会直接 404。
  const plain = notePlainText(note?.content);
  const content = (plain && plain !== NOTE_EMPTY_BODY) ? plain : String(note?.title || "").trim();
  noteReviewNote = { ...note, content };
  noteReviewSession = `note_review_${Date.now()}`;
  $("noteReviewConfirm").hidden = true;
  $("noteReviewPage").hidden = false;
  document.body.classList.add("note-review-open");
  document.querySelectorAll(".fullscreen-page").forEach((p) => { if (p.id !== "noteReviewPage") p.hidden = true; });
  $("noteReviewSelect").hidden = true;
  $("noteReviewChat").hidden = false;
  $("noteReviewMessages").replaceChildren();
  const attachmentCount = (noteReviewNote.attachments || []).length;
  $("noteReviewStatus").textContent = `正在阅读笔记《${noteReviewNote.title}》${attachmentCount ? ` 与 ${attachmentCount} 个附件` : ""}...`;
  // 首条请求负责建会话，把它存起来：在它建好之前用户再发消息会先等它，不会各建一个会话把对话拆成两条。
  // 注意这里读到的 noteReviewBoot 仍是 null（赋值发生在 sendNoteReview 同步执行完之后），所以首条不会自等。
  const boot = sendNoteReview("请根据这篇笔记的内容，向我提出第一个问题");
  noteReviewBoot = boot;
  try { await boot; } finally { noteReviewBoot = null; }
}

async function sendNoteReview(message) {
  const messages = $("noteReviewMessages"); if (!messages) return;
  // 首条消息还在建会话时就等它，避免并发各建一个会话导致对话分叉
  const boot = noteReviewBoot; if (boot) await boot;
  messages.append(reviewMessageRow("user", message));
  const sendButton = $("noteReviewSend"); if (sendButton) sendButton.disabled = true;
  // 记下发这一轮时的会话：请求在飞的时候用户可能已经退出又重开了一场复盘，
  // 那种情况下回来的 session_id 属于上一场，不能拿它覆盖当前会话（否则后续几轮会写进旧记录）。
  const sessionAtSend = noteReviewSession;
  try {
    // 后端在 session 查不到且 note_content 非空时会现场建会话，并把真 session_id 回给我们。
    // note_id / note_title 只是给落盘的复盘记录打标签，普通对话不传这两个字段。
    const data = await request("/api/chat", { session_id: noteReviewSession, message, note_content: noteReviewNote?.content || "", note_id: noteReviewNote?.id || "", note_title: noteReviewNote?.title || "" });
    // 先算「还是同一场复盘」再改写会话 id：赋值之后这个比较就不成立了。
    const current = noteReviewSession === sessionAtSend;
    if (data?.session_id && current) noteReviewSession = data.session_id;
    // 把附件读取结果落到状态行（就是聊天视图顶部那行小字）。没有它的话，扫描件这类
    // 「读不出来」对用户完全不可见，只会觉得「AI 读不懂我的附件」。
    if (current) {
      $("noteReviewStatus").textContent = data?.attachment_notes?.length
        ? data.attachment_notes.join("　")
        : `对话中：《${noteReviewNote?.title || "笔记"}》`;
    }
    messages.append(reviewMessageRow("assistant", data?.answer || "暂时没有反馈"));
  } catch (error) {
    const status = Number(error?.status || error?.response?.status);
    const text = status === 404 ? "⚠️ 复盘会话未找到，请重新开始复盘。" : status === 503 ? "⚠️ AI 服务暂时不可用，请稍后重试。" : "⚠️ 复盘暂时无法继续，请稍后重试。";
    messages.append(reviewMessageRow("assistant", text));
  } finally {
    if (sendButton) sendButton.disabled = false;
  }
}
// 复盘页内四个视图互斥（选笔记 / 聊天 / 历史列表 / 记录详情）
function setNoteReviewView(view) { $("noteReviewSelect").hidden = view !== "select"; $("noteReviewChat").hidden = view !== "chat"; $("noteReviewHistory").hidden = view !== "history"; $("noteReviewRecord").hidden = view !== "record"; }

let pendingReviewDeleteId = null, currentReviewRecord = null, noteReviewSummaryRequested = false;

function formatReviewTime(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }

// 关闭/离开复盘页时后台生成一次小结。刻意不 await —— 生成要打 LLM，等它会让「返回」卡住。
// 服务端是幂等的（已有小结直接返回），所以这里多调一次不会有额外开销；keepalive 让请求能活过页面卸载。
function requestReviewSummary() {
  const id = noteReviewSession;
  // 还是本地临时 id 说明首轮没建会话成功，服务端根本没有这条记录
  if (!id || id.startsWith("note_review_") || noteReviewSummaryRequested) return;
  noteReviewSummaryRequested = true;
  authFetch(apiUrl(`/api/reviews/${encodeURIComponent(id)}/summary`), { method: "POST", keepalive: true }).catch(() => {});
}

async function openNoteReviewHistory() {
  requestReviewSummary();
  setNoteReviewView("history");
  const list = $("noteReviewHistoryList");
  list.innerHTML = "<p class=\"empty-state\">正在加载…</p>";
  try {
    // request() 封装只支持 POST，GET 走裸 fetch（同 openSchedulePage）
    const response = await authFetch(apiUrl("/api/reviews"), { cache: "no-store" });
    if (!response.ok) throw new Error("review list unavailable");
    const data = await response.json();
    renderReviewHistory(Array.isArray(data.reviews) ? data.reviews : []);
  } catch { list.innerHTML = "<p class=\"empty-state\">历史记录加载失败，请稍后重试。</p>"; }
}

function renderReviewHistory(reviews) {
  const list = $("noteReviewHistoryList"); if (!list) return;
  list.replaceChildren();
  if (!reviews.length) { list.innerHTML = "<p class=\"empty-state\">还没有复盘记录。选一条笔记聊上几句，就会出现在这里。</p>"; return; }
  reviews.forEach((review) => {
    const item = document.createElement("article"); item.className = "note-card review-history-item";
    const open = document.createElement("button"); open.type = "button"; open.className = "review-history-open";
    const title = document.createElement("strong"); title.textContent = review.noteTitle || "未命名笔记";
    const meta = document.createElement("small"); meta.textContent = `${formatReviewTime(review.startedAt)} · ${review.turnCount || 0} 轮${review.summary ? " · 已小结" : ""}`;
    open.append(title, meta);
    open.addEventListener("click", () => openReviewRecord(review.id));
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "danger-text review-history-delete"; remove.textContent = "删除";
    remove.addEventListener("click", () => { pendingReviewDeleteId = review.id; $("noteReviewDeleteText").textContent = `确定删除《${review.noteTitle || "未命名笔记"}》的复盘记录吗？删除后无法恢复。`; $("noteReviewDeleteConfirm").hidden = false; });
    item.append(open, remove);
    list.append(item);
  });
}

async function deleteReviewRecord() {
  const id = pendingReviewDeleteId; pendingReviewDeleteId = null;
  $("noteReviewDeleteConfirm").hidden = true;
  if (!id) return;
  try {
    const response = await authFetch(apiUrl(`/api/reviews/${encodeURIComponent(id)}`), { method: "DELETE" });
    if (!response.ok) throw new Error("review delete failed");
    showToast("已删除");
  } catch { showToast("删除失败，请稍后重试"); }
  openNoteReviewHistory();
}

async function openReviewRecord(id) {
  setNoteReviewView("record");
  $("reviewRecordTitle").textContent = "加载中…";
  $("reviewRecordMessages").replaceChildren();
  $("reviewRecordSummary").replaceChildren();
  try {
    const response = await authFetch(apiUrl(`/api/reviews/${encodeURIComponent(id)}`), { cache: "no-store" });
    if (!response.ok) throw new Error("review unavailable");
    const data = await response.json();
    const record = data.review || {};
    currentReviewRecord = record;
    $("reviewRecordTitle").textContent = record.noteTitle || "未命名笔记";
    const box = $("reviewRecordMessages");
    // 与实时复盘共用 reviewMessageRow，所以回看和当时的观感一致
    (record.messages || []).forEach((message) => box.append(reviewMessageRow(message.role, message.content)));
    renderReviewSummary(record);
  } catch {
    currentReviewRecord = null;
    $("reviewRecordTitle").textContent = "记录加载失败";
    $("reviewRecordMessages").innerHTML = "<p class=\"empty-state\">这条记录读不出来了，请返回重试。</p>";
  }
}

function renderReviewSummary(record) {
  const box = $("reviewRecordSummary"); if (!box) return;
  box.replaceChildren();
  if (record.summary) {
    const title = document.createElement("h4"); title.textContent = "AI 小结";
    const body = document.createElement("p"); body.textContent = record.summary;
    box.append(title, body);
    return;
  }
  // 小结是离开复盘页时在后台生成的：用户直接关标签页、或那次生成失败时就没有 —— 这里兜底补一次
  const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = "生成 AI 小结";
  button.addEventListener("click", async () => {
    button.disabled = true; button.textContent = "生成中…";
    try { await request(`/api/reviews/${encodeURIComponent(record.id)}/summary`, {}); await openReviewRecord(record.id); }
    catch { button.disabled = false; button.textContent = "生成失败，点击重试"; }
  });
  box.append(button);
}

function closeReviewRecord() { currentReviewRecord = null; openNoteReviewHistory(); }

function openNoteReviewPage() {
  document.querySelectorAll(".fullscreen-page").forEach((p) => { p.hidden = p.id !== "noteReviewPage"; });
  document.body.classList.add("note-review-open");
  // 清掉上一次可能残留的弹层与状态，避免串到这一次
  $("noteReviewConfirm").hidden = true;
  $("noteReviewDeleteConfirm").hidden = true;
  noteReviewNote = null; noteReviewSession = null; noteReviewBoot = null;
  pendingReviewDeleteId = null; currentReviewRecord = null; noteReviewSummaryRequested = false;
  $("noteReviewMessages").replaceChildren();
  $("noteReviewStatus").textContent = "";
  setNoteReviewView("select");
  renderNoteReviewList();
}let currentBookId = null, editingBookId = null, pendingBookCoverFile = null, pendingBookCoverUrl = "", editingCardId = null, editingNoteId = null, pendingCardFrontImageUrl = "", pendingCardBackImageUrl = "", actionNoteId = null, actionFolderId = null, pendingAttachments = [], currentCategory = "all", pendingAvatarFile = null, pendingAvatarUrl = "", pendingNoteTemplate = { category: "basic", pattern: "blank", color: "white" }, pendingNoteCoverFile = null, pendingNoteCoverUrl = "", reviewQueue = [], reviewPosition = 0, reviewFilter = "unmastered", reviewPhase = "memory", pendingMemoryChoice = null, noteLayout = "grid", batchMode = false, selectedNoteIds = new Set(), quickImportKind = "", reviewManageMode = false, selectedReviewIds = new Set(), activeReviewCard = null, reviewStats = { total: 0, mastered: 0, difficult: new Set() }, noteSearchTimer = null, savedEditorRange = null;
const LEVEL_THRESHOLDS = [{ level: "LV.1", title: "学术萌新", min: 0, next: 100 }, { level: "LV.2", title: "知识学徒", min: 100, next: 300 }, { level: "LV.3", title: "科研助手", min: 300, next: 500 }, { level: "LV.4", title: "探索达人", min: 500, next: 1000 }, { level: "LV.5", title: "刘看山首席研究员", min: 1000, next: null }];
function getLevelInfo(points) { const value = Math.max(0, Number(points) || 0); const item = [...LEVEL_THRESHOLDS].reverse().find((level) => value >= level.min) || LEVEL_THRESHOLDS[0]; const progressPercent = item.next === null ? 100 : Math.max(0, Math.min(100, ((value - item.min) / (item.next - item.min)) * 100)); return { level: item.level, title: item.title, currentMin: item.min, nextMax: item.next, progressPercent }; }
const defaultNoteTemplate = () => ({ category: "basic", pattern: "blank", color: "white" });
function templateClasses(template = defaultNoteTemplate()) { return `template-${template.pattern || "blank"} color-${template.color || "white"}`; }
function attachmentState(files = []) { if (!files.length) return null; if (files.some((file) => file.syncStatus === "local" || String(file.url || "").startsWith("blob:"))) return { key: "local", label: "本地暂存" }; if (files.every((file) => file.syncStatus === "synced")) return { key: "synced", label: "已同步云端" }; return { key: "pending", label: "未同步" }; }
function syncBadge(state) { const badge = document.createElement("span"); badge.className = `sync-badge ${state.key}`; badge.textContent = state.label; return badge; }
function formatFileSize(bytes) { const value = Number(bytes) || 0; if (!value) return "大小未知"; if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB`; }
function getPoints() { if (authProfile) return Math.max(0, Number(authProfile.points) || 0); try { return Number(JSON.parse(localStorage.getItem(storageKeys.points))?.total || 0); } catch { return 0; } }
function setPoints(total, reason = "") { const safe = Math.max(0, Number(total) || 0); localStorage.setItem(storageKeys.points, JSON.stringify({ total: safe, reason, lastUpdatedAt: new Date().toISOString() })); if (authProfile) authProfile.points = safe; return safe; }
function addPoints(amount, reason = "") {
  const before = getPoints(), previousLevel = getLevelInfo(before), total = before + amount, currentLevel = getLevelInfo(total);
  // 先本地乐观更新，界面立刻有反馈；服务端返回**权威值**后再对齐一次。
  // 本地不是权威：刷新页面时 /api/auth/me 会重新灌一遍，乐观多算的会自己纠正回来。
  setPoints(total, reason);
  renderProfile(); updateReviewCounts();
  if (previousLevel.level !== currentLevel.level) { showToast(`🎉 恭喜升级！${currentLevel.level} ${currentLevel.title}`); document.querySelectorAll(".level-card").forEach((card) => { card.classList.remove("level-flash"); void card.offsetWidth; card.classList.add("level-flash"); }); }
  // 只发增量：服务端是唯一权威，客户端说「我现在有 9999 分」不该被当真。
  authFetch(apiUrl("/api/user/points"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ delta: amount, reason }) })
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => { if (data?.points) { setPoints(data.points.total, data.points.reason); renderProfile(); } })
    .catch(() => { /* 离线时保持乐观值，下次登录会对齐 */ });
  return total;
}

/* 书架空态那张立体书本插画（内联 SVG：不额外发请求、不引图片资源、跟着页面一起缩放）。
   等轴测的一本合着的书 —— 顶面是封面，左下立面是书脊（深一档），右下立面是书口
   （浅色 + 两道页纹），底下压一层很淡的椭圆投影。三个面各自用**自己的填充色**描一圈
   5px、linejoin:round 的边：这是给多边形加圆角的土办法，顺带把面与面的接缝盖住。
   颜色全部取自 style.css 的 --brand-* 色板（500 封面 / 800 书脊 / 50 书口），
   SVG 属性里写不了 var()，所以这里是字面量 —— 改色板时要一起改。 */
const BOOK_ART = `<svg class="empty-art" viewBox="0 0 128 128" aria-hidden="true" focusable="false">
  <ellipse cx="69" cy="90" rx="45" ry="8" fill="rgba(74,144,226,.14)"/>
  <polygon points="22,42 78,70 78,82 22,54" fill="#2F6BB0" stroke="#2F6BB0" stroke-width="5" stroke-linejoin="round"/>
  <polygon points="78,70 116,54 116,66 78,82" fill="#F1F7FF" stroke="#F1F7FF" stroke-width="5" stroke-linejoin="round"/>
  <path d="M80 75 L112 61 M80 78.5 L112 64.5" stroke="rgba(74,144,226,.30)" stroke-width="1.6" stroke-linecap="round"/>
  <polygon points="22,42 60,26 116,54 78,70" fill="#67AAF3" stroke="#67AAF3" stroke-width="5" stroke-linejoin="round"/>
  <polygon points="42,44 63,35 97,52 76,61" fill="none" stroke="rgba(255,255,255,.55)" stroke-width="2" stroke-linejoin="round"/>
</svg>`;

function renderBooks() {
  const list = $("bookList"); list.replaceChildren();
  const books = getBooks();
  books.forEach((book) => {
    const item = document.createElement("article"); item.className = "book-card"; const button = document.createElement("button"); button.type = "button"; button.className = "book-open";
    const cover = document.createElement("span"); cover.className = "book-cover"; if (book.coverUrl || book.localCoverDataUrl) { const image = document.createElement("img"); image.src = book.coverUrl || book.localCoverDataUrl; image.alt = ""; cover.append(attachImageFallback(image, cover, book.icon || "▤")); } else cover.textContent = book.icon || "▤";
    const copy = document.createElement("span"); copy.className = "book-copy"; const title = document.createElement("strong"); title.textContent = book.name; const count = document.createElement("small"); count.textContent = `${book.cards.length} 张卡片`; copy.append(title, count);
    const edit = document.createElement("button"); edit.type = "button"; edit.className = "book-edit"; edit.setAttribute("aria-label", `编辑${book.name}`); edit.textContent = "✎";
    button.append(cover, copy); button.addEventListener("click", () => openBook(book.id)); edit.addEventListener("click", () => openBookSheet(book.id)); item.append(button, edit); list.append(item);
  });
  // 新账号第一次进来这里必须是空的（`.book-list` 是单列 grid，`.empty-state` 正好铺满）。
  // 判据用上面读到的 books 而不是 list.children —— 后者在 id 被改错时是 undefined（$() 的兜底桩没有 children），会直接抛。
  if (!books.length) {
    const empty = document.createElement("div"); empty.className = "empty-state empty-state-book";
    empty.innerHTML = BOOK_ART;   // 静态常量，不含任何用户数据
    const tip = document.createElement("p"); tip.textContent = "还没有书本，点右上角「＋ 新建书本」建第一本";
    empty.append(tip); list.append(empty);
  }
}
function openBook(bookId) {
  const book = getBooks().find((item) => item.id === bookId); if (!book) return; currentBookId = bookId; $("bookDetailTitle").textContent = book.name;
  const list = $("miniCardList"); list.replaceChildren();
  book.cards.forEach((card) => { const item = document.createElement("button"); item.type = "button"; item.className = "mini-card"; const title = document.createElement("strong"); title.textContent = card.front; const body = document.createElement("p"); body.textContent = card.back; const status = document.createElement("span"); status.className = `mini-card-status ${card.status}`; status.textContent = card.status === "mastered" ? "已掌握" : "待复习"; item.append(title, body, status); if (card.localOnly) { const badge = document.createElement("span"); badge.className = "local-card-badge"; badge.textContent = "[本地暂存]"; item.append(badge); } item.addEventListener("click", () => openCardSheet(card.id)); list.append(item); });
  // 空书里的提示语。不用挂额外事件：存卡/删卡之后一定会重跑 openBook()，这句话自己就没了。
  if (!book.cards.length) { const empty = document.createElement("p"); empty.className = "empty-state"; empty.textContent = "添加第一张卡片"; list.append(empty); }
  $("bookList").hidden = true; $("newBookButton").hidden = true; $("bookDetail").hidden = false;
}
function renderCategories() { const list = $("noteCategories"); list.replaceChildren(); [{ id: "all", name: "全部" }, ...getCategories()].forEach((category) => { const button = document.createElement("button"); button.type = "button"; button.className = `note-category ${currentCategory === category.id ? "active" : ""}`; button.textContent = category.name; button.addEventListener("click", () => { currentCategory = category.id; renderCategories(); renderNotes($("noteSearch").value); }); list.append(button); }); const add = document.createElement("button"); add.type = "button"; add.className = "note-category"; add.textContent = "＋ 新建分类"; add.addEventListener("click", openFolderSheet); list.append(add); }
function renderAttachments() { const list = $("noteAttachments"); list.replaceChildren(); pendingAttachments.filter((file) => file.noteId === editingNoteId).forEach((file) => { const index = pendingAttachments.indexOf(file), item = document.createElement("div"); item.className = "attachment-card"; const icon = document.createElement("span"); icon.textContent = file.mimeType === "application/pdf" ? "📄" : "🖼️"; const copy = document.createElement("span"); copy.className = "attachment-copy"; const name = document.createElement("strong"); name.textContent = `${file.name} · ${formatFileSize(file.size)}`; const state = attachmentState([file]); copy.append(name, syncBadge(state)); const open = document.createElement("button"); open.type = "button"; open.textContent = "预览"; open.addEventListener("click", () => previewAttachment(file)); const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "移除"; remove.addEventListener("click", () => { pendingAttachments.splice(index, 1); saveAttachmentDrafts(); renderAttachments(); }); item.append(icon, copy, open, remove); if (state.key !== "synced") { const warning = document.createElement("p"); warning.className = "attachment-warning"; warning.textContent = "刷新浏览器可能丢失，请尽快完成后端对接。"; item.append(warning); } list.append(item); }); }
function applyNoteTemplate(template) { const body = $("noteBody"); body.className = body.className.split(" ").filter((name) => !name.startsWith("template-") && !name.startsWith("color-") && name !== "template-editor").join(" "); body.classList.add("template-editor", ...templateClasses(template).split(" ")); }
function attachmentDraftMap() { try { const value = JSON.parse(localStorage.getItem(storageKeys.attachmentDrafts)); return value && !Array.isArray(value) ? value : {}; } catch { return {}; } }
function saveAttachmentDrafts() { const drafts = attachmentDraftMap(); const local = pendingAttachments.filter((item) => item.noteId === editingNoteId && item.syncStatus === "local"); if (local.length) drafts[editingNoteId] = local; else delete drafts[editingNoteId]; localStorage.setItem(storageKeys.attachmentDrafts, JSON.stringify(drafts)); }
function openNoteSheet(noteId = null, template = null) { const note = getNotes().find((item) => item.id === noteId); editingNoteId = note?.id || makeId("note"); pendingNoteTemplate = clone(note?.template || template || defaultNoteTemplate()); pendingNoteCoverFile = null; pendingNoteCoverUrl = note?.coverUrl || ""; const drafts = attachmentDraftMap()[editingNoteId] || []; pendingAttachments = clone((note?.attachments || drafts).filter((file) => !file.noteId || file.noteId === editingNoteId).map((file) => ({ ...file, noteId: editingNoteId }))); $("noteSheetTitle").textContent = note ? "编辑笔记" : "新建笔记"; $("noteTitle").value = note?.title || ""; $("noteBody").innerHTML = note?.content || ""; applyNoteTemplate(pendingNoteTemplate); const select = $("noteCategorySelect"); select.replaceChildren(); select.add(new Option("根目录", "")); getCategories().forEach((category) => select.add(new Option(category.name, category.id))); select.value = noteFolderId(note || {}) || (currentCategory === "all" ? "" : currentCategory); renderAttachments(); $("deleteNote").hidden = !note; $("sheetBackdrop").hidden = false; $("noteSheet").hidden = false; document.body.classList.add("sheet-open"); $("noteTitle").focus(); }
function closeNoteSheet() { $("noteSheet").hidden = true; $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); }
function closeNoteActionSheet() { $("noteActionSheet").hidden = true; $("renameNotePanel").hidden = true; $("deleteNotePanel").hidden = true; $("noteActionMenu").hidden = false; if (!document.querySelector(".bottom-sheet:not([hidden])")) { $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); } }
function openNoteActionSheet(noteId) { const note = getNotes().find((item) => item.id === noteId); if (!note) return; actionNoteId = noteId; $("noteActionName").textContent = note.title; $("noteActionThumb").className = `note-action-thumb ${templateClasses(note.template)}`; $("noteActionThumb").textContent = note.attachments?.some((file) => file.mimeType === "application/pdf") ? "PDF" : "笔记"; $("noteActionMenu").hidden = false; $("renameNotePanel").hidden = true; $("deleteNotePanel").hidden = true; $("sheetBackdrop").hidden = false; $("noteActionSheet").hidden = false; document.body.classList.add("sheet-open"); }
function renderFolderActionSummary() { const folder = getCategories().find((item) => item.id === actionFolderId); if (!folder) return; $("folderActionName").textContent = folder.name; const thumb = $("folderActionThumb"); thumb.replaceChildren(); if (folder.coverUrl) { const image = document.createElement("img"); image.src = folder.coverUrl; image.alt = ""; thumb.append(attachImageFallback(image, thumb, "▱")); } else thumb.textContent = "▱"; const status = $("folderCoverStatus"); status.hidden = !folder.coverUrl; status.className = `sync-badge ${folder.syncStatus === "synced" ? "synced" : "local"}`; status.textContent = folder.syncStatus === "synced" ? "已同步云端" : "本地暂存"; $("deleteFolderAction").disabled = false; $("deleteFolderAction").textContent = "删除文件夹"; }
function openFolderActionSheet(folderId) { if (!getCategories().some((item) => item.id === folderId)) return; actionFolderId = folderId; $("folderActionMenu").hidden = false; $("renameFolderPanel").hidden = true; $("deleteFolderPanel").hidden = true; renderFolderActionSummary(); $("sheetBackdrop").hidden = false; $("folderActionSheet").hidden = false; document.body.classList.add("sheet-open"); }
function closeFolderActionSheet() { $("folderActionSheet").hidden = true; $("renameFolderPanel").hidden = true; $("deleteFolderPanel").hidden = true; $("folderActionMenu").hidden = false; if (!document.querySelector(".bottom-sheet:not([hidden])")) { $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); } }
function openTemplateSheet() { pendingNoteTemplate = defaultNoteTemplate(); pendingNoteCoverFile = null; pendingNoteCoverUrl = ""; renderTemplateSelection(); $("sheetBackdrop").hidden = false; $("noteTemplateSheet").hidden = false; document.body.classList.add("sheet-open"); }
function closeTemplateSheet() { $("noteTemplateSheet").hidden = true; $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); }
function renderTemplateSelection() { document.querySelectorAll("[data-template-category]").forEach((button) => button.classList.toggle("active", button.dataset.templateCategory === pendingNoteTemplate.category)); document.querySelectorAll("[data-template-pattern]").forEach((button) => button.classList.toggle("active", button.dataset.templatePattern === pendingNoteTemplate.pattern)); document.querySelectorAll("[data-template-color]").forEach((button) => button.classList.toggle("active", button.dataset.templateColor === pendingNoteTemplate.color)); const paper = $("templatePaperPreview"), cover = $("templateCoverPreview"); paper.className = `template-paper ${templateClasses(pendingNoteTemplate)}`; cover.className = `template-cover color-${pendingNoteTemplate.color}`; cover.replaceChildren(); const renderDefault = () => { cover.replaceChildren(); const label = document.createElement("span"); label.textContent = "NOTE"; const hint = document.createElement("small"); hint.textContent = "默认封面"; cover.append(label, hint); }; if (pendingNoteCoverUrl) { const image = document.createElement("img"); image.src = pendingNoteCoverUrl; image.alt = "封面预览"; cover.append(attachImageFallback(image, cover)); image.addEventListener("error", renderDefault, { once: true }); } else renderDefault(); }
function openNoteCreateSheet() { $("sheetBackdrop").hidden = false; $("noteCreateSheet").hidden = false; document.body.classList.add("sheet-open"); }
function closeNoteCreateSheet() { $("noteCreateSheet").hidden = true; if (!document.querySelector(".bottom-sheet:not([hidden])")) { $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); } }
function openFolderSheet() { $("folderName").value = ""; $("sheetBackdrop").hidden = false; $("folderSheet").hidden = false; document.body.classList.add("sheet-open"); window.setTimeout(() => $("folderName").focus(), 80); }
function closeFolderSheet() { $("folderSheet").hidden = true; if (!document.querySelector(".bottom-sheet:not([hidden])")) { $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); } }
async function addImportedAttachment(file) { if (!file || !editingNoteId) return; let url = URL.createObjectURL(file), syncStatus = "local"; try { url = await uploadFile(file, "note_attachment"); syncStatus = "synced"; } catch { showToast("后端存储服务暂未接通，文件已在本机临时保存，刷新页面可能失效。"); } pendingAttachments.push({ id: makeId("attachment"), noteId: editingNoteId, name: file.name, url, mimeType: file.type, size: file.size, syncStatus }); saveAttachmentDrafts(); renderAttachments(); }
function setBatchMode(enabled) { batchMode = enabled; if (!enabled) selectedNoteIds.clear(); $("batchToolbar").hidden = !enabled; $("toggleBatchMode").textContent = enabled ? "×" : "⠿"; renderNotes($("noteSearch").value); }
function openBatchMove() { const box = $("batchFolderOptions"); box.replaceChildren(); getCategories().forEach((category) => { const button = document.createElement("button"); button.type = "button"; button.textContent = category.name; button.addEventListener("click", () => { const notes = getNotes(); notes.forEach((note) => { if (selectedNoteIds.has(note.id)) { note.folderId = category.id; delete note.categoryId; } }); localStorage.setItem(storageKeys.notes, JSON.stringify(notes)); $("batchMoveSheet").hidden = true; setBatchMode(false); showToast(`已移动到${category.name}`); }); box.append(button); }); $("sheetBackdrop").hidden = false; $("batchMoveSheet").hidden = false; document.body.classList.add("sheet-open"); }
function renderCardImagePreview(side) { const url = side === "front" ? pendingCardFrontImageUrl : pendingCardBackImageUrl, preview = $(side === "front" ? "cardFrontImagePreview" : "cardBackImagePreview"), remove = $(side === "front" ? "removeCardFrontImage" : "removeCardBackImage"); preview.src = url || ""; preview.hidden = !url; remove.hidden = !url; }
function openCardSheet(cardId = null) { editingCardId = cardId; const book = getBooks().find((item) => item.id === currentBookId); const card = book?.cards.find((item) => item.id === cardId); pendingCardFrontImageUrl = card?.frontImageUrl || ""; pendingCardBackImageUrl = card?.backImageUrl || ""; $("cardSheetTitle").textContent = card ? "编辑知识卡片" : "新建知识卡片"; $("cardFront").value = card?.front || ""; $("cardBack").value = card?.back || ""; renderCardImagePreview("front"); renderCardImagePreview("back"); $("deleteCard").hidden = !card; $("sheetBackdrop").hidden = false; $("cardSheet").hidden = false; document.body.classList.add("sheet-open"); $("cardFront").focus(); }
function closeCardSheet() { $("cardSheet").hidden = true; $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); }
function openBookSheet(bookId = null) { editingBookId = bookId; const book = getBooks().find((item) => item.id === bookId); pendingBookCoverFile = null; pendingBookCoverUrl = book?.coverUrl || book?.localCoverDataUrl || ""; $("bookSheetTitle").textContent = book ? "编辑书本" : "新建书本"; $("bookName").value = book?.name || ""; const preview = $("bookCoverPreview"); preview.replaceChildren(); if (pendingBookCoverUrl) { const image = document.createElement("img"); image.src = pendingBookCoverUrl; preview.append(attachImageFallback(image, preview, book?.icon || "▤")); } else preview.textContent = book?.icon || "▤"; $("sheetBackdrop").hidden = false; $("bookSheet").hidden = false; document.body.classList.add("sheet-open"); }
function closeBookSheet() { pendingSaveCardAfterNewBook = false; $("bookSheet").hidden = true; $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); }
function previewAttachment(file) { $("attachmentPreviewTitle").textContent = file.name; const body = $("attachmentPreviewBody"); body.replaceChildren(); if (!file.url) { showToast("该附件需要重新上传后才能预览"); return; } const viewer = document.createElement(file.mimeType === "application/pdf" ? "iframe" : "img"); viewer.src = file.url; body.append(viewer); $("attachmentPreview").hidden = false; }
function openProfileSheet() { const user = getUser(); pendingAvatarFile = null; pendingAvatarUrl = user.avatarUrl || ""; $("profileNicknameInput").value = user.nickname; $("profileSignatureInput").value = user.signature; $("profileAvatarPreview").src = pendingAvatarUrl; $("profileAvatarPreview").closest(".avatar-picker").classList.toggle("has-preview", Boolean(pendingAvatarUrl)); $("sheetBackdrop").hidden = false; $("profileSheet").hidden = false; document.body.classList.add("sheet-open"); }
function closeProfileSheet() { $("profileSheet").hidden = true; $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); }
function openSettingsSheet() { $("sheetBackdrop").hidden = false; $("settingsSheet").hidden = false; document.body.classList.add("sheet-open"); }
function openZhihuLibrary() { $("sheetBackdrop").hidden = false; $("zhihuConsentSheet").hidden = false; document.body.classList.add("sheet-open"); }
function closeZhihuConsent() { $("zhihuConsentSheet").hidden = true; if (!document.querySelector(".bottom-sheet:not([hidden])")) { $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); } }
function confirmZhihuConsent() { closeZhihuConsent(); request("/api/zhihu/oauth/authorize", null, { method: "GET" }).then((data) => { if (data.url) window.open(data.url, "_blank", "noopener"); }).catch((error) => showToast(error.message || "知乎 OAuth 尚未配置，请联系管理员")); }
function closeSettingsSheet() { $("settingsSheet").hidden = true; if (!document.querySelector(".bottom-sheet:not([hidden])")) { $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); } }
function openAboutSheet() { closeSettingsSheet(); $("sheetBackdrop").hidden = false; $("aboutSheet").hidden = false; document.body.classList.add("sheet-open"); }
function closeAboutSheet() { $("aboutSheet").hidden = true; if (!document.querySelector(".bottom-sheet:not([hidden])")) { $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); } }
function allReviewCards() { return getBooks().flatMap((book) => book.cards.map((card) => ({ ...card, bookId: book.id, bookName: book.name }))); }
// 徽章必须数「点进这个 tab 会看到的东西」：三个 tab 是状态分类（未掌握/已掌握/全部），
// 所以未掌握 = status 未掌握，未掌握 + 已掌握 = 全部。以前这里数的是「今天到期的」，
// 于是复习完最后一张就出现「未掌握 0 + 已掌握 0 ≠ 全部 1」，而且从「我的记录 → 未掌握卡片 1」
// 点进来会变成 0。今天到期的张数在右上角铃铛（renderNotificationBadge）那边，别混进来。
function updateReviewCounts() { const cards = allReviewCards(); $("unmasteredCount").textContent = cards.filter((card) => card.status === "unmastered").length; $("masteredCount").textContent = cards.filter((card) => card.status === "mastered").length; $("allCount").textContent = cards.length; }
// 手动改状态。因为状态是派生的，光改 status 会被下一次 getBooks() 立刻改回去 ——
// 必须连着到期时间一起改：「移出已掌握」= 立刻到期、重新进复习队列；
// 「标记为已掌握」= 把到期时间推到至少明天（没有到期时间就没法「已掌握」）。
function persistReviewStatus(cardRef, status) {
  const book = getBooks().find((item) => item.id === cardRef.bookId);
  const card = book?.cards.find((item) => item.id === cardRef.id);
  if (!card || card.status === status) return false;
  card.status = status;
  if (status === "unmastered") card.next_review_due = new Date().toISOString();
  else if (!(Date.parse(card.next_review_due) > Date.now())) card.next_review_due = new Date(Date.now() + 86400000).toISOString();
  saveBook(book);
  return true;
}
function switchReviewFilter(filter) { reviewFilter = filter; document.querySelectorAll(".review-filter").forEach((button) => button.classList.toggle("active", button.dataset.reviewFilter === filter)); const testing = filter === "unmastered"; $("reviewTest").hidden = !testing; $("reviewLibrary").hidden = testing; if (testing) buildReviewQueue(); else { updateReviewCounts(); renderReviewLibrary(filter); } }

function removeReviewCards(ids) { const wanted = new Set(ids); getBooks().forEach((book) => { const cards = book.cards.filter((card) => !wanted.has(card.id)); if (cards.length !== book.cards.length) { book.cards = cards; saveBook(book); } }); updateReviewCounts(); renderProfile(); }
function buildReviewQueue() { const allCards = allReviewCards(); reviewQueue = allCards.filter((card) => isCardDue(card)); reviewPosition = 0; reviewPhase = "memory"; reviewStats = { total: reviewQueue.length, mastered: 0, difficult: new Set() }; saveReviewSession(); updateReviewCounts(); updateReviewCard(); }
function readReviewSession() { try { const value = JSON.parse(localStorage.getItem(storageKeys.reviewSession)); return { total: Number(value?.total) || 0, mastered: Number(value?.mastered) || 0, difficult: new Set(value?.difficult || []) }; } catch { return { total: 0, mastered: 0, difficult: new Set() }; } }
function saveReviewSession() { localStorage.setItem(storageKeys.reviewSession, JSON.stringify({ total: reviewStats.total, mastered: reviewStats.mastered, difficult: [...reviewStats.difficult] })); }
function setReviewPhase(phase, choice = null) { reviewPhase = phase; const answer = phase === "answer"; if (choice) { pendingMemoryChoice = choice; if (choice !== "know" && reviewQueue[reviewPosition]) reviewStats.difficult.add(reviewQueue[reviewPosition].id); saveReviewSession(); } if (!answer) pendingMemoryChoice = null; $("flashcard").classList.toggle("flipped", answer); $("memoryActions").hidden = answer; $("answerActions").hidden = !answer; const choiceLabel = { know: "认识", vague: "模糊", forgot: "忘记了" }[pendingMemoryChoice]; $("reviewHint").textContent = answer ? `你刚才选择了「${choiceLabel || "查看释义"}」。请对照释义，再决定“下一词”或“记错了”` : "瞬间想起含义，选「认识」；思考后想起含义，选「模糊」"; }
function updateReviewCard() { const card = reviewQueue[reviewPosition]; $("flashcard").classList.remove("flipped", "leaving"); setReviewPhase("memory"); if (!card) { const user = getUser(), avatar = localStorage.getItem("userAvatar") || user.avatarUrl || ""; $("reviewCompleteAvatar").src = avatar || "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Ccircle cx='32' cy='32' r='31' fill='%23e4f0ea'/%3E%3Cpath d='M18 41c4-13 8-19 14-19s10 6 14 19' fill='none' stroke='%23176b4d' stroke-width='4' stroke-linecap='round'/%3E%3Ccircle cx='26' cy='29' r='2' fill='%23176b4d'/%3E%3Ccircle cx='38' cy='29' r='2' fill='%23176b4d'/%3E%3C/svg%3E"; $("flashcard").hidden = true; $("reviewComplete").hidden = false; $("reviewProgress").textContent = reviewStats.total ? "已完成" : "今日 0 张"; $("memoryActions").hidden = true; $("answerActions").hidden = true; $("reviewHint").hidden = true; // 这个分支同时服务"进来就没卡"和"复习完最后一张"，靠 reviewStats.total 区分：两者都显示「🎉 今日复习任务完成」的话，新账号一进来看到的就是假的庆祝。
  $("reviewCompleteTitle").hidden = !reviewStats.total; $("reviewCompleteText").textContent = reviewStats.total ? `本次复习 ${reviewStats.total} 张卡片，其中一次就想起 ${reviewStats.mastered} 张、模糊或遗忘 ${reviewStats.difficult.size} 张。` : reviewTestEmptyMessage(); $("pointsChip").textContent = `累计能量 ${getPoints()}`; updateReviewCounts(); return; } $("flashcard").hidden = false; $("reviewComplete").hidden = true; $("reviewHint").hidden = false; $("flashFront").textContent = card.front; $("flashBack").textContent = card.back; [["flashFrontImage", card.frontImageUrl], ["flashBackImage", card.backImageUrl]].forEach(([id, url]) => { $(id).src = url || ""; $(id).hidden = !url; }); $("reviewProgress").textContent = `${reviewStats.total - reviewQueue.length + 1} / ${reviewStats.total}`; }
async function applyReviewSchedule(cardRef, quality) {
  const book = getBooks().find((item) => item.id === cardRef.bookId);
  const card = book?.cards.find((item) => item.id === cardRef.id);
  // 本地书架里查不到也要往下走 —— 卡片可能只存在于服务端（本地那份书架的写入失败过），
  // 那种情况下**服务端照样得推进它的排期**，不能在这里静默 return 把整次复习吞掉。
  if (card) { scheduleCardLocally(card, quality); saveBook(book); }
  try {
    const response = await authFetch(apiUrl(`/api/cards/${encodeURIComponent(cardRef.id)}/review`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ quality }) });
    if (response.ok) {
      const data = await response.json();
      // **排期以服务端返回的为准**。本地那份是「复习时刻 + 间隔」自己算的，服务端算的是同一个东西，
      // 但只有它写进了 cards.json —— 只信本地就会「刚复习完的卡还挂在今天」。
      if (card) { Object.assign(card, data.card || {}); saveBook(book); }
      const known = scheduleCards.find((item) => item.id === cardRef.id);
      if (known) Object.assign(known, data.card || {});
      Object.assign(cardRef, data.card || {});
    }
  } catch { /* 离线：本地那份排期先顶着，下次开学习日志会用服务端那份覆盖回来 */ }
  await checkDueCards();
  // 学习日志页开着的话，立刻按服务端最新排期重画 —— 刚复习完的那张必须当场从「1天」挪到「2天」，
  // 不能等用户自己退出去再进来。
  if ($("schedulePage") && !$("schedulePage").hidden) { await refreshScheduleCards(); renderSchedule(scheduleFilter); }
}
async function slideToNext(quality) { const current = reviewQueue[reviewPosition]; if (!current) return; $("flashcard").classList.add("leaving"); await applyReviewSchedule(current, quality); if (quality === "掌握") { addPoints(10, "复习掌握"); reviewStats.mastered += 1; } else reviewStats.difficult.add(current.id); reviewQueue.splice(reviewPosition, 1); if (quality === "忘记了") reviewQueue.push(current); saveReviewSession(); reviewPosition = 0; window.setTimeout(updateReviewCard, 240); }
function finishReviewAnswer(action) { if (!reviewQueue[reviewPosition]) return; const quality = action === "wrong" || pendingMemoryChoice === "forgot" ? "忘记了" : pendingMemoryChoice === "vague" ? "模糊" : "掌握"; slideToNext(quality); }
function renderReviewLibrary(filter) { const cards = allReviewCards().filter((card) => filter === "all" || card.status === filter); const list = $("reviewCardList"); list.replaceChildren();
  // 列表为空时以前是整片空白：「未掌握」那个 tab 走的是复习测试、有自己的「今天没有需要复习的
  // 卡片啦！」，而「已掌握」「全部」直接落在这个列表上，什么提示都没有。三个 tab 各给一句，
  // 措辞跟未掌握那条对齐（「未掌握」这里只在编辑模式下会显示列表，所以也要有）。
  if (!cards.length) { const empty = document.createElement("p"); empty.className = "empty-state"; empty.textContent = filter === "mastered" ? "还没有已掌握哦 —— 复习过的卡片当天就算已掌握，到期后会自动变回未掌握" : filter === "unmastered" ? "没有待复习的卡片哦" : "还没有卡片哦"; list.append(empty); return; }
  cards.forEach((card) => { const item = document.createElement("article"); item.className = `review-list-card${reviewManageMode ? " manage" : ""}${selectedReviewIds.has(card.id) ? " selected" : ""}`; const title = document.createElement("strong"); title.textContent = card.front; const status = document.createElement("span"); status.className = `mini-card-status ${card.status}`; status.textContent = card.status === "mastered" ? "已掌握" : "待复习"; const body = document.createElement("p"); body.textContent = card.back; if (reviewManageMode) { const check = document.createElement("span"); check.className = "review-check"; check.textContent = "✓"; item.append(check); item.addEventListener("click", () => { if (selectedReviewIds.has(card.id)) selectedReviewIds.delete(card.id); else selectedReviewIds.add(card.id); renderReviewLibrary(filter); }); } else { const more = document.createElement("button"); more.type = "button"; more.className = "review-more"; more.textContent = "⋮"; more.addEventListener("click", () => openReviewCardAction(card)); item.append(more); } item.append(title, status, body); list.append(item); }); }
function setReviewManageMode(enabled) { reviewManageMode = enabled; selectedReviewIds.clear(); $("reviewManageToolbar").hidden = !enabled; $("reviewEdit").textContent = enabled ? "完成" : "编辑"; $("reviewUnmasterSelected").hidden = reviewFilter !== "mastered"; if (reviewFilter === "unmastered") { $("reviewTest").hidden = enabled; $("reviewLibrary").hidden = !enabled; } renderReviewLibrary(reviewFilter); }
function openReviewCardAction(card) { activeReviewCard = card; $("reviewActionTitle").textContent = card.front; $("toggleReviewCardStatus").textContent = card.status === "mastered" ? "移出已掌握" : "标记为已掌握"; $("sheetBackdrop").hidden = false; $("reviewCardActionSheet").hidden = false; document.body.classList.add("sheet-open"); }

function noteFolderId(note) { return note.folderId ?? note.categoryId ?? null; }
// 笔记卡片的两块主内容：缩略图 + 标题/类型/日期。笔记页与笔记复盘的选笔记列表共用一份，
// 两处观感才不会走偏（复盘那个列表以前是自己拼的一行文字，跟笔记页完全不像）。
function noteCardParts(note) {
  const attachments = (note.attachments || []).filter((file) => file.noteId === note.id);
  const imageFile = attachments.find((file) => file.mimeType?.startsWith("image/"));
  const pdfFile = attachments.find((file) => file.mimeType === "application/pdf");
  const thumbnail = document.createElement("span"); thumbnail.className = `note-thumbnail${pdfFile ? " pdf" : ""}`;
  const previewUrl = note.coverUrl || imageFile?.url;
  if (previewUrl) { const image = document.createElement("img"); image.src = previewUrl; image.alt = ""; image.onerror = () => { thumbnail.replaceChildren(); thumbnail.classList.add("template-thumbnail", ...templateClasses(note.template).split(" ")); }; thumbnail.append(image); }
  else if (pdfFile) thumbnail.textContent = "PDF";
  else thumbnail.classList.add("template-thumbnail", ...templateClasses(note.template).split(" "));
  const copy = document.createElement("span"); copy.className = "note-copy";
  const title = document.createElement("h3"); title.textContent = note.title;
  const type = document.createElement("small"); type.className = "note-type";
  type.textContent = pdfFile ? `📄 ${pdfFile.name} · ${formatFileSize(pdfFile.size)}` : imageFile ? `🖼 ${imageFile.name} · ${formatFileSize(imageFile.size)}` : "文本笔记";
  const time = document.createElement("time"); time.textContent = new Date(note.date).toLocaleDateString("zh-CN");
  copy.append(title, type, time);
  const state = attachmentState(attachments); if (state) copy.append(syncBadge(state));
  return { thumbnail, copy };
}
function renderNotes(query = "") {
  const normalized = query.trim().toLowerCase(), list = $("noteList"), categories = getCategories(), activeFolder = categories.find((item) => item.id === currentCategory); list.replaceChildren();
  const allNotes = getNotes(); const notes = allNotes.filter((note) => { const folderMatch = currentCategory === "all" ? (normalized ? true : !noteFolderId(note)) : noteFolderId(note) === currentCategory; return folderMatch && (String(note.title || "").includes(query.trim()) || String(note.content || "").includes(query.trim()) || !normalized); });
  $("notesPageTitle").textContent = currentCategory === "all" ? "全部笔记" : activeFolder?.name || "文件夹"; $("folderBreadcrumb").hidden = currentCategory === "all"; $("folderBreadcrumbName").textContent = activeFolder?.name || "";
  if (currentCategory === "all" && !normalized && !batchMode) categories.forEach((folder) => { const card = document.createElement("article"); card.className = "folder-card"; const open = document.createElement("button"); open.type = "button"; open.className = "folder-open"; const cover = document.createElement("span"); cover.className = "folder-cover"; if (folder.coverUrl) { const image = document.createElement("img"); image.src = folder.coverUrl; image.alt = ""; cover.append(attachImageFallback(image, cover, "▱")); } else cover.textContent = "▱"; const title = document.createElement("strong"); title.textContent = folder.name; const count = document.createElement("small"); count.textContent = `${allNotes.filter((note) => noteFolderId(note) === folder.id).length} 篇笔记`; open.append(cover, title, count); open.addEventListener("click", () => { currentCategory = folder.id; renderCategories(); renderNotes($("noteSearch").value); }); const more = document.createElement("button"); more.type = "button"; more.className = "folder-more"; more.textContent = "⋮"; more.setAttribute("aria-label", `管理${folder.name}`); more.addEventListener("click", () => openFolderActionSheet(folder.id)); card.append(open, more); list.append(card); });
  if (!batchMode) { const create = document.createElement("button"); create.type = "button"; create.className = "new-note-card"; create.innerHTML = "<b>＋</b><span>新建与导入</span>"; create.addEventListener("click", openNoteCreateSheet); // 「新建与导入」永远排在左上角第一个，所以用 prepend 而不是 append
    list.prepend(create); }
  notes.forEach((note) => { const item = document.createElement("article"); item.className = `note-card${batchMode ? " batch-mode" : ""}${selectedNoteIds.has(note.id) ? " selected" : ""}`; item.tabIndex = 0; const { thumbnail, copy } = noteCardParts(note); const star = document.createElement("button"); star.type = "button"; star.className = `note-star${note.starred ? " active" : ""}`; star.textContent = note.starred ? "★" : "☆"; star.addEventListener("click", (event) => { event.stopPropagation(); note.starred = !note.starred; saveNote(note); renderNotes($("noteSearch").value); }); const more = document.createElement("button"); more.type = "button"; more.className = "note-more"; more.textContent = "⋮"; more.addEventListener("click", (event) => { event.stopPropagation(); openNoteActionSheet(note.id); }); if (batchMode) { const select = document.createElement("span"); select.className = "note-select"; select.textContent = "✓"; item.append(select); } item.append(thumbnail, copy, star, more); item.addEventListener("click", () => { if (batchMode) { if (selectedNoteIds.has(note.id)) selectedNoteIds.delete(note.id); else selectedNoteIds.add(note.id); renderNotes($("noteSearch").value); } else openNoteSheet(note.id); }); list.append(item); });
  if (normalized && !notes.length) { const empty = document.createElement("div"); empty.className = "note-empty"; empty.textContent = "没有找到相关笔记"; list.append(empty); }
  // 一篇都没有 —— 和"搜不到"是两回事，文案得分开。用 .note-empty（自带 grid-column:1/-1），
  // 否则这句话只会占两列网格里的左边一列。
  if (!notes.length && !normalized) { const empty = document.createElement("div"); empty.className = "note-empty"; empty.textContent = currentCategory === "all" ? "还没有笔记，点「＋ 新建」写下第一条" : "这个文件夹还是空的"; list.append(empty); }
}

// 等级表是五级，门槛与 getLevelInfo() 里那份一致 —— 改门槛时两处要一起改。
// （这里原本还压着一份四级的旧版（LV.4 知识建构者）：同名函数声明了两次，后一份覆盖
// 前一份，那份永远不会执行，已删除。屏幕上显示的本来就是下面这一份，外观无变化。）
function renderRules() { const levels = [["LV.1 学术萌新","0–99","建立习惯"],["LV.2 知识学徒","100–299","积累知识"],["LV.3 科研助手","300–499","辅助研究"],["LV.4 探索达人","500–999","跨域探索"],["LV.5 刘看山首席研究员","1000+","持续创造"]]; const actions = [["每日登录","+3","揉揉眼睛醒来，获得今日口粮"],["读懂新概念","+10","头顶冒出小灯泡"],["深入追问（达3次）","+5","戴上小眼镜陪你钻研"],["发现跨学科同源","+15","拿到放大镜，找到逻辑宝藏"],["存为知识卡片","+5","把知识果实放进小背包"],["复习考核掌握","+10","开心转圈圈，播撒星星"],["复习考核遗忘/模糊","+2","拍拍你，鼓励“没关系，再来一次”"],["新建笔记","+10","在纸上画下你的思考轨迹"],["整理书架/新建书籍","+5","整理书架，成就感满满"]]; const fill = (id, rows) => { const box = $(id); box.replaceChildren(); rows.forEach(([name, energy, note]) => { const row = document.createElement("div"); row.className = "table-row"; const strong = document.createElement("strong"); strong.textContent = name; const value = document.createElement("span"); value.className = "energy"; value.textContent = energy; const text = document.createElement("p"); text.textContent = note; row.append(strong, value, text); box.append(row); }); }; fill("levelTable", levels); fill("pointsTable", actions); }
function renderProfile() { const user = getUser(), points = getPoints(), level = getLevelInfo(points), cards = allReviewCards(), first = localStorage.getItem(storageKeys.firstLogin) || new Date().toISOString(); if (!localStorage.getItem(storageKeys.firstLogin)) localStorage.setItem(storageKeys.firstLogin, first); $("profileNickname").textContent = user.nickname; $("profileSignature").textContent = user.signature; $("profileAvatar").src = user.avatarUrl || ""; $("profileAvatar").hidden = !user.avatarUrl; $("avatarFallback").hidden = Boolean(user.avatarUrl); $("levelLabel").textContent = `${level.level} · ${level.title}`; $("rulesLevel").textContent = `${level.level} · ${level.title}`; $("profilePoints").textContent = points; $("rulesPoints").textContent = points; $("levelProgress").style.width = `${level.progressPercent}%`; $("rulesProgress").style.width = `${level.progressPercent}%`; $("levelRemaining").textContent = level.nextMax === null ? "已达到最高等级" : `距离下一级还差 ${level.nextMax - points} 能量`; $("recordMastered").textContent = cards.filter((card) => card.status === "mastered").length; $("recordUnmastered").textContent = cards.filter((card) => card.status === "unmastered").length; $("recordDays").textContent = Math.max(1, Math.floor((Date.now() - new Date(first)) / 86400000) + 1); }
let scheduleCards = [], scheduleFilter = "1";
// —— 复习计划表（这一页唯一的规则，改之前先读完）——
//
// 标签 [1天] [2天] [4天] [7天] [15天] 说的是**第几个复习节点**，不是「今天/明天到期」。
// 每个节点的日期从**建卡那天**往后推：第 1 天 = 建卡日 +1 天、第 2 天 = 建卡日 +2 天、
// 第 4 天 = +4 天、第 7 天 = +7 天、第 15 天 = +15 天。所以：
//
//   9/13 建 apple → [1天] 落在 9/14、[2天] 落在 9/15、[4天] 落在 9/17
//   9/14 建 banana → [1天] 落在 9/15、[2天] 落在 9/16、[4天] 落在 9/18
//
// ⚠️ 这是一张**计划表**，不是「今天该复习什么」的待办。节点日期过了也照样列出来 ——
// 复习过的标「✓ 已完成」，拖过去还没复习的标「已逾期」，**不隐藏**：隐藏了用户就看不到
// 自己错过了哪几个节点。判定「今天要不要复习」的唯一依据是 next_review_due，那是复习页
// 和右上角铃铛的事，跟这一页无关。
//
// 别改回「按 next_review_due 分区」那版（2026-09-13 试过，用户当场否掉）：那样 [2天] 里
// 只会剩下明天到期的卡，而用户要的是「每张卡在未来的第 N 天分别落在哪一天」这张时间表。
const SCHEDULE_PLAN_DAYS = [1, 2, 4, 7, 15];
// 日期只取「日」——建卡那一刻的时分秒丢掉，一律用本地零点当锚点，否则「+N 天」会被时分带偏一天。
function scheduleDayKey(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "unknown" : new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime(); }
// 推算的锚点：建卡时间。老卡片可能没有 created_at（服务端 normalize_card 不补这个字段），
// 依次往后找 —— 建卡时间 → 上次复习时间 → 下次到期时间 → 现在。有个真锚点就比按今天算强。
function scheduleAnchor(card) {
  for (const value of [card?.created_at, card?.createdAt, card?.created, card?.last_reviewed_at, card?.next_review_due]) {
    // ⚠️ 必须写成 `value ? Date.parse(value) : NaN`。写成 `Date.parse(value || 0)` 会把
    // null / "" 变成 Date.parse(0) = 0，而 0 **是**有限数 —— 于是一张 last_reviewed_at 为
    // null 的卡会被当成 1970 年建的，整张计划表飘到半个世纪以前。
    const at = value ? Date.parse(value) : NaN;
    if (Number.isFinite(at) && at > 0) return at;
  }
  return Date.now();
}
// 第 days 天那个节点落在哪一天（本地中午的时间戳，同一天怎么算都是同一天）。
function schedulePlanDate(card, days) { const base = new Date(scheduleAnchor(card)); return new Date(base.getFullYear(), base.getMonth(), base.getDate() + days, 12).getTime(); }
// 分组标题就是那个日期本身。用户明确要求按计划日期分组，别再写「今天/明天」——
// 这是一张未来时间表，「明天」这种相对说法在这一页没有意义。
function scheduleLabel(key) { if (key === "unknown") return "待安排"; const date = new Date(Number(key)); return `${date.getMonth() + 1}月${date.getDate()}日`; }
function formatRecentReview(value) { if (!value) return "最近复习：从未复习"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "最近复习：从未复习" : `最近复习：${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`; }
// 这一条节点的状态。review_stage 是「已经复习过几次」，正好等于「前几个节点已经走完」——
// 复习过 3 次就是第 1/2/4 天那三个节点都完成了。所以拿它跟节点序号比。
// 日期过了却还没复习 = 逾期，如实标出来（计划表的价值就在这里）。
function scheduleEntryState(entry) {
  if ((Number(entry.card.review_stage) || 0) > entry.index) return "✓ 已完成";
  return entry.at < scheduleDayKey(Date.now()) ? "已逾期" : "待复习";
}
function scheduleEntry(entry) {
  const item = document.createElement("button"); item.type = "button"; item.className = "schedule-item";
  const title = document.createElement("strong"); title.textContent = entry.card.front || "未命名卡片";
  const meta = document.createElement("small"); meta.textContent = `${entry.card.bookName || "知识卡片"} · 第${entry.days}天复习`;
  const recent = document.createElement("small"); recent.className = "schedule-recent"; recent.textContent = formatRecentReview(entry.card.last_reviewed_at);
  const tag = document.createElement("span"); tag.textContent = scheduleEntryState(entry);
  item.append(title, meta, recent, tag);
  // 点条目就去复习它。别自己拼 reviewQueue —— activateAppPage 内部会 switchReviewFilter("unmastered")，
  // 那一步 buildReviewQueue() 会把队列按「今天到期的」整个重建，手工塞的那一条立刻被覆盖掉。
  item.addEventListener("click", () => { closeSchedulePage(); activateAppPage("reviewPage"); });
  return item;
}
// 一个计划日期一组。**默认收起**，只露一行「9月15日（共 2 张）」，点标题（或回车/空格）
// 才展开当天的卡片 —— 全部展开就是一整条长图，几十条翻不到头。
// 卡片装在 .schedule-group-body 里（一个没有任何样式的 div）：条目之间靠 margin-top 撑开，
// 包一层不改外观，只是收起时能整块 hidden。
function scheduleGroup(day, entries) {
  const group = document.createElement("section"); group.className = "schedule-group";
  const heading = document.createElement("h3"); group.append(heading);
  const body = document.createElement("div"); body.className = "schedule-group-body";
  entries.forEach((entry) => body.append(scheduleEntry(entry)));
  body.hidden = true;
  const paint = () => { heading.textContent = `${body.hidden ? "▸ " : "▾ "}${scheduleLabel(day)}（共 ${entries.length} 张）`; };
  paint(); group.append(body);
  heading.setAttribute("role", "button"); heading.setAttribute("tabindex", "0"); heading.setAttribute("aria-expanded", String(!body.hidden));
  const toggle = () => { body.hidden = !body.hidden; heading.setAttribute("aria-expanded", String(!body.hidden)); paint(); };
  heading.addEventListener("click", toggle);
  heading.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } });
  return group;
}
// 按计划日期分组渲染。**先按节点挑条目、再按日期分组** —— 顺序反了就会又变成「每组都渲染全部日期」。
// 分出来的实体是「卡片 × 节点」这一对，不是卡片本身：同一张卡在 [1天] 和 [2天] 各有一条，
// 那是两个不同的日子，本来就该各出现一次。
function renderSchedule(filter = scheduleFilter) {
  const list = $("scheduleList"); if (!list) return;
  scheduleFilter = filter;
  document.querySelectorAll("[data-schedule-filter]").forEach((button) => button.classList.toggle("active", button.dataset.scheduleFilter === filter));
  const showAll = filter === "all";
  const entries = [];
  scheduleCards.forEach((card) => SCHEDULE_PLAN_DAYS.forEach((days, index) => {
    if (showAll || String(days) === filter) entries.push({ card, days, index, at: schedulePlanDate(card, days) });
  }));
  console.log("复习计划", { 节点: filter, 条目: entries.length });
  list.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("p"); empty.className = "empty-state";
    empty.textContent = showAll ? "还没有卡片哦 —— 去「卡片」里新建，或从一次同源发现里把结果存成卡片吧！" : `还没有卡片。新建一张卡，它就会自动排上第 ${SCHEDULE_PLAN_DAYS.join(" / ")} 天的复习计划`;
    list.append(empty); return;
  }
  const groups = new Map();
  entries.forEach((entry) => { const day = scheduleDayKey(entry.at); if (!groups.has(day)) groups.set(day, []); groups.get(day).push(entry); });
  [...groups.entries()].sort((a, b) => a[0] - b[0]).forEach(([day, items]) => list.append(scheduleGroup(day, items)));
}
// 计划表的数据源。**服务端是卡片内容的权威**：进页面、复习完都重新拉一次，拿它上面的
// created_at（推算基准）和 review_stage（哪些节点已完成）；本地那份只在请求失败时兜底。
async function refreshScheduleCards() {
  const local = allReviewCards();
  const byId = new Map(local.map((card) => [card.id, card]));
  scheduleCards = local;
  try {
    const response = await authFetch(apiUrl("/api/cards/schedule"), { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    if (!Array.isArray(data.cards)) return;
    const seen = new Set();
    const remote = data.cards.map((card) => {
      seen.add(card.id); const mine = byId.get(card.id);
      // created_at 是推算基准，服务端不补这个字段（见 card_store.normalize_card），
      // 所以缺了就从本地那份补回来 —— 补不上还有 scheduleAnchor 里的兜底链。
      return { ...card, created_at: card.created_at || card.createdAt || mine?.created_at, bookId: mine?.bookId, bookName: mine?.bookName || "知识卡片" };
    });
    // 后端没存上的卡（建卡那次 /api/cards/save 失败的）不能在计划表里凭空消失。
    scheduleCards = [...remote, ...local.filter((card) => !seen.has(card.id) && card.localOnly)];
  } catch { /* 离线：保留本地那份，页面还能看 */ }
}
async function openSchedulePage() {
  document.querySelectorAll(".fullscreen-page").forEach((page) => { page.hidden = page.id !== "schedulePage"; });
  document.body.classList.add("schedule-open"); $("schedulePage").hidden = false;
  scheduleFilter = "1";
  renderSchedule(scheduleFilter);          // 先用本地快照铺一下，避免白屏
  await refreshScheduleCards();            // 再用服务端那份覆盖
  if (!$("schedulePage").hidden) renderSchedule(scheduleFilter);
}
function closeSchedulePage() { $("schedulePage").hidden = true; document.body.classList.remove("schedule-open"); }
// 这里原本还有一个 renderScheduleFuture()，它当年**用 `renderSchedule = renderScheduleFuture`
// 把上面那个真实现整个覆盖掉**，于是页面上永远只有它那一份。它算日期的思路（建卡日 +1/2/4/7/15）
// 恰恰就是现在这一版要的东西，之所以删掉又加回来，是因为中间有过一版改成「按 next_review_due
// 分区」——用户 2026-09-13 明确否掉了那一版，要的就是这张按建卡日推算的计划表。
//
// 教训不是「别做投影」，而是**别用覆盖赋值伪造第二份实现**：要么改 renderSchedule()，要么
// 什么都别做。现在全页只有上面 renderSchedule() 一个入口，两条实现并存的坑不会再出现。



document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => activatePanel(tab.dataset.panel)));
$("profileToggle").addEventListener("click", () => { const profile = $("logicProfile"); profile.hidden = !profile.hidden; $("profileToggle").setAttribute("aria-expanded", String(!profile.hidden)); $("profileToggle").textContent = profile.hidden ? "查看逻辑画像 ›" : "收起逻辑画像⌄"; });
document.querySelectorAll(".example-prompt").forEach((button) => button.addEventListener("click", () => {
  const panelId = button.dataset.target === "discoverText" ? "discoverPanel" : "explainPanel";
  setDraftForPanel(panelId, button.textContent.trim()); $(button.dataset.target).focus();
}));
$("explainText").addEventListener("input", (event) => setDraftForPanel("explainPanel", event.target.value));
$("discoverText").addEventListener("input", (event) => setDraftForPanel("discoverPanel", event.target.value));
$("manualImportText").addEventListener("input", (event) => setDraftForPanel(state.importPanelTarget, event.target.value));

$("explainToDiscover").addEventListener("click", () => {
  state.activeKnowledgePanel = "discoverPanel"; state.importPanelTarget = "discoverPanel"; state.importTarget = "discoverText";
  document.querySelectorAll(".tab").forEach((item) => { const active = item.dataset.panel === "discoverPanel"; item.classList.toggle("active", active); item.setAttribute("aria-selected", String(active)); });
  revealKnowledgeEditor(); $("discoverText").focus();
});
$("backToHome").addEventListener("click", () => {
  $("explainResult").hidden = true; document.querySelector("#explainPanel > .input-area").hidden = false; clearDraftForPanel("explainPanel"); showKnowledgeLauncher("explainPanel");
});
 $("historyBackButton").addEventListener("click", () => { const panel = state.activeKnowledgePanel; if (panel === "discoverPanel") { $("discoverResult").hidden = true; document.querySelector("#discoverPanel > .input-area").hidden = false; } else { $("explainResult").hidden = true; document.querySelector("#explainPanel > .input-area").hidden = false; } clearDraftForPanel(panel); showKnowledgeLauncher(panel); });
$("discoverBackToLauncher").addEventListener("click", () => {
  $("discoverResult").hidden = true;
  document.querySelector("#discoverPanel > .input-area").hidden = false;
  clearDraftForPanel("discoverPanel");
  showKnowledgeLauncher("discoverPanel");
});
$("mappingSheetClose").addEventListener("click", closeMappingSheet);
$("saveCardButton").addEventListener("click", openSaveCardSheet);
$("saveCardSheetClose").addEventListener("click", closeSaveCardSheet);
$("cancelSaveCard").addEventListener("click", closeSaveCardSheet);
$("confirmSaveCard").addEventListener("click", saveKnowledgeCard);
// 下拉里选「＋ 新建书本…」：先把 value 拨回原来那本（哨兵值不能留在 select 上，理由见
// NEW_BOOK_OPTION 的注释），再转去建书 —— 与「一本书都没有」那条路完全同一套。
$("shelfSelect").addEventListener("change", () => {
  if ($("shelfSelect").value !== NEW_BOOK_OPTION) { lastShelfBookId = $("shelfSelect").value; return; }
  $("shelfSelect").value = lastShelfBookId;
  closeSaveCardSheet();          // 先收掉存卡弹层，别让它留在建书弹层底下（两层同为 z-index 31）
  startNewBookForSaveCard();
});
$("notificationButton").addEventListener("click", () => { const count = currentDueNotificationCount; showToast(count > 0 ? `您还有 ${count} 个待复习哦～` : "今日复习已完成！真棒！"); });
$("settingsButton").addEventListener("click", openSettingsSheet);
$("settingsSheetClose").addEventListener("click", closeSettingsSheet);
$("accountSettingsButton").addEventListener("click", () => { closeSettingsSheet(); openProfileSheet(); });
$("aboutKnowledgeButton").addEventListener("click", openAboutSheet);
$("toggleZhihuSelectAll")?.addEventListener("click", toggleZhihuSelectAll);
$("zhihuLibraryCard")?.addEventListener("click", openZhihuLibrary);
$("zhihuLibraryCard")?.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openZhihuLibrary(); } });
$("zhihuConsentClose")?.addEventListener("click", closeZhihuConsent);
$("zhihuConsentCancel")?.addEventListener("click", closeZhihuConsent);
$("zhihuConsentConfirm")?.addEventListener("click", confirmZhihuConsent);
$("connectZhihuButton")?.addEventListener("click", async () => {
  try { const data = await request("/api/zhihu/oauth/authorize", null, { method: "GET" }); if (data.url) window.open(data.url, "_blank", "noopener"); }
  catch (error) { showToast(error.message || "知乎 OAuth 尚未配置，请联系管理员"); }
});
$("aboutSheetClose").addEventListener("click", closeAboutSheet);
$("brandLogo")?.addEventListener("error", (event) => { event.currentTarget.hidden = true; });
document.addEventListener("click", (event) => { if (!$("toast").hidden && !$("notificationButton").contains(event.target) && !$("toast").contains(event.target)) { $("toast").hidden = true; window.clearTimeout(toastTimer); } });
$("historyClose").addEventListener("click", closeHistory);
$("closeHistoryDelete").addEventListener("click", () => { $("historyDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; });
$("cancelHistoryDelete").addEventListener("click", () => { $("historyDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; });
$("confirmHistoryDelete").addEventListener("click", () => { if (pendingHistoryDeleteId) saveHistory(getHistory().filter((entry) => entry.id !== pendingHistoryDeleteId)); pendingHistoryDeleteId = null; $("historyDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; renderHistory(); renderHistoryMemory(); showToast("历史记录已删除"); });
document.querySelectorAll(".history-filter").forEach((button) => button.addEventListener("click", () => { historyFilterType = button.dataset.historyType; renderHistory(); }));
$("clearHistory").addEventListener("click", () => { saveHistory(getHistory().filter((item) => item.type !== historyFilterType)); renderHistory(); renderHistoryMemory(); showToast(`${historyFilterType}历史记录已清空`); });
$("historyMemoryMore").addEventListener("click", openHistory);
$("clearCurrentHistory").addEventListener("click", () => { const type = historyFilterType; saveHistory(getHistory().filter((item) => item.type !== type)); renderHistory(); renderHistoryMemory(); showToast("当前模式历史已清空"); });
$("backToTop").addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
window.addEventListener("scroll", () => $("backToTop").classList.toggle("visible", window.scrollY > 300), { passive: true });
$("sheetBackdrop").addEventListener("click", () => { closeMappingSheet(); closeSaveCardSheet(); closeNoteSheet(); closeCardSheet(); closeImportSheet(); closeBookSheet(); closeProfileSheet(); closeSettingsSheet(); closeAboutSheet(); closeNoteActionSheet(); closeFolderActionSheet(); closeTemplateSheet(); closeNoteCreateSheet(); closeFolderSheet(); $("noteMoveSheet").hidden = true; $("batchMoveSheet").hidden = true; $("batchDeleteConfirm").hidden = true; $("cardDeleteConfirm").hidden = true; $("historyDeleteConfirm").hidden = true; $("reviewCardActionSheet").hidden = true; $("reviewDeleteConfirm").hidden = true; });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeMappingSheet(); closeSaveCardSheet(); } });
document.querySelectorAll(".bottom-tab").forEach((tab) => tab.addEventListener("click", () => activateAppPage(tab.dataset.appPage)));

$("importContentButton").addEventListener("click", () => openImportSheet("importSheet"));
$("discoverImportContentButton").addEventListener("click", () => openImportSheet("importSheet"));
document.querySelectorAll("[data-launch-import]").forEach((button) => button.addEventListener("click", () => {
  state.importPanelTarget = state.activeKnowledgePanel;
  state.importTarget = state.importPanelTarget === "discoverPanel" ? "discoverText" : "explainText";
  openImportSheet("importSheet");
  const panels = { manual: "manualImportPanel", image: "imageImportPanel", zhihu: "zhihuSearchPanel" };
  showImportPanel(panels[button.dataset.launchImport]);
  if (button.dataset.launchImport === "image") $("imageImportFile").click();
}));
document.querySelectorAll(".back-to-launcher").forEach((button) => button.addEventListener("click", () => showKnowledgeLauncher(state.activeKnowledgePanel)));
$("importSheetClose").addEventListener("click", closeImportSheet);
document.querySelectorAll("[data-import-panel]").forEach((button) => button.addEventListener("click", () => showImportPanel(button.dataset.importPanel)));
document.querySelectorAll(".import-back").forEach((button) => button.addEventListener("click", () => showImportPanel(null)));
$("confirmManualImport").addEventListener("click", () => { const text = $("manualImportText").value.trim(); if (!text) return; placeImportedText(text, "内容已放入输入框", state.importPanelTarget); });
$("confirmZhihuSearch").addEventListener("click", searchZhihu);
$("zhihuSearchQuery").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); searchZhihu(); } });
document.querySelectorAll("[data-zhihu-mode]").forEach((button) => button.addEventListener("click", () => {
  state.zhihuMode = button.dataset.zhihuMode;
  document.querySelectorAll("[data-zhihu-mode]").forEach((item) => { const active = item === button; item.classList.toggle("active", active); item.setAttribute("aria-selected", String(active)); });
  state.zhihuResearchItems = []; $("zhihuSearchResults").replaceChildren(); $("zhihuResearchToolbar").hidden = true;
  $("zhihuSearchFeedback").textContent = state.zhihuMode === "research" ? "输入一个主题，我会从核心概念、机制原理和争议边界三个角度搜索。" : "例如：负反馈为什么能让系统稳定？结果来自知乎开放平台。";
  $("confirmZhihuSearch").textContent = state.zhihuMode === "research" ? "研究" : "搜索";
}));
$("useZhihuResearch").addEventListener("click", useZhihuResearch);
$("cancelLoading").addEventListener("click", cancelDiscover);
$("imageImportFile").addEventListener("change", (event) => importImage(event.target.files?.[0]));

$("backToShelf").addEventListener("click", () => { $("bookDetail").hidden = true; $("bookList").hidden = false; $("newBookButton").hidden = false; });
$("newBookButton").addEventListener("click", () => openBookSheet());
$("bookSheetClose").addEventListener("click", closeBookSheet); $("cancelBook").addEventListener("click", closeBookSheet);
$("bookCoverFile").addEventListener("change", async (event) => { pendingBookCoverFile = event.target.files?.[0] || null; if (!pendingBookCoverFile) return; pendingBookCoverUrl = await fileDataUrl(pendingBookCoverFile); $("bookCoverPreview").innerHTML = `<img alt="封面预览">`; $("bookCoverPreview").querySelector("img").src = pendingBookCoverUrl; });
$("saveBookButton").addEventListener("click", async () => {
  const name = $("bookName").value.trim(); if (!name) return;
  const existing = getBooks().find((item) => item.id === editingBookId);
  // 先把标记位取走再往下走：closeBookSheet() 会把它清掉（那是给"取消"用的）。
  const thenSaveCard = pendingSaveCardAfterNewBook; pendingSaveCardAfterNewBook = false;
  let coverUrl = existing?.coverUrl || "", localCoverDataUrl = existing?.localCoverDataUrl || "";
  if (pendingBookCoverFile) { try { coverUrl = await uploadFile(pendingBookCoverFile, "book_cover"); localCoverDataUrl = ""; } catch { localCoverDataUrl = pendingBookCoverUrl; showToast("封面上传失败，已保存在当前设备"); } }
  const book = { id: editingBookId || makeId("book"), name, icon: existing?.icon || "▤", coverUrl, localCoverDataUrl, cards: existing?.cards || [] };
  saveBook(book); renderBooks(); closeBookSheet();
  // 从「存为卡片」过来的：书架刚建好，接着把那张卡存进去，不用用户再点一次。
  // 走的是同一个 saveKnowledgeCard()，不复制一份存卡逻辑；它按 shelfSelect 选中的书架存，
  // 所以这里要先把新书塞进下拉并选中它。lastShelfBookId 一起跟上 —— 它是「下拉当前指向
  // 那本真书」的副本，这里绕过 change 监听直接改 value，不手动同步就会留个旧值。
  if (thenSaveCard) { renderShelfOptions(); $("shelfSelect").value = book.id; lastShelfBookId = book.id; saveKnowledgeCard(); }
});
$("newCardButton").addEventListener("click", () => openCardSheet());
$("cardSheetClose").addEventListener("click", closeCardSheet);
$("cancelCard").addEventListener("click", closeCardSheet);
$("saveCard").addEventListener("click", async () => { const front = $("cardFront").value.trim(), back = $("cardBack").value.trim(); if (!front || !back) return; const book = getBooks().find((item) => item.id === currentBookId); if (!book) return; const index = book.cards.findIndex((item) => item.id === editingCardId), existing = index >= 0 ? book.cards[index] : null; const card = { ...(existing || newReviewFields()), id: editingCardId || makeId("card"), front, back, frontImageUrl: pendingCardFrontImageUrl, backImageUrl: pendingCardBackImageUrl }; if (index >= 0) book.cards[index] = card; else book.cards.push(card); saveBook(book); try { const response = await authFetch(apiUrl("/api/cards/save"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(card) }); if (response.ok) Object.assign(card, (await response.json()).card || {}); } catch { card.localOnly = true; } saveBook(book); checkDueCards(); openBook(book.id); closeCardSheet(); });
async function setCardImage(side, file) { if (!file) return; let url = await fileDataUrl(file); if (side === "front") pendingCardFrontImageUrl = url; else pendingCardBackImageUrl = url; renderCardImagePreview(side); try { url = await uploadFile(file, `card_${side}_image`); if (side === "front") pendingCardFrontImageUrl = url; else pendingCardBackImageUrl = url; renderCardImagePreview(side); showToast("卡片图片已同步云端"); } catch { showToast("图片已保存在本机卡片中"); } }
$("chooseCardFrontImage").addEventListener("click", () => $("cardFrontImageFile").click()); $("chooseCardBackImage").addEventListener("click", () => $("cardBackImageFile").click());
$("cardFrontImageFile").addEventListener("change", async (event) => { await setCardImage("front", event.target.files?.[0]); event.target.value = ""; }); $("cardBackImageFile").addEventListener("change", async (event) => { await setCardImage("back", event.target.files?.[0]); event.target.value = ""; });
$("removeCardFrontImage").addEventListener("click", () => { pendingCardFrontImageUrl = ""; renderCardImagePreview("front"); }); $("removeCardBackImage").addEventListener("click", () => { pendingCardBackImageUrl = ""; renderCardImagePreview("back"); });
$("deleteCard").addEventListener("click", () => { $("cardSheet").hidden = true; $("cardDeleteConfirm").hidden = false; });
$("closeCardDeleteConfirm").addEventListener("click", () => { $("cardDeleteConfirm").hidden = true; $("cardSheet").hidden = false; });
$("cancelCardDelete").addEventListener("click", () => { $("cardDeleteConfirm").hidden = true; $("cardSheet").hidden = false; });
$("confirmCardDelete").addEventListener("click", () => { const book = getBooks().find((item) => item.id === currentBookId); if (!book) return; book.cards = book.cards.filter((item) => item.id !== editingCardId); saveBook(book); openBook(book.id); $("cardDeleteConfirm").hidden = true; closeCardSheet(); });
$("flashcard").addEventListener("click", () => {});
document.querySelectorAll("[data-memory-action]").forEach((button) => button.addEventListener("click", () => setReviewPhase("answer", button.dataset.memoryAction)));
document.querySelectorAll("[data-answer-action]").forEach((button) => button.addEventListener("click", () => finishReviewAnswer(button.dataset.answerAction)));
document.querySelectorAll(".review-filter").forEach((button) => button.addEventListener("click", () => switchReviewFilter(button.dataset.reviewFilter)));
$("viewMasteredAfterReview").addEventListener("click", () => switchReviewFilter("mastered")); $("returnProfileAfterReview").addEventListener("click", () => activateAppPage("petPage"));
$("reviewEdit").addEventListener("click", () => setReviewManageMode(!reviewManageMode)); $("reviewCancelEdit").addEventListener("click", () => setReviewManageMode(false));
$("reviewSelectAll").addEventListener("click", () => { const cards = allReviewCards().filter((card) => reviewFilter === "all" || card.status === reviewFilter); if (selectedReviewIds.size === cards.length) selectedReviewIds.clear(); else cards.forEach((card) => selectedReviewIds.add(card.id)); renderReviewLibrary(reviewFilter); });
$("reviewUnmasterSelected").addEventListener("click", () => { allReviewCards().filter((card) => selectedReviewIds.has(card.id)).forEach((card) => persistReviewStatus(card, "unmastered")); setReviewManageMode(false); switchReviewFilter("mastered"); });
$("reviewDeleteSelected").addEventListener("click", () => { if (!selectedReviewIds.size) return showToast("请先选择卡片"); $("reviewDeleteText").textContent = `确定删除选中的 ${selectedReviewIds.size} 张卡片吗？删除后无法恢复。`; $("sheetBackdrop").hidden = false; $("reviewDeleteConfirm").hidden = false; });
$("closeReviewDelete").addEventListener("click", () => { $("reviewDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; }); $("cancelReviewDelete").addEventListener("click", () => { $("reviewDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; });
$("confirmReviewDelete").addEventListener("click", () => { removeReviewCards(selectedReviewIds); $("reviewDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; setReviewManageMode(false); switchReviewFilter(reviewFilter); });
$("closeReviewAction").addEventListener("click", () => { $("reviewCardActionSheet").hidden = true; $("sheetBackdrop").hidden = true; });
$("toggleReviewCardStatus").addEventListener("click", () => { if (!activeReviewCard) return; persistReviewStatus(activeReviewCard, activeReviewCard.status === "mastered" ? "unmastered" : "mastered"); $("reviewCardActionSheet").hidden = true; $("sheetBackdrop").hidden = true; switchReviewFilter(reviewFilter); });
$("deleteReviewCard").addEventListener("click", () => { if (!activeReviewCard) return; selectedReviewIds = new Set([activeReviewCard.id]); $("reviewCardActionSheet").hidden = true; $("reviewDeleteText").textContent = "确定删除这张卡片吗？删除后无法恢复。"; $("reviewDeleteConfirm").hidden = false; });
$("noteSearch").addEventListener("input", (event) => { const value = event.target.value; $("clearNoteSearch").hidden = !value; window.clearTimeout(noteSearchTimer); noteSearchTimer = window.setTimeout(() => renderNotes(value), 300); });
$("clearNoteSearch").addEventListener("click", () => { window.clearTimeout(noteSearchTimer); $("noteSearch").value = ""; $("clearNoteSearch").hidden = true; renderNotes(); $("noteSearch").focus(); });
$("categoriesLeft").addEventListener("click", () => $("noteCategories").scrollBy({ left: -180, behavior: "smooth" }));
$("categoriesRight").addEventListener("click", () => $("noteCategories").scrollBy({ left: 180, behavior: "smooth" }));
$("addNoteCategory").addEventListener("click", openFolderSheet);
$("toggleNoteLayout").addEventListener("click", () => { noteLayout = noteLayout === "grid" ? "list" : "grid"; $("notesPage").classList.toggle("list-layout", noteLayout === "list"); $("toggleNoteLayout").textContent = noteLayout === "grid" ? "▦" : "☷"; });
$("focusNoteSearch").addEventListener("click", () => $("noteSearch").focus());
$("toggleBatchMode").addEventListener("click", () => setBatchMode(!batchMode));
$("closeNoteCreate").addEventListener("click", closeNoteCreateSheet);
$("createNoteMenu").addEventListener("click", () => { $("noteCreateSheet").hidden = true; openTemplateSheet(); });
$("createFolderMenu").addEventListener("click", () => { $("noteCreateSheet").hidden = true; openFolderSheet(); });
$("importImageMenu").addEventListener("click", () => { quickImportKind = "image"; $("quickNoteImport").accept = "image/*"; $("quickNoteImport").click(); });
$("importPdfMenu").addEventListener("click", () => { quickImportKind = "pdf"; $("quickNoteImport").accept = "application/pdf"; $("quickNoteImport").click(); });
$("quickNoteImport").addEventListener("change", async (event) => { const file = event.target.files?.[0]; if (!file) return; $("noteCreateSheet").hidden = true; pendingNoteTemplate = defaultNoteTemplate(); openNoteSheet(null, pendingNoteTemplate); await addImportedAttachment(file); $("noteTitle").value = file.name.replace(/\.[^.]+$/, ""); event.target.value = ""; });
$("closeFolderSheet").addEventListener("click", closeFolderSheet); $("cancelFolder").addEventListener("click", closeFolderSheet);
$("saveFolder").addEventListener("click", () => { const name = $("folderName").value.trim(); if (!name) return; const categories = getCategories(); const category = { id: makeId("category"), name, coverUrl: "", syncStatus: "default" }; categories.push(category); saveCategories(categories); currentCategory = category.id; renderCategories(); renderNotes($("noteSearch").value); closeFolderSheet(); addPoints(5, "整理书架/新建书籍"); showToast("文件夹已创建"); });
$("batchSelectAll").addEventListener("click", () => { const visible = getNotes().filter((note) => currentCategory === "all" ? !noteFolderId(note) : noteFolderId(note) === currentCategory); if (selectedNoteIds.size === visible.length) selectedNoteIds.clear(); else visible.forEach((note) => selectedNoteIds.add(note.id)); renderNotes($("noteSearch").value); });
$("batchMove").addEventListener("click", () => { if (!selectedNoteIds.size) return showToast("请先选择笔记"); openBatchMove(); }); $("closeBatchMove").addEventListener("click", () => { $("batchMoveSheet").hidden = true; $("sheetBackdrop").hidden = true; });
$("batchDelete").addEventListener("click", () => { if (!selectedNoteIds.size) return showToast("请先选择笔记"); $("batchDeleteText").textContent = `将删除 ${selectedNoteIds.size} 篇笔记，删除后无法恢复。`; $("sheetBackdrop").hidden = false; $("batchDeleteConfirm").hidden = false; });
$("closeBatchDelete").addEventListener("click", () => { $("batchDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; }); $("cancelBatchDelete").addEventListener("click", () => { $("batchDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; });
$("confirmBatchDelete").addEventListener("click", () => { localStorage.setItem(storageKeys.notes, JSON.stringify(getNotes().filter((note) => !selectedNoteIds.has(note.id)))); $("batchDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; setBatchMode(false); renderProfile(); showToast("所选笔记已删除"); });
$("batchShare").addEventListener("click", async () => { if (!selectedNoteIds.size) return showToast("请先选择笔记"); const text = getNotes().filter((note) => selectedNoteIds.has(note.id)).map((note) => `${note.title}\n${note.content.replace(/<[^>]+>/g, "")}`).join("\n\n"); if (navigator.share) await navigator.share({ title: "Logic-Coloc 笔记", text }).catch(() => {}); else { await navigator.clipboard?.writeText(text); showToast("笔记内容已复制"); } });
$("batchExport").addEventListener("click", () => { if (!selectedNoteIds.size) return showToast("请先选择笔记"); const data = JSON.stringify(getNotes().filter((note) => selectedNoteIds.has(note.id)), null, 2), link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([data], { type: "application/json" })); link.download = "logic-coloc-notes.json"; link.click(); URL.revokeObjectURL(link.href); });
document.querySelectorAll("[data-rich-command]").forEach((button) => button.addEventListener("click", () => { $("noteBody").focus(); document.execCommand(button.dataset.richCommand, false); }));
$("fontName").addEventListener("change", (event) => { $("noteBody").focus(); document.execCommand("fontName", false, event.target.value); }); $("fontSize").addEventListener("change", (event) => { $("noteBody").focus(); document.execCommand("fontSize", false, event.target.value); }); $("foreColor").addEventListener("input", (event) => { $("noteBody").focus(); document.execCommand("foreColor", false, event.target.value); }); $("hiliteColor").addEventListener("input", (event) => { $("noteBody").focus(); document.execCommand("hiliteColor", false, event.target.value); });
$("noteBody").addEventListener("keyup", () => { const selection = window.getSelection(); if (selection?.rangeCount) savedEditorRange = selection.getRangeAt(0).cloneRange(); });
$("noteBody").addEventListener("mouseup", () => { const selection = window.getSelection(); if (selection?.rangeCount) savedEditorRange = selection.getRangeAt(0).cloneRange(); });
$("insertEditorImage").addEventListener("click", () => $("editorImageFile").click());
$("editorImageFile").addEventListener("change", async (event) => { const file = event.target.files?.[0]; if (!file) return; let url = URL.createObjectURL(file), synced = false; try { url = await uploadFile(file, "note_inline_image"); synced = true; } catch { showToast("图片已暂存本地"); } const editor = $("noteBody"), image = document.createElement("img"); image.src = url; image.alt = file.name; editor.focus(); const selection = window.getSelection(); if (savedEditorRange && editor.contains(savedEditorRange.commonAncestorContainer)) { selection.removeAllRanges(); selection.addRange(savedEditorRange); savedEditorRange.deleteContents(); savedEditorRange.insertNode(image); savedEditorRange.setStartAfter(image); savedEditorRange.collapse(true); selection.removeAllRanges(); selection.addRange(savedEditorRange); } else editor.append(image); if (synced) showToast("图片已插入并同步云端"); event.target.value = ""; });
$("noteSheetClose").addEventListener("click", closeNoteSheet);
$("cancelNote").addEventListener("click", closeNoteSheet);
$("saveNote").addEventListener("click", async () => {
  const title = $("noteTitle").value.trim(), body = $("noteBody").innerHTML.trim();
  if (!title && !body) return;
  const existing = getNotes().find((item) => item.id === editingNoteId); const attachments = pendingAttachments.filter((file) => file.noteId === editingNoteId).map((file) => ({ ...file, noteId: editingNoteId })); const note = { id: editingNoteId, folderId: $("noteCategorySelect").value, title: title || "未命名笔记", content: body || NOTE_EMPTY_BODY, coverUrl: pendingNoteCoverUrl, date: new Date().toISOString(), attachments, template: clone(pendingNoteTemplate), starred: existing?.starred || false, syncStatus: "local" }; saveNote(note); saveAttachmentDrafts(); if (!existing) addPoints(10, "新建笔记");
  if (DEMO_MODE) { showToast("演示笔记已保存（刷新页面后自动清空）"); } else { try { const response = await authFetch(apiUrl("/api/notes/save"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...note, syncStatus: "synced" }) }); if (!response.ok) throw new Error(); note.syncStatus = "synced"; saveNote(note); showToast("笔记已同步云端"); } catch { showToast("后端暂未连通，已保存在本机浏览器中。"); } }
  renderNotes($("noteSearch").value); renderProfile(); closeNoteSheet();
});
$("noteAttachmentFile").addEventListener("change", async (event) => { const file = event.target.files?.[0]; if (!file) return; await addImportedAttachment(file); event.target.value = ""; }); // TODO: replace /api/upload and blob: URLs with permanent server URLs when storage API is ready.
$("noteCoverFile").addEventListener("change", async (event) => { const file = event.target.files?.[0]; if (!file) return; pendingNoteCoverFile = file; pendingNoteCoverUrl = await fileDataUrl(file); renderTemplateSelection(); try { pendingNoteCoverUrl = await uploadFile(file, "note_cover"); renderTemplateSelection(); showToast("封面已同步云端"); } catch { showToast("后端暂未连通，封面已保存在本机浏览器中。"); } event.target.value = ""; });
$("folderBreadcrumb").addEventListener("click", () => { currentCategory = "all"; renderCategories(); renderNotes($("noteSearch").value); });
$("deleteNote").addEventListener("click", () => { if (!editingNoteId) return; closeNoteSheet(); openNoteActionSheet(editingNoteId); $("noteActionMenu").hidden = true; $("deleteNotePanel").hidden = false; });
$("noteActionClose").addEventListener("click", closeNoteActionSheet); $("cancelNoteAction").addEventListener("click", closeNoteActionSheet);
$("renameNoteAction").addEventListener("click", () => { const note = getNotes().find((item) => item.id === actionNoteId); if (!note) return; $("renameNoteInput").value = note.title; $("noteActionMenu").hidden = true; $("renameNotePanel").hidden = false; $("renameNoteInput").focus(); });
$("moveNoteAction").addEventListener("click", () => { const list = $("noteMoveOptions"); list.replaceChildren(); getCategories().forEach((folder) => { const button = document.createElement("button"); button.type = "button"; button.textContent = folder.name; button.addEventListener("click", async () => { const note = getNotes().find((item) => item.id === actionNoteId); if (!note) return; note.folderId = folder.id; saveNote(note); try { const response = await authFetch(apiUrl("/api/notes/move"), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: note.id, folderId: folder.id }) }); if (!response.ok) throw new Error(); } catch { try { const response = await authFetch(apiUrl("/api/notes/save"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(note) }); if (!response.ok) throw new Error(); } catch { showToast("后端暂未连通，移动结果已保存在本机"); } } $("noteMoveSheet").hidden = true; closeNoteActionSheet(); renderNotes($("noteSearch").value); showToast(`已移动到「${folder.name}」`); }); list.append(button); }); $("noteActionSheet").hidden = true; $("noteMoveSheet").hidden = false; });
$("closeNoteMove").addEventListener("click", () => { $("noteMoveSheet").hidden = true; $("noteActionSheet").hidden = false; });
$("cancelRenameNote").addEventListener("click", () => { $("renameNotePanel").hidden = true; $("noteActionMenu").hidden = false; });
$("confirmRenameNote").addEventListener("click", () => { const note = getNotes().find((item) => item.id === actionNoteId), title = $("renameNoteInput").value.trim(); if (!note || !title) return; note.title = title; note.date = new Date().toISOString(); saveNote(note); renderNotes($("noteSearch").value); closeNoteActionSheet(); showToast("笔记名称已更新"); });
$("deleteNoteAction").addEventListener("click", () => { $("noteActionMenu").hidden = true; $("deleteNotePanel").hidden = false; });
$("cancelDeleteNote").addEventListener("click", () => { $("deleteNotePanel").hidden = true; $("noteActionMenu").hidden = false; });
$("confirmDeleteNote").addEventListener("click", () => { if (!actionNoteId) return; deleteNote(actionNoteId); renderNotes($("noteSearch").value); renderProfile(); closeNoteActionSheet(); showToast("笔记已删除"); });
$("folderActionClose").addEventListener("click", closeFolderActionSheet); $("cancelFolderAction").addEventListener("click", closeFolderActionSheet);
$("renameFolderAction").addEventListener("click", () => { const folder = getCategories().find((item) => item.id === actionFolderId); if (!folder) return; $("renameFolderInput").value = folder.name; $("folderActionMenu").hidden = true; $("renameFolderPanel").hidden = false; $("renameFolderInput").focus(); });
$("cancelRenameFolder").addEventListener("click", () => { $("renameFolderPanel").hidden = true; $("folderActionMenu").hidden = false; });
$("confirmRenameFolder").addEventListener("click", () => { const folders = getCategories(), folder = folders.find((item) => item.id === actionFolderId), name = $("renameFolderInput").value.trim(); if (!folder || !name) return; folder.name = name; saveCategories(folders); renderCategories(); renderNotes($("noteSearch").value); renderFolderActionSummary(); $("renameFolderPanel").hidden = true; $("folderActionMenu").hidden = false; showToast("文件夹名称已更新"); });
$("folderCoverFile").addEventListener("change", async (event) => { const file = event.target.files?.[0], folders = getCategories(), folder = folders.find((item) => item.id === actionFolderId); if (!file || !folder) return; folder.coverUrl = await fileDataUrl(file); folder.syncStatus = "local"; saveCategories(folders); renderFolderActionSummary(); renderNotes($("noteSearch").value); try { folder.coverUrl = await uploadFile(file, "note_folder_cover"); folder.syncStatus = "synced"; saveCategories(folders); renderFolderActionSummary(); renderNotes($("noteSearch").value); showToast("文件夹封面已同步云端"); } catch { showToast("后端暂未连通，文件夹封面已在本地暂存"); } event.target.value = ""; });
$("deleteFolderAction").addEventListener("click", () => { $("folderActionMenu").hidden = true; $("deleteFolderPanel").hidden = false; });
$("cancelDeleteFolder").addEventListener("click", () => { $("deleteFolderPanel").hidden = true; $("folderActionMenu").hidden = false; });
$("confirmDeleteFolder").addEventListener("click", async () => { if (!actionFolderId) return; const notes = getNotes(), moved = notes.filter((note) => noteFolderId(note) === actionFolderId); moved.forEach((note) => { note.folderId = null; }); localStorage.setItem(storageKeys.notes, JSON.stringify(notes)); saveCategories(getCategories().filter((folder) => folder.id !== actionFolderId)); await Promise.allSettled(moved.map((note) => authFetch(apiUrl("/api/notes/move"), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: note.id, folderId: null }) }))); if (currentCategory === actionFolderId) currentCategory = "all"; renderCategories(); renderNotes($("noteSearch").value); closeFolderActionSheet(); showToast("文件夹已删除，内部笔记已移到根目录"); });
document.querySelectorAll("[data-template-category]").forEach((button) => button.addEventListener("click", () => { pendingNoteTemplate.category = button.dataset.templateCategory; renderTemplateSelection(); }));
document.querySelectorAll("[data-template-pattern]").forEach((button) => button.addEventListener("click", () => { pendingNoteTemplate.pattern = button.dataset.templatePattern; renderTemplateSelection(); }));
document.querySelectorAll("[data-template-color]").forEach((button) => button.addEventListener("click", () => { pendingNoteTemplate.color = button.dataset.templateColor; renderTemplateSelection(); }));
$("cancelTemplate").addEventListener("click", closeTemplateSheet); $("confirmTemplate").addEventListener("click", () => { const template = clone(pendingNoteTemplate); $("noteTemplateSheet").hidden = true; openNoteSheet(null, template); });
$("closeAttachmentPreview").addEventListener("click", () => { $("attachmentPreview").hidden = true; $("attachmentPreviewBody").replaceChildren(); });
$("editProfileButton").addEventListener("click", openProfileSheet); $("profileSheetClose").addEventListener("click", closeProfileSheet); $("cancelProfile").addEventListener("click", closeProfileSheet);
$("profileAvatarFile").addEventListener("change", async (event) => { pendingAvatarFile = event.target.files?.[0] || null; if (!pendingAvatarFile) return; pendingAvatarUrl = await fileDataUrl(pendingAvatarFile); $("profileAvatarPreview").src = pendingAvatarUrl; $("profileAvatarPreview").closest(".avatar-picker").classList.add("has-preview"); });
// 资料以服务端为准，所以这里是「先提交、成功才更新界面」。以前那句
// 「已保存在当前设备」是假的安慰 —— 换台设备就看不到了，现在宁可说实话。
$("saveProfile").addEventListener("click", async () => {
  const user = { nickname: $("profileNicknameInput").value.trim() || DEFAULT_USER.nickname, signature: $("profileSignatureInput").value.trim(), avatarUrl: pendingAvatarUrl || getUser().avatarUrl };
  const button = $("saveProfile");
  button.disabled = true;
  try {
    if (pendingAvatarFile) {
      // 字段名必须是 file：后端签名是 `file: UploadFile = File(...)`。
      // 之前这里传的是 avatar，会直接被判 422 —— 那个 404/422 一直被 catch 吞成了
      // 「头像上传失败，已保存在当前设备」。
      const form = new FormData();
      form.append("file", pendingAvatarFile);
      const response = await authFetch(apiUrl("/api/user/avatar"), { method: "POST", body: form });
      if (!response.ok) throw new Error("avatar");
      user.avatarUrl = (await response.json()).url || user.avatarUrl;
    }
    const response = await authFetch(apiUrl("/api/user/profile"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nickname: user.nickname, signature: user.signature, avatarUrl: user.avatarUrl }) });
    if (!response.ok) throw new Error("profile");
    setUser((await response.json()).profile || user);
  } catch {
    showToast("服务器暂时不可用，资料未保存。");
    return;
  } finally {
    button.disabled = false;
  }
  pendingAvatarFile = null;
  renderProfile();
  closeProfileSheet();
  showToast("资料已保存");
});
$("openRulesButton").addEventListener("click", () => $("rulesPage").hidden = false); $("closeRulesButton").addEventListener("click", () => $("rulesPage").hidden = true);
$("closeScheduleButton").addEventListener("click", () => { closeSchedulePage(); activateAppPage("petPage"); });
document.querySelectorAll("[data-schedule-filter]").forEach((button) => button.addEventListener("click", () => renderSchedule(button.dataset.scheduleFilter)));
document.querySelector("[data-record-schedule]")?.addEventListener("click", openSchedulePage);
$("recordNoteReview")?.addEventListener("click", openNoteReviewPage);
$("noteReviewSearch")?.addEventListener("input", (e) => renderNoteReviewList(e.target.value)); $("noteReviewStart")?.addEventListener("click", () => { $("noteReviewConfirm").hidden = true; startNoteReview(noteReviewNote); }); $("noteReviewCancel")?.addEventListener("click", () => { $("noteReviewConfirm").hidden = true; });
$("noteReviewSend")?.addEventListener("click", () => { const v=$("noteReviewInput").value.trim(); if(v){ $("noteReviewInput").value=""; sendNoteReview(v); }});
$("closeNoteReview")?.addEventListener("click", () => {
  // 顶部这个「返回」是各子视图唯一的返回入口，逐级退：记录详情 → 历史列表 → 选笔记 → 退出复盘页
  if (!$("noteReviewRecord").hidden) { closeReviewRecord(); return; }
  if (!$("noteReviewHistory").hidden) { openNoteReviewPage(); return; }
  requestReviewSummary();
  $("noteReviewPage").hidden = true;
  document.body.classList.remove("note-review-open");
  activateAppPage("petPage");
});
$("noteReviewHistoryButton")?.addEventListener("click", openNoteReviewHistory);
$("noteReviewDeleteButton")?.addEventListener("click", deleteReviewRecord);
$("noteReviewDeleteCancel")?.addEventListener("click", () => { pendingReviewDeleteId = null; $("noteReviewDeleteConfirm").hidden = true; });
// 直接关标签页时也尽量把小结发出去（keepalive 让它活过页面卸载）
window.addEventListener("pagehide", requestReviewSummary);
document.querySelectorAll("[data-record-filter]").forEach((button) => button.addEventListener("click", () => { activateAppPage("reviewPage"); switchReviewFilter(button.dataset.recordFilter); }));
$("returnToProfile").addEventListener("click", () => { renderProfile(); activateAppPage("petPage"); });

$("profileAvatar").addEventListener("error", () => {
  $("profileAvatar").hidden = true;
  $("avatarFallback").hidden = false;
});
$("profileAvatarPreview").addEventListener("error", () => {
  $("profileAvatarPreview").removeAttribute("src");
  $("profileAvatarPreview").closest(".avatar-picker").classList.remove("has-preview");
});
$("reviewCompleteAvatar").addEventListener("error", () => {
  $("reviewCompleteAvatar").src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Ccircle cx='32' cy='32' r='31' fill='%23e4f0ea'/%3E%3Cpath d='M18 41c4-13 8-19 14-19s10 6 14 19' fill='none' stroke='%23176b4d' stroke-width='4' stroke-linecap='round'/%3E%3Ccircle cx='26' cy='29' r='2' fill='%23176b4d'/%3E%3Ccircle cx='38' cy='29' r='2' fill='%23176b4d'/%3E%3C/svg%3E";
}, { once: true });
["flashFrontImage", "flashBackImage", "cardFrontImagePreview", "cardBackImagePreview"].forEach((id) => {
  $(id).addEventListener("error", () => { $(id).hidden = true; });
});

// Keep the back affordance in sync when a history item restores a result.
const syncHistoryBack = () => {
  const visible = !$("discoverResult").hidden || !$("explainResult").hidden;
  $("historyBackButton").hidden = !visible;
  $("historyMemory").classList.toggle("has-result-back", visible);
};
new MutationObserver(syncHistoryBack).observe($("discoverResult"), { attributes: true, attributeFilter: ["hidden"] });
new MutationObserver(syncHistoryBack).observe($("explainResult"), { attributes: true, attributeFilter: ["hidden"] });

/* ================================ 启动 ================================
   顺序是「先拿到身份，再加载数据」。身份拿不到时一张卡片都不拉：页面被 #authPage
   整个盖住，拉了也看不见，只会往控制台刷一串 401。
   注意 bootApp() 现在只有 initAuth() 一个调用点 —— 必须等身份就位再跑，否则
   那些请求全是匿名 401。 */
async function bootApp() {
  if (DEMO_MODE) {
    [storageKeys.books, storageKeys.notes, storageKeys.categories, storageKeys.explainHistory, storageKeys.discoverHistory, storageKeys.researchHistory].forEach((key) => localStorage.removeItem(key));
  }
  renderBooks();
  renderCategories();
  renderNotes();
  buildReviewQueue();
  renderProfile();
  renderRules();
  hydrateNotesFromServer();
  checkDueCards();
  window.setInterval(() => checkDueCards({ notify: true }), 5 * 60 * 1000);
  checkBackendHealth();
  syncHistoryBack();
  // 上面这一串都是同步的（或自己 catch 掉的），所以首屏已经画完了，下面这次网络往返
  // 不会挡住首屏。两步的顺序是必须的：先把本地暂存的卡片补推到服务端，再和服务端
  // 对账 —— 反过来的话，服务端那份书架会盖掉本地还没推上去的卡片。
  await syncLocalCards();
  await syncLibrary();
  renderBooks(); renderCategories(); renderNotes($("noteSearch")?.value || ""); buildReviewQueue();
}

async function initAuth() {
  // 没有 token = 第一次来（或清过浏览器数据）：静默建一个匿名身份，不打扰用户。
  // 建不出来才回退到兜底提示层 —— 那种情况多半是断网或服务端没起来。
  if (!getToken() && !(await createGuestSession())) {
    showAuthPage("无法连接服务器，请刷新页面重试。");
    return;
  }
  let data;
  try {
    // skipAuthRedirect：这里的 401 是「token 失效」的正常分支，自己处理即可，
    // 交给 authFetch 会再多跳一次、并且把文案覆盖掉。
    const response = await authFetch(apiUrl("/api/auth/me"), { cache: "no-store", skipAuthRedirect: true });
    if (!response.ok) {
      // token 失效（过期 / 换了 LC_SECRET_KEY / 匿名记录被清理）。产品上根本没有登录，
      // 没有任何凭证可问用户要，所以唯一走得通的路是**再建一个匿名身份**。
      // 只重试一次，避免服务端异常时无限刷新。
      setToken("");
      if (sessionStorage.getItem(GUEST_RETRY_KEY) !== "1") {
        sessionStorage.setItem(GUEST_RETRY_KEY, "1");
        if (await createGuestSession()) { window.location.reload(); return; }
      }
      showAuthPage("身份已失效，请刷新页面重试。");
      return;
    }
    data = await response.json();
  } catch {
    showAuthPage("无法连接服务器，请刷新页面重试。");
    return;
  }
  sessionStorage.removeItem(GUEST_RETRY_KEY);
  currentUser = data.user || null;
  authProfile = data.profile || null;
  applyStorageScope(currentUser?.id || "");
  // 顺序要紧，三步不能换：① 认领账号体系之前留在无命名空间 key 里的书架；
  // ② 再擦掉老版本种进 localStorage 的种子数据；③ 最后才进 bootApp()（里面会拉/推
  // 书架）。反过来的话种子会被推到服务端，那就不再是"清一下浏览器缓存"能解决的了。
  adoptLegacyLocalData();
  purgeSeedData();
  hideAuthPage();
  bootApp();
}

// 兜底提示层上的「刷新重试」。刷新即重新走一遍 initAuth —— 会再建一个匿名身份并进应用，
// 所以这一层永远是个出口，不是死路。用整页 reload 而不是重跑 initAuth()，是因为这个文件里
// 的全局态太多（书架、草稿、已渲染的 DOM），重载最省事也最不会漏。
$("authRetryButton").addEventListener("click", () => window.location.reload());

$("explainButton").addEventListener("click", explain);
$("chatButton").addEventListener("click", chat);
$("discoverButton").addEventListener("click", discover);
$("chatText").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); chat(); } });

syncHistoryBack();
initAuth();

















