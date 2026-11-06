# 장기 기억 처리
import os
import json

from dotenv import load_dotenv
from google import genai

from app.database import get_connection
from app.services.embedding_service import create_embedding

load_dotenv()

def extract_user_memories(conversation_text: str) -> list[dict]:
    """
    대화 내용을 분석하여 사용자의 장기 기억 정보를 추출한다.
    """

    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.5-flash-lite",
    )

    if not api_key:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")

    if not conversation_text or not conversation_text.strip():
        raise ValueError("분석할 대화 내용이 없습니다.")

    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 120000},
    )

    prompt = f"""
당신은 사용자와의 대화에서 장기적으로 기억할 정보를 추출하는 역할입니다.

아래 대화에서 사용자에 관한 정보만 추출하세요.

[추출 원칙]
1. 사용자가 직접 알려준 정보를 우선적으로 추출하세요.
2. 사용자에 관한 취향, 성격, 경험, 목표, 배경, 인간관계 등을 추출하세요.
3. 일시적인 질문이나 단순한 인사말은 기억하지 마세요.
4. 캐릭터에 관한 정보는 추출하지 마세요.
5. 대화에 없는 정보를 추측하거나 만들어내지 마세요.
6. 동일한 정보는 중복해서 추출하지 마세요.
7. 사용자가 과거 정보를 정정했다면 정정된 내용을 우선하세요.
8. 기억할 만한 정보가 없다면 빈 배열을 반환하세요.
9. 기억 내용은 나중에 다시 활용할 수 있도록 명확한 문장으로 작성하세요.

[memory_type]
- preference: 취향 및 선호
- personality: 성격 및 성향
- experience: 경험 및 일상
- goal: 목표 및 계획
- relationship: 주변 사람과의 관계
- background: 사용자 배경


[importance 판단 기준]
중요도는 다음 네 가지 기준을 종합하여 1~5점으로 판단하세요.

1. 지속성: 시간이 지나도 유효할 가능성이 높은 정보인가?
2. 활용도: 이후 캐릭터와의 대화에서 활용할 가치가 있는가?
3. 명확성: 사용자가 직접 밝힌 확실한 정보인가?
4. 중요성: 사용자의 취향, 성향, 목표 등 개인화에 중요한 정보인가?

[점수 기준]
- 1: 일시적인 정보이며 향후 대화에 활용할 가치가 거의 없음
- 2: 단기간 참고할 수 있지만 지속성이나 활용도가 낮음
- 3: 일정 기간 유효하고 향후 대화에 참고할 만한 정보
- 4: 지속성이 높고 향후 대화에서 자주 활용할 중요한 정보
- 5: 사용자를 이해하고 개인화된 대화를 제공하는 데 핵심적인 정보

[추가 판단 원칙]
- 최근에 언급했다는 이유만으로 중요도를 높이지 마세요.
- 단순한 일회성 경험은 낮은 점수를 부여하세요.
- 장기적인 취향, 성격, 목표는 높은 점수를 부여할 수 있습니다.
- 사용자가 직접 밝힌 정보라도 일시적인 정보라면 낮은 점수를 부여하세요.
- 네 가지 기준을 종합하여 판단하고 점수만으로 중요도를 과장하지 마세요.

[출력 형식]
반드시 JSON 객체만 출력하세요.
마크다운 코드 블록이나 추가 설명은 출력하지 마세요.

{{
    "memories": [
        {{
            "memory_text": "사용자가 가장 좋아하는 음식은 떡볶이다.",
            "memory_type": "preference",
            "importance": 4
        }}
    ]
}}

[분석할 대화]
{conversation_text}
"""

    response = client.interactions.create(
        model=model_name,
        input=prompt,
    )

    result_text = response.output_text

    if not result_text or not result_text.strip():
        raise ValueError("Gemini가 기억 추출 결과를 반환하지 않았습니다.")

    result_text = result_text.strip()

    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1]
        result_text = result_text.rsplit("```", 1)[0].strip()

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            "Gemini 기억 추출 결과를 JSON으로 변환하지 못했습니다."
        ) from e

    memories = result.get("memories", [])

    if not isinstance(memories, list):
        raise ValueError("기억 추출 결과의 형식이 올바르지 않습니다.")

    valid_types = {
        "preference",
        "personality",
        "experience",
        "goal",
        "relationship",
        "background",
    }

    validated_memories = []

    for memory in memories:
        if not isinstance(memory, dict):
            continue

        memory_text = memory.get("memory_text")
        memory_type = memory.get("memory_type")
        importance = memory.get("importance")

        if not isinstance(memory_text, str) or not memory_text.strip():
            continue

        if memory_type not in valid_types:
            continue

        if not isinstance(importance, int) or isinstance(importance, bool):
            continue

        if not 1 <= importance <= 5:
            continue

        validated_memories.append({
            "memory_text": memory_text.strip(),
            "memory_type": memory_type,
            "importance": importance,
        })

    return validated_memories



def save_user_memories(
    user_id: int,
    character_id: int,
    memories: list[dict],
) -> int:
    """
    추출한 사용자 기억을 임베딩으로 변환하여 DB에 저장한다.
    """

    sql = """
        INSERT INTO character_memories (
            user_id,
            character_id,
            memory_text,
            embedding,
            memory_type,
            importance
        )
        VALUES (%s, %s, %s, %s::vector, %s, %s);
    """

    saved_count = 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            for memory in memories:
                memory_text = memory["memory_text"]

                embedding = create_embedding(memory_text)
                embedding_vector = "[" + ",".join(
                    map(str, embedding)
                ) + "]"

                cur.execute(
                    sql,
                    (
                        user_id,
                        character_id,
                        memory_text,
                        embedding_vector,
                        memory["memory_type"],
                        memory["importance"],
                    ),
                )

                saved_count += 1

    return saved_count
