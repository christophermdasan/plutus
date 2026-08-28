import httpx
import pytest

from app.config import settings
from app.exceptions import LLMError, LLMNotConfiguredError
from app.qa.llm_client import LLMAnswer, LLMClient

CONTEXT = [(4, "Total revenue for fiscal year 2023 was $12.4 million, up from $10.1 million.")]

requires_llm_key = pytest.mark.skipif(
    not settings.llm_api_key, reason="No LLM API key configured"
)


def _client_with_transport(handler) -> LLMClient:
    transport = httpx.MockTransport(handler)
    return LLMClient(api_key="test-key", http_client=httpx.Client(transport=transport))


def test_missing_api_key_raises_a_clear_not_configured_error():
    client = LLMClient(api_key="")
    with pytest.raises(LLMNotConfiguredError):
        client.answer("question", CONTEXT)


def test_http_error_surfaces_the_response_body_not_just_the_status():
    # a bare status code ("400 Bad Request") doesn't say *why*; the reason
    # lives in the body and is what actually lets someone fix the problem
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "model `bogus` does not exist"}})

    with pytest.raises(LLMError) as exc:
        _client_with_transport(handler).answer("question", CONTEXT)

    assert "does not exist" in str(exc.value)


def test_malformed_json_from_the_model_raises_an_llm_error():
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "not json at all"}}]}
        )

    with pytest.raises(LLMError):
        _client_with_transport(handler).answer("question", CONTEXT)


def test_a_well_formed_response_is_parsed_into_an_llm_answer():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"found": true, "answer": "$12.4 million", '
                            '"page": 4, "quote": "Total revenue for fiscal year 2023"}'
                        }
                    }
                ]
            },
        )

    result = _client_with_transport(handler).answer("What was revenue?", CONTEXT)

    assert isinstance(result, LLMAnswer)
    assert result.found is True
    assert result.page == 4
    assert result.answer == "$12.4 million"


def test_request_payload_pins_temperature_and_json_response_format():
    captured = {}

    def handler(request):
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"found": false, "answer": "", "page": 0, "quote": ""}'}}]},
        )

    _client_with_transport(handler).answer("question", CONTEXT)

    assert captured["temperature"] == 0
    assert captured["response_format"] == {"type": "json_object"}
    assert "[Page 4]" in captured["messages"][1]["content"]


@requires_llm_key
def test_answers_a_grounded_question_against_the_real_provider():
    result = LLMClient().answer("What was total revenue in fiscal year 2023?", CONTEXT)
    assert result.found is True
    assert result.page == 4
    assert "12.4" in result.quote


@requires_llm_key
def test_declines_when_the_context_cannot_answer_the_question():
    result = LLMClient().answer("What was the dividend per share?", CONTEXT)
    assert result.found is False


@requires_llm_key
def test_health_check_reports_ok_for_a_working_configuration():
    ok, message, latency_ms = LLMClient().health_check()
    assert ok is True
    assert latency_ms is not None and latency_ms > 0


def test_health_check_reports_failure_for_an_unreachable_endpoint():
    client = LLMClient(api_key="x", base_url="http://localhost:1/v1", timeout=2)
    ok, message, _ = client.health_check()
    assert ok is False
    assert message
