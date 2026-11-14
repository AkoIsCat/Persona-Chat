"""다중 URL 문서를 display_order 순으로 분석해 사실을 누적한다."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from typing import Iterator

from app.database import get_connection
from app.services.character_analysis import (
    PERSONA_FIELDS,
    extract_document_facts,
    synthesize_persona_from_facts,
)

DOCUMENT_PROCESSED_FIELD = "__document_processed__"
STATUS_ACTIVE = "active"
STATUS_CONFLICT = "conflict"
STATUS_DOCUMENT_PROCESSED = "document_processed"

# 캐릭터별 분석 잠금에 쓰는 advisory lock 네임스페이스.
ANALYZE_LOCK_NAMESPACE = 740_391


class AnalysisInProgressError(RuntimeError):
    """같은 캐릭터 분석이 이미 진행 중일 때 발생한다."""


def is_persona_confirmed(character_id: int) -> bool:
    sql = """
        SELECT 1
        FROM character_personas
        WHERE character_id = %s
          AND is_confirmed = TRUE
        LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (character_id,))
            return cur.fetchone() is not None


def get_character_name(character_id: int, user_id: int) -> str | None:
    sql = """
        SELECT name
        FROM characters
        WHERE id = %s
          AND user_id = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (character_id, user_id))
            row = cur.fetchone()
            return row[0] if row else None


def list_source_documents(character_id: int, user_id: int) -> list[dict]:
    """character_urls.display_order 순서로 원본 문서를 반환한다."""
    sql = """
        SELECT
            csd.id,
            cu.display_order,
            cu.url,
            csd.title,
            csd.raw_text
        FROM characters c
        JOIN character_urls cu
            ON cu.character_id = c.id
        JOIN character_source_documents csd
            ON csd.character_url_id = cu.id
        WHERE c.id = %s
          AND c.user_id = %s
        ORDER BY cu.display_order, csd.id
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (character_id, user_id))
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "display_order": row[1],
            "url": row[2],
            "title": row[3],
            "raw_text": row[4],
        }
        for row in rows
    ]


def list_content_facts(character_id: int) -> list[dict]:
    """다음 문서 분석과 최종 합성에 쓰는 active/conflict 사실."""
    sql = """
        SELECT
            id,
            field_name,
            fact_text,
            source_document_ids,
            status,
            related_fact_id
        FROM character_persona_facts
        WHERE character_id = %s
          AND status IN (%s, %s)
        ORDER BY id
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (character_id, STATUS_ACTIVE, STATUS_CONFLICT))
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "field_name": row[1],
            "fact_text": row[2],
            "source_document_ids": list(row[3] or []),
            "status": row[4],
            "related_fact_id": row[5],
        }
        for row in rows
    ]


def get_processed_document_ids(character_id: int) -> set[int]:
    sql = """
        SELECT DISTINCT unnest(source_document_ids)
        FROM character_persona_facts
        WHERE character_id = %s
          AND status = %s
          AND field_name = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    character_id,
                    STATUS_DOCUMENT_PROCESSED,
                    DOCUMENT_PROCESSED_FIELD,
                ),
            )
            rows = cur.fetchall()

    return {int(row[0]) for row in rows if row[0] is not None}


