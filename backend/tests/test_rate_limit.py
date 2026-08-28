"""Rate-limit handling.

A free-tier key hitting its limit is an expected operating condition, not a
crash. Short bursts should be waited out invisibly; a genuine quota
exhaustion should be reported to the user as its own thing, with a hint
they can act on.
"""

import httpx
import pytest

from app.exceptions import LLMRateLimitedError
from app.qa.llm_client import LLMClient

CONTEXT = [(1, "Total revenue for fiscal year 2023 was $12.4 million.")]

_OK_BODY = {
    "choices": [
        {
            "message": {
                "content": '{"found": true, "answer": "$12.4 million", "page": 1, '
                '"quote": "Total revenue for fiscal year 2023 was $12.4 million."}'
            }
        }
    ]
}


def _client(handler) -> LLMClient:
    return LLMClient(api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_a_short_burst_limit_is_retried_and_succeeds_transparently(monkeypatch):
    monkeypatch.setattr("app.qa.llm_client.time.sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                json={"error": {"message": "Rate limit reached. Please try again in 2.5s"}},
            )
        return httpx.Response(200, json=_OK_BODY)

    result = _client(handler).answer("What was revenue?", CONTEXT)

    assert result.found is True
    assert calls["n"] == 2


def test_a_persistent_limit_surfaces_as_a_rate_limit_error(monkeypatch):
    monkeypatch.setattr("app.qa.llm_client.time.sleep", lambda _: None)

    def handler(request):
        return httpx.Response(
            429, json={"error": {"message": "Rate limit reached. Please try again in 2s"}}
        )

    with pytest.raises(LLMRateLimitedError) as exc:
        _client(handler).answer("q", CONTEXT)

    assert "usage limit" in exc.value.message.lower()
    assert exc.value.detail


def test_a_daily_quota_is_not_retried_and_says_so(monkeypatch):
    # a long wait means a hard quota, not a burst - retrying would just
    # hang the request for no reason
    slept = []
    monkeypatch.setattr("app.qa.llm_client.time.sleep", lambda s: slept.append(s))

    def handler(request):
        return httpx.Response(
            429,
            json={
                "error": {
                    "message": "Rate limit reached for requests per day (RPD). "
                    "Please try again in 3600s"
                }
            },
        )

    with pytest.raises(LLMRateLimitedError) as exc:
        _client(handler).answer("q", CONTEXT)

    assert slept == []
    assert "daily quota" in exc.value.detail.lower()


def test_the_retry_after_header_is_respected_when_present(monkeypatch):
    monkeypatch.setattr("app.qa.llm_client.time.sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"error": {}})
        return httpx.Response(200, json=_OK_BODY)

    assert _client(handler).answer("q", CONTEXT).found is True
    assert calls["n"] == 2


def test_health_check_reports_a_rate_limit_rather_than_raising(monkeypatch):
    # the settings screen must show "you're out of quota", not blow up
    monkeypatch.setattr("app.qa.llm_client.time.sleep", lambda _: None)

    def handler(request):
        return httpx.Response(
            429, json={"error": {"message": "Rate limit reached. Please try again in 2s"}}
        )

    ok, message, _ = _client(handler).health_check()

    assert ok is False
    assert "usage limit" in message.lower()
