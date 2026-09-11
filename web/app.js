const API_BASE = "http://127.0.0.1:8000";
const apiUrl = (path) => `${API_BASE}${path}`;
const state = { explainSessionId: null, discoverSessionId: null, activeKnowledgePanel: "discoverPanel", importTarget: "discoverText", importPanelTarget: "discoverPanel", explainConversation: [] };
let explainDraftText = localStorage.getItem("draft_explain") || "";
let discoverDraftText = localStorage.getItem("draft_discover") || "";
// Remove legacy shared-draft keys that caused cross-tab ghost text.
["globalInputText", "currentText", "draft", "inputText"].forEach((key) => localStorage.removeItem(key));
let historyFilterType = "读懂它", pendingHistoryDeleteId = null;

async function checkBackendHealth() {
  try {
    const response = await fetch(apiUrl("/api/health"), { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.code !== 0 || !data.llm_bridge?.ok) {
      console.warn("Logic-Coloc backend dependency is unavailable", data);
      const status = $("sessionStatus");
      if (status && !status.textContent.includes("会话")) status.textContent = "模型服务未连接";
    }
    return data;
  } catch (error) {
    console.error("Logic-Coloc health check failed", error);
    return null;
  }
}

const $ = (id) => document.getElementById(id);
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
  $("loadingText").textContent = text;
  $("loading").hidden = !active;
  if (trigger) trigger.disabled = active;
  window.clearInterval(loadingTimer);
  if (active) {
    loadingStartedAt = Date.now();
    loadingTimer = window.setInterval(() => {
      const seconds = Math.floor((Date.now() - loadingStartedAt) / 1000);
      $("loadingText").textContent = `${text} 已等待 ${seconds} 秒，请不要重复提交…`;
    }, 1000);
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

async function request(path, payload) {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const detail = data.error?.message || (typeof data.detail === "string" ? data.detail : "服务暂时不可用");
    console.error("Logic-Coloc API request failed", { path, status: response.status, data });
    throw new Error(`${detail}（HTTP ${response.status}）`);
  }
  if (data.code && data.code !== 0) console.error("Logic-Coloc API business error", { path, status: response.status, data });
  return data;
}

function extractFirstUrl(value) {
  const match = String(value || "").match(/https?:\/\/[^\s<>"'\]\[）)]+/i);
  return match ? match[0].replace(/[，。！？、；：,.!?;:]+$/, "") : null;
}

let toastTimer;
function showToast(message) { window.clearTimeout(toastTimer); $("toast").textContent = message; $("toast").hidden = false; toastTimer = window.setTimeout(() => { $("toast").hidden = true; }, 3200); }
function openImportSheet(id) { const target = state.activeKnowledgePanel === "discoverPanel" ? "discoverText" : "explainText"; state.importPanelTarget = state.activeKnowledgePanel; $("manualImportText").value = state.activeKnowledgePanel === "discoverPanel" ? discoverDraftText : explainDraftText; $("linkImportText").value = ""; $("sheetBackdrop").hidden = false; $(id).hidden = false; document.body.classList.add("sheet-open"); }
function closeImportSheet() { $("importSheet").hidden = true; $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); showImportPanel(null); }
function showImportPanel(panelId) { $("importOptions").hidden = Boolean(panelId); document.querySelectorAll(".import-panel").forEach((panel) => { panel.hidden = panel.id !== panelId; }); }
function targetInput() { return $(state.importTarget); }
function revealKnowledgeEditor() { $("knowledgeLauncher").hidden = true; ["explainPanel", "discoverPanel"].forEach((id) => { $(id).hidden = id !== state.activeKnowledgePanel; }); }
function placeImportedText(text, message, panelId = state.importPanelTarget) { const inputId = panelId === "discoverPanel" ? "discoverText" : "explainText"; $(inputId).value = text; if (panelId === "discoverPanel") { discoverDraftText = text; localStorage.setItem("draft_discover", text); } else { explainDraftText = text; localStorage.setItem("draft_explain", text); } closeImportSheet(); state.activeKnowledgePanel = panelId; state.importTarget = inputId; revealKnowledgeEditor(); showToast(message); $(inputId).focus(); }
function showKnowledgeLauncher(panelId) {
  state.activeKnowledgePanel = panelId; state.importTarget = panelId === "discoverPanel" ? "discoverText" : "explainText";
  $("explainText").value = explainDraftText;
  $("discoverText").value = discoverDraftText;
  document.querySelectorAll(".tab").forEach((item) => { const active = item.dataset.panel === panelId; item.classList.toggle("active", active); item.setAttribute("aria-selected", String(active)); });
  const discovering = panelId === "discoverPanel";
  $("pageTitle").textContent = discovering ? "探索它在跨学科领域的逻辑同源" : "把专业知识翻译成你能理解的语言";
  $("pageSubtitle").textContent = discovering ? "输入一个概念，看看其他学科里有没有相似的运行机制。" : "粘贴一段专业内容，先理解它，再寻找其他领域中结构相似的概念。";
  $("launcherEyebrow").textContent = panelId === "discoverPanel" ? "导入文本寻找跨学科同源" : "导入文本进行专业解读";
  $("launcherTitle").textContent = discovering ? "导入内容或概念，寻找跨学科同源" : "选择一种方式开始";
  $("knowledgeLauncher").hidden = false; $("explainPanel").hidden = true; $("discoverPanel").hidden = true;
  $("historyBackButton").hidden = true; $("historyMemory").classList.remove("has-result-back");
  renderHistoryMemory();
  window.scrollTo({ top: document.querySelector(".mode-tabs").offsetTop, behavior: "smooth" });
}
function clearDraftForPanel(panelId) {
  if (panelId === "discoverPanel") { discoverDraftText = ""; localStorage.removeItem("draft_discover"); $("discoverText").value = ""; }
  else { explainDraftText = ""; localStorage.removeItem("draft_explain"); $("explainText").value = ""; }
}
async function parseImportedLink() {
  const url = extractFirstUrl($("linkImportText").value); const button = $("parseLinkButton");
  if (!url) { $("linkImportFeedback").textContent = "没有识别到以 http 或 https 开头的链接。"; $("linkImportFeedback").hidden = false; return; }
  button.disabled = true; button.textContent = "解析中…"; $("linkImportFeedback").textContent = `已提取：${url}`; $("linkImportFeedback").hidden = false;
  try {
    const controller = new AbortController(); const timeout = window.setTimeout(() => controller.abort(), 15000);
    const response = await fetch(apiUrl("/api/extract_link"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }), signal: controller.signal }); window.clearTimeout(timeout);
    const data = await response.json().catch(() => ({})); if (!response.ok || data.code !== 0 || !data.text) throw new Error("link import failed");
    placeImportedText(data.text, state.importPanelTarget === "discoverPanel" ? "导入成功，可以开始寻找同源啦" : "导入成功，可以开始解读啦", state.importPanelTarget);
  } catch { showToast("该链接被平台限制，解析失败，请尝试截图导入或手动复制文本。"); showImportPanel("imageImportPanel"); }
  finally { button.disabled = false; button.textContent = "解析链接"; }
}

async function importImage(file) {
  if (!file) return; const feedback = $("imageImportFeedback"); feedback.textContent = "正在识别图片…";
  try {
    const form = new FormData(); form.append("file", file);
    // FastAPI 使用 prefix="/api" 的 router 时，后端应注册 @router.post("/ocr")，最终地址即 /api/ocr。
    const response = await fetch(apiUrl("/api/ocr"), { method: "POST", body: form });
    if (response.status === 404) { closeImportSheet(); revealKnowledgeEditor(); showToast("后端识图接口尚未开启，请直接手动粘贴文本。"); $(state.importPanelTarget === "discoverPanel" ? "discoverText" : "explainText").focus(); return; }
    const data = await response.json().catch(() => ({})); if (!response.ok || !data.text) throw new Error("ocr failed");
    placeImportedText(data.text, state.importPanelTarget === "discoverPanel" ? "识别成功，可以开始寻找同源啦" : "识别成功，可以开始解读啦", state.importPanelTarget);
  }
  catch { feedback.textContent = "图片识别暂时不可用，请尝试手动复制文字。"; showToast("图片识别失败，请尝试手动复制文本。"); }
  finally { $("imageImportFile").value = ""; }
}

