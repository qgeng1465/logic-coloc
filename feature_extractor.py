# -*- coding: utf-8 -*-
"""Step 1 —— LLM 逻辑特征提取：文本 → 5 维逻辑画像 + Top3 术语。

把 LLM 当作「高维逻辑特征提取器」（而非文本生成器），
强制其输出严格 JSON；多次采样取均值，降低打分抖动。
"""
import json
import re
import statistics

import requests

from . import config

DIMS = {
    "system_closure":            "系统封闭性：系统是否自包含、闭环运行（越闭环分越高）",
    "causal_chain_length":       "因果链长度：因果推理链条的长度（链条越长分越高）",
    "negative_feedback_strength": "负反馈强度：自我抑制/纠错/防失控机制的强度",
    "randomness_entropy":        "随机性/熵值：不确定性、随机过程、多路径并存的占比",
    "zero_sum_resource_level":   "资源零和性：资源竞争/守恒/此消彼长的程度",
}

# 中文短标签（图表演示用，与 DIMS 键一一对应）
DIMS_CN = {
    "system_closure":            "系统封闭性",
    "causal_chain_length":       "因果链长度",
    "negative_feedback_strength": "负反馈强度",
    "randomness_entropy":        "随机性/熵值",
    "zero_sum_resource_level":   "资源零和性",
}

EXTRACT_SYS = (
    "你是一个「底层逻辑结构分析器」。给用户提供的文本在 5 个逻辑维度打分(0-100 整数)，并提取 Top 3 核心术语。\n"
    "维度定义：\n" + "\n".join(f"- {k} {v}" for k, v in DIMS.items()) +
    "\n只输出一个 JSON 对象，不要输出任何其他文字、不要用代码围栏。格式严格如下：\n"
    '{"scores": {"system_closure": 50, "causal_chain_length": 50, "negative_feedback_strength": 50, '
    '"randomness_entropy": 50, "zero_sum_resource_level": 50}, "top_terms": ["术语1", "术语2", "术语3"]}'
)


def llm(system, user, max_tokens=2048, temperature=0.3, retries=2):
    """调用 Anthropic 兼容端点；只取 text 内容块（跳过 thinking）。失败自动重试。"""
    last = None
    headers = {"anthropic-version": "2023-06-01"}
    if config.API_KEY:
        headers["Authorization"] = f"Bearer {config.API_KEY}"
        headers["x-api-key"] = config.API_KEY
    for _ in range(retries + 1):
        try:
            payload = {"model": config.MODEL, "max_tokens": max_tokens,
                       "temperature": temperature, "system": system,
                       "messages": [{"role": "user", "content": user}]}
            if config.THINKING and config.THINKING.lower() != "none":
                payload["thinking"] = {"type": config.THINKING}
            r = requests.post(f"{config.BRIDGE}/v1/messages", json=payload, headers=headers, timeout=config.TIMEOUT)
            r.raise_for_status()
            data = r.json()
            text = "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
            if text.strip():
                return text
            last = f"空内容: {json.dumps(data)[:200]}"
        except Exception as e:  # noqa: BLE001 —— 网络/端点抖动，重试兜底
            last = repr(e)
    raise RuntimeError(f"LLM 调用失败: {last}")


def extract_json(text):
    """从 LLM 输出抽取严格 JSON 对象（容忍代码围栏/前后缀杂音）。"""
    t = re.sub(r"^`{3}(?:json)?\s*|\s*`{3}$", "", text.strip(), flags=re.S)
    m = re.search(r"\{.*\}", t, flags=re.S)
    if not m:
        raise ValueError(f"无 JSON 对象: {text[:200]!r}")
    return json.loads(m.group(0))


def _validate(scores):
    vec = []
    for d in DIMS:
        v = scores[d]
        assert isinstance(v, (int, float)) and not isinstance(v, bool), f"{d} 非数值: {v!r}"
        assert 0 <= v <= 100, f"{d} 越界: {v}"
        vec.append(float(v))
    return vec


def _extract_once(text):
    """单次采样提取；JSON 解析失败时重试一次（偶发截断兜底）。"""
    last = None
    for _ in range(2):
        raw = llm(EXTRACT_SYS, text, max_tokens=2048)
        try:
            obj = extract_json(raw)
            return _validate(obj["scores"]), obj["top_terms"]
        except Exception as e:  # noqa: BLE001
            last = f"{e} | raw={raw[:120]!r}"
    raise ValueError(f"特征提取失败: {last}")


def extract_features(text, tries=None):
    """采样 tries 次，各维度取均值去噪；返回 (平均5维向量, 平均Top3术语)。

    tries 默认取 config.SAMPLES。LLM 打分存在抖动（同一文本两次采样可差 ±10~20 分），
    均值化后余弦相似度对噪声天然鲁棒（同文本两次采样余弦 ≈ 0.99）。
    """
    tries = tries or config.SAMPLES
    vecs, terms = [], []
    for _ in range(tries):
        vec, term = _extract_once(text)
        vecs.append(vec)
        terms.append(term)
    avg = [statistics.mean(v) for v in zip(*vecs)]
    top = [statistics.mode(c) for c in zip(*terms)]
    return avg, top
