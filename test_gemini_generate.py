
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError(".env에서 GEMINI_API_KEY를 찾을 수 없습니다.")

client = genai.Client(
    api_key=api_key,
    http_options={"timeout": 120000}
)

try:
    print("Gemini 3.5 Flash Lite 응답 확인 중...\n")

    response = client.interactions.create(
        model="gemini-3.5-flash-lite",
        input="캐릭터의 성격과 말투를 분석할 때 고려해야 할 항목을 3가지 알려줘."
    )

    print(response)

except Exception as e:
    print("Gemini API 요청에 실패했습니다.")
    print(f"오류 유형: {type(e).__name__}")
    print(f"오류 내용: {e}")
