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

# 리서치 복합 질문 분해(E-21 열린 질문 1단계). 숫자·사실을 만들지 않고
# 다음에 던질 단일 질의 문장 하나만 쓴다.
RESEARCH_COMPOSE_PROMPT_VERSION = "research-compose/1.0.0"

_SYSTEM_PROMPTS: Mapping[str, str] = {
    RESEARCH_COMPOSE_PROMPT_VERSION: (
        "너는 주식 리서치 질문 분해기다. 사용자의 복합 질문을, 아래 틀의 단일\n"
        "질의 문장으로 하나씩 바꿔 던진다. 계산은 별도 엔진이 한다.\n"
        "\n"
        "쓸 수 있는 질문 틀(값은 바꿔도 된다):\n"
        "- 어제 뭐가 올랐어? / 이번 주 시장 어땠어?\n"
        "- 삼성전자 2026-06-29에 왜 올랐어? / 삼성전자 올해 크게 오른 날 알려줘\n"
        "- 삼성전자 어떤 테마에 속해? / 삼성전자랑 같이 움직이는 종목은?\n"
        "- 2차전지 테마에 어떤 종목이 있어? / 2차전지 테마 과거에 뭘로 움직였어?\n"
        "- 2차전지랑 반도체 대표주(생산) 중에 올해 어디가 더 셌어? / 올해 자주 나온 테마는?\n"
        "- 정책 소재에 어떤 테마가 반응했어? / 올해 정책 소재 몇 번 나왔어?\n"
        "- 로봇 정책 소재에 어떤 테마가 반응했어? (소재 앞 낱말로 좁힐 수 있다)\n"
        "- 로봇 정책 소재 발표 뒤 당시 주도주 주가 어떻게 됐어?\n"
        "- 정책 소재는 기대감이었어 확정이었어? / 정책 소재 처음 나온 소재야?\n"
        "- 한화에어로스페이스가 직접 한 일만 알려줘 / 한화에어로스페이스 1조 넘는 수주 몇 건이야?\n"
        "- 한화에어로스페이스 수주 발표 뒤에 주가 어떻게 됐어?\n"
        "\n"
        "규칙:\n"
        "1. steps의 result는 엔진이 실제 계산한 결과 요약이다. 다음 질문에는 원 질문과\n"
        "   result에 글자 그대로 등장하는 회사·테마·날짜만 쓴다. 지어내지 않는다.\n"
        "2. '당시 주도주' 같은 참조는 앞 단계 result에 나온 실제 종목명으로 바꿔 쓴다.\n"
        "3. 원 질문이 이미 충분히 답해졌으면 next를 null로 한다.\n"
        "4. 한 번에 질문 문장 하나만 낸다. 문장은 위 틀을 따른다.\n"
        "5. 앞 단계가 '해석 실패'면 같은 뜻을 위 틀에 더 가깝게 한 번만 고쳐 쓴다.\n"
        "\n"
        '다음 키를 가진 JSON 객체 하나만 출력한다: {"next": "질문 문장 또는 null"}'
    ),
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
