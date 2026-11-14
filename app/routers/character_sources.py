from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_current_user_id
from app.database import get_connection
from app.services.chunk_service import save_document_chunks
from app.services.tavily_service import extract_urls, search_character_material

router = APIRouter(
    prefix="/characters",
    tags=["character sources"],
)


def _normalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    path = unquote(parsed.path or "")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def _registered_url(source_url: str, urls: list[str]) -> str | None:
    normalized = _normalize_url(source_url)
    for url in urls:
        if url == source_url or _normalize_url(url) == normalized:
            return url
    return None


def _materials_from_extract(urls: list[str], response: dict) -> list[dict]:
    materials = []
    seen_text = set()

    for item in response.get("results") or []:
        if not isinstance(item, dict):
            continue

        raw_text = item.get("raw_content") or ""
        if not isinstance(raw_text, str) or not raw_text.strip():
            continue

        source_url = _registered_url(item.get("url") or "", urls)
        if source_url is None:
            continue

        normalized = _normalize_text(raw_text)
        if normalized in seen_text:
            continue

        seen_text.add(normalized)
        title = item.get("title")
        materials.append(
            {
                "url": source_url,
                "title": title if isinstance(title, str) and title.strip() else source_url,
                "raw_content": raw_text,
                "from_search": False,
            }
        )

    return materials


def _search_fallback(urls: list[str], materials: list[dict]) -> list[dict]:
    covered = {_normalize_url(item["url"]) for item in materials}
    seen_text = {_normalize_text(item["raw_content"]) for item in materials}
    fallback = []

    for url in urls:
        if _normalize_url(url) in covered:
            continue

        try:
            hits = search_character_material(url)
        except Exception:
            print("Tavily Search 실패")
            continue

        for hit in hits:
            raw_text = hit.get("raw_content") or ""
            normalized = _normalize_text(raw_text)
            if not normalized or normalized in seen_text:
                continue

            seen_text.add(normalized)
            title = hit.get("title") or url
            fallback.append(
                {
                    "url": url,
                    "title": title,
                    "raw_content": raw_text,
                    "from_search": True,
                }
            )

    return fallback

@router.post("/{character_id}/extract", status_code=201)
def extract_character_sources(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # 로그인한 사용자의 캐릭터인지 확인하고 URL 조회
                cur.execute(
                    """
                    SELECT cu.id, cu.url
                    FROM characters c
                    JOIN character_urls cu
                      ON cu.character_id = c.id
                    WHERE c.id = %s AND c.user_id = %s
                    ORDER BY cu.display_order
                    """,
                    (character_id, user_id),
                )
                rows = cur.fetchall()

                if not rows:
                    raise HTTPException(
                        status_code=404,
                        detail="캐릭터를 찾을 수 없거나 등록된 URL이 없습니다.",
                    )

                url_ids = [row[0] for row in rows]
                urls = [row[1] for row in rows]

                # DB 연결을 유지한 채 외부 API를 호출하지 않도록
                # 조회한 값을 저장하고 연결을 종료
        print("Tavily 요청 URL:", urls)
        try:
            response = extract_urls(urls)
        except Exception:
            print("Tavily Extract 실패")
            response = {"results": [], "failed_results": [{"url": url} for url in urls]}
        else:
            print("Tavily 응답:", response)

        materials = _materials_from_extract(urls, response)
        materials.extend(_search_fallback(urls, materials))

        if not materials:
            raise HTTPException(
                status_code=502,
                detail="Tavily에서 추출된 자료를 반환하지 않았습니다.",
            )

        saved_count = 0
        search_document_ids = []

        with get_connection() as conn:
            with conn.cursor() as cur:
                for item in materials:
                    source_url = item.get("url", "")
                    title = item.get("title") or source_url
                    raw_text = item.get("raw_content") or ""

                    if not raw_text.strip():
                        continue

                    # 결과 URL과 일치하는 등록 URL을 찾아 연결
                    cur.execute(
                        """
                        SELECT id
                        FROM character_urls
                        WHERE character_id = %s AND url = %s
                        """,
                        (character_id, source_url),
                    )
                    matched = cur.fetchone()

                    if not matched:
                        continue

                    # 같은 URL에 같은 본문이 있으면 새 문서를 만들지 않는다.
                    cur.execute(
                        """
                        SELECT id
                        FROM character_source_documents
                        WHERE character_url_id = %s
                          AND raw_text = %s
                        LIMIT 1
                        """,
                        (matched[0], raw_text),
                    )
                    if cur.fetchone():
                        continue

                    cur.execute(
                        """
                        INSERT INTO character_source_documents
                            (character_url_id, title, raw_text, fetched_at)
                        VALUES (%s, %s, %s, NOW())
                        RETURNING id
                        """,
                        (matched[0], title, raw_text),
                    )

                    saved_count += 1
                    if item.get("from_search"):
                        inserted = cur.fetchone()
                        if inserted:
                            search_document_ids.append(inserted[0])

        for document_id in search_document_ids:
            save_document_chunks(document_id)

        if saved_count == 0:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM character_source_documents csd
                        JOIN character_urls cu
                          ON cu.id = csd.character_url_id
                        WHERE cu.character_id = %s
                        """,
                        (character_id,),
                    )
                    existing_count = cur.fetchone()[0]

            if existing_count > 0:
                return {
                    "character_id": character_id,
                    "saved_count": 0,
                    "message": "동일한 원작 자료가 이미 저장되어 있어 새로 저장하지 않았습니다.",
                }

            raise HTTPException(
                status_code=502,
                detail="추출된 텍스트를 저장하지 못했습니다.",
            )

        return {
            "character_id": character_id,
            "saved_count": saved_count,
            "message": "원작 자료를 추출하고 저장했습니다.",
        }

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="원작 자료 추출 중 오류가 발생했습니다. 서버 로그를 확인해 주세요.",
        )
