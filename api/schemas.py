# -*- coding: utf-8 -*-
"""Pydantic request and response schemas for the future API layer."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any, Literal

from ..agents.schemas import (
    Concept,
    CritiqueResult,
    DiscoverLearningReport,
    HomologyResult,
    LogicProfile,
    MappingResult,
    Session,
)
from ..rag.schemas import CandidateConcept


def _require_non_blank(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError("must not be blank")
    return value


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    title: str = ""
    source: str = ""

    _validate_text = field_validator("text")(_require_non_blank)


class ExplainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    concept: Concept
    logic_profile: LogicProfile | None = None
    explanation: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    message: str
    note_content: str | None = None
    # 笔记复盘专用：note_content 非空时后端会把这一轮落到 data/review_records.json，
    # 这两个字段只用于给记录打标题标签。普通对话不传。
    note_id: str = ""
    note_title: str = ""

    _validate_required_text = field_validator("session_id", "message")(_require_non_blank)


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    answer: str
    updated_summary: str = ""
    # 笔记复盘读附件的结果，一行一个附件（"已读取附件《x.pdf》：PDF 共 116 页、66458 字，
    # 节选开头 8000 字" / "附件《y.pdf》：没有提取到文字（可能是扫描件）"）。普通对话为空。
    # 存在的意义是：扫描件这类降级否则对用户完全不可见，用户只会觉得「AI 读不懂我的附件」。
    attachment_notes: list[str] = Field(default_factory=list)


class UserUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nickname: str = ""
    signature: str = ""


class CredentialsPayload(BaseModel):
    """注册与登录共用。不收集邮箱、不发验证码——没有邮件服务，加了也只是个摆设。"""

    model_config = ConfigDict(extra="forbid")

    username: str
    password: str


class ProfilePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nickname: str | None = None
    signature: str | None = None
    avatarUrl: str | None = None


class PointsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 只接受增量，不接受绝对值：服务端是能量的唯一权威，客户端说"我现在有 9999 分"
    # 不该被当真。夹在 ±1000 是防手滑写出个荒谬的值。
    delta: int = Field(ge=-1000, le=1000)
    reason: str = ""


class DiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    session_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=50)
    # 前端生成的一次性任务标识，用于在用户取消时通知后端停止后续阶段。
    request_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")

    _validate_required_text = field_validator("text", "session_id")(_require_non_blank)


class DiscoverCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: CandidateConcept
    homonomy_score: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_score_reason: str = ""
    homonomy_score_reason: str = ""
    mapping: MappingResult | None = None
    critique: CritiqueResult | None = None
    learning_report: DiscoverLearningReport | None = None


class DiscoverResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int = 0
    message: str = ""
    session_id: str = ""
    concept: Concept
    candidates: list[DiscoverCandidateResult] = Field(default_factory=list)
    results: list[HomologyResult] = Field(default_factory=list)
    mappings: list[MappingResult] = Field(default_factory=list)
    critiques: list[CritiqueResult] = Field(default_factory=list)
    learning_reports: list[DiscoverLearningReport] = Field(default_factory=list)
    report: str = ""
    errors: list[str] = Field(default_factory=list)


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: Session


class OCRResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int = 0
    text: str


class ZhihuSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    count: int = Field(default=5, ge=1, le=10)

    _validate_query = field_validator("query")(_require_non_blank)


class ZhihuSearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = ""
    summary: str = ""
    content_type: str = ""
    author_name: str = ""
    vote_up_count: int = 0
    comment_count: int = 0


class ZhihuSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int = 0
    items: list[ZhihuSearchItem] = Field(default_factory=list)


class ZhihuResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    items: list[ZhihuSearchItem] = Field(min_length=2, max_length=6)

    _validate_topic = field_validator("topic")(_require_non_blank)


class ZhihuResearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int = 0
    title: str
    content: str
    source_count: int = 0

class ZhihuLibraryItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = ""
    title: str = ""
    url: str = ""
    summary: str = ""
    author_name: str = ""
    source_type: str = "favorite"
    saved_at: str = ""
    categories: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

class ZhihuLibrarySyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ZhihuLibraryItem] = Field(default_factory=list, max_length=2000)

class CardPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    front: str
    back: str
    source: str = ""
    sessionId: str = ""
    createdAt: str = ""
    status: str = "unmastered"
    last_reviewed_at: str | None = None
    next_review_due: str | None = None
    review_stage: int = Field(default=0, ge=0)
    ease_factor: float = Field(default=2.5, gt=0)


class CardReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality: Literal["忘记了", "模糊", "掌握", "forgot", "vague", "mastered"]


class NoteAttachment(BaseModel):
    model_config = ConfigDict(extra="allow")

    noteId: str
    name: str
    url: str
    mimeType: str = ""
    size: int = 0
    syncStatus: str = "synced"


class NotePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    content: str = ""
    coverUrl: str = ""
    folderId: str | None = None
    attachments: list[NoteAttachment] = Field(default_factory=list)


class NoteMovePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    folderId: str | None = None


class LibraryPayload(BaseModel):
    """书架 / 笔记本的整份提交。

    两个字段都可选，且**默认 None 而不是 []**：前端保存书架时不该顺手把笔记本
    覆盖成空，反之亦然。`None` = 这次不动这一半，`[]` = 明确要清空这一半。

    内容用 `Any` 而不是声明 BookPayload / CategoryPayload：这两样的字段前端还在
    演进（卡片内嵌在书里，字段一直在加），逐字段建模会让每次前端加个字段就 422。
    落盘前 `library_store` 会做"只收 dict"的清洗，所以放宽到这里是安全的。
    """

    model_config = ConfigDict(extra="forbid")

    books: Any | None = None
    categories: Any | None = None
