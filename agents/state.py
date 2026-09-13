# -*- coding: utf-8 -*-
"""LangGraph-compatible state model for Logic-Coloc."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..rag.schemas import CandidateConcept
from .schemas import (
    Concept,
    CritiqueResult,
    DiscoverLearningReport,
    HomologyResult,
    Intent,
    KnowledgeContext,
    LogicProfile,
    MappingResult,
    Message,
)


class AgentState(BaseModel):
    """Structured state shared by future LangGraph nodes."""

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    user_input: str | None = None
    # 存进会话历史的文本。默认 None = 沿用 user_input（普通对话的行为一字不变）。
    # 笔记复盘要显式设成用户原话：user_input 里拼着笔记正文和附件全文，而会话历史
    # 每轮全量重发（上限 10 条），整段塞进去就会被放大成 11 份。
    stored_input: str | None = None
    # 笔记复盘这类纯对话轮次：user_input 是导师指令而不是待分析的文本，
    # 对它做逻辑特征提取会让 LLM 跟着指令回答、不输出 JSON（实测 500），
    # 且提取结果在 generate_response 里根本用不到，故整轮跳过。
    skip_extraction: bool = False
    top_k: int = Field(default=3, ge=1, le=5)
    intent: Intent | None = None
    concept: Concept | None = None
    domain: str | None = None
    mechanism: str | None = None
    logic_profile: LogicProfile | None = None
    knowledge_context: KnowledgeContext | None = None
    conversation_summary: str | None = None
    recent_messages: list[Message] = Field(default_factory=list)
    candidate_concepts: list[CandidateConcept] = Field(default_factory=list)
    retrieval_scores: dict[str, float] = Field(default_factory=dict)
    homonomy_results: list[HomologyResult] = Field(default_factory=list)
    mapping_results: list[MappingResult] = Field(default_factory=list)
    critique_results: list[CritiqueResult] = Field(default_factory=list)
    learning_reports: list[DiscoverLearningReport] = Field(default_factory=list)
    final_response: str | None = None
    errors: list[str] = Field(default_factory=list)
    # 仅在进程内传递，不进入序列化结果。发现同源的每个昂贵阶段都会调用它。
    cancel_check: object | None = Field(default=None, exclude=True)
