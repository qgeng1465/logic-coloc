# -*- coding: utf-8 -*-
"""生成全部演示物料图（读离线缓存，纯本地渲染，可重复运行）。

    python3 -m logic_coloc.make_figures

产出（assets/）：
    fig_mapping_demo{1,2}.png   跨域概念映射图（二分图，Pitch 的 Aha 视觉）
    fig_heatmap_matrix.png      5 文本全配对同源度矩阵热图
    fig_method_compare.png      余弦 vs 朴素 Wasserstein 度量对比（答辩防身）
    fig_dims_card.png           5 个底层逻辑维度说明卡
    fig_poster_16x9.png         16:9 收尾海报（PPT 末页/封面）
    radar_demo{1,2,3}.png       雷达图（带分数标题，中文维度标签）
"""
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

from .cache import DemoCache  # noqa: E402
from .demo_texts import DEMO_PAIRS, SHORT_NAMES, TEXTS  # noqa: E402
from .feature_extractor import DIMS, DIMS_CN  # noqa: E402
from .homonomy import homonomy_cosine, homonomy_wasserstein  # noqa: E402
from .plots import render_radar  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

BLUE, RED, PURPLE, SLATE = "#2563eb", "#dc2626", "#7c3aed", "#64748b"
NAVY, PAPER = "#0f172a", "#f8fafc"
PAIR_COLORS = [BLUE, RED, PURPLE]


def _save(fig, name):
    path = os.path.join(ASSETS, name)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  生成 {name}")
    return path


# ---------------------------------------------------------------- 映射图
def fig_mapping(dp, cache):
    res = cache.get_pair(dp["key"])
    mp, score = res["mapping"], res["score"]
    fig, ax = plt.subplots(figsize=(9.2, 5.0), dpi=150)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    fig.patch.set_facecolor(PAPER)

    ax.text(5, 9.5, f"跨域概念映射 · {SHORT_NAMES[dp['a']]} × {SHORT_NAMES[dp['b']]}",
            ha="center", fontsize=15, weight="bold", color=NAVY)
    ax.text(5, 8.75, f"底层逻辑同源度 {score}%", ha="center", fontsize=12,
            color=BLUE, weight="bold")

    ax.text(2.1, 7.9, SHORT_NAMES[dp["a"]], ha="center", fontsize=12, color=BLUE, weight="bold")
    ax.text(7.9, 7.9, SHORT_NAMES[dp["b"]], ha="center", fontsize=12, color=RED, weight="bold")

    ys = [6.1, 4.0, 1.9]
    for i, (k, v) in enumerate(mp.items()):
        c = PAIR_COLORS[i % 3]
        ax.add_patch(FancyBboxPatch((0.4, ys[i] - 0.72), 3.4, 1.44,
                                    boxstyle="round,pad=0.12", fc="white", ec=c, lw=2.2))
        ax.text(2.1, ys[i], k, ha="center", va="center", fontsize=12.5, color=NAVY)
        ax.add_patch(FancyBboxPatch((6.2, ys[i] - 0.72), 3.4, 1.44,
                                    boxstyle="round,pad=0.12", fc="white", ec=c, lw=2.2))
        ax.text(7.9, ys[i], v, ha="center", va="center", fontsize=12.5, color=NAVY)
        ax.plot([3.95, 6.05], [ys[i], ys[i]], color=c, lw=2.6, zorder=1)
        ax.plot([3.95, 6.05], [ys[i], ys[i]], color=c, lw=1.2,
                marker="o", ms=6, mec="white", mfc=c, zorder=2)

    return _save(fig, f"fig_mapping_{dp['key']}.png")


