const state = { explainSessionId: null, discoverSessionId: null };

const $ = (id) => document.getElementById(id);
const profileLabels = {
  system_closure: "系统封闭性",
  causal_chain_length: "因果链长度",
  negative_feedback_strength: "负反馈强度",
  randomness_entropy: "随机性 / 熵",
  zero_sum_resource_level: "资源零和性",
};

function setLoading(active, text = "正在分析…") {
  $("loadingText").textContent = text;
  $("loading").hidden = !active;
  document.querySelectorAll("button").forEach((button) => { button.disabled = active; });
}

function showError(id, message) {
  const element = $(id);
  element.textContent = message || "请求失败，请稍后重试。";
  element.hidden = !message;
}

async function request(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const detail = data.error?.message || (typeof data.detail === "string" ? data.detail : "服务暂时不可用");
    throw new Error(`${detail}（HTTP ${response.status}）`);
  }
  return data;
}

function renderRichText(element, content) {
  const escape = (value) => value.replace(/[&<>"']/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
  const lines = String(content || "").replace(/```(?:markdown|text)?/gi, "").replace(/```/g, "").split(/\r?\n/);
  const html = [];
  let listOpen = false;
  const closeList = () => { if (listOpen) { html.push("</ul>"); listOpen = false; } };
  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line || /^[-*_]{3,}$/.test(line)) { closeList(); return; }
    const inline = escape(line).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/__(.+?)__/g, "<strong>$1</strong>");
    if (/^#{1,3}\s+/.test(line)) { closeList(); html.push(`<h4>${inline.replace(/^#{1,3}\s+/, "")}</h4>`); return; }
    if (/^[-*•]\s+/.test(line)) { if (!listOpen) { html.push("<ul>"); listOpen = true; } html.push(`<li>${inline.replace(/^[-*•]\s+/, "")}</li>`); return; }
    closeList(); html.push(`<p>${inline}</p>`);
  });
  closeList(); element.innerHTML = html.join("");
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
  $("logicProfile").hidden = false;
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
}

async function explain() {
  const text = $("explainText").value.trim();
  showError("explainError", "");
  if (!text) { showError("explainError", "请先输入需要解释的内容。"); return; }
  setLoading(true, "正在提取逻辑结构…");
  try {
    const data = await request("/api/explain", { text });
    state.explainSessionId = data.session_id;
    $("sessionStatus").textContent = `读懂会话 ${state.explainSessionId.slice(0, 8)}`;
    $("conceptName").textContent = data.concept?.name || "分析结果";
    renderRichText($("explanation"), data.explanation || "暂时没有生成解释。");
    renderProfile(data.logic_profile);
    $("explainResult").hidden = false;
    $("followUp").hidden = false;
    if ((data.explanation || "").includes("LLM 调用失败") || (data.explanation || "").includes("无法完成特征提取")) {
      showError("explainError", "解释服务当前无法连接模型后端，请确认 LLM 网关已经启动。");
    }
  } catch (error) { showError("explainError", error.message); }
  finally { setLoading(false); }
}

