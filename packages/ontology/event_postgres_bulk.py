"""고유 사건을 COPY staging과 집합 연산으로 PostgreSQL에 적재한다.

행별 loader의 append-only·idempotency 계약은 유지한다. 차이는 모든 입력을
임시 테이블에 한 번 전송하고, 충돌 검증과 INSERT를 DB 안에서 집합으로 수행해
대량 입력의 왕복 횟수를 입력 행 수와 무관하게 제한한다는 점이다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from .event_dedup import CanonicalCatalyst, DeduplicationResult
from .event_postgres import (
    CatalystCompanyMissingError,
    CatalystIdentityConflictError,
    CatalystLoadCounts,
    CatalystSourceConflictError,
    CatalystTransformConflictError,
    PostgresCatalystEventStore,
    _normalize_name,
    _reaction_hash,
)
from .postgres import DbCursor


def _copy_rows(
    db: DbCursor,
    query: str,
    rows: Iterable[tuple[object, ...]],
) -> None:
    copy_method = getattr(db, "copy", None)
    if copy_method is None:
        raise RuntimeError(
            "bulk 적재에는 psycopg COPY를 지원하는 cursor가 필요합니다."
        )
    with copy_method(query) as stream:
        for row in rows:
            stream.write_row(row)


def _create_stage_tables(db: DbCursor) -> None:
    statements = (
        "CREATE TEMP TABLE catalyst_source_stage ("
        " stage_mention_key text PRIMARY KEY, history_id bigint NOT NULL,"
        " clause_order integer NOT NULL, source_revision_hash text NOT NULL,"
        " source_text_hash text NOT NULL, start_offset integer NOT NULL,"
        " end_offset integer NOT NULL, transform_version text NOT NULL,"
        " output_hash text NOT NULL, observed_at timestamptz NOT NULL,"
        " UNIQUE (history_id, clause_order, transform_version)"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_source_map ("
        " stage_mention_key text PRIMARY KEY, source_mention_id bigint NOT NULL UNIQUE"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_actor_stage ("
        " actor_id text PRIMARY KEY, actor_kind text NOT NULL,"
        " canonical_name text NOT NULL, normalized_name text NOT NULL,"
        " geography_code text, identity_hash text NOT NULL, alias text NOT NULL,"
        " normalized_alias text NOT NULL"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_actor_inserted ("
        " actor_id text PRIMARY KEY"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_project_stage ("
        " project_id text PRIMARY KEY, project_fingerprint text NOT NULL,"
        " canonical_name text NOT NULL"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_project_inserted ("
        " project_id text PRIMARY KEY"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_project_alias_stage ("
        " project_id text NOT NULL, alias text NOT NULL,"
        " normalized_alias text NOT NULL, stage_mention_key text NOT NULL"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_identity_stage ("
        " catalyst_id text PRIMARY KEY, dedup_key text NOT NULL,"
        " dedup_policy_version text NOT NULL"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_revision_stage ("
        " stage_revision_key bigint PRIMARY KEY, catalyst_id text NOT NULL UNIQUE,"
        " primary_stage_mention_key text NOT NULL, occurred_on date, known_on date,"
        " vocabulary_version text NOT NULL, primary_type_id text,"
        " type_ids_json text NOT NULL, event_stage text NOT NULL,"
        " stage_keyword text, stage_evidence_start integer,"
        " stage_evidence_end integer, certainty text NOT NULL,"
        " novelty_type text NOT NULL, action text, object_text text, project_id text,"
        " officiality text NOT NULL, continuation boolean NOT NULL,"
        " classification_transform_version text NOT NULL,"
        " event_structure_transform_version text NOT NULL,"
        " company_master_version text NOT NULL, dedup_policy_version text NOT NULL,"
        " dataset_hash text NOT NULL, content_hash text NOT NULL,"
        " output_hash text NOT NULL, generated_at timestamptz NOT NULL"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_revision_map ("
        " stage_revision_key bigint PRIMARY KEY,"
        " catalyst_revision_id bigint NOT NULL UNIQUE"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_source_link_stage ("
        " stage_revision_key bigint NOT NULL, stage_mention_key text NOT NULL,"
        " source_order integer NOT NULL,"
        " PRIMARY KEY (stage_revision_key, stage_mention_key)"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_span_stage ("
        " stage_revision_key bigint NOT NULL, stage_mention_key text NOT NULL,"
        " source_order integer NOT NULL, field text NOT NULL, value text NOT NULL,"
        " keyword text NOT NULL, start_offset integer NOT NULL,"
        " end_offset integer NOT NULL"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_company_role_stage ("
        " stage_revision_key bigint NOT NULL, stage_mention_key text NOT NULL,"
        " company_id bigint NOT NULL, role text NOT NULL, impact text NOT NULL,"
        " evidence_start integer NOT NULL, evidence_end integer NOT NULL"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_participant_stage ("
        " stage_revision_key bigint NOT NULL, stage_mention_key text NOT NULL,"
        " actor_id text NOT NULL, participant_role text NOT NULL,"
        " evidence_start integer NOT NULL, evidence_end integer NOT NULL"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_value_stage ("
        " stage_revision_key bigint NOT NULL, stage_mention_key text NOT NULL,"
        " fact_type text NOT NULL, reported_value text NOT NULL,"
        " normalized_value numeric NOT NULL, unit text NOT NULL, currency text,"
        " value_basis text NOT NULL, eligible_for_sum boolean NOT NULL,"
        " effective_on date, evidence_start integer NOT NULL,"
        " evidence_end integer NOT NULL"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_geography_stage ("
        " stage_revision_key bigint NOT NULL, geography_code text NOT NULL,"
        " PRIMARY KEY (stage_revision_key, geography_code)"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_reaction_stage ("
        " stage_reaction_key bigint PRIMARY KEY, reaction_id text NOT NULL,"
        " history_id bigint NOT NULL, occurred_on date, direction text NOT NULL,"
        " transform_version text NOT NULL, output_hash text NOT NULL,"
        " generated_at timestamptz NOT NULL,"
        " UNIQUE (reaction_id, history_id, transform_version)"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_reaction_inserted ("
        " stage_reaction_key bigint PRIMARY KEY,"
        " reaction_revision_id bigint NOT NULL UNIQUE"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_reaction_map ("
        " stage_reaction_key bigint PRIMARY KEY,"
        " reaction_revision_id bigint NOT NULL UNIQUE"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_reaction_company_stage ("
        " stage_reaction_key bigint NOT NULL, company_id bigint NOT NULL,"
        " role text NOT NULL,"
        " PRIMARY KEY (stage_reaction_key, company_id, role)"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_reaction_link_stage ("
        " stage_revision_key bigint NOT NULL, stage_reaction_key bigint NOT NULL,"
        " PRIMARY KEY (stage_revision_key, stage_reaction_key)"
        ") ON COMMIT DROP",
        "CREATE TEMP TABLE catalyst_relation_stage ("
        " relation_id text PRIMARY KEY, from_catalyst_id text NOT NULL,"
        " to_catalyst_id text NOT NULL, relation_type text NOT NULL,"
        " reason text NOT NULL, dedup_policy_version text NOT NULL,"
        " created_at timestamptz NOT NULL"
        ") ON COMMIT DROP",
    )
    for statement in statements:
        db.execute(statement)


def load_catalyst_events_bulk(
    store: PostgresCatalystEventStore,
    result: DeduplicationResult,
    *,
    generated_at: datetime,
) -> CatalystLoadCounts:
    """COPY staging과 set-based INSERT로 전체 artifact를 한 번에 적재한다."""

    histories = store.current_histories()
    company_ids = store.company_ids()
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
            stored = store._history_for_mention(histories, draft.source_mention)
            if stored is None:
                missing = True
                continue
            if not store._source_matches(
                stored,
                draft.source_mention,
                draft.raw_text,
            ):
                mismatched = True
        missing_histories += int(missing)
        mismatched_histories += int(mismatched)
        if not missing and not mismatched:
            eligible.append(catalyst)
    eligible_tuple = tuple(eligible)
    eligible_ids = {item.catalyst_id for item in eligible_tuple}

    source_rows: dict[str, tuple[object, ...]] = {}
    source_versions: dict[tuple[int, int, str], str] = {}
    actor_rows: dict[str, tuple[object, ...]] = {}
    revision_keys = {
        catalyst.catalyst_id: index
        for index, catalyst in enumerate(eligible_tuple, start=1)
    }
    for catalyst in eligible_tuple:
        for draft in catalyst.drafts:
            mention = draft.source_mention
            stored = histories[(mention.source_theme_id, mention.source_history_key)]
            version_key = (
                stored.history_id,
                mention.clause_order,
                mention.transform_version,
            )
            previous_hash = source_versions.get(version_key)
            if previous_hash is not None and previous_hash != mention.output_hash:
                raise CatalystSourceConflictError(
                    "입력에 같은 history 절·변환 버전의 서로 다른 source mention이 "
                    "함께 있습니다."
                )
            source_versions[version_key] = mention.output_hash
            row = (
                mention.output_hash,
                stored.history_id,
                mention.clause_order,
                mention.source_revision_hash,
                mention.source_text_hash,
                mention.start,
                mention.end,
                mention.transform_version,
                mention.output_hash,
                stored.observed_at,
            )
            existing_source = source_rows.get(mention.output_hash)
            if existing_source is not None and existing_source != row:
                raise CatalystSourceConflictError(
                    f"source mention output_hash={mention.output_hash}가 충돌합니다."
                )
            source_rows[mention.output_hash] = row

            for participant in draft.participants:
                actor = participant.actor
                actor_row = (
                    actor.actor_key,
                    actor.actor_kind.value,
                    actor.canonical_name,
                    _normalize_name(actor.canonical_name),
                    actor.geography_code,
                    actor.identity_hash,
                    actor.evidence_text,
                    _normalize_name(actor.evidence_text),
                )
                previous_actor = actor_rows.get(actor.actor_key)
                actor_identity = (actor_row[1], actor_row[4], actor_row[5])
                previous_identity = (
                    None
                    if previous_actor is None
                    else (previous_actor[1], previous_actor[4], previous_actor[5])
                )
                if previous_identity is not None and previous_identity != actor_identity:
                    raise CatalystIdentityConflictError(
                        f"actor_id={actor.actor_key}가 입력 안에서 충돌합니다."
                    )
                if previous_actor is None or (
                    str(actor_row[3]),
                    str(actor_row[2]),
                    str(actor_row[7]),
                    str(actor_row[6]),
                ) < (
                    str(previous_actor[3]),
                    str(previous_actor[2]),
                    str(previous_actor[7]),
                    str(previous_actor[6]),
                ):
                    actor_rows[actor.actor_key] = actor_row

    projects = {
        project.project_id: project
        for project in result.projects
        if any(catalyst_id in eligible_ids for catalyst_id in project.catalyst_ids)
    }
    for catalyst in eligible_tuple:
        for draft in catalyst.drafts:
            if draft.project_id is not None and draft.project_id not in projects:
                raise CatalystIdentityConflictError(
                    f"project_id={draft.project_id}의 project record가 없습니다."
                )

    reaction_rows: dict[
        tuple[str, int, str], tuple[int, Any, int, str, str]
    ] = {}
    reaction_stage_keys: dict[tuple[str, int, str], int] = {}
    for catalyst in eligible_tuple:
        transform_version = catalyst.primary.transform_version
        for reaction in catalyst.theme_reactions:
            history = histories[
                (reaction.source_theme_id, reaction.source_history_key)
            ]
            key = (reaction.reaction_key, history.history_id, transform_version)
            output_hash = _reaction_hash(reaction)
            previous = reaction_rows.get(key)
            if previous is not None and previous[4] != output_hash:
                raise CatalystTransformConflictError(
                    f"reaction_id={reaction.reaction_key}가 입력 안에서 충돌합니다."
                )
            if previous is None:
                stage_key = len(reaction_rows) + 1
                reaction_stage_keys[key] = stage_key
                reaction_rows[key] = (
                    stage_key,
                    reaction,
                    history.history_id,
                    transform_version,
                    output_hash,
                )

    db = store._connection.cursor()
    try:
        if getattr(db, "copy", None) is None:
            raise RuntimeError(
                "bulk 적재에는 psycopg COPY를 지원하는 cursor가 필요합니다."
            )
        db.execute(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended('ontology.catalyst_events.bulk', 0))"
        )
        db.fetchone()
        _create_stage_tables(db)

        _copy_rows(
            db,
            "COPY catalyst_source_stage"
            " (stage_mention_key, history_id, clause_order, source_revision_hash,"
            " source_text_hash, start_offset, end_offset, transform_version,"
            " output_hash, observed_at) FROM STDIN",
            source_rows.values(),
        )
        _copy_rows(
            db,
            "COPY catalyst_actor_stage"
            " (actor_id, actor_kind, canonical_name, normalized_name,"
            " geography_code, identity_hash, alias, normalized_alias) FROM STDIN",
            actor_rows.values(),
        )
        _copy_rows(
            db,
            "COPY catalyst_project_stage"
            " (project_id, project_fingerprint, canonical_name) FROM STDIN",
            (
                (
                    project.project_id,
                    project.project_fingerprint,
                    project.reference,
                )
                for project in projects.values()
            ),
        )
        _copy_rows(
            db,
            "COPY catalyst_identity_stage"
            " (catalyst_id, dedup_key, dedup_policy_version) FROM STDIN",
            (
                (
                    catalyst.catalyst_id,
                    catalyst.dedup_key,
                    catalyst.dedup_policy_version,
                )
                for catalyst in eligible_tuple
            ),
        )

        def revision_rows() -> Iterable[tuple[object, ...]]:
            for catalyst in eligible_tuple:
                primary = catalyst.primary
                stage = primary.stage_evidence
                yield (
                    revision_keys[catalyst.catalyst_id],
                    catalyst.catalyst_id,
                    primary.source_mention.output_hash,
                    primary.occurred_on,
                    primary.known_on,
                    primary.ontology_version,
                    primary.primary_catalyst_type,
                    json.dumps(
                        list(primary.catalyst_types),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
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
                )

        _copy_rows(
            db,
            "COPY catalyst_revision_stage"
            " (stage_revision_key, catalyst_id, primary_stage_mention_key,"
            " occurred_on, known_on, vocabulary_version, primary_type_id,"
            " type_ids_json, event_stage, stage_keyword, stage_evidence_start,"
            " stage_evidence_end, certainty, novelty_type, action, object_text,"
            " project_id, officiality, continuation,"
            " classification_transform_version, event_structure_transform_version,"
            " company_master_version, dedup_policy_version, dataset_hash,"
            " content_hash, output_hash, generated_at) FROM STDIN",
            revision_rows(),
        )

        def source_link_rows() -> Iterable[tuple[object, ...]]:
            for catalyst in eligible_tuple:
                revision_key = revision_keys[catalyst.catalyst_id]
                for order, mention in enumerate(catalyst.source_mentions):
                    yield revision_key, mention.output_hash, order

        _copy_rows(
            db,
            "COPY catalyst_source_link_stage"
            " (stage_revision_key, stage_mention_key, source_order) FROM STDIN",
            source_link_rows(),
        )

        def project_alias_rows() -> Iterable[tuple[object, ...]]:
            for catalyst in eligible_tuple:
                for draft in catalyst.drafts:
                    if draft.project_id is None or draft.project_reference is None:
                        continue
                    yield (
                        draft.project_id,
                        draft.project_reference,
                        _normalize_name(draft.project_reference),
                        draft.source_mention.output_hash,
                    )

        _copy_rows(
            db,
            "COPY catalyst_project_alias_stage"
            " (project_id, alias, normalized_alias, stage_mention_key) FROM STDIN",
            project_alias_rows(),
        )

        def span_rows() -> Iterable[tuple[object, ...]]:
            for catalyst in eligible_tuple:
                revision_key = revision_keys[catalyst.catalyst_id]
                for draft in catalyst.drafts:
                    for span in draft.evidence:
                        yield (
                            revision_key,
                            draft.source_mention.output_hash,
                            span.source_order,
                            span.field,
                            span.value,
                            span.keyword,
                            span.start,
                            span.end,
                        )

        _copy_rows(
            db,
            "COPY catalyst_span_stage"
            " (stage_revision_key, stage_mention_key, source_order, field, value,"
            " keyword, start_offset, end_offset) FROM STDIN",
            span_rows(),
        )

        def company_role_rows() -> Iterable[tuple[object, ...]]:
            for catalyst in eligible_tuple:
                revision_key = revision_keys[catalyst.catalyst_id]
                for draft in catalyst.drafts:
                    for role in draft.company_roles:
                        yield (
                            revision_key,
                            draft.source_mention.output_hash,
                            company_ids[role.seed_stock_code],
                            role.role,
                            role.impact.value,
                            role.evidence_start,
                            role.evidence_end,
                        )

        _copy_rows(
            db,
            "COPY catalyst_company_role_stage"
            " (stage_revision_key, stage_mention_key, company_id, role, impact,"
            " evidence_start, evidence_end) FROM STDIN",
            company_role_rows(),
        )

        def participant_rows() -> Iterable[tuple[object, ...]]:
            for catalyst in eligible_tuple:
                revision_key = revision_keys[catalyst.catalyst_id]
                for draft in catalyst.drafts:
                    for participant in draft.participants:
                        yield (
                            revision_key,
                            draft.source_mention.output_hash,
                            participant.actor.actor_key,
                            participant.participant_role,
                            participant.actor.start,
                            participant.actor.end,
                        )

        _copy_rows(
            db,
            "COPY catalyst_participant_stage"
            " (stage_revision_key, stage_mention_key, actor_id, participant_role,"
            " evidence_start, evidence_end) FROM STDIN",
            participant_rows(),
        )

        def value_rows() -> Iterable[tuple[object, ...]]:
            for catalyst in eligible_tuple:
                revision_key = revision_keys[catalyst.catalyst_id]
                for draft in catalyst.drafts:
                    for value in draft.values:
                        yield (
                            revision_key,
                            draft.source_mention.output_hash,
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
                        )

        _copy_rows(
            db,
            "COPY catalyst_value_stage"
            " (stage_revision_key, stage_mention_key, fact_type, reported_value,"
            " normalized_value, unit, currency, value_basis, eligible_for_sum,"
            " effective_on, evidence_start, evidence_end) FROM STDIN",
            value_rows(),
        )

        def geography_rows() -> Iterable[tuple[object, ...]]:
            for catalyst in eligible_tuple:
                codes = {
                    code
                    for draft in catalyst.drafts
                    for code in draft.geography_codes
                }
                for code in sorted(codes):
                    yield revision_keys[catalyst.catalyst_id], code

        _copy_rows(
            db,
            "COPY catalyst_geography_stage"
            " (stage_revision_key, geography_code) FROM STDIN",
            geography_rows(),
        )
        _copy_rows(
            db,
            "COPY catalyst_reaction_stage"
            " (stage_reaction_key, reaction_id, history_id, occurred_on, direction,"
            " transform_version, output_hash, generated_at) FROM STDIN",
            (
                (
                    stage_key,
                    reaction.reaction_key,
                    history_id,
                    reaction.occurred_on,
                    reaction.direction,
                    transform_version,
                    output_hash,
                    generated_at,
                )
                for (
                    stage_key,
                    reaction,
                    history_id,
                    transform_version,
                    output_hash,
                ) in reaction_rows.values()
            ),
        )

        def reaction_company_rows() -> Iterable[tuple[object, ...]]:
            for (
                stage_key,
                reaction,
                _history_id,
                _transform_version,
                _output_hash,
            ) in reaction_rows.values():
                for stock_code in reaction.leader_stock_codes:
                    yield stage_key, company_ids[stock_code], "LEADER"
                for stock_code in reaction.related_stock_codes:
                    yield stage_key, company_ids[stock_code], "RELATED"

        _copy_rows(
            db,
            "COPY catalyst_reaction_company_stage"
            " (stage_reaction_key, company_id, role) FROM STDIN",
            reaction_company_rows(),
        )

        def reaction_link_rows() -> Iterable[tuple[object, ...]]:
            for catalyst in eligible_tuple:
                revision_key = revision_keys[catalyst.catalyst_id]
                transform_version = catalyst.primary.transform_version
                for reaction in catalyst.theme_reactions:
                    history = histories[
                        (reaction.source_theme_id, reaction.source_history_key)
                    ]
                    key = (
                        reaction.reaction_key,
                        history.history_id,
                        transform_version,
                    )
                    yield revision_key, reaction_stage_keys[key]

        _copy_rows(
            db,
            "COPY catalyst_reaction_link_stage"
            " (stage_revision_key, stage_reaction_key) FROM STDIN",
            reaction_link_rows(),
        )
        _copy_rows(
            db,
            "COPY catalyst_relation_stage"
            " (relation_id, from_catalyst_id, to_catalyst_id, relation_type,"
            " reason, dedup_policy_version, created_at) FROM STDIN",
            (
                (
                    relation.relation_key,
                    relation.from_catalyst_id,
                    relation.to_catalyst_id,
                    relation.relation_type.value,
                    relation.reason,
                    relation.dedup_policy_version,
                    generated_at,
                )
                for relation in result.relations
                if relation.from_catalyst_id in eligible_ids
                and relation.to_catalyst_id in eligible_ids
            ),
        )
        db.execute(
            "ANALYZE catalyst_source_stage, catalyst_actor_stage,"
            " catalyst_project_stage, catalyst_identity_stage,"
            " catalyst_revision_stage, catalyst_reaction_stage,"
            " catalyst_relation_stage"
        )

        db.execute(
            "SELECT s.history_id, s.clause_order, s.transform_version"
            " FROM catalyst_source_stage s"
            " JOIN ontology.source_mention_history l"
            "   ON l.history_id = s.history_id"
            "  AND l.clause_order = s.clause_order"
            "  AND l.transform_version = s.transform_version"
            " JOIN ontology.source_mentions m"
            "   ON m.source_mention_id = l.source_mention_id"
            " WHERE m.output_hash <> s.output_hash LIMIT 1"
        )
        conflict = db.fetchone()
        if conflict is not None:
            raise CatalystSourceConflictError(
                "같은 history 절·변환 버전이 다른 source mention으로 이미 "
                f"존재합니다: history_id={int(conflict[0])}"
            )
        db.execute(
            "SELECT s.actor_id FROM catalyst_actor_stage s"
            " JOIN ontology.actor_entities a ON a.actor_id = s.actor_id"
            " WHERE a.identity_hash <> s.identity_hash"
            "    OR a.actor_kind <> s.actor_kind"
            "    OR a.geography_code IS DISTINCT FROM s.geography_code"
            " LIMIT 1"
        )
        conflict = db.fetchone()
        if conflict is not None:
            raise CatalystIdentityConflictError(
                f"actor_id={str(conflict[0])}가 다른 identity로 이미 존재합니다."
            )
        db.execute(
            "SELECT s.project_id FROM catalyst_project_stage s"
            " JOIN ontology.projects p ON p.project_id = s.project_id"
            " WHERE p.project_fingerprint <> s.project_fingerprint LIMIT 1"
        )
        conflict = db.fetchone()
        if conflict is not None:
            raise CatalystIdentityConflictError(
                f"project_id={str(conflict[0])}가 다른 fingerprint로 존재합니다."
            )
        db.execute(
            "SELECT s.catalyst_id FROM catalyst_identity_stage s"
            " JOIN ontology.catalysts c ON c.catalyst_id = s.catalyst_id"
            " WHERE c.dedup_key <> s.dedup_key LIMIT 1"
        )
        conflict = db.fetchone()
        if conflict is not None:
            raise CatalystIdentityConflictError(
                f"catalyst_id={str(conflict[0])}가 다른 dedup key로 존재합니다."
            )
        db.execute(
            "WITH referenced AS ("
            " SELECT geography_code FROM catalyst_actor_stage"
            "  WHERE geography_code IS NOT NULL"
            " UNION SELECT geography_code FROM catalyst_geography_stage"
            ") SELECT r.geography_code FROM referenced r"
            " LEFT JOIN ontology.geographies g"
            "   ON g.geography_code = r.geography_code"
            " WHERE g.geography_code IS NULL LIMIT 1"
        )
        conflict = db.fetchone()
        if conflict is not None:
            raise CatalystIdentityConflictError(
                f"geography_code={str(conflict[0])}가 geography master에 없습니다."
            )
        db.execute(
            "SELECT s.catalyst_id FROM catalyst_revision_stage s"
            " JOIN ontology.catalyst_revisions r"
            "   ON r.catalyst_id = s.catalyst_id"
            "  AND r.dataset_hash = s.dataset_hash"
            "  AND r.vocabulary_version = s.vocabulary_version"
            "  AND r.classification_transform_version ="
            "      s.classification_transform_version"
            "  AND r.event_structure_transform_version ="
            "      s.event_structure_transform_version"
            "  AND r.company_master_version = s.company_master_version"
            "  AND r.dedup_policy_version = s.dedup_policy_version"
            " WHERE r.output_hash <> s.output_hash LIMIT 1"
        )
        conflict = db.fetchone()
        if conflict is not None:
            raise CatalystTransformConflictError(
                f"catalyst_id={str(conflict[0])}의 같은 dataset·변환 버전이 "
                "다른 결과를 만들었습니다. 해당 버전을 올리십시오."
            )
        db.execute(
            "SELECT s.reaction_id FROM catalyst_reaction_stage s"
            " JOIN ontology.theme_reaction_revisions r"
            "   ON r.reaction_id = s.reaction_id"
            "  AND r.history_id = s.history_id"
            "  AND r.transform_version = s.transform_version"
            " WHERE r.output_hash <> s.output_hash LIMIT 1"
        )
        conflict = db.fetchone()
        if conflict is not None:
            raise CatalystTransformConflictError(
                f"reaction_id={str(conflict[0])}의 같은 원천·변환 버전이 "
                "다른 결과로 존재합니다."
            )
        db.execute(
            "SELECT s.relation_id FROM catalyst_relation_stage s"
            " JOIN ontology.catalyst_relations r ON r.relation_id = s.relation_id"
            " WHERE r.from_catalyst_id <> s.from_catalyst_id"
            "    OR r.to_catalyst_id <> s.to_catalyst_id"
            "    OR r.relation_type <> s.relation_type"
            "    OR r.dedup_policy_version <> s.dedup_policy_version LIMIT 1"
        )
        conflict = db.fetchone()
        if conflict is not None:
            raise CatalystIdentityConflictError(
                f"relation_id={str(conflict[0])}가 충돌합니다."
            )
        db.execute(
            "SELECT s.relation_id FROM catalyst_relation_stage s"
            " JOIN ontology.catalyst_relations r"
            "   ON r.from_catalyst_id = s.from_catalyst_id"
            "  AND r.to_catalyst_id = s.to_catalyst_id"
            "  AND r.relation_type = s.relation_type"
            "  AND r.dedup_policy_version = s.dedup_policy_version"
            " WHERE r.relation_id <> s.relation_id LIMIT 1"
        )
        conflict = db.fetchone()
        if conflict is not None:
            raise CatalystIdentityConflictError(
                f"relation_id={str(conflict[0])}의 관계 fact가 이미 존재합니다."
            )

        db.execute(
            "INSERT INTO ontology.source_mentions"
            " (source_kind, source_revision_hash, source_text_hash, start_offset,"
            " end_offset, transform_version, output_hash, review_status, observed_at)"
            " SELECT 'INFOSTOCK_THEME_HISTORY', s.source_revision_hash,"
            " s.source_text_hash, s.start_offset, s.end_offset, s.transform_version,"
            " s.output_hash, 'AI_DRAFT', s.observed_at"
            " FROM catalyst_source_stage s"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM ontology.source_mention_history l"
            "   WHERE l.history_id = s.history_id"
            "     AND l.clause_order = s.clause_order"
            "     AND l.transform_version = s.transform_version"
            " ) ON CONFLICT (source_kind, source_revision_hash, transform_version,"
            " output_hash) DO NOTHING"
        )
        source_mentions_inserted = db.rowcount
        db.execute(
            "INSERT INTO catalyst_source_map"
            " (stage_mention_key, source_mention_id)"
            " SELECT s.stage_mention_key, m.source_mention_id"
            " FROM catalyst_source_stage s"
            " JOIN ontology.source_mentions m"
            "   ON m.source_kind = 'INFOSTOCK_THEME_HISTORY'"
            "  AND m.source_revision_hash = s.source_revision_hash"
            "  AND m.transform_version = s.transform_version"
            "  AND m.output_hash = s.output_hash"
        )
        db.execute(
            "INSERT INTO ontology.source_mention_history"
            " (source_mention_id, history_id, clause_order, transform_version)"
            " SELECT m.source_mention_id, s.history_id, s.clause_order,"
            " s.transform_version FROM catalyst_source_stage s"
            " JOIN catalyst_source_map m"
            "   ON m.stage_mention_key = s.stage_mention_key"
            " ON CONFLICT (history_id, clause_order, transform_version) DO NOTHING"
        )
        db.execute(
            "SELECT s.stage_mention_key FROM catalyst_source_stage s"
            " LEFT JOIN catalyst_source_map m"
            "   ON m.stage_mention_key = s.stage_mention_key"
            " LEFT JOIN ontology.source_mention_history l"
            "   ON l.history_id = s.history_id"
            "  AND l.clause_order = s.clause_order"
            "  AND l.transform_version = s.transform_version"
            " WHERE m.source_mention_id IS NULL"
            "    OR l.source_mention_id <> m.source_mention_id LIMIT 1"
        )
        conflict = db.fetchone()
        if conflict is not None:
            raise CatalystSourceConflictError(
                f"source mention mapping을 만들지 못했습니다: {str(conflict[0])}"
            )

        db.execute(
            "WITH inserted AS ("
            " INSERT INTO ontology.actor_entities"
            " (actor_id, actor_kind, canonical_name, normalized_name,"
            " geography_code, company_id, identity_hash)"
            " SELECT actor_id, actor_kind, canonical_name, normalized_name,"
            " geography_code, NULL, identity_hash FROM catalyst_actor_stage"
            " ON CONFLICT (actor_id) DO NOTHING RETURNING actor_id"
            ") INSERT INTO catalyst_actor_inserted (actor_id)"
            " SELECT actor_id FROM inserted"
        )
        actors_inserted = db.rowcount
        db.execute(
            "INSERT INTO ontology.actor_aliases"
            " (actor_id, alias, normalized_alias, valid_from, valid_to, source_kind)"
            " SELECT s.actor_id, s.alias, s.normalized_alias, NULL, NULL, 'CONTROLLED'"
            " FROM catalyst_actor_stage s"
            " JOIN catalyst_actor_inserted i ON i.actor_id = s.actor_id"
            " ON CONFLICT (actor_id, normalized_alias, valid_from, valid_to)"
            " DO NOTHING"
        )
        db.execute(
            "WITH inserted AS ("
            " INSERT INTO ontology.projects"
            " (project_id, project_fingerprint, canonical_name)"
            " SELECT project_id, project_fingerprint, canonical_name"
            " FROM catalyst_project_stage"
            " ON CONFLICT (project_id) DO NOTHING RETURNING project_id"
            ") INSERT INTO catalyst_project_inserted (project_id)"
            " SELECT project_id FROM inserted"
        )
        projects_inserted = db.rowcount
        db.execute(
            "INSERT INTO ontology.project_aliases"
            " (project_id, alias, normalized_alias, source_mention_id)"
            " SELECT DISTINCT s.project_id, s.alias, s.normalized_alias,"
            " m.source_mention_id FROM catalyst_project_alias_stage s"
            " JOIN catalyst_source_map m"
            "   ON m.stage_mention_key = s.stage_mention_key"
            " ON CONFLICT DO NOTHING"
        )
        db.execute(
            "INSERT INTO ontology.catalysts"
            " (catalyst_id, dedup_key, dedup_policy_version)"
            " SELECT catalyst_id, dedup_key, dedup_policy_version"
            " FROM catalyst_identity_stage"
            " ON CONFLICT (catalyst_id) DO NOTHING"
        )
        db.execute(
            "WITH prepared AS ("
            " SELECT s.*, COALESCE(("
            "   SELECT MAX(r.revision_no) FROM ontology.catalyst_revisions r"
            "   WHERE r.catalyst_id = s.catalyst_id"
            " ), 0) + 1 AS revision_no"
            " FROM catalyst_revision_stage s"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM ontology.catalyst_revisions r"
            "   WHERE r.catalyst_id = s.catalyst_id"
            "     AND r.output_hash = s.output_hash"
            " )"
            "), inserted AS ("
            " INSERT INTO ontology.catalyst_revisions"
            " (catalyst_id, revision_no, primary_source_mention_id, occurred_on,"
            " known_on, vocabulary_version, primary_type_id, type_ids, event_stage,"
            " stage_keyword, stage_evidence_start, stage_evidence_end, certainty,"
            " novelty_type, action, object_text, project_id, officiality,"
            " continuation, classification_transform_version,"
            " event_structure_transform_version, company_master_version,"
            " dedup_policy_version, dataset_hash, content_hash, output_hash, created_at)"
            " SELECT s.catalyst_id, s.revision_no, m.source_mention_id,"
            " s.occurred_on, s.known_on, s.vocabulary_version, s.primary_type_id,"
            " ARRAY(SELECT jsonb_array_elements_text(s.type_ids_json::jsonb)),"
            " s.event_stage, s.stage_keyword, s.stage_evidence_start,"
            " s.stage_evidence_end, s.certainty, s.novelty_type, s.action,"
            " s.object_text, s.project_id, s.officiality, s.continuation,"
            " s.classification_transform_version,"
            " s.event_structure_transform_version, s.company_master_version,"
            " s.dedup_policy_version, s.dataset_hash, s.content_hash, s.output_hash,"
            " s.generated_at FROM prepared s"
            " JOIN catalyst_source_map m"
            "   ON m.stage_mention_key = s.primary_stage_mention_key"
            " ORDER BY s.stage_revision_key"
            " RETURNING catalyst_revision_id, catalyst_id, output_hash"
            ") INSERT INTO catalyst_revision_map"
            " (stage_revision_key, catalyst_revision_id)"
            " SELECT s.stage_revision_key, i.catalyst_revision_id"
            " FROM inserted i JOIN catalyst_revision_stage s"
            "   ON s.catalyst_id = i.catalyst_id AND s.output_hash = i.output_hash"
        )
        inserted_revisions = db.rowcount
        existing_revisions = len(eligible_tuple) - inserted_revisions
        db.execute(
            "INSERT INTO ontology.catalyst_source_mentions"
            " (catalyst_revision_id, source_mention_id, source_order)"
            " SELECT r.catalyst_revision_id, m.source_mention_id, s.source_order"
            " FROM catalyst_source_link_stage s"
            " JOIN catalyst_revision_map r"
            "   ON r.stage_revision_key = s.stage_revision_key"
            " JOIN catalyst_source_map m"
            "   ON m.stage_mention_key = s.stage_mention_key"
            " ORDER BY s.stage_revision_key, s.source_order"
        )
        db.execute(
            "INSERT INTO ontology.catalyst_revision_spans"
            " (catalyst_revision_id, source_mention_id, source_order, field, value,"
            " keyword, start_offset, end_offset)"
            " SELECT r.catalyst_revision_id, m.source_mention_id, s.source_order,"
            " s.field, s.value, s.keyword, s.start_offset, s.end_offset"
            " FROM catalyst_span_stage s"
            " JOIN catalyst_revision_map r"
            "   ON r.stage_revision_key = s.stage_revision_key"
            " JOIN catalyst_source_map m"
            "   ON m.stage_mention_key = s.stage_mention_key"
        )
        db.execute(
            "INSERT INTO ontology.catalyst_company_roles"
            " (catalyst_revision_id, source_mention_id, company_id, role, impact,"
            " evidence_start, evidence_end)"
            " SELECT r.catalyst_revision_id, m.source_mention_id, s.company_id,"
            " s.role, s.impact, s.evidence_start, s.evidence_end"
            " FROM catalyst_company_role_stage s"
            " JOIN catalyst_revision_map r"
            "   ON r.stage_revision_key = s.stage_revision_key"
            " JOIN catalyst_source_map m"
            "   ON m.stage_mention_key = s.stage_mention_key"
        )
        company_roles_inserted = db.rowcount
        db.execute(
            "INSERT INTO ontology.catalyst_participants"
            " (catalyst_revision_id, source_mention_id, actor_id, participant_role,"
            " evidence_start, evidence_end)"
            " SELECT r.catalyst_revision_id, m.source_mention_id, s.actor_id,"
            " s.participant_role, s.evidence_start, s.evidence_end"
            " FROM catalyst_participant_stage s"
            " JOIN catalyst_revision_map r"
            "   ON r.stage_revision_key = s.stage_revision_key"
            " JOIN catalyst_source_map m"
            "   ON m.stage_mention_key = s.stage_mention_key"
        )
        participants_inserted = db.rowcount
        db.execute(
            "INSERT INTO ontology.catalyst_values"
            " (catalyst_revision_id, source_mention_id, fact_type, reported_value,"
            " normalized_value, unit, currency, value_basis, eligible_for_sum,"
            " effective_on, evidence_start, evidence_end)"
            " SELECT r.catalyst_revision_id, m.source_mention_id, s.fact_type,"
            " s.reported_value, s.normalized_value, s.unit, s.currency,"
            " s.value_basis, s.eligible_for_sum, s.effective_on, s.evidence_start,"
            " s.evidence_end FROM catalyst_value_stage s"
            " JOIN catalyst_revision_map r"
            "   ON r.stage_revision_key = s.stage_revision_key"
            " JOIN catalyst_source_map m"
            "   ON m.stage_mention_key = s.stage_mention_key"
        )
        values_inserted = db.rowcount
        db.execute(
            "INSERT INTO ontology.catalyst_geographies"
            " (catalyst_revision_id, geography_code)"
            " SELECT r.catalyst_revision_id, s.geography_code"
            " FROM catalyst_geography_stage s"
            " JOIN catalyst_revision_map r"
            "   ON r.stage_revision_key = s.stage_revision_key"
        )

        db.execute(
            "INSERT INTO ontology.theme_reactions (reaction_id, created_at)"
            " SELECT DISTINCT reaction_id, generated_at"
            " FROM catalyst_reaction_stage ON CONFLICT (reaction_id) DO NOTHING"
        )
        db.execute(
            "WITH prepared AS ("
            " SELECT s.*, COALESCE(("
            "   SELECT MAX(r.revision_no)"
            "   FROM ontology.theme_reaction_revisions r"
            "   WHERE r.reaction_id = s.reaction_id"
            " ), 0) + 1 AS revision_no"
            " FROM catalyst_reaction_stage s"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM ontology.theme_reaction_revisions r"
            "   WHERE r.reaction_id = s.reaction_id"
            "     AND r.history_id = s.history_id"
            "     AND r.transform_version = s.transform_version"
            " )"
            "), inserted AS ("
            " INSERT INTO ontology.theme_reaction_revisions"
            " (reaction_id, revision_no, history_id, occurred_on, direction,"
            " transform_version, output_hash, created_at)"
            " SELECT reaction_id, revision_no, history_id, occurred_on, direction,"
            " transform_version, output_hash, generated_at FROM prepared"
            " ORDER BY stage_reaction_key"
            " RETURNING reaction_revision_id, reaction_id, history_id,"
            " transform_version"
            ") INSERT INTO catalyst_reaction_inserted"
            " (stage_reaction_key, reaction_revision_id)"
            " SELECT s.stage_reaction_key, i.reaction_revision_id"
            " FROM inserted i JOIN catalyst_reaction_stage s"
            "   ON s.reaction_id = i.reaction_id"
            "  AND s.history_id = i.history_id"
            "  AND s.transform_version = i.transform_version"
        )
        reactions_inserted = db.rowcount
        db.execute(
            "INSERT INTO catalyst_reaction_map"
            " (stage_reaction_key, reaction_revision_id)"
            " SELECT s.stage_reaction_key, r.reaction_revision_id"
            " FROM catalyst_reaction_stage s"
            " JOIN ontology.theme_reaction_revisions r"
            "   ON r.reaction_id = s.reaction_id"
            "  AND r.history_id = s.history_id"
            "  AND r.transform_version = s.transform_version"
        )
        db.execute(
            "INSERT INTO ontology.theme_reaction_company_roles"
            " (reaction_revision_id, company_id, role)"
            " SELECT i.reaction_revision_id, s.company_id, s.role"
            " FROM catalyst_reaction_company_stage s"
            " JOIN catalyst_reaction_inserted i"
            "   ON i.stage_reaction_key = s.stage_reaction_key"
            " ON CONFLICT DO NOTHING"
        )
        db.execute(
            "INSERT INTO ontology.catalyst_theme_reactions"
            " (catalyst_revision_id, reaction_revision_id)"
            " SELECT r.catalyst_revision_id, q.reaction_revision_id"
            " FROM catalyst_reaction_link_stage s"
            " JOIN catalyst_revision_map r"
            "   ON r.stage_revision_key = s.stage_revision_key"
            " JOIN catalyst_reaction_map q"
            "   ON q.stage_reaction_key = s.stage_reaction_key"
        )
        db.execute(
            "INSERT INTO ontology.catalyst_relations"
            " (relation_id, from_catalyst_id, to_catalyst_id, relation_type,"
            " reason, dedup_policy_version, review_status, created_at)"
            " SELECT relation_id, from_catalyst_id, to_catalyst_id, relation_type,"
            " reason, dedup_policy_version, 'AI_DRAFT', created_at"
            " FROM catalyst_relation_stage ON CONFLICT (relation_id) DO NOTHING"
        )
        relations_inserted = db.rowcount
        store._connection.commit()
        return CatalystLoadCounts(
            total_catalysts=len(result.catalysts),
            inserted_revisions=inserted_revisions,
            existing_revisions=existing_revisions,
            skipped_catalysts=len(result.catalysts) - len(eligible_tuple),
            missing_histories=missing_histories,
            mismatched_histories=mismatched_histories,
            source_mentions_inserted=source_mentions_inserted,
            source_mentions_existing=len(source_rows) - source_mentions_inserted,
            projects_inserted=projects_inserted,
            actors_inserted=actors_inserted,
            company_roles_inserted=company_roles_inserted,
            participants_inserted=participants_inserted,
            values_inserted=values_inserted,
            reactions_inserted=reactions_inserted,
            relations_inserted=relations_inserted,
        )
    except BaseException:
        store._connection.rollback()
        raise
    finally:
        db.close()
