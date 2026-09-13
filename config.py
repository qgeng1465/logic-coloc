# -*- coding: utf-8 -*-
"""全局配置：LLM 端点、模型、演示默认参数（均可用环境变量覆盖）。"""
import os
from pathlib import Path


def _load_dotenv() -> None:
    """Load simple KEY=VALUE settings before reading runtime configuration.

    The project intentionally avoids a dotenv dependency; this keeps local
    `.env` settings available to uvicorn processes started from any directory.
    Existing environment variables always take precedence.
    """
    for candidate in (Path(__file__).resolve().parent / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if not candidate.is_file():
            continue
        try:
            for raw in candidate.read_text(encoding="utf-8-sig").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            pass
        break


_load_dotenv()

# LLM 端点：本机 claude-openai-bridge（Anthropic 兼容 /v1/messages）
# 也可改为 https://api.deepseek.com/anthropic 直连
BRIDGE   = os.environ.get("LC_BRIDGE", "https://api.deepseek.com/anthropic")
MODEL    = os.environ.get("LC_MODEL", "chatgpt-4o-latest")   # OpenAI-compatible model
API_KEY  = os.environ.get("LC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
TIMEOUT  = int(os.environ.get("LC_TIMEOUT", "180"))
# thinking 参数：实测 DeepSeek 兼容端点上「disabled」+ 低 max_tokens 会偶发空响应，
# 「不传」+ max_tokens≥2048 最稳定（默认 none=不传）。
THINKING = os.environ.get("LC_THINKING", "none")
SAMPLES  = int(os.environ.get("LC_SAMPLES", "3"))            # LLM 采样次数（取均值去噪）
METHOD   = os.environ.get("LC_METHOD", "cosine")             # cosine | wasserstein
THRESHOLD = float(os.environ.get("LC_THRESHOLD", "0.85"))    # 触发实体映射的阈值
GAMMA_W1  = float(os.environ.get("LC_GAMMA", "0.06"))        # W1 副视角指数系数

# 账号登录态 token 的 HMAC 签名密钥。
# 留空则首次使用时自动生成一份存到 data/.secret_key —— 本地开发开箱即用，重启后
# 已登录的人也不用重新登。但容器重建会连这个文件一起丢掉，**线上应当由平台注入一个
# 固定值**，否则每次重新部署所有人都会被踢回登录页。见 docs/部署指南-CloudBase.md。
SECRET_KEY = os.environ.get("LC_SECRET_KEY", "")
