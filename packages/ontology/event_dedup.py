"""현실 사건 중복과 프로젝트 진행 단계를 결정론적으로 분리한다."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from itertools import combinations
from typing import Any

from packages.infostock.hashing import sha256_json

from .event_structure import CatalystDraft, SourceMentionDraft, ThemeReactionDraft
from .projects import EventStage, event_stage_rank

DEDUP_POLICY_VERSION = "event-dedup/1.0.2"


class CatalystRelationType(StrEnum):
    ADVANCES = "ADVANCES"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"


@dataclass(frozen=True, slots=True)
class AggregateCounts:
    source_record_count: int
    theme_reaction_count: int
    unique_catalyst_count: int
    project_count: int

    def as_dict(self) -> dict[str, int]:
        return {
            "sourceRecordCount": self.source_record_count,
            "themeReactionCount": self.theme_reaction_count,
            "uniqueCatalystCount": self.unique_catalyst_count,
            "projectCount": self.project_count,
        }


def _draft_sort_key(draft: CatalystDraft) -> tuple[bool, date, str, str, int, str]:
    mention = draft.source_mention
    return (
        draft.occurred_on is None,
        draft.occurred_on or date.max,
        mention.source_theme_id,
        mention.source_history_key,
        mention.clause_order,
        mention.output_hash,
    )


def _unique_mentions(drafts: tuple[CatalystDraft, ...]) -> tuple[SourceMentionDraft, ...]:
    by_hash = {draft.source_mention.output_hash: draft.source_mention for draft in drafts}
    return tuple(
        sorted(
            by_hash.values(),
            key=lambda item: (
                item.source_theme_id,
                item.source_history_key,
                item.clause_order,
                item.output_hash,
            ),
        )
    )


def _unique_reactions(drafts: tuple[CatalystDraft, ...]) -> tuple[ThemeReactionDraft, ...]:
    by_key = {draft.reaction.reaction_key: draft.reaction for draft in drafts}
    return tuple(
        sorted(
            by_key.values(),
            key=lambda item: (
                item.occurred_on is None,
                item.occurred_on or date.max,
                item.source_theme_id,
                item.source_history_key,
                item.reaction_key,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class CanonicalCatalyst:
    catalyst_id: str
    dedup_key: str
    drafts: tuple[CatalystDraft, ...]
    source_mentions: tuple[SourceMentionDraft, ...]
    theme_reactions: tuple[ThemeReactionDraft, ...]
    dedup_policy_version: str = DEDUP_POLICY_VERSION

    def __post_init__(self) -> None:
        if not self.drafts:
            raise ValueError("고유 사건에는 원천 초안이 하나 이상 필요합니다.")
        if self.catalyst_id != f"catalyst_{self.dedup_key[:24]}":
            raise ValueError("catalyst_id와 dedup key가 맞지 않습니다.")
        if any(draft.dedup_key != self.dedup_key for draft in self.drafts):
            raise ValueError("서로 다른 자동 병합 key의 사건을 합칠 수 없습니다.")
        if tuple(sorted(self.drafts, key=_draft_sort_key)) != self.drafts:
            raise ValueError("사건 초안 순서는 결정론적으로 정렬돼야 합니다.")
        version_sets = {
            (
                draft.dataset_hash,
                draft.ontology_version,
                draft.classification_transform_version,
                draft.company_master_version,
                draft.transform_version,
            )
            for draft in self.drafts
        }
        if len(version_sets) != 1:
            raise ValueError("서로 다른 dataset·변환 버전의 초안을 한 번에 합칠 수 없습니다.")

    @property
    def primary(self) -> CatalystDraft:
        return self.drafts[0]

    @property
    def output_hash(self) -> str:
        return sha256_json(self.as_dict(include_raw_text=False))

    def as_dict(self, *, include_raw_text: bool = False) -> dict[str, Any]:
        return {
            "catalystId": self.catalyst_id,
            "dedupKey": self.dedup_key,
            "dedupPolicyVersion": self.dedup_policy_version,
            "primary": self.primary.as_dict(include_raw_text=include_raw_text),
            "draftOutputHashes": [draft.output_hash for draft in self.drafts],
            "sourceMentions": [
                {
                    "sourceThemeId": mention.source_theme_id,
                    "sourceHistoryKey": mention.source_history_key,
                    "clauseOrder": mention.clause_order,
                    "outputHash": mention.output_hash,
                }
                for mention in self.source_mentions
            ],
            "themeReactions": [reaction.reaction_key for reaction in self.theme_reactions],
        }


@dataclass(frozen=True, slots=True)
class ProjectDraft:
    project_id: str
    project_fingerprint: str
    reference: str
    catalyst_ids: tuple[str, ...]
    first_occurred_on: date | None
    last_occurred_on: date | None
    latest_stage: EventStage

    @property
    def output_hash(self) -> str:
        return sha256_json(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "projectId": self.project_id,
            "projectFingerprint": self.project_fingerprint,
            "reference": self.reference,
            "catalystIds": list(self.catalyst_ids),
            "firstOccurredOn": (
                self.first_occurred_on.isoformat() if self.first_occurred_on else None
            ),
            "lastOccurredOn": (
                self.last_occurred_on.isoformat() if self.last_occurred_on else None
            ),
            "latestStage": self.latest_stage.value,
        }


@dataclass(frozen=True, slots=True)
class CatalystRelationDraft:
    from_catalyst_id: str
    to_catalyst_id: str
    relation_type: CatalystRelationType
    reason: str
    dedup_policy_version: str = DEDUP_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.from_catalyst_id == self.to_catalyst_id:
            raise ValueError("사건은 자기 자신과 관계를 맺을 수 없습니다.")

    @property
    def relation_key(self) -> str:
        digest = sha256_json(
            {
                "from": self.from_catalyst_id,
                "to": self.to_catalyst_id,
                "type": self.relation_type.value,
                "reason": self.reason,
                "policy": self.dedup_policy_version,
            }
        )
        return f"relation_{digest[:24]}"

    def as_dict(self) -> dict[str, str]:
        return {
            "relationKey": self.relation_key,
            "fromCatalystId": self.from_catalyst_id,
            "toCatalystId": self.to_catalyst_id,
            "relationType": self.relation_type.value,
            "reason": self.reason,
            "dedupPolicyVersion": self.dedup_policy_version,
        }


@dataclass(frozen=True, slots=True)
class DeduplicationResult:
    catalysts: tuple[CanonicalCatalyst, ...]
    projects: tuple[ProjectDraft, ...]
    relations: tuple[CatalystRelationDraft, ...]
    counts: AggregateCounts
    dataset_hashes: tuple[str, ...]
    dedup_policy_version: str = DEDUP_POLICY_VERSION

    @property
    def artifact_hash(self) -> str:
        return sha256_json(
            {
                "dedupPolicyVersion": self.dedup_policy_version,
                "datasetHashes": list(self.dataset_hashes),
                "catalystOutputHashes": [item.output_hash for item in self.catalysts],
                "projectOutputHashes": [item.output_hash for item in self.projects],
                "relationKeys": [item.relation_key for item in self.relations],
                "counts": self.counts.as_dict(),
            }
        )

    def report(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "dedupPolicyVersion": self.dedup_policy_version,
            "datasetHashes": list(self.dataset_hashes),
            **self.counts.as_dict(),
            "mergedSourceMentionCount": sum(
                max(0, len(item.source_mentions) - 1) for item in self.catalysts
            ),
            "autoMergedCatalystCount": sum(
                len(item.source_mentions) > 1 for item in self.catalysts
            ),
            "maxSourceMentionsPerCatalyst": max(
                (len(item.source_mentions) for item in self.catalysts),
                default=0,
            ),
            "advanceRelationCount": sum(
                item.relation_type is CatalystRelationType.ADVANCES
                for item in self.relations
            ),
            "possibleDuplicateCount": sum(
                item.relation_type is CatalystRelationType.POSSIBLE_DUPLICATE
                for item in self.relations
            ),
            "artifactHash": self.artifact_hash,
            "reviewStatus": "AI_DRAFT",
        }


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z가-힣]+", "", normalized)


def _company_codes(catalyst: CanonicalCatalyst) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                role.seed_stock_code
                for draft in catalyst.drafts
                for role in draft.company_roles
            }
        )
    )


def _value_signature(catalyst: CanonicalCatalyst) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            {
                (value.fact_type.value, str(value.normalized_value), value.unit)
                for draft in catalyst.drafts
                for value in draft.values
            }
        )
    )


def _possible_duplicate_key(catalyst: CanonicalCatalyst) -> tuple[Any, ...] | None:
    primary = catalyst.primary
    companies = _company_codes(catalyst)
    if primary.occurred_on is None or (primary.project_id is None and not companies):
        return None
    return (
        primary.occurred_on,
        primary.event_stage,
        primary.primary_catalyst_type,
        primary.project_id,
        companies,
    )


def _possible_duplicate(left: CanonicalCatalyst, right: CanonicalCatalyst) -> bool:
    left_primary = left.primary
    right_primary = right.primary
    if left_primary.project_id is not None:
        return True
    if _value_signature(left) != _value_signature(right):
        return False
    left_text = _normalize_text(left_primary.raw_text)
    right_text = _normalize_text(right_primary.raw_text)
    if not left_text or not right_text:
        return False
    shorter, longer = sorted((left_text, right_text), key=len)
    return shorter in longer and len(shorter) / len(longer) >= 0.75


def _project_sort_key(catalyst: CanonicalCatalyst) -> tuple[bool, date, int, str]:
    primary = catalyst.primary
    return (
        primary.occurred_on is None,
        primary.occurred_on or date.max,
        event_stage_rank(primary.event_stage),
        catalyst.catalyst_id,
    )


def _build_projects(
    catalysts: tuple[CanonicalCatalyst, ...],
) -> tuple[tuple[ProjectDraft, ...], tuple[CatalystRelationDraft, ...]]:
    grouped: dict[str, list[CanonicalCatalyst]] = defaultdict(list)
    for catalyst in catalysts:
        if catalyst.primary.project_id is not None:
            grouped[catalyst.primary.project_id].append(catalyst)

    projects: list[ProjectDraft] = []
    advances: list[CatalystRelationDraft] = []
    for project_id, members in sorted(grouped.items()):
        ordered = tuple(sorted(members, key=_project_sort_key))
        primary = ordered[0].primary
        fingerprint = primary.project_fingerprint
        reference = primary.project_reference
        if fingerprint is None or reference is None:  # project_id invariant
            raise ValueError("프로젝트 연결 사건에 fingerprint·reference가 없습니다.")
        occurred_dates = [
            item.primary.occurred_on
            for item in ordered
            if item.primary.occurred_on is not None
        ]
        latest = max(
            ordered,
            key=lambda item: (
                item.primary.occurred_on or date.min,
                event_stage_rank(item.primary.event_stage),
                item.catalyst_id,
            ),
        )
        projects.append(
            ProjectDraft(
                project_id=project_id,
                project_fingerprint=fingerprint,
                reference=reference,
                catalyst_ids=tuple(item.catalyst_id for item in ordered),
                first_occurred_on=min(occurred_dates) if occurred_dates else None,
                last_occurred_on=max(occurred_dates) if occurred_dates else None,
                latest_stage=latest.primary.event_stage,
            )
        )
        known = [item for item in ordered if item.primary.occurred_on is not None]
        for before, after in zip(known, known[1:], strict=False):
            if event_stage_rank(after.primary.event_stage) <= event_stage_rank(
                before.primary.event_stage
            ):
                continue
            advances.append(
                CatalystRelationDraft(
                    from_catalyst_id=before.catalyst_id,
                    to_catalyst_id=after.catalyst_id,
                    relation_type=CatalystRelationType.ADVANCES,
                    reason="같은 명시 프로젝트에서 날짜·단계가 앞으로 진행됨",
                )
            )
    return tuple(projects), tuple(advances)


def deduplicate_catalysts(
    drafts: tuple[CatalystDraft, ...],
) -> DeduplicationResult:
    """확실한 복제만 자동 병합하고 애매한 쌍은 관계로 남긴다."""

    grouped: dict[str, list[CatalystDraft]] = defaultdict(list)
    for draft in sorted(drafts, key=_draft_sort_key):
        grouped[draft.dedup_key].append(draft)

    catalysts = tuple(
        CanonicalCatalyst(
            catalyst_id=f"catalyst_{dedup_key[:24]}",
            dedup_key=dedup_key,
            drafts=tuple(group),
            source_mentions=_unique_mentions(tuple(group)),
            theme_reactions=_unique_reactions(tuple(group)),
        )
        for dedup_key, group in sorted(grouped.items())
    )
    projects, advances = _build_projects(catalysts)

    candidates: dict[tuple[Any, ...], list[CanonicalCatalyst]] = defaultdict(list)
    for catalyst in catalysts:
        key = _possible_duplicate_key(catalyst)
        if key is not None:
            candidates[key].append(catalyst)
    possible: list[CatalystRelationDraft] = []
    for members in candidates.values():
        for left, right in combinations(sorted(members, key=lambda item: item.catalyst_id), 2):
            if not _possible_duplicate(left, right):
                continue
            possible.append(
                CatalystRelationDraft(
                    from_catalyst_id=left.catalyst_id,
                    to_catalyst_id=right.catalyst_id,
                    relation_type=CatalystRelationType.POSSIBLE_DUPLICATE,
                    reason="같은 날짜·단계·회사 또는 프로젝트지만 자동 병합 key가 다름",
                )
            )

    relations = tuple(
        sorted(
            (*advances, *possible),
            key=lambda item: (
                item.relation_type.value,
                item.from_catalyst_id,
                item.to_catalyst_id,
            ),
        )
    )
    source_records = {
        (draft.source_mention.source_theme_id, draft.source_mention.source_history_key)
        for draft in drafts
    }
    reactions = {draft.reaction.reaction_key for draft in drafts}
    return DeduplicationResult(
        catalysts=catalysts,
        projects=projects,
        relations=relations,
        counts=AggregateCounts(
            source_record_count=len(source_records),
            theme_reaction_count=len(reactions),
            unique_catalyst_count=len(catalysts),
            project_count=len(projects),
        ),
        dataset_hashes=tuple(sorted({draft.dataset_hash for draft in drafts})),
    )
