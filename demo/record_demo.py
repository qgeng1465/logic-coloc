#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""录制 logic-coloc 线上 demo。整段连续录制成 webm，同时在 steps.jsonl 里打时间戳，
后处理脚本 edit_demo.py 按时间戳剪掉/加速等待段。

流程: 首页 → 读懂它(LLM,加速) → 存为卡片 → 跨学科理解(LLM,加速) → 知乎搜索
      → 看山桌宠 → 复习(掌握+10能量,看山庆祝) → 回首页收尾

用法:
    pip install playwright && playwright install chromium
    LC_DEMO_BASE=https://你的域名 python3 record_demo.py
录出来的 webm 路径写进同目录 raw.video.path，交给 edit_demo.py 剪。
"""
import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get(
    "LC_DEMO_BASE",
    "https://logic-coloc-302528-11-1473737765.sh.run.tcloudbase.com",
).rstrip("/")
OUT = Path(__file__).resolve().parent
OUT.mkdir(exist_ok=True)
STEPS = OUT / "steps.jsonl"
TEXT_EXPLAIN = (
    "免疫系统的负反馈调节：当病原体被清除后，调节性T细胞会抑制效应T细胞继续增殖，"
    "防止免疫反应过度而损伤自身组织。"
)
# S2 的输入必须能过检索的确定性锚点闸门（rag/retriever.py 的 MIN_RETRIEVAL_SCORE）。
# 原先用的「服务端熔断器…」实测线上返回 0 候选（code=0、无报错、约 8s 结束），
# 结果面板会正常显示但没有候选卡，录制脚本等候选出现就会一直等到超时。
# 「排队系统」实测 3 个候选全部过阈值且有术语映射，且与 S1 的免疫文本跨域。
TEXT_DISCOVER = (
    "排队系统：到达率超过服务能力时队列不断变长，"
    "系统通过增加服务窗口或限制进入来把队长拉回稳定区间。"
)
TEXT_ZHIHU = "负反馈 为什么能让系统稳定"

# 合成光标：跟随 mousemove，点击时放大，录出来的视频才有“手”。
CURSOR_JS = """
(() => {
  const c = document.createElement('div');
  c.id = '__demo_cursor';
  c.style.cssText = [
    'position:fixed', 'left:-40px', 'top:-40px', 'width:22px', 'height:22px',
    'border-radius:50%', 'background:rgba(255,96,64,.85)', 'border:2.5px solid #fff',
    'box-shadow:0 1px 6px rgba(0,0,0,.35)', 'pointer-events:none',
    'z-index:2147483647', 'transform:translate(-50%,-50%) scale(1)',
    'transition:left .16s ease-out, top .16s ease-out, transform .1s ease-out',
  ].join(';');
  const ring = document.createElement('div');
  ring.style.cssText = [
    'position:fixed', 'left:-40px', 'top:-40px', 'width:44px', 'height:44px',
    'border-radius:50%', 'border:3px solid rgba(255,96,64,.55)', 'pointer-events:none',
    'z-index:2147483646', 'transform:translate(-50%,-50%) scale(.4)', 'opacity:0',
    'transition:left .16s ease-out, top .16s ease-out, transform .25s ease-out, opacity .25s',
  ].join(';');
  const add = () => {
    if (!document.getElementById('__demo_cursor')) {
      document.body.appendChild(c);
      document.body.appendChild(ring);
    }
  };
  if (document.body) add(); else document.addEventListener('DOMContentLoaded', add);
  document.addEventListener('mousemove', (e) => {
    c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px';
    ring.style.left = e.clientX + 'px'; ring.style.top = e.clientY + 'px';
  }, true);
  document.addEventListener('mousedown', () => { c.style.transform = 'translate(-50%,-50%) scale(.72)'; }, true);
  document.addEventListener('mouseup', () => {
    c.style.transform = 'translate(-50%,-50%) scale(1)';
    ring.style.opacity = '1'; ring.style.transform = 'translate(-50%,-50%) scale(1.15)';
    setTimeout(() => { ring.style.opacity = '0'; ring.style.transform = 'translate(-50%,-50%) scale(.4)'; }, 240);
  }, true);
})();
"""

marks = []

# 当前 page，给 mark() 做存活探测用。
PAGE: dict = {"p": None}


class TakeAborted(RuntimeError):
    """这一条录废了，直接放弃重录 —— 别剪出一条「每一下都点了但页面没反应」的片子。"""


def assert_alive() -> None:
    """后端实例重启会丢掉内存里的匿名账号，前端随即弹「连接已中断，请刷新页面重试」。
    之后所有点击都会走 JS 兜底勉强成功，但页面毫无反应，录出来的是废片。"""
    page = PAGE["p"]
    if page is None:
        return
    try:
        disconnected = page.evaluate(
            "() => (document.body ? document.body.innerText : '').includes('连接已中断')"
        )
    except Exception:
        return
    if disconnected:
        raise TakeAborted("页面出现「连接已中断」，本条作废")


def mark(name: str, accel: bool = False) -> None:
    assert_alive()
    marks.append({"name": name, "t": round(time.time(), 3), "accel": accel})
    STEPS.write_text(json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[mark] {name} accel={accel}", flush=True)


def hold(page, ms: int) -> None:
    page.wait_for_timeout(ms)


def wait_llm(page, ok_expr: str, error_sel: str, timeout_ms: int = 300000) -> None:
    """等 LLM 结果：ok_expr 为真或错误条出现非空文本都算结束。"""
    page.wait_for_function(
        """([ok, errSel]) => {
          const r = eval(ok);
          const e = document.querySelector(errSel);
          return r || (e && !e.hidden && e.textContent.trim().length > 0);
        }""",
        arg=[ok_expr, error_sel],
        timeout=timeout_ms,
    )


def type_into(page, selector: str, text: str, delay: int = 16) -> None:
    page.fill(selector, "")
    page.type(selector, text, delay=delay)


def tap(page, selector: str, timeout_ms: int = 5000) -> None:
    """点一个选择器；被顶栏遮住/滚出视口时先用 JS 兜底触发，保证 demo 流程不因可见性判死。"""
    try:
        page.click(selector, timeout=timeout_ms)
    except Exception:
        try:
            page.hover(selector, timeout=2000)
        except Exception:
            pass
        page.evaluate(f"document.querySelector({json.dumps(selector)}).click()")
    page.wait_for_timeout(250)


def main() -> None:
    marks.clear()  # 重录时不能把上一条的打点带上
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb"])
        ctx = browser.new_context(
            viewport={"width": 430, "height": 932},
            device_scale_factor=1,
            record_video_dir=str(OUT),
            record_video_size={"width": 430, "height": 932},
        )
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        PAGE["p"] = page
        mark("v0_start")

        # ---- S0 首页（顶栏看山会循环打招呼） ----
        mark("s0_load")
        # CloudBase 测试域名有一页「风险提醒/确定访问」人工确认闸：首次用浏览器访问会先落到
        # 那一页，点「确定访问」设 cookie（cloudbase_confirm_domain_access）后才进真应用。
        # 发现风险页就点掉它，然后等应用骨架出现。
        page.goto(BASE, wait_until="domcontentloaded", timeout=45000)
        if page.title().startswith("风险") and page.locator(".bottom-nav").count() == 0:
            page.wait_for_selector("button:has-text('确定访问')", timeout=20000)
            page.click("button:has-text('确定访问')")
        page.wait_for_selector(".bottom-nav", timeout=45000)
        hold(page, 5200)

        # ---- S1 读懂它：手动创建 → 输入 → LLM（加速）→ 结果 → 存为卡片 ----
        mark("s1_tab")
        tap(page, "button.tab[data-panel=\"explainPanel\"]")
        hold(page, 1000)
        tap(page, "[data-launch-import=\"manual\"]")
        page.wait_for_selector("#manualImportPanel:not([hidden])", timeout=8000)
        hold(page, 600)
        mark("s1_type")
        type_into(page, "#manualImportText", TEXT_EXPLAIN)
        hold(page, 400)
        tap(page, "#confirmManualImport")
        page.wait_for_selector("#explainPanel:not([hidden])", timeout=8000)
        hold(page, 900)
        mark("s1_wait", accel=True)
        tap(page, "#explainButton")
        wait_llm(page, "document.querySelector('#explainResult') && !document.querySelector('#explainResult').hidden",
                 "#explainError")
        mark("s1_show")
        hold(page, 2000)
        page.evaluate("document.querySelector('#explainResult').scrollIntoView({behavior:'smooth',block:'start'})")
        hold(page, 2400)
        # 展开详细回答
        page.evaluate("""() => {
          const d = document.querySelector('#answerDisclosure');
          if (d && !d.open) { d.open = true; d.scrollIntoView({behavior:'smooth',block:'center'}); }
        }""")
        hold(page, 2600)
        # 逻辑画像
        page.evaluate("document.querySelector('#profileToggle').scrollIntoView({behavior:'smooth',block:'center'})")
        hold(page, 700)
        tap(page, "#profileToggle")
        hold(page, 2200)
        # 存为卡片（首次无书架 → 走「新建书本」→ 自动存入）
        mark("s1_save")
        page.evaluate("document.querySelector('#saveCardButton').scrollIntoView({behavior:'smooth',block:'center'})")
        hold(page, 700)
        tap(page, "#saveCardButton")
        page.wait_for_timeout(1200)
        book_sheet = page.evaluate("!document.querySelector('#bookSheet').hidden")
        if book_sheet:
            type_into(page, "#bookName", "跨学科机制", delay=28)
            hold(page, 500)
            tap(page, "#saveBookButton")
        else:
            tap(page, "#confirmSaveCard")
        page.wait_for_timeout(2400)  # toast「已存入…」
        mark("s1_saved")
        hold(page, 800)
        # 回到导入方式
        tap(page, "#backToHome")
        hold(page, 1200)

        # ---- S2 跨学科理解：输入熔断器 → LLM（加速）→ 同源结果 + 术语对照 ----
        mark("s2_tab")
        tap(page, "button.tab[data-panel=\"discoverPanel\"]")
        hold(page, 1000)
        tap(page, "[data-launch-import=\"manual\"]")
        page.wait_for_selector("#manualImportPanel:not([hidden])", timeout=8000)
        hold(page, 500)
        mark("s2_type")
        type_into(page, "#manualImportText", TEXT_DISCOVER)
        hold(page, 400)
        tap(page, "#confirmManualImport")
        page.wait_for_selector("#discoverPanel:not([hidden])", timeout=8000)
        hold(page, 900)
        mark("s2_wait", accel=True)
        tap(page, "#discoverButton")
        # 只等「结果面板或其错误条出现」就够。不要要求候选列表非空：检索闸门按
        # 确定性词面锚点放行，某些输入会合法地返回 0 候选（结果面板照样会显示），
        # 那样的条件会一直等到超时。
        wait_llm(page, "document.querySelector('#discoverResult') && !document.querySelector('#discoverResult').hidden",
                 "#discoverError", timeout_ms=480000)
        mark("s2_show")
        hold(page, 2200)
        page.evaluate("document.querySelector('#discoverReport').scrollIntoView({behavior:'smooth',block:'start'})")
        hold(page, 2400)
        page.evaluate("window.scrollBy({top:760,behavior:'smooth'})")
        hold(page, 2400)
        page.evaluate("window.scrollBy({top:760,behavior:'smooth'})")
        hold(page, 2200)
        # 打开第一张候选卡的术语对照
        mark("s2_mapping")
        opened = page.evaluate("""() => {
          const btn = [...document.querySelectorAll('#candidateList button')]
            .find(b => b.textContent.includes('术语'));
          if (btn) { btn.scrollIntoView({behavior:'smooth',block:'center'}); return true; }
          return false;
        }""")
        if opened:
            hold(page, 900)
            page.evaluate("""() => {
              [...document.querySelectorAll('#candidateList button')]
                .find(b => b.textContent.includes('术语')).click();
            }""")
            hold(page, 2200)
            tap(page, "#mappingSheetClose")
            hold(page, 700)
        # 回到导入方式
        mark("s2_back")
        page.evaluate("window.scrollTo({top:0,behavior:'smooth'})")
        hold(page, 800)
        tap(page, "#discoverBackToLauncher")
        hold(page, 1100)

        # ---- S3 知乎搜索（配好就有结果，没配也能看到提示；等待段适当加速） ----
        mark("s3_open")
        tap(page, "[data-launch-import=\"zhihu\"]")
        page.wait_for_selector("#zhihuSearchPanel:not([hidden])", timeout=8000)
        hold(page, 700)
        mark("s3_type")
        type_into(page, "#zhihuSearchQuery", TEXT_ZHIHU, delay=26)
        hold(page, 400)
        mark("s3_wait", accel=True)
        tap(page, "#confirmZhihuSearch")
        # 最多等 25s：出结果、出提示、或报错都算完
        try:
            page.wait_for_function("""() => {
              const list = document.querySelector('#zhihuSearchResults');
              const fb = document.querySelector('#zhihuSearchFeedback');
              const help = document.querySelector('#zhihuSetupHelp');
              const busy = document.querySelector('#confirmZhihuSearch').disabled;
              if (busy) return false;
              return (list && list.children.length > 0) || (help && !help.hidden)
                     || (fb && fb.textContent.includes('失败'));
            }""", timeout=25000)
        except Exception:
            pass
        mark("s3_show")
        hold(page, 2600)
        page.evaluate("""() => {
          const list = document.querySelector('#zhihuSearchResults');
          if (list && list.children.length) list.scrollIntoView({behavior:'smooth',block:'start'});
        }""")
        hold(page, 1600)
        tap(page, "#importSheetClose")
        hold(page, 800)

        # ---- S4 看山：等级页 → 桌宠页 → 六个动作 + 摸头 ----
        mark("s4_nav")
        tap(page, ".bottom-tab[data-app-page=\"petPage\"]")
        hold(page, 1800)
        page.evaluate("window.scrollBy({top:620,behavior:'smooth'})")
        hold(page, 1400)
        tap(page, "#desktopPetCard")
        page.wait_for_selector("#desktopPetPage:not([hidden])", timeout=8000)
        hold(page, 2000)
        for act, label in [("wave", "s4_wave"), ("idea", "s4_idea"), ("followup", "s4_followup"),
                           ("crosslink", "s4_crosslink"), ("levelup", "s4_levelup"), ("normal", "s4_normal")]:
            mark(label)
            tap(page, f'[data-pet-action={json.dumps(act)}]')
            hold(page, 2400)
        mark("s4_touch")
        tap(page, "#desktopPetSpriteButton")
        hold(page, 2400)
        tap(page, "#desktopPetBack")
        hold(page, 1000)

        # ---- S5 复习：翻卡 → 认识 → 下一词 → 完成 +10 能量（顶栏看山庆祝） ----
        mark("s5_nav")
        tap(page, ".bottom-tab[data-app-page=\"reviewPage\"]")
        hold(page, 2000)
        tap(page, "[data-memory-action=\"know\"]")
        hold(page, 1800)  # 翻面，看到核心定义
        tap(page, "[data-answer-action=\"next\"]")
        hold(page, 3200)  # 完成页 + 能量 +10 + 顶栏看山 levelup
        mark("s5_done")
        page.evaluate("window.scrollBy({top:380,behavior:'smooth'})")
        hold(page, 2200)

        # ---- S6 回求知首页：历史记忆已有两条，收尾 ----
        mark("s6_home")
        tap(page, ".bottom-tab[data-app-page=\"knowledge\"]")
        hold(page, 2200)
        mark("v1_end")
        hold(page, 2600)

        video = page.video
        ctx.close()
        raw_path = video.path()
        browser.close()

    (OUT / "raw.video.path").write_text(str(raw_path), encoding="utf-8")
    print(f"[done] raw video: {raw_path}", flush=True)


if __name__ == "__main__":
    # 线上实例偶尔重启/抖动，一条录废就重来；每次都是新的浏览器上下文，
    # 所以每轮拿到的是新的匿名账号，不会踩着上一条的失效 token。
    for attempt in range(1, 4):
        try:
            main()
            break
        except TakeAborted as exc:
            print(f"[retry] attempt {attempt} aborted: {exc}", flush=True)
            time.sleep(15)
    else:
        raise SystemExit("连续 3 条都录废了，先看看线上是不是又断了")
