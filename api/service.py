# -*- coding: utf-8 -*-
"""Service layer connecting the HTTP API to the existing LangGraph agent."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from ..agents.graph import build_graph
from ..agents.schemas import Concept, Intent, KnowledgeContext, Message, MessageRole
from ..rag.corpus import Corpus
from ..rag.retriever import Retriever
from ..sessions.manager import SessionManager
from .schemas import (
    ChatRequest,
    ChatResponse,
    DiscoverRequest,
    DiscoverCandidateResult,
    DiscoverResponse,
    ExplainRequest,
    ExplainResponse,
    SessionResponse,
    OCRResponse,
)


logger = logging.getLogger(__name__)


class ServiceError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


class LLMBackendUnavailableError(ServiceError):
    def __init__(self) -> None:
        super().__init__(status_code=503, code="LLM_BACKEND_UNAVAILABLE", message="模型服务暂时不可用，请稍后重试。")


class AgentExecutionError(ServiceError):
    def __init__(self) -> None:
        super().__init__(status_code=500, code="AGENT_EXECUTION_FAILED", message="处理请求时发生错误。")


class OCRExecutionError(ServiceError):
    def __init__(self, message: str = "图片识别失败，请尝试更清晰的截图。") -> None:
        super().__init__(status_code=422, code="OCR_FAILED", message=message)


@lru_cache(maxsize=1)
def _get_ocr_engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


class LogicColocService:
    """Own and reuse the application's Session, RAG, and Graph runtime."""

    def __init__(
        self,
        *,
        session_manager: SessionManager | None = None,
        corpus: Corpus | None = None,
        retriever: Retriever | None = None,
        graph: Any | None = None,
    ) -> None:
        self.session_manager = session_manager or SessionManager()
        self.corpus = corpus or Corpus().load()
        self.retriever = retriever or Retriever(self.corpus)
        self.graph = graph or build_graph(manager=self.session_manager, retriever=self.retriever)

    def _create_session(self, text: str, source: dict[str, Any] | None = None) -> str:
        session = self.session_manager.create_session(
            knowledge=KnowledgeContext(source_text=text, concept=Concept(name=text)),
            source=source,
        )
        return session.session_id

    @staticmethod
    def _check_agent_result(result: dict[str, Any]) -> None:
        errors = [str(error) for error in result.get("errors") or []]
        llm_stage_errors = [
            error for error in errors
            if error.startswith("extract_features:") or error.startswith("generate_explanation:")
        ]
        if not llm_stage_errors:
            return
        internal = " ".join(llm_stage_errors)
        logger.error("Agent LLM stage failed: %s", internal)
        backend_markers = ("LLM 调用失败", "ConnectionError", "HTTPConnectionPool", "MaxRetryError", "拒绝连接")
        if any(marker in internal for marker in backend_markers):
            raise LLMBackendUnavailableError()
        raise AgentExecutionError()

    def explain(self, request: ExplainRequest) -> ExplainResponse:
        session_id = self._create_session(
            request.text,
            {"title": request.title, "source": request.source},
        )
        result = self.graph.invoke({"session_id": session_id, "user_input": request.text})
        self._check_agent_result(result)
        knowledge = result.get("knowledge_context") or self.session_manager.get_session(session_id).knowledge
        return ExplainResponse(
            session_id=session_id,
            concept=knowledge.concept,
            logic_profile=result.get("logic_profile") or knowledge.logic_profile,
            explanation=result.get("final_response") or "",
        )

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.session_manager.get_session(request.session_id)
        result = self.graph.invoke({"session_id": request.session_id, "user_input": request.message})
        self._check_agent_result(result)
        session = self.session_manager.get_session(request.session_id)
        return ChatResponse(
            session_id=request.session_id,
            answer=result.get("final_response") or "",
            updated_summary=session.conversation_summary,
        )

    def discover(self, request: DiscoverRequest) -> DiscoverResponse:
        session_id = request.session_id
        if session_id is None:
            session_id = self._create_session(request.text)
        else:
            self.session_manager.get_session(session_id)
        result = self.graph.invoke(
            {
                "session_id": session_id,
                "user_input": request.text,
                "intent": Intent.DISCOVER_HOMOLOGY,
                "top_k": min(request.top_k, 5),
            }
        )
        self._check_agent_result(result)
        knowledge = result.get("knowledge_context") or self.session_manager.get_session(session_id).knowledge
        limit = min(request.top_k, 5)
        candidates = list(result.get("candidate_concepts") or [])[:limit]
        retrieval_scores = result.get("retrieval_scores") or {}
        homonomy_by_id = {item.candidate_id: item for item in result.get("homonomy_results") or []}
        mapping_by_id = {item.candidate_id: item for item in result.get("mapping_results") or []}
        critique_by_id = {item.candidate_id: item for item in result.get("critique_results") or []}
        report_by_id = {item.candidate_id: item for item in result.get("learning_reports") or []}
        if report_by_id:
            context_parts = []
            for report in report_by_id.values():
                lessons = "；".join(
                    f"{lesson.title}：{lesson.explanation}"
                    for lesson in report.target_domain_lessons
                )
                context_parts.append(
                    f"候选领域：{report.candidate_concept.name}。"
                    f"交集课程：{report.intersection_lesson}。"
                    f"第二领域课程：{lessons}。"
                    f"边界：{'；'.join(report.failure_boundaries)}"
                )
            self.session_manager.add_message(
                session_id,
                Message(
                    role=MessageRole.SYSTEM,
                    content="以下是本次发现同源的学习报告上下文，后续回答应优先依据它：\n" + "\n".join(context_parts),
                    metadata={"kind": "discover_learning_reports"},
                ),
            )
        candidate_results = []
        public_results = []
        for candidate in candidates:
            retrieval_score = retrieval_scores.get(candidate.id, candidate.retrieval_score)
            retrieval_reason = self._retrieval_reason(request.text, candidate, retrieval_score)
            candidate_copy = candidate.model_copy(update={"retrieval_score": retrieval_score})
            homonomy_result = homonomy_by_id.get(candidate.id)
            homonomy_reason = self._homonomy_reason(homonomy_result.score if homonomy_result else None, candidate)
            candidate_results.append(
                DiscoverCandidateResult(
                    candidate=candidate_copy,
                    homonomy_score=homonomy_result.score if homonomy_result else None,
                    retrieval_score_reason=retrieval_reason,
                    homonomy_score_reason=homonomy_reason,
                    mapping=mapping_by_id.get(candidate.id),
                    critique=critique_by_id.get(candidate.id),
                    learning_report=report_by_id.get(candidate.id),
                )
            )
            report = report_by_id.get(candidate.id)
            critique = critique_by_id.get(candidate.id)
            homology_result = homonomy_by_id.get(candidate.id)
            if homology_result:
                metrics = {
                    item.dimension: round(item.similarity, 4)
                    for item in (report.dimension_comparisons if report else [])
                }
                public_results.append(homology_result.model_copy(update={
                    "concept": candidate.concept,
                    "domain": candidate.domain,
                    "retrieval_score": retrieval_score,
                    "reliability": ({
                        "RELIABLE_WITH_LIMITS": "有限成立",
                        "NEEDS_REVIEW": "待检验",
                        "REJECTED": "不成立",
                        "INSUFFICIENT_EVIDENCE": "证据不足",
                    }.get(report.verdict, "待检验") if report else "待检验"),
                    "summary": report.mechanism_summary if report else candidate.description,
                    "mechanism": report.mechanism_summary if report else candidate.mechanism,
                    "critique": (critique.summary if critique else (report.verdict_reason if report else "")),
                    "metrics": metrics,
                    "references": [source.id for source in (report.source_references if report else candidate.sources)],
                    "retrieval_score_reason": retrieval_reason,
                    "homonomy_score_reason": homonomy_reason,
                    "retrieval_source": "vector_db",
                    "homonomy_source": "llm_rubric",
                }))
        public_errors = ["部分候选处理失败。"] if result.get("errors") else []
        return DiscoverResponse(
            session_id=session_id,
            concept=knowledge.concept,
            candidates=candidate_results,
            results=public_results,
            mappings=[mapping_by_id[item.candidate.id] for item in candidate_results if item.candidate.id in mapping_by_id],
            critiques=[critique_by_id[item.candidate.id] for item in candidate_results if item.candidate.id in critique_by_id],
            learning_reports=[report_by_id[item.candidate.id] for item in candidate_results if item.candidate.id in report_by_id],
            report=result.get("final_response") or "",
            errors=public_errors,
        )

    @staticmethod
    def _retrieval_reason(query: str, candidate: Any, score: float | None) -> str:
        q = query.lower()
        hits = [term for term in [candidate.concept, candidate.domain, *candidate.keywords] if term and term.lower() in q]
        if hits:
            return f"确定性检索分数来自 Retriever 的词项/领域匹配；命中：{'、'.join(hits[:3])}。"
        return f"确定性检索分数来自 Retriever 的逻辑画像相似度（当前值 {float(score or 0):.1%}），不是模型生成。"

    @staticmethod
    def _homonomy_reason(score: float | None, candidate: Any) -> str:
        value = float(score or 0)
        if value >= 0.90:
            band = "90–100%：底层机制几乎完全对应"
        elif value >= 0.70:
            band = "70–89%：共享宏观结构，具体实现有差异"
        elif value >= 0.50:
            band = "50–69%：抽象概念相似，但机制有本质区别"
        else:
            band = "0–49%：相似度低，可能是强行类比"
        return f"确定性五维逻辑画像余弦相似度为 {value:.1%}；按评分量表属于{band}。候选机制：{candidate.mechanism or candidate.description}"

    def get_session(self, session_id: str) -> SessionResponse:
        return SessionResponse(session=self.session_manager.get_session(session_id))

    def ocr(self, image_bytes: bytes) -> OCRResponse:
        try:
            result, _ = _get_ocr_engine()(image_bytes)
        except Exception as exc:
            logger.exception("OCR execution failed")
            raise OCRExecutionError() from exc
        lines = [str(item[1]).strip() for item in (result or []) if len(item) > 1 and str(item[1]).strip()]
        if not lines:
            raise OCRExecutionError("图片中没有识别到文字，请尝试更清晰的截图。")
        return OCRResponse(text="\n".join(lines))
