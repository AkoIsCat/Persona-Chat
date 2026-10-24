from app.core.security import(
  create_access_token,
  verify_access_token,
)

# 사용자 ID 1번으로 토큰 생성
token = create_access_token(1)

# 정상 토큰 검증
print("정상 토큰:", verify_access_token(token))

# 잘못된 토큰 검증
print("잘못된 토큰:", verify_access_token("invalid-token"))