# ---------------------------------------------------------------- 矩阵热图
def fig_matrix(cache):
    names = list(TEXTS.keys())
    n = len(names)
    vecs = {k: cache.get_text(TEXTS[k])["vec"] for k in names}
    M = np.zeros((n, n))
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            M[i, j] = homonomy_cosine(vecs[a], vecs[b]) * 100

    labels = [SHORT_NAMES[k] for k in names]
    fig, ax = plt.subplots(figsize=(7.4, 6.4), dpi=150)
    im = ax.imshow(M, cmap="Blues", vmin=60, vmax=100)
    ax.set_xticks(range(n), labels, fontsize=10.5)
    ax.set_yticks(range(n), labels, fontsize=10.5)
    ax.set_title("全配对底层逻辑同源度矩阵（余弦，%）", fontsize=13, weight="bold",
                 color=NAVY, pad=14)
    for i in range(n):
        for j in range(n):
            ax.text(j, i, f"{M[i, j]:.0f}", ha="center", va="center", fontsize=10.5,
                    color="white" if M[i, j] >= 88 else NAVY,
                    weight="bold" if M[i, j] >= 88 else "normal")
    # 高亮两个同源演示对
    for (a, b), c in [((0, 1), BLUE), ((2, 3), PURPLE)]:
        ax.add_patch(Rectangle((b - 0.5, a - 0.5), 1, 1, fill=False, ec=c, lw=3))
        ax.add_patch(Rectangle((a - 0.5, b - 0.5), 1, 1, fill=False, ec=c, lw=3))
    fig.colorbar(im, ax=ax, shrink=0.82, label="同源度 (%)")
    fig.tight_layout()
    return _save(fig, "fig_heatmap_matrix.png")


# ---------------------------------------------------------------- 度量对比
def fig_methods(cache):
    pairs = [("A_免疫负反馈", "B_服务器熔断", "免疫×服务器\n(语义: 同源)"),
             ("C_量子叠加", "D_期权定价", "量子×期权\n(语义: 同源)"),
             ("A_免疫负反馈", "C_量子叠加", "免疫×量子\n(语义: 异源)"),
             ("B_服务器熔断", "C_量子叠加", "服务器×量子\n(语义: 异源)")]
    vecs = {k: cache.get_text(TEXTS[k])["vec"] for k in TEXTS}
    cos = [homonomy_cosine(vecs[a], vecs[b]) * 100 for a, b, _ in pairs]
    w1 = [homonomy_wasserstein(vecs[a], vecs[b]) * 100 for a, b, _ in pairs]

    x = np.arange(len(pairs))
    w = 0.36
    fig, ax = plt.subplots(figsize=(9.6, 5.6), dpi=150)
    fig.patch.set_facecolor(PAPER)
    ax.bar(x - w / 2, cos, w, color=BLUE, label="余弦（维度对齐，主评分）")
    ax.bar(x + w / 2, w1, w, color=SLATE, label="朴素 Wasserstein（副视角）")
    ax.axhline(85, color=RED, ls="--", lw=1.6)
    ax.text(3.42, 86.5, "阈值 85%", color=RED, fontsize=10, ha="right")
    for xi, v in zip(x - w / 2, cos):
        ax.text(xi, v + 1.2, f"{v:.0f}", ha="center", fontsize=10.5, color=BLUE, weight="bold")
    for xi, v in zip(x + w / 2, w1):
        ax.text(xi, v + 1.2, f"{v:.0f}", ha="center", fontsize=10.5, color=SLATE)
    ax.set_xticks(x, [p[2] for p in pairs], fontsize=10.5)
    ax.set_ylim(0, 112)
    ax.set_ylabel("同源度 (%)", fontsize=11)
    ax.set_title("为什么用「维度对齐余弦」而不是朴素 Wasserstein",
                 fontsize=13.5, weight="bold", color=NAVY, pad=12)
    ax.text(0.5, -0.30,
            "朴素 W1 把向量当「无序样本」，丢掉维度身份 → 把异源对「免疫×量子」排到最高（72%）；余弦排序与语义完全一致",
            transform=ax.transAxes, ha="center", fontsize=10, color=SLATE)
    ax.legend(loc="upper right", fontsize=10, framealpha=0.95)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return _save(fig, "fig_method_compare.png")


# ---------------------------------------------------------------- 维度卡
def fig_dims():
    fig, ax = plt.subplots(figsize=(9.6, 5.2), dpi=150)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    fig.patch.set_facecolor(PAPER)
    ax.text(5, 9.3, "Step 1 · LLM 在 5 个底层逻辑维度上打分（0–100）",
            ha="center", fontsize=14.5, weight="bold", color=NAVY)

    rows = list(DIMS.items())
    for i, (key, desc) in enumerate(rows):
        y = 7.6 - i * 1.62
        name = DIMS_CN[key]
        short = desc.split("：", 1)[1].split("（")[0]
        ax.add_patch(FancyBboxPatch((0.5, y - 0.62), 9.0, 1.3,
                                    boxstyle="round,pad=0.1", fc="white",
                                    ec=[BLUE, RED, PURPLE, SLATE, "#0d9488"][i % 5], lw=1.8))
        ax.text(1.0, y, name, ha="left", va="center", fontsize=12.5,
                weight="bold", color=NAVY)
        ax.text(4.15, y, short, ha="left", va="center", fontsize=11.5, color=SLATE)
    return _save(fig, "fig_dims_card.png")


