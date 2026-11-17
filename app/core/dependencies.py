from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import read_access_token
from app.database import get_connection

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

  parsed = read_access_token(credentials.credentials)

  if parsed is None:
    raise HTTPException(
      status_code=401,
      detail="유효하지 않거나 만료된 토큰입니다.",
      headers={"WWW-Authenticate" : "Bearer"},
    )

  user_id, issued_at = parsed
  with get_connection() as conn:
    with conn.cursor() as cur:
      cur.execute(
        """
        SELECT valid_after
        FROM user_token_revocations
        WHERE user_id = %s
        """,
        (user_id,),
      )
      revoked = cur.fetchone()

  if revoked is not None and issued_at <= revoked[0]:
    raise HTTPException(
      status_code=401,
      detail="비밀번호가 변경되어 다시 로그인해 주세요.",
      headers={"WWW-Authenticate": "Bearer"},
    )

  return user_id