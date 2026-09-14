# -*- coding: utf-8 -*-
"""FastAPI routes for Logic-Coloc."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urlencode
import secrets

import requests

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from ..agents import tools
from ..agents.graph import DiscoveryCancelled
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
from .schemas import CardPayload, CardReviewPayload, ChatRequest, ChatResponse, CredentialsPayload, DiscoverRequest, DiscoverResponse, ExplainRequest, ExplainResponse, LibraryPayload, NoteMovePayload, NotePayload, OCRResponse, PointsPayload, ProfilePayload, SessionResponse, UserUpdatePayload, ZhihuSearchItem, ZhihuSearchRequest, ZhihuSearchResponse, ZhihuResearchRequest, ZhihuResearchResponse, ZhihuLibrarySyncRequest
from .card_store import due_cards, load_cards, review_card, save_card, _parse_timestamp
from .library_store import load_library, save_library
from .service import LLMBackendUnavailableError, LogicColocService, ServiceError
from . import zhihu_store
from .. import config
from .. import feature_extractor


router = APIRouter(prefix="/api")
_service = LogicColocService()
logger = logging.getLogger(__name__)
# Discovery performs feature extraction plus several grounded learning reports.
# Keep the request bounded, but allow the configured LLM enough time to finish.
DISCOVER_TIMEOUT_SECONDS = int(__import__("os").environ.get("LC_DISCOVER_TIMEOUT", "240"))
# 复盘小结是后台副作用，用户没在等它。llm() 自身没有单次超时，
# 端点数掉时会重试 3 次 × config.TIMEOUT（默认 180s），不封顶会占住线程池近 9 分钟。
REVIEW_SUMMARY_TIMEOUT_SECONDS = int(os.environ.get("LC_REVIEW_SUMMARY_TIMEOUT", "60"))
_discover_cancel_events: dict[tuple[str, str], tuple[threading.Event, float]] = {}
_discover_cancel_lock = threading.Lock()
_discover_jobs: dict[tuple[str, str], dict] = {}
_discover_jobs_lock = threading.Lock()
_zhihu_rate_lock = threading.Lock()
_zhihu_last_request_at = 0.0
ZHIHU_MIN_REQUEST_INTERVAL = float(os.environ.get("LC_ZHIHU_REQUEST_INTERVAL", "1.5"))
ZHIHU_CACHE_TTL_SECONDS = int(os.environ.get("LC_ZHIHU_CACHE_TTL", "1800"))
_zhihu_search_cache: dict[tuple[str, int], tuple[float, ZhihuSearchResponse]] = {}
_zhihu_oauth_states: dict[str, tuple[str, float]] = {}


def _wait_for_zhihu_slot() -> None:
    """Serialize upstream calls so research mode cannot burst three requests at Zhihu."""
    global _zhihu_last_request_at
    with _zhihu_rate_lock:
        remaining = ZHIHU_MIN_REQUEST_INTERVAL - (time.monotonic() - _zhihu_last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        _zhihu_last_request_at = time.monotonic()


def _discover_event(user_id: str, request_id: str) -> threading.Event:
    now = time.monotonic()
    with _discover_cancel_lock:
        # 请求量很小；顺手清理一小时前的标识，避免客户端断线后常驻。
        stale = [key for key, (_, created) in _discover_cancel_events.items() if now - created > 3600]
        for key in stale:
            _discover_cancel_events.pop(key, None)
        existing = _discover_cancel_events.get((user_id, request_id))
        # “取消”请求可能比分析请求更早到达服务器；复用预先置位的事件，避免快点
        # 取消时出现前端已停、后端却仍完整运行的竞态。
        event = existing[0] if existing else threading.Event()
        _discover_cancel_events[(user_id, request_id)] = (event, now)
        return event


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


@router.post("/auth/guest")
def guest_route() -> dict:
    """免注册的匿名身份：浏览器首次访问时静默调它，直接拿到一份可用的登录态。

    **这是本路由表里第 5 个匿名口**（其余三个见 CLAUDE.md 的路由表），故意的 ——
    把登录从「必经之路」变成「可选项」正是它存在的意义。签发的 token 与注册账号
    完全同源，后续所有接口一视同仁，数据同样隔离在 `data/users/<uid>/`。

    滥用面：每个请求都会往 `data/users.json` 追加一条记录，所以由
    `config.MAX_GUESTS` 封顶（超限淘汰最早的匿名记录，见 auth_store.create_guest）。
    """
    user = auth_store.create_guest()
    return {"code": 0, "token": auth_store.issue_token(user["id"]), "user": user}


@router.post("/auth/upgrade")
def upgrade_route(payload: CredentialsPayload, user: dict = Depends(current_user)) -> dict:
    """给匿名身份补一个用户名密码 —— 「保存我的知识 / 换台设备继续用」时才需要。

    uid 不变，所以已经攒下的笔记、卡片、能量一个都不会丢。返回的 token 只是给前端
    一个和登录一致的形状；旧 token 依然有效（无状态签名，服务端没有可吊销的表）。
    """
    try:
        bound = auth_store.bind_credentials(user["id"], payload.username, payload.password)
    except auth_store.UsernameTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except auth_store.NotAGuestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # 用户名/密码格式不合规：请求本身的问题，422 而不是 500。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "token": auth_store.issue_token(bound["id"]), "user": bound}


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


@router.post("/zhihu/search", response_model=ZhihuSearchResponse)
def zhihu_search(request: ZhihuSearchRequest, user: dict = Depends(current_user)) -> ZhihuSearchResponse:
    """Search Zhihu through its official developer API; never scrape article pages."""
    secret = os.environ.get("ZHIHU_ACCESS_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="尚未配置知乎开放平台 Access Secret，请在后端环境变量中设置 ZHIHU_ACCESS_SECRET")
    cache_key = (request.query.strip().casefold(), request.count)
    cached = _zhihu_search_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] <= ZHIHU_CACHE_TTL_SECONDS:
        return cached[1]
    _wait_for_zhihu_slot()
    try:
        response = requests.get(
            "https://developer.zhihu.com/api/v1/content/zhihu_search",
            params={"Query": request.query.strip(), "Count": request.count},
            headers={
                "Authorization": f"Bearer {secret}",
                "X-Request-Timestamp": str(int(datetime.now(timezone.utc).timestamp())),
                "Content-Type": "application/json",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        logger.info("Zhihu developer API request failed: %s", exc)
        raise HTTPException(status_code=502, detail="暂时无法连接知乎开放平台，请稍后重试") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="知乎开放平台返回了无法识别的数据") from exc
    api_code = int(payload.get("Code", payload.get("code", 0)) or 0)
    if not response.ok or api_code != 0:
        messages = {
            20001: "知乎开放平台鉴权失败，请检查 Access Secret",
            30001: "知乎搜索调用过于频繁或今日额度已用完",
            90001: "知乎开放平台内部错误，请稍后重试",
        }
        detail = messages.get(api_code) or str(payload.get("Message") or payload.get("message") or "知乎搜索失败")
        # 30001 是频率/额度限制，不是网关故障。明确返回 429，研究模式才能退避重试，
        # 并在后续角度失败时保留已经拿到的内容。
        status_code = 429 if api_code == 30001 else 502
        raise HTTPException(status_code=status_code, detail=detail, headers={"Retry-After": "3"} if status_code == 429 else None)
    data = payload.get("Data") or payload.get("data") or {}
    raw_items = data.get("Items") or data.get("items") or []
    items = [ZhihuSearchItem(
        url=str(item.get("Url") or item.get("url") or ""),
        title=str(item.get("Title") or item.get("title") or ""),
        summary=str(item.get("ContentText") or item.get("content_text") or ""),
        content_type=str(item.get("ContentType") or item.get("content_type") or ""),
        author_name=str(item.get("AuthorName") or item.get("author_name") or ""),
        vote_up_count=int(item.get("VoteUpCount") or item.get("vote_up_count") or 0),
        comment_count=int(item.get("CommentCount") or item.get("comment_count") or 0),
    ) for item in raw_items if isinstance(item, dict)]
    result = ZhihuSearchResponse(items=items)
    _zhihu_search_cache[cache_key] = (time.monotonic(), result)
    # 小型进程内缓存足够保护额度；限制条数，避免长期运行无限增长。
    if len(_zhihu_search_cache) > 100:
        oldest = min(_zhihu_search_cache, key=lambda key: _zhihu_search_cache[key][0])
        _zhihu_search_cache.pop(oldest, None)
    return result


@router.post("/zhihu/research", response_model=ZhihuResearchResponse)
def zhihu_research(request: ZhihuResearchRequest, user: dict = Depends(current_user)) -> ZhihuResearchResponse:
    """Turn selected official Zhihu snippets into one grounded, source-listed study note."""
    source_lines = []
    for index, item in enumerate(request.items, 1):
        source_lines.append(f"[{index}] 标题：{item.title}\n作者：{item.author_name or '未知'}；赞同：{item.vote_up_count}；评论：{item.comment_count}\n摘要：{item.summary or '暂无摘要'}\n原文：{item.url}")
    prompt = f"学习主题：{request.topic}\n\n知乎资料（只能依据这些摘要，不得补写摘要中没有的事实）：\n" + "\n\n".join(source_lines)
    system = """你是严谨的学习笔记编辑。请把多篇知乎摘要整合成一篇适合初学者阅读的中文学习笔记。