# ---------------------------------------------------------------- 16:9 海报
def fig_poster(cache):
    fig, ax = plt.subplots(figsize=(16, 9), dpi=100)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 9)
    ax.axis("off")
    fig.patch.set_facecolor(NAVY)
    ax.add_patch(Rectangle((0, 0), 16, 9, fc=NAVY, ec="none"))

    ax.text(8, 8.15, "Logic-Coloc", ha="center", fontsize=44,
            weight="bold", color="white")
    ax.text(8, 7.25, "跨学科知识同源翻译机 —— 不翻译语言，翻译思维",
            ha="center", fontsize=19, color="#93c5fd")

    cards = [
        (DEMO_PAIRS[0], BLUE, cache, "同源"),
        (DEMO_PAIRS[1], PURPLE, cache, "同源"),
        (DEMO_PAIRS[2], SLATE, cache, "不同源（对照）"),
    ]
    xs = [2.9, 8.0, 13.1]
    for (dp, color, _, tag), xc in zip(cards, xs):
        res = cache.get_pair(dp["key"])
        ax.add_patch(FancyBboxPatch((xc - 2.15, 2.35), 4.3, 4.0,
                                    boxstyle="round,pad=0.12", fc="#1e293b",
                                    ec=color, lw=2.4))
        ax.text(xc, 5.85, f"{SHORT_NAMES[dp['a']]} × {SHORT_NAMES[dp['b']]}",
                ha="center", fontsize=13.5, color="#e2e8f0")
        ax.text(xc, 4.65, f"{res['score']}%", ha="center", fontsize=40,
                weight="bold", color=color)
        ax.text(xc, 3.75, tag, ha="center", fontsize=12.5,
                color="#86efac" if tag.startswith("同源") else "#fca5a5")
        mp = res.get("mapping")
        if mp:
            k, v = next(iter(mp.items()))
            ax.text(xc, 3.0, f"{k} → {v}", ha="center", fontsize=11.5, color="#94a3b8")
        else:
            ax.text(xc, 3.0, "低于阈值 · 不触发映射", ha="center", fontsize=11.5, color="#94a3b8")

    ax.text(8, 1.15, "LLM 5 维逻辑画像  +  本地确定性余弦同源度  +  跨域概念映射",
            ha="center", fontsize=15, color="white")
    ax.text(8, 0.5, "离线缓存可演示   ·   ./demo.sh 一键启动",
            ha="center", fontsize=12.5, color="#64748b")
    return _save(fig, "fig_poster_16x9.png")


# ---------------------------------------------------------------- 雷达重渲染
def fig_radars(cache):
    for dp in DEMO_PAIRS:
        va = cache.get_text(TEXTS[dp["a"]])["vec"]
        vb = cache.get_text(TEXTS[dp["b"]])["vec"]
        res = cache.get_pair(dp["key"])
        render_radar(va, vb, [DIMS_CN[d] for d in DIMS],
                     name_a=SHORT_NAMES[dp["a"]], name_b=SHORT_NAMES[dp["b"]],
                     save_path=os.path.join(ASSETS, f"radar_{dp['key']}.png"),
                     title=f"{SHORT_NAMES[dp['a']]} × {SHORT_NAMES[dp['b']]}  ·  同源度 {res['score']}%")
        plt.close("all")
        print(f"  重渲染 radar_{dp['key']}.png")


def main():
    os.makedirs(ASSETS, exist_ok=True)
    cache = DemoCache()
    print("== 生成演示物料图 ==")
    for dp in DEMO_PAIRS:
        if cache.get_pair(dp["key"]) and cache.get_pair(dp["key"]).get("mapping"):
            fig_mapping(dp, cache)
        else:
            print(f"  跳过 fig_mapping_{dp['key']}（无映射，异源对照）")
    fig_matrix(cache)
    fig_methods(cache)
    fig_dims()
    fig_poster(cache)
    fig_radars(cache)
    print(f"== 完成，全部在 {ASSETS} ==")


if __name__ == "__main__":
    main()
