// 从真实 app.js 切出复习计划页的渲染函数来跑 —— 验的是文件里那份代码本身。
// 由 tests/test_schedule_page.py 通过 node 调起；找不到 node 时那条用例会自己 skip。
// 想手工跑：node tests/schedule_page_check.js（退出码 0 = 全过）。
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");

const cutLine = (marker) => {
  const i = src.indexOf(marker);
  if (i < 0) throw new Error("找不到 " + marker);
  return src.slice(i, src.indexOf("\n", i));
};
// 按花括号配对切出整个函数体（这些函数有单行的也有多行的，字符串里的括号要跳过）。
const cutFunc = (name) => {
  const i = src.indexOf("\nfunction " + name + "(") + 1;
  if (i < 1) throw new Error("找不到函数 " + name);
  let depth = 0, quote = null;
  for (let j = i; j < src.length; j += 1) {
    const c = src[j];
    if (quote) { if (c === "\\") j += 1; else if (c === quote) quote = null; continue; }
    if (c === '"' || c === "'" || c === "`") { quote = c; continue; }
    if (c === "{") depth += 1;
    else if (c === "}") { depth -= 1; if (depth === 0) return src.slice(i, j + 1); }
  }
  throw new Error(name + " 的花括号没配平");
};

// —— 假 DOM ——
function makeEl(tag) {
  return {
    tagName: tag, children: [], className: "", dataset: {}, hidden: false, type: "", attrs: {}, listeners: {},
    _text: "", _html: "",
    classList: { _s: new Set(), toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } },
    set textContent(v) { this._text = String(v); }, get textContent() { return this._text; },
    set innerHTML(v) { this._html = String(v); }, get innerHTML() { return this._html; },
    append(...kids) { kids.forEach((k) => this.children.push(k)); },
    replaceChildren(...kids) { this.children = kids; },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    fire(type, event = {}) { (this.listeners[type] || []).forEach((fn) => fn(event)); },
  };
}
// 按钮的**顺序以 index.html 为准**：下面这套假 DOM 直接照抄真 HTML 里的 data-schedule-filter，
// 这样「全部排在最前」才钉得住 —— 有人把顺序挪回去，最后那条断言会当场红。
const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
const htmlFilters = [...html.matchAll(/data-schedule-filter="([^"]+)"/g)].map((m) => m[1]);
const filterButtons = htmlFilters.map((value) => { const b = makeEl("button"); b.dataset.scheduleFilter = value; return b; });
const scheduleList = makeEl("div");
const document = { createElement: makeEl, querySelectorAll: (sel) => (sel === "[data-schedule-filter]" ? filterButtons : []) };
const $ = (id) => (id === "scheduleList" ? scheduleList : null);
let scheduleCards = [], scheduleFilter = "1";
const activateAppPage = () => {}; const closeSchedulePage = () => {};
const allReviewCards = () => scheduleCards;
// app.js 里那句调试 console.log 接成静音，但断言要打出来。
const console = { log: (first, ...rest) => { if (typeof first === "string" && first.startsWith("复习计划")) return; process.stdout.write([first, ...rest].map(String).join(" ") + "\n"); } };

// 直接 eval：function 声明会外泄到本作用域（renderSchedule 等直接用），const/let 不外泄 ——
// 所以那个 const（SCHEDULE_PLAN_DAYS）必须在同一次 eval 里喂进去，让函数在词法上闭包到它。
eval([
  cutLine("const SCHEDULE_PLAN_DAYS"),
  cutFunc("scheduleDayKey"),
  cutFunc("scheduleAnchor"),
  cutFunc("schedulePlanDate"),
  cutFunc("scheduleLabel"),
  cutFunc("formatRecentReview"),
  cutFunc("scheduleEntryState"),
  cutFunc("scheduleEntry"),
  cutFunc("scheduleGroup"),
  cutFunc("renderSchedule"),
].join("\n"));

function rows() {
  const out = [];
  const walk = (node, heading) => {
    if (node.hidden) return;   // 收起的那组：DOM 里还在，但用户看不到，不该被数进来
    if (node.tagName === "section") heading = node.children.find((c) => c.tagName === "h3")?.textContent;
    if (node.tagName === "button" && node.className === "schedule-item") {
      const texts = node.children.filter((c) => c.tagName !== "span").map((c) => c.textContent);
      out.push({ heading, title: texts[0], meta: texts[1], recent: texts[2], state: node.children.find((c) => c.tagName === "span")?.textContent });
      return;
    }
    (node.children || []).forEach((k) => walk(k, heading));
  };
  walk(scheduleList, null);
  return out;
}
const sections = () => scheduleList.children.filter((n) => n.tagName === "section");
const headingOf = (section) => section.children.find((c) => c.tagName === "h3");
const bodyOf = (section) => section.children.find((c) => c.className === "schedule-group-body");
// 展开所有分组，再读里面的条目 —— 收起状态下 rows() 是空的（这是设计如此）。
const expandAll = () => sections().forEach((s) => { if (bodyOf(s).hidden) headingOf(s).fire("click"); });
const titles = () => rows().map((r) => r.title);
// 展开后标题前面会变成 ▾ —— 只关心日期那一半时把这个记号剥掉再比。
const bare = (text) => String(text).replace(/^[▸▾] /, "");

