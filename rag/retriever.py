# -*- coding: utf-8 -*-
"""Deterministic string-based Retriever for RAG V0."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..homonomy import homonomy_score
from ..agents.schemas import LogicProfile
from .schemas import CandidateConcept


class Retriever:
    """Rank corpus concepts using explainable, local string matching."""

    MIN_RETRIEVAL_SCORE = 0.12

    # Mechanism aliases bridge everyday wording and curated disciplinary terms.
    # They are deliberately narrow: each group describes the same control
    # structure, not merely related subject matter.
    TERM_GROUPS = (
        {"负反馈", "反馈控制", "自我纠错", "纠偏", "闭环控制", "误差修正"},
        {"稳态", "动态平衡", "稳定", "调节", "自稳态"},
        {"扰动", "干扰", "波动", "偏差", "误差"},
    )

    WEIGHTS = {
        "concept": 0.30,
        "keyword": 0.30,
        "text": 0.20,
        "domain": 0.10,
        "logic_profile": 0.10,
    }

    def __init__(self, corpus: Any) -> None:
        self.corpus = corpus

    @staticmethod
    def _norm(value: object) -> str:
        return str(value).strip().lower()

    @classmethod
    def _contains(cls, haystack: object, needle: object) -> bool:
        left, right = cls._norm(haystack), cls._norm(needle)
        if not left or not right:
            return False
        if right in left or left in right:
            return True
        return any(
            any(term in left for term in group) and any(term in right for term in group)
            for group in cls.TERM_GROUPS
        )

    @classmethod
    def _query_parts(cls, query: str | dict[str, Any]) -> tuple[str, LogicProfile | None]:
        if isinstance(query, str):
            return query, None
        if not isinstance(query, dict):
            raise TypeError("query must be a string or a mapping with a 'text' field")
        text = query.get("text", "")
        if not isinstance(text, str):
            raise TypeError("query['text'] must be a string")
        profile_data = query.get("logic_profile")
        profile = None if profile_data is None else (
            profile_data if isinstance(profile_data, LogicProfile) else LogicProfile.model_validate(profile_data)
        )
        return text, profile

    @classmethod
    def _keyword_score(cls, candidate: CandidateConcept, query_text: str, keywords: Sequence[str] | None) -> float:
        # Use both model-extracted terms and explicit words in the source. A
        # three-term LLM summary can omit an obvious anchor such as “负反馈”.
        requested = [cls._norm(item) for item in keywords or [] if cls._norm(item)]
        requested.extend(
            term for group in cls.TERM_GROUPS for term in group
            if term in cls._norm(query_text) and term not in requested
        )
        if requested:
            matched = sum(any(cls._contains(candidate_kw, item) for candidate_kw in candidate.keywords) for item in requested)
            return matched / len(requested)
        if not candidate.keywords:
            return 0.0
        matched = sum(1 for item in candidate.keywords if cls._contains(query_text, item))
        return matched / len(candidate.keywords)

    @classmethod
    def _score(cls, candidate: CandidateConcept, query_text: str, keywords: Sequence[str] | None, domain: str | None, profile: LogicProfile | None) -> float:
        concept_score = 1.0 if cls._contains(query_text, candidate.concept) else 0.0
        keyword_score = cls._keyword_score(candidate, query_text, keywords)
        text_score = max(
            (1.0 if cls._contains(query_text, field) else 0.0)
            for field in (candidate.description, candidate.mechanism)
        )
        domain_score = 1.0 if domain is None and cls._contains(query_text, candidate.domain) else 0.0
        logic_score = 0.0
        if profile is not None:
            logic_score = float(homonomy_score(profile.to_core_vector(), candidate.logic_profile.to_core_vector()))
        active = {"concept": concept_score, "keyword": keyword_score, "text": text_score, "domain": domain_score, "logic_profile": logic_score}
        active_weights = sum(weight for name, weight in cls.WEIGHTS.items() if name != "logic_profile" or profile is not None)
        return sum(cls.WEIGHTS[name] * value for name, value in active.items()) / active_weights

    def retrieve(
        self,
        query: str | dict[str, Any],
        top_k: int = 5,
        domain: str | None = None,
        keywords: Sequence[str] | None = None,
    ) -> list[CandidateConcept]:
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")
        query_text, profile = self._query_parts(query)
        scored: list[tuple[CandidateConcept, float, float]] = []
        for candidate in self.corpus.all():
            if domain is not None and candidate.domain != domain:
                continue
            score = self._score(candidate, query_text, keywords, domain, profile)
            lexical_score = self._score(candidate, query_text, keywords, domain, None)
            scored.append((candidate, score, lexical_score))
        # Require at least one explainable lexical/mechanism anchor, then allow
        # the logic profile to rank cross-domain candidates. Alias groups above
        # make that anchor robust to wording such as “自我纠错” vs “负反馈”.
        if not any(lexical_score >= self.MIN_RETRIEVAL_SCORE for _, _, lexical_score in scored):
            return []
        results = [
            candidate.model_copy(update={"retrieval_score": min(1.0, max(0.0, score))})
            for candidate, score, _ in scored if score > 0.0
        ]
        results.sort(key=lambda item: (-item.retrieval_score, item.concept, item.id))
        return results[:top_k]
