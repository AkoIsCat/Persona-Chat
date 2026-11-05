from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_current_user_id
from app.database import get_connection
from app.services.tavily_service import extract_urls

router = APIRouter(
    prefix="/characters",
    tags=["character sources"],
)

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
        response = extract_urls(urls)
        print("Tavily 응답:", response)
        results = response.get("results", [])

        if not results:
            raise HTTPException(
                status_code=502,
                detail="Tavily에서 추출된 자료를 반환하지 않았습니다.",
            )

        saved_count = 0

        with get_connection() as conn:
            with conn.cursor() as cur:
                for item in results:
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

        if saved_count == 0:
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
