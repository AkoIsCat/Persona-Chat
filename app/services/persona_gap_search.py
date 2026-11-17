from urllib.parse import unquote, urlparse

from app.core.perf import perf_span
from app.database import get_connection
from app.services.character_analysis import (
    PERSONA_FIELDS,
    _gemini_client,
    _parse_json_object,
)
from app.services.chunk_service import save_document_chunks
from app.services.search_verification import (
    search_document_title,
    select_verified_search_hits,
)
from app.services.tavily_service import search_supplemental_material

MAX_GAP_SEARCHES = 3
SEARCHABLE_FIELDS = [field for field in PERSONA_FIELDS if field != "summary"]

FIELD_FOCUS = {
    "basic_info": "기본 정보 출신 소속 나이 역할",
    "personality": "성격과 성향",
    "values_and_beliefs": "가치관과 신념",
    "goals": "목표와 목적",
    "background": "배경과 과거",
    "worldview": "세계관과 작품 배경",
    "speech_style": "말투와 어휘",
    "behavior_patterns": "행동 패턴과 습관",
}

FIELD_CRITERIA = {
    "basic_info": "캐릭터의 신원과 기본 설정이 구체적인가",
    "personality": "성격, 성향, 감정 표현, 행동 특성이 구체적인가",
    "values_and_beliefs": "신념, 중요하게 생각하는 가치, 판단 기준이 구체적인가",
    "goals": "이루려는 목적이나 행동 동기가 구체적인가",
    "background": "과거 사건, 성장 과정, 주요 이력이 구체적인가",
    "worldview": "작품의 세계 구조, 조직, 세력, 배경 설정이 구체적인가",
    "speech_style": "실제 대사나 말투 특징이 구체적인가",
    "behavior_patterns": "반복되는 행동이나 반응 특징이 구체적인가",
}


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def _url_clues(documents: list[dict]) -> str:
    clues = []
    for document in documents:
        path = unquote(urlparse((document.get("url") or "")).path)
        for part in path.split("/"):
            part = part.strip()
            if not part or part in {"w", "wiki"} or part in clues:
                continue
            clues.append(part)
    return " ".join(clues)[:200]


