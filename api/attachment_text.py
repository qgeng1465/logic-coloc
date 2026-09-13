# -*- coding: utf-8 -*-
"""把笔记附件的文字抽出来，交给复盘导师。

复盘时 AI 只能看到我们放进提示词里的文字 —— 它没法自己打开 PDF。这个模块负责把
`notes.json` 里 attachments 的 url 还原成磁盘文件、读成纯文本、按预算截断。

三件事先说清楚：

1. **url → 磁盘路径是全项目唯一一处「外部输入直连文件读取」**，而 attachment.url
   是客户端可控的（整篇笔记由前端 POST 上来）。`/uploads/*` 确实是匿名静态口，但那是
   「知道随机文件名就能下载」；这里若放行越界路径，`/uploads/../../.env` 就能把含真实
   密钥的 .env 读出来塞进提示词、发给第三方 LLM —— 性质完全不同。所以
   `resolve_upload_path` 做包含性校验，宁可返回 None 也绝不放行。

2. **路径常量一律在函数体内从 user_paths 取**，不许 `from .user_paths import UPLOAD_DIR`。
   `api/user_paths.py` 的模块注释写了原因：测试靠 `monkeypatch.setattr` 打 patch，
   import 期快照不跟着变，测试就会读到真实 uploads/（`note_store.py` 那份 UPLOAD_DIR
   就是反例，得靠 fixture 额外补一刀）。

3. **任何抽取失败都不许抛到路由**。一个坏附件不该让整场复盘 500 —— 用户只会看到
   「处理请求时发生错误」，完全猜不到是附件的问题。失败一律降级成一行如实说明，既让
   导师知道自己没有材料、也让前端能把这行原样显示给用户。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlsplit

from . import user_paths


logger = logging.getLogger(__name__)

# 长度预算。附件文字**每一轮都会被重新拼进提示词**（它不进会话历史，见 service.chat），
# 所以这是每轮的固定开销：总量不超过 FULL_TEXT_LIMIT 就全文进；超了只进开头 HEAD_CHARS
# 并如实写明真实规模 —— 让模型知道自己看的是节选，远好过让它以为文档就这么短。
FULL_TEXT_LIMIT = 20_000
HEAD_CHARS = 8_000
MAX_ATTACHMENTS = 3

# 单个附件的字节上限，兜底用。真实上限由调用方传（`routes.py` 传
# `note_store.MAX_UPLOAD_BYTES`，也就是上传口那个数），这里只是让本模块不依赖 store
# 模块、单独调用时也不至于去读一个 1GB 的文件。
DEFAULT_MAX_BYTES = 10 * 1024 * 1024

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
_CACHE_LIMIT = 32

# key = (磁盘路径, 字节数, mtime_ns) → (文字, 页数)
_cache: dict[tuple[str, int, int], tuple[str, int]] = {}
_cache_lock = Lock()


@dataclass(frozen=True)
class AttachmentDigest:
    """一次复盘的附件读取结果。"""

    block: str = ""  # 拼进提示词的那一整块；没有可读附件时为空串
    notes: list[str] = field(default_factory=list)  # 给前端显示的人话，一行一个附件


def resolve_upload_path(url: str) -> Path | None:
    """把附件 url 还原成 uploads/ 下的真实文件路径；越界一律 None。

    两种落盘形态都要认（实测 uploads/ 里两种并存）：
      - 旧：/uploads/<uuid>_<名字>         账号体系之前上传的
      - 新：/uploads/<uid>/<uuid>_<名字>
    区别只是 "/uploads/" 之后那段有没有 uid 子目录，所以统一「取 /uploads/ 之后的部分
    再拼到 UPLOAD_DIR 下」就都覆盖了。url 通常是绝对地址（后端用 request.base_url
    拼的），也可能被改写成相对路径，用 urlsplit 取 path 两种都对。
    """
    if not url:
        return None
    # 先 unquote：否则 %2e%2e%2f 这种编码过的 ../ 会绕过下面的包含性校验。
    path = unquote(urlsplit(str(url)).path)
    marker = "/uploads/"
    index = path.find(marker)
    if index < 0:
        return None
    relative = path[index + len(marker) :]
    if not relative:
        return None

    upload_dir = Path(user_paths.UPLOAD_DIR)  # 函数体内取，见模块注释第 2 条
    try:
        candidate = (upload_dir / relative).resolve()
        root = upload_dir.resolve()
    except (OSError, ValueError):
        # Windows 上的非法字符、超长路径、NUL 字节等
        return None
    # resolve() 已经把符号链接展开，所以「软链指向 uploads 外面」也会在这里被拒。
    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def _normalize_page(text: str) -> str:
    """收拾 PDF 抽出来那种碎排版：行内空白并成一个空格、3 个以上换行压成 2 个。

    刻意**保留换行**（段落结构对理解有用），不像 `notePlainText` 那样全折成一行。
    """
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf_text(path: Path) -> tuple[str, int]:
    """PDF → (带页码标记的文字, 总页数)。只取文字层，扫描件返回空串（由调用方降级）。

    每页加 `【第 N 页】` 标记，是为了让导师能问「第 3 页的课程设置里……」并自己核对
    —— 那比笼统地问「这篇讲了什么」有用得多，多出来的字符量相对预算可以忽略。
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # 线上镜像没重建、依赖没装上时兜住，别把整个 app 拖崩
        raise RuntimeError("pypdf 未安装，无法读取 PDF") from exc

    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        # 试空口令：不少「加密」PDF 只是设了权限位，并没有打开密码。
        raise RuntimeError("PDF 已加密，无法读取")

    pages = reader.pages
    total = len(pages)
    blocks: list[str] = []
    for index, page in enumerate(pages):
        text = _normalize_page(page.extract_text() or "")
        if not text:
            # 没有文字层的页（扫描件）直接跳过：这样整份都是扫描件时返回空串，
            # 调用方靠「空串」判定降级 —— 若照常写页码标记，一页空白也会凑出
            # 一堆字符、把降级判据骗过去。
            continue
        blocks.append(f"【第 {index + 1} 页】\n{text}" if total > 1 else text)
    return "\n\n".join(blocks), total


