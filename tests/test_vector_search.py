
from app.services.vector_search_service import search_similar_chunks


def main():
    character_id = 3

    query = "김성규가 인피니트 멤버로 데뷔한 연도"

    results = search_similar_chunks(
    character_id=character_id,
    query=query,
    top_k=83,
    )

    print(f"\n질문: {query}")
    print(f"검색 결과: {len(results)}개")

    for result in results:
      if result["id"] == 14:
          content = result["content"]

          position = content.find("데뷔")

          print("정답 청크 ID:", result["id"])
          print("검색 순위:", results.index(result) + 1)
          print("데뷔 정보:", content[position - 50:position + 150])
          break
    else:
      print("정답 청크를 검색하지 못했습니다.")

if __name__ == "__main__":
    main()