function renderRichText(element, content) {
  const escape = (value) => value.replace(/[&<>"']/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
  const lines = String(content || "").replace(/```(?:markdown|text)?/gi, "").replace(/```/g, "").split(/\r?\n/);
  const html = [];
  let listOpen = null;
  const closeList = () => { if (listOpen) { html.push(`</${listOpen}>`); listOpen = null; } };
  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line || /^[-*_]{3,}$/.test(line)) { closeList(); return; }
    const inline = escape(line).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/__(.+?)__/g, "<strong>$1</strong>");
    if (/^#{1,3}\s+/.test(line)) { closeList(); html.push(`<h3>${inline.replace(/^#{1,3}\s+/, "")}</h3>`); return; }
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

function openSaveCardSheet() {
  $("saveCardConcept").textContent = $("conceptName").textContent || "当前解释";
  $("saveCardStatus").textContent = "卡片将保存到默认书架，并同步到复习。";
  $("confirmSaveCard").disabled = false;
  $("sheetBackdrop").hidden = false; $("saveCardSheet").hidden = false; document.body.classList.add("sheet-open");
}

function closeSaveCardSheet() {
  $("sheetBackdrop").hidden = true; $("saveCardSheet").hidden = true; document.body.classList.remove("sheet-open");
}

async function saveKnowledgeCard() {
  const button = $("confirmSaveCard");
  button.disabled = true; button.textContent = "正在保存…"; $("saveCardStatus").textContent = "正在写入后端卡片库…";
  const source = state.activeKnowledgePanel === "discoverPanel" ? "跨学科理解" : "读懂它";
  const sessionId = source === "跨学科理解" ? state.discoverSessionId : state.explainSessionId;
  const card = { id: crypto.randomUUID ? crypto.randomUUID() : makeId("card"), front: $("conceptName").textContent || "知识卡片", back: $("explanationCore").textContent || $("explanation").textContent || "", source: source === "读懂它" ? "explain" : "discover", sessionId, createdAt: new Date().toISOString(), status: "unmastered" };
  try {
    const response = await fetch(apiUrl("/api/cards/save"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(card) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    card.syncStatus = "synced"; const syncedBook = getBooks().find((item) => item.id === "book_cross") || getBooks()[0]; syncedBook.cards = syncedBook.cards.filter((item) => item.front !== card.front || item.sessionId !== card.sessionId); syncedBook.cards.push(card); saveBook(syncedBook); renderBooks(); buildReviewQueue(); $("saveCardStatus").textContent = "已写入后端并同步到复习。"; showToast("✅ 已存入卡片，可在「卡片」Tab 查看。");
  } catch {
    const book = getBooks().find((item) => item.id === "book_cross") || getBooks()[0];
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
        const response = await fetch(apiUrl("/api/cards/save"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(card) });
        if (!response.ok) continue;
        delete card.localOnly; card.syncStatus = "synced"; changed = true;
      } catch { /* keep local fallback until the API is available */ }
    }
    if (changed) saveBook(book);
  }
  if (changed) { renderBooks(); buildReviewQueue(); }
}

function renderProfile(profile) {
  const container = $("profileRows");
  container.replaceChildren();
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
  $("logicProfile").hidden = true;
  $("profileToggle").setAttribute("aria-expanded", "false");
  $("profileToggle").textContent = "查看逻辑画像 ›";
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
    explainDraftText = text;
    state.explainSessionId = data.session_id;
    addHistory("读懂它", text, state.explainSessionId, data);
    $("sessionStatus").textContent = `读懂会话 ${state.explainSessionId.slice(0, 8)}`;
    $("conceptName").textContent = data.concept?.name || "分析结果";
    $("explainUserMessage").textContent = text;
    renderCoreAnswer(data.explanation || "暂时没有生成解释。");
    renderProfile(data.logic_profile);
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
  const records = data.candidates?.length ? data.candidates : (data.results || []).map((candidate) => ({ candidate }));
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
    const reliable = report?.verdict === "RELIABLE_WITH_LIMITS" || record.reliability === "有限成立";
    const reliabilityLabel = report ? (verdictLabels[report.verdict] || "待核验") : (record.reliability || "待核验");
    const card = document.createElement("details"); card.className = "candidate";
    const summary = document.createElement("summary"); summary.className = "candidate-summary";
    const head = document.createElement("div"); head.className = "candidate-head";
    const title = document.createElement("h3"); title.textContent = candidateName;
    const badge = document.createElement("span"); badge.className = `reliability ${reliable ? "yes" : ""}`; badge.textContent = reliabilityLabel;
    head.append(title, badge);
    const domain = document.createElement("span"); domain.className = "domain-pill"; domain.textContent = candidate.domain || "未标注";
    const scores = document.createElement("div"); scores.className = "scores";
    [["检索相关性", retrieval], ["逻辑同源度", homology]].forEach(([label, value]) => {
      const score = document.createElement("div"); score.className = "score";
      const name = document.createElement("span"); name.textContent = label;
      const strong = document.createElement("strong"); strong.textContent = valueOrUnavailable(value);
      const track = document.createElement("div"); track.className = "track";
      const fill = document.createElement("div"); fill.className = `fill ${label === "逻辑同源度" ? "candidate-fill" : ""}`; fill.style.width = Number.isFinite(Number(value)) ? `${Math.round(Number(value) * 100)}%` : "0%";
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
  setLoading(true, "正在分析逻辑结构、检索候选并生成学习报告…", $("discoverButton"));
  try {
    // Three candidates keep the full grounded reports within the model timeout
    // while still providing a meaningful cross-disciplinary candidate stream.
    const payload = { text, top_k: 3 };
    const data = await request("/api/discover", payload);
    if (data.code !== 0) {
      state.discoverSessionId = null;
      $("discoverResult").hidden = true;
      showError("discoverError", data.message || "模型服务异常，请稍后重试");
      return;
    }
    state.discoverSessionId = data.session_id;
    discoverDraftText = text;
    addHistory("跨学科理解", text, state.discoverSessionId, data);
    discoverDraftText = ""; $("discoverText").value = "";
    $("sessionStatus").textContent = `发现会话 ${state.discoverSessionId.slice(0, 8)}`;
    $("discoverTitle").textContent = `发现同源 · ${data.concept?.name || text.slice(0, 30)}`;
    renderRichText($("discoverReport"), data.report || "同源分析已完成。");
    renderCandidates(data); $("discoverResult").hidden = false; $("historyBackButton").hidden = false; $("historyMemory").classList.add("has-result-back");
    if ((data.report || "").includes("LLM 调用失败") || (data.report || "").includes("无法完成特征提取")) {
      showError("discoverError", "同源分析当前无法连接模型后端，请确认 LLM 网关已经启动。");
    }
  } catch (error) { console.error("Discover request failed", error); showError("discoverError", error.message); }
  finally { setLoading(false, "", $("discoverButton")); }
}

function activatePanel(panelId) {
  showKnowledgeLauncher(panelId);
}

function activateAppPage(pageId) {
  const knowledgeActive = pageId === "knowledge";
  document.querySelectorAll(".knowledge-shell").forEach((item) => { item.hidden = !knowledgeActive; });
  document.querySelectorAll(".app-page").forEach((item) => { item.hidden = item.id !== pageId; });
  document.querySelectorAll(".bottom-tab").forEach((item) => {
    const active = item.dataset.appPage === pageId;
    item.classList.toggle("active", active); item.setAttribute("aria-selected", String(active));
  });
  if (knowledgeActive) showKnowledgeLauncher(state.activeKnowledgePanel);
  else window.scrollTo({ top: 0, behavior: "smooth" });
}

const storageKeys = { books: "logic_coloc_books_v1", notes: "logic_coloc_notes_v1", history: "logic_coloc_history_v1", explainHistory: "explain_history", discoverHistory: "discover_history", points: "logic_coloc_points_v1", awards: "logic_coloc_review_awarded_date_v1", user: "logic_coloc_user_v1", firstLogin: "logic_coloc_first_login_v1", categories: "logic_coloc_note_categories_v1", attachmentDrafts: "logic_coloc_attachment_drafts_v1", reviewSession: "logic_coloc_review_session_v1" };
function readHistoryKey(key) { try { const items = JSON.parse(localStorage.getItem(key)); return Array.isArray(items) ? items : []; } catch { return []; } }
function normalizeHistoryItem(item) { const rawType = String(item.type || item.mode || ""); const type = rawType.includes("跨") || rawType.toLowerCase().includes("discover") ? "跨学科理解" : "读懂它"; const timestamp = typeof item.timestamp === "number" ? new Date(item.timestamp < 100000000000 ? item.timestamp * 1000 : item.timestamp).toISOString() : (item.timestamp || new Date().toISOString()); return { id: item.id || makeId("history"), type, input: item.input || item.text || "", sessionId: item.sessionId || item.session_id || "", timestamp, fullResponse: item.fullResponse || item.response || null }; }
function migrateHistory() { const legacy = readHistoryKey(storageKeys.history); const explain = readHistoryKey(storageKeys.explainHistory); const discover = readHistoryKey(storageKeys.discoverHistory); if (legacy.length || explain.length || discover.length) { const merged = [...legacy, ...explain, ...discover].map(normalizeHistoryItem); const unique = [...new Map(merged.map((item) => [item.sessionId || item.id, item])).values()]; localStorage.setItem(storageKeys.explainHistory, JSON.stringify(unique.filter((item) => item.type === "读懂它"))); localStorage.setItem(storageKeys.discoverHistory, JSON.stringify(unique.filter((item) => item.type === "跨学科理解"))); if (legacy.length) localStorage.removeItem(storageKeys.history); } }
function getHistory() { migrateHistory(); return [...readHistoryKey(storageKeys.explainHistory), ...readHistoryKey(storageKeys.discoverHistory)].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp)); }
function saveHistory(items) { localStorage.setItem(storageKeys.explainHistory, JSON.stringify(items.filter((item) => item.type === "读懂它").slice(0, 100))); localStorage.setItem(storageKeys.discoverHistory, JSON.stringify(items.filter((item) => item.type === "跨学科理解").slice(0, 100))); }
function historyType() { return state.activeKnowledgePanel === "discoverPanel" ? "跨学科理解" : "读懂它"; }
function addHistory(type, input, sessionId, fullResponse = null) { if (!sessionId) return; const items = getHistory().filter((item) => item.sessionId !== sessionId); items.unshift({ id: makeId("history"), type, input, sessionId, timestamp: new Date().toISOString(), fullResponse }); saveHistory(items); renderHistoryMemory(); }
function renderHistoryMemory() { const box = $("historyMemoryList"); if (!box) return; box.replaceChildren(); const type = historyType(); const items = getHistory().filter((item) => item.type === type).slice(0, 5); if (!items.length) { box.textContent = "暂无历史记忆，去探索第一个概念吧～"; return; } items.forEach((item) => { const row = document.createElement("button"); row.type = "button"; row.className = `history-memory-item ${type === "读懂它" ? "explain" : "discover"}`; row.textContent = `[${type}] ${String(item.input).slice(0, 34)} · ${new Date(item.timestamp).toLocaleDateString("zh-CN")}`; row.addEventListener("click", () => restoreHistory(item)); box.append(row); }); }
function restoreHistory(item) { closeHistory(); const discover = item.type === "跨学科理解"; state.activeKnowledgePanel = discover ? "discoverPanel" : "explainPanel"; showKnowledgeLauncher(state.activeKnowledgePanel); revealKnowledgeEditor(); const panel = $(state.activeKnowledgePanel); const inputArea = panel?.querySelector(".input-area"); if (inputArea) inputArea.hidden = true; if (discover) { state.discoverSessionId = item.sessionId; discoverDraftText = item.input; $("discoverText").value = item.input; $("sessionStatus").textContent = `发现会话 ${(item.sessionId || "").slice(0, 8)}`; if (item.fullResponse) { const response = item.fullResponse; $("discoverTitle").textContent = `发现同源 · ${response.concept?.name || item.input}`; renderRichText($("discoverReport"), response.report || "同源分析已完成。"); renderCandidates(response); $("discoverResult").hidden = false; } else { $("discoverResult").hidden = true; showToast("这条历史没有保存完整结果，请重新发起查询"); } } else { state.explainSessionId = item.sessionId; explainDraftText = item.input; $("explainText").value = item.input; $("sessionStatus").textContent = `读懂会话 ${(item.sessionId || "").slice(0, 8)}`; state.explainConversation = []; $("conversation").replaceChildren(); if (item.fullResponse) { const response = item.fullResponse; $("conceptName").textContent = response.concept?.name || "分析结果"; $("explainUserMessage").textContent = item.input; renderCoreAnswer(response.explanation || ""); renderProfile(response.logic_profile); (response.conversation || []).forEach((message) => addMessage(message.role, message.content)); $("explainResult").hidden = false; $("followUp").hidden = false; } else { $("explainResult").hidden = true; $("followUp").hidden = true; showToast("这条历史没有保存完整结果，请重新发起查询"); } } window.scrollTo({ top: 0, behavior: "smooth" }); }
function renderHistory() { const box = $("historyList"); box.replaceChildren(); document.querySelectorAll(".history-filter").forEach((button) => { const active = button.dataset.historyType === historyFilterType; button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active)); }); const items = getHistory().filter((item) => item.type === historyFilterType); if (!items.length) { box.textContent = `暂无${historyFilterType}历史记录`; return; } items.forEach((item) => { const row = document.createElement("article"); row.className = "history-item"; const open = document.createElement("button"); open.className = "history-open"; open.type = "button"; const type = document.createElement("strong"); type.className = "history-type"; type.textContent = `[${item.type}]`; const text = document.createElement("span"); text.className = "history-summary"; text.textContent = String(item.input).slice(0, 80); open.append(type, text); open.addEventListener("click", () => restoreHistory(item)); const remove = document.createElement("button"); remove.type = "button"; remove.className = "history-remove"; remove.textContent = "⋮"; remove.setAttribute("aria-label", "更多操作"); remove.addEventListener("click", (event) => { event.stopPropagation(); pendingHistoryDeleteId = item.id; $("sheetBackdrop").hidden = false; $("historyDeleteConfirm").hidden = false; document.body.classList.add("sheet-open"); }); const time = document.createElement("small"); time.textContent = new Date(item.timestamp).toLocaleString("zh-CN"); row.append(open, remove, time); box.append(row); }); }
function openHistory() { historyFilterType = historyType(); renderHistory(); $("sheetBackdrop").hidden = false; $("historySheet").hidden = false; document.body.classList.add("sheet-open"); }
function closeHistory() { $("historySheet").hidden = true; if (!document.querySelector(".bottom-sheet:not([hidden])")) { $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); } }
const seedBooks = [
  { id: "book_agent", name: "Agent 探索", icon: "▤", cards: [{ id: "card_mcp", front: "MCP", back: "Model Context Protocol：让模型以统一方式连接工具与上下文的协议。", status: "unmastered" }, { id: "card_state", front: "LangGraph State", back: "让工作流节点共享并持续更新结构化状态。", status: "unmastered" }] },
  { id: "book_cross", name: "跨学科机制", icon: "◇", cards: [{ id: "card_feedback", front: "负反馈", back: "输出反过来抑制偏差，使系统趋于稳定。", status: "unmastered" }, { id: "card_homology", front: "同源不等于等价", back: "机制角色可以相似，但实现材料和成立边界可能不同。", status: "unmastered" }] },
  { id: "book_system", name: "系统科学", icon: "▦", cards: [{ id: "card_closure", front: "系统闭合性", back: "描述系统边界及其与环境交换的程度。", status: "mastered" }] },
];
const seedNotes = [{ id: "note_mcp", title: "如何理解 MCP", content: "把它看成模型与外部能力之间的标准插座。", date: new Date().toISOString() }, { id: "note_agent", title: "Agent 与 Workflow 的区别", content: "Workflow 的路径更固定，Agent 会根据状态选择下一步。", date: new Date(Date.now() - 86400000).toISOString() }];
const seedCategories = [{ id: "learning", name: "我的学习笔记", coverUrl: "", syncStatus: "default" }, { id: "research", name: "科研日志", coverUrl: "", syncStatus: "default" }];
const clone = (value) => JSON.parse(JSON.stringify(value));
const makeId = (prefix) => `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
function readStore(key, fallback) { try { const value = JSON.parse(localStorage.getItem(key)); return Array.isArray(value) ? value : clone(fallback); } catch { return clone(fallback); } }
function getBooks() { const books = readStore(storageKeys.books, seedBooks); if (!localStorage.getItem(storageKeys.books)) localStorage.setItem(storageKeys.books, JSON.stringify(books)); return books; }
function saveBook(book) { const books = getBooks(); const index = books.findIndex((item) => item.id === book.id); if (index >= 0) books[index] = book; else books.push(book); localStorage.setItem(storageKeys.books, JSON.stringify(books)); return book; }
function getNotes() { const notes = readStore(storageKeys.notes, seedNotes), seen = new Set(); let changed = false; notes.forEach((note) => { note.attachments = (note.attachments || []).filter((file) => { const key = file.id || file.url || `${file.name}:${file.size}`; const owner = file.noteId || note.id; if (owner !== note.id || seen.has(key)) { changed = true; return false; } if (!file.noteId) { file.noteId = note.id; changed = true; } seen.add(key); return true; }); }); if (!localStorage.getItem(storageKeys.notes) || changed) localStorage.setItem(storageKeys.notes, JSON.stringify(notes)); return notes.sort((a, b) => new Date(b.date) - new Date(a.date)); }
function saveNote(note) { const notes = getNotes(); const index = notes.findIndex((item) => item.id === note.id); if (index >= 0) notes[index] = note; else notes.unshift(note); localStorage.setItem(storageKeys.notes, JSON.stringify(notes)); return note; }
function deleteNote(noteId) { localStorage.setItem(storageKeys.notes, JSON.stringify(getNotes().filter((item) => item.id !== noteId))); }
async function hydrateNotesFromServer() { try { const response = await fetch(apiUrl("/api/notes")); if (!response.ok) return; const data = await response.json(); if (Array.isArray(data.notes) && data.notes.length) { const merged = new Map(getNotes().map((note) => [note.id, note])); data.notes.forEach((note) => merged.set(note.id, note)); localStorage.setItem(storageKeys.notes, JSON.stringify([...merged.values()])); } } catch { /* Offline mode keeps local data. */ } renderNotes($("noteSearch")?.value || ""); }
function migrateRootNotes() { const notes = readStore(storageKeys.notes, seedNotes); let changed = false; notes.forEach((note) => { if (note.folderId === "default" || note.categoryId === "default") { note.folderId = null; delete note.categoryId; changed = true; } }); if (changed) localStorage.setItem(storageKeys.notes, JSON.stringify(notes)); }
function getCategories() { const items = readStore(storageKeys.categories, seedCategories).filter((folder) => folder.id !== "default"); let changed = !localStorage.getItem(storageKeys.categories); items.forEach((folder) => { if (!("coverUrl" in folder)) { folder.coverUrl = ""; changed = true; } if (!("syncStatus" in folder)) { folder.syncStatus = folder.coverUrl?.startsWith("blob:") || folder.coverUrl?.startsWith("data:") ? "local" : "default"; changed = true; } }); if (changed || readStore(storageKeys.categories, []).some((folder) => folder.id === "default")) localStorage.setItem(storageKeys.categories, JSON.stringify(items)); migrateRootNotes(); return items; }
function saveCategories(folders) { localStorage.setItem(storageKeys.categories, JSON.stringify(folders)); }
function getUser() { try { return JSON.parse(localStorage.getItem(storageKeys.user)) || { nickname: "学术萌新", signature: "记录每一次深度思考，留给未来的自己。", avatarUrl: "" }; } catch { return { nickname: "学术萌新", signature: "记录每一次深度思考，留给未来的自己。", avatarUrl: "" }; } }
function fileDataUrl(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); }); }
async function uploadFile(file, purpose) { const form = new FormData(); form.append("file", file); form.append("purpose", purpose); const response = await fetch(apiUrl("/api/upload"), { method: "POST", body: form }); if (!response.ok) throw new Error("upload unavailable"); const data = await response.json(); if (!data.url) throw new Error("missing url"); return data.url; }
let currentBookId = null, editingBookId = null, pendingBookCoverFile = null, pendingBookCoverUrl = "", editingCardId = null, editingNoteId = null, pendingCardFrontImageUrl = "", pendingCardBackImageUrl = "", actionNoteId = null, actionFolderId = null, pendingAttachments = [], currentCategory = "all", pendingAvatarFile = null, pendingAvatarUrl = "", pendingNoteTemplate = { category: "basic", pattern: "blank", color: "white" }, pendingNoteCoverFile = null, pendingNoteCoverUrl = "", reviewQueue = [], reviewPosition = 0, reviewFilter = "unmastered", reviewPhase = "memory", pendingMemoryChoice = null, noteLayout = "grid", batchMode = false, selectedNoteIds = new Set(), quickImportKind = "", reviewManageMode = false, selectedReviewIds = new Set(), activeReviewCard = null, reviewStats = { total: 0, mastered: 0, difficult: new Set() }, noteSearchTimer = null, savedEditorRange = null;
const LEVEL_THRESHOLDS = [{ level: "LV.1", title: "学术萌新", min: 0, next: 100 }, { level: "LV.2", title: "知识学徒", min: 100, next: 300 }, { level: "LV.3", title: "科研助手", min: 300, next: 500 }, { level: "LV.4", title: "探索达人", min: 500, next: 1000 }, { level: "LV.5", title: "刘看山首席研究员", min: 1000, next: null }];
function getLevelInfo(points) { const value = Math.max(0, Number(points) || 0); const item = [...LEVEL_THRESHOLDS].reverse().find((level) => value >= level.min) || LEVEL_THRESHOLDS[0]; const progressPercent = item.next === null ? 100 : Math.max(0, Math.min(100, ((value - item.min) / (item.next - item.min)) * 100)); return { level: item.level, title: item.title, currentMin: item.min, nextMax: item.next, progressPercent }; }
const defaultNoteTemplate = () => ({ category: "basic", pattern: "blank", color: "white" });
function templateClasses(template = defaultNoteTemplate()) { return `template-${template.pattern || "blank"} color-${template.color || "white"}`; }
function attachmentState(files = []) { if (!files.length) return null; if (files.some((file) => file.syncStatus === "local" || String(file.url || "").startsWith("blob:"))) return { key: "local", label: "本地暂存" }; if (files.every((file) => file.syncStatus === "synced")) return { key: "synced", label: "已同步云端" }; return { key: "pending", label: "未同步" }; }
function syncBadge(state) { const badge = document.createElement("span"); badge.className = `sync-badge ${state.key}`; badge.textContent = state.label; return badge; }
function formatFileSize(bytes) { const value = Number(bytes) || 0; if (!value) return "大小未知"; if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB`; }
function getPoints() { try { return Number(JSON.parse(localStorage.getItem(storageKeys.points))?.total || 0); } catch { return 0; } }
function addPoints(amount, reason = "") { const before = getPoints(), previousLevel = getLevelInfo(before), total = before + amount, currentLevel = getLevelInfo(total); localStorage.setItem(storageKeys.points, JSON.stringify({ total, reason, lastUpdatedAt: new Date().toISOString() })); renderProfile(); updateReviewCounts(); if (previousLevel.level !== currentLevel.level) { showToast(`🎉 恭喜升级！${currentLevel.level} ${currentLevel.title}`); document.querySelectorAll(".level-card").forEach((card) => { card.classList.remove("level-flash"); void card.offsetWidth; card.classList.add("level-flash"); }); } return total; }
function renderRules() { const levels = [["LV.1 学术萌新","0–99","建立习惯"],["LV.2 跨域学徒","100–299","形成连接"],["LV.3 逻辑探索者","300–699","验证机制"],["LV.4 知识建构者","700+","迁移应用"]]; const actions = [["每日登录","+3","揉揉眼睛醒来，获得今日口粮"],["读懂新概念","+10","头顶冒出小灯泡"],["深入追问（达3次）","+5","戴上小眼镜陪你钻研"],["发现跨学科同源","+15","拿到放大镜，找到逻辑宝藏"],["存为知识卡片","+5","把知识果实放进小背包"],["复习考核掌握","+10","开心转圈圈，播撒星星"],["复习考核遗忘/模糊","+2","拍拍你，鼓励“没关系，再来一次”"],["新建笔记","+10","在纸上画下你的思考轨迹"],["整理书架/新建书籍","+5","整理书架，成就感满满"]]; const fill = (id, rows) => { const box = $(id); box.replaceChildren(); rows.forEach(([name, energy, note]) => { const row = document.createElement("div"); row.className = "table-row"; const strong = document.createElement("strong"); strong.textContent = name; const value = document.createElement("span"); value.className = "energy"; value.textContent = energy; const text = document.createElement("p"); text.textContent = note; row.append(strong, value, text); box.append(row); }); }; fill("levelTable", levels); fill("pointsTable", actions); }

