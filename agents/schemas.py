# -*- coding: utf-8 -*-
"""Shared Pydantic schemas for the Logic-Coloc agent layer."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..feature_extractor import DIMS


CORE_DIMENSIONS = tuple(DIMS)


class LogicProfile(BaseModel):
    """Normalized 0.0-1.0 representation of the existing five-dimension profile."""

    model_config = ConfigDict(extra="forbid")

    DIMENSIONS: ClassVar[tuple[str, ...]] = (
        "system_closure",
        "causal_chain_length",
        "negative_feedback_strength",
        "randomness_entropy",
        "zero_sum_resource_level",
    )

    if DIMENSIONS != CORE_DIMENSIONS:
        raise RuntimeError(f"LogicProfile dimensions do not match feature_extractor.DIMS: {CORE_DIMENSIONS}")

    system_closure: float = Field(..., ge=0.0, le=1.0)
    causal_chain_length: float = Field(..., ge=0.0, le=1.0)
    negative_feedback_strength: float = Field(..., ge=0.0, le=1.0)
    randomness_entropy: float = Field(..., ge=0.0, le=1.0)
    zero_sum_resource_level: float = Field(..., ge=0.0, le=1.0)

    @classmethod
    def from_core_vector(cls, vector: Sequence[float]) -> "LogicProfile":
        """Create a normalized profile from the current core 0-100 vector."""
        if len(vector) != len(cls.DIMENSIONS):
            raise ValueError(f"expected {len(cls.DIMENSIONS)} values, got {len(vector)}")
        data = {name: float(value) / 100.0 for name, value in zip(cls.DIMENSIONS, vector)}
        return cls(**data)

    @classmethod
    def from_core_scores(cls, scores: dict[str, float]) -> "LogicProfile":
        """Create a normalized profile from a current core score mapping."""
        return cls(**{name: float(scores[name]) / 100.0 for name in cls.DIMENSIONS})

    def to_core_vector(self) -> list[float]:
        """Return values in the current core 0-100 vector order."""
        return [getattr(self, name) * 100.0 for name in self.DIMENSIONS]

    def to_core_scores(self) -> dict[str, float]:
        """Return a current core-compatible 0-100 score mapping."""
        return {name: getattr(self, name) * 100.0 for name in self.DIMENSIONS}


class EvidenceSource(BaseModel):
    """A curated, externally verifiable source supporting corpus facts."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    publisher_or_author: str
    url_or_identifier: str
    locator: str = ""
    supports: list[str] = Field(default_factory=list)


class Concept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    domain: str | None = None
    description: str | None = None
    mechanism: str | None = None
    key_terms: list[str] = Field(default_factory=list)


class KnowledgeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_text: str
    concept: Concept
    logic_profile: LogicProfile | None = None
    domain: str | None = None
    mechanism: str | None = None
    summary: str = ""
    key_terms: list[str] = Field(default_factory=list)


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    source: dict[str, Any] = Field(default_factory=dict)
    knowledge: KnowledgeContext
    conversation_summary: str = ""
    recent_messages: list[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # 会话归属的账号。默认空串是为了兼容既有测试与匿名调用；路由层永远会填。
    # 会话 id 是 uuid4，本身不可猜，但"知道 id 就能读"会让按账号隔离的落盘文件
    # 之外多出一条旁路，所以 /api/chat 与 /api/session/{id} 会校验它。
    owner_id: str = ""


class Intent(str, Enum):
    EXPLAIN = "EXPLAIN"
    SIMPLIFY = "SIMPLIFY"
    EXAMPLE = "EXAMPLE"
    TERM = "TERM"
    WHY = "WHY"
    COMPARE = "COMPARE"
    FOLLOW_UP = "FOLLOW_UP"
    DISCOVER_HOMOLOGY = "DISCOVER_HOMOLOGY"


class HomologyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    score: float = Field(..., ge=0.0, le=1.0)
    method: Literal["cosine", "wasserstein"] = "cosine"
    threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    passed: bool | None = None
    # Public discovery-card fields.  They are optional so the deterministic
    # homology engine and its existing callers remain unchanged.
    concept: str | None = None
    domain: str | None = None
    retrieval_score: float | None = Field(default=None, ge=0.0, le=1.0)
    reliability: str | None = None
    summary: str | None = None
    mechanism: str | None = None
    critique: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list)
    retrieval_score_reason: str | None = None
    homonomy_score_reason: str | None = None
    retrieval_source: str = "vector_db"
    homonomy_source: str = "llm_rubric"

    @field_validator("passed", mode="before")
    @classmethod
    def _allow_missing_passed(cls, value: bool | None) -> bool | None:
        return value

    def model_post_init(self, __context: Any) -> None:
        if self.passed is None:
            object.__setattr__(self, "passed", self.score >= self.threshold)


class MappingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    a_terms: list[str] = Field(default_factory=list)
    b_terms: list[str] = Field(default_factory=list)
    mapping: dict[str, str] = Field(default_factory=dict)


MappingType = Literal["entity", "process", "signal", "state", "objective", "constraint", "failure_mode"]
Verdict = Literal["RELIABLE_WITH_LIMITS", "NEEDS_REVIEW", "REJECTED", "INSUFFICIENT_EVIDENCE"]


class DimensionComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    source_value: float = Field(..., ge=0.0, le=1.0)
    candidate_value: float = Field(..., ge=0.0, le=1.0)
    similarity: float = Field(..., ge=0.0, le=1.0)
    plain_language_reason: str


class MappingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_term: str
    source_type: MappingType
    source_role: str
    target_term: str
    target_type: MappingType
    target_role: str
    correspondence_reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    type_match: bool = False


class LearningSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    explanation: str
    connection_to_source: str
    example: str
    check_question: str
    check_answer: str = ""


class RecommendedBook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    author: str = ""
    reason: str
    scope: str = ""


class DiscoverLearningReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    source_concept: Concept
    source_primer: str
    candidate_concept: Concept
    candidate_primer: str
    retrieval_score: float = Field(..., ge=0.0, le=1.0)
    homonomy_result: HomologyResult
    dimension_comparisons: list[DimensionComparison] = Field(default_factory=list)
    mechanism_summary: str
    intersection_lesson: str = ""
    target_domain_lessons: list[LearningSection] = Field(default_factory=list)
    transferable_knowledge: list[str] = Field(default_factory=list)
    new_knowledge: list[str] = Field(default_factory=list)
    understanding_checks: list[str] = Field(default_factory=list)
    mapping_evidence: list[MappingEvidence] = Field(default_factory=list)
    valid_conditions: list[str] = Field(default_factory=list)
    failure_boundaries: list[str] = Field(default_factory=list)
    known_differences: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    source_references: list[EvidenceSource] = Field(default_factory=list)
    verdict: Verdict
    verdict_reason: str
    learning_next_steps: list[str] = Field(default_factory=list)
    recommended_books: list[RecommendedBook] = Field(default_factory=list)


class CritiqueResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    is_valid: bool
    issues: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    summary: str = ""
    verdict: Verdict | None = None
