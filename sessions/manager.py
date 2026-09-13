# -*- coding: utf-8 -*-
"""In-memory Knowledge Session manager.

This layer manages Session schemas only. It does not call LLMs, RAG, mapping,
homology scoring, or any future LangGraph nodes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import uuid4

from ..agents.schemas import KnowledgeContext, Message, Session

RECENT_MESSAGE_LIMIT = 10


class SessionNotFoundError(KeyError):
    """Raised when a requested Knowledge Session does not exist."""


class DuplicateSessionError(ValueError):
    """Raised when attempting to create a Session with an existing id."""


class SessionManager:
    """Small in-memory manager for Knowledge Session objects."""

    def __init__(self, recent_message_limit: int = RECENT_MESSAGE_LIMIT):
        if recent_message_limit < 1:
            raise ValueError("recent_message_limit must be >= 1")
        self.recent_message_limit = recent_message_limit
        self._sessions: dict[str, Session] = {}

    def create_session(
        self,
        *,
        knowledge: KnowledgeContext,
        source: dict[str, Any] | None = None,
        session_id: str | None = None,
        conversation_summary: str = "",
        recent_messages: Sequence[Message] | None = None,
        owner_id: str = "",
    ) -> Session:
        """Create and store a new Session."""
        resolved_id = session_id or self._new_session_id()
        if resolved_id in self._sessions:
            raise DuplicateSessionError(f"session already exists: {resolved_id}")

        now = self._now()
        session = Session(
            session_id=resolved_id,
            source=dict(source or {}),
            knowledge=knowledge,
            conversation_summary=conversation_summary,
            recent_messages=list(recent_messages or []),
            created_at=now,
            updated_at=now,
            owner_id=owner_id,
        )
        self._sessions[resolved_id] = self._copy_session(session)
        return self._copy_session(session)

    def get_session(self, session_id: str) -> Session:
        """Return a copy of an existing Session."""
        return self._copy_session(self._require_session(session_id))

    def update_session(
        self,
        session_id: str,
        *,
        source: dict[str, Any] | None = None,
        knowledge: KnowledgeContext | None = None,
        conversation_summary: str | None = None,
        recent_messages: Sequence[Message] | None = None,
    ) -> Session:
        """Patch an existing Session and return the updated copy."""
        session = self._require_session(session_id)
        updates: dict[str, Any] = {"updated_at": self._now()}
        if source is not None:
            updates["source"] = dict(source)
        if knowledge is not None:
            updates["knowledge"] = knowledge
        if conversation_summary is not None:
            updates["conversation_summary"] = conversation_summary
        if recent_messages is not None:
            updates["recent_messages"] = list(recent_messages)
        return self._replace_session(session, updates)

    def add_message(self, session_id: str, message: Message) -> Session:
        """Append a message without overwriting previous messages."""
        session = self._require_session(session_id)
        messages = [*session.recent_messages, message]
        return self._replace_session(session, {"recent_messages": messages, "updated_at": self._now()})

    def update_knowledge_context(self, session_id: str, knowledge_context: KnowledgeContext) -> Session:
        """Replace the stable KnowledgeContext for a Session."""
        return self.update_session(session_id, knowledge=knowledge_context)

    def update_summary(self, session_id: str, summary: str) -> Session:
        """Replace the conversation summary for a Session."""
        return self.update_session(session_id, conversation_summary=summary)

    def get_context_for_agent(self, session_id: str) -> dict[str, Any]:
        """Return bounded context for future Agent runs.

        The full Session remains in memory, but Agent context receives only the
        stable knowledge, current summary, and the last N messages.
        """
        session = self._require_session(session_id)
        return {
            "session_id": session.session_id,
            "stable_knowledge_context": session.knowledge,
            "conversation_summary": session.conversation_summary,
            "recent_messages": list(session.recent_messages[-self.recent_message_limit :]),
        }

    def summarize_session(self, session_id: str) -> str:
        """Create a deterministic lightweight summary from recent messages.

        This intentionally does not call an LLM. Future Agent stages can replace
        this with an LLM-backed summarizer behind the same manager interface.
        """
        session = self._require_session(session_id)
        messages = session.recent_messages
        if not messages:
            summary = session.conversation_summary
        else:
            snippets = [self._format_message_for_summary(msg) for msg in messages[-self.recent_message_limit :]]
            summary = "\n".join(snippets)
        self.update_summary(session_id, summary)
        return summary

    def _new_session_id(self) -> str:
        while True:
            session_id = uuid4().hex
            if session_id not in self._sessions:
                return session_id

    def _require_session(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFoundError(f"session not found: {session_id}") from exc

    def _replace_session(self, session: Session, updates: dict[str, Any]) -> Session:
        updated = session.model_copy(update=updates)
        self._sessions[updated.session_id] = self._copy_session(updated)
        return self._copy_session(updated)

    @staticmethod
    def _copy_session(session: Session) -> Session:
        return Session.model_validate(session.model_dump())

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _format_message_for_summary(message: Message) -> str:
        content = " ".join(message.content.split())
        if len(content) > 80:
            content = f"{content[:77]}..."
        return f"{message.role.value}: {content}"

