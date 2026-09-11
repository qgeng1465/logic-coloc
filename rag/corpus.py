# -*- coding: utf-8 -*-
"""Local JSONL corpus loader for RAG V0."""
from __future__ import annotations

import json
from pathlib import Path

from .schemas import CandidateConcept


class Corpus:
    """Load and expose an ordered collection of strictly validated concepts."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / "data" / "corpus" / "example.jsonl"
        self._items: list[CandidateConcept] = []

    def load(self) -> "Corpus":
        """Read the JSONL file, validating every line with CandidateConcept."""
        items: list[CandidateConcept] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                payload = json.loads(line)
                items.append(CandidateConcept.model_validate(payload))
        self._items = items
        return self

    def all(self) -> list[CandidateConcept]:
        return list(self._items)

    def get(self, concept_id: str) -> CandidateConcept | None:
        return next((item for item in self._items if item.id == concept_id), None)

    def __len__(self) -> int:
        return len(self._items)
