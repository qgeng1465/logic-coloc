# -*- coding: utf-8 -*-
"""FastAPI routes for Logic-Coloc."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from ..agents import tools
from ..agents.schemas import Concept

from ..sessions.manager import SessionNotFoundError
from . import auth_store, user_paths
from .attachment_text import AttachmentDigest, extract_note_attachments
from .note_store import MAX_UPLOAD_BYTES, ensure_storage, load_notes, move_note, safe_upload_name, save_note
from .review_store import (
    append_review_turn,
    delete_review,
    get_review,
    load_reviews,
    review_list_item,
    save_review_summary,
)
from .schemas import CardPayload, CardReviewPayload, ChatRequest, ChatResponse, CredentialsPayload, DiscoverRequest, DiscoverResponse, ExplainRequest, ExplainResponse, LibraryPayload, NoteMovePayload, NotePayload, OCRResponse, PointsPayload, ProfilePayload, SessionResponse, UserUpdatePayload
from .card_store import due_cards, load_cards, review_card, save_card, _parse_timestamp
from .library_store import load_library, save_library
from .service import LLMBackendUnavailableError, LogicColocService, ServiceError
from .. import config


router = APIRouter(prefix="/api")
_service = LogicColocService()
logger = logging.getLogger(__name__)
# Discovery performs feature extraction plus several grounded learning reports.
# Keep the request bounded, but allow the configured LLM enough time to finish.
DISCOVER_TIMEOUT_SECONDS = int(__import__("os").environ.get("LC_DISCOVER_TIMEOUT", "240"))
# 复盘小结是后台副作用，用户没在等它。llm() 自身没有单次超时，
# 端点数掉时会重试 3 次 × config.TIMEOUT（默认 180s），不封顶会占住线程池近 9 分钟。
REVIEW_SUMMARY_TIMEOUT_SECONDS = int(os.environ.get("LC_REVIEW_SUMMARY_TIMEOUT", "60"))


def get_service() -> LogicColocService:
    return _service


def current_user(authorization: str | None = Header(default=None)) -> dict:
    """认证依赖：从 `Authorization: Bearer <token>` 解出当前账号。

    任何一步失败都抛同一个 401、同一句文案，不区分「没带 token」「签名不对」「过期」
    「账号已不存在」—— 客户端处理方式都一样（回登录页），细分只是白送给攻击者信息。
    """
    token = ""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            token = value.strip()
    user_id = auth_store.verify_token(token) if token else None
    user = auth_store.get_user(user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


def _store_upload(file_bytes: bytes, original_name: str, user_id: str, request: Request) -> str:
    """落盘一个上传文件并返回可访问的 URL。上传目录按账号分子目录。"""
    try:
        filename = safe_upload_name(original_name)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    path = user_paths.scoped_upload(user_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(file_bytes)
    base = str(request.base_url).rstrip("/")
    return f"{base}/uploads/{user_id}/{filename}"


@router.post("/auth/register")
def register_route(payload: CredentialsPayload) -> dict:
    try:
        user = auth_store.register(payload.username, payload.password)
    except auth_store.UsernameTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        # 用户名/密码格式不合规：这是请求本身的问题，给 422 而不是 500。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "token": auth_store.issue_token(user["id"]), "user": user}


@router.post("/auth/login")
def login_route(payload: CredentialsPayload) -> dict:
    user = auth_store.authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    return {"code": 0, "token": auth_store.issue_token(user["id"]), "user": user}


@router.get("/auth/me")
def me_route(user: dict = Depends(current_user)) -> dict:
    """前端启动时用它校验 localStorage 里那个 token 是否还有效。"""
    return {"code": 0, "user": user, "profile": auth_store.load_profile(user["id"])}


@router.get("/user/profile")
def get_user_profile_route(user: dict = Depends(current_user)) -> dict:
    return {"code": 0, "profile": auth_store.load_profile(user["id"])}


@router.post("/user/profile")
def update_user_profile_route(payload: ProfilePayload, user: dict = Depends(current_user)) -> dict:
    # exclude_none：只改传上来的字段，没传的保持原值（传空串是「我要清空」，仍然生效）。
    fields = payload.model_dump(exclude_none=True)
    return {"code": 0, "profile": auth_store.save_profile(user["id"], **fields)}


@router.post("/user/points")
def add_user_points_route(payload: PointsPayload, user: dict = Depends(current_user)) -> dict:
    return {"code": 0, "points": auth_store.add_points(user["id"], payload.delta, payload.reason)}


@router.post("/user/avatar")
async def upload_user_avatar_route(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
) -> dict:
    """上传头像并写进资料。

    这个接口此前**根本不存在**（前端 web/app.js 一直在 POST 它，拿到 404 后靠 catch
    把头像塞回 localStorage），顺带在这里补上。
    """
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image uploads are supported")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="empty file")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file is too large")
    url = _store_upload(content, file.filename or "avatar.png", user["id"], request)
    auth_store.save_profile(user["id"], avatarUrl=url)
    return {"code": 0, "url": url}


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
def explain(request: ExplainRequest, service: LogicColocService = Depends(get_service), user: dict = Depends(current_user)) -> ExplainResponse:
    try:
        return service.explain(request, user_id=user["id"])
    except Exception as exc:
        _raise_http_error(exc)


def _read_review_attachments(request: ChatRequest, user_id: str) -> AttachmentDigest:
    """把这篇笔记的附件读成文字，交给 service 拼进导师提示词。

    在路由层读而不是在 service 里读，理由同下面 `append_review_turn` 上方那段注释
    （service 不 import 任何 store）。找不到笔记、读笔记失败都不抛 —— 复盘照常凭正文
    进行，只是没有附件材料。
    """
    note_id = (request.note_id or "").strip()
    if not note_id:
        return AttachmentDigest()
    try:
        notes = load_notes(user_id=user_id)
    except Exception:
        logger.exception("Failed to load notes while reading review attachments")
        return AttachmentDigest()
    for note in notes:
        if str(note.get("id", "")) == note_id:
            return extract_note_attachments(note.get("attachments"), max_bytes=MAX_UPLOAD_BYTES)
    return AttachmentDigest()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, service: LogicColocService = Depends(get_service), user: dict = Depends(current_user)) -> ChatResponse:
    # 只有复盘轮次才去碰附件：普通对话没有 note_content，也就没有笔记可读。
    attachment = _read_review_attachments(request, user["id"]) if request.note_content else AttachmentDigest()
    try:
        response = service.chat(request, user_id=user["id"], attachment=attachment)
    except Exception as exc:
        _raise_http_error(exc)
    if request.note_content:
        # 笔记复盘每轮都落盘，让对话在服务重启后仍能回看。三点注意：
        # - 放路由层而不是 service 层：service 不 import 任何 store，notes/cards 也都在这里落盘；
        # - 存 request.message（用户真实那句话），不是 service 拼出来的导师指令模板；
        # - request.session_id 已被 service.chat 就地改写成后端生成的真 id，一条复盘对应一条记录。
        #
        # 落盘是聊天的副作用，不该让它把已经拿到的回答变成 500：磁盘满/只读/文件被占用时
        # 只记日志，回答照常返回。（notes/cards 可以失败，因为那是用户显式点「保存」。）
        try:
            append_review_turn(
                request.session_id,
                question=request.message,
                answer=response.answer,
                note_id=request.note_id,
                note_title=request.note_title,
                user_id=user["id"],
            )
        except Exception:
            logger.exception("Failed to persist note review turn")
    return response


def _discover_failure(request: DiscoverRequest) -> DiscoverResponse:
    """Return a stable public response without exposing model or gateway details."""
    return DiscoverResponse(
        code=500,
        message="模型服务异常，请稍后重试",
        concept=Concept(name=request.text),
        errors=["MODEL_SERVICE_UNAVAILABLE"],
    )


@router.post("/discover", response_model=DiscoverResponse)
async def discover(request: DiscoverRequest, service: LogicColocService = Depends(get_service), user: dict = Depends(current_user)) -> DiscoverResponse:
    try:
        return await asyncio.wait_for(
            run_in_threadpool(service.discover, request, user_id=user["id"]),
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
def get_session(session_id: str, service: LogicColocService = Depends(get_service), user: dict = Depends(current_user)) -> SessionResponse:
    try:
        return service.get_session(session_id, user_id=user["id"])
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/ocr", response_model=OCRResponse)
async def ocr(file: UploadFile = File(...), service: LogicColocService = Depends(get_service), user: dict = Depends(current_user)) -> OCRResponse:
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
async def upload(request: Request, file: UploadFile = File(...), user: dict = Depends(current_user)) -> dict:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="empty file")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file is too large")
    return {"code": 0, "url": _store_upload(content, file.filename or "file", user["id"], request)}


@router.post("/notes/save")
def save_note_route(note: NotePayload, user: dict = Depends(current_user)) -> dict:
    save_note(note.model_dump(), user_id=user["id"])
    return {"code": 0, "message": "success"}


@router.get("/library")
def get_library_route(user: dict = Depends(current_user)) -> dict:
    """书架 + 笔记本。新账号第一次进来就是两个空数组 —— 这里**不能**塞任何默认内容。

    前端启动时拿它决定书架是空的还是满的；服务端为空而本地有数据时，前端会把本地
    推上来（老账号的迁移路径）。
    """
    library = load_library(user_id=user["id"])
    return {"code": 0, **library}


@router.post("/library")
def save_library_route(payload: LibraryPayload, user: dict = Depends(current_user)) -> dict:
    """只覆盖请求里**非 None** 的那一半，返回合并后的完整库。

    前端保存书架和保存笔记本走同一个接口，靠字段是否为 None 区分；传 `[]` 表示
    明确清空。这个区分不能省 —— 否则删光最后一本书会连带把笔记本一起清掉。
    """
    library = save_library(payload.books, payload.categories, user_id=user["id"])
    return {"code": 0, **library}

@router.post("/cards/save")
def save_card_route(card: CardPayload, user: dict = Depends(current_user)) -> dict:
    saved = save_card(card.model_dump(), user_id=user["id"])
    return {"code": 0, "message": "success", "card": saved}


@router.get("/cards/due")
def get_due_cards_route(user: dict = Depends(current_user)) -> dict:
    cards = due_cards(user_id=user["id"])
    return {"code": 0, "count": len(cards), "cards": cards}


@router.get("/cards/schedule")
def get_cards_schedule_route(user: dict = Depends(current_user)) -> dict:
    """Return review cards ordered by their next Ebbinghaus review time."""
    cards = [card for card in load_cards(user_id=user["id"]) if card.get("next_review_due")]
    cards.sort(key=lambda card: (_parse_timestamp(card.get("next_review_due")) or datetime.max.replace(tzinfo=timezone.utc)).timestamp())
    return {"code": 0, "cards": cards}


@router.post("/user/update")
def update_user_route(payload: UserUpdatePayload, user: dict = Depends(current_user)) -> dict:
    """兼容旧路径：前端「保存资料」一直在打这里（改前是个什么都不存的 echo 桩）。

    只覆盖非空字段：请求里漏传某个字段时不该把它清空。
    """
    fields = {key: value for key, value in payload.model_dump().items() if value}
    profile = auth_store.save_profile(user["id"], **fields)
    return {"code": 0, "message": "success", "user": user, "profile": profile}


@router.post("/cards/{card_id}/review")
def review_card_route(card_id: str, payload: CardReviewPayload, user: dict = Depends(current_user)) -> dict:
    card = review_card(card_id, payload.quality, user_id=user["id"])
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    return {"code": 0, "message": "success", "card": card}


@router.get("/notes")
def get_notes_route(user: dict = Depends(current_user)) -> dict:
    return {"code": 0, "notes": load_notes(user_id=user["id"])}


@router.patch("/notes/move")
def move_note_route(payload: NoteMovePayload, user: dict = Depends(current_user)) -> dict:
    note = move_note(payload.id, payload.folderId, user_id=user["id"])
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return {"code": 0, "message": "success", "note": note}


@router.get("/reviews")
def get_reviews_route(user: dict = Depends(current_user)) -> dict:
    """复盘记录列表。刻意不含 messages —— 一场复盘可能聊很长，列表不需要全文。"""
    return {"code": 0, "reviews": [review_list_item(record) for record in load_reviews(user_id=user["id"])]}


@router.get("/reviews/{review_id}")
def get_review_route(review_id: str, user: dict = Depends(current_user)) -> dict:
    record = get_review(review_id, user_id=user["id"])
    if record is None:
        raise HTTPException(status_code=404, detail="review not found")
    return {"code": 0, "review": record}


@router.post("/reviews/{review_id}/summary")
async def save_review_summary_route(review_id: str, force: bool = False, user: dict = Depends(current_user)) -> dict:
    """生成并落盘复盘小结。

    前端在关闭复盘页时发一次，详情页发现没有小结时再兜底发一次。因此这里**幂等**：
    已有小结直接返回，不重复烧 LLM；确实想重新生成才带 ?force=1。
    """
    record = get_review(review_id, user_id=user["id"])
    if record is None:
        raise HTTPException(status_code=404, detail="review not found")
    existing = str(record.get("summary") or "")
    if existing and not force:
        return {"code": 0, "summary": existing, "cached": True}
    messages = record.get("messages") or []
    if not messages:
        raise HTTPException(status_code=422, detail="review transcript is empty")
    try:
        summary = await asyncio.wait_for(
            run_in_threadpool(tools.summarize_review, str(record.get("noteTitle") or ""), messages),
            timeout=REVIEW_SUMMARY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        logger.exception("Review summary timed out after %s seconds", REVIEW_SUMMARY_TIMEOUT_SECONDS)
        raise LLMBackendUnavailableError() from exc
    except ValueError as exc:
        # 空对话记录是请求本身的问题，不是模型后端不可用，别混成 503。
        raise HTTPException(status_code=422, detail="review transcript is empty") from exc
    except Exception as exc:
        logger.exception("Review summary generation failed")
        raise LLMBackendUnavailableError() from exc
    save_review_summary(review_id, summary, user_id=user["id"])
    return {"code": 0, "summary": summary}


@router.delete("/reviews/{review_id}")
def delete_review_route(review_id: str, user: dict = Depends(current_user)) -> dict:
    if not delete_review(review_id, user_id=user["id"]):
        raise HTTPException(status_code=404, detail="review not found")
    return {"code": 0, "message": "success"}
