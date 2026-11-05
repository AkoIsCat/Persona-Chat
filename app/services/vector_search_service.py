
from app.database import get_connection
from app.services.embedding_service import create_embedding


def search_similar_chunks(
    character_id: int,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """
    사용자 질문과 의미적으로 유사한 캐릭터 원문 청크를 검색한다.
    """

    # 1. 질문을 벡터로 변환
    query_embedding = create_embedding(query)

    # pgvector에서 사용할 벡터 문자열 생성
    embedding_vector = "[" + ",".join(
        map(str, query_embedding)
    ) + "]"

    # 2. 캐릭터별 유사도 검색
    sql = """
        SELECT
            cc.id,
            cc.document_id,
            cc.chunk_index,
            cc.content,
            cc.embedding <=> %s::vector AS distance
        FROM character_chunks cc
        JOIN character_source_documents csd
            ON cc.document_id = csd.id
        JOIN character_urls cu
            ON csd.character_url_id = cu.id
        WHERE cu.character_id = %s
          AND cc.embedding IS NOT NULL
        ORDER BY cc.embedding <=> %s::vector
        LIMIT %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    embedding_vector,
                    character_id,
                    embedding_vector,
                    top_k,
                ),
            )

            rows = cur.fetchall()

    # 3. 검색 결과 반환
    return [
        {
            "id": row[0],
            "document_id": row[1],
            "chunk_index": row[2],
            "content": row[3],
            "distance": float(row[4]),
            "similarity": 1 - float(row[4]),
        }
        for row in rows
    ]
