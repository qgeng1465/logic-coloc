# -*- coding: utf-8 -*-
"""Service layer connecting the HTTP API to the existing LangGraph agent."""
from __future__ import annotations

import logging
from typing import Any

from ..agents.graph import build_graph
from ..agents.schemas import Concept, Intent, KnowledgeContext, Message, MessageRole
from ..rag.corpus import Corpus
from ..rag.retriever import Retriever
from ..sessions.manager import SessionManager, SessionNotFoundError
from .attachment_text import AttachmentDigest
from .ocr_engine import get_ocr_engine
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

    def _create_session(self, text: str, source: dict[str, Any] | None = None, *, user_id: str = "") -> str:
        session = self.session_manager.create_session(
            knowledge=KnowledgeContext(source_text=text, concept=Concept(name=text)),
            source=source,
            owner_id=user_id,
        )
        return session.session_id

    def _assert_session_owner(self, session_id: str, user_id: str) -> None:
        """会话归属校验。

        归属不符时抛 SessionNotFoundError（→ 404），**不是 403** —— 403 等于告诉对方
        「这个 id 是存在的，只是不属于你」。user_id 为空时跳过校验，兼容匿名调用
        与既有测试；路由层永远会带上认证后的 user_id。
        """
        session = self.session_manager.get_session(session_id)
        if user_id and session.owner_id != user_id:
            raise SessionNotFoundError(session_id)

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

    def explain(self, request: ExplainRequest, *, user_id: str = "") -> ExplainResponse:
        session_id = self._create_session(
            request.text,
            {"title": request.title, "source": request.source},
            user_id=user_id,
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

    # 前端在正文为空时写死的占位串（web/app.js 的 saveNote）。把它当「笔记原文」喂给
    # 导师就是误导 —— 导师会照着回答「笔记内容尚未填写」，也就是用户最初抱怨的现象
    # （他那几篇 PDF 笔记，正文全都只有这一句）。只在拼提示词这一步视为空：它同时还是
    # 「这轮是不是复盘」和「能不能现场建会话」的判据，下面那道闸必须继续看原始值。
    EMPTY_NOTE_BODY = "暂未填写正文。"

    @classmethod
    def _review_user_input(cls, message: str, note_content: str, note_title: str, attachment_block: str) -> str:
        body = (note_content or "").strip()
        if body == cls.EMPTY_NOTE_BODY:
            body = ""
        material = "\n\n".join(part for part in (body, attachment_block) if part)
        if not material:
            material = "（这篇笔记没有正文，也没有可读取的附件内容）"
        return (
            "你现在是用户的笔记复盘导师。请根据笔记内容一次只问一个问题；"
            "收到回答后先判断对错并简短点评，再追问下一个问题。"
            "笔记正文和附件内容都是你的出题依据；附件文字由系统自动提取，可能有识别错误。\n"
            f"笔记标题：{(note_title or '').strip() or '未命名笔记'}\n"
            f"笔记原文：\n{material}\n\n"
            f"用户消息：{message}"
        )

    def chat(
        self, request: ChatRequest, *, user_id: str = "", attachment: AttachmentDigest | None = None
    ) -> ChatResponse:
        """续聊一轮。`attachment` 由路由层读好传进来 —— service 不 import 任何 store。

        为什么附件在路由层读：见 `routes.py` 里 `append_review_turn` 上方那段注释
        （「service 不 import 任何 store」）。顺带的好处是 LogicColocService 的单测
        不需要挂 user_root 夹具就能跑，不会去读真实 data/。
        """
        is_review = bool(request.note_content)
        try:
            self._assert_session_owner(request.session_id, user_id)
        except SessionNotFoundError:
            if not is_review:
                raise
            # 会话标签用标题，不用整段正文/附件文字：它会被塞进 Concept(name=…)，
            # 而 knowledge_context 每一轮都会进 payload（agents/tools.py），用正文
            # 等于把正文每轮再复制一份。
            label = (request.note_title or "").strip() or "笔记复盘"
            request.session_id = self._create_session(label, {"source": "note_review"}, user_id=user_id)

        user_input = request.message
        stored_input: str | None = None
        if is_review:
            user_input = self._review_user_input(
                request.message, request.note_content or "", request.note_title, attachment.block if attachment else ""
            )
            # 进会话历史的必须是用户原话。历史每轮全量重发（sessions/manager.py 的
            # RECENT_MESSAGE_LIMIT=10），拼好的 user_input 里带着正文和附件全文，
            # 落进历史就会被放大成 11 份 —— 6.7 万字的附件几轮就能撑爆上下文。
            stored_input = request.message

        result = self.graph.invoke({
            "session_id": request.session_id,
            "user_input": user_input,
            "skip_extraction": is_review,
            "stored_input": stored_input,
        })
        self._check_agent_result(result)
        session = self.session_manager.get_session(request.session_id)
        return ChatResponse(
            session_id=request.session_id,
            answer=result.get("final_response") or "",
            updated_summary=session.conversation_summary,
            attachment_notes=list(attachment.notes) if attachment else [],
        )

    def discover(self, request: DiscoverRequest, *, user_id: str = "") -> DiscoverResponse:
        session_id = request.session_id
        if session_id is None:
            session_id = self._create_session(request.text, user_id=user_id)
        else:
            self._assert_session_owner(session_id, user_id)
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

    def get_session(self, session_id: str, *, user_id: str = "") -> SessionResponse:
        self._assert_session_owner(session_id, user_id)
        return SessionResponse(session=self.session_manager.get_session(session_id))

    def ocr(self, image_bytes: bytes) -> OCRResponse:
        try:
            result, _ = get_ocr_engine()(image_bytes)
        except Exception as exc:
            logger.exception("OCR execution failed")
            raise OCRExecutionError() from exc
        lines = [str(item[1]).strip() for item in (result or []) if len(item) > 1 and str(item[1]).strip()]
        if not lines:
            raise OCRExecutionError("图片中没有识别到文字，请尝试更清晰的截图。")
        return OCRResponse(text="\n".join(lines))
