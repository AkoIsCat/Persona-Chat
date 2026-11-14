# 원문 수집, 캐릭터 분석
import os
import json

from dotenv import load_dotenv
from google import genai

from app.core.perf import perf_span

load_dotenv()

PERSONA_FIELDS = [
    "basic_info",
    "personality",
    "values_and_beliefs",
    "goals",
    "background",
    "worldview",
    "speech_style",
    "behavior_patterns",
    "summary",
]

VALID_FACT_ACTIONS = {"add", "refine", "duplicate", "conflict"}
COMPLETED_INTERACTION_STATUSES = {"completed", "idle"}


class FactExtractionError(ValueError):
    """사실 추출 응답을 완료로 믿을 수 없을 때 발생한다."""


def _gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")

    model_name = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 120000},
    )
    return client, model_name


def _parse_json_object(result_text: str, error_message: str) -> dict:
    result_text = result_text.strip()
    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1]
        result_text = result_text.rsplit("```", 1)[0].strip()

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError as e:
        raise ValueError(error_message) from e

    if not isinstance(result, dict):
        raise ValueError(error_message)
    return result


def _format_existing_facts(existing_facts: list[dict]) -> str:
    if not existing_facts:
        return "아직 누적된 정보가 없습니다."

    lines = []
    for fact in existing_facts:
        lines.append(
            "- "
            f"ID: {fact['id']}, "
            f"항목: {fact['field_name']}, "
            f"상태: {fact['status']}, "
            f"출처문서: {fact.get('source_document_ids') or []}, "
            f"관련ID: {fact.get('related_fact_id')}, "
            f"내용: {fact['fact_text']}"
        )
    return "\n".join(lines)