function renderBooks() {
  const list = $("bookList"); list.replaceChildren();
  getBooks().forEach((book) => {
    const item = document.createElement("article"); item.className = "book-card"; const button = document.createElement("button"); button.type = "button"; button.className = "book-open";
    const cover = document.createElement("span"); cover.className = "book-cover"; if (book.coverUrl || book.localCoverDataUrl) { const image = document.createElement("img"); image.src = book.coverUrl || book.localCoverDataUrl; image.alt = ""; cover.append(attachImageFallback(image, cover, book.icon || "▤")); } else cover.textContent = book.icon || "▤";
    const copy = document.createElement("span"); copy.className = "book-copy"; const title = document.createElement("strong"); title.textContent = book.name; const count = document.createElement("small"); count.textContent = `${book.cards.length} 张卡片`; copy.append(title, count);
    const edit = document.createElement("button"); edit.type = "button"; edit.className = "book-edit"; edit.setAttribute("aria-label", `编辑${book.name}`); edit.textContent = "✎";
    button.append(cover, copy); button.addEventListener("click", () => openBook(book.id)); edit.addEventListener("click", () => openBookSheet(book.id)); item.append(button, edit); list.append(item);
  });
}
function openBook(bookId) {
  const book = getBooks().find((item) => item.id === bookId); if (!book) return; currentBookId = bookId; $("bookDetailTitle").textContent = book.name;
  const list = $("miniCardList"); list.replaceChildren();
  book.cards.forEach((card) => { const item = document.createElement("button"); item.type = "button"; item.className = "mini-card"; const title = document.createElement("strong"); title.textContent = card.front; const body = document.createElement("p"); body.textContent = card.back; const status = document.createElement("span"); status.className = `mini-card-status ${card.status}`; status.textContent = card.status === "mastered" ? "已掌握" : "待复习"; item.append(title, body, status); if (card.localOnly) { const badge = document.createElement("span"); badge.className = "local-card-badge"; badge.textContent = "[本地暂存]"; item.append(badge); } item.addEventListener("click", () => openCardSheet(card.id)); list.append(item); });
  $("bookList").hidden = true; $("newBookButton").hidden = true; $("bookDetail").hidden = false;
}
function renderNotes(query = "") {
  const normalized = query.trim().toLowerCase(); const list = $("noteList"); list.replaceChildren(); const category = getCategories().find((item) => item.id === currentCategory); const notes = getNotes().filter((note) => (currentCategory === "all" || (note.categoryId || "default") === currentCategory) && `${note.title} ${note.content}`.toLowerCase().includes(normalized));
  $("notesPageTitle").textContent = currentCategory === "all" ? "全部笔记" : category?.name || "分类笔记"; $("notesPageCount").textContent = `${notes.length} 篇笔记`;
  if (!batchMode) { const create = document.createElement("button"); create.type = "button"; create.className = "new-note-card"; create.innerHTML = "<b>＋</b><span>新建与导入</span>"; create.addEventListener("click", openNoteCreateSheet); list.append(create); }
  notes.forEach((note) => { const item = document.createElement("article"); item.className = `note-card${batchMode ? " batch-mode" : ""}${selectedNoteIds.has(note.id) ? " selected" : ""}`; item.tabIndex = 0; const attachments = note.attachments || []; const imageFile = attachments.find((file) => file.mimeType?.startsWith("image/")); const pdfFile = attachments.find((file) => file.mimeType === "application/pdf"); const state = attachmentState(attachments); const thumbnail = document.createElement("span"); thumbnail.className = `note-thumbnail${pdfFile ? " pdf" : ""}`; if (imageFile?.url) { const image = document.createElement("img"); image.src = imageFile.url; image.alt = ""; image.onerror = () => { thumbnail.replaceChildren(); thumbnail.classList.add("template-thumbnail", ...templateClasses(note.template).split(" ")); }; thumbnail.append(image); } else if (pdfFile) thumbnail.textContent = "PDF"; else thumbnail.classList.add("template-thumbnail", ...templateClasses(note.template).split(" ")); const copy = document.createElement("span"); copy.className = "note-copy"; const title = document.createElement("h3"); title.textContent = note.title; const type = document.createElement("small"); type.className = "note-type"; type.textContent = pdfFile ? `📄 PDF · ${formatFileSize(pdfFile.size)}` : imageFile ? `🖼 图片 · ${formatFileSize(imageFile.size)}` : "文本笔记"; const time = document.createElement("time"); time.textContent = new Date(note.date).toLocaleDateString("zh-CN"); copy.append(title, type, time); if (state) copy.append(syncBadge(state)); const star = document.createElement("button"); star.type = "button"; star.className = `note-star${note.starred ? " active" : ""}`; star.textContent = note.starred ? "★" : "☆"; star.setAttribute("aria-label", "收藏笔记"); star.addEventListener("click", (event) => { event.stopPropagation(); note.starred = !note.starred; saveNote(note); renderNotes($("noteSearch").value); }); const more = document.createElement("button"); more.type = "button"; more.className = "note-more"; more.textContent = "⋮"; more.setAttribute("aria-label", "更多操作"); more.addEventListener("click", (event) => { event.stopPropagation(); openNoteActionSheet(note.id); }); if (batchMode) { const select = document.createElement("span"); select.className = "note-select"; select.textContent = "✓"; item.append(select); } item.append(thumbnail, copy, star, more); item.addEventListener("click", () => { if (batchMode) { if (selectedNoteIds.has(note.id)) selectedNoteIds.delete(note.id); else selectedNoteIds.add(note.id); renderNotes($("noteSearch").value); } else openNoteSheet(note.id); }); item.addEventListener("keydown", (event) => { if (event.key === "Enter") item.click(); }); list.append(item); });
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
function closeBookSheet() { $("bookSheet").hidden = true; $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); }
function previewAttachment(file) { $("attachmentPreviewTitle").textContent = file.name; const body = $("attachmentPreviewBody"); body.replaceChildren(); if (!file.url) { showToast("该附件需要重新上传后才能预览"); return; } const viewer = document.createElement(file.mimeType === "application/pdf" ? "iframe" : "img"); viewer.src = file.url; body.append(viewer); $("attachmentPreview").hidden = false; }
function renderProfile() { const user = getUser(), points = getPoints(), cards = allReviewCards(), first = localStorage.getItem(storageKeys.firstLogin) || new Date().toISOString(); if (!localStorage.getItem(storageKeys.firstLogin)) localStorage.setItem(storageKeys.firstLogin, first); $("profileNickname").textContent = user.nickname; $("profileSignature").textContent = user.signature; $("profileAvatar").src = user.avatarUrl || ""; $("profileAvatar").hidden = !user.avatarUrl; $("avatarFallback").hidden = Boolean(user.avatarUrl); $("profilePoints").textContent = points; $("rulesPoints").textContent = points; const progress = Math.min(points, 100); $("levelProgress").style.width = `${progress}%`; $("rulesProgress").style.width = `${progress}%`; $("levelRemaining").textContent = `距离下一级还差 ${Math.max(0, 100 - points)} 能量`; $("recordMastered").textContent = cards.filter((card) => card.status === "mastered").length; $("recordUnmastered").textContent = cards.filter((card) => card.status === "unmastered").length; $("recordNotes").textContent = getNotes().length; $("recordDays").textContent = Math.max(1, Math.floor((Date.now() - new Date(first)) / 86400000) + 1); }
function openProfileSheet() { const user = getUser(); pendingAvatarFile = null; pendingAvatarUrl = user.avatarUrl || ""; $("profileNicknameInput").value = user.nickname; $("profileSignatureInput").value = user.signature; $("profileAvatarPreview").src = pendingAvatarUrl; $("profileAvatarPreview").closest(".avatar-picker").classList.toggle("has-preview", Boolean(pendingAvatarUrl)); $("sheetBackdrop").hidden = false; $("profileSheet").hidden = false; document.body.classList.add("sheet-open"); }
function closeProfileSheet() { $("profileSheet").hidden = true; $("sheetBackdrop").hidden = true; document.body.classList.remove("sheet-open"); }
function allReviewCards() { return getBooks().flatMap((book) => book.cards.map((card) => ({ ...card, bookId: book.id, bookName: book.name }))); }
function updateReviewCounts() { const cards = allReviewCards(); $("unmasteredCount").textContent = cards.filter((card) => card.status === "unmastered").length; $("masteredCount").textContent = cards.filter((card) => card.status === "mastered").length; $("allCount").textContent = cards.length; $("reviewPoints").textContent = `能量 ${getPoints()}`; }
function buildReviewQueue() { reviewQueue = allReviewCards().filter((card) => card.status === "unmastered"); reviewPosition = 0; reviewPhase = "memory"; updateReviewCounts(); updateReviewCard(); }
function setReviewPhase(phase, choice = null) { reviewPhase = phase; const answer = phase === "answer"; if (choice) pendingMemoryChoice = choice; if (!answer) pendingMemoryChoice = null; $("flashcard").classList.toggle("flipped", answer); $("memoryActions").hidden = answer; $("answerActions").hidden = !answer; const choiceLabel = { know: "认识", vague: "模糊", forgot: "忘记了" }[pendingMemoryChoice]; $("reviewHint").textContent = answer ? `你刚才选择了「${choiceLabel || "查看释义"}」。请对照释义，再决定“下一词”或“记错了”` : "瞬间想起含义，选「认识」；思考后想起含义，选「模糊」"; }
function updateReviewCard() { const card = reviewQueue[reviewPosition]; $("flashcard").classList.remove("flipped", "leaving"); setReviewPhase("memory"); if (!card) { $("flashcard").hidden = true; $("reviewComplete").hidden = false; $("reviewProgress").textContent = "已完成"; $("memoryActions").hidden = true; $("answerActions").hidden = true; $("reviewHint").hidden = true; $("reviewCompleteText").textContent = "所有待复习卡片都已掌握。"; $("pointsChip").textContent = `累计能量 ${getPoints()}`; updateReviewCounts(); return; } $("flashcard").hidden = false; $("reviewComplete").hidden = true; $("reviewHint").hidden = false; $("flashFront").textContent = card.front; $("flashBack").textContent = card.back; $("reviewProgress").textContent = `${reviewPosition + 1} / ${reviewQueue.length}`; }
function persistReviewStatus(cardRef, status) { const book = getBooks().find((item) => item.id === cardRef.bookId); const card = book?.cards.find((item) => item.id === cardRef.id); if (!card || card.status === status) return false; card.status = status; saveBook(book); return true; }
function slideToNext(mode) { const current = reviewQueue[reviewPosition]; if (!current) return; $("flashcard").classList.add("leaving"); if (mode === "master") { if (persistReviewStatus(current, "mastered")) addPoints(10, "复习掌握"); reviewQueue.splice(reviewPosition, 1); if (reviewPosition >= reviewQueue.length) reviewPosition = 0; } else { const moved = reviewQueue.splice(reviewPosition, 1)[0]; reviewQueue.push(moved); if (reviewPosition >= reviewQueue.length) reviewPosition = 0; } window.setTimeout(updateReviewCard, 240); }
function renderReviewLibrary(filter) { const cards = allReviewCards().filter((card) => filter === "all" || card.status === filter); const list = $("reviewCardList"); list.replaceChildren(); cards.forEach((card) => { const item = document.createElement("article"); item.className = "review-list-card"; const title = document.createElement("strong"); title.textContent = card.front; const status = document.createElement("span"); status.className = `mini-card-status ${card.status}`; status.textContent = card.status === "mastered" ? "已掌握" : "待复习"; const body = document.createElement("p"); body.textContent = card.back; item.append(title, status, body); if (card.status === "mastered") { const reset = document.createElement("button"); reset.type = "button"; reset.className = "review-reset"; reset.textContent = "重置为未掌握"; reset.addEventListener("click", () => { persistReviewStatus(card, "unmastered"); updateReviewCounts(); renderReviewLibrary(filter); }); item.append(reset); } list.append(item); }); }
function switchReviewFilter(filter) { reviewFilter = filter; document.querySelectorAll(".review-filter").forEach((button) => button.classList.toggle("active", button.dataset.reviewFilter === filter)); const testing = filter === "unmastered"; $("reviewTest").hidden = !testing; $("reviewLibrary").hidden = testing; if (testing) buildReviewQueue(); else { updateReviewCounts(); renderReviewLibrary(filter); } }

