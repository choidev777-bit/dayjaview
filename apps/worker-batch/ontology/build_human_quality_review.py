#!/usr/bin/env python3
"""E-22의 남은 사람 품질 관문을 위한 결정론적 검수팩을 만든다.

검수 원천은 이미 생성된 로컬 산출물, Daily 수집본, 로컬 서비스 DB뿐이다.
외부 API는 사용하지 않는다. 자동 판정은 candidate 열에 두고, 독립 검수자와
최종 사람의 열은 비워 둔다. 최종 승인 전 review_status는 항상 AI_DRAFT다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.infostock.daily_api import load_daily_api_backfill
from packages.ontology import VOCABULARY, classify_catalyst
from packages.ontology.daily_mentions import (
    classify_daily_format,
    mentions_from_daily_post,
)
from packages.ontology.event_structure import split_event_clauses

SAMPLE_SEED = "human-quality-review/2026-08-17/v1"
TARGET_PER_GROUP = 30
AUTO_MERGED_PAIR_SAMPLE = 100
REVIEW_STATUS = "AI_DRAFT"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="E-22 회사 역할·사건·금액·중복 사람 검수팩을 만듭니다."
    )
    parser.add_argument(
        "--research-dir",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology",
    )
    parser.add_argument(
        "--daily-input",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "infostock" / "daily-full-20260814",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "research"
            / "ontology"
            / "human_quality_review"
        ),
    )
    parser.add_argument("--database-url-env", default="INFOSTOCK_DATABASE_URL")
    return parser


def _stable_score(*parts: object) -> int:
    payload = "\x1f".join((SAMPLE_SEED, *(str(part) for part in parts)))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest(), "big")


@dataclass
class StableSampler:
    """key hash가 작은 행 N개를 순서와 무관하게 유지한다."""

    limit: int
    salt: str
    _heap: list[tuple[int, str, dict[str, object]]] = field(default_factory=list)

    def add(self, key: str, row: dict[str, object]) -> None:
        if self.limit <= 0:
            return
        score = _stable_score(self.salt, key)
        item = (-score, key, row)
        if len(self._heap) < self.limit:
            heapq.heappush(self._heap, item)
            return
        current_max = -self._heap[0][0]
        if score < current_max:
            heapq.heapreplace(self._heap, item)

    def rows(self) -> list[dict[str, object]]:
        return [
            row
            for _, _, row in sorted(
                ((-neg_score, key, row) for neg_score, key, row in self._heap),
                key=lambda item: (item[0], item[1]),
            )
        ]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_tsv(
    path: Path,
    rows: Sequence[dict[str, object]],
    columns: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(columns),
            dialect="excel-tab",
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _preserve_review_columns(
    path: Path,
    rows: list[dict[str, object]],
    columns: Sequence[str],
) -> None:
    """동일 review_key의 독립·사람 판정을 재생성 시 보존한다."""

    if not path.is_file() or "review_key" not in columns:
        return
    review_columns = [
        column
        for column in columns
        if column.startswith("independent_")
        or column.startswith("human_")
        or column == "review_status"
    ]
    with path.open(encoding="utf-8", newline="") as stream:
        existing = {
            str(row["review_key"]): row
            for row in csv.DictReader(stream, dialect="excel-tab")
        }
    for row in rows:
        prior = existing.get(str(row["review_key"]))
        if prior is None:
            continue
        for column in review_columns:
            if prior.get(column, "") != "":
                row[column] = prior[column]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gold_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.open(encoding="utf-8"):
        value = line.rstrip("\n")
        if value and not value.startswith("#"):
            keys.add(value.split("\t", 1)[0])
    return keys


def _evidence_json(classification: Any) -> str:
    return _json(
        [
            {
                "field": span.field,
                "value": span.value,
                "keyword": span.keyword,
                "start": span.start,
                "end": span.end,
            }
            for span in classification.evidence_spans
        ]
    )


def _load_histories_and_role_sample(
    path: Path,
) -> tuple[
    dict[tuple[str, str], dict[str, str]],
    list[dict[str, object]],
    Counter[str],
]:
    raw_by_key: dict[tuple[str, str], dict[str, str]] = {}
    role_samplers = {
        role: StableSampler(TARGET_PER_GROUP, f"role:{role}")
        for role in (
            "ACTOR",
            "ISSUER",
            "CONTRACTOR",
            "COUNTERPARTY",
            "TARGET",
            "BENEFICIARY",
            "ADVERSELY_AFFECTED",
            "LEADER",
            "RELATED",
        )
    }
    role_population: Counter[str] = Counter()
    unique_role_rows: dict[str, dict[str, tuple[int, str, dict[str, object]]]] = {
        role: {} for role in role_samplers
    }
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            history = json.loads(line)
            theme_id = str(history["sourceThemeId"])
            history_key = str(history["sourceHistoryKey"])
            raw_text = str(history["rawText"])
            raw_by_key[(theme_id, history_key)] = {
                "raw_text": raw_text,
                "theme_name": str(history.get("themeName") or ""),
                "event_date": str(history.get("eventDate") or ""),
            }
            # 구조 참조(주도주·구성종목) mention은 이름만 근거로 남아, rawText만
            # 보여주면 검수자가 그 이름이 어느 명단에서 왔는지 확인할 수 없다.
            # 특히 RELATED의 출처인 구성종목 명단은 rawText에 아예 없다.
            # 같은 history의 같은 종류 mention을 모아 명단 자체를 같이 싣는다.
            reference_names: dict[str, list[str]] = {}
            reference_slot: dict[int, tuple[str, int]] = {}
            for sibling_index, sibling in enumerate(history.get("mentions", [])):
                sibling_kind = str(sibling.get("mentionKind") or "")
                if sibling_kind == "BODY":
                    continue
                names = reference_names.setdefault(sibling_kind, [])
                names.append(str(sibling.get("mentionText") or ""))
                reference_slot[sibling_index] = (sibling_kind, len(names))
            for mention_index, mention in enumerate(history.get("mentions", [])):
                slot = reference_slot.get(mention_index)
                if slot is None:
                    reference_list = ""
                    reference_position = ""
                else:
                    slot_kind, slot_position = slot
                    slot_names = reference_names[slot_kind]
                    reference_list = ", ".join(slot_names)
                    reference_position = f"{slot_position}/{len(slot_names)}"
                for role_index, role in enumerate(mention.get("roles", [])):
                    candidate_role = str(role["role"])
                    role_population[candidate_role] += 1
                    review_key = (
                        f"{theme_id}/{history_key}/m{mention_index}/r{role_index}"
                    )
                    row: dict[str, object] = {
                            "review_key": review_key,
                            "source_theme_id": theme_id,
                            "source_history_key": history_key,
                            "event_date": history.get("eventDate") or "",
                            "theme_name": history.get("themeName") or "",
                            "raw_text": raw_text,
                            "mention_kind": mention.get("mentionKind") or "",
                            "mention_text": mention.get("mentionText") or "",
                            "seed_stock_code": mention.get("seedStockCode") or "",
                            "resolution_status": mention.get("resolutionStatus") or "",
                            "resolution_basis": mention.get("resolutionBasis") or "",
                            "candidate_role": candidate_role,
                            "candidate_evidence_source": (
                                "RAW_TEXT"
                                if role.get("extractionBasis") == "BODY_RULE"
                                else "STRUCTURED_REFERENCE"
                            ),
                            "candidate_evidence_container": (
                                raw_text
                                if role.get("extractionBasis") == "BODY_RULE"
                                else mention.get("mentionText") or ""
                            ),
                            "candidate_evidence_text": role.get("evidenceText") or "",
                            "candidate_evidence_start": role.get("start", ""),
                            "candidate_evidence_end": role.get("end", ""),
                            "candidate_extraction_basis": role.get("extractionBasis")
                            or "",
                            "candidate_reference_list": reference_list,
                            "candidate_reference_position": reference_position,
                            "independent_role": "",
                            "independent_mention_resolution": "",
                            "independent_evidence_ok": "",
                            "independent_note": "",
                            "human_role": "",
                            "human_mention_resolution": "",
                            "human_evidence_ok": "",
                            "human_note": "",
                            "review_status": REVIEW_STATUS,
                        }
                    semantic_key = _json(
                        {
                            "rawText": raw_text,
                            "seedStockCode": mention.get("seedStockCode") or "",
                            "role": candidate_role,
                            "evidenceText": role.get("evidenceText") or "",
                            "extractionBasis": role.get("extractionBasis") or "",
                        }
                    )
                    score = _stable_score(f"role:{candidate_role}", review_key)
                    prior = unique_role_rows[candidate_role].get(semantic_key)
                    if prior is None or (score, review_key) < (prior[0], prior[1]):
                        unique_role_rows[candidate_role][semantic_key] = (
                            score,
                            review_key,
                            row,
                        )
    for role, unique in unique_role_rows.items():
        for _, review_key, row in unique.values():
            role_samplers[role].add(review_key, row)
    rows = [
        row
        for role in role_samplers
        for row in role_samplers[role].rows()
    ]
    return raw_by_key, rows, role_population


def _build_goldset_topup(
    raw_by_key: dict[tuple[str, str], dict[str, str]],
    research_dir: Path,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    score = json.loads(
        (research_dir / "goldset_score_human_confirmed_test.json").read_text(
            encoding="utf-8"
        )
    )
    target = int(score["minConfirmedPerType"])
    deficits = {
        type_id: target - int(score["perTypeConfirmed"][type_id]["confirmed"])
        for type_id in score["unmeasurableTypes"]
    }
    used = _gold_keys(REPOSITORY_ROOT / "tests" / "ontology" / "goldset_v1.tsv")
    used |= _gold_keys(
        REPOSITORY_ROOT / "tests" / "ontology" / "goldset_supplement.tsv"
    )
    samplers = {
        type_id: StableSampler(deficit * 2, f"goldset-topup:{type_id}")
        for type_id, deficit in deficits.items()
    }
    for (theme_id, history_key), history in raw_by_key.items():
        key = f"{theme_id}/{history_key}"
        if key in used:
            continue
        raw_text = history["raw_text"]
        result = classify_catalyst(raw_text)
        primary = result.primary_type_id or "OTHER"
        sampler = samplers.get(primary)
        if sampler is None:
            continue
        sampler.add(
            key,
            {
                "review_key": key,
                "intended_split": "",
                "source_theme_id": theme_id,
                "source_history_key": history_key,
                "theme_name": history["theme_name"],
                "event_date": history["event_date"],
                "raw_text": raw_text,
                "candidate_primary": primary,
                "candidate_alt": "|".join(result.type_ids[1:]),
                "candidate_direction": result.direction,
                "candidate_certainty": result.certainty,
                "candidate_evidence": _evidence_json(result),
                "independent_primary": "",
                "independent_alt": "",
                "independent_direction": "",
                "independent_certainty": "",
                "independent_note": "",
                "human_primary": "",
                "human_alt": "",
                "human_direction": "",
                "human_certainty": "",
                "human_note": "",
                "review_status": REVIEW_STATUS,
            },
        )
    rows: list[dict[str, object]] = []
    for type_id in sorted(samplers):
        selected = samplers[type_id].rows()
        expected = deficits[type_id] * 2
        if len(selected) != expected:
            raise RuntimeError(
                f"{type_id} 보강 표본이 부족합니다: {len(selected)} / {expected}"
            )
        for index, row in enumerate(selected):
            row["intended_split"] = "dev" if index % 2 == 0 else "test"
            rows.append(row)
    return rows, deficits


def _build_daily_sample(
    daily_input: Path,
) -> tuple[list[dict[str, object]], Counter[str], dict[str, list[dict[str, str]]]]:
    backfill, _ = load_daily_api_backfill(daily_input)
    samplers: dict[tuple[str, str], StableSampler] = {}
    population: Counter[str] = Counter()
    posts_by_date: dict[str, list[dict[str, str]]] = defaultdict(list)
    for post in backfill.posts:
        published = post.published_date.isoformat() if post.published_date else ""
        if published:
            posts_by_date[published].append(
                {
                    "sourcePostKey": post.source_post_key,
                    "title": post.title,
                    "bodyStatus": post.body_status,
                    "formatFamily": classify_daily_format(post).value,
                }
            )
        for mention in mentions_from_daily_post(post):
            relation_type = mention.relation_type
            status = mention.serving_status.value
            population[f"{relation_type}:{status}"] += 1
            sampler = samplers.setdefault(
                (relation_type, status),
                StableSampler(
                    TARGET_PER_GROUP,
                    f"daily:{relation_type}:{status}",
                ),
            )
            review_key = (
                f"{mention.source_post_key}/{mention.source_relation_order}/"
                f"{mention.source_text_hash[:16]}"
            )
            sampler.add(
                review_key,
                {
                    "review_key": review_key,
                    "source_post_key": mention.source_post_key,
                    "source_relation_order": mention.source_relation_order,
                    "published_date": published,
                    "trading_date": (
                        mention.trading_date.isoformat() if mention.trading_date else ""
                    ),
                    "trading_date_basis": mention.trading_date_basis.value,
                    "raw_text": mention.raw_text,
                    "candidate_relation_type": relation_type,
                    "candidate_scope": mention.mention_scope.value,
                    "candidate_theme_name": mention.source_theme_name or "",
                    "candidate_stock_name": mention.source_stock_name or "",
                    "candidate_stock_code": mention.source_stock_code or "",
                    "candidate_suggested_role": mention.suggested_role or "",
                    "candidate_serving_status": status,
                    "candidate_span_start": mention.start,
                    "candidate_span_end": mention.end,
                    "independent_relation_type": "",
                    "independent_scope": "",
                    "independent_theme_name": "",
                    "independent_role": "",
                    "independent_span_ok": "",
                    "independent_serving_status": "",
                    "independent_note": "",
                    "human_relation_type": "",
                    "human_scope": "",
                    "human_theme_name": "",
                    "human_role": "",
                    "human_span_ok": "",
                    "human_serving_status": "",
                    "human_note": "",
                    "review_status": REVIEW_STATUS,
                },
            )
    rows: list[dict[str, object]] = []
    for relation_type in ("DESCRIPTION", "SECTION_DETAIL", "THEME_STOCK"):
        review_rows = samplers.get(
            (relation_type, "REVIEW_REQUIRED"), StableSampler(0, "unused")
        ).rows()[:10]
        eligible_need = TARGET_PER_GROUP - len(review_rows)
        eligible_rows = samplers.get(
            (relation_type, "ELIGIBLE"), StableSampler(0, "unused")
        ).rows()[:eligible_need]
        selected = review_rows + eligible_rows
        if len(selected) < TARGET_PER_GROUP:
            remainder = samplers.get(
                (relation_type, "REVIEW_REQUIRED"), StableSampler(0, "unused")
            ).rows()[len(review_rows) :]
            selected.extend(remainder[: TARGET_PER_GROUP - len(selected)])
        if len(selected) != TARGET_PER_GROUP:
            raise RuntimeError(
                f"Daily {relation_type} 표본이 부족합니다: {len(selected)}"
            )
        rows.extend(selected)
    return rows, population, posts_by_date


def _history_clause(
    raw_by_key: dict[tuple[str, str], dict[str, str]],
    theme_id: str,
    history_key: str,
    clause_order: int,
) -> str:
    history = raw_by_key.get((theme_id, history_key))
    if history is None:
        return ""
    raw_text = history["raw_text"]
    clauses = split_event_clauses(raw_text)
    if 0 <= clause_order < len(clauses):
        return clauses[clause_order].text
    return raw_text


def _load_catalysts(
    path: Path,
    raw_by_key: dict[tuple[str, str], dict[str, str]],
) -> tuple[
    dict[str, dict[str, Any]],
    list[dict[str, object]],
    Counter[str],
    list[dict[str, object]],
    Counter[str],
    list[dict[str, object]],
]:
    stage_samplers: dict[str, StableSampler] = {}
    value_samplers: dict[str, StableSampler] = {}
    stage_population: Counter[str] = Counter()
    value_population: Counter[str] = Counter()
    auto_merged = StableSampler(AUTO_MERGED_PAIR_SAMPLE, "dedup:auto-merged")
    cat_by_id: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            catalyst = json.loads(line)
            catalyst_id = str(catalyst["catalystId"])
            primary = catalyst["primary"]
            mention = primary["sourceMention"]
            theme_id = str(mention["sourceThemeId"])
            history_key = str(mention["sourceHistoryKey"])
            full = raw_by_key.get((theme_id, history_key), {}).get("raw_text", "")
            start = int(mention.get("start") or 0)
            end = int(mention.get("end") or len(full))
            raw_clause = full[start:end] if 0 <= start < end <= len(full) else full
            compact = {
                "catalyst_id": catalyst_id,
                "occurred_on": str(primary.get("occurredOn") or ""),
                "event_stage": str(primary.get("eventStage") or ""),
                "primary_type": str(primary.get("primaryCatalystType") or "OTHER"),
                "project_id": str(primary.get("projectId") or ""),
                "project_reference": str(primary.get("projectReference") or ""),
                "raw_text": raw_clause,
                "source_mentions": catalyst.get("sourceMentions", []),
                "values": primary.get("values", []),
                "stage_evidence": primary.get("stageEvidence"),
            }
            cat_by_id[catalyst_id] = compact
            stage = compact["event_stage"]
            stage_population[stage] += 1
            stage_sampler = stage_samplers.setdefault(
                stage, StableSampler(TARGET_PER_GROUP, f"stage:{stage}")
            )
            evidence = compact["stage_evidence"] or {}
            evidence_start = evidence.get("start", "")
            evidence_end = evidence.get("end", "")
            if evidence_start != "" and evidence_end != "":
                evidence_start = int(evidence_start) - start
                evidence_end = int(evidence_end) - start
            stage_sampler.add(
                catalyst_id,
                {
                    "review_key": catalyst_id,
                    "catalyst_id": catalyst_id,
                    "occurred_on": compact["occurred_on"],
                    "primary_type": compact["primary_type"],
                    "project_id": compact["project_id"],
                    "project_reference": compact["project_reference"],
                    "raw_text": raw_clause,
                    "candidate_stage": stage,
                    "candidate_stage_keyword": evidence.get("keyword", ""),
                    "candidate_evidence_start": evidence_start,
                    "candidate_evidence_end": evidence_end,
                    "independent_stage": "",
                    "independent_evidence_ok": "",
                    "independent_note": "",
                    "human_stage": "",
                    "human_evidence_ok": "",
                    "human_note": "",
                    "review_status": REVIEW_STATUS,
                },
            )
            for value_index, value in enumerate(compact["values"]):
                fact_type = str(value["factType"])
                value_population[fact_type] += 1
                value_sampler = value_samplers.setdefault(
                    fact_type,
                    StableSampler(TARGET_PER_GROUP, f"value:{fact_type}"),
                )
                value_key = f"{catalyst_id}/v{value_index}"
                ev_start = int(value["evidenceStart"])
                ev_end = int(value["evidenceEnd"])
                value_sampler.add(
                    value_key,
                    {
                        "review_key": value_key,
                        "catalyst_id": catalyst_id,
                        "occurred_on": compact["occurred_on"],
                        "primary_type": compact["primary_type"],
                        "event_stage": stage,
                        "raw_text": raw_clause,
                        "candidate_fact_type": fact_type,
                        "candidate_reported_value": value.get("reportedValue") or "",
                        "candidate_normalized_value": value.get("normalizedValue")
                        or "",
                        "candidate_unit": value.get("unit") or "",
                        "candidate_currency": value.get("currency") or "",
                        "candidate_value_basis": value.get("valueBasis") or "",
                        "candidate_eligible_for_sum": value.get("eligibleForSum"),
                        "candidate_evidence_text": raw_clause[ev_start:ev_end],
                        "candidate_evidence_start": ev_start,
                        "candidate_evidence_end": ev_end,
                        "independent_fact_type": "",
                        "independent_reported_value": "",
                        "independent_normalized_value": "",
                        "independent_unit": "",
                        "independent_currency": "",
                        "independent_value_basis": "",
                        "independent_eligible_for_sum": "",
                        "independent_evidence_ok": "",
                        "independent_note": "",
                        "human_fact_type": "",
                        "human_reported_value": "",
                        "human_normalized_value": "",
                        "human_unit": "",
                        "human_currency": "",
                        "human_value_basis": "",
                        "human_eligible_for_sum": "",
                        "human_evidence_ok": "",
                        "human_note": "",
                        "review_status": REVIEW_STATUS,
                    },
                )
            source_mentions = compact["source_mentions"]
            if len(source_mentions) > 1:
                left, right = source_mentions[:2]
                left_text = _history_clause(
                    raw_by_key,
                    str(left["sourceThemeId"]),
                    str(left["sourceHistoryKey"]),
                    int(left["clauseOrder"]),
                )
                right_text = _history_clause(
                    raw_by_key,
                    str(right["sourceThemeId"]),
                    str(right["sourceHistoryKey"]),
                    int(right["clauseOrder"]),
                )
                auto_merged.add(
                    catalyst_id,
                    _dedup_row(
                        review_key=f"auto/{catalyst_id}",
                        candidate_basis="AUTO_MERGED",
                        candidate_same_event="YES",
                        relation_reason="동일 dedup key로 자동 병합",
                        left_id=(
                            f"{left['sourceThemeId']}/{left['sourceHistoryKey']}/"
                            f"c{left['clauseOrder']}"
                        ),
                        left_text=left_text,
                        left_meta=compact,
                        right_id=(
                            f"{right['sourceThemeId']}/{right['sourceHistoryKey']}/"
                            f"c{right['clauseOrder']}"
                        ),
                        right_text=right_text,
                        right_meta=compact,
                    ),
                )
    stage_rows = [
        row
        for stage in sorted(stage_samplers)
        for row in stage_samplers[stage].rows()
    ]
    value_rows = [
        row
        for fact_type in sorted(value_samplers)
        for row in value_samplers[fact_type].rows()
    ]
    return (
        cat_by_id,
        stage_rows,
        stage_population,
        value_rows,
        value_population,
        auto_merged.rows(),
    )


def _load_db_value_sample(
    database_url: str,
) -> tuple[list[dict[str, object]], Counter[str], str]:
    """실제 서비스 DB의 최신 catalyst value fact 전체에서 표집한다."""

    import psycopg

    samplers: dict[str, StableSampler] = {}
    population: Counter[str] = Counter()
    rows: list[dict[str, object]] = []
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        with connection.cursor() as db:
            db.execute("SELECT current_database()")
            database_name = str(db.fetchone()[0])
            db.execute(
                "WITH latest AS ("
                " SELECT DISTINCT ON (catalyst_id) catalyst_revision_id,"
                " catalyst_id, occurred_on, primary_type_id, event_stage"
                " FROM ontology.catalyst_revisions"
                " ORDER BY catalyst_id, revision_no DESC"
                ")"
                " SELECT cv.catalyst_value_id, l.catalyst_id, l.occurred_on,"
                " l.primary_type_id, l.event_stage, cv.fact_type,"
                " cv.reported_value, cv.normalized_value, cv.unit, cv.currency,"
                " cv.value_basis, cv.eligible_for_sum, h.raw_text,"
                " cv.evidence_start, cv.evidence_end"
                " FROM latest l"
                " JOIN ontology.catalyst_values cv"
                " ON cv.catalyst_revision_id = l.catalyst_revision_id"
                " JOIN ontology.source_mention_history smh"
                " ON smh.source_mention_id = cv.source_mention_id"
                " JOIN core.infostock_theme_history h ON h.history_id = smh.history_id"
                " ORDER BY cv.catalyst_value_id"
            )
            for record in db.fetchall():
                fact_id = int(record[0])
                fact_type = str(record[5])
                raw_text = str(record[12] or "")
                ev_start = int(record[13])
                ev_end = int(record[14])
                population[fact_type] += 1
                sampler = samplers.setdefault(
                    fact_type,
                    StableSampler(TARGET_PER_GROUP, f"value-db:{fact_type}"),
                )
                review_key = f"db-value-{fact_id}"
                sampler.add(
                    review_key,
                    {
                        "review_key": review_key,
                        "catalyst_id": str(record[1]),
                        "occurred_on": str(record[2] or ""),
                        "primary_type": str(record[3] or "OTHER"),
                        "event_stage": str(record[4]),
                        "raw_text": raw_text,
                        "candidate_fact_type": fact_type,
                        "candidate_reported_value": str(record[6]),
                        "candidate_normalized_value": str(record[7]),
                        "candidate_unit": str(record[8]),
                        "candidate_currency": str(record[9] or ""),
                        "candidate_value_basis": str(record[10]),
                        "candidate_eligible_for_sum": bool(record[11]),
                        "candidate_evidence_text": raw_text[ev_start:ev_end],
                        "candidate_evidence_start": ev_start,
                        "candidate_evidence_end": ev_end,
                        "independent_fact_type": "",
                        "independent_reported_value": "",
                        "independent_normalized_value": "",
                        "independent_unit": "",
                        "independent_currency": "",
                        "independent_value_basis": "",
                        "independent_eligible_for_sum": "",
                        "independent_evidence_ok": "",
                        "independent_note": "",
                        "human_fact_type": "",
                        "human_reported_value": "",
                        "human_normalized_value": "",
                        "human_unit": "",
                        "human_currency": "",
                        "human_value_basis": "",
                        "human_eligible_for_sum": "",
                        "human_evidence_ok": "",
                        "human_note": "",
                        "review_status": REVIEW_STATUS,
                    },
                )
        connection.rollback()
    for fact_type in sorted(samplers):
        rows.extend(samplers[fact_type].rows())
    return rows, population, database_name


def _dedup_row(
    *,
    review_key: str,
    candidate_basis: str,
    candidate_same_event: str,
    relation_reason: str,
    left_id: str,
    left_text: str,
    left_meta: dict[str, Any],
    right_id: str,
    right_text: str,
    right_meta: dict[str, Any],
) -> dict[str, object]:
    return {
        "review_key": review_key,
        "candidate_basis": candidate_basis,
        "candidate_same_event": candidate_same_event,
        "relation_reason": relation_reason,
        "left_id": left_id,
        "left_date": left_meta.get("occurred_on", ""),
        "left_stage": left_meta.get("event_stage", ""),
        "left_primary_type": left_meta.get("primary_type", ""),
        "left_project_id": left_meta.get("project_id", ""),
        "left_values": _json(left_meta.get("values", [])),
        "left_raw_text": left_text,
        "right_id": right_id,
        "right_date": right_meta.get("occurred_on", ""),
        "right_stage": right_meta.get("event_stage", ""),
        "right_primary_type": right_meta.get("primary_type", ""),
        "right_project_id": right_meta.get("project_id", ""),
        "right_values": _json(right_meta.get("values", [])),
        "right_raw_text": right_text,
        "independent_same_event": "",
        "independent_note": "",
        "human_same_event": "",
        "human_note": "",
        "review_status": REVIEW_STATUS,
    }


def _build_projects(
    path: Path, cat_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            project = json.loads(line)
            timeline = [
                {
                    "catalystId": catalyst_id,
                    "occurredOn": cat_by_id.get(catalyst_id, {}).get(
                        "occurred_on", ""
                    ),
                    "stage": cat_by_id.get(catalyst_id, {}).get("event_stage", ""),
                    "rawText": cat_by_id.get(catalyst_id, {}).get("raw_text", ""),
                }
                for catalyst_id in project["catalystIds"]
            ]
            rows.append(
                {
                    "review_key": project["projectId"],
                    "project_id": project["projectId"],
                    "candidate_reference": project["reference"],
                    "candidate_latest_stage": project["latestStage"],
                    "first_occurred_on": project.get("firstOccurredOn") or "",
                    "last_occurred_on": project.get("lastOccurredOn") or "",
                    "catalyst_count": len(project["catalystIds"]),
                    "candidate_timeline": _json(timeline),
                    "independent_same_project": "",
                    "independent_reference": "",
                    "independent_latest_stage": "",
                    "independent_note": "",
                    "human_same_project": "",
                    "human_reference": "",
                    "human_latest_stage": "",
                    "human_note": "",
                    "review_status": REVIEW_STATUS,
                }
            )
    return rows


def _build_possible_duplicates(
    path: Path,
    cat_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            relation = json.loads(line)
            if relation["relationType"] != "POSSIBLE_DUPLICATE":
                continue
            left_id = str(relation["fromCatalystId"])
            right_id = str(relation["toCatalystId"])
            left = cat_by_id[left_id]
            right = cat_by_id[right_id]
            rows.append(
                _dedup_row(
                    review_key=str(relation["relationKey"]),
                    candidate_basis="POSSIBLE_DUPLICATE",
                    candidate_same_event="NO",
                    relation_reason=str(relation["reason"]),
                    left_id=left_id,
                    left_text=str(left["raw_text"]),
                    left_meta=left,
                    right_id=right_id,
                    right_text=str(right["raw_text"]),
                    right_meta=right,
                )
            )
    return rows


def _build_answer_mismatches(
    path: Path,
    posts_by_date: dict[str, list[dict[str, str]]],
) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for index, mismatch in enumerate(payload.get("mismatches", [])):
        published = str(mismatch["publishedDate"])
        cohort = sorted(
            posts_by_date.get(published, []),
            key=lambda item: (item["title"], item["sourcePostKey"]),
        )
        expected_multi = len(cohort) > 1
        rows.append(
            {
                "review_key": f"answer-mismatch-{index:03d}",
                "published_date": published,
                "source_file": mismatch.get("sourceFile") or "",
                "parse_status": mismatch.get("parseStatus") or "",
                "mismatch_reasons": _json(mismatch.get("reasons", [])),
                "same_publish_date_post_count": len(cohort),
                "same_publish_date_posts": _json(cohort),
                "candidate_resolution": (
                    "EXPECTED_MULTI_POST_AGGREGATION"
                    if expected_multi
                    else "INVESTIGATE"
                ),
                "candidate_note": (
                    "같은 발행일에 여러 거래일 게시물이 있어 단일 원문과 일자 집계의 "
                    "상세 문단 수가 다르다."
                    if expected_multi
                    else "동일 발행일 복수 게시물로 설명되지 않는다."
                ),
                "independent_resolution": "",
                "independent_note": "",
                "human_resolution": "",
                "human_note": "",
                "review_status": REVIEW_STATUS,
            }
        )
    return rows


ROLE_DEFINITIONS = {
    "ACTOR": "회사가 원문의 행동 주체다.",
    "ISSUER": "회사가 증권·공시·실적·배당 등의 발행·공시 주체다.",
    "CONTRACTOR": "회사가 수주·공급·납품 계약을 체결하거나 수행한다.",
    "COUNTERPARTY": "회사가 다른 주체의 계약·협력 상대방이다.",
    "TARGET": "회사가 인수·제재·소송·조치 등의 대상이다.",
    "BENEFICIARY": "회사가 수혜 대상으로 명시됐다.",
    "ADVERSELY_AFFECTED": "회사가 피해·부정적 영향 대상으로 명시됐다.",
    "LEADER": "회사가 주도주 목록에만 등장한다.",
    "RELATED": "회사가 테마 구성·관련주 목록에만 등장한다.",
}

STAGE_DEFINITIONS = {
    "RUMOR": "기대·전망·가능성·추진설·관측·루머 단계",
    "REVIEW": "검토·타당성 조사 단계",
    "DISCUSSION": "협상·논의·협의 단계",
    "BID": "입찰·응찰·제안서 제출 단계",
    "SHORTLIST": "숏리스트·예비후보 단계",
    "PREFERRED_BIDDER": "우선협상대상자 선정 단계",
    "SIGNED": "본계약·공급계약·수주·MOU 체결 단계",
    "EXECUTING": "납품·공급·생산 개시 또는 착공·이행 단계",
    "COMPLETED": "납품·사업 완료 또는 완공·준공 단계",
    "DELAYED": "납기·일정 지연 또는 연기 단계",
    "CANCELLED": "계약 해지·수주 취소·철회·중단 단계",
    "UNSPECIFIED": "원문에 위 단계 표지가 없다.",
}

VALUE_DEFINITIONS = {
    "CONTRACT_VALUE": "계약·수주 금액",
    "INVESTMENT_VALUE": "투자·시설·사업비 금액",
    "CAPACITY": "GW·MW·톤 등 생산·설비 용량",
    "QUANTITY": "대·기·개 등 수량",
    "STAKE_PERCENT": "지분율",
}


def _definitions() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for definition in VOCABULARY:
        rows.append(
            {
                "category": "CATALYST_TYPE",
                "item_id": definition.type_id,
                "name_ko": definition.name_ko,
                "description_ko": definition.description_ko,
                "allowed_values_or_note": "",
            }
        )
    for role, description in ROLE_DEFINITIONS.items():
        rows.append(
            {
                "category": "COMPANY_ROLE",
                "item_id": role,
                "name_ko": role,
                "description_ko": description,
                "allowed_values_or_note": "",
            }
        )
    for stage, description in STAGE_DEFINITIONS.items():
        rows.append(
            {
                "category": "EVENT_STAGE",
                "item_id": stage,
                "name_ko": stage,
                "description_ko": description,
                "allowed_values_or_note": "",
            }
        )
    for fact_type, description in VALUE_DEFINITIONS.items():
        rows.append(
            {
                "category": "VALUE_FACT",
                "item_id": fact_type,
                "name_ko": fact_type,
                "description_ko": description,
                "allowed_values_or_note": "",
            }
        )
    rows.extend(
        [
            {
                "category": "DAILY_RELATION_TYPE",
                "item_id": "DESCRIPTION",
                "name_ko": "테마 설명",
                "description_ko": "테마 머리글 또는 요약 설명 relation",
                "allowed_values_or_note": "scope=HEADLINE",
            },
            {
                "category": "DAILY_RELATION_TYPE",
                "item_id": "SECTION_DETAIL",
                "name_ko": "테마 상세",
                "description_ko": "해당 테마의 상세 서술 relation",
                "allowed_values_or_note": "scope=DETAIL",
            },
            {
                "category": "DAILY_RELATION_TYPE",
                "item_id": "THEME_STOCK",
                "name_ko": "테마 종목",
                "description_ko": "테마에 연결된 종목 행 relation",
                "allowed_values_or_note": "scope=STOCK_ROW; role=RELATED",
            },
            {
                "category": "DAILY_SCOPE",
                "item_id": "HEADLINE|DETAIL|STOCK_ROW",
                "name_ko": "Daily mention 범위",
                "description_ko": "머리글·상세 문단·종목 행을 구분한다.",
                "allowed_values_or_note": "HEADLINE|DETAIL|STOCK_ROW",
            },
            {
                "category": "DAILY_SERVING_STATUS",
                "item_id": "ELIGIBLE|REVIEW_REQUIRED|EXCLUDED",
                "name_ko": "Daily 서빙 상태",
                "description_ko": "자동 서빙 가능·사람 검토 필요·제외를 구분한다.",
                "allowed_values_or_note": "ELIGIBLE|REVIEW_REQUIRED|EXCLUDED",
            },
            {
                "category": "DECISION",
                "item_id": "YES_NO_UNCERTAIN",
                "name_ko": "이진 검수",
                "description_ko": "근거가 부족하면 억지로 YES/NO를 고르지 않는다.",
                "allowed_values_or_note": "YES|NO|UNCERTAIN",
            },
            {
                "category": "EVIDENCE_SOURCE",
                "item_id": "RAW_TEXT",
                "name_ko": "원문 근거",
                "description_ko": "근거 오프셋이 raw_text를 기준으로 한다.",
                "allowed_values_or_note": "candidate_evidence_container=raw_text",
            },
            {
                "category": "EVIDENCE_SOURCE",
                "item_id": "STRUCTURED_REFERENCE",
                "name_ko": "구조화 참조 근거",
                "description_ko": "근거 오프셋이 회사 목록의 mention_text를 기준으로 한다.",
                "allowed_values_or_note": (
                    "candidate_evidence_container=mention_text; raw_text 오프셋이 아님"
                ),
            },
            {
                "category": "MENTION_RESOLUTION",
                "item_id": "VALID|INVALID_MENTION|UNCERTAIN",
                "name_ko": "회사 mention 유효성",
                "description_ko": "회사 mention 자체가 실제 회사 지칭인지 먼저 판정한다.",
                "allowed_values_or_note": "VALID|INVALID_MENTION|UNCERTAIN",
            },
            {
                "category": "REVIEW_STATUS",
                "item_id": "AI_DRAFT",
                "name_ko": "검수 전",
                "description_ko": "candidate 또는 독립 검수만 있고 사람이 확정하지 않았다.",
                "allowed_values_or_note": "승격 판정 제외",
            },
            {
                "category": "REVIEW_STATUS",
                "item_id": "HUMAN_CONFIRMED",
                "name_ko": "사람 확정",
                "description_ko": "사람이 원문과 후보 판정을 보고 최종 확정했다.",
                "allowed_values_or_note": "승격 판정 포함",
            },
        ]
    )
    return rows


PACK_COLUMNS: dict[str, tuple[str, ...]] = {
    "goldset_topup_candidate.tsv": (
        "review_key",
        "intended_split",
        "source_theme_id",
        "source_history_key",
        "theme_name",
        "event_date",
        "raw_text",
        "candidate_primary",
        "candidate_alt",
        "candidate_direction",
        "candidate_certainty",
        "candidate_evidence",
        "independent_primary",
        "independent_alt",
        "independent_direction",
        "independent_certainty",
        "independent_note",
        "human_primary",
        "human_alt",
        "human_direction",
        "human_certainty",
        "human_note",
        "review_status",
    ),
    "daily_mentions_candidate.tsv": (
        "review_key",
        "source_post_key",
        "source_relation_order",
        "published_date",
        "trading_date",
        "trading_date_basis",
        "raw_text",
        "candidate_relation_type",
        "candidate_scope",
        "candidate_theme_name",
        "candidate_stock_name",
        "candidate_stock_code",
        "candidate_suggested_role",
        "candidate_serving_status",
        "candidate_span_start",
        "candidate_span_end",
        "independent_relation_type",
        "independent_scope",
        "independent_theme_name",
        "independent_role",
        "independent_span_ok",
        "independent_serving_status",
        "independent_note",
        "human_relation_type",
        "human_scope",
        "human_theme_name",
        "human_role",
        "human_span_ok",
        "human_serving_status",
        "human_note",
        "review_status",
    ),
    "company_roles_candidate.tsv": (
        "review_key",
        "source_theme_id",
        "source_history_key",
        "event_date",
        "theme_name",
        "raw_text",
        "mention_kind",
        "mention_text",
        "seed_stock_code",
        "resolution_status",
        "resolution_basis",
        "candidate_role",
        "candidate_evidence_source",
        "candidate_evidence_container",
        "candidate_evidence_text",
        "candidate_evidence_start",
        "candidate_evidence_end",
        "candidate_extraction_basis",
        "candidate_reference_list",
        "candidate_reference_position",
        "independent_role",
        "independent_mention_resolution",
        "independent_evidence_ok",
        "independent_note",
        "human_role",
        "human_mention_resolution",
        "human_evidence_ok",
        "human_note",
        "review_status",
    ),
    "event_stages_candidate.tsv": (
        "review_key",
        "catalyst_id",
        "occurred_on",
        "primary_type",
        "project_id",
        "project_reference",
        "raw_text",
        "candidate_stage",
        "candidate_stage_keyword",
        "candidate_evidence_start",
        "candidate_evidence_end",
        "independent_stage",
        "independent_evidence_ok",
        "independent_note",
        "human_stage",
        "human_evidence_ok",
        "human_note",
        "review_status",
    ),
    "projects_candidate.tsv": (
        "review_key",
        "project_id",
        "candidate_reference",
        "candidate_latest_stage",
        "first_occurred_on",
        "last_occurred_on",
        "catalyst_count",
        "candidate_timeline",
        "independent_same_project",
        "independent_reference",
        "independent_latest_stage",
        "independent_note",
        "human_same_project",
        "human_reference",
        "human_latest_stage",
        "human_note",
        "review_status",
    ),
    "value_facts_candidate.tsv": (
        "review_key",
        "catalyst_id",
        "occurred_on",
        "primary_type",
        "event_stage",
        "raw_text",
        "candidate_fact_type",
        "candidate_reported_value",
        "candidate_normalized_value",
        "candidate_unit",
        "candidate_currency",
        "candidate_value_basis",
        "candidate_eligible_for_sum",
        "candidate_evidence_text",
        "candidate_evidence_start",
        "candidate_evidence_end",
        "independent_fact_type",
        "independent_reported_value",
        "independent_normalized_value",
        "independent_unit",
        "independent_currency",
        "independent_value_basis",
        "independent_eligible_for_sum",
        "independent_evidence_ok",
        "independent_note",
        "human_fact_type",
        "human_reported_value",
        "human_normalized_value",
        "human_unit",
        "human_currency",
        "human_value_basis",
        "human_eligible_for_sum",
        "human_evidence_ok",
        "human_note",
        "review_status",
    ),
    "duplicate_pairs_candidate.tsv": (
        "review_key",
        "candidate_basis",
        "candidate_same_event",
        "relation_reason",
        "left_id",
        "left_date",
        "left_stage",
        "left_primary_type",
        "left_project_id",
        "left_values",
        "left_raw_text",
        "right_id",
        "right_date",
        "right_stage",
        "right_primary_type",
        "right_project_id",
        "right_values",
        "right_raw_text",
        "independent_same_event",
        "independent_note",
        "human_same_event",
        "human_note",
        "review_status",
    ),
    "answer_mismatch_candidate.tsv": (
        "review_key",
        "published_date",
        "source_file",
        "parse_status",
        "mismatch_reasons",
        "same_publish_date_post_count",
        "same_publish_date_posts",
        "candidate_resolution",
        "candidate_note",
        "independent_resolution",
        "independent_note",
        "human_resolution",
        "human_note",
        "review_status",
    ),
    "quality_review_definitions.tsv": (
        "category",
        "item_id",
        "name_ko",
        "description_ko",
        "allowed_values_or_note",
    ),
}


def _distribution(rows: Iterable[dict[str, object]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(field, "")) for row in rows).items()))


def _validate_written_packs(
    out_dir: Path,
    packs: dict[str, list[dict[str, object]]],
) -> dict[str, int]:
    checked_rows = 0
    checked_spans = 0
    for name, expected_rows in packs.items():
        with (out_dir / name).open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, dialect="excel-tab")
            if tuple(reader.fieldnames or ()) != PACK_COLUMNS[name]:
                raise RuntimeError(f"{name} 열 계약이 다릅니다.")
            rows = list(reader)
        if len(rows) != len(expected_rows):
            raise RuntimeError(f"{name} 행 수가 생성 결과와 다릅니다.")
        if "review_key" in PACK_COLUMNS[name]:
            keys = [row["review_key"] for row in rows]
            if len(set(keys)) != len(keys) or any(not key for key in keys):
                raise RuntimeError(f"{name} review_key가 비었거나 중복됩니다.")
        for row in rows:
            checked_rows += 1
            status = row.get("review_status")
            if status not in {None, "AI_DRAFT", "HUMAN_CONFIRMED"}:
                raise RuntimeError(f"{name} review_status가 올바르지 않습니다: {status}")
            if name == "daily_mentions_candidate.tsv":
                raw_text = row["raw_text"]
                start, end = int(row["candidate_span_start"]), int(
                    row["candidate_span_end"]
                )
                if raw_text[start:end] != raw_text:
                    raise RuntimeError(f"{name} span이 원문 전체와 다릅니다.")
                checked_spans += 1
            elif name == "company_roles_candidate.tsv":
                source = row["candidate_evidence_source"]
                expected_source = (
                    "RAW_TEXT"
                    if row["candidate_extraction_basis"] == "BODY_RULE"
                    else "STRUCTURED_REFERENCE"
                )
                if source != expected_source:
                    raise RuntimeError(f"{name} 역할 evidence source가 다릅니다.")
                container = row["candidate_evidence_container"]
                evidence = row["candidate_evidence_text"]
                start, end = int(row["candidate_evidence_start"]), int(
                    row["candidate_evidence_end"]
                )
                valid = (
                    container[start:end] == evidence
                    and container
                    == (
                        row["raw_text"]
                        if source == "RAW_TEXT"
                        else row["mention_text"]
                    )
                )
                if not valid:
                    raise RuntimeError(f"{name} 역할 evidence가 원천과 다릅니다.")
                # 구조 참조는 이름만으로는 검증할 수 없다. 출처 명단을 반드시 싣는다.
                if source == "STRUCTURED_REFERENCE":
                    listed = row["candidate_reference_list"].split(", ")
                    if row["mention_text"] not in listed:
                        raise RuntimeError(
                            f"{name} 구조 참조에 출처 명단이 없습니다."
                        )
                checked_spans += 1
            elif name == "event_stages_candidate.tsv":
                keyword = row["candidate_stage_keyword"]
                if keyword:
                    start, end = int(row["candidate_evidence_start"]), int(
                        row["candidate_evidence_end"]
                    )
                    if row["raw_text"][start:end] != keyword:
                        raise RuntimeError(f"{name} 단계 evidence span이 다릅니다.")
                    checked_spans += 1
            elif name == "value_facts_candidate.tsv":
                start, end = int(row["candidate_evidence_start"]), int(
                    row["candidate_evidence_end"]
                )
                if (
                    row["raw_text"][start:end]
                    != row["candidate_evidence_text"]
                ):
                    raise RuntimeError(f"{name} 금액 evidence span이 다릅니다.")
                checked_spans += 1
            elif name == "goldset_topup_candidate.tsv":
                for evidence in json.loads(row["candidate_evidence"]):
                    if (
                        row["raw_text"][int(evidence["start"]) : int(evidence["end"])]
                        != evidence["keyword"]
                    ):
                        raise RuntimeError(f"{name} 분류 evidence span이 다릅니다.")
                    checked_spans += 1
            elif name == "duplicate_pairs_candidate.tsv":
                if (
                    row["left_id"] == row["right_id"]
                    or not row["left_raw_text"]
                    or not row["right_raw_text"]
                ):
                    raise RuntimeError(f"{name} 사건 쌍이 올바르지 않습니다.")
            elif name == "projects_candidate.tsv":
                timeline = json.loads(row["candidate_timeline"])
                if len(timeline) != int(row["catalyst_count"]):
                    raise RuntimeError(f"{name} 프로젝트 timeline 수가 다릅니다.")
    return {"checkedRows": checked_rows, "checkedSpans": checked_spans}


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    research_dir = arguments.research_dir
    out_dir = arguments.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    database_url = os.environ.get(str(arguments.database_url_env), "").strip()
    if not database_url:
        raise RuntimeError(
            f"{arguments.database_url_env} 환경변수의 로컬 서비스 DB URL이 필요합니다."
        )

    raw_by_key, role_rows, role_population = _load_histories_and_role_sample(
        research_dir / "company_labels.jsonl"
    )
    topup_rows, topup_deficits = _build_goldset_topup(raw_by_key, research_dir)
    daily_rows, daily_population, posts_by_date = _build_daily_sample(
        arguments.daily_input
    )
    (
        cat_by_id,
        stage_rows,
        stage_population,
        _primary_value_rows,
        _primary_value_population,
        auto_merged_rows,
    ) = _load_catalysts(research_dir / "company_catalysts.jsonl", raw_by_key)
    value_rows, value_population, database_name = _load_db_value_sample(database_url)
    project_rows = _build_projects(
        research_dir / "company_projects.jsonl", cat_by_id
    )
    possible_duplicate_rows = _build_possible_duplicates(
        research_dir / "company_catalyst_relations.jsonl", cat_by_id
    )
    duplicate_rows = auto_merged_rows + possible_duplicate_rows
    answer_rows = _build_answer_mismatches(
        research_dir / "answer_review_queue.json", posts_by_date
    )
    definition_rows = _definitions()

    packs: dict[str, list[dict[str, object]]] = {
        "goldset_topup_candidate.tsv": topup_rows,
        "daily_mentions_candidate.tsv": daily_rows,
        "company_roles_candidate.tsv": role_rows,
        "event_stages_candidate.tsv": stage_rows,
        "projects_candidate.tsv": project_rows,
        "value_facts_candidate.tsv": value_rows,
        "duplicate_pairs_candidate.tsv": duplicate_rows,
        "answer_mismatch_candidate.tsv": answer_rows,
        "quality_review_definitions.tsv": definition_rows,
    }
    for name, rows in packs.items():
        _preserve_review_columns(out_dir / name, rows, PACK_COLUMNS[name])
        _write_tsv(out_dir / name, rows, PACK_COLUMNS[name])
    validation = _validate_written_packs(out_dir, packs)

    manifest: dict[str, object] = {
        "schemaVersion": "1.0.0",
        "sampleSeed": SAMPLE_SEED,
        "reviewStatus": REVIEW_STATUS,
        "sourceDatabase": database_name,
        "validation": validation,
        "sourceArtifactHashes": {
            name: _sha256(research_dir / name)
            for name in (
                "company_labels.jsonl",
                "company_catalysts.jsonl",
                "company_projects.jsonl",
                "company_catalyst_relations.jsonl",
                "answer_review_queue.json",
            )
        },
        "packs": {
            name: {
                "rows": len(rows),
                "sha256": _sha256(out_dir / name),
            }
            for name, rows in packs.items()
        },
        "goldsetTopup": {
            "deficitsPerTestType": dict(sorted(topup_deficits.items())),
            "distribution": _distribution(topup_rows, "candidate_primary"),
            "splitDistribution": _distribution(topup_rows, "intended_split"),
        },
        "daily": {
            "population": dict(sorted(daily_population.items())),
            "sampleDistribution": _distribution(
                daily_rows, "candidate_relation_type"
            ),
            "servingStatusDistribution": _distribution(
                daily_rows, "candidate_serving_status"
            ),
        },
        "roles": {
            "population": dict(sorted(role_population.items())),
            "sampleDistribution": _distribution(role_rows, "candidate_role"),
        },
        "stages": {
            "population": dict(sorted(stage_population.items())),
            "sampleDistribution": _distribution(stage_rows, "candidate_stage"),
        },
        "values": {
            "population": dict(sorted(value_population.items())),
            "sampleDistribution": _distribution(
                value_rows, "candidate_fact_type"
            ),
        },
        "projects": {"population": len(project_rows), "sampled": len(project_rows)},
        "duplicates": {
            "autoMergedSample": len(auto_merged_rows),
            "possibleDuplicatePopulation": len(possible_duplicate_rows),
            "sampleDistribution": _distribution(
                duplicate_rows, "candidate_basis"
            ),
        },
        "answerMismatches": len(answer_rows),
    }
    manifest_path = out_dir / "human_quality_review_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "outDir": str(out_dir),
                "manifestPath": str(manifest_path),
                "packRows": {
                    name: len(rows) for name, rows in sorted(packs.items())
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
