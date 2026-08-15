"""OpenAI 실공급자 client. 전달된 기사만 구조화하고 웹 검색·외부 지식을 쓰지 않는다.

환경변수 설정 (`.env.example` 참조):
- ``OPENAI_API_KEY``: 없으면 client가 조립되지 않는다 (LLM 요약 비활성).
- ``OPENAI_MODEL``: 생략하면 :data:`DEFAULT_OPENAI_MODEL`.
- ``OPENAI_REASONING_EFFORT``: 생략 가능. minimal/low/medium/high만 허용.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx

from .models import GROUNDING_PROMPT_VERSION

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
REASONING_EFFORTS = ("minimal", "low", "medium", "high")

_SYSTEM_PROMPTS: Mapping[str, str] = {
    GROUNDING_PROMPT_VERSION: (
        "너는 국내 주식 테마의 상승 소재를 구조화하는 도구다.\n"
        "\n"
        "규칙:\n"
        "1. 입력 JSON의 articles만 근거로 사용한다. 외부 지식·웹 검색·추측을 쓰지 않는다.\n"
        "2. 기사들이 이 테마의 상승 이유를 설명하지 못하면 grounded를 false로 한다.\n"
        "3. catalystSummary는 기사 문장을 그대로 옮기지 말고 한 문장으로 새로 요약한다.\n"
        "4. eventEntities에는 기사 제목·본문에 글자 그대로 등장하는 표현만 담는다.\n"
        "5. stockIds는 candidateStockIds의 부분집합, candidateThemeIds는 [themeId]만 허용된다.\n"
        "6. confidence는 0과 1 사이 숫자다.\n"
        "\n"
        "다음 키를 가진 JSON 객체 하나만 출력한다:\n"
        '{"stockIds": [], "candidateThemeIds": [], "catalystSummary": "", '
        '"eventEntities": [], "confidence": 0.0, "grounded": false}'
    ),
}


class OpenAiLlmClient:
    """Chat Completions JSON 모드 호출. 실패는 예외로 올려 PROVIDER_ERROR로 기록되게 한다."""

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_OPENAI_MODEL,
        *,
        reasoning_effort: str | None = None,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI API key가 비어 있습니다")
        if not model_name.strip():
            raise ValueError("OpenAI 모델명이 비어 있습니다")
        if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(
                f"OPENAI_REASONING_EFFORT는 {'/'.join(REASONING_EFFORTS)}만 허용됩니다: {reasoning_effort!r}"
            )
        self.model_name = model_name
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._reasoning_effort = reasoning_effort
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def structure(
        self,
        *,
        prompt_version: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        system_prompt = _SYSTEM_PROMPTS.get(prompt_version)
        if system_prompt is None:
            raise ValueError(f"등록되지 않은 prompt version입니다: {prompt_version!r}")
        body: dict[str, object] = {
            "model": self.model_name,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
        }
        if self._reasoning_effort is not None:
            body["reasoning_effort"] = self._reasoning_effort
        response = self._client.post(
            OPENAI_CHAT_COMPLETIONS_URL, json=body, headers=self._headers
        )
        response.raise_for_status()
        parsed = json.loads(_content(response.json()))
        if not isinstance(parsed, dict):
            raise ValueError("OpenAI 출력이 JSON 객체가 아닙니다")
        return parsed


def _content(data: object) -> str:
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
    raise ValueError("OpenAI 응답에서 content를 찾지 못했습니다")


def create_live_llm_client(
    environ: Mapping[str, str],
    *,
    http_client: httpx.Client | None = None,
) -> OpenAiLlmClient | None:
    """OPENAI_API_KEY가 설정된 경우에만 client를 조립한다. 없으면 None."""

    api_key = environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    return OpenAiLlmClient(
        api_key,
        environ.get("OPENAI_MODEL", "").strip() or DEFAULT_OPENAI_MODEL,
        reasoning_effort=environ.get("OPENAI_REASONING_EFFORT", "").strip() or None,
        http_client=http_client,
    )