function removeReviewCards(ids) { const wanted = new Set(ids); getBooks().forEach((book) => { const cards = book.cards.filter((card) => !wanted.has(card.id)); if (cards.length !== book.cards.length) { book.cards = cards; saveBook(book); } }); updateReviewCounts(); renderProfile(); }
function buildReviewQueue() { const allCards = allReviewCards(); reviewQueue = allCards.filter((card) => card.status === "unmastered"); reviewPosition = 0; reviewPhase = "memory"; const masteredTotal = allCards.filter((card) => card.status === "mastered").length; reviewStats = reviewQueue.length ? { total: reviewQueue.length, mastered: 0, difficult: new Set() } : { total: masteredTotal, mastered: masteredTotal, difficult: new Set() }; saveReviewSession(); updateReviewCounts(); updateReviewCard(); }
function readReviewSession() { try { const value = JSON.parse(localStorage.getItem(storageKeys.reviewSession)); return { total: Number(value?.total) || 0, mastered: Number(value?.mastered) || 0, difficult: new Set(value?.difficult || []) }; } catch { return { total: 0, mastered: 0, difficult: new Set() }; } }
function saveReviewSession() { localStorage.setItem(storageKeys.reviewSession, JSON.stringify({ total: reviewStats.total, mastered: reviewStats.mastered, difficult: [...reviewStats.difficult] })); }
function setReviewPhase(phase, choice = null) { reviewPhase = phase; const answer = phase === "answer"; if (choice) { pendingMemoryChoice = choice; if (choice !== "know" && reviewQueue[reviewPosition]) reviewStats.difficult.add(reviewQueue[reviewPosition].id); saveReviewSession(); } if (!answer) pendingMemoryChoice = null; $("flashcard").classList.toggle("flipped", answer); $("memoryActions").hidden = answer; $("answerActions").hidden = !answer; const choiceLabel = { know: "认识", vague: "模糊", forgot: "忘记了" }[pendingMemoryChoice]; $("reviewHint").textContent = answer ? `你刚才选择了「${choiceLabel || "查看释义"}」。请对照释义，再决定“下一词”或“记错了”` : "瞬间想起含义，选「认识」；思考后想起含义，选「模糊」"; }
function updateReviewCard() { const card = reviewQueue[reviewPosition]; $("flashcard").classList.remove("flipped", "leaving"); setReviewPhase("memory"); if (!card) { const user = getUser(), avatar = localStorage.getItem("userAvatar") || user.avatarUrl || ""; $("reviewCompleteAvatar").src = avatar || "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Ccircle cx='32' cy='32' r='31' fill='%23e4f0ea'/%3E%3Cpath d='M18 41c4-13 8-19 14-19s10 6 14 19' fill='none' stroke='%23176b4d' stroke-width='4' stroke-linecap='round'/%3E%3Ccircle cx='26' cy='29' r='2' fill='%23176b4d'/%3E%3Ccircle cx='38' cy='29' r='2' fill='%23176b4d'/%3E%3C/svg%3E"; $("flashcard").hidden = true; $("reviewComplete").hidden = false; $("reviewProgress").textContent = "已完成"; $("memoryActions").hidden = true; $("answerActions").hidden = true; $("reviewHint").hidden = true; $("reviewCompleteText").textContent = `本次复习 ${reviewStats.total} 张卡片，其中已掌握 ${reviewStats.mastered} 张，模糊/遗忘 ${reviewStats.difficult.size} 张。`; $("pointsChip").textContent = `累计能量 ${getPoints()}`; updateReviewCounts(); return; } $("flashcard").hidden = false; $("reviewComplete").hidden = true; $("reviewHint").hidden = false; $("flashFront").textContent = card.front; $("flashBack").textContent = card.back; [["flashFrontImage", card.frontImageUrl], ["flashBackImage", card.backImageUrl]].forEach(([id, url]) => { $(id).src = url || ""; $(id).hidden = !url; }); $("reviewProgress").textContent = `${reviewStats.total - reviewQueue.length + 1} / ${reviewStats.total}`; }
function slideToNext(mode) { const current = reviewQueue[reviewPosition]; if (!current) return; $("flashcard").classList.add("leaving"); if (mode === "master") { if (persistReviewStatus(current, "mastered")) { addPoints(10, "复习掌握"); reviewStats.mastered += 1; } reviewQueue.splice(reviewPosition, 1); } else { reviewStats.difficult.add(current.id); const moved = reviewQueue.splice(reviewPosition, 1)[0]; reviewQueue.push(moved); } saveReviewSession(); reviewPosition = 0; window.setTimeout(updateReviewCard, 240); }
function finishReviewAnswer(action) { if (!reviewQueue[reviewPosition]) return; const confirmedMastery = action === "next" && pendingMemoryChoice === "know"; slideToNext(confirmedMastery ? "master" : "requeue"); }
function renderReviewLibrary(filter) { const cards = allReviewCards().filter((card) => filter === "all" || card.status === filter); const list = $("reviewCardList"); list.replaceChildren(); cards.forEach((card) => { const item = document.createElement("article"); item.className = `review-list-card${reviewManageMode ? " manage" : ""}${selectedReviewIds.has(card.id) ? " selected" : ""}`; const title = document.createElement("strong"); title.textContent = card.front; const status = document.createElement("span"); status.className = `mini-card-status ${card.status}`; status.textContent = card.status === "mastered" ? "已掌握" : "待复习"; const body = document.createElement("p"); body.textContent = card.back; if (reviewManageMode) { const check = document.createElement("span"); check.className = "review-check"; check.textContent = "✓"; item.append(check); item.addEventListener("click", () => { if (selectedReviewIds.has(card.id)) selectedReviewIds.delete(card.id); else selectedReviewIds.add(card.id); renderReviewLibrary(filter); }); } else { const more = document.createElement("button"); more.type = "button"; more.className = "review-more"; more.textContent = "⋮"; more.addEventListener("click", () => openReviewCardAction(card)); item.append(more); } item.append(title, status, body); list.append(item); }); }
function setReviewManageMode(enabled) { reviewManageMode = enabled; selectedReviewIds.clear(); $("reviewManageToolbar").hidden = !enabled; $("reviewEdit").textContent = enabled ? "完成" : "编辑"; $("reviewUnmasterSelected").hidden = reviewFilter !== "mastered"; if (reviewFilter === "unmastered") { $("reviewTest").hidden = enabled; $("reviewLibrary").hidden = !enabled; } renderReviewLibrary(reviewFilter); }
function openReviewCardAction(card) { activeReviewCard = card; $("reviewActionTitle").textContent = card.front; $("toggleReviewCardStatus").textContent = card.status === "mastered" ? "移出已掌握" : "标记为已掌握"; $("sheetBackdrop").hidden = false; $("reviewCardActionSheet").hidden = false; document.body.classList.add("sheet-open"); }

