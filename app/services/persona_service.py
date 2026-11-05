
from app.database import get_connection


def get_confirmed_persona(character_id: int) -> dict | None:
    """
    캐릭터의 확정된 페르소나를 조회한다.
    확정되지 않았거나 존재하지 않으면 None을 반환한다.
    """

    sql = """
        SELECT
            basic_info,
            personality,
            values_and_beliefs,
            goals,
            background,
            worldview,
            speech_style,
            behavior_patterns,
            summary
        FROM character_personas
        WHERE character_id = %s
          AND is_confirmed = TRUE;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (character_id,))
            row = cur.fetchone()

    if row is None:
        return None

    fields = [
        "basic_info",
        "personality",
        "values_and_beliefs",
        "goals",
        "background",
        "worldview",
        "speech_style",
        "behavior_patterns",
        "summary",
    ]

    return dict(zip(fields, row))
