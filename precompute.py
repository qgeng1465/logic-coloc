# -*- coding: utf-8 -*-
"""预计算演示数据（离线缓存 + 雷达图资产）。

在演示前跑一次（约 1 分钟，需 LLM 端点可用）：
    python3 -m logic_coloc.precompute
之后现场演示全程离线秒开：数据来自 data/demo_cache.json，
雷达图 PNG 已保存在 assets/ 可直接贴进 PPT/快闪脚本。
"""
import os

from . import config
from .cache import DemoCache
from .demo_texts import DEMO_PAIRS, TEXTS
from .feature_extractor import DIMS, DIMS_CN, extract_features
from .homonomy import homonomy_score
from .mapper import map_entities
from .plots import render_radar

# 出图落在**包目录自己的** assets/（与 Web 端的 web/assets/ 图标是两个目录，别混）。
# 早先取的是包的上一级目录，会在用户的项目根凭空多出一个 assets/，2026-09-13 改到包内。
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def main():
    os.makedirs(ASSETS, exist_ok=True)
    cache = DemoCache()

    print("== [1/3] 预热文本特征（LLM 提取，命中缓存则跳过） ==")
    for name, text in TEXTS.items():
        hit = cache.get_text(text)
        if hit:
            print(f"  {name:<12} 缓存命中  vec={[round(x) for x in hit['vec']]}  {hit['terms']}")
            continue
        vec, terms = extract_features(text, tries=config.SAMPLES)
        cache.set_text(text, vec, terms, source="live")
        cache.save()
        print(f"  {name:<12} 已计算    vec={[round(x) for x in vec]}  {terms}")

    print("== [2/3] 配对评分 + 映射 + 雷达图 ==")
    for dp in DEMO_PAIRS:
        ta, tb = TEXTS[dp["a"]], TEXTS[dp["b"]]
        va = cache.get_text(ta)["vec"]
        vb = cache.get_text(tb)["vec"]
        s = homonomy_score(va, vb, config.METHOD)

        result = {"key": dp["key"], "a": dp["a"], "b": dp["b"], "title": dp["title"],
                  "score": round(s * 100, 1), "method": config.METHOD,
                  "threshold": config.THRESHOLD, "mapping": None}

        if s >= config.THRESHOLD:
            hit = cache.get_pair(dp["key"])
            result["mapping"] = (hit or {}).get("mapping")
            if not result["mapping"]:
                mp = map_entities(ta, tb)
                result["mapping"] = mp["mapping"]
        cache.set_pair(dp["key"], result)
        cache.save()

        mdesc = "无（低于阈值）" if not result["mapping"] else " · ".join(
            f"{k}⟷{v}" for k, v in result["mapping"].items())
        print(f"  {dp['key']:<8} {dp['a']} × {dp['b']} → {result['score']}%  映射: {mdesc}")

        png = os.path.join(ASSETS, f"radar_{dp['key']}.png")
        render_radar(va, vb, [DIMS_CN[d] for d in DIMS],
                     name_a=dp["a"].split("_")[0], name_b=dp["b"].split("_")[0],
                     save_path=png, title=f"{dp['title']}  ·  同源度 {result['score']}%")
        print(f"           雷达图 → {png}")

    print("== [3/3] 完成 ==")
    print(f"  缓存文件: {cache.path}")
    print(f"  雷达图:   {ASSETS}/  (共 {len(os.listdir(ASSETS))} 个 PNG)")


if __name__ == "__main__":
    main()
