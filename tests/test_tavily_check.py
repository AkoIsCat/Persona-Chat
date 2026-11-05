import os
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

url = "https://namu.wiki/w/%EA%B9%80%EC%84%B1%EA%B7%9C(%EC%9D%B8%ED%94%BC%EB%8B%88%ED%8A%B8)"

response = client.extract(urls=url)

print(response)