let failed = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed += 1;
  console.log(`${ok ? "OK  " : "FAIL"} ${name}${ok ? "" : `\n     得到 ${JSON.stringify(got)}\n     期望 ${JSON.stringify(want)}`}`);
}
function checkTrue(name, got, hint = "") {
  if (!got) failed += 1;
  console.log(`${got ? "OK  " : "FAIL"} ${name}${got ? "" : `  ${hint}`}`);
}
// 一律用「本地中午」造日期：跨夏令时不会把某张卡甩到隔壁那一天去。
const noon = (y, m, d) => new Date(y, m - 1, d, 12, 0, 0, 0).getTime();
// 固定用 2026 年的日期（跟跑测试那天无关），这样断言可以写成字面量。
const card = (id, created) => ({ id, front: id, bookName: "神经网络", created_at: new Date(created).toISOString(), last_reviewed_at: null, review_stage: 0, next_review_due: new Date(created).toISOString() });

console.log("── 用户给的算例：9/13 建 apple、9/14 建 banana ──");
const apple = card("apple", noon(2026, 9, 13));
const banana = card("banana", noon(2026, 9, 14));
scheduleCards = [apple, banana];
// [1天] = 建卡日 +1 天
renderSchedule("1");
expandAll();
check("[1天] 的两条：apple 9/14、banana 9/15", rows().map((r) => [bare(r.heading), r.title]), [
  ["9月14日（共 1 张）", "apple"],
  ["9月15日（共 1 张）", "banana"],
]);
// [2天] = 建卡日 +2 天
renderSchedule("2");
expandAll();
check("[2天] 的两条：apple 9/15、banana 9/16", rows().map((r) => [bare(r.heading), r.title]), [
  ["9月15日（共 1 张）", "apple"],
  ["9月16日（共 1 张）", "banana"],
]);
// [4天] = 建卡日 +4 天
renderSchedule("4");
expandAll();
check("[4天] 的两条：apple 9/17、banana 9/18", rows().map((r) => [bare(r.heading), r.title]), [
  ["9月17日（共 1 张）", "apple"],
  ["9月18日（共 1 张）", "banana"],
]);
check("[4天] 里每条都写着第几天复习", rows().map((r) => r.meta), ["神经网络 · 第4天复习", "神经网络 · 第4天复习"]);

console.log("\n── 五个节点各自加几天（建卡日 9/13）──");
const NODE_DATES = { "1": "9月14日", "2": "9月15日", "4": "9月17日", "7": "9月20日", "15": "9月28日" };
scheduleCards = [apple];
Object.entries(NODE_DATES).forEach(([tab, date]) => {
  renderSchedule(tab);
  check(`[${tab}天] 里 apple 落在 ${date}`, headingOf(sections()[0]).textContent, `▸ ${date}（共 1 张）`);
});
checkTrue("节点表只有 1/2/4/7/15 这五个", ["1", "2", "4", "7", "15"].every((d) => { renderSchedule(d); return sections().length === 1; }), "");
renderSchedule("all"); expandAll();
check("五个节点就是第 1/2/4/7/15 天复习", rows().map((r) => r.meta.replace("神经网络 · ", "")), ["第1天复习", "第2天复习", "第4天复习", "第7天复习", "第15天复习"]);

console.log("\n── 验收②：点 [2天] 看到的是未来日期分组，不是「只有今天」──");
scheduleCards = [card("a", noon(2026, 9, 13)), card("b", noon(2026, 9, 13)), card("c", noon(2026, 9, 20))];
renderSchedule("2");
check("三个日期分组，按日期升序", sections().map((s) => headingOf(s).textContent), [
  "▸ 9月15日（共 2 张）", "▸ 9月22日（共 1 张）",
]);
checkTrue("没有任何一组是「今天/明天」这种相对说法", sections().every((s) => /^\▸ \d+月\d+日（共 \d+ 张）$/.test(headingOf(s).textContent)), sections().map((s) => headingOf(s).textContent).join(" / "));
check("同一天的两张合并成一组并给出张数", headingOf(sections()[0]).textContent.endsWith("（共 2 张）"), true);
expandAll();
check("展开后同一天的卡片都看得到", titles(), ["a", "b", "c"]);

console.log("\n── 验收③：默认只露日期卡片，点日期才展开 ──");
renderSchedule("2");
check("默认全部收起", sections().map((s) => bodyOf(s).hidden), [true, true]);
check("收起时一条具体的卡都看不到", rows().length, 0);
checkTrue("标题带 ▸ 提示可展开", sections().every((s) => headingOf(s).textContent.startsWith("▸ ")), "");
checkTrue("标题挂了 role=button 和 aria-expanded=false", sections().every((s) => headingOf(s).attrs.role === "button" && headingOf(s).attrs["aria-expanded"] === "false"), "");
headingOf(sections()[0]).fire("click");
check("点一下展开那一组", bodyOf(sections()[0]).hidden, false);
check("展开后看到卡片名", titles(), ["a", "b"]);
checkTrue("标题变成 ▾", headingOf(sections()[0]).textContent.startsWith("▾ "), headingOf(sections()[0]).textContent);
check("没点的那组还收着", bodyOf(sections()[1]).hidden, true);
headingOf(sections()[0]).fire("click");
check("再点一下收回去", rows().length, 0);
headingOf(sections()[1]).fire("keydown", { key: "Enter", preventDefault() {} });
check("回车也能展开", bodyOf(sections()[1]).hidden, false);
headingOf(sections()[1]).fire("keydown", { key: " ", preventDefault() {} });
check("空格键也能收起", bodyOf(sections()[1]).hidden, true);

