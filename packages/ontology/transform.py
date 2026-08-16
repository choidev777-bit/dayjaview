"""원인문 → 소재 라벨 versioned transform (E-17).

입력은 인포스탁 history 원인문 한 줄이며 외부 상태 없이 결정론적으로
분류한다. 유형은 span 선점 매칭(시작 위치 → 긴 키워드 우선)으로 뽑고,
primary는 원문에서 가장 먼저 등장한 유형이다(인포스탁 원인문은 주 사유를
앞에 쓴다 — 표본 1,000건 정독으로 확인). 방향은 꼬리 동사를 우선하고,
확실성은 가장 오른쪽 표지의 계열을 취한다(한국어 후치 수식).

LLM을 쓰지 않는다. 점수·순위·반응 크기도 만들지 않는다.
로직이나 표지 처리 규칙을 고치면 TRANSFORM_VERSION을 올린다.
"""

from __future__ import annotations

import re

from .models import (
    CatalystClassification,
    Certainty,
    Direction,
    EvidenceSpan,
    ParsedCauseSentence,
)
from .vocabulary import (
    ANTICIPATION_MARKERS,
    CONFIRMED_MARKERS,
    CONTINUATION_MARKERS,
    DOWN_DIRECTION_TOKENS,
    FALLBACK_VOCABULARY,
    MIXED_DIRECTION_TOKENS,
    UP_DIRECTION_TOKENS,
    VOCABULARY,
    VOCABULARY_VERSION,
)

TRANSFORM_VERSION = "catalyst-transform/1.1.0"

# "(주도주 : ...)" 또는 "(관련주 : A, B)" 같은 문장 끝 종목 나열 괄호.
_TRAILING_REFERENCE_RE = re.compile(r"\([^()]*\)\s*[.…]?\s*$")
# "... 등에 상승" 꼬리. 연결어와 방향 동사가 문장 끝에 붙은 경우만 잡는다.
# 열거 조사 "등"은 어절 첫머리일 때만 연결어다 — "폭등에/급등에"의 "등에"를
# 삼키면 core가 "국내 증시 폭"처럼 잘려 유형·확실성 표지를 잃는다.
_TAIL_RE = re.compile(
    r"(?:(?<=\s)등(?:에|으로)?|에|으로|속에?|가운데|하며|하면서|받으며|힘입어|따라)?\s*"
    r"(?P<verb>급등|급락|급반등|급반락|상승|하락|강세|약세|폭등|폭락"
    r"|상한가|하한가|등락|혼조|반등|오름세|내림세)"
    r"(?:세)?\s*[.…]?\s*$"
)

_UP_SET = frozenset(UP_DIRECTION_TOKENS)
_DOWN_SET = frozenset(DOWN_DIRECTION_TOKENS)


def _find_all(text: str, needle: str, end: int) -> list[int]:
    positions: list[int] = []
    cursor = text.find(needle, 0, end)
    while cursor != -1:
        positions.append(cursor)
        cursor = text.find(needle, cursor + 1, end)
    return positions


def parse_cause_sentence(raw_text: str) -> ParsedCauseSentence:
    """원인문을 사유 본문·방향 꼬리·종목 나열 괄호로 해부한다."""

    reference_match = _TRAILING_REFERENCE_RE.search(raw_text)
    body_end = reference_match.start() if reference_match else len(raw_text)
    trailing_reference = (
        reference_match.group(0).strip() if reference_match else None
    )
    tail_match = _TAIL_RE.search(raw_text, 0, body_end)
    if tail_match is not None and tail_match.start() > 0:
        core_end = tail_match.start()
        tail = tail_match.group(0).strip()
        direction_verb: str | None = tail_match.group("verb")
        direction_verb_start: int | None = tail_match.start("verb")
    else:
        core_end = body_end
        tail = None
        direction_verb = None
        direction_verb_start = None
    return ParsedCauseSentence(
        raw_text=raw_text,
        core_text=raw_text[:core_end].strip(),
        core_end=core_end,
        tail_text=tail,
        trailing_reference_text=trailing_reference,
        direction_verb=direction_verb,
        direction_verb_start=direction_verb_start,
    )


def _direction_of(token: str) -> Direction:
    if token in _UP_SET:
        return "UP"
    if token in _DOWN_SET:
        return "DOWN"
    return "MIXED"


def _classify_direction(
    parsed: ParsedCauseSentence,
) -> tuple[Direction, tuple[EvidenceSpan, ...]]:
    text = parsed.raw_text
    scan_end = (
        parsed.core_end
        if parsed.tail_text is None
        else parsed.core_end + len(parsed.tail_text) + 8
    )
    mixed_positions = [
        (position, token)
        for token in MIXED_DIRECTION_TOKENS
        for position in _find_all(text, token, min(scan_end, len(text)))
    ]
    if mixed_positions:
        position, token = min(mixed_positions)
        span = EvidenceSpan(
            "direction", "MIXED", token, position, position + len(token)
        )
        return "MIXED", (span,)
    if parsed.direction_verb is not None and parsed.direction_verb_start is not None:
        verb = parsed.direction_verb
        start = parsed.direction_verb_start
        span = EvidenceSpan(
            "direction", _direction_of(verb), verb, start, start + len(verb)
        )
        return _direction_of(verb), (span,)
    directional = [
        (position, token)
        for token in (*UP_DIRECTION_TOKENS, *DOWN_DIRECTION_TOKENS)
        for position in _find_all(text, token, len(text))
    ]
    if not directional:
        return "UNKNOWN", ()
    # 결과 서술이 문장 뒤에 오므로 마지막 방향 표지의 계열을 취한다.
    position, token = max(directional)
    span = EvidenceSpan(
        "direction", _direction_of(token), token, position, position + len(token)
    )
    return _direction_of(token), (span,)


