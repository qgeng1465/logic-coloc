# -*- coding: utf-8 -*-
"""Pydantic schemas for local corpus and retrieval results."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..agents.schemas import EvidenceSource, LogicProfile


class CandidateConcept(BaseModel):
    """A corpus candidate returned by RAG before deterministic homology scoring."""

    model_config = ConfigDict(extra="forbid")

    id: str
    concept: str
    domain: str
    description: str = ""
    mechanism: str = ""
    logic_profile: LogicProfile
    keywords: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    mechanism_roles: dict[str, str] = Field(default_factory=dict)
    valid_conditions: list[str] = Field(default_factory=list)
    common_misconceptions: list[str] = Field(default_factory=list)
    sources: list[EvidenceSource] = Field(default_factory=list)
    retrieval_score: float = Field(default=0.0, ge=0.0, le=1.0)


class CorpusRecord(CandidateConcept):
    """On-disk JSONL corpus record for RAG V0."""