def extract_document_fact_operations(
    character_name: str,
    raw_text: str,
    existing_facts: list[dict] | None = None,
    title: str | None = None,
    url: str | None = None,
) -> list[dict]:
    """
    문서 하나를 이미 누적된 사실과 비교해 add/refine/duplicate/conflict 작업을 반환한다.
    """
    if not character_name or not str(character_name).strip():
        raise ValueError("분석 대상 캐릭터 이름이 없습니다.")
    if not raw_text or not raw_text.strip():
        raise ValueError("분석할 원작 텍스트가 없습니다.")

    existing_facts = existing_facts or []
    client, model_name = _gemini_client()
    existing_facts_text = _format_existing_facts(existing_facts)

    prompt = f"""
당신은 원작 자료를 바탕으로 캐릭터 정보를 누적 분석하는 전문가입니다.

분석 대상 캐릭터 이름: {character_name}

아래 현재 원작 자료만 읽고, 이미 누적된 정보와 비교하여
개별 정보 단위의 처리 작업을 JSON으로 반환하세요.

[분석 원칙]
1. 반드시 제공된 현재 원작 자료에 근거하여 분석하세요.
2. 자료에서 확인할 수 없는 사실은 추측하거나 만들어내지 마세요.
3. 분석 대상은 위에 적힌 캐릭터 이름입니다.
4. 누적된 기존 정보를 함께 보고, 현재 문서의 내용을 그 위에 반영하세요.
5. 서로 다른 내용이 발견되면 임의로 하나를 선택하지 말고 conflict로 별도 보존하세요.
6. duplicate는 기존 사실과 의미뿐 아니라 정보까지 완전히 같을 때만 사용하세요. 새 정보가 하나라도 있으면 duplicate로 하지 마세요.
7. 같은 사실에 세부 정보가 더해지고, 현재 문서가 기존 정보와 새 정보를 모두 뒷받침할 때만 refine로 하세요. fact_text에는 기존 정보의 모든 내용과 새 내용을 빠짐없이 적으세요.
8. 현재 문서가 기존 사실의 일부만 뒷받침하면 그 사실 전체에 duplicate나 refine를 붙이지 마세요. 새로 확인된 독립 정보만 add로 분리하세요.
9. 하나의 fact_text에는 하나의 핵심 정보만 적으세요. 여러 인물, 사건, 연도를 대표 문장 하나로 뭉뚱그리지 마세요. 바이올린 연주, 양봉, 리볼버 사격 연습, 겉모습을 통한 추리는 각각 별도의 사실로 적으세요.
10. 기존 사실에 없는 정보는 각각 독립적인 add로 빠뜨리지 말고 표시하세요. 인물과의 첫 만남과 그 만남을 주선한 인물, 캐릭터의 과거와 주변 인물과의 관계, 작품 발표 연도와 캐릭터의 첫 등장 시점, 주요 사건의 발생 과정과 결과, 사건에 등장하는 주요 인물과 캐릭터의 관계, 직업과 능력, 습관과 행동 패턴, 성격과 가치관을 보여주는 구체적인 행동이 해당됩니다.
11. 기존 사실과 일부만 겹친다는 이유로 새 정보를 통째로 생략하지 마세요. 기존 사실에 이미 있는 정보와 새로 확인한 정보를 구분하고, 새 정보는 추출하세요.
12. 서로 연결되어 있어도 구분되는 정보는 각각 저장하세요. 만남의 경위와 동거 사실은 한 문장으로 합치지 마세요.
13. 작품의 출판 정보와 캐릭터의 개인 정보를 구분하세요. 작품의 서술이나 특징을 캐릭터의 습관이나 행동으로 바꾸지 마세요. 작품이 어떤 도구를 처음 도입했다는 문장을, 캐릭터가 그 도구를 사용한다는 사실로 옮기지 마세요.
14. 다른 인물의 과거 생애 전체는 분석 대상의 사실로 저장하지 마세요. 다만 분석 대상이 그 인물과 만난 경위, 관계, 함께한 사건과 그 결과는 add하세요.
15. 원문에서 확인되지 않는 내용은 추측해서 추가하지 마세요. 위 항목을 모두 확인한 뒤에도 새 정보가 없을 때만 operations를 빈 배열로 반환하세요.

[field_name 허용 값]
- basic_info
- personality
- values_and_beliefs
- goals
- background
- worldview
- speech_style
- behavior_patterns
- summary

[action 허용 값]
- add: 기존 사실에 없는 독립 정보 (existing_fact_id는 null, fact_text는 그 정보만)
- refine: 같은 사실을 보완 (existing_fact_id 필수, fact_text는 기존 정보 전부와 새 정보를 포함한 전체)
- duplicate: 기존 사실과 정보가 완전히 같음 (existing_fact_id 필수, fact_text는 기존 사실과 같음)
- conflict: 상충 정보 (기존을 덮어쓰지 않음, existing_fact_id에 상충 대상 ID)

[출력 형식]
반드시 JSON 객체만 출력하세요.
마크다운 코드 블록이나 추가 설명은 출력하지 마세요.

{{
  "operations": [
    {{
      "action": "add",
      "field_name": "background",
      "fact_text": "지인이 분석 대상과 다른 인물의 첫 만남을 주선했다.",
      "existing_fact_id": null
    }},
    {{
      "action": "add",
      "field_name": "background",
      "fact_text": "분석 대상은 그 인물과 함께 살게 되었다.",
      "existing_fact_id": null
    }}
  ]
}}

[누적된 기존 정보]
{existing_facts_text}

[현재 원작 자료]
제목: {title or "제목 없음"}
URL: {url or "URL 없음"}

{raw_text}
"""

    with perf_span("Gemini 사실 비교"):
        response = client.interactions.create(model=model_name, input=prompt)
    return parse_fact_operation_response(
        getattr(response, "output_text", None),
        status=getattr(response, "status", None),
        errors=getattr(response, "errors", None),
    )