def _classify_certainty(
    parsed: ParsedCauseSentence,
) -> tuple[Certainty, tuple[EvidenceSpan, ...]]:
    text = parsed.raw_text
    candidates: list[tuple[int, int, Certainty, str]] = []
    families: tuple[tuple[Certainty, tuple[str, ...]], ...] = (
        ("ANTICIPATION", ANTICIPATION_MARKERS),
        ("CONFIRMED", CONFIRMED_MARKERS),
    )
    for certainty, markers in families:
        for marker in markers:
            for position in _find_all(text, marker, parsed.core_end):
                candidates.append((position, len(marker), certainty, marker))
    if not candidates:
        return "UNSPECIFIED", ()
    # 가장 오른쪽 표지 — 끝 위치 기준, 같으면 긴 표지가 이긴다. 끝 위치로
    # 비교해야 "검토 소식" 같은 복합 표지가 안에 든 "소식"을 가릴 수 있다.
    position, length, certainty, marker = max(
        candidates, key=lambda item: (item[0] + item[1], item[1])
    )
    span = EvidenceSpan("certainty", certainty, marker, position, position + length)
    return certainty, (span,)


def _classify_continuation(
    parsed: ParsedCauseSentence,
) -> tuple[bool, tuple[EvidenceSpan, ...]]:
    found: list[tuple[int, str]] = []
    for marker in CONTINUATION_MARKERS:
        positions = _find_all(parsed.raw_text, marker, parsed.core_end)
        if positions:
            found.append((positions[0], marker))
    if not found:
        return False, ()
    position, marker = min(found)
    span = EvidenceSpan(
        "continuation", "CONTINUATION", marker, position, position + len(marker)
    )
    return True, (span,)


def _classify_types(
    parsed: ParsedCauseSentence,
) -> tuple[tuple[str, ...], tuple[EvidenceSpan, ...]]:
    text = parsed.raw_text
    matches: list[tuple[int, int, int, str, str]] = []
    for rank, definition in enumerate(VOCABULARY):
        for keyword in definition.keywords:
            for position in _find_all(text, keyword, parsed.core_end):
                matches.append(
                    (position, -len(keyword), rank, definition.type_id, keyword)
                )
    matches.sort()
    accepted: list[tuple[int, int, str, str]] = []
    claimed_end = -1
    for position, negative_length, _rank, type_id, keyword in matches:
        end = position - negative_length
        if position < claimed_end:
            continue
        accepted.append((position, end, type_id, keyword))
        claimed_end = end
    type_ids: list[str] = []
    spans: list[EvidenceSpan] = []
    for position, end, type_id, keyword in accepted:
        if type_id not in type_ids:
            type_ids.append(type_id)
        spans.append(EvidenceSpan("catalyst_type", type_id, keyword, position, end))
    # 소재 주제어는 primary를 놓고 다투지 않는다 — 본 어휘(행위·사건 명사)가
    # 비었을 때만 첫 주제어가 primary가 되고, 그 외에는 뒤에 덧붙는다.
    fallback_matches = [
        (position, -len(keyword), type_id, keyword)
        for type_id, keywords in FALLBACK_VOCABULARY
        for keyword in keywords
        for position in _find_all(text, keyword, parsed.core_end)
    ]
    for position, negative_length, type_id, keyword in sorted(fallback_matches):
        if type_id not in type_ids:
            type_ids.append(type_id)
            spans.append(
                EvidenceSpan(
                    "catalyst_type", type_id, keyword, position, position - negative_length
                )
            )
    if not type_ids:
        # 어휘가 비었는데 사유 본문 안에 시세 동사가 있으면 다른 자산의
        # 가격 움직임 서술이다 — 시장·주가 동조로 폴백한다("퍼스트 솔라 급등").
        directional = [
            (position, token)
            for token in (*UP_DIRECTION_TOKENS, *DOWN_DIRECTION_TOKENS)
            for position in _find_all(text, token, parsed.core_end)
        ]
        if directional:
            position, token = min(directional)
            type_ids.append("MARKET_SYNC")
            spans.append(
                EvidenceSpan(
                    "catalyst_type", "MARKET_SYNC", token, position, position + len(token)
                )
            )
    return tuple(type_ids), tuple(spans)


def classify_catalyst(raw_text: str) -> CatalystClassification:
    """원인문 한 줄을 4축(유형·방향·확실성·지속)으로 분류한다."""

    parsed = parse_cause_sentence(raw_text)
    type_ids, type_spans = _classify_types(parsed)
    direction, direction_spans = _classify_direction(parsed)
    certainty, certainty_spans = _classify_certainty(parsed)
    continuation, continuation_spans = _classify_continuation(parsed)
    return CatalystClassification(
        vocabulary_version=VOCABULARY_VERSION,
        transform_version=TRANSFORM_VERSION,
        type_ids=type_ids,
        primary_type_id=type_ids[0] if type_ids else None,
        direction=direction,
        certainty=certainty,
        continuation=continuation,
        evidence_spans=(
            *type_spans,
            *direction_spans,
            *certainty_spans,
            *continuation_spans,
        ),
    )
