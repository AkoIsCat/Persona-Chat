from app.services.embedding_service import create_embedding

text = "김성규는 인피니트의 리더이자 메인보컬이다."

try:
    embedding = create_embedding(text)

    print("임베딩 생성 성공!")
    print("벡터 차원:", len(embedding))
    print("앞의 5개 값:", embedding[:5])

except Exception as e:
    print("임베딩 생성 실패:", e)