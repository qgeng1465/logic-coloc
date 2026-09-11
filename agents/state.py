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