def extract_document_facts(
    character_name: str,
    document_title: str | None,
    document_url: str | None,
    document_body: str,
) -> list[dict]:
    """
    기존 사실과 비교하지 않고, 원문에서 독립적인 사실 후보를 추출한다.
    """
    if not character_name or not str(character_name).strip():
        raise ValueError("분석 대상 캐릭터 이름이 없습니다.")
    if not document_body or not str(document_body).strip():
        raise ValueError("분석할 원작 텍스트가 없습니다.")

    client, model_name = _gemini_client()
    prompt = f"""
당신은 원작 자료에서 캐릭터 사실을 추출하는 전문가입니다.

분석 대상 캐릭터 이름: {character_name}

아래 현재 원작 자료만 읽고, 분석 대상에 관한 사실 후보를 JSON으로 반환하세요.
이미 저장된 사실과 중복되는지 판단하지 않는다.

[추출 원칙]
1. 원문에 명시된 캐릭터 관련 사실을 최대한 빠뜨리지 않고 추출한다. 각 유형에 해당하는 정보가 원문에 있으면 기존 사실과 비교하지 말고 독립적인 후보로 추출한다.
2. 하나의 후보에는 하나의 핵심 사실만 담는다. 여러 사건이나 인물 관계를 하나의 사실로 합치지 않는다.
3. 다음 정보가 원문에 있으면 각각 분리한다. 출생, 직업, 신분, 작품 내 첫 등장 시점. 캐릭터가 처음 등장한 작품과 작품의 발표 연도. 다른 인물과 처음 만나게 된 경위. 만남을 주선하거나 연결한 인물. 인간관계와 동거 관계. 참여한 주요 사건과 사건의 진행 및 결과. 능력, 기술, 습관, 구체적인 행동. 성격과 가치관을 보여주는 행동. 과거와 배경.
4. 작품 발표 연도와 캐릭터의 첫 등장은 원문에 명시되어 있다면 별도 사실로 추출한다. 작품의 출판 정보라는 이유만으로 캐릭터와 무관하다고 판단해 제외하지 않는다.
5. 다른 인물과의 첫 만남은 만남을 주선한 인물과 실제 만남을 구분한다.
6. 다른 인물의 생애나 배경을 대상 캐릭터의 사실로 잘못 저장하지 않는다. 대상 캐릭터와 만난 경위, 관계, 함께한 사건만 추출한다.
7. 작품의 줄거리를 캐릭터의 개인적 습관이나 성격으로 바꾸지 않는다.
8. 원문에 없는 정보는 추측하지 않는다. 사실이 없을 때만 facts를 빈 배열로 반환한다.

[field_name 허용 값]
사실의 핵심 내용을 기준으로 가장 적절한 필드 하나만 선택한다.
- basic_info: 출생, 신분, 직업, 작품 내 첫 등장 등 기본적인 인물 정보
- personality: 성격과 기질
- values_and_beliefs: 신념과 가치관
- goals: 목표와 동기
- background: 과거, 인간관계, 만남의 경위, 주요 사건, 작품 발표 및 첫 등장 관련 맥락
- worldview: 세계와 사회를 바라보는 관점
- speech_style: 말투와 언어 습관
- behavior_patterns: 반복적인 습관, 행동 방식, 구체적인 행동
- summary: 여러 사실을 한 항목으로 묶는 종합 정보. 개별 사실은 summary 대신 위 필드 중 하나에 둔다.

[출력 형식]
반드시 JSON 객체만 출력하세요.
마크다운 코드 블록이나 추가 설명은 출력하지 마세요.
아래 예시는 형식이다. 원문에 없는 내용은 출력하지 않는다.

{{
  "facts": [
    {{
      "field_name": "background",
      "fact_text": "스탬퍼드의 소개로 존 왓슨과 처음 만나게 되었다."
    }},
    {{
      "field_name": "background",
      "fact_text": "존 왓슨과 221B 베이커 가에서 함께 살게 되었다."
    }},
    {{
      "field_name": "basic_info",
      "fact_text": "1887년 발표된 작품에서 처음 등장했다."
    }}
  ]
}}

[현재 원작 자료]
제목: {document_title or "제목 없음"}
URL: {document_url or "URL 없음"}

{document_body}
"""

    with perf_span("Gemini 사실 추출"):
        response = client.interactions.create(model=model_name, input=prompt)
    return parse_fact_candidate_response(
        getattr(response, "output_text", None),
        status=getattr(response, "status", None),
        errors=getattr(response, "errors", None),
    )


def parse_fact_candidate_response(
    result_text: str | None,
    status: object | None = None,
    errors: object | None = None,
) -> list[dict]:
    """정상 완료된 JSON의 빈 facts만 빈 목록으로 반환한다."""
    status_value = getattr(status, "value", status)
    status_text = str(status_value or "").strip().lower()
    if "." in status_text:
        status_text = status_text.rsplit(".", 1)[-1]
    if status_text and status_text not in COMPLETED_INTERACTION_STATUSES:
        raise FactExtractionError(
            f"Gemini 사실 추출이 완료되지 않았습니다. status={status_text}"
        )
    if errors:
        raise FactExtractionError("Gemini 사실 추출 응답에 오류가 포함되어 있습니다.")
    if result_text is None or not str(result_text).strip():
        raise FactExtractionError("Gemini가 분석 결과를 반환하지 않았습니다.")

    try:
        result = _parse_json_object(
            str(result_text),
            "Gemini 분석 결과를 JSON으로 변환하지 못했습니다.",
        )
    except ValueError as exc:
        raise FactExtractionError(
            "Gemini 분석 결과를 JSON으로 변환하지 못했습니다."
        ) from exc

    if "facts" not in result:
        raise FactExtractionError("Gemini 분석 결과에 facts가 없습니다.")
    facts = result["facts"]
    if not isinstance(facts, list):
        raise FactExtractionError("Gemini 분석 결과의 facts 형식이 올바르지 않습니다.")

    normalized = []
    for item in facts:
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("field_name", "")).strip()
        fact_text = str(item.get("fact_text", "")).strip()
        if field_name not in PERSONA_FIELDS or not fact_text:
            continue
        normalized.append({"field_name": field_name, "fact_text": fact_text})

    if facts and not normalized:
        raise FactExtractionError("Gemini 사실 후보가 모두 형식에 맞지 않습니다.")
    return normalized


