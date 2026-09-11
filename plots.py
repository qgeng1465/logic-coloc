# -*- coding: utf-8 -*-
"""雷达图渲染（matplotlib，无额外前端依赖；可用作 Streamlit 图或保存 PNG 资产）。"""
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

# 注册中文字体（按候选路径逐个尝试；找不到时退化为英文标签）
_CJK_FONT_CANDIDATES = [
    os.environ.get("LC_CJK_FONT", ""),
    "/home/qiushuogeng/.fonts/wqy-microhei.ttc",
    "/home/qiushuogeng/.fonts/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
for _p in _CJK_FONT_CANDIDATES:
    if _p and os.path.exists(_p):
        try:
            font_manager.fontManager.addfont(_p)
        except Exception:  # noqa: BLE001
            continue
_PREFERRED = ["WenQuanYi Micro Hei", "Noto Sans CJK SC", "Noto Sans CJK JP"]
for _name in _PREFERRED:
    if _name in {f.name for f in font_manager.fontManager.ttflist}:
        plt.rcParams["font.family"] = _name
        break
plt.rcParams["axes.unicode_minus"] = False


def _no_emoji(s):
    """去掉 matplotlib 字体缺字形的 emoji（否则标题出现方框）。"""
    import re
    return re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", str(s)).strip()


def render_radar(vec_a, vec_b, labels, name_a="A", name_b="B",
                 save_path=None, title="5 维底层逻辑画像对比", dpi=150):
    """绘制 A/B 两向量在逻辑维度上的雷达重合图；save_path 非空则保存 PNG。

    返回 matplotlib Figure（调用方可用 st.pyplot 展示或再保存）。
    """
    ang = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    fig, ax = plt.subplots(figsize=(6.6, 4.9), subplot_kw=dict(polar=True))

    for vec, color, name in [(vec_a, "#2563eb", name_a), (vec_b, "#dc2626", name_b)]:
        v = list(vec) + [vec[0]]
        a = ang + [ang[0]]
        ax.plot(a, v, color=color, linewidth=2.2, label=name)
        ax.fill(a, v, color=color, alpha=0.12)

    ax.set_xticks(ang)
    ax.set_xticklabels([_no_emoji(x) for x in labels], fontsize=9)
    ax.set_ylim(0, 100)
    ax.set_title(_no_emoji(title), pad=22, fontsize=12)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.12), fontsize=9)
    fig.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=dpi, bbox_inches="tight")
    return fig
