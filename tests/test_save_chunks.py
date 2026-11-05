from app.services.chunk_service import save_document_chunks

document_id = 3

try:
    count = save_document_chunks(document_id)
    print(f"저장 완료: {count}개 청크")

except Exception as e:
    print("저장 실패:", e)