console.log("\n── 计划表：过去的节点照样列出来，标已完成 / 已逾期 ──");
const longAgo = card("老卡", noon(2020, 1, 1));
scheduleCards = [longAgo];
renderSchedule("1");
expandAll();
check("2020 年建的老卡，第 1 天节点照常显示", bare(headingOf(sections()[0]).textContent), "1月2日（共 1 张）");
check("没复习过、日期早过去了 → 已逾期", rows()[0].state, "已逾期");
checkTrue("没有任何一条带「今天/明天」", !/今天|明天/.test(rows()[0].heading), rows()[0].heading);
scheduleCards = [{ ...longAgo, review_stage: 1 }];
renderSchedule("1");
expandAll();
check("复习过 1 次 → 第 1 天节点标已完成", rows()[0].state, "✓ 已完成");
scheduleCards = [{ ...longAgo, review_stage: 1 }];
renderSchedule("2");
expandAll();
check("第 2 天节点还没走完 → 还是已逾期", rows()[0].state, "已逾期");
scheduleCards = [{ ...longAgo, review_stage: 3 }];
renderSchedule("4");
expandAll();
check("复习过 3 次 → 第 4 天（第 3 个节点）已完成", rows()[0].state, "✓ 已完成");

console.log("\n── 兼容已复习卡片：锚点在建卡日，复习不会把计划日期挪走 ──");
const reviewed = { ...apple, review_stage: 1, last_reviewed_at: new Date(noon(2026, 9, 14)).toISOString() };
scheduleCards = [reviewed];
renderSchedule("2");
check("复习完之后 [2天] 的日期还是 9/15（不是被 last_reviewed_at 推到 9/16）", headingOf(sections()[0]).textContent, "▸ 9月15日（共 1 张）");
scheduleCards = [{ ...apple, created_at: undefined, createdAt: undefined, next_review_due: new Date(noon(2026, 9, 13)).toISOString() }];
renderSchedule("1");
check("没有 created_at 的卡退回 next_review_due 当锚点，不至于飘到今天", headingOf(sections()[0]).textContent, "▸ 9月14日（共 1 张）");
scheduleCards = [{ ...apple, created_at: undefined, createdAt: undefined, created: undefined, next_review_due: undefined, last_reviewed_at: undefined }];
renderSchedule("1");
checkTrue("什么时间都没有才落回「今天起算」（不至于不显示）", sections().length === 1, "");

console.log("\n── 「全部」：五个节点汇总，按日期排 ──");
scheduleCards = [apple];
renderSchedule("all");
check("apple 的五个节点落在 9/14、9/15、9/17、9/20、9/28", sections().map((s) => headingOf(s).textContent), [
  "▸ 9月14日（共 1 张）", "▸ 9月15日（共 1 张）", "▸ 9月17日（共 1 张）", "▸ 9月20日（共 1 张）", "▸ 9月28日（共 1 张）",
]);
check("「全部」也是默认收起的", sections().map((s) => bodyOf(s).hidden), [true, true, true, true, true]);
expandAll();
check("「全部」里每张卡出现 5 次（五个节点各一次）", titles(), ["apple", "apple", "apple", "apple", "apple"]);

console.log("\n── 筛选按钮高亮与空态 ──");
scheduleCards = [];
renderSchedule("2");
check("没有卡时给一句空态", /新建一张卡/.test(scheduleList.children[0].textContent), true);
check("空态里列出五个节点", /第 1 \/ 2 \/ 4 \/ 7 \/ 15 天/.test(scheduleList.children[0].textContent), true);
check("只有被选中的那个栏高亮", filterButtons.filter((b) => b.classList.contains("active")).map((b) => b.dataset.scheduleFilter), ["2"]);
renderSchedule("all");
check("换栏后高亮跟着走", filterButtons.filter((b) => b.classList.contains("active")).map((b) => b.dataset.scheduleFilter), ["all"]);
check("「全部」空了也不复用分节点那句空态", /还没有卡片哦/.test(scheduleList.children[0].textContent), true);
check("按钮顺序照 index.html：全部排最前", htmlFilters, ["all", "1", "2", "4", "7", "15"]);
checkTrue("六个按钮都在（全部 / 1 / 2 / 4 / 7 / 15）", filterButtons.length === 6, String(filterButtons.length));

console.log(failed ? `\n${failed} 项不符 ✗` : "\n全部通过 ✓");
process.exit(failed ? 1 : 0);