要求：去重合并共同知识；明确标出不同文章新增的观点；不把赞同数当作事实正确性；对摘要没有覆盖的内容不要臆测。
输出严格 JSON，不要 Markdown 代码围栏，格式：{"title":"不超过30字的标题","content":"完整笔记正文"}
正文必须包含以下小标题：一、先建立整体理解；二、核心概念与运行机制；三、不同资料补充的观点；四、容易混淆的地方与边界；五、学习后的自测问题；六、参考来源。
“参考来源”必须逐条列出 [1]...[N]，包含标题、作者、赞同/评论数量和原文 URL。"""
    try:
        raw = feature_extractor.llm(system, prompt, max_tokens=3000, temperature=0.2)
        data = feature_extractor.extract_json(raw)
        title = str(data.get("title") or f"{request.topic}：知乎资料整合笔记").strip()
        content = str(data.get("content") or "").strip()
        if not content:
            raise ValueError("empty research note")
        return ZhihuResearchResponse(title=title, content=content, source_count=len(request.items))
    except Exception as exc:
        logger.exception("Zhihu research note generation failed: %s", exc)
        raise HTTPException(status_code=502, detail="知乎资料整合失败，请稍后重试") from exc

@router.get("/zhihu/oauth/authorize")
def zhihu_oauth_authorize(request: Request, user: dict = Depends(current_user)) -> dict:
    """Return the official OAuth URL. Endpoints/scopes are configured by deployment."""
    client_id = os.environ.get("ZHIHU_OAUTH_CLIENT_ID", "").strip()
    authorize = os.environ.get("ZHIHU_OAUTH_AUTHORIZE_URL", "https://www.zhihu.com/oauth/authorize").strip()
    if not client_id:
        raise HTTPException(status_code=503, detail="尚未配置知乎 OAuth Client ID")
    state = secrets.token_urlsafe(24); _zhihu_oauth_states[state] = (user["id"], time.monotonic())
    redirect_uri = os.environ.get("ZHIHU_OAUTH_REDIRECT_URI", str(request.base_url).rstrip("/") + "/api/zhihu/oauth/callback")
    scope = os.environ.get("ZHIHU_OAUTH_SCOPE", "read")
    return {"url": authorize + "?" + urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": scope, "state": state})}

@router.get("/zhihu/oauth/callback")
def zhihu_oauth_callback(request: Request, code: str = "", state: str = "", error: str = "") -> dict:
    record = _zhihu_oauth_states.pop(state, None)
    if not record or time.monotonic() - record[1] > 600: raise HTTPException(status_code=400, detail="OAuth state 无效或已过期")
    if error or not code: raise HTTPException(status_code=400, detail="知乎授权未完成")
    token_url = os.environ.get("ZHIHU_OAUTH_TOKEN_URL", "").strip()
    if not token_url: raise HTTPException(status_code=503, detail="尚未配置知乎 OAuth Token 地址")
    try:
        response = requests.post(token_url, data={"grant_type":"authorization_code", "code":code, "client_id":os.environ.get("ZHIHU_OAUTH_CLIENT_ID", ""), "client_secret":os.environ.get("ZHIHU_OAUTH_CLIENT_SECRET", ""), "redirect_uri":os.environ.get("ZHIHU_OAUTH_REDIRECT_URI", str(request.base_url).rstrip("/") + "/api/zhihu/oauth/callback")}, timeout=20)
        response.raise_for_status(); payload = response.json()
    except (requests.RequestException, ValueError) as exc: raise HTTPException(status_code=502, detail="知乎 OAuth 换取令牌失败") from exc
    token = {k: payload.get(k) for k in ("access_token", "refresh_token", "expires_in", "scope") if payload.get(k) is not None}
    path = user_paths.ensure_user_storage(record[0]) / "zhihu_oauth.json"; path.write_text(__import__('json').dumps(token, ensure_ascii=False), encoding="utf-8")
    return {"code": 0, "connected": True, "scope": token.get("scope", "")}

@router.get("/zhihu/library")
def zhihu_library(user: dict = Depends(current_user)) -> dict:
    return {"code": 0, "items": zhihu_store.load(user["id"])}

@router.post("/zhihu/library/sync")
def zhihu_library_sync(request: ZhihuLibrarySyncRequest, user: dict = Depends(current_user)) -> dict:
    items = [item.model_dump() for item in request.items]
    groups = [("计算机与人工智能", "人工智能 深度学习 机器学习 神经网络 算法 编程 数据"), ("数学与统计", "数学 统计 概率 微积分"), ("工程与控制", "自动控制 工程 机械 电路"), ("学习方法", "学习 教育 读书 方法"), ("社会科学", "社会 心理 经济 历史")]
    for item in items:
        text = (item.get("title", "") + " " + item.get("summary", "")).lower()
        item["categories"] = [name for name, words in groups if any(word in text for word in words.split())] or ["待整理"]
    return {"code": 0, "items": zhihu_store.merge(user["id"], items)}

@router.get("/zhihu/library/similar")
def zhihu_library_similar(query: str, user: dict = Depends(current_user)) -> dict:
    terms = {x for x in query.lower().split() if len(x) > 1}
    matches = []
    for item in zhihu_store.load(user["id"]):
        words = set((item.get("title", "") + " " + item.get("summary", "")).lower().split())
        score = len(terms & words) / max(1, len(terms))
        if score >= 0.2: matches.append((score, item))
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return {"code": 0, "items": [item for _, item in matches[:3]]}


def _run_discover_job(request: DiscoverRequest, user_id: str, request_id: str) -> None:
    """在后台线程运行长发现任务；HTTP 请求本身立即返回，规避云网关 504。"""
    key = (user_id, request_id)
    try:
        cancel_event = _discover_event(user_id, request_id)
        with _discover_jobs_lock:
            _discover_jobs[key]["status"] = "running"
        result = _service.discover(request, user_id=user_id, cancel_check=cancel_event.is_set)
        with _discover_jobs_lock:
            _discover_jobs[key].update(status="done", result=result)
    except DiscoveryCancelled:
        with _discover_jobs_lock:
            _discover_jobs[key].update(status="cancelled", result=DiscoverResponse(code=499, message="分析已取消", concept=Concept(name=request.text), errors=["DISCOVERY_CANCELLED"]))
    except Exception as exc:
        logger.exception("Background discover job failed")
        with _discover_jobs_lock:
            _discover_jobs[key].update(status="done", result=_discover_failure(request))
    finally:
        with _discover_cancel_lock:
            _discover_cancel_events.pop(key, None)


@router.post("/discover", response_model=None)
async def discover(request: DiscoverRequest, service: LogicColocService = Depends(get_service), user: dict = Depends(current_user)) -> DiscoverResponse:
    if request.async_mode:
        request_id = request.request_id or secrets.token_urlsafe(18)
        key = (user["id"], request_id)
        with _discover_jobs_lock:
            existing = _discover_jobs.get(key)
            if not existing or existing.get("status") in {"done", "cancelled"}:
                _discover_jobs[key] = {"status": "queued", "result": None, "created": time.monotonic()}
                threading.Thread(target=_run_discover_job, args=(request, user["id"], request_id), daemon=True).start()
        return {"code": 0, "task_id": request_id, "status": "queued", "async": True}
    cancel_event = _discover_event(user["id"], request.request_id) if request.request_id else None
    try:
        return await asyncio.wait_for(
            run_in_threadpool(service.discover, request, user_id=user["id"], cancel_check=cancel_event.is_set if cancel_event else None),
            timeout=DISCOVER_TIMEOUT_SECONDS,
        )
    except DiscoveryCancelled:
        return DiscoverResponse(code=499, message="分析已取消", concept=Concept(name=request.text), errors=["DISCOVERY_CANCELLED"])
    except asyncio.TimeoutError:
        logger.exception("Discover request timed out after %s seconds", DISCOVER_TIMEOUT_SECONDS)
        return _discover_failure(request)
    except ServiceError:
        logger.exception("Discover model service failed")
        return _discover_failure(request)
    except Exception:
        logger.exception("Unexpected discover request failure")
        return _discover_failure(request)
    finally:
        if request.request_id:
            with _discover_cancel_lock:
                _discover_cancel_events.pop((user["id"], request.request_id), None)


@router.get("/discover/{request_id}")
def discover_status(request_id: str, user: dict = Depends(current_user)) -> dict:
    key = (user["id"], request_id)
    with _discover_jobs_lock:
        job = _discover_jobs.get(key)
        if not job:
            raise HTTPException(status_code=404, detail="discover task not found")
        result = job.get("result")
        status = job.get("status", "queued")
        if result is not None:
            return {"code": result.code, "status": status, "done": True, "result": result.model_dump()}
        return {"code": 0, "status": status, "done": False}


@router.post("/discover/{request_id}/cancel")
def cancel_discover(request_id: str, user: dict = Depends(current_user)) -> dict:
    with _discover_cancel_lock:
        entry = _discover_cancel_events.get((user["id"], request_id))
        event = entry[0] if entry else threading.Event()
        event.set()
        _discover_cancel_events[(user["id"], request_id)] = (event, time.monotonic())
    return {"code": 0, "cancelled": True}


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
