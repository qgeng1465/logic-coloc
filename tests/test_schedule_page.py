# -*- coding: utf-8 -*-
"""复习计划页的护栏 —— 跑 tests/schedule_page_check.js。

这一页的语义被**推翻过一次**，改之前先看清现在是哪一版：

- 2026-09-13 上午那一版是「按 next_review_due 分区」：[1天] 只有今天到期的卡、[2天] 只有明天
  到期的，一张卡只属于一栏。用户当场否掉 —— 那不是他要的东西。
- **现在这一版是计划表**：标签 [1天] [2天] [4天] [7天] [15天] 说的是第几个复习**节点**，
  每个节点的日期从**建卡那天**往后推（建卡日 +1 / +2 / +4 / +7 / +15 天）。所以 9/13 建的卡
  在 [1天] 里落在 9/14、[2天] 里落在 9/15、[4天] 里落在 9/17；9/14 建的每个节点晚一天。

节点日期过了也照样列出来（「✓ 已完成」/「已逾期」），**不隐藏**——它是一张计划表，不是
「今天该复习什么」的待办。判定今天要不要复习的唯一依据始终是 `next_review_due`，那是复习页
和右上角铃铛的事。

这张表只能在真代码上验：Python 里重写一遍「建卡日 +N 天」等于抄一份平行实现，抄错了照样绿。
所以这里用 node 把 app.js 里那几个真函数 eval 出来跑。node 不在的环境（容器、CI）自动跳过
—— 它是开发机上的护栏，不是构建的门。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
CHECK = TESTS_DIR / "schedule_page_check.js"
WEB_DIR = TESTS_DIR.parent / "web"


def test_plan_table_nodes_and_collapsing() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("没装 node —— 这条护栏要在开发机上跑，容器里跳过")
    result = subprocess.run(
        [node, str(CHECK)], capture_output=True, text=True, encoding="utf-8", cwd=str(TESTS_DIR.parent), timeout=120
    )
    assert result.returncode == 0, f"复习计划页的行为不符：\n{result.stdout}\n{result.stderr}"


def test_filter_row_is_one_horizontally_scrollable_line() -> None:
    """筛选栏是**一行、横着滚**的，滚动条留着 —— 不是换行，也不是藏掉滚动条。

    2026-09-13 我把它改成过 `flex-wrap: wrap`，理由写的是「横向滚动时 390px 下最后一个按钮点不到」，
    那个理由本身是错的：`overflow-x: auto` 的元素自己就是滚动容器，外层 `#schedulePage` 的
    `overflow-x: hidden` 只裁它自己的框，裁不到里面的滚动。用户当场否掉 —— 换行后「全部」被甩到
    第二行孤零零一个，很难看；他要的是「横着的滚轮 + 全部放最前」。

    顺序那半条由 `schedule_page_check.js` 直接读 index.html 断言（假 DOM 骗不了它），
    这里只看 style.css 的静态文本，所以放在 Python 这边 —— 假 DOM 看不见 CSS。
    """
    css = (WEB_DIR / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.schedule-filters\s*\{([^}]*)\}", css)
    assert rule, "style.css 里找不到 .schedule-filters 规则"
    body = rule.group(1)
    assert "overflow-x: auto" in body, f"筛选栏不是横向滚动的：{body!r}"
    assert "flex-wrap" not in body, f"筛选栏又被改回换行了：「全部」会掉到第二行：{body!r}"
    assert "scrollbar-width: none" not in body, f"滚动条被藏了 —— 那是用户点名要的「横着的滚轮」：{body!r}"
