
import os

from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()


def extract_urls(urls: list[str]) -> dict:
    """Tavily를 이용해 지정한 URL의 텍스트를 추출한다."""

    api_key = os.getenv("TAVILY_API_KEY")

    if not api_key:
        raise ValueError("TAVILY_API_KEY가 설정되지 않았습니다.")

    client = TavilyClient(api_key=api_key)

    response = client.extract(
        urls=urls,
        format="text",
    )

    return response