function noteFolderId(note) { return note.folderId ?? note.categoryId ?? null; }
function renderNotes(query = "") {
  const normalized = query.trim().toLowerCase(), list = $("noteList"), categories = getCategories(), activeFolder = categories.find((item) => item.id === currentCategory); list.replaceChildren();
  const allNotes = getNotes(); const notes = allNotes.filter((note) => { const folderMatch = currentCategory === "all" ? (normalized ? true : !noteFolderId(note)) : noteFolderId(note) === currentCategory; return folderMatch && (String(note.title || "").includes(query.trim()) || String(note.content || "").includes(query.trim()) || !normalized); });
  $("notesPageTitle").textContent = currentCategory === "all" ? "全部笔记" : activeFolder?.name || "文件夹"; $("notesPageCount").textContent = `${notes.length} 篇笔记`; $("folderBreadcrumb").hidden = currentCategory === "all"; $("folderBreadcrumbName").textContent = activeFolder?.name || "";
  if (currentCategory === "all" && !normalized && !batchMode) categories.forEach((folder) => { const card = document.createElement("article"); card.className = "folder-card"; const open = document.createElement("button"); open.type = "button"; open.className = "folder-open"; const cover = document.createElement("span"); cover.className = "folder-cover"; if (folder.coverUrl) { const image = document.createElement("img"); image.src = folder.coverUrl; image.alt = ""; cover.append(attachImageFallback(image, cover, "▱")); } else cover.textContent = "▱"; const title = document.createElement("strong"); title.textContent = folder.name; const count = document.createElement("small"); count.textContent = `${allNotes.filter((note) => noteFolderId(note) === folder.id).length} 篇笔记`; open.append(cover, title, count); open.addEventListener("click", () => { currentCategory = folder.id; renderCategories(); renderNotes($("noteSearch").value); }); const more = document.createElement("button"); more.type = "button"; more.className = "folder-more"; more.textContent = "⋮"; more.setAttribute("aria-label", `管理${folder.name}`); more.addEventListener("click", () => openFolderActionSheet(folder.id)); card.append(open, more); list.append(card); });
  if (!batchMode) { const create = document.createElement("button"); create.type = "button"; create.className = "new-note-card"; create.innerHTML = "<b>＋</b><span>新建与导入</span>"; create.addEventListener("click", openNoteCreateSheet); list.append(create); }
  notes.forEach((note) => { const item = document.createElement("article"); item.className = `note-card${batchMode ? " batch-mode" : ""}${selectedNoteIds.has(note.id) ? " selected" : ""}`; item.tabIndex = 0; const attachments = (note.attachments || []).filter((file) => file.noteId === note.id), imageFile = attachments.find((file) => file.mimeType?.startsWith("image/")), pdfFile = attachments.find((file) => file.mimeType === "application/pdf"), state = attachmentState(attachments); const thumbnail = document.createElement("span"); thumbnail.className = `note-thumbnail${pdfFile ? " pdf" : ""}`; const previewUrl = note.coverUrl || imageFile?.url; if (previewUrl) { const image = document.createElement("img"); image.src = previewUrl; image.alt = ""; image.onerror = () => { thumbnail.replaceChildren(); thumbnail.classList.add("template-thumbnail", ...templateClasses(note.template).split(" ")); }; thumbnail.append(image); } else if (pdfFile) thumbnail.textContent = "PDF"; else thumbnail.classList.add("template-thumbnail", ...templateClasses(note.template).split(" ")); const copy = document.createElement("span"); copy.className = "note-copy"; const title = document.createElement("h3"); title.textContent = note.title; const type = document.createElement("small"); type.className = "note-type"; type.textContent = pdfFile ? `📄 ${pdfFile.name} · ${formatFileSize(pdfFile.size)}` : imageFile ? `🖼 ${imageFile.name} · ${formatFileSize(imageFile.size)}` : "文本笔记"; const time = document.createElement("time"); time.textContent = new Date(note.date).toLocaleDateString("zh-CN"); copy.append(title, type, time); if (state) copy.append(syncBadge(state)); const star = document.createElement("button"); star.type = "button"; star.className = `note-star${note.starred ? " active" : ""}`; star.textContent = note.starred ? "★" : "☆"; star.addEventListener("click", (event) => { event.stopPropagation(); note.starred = !note.starred; saveNote(note); renderNotes($("noteSearch").value); }); const more = document.createElement("button"); more.type = "button"; more.className = "note-more"; more.textContent = "⋮"; more.addEventListener("click", (event) => { event.stopPropagation(); openNoteActionSheet(note.id); }); if (batchMode) { const select = document.createElement("span"); select.className = "note-select"; select.textContent = "✓"; item.append(select); } item.append(thumbnail, copy, star, more); item.addEventListener("click", () => { if (batchMode) { if (selectedNoteIds.has(note.id)) selectedNoteIds.delete(note.id); else selectedNoteIds.add(note.id); renderNotes($("noteSearch").value); } else openNoteSheet(note.id); }); list.append(item); });
  if (normalized && !notes.length) { const empty = document.createElement("div"); empty.className = "note-empty"; empty.textContent = "没有找到相关笔记"; list.append(empty); }
}

