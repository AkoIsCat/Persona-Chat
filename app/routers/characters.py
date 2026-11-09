# 캐릭터 생성, 조회
from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation

from app.core.dependencies import get_current_user_id
from app.database import get_connection
from app.schemas import CharacterCreate

router = APIRouter(prefix="/characters", tags=["characters"])

@router.get("")
def get_characters(
    user_id: int = Depends(get_current_user_id),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, description, image_url, created_at
                FROM characters
                WHERE user_id = %s
                ORDER BY created_at DESC, id DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "image_url": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]

@router.get("/{character_id}/memories")
def get_character_memories(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM characters
                WHERE id = %s AND user_id = %s
                """,
                (character_id, user_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="캐릭터를 찾을 수 없습니다.",
                )

            cur.execute(
                """
                SELECT
                    id,
                    memory_text,
                    memory_type,
                    importance,
                    created_at,
                    updated_at
                FROM character_memories
                WHERE user_id = %s AND character_id = %s
                ORDER BY importance DESC, updated_at DESC, id DESC
                """,
                (user_id, character_id),
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "memory_text": row[1],
            "memory_type": row[2],
            "importance": row[3],
            "created_at": row[4],
            "updated_at": row[5],
        }
        for row in rows
    ]

@router.post("", status_code=201)
def create_charceter(
  payload: CharacterCreate,
  user_id: int = Depends(get_current_user_id),
):
  urls = [str(url) for url in payload.urls]

  # 같은 요청 안에 중복된 URL이 있는지 확인
  if len(urls) != len(set(urls)):
    raise HTTPException(
      status_code=400,
      detail="중복된 URL은 등록할 수 없습니다.",
    )

  try:
    with get_connection() as conn:
      with conn.cursor() as cur:
        # 캐릭터 기본 정보 저장
        cur.execute(
          """
          INSERT INTO characters (user_id, name)
          VALUES (%s, %s)
          RETURNING id, name, description, image_url, created_at
          """,
          (user_id, payload.name.strip()),
        )
        character = cur.fetchone()

        character_id = character[0]

        # 캐릭터의 원작 URL 저장
        for order, url in enumerate(urls, start=1):
          cur.execute(
            """
            INSERT INTO character_urls
              (character_id, url, display_order)  
            VALUES (%s, %s, %s)
            """,
            (character_id, url, order),
          )

    return {
      "id" : character[0],
      "name": character[1],
      "description": character[2],
      "image_url": character[3],
      "created_at": character[4],
      "urls": urls,
    }

  except UniqueViolation:
    raise HTTPException(
      status_code=409,
      detail="중복된 URL은 등록할 수 없습니다.",
    )
