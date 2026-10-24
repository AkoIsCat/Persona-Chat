# 비밀번호 해시, JWT
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import (
  JWT_SECRET_KEY,
  JWT_ALGORITHM,
  JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
)

password_hash = PasswordHash.recommended()

def hash_password(password: str) -> str:
  """비밀번호를 해시로 변환한다."""
  return password_hash.hash(password)

def verify_password(
    plain_password: str,
    hashed_password: str,
) -> bool:
  """입력한 비밀번호와 저장된 해시를 비교한다."""
  return password_hash.verify(
    plain_password,
    hashed_password,
  )

def create_access_token(user_id: int) -> str:
  """사용자 ID를 담은 JWT 액세스 토큰을 생성한다."""
  now = datetime.now(timezone.utc)
  expires_at = now + timedelta(
          minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES
      )

  payload = {
          "sub": str(user_id),
          "iat": now,
          "exp": expires_at,
      }
  
  return jwt.encode(
    payload,
    JWT_SECRET_KEY,
    algorithm=JWT_ALGORITHM
      )