def group_insufficient_fields(fields: list[str]) -> list[list[str]]:
    """검색 가능한 부족 항목을 최대 3개 검색 묶음으로 나눈다."""

    ordered = [field for field in SEARCHABLE_FIELDS if field in fields]
    if not ordered:
        return []
    if len(ordered) <= MAX_GAP_SEARCHES:
        return [[field] for field in ordered]

    groups: list[list[str]] = [[] for _ in range(MAX_GAP_SEARCHES)]
    for index, field in enumerate(ordered):
        groups[index * MAX_GAP_SEARCHES // len(ordered)].append(field)
    return [group for group in groups if group]


def build_gap_search_plans(
    character_name: str,
    documents: list[dict],
    fields: list[str],
) -> list[dict]:
    clue = _url_clues(documents)
    plans = []
    for group in group_insufficient_fields(fields):
        focus = " ".join(FIELD_FOCUS[field] for field in group)
        parts = [character_name.strip(), clue, focus]
        query = " ".join(part for part in parts if part).strip()
        plans.append({"fields": group, "query": query})
    return plans[:MAX_GAP_SEARCHES]


def parse_insufficient_fields(payload: dict) -> tuple[list[str], dict[str, str]]:
    raw_fields = payload.get("insufficient_fields")
    if not isinstance(raw_fields, list):
        raise ValueError("insufficient_fields가 없습니다.")

    reasons = payload.get("reason")
    if not isinstance(reasons, dict):
        reasons = {}

    fields = []
    for field in raw_fields:
        if field in SEARCHABLE_FIELDS and field not in fields:
            fields.append(field)

    cleaned = {}
    for field in fields:
        reason = reasons.get(field)
        cleaned[field] = reason.strip() if isinstance(reason, str) else ""
    return fields, cleaned


def _sufficiency_prompt(character_name: str, facts: list[dict]) -> str:
    lines = []
    for fact in facts:
        field_name = fact.get("field_name")
        if field_name not in PERSONA_FIELDS:
            continue
        status = fact.get("status") or "active"
        text = (fact.get("fact_text") or "").strip()
        if not text:
            continue
        lines.append(f"- 항목: {field_name}, 상태: {status}, 내용: {text}")
    fact_text = "\n".join(lines) if lines else "누적된 사실이 없습니다."

    return f"""
당신은 캐릭터 페르소나 항목의 정보 충실도를 판단하는 검토자입니다.

분석 대상 캐릭터 이름: {character_name}

아래 누적된 사실만 보고, 각 항목을 설명하기에 정보가 충분한지 판단하세요.
항목에 문장이 있다는 이유만으로 충분하다고 보지 마세요.
짧은 내용이어도 그 항목의 핵심이 확인되면 충분합니다.
일부만 확인되어 보충이 필요하거나 전혀 확인되지 않으면 부족입니다.
자료에 없는 사실을 추측해서 부족 여부를 정하지 마세요.
summary는 다른 항목의 종합이므로 insufficient_fields에 넣지 마세요.

판단 대상은 다음 8개입니다.
{chr(10).join(f"- {field}" for field in SEARCHABLE_FIELDS)}

[출력 형식]
반드시 JSON 객체만 출력하세요.
마크다운 코드 블록이나 추가 설명은 출력하지 마세요.

{{
  "insufficient_fields": [],
  "reason": {{}}
}}

[누적된 사실]
{fact_text}
"""


def assess_insufficient_fields(
    character_name: str,
    facts: list[dict],
) -> tuple[list[str], dict[str, str]]:
    client, model_name = _gemini_client()
    with perf_span("Gemini 정보 충실도 판단"):
        response = client.interactions.create(
            model=model_name,
            input=_sufficiency_prompt(character_name, facts),
        )
    payload = _parse_json_object(
        getattr(response, "output_text", None) or "",
        "정보 충실도 판단 결과를 JSON으로 변환하지 못했습니다.",
    )
    return parse_insufficient_fields(payload)


def _log_sufficiency(fields: list[str], reasons: dict[str, str]) -> None:
    selected = set(fields)
    for field in SEARCHABLE_FIELDS:
        decision = "부족" if field in selected else "충분"
        reason = reasons.get(field) or ""
        print(f"정보 충실도 | 항목={field} | 판정={decision} | 사유={reason}")
    print("정보 충실도 | 항목=summary | 판정=검색제외 | 사유=다른 항목의 종합")


def _excerpt(text: str, limit: int = 2000) -> str:
    compacted = " ".join((text or "").split())
    if len(compacted) <= limit:
        return compacted
    return compacted[:limit]


def _field_relevance_prompt(
    character_name: str,
    fields: list[str],
    candidates: list[dict],
) -> str:
    criteria = "\n".join(
        f"- {field}: {FIELD_CRITERIA[field]}" for field in fields
    )
    blocks = []
    for index, item in enumerate(candidates):
        blocks.append(
            "\n".join(
                [
                    f"[검색 결과 {index}]",
                    f"id: {index}",
                    f"제목: {item.get('title') or ''}",
                    f"검색 URL: {item.get('search_url') or ''}",
                    "본문 발췌:",
                    _excerpt(item.get("raw_content") or ""),
                ]
            )
        )
    return f"""
당신은 검색 문서가 캐릭터의 부족한 페르소나 항목을 실제로 보충하는지 판단하는 검토자입니다.

분석 대상 캐릭터 이름: {character_name}

이 문서가 같은 인물인지는 이미 확인되었습니다. 이번에는 본문에 아래 부족 항목을 보충할 구체적인 근거가 있는지만 판단하세요.
항목 이름이나 검색어가 본문에 나온다는 이유만으로 통과시키지 마세요.
출생지, 학력, 데뷔 이력만 있는 문서는 성격, 가치관, 목표, 세계관, 말투를 보충하지 못합니다.
한 문서가 여러 항목의 근거를 가지면 해당 항목을 모두 fields에 넣으세요.
근거가 있는 항목이 없으면 fields는 빈 배열입니다.
fields에 넣은 항목마다 reason에 본문의 어떤 내용이 근거인지 적으세요.

[부족 항목]
{criteria}

[출력 형식]
반드시 JSON 객체만 출력하세요.
마크다운 코드 블록이나 추가 설명은 출력하지 마세요.
모든 검색 결과 id를 한 번씩 포함하세요.

{{
  "results": [
    {{"id": 0, "fields": [], "reason": "출생지와 데뷔 이력만 있어 부족한 항목의 근거가 없다."}}
  ]
}}

[검색 결과]
{chr(10).join(blocks)}
"""


def parse_field_relevance(
    payload: dict,
    requested_fields: list[str],
) -> dict[int, tuple[list[str], str]]:
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise ValueError("results가 없습니다.")

    allowed = [field for field in SEARCHABLE_FIELDS if field in requested_fields]
    parsed = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        raw_fields = row.get("fields")
        if not isinstance(raw_fields, list):
            raw_fields = []
        matched = []
        for field in raw_fields:
            if field in allowed and field not in matched:
                matched.append(field)
        reason = row.get("reason")
        reason = reason.strip() if isinstance(reason, str) else ""
        parsed[index] = (matched, reason)
    return parsed


def select_field_relevant_hits(
    character_name: str,
    fields: list[str],
    candidates: list[dict],
) -> list[dict]:
    """동일 인물로 확인된 문서 중 부족 항목의 근거가 있는 것만 남긴다."""

    if not candidates or not fields:
        return []

    try:
        client, model_name = _gemini_client()
        with perf_span("Gemini 항목 적합성 검증"):
            response = client.interactions.create(
                model=model_name,
                input=_field_relevance_prompt(character_name, fields, candidates),
            )
        payload = _parse_json_object(
            getattr(response, "output_text", None) or "",
            "항목 적합성 검증 결과를 JSON으로 변환하지 못했습니다.",
        )
        decisions = parse_field_relevance(payload, fields)
    except Exception:
        for candidate in candidates:
            print(
                "항목 적합성 | 판정=제외 | "
                f"검색URL={candidate.get('search_url') or ''} | "
                "항목= | 사유=항목 적합성 검증에 실패해 제외"
            )
        print(f"항목 적합성 검증 | 후보={len(candidates)} | 통과=0 | 제외={len(candidates)}")
        return []

    kept = []
    for index, candidate in enumerate(candidates):
        matched, reason = decisions.get(index, ([], ""))
        if matched and reason:
            print(
                "항목 적합성 | 판정=통과 | "
                f"검색URL={candidate.get('search_url') or ''} | "
                f"항목={','.join(matched)} | 사유={reason}"
            )
            kept.append(candidate)
            continue
        print(
            "항목 적합성 | 판정=제외 | "
            f"검색URL={candidate.get('search_url') or ''} | "
            "항목= | 사유="
            + (reason or "부족한 항목을 보충할 구체적 근거가 없음")
        )
    print(
        "항목 적합성 검증 | "
        f"후보={len(candidates)} | 통과={len(kept)} | 제외={len(candidates) - len(kept)}"
    )
    return kept


def _anchor_source(character_id: int, user_id: int) -> tuple[int, str] | None:
    sql = """
        SELECT cu.id, cu.url
        FROM character_urls cu
        JOIN characters c
            ON c.id = cu.character_id
        WHERE c.id = %s
          AND c.user_id = %s
        ORDER BY cu.display_order
        LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (character_id, user_id))
            row = cur.fetchone()
    if not row:
        return None
    return int(row[0]), row[1]


def _existing_bodies(character_id: int) -> set[str]:
    sql = """
        SELECT csd.raw_text
        FROM character_source_documents csd
        JOIN character_urls cu
            ON cu.id = csd.character_url_id
        WHERE cu.character_id = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (character_id,))
            rows = cur.fetchall()
    return {_normalize_text(row[0]) for row in rows if row and row[0]}


