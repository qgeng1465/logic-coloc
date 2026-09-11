# -*- coding: utf-8 -*-
"""Small file-backed store for H5 notes and attachments."""
from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Lock
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = PROJECT_ROOT / "uploads"
NOTES_FILE = DATA_DIR / "notes.json"
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_notes_lock = Lock()


def _normalize_attachment_ownership(notes: list[dict]) -> tuple[list[dict], bool]:
    """Bind legacy attachments to one note and discard cross-note duplicates."""
    seen: set[str] = set()
    changed = False
    for note in notes:
        if note.get("folderId") == "default" or note.get("categoryId") == "default":
            note["folderId"] = None
            note.pop("categoryId", None)
            changed = True
        note_id = str(note.get("id", ""))
        owned = []
        for attachment in note.get("attachments", []):
            key = str(attachment.get("id") or attachment.get("url") or f"{attachment.get('name')}:{attachment.get('size')}")
            owner = str(attachment.get("noteId") or note_id)
            if owner != note_id or key in seen:
                changed = True
                continue
            if attachment.get("noteId") != note_id:
                attachment = {**attachment, "noteId": note_id}
                changed = True
            seen.add(key)
            owned.append(attachment)
        note["attachments"] = owned
    return notes, changed


def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not NOTES_FILE.exists():
        NOTES_FILE.write_text("[]\n", encoding="utf-8")


def safe_upload_name(original_name: str) -> str:
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("unsupported file type")
    stem = Path(original_name).stem or "file"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "file"
    return f"{uuid4().hex}_{stem}{suffix}"


def load_notes() -> list[dict]:
    ensure_storage()
    with _notes_lock:
        try:
            value = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            value = []
        notes, changed = _normalize_attachment_ownership(value if isinstance(value, list) else [])
        if changed:
            temporary = NOTES_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(NOTES_FILE)
        return notes


def save_note(note: dict) -> dict:
    note_id = str(note["id"])
    if note.get("folderId") == "default" or note.get("categoryId") == "default":
        note["folderId"] = None
        note.pop("categoryId", None)
    note["attachments"] = [
        {**attachment, "noteId": note_id}
        for attachment in note.get("attachments", [])
        if not attachment.get("noteId") or str(attachment.get("noteId")) == note_id
    ]
    ensure_storage()
    with _notes_lock:
        try:
            notes = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            notes = []
        if not isinstance(notes, list):
            notes = []
        index = next((i for i, item in enumerate(notes) if item.get("id") == note["id"]), -1)
        if index >= 0:
            notes[index] = note
        else:
            notes.append(note)
        temporary = NOTES_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(NOTES_FILE)
    return note


def move_note(note_id: str, folder_id: str | None) -> dict | None:
    ensure_storage()
    with _notes_lock:
        try:
            notes = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            notes = []
        note = next((item for item in notes if str(item.get("id")) == note_id), None)
        if note is None:
            return None
        note["folderId"] = folder_id
        temporary = NOTES_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(NOTES_FILE)
        return note
