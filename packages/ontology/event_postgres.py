"""고유 현실 사건과 프로젝트 구조의 PostgreSQL append 적재."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from packages.infostock.hashing import sha256_json, sha256_text

from .event_dedup import CanonicalCatalyst, DeduplicationResult
from .event_structure import SourceMentionDraft, ThemeReactionDraft
from .postgres import DbConnection, DbCursor


class CatalystTransformConflictError(RuntimeError):
    """같은 dataset·변환 버전이 기존 사건과 다른 결과를 만들었다."""


class CatalystSourceConflictError(RuntimeError):
    """같은 history 절·변환 버전의 source mention 결과가 달라졌다."""


class CatalystCompanyMissingError(RuntimeError):
    """사건 역할이 회사 master에 없는 종목코드를 참조했다."""


class CatalystIdentityConflictError(RuntimeError):
    """안정 ID가 기존 project·actor identity와 충돌했다."""


@dataclass(frozen=True, slots=True)
class CatalystLoadCounts:
    total_catalysts: int
    inserted_revisions: int
    existing_revisions: int
    skipped_catalysts: int
    missing_histories: int
    mismatched_histories: int
    source_mentions_inserted: int
    source_mentions_existing: int
    projects_inserted: int
    actors_inserted: int
    company_roles_inserted: int
    participants_inserted: int
    values_inserted: int
    reactions_inserted: int
    relations_inserted: int


@dataclass(frozen=True, slots=True)
class _StoredHistory:
    history_id: int
    content_hash: str
    raw_text: str
    observed_at: datetime


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", "", normalized.strip())


def _reaction_hash(reaction: ThemeReactionDraft) -> str:
    return sha256_json(
        {
            "reactionKey": reaction.reaction_key,
            "sourceThemeId": reaction.source_theme_id,
            "sourceHistoryKey": reaction.source_history_key,
            "occurredOn": (
                reaction.occurred_on.isoformat() if reaction.occurred_on else None
            ),
            "direction": reaction.direction,
            "leaderStockCodes": list(reaction.leader_stock_codes),
            "relatedStockCodes": list(reaction.related_stock_codes),
        }
    )


class PostgresCatalystEventStore:
    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def current_histories(self) -> dict[tuple[str, str], _StoredHistory]:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT theme.source_theme_id, history.source_history_key,"
                " history.history_id, history.content_hash, history.raw_text,"
                " history.observed_from"
                " FROM core.infostock_theme_history history"
                " JOIN core.infostock_themes theme"
                "   ON theme.theme_id = history.theme_id"
                " WHERE history.observed_to IS NULL"
            )
            return {
                (str(row[0]), str(row[1])): _StoredHistory(
                    history_id=int(row[2]),
                    content_hash=str(row[3]),
                    raw_text=str(row[4]),
                    observed_at=row[5],
                )
                for row in db.fetchall()
            }
        finally:
            db.close()

    def company_ids(self) -> dict[str, int]:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT seed_stock_code, company_id"
                " FROM core.company_entities ORDER BY seed_stock_code"
            )
            return {str(row[0]): int(row[1]) for row in db.fetchall()}
        finally:
            db.close()

    @staticmethod
    def _history_for_mention(
        histories: dict[tuple[str, str], _StoredHistory],
        mention: SourceMentionDraft,
    ) -> _StoredHistory | None:
        return histories.get((mention.source_theme_id, mention.source_history_key))

    @staticmethod
    def _source_matches(
        stored: _StoredHistory,
        mention: SourceMentionDraft,
        raw_text: str,
    ) -> bool:
        return (
            stored.content_hash == mention.source_revision_hash
            and 0 <= mention.start < mention.end <= len(stored.raw_text)
            and stored.raw_text[mention.start : mention.end] == raw_text
            and sha256_text(raw_text) == mention.source_text_hash
        )

    def _source_mention_id(
        self,
        db: DbCursor,
        *,
        mention: SourceMentionDraft,
        history: _StoredHistory,
    ) -> tuple[int, bool]:
        db.execute(
            "SELECT mention.source_mention_id, mention.output_hash"
            " FROM ontology.source_mention_history link"
            " JOIN ontology.source_mentions mention"
            "   ON mention.source_mention_id = link.source_mention_id"
            " WHERE link.history_id = %s AND link.clause_order = %s"
            "   AND link.transform_version = %s",
            (history.history_id, mention.clause_order, mention.transform_version),
        )
        found = db.fetchone()
        if found is not None:
            if str(found[1]) != mention.output_hash:
                raise CatalystSourceConflictError(
                    "같은 history 절·변환 버전이 다른 source mention을 "
                    "만들었습니다. 규칙을 고쳤다면 "
                    "EVENT_STRUCTURE_TRANSFORM_VERSION을 올리십시오."
                )
            return int(found[0]), False

        db.execute(
            "INSERT INTO ontology.source_mentions"
            " (source_kind, source_revision_hash, source_text_hash,"
            " start_offset, end_offset, transform_version, output_hash,"
            " review_status, observed_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, 'AI_DRAFT', %s)"
            " RETURNING source_mention_id",
            (
                "INFOSTOCK_THEME_HISTORY",
                mention.source_revision_hash,
                mention.source_text_hash,
                mention.start,
                mention.end,
                mention.transform_version,
                mention.output_hash,
                history.observed_at,
            ),
        )
        inserted = db.fetchone()
        if inserted is None:  # pragma: no cover - RETURNING은 항상 행을 준다.
            raise RuntimeError("history source mention을 만들지 못했습니다.")
        source_mention_id = int(inserted[0])
        db.execute(
            "INSERT INTO ontology.source_mention_history"
            " (source_mention_id, history_id, clause_order, transform_version)"
            " VALUES (%s, %s, %s, %s)",
            (
                source_mention_id,
                history.history_id,
                mention.clause_order,
                mention.transform_version,
            ),
        )
        return source_mention_id, True

    @staticmethod
    def _ensure_actor(db: DbCursor, participant: Any) -> bool:
        actor = participant.actor
        db.execute(
            "SELECT identity_hash, actor_kind, geography_code"
            " FROM ontology.actor_entities WHERE actor_id = %s",
            (actor.actor_key,),
        )
        found = db.fetchone()
        if found is not None:
            if (
                str(found[0]) != actor.identity_hash
                or str(found[1]) != actor.actor_kind.value
                or (None if found[2] is None else str(found[2]))
                != actor.geography_code
            ):
                raise CatalystIdentityConflictError(
                    f"actor_id={actor.actor_key}가 다른 identity로 이미 존재합니다."
                )
            return False
        db.execute(
            "INSERT INTO ontology.actor_entities"
            " (actor_id, actor_kind, canonical_name, normalized_name,"
            " geography_code, company_id, identity_hash)"
            " VALUES (%s, %s, %s, %s, %s, NULL, %s)",
            (
                actor.actor_key,
                actor.actor_kind.value,
                actor.canonical_name,
                _normalize_name(actor.canonical_name),
                actor.geography_code,
                actor.identity_hash,
            ),
        )
        db.execute(
            "INSERT INTO ontology.actor_aliases"
            " (actor_id, alias, normalized_alias, valid_from, valid_to, source_kind)"
            " VALUES (%s, %s, %s, NULL, NULL, 'CONTROLLED')",
            (
                actor.actor_key,
                actor.evidence_text,
                _normalize_name(actor.evidence_text),
            ),
        )
        return True

    @staticmethod
    def _ensure_project(db: DbCursor, project: Any) -> bool:
        db.execute(
            "SELECT project_fingerprint FROM ontology.projects WHERE project_id = %s",
            (project.project_id,),
        )
        found = db.fetchone()
        if found is not None:
            if str(found[0]) != project.project_fingerprint:
                raise CatalystIdentityConflictError(
                    f"project_id={project.project_id}가 다른 fingerprint로 존재합니다."
                )
            return False
        db.execute(
            "INSERT INTO ontology.projects"
            " (project_id, project_fingerprint, canonical_name)"
            " VALUES (%s, %s, %s)",
            (project.project_id, project.project_fingerprint, project.reference),
        )
        return True

    @staticmethod
    def _ensure_catalyst(db: DbCursor, catalyst: CanonicalCatalyst) -> None:
        db.execute(
            "SELECT dedup_key FROM ontology.catalysts WHERE catalyst_id = %s",
            (catalyst.catalyst_id,),
        )
        found = db.fetchone()
        if found is not None:
            if str(found[0]) != catalyst.dedup_key:
                raise CatalystIdentityConflictError(
                    f"catalyst_id={catalyst.catalyst_id}가 다른 dedup key로 존재합니다."
                )
            return
        db.execute(
            "INSERT INTO ontology.catalysts"
            " (catalyst_id, dedup_key, dedup_policy_version) VALUES (%s, %s, %s)",
            (
                catalyst.catalyst_id,
                catalyst.dedup_key,
                catalyst.dedup_policy_version,
            ),
        )

    @staticmethod
    def _existing_revision(
        db: DbCursor, catalyst: CanonicalCatalyst
    ) -> tuple[int, int, tuple[str, ...], str] | None:
        db.execute(
            "SELECT catalyst_revision_id, revision_no, dataset_hash,"
            " vocabulary_version, classification_transform_version,"
            " event_structure_transform_version, company_master_version,"
            " dedup_policy_version, output_hash"
            " FROM ontology.catalyst_revisions WHERE catalyst_id = %s"
            " ORDER BY revision_no DESC LIMIT 1",
            (catalyst.catalyst_id,),
        )
        found = db.fetchone()
        if found is None:
            return None
        versions = tuple(str(found[index]) for index in range(2, 8))
        return int(found[0]), int(found[1]), versions, str(found[8])

    @staticmethod
    def _insert_revision(
        db: DbCursor,
        *,
        catalyst: CanonicalCatalyst,
        revision_no: int,
        primary_source_mention_id: int,
        generated_at: datetime,
    ) -> int:
        primary = catalyst.primary
        stage = primary.stage_evidence
        db.execute(
            "INSERT INTO ontology.catalyst_revisions"
            " (catalyst_id, revision_no, primary_source_mention_id,"
            " occurred_on, known_on, vocabulary_version, primary_type_id,"
            " type_ids, event_stage, stage_keyword, stage_evidence_start,"
            " stage_evidence_end, certainty, novelty_type, action, object_text,"
            " project_id, officiality, continuation,"
            " classification_transform_version,"
            " event_structure_transform_version, company_master_version,"
            " dedup_policy_version, dataset_hash, content_hash, output_hash,"
            " created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING catalyst_revision_id",
            (
                catalyst.catalyst_id,
                revision_no,
                primary_source_mention_id,
                primary.occurred_on,
                primary.known_on,
                primary.ontology_version,
                primary.primary_catalyst_type,
                list(primary.catalyst_types),
                primary.event_stage.value,
                stage.keyword,
                stage.start,
                stage.end,
                primary.certainty,
                primary.novelty_type.value,
                primary.action,
                primary.object_text,
                primary.project_id,
                primary.officiality.value,
                primary.continuation,
                primary.classification_transform_version,
                primary.transform_version,
                primary.company_master_version,
                catalyst.dedup_policy_version,
                primary.dataset_hash,
                primary.content_hash,
                catalyst.output_hash,
                generated_at,
            ),
        )
        inserted = db.fetchone()
        if inserted is None:  # pragma: no cover
            raise RuntimeError("catalyst revision을 만들지 못했습니다.")
        return int(inserted[0])

    @staticmethod
    def _insert_revision_facts(
        db: DbCursor,
        *,
        catalyst: CanonicalCatalyst,
        catalyst_revision_id: int,
        mention_ids: dict[str, int],
        company_ids: dict[str, int],
    ) -> tuple[int, int, int]:
        mention_order = {
            mention.output_hash: order
            for order, mention in enumerate(catalyst.source_mentions)
        }
        for mention in catalyst.source_mentions:
            db.execute(
                "INSERT INTO ontology.catalyst_source_mentions"
                " (catalyst_revision_id, source_mention_id, source_order)"
                " VALUES (%s, %s, %s)",
                (
                    catalyst_revision_id,
                    mention_ids[mention.output_hash],
                    mention_order[mention.output_hash],
                ),
            )

        roles_inserted = 0
        participants_inserted = 0
        values_inserted = 0
        geographies: set[str] = set()
        for draft in catalyst.drafts:
            source_mention_id = mention_ids[draft.source_mention.output_hash]
            for span in draft.evidence:
                db.execute(
                    "INSERT INTO ontology.catalyst_revision_spans"
                    " (catalyst_revision_id, source_mention_id, source_order,"
                    " field, value, keyword, start_offset, end_offset)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        catalyst_revision_id,
                        source_mention_id,
                        span.source_order,
                        span.field,
                        span.value,
                        span.keyword,
                        span.start,
                        span.end,
                    ),
                )
            for role in draft.company_roles:
                db.execute(
                    "INSERT INTO ontology.catalyst_company_roles"
                    " (catalyst_revision_id, source_mention_id, company_id, role,"
                    " impact, evidence_start, evidence_end)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        catalyst_revision_id,
                        source_mention_id,
                        company_ids[role.seed_stock_code],
                        role.role,
                        role.impact.value,
                        role.evidence_start,
                        role.evidence_end,
                    ),
                )
                roles_inserted += 1
            for participant in draft.participants:
                db.execute(
                    "INSERT INTO ontology.catalyst_participants"
                    " (catalyst_revision_id, source_mention_id, actor_id,"
                    " participant_role, evidence_start, evidence_end)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        catalyst_revision_id,
                        source_mention_id,
                        participant.actor.actor_key,
                        participant.participant_role,
                        participant.actor.start,
                        participant.actor.end,
                    ),
                )
                participants_inserted += 1
            geographies.update(draft.geography_codes)
            for value in draft.values:
                db.execute(
                    "INSERT INTO ontology.catalyst_values"
                    " (catalyst_revision_id, source_mention_id, fact_type,"
                    " reported_value, normalized_value, unit, currency,"
                    " value_basis, eligible_for_sum, effective_on,"
                    " evidence_start, evidence_end)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        catalyst_revision_id,
                        source_mention_id,
                        value.fact_type.value,
                        value.reported_value,
                        value.normalized_value,
                        value.unit,
                        value.currency,
                        value.value_basis.value,
                        value.eligible_for_sum,
                        draft.occurred_on,
                        value.evidence_start,
                        value.evidence_end,
                    ),
                )
                values_inserted += 1
        for geography_code in sorted(geographies):
            db.execute(
                "INSERT INTO ontology.catalyst_geographies"
                " (catalyst_revision_id, geography_code) VALUES (%s, %s)",
                (catalyst_revision_id, geography_code),
            )
        return roles_inserted, participants_inserted, values_inserted

    @staticmethod
    def _ensure_reaction(
        db: DbCursor,
        *,
        reaction: ThemeReactionDraft,
        history_id: int,
        transform_version: str,
        company_ids: dict[str, int],
        generated_at: datetime,
    ) -> tuple[int, bool]:
        output_hash = _reaction_hash(reaction)
        db.execute(
            "SELECT reaction_id FROM ontology.theme_reactions WHERE reaction_id = %s",
            (reaction.reaction_key,),
        )
        if db.fetchone() is None:
            db.execute(
                "INSERT INTO ontology.theme_reactions (reaction_id, created_at)"
                " VALUES (%s, %s)",
                (reaction.reaction_key, generated_at),
            )
        db.execute(
            "SELECT reaction_revision_id, output_hash"
            " FROM ontology.theme_reaction_revisions"
            " WHERE reaction_id = %s AND history_id = %s"
            "   AND transform_version = %s",
            (reaction.reaction_key, history_id, transform_version),
        )
        found = db.fetchone()
        if found is not None:
            if str(found[1]) != output_hash:
                raise CatalystTransformConflictError(
                    f"reaction_id={reaction.reaction_key}의 같은 원천·변환 버전이 "
                    "다른 결과로 존재합니다."
                )
            return int(found[0]), False
        db.execute(
            "INSERT INTO ontology.theme_reaction_revisions"
            " (reaction_id, revision_no, history_id, occurred_on, direction,"
            " transform_version, output_hash, created_at)"
            " SELECT %s, COALESCE(MAX(revision_no), 0) + 1, %s, %s, %s, %s, %s, %s"
            " FROM ontology.theme_reaction_revisions WHERE reaction_id = %s"
            " RETURNING reaction_revision_id",
            (
                reaction.reaction_key,
                history_id,
                reaction.occurred_on,
                reaction.direction,
                transform_version,
                output_hash,
                generated_at,
                reaction.reaction_key,
            ),
        )
        inserted = db.fetchone()
        if inserted is None:  # pragma: no cover
            raise RuntimeError("theme reaction revision을 만들지 못했습니다.")
        reaction_revision_id = int(inserted[0])
        for stock_code in reaction.leader_stock_codes:
            db.execute(
                "INSERT INTO ontology.theme_reaction_company_roles"
                " (reaction_revision_id, company_id, role)"
                " VALUES (%s, %s, 'LEADER')",
                (reaction_revision_id, company_ids[stock_code]),
            )
        for stock_code in reaction.related_stock_codes:
            db.execute(
                "INSERT INTO ontology.theme_reaction_company_roles"
                " (reaction_revision_id, company_id, role)"
                " VALUES (%s, %s, 'RELATED')",
                (reaction_revision_id, company_ids[stock_code]),
            )
        return reaction_revision_id, True

    def load(
        self,
        result: DeduplicationResult,
        *,
        generated_at: datetime,
    ) -> CatalystLoadCounts:
        histories = self.current_histories()
        company_ids = self.company_ids()
        referenced_codes = {
            role.seed_stock_code
            for catalyst in result.catalysts
            for draft in catalyst.drafts
            for role in draft.company_roles
        } | {
            stock_code
            for catalyst in result.catalysts
            for reaction in catalyst.theme_reactions
            for stock_code in (
                *reaction.leader_stock_codes,
                *reaction.related_stock_codes,
            )
        }
        missing_companies = sorted(referenced_codes - company_ids.keys())
        if missing_companies:
            raise CatalystCompanyMissingError(
                "회사 master에 없는 종목코드입니다: " + ", ".join(missing_companies)
            )

        eligible: list[CanonicalCatalyst] = []
        missing_histories = 0
        mismatched_histories = 0
        for catalyst in result.catalysts:
            missing = False
            mismatched = False
            for draft in catalyst.drafts:
                stored = self._history_for_mention(histories, draft.source_mention)
                if stored is None:
                    missing = True
                    continue
                if not self._source_matches(
                    stored, draft.source_mention, draft.raw_text
                ):
                    mismatched = True
            if missing:
                missing_histories += 1
            if mismatched:
                mismatched_histories += 1
            if not missing and not mismatched:
                eligible.append(catalyst)

        eligible_ids = {item.catalyst_id for item in eligible}
        mention_ids: dict[str, int] = {}
        source_mentions_inserted = 0
        source_mentions_existing = 0
        projects_inserted = 0
        actors_inserted = 0
        inserted_revisions = 0
        existing_revisions = 0
        company_roles_inserted = 0
        participants_inserted = 0
        values_inserted = 0
        reactions_inserted = 0
        relations_inserted = 0
        db = self._connection.cursor()
        try:
            for catalyst in eligible:
                for draft in catalyst.drafts:
                    mention = draft.source_mention
                    if mention.output_hash in mention_ids:
                        continue
                    stored = self._history_for_mention(histories, mention)
                    if stored is None:  # preflight invariant
                        raise RuntimeError("검증된 source history가 사라졌습니다.")
                    mention_id, inserted = self._source_mention_id(
                        db, mention=mention, history=stored
                    )
                    mention_ids[mention.output_hash] = mention_id
                    source_mentions_inserted += int(inserted)
                    source_mentions_existing += int(not inserted)

            for catalyst in eligible:
                for draft in catalyst.drafts:
                    for participant in draft.participants:
                        actors_inserted += int(self._ensure_actor(db, participant))

            projects_by_id = {
                project.project_id: project
                for project in result.projects
                if any(item in eligible_ids for item in project.catalyst_ids)
            }
            for project in projects_by_id.values():
                projects_inserted += int(self._ensure_project(db, project))
            for catalyst in eligible:
                for draft in catalyst.drafts:
                    if draft.project_id is None or draft.project_reference is None:
                        continue
                    db.execute(
                        "INSERT INTO ontology.project_aliases"
                        " (project_id, alias, normalized_alias, source_mention_id)"
                        " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (
                            draft.project_id,
                            draft.project_reference,
                            _normalize_name(draft.project_reference),
                            mention_ids[draft.source_mention.output_hash],
                        ),
                    )

            for catalyst in eligible:
                self._ensure_catalyst(db, catalyst)
                existing = self._existing_revision(db, catalyst)
                if existing is not None and existing[3] == catalyst.output_hash:
                    existing_revisions += 1
                    continue
                primary = catalyst.primary
                current_versions = (
                    primary.dataset_hash,
                    primary.ontology_version,
                    primary.classification_transform_version,
                    primary.transform_version,
                    primary.company_master_version,
                    catalyst.dedup_policy_version,
                )
                if existing is not None and existing[2] == current_versions:
                    raise CatalystTransformConflictError(
                        f"catalyst_id={catalyst.catalyst_id}의 같은 dataset·변환 "
                        "버전이 다른 결과를 만들었습니다. 해당 버전을 올리십시오."
                    )
                revision_no = 1 if existing is None else existing[1] + 1
                revision_id = self._insert_revision(
                    db,
                    catalyst=catalyst,
                    revision_no=revision_no,
                    primary_source_mention_id=mention_ids[
                        primary.source_mention.output_hash
                    ],
                    generated_at=generated_at,
                )
                roles, participants, values = self._insert_revision_facts(
                    db,
                    catalyst=catalyst,
                    catalyst_revision_id=revision_id,
                    mention_ids=mention_ids,
                    company_ids=company_ids,
                )
                company_roles_inserted += roles
                participants_inserted += participants
                values_inserted += values
                for reaction in catalyst.theme_reactions:
                    history = histories[
                        (reaction.source_theme_id, reaction.source_history_key)
                    ]
                    reaction_revision_id, reaction_inserted = self._ensure_reaction(
                        db,
                        reaction=reaction,
                        history_id=history.history_id,
                        transform_version=primary.transform_version,
                        company_ids=company_ids,
                        generated_at=generated_at,
                    )
                    reactions_inserted += int(reaction_inserted)
                    db.execute(
                        "INSERT INTO ontology.catalyst_theme_reactions"
                        " (catalyst_revision_id, reaction_revision_id)"
                        " VALUES (%s, %s)",
                        (revision_id, reaction_revision_id),
                    )
                inserted_revisions += 1

            for relation in result.relations:
                if (
                    relation.from_catalyst_id not in eligible_ids
                    or relation.to_catalyst_id not in eligible_ids
                ):
                    continue
                db.execute(
                    "SELECT from_catalyst_id, to_catalyst_id, relation_type,"
                    " dedup_policy_version FROM ontology.catalyst_relations"
                    " WHERE relation_id = %s",
                    (relation.relation_key,),
                )
                found = db.fetchone()
                expected = (
                    relation.from_catalyst_id,
                    relation.to_catalyst_id,
                    relation.relation_type.value,
                    relation.dedup_policy_version,
                )
                if found is not None:
                    if tuple(str(item) for item in found) != expected:
                        raise CatalystIdentityConflictError(
                            f"relation_id={relation.relation_key}가 충돌합니다."
                        )
                    continue
                db.execute(
                    "INSERT INTO ontology.catalyst_relations"
                    " (relation_id, from_catalyst_id, to_catalyst_id,"
                    " relation_type, reason, dedup_policy_version, review_status,"
                    " created_at) VALUES (%s, %s, %s, %s, %s, %s, 'AI_DRAFT', %s)",
                    (
                        relation.relation_key,
                        relation.from_catalyst_id,
                        relation.to_catalyst_id,
                        relation.relation_type.value,
                        relation.reason,
                        relation.dedup_policy_version,
                        generated_at,
                    ),
                )
                relations_inserted += 1

            self._connection.commit()
            return CatalystLoadCounts(
                total_catalysts=len(result.catalysts),
                inserted_revisions=inserted_revisions,
                existing_revisions=existing_revisions,
                skipped_catalysts=len(result.catalysts) - len(eligible),
                missing_histories=missing_histories,
                mismatched_histories=mismatched_histories,
                source_mentions_inserted=source_mentions_inserted,
                source_mentions_existing=source_mentions_existing,
                projects_inserted=projects_inserted,
                actors_inserted=actors_inserted,
                company_roles_inserted=company_roles_inserted,
                participants_inserted=participants_inserted,
                values_inserted=values_inserted,
                reactions_inserted=reactions_inserted,
                relations_inserted=relations_inserted,
            )
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()
