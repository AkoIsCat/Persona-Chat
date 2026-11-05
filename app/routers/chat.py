# 캐릭터 대화
from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_current_user_id
from app.database import get_connection
from app.schemas import ChatRoomCreate
from app.schemas import ChatMessageCreate

router = APIRouter(prefix='/chat', tags=["chat"])

@router.post("/rooms", status_code=201)
def create_chat_room(
  payload: ChatRoomCreate,
  user_id: int = Depends(get_current_user_id),
):
  with get_connection() as conn:
    with conn.cursor() as cur:
      # 캐릭터 존재 및 소유권 확인
      cur.execute(
        """
        SELECT id
        FROM characters
        WHERE id = %s AND user_id = %s;
        """,
        (payload.character_id, user_id),
      )

      character = cur.fetchone()

      if character is None:
        raise HTTPException(
          status_code=404,
          detail="캐릭터를 찾을 수 없습니다."
        )


      cur.execute(
        """
        SELECT id
        FROM character_personas
        WHERE character_id = %s
          AND is_confirmed = TRUE;
        """,
        (payload.character_id,),
      )

      persona = cur.fetchone()

      if persona is None:
        raise HTTPException(
          status_code=400,
          detail="페르소나가 확정되지 않은 캐릭터입니다.",
        )

      # 채팅방 생성
      cur.execute(
        """
        INSERT INTO chat_rooms (user_id, character_id)
        VALUES (%s, %s)
        RETURNING id, user_id, character_id, title, created_at;
        """,
        (user_id, payload.character_id),
      )

      room = cur.fetchone()


  return {
    "id": room[0],
    "user_id": room[1],
    "character_id": room[2],
    "title": room[3],
    "created_at": room[4],
  }

@router.get("/rooms")
def get_chat_rooms(
    user_id: int = Depends(get_current_user_id),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cr.id,
                    cr.character_id,
                    c.name,
                    cr.title,
                    cr.created_at,
                    cr.updated_at
                FROM chat_rooms cr
                JOIN characters c
                    ON cr.character_id = c.id
                WHERE cr.user_id = %s
                ORDER BY cr.updated_at DESC;
                """,
                (user_id,),
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "character_id": row[1],
            "character_name": row[2],
            "title": row[3],
            "created_at": row[4],
            "updated_at": row[5],
        }
        for row in rows
    ]

@router.post("/rooms/{room_id}/messages", status_code=201)
def create_chat_message(
    room_id: int,
    payload: ChatMessageCreate,
    user_id: int = Depends(get_current_user_id),
):
    message = payload.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="메시지를 입력해 주세요.",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 채팅방 소유권 확인
            cur.execute(
                """
                SELECT id
                FROM chat_rooms
                WHERE id = %s AND user_id = %s;
                """,
                (room_id, user_id),
            )

            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="채팅방을 찾을 수 없습니다.",
                )

            # 사용자 메시지 저장
            cur.execute(
                """
                INSERT INTO chat_messages (chat_room_id, role, content)
                VALUES (%s, 'user', %s)
                RETURNING id, role, content, created_at;
                """,
                (room_id, message),
            )

            row = cur.fetchone()

            # 채팅방 수정 시간 갱신
            cur.execute(
                """
                UPDATE chat_rooms
                SET updated_at = NOW()
                WHERE id = %s;
                """,
                (room_id,),
            )

    return {
        "id": row[0],
        "role": row[1],
        "content": row[2],
        "created_at": row[3],
    }

