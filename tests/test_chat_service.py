from app.services.chat_service import generate_character_response


def test_generate_character_response():
    character_id = 3
    user_message = "인피니트로 데뷔했을 때가 언제야?"

    response = generate_character_response(
        character_id=character_id,
        user_message=user_message,
    )

    print("\n===== Gemini 캐릭터 답변 =====")
    print(response)


if __name__ == "__main__":
    test_generate_character_response()