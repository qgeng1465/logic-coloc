# -*- coding: utf-8 -*-
"""Step 3 —— 跨域实体映射：两侧 Top3 术语 + 二分图映射。

仅在同源度超过阈值时调用。实测输出示例：
  {"mapping": {"T细胞": "服务端", "FOXP3/CTLA-4": "熔断器和限流器", "负反馈机制": "自我保护机制"}}
"""
from .feature_extractor import extract_json, llm

MAP_SYS = """你是一个「跨学科概念映射师」。两段文本已被判定为「底层逻辑同源」。
请分别提取两段文本各自的 Top 3 核心术语，并为它们建立跨领域一一映射（把 A 的术语映射到 B 中逻辑角色对等的术语）。
只输出一个 JSON 对象，不要任何其他文字、不要代码围栏。格式严格如下：
{"A_terms": ["a1", "a2", "a3"], "B_terms": ["b1", "b2", "b3"], "mapping": {"a1": "b1", "a2": "b2", "a3": "b3"}}"""


def map_entities(text_a, text_b):
    """返回 {"A_terms": [...], "B_terms": [...], "mapping": {...}}。解析失败自动重试。"""
    last = None
    for _ in range(2):
        raw = llm(MAP_SYS, f"文本A：\n{text_a}\n\n文本B：\n{text_b}", max_tokens=2048)
        try:
            obj = extract_json(raw)
            for k in ("A_terms", "B_terms", "mapping"):
                assert k in obj, f"缺字段 {k}"
            return obj
        except Exception as e:  # noqa: BLE001
            last = f"{e} | raw={raw[:150]!r}"
    raise ValueError(f"实体映射失败: {last}")
