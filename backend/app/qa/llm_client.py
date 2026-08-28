"""LLM access over any OpenAI-compatible chat-completions endpoint.

One client, one code path. This replaces the previous three-module
arrangement (a native-Ollama client, an OpenAI-compatible client, and a
dispatcher choosing between them) now that generation is always hosted.

The endpoint stays configurable rather than hardcoded to Groq: the same
wire format is spoken by Together, Fireworks, OpenRouter, vLLM and others,
so pointing somewhere else is a config change, not a code change.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator

import httpx
from pydantic import BaseModel, ValidationError as PydanticValidationError

from app.config import settings
from app.exceptions import LLMError, LLMNotConfiguredError, LLMRateLimitedError
from app.qa import prompts

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 4
_DEFAULT_BACKOFF_SECONDS = 5.0
_MAX_BACKOFF_SECONDS = 30.0
# Beyond this, the wait is a real quota exhaustion rather than a burst
# limit, and the honest thing is to tell the user instead of hanging.
_RETRYABLE_WAIT_SECONDS = 10.0


def _quota_hint(response: httpx.Response, delay: float) -> str:
    """A concrete next step, based on which limit the provider named."""
    body = response.text.lower()
    if "per day" in body or "rpd" in body or "tpd" in body:
        return (
            "The daily quota for this API key is used up. It resets on the "
            "provider's schedule, or you can upgrade the plan for a higher limit."
        )
    wait = f"about {int(delay)} seconds" if delay >= 1 else "a moment"
    return f"This is a short-term rate limit. Wait {wait} and ask again."


class LLMAnswer(BaseModel):
    """The structured shape every answer must arrive in.

    Constraining generation to this schema guarantees the *shape* of a
    citation. It says nothing about whether the content is true - that is
    the verifier's job, downstream.
    """

    found: bool
    answer: str = ""
    page: int = 0
    quote: str = ""


_HEALTH_CONTEXT = [(1, "Total revenue for fiscal year 2023 was $12.4 million.")]
_HEALTH_QUESTION = "What was total revenue?"


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        http_client: httpx.Client | None = None,
    ):
        self.api_key = settings.llm_api_key if api_key is None else api_key
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout_seconds
        self._http = http_client

    # -- internals ---------------------------------------------------------

    def _require_key(self) -> None:
        if not self.api_key:
            raise LLMNotConfiguredError(
                "No LLM API key is configured.",
                detail="Set LLM_API_KEY in the backend environment, then restart.",
            )

    def _payload(self, system: str, user: str, *, stream: bool = False) -> dict:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": settings.llm_max_tokens,
            "stream": stream,
        }

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        """How long the provider asked us to wait, if it said."""
        header = response.headers.get("retry-after")
        if header:
            try:
                return min(float(header), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        # Groq puts the wait in the error body ("try again in 2.97s") when
        # it doesn't send a Retry-After header.
        match = re.search(r"try again in ([\d.]+)s", response.text)
        if match:
            return min(float(match.group(1)) + 0.5, _MAX_BACKOFF_SECONDS)
        return _DEFAULT_BACKOFF_SECONDS

    def _post(self, payload: dict) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"

        for attempt in range(_MAX_ATTEMPTS):
            try:
                client = self._http
                if client is not None:
                    response = client.post(url, headers=headers, json=payload, timeout=self.timeout)
                else:
                    response = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
            except httpx.HTTPError as exc:
                raise LLMError(f"Could not reach the LLM provider: {exc}") from exc

            # Rate limits are an expected condition on free tiers, not a
            # failure - wait the interval the provider names and retry
            # rather than surfacing a 429 to the user.
            if response.status_code == 429:
                delay = self._retry_after(response)
                if attempt < _MAX_ATTEMPTS - 1 and delay <= _RETRYABLE_WAIT_SECONDS:
                    logger.info("Rate limited by provider; retrying in %.1fs", delay)
                    time.sleep(delay)
                    continue
                # Either we've retried enough or the provider is asking for
                # a long wait (a daily/hard quota rather than a burst).
                # Surface it as its own condition so the UI can explain it.
                raise LLMRateLimitedError(
                    "The AI provider's usage limit has been reached.",
                    detail=_quota_hint(response, delay),
                    retry_after=delay,
                )

            if response.is_error:
                # The status alone doesn't say why; the body carries the
                # actual reason (bad model name, invalid parameter).
                raise LLMError(
                    f"LLM provider returned HTTP {response.status_code}: {response.text[:500]}"
                )
            return response

        raise LLMError("LLM provider is rate limiting; try again shortly.")

    @staticmethod
    def _parse(content: str) -> LLMAnswer:
        try:
            return LLMAnswer.model_validate_json(content)
        except PydanticValidationError as exc:
            raise LLMError(f"LLM returned malformed JSON: {content[:300]}") from exc

    # -- public API --------------------------------------------------------

    def answer(self, question: str, passages: list[tuple[int, str]]) -> LLMAnswer:
        self._require_key()
        response = self._post(
            self._payload(
                prompts.ANSWER_SYSTEM_PROMPT, prompts.build_answer_prompt(question, passages)
            )
        )
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Unexpected LLM response shape: {response.text[:300]}") from exc
        return self._parse(content)

    def stream_answer(self, question: str, passages: list[tuple[int, str]]) -> Iterator[str]:
        """Yield raw content deltas as they arrive.

        The model emits a JSON document, so callers get JSON fragments, not
        display-ready prose - the caller accumulates and parses. Streaming
        exists so the UI can show progress on a multi-second call, not
        because partial JSON is independently useful.
        """
        self._require_key()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = self._payload(
            prompts.ANSWER_SYSTEM_PROMPT,
            prompts.build_answer_prompt(question, passages),
            stream=True,
        )
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            ) as response:
                if response.is_error:
                    response.read()
                    raise LLMError(
                        f"LLM provider returned HTTP {response.status_code}: {response.text[:500]}"
                    )
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ").strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0].get("delta", {})
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if content := delta.get("content"):
                        yield content
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach the LLM provider: {exc}") from exc

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Generic structured call for auxiliary features.

        Used by metadata extraction and suggested-question generation, which
        want JSON but not the citation schema.
        """
        self._require_key()
        response = self._post(self._payload(system_prompt, user_prompt))
        try:
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Unexpected LLM response: {response.text[:300]}") from exc

    def health_check(self) -> tuple[bool, str, int | None]:
        """Ask a real grounded question and check the answer is right.

        Deliberately not a ping: a reachable endpoint that answers badly (or
        is configured with a model that doesn't exist) is just as useless as
        an unreachable one, and only an end-to-end check catches that.
        """
        started = time.perf_counter()
        try:
            result = self.answer(_HEALTH_QUESTION, _HEALTH_CONTEXT)
        except LLMNotConfiguredError as exc:
            return False, exc.message, None
        except LLMError as exc:
            return False, str(exc), None

        latency_ms = int((time.perf_counter() - started) * 1000)
        if not result.found or "12.4" not in result.answer:
            return (
                False,
                "Connected, but the model did not answer the test question correctly - "
                "check the configured model name.",
                latency_ms,
            )
        return True, "Connection verified.", latency_ms
