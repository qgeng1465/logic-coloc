# -*- coding: utf-8 -*-
"""Step 2 —— 同源度计算（本地确定性，切断 LLM）。

主评分 = 5 维对齐余弦相似度（已验证语义排序正确）。
副视角 = 一维 Wasserstein（EMD），仅衡量「分布形状」，忽略维度身份，语义区分弱。
"""
import math

from scipy.stats import wasserstein_distance

from . import config


def cosine_sim(a, b):
    n = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / n if n else 0.0


def homonomy_cosine(a, b):
    """主评分：5 维对齐余弦，映射到 [0,1]。"""
    return max(0.0, cosine_sim(a, b))


def homonomy_wasserstein(a, b, gamma=None):
    """可选副视角：一维 EMD → exp(-gamma*W1)。

    ⚠️ 局限：W1 把向量当作「5 个无序样本」，丢弃维度身份，
    只比较取值分布形状。实测会误判（见 README §4.1），仅作可视化副视角。
    """
    gamma = gamma if gamma is not None else config.GAMMA_W1
    return math.exp(-gamma * wasserstein_distance(a, b))


def homonomy_score(a, b, method=None, **kw):
    method = method or config.METHOD
    if method == "wasserstein":
        return homonomy_wasserstein(a, b, **kw)
    return homonomy_cosine(a, b)