// The current five-level model supersedes the early four-level prototype above.
function renderRules() { const levels = [["LV.1 学术萌新","0–99","建立习惯"],["LV.2 知识学徒","100–299","积累知识"],["LV.3 科研助手","300–499","辅助研究"],["LV.4 探索达人","500–999","跨域探索"],["LV.5 刘看山首席研究员","1000+","持续创造"]]; const actions = [["每日登录","+3","揉揉眼睛醒来，获得今日口粮"],["读懂新概念","+10","头顶冒出小灯泡"],["深入追问（达3次）","+5","戴上小眼镜陪你钻研"],["发现跨学科同源","+15","拿到放大镜，找到逻辑宝藏"],["存为知识卡片","+5","把知识果实放进小背包"],["复习考核掌握","+10","开心转圈圈，播撒星星"],["复习考核遗忘/模糊","+2","拍拍你，鼓励“没关系，再来一次”"],["新建笔记","+10","在纸上画下你的思考轨迹"],["整理书架/新建书籍","+5","整理书架，成就感满满"]]; const fill = (id, rows) => { const box = $(id); box.replaceChildren(); rows.forEach(([name, energy, note]) => { const row = document.createElement("div"); row.className = "table-row"; const strong = document.createElement("strong"); strong.textContent = name; const value = document.createElement("span"); value.className = "energy"; value.textContent = energy; const text = document.createElement("p"); text.textContent = note; row.append(strong, value, text); box.append(row); }); }; fill("levelTable", levels); fill("pointsTable", actions); }
function renderProfile() { const user = getUser(), points = getPoints(), level = getLevelInfo(points), cards = allReviewCards(), first = localStorage.getItem(storageKeys.firstLogin) || new Date().toISOString(); if (!localStorage.getItem(storageKeys.firstLogin)) localStorage.setItem(storageKeys.firstLogin, first); $("profileNickname").textContent = user.nickname; $("profileSignature").textContent = user.signature; $("profileAvatar").src = user.avatarUrl || ""; $("profileAvatar").hidden = !user.avatarUrl; $("avatarFallback").hidden = Boolean(user.avatarUrl); $("levelLabel").textContent = `${level.level} · ${level.title}`; $("rulesLevel").textContent = `${level.level} · ${level.title}`; $("profilePoints").textContent = points; $("rulesPoints").textContent = points; $("levelProgress").style.width = `${level.progressPercent}%`; $("rulesProgress").style.width = `${level.progressPercent}%`; $("levelRemaining").textContent = level.nextMax === null ? "已达到最高等级" : `距离下一级还差 ${level.nextMax - points} 能量`; $("recordMastered").textContent = cards.filter((card) => card.status === "mastered").length; $("recordUnmastered").textContent = cards.filter((card) => card.status === "unmastered").length; $("recordNotes").textContent = getNotes().length; $("recordDays").textContent = Math.max(1, Math.floor((Date.now() - new Date(first)) / 86400000) + 1); }

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => activatePanel(tab.dataset.panel)));
document.querySelectorAll(".example-prompt").forEach((button) => button.addEventListener("click", () => {
  const target = $(button.dataset.target); target.value = button.textContent.trim(); if (button.dataset.target === "discoverText") discoverDraftText = target.value; else explainDraftText = target.value; target.focus();
}));
$("explainText").addEventListener("input", (event) => { explainDraftText = event.target.value; localStorage.setItem("draft_explain", explainDraftText); });
$("discoverText").addEventListener("input", (event) => { discoverDraftText = event.target.value; localStorage.setItem("draft_discover", discoverDraftText); });
$("manualImportText").addEventListener("input", (event) => { if (state.importPanelTarget === "discoverPanel") { discoverDraftText = event.target.value; localStorage.setItem("draft_discover", discoverDraftText); } else { explainDraftText = event.target.value; localStorage.setItem("draft_explain", explainDraftText); } });