def find_processed_identical_document(
    character_id: int,
    document_id: int,
) -> int | None:
    """
    같은 URL에 본문이 같은 이전 문서가 이미 처리됐으면 그 문서 ID를 반환한다.
    """
    sql = """
        SELECT older.id
        FROM character_source_documents newer
        JOIN character_urls cu
            ON cu.id = newer.character_url_id
        JOIN character_source_documents older
            ON older.character_url_id = cu.id
           AND older.raw_text = newer.raw_text
           AND older.id <> newer.id
        WHERE newer.id = %s
          AND cu.character_id = %s
          AND EXISTS (
              SELECT 1
              FROM character_persona_facts f
              WHERE f.character_id = %s
                AND f.status = %s
                AND f.field_name = %s
                AND older.id = ANY (f.source_document_ids)
          )
        ORDER BY older.id
        LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    document_id,
                    character_id,
                    character_id,
                    STATUS_DOCUMENT_PROCESSED,
                    DOCUMENT_PROCESSED_FIELD,
                ),
            )
            row = cur.fetchone()
            return int(row[0]) if row else None


def mark_document_processed(cur, character_id: int, document_id: int) -> None:
    """사실이 0건이어도 문서 처리 완료를 sentinel 행으로 남긴다."""
    cur.execute(
        """
        SELECT 1
        FROM character_persona_facts
        WHERE character_id = %s
          AND status = %s
          AND field_name = %s
          AND %s = ANY(source_document_ids)
        LIMIT 1
        """,
        (
            character_id,
            STATUS_DOCUMENT_PROCESSED,
            DOCUMENT_PROCESSED_FIELD,
            document_id,
        ),
    )
    if cur.fetchone():
        return

    cur.execute(
        """
        INSERT INTO character_persona_facts (
            character_id,
            field_name,
            fact_text,
            source_document_ids,
            status
        )
        VALUES (%s, %s, %s, %s::bigint[], %s)
        """,
        (
            character_id,
            DOCUMENT_PROCESSED_FIELD,
            "",
            [document_id],
            STATUS_DOCUMENT_PROCESSED,
        ),
    )


def _normalize_fact_text(text: str) -> str:
    return " ".join((text or "").split())


def _fact_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。])\s+|\n+", (text or "").strip())
    return [part.strip() for part in parts if part.strip()]


def _same_fact_information(left: str, right: str) -> bool:
    return _normalize_fact_text(left) == _normalize_fact_text(right)


def _text_covers_fact(container: str, fact_text: str) -> bool:
    """container가 fact_text의 모든 문장을 포함할 때만 참이다."""
    sentences = _fact_sentences(fact_text)
    if not sentences:
        return False
    normalized = _normalize_fact_text(container)
    return all(
        _normalize_fact_text(sentence) in normalized
        for sentence in sentences
    )


def _novel_fact_text(existing_text: str, incoming_text: str) -> str:
    """기존 사실에 없는 문장만 돌려준다."""
    covered = _normalize_fact_text(existing_text)
    novel = [
        sentence
        for sentence in _fact_sentences(incoming_text)
        if _normalize_fact_text(sentence) not in covered
    ]
    return " ".join(novel)


def fact_belongs_to_character(cur, fact_id: int, character_id: int) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM character_persona_facts
        WHERE id = %s
          AND character_id = %s
        LIMIT 1
        """,
        (fact_id, character_id),
    )
    return cur.fetchone() is not None


