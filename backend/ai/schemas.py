"""Pydantic contracts for every structured LLM call.

Phase 1 only needs the smoke-test schema. Later phases add here:
  Phase 4: AnalysisPlan, GeneratedSQL
  Phase 6: Narrative (findings / decision / actions, each carrying evidence_ids)

Rules for schemas sent to Groq strict mode:
  * no open-ended dicts (see strict_schema.py)
  * optional values are `X | None` (they are still *required* keys)
  * value constraints are fine to declare; Pydantic enforces them after the call
"""
from typing import Literal

from pydantic import BaseModel


class LLMSmokeTest(BaseModel):
    status: Literal["ok"]
    echo: str
