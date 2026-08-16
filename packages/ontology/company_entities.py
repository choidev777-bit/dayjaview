"""회사 정체성과 alias (회사 온톨로지 단계 2).

사용자가 묻는 "회사"와 거래되는 "종목"은 다른 엔티티다. 상장 종목 정본은
`core.infostock_stocks`이며 여기서 새 종목 ID를 만들지 않는다. 이 모듈은 이미
수집된 종목 참조에서 회사·alias·회사-종목 관계를 결정론적으로 만든다.

원칙은 precision 우선이다.

- 6자리 종목코드가 있는 참조만 회사에 연결한다. 코드가 없으면 검수로 남긴다.
- 편집거리·임베딩으로 회사를 합치지 않는다. 우선주는 이름 접미사와 종목코드가
  **둘 다** 맞을 때만 보통주와 같은 회사가 된다.
- 사명 이력은 KRX 상장 이름(`KRX_LISTING`)을 먼저 쓴다. 인포스탁은 과거 기록의
  이름을 현재 이름으로 소급 정규화한 코드가 있어 그 원천만으로는 이력이 없다.
  KRX가 모르는 이름만 관측 구간(`OBSERVED_MENTION`)으로 남기며, 이는 공식
  유효기간이 아니다.
- 그 구간 밖의 시점으로 물으면 자동 연결하지 않고 후보만 돌려준다.

규칙을 고치면 COMPANY_MASTER_VERSION을 올린다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from typing import TYPE_CHECKING, Literal

from packages.infostock.hashing import sha256_json

from .krx_names import KrxNameIndex, KrxNameWindow

if TYPE_CHECKING:
    from packages.infostock.models import ImportBundle

COMPANY_MASTER_VERSION = "company-master/1.0.0"

STOCK_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
CORP_CODE_RE = re.compile(r"^[0-9]{8}$")
# 원천이 이름 없이 "087730-"처럼 코드 표기만 남긴 참조. 이름으로 쓰지 않는다.
# 꼬리 붙임표를 요구한다 — "SIMPAC"·"INVENI"처럼 여섯 글자 대문자 사명이 있다.
_CODE_DISPLAY_RE = re.compile(r"[0-9A-Z]{6}-")

# 우선주 이름 접미사. 이 표지만으로는 우선주로 보지 않는다 — "연우"처럼 보통주
# 이름도 걸리기 때문에 종목코드 짝이 함께 맞아야 한다.
_SHARE_CLASS_SUFFIX_RE = re.compile(r"(?:[0-9]+)?우(?:B)?$")

# 안전한 정규화만 한다. 법인 표기와 공백·대소문자 차이는 같은 이름으로 보고,
# 그 밖의 글자는 건드리지 않는다.
_CORPORATE_FORMS = ("주식회사", "(주)", "(유)", "(재)", "(사)", "(합)")
_WHITESPACE_RE = re.compile(r"\s+")

AliasType = Literal["CURRENT_NAME", "PAST_NAME", "SHARE_CLASS_NAME"]
ShareClass = Literal["COMMON", "PREFERRED", "UNKNOWN"]
LinkBasis = Literal["STOCK_CODE", "SHARE_CLASS_NAME_AND_CODE"]
SourceAuthority = Literal[
    "CURRENT_MEMBERSHIP", "HISTORICAL_REFERENCE", "DAILY_REFERENCE", "KRX_LISTING"
]
NameBasis = Literal[
    "CURRENT_MEMBERSHIP",
    "HISTORICAL_REFERENCE",
    "DAILY_REFERENCE",
    "KRX_LISTING",
    "UNKNOWN",
]
ValidityBasis = Literal["OBSERVED_MENTION", "KRX_LISTING"]
ChangeType = Literal["CREATED", "NAME_CHANGED", "INSTRUMENT_LINKED"]
ReviewSourceKind = Literal["HISTORY_LEADER", "HISTORY_MEMBER", "THEME_MEMBERSHIP"]
ReviewReason = Literal["SOURCE_CODE_MISSING", "CODE_INVALID"]
ResolutionStatus = Literal["RESOLVED", "AMBIGUOUS", "OUT_OF_VALIDITY", "UNKNOWN"]

# core.infostock_stocks의 current_name을 고르는 순위와 같아야 한다.
_AUTHORITY_RANK: dict[str, int] = {
    "CURRENT_MEMBERSHIP": 30,
    "HISTORICAL_REFERENCE": 20,
    "DAILY_REFERENCE": 10,
}


def normalize_company_name(name: str) -> str:
    """비교용 정규화. 공백·법인 표기·대소문자만 지운다."""

    text = unicodedata.normalize("NFKC", name).strip()
    for form in _CORPORATE_FORMS:
        text = text.replace(form, "")
    return _WHITESPACE_RE.sub("", text).casefold()


def split_share_class(name: str) -> tuple[str, bool]:
    """우선주 접미사를 떼어 (본디 이름, 접미사 있었나)를 돌려준다.

    남는 이름이 한 글자면 접미사로 보지 않는다 — "연우"의 "우"는 접미사가 아니다.
    """

    match = _SHARE_CLASS_SUFFIX_RE.search(name)
    if match is None or match.start() < 2:
        return name, False
    return name[: match.start()], True


@dataclass(frozen=True, slots=True)
class CompanyAliasDraft:
    """회사 이름 하나와 그 이름이 관측된 구간."""

    alias: str
    normalized_alias: str
    alias_type: AliasType
    validity_basis: ValidityBasis
    source_authority: SourceAuthority
    valid_from: date | None
    valid_to: date | None
    mention_count: int

    def covers(self, as_of: date | None) -> bool:
        """as_of가 관측 구간 안인가. None 경계는 열려 있다는 뜻이다."""

        if as_of is None:
            return True
        if self.valid_from is not None and as_of < self.valid_from:
            return False
        return self.valid_to is None or as_of <= self.valid_to


@dataclass(frozen=True, slots=True)
class CompanyInstrumentDraft:
    """회사와 상장 종목의 연결 하나."""

    stock_code: str
    share_class: ShareClass
    link_basis: LinkBasis
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True, slots=True)
class CompanyRevisionDraft:
    """사명 변경·종목 연결 같은 회사 이력 한 줄."""

    change_type: ChangeType
    effective_on: date | None
    previous_value: str | None
    new_value: str | None

    def content_hash(self, seed_stock_code: str) -> str:
        return sha256_json(
            {
                "seedStockCode": seed_stock_code,
                "changeType": self.change_type,
                "effectiveOn": None
                if self.effective_on is None
                else self.effective_on.isoformat(),
                "previousValue": self.previous_value,
                "newValue": self.new_value,
            }
        )


@dataclass(frozen=True, slots=True)
class CompanyDraft:
    """적재 전 회사 한 곳."""

    seed_stock_code: str
    canonical_name: str
    name_basis: NameBasis
    dart_corp_code: str | None
    aliases: tuple[CompanyAliasDraft, ...]
    instruments: tuple[CompanyInstrumentDraft, ...]
    revisions: tuple[CompanyRevisionDraft, ...]


@dataclass(frozen=True, slots=True)
class UnresolvedReferenceDraft:
    """종목코드가 없어 회사에 연결하지 못한 원천 참조."""

    source_kind: ReviewSourceKind
    source_name: str
    normalized_name: str
    reason: ReviewReason
    mention_count: int
    first_event_date: date | None
    last_event_date: date | None


@dataclass(frozen=True, slots=True)
class CompanyMaster:
    """한 번의 build 산출물 전체."""

    master_version: str
    companies: tuple[CompanyDraft, ...]
    unresolved: tuple[UnresolvedReferenceDraft, ...]

    @property
    def instrument_count(self) -> int:
        return sum(len(company.instruments) for company in self.companies)

    @property
    def alias_count(self) -> int:
        return sum(len(company.aliases) for company in self.companies)

    @property
    def revision_count(self) -> int:
        return sum(len(company.revisions) for company in self.companies)


@dataclass(frozen=True, slots=True)
class CompanyCandidate:
    """해석 후보 하나. 어떤 이름의 어느 구간으로 걸렸는지 남긴다."""

    seed_stock_code: str
    canonical_name: str
    matched_alias: str
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True, slots=True)
class CompanyResolution:
    """회사 슬롯 해석 결과."""

    status: ResolutionStatus
    matched_by: Literal["STOCK_CODE", "ALIAS", "NONE"]
    candidates: tuple[CompanyCandidate, ...]

    @property
    def seed_stock_code(self) -> str | None:
        if self.status != "RESOLVED":
            return None
        return self.candidates[0].seed_stock_code


@dataclass
class _NameObservation:
    """(종목코드, 이름) 한 쌍의 누적 관측."""

    name: str
    authority: SourceAuthority
    rank: int
    sequence: int
    mention_count: int = 0
    first_event_date: date | None = None
    last_event_date: date | None = None

    def observe(
        self, *, authority: SourceAuthority, rank: int, sequence: int, event_date: date | None
    ) -> None:
        self.mention_count += 1
        if rank > self.rank or (rank == self.rank and sequence < self.sequence):
            self.authority = authority
            self.rank = rank
            self.sequence = sequence
        if event_date is not None:
            if self.first_event_date is None or event_date < self.first_event_date:
                self.first_event_date = event_date
            if self.last_event_date is None or event_date > self.last_event_date:
                self.last_event_date = event_date


@dataclass
class _UnresolvedAccumulator:
    source_name: str
    reason: ReviewReason
    mention_count: int = 0
    first_event_date: date | None = None
    last_event_date: date | None = None
    names: dict[str, int] = field(default_factory=dict)

    def observe(self, name: str, event_date: date | None) -> None:
        self.mention_count += 1
        self.names[name] = self.names.get(name, 0) + 1
        if event_date is not None:
            if self.first_event_date is None or event_date < self.first_event_date:
                self.first_event_date = event_date
            if self.last_event_date is None or event_date > self.last_event_date:
                self.last_event_date = event_date

    @property
    def representative_name(self) -> str:
        return min(self.names.items(), key=lambda item: (-item[1], item[0]))[0]


def _current_name_key(entry: _NameObservation) -> tuple[int, int, int, int]:
    """현재 이름 우선순위: 원천 권위 → 최근 관측 → 등장 횟수 → 처음 나온 순서."""

    last_seen = 0 if entry.last_event_date is None else entry.last_event_date.toordinal()
    return (-entry.rank, -last_seen, -entry.mention_count, entry.sequence)


def build_company_master(
    bundle: ImportBundle,
    *,
    corp_codes: Mapping[str, str] | None = None,
    krx_names: KrxNameIndex | None = None,
) -> CompanyMaster:
    """수집본에서 회사 master를 만든다. 같은 입력이면 같은 결과다.

    `corp_codes`는 OpenDART 고유번호 대조표(6자리 종목코드 → 8자리 corp_code)다.
    `krx_names`는 KRX 일별매매에서 뽑은 종목명 이력이다. 인포스탁은 과거 기록의
    이름을 현재 이름으로 소급 정규화한 코드가 있어 그 원천만으로는 사명 이력이
    나오지 않는다. 둘 다 없으면 비워 둔다. 지어내지 않는다.
    """

    observations: dict[str, dict[str, _NameObservation]] = {}
    unresolved: dict[tuple[ReviewSourceKind, str], _UnresolvedAccumulator] = {}
    sequence = 0

    def observe(
        code: str | None,
        name: str,
        *,
        authority: SourceAuthority,
        event_date: date | None,
        kind: ReviewSourceKind,
        quality_status: str,
    ) -> None:
        nonlocal sequence
        sequence += 1
        if code is not None and STOCK_CODE_RE.fullmatch(code):
            by_name = observations.setdefault(code, {})
            entry = by_name.get(name)
            if entry is None:
                entry = _NameObservation(
                    name=name,
                    authority=authority,
                    rank=_AUTHORITY_RANK[authority],
                    sequence=sequence,
                )
                by_name[name] = entry
            entry.observe(
                authority=authority,
                rank=_AUTHORITY_RANK[authority],
                sequence=sequence,
                event_date=event_date,
            )
            return
        reason: ReviewReason = (
            "CODE_INVALID"
            if code is not None or quality_status == "CODE_INVALID"
            else "SOURCE_CODE_MISSING"
        )
        key = (kind, normalize_company_name(name))
        accumulator = unresolved.get(key)
        if accumulator is None:
            accumulator = _UnresolvedAccumulator(source_name=name, reason=reason)
            unresolved[key] = accumulator
        accumulator.observe(name, event_date)

    # 현재 구성종목이 가장 최신 이름을 가진다(core.infostock_stocks와 같은 순위).
    for detail in sorted(bundle.details, key=lambda item: int(item.source_theme_id)):
        for membership in detail.memberships:
            observe(
                membership.stock_code,
                membership.stock_name,
                authority="CURRENT_MEMBERSHIP",
                event_date=None,
                kind="THEME_MEMBERSHIP",
                quality_status=membership.quality_status,
            )
    for detail in sorted(bundle.details, key=lambda item: int(item.source_theme_id)):
        for history in detail.history:
            for reference in history.leaders:
                observe(
                    reference.stock_code,
                    reference.name,
                    authority="HISTORICAL_REFERENCE",
                    event_date=history.event_date,
                    kind="HISTORY_LEADER",
                    quality_status=reference.quality_status,
                )
            for reference in history.member_stocks:
                observe(
                    reference.stock_code,
                    reference.name,
                    authority="HISTORICAL_REFERENCE",
                    event_date=history.event_date,
                    kind="HISTORY_MEMBER",
                    quality_status=reference.quality_status,
                )

    current_name = {
        code: min(by_name.values(), key=_current_name_key).name
        for code, by_name in observations.items()
    }
    normalized_names = {
        code: {normalize_company_name(name) for name in by_name}
        for code, by_name in observations.items()
    }

    # 우선주 → 보통주 연결. 이름 접미사와 종목코드가 둘 다 맞을 때만 합친다.
    merged_into: dict[str, str] = {}
    for code in sorted(observations):
        base_name, has_suffix = split_share_class(current_name[code])
        if not has_suffix or not base_name:
            continue
        base_code = code[:5] + "0"
        if base_code == code or base_code not in observations:
            continue
        if normalize_company_name(base_name) in normalized_names[base_code]:
            merged_into[code] = base_code

    krx_by_code = {} if krx_names is None else krx_names.by_code()
    companies: list[CompanyDraft] = []
    for seed in sorted(observations):
        if seed in merged_into:
            continue
        extra = [code for code in sorted(merged_into) if merged_into[code] == seed]
        companies.append(
            _build_company(
                seed,
                extra,
                observations=observations,
                current_name=current_name,
                corp_codes=corp_codes or {},
                krx_index=krx_names,
                krx_by_code=krx_by_code,
            )
        )

    reviews = tuple(
        UnresolvedReferenceDraft(
            source_kind=kind,
            source_name=accumulator.representative_name,
            normalized_name=normalized,
            reason=accumulator.reason,
            mention_count=accumulator.mention_count,
            first_event_date=accumulator.first_event_date,
            last_event_date=accumulator.last_event_date,
        )
        for (kind, normalized), accumulator in sorted(unresolved.items())
    )
    return CompanyMaster(
        master_version=COMPANY_MASTER_VERSION,
        companies=tuple(companies),
        unresolved=reviews,
    )


def _alias_drafts(
    entries: Iterable[_NameObservation],
    *,
    current: str,
    alias_type_for_current: AliasType,
    krx_windows: tuple[KrxNameWindow, ...] = (),
    krx_index: KrxNameIndex | None = None,
) -> list[CompanyAliasDraft]:
    """KRX 상장 이름을 먼저 깔고 인포스탁 관측을 그 위에 얹는다.

    같은 이름이 양쪽에 있으면 유효기간은 KRX 것을 쓰고 인포스탁에서는 등장
    횟수만 가져온다. KRX가 그 종목의 현재 이름을 알고 있으면 현재 이름은 KRX가
    정한다 — 인포스탁 관측만으로 열린 이름을 만들지 않는다.
    """

    drafts: dict[str, CompanyAliasDraft] = {}
    krx_has_open = False
    # KRX 수집은 2010-01-04부터라 그 종목의 첫 이름은 시작이 잘려 있다. 그 하나만
    # 인포스탁 관측으로 앞으로 늘린다. 뒤 이름을 늘리면 소급 정규화된 이름이
    # 과거까지 유효해져 이 색인을 쓰는 이유가 사라진다.
    earliest = min(
        krx_windows, key=lambda window: (window.first_date, window.name), default=None
    )
    earliest_key = None if earliest is None else normalize_company_name(earliest.name)
    for window in krx_windows:
        is_open = krx_index is not None and krx_index.is_open(window)
        krx_has_open = krx_has_open or is_open
        alias_type: AliasType = alias_type_for_current if is_open else "PAST_NAME"
        drafts[normalize_company_name(window.name)] = CompanyAliasDraft(
            alias=window.name,
            normalized_alias=normalize_company_name(window.name),
            alias_type=alias_type,
            validity_basis="KRX_LISTING",
            source_authority="KRX_LISTING",
            valid_from=window.first_date,
            valid_to=None if is_open else window.last_date,
            mention_count=0,
        )
    for entry in entries:
        # 원천이 이름 없이 코드 표기만 남긴 참조는 이름이 아니다.
        if _CODE_DISPLAY_RE.fullmatch(entry.name):
            continue
        normalized = normalize_company_name(entry.name)
        found = drafts.get(normalized)
        if found is not None:
            widen = (
                normalized == earliest_key
                and entry.first_event_date is not None
                and found.valid_from is not None
                and entry.first_event_date < found.valid_from
            )
            drafts[normalized] = replace(
                found,
                mention_count=found.mention_count + entry.mention_count,
                valid_from=entry.first_event_date if widen else found.valid_from,
            )
            continue
        is_current = entry.name == current and not krx_has_open
        drafts[normalized] = CompanyAliasDraft(
            alias=entry.name,
            normalized_alias=normalized,
            alias_type=alias_type_for_current if is_current else "PAST_NAME",
            validity_basis="OBSERVED_MENTION",
            source_authority=entry.authority,
            valid_from=entry.first_event_date,
            valid_to=None if is_current else entry.last_event_date,
            mention_count=entry.mention_count,
        )
    return [drafts[key] for key in sorted(drafts)]


def _build_company(
    seed: str,
    extra_codes: list[str],
    *,
    observations: dict[str, dict[str, _NameObservation]],
    current_name: dict[str, str],
    corp_codes: Mapping[str, str],
    krx_index: KrxNameIndex | None,
    krx_by_code: Mapping[str, tuple[KrxNameWindow, ...]],
) -> CompanyDraft:
    aliases = _alias_drafts(
        sorted(observations[seed].values(), key=lambda entry: entry.name),
        current=current_name[seed],
        alias_type_for_current="CURRENT_NAME",
        krx_windows=krx_by_code.get(seed, ()),
        krx_index=krx_index,
    )
    # 종목이 언제부터 이 회사의 것이었는지는 원천이 없다. 관측 시작을 유효
    # 시작으로 쓰면 그 전 기간을 "아니었다"로 만들므로 비워 둔다.
    instruments = [
        CompanyInstrumentDraft(
            stock_code=seed,
            share_class=_seed_share_class(seed, current_name[seed], observations),
            link_basis="STOCK_CODE",
            valid_from=None,
            valid_to=None,
        )
    ]
    seen = {(alias.normalized_alias, alias.alias_type) for alias in aliases}
    linked_on: dict[str, date | None] = {}
    for code in extra_codes:
        for alias in _alias_drafts(
            sorted(observations[code].values(), key=lambda entry: entry.name),
            current=current_name[code],
            alias_type_for_current="SHARE_CLASS_NAME",
            krx_windows=krx_by_code.get(code, ()),
            krx_index=krx_index,
        ):
            key = (alias.normalized_alias, alias.alias_type)
            if key in seen:
                continue
            seen.add(key)
            aliases.append(alias)
        linked_on[code] = _earliest(observations[code].values())
        instruments.append(
            CompanyInstrumentDraft(
                stock_code=code,
                share_class="PREFERRED",
                link_basis="SHARE_CLASS_NAME_AND_CODE",
                valid_from=None,
                valid_to=None,
            )
        )

    corp_code = corp_codes.get(seed)
    if corp_code is not None and not CORP_CODE_RE.fullmatch(corp_code):
        corp_code = None
    canonical, name_basis = _canonical_name(
        aliases,
        seed=seed,
        fallback=current_name[seed],
        fallback_basis=observations[seed][current_name[seed]].authority,
    )
    return CompanyDraft(
        seed_stock_code=seed,
        canonical_name=canonical,
        name_basis=name_basis,
        dart_corp_code=corp_code,
        aliases=tuple(aliases),
        instruments=tuple(instruments),
        revisions=tuple(
            _revision_drafts(
                aliases, instruments, canonical=canonical, linked_on=linked_on
            )
        ),
    )


def _canonical_name(
    aliases: list[CompanyAliasDraft],
    *,
    seed: str,
    fallback: str,
    fallback_basis: NameBasis,
) -> tuple[str, NameBasis]:
    """대표 이름은 KRX가 아는 현재 이름 → 마지막 상장 이름 순으로 고른다.

    상장폐지된 종목은 열린 이름이 없으므로 마지막으로 거래된 이름이 대표다.
    어느 원천도 이름을 주지 않으면 종목코드를 두고 이름 미상으로 표시한다.
    """

    for alias in aliases:
        if alias.alias_type == "CURRENT_NAME" and alias.validity_basis == "KRX_LISTING":
            return alias.alias, "KRX_LISTING"
    for alias in aliases:
        if alias.alias_type == "CURRENT_NAME":
            return alias.alias, alias.source_authority
    listed = [
        (alias.valid_from, alias.alias)
        for alias in aliases
        if alias.validity_basis == "KRX_LISTING" and alias.valid_from is not None
    ]
    if listed:
        return max(listed)[1], "KRX_LISTING"
    if _CODE_DISPLAY_RE.fullmatch(fallback):
        return seed, "UNKNOWN"
    return fallback, fallback_basis


def _seed_share_class(
    seed: str, name: str, observations: dict[str, dict[str, _NameObservation]]
) -> ShareClass:
    """짝을 찾은 우선주만 PREFERRED다.

    이름이 우선주형인데 보통주 후보 코드가 있고 이름이 어긋나면 판정하지 않는다.
    "연우"처럼 보통주 코드까지 자기 자신인 경우는 접미사가 아니므로 COMMON이다.
    """

    _, has_suffix = split_share_class(name)
    base_code = seed[:5] + "0"
    if has_suffix and base_code != seed and base_code in observations:
        return "UNKNOWN"
    return "COMMON"


def _earliest(entries: Iterable[_NameObservation]) -> date | None:
    dates = [entry.first_event_date for entry in entries if entry.first_event_date]
    return min(dates) if dates else None


def _revision_drafts(
    aliases: list[CompanyAliasDraft],
    instruments: list[CompanyInstrumentDraft],
    *,
    canonical: str,
    linked_on: dict[str, date | None],
) -> list[CompanyRevisionDraft]:
    """관측으로 확인되는 이력만 만든다. 상장폐지·합병·분할은 원천이 따로 필요하다."""

    dated: list[tuple[date, CompanyAliasDraft]] = [
        (alias.valid_from, alias)
        for alias in aliases
        if alias.alias_type != "SHARE_CLASS_NAME" and alias.valid_from is not None
    ]
    dated.sort(key=lambda item: (item[0], item[1].alias))
    revisions = [
        CompanyRevisionDraft(
            change_type="CREATED",
            effective_on=dated[0][0] if dated else None,
            previous_value=None,
            new_value=dated[0][1].alias if dated else canonical,
        )
    ]
    # 앞 이름의 관측이 끝난 뒤에 다음 이름이 시작할 때만 사명 변경으로 본다.
    for (_, previous), (started_on, current) in zip(dated, dated[1:], strict=False):
        if previous.valid_to is None or previous.valid_to >= started_on:
            continue
        revisions.append(
            CompanyRevisionDraft(
                change_type="NAME_CHANGED",
                effective_on=started_on,
                previous_value=previous.alias,
                new_value=current.alias,
            )
        )
    for instrument in instruments[1:]:
        revisions.append(
            CompanyRevisionDraft(
                change_type="INSTRUMENT_LINKED",
                effective_on=linked_on.get(instrument.stock_code),
                previous_value=None,
                new_value=instrument.stock_code,
            )
        )
    return revisions


def resolve_company(
    master: CompanyMaster, query: str, *, as_of: date | None = None
) -> CompanyResolution:
    """회사 슬롯을 해석한다. 종목코드 → 유효한 alias 순이며 추측하지 않는다."""

    text = query.strip()
    if STOCK_CODE_RE.fullmatch(text):
        for company in master.companies:
            for instrument in company.instruments:
                if instrument.stock_code != text:
                    continue
                return CompanyResolution(
                    status="RESOLVED",
                    matched_by="STOCK_CODE",
                    candidates=(
                        CompanyCandidate(
                            seed_stock_code=company.seed_stock_code,
                            canonical_name=company.canonical_name,
                            matched_alias=text,
                            valid_from=instrument.valid_from,
                            valid_to=instrument.valid_to,
                        ),
                    ),
                )
        return CompanyResolution(status="UNKNOWN", matched_by="NONE", candidates=())

    normalized = normalize_company_name(text)
    if not normalized:
        return CompanyResolution(status="UNKNOWN", matched_by="NONE", candidates=())

    matched: list[tuple[CompanyDraft, CompanyAliasDraft]] = [
        (company, alias)
        for company in master.companies
        for alias in company.aliases
        if alias.normalized_alias == normalized
    ]
    if not matched:
        return CompanyResolution(status="UNKNOWN", matched_by="NONE", candidates=())

    valid = [pair for pair in matched if pair[1].covers(as_of)]
    used = valid or matched
    candidates = tuple(
        CompanyCandidate(
            seed_stock_code=company.seed_stock_code,
            canonical_name=company.canonical_name,
            matched_alias=alias.alias,
            valid_from=alias.valid_from,
            valid_to=alias.valid_to,
        )
        for company, alias in sorted(
            used, key=lambda pair: (pair[0].seed_stock_code, pair[1].alias)
        )
    )
    if not valid:
        return CompanyResolution(
            status="OUT_OF_VALIDITY", matched_by="ALIAS", candidates=candidates
        )
    distinct = {candidate.seed_stock_code for candidate in candidates}
    if len(distinct) > 1:
        return CompanyResolution(
            status="AMBIGUOUS", matched_by="ALIAS", candidates=candidates
        )
    return CompanyResolution(
        status="RESOLVED", matched_by="ALIAS", candidates=candidates
    )