async function chat() {
  const message = $("chatText").value.trim();
  showError("chatError", "");
  if (!state.explainSessionId) { showError("chatError", "请先完成一次“读懂它”。"); return; }
  if (!message) { showError("chatError", "请输入你的问题。"); return; }
  addMessage("user", message); $("chatText").value = ""; setLoading(true, "正在结合上下文回答…");
  try {
    const data = await request("/api/chat", { session_id: state.explainSessionId, message });
    addMessage("assistant", data.answer || "暂时没有生成回答。");
  } catch (error) { showError("chatError", error.message); }
  finally { setLoading(false); }
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

function renderLearningReport(card, report) {
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
      const block = document.createElement("div"); block.className = "lesson";
      const title = document.createElement("strong"); title.textContent = `${index + 1}. ${lesson.title}`;
      const explanation = document.createElement("p"); explanation.textContent = lesson.explanation;
      const connection = document.createElement("p"); connection.className = "lesson-bridge"; connection.textContent = `和你已知内容的连接：${lesson.connection_to_source}`;
      const example = document.createElement("p"); example.textContent = `例子：${lesson.example}`;
      const check = document.createElement("p"); check.className = "lesson-check"; check.textContent = `学完自测：${lesson.check_question}`;
      block.append(title, explanation, connection, example, check);
      const answer = document.createElement("button"); answer.type = "button"; answer.className = "quiz-answer-button"; answer.textContent = "查看答案";
      const answerText = document.createElement("p"); answerText.className = "quiz-answer"; answerText.hidden = true;
      answerText.textContent = `答案：${lesson.check_answer || "请先用自己的话回答，再对照本节解释和例子核对。"}`;
      answer.addEventListener("click", () => { answerText.hidden = !answerText.hidden; answer.textContent = answerText.hidden ? "查看答案" : "收起答案"; });
      block.append(answer, answerText);
      section.append(block);
    });
    card.append(section);
  }

  const transferGrid = document.createElement("div"); transferGrid.className = "transfer-grid";
  appendListSection(transferGrid, "可以带过去的直觉", report.transferable_knowledge, "transferable");
  appendListSection(transferGrid, "进入第二领域必须新学", report.new_knowledge, "new-knowledge");
  if (transferGrid.childElementCount) card.append(transferGrid);
  appendListSection(card, "理解检查", report.understanding_checks, "understanding-checks");

  if (report.dimension_comparisons?.length) {
    const section = document.createElement("section"); section.className = "report-section";
    const heading = document.createElement("h4"); heading.textContent = "五维结构证据"; section.append(heading);
    const dimensions = document.createElement("div"); dimensions.className = "dimension-list";
    report.dimension_comparisons.forEach((dimension) => {
      const row = document.createElement("div"); row.className = "dimension-row";
      const name = document.createElement("strong"); name.textContent = profileLabels[dimension.dimension] || dimension.dimension;
      const values = document.createElement("span");
      values.textContent = `原概念 ${Math.round(dimension.source_value * 100)} · 候选 ${Math.round(dimension.candidate_value * 100)}`;
      const reason = document.createElement("p"); reason.textContent = dimension.plain_language_reason;
      row.append(name, values, reason); dimensions.append(row);
    });
    section.append(dimensions); card.append(section);
  }

  if (report.mapping_evidence?.length) {
    const section = document.createElement("section"); section.className = "report-section";
    const heading = document.createElement("h4"); heading.textContent = "术语与机制角色对照"; section.append(heading);
    const mappings = document.createElement("div"); mappings.className = "mapping-evidence";
    report.mapping_evidence.forEach((mapping) => {
      const row = document.createElement("div"); row.className = "mapping-row";
      const pair = document.createElement("strong"); pair.textContent = `${mapping.source_term} ↔ ${mapping.target_term}`;
      const roles = document.createElement("p"); roles.textContent = `原概念角色：${mapping.source_role}；候选角色：${mapping.target_role}`;
      const reason = document.createElement("p"); reason.textContent = `对应依据：${mapping.correspondence_reason}`;
      row.append(pair, roles, reason);
      if (mapping.limitations?.length) {
        const limits = document.createElement("p"); limits.className = "mapping-limit";
        limits.textContent = `局限：${mapping.limitations.join("；")}`; row.append(limits);
      }
      if (mapping.evidence_refs?.length) {
        const refs = document.createElement("small"); refs.textContent = `依据编号：${mapping.evidence_refs.join("、")}`; row.append(refs);
      }
      mappings.append(row);
    });
    section.append(mappings); card.append(section);
  }

  const boundaryGrid = document.createElement("div"); boundaryGrid.className = "boundary-grid";
  appendListSection(boundaryGrid, "成立条件", report.valid_conditions, "conditions");
  appendListSection(boundaryGrid, "关键差异", report.known_differences, "differences");
  appendListSection(boundaryGrid, "失效边界", report.failure_boundaries, "boundaries");
  appendListSection(boundaryGrid, "不能这样说", report.prohibited_claims, "prohibited");
  if (boundaryGrid.childElementCount) card.append(boundaryGrid);

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
    section.append(list); card.append(section);
  }
  appendListSection(card, "下一步怎么学", report.learning_next_steps, "next-steps");
  if (report.recommended_books?.length) {
    const section = document.createElement("section"); section.className = "report-section books";
    const heading = document.createElement("h4"); heading.textContent = "推荐书籍"; section.append(heading);
    const list = document.createElement("ul");
    report.recommended_books.forEach((book) => { const item = document.createElement("li"); item.textContent = `${book.title}${book.author ? ` · ${book.author}` : ""}：${book.reason}${book.scope ? `（${book.scope}）` : ""}`; list.append(item); });
    section.append(list); card.append(section);
  }
}

