import os
from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_current_user_id
from app.database import get_connection

from app.schemas import CharacterPersonaUpdate
from app.services.character_analysis import analyze_character

router = APIRouter(
    prefix="/characters",
    tags=["personas"],
)

# 페르소나 분석 및 저장
@router.post("/{character_id}/analyze", status_code=201)
def analyze_character_persona(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
):
    # 캐릭터의 원작 자료 조회
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cu.display_order,
                    cu.url,
                    csd.title,
                    csd.raw_text
                FROM characters c
                JOIN character_urls cu
                    ON cu.character_id = c.id
                JOIN character_source_documents csd
                    ON csd.character_url_id = cu.id
                WHERE c.id = %s
                  AND c.user_id = %s
                ORDER BY cu.display_order, csd.id
                """,
                (character_id, user_id),
            )

            rows = cur.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="분석할 원작 자료가 없습니다.",
        )

    # 기준 자료와 추가 자료 구분
    source_texts = []

    for order, url, title, raw_text in rows:
        source_type = (
            "기준 원작 자료"
            if order == 1
            else f"추가 원작 자료 {order - 1}"
        )

        source_texts.append(
            f"""
[{source_type}]
제목: {title or "제목 없음"}
URL: {url}

{raw_text}
"""
        )

    combined_text = "\n\n".join(source_texts)

    # Gemini 캐릭터 분석
    try:
        result = analyze_character(combined_text)

        model_name = os.getenv(
            "GEMINI_MODEL",
            "gemini-3.5-flash-lite",
        )

    except Exception as e:
        print("Gemini 분석 오류:", e)
        raise HTTPException(
            status_code=502,
            detail="캐릭터 분석 중 오류가 발생했습니다.",
        )

    # 분석 결과 DB 저장
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO character_personas (
                    character_id,
                    basic_info,
                    personality,
                    values_and_beliefs,
                    goals,
                    background,
                    worldview,
                    speech_style,
                    behavior_patterns,
                    summary,
                    model_name
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (character_id)
                DO UPDATE SET
                    basic_info = EXCLUDED.basic_info,
                    personality = EXCLUDED.personality,
                    values_and_beliefs = EXCLUDED.values_and_beliefs,
                    goals = EXCLUDED.goals,
                    background = EXCLUDED.background,
                    worldview = EXCLUDED.worldview,
                    speech_style = EXCLUDED.speech_style,
                    behavior_patterns = EXCLUDED.behavior_patterns,
                    summary = EXCLUDED.summary,
                    model_name = EXCLUDED.model_name,
                    updated_at = NOW()
                WHERE character_personas.is_confirmed = FALSE
                RETURNING id
                """,
                (
                    character_id,
                    result["basic_info"],
                    result["personality"],
                    result["values_and_beliefs"],
                    result["goals"],
                    result["background"],
                    result["worldview"],
                    result["speech_style"],
                    result["behavior_patterns"],
                    result["summary"],
                    model_name,
                ),
            )

            row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=409,
            detail="이미 확정된 페르소나는 다시 분석할 수 없습니다.",
        )

    return {
        "character_id": character_id,
        "persona_id": row[0],
        "persona": result,
        "is_confirmed": False,
        "message": "캐릭터 분석 및 임시 페르소나 저장이 완료되었습니다.",
    }
# 페르소나 조회
@router.get("/{character_id}/persona")
def get_character_persona(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cp.id,
                    cp.basic_info,
                    cp.personality,
                    cp.values_and_beliefs,
                    cp.goals,
                    cp.background,
                    cp.worldview,
                    cp.speech_style,
                    cp.behavior_patterns,
                    cp.summary,
                    cp.is_confirmed,
                    cp.model_name,
                    cp.created_at,
                    cp.updated_at
                FROM character_personas cp
                JOIN characters c
                    ON c.id = cp.character_id
                WHERE cp.character_id = %s
                  AND c.user_id = %s
                """,
                (character_id, user_id),
            )

            row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="저장된 페르소나가 없습니다.",
        )

    return {
        "character_id": character_id,
        "persona_id": row[0],
        "persona": {
            "basic_info": row[1],
            "personality": row[2],
            "values_and_beliefs": row[3],
            "goals": row[4],
            "background": row[5],
            "worldview": row[6],
            "speech_style": row[7],
            "behavior_patterns": row[8],
            "summary": row[9],
        },
        "is_confirmed": row[10],
        "model_name": row[11],
        "created_at": row[12],
        "updated_at": row[13],
    }
# 페르소나 수정
@router.put("/{character_id}/persona")
def update_character_persona(
    character_id: int,
    payload: CharacterPersonaUpdate,
    user_id: int = Depends(get_current_user_id),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE character_personas cp
                SET
                    basic_info = %s,
                    personality = %s,
                    values_and_beliefs = %s,
                    goals = %s,
                    background = %s,
                    worldview = %s,
                    speech_style = %s,
                    behavior_patterns = %s,
                    summary = %s,
                    updated_at = NOW()
                FROM characters c
                WHERE cp.character_id = c.id
                  AND cp.character_id = %s
                  AND c.user_id = %s
                  AND cp.is_confirmed = FALSE
                RETURNING cp.id
                """,
                (
                    payload.basic_info,
                    payload.personality,
                    payload.values_and_beliefs,
                    payload.goals,
                    payload.background,
                    payload.worldview,
                    payload.speech_style,
                    payload.behavior_patterns,
                    payload.summary,
                    character_id,
                    user_id,
                ),
            )

            row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="수정할 수 있는 페르소나가 없습니다. 캐릭터 또는 페르소나를 확인해 주세요.",
        )

    return {
        "character_id": character_id,
        "persona_id": row[0],
        "message": "캐릭터 페르소나가 수정되었습니다.",
    }
#페르소나 확정
@router.post("/{character_id}/persona/confirm")
def confirm_character_persona(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE character_personas cp
                SET
                    is_confirmed = TRUE,
                    updated_at = NOW()
                FROM characters c
                WHERE cp.character_id = c.id
                  AND cp.character_id = %s
                  AND c.user_id = %s
                  AND cp.is_confirmed = FALSE
                RETURNING cp.id
                """,
                (character_id, user_id),
            )

            row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="확정할 수 있는 페르소나가 없습니다.",
        )

    return {
        "character_id": character_id,
        "persona_id": row[0],
        "is_confirmed": True,
        "message": "캐릭터 페르소나가 최종 확정되었습니다.",
    }

