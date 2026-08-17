"""history 원천을 현실 사건 초안과 시장 반응으로 분리한다 (E-22 단계 4)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from packages.infostock.hashing import sha256_json, sha256_text

from .company_roles import (
    DIRECT_EVENT_ROLES,
    CompanyRole,
    HistoryCompanyRoleLabel,
)
from .participants import ActorMention, extract_actor_mentions
from .projects import (
    EventStage,
    EventStageEvidence,
    detect_event_stage,
    extract_project_reference,
    project_fingerprint,
    project_id_from_fingerprint,
)
from .transform import classify_catalyst, parse_cause_sentence

EVENT_STRUCTURE_TRANSFORM_VERSION = "event-structure-transform/1.0.8"
_CLAUSE_BREAK_RE = re.compile(
    r"[;；]\s*|\n+|(?<=[.!?。])\s+|(?<=소식)[,·]\s*(?=[가-힣A-Za-z0-9])"
)
_SOFT_CLAUSE_BREAK_RE = re.compile(r"\s+및\s+|[,·]\s*")
_EDGE_RE = re.compile(r"^[\s,·:：-]+|[\s,·:：-]+$")
_ACTION_MARKERS = (
    "공급계약 체결",
    "수주계약 체결",
    "본계약 체결",
    "계약 체결",
    "우선협상대상자 선정",
    "입찰 참여",
    "승인 신청",
    "허가 신청",
    "투자 결정",
    "합병 결정",
    "인수 추진",
    "납품 완료",
    "공급 개시",
    "생산 개시",
    "개발 성공",
    "업무협약 체결",
    "양해각서 체결",
    "계약 해지",
    "수주 취소",
    "수주",
    "투자",
    "개발",
    "승인",
    "허가",
    "발표",
    "공급",
    "납품",
    "인수",
    "합병",
    "제재",
    "규제",
    "출시",
    "공개",
    "생산",
    "착공",
    "완공",
    "취소",
    "해지",
)
_OFFICIAL_MARKERS = (
    "공시",
    "발표",
    "계약 체결",
    "수주",
    "승인",
    "허가",
)
_COMPOUND_KRW_RE = re.compile(
    r"(?P<jo>\d+(?:\.\d+)?)\s*조\s*(?P<eok>\d[\d,]*)\s*억\s*원"
)
_MONEY_RE = re.compile(
    r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<scale>조|억|만)?\s*(?P<currency>원|달러|유로|엔)"
)
_PERCENT_RE = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*%")
_STAKE_CONTEXT_MARKERS = ("지분", "지분율", "보유", "인수", "매각", "취득", "처분")
_CAPACITY_RE = re.compile(
    r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>GW|MW|톤)"
)
_QUANTITY_RE = re.compile(r"(?P<number>\d[\d,]*)\s*(?P<unit>대|기|개)")
_APPROX_RE = re.compile(r"(?<![가-힣A-Za-z0-9])약\s*(?=[0-9가-힣])")


class Officiality(StrEnum):
    OFFICIAL = "OFFICIAL"
    REPORTED = "REPORTED"
    UNSPECIFIED = "UNSPECIFIED"


class NoveltyType(StrEnum):
    NEW = "NEW"
    REEMERGENCE = "REEMERGENCE"
    UNSPECIFIED = "UNSPECIFIED"


class CompanyImpact(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class ValueFactType(StrEnum):
    CONTRACT_VALUE = "CONTRACT_VALUE"
    INVESTMENT_VALUE = "INVESTMENT_VALUE"
    CAPACITY = "CAPACITY"
    QUANTITY = "QUANTITY"
    STAKE_PERCENT = "STAKE_PERCENT"


class ValueBasis(StrEnum):
    EXACT = "EXACT"
    ESTIMATE = "ESTIMATE"
    UP_TO = "UP_TO"
    LOWER_BOUND = "LOWER_BOUND"
    RANGE = "RANGE"
    TOTAL_PROJECT = "TOTAL_PROJECT"
    COMPANY_SHARE = "COMPANY_SHARE"


@dataclass(frozen=True, slots=True)
class ClauseSpan:
    source_order: int
    text: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.source_order < 0 or self.start < 0 or self.end <= self.start:
            raise ValueError("사건 절 span이 올바르지 않습니다.")


@dataclass(frozen=True, slots=True)
class CatalystEvidenceDraft:
    source_order: int
    field: str
    value: str
    keyword: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class CatalystCompanyRoleDraft:
    seed_stock_code: str
    role: CompanyRole
    impact: CompanyImpact
    evidence_start: int
    evidence_end: int


@dataclass(frozen=True, slots=True)
class CatalystParticipantDraft:
    actor: ActorMention
    participant_role: Literal["COUNTERPARTY", "LOCATION", "PARTICIPANT"]


@dataclass(frozen=True, slots=True)
class CatalystValueDraft:
    fact_type: ValueFactType
    reported_value: str
    normalized_value: Decimal
    unit: str
    currency: str | None
    value_basis: ValueBasis
    eligible_for_sum: bool
    evidence_start: int
    evidence_end: int


@dataclass(frozen=True, slots=True)
class SourceMentionDraft:
    source_theme_id: str
    source_history_key: str
    clause_order: int
    source_revision_hash: str
    source_text_hash: str
    start: int
    end: int
    transform_version: str
    output_hash: str


@dataclass(frozen=True, slots=True)
class ThemeReactionDraft:
    reaction_key: str
    source_theme_id: str
    theme_name: str
    source_history_key: str
    occurred_on: date | None
    direction: str
    leader_stock_codes: tuple[str, ...]
    related_stock_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalystDraft:
    source_mention: SourceMentionDraft
    raw_text: str
    occurred_on: date | None
    known_on: date | None
    primary_catalyst_type: str | None
    catalyst_types: tuple[str, ...]
    event_stage: EventStage
    stage_evidence: EventStageEvidence
    certainty: str
    novelty_type: NoveltyType
    action: str | None
    object_text: str | None
    project_reference: str | None
    project_fingerprint: str | None
    project_id: str | None
    participants: tuple[CatalystParticipantDraft, ...]
    company_roles: tuple[CatalystCompanyRoleDraft, ...]
    geography_codes: tuple[str, ...]
    values: tuple[CatalystValueDraft, ...]
    officiality: Officiality
    continuation: bool
    evidence: tuple[CatalystEvidenceDraft, ...]
    reaction: ThemeReactionDraft
    ontology_version: str
    classification_transform_version: str
    company_master_version: str
    transform_version: str
    dataset_hash: str
    content_hash: str
    dedup_key: str

    @property
    def output_hash(self) -> str:
        return sha256_json(self.as_dict(include_raw_text=False))

    def as_dict(self, *, include_raw_text: bool = True) -> dict[str, Any]:
        row: dict[str, Any] = {
            "sourceMention": {
                "sourceThemeId": self.source_mention.source_theme_id,
                "sourceHistoryKey": self.source_mention.source_history_key,
                "clauseOrder": self.source_mention.clause_order,
                "sourceRevisionHash": self.source_mention.source_revision_hash,
                "sourceTextHash": self.source_mention.source_text_hash,
                "start": self.source_mention.start,
                "end": self.source_mention.end,
                "transformVersion": self.source_mention.transform_version,
                "outputHash": self.source_mention.output_hash,
            },
            "occurredOn": self.occurred_on.isoformat() if self.occurred_on else None,
            "knownOn": self.known_on.isoformat() if self.known_on else None,
            "primaryCatalystType": self.primary_catalyst_type,
            "catalystTypes": list(self.catalyst_types),
            "eventStage": self.event_stage.value,
            "stageEvidence": (
                None
                if self.stage_evidence.keyword is None
                else {
                    "keyword": self.stage_evidence.keyword,
                    "start": self.stage_evidence.start,
                    "end": self.stage_evidence.end,
                }
            ),
            "certainty": self.certainty,
            "noveltyType": self.novelty_type.value,
            "action": self.action,
            "objectText": self.object_text,
            "projectReference": self.project_reference,
            "projectFingerprint": self.project_fingerprint,
            "projectId": self.project_id,
            "participants": [
                {
                    "actorKey": item.actor.actor_key,
                    "identityHash": item.actor.identity_hash,
                    "canonicalName": item.actor.canonical_name,
                    "actorKind": item.actor.actor_kind.value,
                    "geographyCode": item.actor.geography_code,
                    "participantRole": item.participant_role,
                    "start": item.actor.start,
                    "end": item.actor.end,
                }
                for item in self.participants
            ],
            "companyRoles": [
                {
                    "seedStockCode": item.seed_stock_code,
                    "role": item.role,
                    "impact": item.impact.value,
                    "evidenceStart": item.evidence_start,
                    "evidenceEnd": item.evidence_end,
                }
                for item in self.company_roles
            ],
            "geographyCodes": list(self.geography_codes),
            "values": [
                {
                    "factType": item.fact_type.value,
                    "reportedValue": item.reported_value,
                    "normalizedValue": str(item.normalized_value),
                    "unit": item.unit,
                    "currency": item.currency,
                    "valueBasis": item.value_basis.value,
                    "eligibleForSum": item.eligible_for_sum,
                    "evidenceStart": item.evidence_start,
                    "evidenceEnd": item.evidence_end,
                }
                for item in self.values
            ],
            "officiality": self.officiality.value,
            "continuation": self.continuation,
            "evidence": [
                {
                    "sourceOrder": item.source_order,
                    "field": item.field,
                    "value": item.value,
                    "keyword": item.keyword,
                    "start": item.start,
                    "end": item.end,
                }
                for item in self.evidence
            ],
            "reaction": {
                "reactionKey": self.reaction.reaction_key,
                "sourceThemeId": self.reaction.source_theme_id,
                "themeName": self.reaction.theme_name,
                "sourceHistoryKey": self.reaction.source_history_key,
                "occurredOn": (
                    self.reaction.occurred_on.isoformat()
                    if self.reaction.occurred_on
                    else None
                ),
                "direction": self.reaction.direction,
                "leaderStockCodes": list(self.reaction.leader_stock_codes),
                "relatedStockCodes": list(self.reaction.related_stock_codes),
            },
            "ontologyVersion": self.ontology_version,
            "classificationTransformVersion": self.classification_transform_version,
            "companyMasterVersion": self.company_master_version,
            "transformVersion": self.transform_version,
            "datasetHash": self.dataset_hash,
            "contentHash": self.content_hash,
            "dedupKey": self.dedup_key,
        }
        if include_raw_text:
            row["rawText"] = self.raw_text
        return row


def _contains_action(text: str) -> bool:
    return any(marker in text for marker in _ACTION_MARKERS)


def _split_soft_range(core: str, start: int, end: int) -> tuple[tuple[int, int], ...]:
    """행동이 양쪽에 명시된 쉼표·'및'만 사건 경계로 승격한다."""

    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        chosen: re.Match[str] | None = None
        for match in _SOFT_CLAUSE_BREAK_RE.finditer(core, cursor, end):
            if not _contains_action(core[cursor : match.start()]):
                continue
            if not _contains_action(core[match.end() : end]):
                continue
            chosen = match
            break
        if chosen is None:
            ranges.append((cursor, end))
            break
        ranges.append((cursor, chosen.start()))
        cursor = chosen.end()
    return tuple(ranges)


def split_event_clauses(raw_text: str) -> tuple[ClauseSpan, ...]:
    parsed = parse_cause_sentence(raw_text)
    core = raw_text[: parsed.core_end]
    boundaries = [0]
    for match in _CLAUSE_BREAK_RE.finditer(core):
        boundaries.extend((match.start(), match.end()))
    boundaries.append(len(core))
    hard_ranges = zip(boundaries[::2], boundaries[1::2], strict=False)
    raw_ranges = (
        soft_range
        for hard_start, hard_end in hard_ranges
        for soft_range in _split_soft_range(core, hard_start, hard_end)
    )
    clauses: list[ClauseSpan] = []
    for start, end in raw_ranges:
        value = core[start:end]
        stripped = _EDGE_RE.sub("", value)
        if not stripped:
            continue
        left = value.find(stripped)
        clause_start = start + left
        clauses.append(
            ClauseSpan(
                source_order=len(clauses),
                text=stripped,
                start=clause_start,
                end=clause_start + len(stripped),
            )
        )
    if clauses:
        return tuple(clauses)
    stripped = _EDGE_RE.sub("", core)
    if not stripped:
        return ()
    start = core.find(stripped)
    return (ClauseSpan(0, stripped, start, start + len(stripped)),)


def _impact(role: CompanyRole) -> CompanyImpact:
    if role == "BENEFICIARY":
        return CompanyImpact.POSITIVE
    if role == "ADVERSELY_AFFECTED":
        return CompanyImpact.NEGATIVE
    return CompanyImpact.UNKNOWN


def _company_roles_for_clause(
    label: HistoryCompanyRoleLabel, clause: ClauseSpan
) -> tuple[CatalystCompanyRoleDraft, ...]:
    found: list[CatalystCompanyRoleDraft] = []
    for mention in label.mentions:
        if mention.mention_kind != "BODY" or mention.seed_stock_code is None:
            continue
        if mention.start < clause.start or mention.end > clause.end:
            continue
        for role in mention.roles:
            if role.start < clause.start or role.end > clause.end:
                continue
            found.append(
                CatalystCompanyRoleDraft(
                    seed_stock_code=mention.seed_stock_code,
                    role=role.role,
                    impact=_impact(role.role),
                    evidence_start=role.start,
                    evidence_end=role.end,
                )
            )
    return tuple(
        sorted(
            found,
            key=lambda item: (
                item.evidence_start,
                item.seed_stock_code,
                item.role,
            ),
        )
    )


def _participant_role(text: str, actor: ActorMention) -> Literal[
    "COUNTERPARTY", "LOCATION", "PARTICIPANT"
]:
    local_end = actor.end
    after = text[local_end : local_end + 8]
    if re.match(r"\s*(?:정부\s*)?(?:와|과|로부터|상대로|에게)", after):
        return "COUNTERPARTY"
    return "LOCATION" if actor.geography_code else "PARTICIPANT"


def _action_and_object(clause: ClauseSpan) -> tuple[str | None, str | None]:
    matches = [
        (clause.text.find(marker), marker)
        for marker in _ACTION_MARKERS
        if clause.text.find(marker) >= 0
    ]
    if not matches:
        return None, None
    position, action = min(matches, key=lambda item: (item[0], -len(item[1])))
    prefix = clause.text[:position].strip(" ,·:：-")
    object_text = prefix.rsplit(",", maxsplit=1)[-1].strip() if prefix else None
    return action, object_text or None


def _basis(text: str, start: int, end: int) -> ValueBasis:
    window = text[max(0, start - 16) : min(len(text), end + 12)]
    if "총사업비" in window or "총 사업비" in window:
        return ValueBasis.TOTAL_PROJECT
    if "회사 몫" in window or "귀속분" in window:
        return ValueBasis.COMPANY_SHARE
    if "최대" in window:
        return ValueBasis.UP_TO
    if "이상" in window or "초과" in window:
        return ValueBasis.LOWER_BOUND
    if _APPROX_RE.search(window) is not None or "추정" in window:
        return ValueBasis.ESTIMATE
    if "~" in window or "에서" in window and "까지" in window:
        return ValueBasis.RANGE
    return ValueBasis.EXACT


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def _currency(code: str) -> str:
    return {"원": "KRW", "달러": "USD", "유로": "EUR", "엔": "JPY"}[code]


def _money_fact_type(text: str) -> ValueFactType | None:
    if any(marker in text for marker in ("계약", "수주", "공급", "납품")):
        return ValueFactType.CONTRACT_VALUE
    if "투자" in text:
        return ValueFactType.INVESTMENT_VALUE
    return None


def _eligible_for_sum(
    text: str, start: int, end: int, basis: ValueBasis
) -> bool:
    if basis is ValueBasis.EXACT:
        return True
    if basis is not ValueBasis.COMPANY_SHARE:
        return False
    window = text[max(0, start - 16) : min(len(text), end + 12)]
    if _APPROX_RE.search(window) is not None:
        return False
    if any(marker in window for marker in ("추정", "최대", "이상", "초과", "~")):
        return False
    return not ("에서" in window and "까지" in window)


def extract_catalyst_values(
    text: str, *, offset: int = 0
) -> tuple[CatalystValueDraft, ...]:
    values: list[CatalystValueDraft] = []
    occupied: list[tuple[int, int]] = []
    fact_type = _money_fact_type(text)

    for match in _COMPOUND_KRW_RE.finditer(text):
        if fact_type is None:
            continue
        start, end = match.span()
        normalized = _decimal(match.group("jo")) * Decimal(10**12) + _decimal(
            match.group("eok")
        ) * Decimal(10**8)
        basis = _basis(text, start, end)
        values.append(
            CatalystValueDraft(
                fact_type=fact_type,
                reported_value=match.group(0),
                normalized_value=normalized,
                unit="KRW",
                currency="KRW",
                value_basis=basis,
                eligible_for_sum=_eligible_for_sum(text, start, end, basis),
                evidence_start=offset + start,
                evidence_end=offset + end,
            )
        )
        occupied.append((start, end))

    scale = {None: Decimal(1), "만": Decimal(10**4), "억": Decimal(10**8), "조": Decimal(10**12)}
    for match in _MONEY_RE.finditer(text):
        start, end = match.span()
        if any(start < old_end and old_start < end for old_start, old_end in occupied):
            continue
        if fact_type is None:
            continue
        normalized = _decimal(match.group("number")) * scale[match.group("scale")]
        basis = _basis(text, start, end)
        currency = _currency(match.group("currency"))
        values.append(
            CatalystValueDraft(
                fact_type=fact_type,
                reported_value=match.group(0),
                normalized_value=normalized,
                unit=currency,
                currency=currency,
                value_basis=basis,
                eligible_for_sum=_eligible_for_sum(text, start, end, basis),
                evidence_start=offset + start,
                evidence_end=offset + end,
            )
        )

    for match in _PERCENT_RE.finditer(text):
        start, end = match.span()
        context = text[max(0, start - 16) : min(len(text), end + 12)]
        if not any(marker in context for marker in _STAKE_CONTEXT_MARKERS):
            continue
        values.append(
            CatalystValueDraft(
                fact_type=ValueFactType.STAKE_PERCENT,
                reported_value=match.group(0),
                normalized_value=_decimal(match.group("number")),
                unit="PERCENT",
                currency=None,
                value_basis=_basis(text, start, end),
                eligible_for_sum=False,
                evidence_start=offset + start,
                evidence_end=offset + end,
            )
        )
    for match in _CAPACITY_RE.finditer(text):
        start, end = match.span()
        unit = match.group("unit")
        normalized = _decimal(match.group("number"))
        normalized_unit = "TON" if unit == "톤" else "MW"
        if unit == "GW":
            normalized *= Decimal(1000)
        values.append(
            CatalystValueDraft(
                fact_type=ValueFactType.CAPACITY,
                reported_value=match.group(0),
                normalized_value=normalized,
                unit=normalized_unit,
                currency=None,
                value_basis=_basis(text, start, end),
                eligible_for_sum=False,
                evidence_start=offset + start,
                evidence_end=offset + end,
            )
        )
    for match in _QUANTITY_RE.finditer(text):
        start, end = match.span()
        values.append(
            CatalystValueDraft(
                fact_type=ValueFactType.QUANTITY,
                reported_value=match.group(0),
                normalized_value=_decimal(match.group("number")),
                unit={"대": "UNIT", "기": "UNIT", "개": "UNIT"}[match.group("unit")],
                currency=None,
                value_basis=_basis(text, start, end),
                eligible_for_sum=False,
                evidence_start=offset + start,
                evidence_end=offset + end,
            )
        )
    return tuple(sorted(values, key=lambda item: (item.evidence_start, item.fact_type)))


def _normalized_clause(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^0-9a-z가-힣]+", "", normalized)


def _source_mention(label: HistoryCompanyRoleLabel, clause: ClauseSpan) -> SourceMentionDraft:
    payload = {
        "sourceThemeId": label.source_theme_id,
        "sourceHistoryKey": label.source_history_key,
        "clauseOrder": clause.source_order,
        "sourceRevisionHash": label.history_content_hash,
        "sourceTextHash": sha256_text(clause.text),
        "start": clause.start,
        "end": clause.end,
        "transformVersion": EVENT_STRUCTURE_TRANSFORM_VERSION,
    }
    return SourceMentionDraft(
        source_theme_id=label.source_theme_id,
        source_history_key=label.source_history_key,
        clause_order=clause.source_order,
        source_revision_hash=label.history_content_hash,
        source_text_hash=sha256_text(clause.text),
        start=clause.start,
        end=clause.end,
        transform_version=EVENT_STRUCTURE_TRANSFORM_VERSION,
        output_hash=sha256_json(payload),
    )


def _reaction(label: HistoryCompanyRoleLabel, direction: str) -> ThemeReactionDraft:
    leaders = tuple(
        sorted(
            {
                mention.seed_stock_code
                for mention in label.mentions
                if mention.mention_kind == "LEADER_LIST"
                and mention.seed_stock_code is not None
            }
        )
    )
    related = tuple(
        sorted(
            {
                mention.seed_stock_code
                for mention in label.mentions
                if mention.mention_kind == "MEMBERSHIP"
                and mention.seed_stock_code is not None
            }
        )
    )
    reaction_key = sha256_json(
        {
            "sourceThemeId": label.source_theme_id,
            "sourceHistoryKey": label.source_history_key,
        }
    )
    return ThemeReactionDraft(
        reaction_key=f"reaction_{reaction_key[:24]}",
        source_theme_id=label.source_theme_id,
        theme_name=label.theme_name,
        source_history_key=label.source_history_key,
        occurred_on=label.event_date,
        direction=direction,
        leader_stock_codes=leaders,
        related_stock_codes=related,
    )


def structure_history_catalysts(
    label: HistoryCompanyRoleLabel,
    *,
    dataset_hash: str,
) -> tuple[CatalystDraft, ...]:
    """history 역할 라벨 한 건을 원자 사건 초안으로 바꾼다."""

    whole = classify_catalyst(label.raw_text)
    reaction = _reaction(label, whole.direction)
    drafts: list[CatalystDraft] = []
    for clause in split_event_clauses(label.raw_text):
        classification = classify_catalyst(clause.text)
        stage = detect_event_stage(clause.text, offset=clause.start)
        roles = _company_roles_for_clause(label, clause)
        actors = extract_actor_mentions(clause.text, offset=clause.start)
        participants = tuple(
            CatalystParticipantDraft(
                actor=actor,
                participant_role=_participant_role(label.raw_text, actor),
            )
            for actor in actors
        )
        company_codes = tuple(
            sorted(
                {
                    role.seed_stock_code
                    for role in roles
                }
            )
        )
        project_reference = extract_project_reference(clause.text)
        fingerprint = project_fingerprint(
            reference=project_reference,
            company_codes=company_codes,
            participant_keys=tuple(actor.actor_key for actor in actors),
        )
        values = extract_catalyst_values(clause.text, offset=clause.start)
        action, object_text = _action_and_object(clause)
        evidence = tuple(
            CatalystEvidenceDraft(
                source_order=order,
                field=span.field,
                value=span.value,
                keyword=span.keyword,
                start=clause.start + span.start,
                end=clause.start + span.end,
            )
            for order, span in enumerate(classification.evidence_spans)
        )
        project_id = project_id_from_fingerprint(fingerprint)
        identity: dict[str, Any] = {
            "occurredOn": label.event_date.isoformat() if label.event_date else None,
            "stage": stage.stage.value,
            "primaryType": classification.primary_type_id,
            "companyCodes": company_codes,
            "values": [
                (
                    item.fact_type.value,
                    str(item.normalized_value),
                    item.unit,
                    item.currency,
                    item.value_basis.value,
                )
                for item in values
            ],
            "missingDateSource": (
                label.source_history_key if label.event_date is None else None
            ),
        }
        identity["normalizedClause"] = _normalized_clause(clause.text)
        if project_id is None:
            identity["participants"] = sorted(actor.actor_key for actor in actors)
        else:
            identity["projectId"] = project_id
            if not company_codes:
                identity["participants"] = sorted(actor.actor_key for actor in actors)
        dedup_key = sha256_json(identity)
        drafts.append(
            CatalystDraft(
                source_mention=_source_mention(label, clause),
                raw_text=clause.text,
                occurred_on=label.event_date,
                known_on=label.event_date,
                primary_catalyst_type=classification.primary_type_id,
                catalyst_types=classification.type_ids,
                event_stage=stage.stage,
                stage_evidence=stage,
                certainty=classification.certainty,
                novelty_type=(
                    NoveltyType.REEMERGENCE
                    if classification.continuation
                    else NoveltyType.NEW
                ),
                action=action,
                object_text=object_text,
                project_reference=project_reference,
                project_fingerprint=fingerprint,
                project_id=project_id,
                participants=participants,
                company_roles=roles,
                geography_codes=tuple(
                    sorted(
                        {
                            actor.geography_code
                            for actor in actors
                            if actor.geography_code is not None
                        }
                    )
                ),
                values=values,
                officiality=(
                    Officiality.OFFICIAL
                    if (
                        stage.stage is not EventStage.RUMOR
                        and classification.certainty != "ANTICIPATION"
                        and any(
                            marker in clause.text for marker in _OFFICIAL_MARKERS
                        )
                    )
                    else Officiality.REPORTED
                ),
                continuation=classification.continuation,
                evidence=evidence,
                reaction=reaction,
                ontology_version=classification.vocabulary_version,
                classification_transform_version=classification.transform_version,
                company_master_version=label.company_master_version,
                transform_version=EVENT_STRUCTURE_TRANSFORM_VERSION,
                dataset_hash=dataset_hash,
                content_hash=sha256_text(clause.text),
                dedup_key=dedup_key,
            )
        )
    return tuple(drafts)


def build_history_catalyst_drafts(
    labels: tuple[HistoryCompanyRoleLabel, ...],
    *,
    dataset_hash: str,
) -> tuple[tuple[CatalystDraft, ...], dict[str, Any]]:
    drafts = tuple(
        draft
        for label in sorted(
            labels,
            key=lambda item: (
                item.event_date is None,
                item.event_date or date.max,
                item.source_theme_id,
                item.source_history_key,
            ),
        )
        for draft in structure_history_catalysts(label, dataset_hash=dataset_hash)
    )
    return drafts, {
        "schemaVersion": "1.0.0",
        "datasetHash": dataset_hash,
        "transformVersion": EVENT_STRUCTURE_TRANSFORM_VERSION,
        "sourceHistoryCount": len(labels),
        "catalystDraftCount": len(drafts),
        "directCompanyRoleCount": sum(
            role.role in DIRECT_EVENT_ROLES
            for draft in drafts
            for role in draft.company_roles
        ),
        "projectLinkedDraftCount": sum(draft.project_id is not None for draft in drafts),
        "valueFactCount": sum(len(draft.values) for draft in drafts),
        "reviewStatus": "AI_DRAFT",
    }
