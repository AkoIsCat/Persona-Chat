
from app.database import get_connection
from app.services.chunk_service import split_text

document_id = 3

try:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT raw_text
                FROM character_source_documents
                WHERE id = %s
                """,
                (document_id,),
            )

            row = cur.fetchone()

            if not row:
                print("해당 문서를 찾을 수 없습니다.")
            else:
                text = row[0]

                chunks = split_text(
                    text,
                    chunk_size=1000,
                    overlap=200,
                )

                print(f"전체 원문 길이: {len(text):,}자")
                print(f"전체 청크 개수: {len(chunks)}개")

                # 앞쪽 3개와 마지막 청크만 출력
                indexes = list(range(min(3, len(chunks))))

                if len(chunks) > 3:
                    indexes.append(len(chunks) - 1)

                for index in indexes:
                    chunk = chunks[index]

                    print(f"\n[청크 {index}]")
                    print(f"길이: {len(chunk)}자")
                    print(chunk[:500])

except Exception as e:
    print("테스트 실패:", e)
