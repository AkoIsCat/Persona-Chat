# 장기 기억 처리
import os
import json

from dotenv import load_dotenv
from google import genai

from app.database import get_connection
from app.services.embedding_service import create_embedding

load_dotenv()
# 장기 기억 추출 함수
def extract_user_memories(
    conversation_text: str,
    existing_memories: list[dict] | None = None,
) -> list[dict]:
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

    existing_memory_text = "\n".join(
        f"- ID: {memory['id']}, "
        f"내용: {memory['memory_text']}, "
        f"유형: {memory['memory_type']}"
        for memory in (existing_memories or [])
    )

    if not existing_memory_text:
        existing_memory_text = "저장된 기억이 없습니다."

    prompt = f"""
당신은 사용자와의 대화에서 장기적으로 기억할 정보를 추출하는 역할입니다.

아래 대화에서 사용자에 관한 정보만 추출하세요.

[추출 원칙]
1. 사용자가 직접 알려준 정보만 추출하세요.
2. 사용자에 관한 취향, 성격, 경험, 목표, 배경, 인간관계 등을 추출하세요.
3. 일시적인 질문이나 단순한 인사말은 기억하지 마세요.
4. 캐릭터에 관한 정보는 추출하지 마세요.
5. 대화에 없는 정보를 추측하거나 만들어내지 마세요.
6. 기존 기억과 동일한 정보는 추출하지 마세요.
7. 기존 기억과 다른 새로운 정보는 action을 insert로 지정하세요.
8. 사용자가 기존 정보를 명확하게 정정했다면 action을 update로 지정하세요.
9. update할 때는 정정 대상이 되는 기존 기억의 ID를 지정하세요.
10. 기존 기억과 모순되지만 정정 여부가 불분명하다면 추출하지 마세요.
11. 기존 기억을 수정할 필요가 없다면 아무것도 반환하지 마세요.
12. 기억 내용은 나중에 다시 활용할 수 있도록 명확한 문장으로 작성하세요.

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
            "action": "insert",
            "existing_memory_id": null,
            "memory_text": "사용자가 가장 좋아하는 음식은 떡볶이다.",
            "memory_type": "preference",
            "importance": 4
        }},
        {{
            "action": "update",
            "existing_memory_id": 1,
            "memory_text": "사용자가 가장 좋아하는 음식은 피자다.",
            "memory_type": "preference",
            "importance": 4
        }}
    ]
}}

[기존에 저장된 사용자 기억]
{existing_memory_text}

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

    # 기존 기억 ID 확인용
    existing_memory_ids = {
        memory["id"]
        for memory in (existing_memories or [])
    }

    validated_memories = []

    for memory in memories:
        if not isinstance(memory, dict):
            continue

        action = memory.get("action")
        existing_memory_id = memory.get("existing_memory_id")
        memory_text = memory.get("memory_text")
        memory_type = memory.get("memory_type")
        importance = memory.get("importance")

        # action 검증
        if action not in {"insert", "update"}:
            continue

        # 기억 내용 검증
        if not isinstance(memory_text, str) or not memory_text.strip():
            continue

        if memory_type not in valid_types:
            continue

        if (
            not isinstance(importance, int)
            or isinstance(importance, bool)
            or not 1 <= importance <= 5
        ):
            continue

        # 신규 기억 검증
        if action == "insert":
            if existing_memory_id is not None:
                continue

        # 기존 기억 수정 검증
        elif action == "update":
            if (
                not isinstance(existing_memory_id, int)
                or isinstance(existing_memory_id, bool)
                or existing_memory_id not in existing_memory_ids
            ):
                continue

        validated_memories.append({
            "action": action,
            "existing_memory_id": existing_memory_id,
            "memory_text": memory_text.strip(),
            "memory_type": memory_type,
            "importance": importance,
        })

    return validated_memories

# 기존 사용자 기억 조회 함수
def get_user_memories(
    user_id: int,
    character_id: int,
) -> list[dict]:
    """
    특정 사용자와 캐릭터에 저장된 장기 기억을 조회한다.
    """

    sql = """
        SELECT id, memory_text, memory_type, importance
        FROM character_memories
        WHERE user_id = %s
          AND character_id = %s
        ORDER BY importance DESC, updated_at DESC;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, character_id))
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "memory_text": row[1],
            "memory_type": row[2],
            "importance": row[3],
        }
        for row in rows
    ]

# 장기 기억 저장 함수
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

# 기억 저장 및 수정 함수
def apply_user_memories(
    user_id: int,
    character_id: int,
    memories: list[dict],
) -> dict:
    """
    추출한 기억을 신규 저장하거나 기존 기억을 수정한다.
    """

    insert_sql = """
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

    update_sql = """
        UPDATE character_memories
        SET memory_text = %s,
            embedding = %s::vector,
            memory_type = %s,
            importance = %s,
            updated_at = NOW()
        WHERE id = %s
          AND user_id = %s
          AND character_id = %s;
    """

    inserted_count = 0
    updated_count = 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            for memory in memories:
                memory_text = memory["memory_text"]

                embedding = create_embedding(memory_text)
                embedding_vector = "[" + ",".join(
                    map(str, embedding)
                ) + "]"

                if memory["action"] == "insert":
                    cur.execute(
                        insert_sql,
                        (
                            user_id,
                            character_id,
                            memory_text,
                            embedding_vector,
                            memory["memory_type"],
                            memory["importance"],
                        ),
                    )
                    inserted_count += 1

                elif memory["action"] == "update":
                    cur.execute(
                        update_sql,
                        (
                            memory_text,
                            embedding_vector,
                            memory["memory_type"],
                            memory["importance"],
                            memory["existing_memory_id"],
                            user_id,
                            character_id,
                        ),
                    )

                    if cur.rowcount > 0:
                        updated_count += 1

    return {
        "inserted": inserted_count,
        "updated": updated_count,
    }

# 기존 기억 유사도 검색 함수
def search_similar_memories(
    user_id: int,
    character_id: int,
    memory_text: str,
    top_k: int = 3,
) -> list[dict]:
    """
    기존 장기 기억 중 새로운 기억과 유사한 항목을 검색한다.
    """

    embedding = create_embedding(memory_text)
    embedding_vector = "[" + ",".join(map(str, embedding)) + "]"

    sql = """
        SELECT
            id,
            memory_text,
            memory_type,
            importance,
            embedding <=> %s::vector AS distance
        FROM character_memories
        WHERE user_id = %s
          AND character_id = %s
          AND embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    embedding_vector,
                    user_id,
                    character_id,
                    embedding_vector,
                    top_k,
                ),
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "memory_text": row[1],
            "memory_type": row[2],
            "importance": row[3],
            "distance": float(row[4]),
            "similarity": 1 - float(row[4]),
        }
        for row in rows
    ]
