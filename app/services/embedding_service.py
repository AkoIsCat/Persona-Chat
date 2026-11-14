import os

from dotenv import load_dotenv
from google import genai

from app.core.perf import perf_span

load_dotenv()

def create_embedding(text: str) -> list[float]:
  api_key = os.getenv("GEMINI_API_KEY")

  if not api_key:
    raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")

  client = genai.Client(api_key=api_key)

  with perf_span("임베딩 생성"):
    response = client.models.embed_content(
      model="gemini-embedding-001",
      contents=text,
      config={
        "output_dimensionality": 768,
      },
    )

  return response.embeddings[0].values