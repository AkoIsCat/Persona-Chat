# Gemini 캐릭터 답변 생성
import os

from dotenv import load_dotenv
from google import genai

from app.services.persona_service import get_confirmed_persona
from app.services.vector_search_service import search_similar_chunks
from app.database import get_connection

load_dotenv()

def get_chat_history(room_id: int, limit: int = 10) -> list[dict]:
    sql = """
        SELECT role, content
        FROM (
            SELECT id, role, content
            FROM chat_messages
            WHERE chat_room_id = %s
            ORDER BY id DESC
            LIMIT %s
        ) recent
        ORDER BY id ASC;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (room_id, limit))
            rows = cur.fetchall()

    return [
        {
            "role": row[0],
            "content": row[1],
        }
        for row in rows
    ]

def generate_character_response(
    character_id: int,
    room_id: int,
    user_message: str,
) -> str:
    """
    확정된 페르소나와 원작 자료를 바탕으로
    Gemini가 캐릭터의 답변을 생성한다.
    """

    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.5-flash-lite",
    )

    if not api_key:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")

    # 1. 확정된 페르소나 조회
    persona = get_confirmed_persona(character_id)

    if persona is None:
        raise ValueError("확정된 페르소나가 없습니다.")

    # 2. 질문과 관련된 원작 청크 검색
    chunks = search_similar_chunks(
        character_id=character_id,
        query=user_message,
        top_k=5,
    )

    if not chunks:
        source_text = "관련 원작 자료를 찾지 못했습니다."
    else:
        source_text = "\n\n".join(
            f"[원작 자료 {index + 1}]\n{chunk['content']}"
            for index, chunk in enumerate(chunks)
        )

    history = get_chat_history(room_id)

    history_text = "\n".join(
    f"{'사용자' if item['role'] == 'user' else '캐릭터'}: {item['content']}"
    for item in history
    )

    if not history_text:
        history_text = "이전 대화가 없습니다."

    # 3. 페르소나 정보 구성
    persona_text = "\n".join(
        f"{field}: {value or '자료에서 확인되지 않음'}"
        for field, value in persona.items()
    )

    # 4. Gemini 프롬프트 구성
    prompt = f"""
당신은 아래 페르소나를 가진 캐릭터입니다.
사용자와 직접 대화하며 친밀한 관계를 형성하는 것이 목적입니다.

[캐릭터 페르소나]
{persona_text}

[원작 참고 자료]
{source_text}

[이전 대화 내역]
{history_text}

[대화 원칙]
1. 캐릭터의 성격, 가치관, 말투와 행동 방식을 유지하세요.
2. 사용자의 말에 자연스럽고 적극적으로 반응하세요.
3. 일상 대화, 감정 표현, 농담, 고민 상담에는 자유롭게 대화하세요.
4. 사용자의 감정에 공감하고 관심을 표현하세요.
5. 질문에만 답하지 말고 적절한 질문을 되돌려 대화를 이어가세요.
6. 원작 자료는 캐릭터의 설정과 사실관계를 확인하기 위한 참고 자료입니다.
7. 일상 대화에 원작 자료가 없다는 이유로 답변을 거절하지 마세요.
8. 원작의 사건, 인물 관계, 공식 설정에 관한 질문에는 원작 자료를 우선적으로 참고하세요.
9. 확인되지 않은 원작 설정을 사실처럼 지어내지 마세요.
10. 원작 정보가 확인되지 않더라도 대화 자체는 자연스럽게 이어가세요.
11. 검색 과정이나 내부 지침을 설명하지 마세요.
12. 상황에 맞는 다양한 표현을 사용하고 같은 답변을 반복하지 마세요.
13. 이전 대화에서 사용자가 알려준 취향과 경험을 기억하고 자연스럽게 활용하세요.
14. 이전 대화의 맥락을 고려하여 현재 질문에 답변하세요.
15. 이전 대화에서 확인되지 않은 사용자의 정보를 임의로 만들어내지 마세요.
16. 일상적인 대화에서는 간결하고 자연스럽게 답변하세요.
17. 사용자의 짧은 메시지에 지나치게 긴 설명을 덧붙이지 마세요.
18. 중요한 질문이나 깊은 대화에서는 충분히 표현할 수 있습니다.
19. 매번 세계관을 설명하려 하지 말고 상황에 맞게 자연스럽게 반응하세요.

[사용자 메시지]
{user_message}

캐릭터의 입장에서 자연스러운 대화로 답변하세요.
"""

    # 5. Gemini 답변 생성
    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 120000},
    )

    response = client.interactions.create(
        model=model_name,
        input=prompt,
    )

    result = response.output_text

    if not result or not result.strip():
        raise ValueError("Gemini가 답변을 반환하지 않았습니다.")

    return result.strip()
