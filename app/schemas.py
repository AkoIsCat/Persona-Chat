# 요청, 응답 데이터 검증
from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

class UserCreate(BaseModel):
  email: EmailStr
  password: str = Field(min_length=8, max_length=128)
  nickname: str = Field(min_length=1, max_length=50)

class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    remember: bool = False 

class CharacterCreate(BaseModel):
   name: str = Field(min_length=1, max_length=100)
   urls: list[HttpUrl] = Field(min_length=1, max_length=3)

class CharacterPersonaUpdate(BaseModel):
    basic_info: str
    personality: str
    values_and_beliefs: str
    goals: str
    background: str
    worldview: str
    speech_style: str
    behavior_patterns: str
    summary: str

class ChatRoomCreate(BaseModel):
    character_id: int

class ChatMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=5000)

class UserNicknameUpdate(BaseModel):
    nickname: str = Field(min_length=1, max_length=50)

class UserPasswordUpdate(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetVerify(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")

class PasswordResetConfirm(BaseModel):
    email: EmailStr
    reset_token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)
    new_password_confirm: str = Field(min_length=8, max_length=128)

    @field_validator("new_password_confirm")
    @classmethod
    def passwords_match(cls, value: str, info) -> str:
        password = info.data.get("new_password")
        if password is not None and value != password:
            raise ValueError("새 비밀번호가 일치하지 않습니다.")
        return value

class MemoryUpdate(BaseModel):
    memory_text: str | None = None
    memory_type: str | None = None
    importance: int | None = None

    @field_validator("memory_text")
    @classmethod
    def memory_text_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("기억 내용을 입력해 주세요.")
        return stripped

    @field_validator("memory_type")
    @classmethod
    def memory_type_length(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("기억 유형을 입력해 주세요.")
        if len(stripped) > 50:
            raise ValueError("기억 유형은 50자 이하여야 합니다.")
        return stripped

    @field_validator("importance")
    @classmethod
    def importance_range(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise ValueError("중요도는 1부터 5 사이의 정수여야 합니다.")
        return value
