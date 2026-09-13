# -*- coding: utf-8 -*-
"""笔记复盘对话记录的本地持久化（与 AI 逻辑无关）。

会话本体存在 SessionManager 的内存 dict 里，服务一重启就没了；这个 store 把
「完整对话」落到 data/review_records.json，供复盘页的「历史记录」回看。
一条记录的 id 就是本次复盘那个真 session_id —— 一次复盘天然只对应一条记录。
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from . import user_paths
from .card_store import _iso, utc_now


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
REVIEWS_FILE = DATA_DIR / "review_records.json"
# 每轮复盘都要 read-modify-write 同一个文件，锁住整个动作而不是单次读写。
_reviews_lock = Lock()


def _reviews_path(user_id: str) -> Path:
    """复盘记录文件路径。带 user_id 走该账号的目录，为空则维持旧的全局文件。"""
    if user_id:
        user_paths.ensure_user_storage(user_id)
    return user_paths.scoped(user_id, REVIEWS_FILE, "review_records.json")


def _read_unlocked(path: Path) -> list[dict[str, Any]]:
    """读记录。读不动就回退空列表，不让复盘流程因此挂掉。

    内容坏掉时**先把坏文件挪到 .bak 再返回空**：直接返回 [] 的话，下一次 append 会用
    一条新记录把整个文件覆盖掉，等于静默清空用户全部历史；挪走至少留下抢救的可能。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        try:
            path.replace(path.with_suffix(".bak"))
        except OSError:
            pass
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _write_unlocked(path: Path, records: list[dict[str, Any]]) -> None:
    """原子写：先写 .tmp 再 replace，避免中途失败留下半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _find(records: list[dict[str, Any]], record_id: str) -> dict[str, Any] | None:
    return next((record for record in records if record.get("id") == record_id), None)


def load_reviews(*, user_id: str = "") -> list[dict[str, Any]]:
    """全部记录，按开始时间倒序（不依赖文件里的插入顺序）。"""
    path = _reviews_path(user_id)
    with _reviews_lock:
        records = _read_unlocked(path)
    # startedAt 是统一的 ISO-8601 UTC 字符串，字典序倒序即时间倒序。
    records.sort(key=lambda record: str(record.get("startedAt") or ""), reverse=True)
    return records


def get_review(record_id: str, *, user_id: str = "") -> dict[str, Any] | None:
    with _reviews_lock:
        return _find(_read_unlocked(_reviews_path(user_id)), record_id)


def append_review_turn(
    record_id: str,
    *,
    question: str,
    answer: str,
    note_id: str = "",
    note_title: str = "",
    user_id: str = "",
) -> dict[str, Any]:
    """追加一轮问答；记录不存在就新建（此刻落 startedAt）。"""
    now = _iso(utc_now())
    path = _reviews_path(user_id)
    with _reviews_lock:
        records = _read_unlocked(path)
        record = _find(records, record_id)
        if record is None:
            record = {
                "id": record_id,
                "noteId": note_id,
                "noteTitle": note_title,
                "startedAt": now,
                "updatedAt": now,
                "messages": [],
                "summary": "",
            }
            records.insert(0, record)
        # 标题/笔记 id 可能在某几轮才带上，缺了就补，已有值不覆盖。
        if note_id and not record.get("noteId"):
            record["noteId"] = note_id
        if note_title and not record.get("noteTitle"):
            record["noteTitle"] = note_title
        messages = record.setdefault("messages", [])
        messages.append({"role": "user", "content": question, "at": now})
        messages.append({"role": "assistant", "content": answer, "at": now})
        record["updatedAt"] = now
        _write_unlocked(path, records)
    return record


def save_review_summary(record_id: str, summary: str, *, user_id: str = "") -> dict[str, Any] | None:
    now = _iso(utc_now())
    path = _reviews_path(user_id)
    with _reviews_lock:
        records = _read_unlocked(path)
        record = _find(records, record_id)
        if record is None:
            return None
        record["summary"] = summary
        record["updatedAt"] = now
        _write_unlocked(path, records)
    return record


def delete_review(record_id: str, *, user_id: str = "") -> bool:
    path = _reviews_path(user_id)
    with _reviews_lock:
        records = _read_unlocked(path)
        remaining = [record for record in records if record.get("id") != record_id]
        if len(remaining) == len(records):
            return False
        _write_unlocked(path, remaining)
    return True


def review_list_item(record: dict[str, Any]) -> dict[str, Any]:
    """列表投影：故意不带 messages —— 一场复盘可能聊得很长，列表不需要全文。"""
    messages = record.get("messages") or []
    return {
        "id": record.get("id", ""),
        "noteId": record.get("noteId", ""),
        "noteTitle": record.get("noteTitle", ""),
        "startedAt": record.get("startedAt", ""),
        "updatedAt": record.get("updatedAt", ""),
        "turnCount": sum(1 for message in messages if message.get("role") == "user"),
        "summary": record.get("summary", ""),
    }
