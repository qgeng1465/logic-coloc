# -*- coding: utf-8 -*-
"""命令行演示：一键输出「同源度 + 概念映射 + 雷达图」。

用法：
    python3 -m logic_coloc.run_demo --pair demo1          # 预置演示对（离线缓存）
    python3 -m logic_coloc.run_demo --a "文本A" --b "文本B" # 自定义文本（需在线 LLM）
    python3 -m logic_coloc.run_demo --pair demo2 --png /tmp/demo2.png
    python3 -m logic_coloc.run_demo --pair demo3 --method wasserstein
"""
import argparse
import json
import os
import sys

from . import config
from .cache import DemoCache
from .demo_texts import DEMO_PAIRS, TEXTS
from .feature_extractor import DIMS, extract_features
from .homonomy import homonomy_score
from .mapper import map_entities
from .plots import render_radar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve(args):
    """解析 --pair 或 --a/--b → (文本A, 文本B, 显示名)。"""
    if args.pair:
        dp = next((d for d in DEMO_PAIRS if d["key"] == args.pair), None)
        if not dp:
            raise SystemExit(f"未知 --pair {args.pair}，可用: {[d['key'] for d in DEMO_PAIRS]}")
        return TEXTS[dp["a"]], TEXTS[dp["b"]], f"{dp['a']} × {dp['b']}", dp
    if args.a and args.b:
        return args.a, args.b, "自定义文本对", None
    raise SystemExit("需提供 --pair 或 --a/--b")


def main():
    ap = argparse.ArgumentParser(description="Logic-Coloc 命令行演示")
    ap.add_argument("--pair", help="预置演示对: demo1 / demo2 / demo3")
    ap.add_argument("--a", "--text-a", help="自定义文本A")
    ap.add_argument("--b", "--text-b", help="自定义文本B")
    ap.add_argument("--method", choices=["cosine", "wasserstein"], default=config.METHOD)
    ap.add_argument("--threshold", type=float, default=config.THRESHOLD)
    ap.add_argument("--png", help="保存雷达图 PNG 的路径")
    ap.add_argument("--json", help="保存结果 JSON 的路径")
    ap.add_argument("--force", action="store_true", help="忽略缓存强制重新计算")
    args = ap.parse_args()

    a, b, label, dp = resolve(args)
    cache = DemoCache()

    def profile(text):
        if not args.force:
            hit = cache.get_text(text)
            if hit:
                return hit["vec"], hit["terms"], "缓存"
        vec, terms = extract_features(text, tries=config.SAMPLES)
        cache.set_text(text, vec, terms, source="live")
        cache.save()
        return vec, terms, "在线"

    va, ta, sa = profile(a)
    vb, tb, sb = profile(b)
    score = homonomy_score(va, vb, args.method)

    print("=" * 64)
    print("🧬 Logic-Coloc · 跨学科知识同源翻译机")
    print("=" * 64)
    print(f"配对   : {label}")
    print(f"度量   : {args.method}  阈值: {args.threshold}")
    print(f"特征   : A@{sa}  {[round(x) for x in va]}   {ta}")
    print(f"         B@{sb}  {[round(x) for x in vb]}   {tb}")
    print("-" * 64)
    print(f"底层逻辑同源度: {score * 100:.1f}%   ({'同源 ✓' if score >= args.threshold else '不同源 ✗'})")
    print("-" * 64)

    result = {"pair": label, "method": args.method, "threshold": args.threshold,
              "score": round(score * 100, 1), "vec_a": va, "vec_b": vb,
              "terms_a": ta, "terms_b": tb, "mapping": None}

    if score >= args.threshold:
        if dp and not args.force:
            hit = cache.get_pair(dp["key"])
            mapping = (hit or {}).get("mapping")
            src = "缓存"
        else:
            mapping, src = None, ""
        if not mapping:
            mp = map_entities(a, b)
            mapping, src = mp["mapping"], "在线"
        result["mapping"] = mapping
        print("跨域概念映射：")
        for k, v in mapping.items():
            print(f"    `{k}`  ⟷  `{v}`")
        print(f"  (来源: {src})")
    else:
        print(f"同源度低于阈值 {args.threshold:.0%}，不生成概念映射。")

    if args.png:
        png = args.png
        render_radar(va, vb, list(DIMS), name_a="A", name_b="B", save_path=png,
                     title=label[:24])
        print(f"雷达图已保存 → {png}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"结果 JSON 已保存 → {args.json}")
    print("=" * 64)
    return result


if __name__ == "__main__":
    main()
    sys.exit(0)
