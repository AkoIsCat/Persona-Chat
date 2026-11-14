from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_current_user_id
from app.database import get_connection

from app.schemas import CharacterPersonaUpdate
from app.services.persona_facts_service import (
    AnalysisInProgressError,
    run_sequential_persona_analysis,
)

router = APIRouter(
    prefix="/characters",
    tags=["personas"],
)

# 페르소나 분석 및 저장 (URL별 순차 누적 분석)
@router.post("/{character_id}/analyze", status_code=201)
def analyze_character_persona(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
):
    try:
        result = run_sequential_persona_analysis(character_id, user_id)
    except AnalysisInProgressError:
        raise HTTPException(
            status_code=409,
            detail="해당 캐릭터 분석이 이미 진행 중입니다. 잠시 후 다시 시도해 주세요.",
        )
    except PermissionError:
        raise HTTPException(
            status_code=409,
            detail="이미 확정된 페르소나는 다시 분석할 수 없습니다.",
        )
    except LookupError as e:
        detail = (
            "분석할 원작 자료가 없습니다."
            if str(e) == "no_source_documents"
            else "캐릭터를 찾을 수 없습니다."
        )
        raise HTTPException(status_code=404, detail=detail)
    except Exception as e:
        print("Gemini 순차 분석 오류:", e)
        raise HTTPException(
            status_code=502,
            detail="캐릭터 분석 중 오류가 발생했습니다.",
        )

    return {
        "character_id": result["character_id"],
        "persona_id": result["persona_id"],
        "persona": result["persona"],
        "is_confirmed": result["is_confirmed"],
        "message": result["message"],
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