def parse_fact_operation_response(
    result_text: str | None,
    status: object | None = None,
    errors: object | None = None,
) -> list[dict]:
    """
    정상 완료된 JSON의 빈 operations만 빈 목록으로 반환한다.
    비어 있거나, 잘리거나, 형식이 잘못된 응답은 FactExtractionError다.
    """
    status_value = getattr(status, "value", status)
    status_text = str(status_value or "").strip().lower()
    if "." in status_text:
        status_text = status_text.rsplit(".", 1)[-1]
    if status_text and status_text not in COMPLETED_INTERACTION_STATUSES:
        raise FactExtractionError(
            f"Gemini 사실 추출이 완료되지 않았습니다. status={status_text}"
        )
    if errors:
        raise FactExtractionError("Gemini 사실 추출 응답에 오류가 포함되어 있습니다.")
    if result_text is None or not str(result_text).strip():
        raise FactExtractionError("Gemini가 분석 결과를 반환하지 않았습니다.")

    try:
        result = _parse_json_object(
            str(result_text),
            "Gemini 분석 결과를 JSON으로 변환하지 못했습니다.",
        )
    except ValueError as exc:
        raise FactExtractionError(
            "Gemini 분석 결과를 JSON으로 변환하지 못했습니다."
        ) from exc

    if "operations" not in result:
        raise FactExtractionError("Gemini 분석 결과에 operations가 없습니다.")
    operations = result["operations"]
    if not isinstance(operations, list):
        raise FactExtractionError("Gemini 분석 결과의 operations 형식이 올바르지 않습니다.")

    normalized = []
    for item in operations:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip().lower()
        field_name = str(item.get("field_name", "")).strip()
        fact_text = str(item.get("fact_text", "")).strip()
        existing_fact_id = item.get("existing_fact_id")

        if action not in VALID_FACT_ACTIONS or field_name not in PERSONA_FIELDS:
            continue
        if action != "duplicate" and not fact_text:
            continue
        if action in {"refine", "duplicate"} and existing_fact_id is None:
            continue

        if existing_fact_id is not None:
            try:
                existing_fact_id = int(existing_fact_id)
            except (TypeError, ValueError):
                if action in {"refine", "duplicate", "conflict"}:
                    continue
                existing_fact_id = None

        normalized.append(
            {
                "action": action,
                "field_name": field_name,
                "fact_text": fact_text,
                "existing_fact_id": existing_fact_id,
            }
        )

    if operations and not normalized:
        raise FactExtractionError(
            "Gemini 사실 추출 작업이 모두 형식에 맞지 않습니다."
        )
    return normalized


def synthesize_persona_from_facts(character_name: str, facts: list[dict]) -> dict:
    """누적된 사실만으로 character_personas의 9개 필드를 만든다."""
    if not character_name or not str(character_name).strip():
        raise ValueError("분석 대상 캐릭터 이름이 없습니다.")

    client, model_name = _gemini_client()
    facts_text = _format_existing_facts(facts)

    prompt = f"""
당신은 누적된 캐릭터 정보를 바탕으로 최종 페르소나를 정리하는 전문가입니다.

분석 대상 캐릭터 이름: {character_name}

아래 누적 정보만 사용해 최종 페르소나 9개 항목을 작성하세요.

[작성 원칙]
1. 누적된 정보에만 근거하세요. 확인되지 않은 사실은 만들어내지 마세요.
2. 확인할 수 없는 항목은 "자료에서 확인되지 않음"이라고 작성하세요.
3. 상충 정보(status=conflict)는 임의로 하나를 선택하지 말고 함께 명시하세요.
4. 동일 정보가 여러 출처에서 확인된 경우 그 내용을 반영해 간결하게 정리하세요.
5. 캐릭터의 성격과 말투를 구체적으로 정리하세요.

[출력 형식]
반드시 JSON 객체만 출력하세요.
마크다운 코드 블록이나 추가 설명은 출력하지 마세요.

{{
    "basic_info": "",
    "personality": "",
    "values_and_beliefs": "",
    "goals": "",
    "background": "",
    "worldview": "",
    "speech_style": "",
    "behavior_patterns": "",
    "summary": ""
}}

[누적된 정보]
{facts_text}
"""

    with perf_span("Gemini 페르소나 정리"):
        response = client.interactions.create(model=model_name, input=prompt)
    result_text = response.output_text
    if not result_text:
        raise ValueError("Gemini가 최종 페르소나 결과를 반환하지 않았습니다.")

    result = _parse_json_object(
        result_text,
        "Gemini 최종 페르소나 결과를 JSON으로 변환하지 못했습니다.",
    )

    persona = {}
    for field in PERSONA_FIELDS:
        value = result.get(field)
        if value is None or not str(value).strip():
            persona[field] = "자료에서 확인되지 않음"
        else:
            persona[field] = str(value).strip()
    return persona


