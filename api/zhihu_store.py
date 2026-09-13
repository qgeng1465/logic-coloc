# -*- coding: utf-8 -*-
"""Per-user Zhihu library (favorites/votes imported through permitted APIs)."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from threading import Lock
from . import user_paths

_lock = Lock()

def _path(user_id: str):
    user_paths.ensure_user_storage(user_id)
    return user_paths.scoped(user_id, user_paths.DATA_DIR / "zhihu_library.json", "zhihu_library.json")

def load(user_id: str) -> list[dict]:
    try:
        value = json.loads(_path(user_id).read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []

def merge(user_id: str, items: list[dict]) -> list[dict]:
    with _lock:
        current = {str(x.get("url")): x for x in load(user_id) if x.get("url")}
        now = datetime.now(timezone.utc).isoformat()
        for item in items:
            url = str(item.get("url", "")).split("?", 1)[0].rstrip("/")
            if not url: continue
            old = current.get(url, {})
            merged = {**old, **item, "url": url, "id": old.get("id") or item.get("id") or url, "updated_at": now}
            merged["saved_at"] = old.get("saved_at") or item.get("saved_at") or now
            merged["categories"] = sorted(set(old.get("categories", [])) | set(item.get("categories", [])))
            current[url] = merged
        result = list(current.values())
        p = _path(user_id); p.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result
