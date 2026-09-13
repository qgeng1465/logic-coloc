# -*- coding: utf-8 -*-
"""OCR 引擎单例。

`/api/ocr`（用户导入截图识字）和 `attachment_text`（复盘时读图片附件）都要用
RapidOCR，而每次实例化都要加载一遍 onnxruntime 的模型（几十 MB）。`lru_cache`
是 per-function 的 —— 两处各写一份就会加载两份、内存翻倍，所以单独放这里共用。
"""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_ocr_engine():
    """RapidOCR 实例。首次调用才 import + 加载模型（冷启动不该背这个包袱）。"""
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()