function renderCandidates(data) {
  const container = $("candidateList"); container.replaceChildren();
  const retrievalScores = data.retrieval_scores || {};
  const records = data.candidates?.length ? data.candidates : (data.results || []).map((candidate) => ({ candidate }));
  records.forEach((record, index) => {
    const candidate = record.candidate || record;
    const id = candidate.id || candidate.candidate_id || "未知候选";
    const homology = record.homonomy_score ?? candidate.score;
    const retrieval = candidate.retrieval_score ?? retrievalScores[id];
    const mappingResult = record.mapping || data.mappings?.find((item) => item.candidate_id === id);
    const mapping = mappingResult?.mapping || {};
    const critique = record.critique || data.critiques?.find((item) => item.candidate_id === id);
    const report = record.learning_report || data.learning_reports?.find((item) => item.candidate_id === id);
    const reliable = report?.verdict === "RELIABLE_WITH_LIMITS";
    const reliabilityLabel = report ? (verdictLabels[report.verdict] || "待核验") : "无证据报告";
    const card = document.createElement("details"); card.className = "candidate"; card.open = index === 0;
    const summary = document.createElement("summary"); summary.className = "candidate-summary";
    const head = document.createElement("div"); head.className = "candidate-head";
    const title = document.createElement("h3"); title.textContent = candidate.concept?.name || candidate.concept || id;
    const badge = document.createElement("span"); badge.className = `reliability ${reliable ? "yes" : ""}`; badge.textContent = reliabilityLabel;
    head.append(title, badge);
    const domain = document.createElement("p"); domain.className = "critique"; domain.textContent = `学科：${candidate.domain || "未标注"}`;
    const scores = document.createElement("div"); scores.className = "scores";
    [["检索相关性", retrieval], ["逻辑同源度", homology]].forEach(([label, value]) => {
      const score = document.createElement("div"); score.className = "score";
      const name = document.createElement("span"); name.textContent = label;
      const strong = document.createElement("strong"); strong.textContent = valueOrUnavailable(value);
      score.append(name, strong); scores.append(score);
    });
    summary.append(head, domain, scores); card.append(summary);
    const body = document.createElement("div"); body.className = "candidate-content";
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
    if (report) renderLearningReport(body, report);
    if (report) {
      const candidateName = candidate.concept?.name || candidate.concept || id;
      const ask = document.createElement("button"); ask.type = "button"; ask.className = "secondary candidate-ask"; ask.textContent = `询问关于「${candidateName}」的问题`;
      const chatBox = document.createElement("div"); chatBox.className = "candidate-chat"; chatBox.hidden = true;
      const row = document.createElement("div"); row.className = "chat-row";
      const input = document.createElement("input"); input.type = "text"; input.placeholder = "输入你没看懂的地方，例如：这个机制为什么会稳定？"; input.setAttribute("aria-label", `询问关于${candidateName}的问题`);
      const send = document.createElement("button"); send.type = "button"; send.className = "secondary"; send.textContent = "发送";
      const conversation = document.createElement("div"); conversation.className = "conversation"; conversation.setAttribute("aria-live", "polite");
      const submit = async () => {
        const question = input.value.trim();
        if (!question) {
          const errorMessage = document.createElement("div"); errorMessage.className = "message chat-error"; errorMessage.textContent = "请先输入一个具体问题。"; conversation.append(errorMessage); input.focus(); return;
        }
        if (!state.discoverSessionId) {
          const errorMessage = document.createElement("div"); errorMessage.className = "message chat-error"; errorMessage.textContent = "当前会话已失效，请重新执行一次“发现同源”。"; conversation.append(errorMessage); return;
        }
        const message = `关于「${candidateName}」：${question}`;
        const userMessage = document.createElement("div"); userMessage.className = "message user"; userMessage.textContent = message; conversation.append(userMessage);
        input.value = ""; setLoading(true, "正在结合这份同源报告回答…");
        try {
          const data = await request("/api/chat", { session_id: state.discoverSessionId, message });
          const answerMessage = document.createElement("div"); answerMessage.className = "message assistant"; renderRichText(answerMessage, data.answer || "暂时没有生成回答。"); conversation.append(answerMessage);
        } catch (error) {
          const errorMessage = document.createElement("div"); errorMessage.className = "message chat-error"; errorMessage.textContent = error.message; conversation.append(errorMessage);
        } finally { setLoading(false); }
      };
      send.addEventListener("click", submit);
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); submit(); } });
      ask.addEventListener("click", () => { chatBox.hidden = !chatBox.hidden; ask.textContent = chatBox.hidden ? `询问关于「${candidateName}」的问题` : "收起对话"; if (!chatBox.hidden) input.focus(); });
      row.append(input, send); chatBox.append(row, conversation); body.append(ask, chatBox);
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
  setLoading(true, "正在检索并验证候选…");
  try {
    const payload = { text, top_k: 5 };
    const data = await request("/api/discover", payload);
    state.discoverSessionId = data.session_id;
    $("sessionStatus").textContent = `发现会话 ${state.discoverSessionId.slice(0, 8)}`;
    renderRichText($("discoverReport"), data.report || "同源分析已完成。");
    renderCandidates(data); $("discoverResult").hidden = false;
    if ((data.report || "").includes("LLM 调用失败") || (data.report || "").includes("无法完成特征提取")) {
      showError("discoverError", "同源分析当前无法连接模型后端，请确认 LLM 网关已经启动。");
    }
  } catch (error) { showError("discoverError", error.message); }
  finally { setLoading(false); }
}

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((item) => { const active = item === tab; item.classList.toggle("active", active); item.setAttribute("aria-selected", String(active)); });
  ["explainPanel", "discoverPanel"].forEach((id) => { $(id).hidden = id !== tab.dataset.panel; });
}));

$("explainButton").addEventListener("click", explain);
$("chatButton").addEventListener("click", chat);
$("discoverButton").addEventListener("click", discover);
$("chatText").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); chat(); } });
