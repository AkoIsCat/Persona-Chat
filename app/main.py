# FastAPI 앱 실행 및 라우터 등록
import os

from fastapi import FastAPI, HTTPException
from pwdlib import PasswordHash
from psycopg.errors import UniqueViolation
from datetime import datetime, timedelta, timezone

import jwt

from app.database import get_connection
from app.schemas import UserCreate, UserLogin

app = FastAPI(
    title="Persona Chat API",
    description="원작 자료 기반 AI 캐릭터 챗봇 API",
    version="0.1.0",
)

password_hash = PasswordHash.recommended()
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
)

def create_access_token(user_id: int) -> str:
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

if not JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY가 .env에 설정되지 않았습니다.")

@app.get("/")
def read_root():
    return {"message" : "PersonaChat API is running!"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/health/db")
def database_health_check():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user;")
                db_name, db_user = cur.fetchone()

                return {
                    "status": "ok",
                    "database": db_name,
                    "user": db_user,
                }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed",
        )

@app.post("/users", status_code=201)
def create_user(user: UserCreate):
    hashed_password = password_hash.hash(user.password)

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash, nickname)
                    VALUES (%s, %s, %s)
                    RETURNING id, email, nickname, created_at
                    """,
                    (
                        user.email,
                        hashed_password,
                        user.nickname,
                    ),
                )

                new_user = cur.fetchone()

            return {
                "id": new_user[0],
            "email": new_user[1],
            "nickname": new_user[2],
            "created_at": new_user[3],
            }

    except UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="이미 가입된 이메일입니다.",
        )

@app.post("/login")
def login(user: UserLogin):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, email, nickname ,password_hash
                FROM users
                WHERE email = %s
                """,
                (user.email,),
            )

            db_user = cur.fetchone()

        if db_user is None:
            raise HTTPException(
                status_code=401,
                detail="이메일 또는 비밀번호가 올바르지 않습니다.",
            )

        is_valid = password_hash.verify(
            user.password,
            db_user[3],
        )

        if not is_valid:
            raise HTTPException(
                status_code=401,
                detail="이메일 또는 비밀번호가 올바르지 않습니다.",
            )

        return {
            "message" : "로그인 성공",
            "user" : {
                "id" : db_user[0],
                "email" : db_user[1],
                "nickname" : db_user[2],
            },
        }

