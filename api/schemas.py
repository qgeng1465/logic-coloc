# -*- coding: utf-8 -*-
"""Pydantic request and response schemas for the future API layer."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    _validate_required_text = field_validator("session_id", "message")(_require_non_blank)


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    answer: str
    updated_summary: str = ""


class DiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    session_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=50)

    _validate_required_text = field_validator("text", "session_id")(_require_non_blank)


class DiscoverCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: CandidateConcept
    homonomy_score: float | None = Field(default=None, ge=0.0, le=1.0)
    mapping: MappingResult | None = None
    critique: CritiqueResult | None = None
    learning_report: DiscoverLearningReport | None = None


class DiscoverResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
