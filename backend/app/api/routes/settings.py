"""Connection settings.

There is no provider or model picker here by design: which model answers is
an operator decision made in server config, not something an end user should
be reconfiguring mid-session. What the UI does need is the ability to check
that the configured connection actually works, which is what /test does.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import LLM
from app.api.schemas import LLMStatusOut
from app.config import settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/llm", response_model=LLMStatusOut)
def llm_status():
    return LLMStatusOut(
        configured=bool(settings.llm_api_key),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
    )


@router.post("/llm/test", response_model=LLMStatusOut)
def test_llm(llm: LLM):
    """Ask the model a real grounded question and check the answer.

    Deliberately not a ping: an endpoint that responds but is configured
    with a model that can't follow the citation schema is just as broken as
    an unreachable one, and only an end-to-end check catches that.
    """
    ok, message, latency_ms = llm.health_check()
    return LLMStatusOut(
        configured=bool(settings.llm_api_key),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        ok=ok,
        message=message,
        latency_ms=latency_ms,
    )
