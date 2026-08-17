"""Daily source mention의 PostgreSQL 적재 (E-22 단계 1)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from packages.infostock.hashing import sha256_text

from .daily_mentions import DailyMentionSourceKind, DailySourceMention
from .postgres import DbConnection


class DailyMentionTransformConflictError(RuntimeError):
    """같은 원천·변환 버전이 다른 결과를 만들었을 때 발생한다."""


@dataclass(frozen=True, slots=True)
class DailyMentionLoadCounts:
    total: int
    inserted: int
    existing: int
    missing_relations: int
    mismatched_relations: int


@dataclass(frozen=True, slots=True)
class _StoredDailyRelation:
    daily_relation_id: int
    source_revision_hash: str
    published_date: date | None
    relation_type: str
    raw_text: str


class PostgresDailyMentionStore:
    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def current_relations(
        self,
    ) -> dict[tuple[str, int], _StoredDailyRelation]:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT post.source_post_key, relation.source_order,"
                " relation.daily_relation_id, revision.normalized_hash,"
                " revision.published_date, relation.relation_type,"
                " relation.raw_text"
                " FROM core.infostock_daily_posts post"
                " JOIN core.infostock_daily_post_revisions revision"
                "   ON revision.daily_post_id = post.daily_post_id"
                "  AND revision.observed_to IS NULL"
                " JOIN core.infostock_daily_relations relation"
                "   ON relation.daily_post_revision_id ="
                "      revision.daily_post_revision_id"
                " ORDER BY post.source_post_key, relation.source_order"
            )
            return {
                (str(row[0]), int(row[1])): _StoredDailyRelation(
                    daily_relation_id=int(row[2]),
                    source_revision_hash=str(row[3]),
                    published_date=row[4],
                    relation_type=str(row[5]),
                    raw_text=str(row[6]),
                )
                for row in db.fetchall()
            }
        finally:
            db.close()

    @staticmethod
    def _kind_matches_relation(mention: DailySourceMention, relation_type: str) -> bool:
        if mention.source_kind is DailyMentionSourceKind.DESCRIPTION:
            return relation_type in {"DESCRIPTION", "SECTION_DETAIL"}
        return relation_type == "THEME_STOCK"

    def load(
        self, mentions: Iterable[DailySourceMention]
    ) -> DailyMentionLoadCounts:
        materialized = tuple(mentions)
        stored_relations = self.current_relations()
        inserted = 0
        existing = 0
        missing_relations = 0
        mismatched_relations = 0
        db = self._connection.cursor()
        try:
            for mention in materialized:
                relation = stored_relations.get(
                    (mention.source_post_key, mention.source_relation_order)
                )
                if relation is None:
                    missing_relations += 1
                    continue
                if (
                    relation.source_revision_hash != mention.source_revision_hash
                    or sha256_text(relation.raw_text) != mention.source_text_hash
                    or relation.published_date != mention.published_date
                    or not self._kind_matches_relation(mention, relation.relation_type)
                ):
                    mismatched_relations += 1
                    continue

                db.execute(
                    "SELECT mention.source_mention_id, mention.output_hash"
                    " FROM ontology.source_mention_daily link"
                    " JOIN ontology.source_mentions mention"
                    "   ON mention.source_mention_id = link.source_mention_id"
                    " WHERE link.daily_relation_id = %s"
                    "   AND link.mention_scope = %s"
                    "   AND link.transform_version = %s",
                    (
                        relation.daily_relation_id,
                        mention.mention_scope.value,
                        mention.transform_version,
                    ),
                )
                found = db.fetchone()
                if found is not None:
                    if str(found[1]) != mention.output_hash:
                        raise DailyMentionTransformConflictError(
                            "같은 Daily relation·변환 버전이 다른 결과를 "
                            "만들었습니다. 규칙을 고쳤다면 "
                            "DAILY_MENTION_TRANSFORM_VERSION을 올리십시오."
                        )
                    existing += 1
                    continue

                db.execute(
                    "INSERT INTO ontology.source_mentions"
                    " (source_kind, source_revision_hash, source_text_hash,"
                    " start_offset, end_offset, transform_version, output_hash,"
                    " review_status, observed_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " RETURNING source_mention_id",
                    (
                        mention.source_kind.value,
                        mention.source_revision_hash,
                        mention.source_text_hash,
                        mention.start,
                        mention.end,
                        mention.transform_version,
                        mention.output_hash,
                        mention.review_status,
                        mention.observed_at,
                    ),
                )
                created = db.fetchone()
                if created is None:  # pragma: no cover - RETURNING은 행을 준다.
                    raise RuntimeError("Daily source mention을 만들지 못했습니다.")
                source_mention_id = int(created[0])
                db.execute(
                    "INSERT INTO ontology.source_mention_daily"
                    " (source_mention_id, daily_relation_id, mention_scope,"
                    " transform_version, trading_date, published_date,"
                    " serving_status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        source_mention_id,
                        relation.daily_relation_id,
                        mention.mention_scope.value,
                        mention.transform_version,
                        mention.trading_date,
                        mention.published_date,
                        mention.serving_status.value,
                    ),
                )
                inserted += 1
            self._connection.commit()
            return DailyMentionLoadCounts(
                total=len(materialized),
                inserted=inserted,
                existing=existing,
                missing_relations=missing_relations,
                mismatched_relations=mismatched_relations,
            )
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()
