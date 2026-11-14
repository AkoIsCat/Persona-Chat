# 캐릭터 대화
from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_current_user_id
from app.core.perf import perf_span
from app.database import get_connection
from app.schemas import ChatRoomCreate
from app.schemas import ChatMessageCreate
from app.services.chat_service import generate_character_response
from app.services.memory import (
    extract_user_memories,
    get_user_memories,
    apply_user_memories,
)

router = APIRouter(prefix='/chat', tags=["chat"])

# 채팅방 생성
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
# 채팅방 조회
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
# 채팅 메세지 생성
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

    # 채팅방 소유권 및 캐릭터 ID 확인
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT character_id
                FROM chat_rooms
                WHERE id = %s AND user_id = %s;
                """,
                (room_id, user_id),
            )
            room = cur.fetchone()

    if room is None:
        raise HTTPException(
            status_code=404,
            detail="채팅방을 찾을 수 없습니다.",
        )

    character_id = room[0]

    # Gemini 캐릭터 답변 생성
    try:
        ai_response = generate_character_response(
        user_id=user_id,
        character_id=character_id,
        room_id=room_id,
        user_message=message,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"캐릭터 답변 생성에 실패했습니다: {str(e)}",
        )

    # 사용자 메시지와 AI 답변 저장
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_messages (chat_room_id, role, content)
                VALUES
                    (%s, 'user', %s),
                    (%s, 'assistant', %s)
                RETURNING id, role, content, created_at;
                """,
                (room_id, message, room_id, ai_response),
            )

            rows = cur.fetchall()

            cur.execute(
                """
                UPDATE chat_rooms
                SET updated_at = NOW()
                WHERE id = %s;
                """,
                (room_id,),
            )

        # 사용자 장기 기억 추출 및 저장
    try:
        with perf_span("장기 기억 처리"):
            # 1. 기존 기억 조회
            existing_memories = get_user_memories(
                user_id=user_id,
                character_id=character_id,
            )

            # 2. 현재 사용자 메시지에서 기억 추출
            memories = extract_user_memories(
                conversation_text=message,
                existing_memories=existing_memories,
            )

            # 3. 신규 기억 저장 및 기존 기억 수정
            if memories:
                result = apply_user_memories(
                    user_id=user_id,
                    character_id=character_id,
                    memories=memories,
                )

                print(
                    f"장기 기억 처리 완료: "
                    f"신규 {result['inserted']}개, "
                    f"수정 {result['updated']}개"
                )

    except Exception as e:
        # 기억 처리에 실패해도 채팅 응답은 정상 반환
        print(f"장기 기억 처리 실패: {e}")
            
    return {
        "user_message": {
            "id": rows[0][0],
            "role": rows[0][1],
            "content": rows[0][2],
            "created_at": rows[0][3],
        },
        "assistant_message": {
            "id": rows[1][0],
            "role": rows[1][1],
            "content": rows[1][2],
            "created_at": rows[1][3],
        },
    }

@router.delete("/rooms/{room_id}")
def delete_chat_room(
    room_id: int,
    user_id: int = Depends(get_current_user_id),
):
    # 채팅 메시지는 chat_rooms FK ON DELETE CASCADE로 함께 삭제된다.
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM chat_rooms
                    WHERE id = %s AND user_id = %s
                    RETURNING id
                    """,
                    (room_id, user_id),
                )
                row = cur.fetchone()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="채팅방을 삭제하지 못했습니다.",
        )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="채팅방을 찾을 수 없습니다.",
        )

    return {
        "message": "채팅방이 삭제되었습니다.",
        "room_id": row[0],
    }

# 채팅 내역 조회
@router.get("/rooms/{room_id}/messages")
def get_chat_messages(
    room_id: int,
    user_id: int = Depends(get_current_user_id),
):
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

            # 채팅 메시지 조회
            cur.execute(
                """
                SELECT id, role, content, created_at
                FROM chat_messages
                WHERE chat_room_id = %s
                ORDER BY created_at ASC, id ASC;
                """,
                (room_id,),
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "role": row[1],
            "content": row[2],
            "created_at": row[3],
        }
        for row in rows
    ]

