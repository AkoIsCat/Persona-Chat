
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError(".env에서 GEMINI_API_KEY를 찾을 수 없습니다.")

client = genai.Client(api_key=api_key)

try:
    print("Gemini API 연결 확인 중...\n")

    models = client.models.list()
    available_models = []

    for model in models:
        actions = model.supported_actions or []

        if "generateContent" in actions:
            available_models.append(model.name)

    if not available_models:
        print("사용 가능한 생성 모델을 찾지 못했습니다.")
    else:
        print("사용 가능한 모델 목록:")
        for model_name in available_models:
            print(model_name)

except Exception as e:
    print("Gemini API 연결에 실패했습니다.")
    print(f"오류 유형: {type(e).__name__}")
    print(f"오류 내용: {e}")