def get_character_fact(cur, fact_id: int, character_id: int) -> dict | None:
    cur.execute(
        """
        SELECT fact_text, status
        FROM character_persona_facts
        WHERE id = %s
          AND character_id = %s
        LIMIT 1
        """,
        (fact_id, character_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"fact_text": row[0] or "", "status": row[1]}


def _insert_active_fact(
    cur,
    character_id: int,
    field_name: str,
    fact_text: str,
    document_id: int,
) -> None:
    cur.execute(
        """
        INSERT INTO character_persona_facts (
            character_id,
            field_name,
            fact_text,
            source_document_ids,
            status
        )
        VALUES (%s, %s, %s, %s::bigint[], %s)
        """,
        (
            character_id,
            field_name,
            fact_text,
            [document_id],
            STATUS_ACTIVE,
        ),
    )


def _replace_fact_text_and_append_source(
    cur,
    fact_id: int,
    character_id: int,
    fact_text: str,
    document_id: int,
) -> None:
    """기존 문장을 모두 포함한 보완 결과로 바꾸고 이번 문서 출처를 더한다."""
    cur.execute(
        """
        UPDATE character_persona_facts
        SET
            fact_text = %s,
            source_document_ids = (
                SELECT ARRAY(
                    SELECT DISTINCT unnest(
                        source_document_ids || %s::bigint[]
                    )
                    ORDER BY 1
                )
            ),
            updated_at = NOW()
        WHERE id = %s
          AND character_id = %s
          AND status = %s
        """,
        (
            fact_text,
            [document_id],
            fact_id,
            character_id,
            STATUS_ACTIVE,
        ),
    )


def _append_source(cur, fact_id: int, document_id: int, character_id: int) -> None:
    cur.execute(
        """
        UPDATE character_persona_facts
        SET
            source_document_ids = (
                SELECT ARRAY(
                    SELECT DISTINCT unnest(
                        source_document_ids || %s::bigint[]
                    )
                    ORDER BY 1
                )
            ),
            updated_at = NOW()
        WHERE id = %s
          AND character_id = %s
          AND status IN (%s, %s)
        """,
        (
            [document_id],
            fact_id,
            character_id,
            STATUS_ACTIVE,
            STATUS_CONFLICT,
        ),
    )


def apply_fact_operations(
    cur,
    character_id: int,
    document_id: int,
    operations: list[dict],
) -> None:
    """
    한 문서의 사실 작업을 반영한다.
    refine/duplicate/conflict의 기존 사실 ID는 같은 캐릭터일 때만 사용한다.
    """
    for operation in operations:
        action = (operation.get("action") or "").strip().lower()
        field_name = (operation.get("field_name") or "").strip()
        fact_text = (operation.get("fact_text") or "").strip()
        existing_fact_id = operation.get("existing_fact_id")

        if field_name not in PERSONA_FIELDS:
            continue

        if action == "add":
            if not fact_text:
                continue
            _insert_active_fact(
                cur, character_id, field_name, fact_text, document_id
            )
            continue

        if action == "duplicate":
            if existing_fact_id is None:
                continue
            fact_id = int(existing_fact_id)
            existing = get_character_fact(cur, fact_id, character_id)
            if existing is None or existing["status"] != STATUS_ACTIVE:
                continue
            if _same_fact_information(existing["fact_text"], fact_text):
                _append_source(cur, fact_id, document_id, character_id)
                continue
            if _text_covers_fact(fact_text, existing["fact_text"]):
                _replace_fact_text_and_append_source(
                    cur, fact_id, character_id, fact_text, document_id
                )
                continue
            novel_text = _novel_fact_text(existing["fact_text"], fact_text)
            if novel_text:
                _insert_active_fact(
                    cur, character_id, field_name, novel_text, document_id
                )
            continue

        if action == "refine":
            if existing_fact_id is None or not fact_text:
                continue
            fact_id = int(existing_fact_id)
            existing = get_character_fact(cur, fact_id, character_id)
            if existing is None or existing["status"] != STATUS_ACTIVE:
                continue
            if _text_covers_fact(fact_text, existing["fact_text"]):
                _replace_fact_text_and_append_source(
                    cur, fact_id, character_id, fact_text, document_id
                )
                continue
            novel_text = _novel_fact_text(existing["fact_text"], fact_text)
            if novel_text:
                _insert_active_fact(
                    cur, character_id, field_name, novel_text, document_id
                )
            continue

        if action == "conflict":
            if not fact_text:
                continue
            related_id = None
            if existing_fact_id is not None:
                candidate = int(existing_fact_id)
                if fact_belongs_to_character(cur, candidate, character_id):
                    related_id = candidate
            cur.execute(
                """
                INSERT INTO character_persona_facts (
                    character_id,
                    field_name,
                    fact_text,
                    source_document_ids,
                    status,
                    related_fact_id
                )
                VALUES (%s, %s, %s, %s::bigint[], %s, %s)
                """,
                (
                    character_id,
                    field_name,
                    fact_text,
                    [document_id],
                    STATUS_CONFLICT,
                    related_id,
                ),
            )


def save_unconfirmed_persona(
    character_id: int,
    persona: dict,
    model_name: str,
) -> int | None:
    """is_confirmed = FALSE일 때만 최종 페르소나를 저장한다."""
    sql = """
        INSERT INTO character_personas (
            character_id,
            basic_info,
            personality,
            values_and_beliefs,
            goals,
            background,
            worldview,
            speech_style,
            behavior_patterns,
            summary,
            model_name
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (character_id)
        DO UPDATE SET
            basic_info = EXCLUDED.basic_info,
            personality = EXCLUDED.personality,
            values_and_beliefs = EXCLUDED.values_and_beliefs,
            goals = EXCLUDED.goals,
            background = EXCLUDED.background,
            worldview = EXCLUDED.worldview,
            speech_style = EXCLUDED.speech_style,
            behavior_patterns = EXCLUDED.behavior_patterns,
            summary = EXCLUDED.summary,
            model_name = EXCLUDED.model_name,
            updated_at = NOW()
        WHERE character_personas.is_confirmed = FALSE
        RETURNING id
    """
    values = (
        character_id,
        persona["basic_info"],
        persona["personality"],
        persona["values_and_beliefs"],
        persona["goals"],
        persona["background"],
        persona["worldview"],
        persona["speech_style"],
        persona["behavior_patterns"],
        persona["summary"],
        model_name,
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, values)
            row = cur.fetchone()
            return row[0] if row else None


_TOKEN_SUFFIXES = (
    "에서는",
    "에서",
    "으로",
    "에게",
    "이다",
    "한다",
    "했다",
    "됐다",
    "되었다",
    "하지",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "와",
    "과",
    "도",
    "만",
    "에",
    "로",
    "년",
)


def _core_tokens(text: str) -> set[str]:
    """조사와 어미를 뺀 내용어만 모은다. 중복 판정에는 쓰지 않는다."""
    tokens = set()
    for token in re.findall(r"[가-힣A-Za-z0-9]+", text or ""):
        trimmed = token
        changed = True
        while changed and len(trimmed) > 1:
            changed = False
            for suffix in _TOKEN_SUFFIXES:
                if trimmed.endswith(suffix) and len(trimmed) - len(suffix) >= 1:
                    trimmed = trimmed[: -len(suffix)]
                    changed = True
                    break
        if trimmed:
            tokens.add(trimmed)
    return tokens


def _years(text: str) -> set[str]:
    return set(re.findall(r"\d{3,4}", text or ""))


def _is_negated(text: str) -> bool:
    return re.search(r"않|없|아니", text or "") is not None


def _same_field(left: dict, field_name: str) -> bool:
    return left.get("field_name") == field_name


def _append_preserved_detail(existing_text: str, detail: str) -> str:
    """기존 문장을 앞에 유지하고, 아직 없는 세부 문장만 뒤에 붙인다."""
    base = (existing_text or "").strip()
    extra = (detail or "").strip()
    if not extra:
        return base
    if _normalize_fact_text(extra) in _normalize_fact_text(base):
        return base
    if base and base[-1] not in ".!?。":
        base += "."
    return f"{base} {extra}".strip()


def _exact_extra_sentences(existing_text: str, incoming_text: str) -> list[str]:
    existing = {_normalize_fact_text(sentence) for sentence in _fact_sentences(existing_text)}
    return [
        sentence
        for sentence in _fact_sentences(incoming_text)
        if _normalize_fact_text(sentence) not in existing
    ]


def _is_safe_specification(existing_text: str, incoming_text: str) -> bool:
    """
    한 문장이 같은 속성을 더 구체적으로 말할 때만 참이다.
    새 인물, 새 연도, 긴 추가어가 있으면 거절한다.
    """
    if _years(existing_text) != _years(incoming_text):
        return False
    old_tokens = _core_tokens(existing_text)
    new_tokens = _core_tokens(incoming_text)
    if not old_tokens or not old_tokens <= new_tokens:
        return False
    extra = new_tokens - old_tokens
    if not extra or any(len(token) > 2 for token in extra):
        return False
    return True


def _is_clear_conflict(existing_text: str, incoming_text: str) -> bool:
    """같은 속성의 연도나 긍정이 서로 다를 때만 참이다."""
    old_years = _years(existing_text)
    new_years = _years(incoming_text)
    cues = ("태어", "출생", "사망", "설립")
    if old_years and new_years and old_years != new_years:
        if any(cue in existing_text and cue in incoming_text for cue in cues):
            return True

    old_tokens = _core_tokens(existing_text)
    new_tokens = _core_tokens(incoming_text)
    if old_tokens and old_tokens == new_tokens and _is_negated(existing_text) != _is_negated(incoming_text):
        return True

    if "직업" in existing_text and "직업" in incoming_text:
        if _is_safe_specification(existing_text, incoming_text) or _is_safe_specification(
            incoming_text, existing_text
        ):
            return False
        old_job = old_tokens - {"직업"}
        new_job = new_tokens - {"직업"}
        if old_job and new_job and old_job.isdisjoint(new_job):
            return True
    return False


def _safe_added_detail(existing_text: str, incoming_text: str) -> str | None:
    """기존 문장을 모두 유지할 수 있는 추가 문장만 반환한다."""
    old_sentences = _fact_sentences(existing_text)
    new_sentences = _fact_sentences(incoming_text)
    if not old_sentences or not new_sentences:
        return None
    old_norms = {_normalize_fact_text(sentence) for sentence in old_sentences}
    if all(_normalize_fact_text(sentence) in {_normalize_fact_text(item) for item in new_sentences} for sentence in old_sentences):
        extras = _exact_extra_sentences(existing_text, incoming_text)
        if extras:
            return " ".join(extras)
        return None
    if len(old_sentences) == 1 and len(new_sentences) == 1 and _is_safe_specification(
        old_sentences[0], new_sentences[0]
    ):
        return new_sentences[0]
    return None


def classify_fact_candidates(
    existing_facts: list[dict],
    candidates: list[dict],
) -> list[dict]:
    """
    같은 field_name 안에서만 후보를 비교한다.
    불확실하면 add하고, 기존 문장을 덮어쓰지 않는다.
    """
    working = []
    for fact in existing_facts:
        if fact.get("status") == STATUS_DOCUMENT_PROCESSED:
            continue
        working.append(
            {
                "id": fact.get("id"),
                "field_name": fact.get("field_name"),
                "fact_text": fact.get("fact_text") or "",
                "status": fact.get("status") or STATUS_ACTIVE,
                "operation_index": None,
            }
        )

    operations = []
    for candidate in candidates:
        field_name = str(candidate.get("field_name") or "").strip()
        fact_text = str(candidate.get("fact_text") or "").strip()
        if field_name not in PERSONA_FIELDS or not fact_text:
            continue
        same_field = [item for item in working if _same_field(item, field_name)]

        exact = next(
            (item for item in same_field if _same_fact_information(item["fact_text"], fact_text)),
            None,
        )
        if exact is not None:
            if exact.get("id") is not None and exact.get("status") == STATUS_ACTIVE:
                operations.append(
                    {
                        "action": "duplicate",
                        "field_name": field_name,
                        "fact_text": exact["fact_text"],
                        "existing_fact_id": exact["id"],
                    }
                )
            continue

        conflicts = [
            item
            for item in same_field
            if item.get("id") is not None and _is_clear_conflict(item["fact_text"], fact_text)
        ]
        refinements = []
        for item in same_field:
            if item.get("status") == STATUS_CONFLICT:
                continue
            detail = _safe_added_detail(item["fact_text"], fact_text)
            if detail:
                refinements.append((item, detail))

        if len(conflicts) == 1 and not refinements:
            target = conflicts[0]
            operations.append(
                {
                    "action": "conflict",
                    "field_name": field_name,
                    "fact_text": fact_text,
                    "existing_fact_id": target["id"],
                }
            )
            working.append(
                {
                    "id": None,
                    "field_name": field_name,
                    "fact_text": fact_text,
                    "status": STATUS_CONFLICT,
                    "operation_index": None,
                }
            )
            continue
        if len(refinements) == 1 and not conflicts:
            target, detail = refinements[0]
            merged = _append_preserved_detail(target["fact_text"], detail)
            if target.get("id") is None:
                target["fact_text"] = merged
                operations[target["operation_index"]]["fact_text"] = merged
            else:
                operations.append(
                    {
                        "action": "refine",
                        "field_name": field_name,
                        "fact_text": merged,
                        "existing_fact_id": target["id"],
                    }
                )
                target["fact_text"] = merged
            continue

        novel_sentences = _fact_sentences(fact_text)
        for item in same_field:
            known = {_normalize_fact_text(sentence) for sentence in _fact_sentences(item["fact_text"])}
            novel_sentences = [
                sentence
                for sentence in novel_sentences
                if _normalize_fact_text(sentence) not in known
            ]
        novel = " ".join(novel_sentences).strip()
        if not novel:
            continue

        pending = {
            "id": None,
            "field_name": field_name,
            "fact_text": novel,
            "status": STATUS_ACTIVE,
            "operation_index": len(operations),
        }
        operations.append(
            {
                "action": "add",
                "field_name": field_name,
                "fact_text": novel,
                "existing_fact_id": None,
            }
        )
        working.append(pending)

    return operations


def _fact_compare_trace_path() -> str:
    return os.getenv("FACT_COMPARE_TRACE_PATH", "").strip()


def _append_fact_compare_trace(payload: dict) -> None:
    """진단 파일 기록 실패는 분석과 저장 트랜잭션에 전달하지 않는다."""
    path = _fact_compare_trace_path()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


def _load_fact_rows_for_trace(character_id: int) -> list[dict]:
    sql = """
        SELECT id, field_name, fact_text, source_document_ids, status, related_fact_id
        FROM character_persona_facts
        WHERE character_id = %s
        ORDER BY id
        -- fact_compare_trace
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (character_id,))
            rows = cur.fetchall()
    return [
        {
            "id": row[0],
            "field_name": row[1],
            "fact_text": row[2] or "",
            "source_document_ids": list(row[3] or []),
            "status": row[4],
            "related_fact_id": row[5],
        }
        for row in rows
    ]


def _fact_row_signature(row: dict) -> tuple:
    sources = tuple(int(source_id) for source_id in (row.get("source_document_ids") or []))
    return (
        row.get("fact_text") or "",
        row.get("status") or "",
        sources,
        row.get("related_fact_id"),
    )


def _affected_fact_rows(
    before_rows: list[dict],
    after_rows: list[dict],
    document_id: int,
) -> list[dict]:
    """이번 문서 처리로 새로 생겼거나 내용이 바뀐 행만 고른다."""
    before = {row["id"]: row for row in before_rows}
    affected = []
    for row in after_rows:
        sources = [int(source_id) for source_id in (row.get("source_document_ids") or [])]
        previous = before.get(row["id"])
        if previous is None:
            if document_id in sources:
                affected.append(row)
            continue
        if _fact_row_signature(previous) != _fact_row_signature(row):
            affected.append(row)
    return affected


def _write_stored_fact_compare_trace(
    character_id: int,
    document_id: int,
    before_rows: list[dict],
) -> None:
    if not _fact_compare_trace_path():
        return
    try:
        after_rows = _load_fact_rows_for_trace(character_id)
        _append_fact_compare_trace(
            {
                "stage": "stored",
                "character_id": character_id,
                "document_id": document_id,
                "items": _affected_fact_rows(before_rows, after_rows, document_id),
            }
        )
    except Exception:
        return


def process_document_facts(
    character_id: int,
    character_name: str,
    document: dict,
) -> None:
    """
    원문에서 사실 후보를 추출한 뒤 코드로 기존 사실과 비교한다.
    사실 반영과 처리 완료 기록은 같은 트랜잭션에서 커밋한다.
    """
    document_id = document["id"]
    existing_facts = list_content_facts(character_id)
    candidates = extract_document_facts(
        character_name=character_name,
        document_title=document.get("title"),
        document_url=document.get("url"),
        document_body=document["raw_text"],
    )
    _append_fact_compare_trace(
        {
            "stage": "candidates",
            "character_id": character_id,
            "document_id": document_id,
            "items": [
                {
                    "field_name": item.get("field_name"),
                    "fact_text": item.get("fact_text"),
                }
                for item in candidates
            ],
        }
    )
    operations = classify_fact_candidates(existing_facts, candidates)
    _append_fact_compare_trace(
        {
            "stage": "operations",
            "character_id": character_id,
            "document_id": document_id,
            "items": [
                {
                    "action": item.get("action"),
                    "field_name": item.get("field_name"),
                    "fact_text": item.get("fact_text"),
                    "existing_fact_id": item.get("existing_fact_id"),
                }
                for item in operations
            ],
        }
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            apply_fact_operations(cur, character_id, document_id, operations)
            mark_document_processed(cur, character_id, document_id)

    _write_stored_fact_compare_trace(character_id, document_id, existing_facts)


def adopt_identical_reextracted_document(
    character_id: int,
    document_id: int,
) -> bool:
    """같은 URL·같은 본문이 이미 처리됐으면 Gemini를 호출하지 않고 완료로 표시한다."""
    twin_id = find_processed_identical_document(character_id, document_id)
    if twin_id is None:
        return False

    with get_connection() as conn:
        with conn.cursor() as cur:
            mark_document_processed(cur, character_id, document_id)
    return True


@contextmanager
def character_analysis_lock(character_id: int) -> Iterator[None]:
    """분석이 끝나는 동안 캐릭터별 세션 advisory lock을 유지한다."""
    lock_key2 = int(character_id) & 0x7FFFFFFF
    conn = None
    acquired = False
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                (ANALYZE_LOCK_NAMESPACE, lock_key2),
            )
            acquired = bool(cur.fetchone()[0])
        if not acquired:
            raise AnalysisInProgressError("analysis_in_progress")
        yield
    finally:
        if conn is not None:
            try:
                if acquired:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT pg_advisory_unlock(%s, %s)",
                            (ANALYZE_LOCK_NAMESPACE, lock_key2),
                        )
            except Exception:
                pass
            conn.close()


def _run_sequential_persona_analysis_unlocked(
    character_id: int,
    user_id: int,
) -> dict:
    if is_persona_confirmed(character_id):
        raise PermissionError("confirmed_persona")

    character_name = get_character_name(character_id, user_id)
    if character_name is None:
        raise LookupError("character_not_found")

    documents = list_source_documents(character_id, user_id)
    if not documents:
        raise LookupError("no_source_documents")

    processed_ids = get_processed_document_ids(character_id)
    skipped = 0
    newly_processed = 0

    for document in documents:
        if document["id"] in processed_ids:
            skipped += 1
            continue

        if adopt_identical_reextracted_document(character_id, document["id"]):
            skipped += 1
            processed_ids.add(document["id"])
            continue

        process_document_facts(character_id, character_name, document)
        newly_processed += 1
        processed_ids.add(document["id"])

    remaining = [
        document["id"]
        for document in documents
        if document["id"] not in processed_ids
    ]
    if remaining:
        raise RuntimeError("incomplete_document_processing")

    facts = list_content_facts(character_id)
    persona = synthesize_persona_from_facts(
        character_name=character_name,
        facts=facts,
    )
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    persona_id = save_unconfirmed_persona(character_id, persona, model_name)
    if persona_id is None:
        raise PermissionError("confirmed_persona")

    return {
        "character_id": character_id,
        "persona_id": persona_id,
        "persona": persona,
        "is_confirmed": False,
        "documents_total": len(documents),
        "documents_skipped": skipped,
        "documents_processed": newly_processed,
        "facts_count": len(facts),
        "message": "캐릭터 분석 및 임시 페르소나 저장이 완료되었습니다.",
    }


def run_sequential_persona_analysis(character_id: int, user_id: int) -> dict:
    with character_analysis_lock(character_id):
        return _run_sequential_persona_analysis_unlocked(character_id, user_id)
