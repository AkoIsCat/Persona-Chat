
import os
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv
from tavily import TavilyClient

from app.core.perf import perf_span

load_dotenv()


def _tavily_client() -> TavilyClient:
    api_key = os.getenv("TAVILY_API_KEY")

    if not api_key:
        raise ValueError("TAVILY_API_KEY가 설정되지 않았습니다.")

    return TavilyClient(api_key=api_key)


def extract_urls(urls: list[str]) -> dict:
    """Tavily를 이용해 지정한 URL의 텍스트를 추출한다."""

    client = _tavily_client()

    with perf_span("Tavily 자료 추출"):
        response = client.extract(
            urls=urls,
            format="text",
        )

    return response


def character_name_from_url(url: str) -> str:
    """나무위키 문서 경로에서 캐릭터 이름을 꺼낸다."""

    parsed = urlparse((url or "").strip())
    parts = [unquote(part).strip() for part in parsed.path.split("/") if part]
    if not parts:
        return ""

    if "w" in parts:
        index = parts.index("w")
        if index + 1 < len(parts):
            return parts[index + 1]

    return parts[-1]


def _obtained_body(item: dict) -> str:
    """검색 응답에 실제로 들어 있는 본문만 반환한다."""

    raw_content = item.get("raw_content")
    if isinstance(raw_content, str) and raw_content.strip():
        return raw_content.strip()

    content = item.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    return ""


def _is_relevant_namu_result(name: str, item: dict) -> bool:
    result_url = item.get("url") or ""
    host = urlparse(result_url).netloc.lower()
    if host != "namu.wiki" and not host.endswith(".namu.wiki"):
        return False

    if not _obtained_body(item):
        return False

    needle = name.casefold().replace(" ", "")
    if not needle:
        return False

    title = item.get("title") or ""
    haystack = f"{title}\n{unquote(result_url)}".casefold().replace(" ", "")
    return needle in haystack


def search_character_material(page_url: str) -> list[dict]:
    """Extract에 실패한 URL의 캐릭터 이름을 나무위키에서 검색한다."""

    name = character_name_from_url(page_url)
    if not name:
        return []

    client = _tavily_client()
    query = f"{name} 나무위키"

    with perf_span("Tavily 검색"):
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=5,
            include_domains=["namu.wiki"],
            include_raw_content="text",
        )

    materials = []
    seen_bodies = set()

    for item in response.get("results") or []:
        if not isinstance(item, dict) or not _is_relevant_namu_result(name, item):
            continue

        body = _obtained_body(item)
        if body in seen_bodies:
            continue

        seen_bodies.add(body)
        title = item.get("title")
        materials.append(
            {
                "title": title.strip() if isinstance(title, str) else "",
                "url": item.get("url") or "",
                "raw_content": body,
            }
        )

    return materials