def save_supplemental_document(
    anchor_url_id: int,
    anchor_url: str,
    title: str,
    search_url: str,
    raw_text: str,
    known_bodies: set[str],
) -> dict | None:
    """검증된 검색 본문을 기존 문서 테이블에 저장한다. 본문에는 검색 URL을 넣지 않는다."""

    body = (raw_text or "").strip()
    normalized = _normalize_text(body)
    if not body or normalized in known_bodies:
        return None

    stored_title = search_document_title(title, search_url)
    sql = """
        INSERT INTO character_source_documents
            (character_url_id, title, raw_text, fetched_at)
        VALUES (%s, %s, %s, NOW())
        RETURNING id
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (anchor_url_id, stored_title, body))
            row = cur.fetchone()
    if not row:
        return None
    known_bodies.add(normalized)
    return {
        "id": int(row[0]),
        "display_order": 1,
        "url": anchor_url,
        "title": stored_title,
        "raw_text": body,
    }


def supplement_persona_from_gaps(
    character_id: int,
    user_id: int,
    character_name: str,
    documents: list[dict],
) -> int:
    """부족한 항목만 검색해 검증된 문서를 사실에 반영한다. 페르소나는 저장하지 않는다."""

    from app.services.persona_facts_service import (
        list_content_facts,
        process_document_facts,
    )

    facts = list_content_facts(character_id)
    try:
        fields, reasons = assess_insufficient_fields(character_name, facts)
    except Exception:
        print("정보 충실도 판단 실패 | 추가 검색을 생략하고 기존 사실로 진행합니다.")
        return 0

    _log_sufficiency(fields, reasons)
    if not fields:
        print("추가 검색 대상 없음 | Tavily 호출=0")
        return 0

    print("추가 검색 대상 | 항목=" + ",".join(fields))
    plans = build_gap_search_plans(character_name, documents, fields)
    hits = []
    seen_bodies = set()
    calls = 0
    for plan in plans:
        if calls >= MAX_GAP_SEARCHES:
            break
        calls += 1
        print(
            "추가 검색어 | "
            f"항목={','.join(plan['fields'])} | "
            f"검색어={plan['query']}"
        )
        try:
            found = search_supplemental_material(plan["query"])
        except Exception:
            print(f"Tavily 추가 검색 실패 | 호출={calls}")
            continue
        print(f"Tavily 검색 결과 | 호출={calls} | 개수={len(found)}")
        for hit in found:
            body = hit.get("raw_content") or ""
            if not body or body in seen_bodies:
                continue
            seen_bodies.add(body)
            hits.append(hit)
    print(f"Tavily 호출 횟수 | {calls}")

    if not hits:
        print("검색 결과 검증 | 후보=0 | 통과=0 | 제외=0")
        return 0

    anchor = _anchor_source(character_id, user_id)
    if anchor is None:
        print("추가 문서 저장 실패 | 연결할 등록 URL이 없습니다.")
        return 0
    anchor_url_id, anchor_url = anchor

    references = [
        {
            "url": document.get("url") or "",
            "title": document.get("title") or "",
            "raw_content": document.get("raw_text") or "",
        }
        for document in documents
    ]
    candidates = [
        {
            "registered_url": anchor_url,
            "search_url": hit.get("url") or "",
            "title": hit.get("title") or "",
            "raw_content": hit.get("raw_content") or "",
        }
        for hit in hits
    ]
    try:
        verified = select_verified_search_hits(references, candidates)
    except Exception:
        print("검색 결과 검증 실패 | 추가 문서를 저장하지 않습니다.")
        return 0

    print(
        "검색 결과 검증 | "
        f"후보={len(candidates)} | "
        f"통과={len(verified)} | "
        f"제외={len(candidates) - len(verified)}"
    )
    if not verified:
        print("항목 적합성 검증 | 후보=0 | 통과=0 | 제외=0")
        return 0

    try:
        verified = select_field_relevant_hits(character_name, fields, verified)
    except Exception:
        print("항목 적합성 검증 실패 | 추가 문서를 저장하지 않습니다.")
        return 0
    if not verified:
        print("검증된 자료가 없어 추가 검색을 종료합니다.")
        return 0

    try:
        known_bodies = _existing_bodies(character_id)
    except Exception:
        print("추가 문서 저장 실패 | 기존 본문을 확인하지 못했습니다.")
        return 0

    saved = 0
    for item in verified:
        try:
            document = save_supplemental_document(
                anchor_url_id,
                anchor_url,
                item.get("title") or "",
                item.get("search_url") or "",
                item.get("raw_content") or "",
                known_bodies,
            )
        except Exception:
            print("추가 문서 저장 실패")
            continue
        if document is None:
            print("추가 문서 저장 생략 | 동일한 본문이 이미 있습니다.")
            continue
        if "검색URL:" in document["raw_text"]:
            print(f"추가 문서 본문 확인 | document_id={document['id']} | 검색URL포함=예")
        saved += 1

        try:
            save_document_chunks(document["id"])
        except Exception:
            print(f"청크 생성 실패 | document_id={document['id']}")

        try:
            before = len(list_content_facts(character_id))
            process_document_facts(character_id, character_name, document)
            after = len(list_content_facts(character_id))
            print(
                "추가 사실 추출 | "
                f"document_id={document['id']} | 결과=완료 | 사실={before}->{after}"
            )
        except Exception:
            print(f"추가 사실 추출 | document_id={document['id']} | 결과=실패")

    print(f"추가 문서 저장 | {saved}")
    return saved
