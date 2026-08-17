"""회사 밖 정부·기관·국가 참여자와 지역 추출 (E-22 단계 4)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from packages.infostock.hashing import sha256_json


class ActorKind(StrEnum):
    COMPANY = "COMPANY"
    GOVERNMENT = "GOVERNMENT"
    PUBLIC_INSTITUTION = "PUBLIC_INSTITUTION"
    PERSON = "PERSON"
    INTERNATIONAL_ORGANIZATION = "INTERNATIONAL_ORGANIZATION"
    COUNTRY = "COUNTRY"
    OTHER = "OTHER"


_GEOGRAPHIES: tuple[tuple[str, str, str], ...] = (
    ("대한민국", "KR", "대한민국"),
    ("한국", "KR", "대한민국"),
    ("미국", "US", "미국"),
    ("중국", "CN", "중국"),
    ("일본", "JP", "일본"),
    ("폴란드", "PL", "폴란드"),
    ("사우디아라비아", "SA", "사우디아라비아"),
    ("사우디", "SA", "사우디아라비아"),
    ("아랍에미리트", "AE", "아랍에미리트"),
    ("UAE", "AE", "아랍에미리트"),
    ("호주", "AU", "호주"),
    ("캐나다", "CA", "캐나다"),
    ("독일", "DE", "독일"),
    ("프랑스", "FR", "프랑스"),
    ("영국", "GB", "영국"),
    ("러시아", "RU", "러시아"),
    ("우크라이나", "UA", "우크라이나"),
    ("인도", "IN", "인도"),
    ("베트남", "VN", "베트남"),
    ("유럽연합", "EU", "유럽연합"),
    ("EU", "EU", "유럽연합"),
)
_GOVERNMENT_SUFFIXES = (
    " 국방부",
    " 국방청",
    " 조달청",
    " 정부",
    " 당국",
    "국방부",
    "국방청",
    "조달청",
    "정부",
    "당국",
)
_KOREAN_RE = re.compile(r"[가-힣]")
_ALLOWED_ATTACHED_SUFFIXES = (
    "은",
    "는",
    "이",
    "가",
    "의",
    "에",
    "와",
    "과",
    "로",
    "측",
    "내",
    "발",
    "향",
    "산",
    "정부",
    "국방부",
    "국방청",
    "조달청",
    "당국",
)
_DELIVERY_OBJECTS = ("차량", "제품", "물품", "선박", "항공기", "장비", "초도")
_DELIVERY_AFTER = ("완료", "예정", "개시", "지연", "물량", "대수")


@dataclass(frozen=True, slots=True)
class ActorMention:
    actor_key: str
    identity_hash: str
    canonical_name: str
    actor_kind: ActorKind
    geography_code: str | None
    start: int
    end: int
    evidence_text: str

    def __post_init__(self) -> None:
        if not self.actor_key or not self.canonical_name:
            raise ValueError("참여자 key·이름은 비울 수 없습니다.")
        if not re.fullmatch(r"[0-9a-f]{64}", self.identity_hash):
            raise ValueError("참여자 identity_hash는 SHA-256이어야 합니다.")
        if self.actor_key != f"actor_{self.identity_hash[:24]}":
            raise ValueError("참여자 key와 identity_hash가 맞지 않습니다.")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("참여자 evidence span이 올바르지 않습니다.")


def _kind(alias: str, suffix: str) -> ActorKind:
    if alias in {"EU", "유럽연합"}:
        return ActorKind.INTERNATIONAL_ORGANIZATION
    return ActorKind.GOVERNMENT if suffix else ActorKind.COUNTRY


def _valid_alias_boundary(text: str, alias: str, start: int, end: int) -> bool:
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    if alias.isascii():
        return not (
            (before and before.isalnum()) or (after and after.isalnum())
        )
    if before and _KOREAN_RE.fullmatch(before):
        return False
    if after and _KOREAN_RE.fullmatch(after):
        return any(text.startswith(suffix, end) for suffix in _ALLOWED_ATTACHED_SUFFIXES)
    return True


def _ambiguous_india_is_country(text: str, start: int, end: int) -> bool:
    if text.startswith("네시아", end):
        return False
    before = text[max(0, start - 8) : start].rstrip()
    after = text[end : end + 8].lstrip()
    if any(before.endswith(item) for item in _DELIVERY_OBJECTS):
        return False
    return not any(after.startswith(item) for item in _DELIVERY_AFTER)


def extract_actor_mentions(text: str, *, offset: int = 0) -> tuple[ActorMention, ...]:
    """통제된 국가·정부 표지만 추출한다. 회사명 추출은 회사 master가 맡는다."""

    candidates: list[ActorMention] = []
    occupied: list[tuple[int, int]] = []
    for alias, code, canonical in sorted(_GEOGRAPHIES, key=lambda row: -len(row[0])):
        pattern = re.compile(re.escape(alias), re.IGNORECASE)
        for match in pattern.finditer(text):
            start, end = match.span()
            if not _valid_alias_boundary(text, alias, start, end):
                continue
            if alias == "인도" and not _ambiguous_india_is_country(text, start, end):
                continue
            if any(start < previous_end and previous_start < end for previous_start, previous_end in occupied):
                continue
            suffix = next(
                (
                    item
                    for item in _GOVERNMENT_SUFFIXES
                    if text.startswith(item, end)
                ),
                "",
            )
            evidence_end = end + len(suffix)
            evidence = text[start:evidence_end]
            kind = _kind(alias.upper(), suffix)
            identity_hash = sha256_json(
                {"kind": kind.value, "name": canonical, "geography": code}
            )
            candidates.append(
                ActorMention(
                    actor_key=f"actor_{identity_hash[:24]}",
                    identity_hash=identity_hash,
                    canonical_name=(f"{canonical}{suffix}" if suffix else canonical),
                    actor_kind=kind,
                    geography_code=code,
                    start=offset + start,
                    end=offset + evidence_end,
                    evidence_text=evidence,
                )
            )
            occupied.append((start, evidence_end))
    return tuple(sorted(candidates, key=lambda item: (item.start, -item.end)))
