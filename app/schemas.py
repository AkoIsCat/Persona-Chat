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