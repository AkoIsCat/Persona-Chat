from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import verify_access_token

bearar_scheme = HTTPBearer(auto_error=False)

def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(
      bearar_scheme
    ),
) -> int:
  """요청의 JWT를 검증하고 로그인한 사용자 ID를 반환한다."""
  if credentials is None:
    raise HTTPException(
      status_code=401,
      detail="인증 토큰이 필요합니다.",
      headers={"WWW-Authenticate":"Bearer"},
    )

  user_id = verify_access_token(credentials.credentials)

  if user_id is None:
    raise HTTPException(
      status_code=401,
      detail="유효하지 않거나 만료된 토큰입니다.",
      headers={"WWW-Authenticate" : "Bearer"},
    )

  return user_id