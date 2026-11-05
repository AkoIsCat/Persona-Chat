import os
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

api_key = os.getenv("TAVILY_API_KEY")

if not api_key:
  raise ValueError(".env에서 TAVILY_API_KEY를 찾을 수 없습니다.")

client = TavilyClient(api_key=api_key)

url="https://namu.wiki/w/카제로스"

response = client.extract(
  urls=[url],
  format="text",
)

print('응답 결과: ')
print(response)