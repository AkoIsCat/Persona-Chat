# 캐릭터 생성, 조회
from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation

from app.core.dependencies import get_current_user_id
from app.database import get_connection
from app.schemas import CharacterCreate
from app.schemas import CharacterCreate

router = APIRouter(prefix="/characters", tags=["characters"])

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
