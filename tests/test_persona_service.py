
from app.services.persona_service import get_confirmed_persona


def test_get_confirmed_persona():
    character_id = 3

    persona = get_confirmed_persona(character_id)

    if persona is None:
        print("확정된 페르소나가 없습니다.")
        return

    print("페르소나 조회 성공!")
    print("캐릭터 ID:", character_id)

    for field, value in persona.items():
        print(f"\n[{field}]")
        print(value[:150] if value else "내용 없음")


if __name__ == "__main__":
    test_get_confirmed_persona()
