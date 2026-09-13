# -*- coding: utf-8 -*-
"""结果缓存：让现场演示可离线、秒开。

- 对每段文本缓存其 5 维画像 + Top3 术语（按内容哈希）。
- 对每个演示对缓存分数与概念映射。
- 预计算脚本（precompute.py）把演示数据全部写进 data/demo_cache.json；
  现场若 LLM 端点不可达，应用直接读缓存也能完整演示。
"""
import hashlib
import json
import os

# 缓存写**包目录自己的** data/，不再写到包的上一级目录。
# 上一级通常是用户的项目根（本机是 E:\，里面还有论文/毕设一堆无关文件夹），我们的代码
# 往那儿掉一个 data/ 很碍眼，也容易和 Web 端真正的数据目录搞混。
# 这里落盘的是 CLI 演示缓存，与 Web 端 data/users.json 之类同处一个 data/ 但互不相干。
_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "demo_cache.json"
)


def _key(text):
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


class DemoCache:
    def __init__(self, path=_DEFAULT_PATH):
        self.path = path
        self.data = {"texts": {}, "pairs": {}}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                self.data = json.load(open(self.path, encoding="utf-8"))
            except Exception:  # noqa: BLE001 —— 缓存损坏时降级为空缓存
                self.data = {"texts": {}, "pairs": {}}
        self.data.setdefault("texts", {})
        self.data.setdefault("pairs", {})
        return self

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)

    # ---- 文本画像 ----
    def get_text(self, text):
        return self.data["texts"].get(_key(text))

    def set_text(self, text, vec, terms, source="live"):
        self.data["texts"][_key(text)] = {"vec": vec, "terms": terms, "source": source}

    # ---- 演示对结果 ----
    def get_pair(self, key):
        return self.data["pairs"].get(key)

    def set_pair(self, key, result):
        self.data["pairs"][key] = result
