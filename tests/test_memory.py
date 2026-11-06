from app.services.memory import extract_user_memories
from app.services.memory import search_similar_memories

conversation = """
사용자: 내가 제일 좋아하는 음식은 떡볶이야.
캐릭터: 매콤한 음식을 좋아하는구나.
사용자: 그리고 나는 컴퓨터정보계열을 전공하고 있어.
캐릭터: 그렇군. 요즘은 어떤 공부를 하고 있지?
"""

result = extract_user_memories(conversation)

print("===== 장기 기억 추출 결과 =====")

for memory in result:
    print(memory)


results = search_similar_memories(
    user_id=2,
    character_id=3,
    memory_text="나는 떡볶이를 가장 좋아해.",
)

print("===== 유사 기억 검색 결과 =====")

for memory in results:
    print(memory)
