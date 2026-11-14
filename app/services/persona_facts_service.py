"""
Sequential multi-URL persona fact accumulation.

Uses only character_persona_facts (no per-URL analysis table).
Document completion is recorded as status='document_processed' sentinel rows
so empty extractions are still distinguishable from failures.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from app.database import get_connection
from app.services.character_analysis import (
    PERSONA_FIELDS,
    extract_document_fact_operations,
    synthesize_persona_from_facts,
)

DOCUMENT_PROCESSED_FIELD = "__document_processed__"
STATUS_ACTIVE = "active"
STATUS_CONFLICT = "conflict"
STATUS_DOCUMENT_PROCESSED = "document_processed"

# PostgreSQL advisory-lock namespace for per-character analyze jobs.
ANALYZE_LOCK_NAMESPACE = 740_391


class AnalysisInProgressError(RuntimeError):
    """Raised when another analyze job already holds the character lock."""


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
    """Return source documents ordered by character_urls.display_order."""
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
    """Active and conflict facts used for accumulation / synthesis."""
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
            cur.execute(
                sql,
                (character_id, STATUS_ACTIVE, STATUS_CONFLICT),
            )
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


def is_document_processed(character_id: int, document_id: int) -> bool:
    return document_id in get_processed_document_ids(character_id)


def find_processed_identical_document(
    character_id: int,
    document_id: int,
) -> int | None:
    """
    If a prior document under the same URL has identical raw_text and was
    already processed, return that older document id.

    Handles re-extract creating a new document_id for the same content
    without adding a second analysis table.
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
    """Record successful document processing even when zero facts were extracted."""
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


def fact_belongs_to_character(
    cur,
    fact_id: int,
    character_id: int,
) -> bool:
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


def append_source_document_id(
    cur,
    fact_id: int,
    document_id: int,
    character_id: int,
) -> bool:
    """
    Append document_id to an existing fact's sources.
    Returns True only when a same-character row was updated.
    """
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
    return cur.rowcount > 0


def apply_fact_operations(
    cur,
    character_id: int,
    document_id: int,
    operations: list[dict[str, Any]],
) -> None:
    """
    Apply Gemini fact operations for one document.

    - add: insert new active fact
    - refine: update existing fact_text and append source
    - duplicate: append source only (no new row)
    - conflict: insert separate conflict row; do not overwrite existing

    existing_fact_id must belong to the same character_id; cross-character
    references are ignored and never mutate another character's facts.
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
            continue

        if action == "duplicate":
            if existing_fact_id is None:
                continue
            fact_id = int(existing_fact_id)
            if not fact_belongs_to_character(cur, fact_id, character_id):
                continue
            append_source_document_id(cur, fact_id, document_id, character_id)
            continue

        if action == "refine":
            if existing_fact_id is None or not fact_text:
                continue
            fact_id = int(existing_fact_id)
            if not fact_belongs_to_character(cur, fact_id, character_id):
                continue
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
    persona: dict[str, str],
    model_name: str,
) -> int | None:
    """
    Upsert final persona only when is_confirmed = FALSE.
    Returns persona id, or None if confirmed persona blocked the update.
    """
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


def process_document_facts(
    character_id: int,
    character_name: str,
    document: dict,
) -> None:
    """
    Analyze one document against accumulated facts and persist results.
    Commits only after operations + processed marker succeed together.
    """
    document_id = document["id"]
    existing_facts = list_content_facts(character_id)
    operations = extract_document_fact_operations(
        character_name=character_name,
        raw_text=document["raw_text"],
        existing_facts=existing_facts,
        title=document.get("title"),
        url=document.get("url"),
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            apply_fact_operations(
                cur,
                character_id,
                document_id,
                operations,
            )
            mark_document_processed(cur, character_id, document_id)


def adopt_identical_reextracted_document(
    character_id: int,
    document_id: int,
) -> bool:
    """
    If identical content was already analyzed under the same URL, mark this
    new document_id processed without calling Gemini. Returns True when adopted.
    """
    twin_id = find_processed_identical_document(character_id, document_id)
    if twin_id is None:
        return False

    with get_connection() as conn:
        with conn.cursor() as cur:
            mark_document_processed(cur, character_id, document_id)
    return True


@contextmanager
def character_analysis_lock(character_id: int) -> Iterator[None]:
    """
    Hold a session-level Postgres advisory lock for the whole analyze job.
    Prevents concurrent analyze API calls for the same character.
    """
    lock_key2 = int(character_id) & 0x7FFFFFFF
    conn = get_connection()
    acquired = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                (ANALYZE_LOCK_NAMESPACE, lock_key2),
            )
            acquired = bool(cur.fetchone()[0])
        if not acquired:
            raise AnalysisInProgressError("analysis_in_progress")

        # Keep the connection (and thus the session lock) open for the job.
        yield
    finally:
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
    """
    Process source documents in display_order, skip already-done docs,
    synthesize character_personas only after every document succeeds.
    """
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

        # Re-extract may insert a new document_id with identical raw_text.
        if adopt_identical_reextracted_document(character_id, document["id"]):
            skipped += 1
            processed_ids.add(document["id"])
            continue

        process_document_facts(character_id, character_name, document)
        newly_processed += 1
        processed_ids.add(document["id"])

    # All documents must be processed before final persona write.
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
    persona_id = save_unconfirmed_persona(
        character_id,
        persona,
        model_name,
    )
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


def run_sequential_persona_analysis(
    character_id: int,
    user_id: int,
) -> dict:
    """Run sequential analysis under a per-character advisory lock."""
    with character_analysis_lock(character_id):
        return _run_sequential_persona_analysis_unlocked(character_id, user_id)
