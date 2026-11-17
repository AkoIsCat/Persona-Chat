from urllib.parse import unquote, urlparse

from app.core.perf import perf_span
from app.services.character_analysis import _gemini_client, _parse_json_object

EXCERPT_CHARS = 2000


def _excerpt(text: str) -> str:
    compacted = " ".join((text or "").split())
    if len(compacted) <= EXCERPT_CHARS:
        return compacted
    return compacted[:EXCERPT_CHARS]


def _path_clue(url: str) -> str:
    parsed = urlparse((url or "").strip())
    path = unquote(parsed.path or "").strip()
    return path or (url or "").strip()


def _log_decision(candidate: dict, decision: str, reason: str) -> None:
    print(
        "검색 자료 검증 | "
        f"판정={decision} | "
        f"등록URL={candidate.get('registered_url') or ''} | "
        f"검색URL={candidate.get('search_url') or ''} | "
        f"제목={candidate.get('title') or ''} | "
        f"사유={reason}"
    )


def _prompt(references: list[dict], candidates: list[dict]) -> str:
    if references:
        reference_blocks = []
        for index, item in enumerate(references, start=1):
            reference_blocks.append(
                "\n".join(
                    [
                        f"[기준 자료 {index}]",
                        f"등록 URL: {item.get('url') or ''}",
                        f"등록 URL 경로: {_path_clue(item.get('url') or '')}",
                        f"제목: {item.get('title') or ''}",
                        "본문 발췌:",
                        _excerpt(item.get("raw_content") or ""),
                    ]
                )
            )
        reference_text = "\n\n".join(reference_blocks)
        standard = """
[판정 기준]
추출에 성공한 기준 자료와 검색 결과를 대조하세요.
1. 동일한 작품 또는 세계관인지 확인하세요.
2. 동일한 소속과 등장인물 관계가 본문에 있는지 확인하세요.
3. 기준 자료와 모순되는 설정이 있으면 제외하세요.
4. 다른 작품의 동명이인이면 제외하세요.
5. 이름이나 일부 설정이 비슷하다는 이유만으로 통과시키지 마세요.
6. 위 근거가 본문에 구체적으로 없으면 불확실하므로 제외하세요.
"""
    else:
        reference_text = "이번 요청에서 추출에 성공한 원작 본문이 없습니다."
        standard = """
[판정 기준]
추출된 원작 본문이 없습니다. 더 보수적으로 판정하세요.
1. 등록 URL과 검색 URL이 같다는 사실만으로 통과시키지 마세요.
2. 등록 URL 경로와 검색 결과의 제목, URL 경로에서 작품이나 문서 대상을 가리키는 단서를 확인하세요.
3. 그 단서와 본문이 같은 인물임을 작품, 세계관, 소속, 인물 관계 중 구체적인 내용으로 뒷받침할 때만 통과시키세요.
4. 경로가 이름뿐이고 본문이 어느 작품의 누구인지 특정하지 못하면 제외하세요.
5. 다른 작품의 동명이인, 기준과 모순되는 설정, 근거가 부족한 자료는 제외하세요.
6. 이름이나 일부 설정이 비슷하다는 이유만으로 통과시키지 마세요.
"""

    candidate_blocks = []
    for index, item in enumerate(candidates):
        candidate_blocks.append(
            "\n".join(
                [
                    f"[검색 결과 {index}]",
                    f"id: {index}",
                    f"등록 URL: {item.get('registered_url') or ''}",
                    f"등록 URL 경로: {_path_clue(item.get('registered_url') or '')}",
                    f"검색 URL: {item.get('search_url') or ''}",
                    f"검색 URL 경로: {_path_clue(item.get('search_url') or '')}",
                    f"제목: {item.get('title') or ''}",
                    "본문 발췌:",
                    _excerpt(item.get("raw_content") or ""),
                ]
            )
        )

    return f"""
당신은 검색으로 모은 나무위키 문서가 사용자가 등록한 캐릭터와 같은 인물인지 판정하는 검토자입니다.

{standard}
[출력 형식]
반드시 JSON 객체만 출력하세요.
마크다운 코드 블록이나 추가 설명은 출력하지 마세요.
모든 검색 결과 id를 한 번씩 포함하세요.
same_character가 true여도 reason에는 작품, 세계관, 소속, 인물 관계 중 무엇을 보고 같은 인물로 봤는지 적으세요.
근거를 특정할 수 없으면 same_character는 false입니다.

{{
  "results": [
    {{"id": 0, "same_character": false, "reason": "다른 작품의 동명이인이다."}}
  ]
}}

[기준 자료]
{reference_text}

[검색 결과]
{chr(10).join(candidate_blocks)}
"""


def _accepted(candidates: list[dict], payload: dict) -> list[dict]:
    rows = payload.get("results")
    if not isinstance(rows, list):
        rows = []

    by_id = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        by_id[index] = row

    kept = []
    for index, candidate in enumerate(candidates):
        row = by_id.get(index)
        if row is None:
            _log_decision(candidate, "제외", "검증 결과에 없어 제외")
            continue

        reason = row.get("reason")
        reason = reason.strip() if isinstance(reason, str) else ""
        if row.get("same_character") is True and reason:
            _log_decision(candidate, "유지", reason)
            kept.append(candidate)
            continue

        _log_decision(candidate, "제외", reason or "동일 인물 근거가 부족해 제외")

    return kept


def select_verified_search_hits(
    references: list[dict],
    candidates: list[dict],
) -> list[dict]:
    """검색 후보를 한 번의 Gemini 호출로 걸러 동일 인물만 반환한다."""

    if not candidates:
        return []

    try:
        client, model_name = _gemini_client()
        with perf_span("Gemini 검색 자료 검증"):
            response = client.interactions.create(
                model=model_name,
                input=_prompt(references, candidates),
            )
        output_text = getattr(response, "output_text", None) or ""
    except Exception:
        for candidate in candidates:
            _log_decision(candidate, "제외", "검증 호출에 실패해 제외")
        return []

    try:
        payload = _parse_json_object(
            output_text,
            "검색 자료 검증 결과를 JSON으로 변환하지 못했습니다.",
        )
    except Exception:
        for candidate in candidates:
            _log_decision(candidate, "제외", "검증 결과를 해석하지 못해 제외")
        return []

    return _accepted(candidates, payload)


def search_document_title(title: str, search_url: str) -> str:
    """문서 제목은 유지하고, 본문이 아닌 title에 실제 검색 URL을 남긴다."""

    label = title.strip() if isinstance(title, str) else ""
    source = search_url.strip() if isinstance(search_url, str) else ""
    if not source:
        return label
    marker = f"검색URL: {source}"
    if marker in label:
        return label
    if label:
        return f"{label} | {marker}"
    return marker
