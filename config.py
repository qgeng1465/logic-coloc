# -*- coding: utf-8 -*-
"""全局配置：LLM 端点、模型、演示默认参数（均可用环境变量覆盖）。"""
import os

# LLM 端点：本机 claude-openai-bridge（Anthropic 兼容 /v1/messages）
# 也可改为 https://api.deepseek.com/anthropic 直连
BRIDGE   = os.environ.get("LC_BRIDGE", "http://127.0.0.1:8388")
MODEL    = os.environ.get("LC_MODEL", "deepseek-v4-flash")   # kimi-k3 / glm-4.6 / deepseek-v4-pro
API_KEY  = os.environ.get("LC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
TIMEOUT  = int(os.environ.get("LC_TIMEOUT", "180"))
# thinking 参数：实测 DeepSeek 兼容端点上「disabled」+ 低 max_tokens 会偶发空响应，
# 「不传」+ max_tokens≥2048 最稳定（默认 none=不传）。
THINKING = os.environ.get("LC_THINKING", "none")
SAMPLES  = int(os.environ.get("LC_SAMPLES", "3"))            # LLM 采样次数（取均值去噪）
METHOD   = os.environ.get("LC_METHOD", "cosine")             # cosine | wasserstein
THRESHOLD = float(os.environ.get("LC_THRESHOLD", "0.85"))    # 触发实体映射的阈值
GAMMA_W1  = float(os.environ.get("LC_GAMMA", "0.06"))        # W1 副视角指数系数
