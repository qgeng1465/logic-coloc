# -*- coding: utf-8 -*-
"""FastAPI routes for Logic-Coloc."""
from __future__ import annotations

import asyncio
import logging
import socket
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from ..agents.schemas import Concept

from ..sessions.manager import SessionNotFoundError
from .note_store import MAX_UPLOAD_BYTES, UPLOAD_DIR, ensure_storage, load_notes, move_note, safe_upload_name, save_note
from .schemas import CardPayload, ChatRequest, ChatResponse, DiscoverRequest, DiscoverResponse, ExplainRequest, ExplainResponse, NoteMovePayload, NotePayload, OCRResponse, SessionResponse
from pathlib import Path
import json
from .service import LogicColocService, ServiceError
from .. import config


router = APIRouter(prefix="/api")
_service = LogicColocService()
logger = logging.getLogger(__name__)
# Discovery performs feature extraction plus several grounded learning reports.
# Keep the request bounded, but allow the configured LLM enough time to finish.
DISCOVER_TIMEOUT_SECONDS = int(__import__("os").environ.get("LC_DISCOVER_TIMEOUT", "240"))


def get_service() -> LogicColocService:
    return _service


@router.get("/health")
def health() -> dict:
    """Expose dependency health without leaking API keys or request content."""
    parsed = urlparse(config.BRIDGE)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    bridge_ok = False
    detail = ""
    try:
        with socket.create_connection((host, port), timeout=1.5):
            bridge_ok = True
    except OSError as exc:
        detail = f"{type(exc).__name__}: {exc}"
    return {
        "code": 0 if bridge_ok else 503,
        "api": "ok",
        "llm_bridge": {"ok": bridge_ok, "host": host, "port": port, "detail": detail},
        "model": config.MODEL,
    }


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, ServiceError):
        raise exc
    if isinstance(exc, SessionNotFoundError):
        raise HTTPException(status_code=404, detail="session not found") from exc
    raise HTTPException(status_code=500, detail="internal service error") from exc


@router.post("/explain", response_model=ExplainResponse)
def explain(request: ExplainRequest, service: LogicColocService = Depends(get_service)) -> ExplainResponse:
    try:
        return service.explain(request)
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, service: LogicColocService = Depends(get_service)) -> ChatResponse:
    try:
        return service.chat(request)
    except Exception as exc:
        _raise_http_error(exc)


def _discover_failure(request: DiscoverRequest) -> DiscoverResponse:
    """Return a stable public response without exposing model or gateway details."""
    return DiscoverResponse(
        code=500,
        message="模型服务异常，请稍后重试",
        concept=Concept(name=request.text),
        errors=["MODEL_SERVICE_UNAVAILABLE"],
    )


@router.post("/discover", response_model=DiscoverResponse)
async def discover(request: DiscoverRequest, service: LogicColocService = Depends(get_service)) -> DiscoverResponse:
    try:
        return await asyncio.wait_for(
            run_in_threadpool(service.discover, request),
            timeout=DISCOVER_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.exception("Discover request timed out after %s seconds", DISCOVER_TIMEOUT_SECONDS)
        return _discover_failure(request)
    except ServiceError:
        logger.exception("Discover model service failed")
        return _discover_failure(request)
    except Exception as exc:
        logger.exception("Unexpected discover request failure")
        return _discover_failure(request)


@router.get("/session/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, service: LogicColocService = Depends(get_service)) -> SessionResponse:
    try:
        return service.get_session(session_id)
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/ocr", response_model=OCRResponse)
async def ocr(file: UploadFile = File(...), service: LogicColocService = Depends(get_service)) -> OCRResponse:
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image uploads are supported")
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="empty image")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image is too large")
    try:
        return service.ocr(image_bytes)
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)) -> dict:
    try:
        filename = safe_upload_name(file.filename or "file")
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="empty file")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file is too large")
    ensure_storage()
    (UPLOAD_DIR / filename).write_bytes(content)
    base = str(request.base_url).rstrip("/")
    return {"code": 0, "url": f"{base}/uploads/{filename}"}


@router.post("/notes/save")
def save_note_route(note: NotePayload) -> dict:
    save_note(note.model_dump())
    return {"code": 0, "message": "success"}

@router.post("/cards/save")
def save_card_route(card: CardPayload) -> dict:
    path = Path(__file__).resolve().parents[1] / "data" / "cards.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try: cards = json.loads(path.read_text(encoding="utf-8"))
    except Exception: cards = []
    cards = [item for item in cards if item.get("id") != card.id]
    cards.insert(0, card.model_dump())
    path.write_text(json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"code": 0, "message": "success", "card": card.model_dump()}


@router.get("/notes")
def get_notes_route() -> dict:
    return {"code": 0, "notes": load_notes()}


@router.patch("/notes/move")
def move_note_route(payload: NoteMovePayload) -> dict:
    note = move_note(payload.id, payload.folderId)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return {"code": 0, "message": "success", "note": note}
