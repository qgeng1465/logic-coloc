# -*- coding: utf-8 -*-
"""FastAPI routes for Logic-Coloc."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..sessions.manager import SessionNotFoundError
from .schemas import ChatRequest, ChatResponse, DiscoverRequest, DiscoverResponse, ExplainRequest, ExplainResponse, SessionResponse
from .service import LogicColocService, ServiceError


router = APIRouter(prefix="/api")
_service = LogicColocService()


def get_service() -> LogicColocService:
    return _service


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, ServiceError):
        raise exc
    if isinstance(exc, SessionNotFoundError):
        raise HTTPException(status_code=404, detail="session not found") from exc
    raise HTTPException(status_code=500, detail="internal service error") from exc


@router.post("/explain", response_model=ExplainResponse)
def explain(request: ExplainRequest, service: LogicColocService = Depends(get_service)) -> ExplainResponse:
    try:
        return service.explain(request)
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, service: LogicColocService = Depends(get_service)) -> ChatResponse:
    try:
        return service.chat(request)
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/discover", response_model=DiscoverResponse)
def discover(request: DiscoverRequest, service: LogicColocService = Depends(get_service)) -> DiscoverResponse:
    try:
        return service.discover(request)
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/session/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, service: LogicColocService = Depends(get_service)) -> SessionResponse:
    try:
        return service.get_session(session_id)
    except Exception as exc:
        _raise_http_error(exc)
