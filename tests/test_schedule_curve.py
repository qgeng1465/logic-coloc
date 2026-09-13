# -*- coding: utf-8 -*-
"""艾宾浩斯曲线图（web/index.html 里那张手写 SVG）的护栏。

2026-09-13 修过一次：图上画了 7 个圆点却只有 5 个横坐标（1/2/4/7/15 天），而且两套 x
压根不在同一处（点在 72/112/…/335，标签在 44/103/…/291），看着就像点浮在半空、跟下面
的字没关系。根因是这张图纯手写，点和标签各写各的，没有任何东西保证它们对得上。

现在图上 8 个节点与卡片下方「复习节点：…」那行一字不差：5分钟 · 30分钟 · 12小时 ·
1天 · 2天 · 4天 · 7天 · 15天。**注意它和筛选栏那 5 个按钮不是一回事**：筛选栏是产品
真正在用的排期（按天开始的 1/2/4/7/15），曲线图是艾宾浩斯理论曲线，把分钟级那三档也
画出来。改这张图前先想清楚改的是哪一个。
"""
import re
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parents[1] / "web" / "index.html"
NODES = ["5分钟", "30分钟", "12小时", "1天", "2天", "4天", "7天", "15天"]


def _svg() -> str:
    svg = re.search(r'<svg class="schedule-curve".*?</svg>', INDEX_HTML.read_text(encoding="utf-8"), re.S)
    assert svg, "web/index.html 里找不到曲线图 —— 是不是把它整个删了"
    return svg.group(0)


def _dots(svg: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)"', svg)]


def test_dots_line_up_with_the_axis_labels() -> None:
    """点和横坐标必须一一对应、且共用同一个 x —— 这才是「对应得上」。"""
    svg = _svg()
    dots = _dots(svg)
    labels = re.findall(r'<text x="([\d.]+)" y="[\d.]+"[^>]*>([^<]+)</text>', svg)
    assert [name for _, name in labels] == NODES, f"横坐标不再是那 8 个节点：{[n for _, n in labels]}"
    assert len(dots) == len(labels), f"{len(dots)} 个点配 {len(labels)} 个横坐标 —— 又对不上了"
    for (cx, _), (tx, name) in zip(dots, labels):
        assert cx == float(tx), f"「{name}」的点在 x={cx}，横坐标却在 x={tx}"


def test_labels_match_the_caption_line() -> None:
    """卡片下方那行「复习节点：…」就是这张图的图例，两边不许各说各话。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    caption = re.search(r"<p>复习节点：([^<]+)</p>", html)
    assert caption, "「复习节点：…」那行没了 —— 图上只剩几个裸坐标，没人知道是什么意思"
    assert [s.strip() for s in caption.group(1).split("·")] == NODES


def test_labels_do_not_overlap() -> None:
    """8 个标签挤在一行里，字号 10 —— 把它们排到互相压字就是白加回来的。"""
    spans = []
    for x, name in re.findall(r'<text x="([\d.]+)" y="[\d.]+"[^>]*>([^<]+)</text>', _svg()):
        half = sum(10 if ord(c) > 0x2E80 else 5.5 for c in name) / 2
        spans.append((float(x) - half, float(x) + half, name))
    for (_, right, name), (left, _, nxt) in zip(spans, spans[1:]):
        assert right <= left, f"「{name}」和「{nxt}」的标签压在一起了"


def test_dots_sit_on_the_curve() -> None:
    """点得落在曲线上，不能浮在旁边 —— 逐段采样贝塞尔再量距离。"""
    d = re.search(r'<path d="([^"]+)"', _svg()).group(1)
    nums = [float(v) for v in re.findall(r"-?[\d.]+", d)]
    assert len(nums) >= 8 and (len(nums) - 2) % 6 == 0, "path 不是 M + n 段三次贝塞尔"
    start, poly = (nums[0], nums[1]), [(nums[0], nums[1])]
    for i in range(2, len(nums), 6):
        c1, c2, end = (nums[i], nums[i + 1]), (nums[i + 2], nums[i + 3]), (nums[i + 4], nums[i + 5])
        for k in range(1, 101):
            t = k / 100
            poly.append((
                (1 - t) ** 3 * start[0] + 3 * (1 - t) ** 2 * t * c1[0] + 3 * (1 - t) * t * t * c2[0] + t ** 3 * end[0],
                (1 - t) ** 3 * start[1] + 3 * (1 - t) ** 2 * t * c1[1] + 3 * (1 - t) * t * t * c2[1] + t ** 3 * end[1],
            ))
        start = end
    for cx, cy in _dots(_svg()):
        near = [abs(cx - x) + abs(cy - y) for x, y in poly if abs(x - cx) < 3]
        assert near and min(near) < 0.6, f"点 ({cx}, {cy}) 没落在曲线上（最近 {min(near, default=99):.2f}）"