$("profileToggle").addEventListener("click", () => {
  const profile = $("logicProfile"); profile.hidden = !profile.hidden;
  $("profileToggle").setAttribute("aria-expanded", String(!profile.hidden));
  $("profileToggle").textContent = profile.hidden ? "查看逻辑画像 ›" : "收起逻辑画像⌄";
});
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
$("historyButton").addEventListener("click", openHistory);
$("historyClose").addEventListener("click", closeHistory);
$("closeHistoryDelete").addEventListener("click", () => { $("historyDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; });
$("cancelHistoryDelete").addEventListener("click", () => { $("historyDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; });
$("confirmHistoryDelete").addEventListener("click", () => { if (pendingHistoryDeleteId) saveHistory(getHistory().filter((entry) => entry.id !== pendingHistoryDeleteId)); pendingHistoryDeleteId = null; $("historyDeleteConfirm").hidden = true; $("sheetBackdrop").hidden = true; renderHistory(); renderHistoryMemory(); showToast("历史记录已删除"); });
document.querySelectorAll(".history-filter").forEach((button) => button.addEventListener("click", () => { historyFilterType = button.dataset.historyType; renderHistory(); }));
$("clearHistory").addEventListener("click", () => { saveHistory(getHistory().filter((item) => item.type !== historyFilterType)); renderHistory(); renderHistoryMemory(); showToast(`${historyFilterType}历史记录已清空`); });
$("historyMemoryMore").addEventListener("click", openHistory);
$("clearCurrentHistory").addEventListener("click", () => { const type = historyType(); saveHistory(getHistory().filter((item) => item.type !== type)); renderHistoryMemory(); showToast("当前模式历史已清空"); });
$("backToTop").addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
window.addEventListener("scroll", () => $("backToTop").classList.toggle("visible", window.scrollY > 300), { passive: true });
$("sheetBackdrop").addEventListener("click", () => { closeMappingSheet(); closeSaveCardSheet(); closeNoteSheet(); closeCardSheet(); closeImportSheet(); closeBookSheet(); closeProfileSheet(); closeNoteActionSheet(); closeFolderActionSheet(); closeTemplateSheet(); closeNoteCreateSheet(); closeFolderSheet(); $("noteMoveSheet").hidden = true; $("batchMoveSheet").hidden = true; $("batchDeleteConfirm").hidden = true; $("cardDeleteConfirm").hidden = true; $("historyDeleteConfirm").hidden = true; $("reviewCardActionSheet").hidden = true; $("reviewDeleteConfirm").hidden = true; });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeMappingSheet(); closeSaveCardSheet(); } });
document.querySelectorAll(".bottom-tab").forEach((tab) => tab.addEventListener("click", () => activateAppPage(tab.dataset.appPage)));

$("importContentButton").addEventListener("click", () => openImportSheet("importSheet"));
$("discoverImportContentButton").addEventListener("click", () => openImportSheet("importSheet"));
document.querySelectorAll("[data-launch-import]").forEach((button) => button.addEventListener("click", () => {
  state.importPanelTarget = state.activeKnowledgePanel;
  state.importTarget = state.importPanelTarget === "discoverPanel" ? "discoverText" : "explainText";
  openImportSheet("importSheet");
  const panels = { link: "linkImportPanel", manual: "manualImportPanel", image: "imageImportPanel" };
  showImportPanel(panels[button.dataset.launchImport]);
  if (button.dataset.launchImport === "image") $("imageImportFile").click();
}));
document.querySelectorAll(".back-to-launcher").forEach((button) => button.addEventListener("click", () => showKnowledgeLauncher(state.activeKnowledgePanel)));
$("importSheetClose").addEventListener("click", closeImportSheet);
document.querySelectorAll("[data-import-panel]").forEach((button) => button.addEventListener("click", () => showImportPanel(button.dataset.importPanel)));
document.querySelectorAll(".import-back").forEach((button) => button.addEventListener("click", () => showImportPanel(null)));
$("parseLinkButton").addEventListener("click", parseImportedLink);
$("confirmManualImport").addEventListener("click", () => { const text = $("manualImportText").value.trim(); if (!text) return; placeImportedText(text, "内容已放入输入框", state.importPanelTarget); });
$("imageImportFile").addEventListener("change", (event) => importImage(event.target.files?.[0]));

$("backToShelf").addEventListener("click", () => { $("bookDetail").hidden = true; $("bookList").hidden = false; $("newBookButton").hidden = false; });
$("newBookButton").addEventListener("click", () => openBookSheet());
$("bookSheetClose").addEventListener("click", closeBookSheet); $("cancelBook").addEventListener("click", closeBookSheet);
$("bookCoverFile").addEventListener("change", async (event) => { pendingBookCoverFile = event.target.files?.[0] || null; if (!pendingBookCoverFile) return; pendingBookCoverUrl = await fileDataUrl(pendingBookCoverFile); $("bookCoverPreview").innerHTML = `<img alt="封面预览">`; $("bookCoverPreview").querySelector("img").src = pendingBookCoverUrl; });
$("saveBookButton").addEventListener("click", async () => { const name = $("bookName").value.trim(); if (!name) return; const existing = getBooks().find((item) => item.id === editingBookId); let coverUrl = existing?.coverUrl || "", localCoverDataUrl = existing?.localCoverDataUrl || ""; if (pendingBookCoverFile) { try { coverUrl = await uploadFile(pendingBookCoverFile, "book_cover"); localCoverDataUrl = ""; } catch { localCoverDataUrl = pendingBookCoverUrl; showToast("封面上传失败，已保存在当前设备"); } } saveBook({ id: editingBookId || makeId("book"), name, icon: existing?.icon || "▤", coverUrl, localCoverDataUrl, cards: existing?.cards || [] }); renderBooks(); closeBookSheet(); });
$("newCardButton").addEventListener("click", () => openCardSheet());
$("cardSheetClose").addEventListener("click", closeCardSheet);
$("cancelCard").addEventListener("click", closeCardSheet);
$("saveCard").addEventListener("click", () => { const front = $("cardFront").value.trim(), back = $("cardBack").value.trim(); if (!front || !back) return; const book = getBooks().find((item) => item.id === currentBookId); if (!book) return; const index = book.cards.findIndex((item) => item.id === editingCardId); const card = { id: editingCardId || makeId("card"), front, back, frontImageUrl: pendingCardFrontImageUrl, backImageUrl: pendingCardBackImageUrl, status: index >= 0 ? book.cards[index].status : "unmastered" }; if (index >= 0) book.cards[index] = card; else book.cards.push(card); saveBook(book); openBook(book.id); closeCardSheet(); });
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
  const existing = getNotes().find((item) => item.id === editingNoteId); const attachments = pendingAttachments.filter((file) => file.noteId === editingNoteId).map((file) => ({ ...file, noteId: editingNoteId })); const note = { id: editingNoteId, folderId: $("noteCategorySelect").value, title: title || "未命名笔记", content: body || "暂未填写正文。", coverUrl: pendingNoteCoverUrl, date: new Date().toISOString(), attachments, template: clone(pendingNoteTemplate), starred: existing?.starred || false, syncStatus: "local" }; saveNote(note); saveAttachmentDrafts(); if (!existing) addPoints(10, "新建笔记");
  try { const response = await fetch(apiUrl("/api/notes/save"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...note, syncStatus: "synced" }) }); if (!response.ok) throw new Error(); note.syncStatus = "synced"; saveNote(note); showToast("笔记已同步云端"); } catch { showToast("后端暂未连通，已保存在本机浏览器中。"); }
  renderNotes($("noteSearch").value); renderProfile(); closeNoteSheet();
});
$("noteAttachmentFile").addEventListener("change", async (event) => { const file = event.target.files?.[0]; if (!file) return; await addImportedAttachment(file); event.target.value = ""; }); // TODO: replace /api/upload and blob: URLs with permanent server URLs when storage API is ready.
$("noteCoverFile").addEventListener("change", async (event) => { const file = event.target.files?.[0]; if (!file) return; pendingNoteCoverFile = file; pendingNoteCoverUrl = await fileDataUrl(file); renderTemplateSelection(); try { pendingNoteCoverUrl = await uploadFile(file, "note_cover"); renderTemplateSelection(); showToast("封面已同步云端"); } catch { showToast("后端暂未连通，封面已保存在本机浏览器中。"); } event.target.value = ""; });
$("folderBreadcrumb").addEventListener("click", () => { currentCategory = "all"; renderCategories(); renderNotes($("noteSearch").value); });
$("deleteNote").addEventListener("click", () => { if (!editingNoteId) return; closeNoteSheet(); openNoteActionSheet(editingNoteId); $("noteActionMenu").hidden = true; $("deleteNotePanel").hidden = false; });
$("noteActionClose").addEventListener("click", closeNoteActionSheet); $("cancelNoteAction").addEventListener("click", closeNoteActionSheet);
$("renameNoteAction").addEventListener("click", () => { const note = getNotes().find((item) => item.id === actionNoteId); if (!note) return; $("renameNoteInput").value = note.title; $("noteActionMenu").hidden = true; $("renameNotePanel").hidden = false; $("renameNoteInput").focus(); });
$("moveNoteAction").addEventListener("click", () => { const list = $("noteMoveOptions"); list.replaceChildren(); getCategories().forEach((folder) => { const button = document.createElement("button"); button.type = "button"; button.textContent = folder.name; button.addEventListener("click", async () => { const note = getNotes().find((item) => item.id === actionNoteId); if (!note) return; note.folderId = folder.id; saveNote(note); try { const response = await fetch(apiUrl("/api/notes/move"), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: note.id, folderId: folder.id }) }); if (!response.ok) throw new Error(); } catch { try { const response = await fetch(apiUrl("/api/notes/save"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(note) }); if (!response.ok) throw new Error(); } catch { showToast("后端暂未连通，移动结果已保存在本机"); } } $("noteMoveSheet").hidden = true; closeNoteActionSheet(); renderNotes($("noteSearch").value); showToast(`已移动到「${folder.name}」`); }); list.append(button); }); $("noteActionSheet").hidden = true; $("noteMoveSheet").hidden = false; });
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
$("confirmDeleteFolder").addEventListener("click", async () => { if (!actionFolderId) return; const notes = getNotes(), moved = notes.filter((note) => noteFolderId(note) === actionFolderId); moved.forEach((note) => { note.folderId = null; }); localStorage.setItem(storageKeys.notes, JSON.stringify(notes)); saveCategories(getCategories().filter((folder) => folder.id !== actionFolderId)); await Promise.allSettled(moved.map((note) => fetch(apiUrl("/api/notes/move"), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: note.id, folderId: null }) }))); if (currentCategory === actionFolderId) currentCategory = "all"; renderCategories(); renderNotes($("noteSearch").value); closeFolderActionSheet(); showToast("文件夹已删除，内部笔记已移到根目录"); });
document.querySelectorAll("[data-template-category]").forEach((button) => button.addEventListener("click", () => { pendingNoteTemplate.category = button.dataset.templateCategory; renderTemplateSelection(); }));
document.querySelectorAll("[data-template-pattern]").forEach((button) => button.addEventListener("click", () => { pendingNoteTemplate.pattern = button.dataset.templatePattern; renderTemplateSelection(); }));
document.querySelectorAll("[data-template-color]").forEach((button) => button.addEventListener("click", () => { pendingNoteTemplate.color = button.dataset.templateColor; renderTemplateSelection(); }));
$("cancelTemplate").addEventListener("click", closeTemplateSheet); $("confirmTemplate").addEventListener("click", () => { const template = clone(pendingNoteTemplate); $("noteTemplateSheet").hidden = true; openNoteSheet(null, template); });
$("closeAttachmentPreview").addEventListener("click", () => { $("attachmentPreview").hidden = true; $("attachmentPreviewBody").replaceChildren(); });
$("editProfileButton").addEventListener("click", openProfileSheet); $("profileSheetClose").addEventListener("click", closeProfileSheet); $("cancelProfile").addEventListener("click", closeProfileSheet);
$("profileAvatarFile").addEventListener("change", async (event) => { pendingAvatarFile = event.target.files?.[0] || null; if (!pendingAvatarFile) return; pendingAvatarUrl = await fileDataUrl(pendingAvatarFile); $("profileAvatarPreview").src = pendingAvatarUrl; $("profileAvatarPreview").closest(".avatar-picker").classList.add("has-preview"); });
$("saveProfile").addEventListener("click", async () => { const user = { nickname: $("profileNicknameInput").value.trim() || "学术萌新", signature: $("profileSignatureInput").value.trim() || "记录每一次深度思考，留给未来的自己。", avatarUrl: pendingAvatarUrl }; if (pendingAvatarFile) { try { const form = new FormData(); form.append("avatar", pendingAvatarFile); const response = await fetch("/api/user/avatar", { method: "POST", body: form }); if (!response.ok) throw new Error(); user.avatarUrl = (await response.json()).url || pendingAvatarUrl; } catch { showToast("头像上传失败，已保存在当前设备"); } } try { const response = await fetch("/api/user/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nickname: user.nickname, signature: user.signature }) }); if (!response.ok) throw new Error(); } catch { showToast("服务器暂时不可用，资料已保存在当前设备"); } localStorage.setItem(storageKeys.user, JSON.stringify(user)); renderProfile(); closeProfileSheet(); }); // TODO: replace with authenticated user API.
$("openRulesButton").addEventListener("click", () => $("rulesPage").hidden = false); $("closeRulesButton").addEventListener("click", () => $("rulesPage").hidden = true);
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

renderBooks();
renderCategories();
renderNotes();
buildReviewQueue();
renderProfile();
renderRules();
hydrateNotesFromServer();
syncLocalCards();

$("explainButton").addEventListener("click", explain);
$("chatButton").addEventListener("click", chat);
$("discoverButton").addEventListener("click", discover);
$("chatText").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); chat(); } });
checkBackendHealth();
// Keep the back affordance in sync when a history item restores a result.
const syncHistoryBack = () => {
  const visible = !$("discoverResult").hidden || !$("explainResult").hidden;
  $("historyBackButton").hidden = !visible;
  $("historyMemory").classList.toggle("has-result-back", visible);
};
new MutationObserver(syncHistoryBack).observe($("discoverResult"), { attributes: true, attributeFilter: ["hidden"] });
new MutationObserver(syncHistoryBack).observe($("explainResult"), { attributes: true, attributeFilter: ["hidden"] });
syncHistoryBack();
