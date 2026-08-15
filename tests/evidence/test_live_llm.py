from __future__ import annotations

import json

import httpx
import pytest

from packages.llm import (
    DEFAULT_OPENAI_MODEL,
    GROUNDING_PROMPT_VERSION,
    OPENAI_CHAT_COMPLETIONS_URL,
    GroundingArticle,
    GroundingRejection,
    GroundingRequest,
    GroundingService,
    OpenAiLlmClient,
    create_live_llm_client,
)

from ._factories import at, grounded_response


def grounding_request() -> GroundingRequest:
    return GroundingRequest(
        theme_id="thm_nuclear",
        display_name="원전",
        candidate_stock_ids=("stk_nuclear_leader", "stk_nuclear_related"),
        reaction_started_at=at(10, 0),
        articles=(
            GroundingArticle(
                news_id="news_1",
                publisher="예시 언론사",
                title="[특징주] 한국원전, 신규 원전 수주 기대에 강세",
                text="한국원전이 체코 신규 원전 수주 기대에 강세를 보였다고 보도됐다.",
                original_url="https://example.com/news/123",
                published_at=at(10, 8),
            ),
        ),
    )


def completion_body(content: object) -> dict[str, object]:
    return {
        "choices": [
            {"message": {"content": json.dumps(content, ensure_ascii=False)}}
        ]
    }


def client_returning(
    body: object,
    *,
    status_code: int = 200,
    captured: list[httpx.Request] | None = None,
    reasoning_effort: str | None = None,
) -> OpenAiLlmClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(status_code, json=body)

    return OpenAiLlmClient(
        "sk-test",
        reasoning_effort=reasoning_effort,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_structure_sends_versioned_prompt_and_payload_in_json_mode() -> None:
    captured: list[httpx.Request] = []
    client = client_returning(
        completion_body(grounded_response()),
        captured=captured,
        reasoning_effort="medium",
    )
    payload = grounding_request().to_payload()

    result = client.structure(prompt_version=GROUNDING_PROMPT_VERSION, payload=payload)

    assert result == grounded_response()
    request = captured[0]
    assert str(request.url) == OPENAI_CHAT_COMPLETIONS_URL
    assert request.headers["Authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["model"] == DEFAULT_OPENAI_MODEL
    assert body["reasoning_effort"] == "medium"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert "articles만 근거로 사용한다" in body["messages"][0]["content"]
    assert json.loads(body["messages"][1]["content"]) == payload


def test_reasoning_effort_is_omitted_when_not_configured() -> None:
    captured: list[httpx.Request] = []
    client = client_returning(completion_body(grounded_response()), captured=captured)

    client.structure(
        prompt_version=GROUNDING_PROMPT_VERSION,
        payload=grounding_request().to_payload(),
    )

    assert "reasoning_effort" not in json.loads(captured[0].content)


def test_unknown_prompt_version_never_reaches_the_network() -> None:
    captured: list[httpx.Request] = []
    client = client_returning(completion_body(grounded_response()), captured=captured)

    with pytest.raises(ValueError, match="prompt version"):
        client.structure(prompt_version="unknown-2099.01.1", payload={})
    assert captured == []


def test_non_object_output_is_rejected() -> None:
    client = client_returning(completion_body(["목록이면 안 된다"]))

    with pytest.raises(ValueError, match="JSON 객체"):
        client.structure(
            prompt_version=GROUNDING_PROMPT_VERSION,
            payload=grounding_request().to_payload(),
        )


def test_grounding_service_accepts_the_live_client_output() -> None:
    service = GroundingService(client_returning(completion_body(grounded_response())))

    outcome = service.structure(grounding_request(), now=at(10, 10))

    assert outcome.catalyst is not None
    assert outcome.catalyst.catalyst_summary == "신규 원전 수주 기대 관련 보도"
    assert outcome.record is not None
    assert outcome.record.model_name == DEFAULT_OPENAI_MODEL
    assert outcome.record.prompt_version == GROUNDING_PROMPT_VERSION


@pytest.mark.parametrize(
    "body",
    (
        {"error": {"message": "rate limited"}},
        {"choices": []},
        completion_body(None),
    ),
    ids=("http-500", "empty-choices", "null-content"),
)
def test_provider_failures_become_provider_error_without_a_summary(body) -> None:
    status_code = 500 if "error" in body else 200
    service = GroundingService(client_returning(body, status_code=status_code))

    outcome = service.structure(grounding_request(), now=at(10, 10))

    assert outcome.called is True
    assert outcome.catalyst is None
    assert outcome.rejection is GroundingRejection.PROVIDER_ERROR
    assert outcome.record is not None
    assert outcome.record.accepted is False


def test_factory_requires_an_api_key() -> None:
    assert create_live_llm_client({}) is None
    assert create_live_llm_client({"OPENAI_API_KEY": "  "}) is None


def test_factory_reads_model_and_effort_from_env() -> None:
    client = create_live_llm_client(
        {
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_MODEL": "gpt-5.6-luna",
            "OPENAI_REASONING_EFFORT": "high",
        }
    )

    assert client is not None
    assert client.model_name == "gpt-5.6-luna"

    fallback = create_live_llm_client({"OPENAI_API_KEY": "sk-test"})
    assert fallback is not None
    assert fallback.model_name == DEFAULT_OPENAI_MODEL


def test_factory_rejects_an_unknown_reasoning_effort() -> None:
    with pytest.raises(ValueError, match="OPENAI_REASONING_EFFORT"):
        create_live_llm_client(
            {"OPENAI_API_KEY": "sk-test", "OPENAI_REASONING_EFFORT": "ultra"}
        )
