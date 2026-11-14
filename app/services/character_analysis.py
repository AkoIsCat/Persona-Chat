# 원문 수집, 캐릭터 분석
import os
import json

from dotenv import load_dotenv
from google import genai

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


def _get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")

    model_name = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.5-flash-lite",
    )
    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 120000},
    )
    return client, model_name


def _strip_json_fence(result_text: str) -> str:
    result_text = result_text.strip()
    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1]
        result_text = result_text.rsplit("```", 1)[0].strip()
    return result_text


def _parse_json_object(result_text: str, error_message: str) -> dict:
    result_text = _strip_json_fence(result_text)
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
    Analyze one source document against accumulated facts.
    Returns operation dicts: add / refine / duplicate / conflict.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("분석할 원작 텍스트가 없습니다.")

    existing_facts = existing_facts or []
    client, model_name = _get_gemini_client()
    existing_facts_text = _format_existing_facts(existing_facts)

    prompt = f"""
당신은 원작 자료를 바탕으로 캐릭터 정보를 누적 분석하는 전문가입니다.

분석 대상 캐릭터 이름: {character_name}

아래 현재 원작 자료를 읽고, 이미 누적된 정보와 비교하여
개별 정보 단위의 처리 작업을 JSON으로 반환하세요.

[분석 원칙]
1. 반드시 제공된 원작 자료에 근거하여 분석하세요.
2. 자료에서 확인할 수 없는 사실은 추측하거나 만들어내지 마세요.
3. 사용자가 입력한 캐릭터 이름을 분석 대상으로 삼으세요.
4. 모든 자료가 동일한 캐릭터에 관한 것이라면 순서와 관계없이 누적 분석에 활용하세요.
5. 서로 다른 내용이 발견되면 임의로 하나를 선택하지 말고 conflict로 별도 보존하세요.
6. 동일한 정보가 다시 확인되면 duplicate로 표시하고 새 행을 만들지 마세요.
7. 기존 정보를 더 구체화하는 내용은 refine으로 표시하세요.
8. 새로운 정보는 add로 표시하세요.
9. 원작에 등장하는 다른 인물의 정보를 분석 대상 캐릭터의 정보와 혼동하지 마세요.
10. 자료에서 추출할 정보가 없다면 operations를 빈 배열로 반환하세요.

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
- add: 새로운 정보 추가 (existing_fact_id는 null)
- refine: 기존 정보 보완 (existing_fact_id 필수, fact_text는 보완된 전체 내용)
- duplicate: 동일 정보 재확인 (existing_fact_id 필수, fact_text는 기존과 같거나 요약)
- conflict: 상충 정보 (기존을 덮어쓰지 않음, existing_fact_id에 상충 대상 ID)

[출력 형식]
반드시 JSON 객체만 출력하세요.
마크다운 코드 블록이나 추가 설명은 출력하지 마세요.

{{
  "operations": [
    {{
      "action": "add",
      "field_name": "personality",
      "fact_text": "추출된 정보",
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

    response = client.interactions.create(
        model=model_name,
        input=prompt,
    )
    result_text = response.output_text
    if not result_text:
        raise ValueError("Gemini가 분석 결과를 반환하지 않았습니다.")

    result = _parse_json_object(
        result_text,
        "Gemini 분석 결과를 JSON으로 변환하지 못했습니다.",
    )

    operations = result.get("operations", [])
    if not isinstance(operations, list):
        raise ValueError("Gemini 분석 결과의 operations 형식이 올바르지 않습니다.")

    normalized: list[dict] = []
    for item in operations:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip().lower()
        field_name = str(item.get("field_name", "")).strip()
        fact_text = str(item.get("fact_text", "")).strip()
        existing_fact_id = item.get("existing_fact_id")

        if action not in VALID_FACT_ACTIONS:
            continue
        if field_name not in PERSONA_FIELDS:
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

    return normalized


def synthesize_persona_from_facts(
    character_name: str,
    facts: list[dict],
) -> dict:
    """
    Build the final 9-field persona from accumulated character_persona_facts.
    """
    client, model_name = _get_gemini_client()
    facts_text = _format_existing_facts(facts)

    prompt = f"""
당신은 누적된 캐릭터 정보를 바탕으로 최종 페르소나를 정리하는 전문가입니다.

분석 대상 캐릭터 이름: {character_name}

아래 누적 정보를 사용해 최종 페르소나 9개 항목을 작성하세요.

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

    response = client.interactions.create(
        model=model_name,
        input=prompt,
    )
    result_text = response.output_text
    if not result_text:
        raise ValueError("Gemini가 최종 페르소나 결과를 반환하지 않았습니다.")

    result = _parse_json_object(
        result_text,
        "Gemini 최종 페르소나 결과를 JSON으로 변환하지 못했습니다.",
    )

    for field in PERSONA_FIELDS:
        if field not in result or not str(result[field]).strip():
            result[field] = "자료에서 확인되지 않음"
        else:
            result[field] = str(result[field]).strip()

    return {field: result[field] for field in PERSONA_FIELDS}


def analyze_character(raw_text: str, character_name: str | None = None) -> dict:
    """
    단일 원작 텍스트를 Gemini로 분석하여
    캐릭터 페르소나 정보를 JSON으로 반환한다.

    순차 다중 URL 분석은 extract_document_fact_operations /
    synthesize_persona_from_facts 경로를 사용한다.
    이 함수는 단일 자료 분석 및 하위 호환용으로 유지한다.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("분석할 원작 텍스트가 없습니다.")

    client, model_name = _get_gemini_client()
    name_line = (
        f"분석 대상 캐릭터 이름: {character_name}\n"
        if character_name
        else ""
    )

    prompt = f"""
당신은 원작 자료를 바탕으로 캐릭터를 분석하는 전문가입니다.

{name_line}
아래 원작 자료를 읽고 캐릭터의 페르소나 정보를 분석하세요.

[분석 원칙]
1. 반드시 제공된 원작 자료에 근거하여 분석하세요.
2. 자료에서 확인할 수 없는 내용은 추측하거나 만들어내지 마세요.
3. 확인할 수 없는 항목은 "자료에서 확인되지 않음"이라고 작성하세요.
4. 여러 정보가 충돌한다면 임의로 하나를 선택하지 말고 그 사실을 명시하세요.
5. 캐릭터의 성격과 말투를 구체적으로 분석하세요.
6. 원작에 등장하는 다른 인물의 정보를 분석 대상 캐릭터의 정보와 혼동하지 마세요.
7. 모든 자료가 동일한 캐릭터에 관한 것이라면 순서와 관계없이 누적 분석에 활용하세요.
8. 자료에서 확인되지 않은 사실은 만들어내지 마세요.
9. 서로 다른 내용이 발견되면 임의로 하나를 선택하지 말고 각각 별도의 정보로 보존하세요.

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

    response = client.interactions.create(
        model=model_name,
        input=prompt,
    )

    result_text = response.output_text

    if not result_text:
        raise ValueError("Gemini가 분석 결과를 반환하지 않았습니다.")

    result = _parse_json_object(
        result_text,
        "Gemini 분석 결과를 JSON으로 변환하지 못했습니다.",
    )

    for field in PERSONA_FIELDS:
        if field not in result:
            result[field] = "자료에서 확인되지 않음"

    return {field: result[field] for field in PERSONA_FIELDS}