def analyze_character(raw_text: str) -> dict:
    """
    원작 텍스트를 Gemini로 분석하여
    캐릭터 페르소나 정보를 JSON으로 반환한다.
    """

    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.5-flash-lite"
    )

    if not api_key:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")

    if not raw_text or not raw_text.strip():
        raise ValueError("분석할 원작 텍스트가 없습니다.")

    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 120000}
    )

    prompt = f"""
당신은 원작 자료를 바탕으로 캐릭터를 분석하는 전문가입니다.

아래 원작 자료를 읽고 캐릭터의 페르소나 정보를 분석하세요.

[분석 원칙]
1. 반드시 제공된 원작 자료에 근거하여 분석하세요.
2. 자료에서 확인할 수 없는 내용은 추측하거나 만들어내지 마세요.
3. 확인할 수 없는 항목은 "자료에서 확인되지 않음"이라고 작성하세요.
4. 여러 정보가 충돌한다면 임의로 하나를 선택하지 말고 그 사실을 명시하세요.
5. 캐릭터의 성격과 말투를 구체적으로 분석하세요.
6. 원작에 등장하는 다른 인물의 정보를 분석 대상 캐릭터의 정보와 혼동하지 마세요.
7. 최초 원작 자료에 등장하는 인물을 분석 대상으로 삼으세요.
8. 추가 자료에 이름이 같은 인물이 등장하더라도 동일 인물이라는 근거가 없다면 분석 대상에 포함하지 마세요.
9. 인물의 소속, 작품, 배경, 관계, 활동 이력 등을 종합하여 동일 인물인지 판단하세요.
10. 서로 다른 작품이나 분야에 등장하는 동명이인의 정보를 혼합하지 마세요.
11. 추가 자료가 최초 원작 자료의 인물과 동일 인물인지 불분명하다면 해당 자료의 정보는 분석에 사용하지 마세요.

[분석 항목]
- basic_info: 캐릭터의 기본 정보
- personality: 성격과 성향
- values_and_beliefs: 가치관 및 신념
- goals: 목표와 욕구
- background: 과거와 배경
- worldview: 세계관 및 세상을 바라보는 관점
- speech_style: 말투, 어휘, 문장 표현 방식
- behavior_patterns: 행동 방식과 반복적인 행동 특성
- summary: 캐릭터의 핵심 특징 요약

[출력 형식]
반드시 JSON 객체만 출력하세요.
마크다운 코드 블록이나 추가 설명은 출력하지 마세요.

{{
    "basic_info": "",
    "personality": "",
    "values_and_beliefs": "",
    "goals": "",
    "background": "",
    "worldview": "",
    "speech_style": "",
    "behavior_patterns": "",
    "summary": ""
}}

[원작 자료]
{raw_text}
"""

    with perf_span("Gemini 분석"):
        response = client.interactions.create(
            model=model_name,
            input=prompt
        )

    result_text = response.output_text

    if not result_text:
        raise ValueError("Gemini가 분석 결과를 반환하지 않았습니다.")

    # JSON 이외의 코드 블록 표시가 포함된 경우 제거
    result_text = result_text.strip()

    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1]
        result_text = result_text.rsplit("```", 1)[0].strip()

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            "Gemini 분석 결과를 JSON으로 변환하지 못했습니다."
        ) from e

    required_fields = [
        "basic_info",
        "personality",
        "values_and_beliefs",
        "goals",
        "background",
        "worldview",
        "speech_style",
        "behavior_patterns",
        "summary"
    ]

    for field in required_fields:
        if field not in result:
            result[field] = "자료에서 확인되지 않음"

    return result
