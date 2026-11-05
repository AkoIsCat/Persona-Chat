import re

from app.database import get_connection
from app.services.embedding_service import create_embedding


def split_text(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 200,
) -> list[str]:
    if not text or not text.strip():
        return []

    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("청크 크기와 overlap 설정을 확인해 주세요.")

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    units = []

    for line in lines:
        # 표 행은 문장 단위로 자르지 않고 하나의 단위로 처리
        if "|" in line:
            units.append(line)
            continue

        # 일반 문장은 문장 부호를 기준으로 분리
        sentences = re.split(r"(?<=[.!?。])\s+", line)

        units.extend(
            sentence.strip()
            for sentence in sentences
            if sentence.strip()
        )

    chunks = []
    current_units = []
    current_length = 0

    for unit in units:
        unit_length = len(unit)

        # 단위 하나가 청크 크기보다 큰 경우
        if unit_length > chunk_size:
            if current_units:
                chunks.append("\n".join(current_units))
                current_units = []
                current_length = 0

            # 긴 단위만 크기 기준으로 분할
            for start in range(0, unit_length, chunk_size):
                chunks.append(unit[start:start + chunk_size])

            continue

        added_length = unit_length + (1 if current_units else 0)

        if current_length + added_length <= chunk_size:
            current_units.append(unit)
            current_length += added_length

        else:
            chunks.append("\n".join(current_units))

            # 이전 청크의 마지막 단위부터 Overlap 구성
            overlap_units = []
            overlap_length = 0

            for previous in reversed(current_units):
                added = len(previous) + (1 if overlap_units else 0)

                if overlap_length + added > overlap:
                    break

                overlap_units.insert(0, previous)
                overlap_length += added

            current_units = overlap_units + [unit]
            current_length = sum(len(item) for item in current_units)
            current_length += len(current_units) - 1

    if current_units:
        chunks.append("\n".join(current_units))

    return chunks

def save_document_chunks(document_id: int) -> int:
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
                raise ValueError("원문 문서를 찾을 수 없습니다.")

            raw_text = row[0]

            chunks = split_text(
                raw_text,
                chunk_size=1000,
                overlap=200,
            )

            # 기존 청크가 있다면 교체
            cur.execute(
                """
                DELETE FROM character_chunks
                WHERE document_id = %s
                """,
                (document_id,),
            )

            for index, content in enumerate(chunks):
                embedding = create_embedding(content)

                cur.execute(
                    """
                    INSERT INTO character_chunks (
                        document_id,
                        chunk_index,
                        content,
                        embedding
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        document_id,
                        index,
                        content,
                        embedding,
                    ),
                )

    return len(chunks)
