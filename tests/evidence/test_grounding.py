from __future__ import annotations

import pytest

from packages.llm import (
    GroundingArticle,
    GroundingRejection,
    GroundingRequest,
    GroundingService,
)

from ._factories import StubLlmClient, at, grounded_response

ARTICLE_TEXT = "한국원전이 체코 신규 원전 수주 기대에 강세를 보였다고 보도됐다. 업계는 협상 진전을 주목하고 있다."


def request(*, articles: tuple[GroundingArticle, ...] | None = None) -> GroundingRequest:
    default = (
        GroundingArticle(
            news_id="news_1",
            publisher="예시 언론사",
            title="[특징주] 한국원전, 신규 원전 수주 기대에 강세",
            text=ARTICLE_TEXT,
            original_url="https://example.com/news/123",
            published_at=at(10, 8),
        ),
    )
    return GroundingRequest(
        theme_id="thm_nuclear",
        display_name="원전",
        candidate_stock_ids=("stk_nuclear_leader", "stk_nuclear_related"),
        reaction_started_at=at(10, 0),
        articles=default if articles is None else articles,
    )


def test_no_article_means_no_llm_call_and_no_cause() -> None:
    client = StubLlmClient([grounded_response()])
    service = GroundingService(client)

    outcome = service.structure(request(articles=()), now=at(10, 10))

    assert outcome.called is False
    assert outcome.catalyst is None
    assert outcome.record is None
    assert outcome.rejection is GroundingRejection.NO_EVIDENCE
    assert client.calls == []


def test_accepted_output_records_model_prompt_and_input_articles() -> None:
    client = StubLlmClient([grounded_response()])
    service = GroundingService(client)
    grounding_request = request()

    outcome = service.structure(grounding_request, now=at(10, 10))

    assert outcome.catalyst is not None
    assert outcome.catalyst.catalyst_summary == "신규 원전 수주 기대 관련 보도"
    assert outcome.record is not None
    assert outcome.record.model_name == "stub-grounding-model"
    assert outcome.record.prompt_version == "catalyst-grounding-2026.08.1"
    assert outcome.record.news_ids == ("news_1",)
    assert outcome.record.request_fingerprint == grounding_request.fingerprint()
    assert outcome.record.accepted is True


@pytest.mark.parametrize(
    ("response", "rejection"),
    (
        ({"stockIds": [], "grounded": True}, GroundingRejection.SCHEMA_INVALID),
        (grounded_response(confidence="높음"), GroundingRejection.SCHEMA_INVALID),
        (grounded_response(grounded=False), GroundingRejection.NOT_GROUNDED),
        # 운영 실제 모양: 접지 실패 응답은 프롬프트 계약대로 빈 값으로 온다.
        # summary 검사를 먼저 하면 SCHEMA_INVALID로 오분류된다(2026-08-18~19).
        (
            grounded_response(
                grounded=False, summary="", entities=(), stock_ids=(), theme_ids=()
            ),
            GroundingRejection.NOT_GROUNDED,
        ),
        (grounded_response(stock_ids=("stk_unknown",)), GroundingRejection.UNKNOWN_STOCK),
        (grounded_response(theme_ids=("thm_other",)), GroundingRejection.UNKNOWN_THEME),
        (grounded_response(confidence=0.2), GroundingRejection.LOW_CONFIDENCE),
        (grounded_response(entities=("기술수출",)), GroundingRejection.UNSUPPORTED_ENTITY),
        (
            grounded_response(summary="한국원전이 체코 신규 원전 수주 기대에 강세를 보였다고 보도됐다."),
            GroundingRejection.VERBATIM_QUOTE,
        ),
    ),
    ids=(
        "schema",
        "confidence-type",
        "not-grounded",
        "not-grounded-empty-fields",
        "unknown-stock",
        "unknown-theme",
        "low-confidence",
        "unsupported-entity",
        "verbatim",
    ),
)
def test_invalid_output_is_discarded_but_still_recorded(response, rejection) -> None:
    service = GroundingService(StubLlmClient([response]))

    outcome = service.structure(request(), now=at(10, 10))

    assert outcome.catalyst is None
    assert outcome.rejection is rejection
    assert outcome.record is not None
    assert outcome.record.accepted is False
    assert outcome.record.rejection is rejection


def test_provider_failure_keeps_the_rule_result_without_a_summary() -> None:
    service = GroundingService(StubLlmClient(error=TimeoutError("모델 응답 없음")))

    outcome = service.structure(request(), now=at(10, 10))

    assert outcome.called is True
    assert outcome.catalyst is None
    assert outcome.rejection is GroundingRejection.PROVIDER_ERROR
    assert outcome.record is not None
    assert outcome.record.raw_output is None
