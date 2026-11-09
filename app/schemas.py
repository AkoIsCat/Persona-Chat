# 요청, 응답 데이터 검증
from pydantic import BaseModel, EmailStr, Field, HttpUrl

class UserCreate(BaseModel):
  email: EmailStr
  password: str = Field(min_length=8, max_length=128)
  nickname: str = Field(min_length=1, max_length=50)

class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128) 

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