def _image_text(path: Path) -> str:
    """图片 → 文字。走 /api/ocr 同一套 rapidocr（单例，见 ocr_engine）。"""
    from .ocr_engine import get_ocr_engine

    result, _ = get_ocr_engine()(path.read_bytes())
    lines = [str(item[1]).strip() for item in (result or []) if len(item) > 1 and str(item[1]).strip()]
    return "\n".join(lines)


def _extract_cached(path: Path) -> tuple[str, int]:
    """抽取并缓存。key 带 mtime_ns：附件文件名是 uuid、内容不会变，但覆盖重传时能失效。

    缓存放进程内存、不落盘：抽取结果是派生数据，落 data/ 会污染真实数据目录；而服务
    本来就是单实例（会话存在进程内存里，CLAUDE.md 要求云上实例数固定为 1），重启丢
    缓存只是多花一次解析。key 里的绝对路径让测试的 tmp_path 天然互不串味。

    加锁是因为 `/api/chat` 是同步路由、FastAPI 把它丢进线程池跑，两个并发复盘会同时
    抽同一份文件。锁只护 dict 读写 —— 解析放锁外，否则一次 2.4s 的 PDF 会卡住所有人。
    """
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        value = _pdf_text(path)
    elif suffix in _IMAGE_SUFFIXES:
        value = (_image_text(path), 0)  # 图片没有「页数」这个概念
    else:
        raise RuntimeError(f"不支持的附件类型：{suffix or '未知'}")

    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.pop(next(iter(_cache)))  # FIFO：附件数量远小于 32，不用 LRU
        _cache[key] = value
    return value


def _kind_label(attachment: dict) -> str:
    mime = str(attachment.get("mimeType") or "")
    if mime == "application/pdf":
        return "PDF"
    if mime.startswith("image/"):
        return "图片"
    return mime or "附件"


def _scale_phrase(pages: int) -> str:
    """「共 10 页、」；图片没有页数就整段省掉。"""
    return f"共 {pages} 页、" if pages else ""


def extract_note_attachments(
    attachments, *, budget: int = FULL_TEXT_LIMIT, max_bytes: int = DEFAULT_MAX_BYTES
) -> AttachmentDigest:
    """把一篇笔记的附件读成「一块提示词文字」+「几行给用户看的人话」。

    刻意不抛异常：任何单点失败都降级成一行说明（见模块注释第 3 条）。
    """
    items = [att for att in (attachments or []) if isinstance(att, dict)]
    if not items:
        return AttachmentDigest()

    blocks: list[str] = []
    notes: list[str] = []
    used = 0

    for index, attachment in enumerate(items):
        name = str(attachment.get("name") or "").strip() or "未命名附件"
        if index >= MAX_ATTACHMENTS:
            notes.append(f"附件《{name}》：未读取（一次复盘最多读 {MAX_ATTACHMENTS} 个）")
            continue

        label = _kind_label(attachment)
        path = resolve_upload_path(str(attachment.get("url") or ""))
        if path is None:
            notes.append(f"附件《{name}》：文件不存在或不可读取")
            blocks.append(f"笔记附件《{name}》：文件不存在或不可读取，请勿就它的内容提问。")
            continue

        size = int(attachment.get("size") or 0)
        if size > max_bytes:
            notes.append(f"附件《{name}》：文件过大，未读取")
            blocks.append(f"笔记附件《{name}》：文件过大，未读取，请勿就它的内容提问。")
            continue

        try:
            text, pages = _extract_cached(path)
        except Exception:
            logger.exception("Failed to read note attachment %s", name)
            notes.append(f"附件《{name}》：读取失败，已跳过")
            blocks.append(f"笔记附件《{name}》：读取失败，请勿就它的内容提问。")
            continue

        text = text.strip()
        if not text:
            reason = "没有提取到文字（可能是扫描件）" if pages else "没有识别到文字"
            notes.append(f"附件《{name}》：{reason}")
            blocks.append(f"笔记附件《{name}》（{label}）：{reason}，请勿就它的内容提问。")
            continue

        remaining = budget - used
        if len(text) <= remaining:
            chunk, clipped = text, False
        elif remaining > 0:
            chunk, clipped = text[: min(HEAD_CHARS, remaining)], True
        else:
            notes.append(f"附件《{name}》：未读取（前面的附件已用满长度预算）")
            continue
        used += len(chunk)

        scale = _scale_phrase(pages)
        if clipped:
            blocks.append(
                f"笔记附件《{name}》（{label}，全文{scale}约 {len(text)} 字，"
                f"以下仅为开头 {len(chunk)} 字的节选）：\n{chunk}\n……（后续内容已省略）"
            )
            notes.append(f"已读取附件《{name}》：{label} {scale}{len(text)} 字，节选开头 {len(chunk)} 字")
        else:
            blocks.append(f"笔记附件《{name}》（{label}，{scale}{len(text)} 字）：\n{chunk}")
            notes.append(f"已读取附件《{name}》：{label} {scale}{len(text)} 字")

    return AttachmentDigest(block="\n\n".join(blocks), notes=notes)
