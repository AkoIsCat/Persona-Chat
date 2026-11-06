from app.database import get_connection
from app.services.memory import apply_user_memories


# 기존 테스트 계정
USER_ID = 2
CHARACTER_ID = 3

# 다른 기억과 겹치지 않는 테스트용 문장
INSERT_TEXT = "테스트용 기억: 사용자는 바닐라 아이스크림을 좋아한다."
UPDATE_TEXT = "테스트용 기억: 사용자는 초콜릿 아이스크림을 좋아한다."


def test_memory_insert_and_update():
    try:
        # 1. 신규 기억 저장
        result = apply_user_memories(
            USER_ID,
            CHARACTER_ID,
            [{
                "action": "insert",
                "existing_memory_id": None,
                "memory_text": INSERT_TEXT,
                "memory_type": "preference",
                "importance": 3,
            }],
        )

        assert result["inserted"] == 1
        print("신규 기억 저장 성공")

        # 저장된 기억 ID 조회
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM character_memories
                    WHERE user_id = %s
                      AND character_id = %s
                      AND memory_text = %s;
                    """,
                    (USER_ID, CHARACTER_ID, INSERT_TEXT),
                )
                row = cur.fetchone()

        assert row is not None
        memory_id = row[0]

        # 2. 기존 기억 수정
        result = apply_user_memories(
            USER_ID,
            CHARACTER_ID,
            [{
                "action": "update",
                "existing_memory_id": memory_id,
                "memory_text": UPDATE_TEXT,
                "memory_type": "preference",
                "importance": 4,
            }],
        )

        assert result["updated"] == 1
        print("기존 기억 수정 성공")

        # 3. DB에 실제 반영됐는지 확인
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT memory_text, importance,
                           vector_dims(embedding)
                    FROM character_memories
                    WHERE id = %s
                      AND user_id = %s
                      AND character_id = %s;
                    """,
                    (memory_id, USER_ID, CHARACTER_ID),
                )
                row = cur.fetchone()

        assert row is not None
        assert row[0] == UPDATE_TEXT
        assert row[1] == 4
        assert row[2] == 768

        print("DB 반영 및 임베딩 확인 성공")

    finally:
        # 테스트 데이터 삭제
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM character_memories
                    WHERE user_id = %s
                      AND character_id = %s
                      AND memory_text IN (%s, %s);
                    """,
                    (USER_ID, CHARACTER_ID, INSERT_TEXT, UPDATE_TEXT),
                )


if __name__ == "__main__":
    test_memory_insert_and_update()
    print("모든 테스트 완료!")