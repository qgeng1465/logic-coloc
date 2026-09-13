# -*- coding: utf-8 -*-
"""附件抽取层测试：路径解析（含目录穿越）、PDF/图片抽取、缓存、截断、降级。

全部离线：PDF 样本由 `_make_pdf` 现造（约 700 字节，不落 fixture 文件），图片走
monkeypatch 掉的假 OCR —— 既不碰网络，也不加载 onnxruntime 模型。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc.api import attachment_text, user_paths


@pytest.fixture(autouse=True)
def isolated_uploads(monkeypatch, tmp_path):
    """把上传目录换到临时目录，并清掉进程内缓存。

    只 patch `user_paths.UPLOAD_DIR` 一处就够 —— `resolve_upload_path` 是在函数体内
    取这个常量的（这是 user_paths 模块 docstring 立下的规矩）。

    缓存必须一起清：它是模块级 dict，跨用例存活；key 里虽然有 tmp_path 所以不会串味，
    但清掉更省心，也让「缓存命中」那条用例的计数是从 0 开始的。
    """
    monkeypatch.setattr(user_paths, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(attachment_text, "_cache", {})
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    return tmp_path / "uploads"


def _make_pdf(pages: list[str]) -> bytes:
    """离线造一个最小 PDF（base14 Helvetica，无压缩），返回字节。

    自己拼而不是用 `pypdf.PdfWriter`：`add_blank_page` 造不出**有字的页**，而「有文字层
    的 PDF」正是这里要测的东西。空串的页干脆不挂 /Contents —— 那就是「扫描件」的形状，
    `extract_text()` 会返回空串。
    """
    objects: dict[int, bytes] = {}
    # 对象号：1=Catalog 2=Pages，之后每页两号（Page、Contents），Font 排在所有页之后
    # —— 页数多于 1 时若把 Font 钉死在 5，会和第 2 页的 Page 号撞车，后写的把字体覆盖掉。
    font_number = 3 + 2 * len(pages)
    kids = []
    next_number = 3
    page_numbers = []
    for text in pages:
        page_no, content_no = next_number, next_number + 1
        next_number += 2
        page_numbers.append((page_no, content_no, text))
        kids.append(f"{page_no} 0 R")

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    objects[font_number] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for page_no, content_no, text in page_numbers:
        resources = f"<< /Font << /F1 {font_number} 0 R >> >>" if text else "<< >>"
        contents = f"/Contents {content_no} 0 R " if text else ""
        objects[page_no] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources {resources}{contents}>>"
        ).encode()
        if text:
            # 用 ( ) 包住字符串；测试文本限定 ASCII，不做转义处理
            stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
            objects[content_no] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)

    # 按对象号顺序写出，并记下每个对象的字节偏移给 xref 用
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"

    total = font_number + 1
    xref_offset = len(out)
    out += b"xref\n0 %d\n" % total
    out += b"0000000000 65535 f \n"
    for number in range(1, total):
        # xref 每行必须正好 20 字节，偏移固定 10 位、补 0
        out += b"%010d 00000 n \n" % offsets.get(number, 0)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (total, xref_offset)
    return bytes(out)


def _put(uploads: Path, name: str, data: bytes, *, scoped: bool = False) -> str:
    """把文件放进上传目录并返回它对外的那种 url（可指定老/新两种落盘形态）。"""
    directory = uploads / ("a" * 32) if scoped else uploads
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    prefix = f"uploads/{'a' * 32}/" if scoped else "uploads/"
    return f"http://127.0.0.1:8000/{prefix}{name}"


# ----------------------------------------------------------------- 路径解析 / 安全


@pytest.mark.parametrize("scoped", [False, True])
def test_resolve_upload_path_accepts_both_layouts(isolated_uploads, scoped: bool) -> None:
    """老（uploads/x.pdf）与新（uploads/<uid>/x.pdf）两种落盘形态都要认。

    实测真实数据里两种并存：账号体系之前上传的 3 个附件是扁平形态，之后的是带 uid 的。
    """
    url = _put(isolated_uploads, "note.pdf", b"%PDF-1.4\n", scoped=scoped)
    resolved = attachment_text.resolve_upload_path(url)
    assert resolved is not None
    assert resolved.name == "note.pdf"
    assert resolved.is_file()


def test_resolve_upload_path_accepts_relative_url(isolated_uploads) -> None:
    """url 也可能是相对路径（没有 host），同样要认。"""
    _put(isolated_uploads, "note.pdf", b"%PDF-1.4\n")
    assert attachment_text.resolve_upload_path("/uploads/note.pdf") is not None


@pytest.mark.parametrize(
    "url",
    [
        "/uploads/../secret.txt",
        "/uploads/%2e%2e/secret.txt",
        "/uploads/sub/../../secret.txt",
        "/uploads/..\\..\\secret.txt",
        "http://127.0.0.1:8000/uploads/../../secret.txt",
    ],
)
def test_resolve_upload_path_rejects_traversal(isolated_uploads, url: str) -> None:
    """目录穿越一律拒绝。

    这是本次唯一的真安全项：attachment.url 由客户端可控，而 /uploads 是匿名静态口，
    放行越界路径就等于「把任意文件读出来塞进提示词、发给第三方 LLM」——
    `/uploads/../../.env` 能直接读走含真实密钥的 .env。
    """
    canary = isolated_uploads.parent / "secret.txt"
    canary.write_text("CANARY-DO-NOT-LEAK", encoding="utf-8")
    assert attachment_text.resolve_upload_path(url) is None


def test_traversal_cannot_leak_file_contents(isolated_uploads, monkeypatch) -> None:
    """穿透过的那份文件，内容绝不能出现在抽取结果里。

    canary 故意用**后缀合法**的 `.png`，假 OCR 也换成「读交给它的那个文件」：若 canary 是个
    `.txt`，光靠「不支持的附件类型」就能挡住内容，这条用例的泄漏断言就成了摆设 ——
    实测把包含性校验删掉后，用 .txt 的版本反而是绿的。
    """
    monkeypatch.setattr(attachment_text, "_image_text", lambda path: path.read_text(encoding="utf-8"))
    canary = isolated_uploads.parent / "secret.png"
    canary.write_text("CANARY-DO-NOT-LEAK", encoding="utf-8")
    digest = attachment_text.extract_note_attachments(
        [{"name": "x", "mimeType": "image/png", "url": "/uploads/../secret.png"}]
    )
    assert "CANARY-DO-NOT-LEAK" not in digest.block
    assert digest.notes and "文件不存在或不可读取" in digest.notes[0]


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://evil.com/uploads/x.pdf",
        "file:///E:/logic_coloc/.env",
        "blob:http://127.0.0.1:8000/abc",
        "http://127.0.0.1:8000/uploads/",
        "https://example.com/x.pdf",
    ],
)
def test_resolve_upload_path_rejects_non_uploads_urls(isolated_uploads, url: str) -> None:
    assert attachment_text.resolve_upload_path(url) is None


def test_resolve_upload_path_rejects_directory(isolated_uploads) -> None:
    """指向目录也要拒（`is_file()` 那道）。"""
    (isolated_uploads / "adir").mkdir(parents=True, exist_ok=True)
    assert attachment_text.resolve_upload_path("/uploads/adir") is None


# --------------------------------------------------------------------- PDF 抽取


def test_pdf_text_is_extracted(isolated_uploads) -> None:
    pytest.importorskip("pypdf")
    url = _put(isolated_uploads, "a.pdf", _make_pdf(["Hello attachment", "Second page here"]))
    digest = attachment_text.extract_note_attachments(
        [{"name": "a.pdf", "mimeType": "application/pdf", "url": url}]
    )
    assert "Hello attachment" in digest.block
    assert "Second page here" in digest.block
    # 多于一页就该有页码标记，好让导师能问「第 2 页讲了什么」
    assert "【第 1 页】" in digest.block and "【第 2 页】" in digest.block
    assert "已读取附件《a.pdf》" in digest.notes[0]
    assert "共 2 页" in digest.notes[0]


def test_single_page_pdf_has_no_page_marker(isolated_uploads) -> None:
    """只有一页时不必加页码标记（省字数）。"""
    pytest.importorskip("pypdf")
    url = _put(isolated_uploads, "one.pdf", _make_pdf(["Only page"]))
    digest = attachment_text.extract_note_attachments(
        [{"name": "one.pdf", "mimeType": "application/pdf", "url": url}]
    )
    assert "Only page" in digest.block
    assert "【第 1 页】" not in digest.block


def test_pdf_without_text_layer_degrades(isolated_uploads) -> None:
    """扫描件（没有文字层）→ 如实说明，且绝不假装读到了内容。

    页码标记只写「有字的页」，所以整份扫描件抽出来是空串 —— 否则一堆页码标记就能把
    「空」这个判据骗过去。
    """
    pytest.importorskip("pypdf")
    url = _put(isolated_uploads, "scan.pdf", _make_pdf(["", ""]))
    digest = attachment_text.extract_note_attachments(
        [{"name": "scan.pdf", "mimeType": "application/pdf", "url": url}]
    )
    assert "没有提取到文字" in digest.block
    assert "笔记附件《scan.pdf》（PDF）" in digest.block
    assert "没有提取到文字" in digest.notes[0]


def test_corrupt_pdf_degrades_without_raising(isolated_uploads) -> None:
    """坏文件不能把异常抛到路由 —— 一个坏附件不该让整场复盘 500。"""
    pytest.importorskip("pypdf")
    url = _put(isolated_uploads, "broken.pdf", b"this is definitely not a pdf")
    digest = attachment_text.extract_note_attachments(
        [{"name": "broken.pdf", "mimeType": "application/pdf", "url": url}]
    )
    assert "读取失败" in digest.block
    assert "读取失败" in digest.notes[0]


# ------------------------------------------------------------------ 图片 / 降级


def test_image_attachment_uses_ocr(isolated_uploads, monkeypatch) -> None:
    """图片走 OCR（这里把 OCR 换成假的，不加载 onnxruntime）。"""
    monkeypatch.setattr(attachment_text, "_image_text", lambda path: "识别出来的字")
    url = _put(isolated_uploads, "shot.png", b"\x89PNG\r\n\x1a\nfake")
    digest = attachment_text.extract_note_attachments(
        [{"name": "shot.png", "mimeType": "image/png", "url": url}]
    )
    assert "识别出来的字" in digest.block
    assert "笔记附件《shot.png》（图片" in digest.block
    # 图片没有「页数」这个概念，不该出现「共 N 页」
    assert "共 " not in digest.notes[0]


def test_image_without_text_degrades(isolated_uploads, monkeypatch) -> None:
    monkeypatch.setattr(attachment_text, "_image_text", lambda path: "")
    url = _put(isolated_uploads, "blank.png", b"\x89PNG\r\n\x1a\nfake")
    digest = attachment_text.extract_note_attachments(
        [{"name": "blank.png", "mimeType": "image/png", "url": url}]
    )
    assert "没有识别到文字" in digest.block


def test_unsupported_suffix_degrades(isolated_uploads) -> None:
    """后缀不在白名单里（手改数据才可能出现，上传口本来就只放行 pdf/png/jpg）。"""
    url = _put(isolated_uploads, "doc.docx", b"PK\x03\x04fake")
    digest = attachment_text.extract_note_attachments(
        [{"name": "doc.docx", "mimeType": "application/msword", "url": url}]
    )
    assert "读取失败" in digest.block


def test_oversized_attachment_is_skipped(isolated_uploads) -> None:
    url = _put(isolated_uploads, "big.pdf", b"%PDF-1.4\n")
    digest = attachment_text.extract_note_attachments(
        [{"name": "big.pdf", "mimeType": "application/pdf", "size": 999 * 1024 * 1024, "url": url}],
        max_bytes=10 * 1024 * 1024,
    )
    assert "文件过大" in digest.block


def test_never_raises_on_junk_attachment_entries(isolated_uploads) -> None:
    """附件字段被手改成各种奇怪东西时也不能抛。"""
    digest = attachment_text.extract_note_attachments(
        [
            {"name": None, "url": None},
            {"name": 123, "url": 456},
            "not-a-dict",
            {"name": "ok.pdf", "mimeType": "application/pdf", "url": "/uploads/missing.pdf"},
        ]
    )
    assert "未命名附件" in digest.block
    assert "文件不存在或不可读取" in digest.block


# ------------------------------------------------------------------ 缓存 / 截断


def test_extraction_is_cached(isolated_uploads, monkeypatch) -> None:
    """同一份附件连抽两次只读一次文件（抽取几百 ms~几秒，而每轮都会走这条路径）。"""
    calls = []
    real = attachment_text._pdf_text

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(attachment_text, "_pdf_text", counting)
    pytest.importorskip("pypdf")
    url = _put(isolated_uploads, "c.pdf", _make_pdf(["cached text"]))
    attachment = [{"name": "c.pdf", "mimeType": "application/pdf", "url": url}]
    first = attachment_text.extract_note_attachments(attachment)
    second = attachment_text.extract_note_attachments(attachment)
    assert len(calls) == 1
    assert first.block == second.block


def test_cache_key_notices_a_replaced_file(isolated_uploads, monkeypatch) -> None:
    """文件被换掉（size/mtime 变了）要重新读，不能拿旧内容糊弄。"""
    pytest.importorskip("pypdf")
    url = _put(isolated_uploads, "r.pdf", _make_pdf(["original text"]))
    attachment = [{"name": "r.pdf", "mimeType": "application/pdf", "url": url}]
    assert "original text" in attachment_text.extract_note_attachments(attachment).block
    _put(isolated_uploads, "r.pdf", _make_pdf(["replaced text after reupload"]))
    assert "replaced text after reupload" in attachment_text.extract_note_attachments(attachment).block


def test_long_attachment_is_clipped_and_says_so(isolated_uploads) -> None:
    """超预算的附件只进开头 HEAD_CHARS，且**如实写明真实规模**。

    不写「已节选」的话，模型会以为自己看到的就是全文，然后问出「第 30 页讲了什么」
    这种它自己都不知道答案的问题。
    """
    long_text = "长" * 50_000
    url = _put(isolated_uploads, "long.pdf", _make_pdf([long_text]))
    pytest.importorskip("pypdf")
    digest = attachment_text.extract_note_attachments(
        [{"name": "long.pdf", "mimeType": "application/pdf", "url": url}]
    )
    assert "节选" in digest.block
    assert "后续内容已省略" in digest.block
    assert "节选开头" in digest.notes[0]
    # 块里不能真塞进 5 万字
    assert len(digest.block) < 10_000


def test_short_attachment_is_not_marked_as_clipped(isolated_uploads) -> None:
    """没超预算就不能说自己是节选（否则白白吓唬模型）。"""
    pytest.importorskip("pypdf")
    url = _put(isolated_uploads, "s.pdf", _make_pdf(["short enough"]))
    digest = attachment_text.extract_note_attachments(
        [{"name": "s.pdf", "mimeType": "application/pdf", "url": url}]
    )
    assert "short enough" in digest.block
    assert "节选" not in digest.block
    assert "节选" not in digest.notes[0]


def test_budget_is_shared_across_attachments(isolated_uploads) -> None:
    """前面附件用掉的额度要算进去，后面的不能各自从头再来一遍。"""
    pytest.importorskip("pypdf")
    first = _put(isolated_uploads, "f.pdf", _make_pdf(["A" * 30]))
    second = _put(isolated_uploads, "g.pdf", _make_pdf(["B" * 30]))
    digest = attachment_text.extract_note_attachments(
        [
            {"name": "f.pdf", "mimeType": "application/pdf", "url": first},
            {"name": "g.pdf", "mimeType": "application/pdf", "url": second},
        ],
        budget=10,  # 只够第一个附件的一部分
    )
    assert "A" in digest.block
    assert "B" not in digest.block
    assert any("未读取（前面的附件已用满长度预算）" in note for note in digest.notes)


def test_more_than_max_attachments_are_named(isolated_uploads) -> None:
    """超出的附件要**点名说明**，不能静默丢弃。"""
    attachments = []
    for index in range(attachment_text.MAX_ATTACHMENTS + 2):
        url = _put(isolated_uploads, f"m{index}.pdf", _make_pdf([f"text {index}"]))
        attachments.append({"name": f"m{index}.pdf", "mimeType": "application/pdf", "url": url})
    pytest.importorskip("pypdf")
    digest = attachment_text.extract_note_attachments(attachments)
    assert any("未读取（一次复盘最多读" in note for note in digest.notes)
    assert len(digest.notes) == len(attachments)


# ---------------------------------------------------------------------- 空输入


def test_no_attachments_returns_empty_digest() -> None:
    """没有附件时块必须是空串 —— 提示词才能和加这个功能之前逐字一致。"""
    for empty in (None, [], (), {}):
        digest = attachment_text.extract_note_attachments(empty)
        assert digest.block == ""
        assert digest.notes == []
