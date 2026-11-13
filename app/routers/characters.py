# 캐릭터 생성, 조회
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from psycopg.errors import UniqueViolation

from app.core.dependencies import get_current_user_id
from app.database import get_connection
from app.schemas import CharacterCreate
from app.services.character_image import (
    read_image,
    remove_file,
    remove_managed_image,
    save_character_image,
)

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

@router.delete("/{character_id}")
def delete_character(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
):
    # 연결된 페르소나, 자료, 기억, 대화는 FK ON DELETE CASCADE로 함께 삭제된다.
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM characters
                    WHERE id = %s AND user_id = %s
                    RETURNING id
                    """,
                    (character_id, user_id),
                )
                row = cur.fetchone()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="캐릭터를 삭제하지 못했습니다.",
        )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="캐릭터를 찾을 수 없습니다.",
        )

    return {
        "message": "캐릭터가 삭제되었습니다.",
        "character_id": row[0],
    }

@router.post("/{character_id}/image")
def upload_character_image(
    character_id: int,
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
):
    content, extension = read_image(file)
    saved_path = None

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT image_url
                    FROM characters
                    WHERE id = %s AND user_id = %s
                    """,
                    (character_id, user_id),
                )
                current = cur.fetchone()
                if current is None:
                    raise HTTPException(
                        status_code=404,
                        detail="캐릭터를 찾을 수 없습니다.",
                    )

                saved_path, image_url = save_character_image(character_id, content, extension)
                cur.execute(
                    """
                    UPDATE characters
                    SET image_url = %s, updated_at = NOW()
                    WHERE id = %s AND user_id = %s
                    RETURNING image_url
                    """,
                    (image_url, character_id, user_id),
                )
                updated = cur.fetchone()
                if updated is None:
                    raise HTTPException(
                        status_code=404,
                        detail="캐릭터를 찾을 수 없습니다.",
                    )
    except HTTPException:
        remove_file(saved_path)
        raise
    except Exception:
        remove_file(saved_path)
        raise HTTPException(
            status_code=500,
            detail="캐릭터 이미지를 저장하지 못했습니다.",
        )

    remove_managed_image(current[0], character_id)
    return {
        "message": "캐릭터 이미지가 변경되었습니다.",
        "character_id": character_id,
        "image_url": updated[0],
    }

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
