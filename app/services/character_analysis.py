# 원문 수집, 캐릭터 분석
import os
import json

from dotenv import load_dotenv
from google import genai

load_dotenv()


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
